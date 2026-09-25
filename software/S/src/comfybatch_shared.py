"""Coordination for several S processes sharing one data directory.

S used to be a single-instance program: a named mutex made the second launch step
aside and open the first window. Users asked for the opposite -- several windows, one
data directory -- so the mutex is gone and the persistence layer has to survive being
shared. Three things break when it is shared naively, and each has a fix here.

1. **Fixed temporary files.** Every writer used ``<target>.tmp`` as its scratch file.
   Two processes writing ``settings.json`` therefore wrote *the same* scratch path, and
   ``os.replace`` published whichever finished last -- including a half-written mix of
   both payloads. :func:`atomic_write_text` uses a scratch name unique to the process and
   the call, then ``os.replace``, which is atomic on Windows and POSIX alike.

2. **Lost updates.** Atomic replace stops corruption but not a lost update: both
   processes read the file, both change a different key, and the second write discards
   the first one's change. :class:`FileLock` is a short-lived advisory lock -- it is held
   for the read-modify-write of one file and nothing else, so it can never grow into the
   process-wide single-instance lock this program deliberately removed.

3. **Stale writers.** A page that loaded the settings an hour ago and then saves them
   would silently roll back whatever another window changed in between. Every file
   written through :func:`locked_json_update` carries a ``_revision`` counter, and the
   caller gets back the revision it replaced and whether the payload had moved on since
   it was read, so the UI can say "another window changed this" instead of overwriting.

Scope limit, stated so it is not mistaken for more than it is: the lock is advisory and
per-file. It coordinates S processes that use this module; it does not stop a text editor
or a backup tool from touching the same file. Nothing here tries to make concurrent
*writers of the same key* agree -- the last writer still wins that key; what is now
guaranteed is that the file is never torn and that the loser is told.
"""

from __future__ import annotations

import errno
import json
import os
import pathlib
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

#: How long to wait for a busy lock before giving up. Writes are small and hold the lock
#: for a read plus a replace, so anything approaching this means something is wrong --
#: and giving up is safe: the caller reports it rather than writing anyway.
LOCK_TIMEOUT_SECONDS = 8.0

#: A lock file older than this is treated as abandoned (the holder crashed between
#: creating and removing it) and is broken. Comfortably longer than any real write.
LOCK_STALE_SECONDS = 30.0

#: How often to re-check a busy lock.
LOCK_POLL_SECONDS = 0.05

#: Key holding the revision counter inside a JSON document. Underscored so it cannot be
#: confused with a user's own setting.
REVISION_KEY = "_revision"


def unique_temp_for(target: pathlib.Path) -> pathlib.Path:
    """A scratch path next to ``target`` that no other process can pick.

    Must be in the same directory as ``target``: ``os.replace`` is only atomic within a
    filesystem, and the temp directory is frequently on another volume.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.parent / f"{target.name}.{os.getpid()}.{time.time_ns()}.tmp"


#: On Windows a concurrent reader can hold the target open for a moment, which makes the
#: replace fail with a sharing violation instead of blocking. Retrying a few times is what
#: keeps "someone is reading the file" from turning into "the write was lost" -- with
#: several processes sharing one data directory that window is now routine, not exotic.
REPLACE_ATTEMPTS = 20
REPLACE_RETRY_SECONDS = 0.02


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` so readers see either the old file or the new one.

    The scratch file is unique per call, flushed and fsynced before the replace, so a
    crash cannot publish a truncated payload.
    """
    temporary = unique_temp_for(path)
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_RETRY_SECONDS)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_json(path: pathlib.Path) -> dict[str, Any]:
    """The document at ``path``, or ``{}`` when it is missing, empty or not an object."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json_with_revision(path: pathlib.Path) -> tuple[dict[str, Any], int]:
    payload = read_json(path)
    return payload, revision_of(payload)


def revision_of(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get(REVISION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def write_json_atomic(path: pathlib.Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


class FileLockTimeout(RuntimeError):
    """The lock stayed busy for the whole timeout."""


class FileLock:
    """A short-lived advisory lock over one file, taken with ``O_CREAT|O_EXCL``.

    ``O_EXCL`` makes the check-and-create atomic on every platform this program runs on,
    so two processes racing cannot both believe they hold it. The holder's pid and a
    random token are written inside, which lets :meth:`release` avoid deleting a lock
    that a *later* holder created after ours was broken as stale.
    """

    def __init__(self, target: pathlib.Path, *, timeout: float = LOCK_TIMEOUT_SECONDS,
                 stale_after: float = LOCK_STALE_SECONDS) -> None:
        self.path = target.with_name(target.name + ".lock")
        self.timeout = timeout
        self.stale_after = stale_after
        self.token = f"{os.getpid()}-{time.time_ns()}"
        self.broken_stale = 0

    def _try_create(self) -> bool:
        # The lock file's directory may not exist yet (a brand-new data directory), and
        # creating the lock must not be what fails first.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileLockTimeout(f"无法创建锁目录：{self.path.parent}（{exc}）") from exc
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError as exc:  # a directory we cannot write to is a real failure
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise
            return False
        with os.fdopen(handle, "w", encoding="utf-8") as fd:
            fd.write(self.token)
        return True

    def _break_if_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True  # it vanished; retry the create
        if age <= self.stale_after:
            return False
        try:
            self.path.unlink(missing_ok=True)
            self.broken_stale += 1
            return True
        except OSError:
            return False

    def acquire(self) -> bool:
        deadline = time.time() + self.timeout
        while True:
            if self._try_create():
                return True
            if self._break_if_stale():
                continue
            if time.time() >= deadline:
                raise FileLockTimeout(f"等待文件锁超时：{self.path}")
            time.sleep(LOCK_POLL_SECONDS)

    def release(self) -> None:
        try:
            if self.path.read_text(encoding="utf-8") == self.token:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


@dataclass
class UpdateOutcome:
    """What a :func:`locked_json_update` call actually did.

    ``applied`` False means nothing was written and the caller must tell the user; the
    other fields say why. ``stale`` is the interesting one: the file's revision had moved
    past the one the caller read, so applying its change would have rolled back another
    window's edit.
    """

    applied: bool
    reason: str = ""
    revision: int = 0
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"applied": self.applied, "reason": self.reason,
                "revision": self.revision, "stale": self.stale}


def locked_json_update(
    path: pathlib.Path,
    mutate: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    expected_revision: int | None = None,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> UpdateOutcome:
    """Read-modify-write ``path`` under :class:`FileLock`.

    ``mutate`` receives the current document and returns the document to store, or
    ``None`` to abort without writing. Passing ``expected_revision`` asks for a
    conflict check: if the stored revision differs, nothing is written and the outcome
    says so, which is how a window that has been open (and idle) for a while avoids
    silently discarding a change made elsewhere.
    """
    lock = FileLock(path, timeout=timeout)
    try:
        lock.acquire()
    except FileLockTimeout as exc:
        return UpdateOutcome(False, str(exc))
    try:
        current = read_json(path)
        revision = revision_of(current)
        if expected_revision is not None and revision != expected_revision:
            return UpdateOutcome(False, "数据已被另一窗口修改，请刷新后重试",
                                 revision=revision, stale=True)
        # Re-read under the lock, so a change that landed after the caller's own read is
        # what ``mutate`` sees as "current" -- the lock is what makes this read reliable.
        updated = mutate(current)
        if updated is None:
            return UpdateOutcome(False, "没有需要写入的变更", revision=revision)
        updated = {**updated, REVISION_KEY: revision + 1}
        write_json_atomic(path, updated)
        return UpdateOutcome(True, revision=revision + 1)
    finally:
        lock.release()

"""Which S processes are running, on which port, and how to reach them.

The program used to be single-instance: a named mutex made a second launch give way and
open the first window, and one record file (``instance.json``) described "the" instance.
Both are gone. Several windows may now share one data directory, so:

* :func:`bind_server` picks the port by **binding** it, not by probing whether it looks
  free. Probing and binding in two steps is a race -- another process can take the port in
  between, and the loser finds out only when it tries to serve. Binding is the check, so
  the URL is never announced for a port this process does not actually own.
* :class:`InstanceRegistry` keeps **one record per instance**, keyed by the instance id
  that ``/api/ping`` already reports. Removing a record removes only that instance's own
  file, so a process exiting can no longer delete the record of a process that is still
  running -- which is exactly what the old shared ``instance.json`` did.

The registry is a directory of small JSON files rather than one file because the whole
point is that writers must not share a file they are also deleting: separate names make
"who removes what" unambiguous. Records of processes that are gone are pruned lazily by
whoever reads, never by a timer that might guess wrong.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Any, Callable

#: How many ports past the preferred one to try before giving up. Matches the previous
#: behaviour (``preferred`` .. ``preferred + 20``) so an occupied 8790 still lands close by.
PORT_SPAN = 20

#: A record whose process cannot be found and whose port does not answer is pruned once it
#: is older than this. The age guard keeps a process that is *starting up* (port not yet
#: listening, pid already alive but the check racing) from having its record removed.
RECORD_GRACE_SECONDS = 20.0


def instance_root() -> pathlib.Path:
    """Where instance records live.

    Deliberately outside ``--data-dir``: a launch that uses a different data directory must
    still be able to see (and not disturb) the instances using another one, and the record
    is about the *process*, not about the data it happens to have open.
    """
    local_data = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local")
    return local_data / "ComfyBatch-S" / "instances"


def legacy_record_path(instance_name: str) -> pathlib.Path:
    """The pre-multi-instance single record file, still read for migration/diagnostics."""
    local_data = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local")
    filename = "instance.json" if instance_name == "default" else f"instance-{instance_name}.json"
    return local_data / "ComfyBatch-S" / filename


def record_path(instance_id: str) -> pathlib.Path:
    safe = "".join(ch for ch in str(instance_id) if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError("实例 ID 为空，无法定位实例记录")
    return instance_root() / f"{safe}.json"


class PortUnavailable(RuntimeError):
    """No port in the requested range could be bound."""


def bind_server(host: str, preferred: int, factory: Callable[[str, int], Any],
                span: int = PORT_SPAN) -> tuple[Any, int]:
    """Bind ``factory(host, port)``, starting at ``preferred`` and walking up ``span`` ports.

    Returns the bound server and the port it actually owns. Raises :class:`PortUnavailable`
    naming the range when every candidate is taken, so the failure says what to do rather
    than surfacing a bare ``OSError`` from deep inside the socket module.
    """
    last: Exception | None = None
    for port in range(preferred, preferred + span + 1):
        try:
            server = factory(host, port)
        except OSError as exc:
            last = exc
            continue
        # Report the port the socket actually got, not the one that was asked for:
        # ``--port 0`` means "any free port", and announcing port 0 would send the browser
        # (and the instance record) to an address nothing is listening on.
        actual = port
        address = getattr(server, "server_address", None)
        if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[1], int):
            actual = address[1]
        return server, actual
    raise PortUnavailable(
        f"端口 {preferred} 至 {preferred + span} 都被占用，无法启动。"
        f"请关闭占用这些端口的程序，或用 --port 指定其它端口。（最后一个错误：{last}）"
    )


def process_alive(pid: int) -> bool:
    """Whether a process with this pid currently exists.

    On Windows the reliable check is a handle query, because ``os.kill(pid, 0)`` has
    different semantics there. Falls back to "assume alive" when the check itself is not
    available -- never prune a record because the *probe* failed.
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 - no ctypes: do not prune on a failed probe
            return True
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


@dataclass
class InstanceRecord:
    instance_id: str
    pid: int
    port: int
    url: str
    instance_name: str = "default"
    started_at: float = 0.0
    path: pathlib.Path | None = None

    @property
    def alive(self) -> bool:
        return process_alive(self.pid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "pid": self.pid,
            "port": self.port,
            "url": self.url,
            "instance_name": self.instance_name,
            "started_at": self.started_at,
            "started_at_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at or time.time())),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], path: pathlib.Path | None = None) -> "InstanceRecord | None":
        try:
            instance_id = str(payload.get("instance_id") or "").strip()
            pid = int(payload.get("pid") or 0)
            port = int(payload.get("port") or 0)
        except (TypeError, ValueError):
            return None
        if not instance_id or port <= 0:
            return None
        return cls(
            instance_id=instance_id,
            pid=pid,
            port=port,
            url=str(payload.get("url") or f"http://127.0.0.1:{port}/"),
            instance_name=str(payload.get("instance_name") or "default"),
            started_at=float(payload.get("started_at") or 0.0),
            path=path,
        )


class InstanceRegistry:
    """Reads and writes this user's per-instance records."""

    def __init__(self, root: pathlib.Path | None = None, *, grace: float = RECORD_GRACE_SECONDS) -> None:
        self.root = root or instance_root()
        self.grace = grace

    # ---------------------------------------------------------------- writing

    def register(self, record: InstanceRecord) -> pathlib.Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = record_path(record.instance_id)
        payload = dict(record.to_dict())
        payload["registered_at"] = time.time()
        temporary = path.parent / f"{path.name}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path

    def refresh(self, instance_id: str, *, port: int, url: str) -> None:
        """Update our own record in place. Only ever touches our own instance id."""
        path = record_path(instance_id)
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        payload.update({"port": port, "url": url, "refreshed_at": time.time()})
        temporary = path.parent / f"{path.name}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def unregister(self, instance_id: str) -> bool:
        """Remove **only** this instance's record.

        The previous single-file design could not do this: whichever process exited last
        deleted the file that also described the process still running. Separate names make
        the ownership explicit, and this method never touches anything else.
        """
        try:
            path = record_path(instance_id)
        except ValueError:
            return False
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------- reading

    def read(self, instance_id: str) -> InstanceRecord | None:
        try:
            path = record_path(instance_id)
        except ValueError:
            return None
        return self._load(path)

    def _load(self, path: pathlib.Path) -> InstanceRecord | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return InstanceRecord.from_dict(payload, path)

    def records(self, *, prune: bool = False) -> list[InstanceRecord]:
        """Every readable record, newest first. Also reads the legacy single file.

        ``prune`` removes records whose process is gone *and* which are past the grace
        period. It is opt-in because pruning is a write, and a read-only caller (the page
        listing instances) should not be deleting other processes' files by accident.
        """
        out: list[InstanceRecord] = []
        if self.root.is_dir():
            for path in sorted(self.root.glob("*.json")):
                record = self._load(path)
                if record is None:
                    continue
                out.append(record)
        out.sort(key=lambda r: r.started_at, reverse=True)
        if prune:
            self._prune(out)
        return out

    def _prune(self, records: list[InstanceRecord]) -> list[str]:
        removed = []
        now = time.time()
        for record in records:
            if record.alive:
                continue
            # An absent timestamp means "unknown", not "ancient": `started_at or now` would
            # quietly read 0.0 as "just started" while any other falsy value would read as
            # infinitely old. Unknown age is never enough to delete someone's record.
            if not record.started_at:
                continue
            if now - record.started_at < self.grace:
                continue
            if self.unregister(record.instance_id):
                removed.append(record.instance_id)
        return removed

    def legacy_records(self) -> list[InstanceRecord]:
        """Records from the pre-multi-instance single file, if one is still around.

        Read-only on purpose: a file this version did not write is not this version's to
        delete, and a still-running older build may be relying on it.
        """
        out = []
        for name in ("default",):
            path = legacy_record_path(name)
            if path.is_file():
                record = self._load(path)
                if record is not None:
                    out.append(record)
        return out

    def all_records(self) -> list[InstanceRecord]:
        seen: dict[str, InstanceRecord] = {}
        for record in self.records() + self.legacy_records():
            seen.setdefault(record.instance_id, record)
        return sorted(seen.values(), key=lambda r: r.started_at, reverse=True)

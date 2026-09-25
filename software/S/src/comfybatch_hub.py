"""Live state fan-out and the single-writer editing lease.

There is deliberately no process-wide lock in this module any more. It used to hold
``InstanceLock``, a Windows named mutex that made the desktop app single-instance;
several windows may now share one data directory, so launching a second process is a
normal thing to do. What replaces it is per-file coordination in
``comfybatch_shared`` plus per-process records in ``comfybatch_instances`` -- and
neither of those can grow into a whole-program lock.

Two problems this solves, both of which lose or corrupt work:

1. **Pages drift.** The page used to poll ``/api/status`` and only adopt the
   server's bundle when it had none of its own, so two open pages could hold
   different configs and neither noticed. :class:`StateHub` broadcasts a version
   bump whenever server state changes, which an SSE stream turns into a push.

2. **Two pages overwrite each other.** Both could save presets, apply parameters
   or start a batch, and the last writer silently won. :class:`EditLease` makes
   editing single-writer: one page holds the lease, others are read-only, and any
   page may take over deliberately.

Deliberate scope limit: the lease is enforced only for clients that identify
themselves with a client id. A script or the test suite calling the API without
one is not gated, because the lease exists to stop two *UI pages* from diverging,
not to lock out automation.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: How long a lease survives without a heartbeat. A closed tab stops renewing
#: and the lease frees itself, so a crash cannot lock the UI out permanently.
LEASE_TTL_SECONDS = 45.0

#: How often the SSE stream sends a comment to keep proxies and browsers from
#: dropping an idle connection.
SSE_HEARTBEAT_SECONDS = 15.0


class StateHub:
    """A monotonic version counter plus a wait/notify pair.

    Writers call :meth:`bump` after mutating anything observable; readers (the
    SSE threads) call :meth:`wait` with the version they last sent, so a stream
    only wakes when something actually changed instead of on a timer.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._version = 0
        self._activated_at = 0.0

    @property
    def version(self) -> int:
        with self._condition:
            return self._version

    def bump(self) -> int:
        with self._condition:
            self._version += 1
            self._condition.notify_all()
            return self._version

    def wait(self, last_seen: int, timeout: float = SSE_HEARTBEAT_SECONDS) -> int:
        """Block until the version moves past ``last_seen``, or the timeout."""
        with self._condition:
            if self._version == last_seen:
                self._condition.wait(timeout)
            return self._version

    # --------------------------------------------------------- activation

    def mark_activated(self) -> int:
        """Record that a second launch asked this instance to come forward."""
        with self._condition:
            self._activated_at = time.time()
            self._version += 1
            self._condition.notify_all()
            return self._version

    @property
    def activated_at(self) -> float:
        with self._condition:
            return self._activated_at


@dataclass
class LeaseHolder:
    client_id: str
    label: str = ""
    since: float = 0.0
    renewed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "label": self.label,
            "since": self.since,
            "renewed_at": self.renewed_at,
        }


@dataclass
class EditLease:
    """Single-writer editing lease with a heartbeat and deliberate takeover."""

    ttl: float = LEASE_TTL_SECONDS
    holder: LeaseHolder | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _expired(self, now: float) -> bool:
        return self.holder is not None and (now - self.holder.renewed_at) > self.ttl

    def status(self, client_id: str = "") -> dict[str, Any]:
        with self._lock:
            now = time.time()
            if self._expired(now):
                self.holder = None
            holder = self.holder.to_dict() if self.holder else None
            return {
                "holder": holder,
                "held": holder is not None,
                "mine": bool(holder and client_id and holder["client_id"] == client_id),
                "ttl": self.ttl,
            }

    def claim(self, client_id: str, label: str = "", *, force: bool = False) -> tuple[bool, dict[str, Any]]:
        """Take the lease. ``force`` is the deliberate takeover path.

        Returns ``(granted, status)``; when not granted the caller must not write
        and the status names the current holder so the UI can explain why.
        """
        if not client_id:
            return True, self.status("")
        with self._lock:
            now = time.time()
            if self._expired(now):
                self.holder = None
            if self.holder is not None and self.holder.client_id != client_id and not force:
                return False, self.status(client_id)
            if self.holder is None or self.holder.client_id != client_id:
                self.holder = LeaseHolder(client_id=client_id, label=label, since=now, renewed_at=now)
            else:
                self.holder.renewed_at = now
                if label:
                    self.holder.label = label
            return True, self.status(client_id)

    def renew(self, client_id: str) -> bool:
        with self._lock:
            if self.holder is None or self.holder.client_id != client_id:
                return False
            self.holder.renewed_at = time.time()
            return True

    def release(self, client_id: str) -> bool:
        with self._lock:
            if self.holder is None or self.holder.client_id != client_id:
                return False
            self.holder = None
            return True

"""In-memory per-AP locking, for when the proxy agent is shared (config.yaml's
proxy_agent.host: 0.0.0.0, per README "To let a team share one proxy agent
instance"). Without this, two people hitting the same shared instance could both
start a conversion/rollback against the same AP at (or near) the same time with
neither aware of the other -- this rejects the second attempt with a clear error
naming which AP(s) are already locked and by whom, rather than letting two actions
race against the same hardware.

A stale-lock timeout (not just release-on-completion) matters here specifically
because a crashed or killed request would otherwise leave an AP locked forever with
no way to clear it short of restarting the whole proxy agent.
"""

import threading
import time

_lock = threading.Lock()
_held = {}  # mac -> {"holder": str, "action": str, "acquired_at": float}

STALE_AFTER_SECONDS = 15 * 60  # a single AP action taking longer than this is not expected


class ApLockError(Exception):
    pass


def _is_stale(entry):
    return (time.time() - entry["acquired_at"]) > STALE_AFTER_SECONDS


def acquire(macs, holder, action):
    """Atomically lock every mac in `macs`, or none of them. Raises ApLockError
    naming the conflicting AP(s) and their holder/action if any are already locked
    by someone else (a stale lock is treated as free and silently reclaimed)."""
    with _lock:
        conflicts = []
        for mac in macs:
            entry = _held.get(mac)
            if entry and not _is_stale(entry):
                conflicts.append(f"{mac} (locked by {entry['holder']} for {entry['action']})")
        if conflicts:
            raise ApLockError(f"{len(conflicts)} AP(s) already have an action in progress: {'; '.join(conflicts)}")
        now = time.time()
        for mac in macs:
            _held[mac] = {"holder": holder, "action": action, "acquired_at": now}


def release(macs):
    with _lock:
        for mac in macs:
            _held.pop(mac, None)


class held_for:
    """Context manager: acquire() on enter, release() on exit (success or
    exception) -- guarantees a lock is never left held past the action it guards."""

    def __init__(self, macs, holder, action):
        self.macs = list(macs)
        self.holder = holder
        self.action = action

    def __enter__(self):
        acquire(self.macs, self.holder, self.action)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        release(self.macs)
        return False

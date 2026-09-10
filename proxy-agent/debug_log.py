"""Logging for the proxy agent: what it's doing, always visible in a log file (and the
native log_viewer.py / GUI Debug console), plus an optional verbose trace of every REST
call and SSH command.

Two tiers, sharing one file (proxy_agent.log) and one in-memory ring buffer:
  - event(...):  lifecycle events (connect, convert, rollback, import results) -- always
                 logged, regardless of the `enabled` toggle. This is "what the agent is
                 doing" at a glance.
  - log(...):    verbose per-REST-call / per-SSH-command trace -- only logged when
                 `enabled` is True (toggled from the GUI's Debug console or the tray
                 icon), since it's much noisier.

Every entry carries a `level` (info/success/warning/error) so both the browser Debug
console and the native log_viewer.py window can color-code failures and successes.
"""

import logging
import re
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

LOG_PATH = Path(__file__).parent / "proxy_agent.log"

_MAX_ENTRIES = 1000
_entries = deque(maxlen=_MAX_ENTRIES)
_lock = Lock()
enabled = False

_SENSITIVE_KEYS = re.compile(r"pass(word)?|secret|token|UIDARUBA|authorization", re.IGNORECASE)

_file_logger = logging.getLogger("aos8_10_migrator")
_file_logger.setLevel(logging.INFO)
_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_file_logger.addHandler(_file_handler)
_file_logger.propagate = False

_PY_LEVEL = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
# SUCCESS isn't a real logging level -- tag it in the message text itself so the file
# (and anything tailing it) can still tell success apart from routine info at a glance.
_LEVEL_LABEL = {"info": "INFO", "success": "SUCCESS", "warning": "WARNING", "error": "ERROR"}


def redact(value):
    """Best-effort scrub of sensitive values from a dict/string before logging.

    Dict keys matching a sensitive pattern have their value masked. Strings longer
    than 12 chars that look like a session token (UIDARUBA=... in a query string) get
    the value portion masked too. This is a safety net, not a guarantee -- never pass
    raw credential material into a log call in the first place.
    """
    if isinstance(value, dict):
        return {k: ("***" if _SENSITIVE_KEYS.search(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, str):
        return re.sub(r"(UIDARUBA=)[^&\s]+", r"\1***", value)
    return value


def _record(category, message, level):
    with _lock:
        _entries.append({"ts": time.time(), "category": category, "message": message, "level": level})
    _file_logger.log(_PY_LEVEL.get(level, logging.INFO), f"[{_LEVEL_LABEL.get(level, 'INFO')}] [{category}] {message}")


def event(category, message, level="info"):
    """Always-on lifecycle logging -- connect/convert/rollback/import results, errors.
    Not gated by `enabled`; this is the baseline "what is my agent doing" visibility."""
    _record(category, message, level)


def log(category, message, level="info"):
    """Verbose per-call trace (raw REST calls, SSH commands) -- only recorded when
    debug mode is on, since it's much noisier than event()."""
    if not enabled:
        return
    _record(category, message, level)


def get_entries(since=None):
    with _lock:
        entries = list(_entries)
    if since:
        entries = [e for e in entries if e["ts"] > since]
    return entries


def clear():
    with _lock:
        _entries.clear()

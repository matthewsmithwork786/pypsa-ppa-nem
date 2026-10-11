"""Track in-flight runs and cancel work only after its browser session is gone."""
from __future__ import annotations

import threading
import time
import uuid

# Let backgrounded mobile tabs reconnect while still reclaiming abandoned work.
GRACE_SECONDS = 900.0

_lock = threading.Lock()
_runs: dict[str, dict] = {}


class RunAbandoned(RuntimeError):
    """Raised inside an orphaned run once its grace period expires unclaimed."""


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def register(run_id: str, session_id: str) -> None:
    with _lock:
        _runs[run_id] = {"session_id": session_id, "last_seen": time.monotonic()}


def touch(run_id: str) -> None:
    """Call when a page load sees this run_id in the URL -- reclaims the run."""
    with _lock:
        if run_id in _runs:
            _runs[run_id]["last_seen"] = time.monotonic()


def unregister(run_id: str) -> None:
    with _lock:
        _runs.pop(run_id, None)


def is_abandoned(run_id: str) -> bool:
    """True once the owning session has been gone longer than the grace window
    and nobody has reclaimed the run_id (via touch) in the meantime."""
    try:
        from streamlit.runtime import Runtime
    except ImportError:  # pragma: no cover - only importable inside Streamlit
        return False

    with _lock:
        info = _runs.get(run_id)
    if info is None:
        return False

    try:
        if not Runtime.exists() or Runtime.instance().is_active_session(info["session_id"]):
            with _lock:
                info["last_seen"] = time.monotonic()
            return False
    except Exception:
        # Any failure reading Runtime state fails safe: never cancel work we
        # can't actually confirm is abandoned.
        return False

    with _lock:
        idle = time.monotonic() - info["last_seen"]
    return idle > GRACE_SECONDS

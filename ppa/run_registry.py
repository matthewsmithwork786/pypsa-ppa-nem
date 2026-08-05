"""Tracks in-flight multi-year runs so an abandoned browser tab's worker pool
gets reclaimed promptly, while a genuine page refresh (which reconnects with
the same run_id in the URL, usually within a second or two) is spared.

Streamlit gives no public "on disconnect" callback, so the owning session's
liveness is polled via ``Runtime.is_active_session`` from inside the run loop
itself (see ``ppa.multi_year.run_multi_year``). A dead session starts a grace
timer instead of cancelling immediately; a fresh page load for the same
run_id (``touch``) resets it, so the run only gets cancelled once nobody has
reclaimed it for GRACE_SECONDS -- an actually abandoned tab, not a refresh.
"""
from __future__ import annotations

import threading
import time
import uuid

# Mobile Chrome (and most mobile browsers) suspends a backgrounded tab's
# websocket almost immediately -- switching apps for a minute looks
# identical, from the server's side, to the tab being closed for good. A
# short grace period would cancel perfectly ordinary "I background the app
# while a multi-minute optimisation runs" usage, which is worse than the
# problem this module exists to solve. 15 minutes comfortably covers normal
# task-switching while still reclaiming memory from tabs that are actually
# gone, well before a truly abandoned run's workers would otherwise finish
# and free it on their own.
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

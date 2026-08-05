"""Disk persistence for completed multi-year run results, keyed by run ID.

Streamlit wipes ``st.session_state`` on every full-page reload -- each reload
is a brand-new server session, not a resume of the old one. Saving a finished
run here under its run_id (also stashed in ``st.query_params["run"]``) lets a
fresh session reload the same results and scenario instead of showing an
empty form. It does not help a refresh *during* a run -- see
``ppa.run_registry`` for that half of the problem (keeping an orphaned run
alive long enough to finish, rather than losing the work outright).
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

_STORE_DIR = Path("/tmp/ppa_runs")
_MAX_AGE_SECONDS = 6 * 3600  # stale runs aren't worth reloading; also bounds disk usage


def _path(run_id: str) -> Path:
    return _STORE_DIR / f"{run_id}.pkl"


def save(run_id: str, payload: dict) -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(run_id).with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh)
    tmp.replace(_path(run_id))
    _prune()


def load(run_id: str) -> dict | None:
    p = _path(run_id)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > _MAX_AGE_SECONDS:
        p.unlink(missing_ok=True)
        return None
    with open(p, "rb") as fh:
        return pickle.load(fh)


def _prune() -> None:
    """Best-effort cleanup of runs older than _MAX_AGE_SECONDS."""
    now = time.time()
    for p in _STORE_DIR.glob("*.pkl"):
        try:
            if now - p.stat().st_mtime > _MAX_AGE_SECONDS:
                p.unlink()
        except OSError:
            pass

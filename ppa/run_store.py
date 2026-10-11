"""Persist completed runs so a page reload can restore results by run ID."""
from __future__ import annotations

import pickle
import re
import time
from pathlib import Path

_STORE_DIR = Path("/tmp/ppa_runs")
_MAX_AGE_SECONDS = 6 * 3600  # stale runs aren't worth reloading; also bounds disk usage


def valid_run_id(run_id: object) -> bool:
    """Accept only IDs emitted by run_registry.new_run_id, never URL paths."""
    return isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{12}", run_id) is not None


def _path(run_id: str) -> Path:
    if not valid_run_id(run_id):
        raise ValueError("Invalid run ID")
    return _STORE_DIR / f"{run_id}.pkl"


def _valid_payload(payload: object) -> bool:
    # Reject incomplete or stale payloads before exposing restored results.
    from ppa.scenario import Scenario
    from ppa.financials import MultiYearFinancialResult
    from ppa.results import OptimisationResult

    return (
        isinstance(payload, dict)
        and isinstance(payload.get("scenario"), Scenario)
        and isinstance(payload.get("fin"), MultiYearFinancialResult)
        and isinstance(payload.get("results"), list)
        and bool(payload["results"])
        and all(isinstance(result, OptimisationResult) for result in payload["results"])
    )


def save(run_id: str, payload: dict) -> None:
    # Validate before touching the filesystem.
    path = _path(run_id)
    if not _valid_payload(payload):
        raise ValueError("Invalid completed-run payload")
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh)
    tmp.replace(path)
    _prune()


def load(run_id: str) -> dict | None:
    if not valid_run_id(run_id):
        return None
    p = _path(run_id)
    try:
        if p.is_symlink() or not p.is_file():
            return None
        if time.time() - p.stat().st_mtime > _MAX_AGE_SECONDS:
            p.unlink(missing_ok=True)
            return None
        with open(p, "rb") as fh:
            payload = pickle.load(fh)
        return payload if _valid_payload(payload) else None
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError,
            TypeError, ValueError):
        # Corrupt or stale files behave like an expired run link.
        return None


def _prune() -> None:
    """Best-effort cleanup of runs older than _MAX_AGE_SECONDS."""
    now = time.time()
    for p in _STORE_DIR.glob("*.pkl"):
        try:
            if now - p.stat().st_mtime > _MAX_AGE_SECONDS:
                p.unlink()
        except OSError:
            pass

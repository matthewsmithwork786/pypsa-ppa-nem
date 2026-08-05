"""The sizing subprocess must never hang forever.

A child forked from the multi-threaded Streamlit server can inherit a lock held
by a thread that does not exist in the child and deadlock before it reaches the
solver. Such a child neither exits nor sends a result, so a wait loop with no
timeout spins behind a ticking heartbeat for as long as the user is willing to
stare at it (the field report was 702 s). These tests pin the two guards that
bound that wait.
"""
from __future__ import annotations

import time

import pandas as pd
import pytest

from ppa import sizing
from ppa.scenario import BASE_SCENARIO


def _wedged_worker(conn, ts, scenario_fields):
    """Stand-in for a deadlocked child: alive, silent, burning no CPU."""
    time.sleep(600)


def _crashing_worker(conn, ts, scenario_fields):
    """Stand-in for an OOM kill: exits without sending anything."""
    raise SystemExit(1)


@pytest.fixture
def tiny_ts():
    return pd.DataFrame({"ts_WindGen": [0.1, 0.2], "ts_PVGen": [0.0, 0.3]})


def test_wedged_child_is_killed_and_reported(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_worker", _wedged_worker)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="stopped making progress"):
        sizing.run_sizing_subprocess(
            tiny_ts, BASE_SCENARIO, poll_interval=0.05, stall_timeout=3.0
        )
    elapsed = time.monotonic() - t0
    # Bounded by the stall timeout, not by the child's 600 s sleep.
    assert elapsed < 30, f"stall guard took {elapsed:.1f}s to fire"


def test_hard_timeout_bounds_the_wait(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_worker", _wedged_worker)
    with pytest.raises(RuntimeError, match="was stopped|stopped making progress"):
        sizing.run_sizing_subprocess(
            tiny_ts, BASE_SCENARIO, poll_interval=0.05,
            stall_timeout=1e6, hard_timeout=3.0,
        )


def test_child_that_dies_is_still_reported_as_a_crash(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_worker", _crashing_worker)
    with pytest.raises(RuntimeError, match="died without returning|failed in subprocess"):
        sizing.run_sizing_subprocess(tiny_ts, BASE_SCENARIO, poll_interval=0.05)


def test_cpu_probe_reads_a_live_process():
    """The stall guard is only meaningful if the CPU probe actually works here."""
    import os

    cpu = sizing._process_cpu_seconds(os.getpid())
    assert cpu is not None and cpu >= 0.0

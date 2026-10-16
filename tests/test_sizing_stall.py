"""Sizing workers must complete or be stopped and reaped."""
from __future__ import annotations

import time

import pandas as pd
import pytest

from ppa import sizing
from ppa.scenario import BASE_SCENARIO


def _sleep_command(request, response):
    import sys
    return [sys.executable, "-c", "import time; time.sleep(600)"]


def _crash_command(request, response):
    import sys
    return [sys.executable, "-c", "raise SystemExit(1)"]


@pytest.fixture
def tiny_ts():
    return pd.DataFrame({"ts_WindGen": [0.1, 0.2], "ts_PVGen": [0.0, 0.3]})


def test_wedged_child_is_killed_and_reported(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_command", _sleep_command)
    # Isolate the watchdog from /proc visibility inside PID-isolated runners.
    monkeypatch.setattr(sizing, "_process_cpu_seconds", lambda pid: 0.0)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="stopped making progress"):
        sizing.run_sizing_subprocess(
            tiny_ts, BASE_SCENARIO, poll_interval=0.05, stall_timeout=3.0
        )
    elapsed = time.monotonic() - t0
    # Bounded by the stall timeout, not by the child's 600 s sleep.
    assert elapsed < 30, f"stall guard took {elapsed:.1f}s to fire"


def test_hard_timeout_bounds_the_wait(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_command", _sleep_command)
    monkeypatch.setattr(sizing, "_process_cpu_seconds", lambda pid: 0.0)
    with pytest.raises(RuntimeError, match="was stopped|stopped making progress"):
        sizing.run_sizing_subprocess(
            tiny_ts, BASE_SCENARIO, poll_interval=0.05,
            stall_timeout=1e6, hard_timeout=3.0,
        )


def test_child_that_dies_is_still_reported_as_a_crash(monkeypatch, tiny_ts):
    monkeypatch.setattr(sizing, "_sizing_command", _crash_command)
    with pytest.raises(RuntimeError, match="died without returning|failed in subprocess"):
        sizing.run_sizing_subprocess(tiny_ts, BASE_SCENARIO, poll_interval=0.05)


def test_cpu_probe_reads_a_live_process():
    """The stall guard is only meaningful if the CPU probe actually works here."""
    from pathlib import Path

    # /proc may expose host PIDs while os.getpid() returns a namespace PID.
    pid = int(Path("/proc/self/stat").read_text().split()[0])
    cpu = sizing._process_cpu_seconds(pid)
    assert cpu is not None and cpu >= 0.0


def test_cancel_reaps_worker_and_removes_files(monkeypatch, tiny_ts):
    paths = []
    processes = []
    popen = sizing.subprocess.Popen

    def launch(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc

    def command(request, response):
        paths.append(request.parent)
        return _sleep_command(request, response)

    def cancel():
        raise KeyboardInterrupt

    monkeypatch.setattr(sizing.subprocess, "Popen", launch)
    monkeypatch.setattr(sizing, "_sizing_command", command)
    with pytest.raises(KeyboardInterrupt):
        sizing.run_sizing_subprocess(tiny_ts, BASE_SCENARIO, heartbeat=cancel)
    assert processes[0].poll() is not None
    assert not paths[0].exists()


def test_real_sizing_after_parent_solver_from_background_thread():
    import dataclasses
    from concurrent.futures import ThreadPoolExecutor
    from ppa.network import build_network
    from ppa.solver import solve

    ts = pd.DataFrame({
        "ts_WindGen": [0.5] * 24, "ts_PVGen": [0.0] * 24,
        "ts_MktPrice": [60.0] * 24, "ppaload_mw": [10.0] * 24,
    }, index=pd.date_range("2025-01-01", periods=24, freq="h"))
    scenario = dataclasses.replace(
        BASE_SCENARIO, sizing_method="full_hourly", optimise_capacity=True,
        include_bess=False, max_build_bess_mw=0, simulation_years=1,
    )
    # Reproduce the hosted sequence: native solver initialised in the parent,
    # followed by sizing launched from a thread while other threads remain alive.
    n = build_network(ts, dataclasses.replace(scenario, optimise_capacity=False))
    assert solve(n, scenario, ts)[0] == "ok"
    expected = sizing.optimise_capacities(ts, scenario)
    with ThreadPoolExecutor(max_workers=1) as pool:
        actual = pool.submit(sizing.run_sizing_subprocess, ts, scenario).result(timeout=60)
    assert actual.status == "ok"
    assert actual.onsw_mw == pytest.approx(expected.onsw_mw, abs=0.1)
    assert actual.pv_mw == pytest.approx(expected.pv_mw, abs=0.1)
    assert actual.sizing_delivery_share == pytest.approx(expected.sizing_delivery_share)


def test_worker_returns_model_error(tiny_ts):
    import dataclasses
    scenario = dataclasses.replace(BASE_SCENARIO, sizing_method="full_hourly")
    with pytest.raises(RuntimeError, match="failed in subprocess"):
        sizing.run_sizing_subprocess(tiny_ts, scenario)

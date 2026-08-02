"""Part A (M3/M4): the multi-year runner must not oversubscribe memory, and a
killed worker pool must degrade to the serial path instead of dying silently.

Background: `run_multi_year` parallelises with a *fork* context. Fork is
copy-on-write, but CPython's reference counting writes to the header of nearly
every object a child touches, so each worker ends up costing roughly the
parent's whole RSS. When the parent is still holding the capacity-sizing LP
(hundreds of thousands of rows), N workers multiply that — and the OOM killer
sends SIGKILL with no traceback, so the process just vanishes.
"""
from __future__ import annotations

import pytest

from ppa import multi_year


# ── M3: the per-worker budget accounts for the parent's own footprint ─────────

def test_worker_count_shrinks_when_parent_is_large(monkeypatch):
    """A large parent RSS must reduce the worker count, even with RAM free.

    8 GB available would allow 6 workers at the flat 1200 MB budget, but a
    parent holding 3 GB means each fork costs ~3 GB, so only 2 fit.
    """
    monkeypatch.setattr(multi_year, "_available_memory_mb", lambda: 8000.0)
    monkeypatch.setattr(multi_year, "_usable_cpu_count", lambda: 16)

    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: 200.0)
    small_parent = multi_year._safe_worker_count(requested=8, n_years=8)

    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: 3000.0)
    large_parent = multi_year._safe_worker_count(requested=8, n_years=8)

    assert large_parent < small_parent, (
        f"a 3 GB parent must yield fewer workers than a 200 MB one "
        f"(got {large_parent} vs {small_parent})"
    )
    # (8000 - 800 reserve) // 3000 == 2
    assert large_parent == 2


def test_worker_count_collapses_to_serial_when_memory_is_tight(monkeypatch):
    """Streamlit Community Cloud (~1 GB cgroup) must fall back to serial."""
    monkeypatch.setattr(multi_year, "_available_memory_mb", lambda: 1000.0)
    monkeypatch.setattr(multi_year, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: 700.0)

    assert multi_year._safe_worker_count(requested=8, n_years=15) == 1


def test_worker_count_allows_parallelism_when_memory_is_plentiful(monkeypatch):
    monkeypatch.setattr(multi_year, "_available_memory_mb", lambda: 32_000.0)
    monkeypatch.setattr(multi_year, "_usable_cpu_count", lambda: 16)
    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: 300.0)

    assert multi_year._safe_worker_count(requested=8, n_years=8) == 8


def test_worker_count_never_exceeds_years_or_cpus(monkeypatch):
    monkeypatch.setattr(multi_year, "_available_memory_mb", lambda: 64_000.0)
    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: 100.0)
    monkeypatch.setattr(multi_year, "_usable_cpu_count", lambda: 4)

    assert multi_year._safe_worker_count(requested=99, n_years=3) == 3
    assert multi_year._safe_worker_count(requested=99, n_years=99) == 4


def test_worker_count_survives_unreadable_parent_rss(monkeypatch):
    """/proc/self/statm is Linux-only; a None reading must not crash."""
    monkeypatch.setattr(multi_year, "_available_memory_mb", lambda: 8000.0)
    monkeypatch.setattr(multi_year, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(multi_year, "_parent_rss_mb", lambda: None)

    workers = multi_year._safe_worker_count(requested=8, n_years=8)
    assert workers >= 1
    # Falls back to the flat budget: (8000 - 800) // 1200 == 6
    assert workers == 6


def test_parent_rss_is_plausible_or_none():
    """The real reader returns a sane value on this platform (or None)."""
    rss = multi_year._parent_rss_mb()
    if rss is not None:
        assert 1.0 < rss < 1_000_000.0, f"implausible parent RSS: {rss} MB"


# ── M4: a killed pool degrades to serial rather than propagating ──────────────

def test_broken_process_pool_falls_back_to_serial(monkeypatch):
    """A SIGKILLed worker (OOM) must be recovered, not raised to the user.

    Forces `_safe_worker_count` to request parallelism, makes the executor raise
    BrokenProcessPool the way an OOM-killed pool does, and asserts every year is
    still solved via the in-process serial path.
    """
    from concurrent.futures.process import BrokenProcessPool

    n_years = 3
    monkeypatch.setattr(multi_year, "_safe_worker_count", lambda requested, n_years: 2)

    class _ExplodingExecutor:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, *a, **kw):
            raise BrokenProcessPool("simulated OOM kill")

    monkeypatch.setattr(multi_year, "ProcessPoolExecutor", _ExplodingExecutor)

    solved: list[int] = []

    def _fake_solve_one_year(idx, sim_year, ts, scenario_dict):
        solved.append(idx)
        return idx, f"result-{idx}"

    monkeypatch.setattr(multi_year, "_solve_one_year", _fake_solve_one_year)

    results = _run_with_stubs(monkeypatch, n_years)

    assert solved == [0, 1, 2], "serial fallback must solve every year"
    assert results == ["result-0", "result-1", "result-2"]


def _run_with_stubs(monkeypatch, n_years: int):
    """Drive run_multi_year with the timeseries/scenario prep stubbed out."""
    import pandas as pd

    from ppa.scenario import Scenario

    scenario = Scenario(name="mem-test", simulation_years=n_years)

    idx = pd.date_range("2025-01-01", periods=24, freq="h")
    frame = pd.DataFrame(
        {
            "ts_PVGen": 0.5,
            "ts_WindGen": 0.5,
            "ts_MktPrice": 50.0,
            "ppaload_mw": 10.0,
        },
        index=idx,
    )
    monkeypatch.setattr(multi_year, "build_year_timeseries", lambda **kw: frame)
    monkeypatch.setattr(multi_year, "pick_weather_year", lambda i, years: years[0])

    series = pd.Series(0.5, index=idx)
    return multi_year.run_multi_year(
        scenario=scenario,
        pv_cf_by_year={2025: series},
        wind_cf_by_year={2025: series},
        prices_by_year={2025: pd.Series(50.0, index=idx)},
        first_sim_year=2025,
        max_workers=2,
    )

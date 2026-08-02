"""Multi-year parallel simulation runner."""
from __future__ import annotations

import dataclasses
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from typing import Callable

import pandas as pd

from ppa.data.timeseries_utils import build_year_timeseries, pick_weather_year
from ppa.network import build_network
from ppa.results import OptimisationResult, extract_results
from ppa.scenario import Scenario
from ppa.solver import solve

import streamlit as st

# Peak RSS of a single full-year EU solve, measured at ~735 MB with io_api="direct".
# Each parallel worker is its own process and pays this in full, so we budget one
# worker per this much *available* RAM. Override via PPA_WORKER_MEM_MB for other
# model sizes.
_PER_WORKER_MEM_MB = int(os.environ.get("PPA_WORKER_MEM_MB", "1200"))

# Headroom left for the OS, the Streamlit process and allocator slack. Without a
# reserve the pool sizes right up to MemAvailable and the OOM killer arrives.
_RESERVE_MEM_MB = int(os.environ.get("PPA_RESERVE_MEM_MB", "800"))


def _available_memory_mb() -> float | None:
    """Best-effort RAM headroom in MB, honouring cgroup limits (containers).

    Returns the min of the cgroup memory headroom and host MemAvailable, or None
    if nothing could be read. Streamlit Community Cloud caps memory via cgroups at
    ~1 GB, well below the host's reported free memory, so cgroup awareness is what
    makes the cloud fall back to serial.
    """
    candidates: list[float] = []

    # cgroup v2 (Streamlit Cloud, most modern containers)
    try:
        with open("/sys/fs/cgroup/memory.max") as fh:
            raw = fh.read().strip()
        if raw != "max":
            limit = int(raw)
            with open("/sys/fs/cgroup/memory.current") as fh:
                used = int(fh.read().strip())
            candidates.append((limit - used) / 1024 / 1024)
    except (OSError, ValueError):
        pass

    # cgroup v1 fallback
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as fh:
            limit = int(fh.read().strip())
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as fh:
            used = int(fh.read().strip())
        if limit < (1 << 62):  # sentinel "unlimited" values are huge
            candidates.append((limit - used) / 1024 / 1024)
    except (OSError, ValueError):
        pass

    # Host-level available memory
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    candidates.append(int(line.split()[1]) / 1024)  # kB → MB
                    break
    except OSError:
        pass

    return min(candidates) if candidates else None


def _parent_rss_mb() -> float | None:
    """Resident set size of *this* process in MB, or None if unreadable."""
    try:
        with open("/proc/self/statm") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError, AttributeError):  # pragma: no cover
        return None


def _usable_cpu_count() -> int:
    """CPU count honouring cgroup/affinity limits, falling back to os.cpu_count()."""
    try:
        return max(1, len(os.sched_getaffinity(0)))  # respects cpuset affinity
    except AttributeError:  # pragma: no cover - non-Linux
        return max(1, os.cpu_count() or 1)


def _safe_worker_count(requested: int, n_years: int) -> int:
    """Clamp the requested worker count to what this machine can actually run.

    Bounded by: years to solve, usable CPUs, and (crucially) available RAM. On a
    memory-constrained host (e.g. Streamlit Community Cloud) this collapses to 1,
    forcing the memory-safe serial path.

    The per-worker budget is the larger of `_PER_WORKER_MEM_MB` (a solve's own
    peak) and **the parent's current RSS**. The parent term matters because the
    pool forks: fork is copy-on-write, but CPython's reference counting writes to
    the header of nearly every object a child touches, so each worker ends up
    paying roughly the parent's whole footprint. A parent still holding something
    large — most importantly the capacity-sizing LP, which is hundreds of
    thousands of rows — therefore multiplies that cost by the worker count. A
    flat per-worker constant misses this entirely and oversubscribes into the
    OOM killer.
    """
    workers = max(1, min(requested, n_years, _usable_cpu_count()))

    mem_mb = _available_memory_mb()
    if mem_mb is not None:
        per_worker = max(_PER_WORKER_MEM_MB, _parent_rss_mb() or 0.0)
        usable_mb = max(0.0, mem_mb - _RESERVE_MEM_MB)
        mem_cap = max(1, int(usable_mb // per_worker))
        workers = min(workers, mem_cap)
    return workers


def _degraded_scenario(scenario: Scenario, year_idx: int) -> Scenario:
    """
    Return a copy of `scenario` with technology degradation applied for simulation year `year_idx`.

    Wind/solar degradation scales the effective CF via p_nom reduction; BESS degradation
    reduces usable energy capacity.
    Even the first year has on average already half a degradation to consider.
    """
    pv_factor = (1.0 - scenario.pv_degradation_rate) ** (year_idx+0.5)
    wind_factor = (1.0 - scenario.wind_degradation_rate) ** (year_idx+0.5)
    bess_factor = (1.0 - scenario.bess_degradation_rate) ** (year_idx+0.5)

    return dataclasses.replace(
        scenario,
        pv_mw=scenario.pv_mw * pv_factor,
        onsw_mw=scenario.onsw_mw * wind_factor,
        bess_mwh=scenario.bess_mwh * bess_factor,
    )


def _solve_one_year(
    sim_year_idx: int,
    sim_year: int,
    ts: pd.DataFrame,
    scenario_fields: dict,
) -> tuple[int, OptimisationResult]:
    """Solve a single year's LP. Returns (sim_year_idx, result).

    Takes the scenario as a plain dict, not a Scenario instance, and rebuilds it
    here. Sending a Scenario across the process boundary pickles the class *by
    reference*; if Streamlit's file watcher has reloaded ppa.scenario, the stale
    class held by a session_state Scenario no longer matches sys.modules and
    pickling dies with "it is not the same object as ppa.scenario.Scenario". A
    dict is a builtin type with no such identity check; rebuilding from the
    module-level Scenario class sidesteps the whole problem.
    """
    scenario = Scenario(**scenario_fields)
    n = build_network(ts, scenario)
    status, condition = solve(n, scenario, ts)
    result = extract_results(n, scenario, ts, status, condition)
    return sim_year_idx, result


def run_multi_year(
    scenario: Scenario,
    pv_cf_by_year: dict[int, pd.Series],
    wind_cf_by_year: dict[int, pd.Series],
    prices_by_year: dict[int, pd.Series],
    load_mw_by_year: dict[int, pd.Series] | None = None,
    first_sim_year: int = 2025,
    max_workers: int = 4,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> list[OptimisationResult]:
    """
    Run `scenario.simulation_years` independent year-simulations in parallel.

    Weather years (CF + prices) are cycled from the available historical keys.
    Using the same historical year for both CF and prices preserves correlations
    (e.g. 2021: high prices + low wind).  Prices are then escalated from that
    historical base year to the simulation year via `scenario.price_escalation_rate`.
    Technology degradation is applied per-year via `scenario.*_degradation_rate`.
    """
    if scenario.optimise_capacity:
        raise ValueError(
            "run_multi_year received a scenario with optimise_capacity=True — "
            "the dispatch simulation needs fixed capacities. Run the sizing LP "
            "first (ppa.sizing.optimise_capacities) and pass the scenario "
            "returned by ppa.sizing.apply_sizing."
        )

    n_years = scenario.simulation_years
    available_weather_years = sorted(pv_cf_by_year.keys())
    available_price_years = sorted(prices_by_year.keys())
    available_load_years = sorted(load_mw_by_year) if load_mw_by_year else []

    # Pre-build all timeseries and per-year scenarios on the main thread
    timeseries_by_idx: dict[int, pd.DataFrame] = {}
    scenario_by_idx: dict[int, Scenario] = {}
    for idx in range(n_years):
        sim_year = first_sim_year + idx
        weather_year = pick_weather_year(idx, available_weather_years)
        # Cycle price years independently if they don't fully overlap with CF years
        price_year = pick_weather_year(idx, available_price_years)
        # Load overrides (custom CSV) are not degraded -- only generation/BESS are.
        degraded = _degraded_scenario(scenario, idx)
        load_kw = (
            {weather_year: load_mw_by_year[pick_weather_year(idx, available_load_years)]}
            if load_mw_by_year else None
        )
        ts = build_year_timeseries(
            sim_year=sim_year,
            weather_year=weather_year,
            ppa_load_mw=degraded.ppaload_mw,
            pv_cf_by_year=pv_cf_by_year,
            wind_cf_by_year=wind_cf_by_year,
            prices_by_year={weather_year: prices_by_year[price_year]},
            price_escalation_rate=scenario.price_escalation_rate,
            load_profile=scenario.load_profile,
            load_mw_by_year=load_kw,
        )
        timeseries_by_idx[idx] = ts
        scenario_by_idx[idx] = degraded

    results: list[OptimisationResult | None] = [None] * n_years
    completed = 0

    def _record(year_idx: int, result: OptimisationResult) -> None:
        nonlocal completed
        results[year_idx] = result
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, n_years, first_sim_year + year_idx)

    workers = _safe_worker_count(max_workers, n_years)
    st.caption(f"Based on available RAM running {n_years} year-simulations with {workers} parallel worker(s) ...")

    def _run_serial() -> None:
        # Serial, in-process. Required on memory-constrained hosts (e.g. Streamlit
        # Community Cloud, ~1 GB): a single solve peaks ~735 MB, so two would not
        # fit and even one *forked* worker would cost parent + child RAM at once
        # and OOM (the "Oh no. Error running app." crash). Running in-process
        # reuses the parent's memory, and single-threaded execution has no
        # shared-heap corruption. Also the recovery path when forked workers are
        # killed mid-run (see BrokenProcessPool below).
        for idx in range(n_years):
            if results[idx] is not None:
                continue  # already solved before the pool died
            year_idx, result = _solve_one_year(
                idx,
                first_sim_year + idx,
                timeseries_by_idx[idx],
                dataclasses.asdict(scenario_by_idx[idx]),
            )
            _record(year_idx, result)

    if workers <= 1:
        _run_serial()
        return results  # type: ignore[return-value]

    # ProcessPoolExecutor, not threads: PyPSA/linopy/HiGHS run non-thread-safe C
    # extensions (model build via pandas/xarray, then the HiGHS solver). Running
    # them concurrently in one process corrupts the shared heap — manifesting as
    # `free(): invalid next size` core dumps and stray ArrowStringArray errors.
    # Separate processes = separate heaps = safe true parallelism. The years are
    # independent; the scenario crosses as a plain dict (see _solve_one_year) and
    # the DataFrame/OptimisationResult pickle cleanly.
    #
    # "fork" specifically: spawn/forkserver re-import the __main__ module, which
    # blows up under Streamlit (it runs the app script as __main__, so each worker
    # would re-execute the whole app). fork inherits the interpreter as-is and
    # still isolates each solve in its own process/heap. Windows has no fork, so
    # fall back to spawn there (requires a `if __name__ == "__main__"` guard).
    try:
        mp_context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - Windows only
        mp_context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp_context) as executor:
            futures = {
                executor.submit(
                    _solve_one_year,
                    idx,
                    first_sim_year + idx,
                    timeseries_by_idx[idx],
                    dataclasses.asdict(scenario_by_idx[idx]),
                ): idx
                for idx in range(n_years)
            }

            for future in as_completed(futures):
                year_idx, result = future.result()  # propagates exceptions
                _record(year_idx, result)
    except BrokenProcessPool:
        # A worker was killed outright — almost always the OOM killer, which
        # sends SIGKILL with no traceback and no exit code. Forking is what
        # makes this likely: fork is copy-on-write, but CPython refcounting
        # dirties nearly every page a child touches, so each worker ends up
        # costing roughly the parent's own RSS. If a big object (e.g. the
        # capacity-sizing LP) is still resident in the parent, N workers
        # multiply it N times.
        #
        # Recover rather than die: the serial path reuses the parent's memory
        # and re-solves only the years that never came back.
        remaining = sum(1 for r in results if r is None)
        st.warning(
            f"Parallel workers were killed (most likely out of memory) after "
            f"{n_years - remaining} of {n_years} year(s). Falling back to the "
            f"serial path for the remaining {remaining} — slower, but it uses "
            f"about 1/{workers} of the memory."
        )
        _run_serial()

    return results  # type: ignore[return-value]

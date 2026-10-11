"""Multi-year parallel simulation runner."""
from __future__ import annotations

import dataclasses
import gc
import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Callable

import pandas as pd

from ppa import run_registry
from ppa.data.timeseries_utils import build_year_timeseries, pick_weather_year
from ppa.network import build_network
from ppa.results import OptimisationResult, extract_results
from ppa.scenario import Scenario
from ppa.solver import solve

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

# Per-worker memory budget; override for larger or smaller model sizes.
_PER_WORKER_MEM_MB = int(os.environ.get("PPA_WORKER_MEM_MB", "1200"))

# Reserve memory for the OS, Streamlit and allocator overhead.
_RESERVE_MEM_MB = int(os.environ.get("PPA_RESERVE_MEM_MB", "800"))


def _available_memory_mb() -> float | None:
    """Best-effort available RAM in MB, respecting container limits."""
    candidates: list[float] = []

    # cgroup v2
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

    # cgroup v1
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as fh:
            limit = int(fh.read().strip())
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as fh:
            used = int(fh.read().strip())
        if limit < (1 << 62):  # sentinel "unlimited" values are huge
            candidates.append((limit - used) / 1024 / 1024)
    except (OSError, ValueError):
        pass

    # Host memory
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
    """Clamp workers by years, usable CPUs and available memory."""
    workers, _ = _worker_count_with_reason(requested, n_years)
    return workers


def _worker_count_with_reason(requested: int, n_years: int) -> tuple[int, str]:
    """Return the safe worker count and a reason if it is reduced."""
    workers = max(1, min(requested, n_years, _usable_cpu_count()))
    reason = ""
    if requested > n_years and n_years < _usable_cpu_count():
        reason = f"only {n_years} year(s) to solve"
    elif workers < requested:
        reason = f"only {_usable_cpu_count()} usable CPU(s)"

    mem_mb = _available_memory_mb()
    if mem_mb is not None:
        per_worker = max(_PER_WORKER_MEM_MB, _parent_rss_mb() or 0.0)
        usable_mb = max(0.0, mem_mb - _RESERVE_MEM_MB)
        mem_cap = max(1, int(usable_mb // per_worker))
        if mem_cap < workers:
            reason = (
                f"only ~{mem_mb / 1024:.1f} GB of memory is available and each "
                f"parallel solve needs ~{per_worker / 1024:.1f} GB "
                f"(plus a {_RESERVE_MEM_MB / 1024:.1f} GB reserve)"
            )
        workers = min(workers, mem_cap)
    return workers, reason


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


def _release_free_memory() -> None:
    """Return free glibc heap arenas to the OS when supported."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - best effort only
        pass


def _solve_one_year(
    sim_year_idx: int,
    sim_year: int,
    ts: pd.DataFrame,
    scenario_fields: dict,
    resolution_h: float = 1.0,
) -> tuple[int, OptimisationResult]:
    """Solve one year and return its index and result.

    Pass scenario fields as a dict so Streamlit hot reloads cannot leave workers
    holding a stale pickled `Scenario` class. `resolution_h` is explicit so
    network construction and result aggregation use the actual snapshot hours.
    """
    scenario = Scenario(**scenario_fields)
    n = build_network(ts, scenario, resolution_h=resolution_h)
    status, condition = solve(n, scenario, ts)
    result = extract_results(n, scenario, ts, status, condition, resolution_h=resolution_h)

    # Release the network and solver model before the next serial solve. The
    # extracted result owns its data and does not reference the network.
    # `Network.model` is read-only, so clear its private backing attribute when
    # available; cleanup must never fail the solve.
    try:
        if hasattr(n, "_model"):
            n._model = None
    except Exception:  # noqa: BLE001 - never fail a solve over cleanup
        pass
    del n
    gc.collect()
    _release_free_memory()
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
    run_id: str | None = None,
    resolution_h: float = 1.0,
) -> list[OptimisationResult]:
    """
    Run independent annual simulations, using parallel workers when safe.

    Weather years (CF + prices) are cycled from the available historical keys.
    Using the same historical year for both CF and prices preserves correlations
    (e.g. 2021: high prices + low wind).  Prices are then escalated from that
    historical base year to the simulation year via `scenario.price_escalation_rate`.
    Technology degradation is applied per-year via `scenario.*_degradation_rate`.

    `run_id` enables cancellation after the associated browser session is
    abandoned, while allowing a reconnect during the registry grace period.
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

    # Hours per snapshot (e.g. 0.5 at 30 min) → minutes, for build_year_timeseries.
    resolution_minutes = max(1, int(round(60.0 * resolution_h)))

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
            resolution_minutes=resolution_minutes,
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

    workers, reason = _worker_count_with_reason(max_workers, n_years)
    if workers < max_workers and reason:
        st.caption(
            f"Running {n_years} year-simulations with **{workers}** parallel "
            f"worker(s), not the {max_workers} selected: {reason}. "
            + ("Years are solved one after another, so this takes longer than a "
               "parallel run would." if workers == 1 else
               "Running more would risk the run being killed for exceeding memory.")
        )
    else:
        st.caption(
            f"Running {n_years} year-simulations with {workers} parallel worker(s)."
        )

    def _run_serial() -> None:
        # Serial execution avoids parent-plus-child memory costs on constrained
        # hosts and recovers years left unfinished after a worker-pool failure.
        for idx in range(n_years):
            if results[idx] is not None:
                continue  # already solved before the pool died
            # Keep the default call shape for hourly solves; pass non-hourly
            # resolution explicitly.
            args = (
                idx,
                first_sim_year + idx,
                timeseries_by_idx[idx],
                dataclasses.asdict(scenario_by_idx[idx]),
            )
            if resolution_h != 1.0:
                args += (resolution_h,)
            year_idx, result = _solve_one_year(*args)
            _record(year_idx, result)

    if workers <= 1:
        _run_serial()
        return results  # type: ignore[return-value]

    # Use processes because PyPSA/linopy/HiGHS extensions are not safe to run
    # concurrently in threads. Fork avoids re-running Streamlit's __main__ app;
    # platforms without fork use spawn.
    try:
        mp_context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - Windows only
        mp_context = multiprocessing.get_context("spawn")

    session_id = None
    if run_id:
        ctx = get_script_run_ctx()
        session_id = ctx.session_id if ctx else None
        if session_id:
            run_registry.register(run_id, session_id)

    try:
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=mp_context) as executor:
                futures = {
                    executor.submit(
                        _solve_one_year,
                        idx,
                        first_sim_year + idx,
                        timeseries_by_idx[idx],
                        dataclasses.asdict(scenario_by_idx[idx]),
                        resolution_h,
                    ): idx
                    for idx in range(n_years)
                }

                # Poll periodically so abandoned browser runs can be cancelled.
                pending = set(futures)
                while pending:
                    done, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
                    for future in done:
                        year_idx, result = future.result()  # propagates exceptions
                        _record(year_idx, result)
                    if session_id and pending and run_registry.is_abandoned(run_id):
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise run_registry.RunAbandoned(
                            f"Run {run_id} abandoned (browser gone, not reclaimed within "
                            f"{run_registry.GRACE_SECONDS:.0f}s) — cancelling to free memory."
                        )
        except BrokenProcessPool:
            # A worker may have been killed for memory use. Resume unfinished
            # years serially to reduce peak memory.
            remaining = sum(1 for r in results if r is None)
            st.warning(
                f"Parallel workers were killed (most likely out of memory) after "
                f"{n_years - remaining} of {n_years} year(s). Falling back to the "
                f"serial path for the remaining {remaining} — slower, but it uses "
                f"about 1/{workers} of the memory."
            )
            _run_serial()
    finally:
        if run_id:
            run_registry.unregister(run_id)

    return results  # type: ignore[return-value]

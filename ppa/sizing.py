"""Capacity co-optimisation: size wind/PV/BESS with a single multi-year investment LP.

Two-stage flow: the sizing LP here optimises capacities + dispatch over the
concatenated simulation horizon (least-cost-to-serve-the-PPA, see
`ppa.network.build_network` sizing mode) at a coarse, configurable time
resolution (`scenario.sizing_resolution_h`, default 3h), then `apply_sizing`
writes the optimal capacities back into a fixed-capacity Scenario that the
existing per-year *hourly* simulation (`ppa.multi_year.run_multi_year`) and
financials consume unchanged.
"""
from __future__ import annotations

import dataclasses
import math
import multiprocessing
import traceback
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from ppa.data.timeseries_utils import build_year_timeseries, pick_weather_year
from ppa.multi_year import _available_memory_mb, _PER_WORKER_MEM_MB
from ppa.network import build_network
from ppa.scenario import Scenario
from ppa.solver import solve


@dataclass
class SizedCapacities:
    onsw_mw: float
    pv_mw: float
    bess_mw: float
    bess_mwh: float
    status: str
    condition: str
    sizing_years_used: int
    horizon_clamped: bool
    resolution_h: int = 1
    # Sizing representation used ("tsam" / "full_hourly" / "coarse")
    sizing_method: str = "coarse"
    # PPA delivery share the sizing LP itself achieves on its representation
    # (clustered typical days / coarse blocks / full hourly) — compared against
    # the full hourly simulation of the sized portfolio to catch clustering
    # losses (plan W14 item 6).
    sizing_delivery_share: float = 0.0
    # Sized connection/transport link MW (carried into dispatch via apply_sizing)
    wind_link_mw: float = 0.0
    pvbess_link_mw: float = 0.0
    sell_link_mw: float = 0.0
    # Which caps bind at the optimum (for the diagnostics expander)
    wind_cap_binding: bool = False
    pv_cap_binding: bool = False
    bess_cap_binding: bool = False
    wind_link_binding: bool = False
    pvbess_link_binding: bool = False
    sell_link_binding: bool = False


def weather_cycle_years(
    requested_years: int, n_weather_years: int, n_price_years: int
) -> tuple[int, str | None]:
    """Cap the sizing horizon at one full cycle of the historical input years.

    The simulation cycles CF and price years from the cached historical sets
    (`pick_weather_year`), so beyond one least-common-multiple cycle the sizing
    LP re-solves near-copies of the same profiles (only slow degradation /
    price-escalation drift differs). Capping there keeps all weather diversity
    at a fraction of the LP size. Returns (capped_years, note) with a
    human-readable note when the cap bites (None otherwise).
    """
    requested_years = max(1, int(requested_years))
    cycle = math.lcm(max(1, int(n_weather_years)), max(1, int(n_price_years)))
    if cycle >= requested_years:
        return requested_years, None

    note = (
        f"Sizing LP horizon set to {cycle} year(s) — one full cycle of the "
        f"{n_weather_years} cached weather year(s) and {n_price_years} price "
        f"year(s). Later years repeat the same profiles, so a "
        f"{requested_years}-year sizing LP would add cost but almost no new "
        "information. The full simulation still runs all "
        f"{requested_years} year(s) hourly with the sized capacities."
    )
    return cycle, note


# Peak RSS of the sizing LP by representation, measured with
# scripts/measure_peak_rss.py on the Corporate PPA case, 1-year horizon
# (baseline after data load ~355 MB is included in these figures):
#
#     full_hourly   1,143 MB   171 s
#     coarse (3 h)    778 MB    10 s
#     tsam                small (the LP is ~25x smaller)
#
# Used to warn BEFORE a solve that would exhaust a memory-limited container,
# because the failure mode otherwise is a silent SIGKILL with no traceback.
SIZING_PEAK_MB = {"full_hourly": 1150.0, "coarse": 800.0, "tsam": 400.0}


def sizing_memory_advice(scenario: Scenario) -> "str | None":
    """Warn when the chosen sizing representation will not fit in RAM.

    Returns a human-readable message, or None when there is enough headroom
    (or memory cannot be read). Deliberately advisory rather than automatic:
    silently switching representation would change the sized fleet without the
    user knowing, and the methods do not agree (docs/sizing_experiments.md E2).
    """
    mem_mb = _available_memory_mb()
    if mem_mb is None:
        return None
    method = getattr(scenario, "sizing_method", "full_hourly")
    needed = SIZING_PEAK_MB.get(method)
    if needed is None or mem_mb >= needed:
        return None

    cheaper = [m for m in ("coarse", "tsam") if SIZING_PEAK_MB[m] <= mem_mb]
    suggestion = (
        f" Switch the sizing representation to "
        f"{' or '.join('Coarse resolution' if m == 'coarse' else 'Typical days' for m in cheaper)}"
        ", or reduce the max-build caps and simulation years."
        if cheaper else
        " Reduce the max-build caps and the simulation years, or run this locally."
    )
    return (
        f"Only ~{mem_mb / 1024:.1f} GB of memory is available, but the "
        f"'{method}' sizing LP peaked at ~{needed / 1024:.1f} GB when measured. "
        f"The solve may be killed by the OS (which happens without a traceback)."
        + suggestion
    )


def clamp_sizing_years(requested_years: int, resolution_h: float = 1.0) -> tuple[int, str | None]:
    """Clamp the sizing-LP horizon to what fits in available RAM.

    A single-year *hourly* solve peaks ~`_PER_WORKER_MEM_MB` MB and linopy LP
    memory grows roughly linearly with snapshots, so a year at `resolution_h`
    hours per snapshot costs ~that much / resolution_h. We budget one year-block
    per that much available memory. Returns (clamped_years, notice) where notice
    is a human-readable message when clamping occurred (None otherwise).
    """
    requested_years = max(1, int(requested_years))
    mem_mb = _available_memory_mb()
    if mem_mb is None:
        return requested_years, None

    per_year_mem_mb = _PER_WORKER_MEM_MB / max(1.0, float(resolution_h))
    fit_years = max(1, int(mem_mb // per_year_mem_mb))
    if fit_years >= requested_years:
        return requested_years, None

    notice = (
        f"Sizing LP horizon reduced from {requested_years} to {fit_years} year(s) "
        f"to fit available memory (~{mem_mb / 1024:.1f} GB free, "
        f"~{per_year_mem_mb / 1024:.1f} GB per simulated year at "
        f"{resolution_h:.0f}h resolution). "
        "Optimised capacities are sized on the reduced horizon; the full "
        f"{requested_years}-year simulation still runs with those capacities."
    )
    return fit_years, notice


def build_sizing_timeseries(
    scenario: Scenario,
    pv_cf_by_year: dict[int, pd.Series],
    wind_cf_by_year: dict[int, pd.Series],
    prices_by_year: dict[int, pd.Series],
    n_sizing_years: int,
    load_mw_by_year: dict[int, pd.Series] | None = None,
) -> pd.DataFrame:
    """Concatenate per-year timeseries into one sizing-LP horizon.

    Reuses `build_year_timeseries` per simulation year, so weather-year cycling
    and price escalation match the per-year simulation exactly. Wind/PV
    degradation is baked into the CF columns per year (mirrors
    `ppa.multi_year._degraded_scenario`, which scales p_nom instead — equivalent
    for the LP since p_nom × p_max_pu bounds output either way).
    """
    available_weather_years = sorted(pv_cf_by_year.keys())
    available_price_years = sorted(prices_by_year.keys())
    available_load_years = sorted(load_mw_by_year) if load_mw_by_year else []

    frames: list[pd.DataFrame] = []
    for idx in range(n_sizing_years):
        sim_year = scenario.first_sim_year + idx
        weather_year = pick_weather_year(idx, available_weather_years)
        price_year = pick_weather_year(idx, available_price_years)
        load_kw = (
            {weather_year: load_mw_by_year[pick_weather_year(idx, available_load_years)]}
            if load_mw_by_year else None
        )
        ts = build_year_timeseries(
            sim_year=sim_year,
            weather_year=weather_year,
            ppa_load_mw=scenario.ppaload_mw,
            pv_cf_by_year=pv_cf_by_year,
            wind_cf_by_year=wind_cf_by_year,
            # Same remap as run_multi_year: build_year_timeseries looks prices up
            # by weather_year, so alias the cycled price year under that key.
            prices_by_year={weather_year: prices_by_year[price_year]},
            price_escalation_rate=scenario.price_escalation_rate,
            load_profile=scenario.load_profile,
            load_mw_by_year=load_kw,
        )
        # Bake technology degradation into the capacity factors for this year
        ts["ts_PVGen"] = ts["ts_PVGen"] * (1.0 - scenario.pv_degradation_rate) ** idx
        ts["ts_WindGen"] = ts["ts_WindGen"] * (1.0 - scenario.wind_degradation_rate) ** idx
        frames.append(ts)

    sizing_ts = pd.concat(frames)
    sizing_ts.index.name = "snapshot"
    return sizing_ts


def coarsen_timeseries(ts: pd.DataFrame, resolution_h: int) -> pd.DataFrame:
    """Downsample an hourly timeseries to `resolution_h`-hour block averages.

    Block-averaging CFs, prices and load preserves per-block energy and cost
    exactly; only intra-block variability (which the sizing LP doesn't need at
    full fidelity) is smoothed. Bins align to midnight, and year blocks are
    whole multiples of common resolutions, so no bin straddles a year boundary.
    """
    if resolution_h <= 1:
        return ts
    coarse = ts.resample(f"{resolution_h}h").mean()
    coarse.index.name = ts.index.name
    return coarse


def optimise_capacities(ts: pd.DataFrame, scenario: Scenario) -> SizedCapacities:
    """Solve the investment LP at coarse resolution and extract optimal capacities.

    `ts` is the hourly timeseries. How it is represented before the solve is
    chosen by `scenario.sizing_method`: "tsam" clusters it into typical days at
    hourly resolution (best fidelity for the size; W14), "full_hourly" keeps the
    exact hourly year (slowest), and "coarse" block-averages to
    `scenario.sizing_resolution_h`-hour blocks (legacy, fastest per snapshot).
    Snapshot weightings (set in `build_network`) keep costs and storage
    dynamics in real hours either way.

    BESS energy capacity fade cannot be time-varied on a StorageUnit, so the
    horizon-average degradation factor is applied to the fixed duration — a
    slight de-rating that approximates multi-year usable-capacity fade.
    """
    resolution_h = max(1, int(scenario.sizing_resolution_h))
    method = scenario.sizing_method
    if method == "tsam":
        from ppa.sizing_tsam import cluster_typical_periods

        ts, weights = cluster_typical_periods(
            ts, n_periods=max(4, int(scenario.sizing_n_periods))
        )
        n_years = max(1, round(float(weights.sum()) / 8760))
        # Report the effective (clustered) resolution for diagnostics
        resolution_h = 1
    elif method == "full_hourly":
        n_years = max(1, round(len(ts) / 8760))
        resolution_h = 1
    else:  # coarse (legacy)
        ts = coarsen_timeseries(ts, resolution_h)
        n_years = max(1, round(len(ts) * resolution_h / 8760))

    avg_bess_factor = (
        sum((1.0 - scenario.bess_degradation_rate) ** i for i in range(n_years)) / n_years
    )

    sizing_scn = dataclasses.replace(
        scenario,
        optimise_capacity=True,
        include_bess=scenario.include_bess and scenario.max_build_bess_mw > 0,
        # Fixed duration for the sizing LP, de-rated for average degradation.
        # bess_max_hours reads bess_mwh/bess_mw, so encode via a 1 MW reference.
        bess_mw=1.0,
        bess_mwh=scenario.bess_max_hours * avg_bess_factor,
        # The LP prices BESS capex as A$/kWh × max_hours; compensate the de-rated
        # hours so capex is still charged on the *nameplate* energy.
        bess_capex_per_kwh=scenario.bess_capex_per_kwh / avg_bess_factor,
    )
    if not sizing_scn.include_bess:
        sizing_scn = dataclasses.replace(sizing_scn, max_build_bess_mw=0.0)

    if method == "tsam":
        # intra_period_hours=1.0 (the default): the occurrence weight is NOT the
        # elapsed time between snapshots, and conflating them sizes storage to
        # zero (docs/sizing_experiments.md E9).
        n = build_network(ts, sizing_scn, snapshot_weightings=weights)
    elif method == "full_hourly":
        n = build_network(ts, sizing_scn, resolution_h=1.0)
    else:
        n = build_network(ts, sizing_scn, resolution_h=resolution_h)

    status, condition = solve(n, sizing_scn, ts)

    # max(0, ·) clamps solver noise (e.g. -0.0 / -1e-9) at zero builds
    if "p_nom_opt" in n.generators.static.columns:
        onsw_mw = max(0.0, float(n.generators.static.p_nom_opt["Gen_OnshoreWind"]))
        pv_mw = max(0.0, float(n.generators.static.p_nom_opt["Gen_PV"]))
        bess_mw = max(0.0, float(n.storage_units.static.p_nom_opt["SU_BESS"]))
    else:
        onsw_mw = 0.0
        pv_mw = 0.0
        bess_mw = 0.0

    # Sized connection/transport link MW (the W12a fix: no longer pinned to the
    # disabled slider caps — the LP co-sizes these with generation).
    if "p_nom_opt" in n.links.static.columns:
        wind_link_mw = max(0.0, float(n.links.static.p_nom_opt["OnshoreWind_to_IPPGeneration"]))
        pvbess_link_mw = max(0.0, float(n.links.static.p_nom_opt["PVBESS_to_IPPGeneration"]))
        sell_link_mw = max(0.0, float(n.links.static.p_nom_opt["IPPGen_to_SellToMarket"]))
    else:
        wind_link_mw = pvbess_link_mw = sell_link_mw = 0.0

    def _infeasibility_hint(scenario: Scenario, condition: str) -> str:
        """Explain the most likely cause of an infeasible sizing LP.

        The hard minimum-delivery constraint is by far the most common way to
        make this problem infeasible, and an unexplained "infeasible" is a
        dead end for the user, so name the constraint and what to change.
        """
        if "infeasible" not in str(condition).lower():
            return ""
        if scenario.enforce_min_delivery:
            return (
                f" The hard minimum-delivery constraint requires "
                f"{scenario.required_delivery_share:.0%} of load to be delivered, "
                f"which cannot be met within the current build caps (wind "
                f"{scenario.max_build_wind_mw:.0f} / PV {scenario.max_build_pv_mw:.0f} / "
                f"BESS {scenario.max_build_bess_mw:.0f} MW"
                + (
                    f", grid connection {scenario.grid_connection_max_mw:.0f} MW"
                    if scenario.grid_connection_max_mw != float("inf") else ""
                )
                + "). Raise the caps, lower the required delivery share, allow more "
                "market buy, or turn the hard constraint off to let the penalty "
                "price decide."
            )
        return (
            " Check the build caps, the grid connection limit and the market-buy "
            "share — one of them is too tight to serve the load."
        )

    def _at_cap(opt: float, cap: float, tol: float = 1e-6) -> bool:
        if cap is None or cap == float("inf"):
            return False
        return opt >= float(cap) - tol * max(1.0, float(cap))

    # Report undegraded nameplate energy (the simulation applies fade per year itself)
    bess_mwh = bess_mw * scenario.bess_max_hours

    # PPA delivery share achieved *within the sizing LP* on its representation,
    # so the diagnostics can compare it against the full hourly simulation of
    # the sized portfolio (plan W14 item 6: a large gap = clustering dropped
    # something). Energy is p (MW) × snapshot weighting (hours), so both sides
    # are weighted the same way to integrate over real hours.
    w = n.snapshot_weightings["objective"].to_numpy()
    load_mwh = float((ts["ppaload_mw"].to_numpy() * w).sum())
    if "IPPGen_to_PPAOfftake" in n.links.dynamic.p1.columns and load_mwh > 0:
        delivered_mwh = float((-n.links.dynamic.p1["IPPGen_to_PPAOfftake"].to_numpy() * w).sum())
        sizing_delivery_share = min(1.0, delivered_mwh / load_mwh)
    else:
        sizing_delivery_share = 0.0

    return SizedCapacities(
        onsw_mw=onsw_mw,
        pv_mw=pv_mw,
        bess_mw=bess_mw,
        bess_mwh=bess_mwh,
        status=status,
        condition=condition + _infeasibility_hint(scenario, condition),
        sizing_years_used=n_years,
        horizon_clamped=n_years < scenario.simulation_years,
        resolution_h=resolution_h,
        sizing_method=method,
        sizing_delivery_share=sizing_delivery_share,
        wind_link_mw=wind_link_mw,
        pvbess_link_mw=pvbess_link_mw,
        sell_link_mw=sell_link_mw,
        wind_cap_binding=_at_cap(onsw_mw, sizing_scn.max_build_wind_mw),
        pv_cap_binding=_at_cap(pv_mw, sizing_scn.max_build_pv_mw),
        bess_cap_binding=_at_cap(bess_mw, sizing_scn.max_build_bess_mw),
        wind_link_binding=_at_cap(wind_link_mw, sizing_scn.grid_connection_max_mw),
        pvbess_link_binding=_at_cap(pvbess_link_mw, sizing_scn.grid_connection_max_mw),
        sell_link_binding=_at_cap(sell_link_mw, sizing_scn.grid_connection_max_mw),
    )


def validate_sizing_representation(
    ts: pd.DataFrame, scenario: Scenario, tolerance: float = 0.05
) -> dict:
    """Re-solve the sizing LP exactly and compare it against the chosen method.

    U8 step 4. The W14 validation metric compared the sizing LP's own delivery
    share against the full simulation's, but that is far too weak: measured
    across every representation the delivery gap sat at 2-4 pp even when the
    sized fleet was 19% wrong (docs/sizing_experiments.md E2). Delivery share
    is simply not sensitive to the sizing decision.

    This compares the thing that actually matters -- **sized MW per
    technology** -- against the exact full-hourly LP. Costs a full-hourly
    solve, so it is an opt-in check, not something to run on every solve.

    Returns a dict with `rows` (per technology: chosen MW, exact MW, delta),
    `max_abs_delta`, `within_tolerance` and `reference_seconds`.
    """
    import time as _time

    exact_scn = dataclasses.replace(scenario, sizing_method="full_hourly")
    t0 = _time.monotonic()
    exact = optimise_capacities(ts, exact_scn)
    reference_seconds = _time.monotonic() - t0
    chosen = optimise_capacities(ts, scenario)

    rows = []
    max_abs = 0.0
    for label, got, want in (
        ("Wind", chosen.onsw_mw, exact.onsw_mw),
        ("Solar", chosen.pv_mw, exact.pv_mw),
        ("BESS", chosen.bess_mw, exact.bess_mw),
    ):
        # Relative error is undefined when the exact LP builds nothing; report
        # None rather than dividing by zero or silently scoring it as perfect.
        delta = (got - want) / want if want > 1e-6 else None
        if delta is not None:
            max_abs = max(max_abs, abs(delta))
        rows.append({
            "Technology": label,
            f"{scenario.sizing_method} (MW)": round(float(got), 1),
            "Exact hourly (MW)": round(float(want), 1),
            "Difference": None if delta is None else round(100.0 * delta, 1),
        })

    return {
        "rows": rows,
        "method": scenario.sizing_method,
        "max_abs_delta": max_abs,
        "within_tolerance": max_abs <= tolerance,
        "tolerance": tolerance,
        "reference_seconds": reference_seconds,
        "exact_status": exact.status,
        "chosen_status": chosen.status,
    }


def sizing_diagnostics(sized: SizedCapacities, scenario: Scenario, ts: pd.DataFrame) -> dict:
    """Per-technology economics + binding constraints for the sizing run.

    Pure, unit-testable summary the Optimisation tab renders in its sizing
    diagnostics expander so "strange sizing results" become an explainable
    answer (plan W12e): annualised A$/MW/yr, achieved CF from the loaded
    profile, implied LCOE, and the PPA tariff / penalty / average spot for
    comparison, plus which caps bind at the optimum.
    """
    horizon_years = len(ts) / 8760.0

    def _crf(rate: float, life: int) -> float:
        return rate / (1 - (1 + rate) ** -life) if rate > 0 else 1.0 / life

    crf = _crf(scenario.target_irr, scenario.project_life_yrs)
    devex = 1.0 + scenario.devex_pct_of_capex
    opex = scenario.opex_rate

    tech_rows = []
    for label, capex_per_kw, cf_col, mw, binding in [
        ("Onshore wind", scenario.wind_capex_per_kw, "ts_WindGen", sized.onsw_mw, sized.wind_cap_binding),
        ("Solar PV", scenario.pv_capex_per_kw, "ts_PVGen", sized.pv_mw, sized.pv_cap_binding),
    ]:
        annualised_per_mw = capex_per_kw * 1_000 * devex * (crf + opex)
        achieved_cf = float(ts[cf_col].mean()) if cf_col in ts.columns else 0.0
        implied_lcoe = (
            annualised_per_mw / (achieved_cf * 8760.0) if achieved_cf > 0 else None
        )
        tech_rows.append(
            {
                "Technology": label,
                "Sized (MW)": round(mw, 1),
                "Annualised cost (A$/MW/yr)": round(annualised_per_mw, 0),
                "Achieved CF": f"{achieved_cf:.2%}",
                "Implied LCOE (A$/MWh)": None if implied_lcoe is None else round(implied_lcoe, 1),
                "Max-build cap binding": "Yes" if binding else "No",
            }
        )

    link_rows = []
    for label, mw, binding in [
        ("Wind link", sized.wind_link_mw, sized.wind_link_binding),
        ("PV+BESS link", sized.pvbess_link_mw, sized.pvbess_link_binding),
        ("Export link", sized.sell_link_mw, sized.sell_link_binding),
    ]:
        link_rows.append(
            {
                "Link": label,
                "Sized (MW)": round(mw, 1),
                "Connection limit binding": "Yes" if binding else "No",
            }
        )

    avg_spot = float(ts["ts_MktPrice"].mean()) if "ts_MktPrice" in ts.columns else None
    return {
        "tech_rows": tech_rows,
        "link_rows": link_rows,
        "ppa_price": scenario.ppa_price,
        "penalty_price": scenario.penalty_price,
        "avg_spot": None if avg_spot is None else round(avg_spot, 1),
        "horizon_years": horizon_years,
        "sizing_merchant_value_share": scenario.sizing_merchant_value_share,
        "sizing_method": scenario.sizing_method,
        "sizing_delivery_share": sized.sizing_delivery_share,
        "delivery_share_full": None,
    }


def _sizing_worker(conn, ts: pd.DataFrame, scenario_fields: dict) -> None:
    """Child-process entry point: solve the sizing LP and send the result back.

    Takes the scenario as a plain dict for the same Streamlit class-reload
    pickling reason as `ppa.multi_year._solve_one_year`.
    """
    try:
        sized = optimise_capacities(ts, Scenario(**scenario_fields))
        conn.send(("ok", sized))
    except BaseException:
        conn.send(("err", traceback.format_exc()))
    finally:
        conn.close()


def run_sizing_subprocess(
    ts: pd.DataFrame,
    scenario: Scenario,
    heartbeat: Callable[[], None] | None = None,
    poll_interval: float = 0.5,
) -> SizedCapacities:
    """Run `optimise_capacities` in a killable child process.

    The solve is one blocking native HiGHS call, so it cannot be interrupted
    in-process (Streamlit's Stop button, Ctrl+C and SIGTERM are all deferred
    until the solver returns). Running it in a child process makes it
    cancellable — `heartbeat` is invoked every `poll_interval` seconds and may
    raise (e.g. a Streamlit StopException); the child is then killed by the
    finally block. Killing the child also returns the LP's multi-GB memory to
    the OS immediately instead of leaving it in the app process.
    """
    try:
        mp_context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - Windows only
        mp_context = multiprocessing.get_context("spawn")

    parent_conn, child_conn = mp_context.Pipe(duplex=False)
    proc = mp_context.Process(
        target=_sizing_worker,
        args=(child_conn, ts, dataclasses.asdict(scenario)),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    try:
        while True:
            if parent_conn.poll(poll_interval):
                kind, payload = parent_conn.recv()
                break
            if not proc.is_alive():
                # Drain a result sent just before exit, else it truly crashed
                if parent_conn.poll(0):
                    kind, payload = parent_conn.recv()
                    break
                raise RuntimeError(
                    "Sizing subprocess died without returning a result "
                    "(likely killed by the OS — out of memory?)."
                )
            if heartbeat is not None:
                heartbeat()  # may raise (user cancelled) → finally kills child
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=5)
        parent_conn.close()

    if kind == "err":
        raise RuntimeError(f"Capacity sizing LP failed in subprocess:\n{payload}")
    return payload


def apply_sizing(scenario: Scenario, sized: SizedCapacities) -> Scenario:
    """Write optimised capacities into a fixed-capacity Scenario for simulation."""
    bess_built = sized.bess_mw > 0.1  # ignore solver noise below 0.1 MW
    return dataclasses.replace(
        scenario,
        onsw_mw=round(sized.onsw_mw, 1),
        pv_mw=round(sized.pv_mw, 1),
        bess_mw=round(sized.bess_mw, 1) if bess_built else 0.0,
        bess_mwh=round(sized.bess_mwh, 1) if bess_built else 0.0,
        include_bess=scenario.include_bess and bess_built,
        # Carry the sized connection MW into dispatch so the simulation uses the
        # same connection capacity the LP assumed (plan W12a carry-through).
        wind_link_mw=round(sized.wind_link_mw, 1),
        pvbess_link_mw=round(sized.pvbess_link_mw, 1),
        sell_link_mw=round(sized.sell_link_mw, 1),
        optimise_capacity=False,
    )

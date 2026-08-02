#!/usr/bin/env python3
"""W15: benchmark HiGHS solver algorithms on the capacity-sizing LP.

Builds the sizing LP for a given scenario and times the default dual simplex
against the interior-point variants available in this HiGHS build:

    {default (dual simplex), "solver": "ipm", "solver": "hipo"}

with `run_crossover` off for the IPM family (the LP is a sizing decision; we
care about the optimum, not a vertex). Prints model dimensions and wall-clock
per solver so the HiPO-vs-simplex decision can be measured, not assumed.

Usage:
    python scripts/bench_solver.py [--hours 8760] [--periods 12] [--method tsam]

The timeseries is synthetic (deterministic sinusoidal PV/wind/price + constant
load) so the script is self-contained and doesn't need NEM data. `--periods`
only applies to the tsam method (typical-day clustering).
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ppa.network import build_network
from ppa.scenario import Scenario
from ppa.sizing import optimise_capacities
from ppa.solver import solve


@dataclass
class BenchResult:
    label: str
    seconds: float
    status: str
    condition: str


def _synthetic_year(hours: int) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=hours, freq="h")
    t = np.arange(hours)
    doy = idx.dayofyear
    hod = (idx.hour + idx.minute / 60.0) % 24
    # Seasonal + diurnal PV, baseline + gusting wind, price following load
    pv = np.maximum(0.0, np.sin(np.pi * (hod - 5.5) / 11.0)) * (0.7 + 0.3 * np.cos(2 * np.pi * doy / 365.0))
    wind = 0.4 + 0.25 * np.sin(2 * np.pi * hod / 24 + 1.0) + 0.1 * np.sin(2 * np.pi * t / 720.0)
    price = np.asarray(60 + 30 * np.cos(2 * np.pi * (hod - 15) / 24) + 10 * np.sin(2 * np.pi * doy / 365.0))
    price[::37] = -15.0  # occasional negative-price hours
    return pd.DataFrame(
        {
            "ts_PVGen": np.clip(pv, 0.0, 1.0),
            "ts_WindGen": np.clip(wind, 0.0, 1.0),
            "ts_MktPrice": price,
            "ppaload_mw": 100.0,
        },
        index=idx,
    )


def _sizing_scenario(method: str, periods: int) -> Scenario:
    return Scenario(
        name="solver-bench",
        optimise_capacity=True,
        sizing_method=method,
        sizing_n_periods=periods,
        sizing_resolution_h=3,
        onsw_mw=50.0,
        pv_mw=50.0,
        include_bess=True,
        bess_mw=20.0,
        bess_mwh=80.0,
        max_build_wind_mw=2000.0,
        max_build_pv_mw=2000.0,
        max_build_bess_mw=2000.0,
        wind_capex_per_kw=100.0,
        pv_capex_per_kw=100.0,
        bess_capex_per_kwh=50.0,
        simulation_years=1,
    )


def _solve_once(n, scenario, ts, label: str, solver_options: dict) -> BenchResult:
    # Give the solver a warm-ish start: solve with the previous options? No —
    # measure cold solves the way the app actually runs them.
    t0 = time.monotonic()
    status, condition = solve(n, scenario, ts, solver_options=solver_options)
    return BenchResult(label, time.monotonic() - t0, status, condition)


def _hipo_available() -> bool:
    """True if this HiGHS build can actually run HiPO (needs highspy-extras)."""
    try:
        import highspy

        h = highspy.Highs()
        h.setOptionValue("solver", "hipo")
        status, value = h.getOptionValue("solver")
        return status == highspy.HighsStatus.kOk and value == "hipo"
    except Exception:  # noqa: BLE001
        return False


def _options_list() -> list[tuple[str, dict]]:
    opts: list[tuple[str, dict]] = [("simplex (default)", {})]
    opts.append(("ipm", {"solver": "ipm"}))
    opts.append(("ipm (no crossover)", {"solver": "ipm", "run_crossover": "off"}))
    if _hipo_available():
        opts.append(("hipo", {"solver": "hipo"}))
        opts.append(("hipo (no crossover)", {"solver": "hipo", "run_crossover": "off"}))
    else:
        print("  (HiPO skipped — install highspy-extras to benchmark it)")
    return opts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=8760, help="hourly snapshots for full_hourly/tsam input")
    ap.add_argument("--method", default="tsam", choices=["tsam", "full_hourly", "coarse"])
    ap.add_argument("--periods", type=int, default=12, help="tsam typical-period count")
    ap.add_argument("--quick", action="store_true", help="benchmark with a small synthetic year (CI)")
    args = ap.parse_args()

    hours = 672 if args.quick else args.hours
    ts = _synthetic_year(hours)

    scn = _sizing_scenario(args.method, args.periods)
    print(f"Sizing representation: {args.method} (input {len(ts)} hourly snapshots)")

    # Build once, then re-solve from a fresh network copy for each option so the
    # model structure (rows/cols/nonzeros) is identical across runs.
    sizing_scn = dataclasses_replace_sizing(scn)
    if args.method == "tsam":
        from ppa.sizing_tsam import cluster_typical_periods  # noqa: PLC0415

        clustered, weights = cluster_typical_periods(ts, n_periods=max(4, args.periods))
        print(f"  clustered to {len(clustered)} hourly snapshots, weights sum {weights.sum():.0f} h")
        base = build_network(clustered, sizing_scn, snapshot_weightings=weights)
        bench_ts = clustered
    elif args.method == "coarse":
        base = build_network(ts, sizing_scn, resolution_h=3.0)
        bench_ts = ts
    else:
        base = build_network(ts, sizing_scn, resolution_h=1.0)
        bench_ts = ts
    _dims: str | None = None

    results: list[BenchResult] = []
    for label, opts in _options_list():
        n = base.copy()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        logging.getLogger("linopy").addHandler(handler)
        r = _solve_once(n, sizing_scn, bench_ts, label, opts)
        logging.getLogger("linopy").removeHandler(handler)
        if _dims is None:
            m = re.search(r"Solution: (\d+) primals, (\d+) duals", buf.getvalue())
            _dims = (
                f"rows={m.group(2)} cols={m.group(1)}" if m else "n/a"
            )
        results.append(r)
        print(f"  {label:28s} {r.seconds:7.2f}s  ({r.status}/{r.condition})")

    if _dims:
        print(f"  model: {_dims}")

    baseline = next(r.seconds for r in results if r.label.startswith("simplex"))
    print("\n  vs dual simplex:")
    for r in results:
        if r.label.startswith("simplex"):
            continue
        ratio = r.seconds / baseline
        verdict = "FASTER >25%" if r.seconds < baseline * 0.75 else "not faster"
        print(f"  {r.label:28s} {ratio:6.2f}x  -> {verdict}")

def dataclasses_replace_sizing(scn: Scenario) -> Scenario:
    """Mirror the sizing-scenario de-rating done in optimise_capacities."""
    import dataclasses

    sizing_scn = dataclasses.replace(
        scn,
        optimise_capacity=True,
        include_bess=scn.include_bess and scn.max_build_bess_mw > 0,
        bess_mw=1.0,
        bess_mwh=scn.bess_max_hours,
        bess_capex_per_kwh=scn.bess_capex_per_kwh,
    )
    if not sizing_scn.include_bess:
        sizing_scn = dataclasses.replace(sizing_scn, max_build_bess_mw=0.0)
    return sizing_scn


if __name__ == "__main__":
    main()

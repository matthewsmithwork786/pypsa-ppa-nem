#!/usr/bin/env python3
"""W12b: sweep `Scenario.sizing_merchant_value_share` on a real case study.

The sizing LP credits merchant sales at `sizing_merchant_value_share` of
*positive* spot prices (a haircut for capture-price cannibalisation, MLF and
curtailment). The share is a conservatism dial, not a derived number, so the
plan (W12b) asks for it to be measured rather than asserted: for each share,
report the sized fleet, the delivery share and the full-simulation project IRR.

Any share > 0 means the LP builds until a cap binds, so `max_build_*` and
`grid_connection_max_mw` become the real sizing decision — the "binding" column
shows which cap stopped the build at each share.

This runs the same pipeline as `ui/tabs/optimisation.py::_run_simulation`:

    sizing LP -> apply_sizing -> run_multi_year (hourly dispatch)
              -> run_multi_year_financial_analysis (IRR)

Usage:
    PYTHONPATH=. python3 scripts/bench_merchant_share.py
    PYTHONPATH=. python3 scripts/bench_merchant_share.py --years 3 --case corporate_ppa

Needs the cached NEM SCADA/price parquets under `data/cache/nem/`.
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import time

import numpy as np

from ppa.financials import run_multi_year_financial_analysis
from ppa.multi_year import run_multi_year
from ppa.scenario import CASE_STUDIES_BY_ID, load_case_study
from ppa.sizing import (
    apply_sizing,
    build_sizing_timeseries,
    run_sizing_subprocess,
    weather_cycle_years,
)

DEFAULT_SHARES = [0.0, 0.25, 0.5, 0.75, 1.0]


def _fmt(x: float, pct: bool = False) -> str:
    if x != x:  # NaN
        return "n/a"
    return f"{x:.1%}" if pct else f"{x:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="corporate_ppa", help="case-study id")
    ap.add_argument("--years", type=int, default=None,
                    help="override simulation_years (default: the case study's own)")
    ap.add_argument("--shares", type=float, nargs="+", default=DEFAULT_SHARES)
    ap.add_argument("--method", default="full_hourly",
                    choices=["tsam", "full_hourly", "coarse"],
                    help="sizing representation (default full_hourly: exact, so the "
                         "sweep measures the merchant share, not clustering error)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    case = CASE_STUDIES_BY_ID.get(args.case)
    if case is None:
        raise SystemExit(f"unknown case '{args.case}'; have {list(CASE_STUDIES_BY_ID)}")

    base = load_case_study(case)
    base = dataclasses.replace(base, optimise_capacity=True, sizing_method=args.method)
    if args.years:
        base = dataclasses.replace(base, simulation_years=args.years)

    from ppa.data import nem_data

    pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(base)

    n_sizing_years, _ = weather_cycle_years(
        base.simulation_years, len(pv_by_year), len(prices_by_year)
    )

    print(f"Case study: {case.name} ({args.case})")
    print(f"  slider (disabled) fleet: wind {base.onsw_mw:.0f} MW · PV {base.pv_mw:.0f} MW · "
          f"BESS {base.bess_mw:.0f} MW/{base.bess_mwh:.0f} MWh")
    print(f"  PPA price A${base.ppa_price:.0f}/MWh · required delivery "
          f"{base.required_delivery_share:.0%} · simulation_years {base.simulation_years}")
    print(f"  sizing: {args.method}, {n_sizing_years}-year LP · build caps: wind "
          f"{base.max_build_wind_mw:.0f} / PV {base.max_build_pv_mw:.0f} / BESS "
          f"{base.max_build_bess_mw:.0f} MW · grid limit {base.grid_connection_max_mw}")
    print()

    header = (f"{'share':>6} {'wind MW':>8} {'PV MW':>7} {'BESS MW':>8} {'exp.link':>9} "
              f"{'deliv%':>7} {'IRR':>7} {'size s':>7} {'disp s':>7}  binding")
    print(header)
    print("-" * len(header))

    for share in args.shares:
        scn = dataclasses.replace(base, sizing_merchant_value_share=float(share))

        sizing_ts = build_sizing_timeseries(
            scn, pv_by_year, wind_by_year, prices_by_year, n_sizing_years
        )
        t0 = time.monotonic()
        sized = run_sizing_subprocess(sizing_ts, scn)
        size_s = time.monotonic() - t0
        if sized.status != "ok":
            print(f"{share:>6.2f}  SIZING FAILED: {sized.status}/{sized.condition}")
            continue

        sim_scn = apply_sizing(scn, sized)
        # Free the full-year sizing frame before run_multi_year forks: fork is
        # copy-on-write but CPython refcounting dirties the pages anyway, so
        # anything still resident here is duplicated into every worker.
        del sizing_ts
        gc.collect()

        t1 = time.monotonic()
        results = run_multi_year(
            scenario=sim_scn,
            pv_cf_by_year=pv_by_year,
            wind_cf_by_year=wind_by_year,
            prices_by_year=prices_by_year,
            first_sim_year=sim_scn.first_sim_year,
            max_workers=args.workers,
        )
        disp_s = time.monotonic() - t1

        fin = run_multi_year_financial_analysis(
            sim_scn, results, first_sim_year=sim_scn.first_sim_year
        )
        delivered = float(np.mean([r.summary.fulfilled_share for r in results]))

        binding = ", ".join(
            label for label, flag in (
                ("wind cap", sized.wind_cap_binding),
                ("PV cap", sized.pv_cap_binding),
                ("BESS cap", sized.bess_cap_binding),
                ("wind link", sized.wind_link_binding),
                ("PV link", sized.pvbess_link_binding),
                ("export link", sized.sell_link_binding),
            ) if flag
        ) or "none"

        print(f"{share:>6.2f} {sized.onsw_mw:>8,.0f} {sized.pv_mw:>7,.0f} "
              f"{sized.bess_mw:>8,.0f} {sized.sell_link_mw:>9,.0f} "
              f"{delivered:>7.1%} {_fmt(fin.irr, pct=True):>7} "
              f"{size_s:>7.1f} {disp_s:>7.1f}  {binding}")


if __name__ == "__main__":
    main()

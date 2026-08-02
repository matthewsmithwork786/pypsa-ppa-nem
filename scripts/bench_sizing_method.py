#!/usr/bin/env python3
"""W14: compare sizing representations — solve time and sized MW.

The plan (W14 item 7) asks for `{full hourly, tsam 8/12/24 typical days,
legacy 3 h coarse}` benchmarked on one case study, so the default
(`sizing_method="tsam"`, 12 periods) is a measured choice rather than an
assumed one.

Two things matter and both are reported:

  * **Cost** — wall-clock for the sizing LP, and the LP's dimensions.
  * **Fidelity** — how close the sized fleet is to the exact full-hourly
    answer, and the gap between the delivery share the sizing LP *believes*
    it achieves on its own representation and the delivery share the sized
    portfolio actually achieves in the full hourly simulation (W14 item 6).
    A large gap means the representation dropped something that matters.

Full hourly is the reference row: `Δ fleet` is the total-MW error against it.

Usage:
    PYTHONPATH=. python3 scripts/bench_sizing_method.py
    PYTHONPATH=. python3 scripts/bench_sizing_method.py --case foundation_deal

Needs the cached NEM SCADA/price parquets under `data/cache/nem/`, and `tsam`
installed for the typical-day rows (they are skipped otherwise).
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import time

import numpy as np

from ppa.multi_year import run_multi_year
from ppa.scenario import CASE_STUDIES_BY_ID, load_case_study
from ppa.sizing import (
    apply_sizing,
    build_sizing_timeseries,
    run_sizing_subprocess,
    weather_cycle_years,
)
from ppa.sizing_tsam import tsam_available


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="corporate_ppa", help="case-study id")
    ap.add_argument("--years", type=int, default=None,
                    help="override simulation_years (default: the case study's own)")
    ap.add_argument("--periods", type=int, nargs="+", default=[8, 12, 24],
                    help="tsam typical-period counts to benchmark")
    ap.add_argument("--coarse-h", type=int, default=3, help="legacy coarse resolution (h)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    case = CASE_STUDIES_BY_ID.get(args.case)
    if case is None:
        raise SystemExit(f"unknown case '{args.case}'; have {list(CASE_STUDIES_BY_ID)}")

    base = load_case_study(case)
    base = dataclasses.replace(base, optimise_capacity=True)
    if args.years:
        base = dataclasses.replace(base, simulation_years=args.years)

    from ppa.data import nem_data

    pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(base)
    n_sizing_years, _ = weather_cycle_years(
        base.simulation_years, len(pv_by_year), len(prices_by_year)
    )
    sizing_ts = build_sizing_timeseries(
        base, pv_by_year, wind_by_year, prices_by_year, n_sizing_years
    )

    configs: list[tuple[str, dict]] = [
        ("full hourly (exact)", {"sizing_method": "full_hourly"}),
    ]
    if tsam_available():
        for p in args.periods:
            configs.append((f"tsam {p} typical days",
                            {"sizing_method": "tsam", "sizing_n_periods": p}))
    else:
        print("  (tsam rows skipped — `pip install tsam` to benchmark them)")
    configs.append((f"coarse {args.coarse_h}h (legacy)",
                    {"sizing_method": "coarse", "sizing_resolution_h": args.coarse_h}))

    print(f"Case study: {case.name} ({args.case}) · {n_sizing_years}-year sizing LP "
          f"over {len(sizing_ts):,} hourly snapshots")
    print(f"  merchant share {base.sizing_merchant_value_share} · PPA A${base.ppa_price:.0f}/MWh "
          f"· required delivery {base.required_delivery_share:.0%}")
    print()

    header = (f"{'method':<22} {'size s':>7} {'wind':>6} {'PV':>6} {'BESS':>6} "
              f"{'total':>6} {'Δfleet':>7} {'LP deliv':>9} {'full deliv':>11} {'gap':>7}")
    print(header)
    print("-" * len(header))

    reference_total: float | None = None
    for label, overrides in configs:
        scn = dataclasses.replace(base, **overrides)

        t0 = time.monotonic()
        sized = run_sizing_subprocess(sizing_ts, scn)
        size_s = time.monotonic() - t0
        if sized.status != "ok":
            print(f"{label:<22}  FAILED: {sized.status}/{sized.condition}")
            continue

        total = sized.onsw_mw + sized.pv_mw + sized.bess_mw
        if reference_total is None:
            reference_total = total
            delta = "ref"
        else:
            delta = f"{(total - reference_total) / reference_total:+.1%}"

        # Full hourly simulation of the sized portfolio: the honest delivery
        # share, against which the sizing LP's own estimate is judged.
        sim_scn = apply_sizing(scn, sized)
        gc.collect()  # see bench_merchant_share: keep the parent small before fork
        results = run_multi_year(
            scenario=sim_scn,
            pv_cf_by_year=pv_by_year,
            wind_cf_by_year=wind_by_year,
            prices_by_year=prices_by_year,
            first_sim_year=sim_scn.first_sim_year,
            max_workers=args.workers,
        )
        full_deliv = float(np.mean([r.summary.fulfilled_share for r in results]))
        gap_pp = (sized.sizing_delivery_share - full_deliv) * 100

        print(f"{label:<22} {size_s:>7.1f} {sized.onsw_mw:>6,.0f} {sized.pv_mw:>6,.0f} "
              f"{sized.bess_mw:>6,.0f} {total:>6,.0f} {delta:>7} "
              f"{sized.sizing_delivery_share:>9.1%} {full_deliv:>11.1%} {gap_pp:>+6.1f}pp")


if __name__ == "__main__":
    main()

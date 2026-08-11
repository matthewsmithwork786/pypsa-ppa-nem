#!/usr/bin/env python3
"""Precompute the per-DUID eligibility/CUF summary that `nem_data.list_eligible_plants`
otherwise has to compute live (one full-year scan per plant) on every cold start.

*** Reads only the already-committed `data/cache/nem/` files -- no network access
needed, unlike the `fetch_*` scripts. Safe to run in this sandbox. ***

The Get Data / Pick Plants map+list calls `list_eligible_plants(check_whole_year=True)`,
which used to iterate `scada_summary()` (a full-year 5-minute scan) over every plant
in the registry just to render the plant picker -- the "loading the plant generation
cache takes a while" complaint. `scada_summary` results don't change unless the
underlying availability/SCADA cache changes, so this script runs that scan once and
writes the result to `data/cache/nem/registry/eligibility_{year}.parquet`;
`list_eligible_plants` reads it back in a single parquet read (falling back to a live
per-plant computation only for DUIDs missing from the cache, e.g. a newly-added
plant), so the map/list renders instantly on every subsequent run -- including a
fresh Cloud Run container, since the parquet ships committed in the image.

Run this whenever `data/cache/nem/availability/` or `.../scada/` changes (new year
fetched, plants added/removed, registry updated), then commit the output parquet:

    python scripts/build_nem_eligibility_cache.py --year 2025
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ppa.data import nem_data


def build(year: int) -> Path:
    registry = nem_data.load_plant_registry()
    df = registry[
        (registry["capacity_registered_mw"] > nem_data.MIN_CAPACITY_MW)
        & (registry["fuel_tech"].isin(nem_data.DEFAULT_FUEL_TECHS))
        & (registry["status"].isin((nem_data.OPERATING_STATUS,)))
    ].copy()

    has_scada_list, sim_ready_list, data_status_list = [], [], []
    reject_reasons_list, coverage_list = [], []
    mean_cf_list, cuf_list, first_output_date_list = [], [], []
    for _, row in df.iterrows():
        duid = row["duid"]
        capacity_mw = float(row["capacity_registered_mw"])
        has_scada = (
            nem_data.availability_path(duid, year).exists()
            or nem_data.scada_path(duid, year).exists()
        )
        summary = nem_data.scada_summary(duid, capacity_mw, year)
        has_scada_list.append(has_scada)
        sim_ready_list.append(summary.status == "ready")
        data_status_list.append(summary.status)
        reject_reasons_list.append(summary.reject_reasons)
        coverage_list.append(summary.check.coverage if summary.check is not None else float("nan"))
        mean_cf_list.append(summary.mean_cf if summary.mean_cf is not None else float("nan"))
        cuf_list.append(summary.cuf if summary.cuf is not None else float("nan"))
        first_output_date_list.append(summary.first_output_date)

    out = df[["duid"]].copy()
    out["has_scada"] = has_scada_list
    out["simulation_ready"] = sim_ready_list
    out["data_status"] = data_status_list
    out["reject_reasons"] = reject_reasons_list
    out["coverage"] = coverage_list
    out["mean_cf"] = mean_cf_list
    out["cuf"] = cuf_list
    out["first_output_date"] = first_output_date_list

    path = nem_data.eligibility_cache_path(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    n_ready = int(out["simulation_ready"].sum())
    print(f"Wrote {path} — {len(out)} plants, {n_ready} simulation-ready.")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=nem_data.DEFAULT_YEAR)
    args = parser.parse_args()
    build(args.year)

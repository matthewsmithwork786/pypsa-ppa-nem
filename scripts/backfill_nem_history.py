#!/usr/bin/env python3
"""Acquire complete operating years of UIGF and matching prices via NEMOSIS.

Monthly target-only checkpoints make downloads resumable. Raw MMS archives can
be removed between years; only eligible plant profiles enter the app cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ppa.data import nem_data
from build_plant_year_manifest import apply_gate, build_manifest, build_year_summary
from fetch_nem_availability import pull_availability
from fetch_nem_scada_prices import NEM_REGIONS, pull_price_all_regions, write_price_for_region

log = logging.getLogger(__name__)


def acquire_year(year: int, cache_dir: Path, work_dir: Path, raw_dir: Path) -> None:
    registry = nem_data.load_plant_registry(cache_dir)
    duids = registry.duid.tolist()
    checkpoint_dir = work_dir / str(year)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    ending = nem_data.canonical_5min_index(year)
    values = pd.DataFrame(np.nan, index=ending, columns=duids, dtype="float32")
    for month in range(1, 13):
        checkpoint = checkpoint_dir / f"availability_{month:02d}.parquet"
        if not checkpoint.exists():
            start = pd.Timestamp(year, month, 1)
            end = start + pd.offsets.MonthBegin(1)
            rows = pull_availability(year, raw_dir, start.strftime("%Y/%m/%d %H:%M:%S"),
                                     end.strftime("%Y/%m/%d %H:%M:%S"), duids=duids)
            rows = rows[["SETTLEMENTDATE", "DUID", "AVAILABILITY"]].copy()
            rows["SETTLEMENTDATE"] = pd.to_datetime(rows.SETTLEMENTDATE)
            rows = rows[(rows.SETTLEMENTDATE > start) & (rows.SETTLEMENTDATE <= end)]
            rows["AVAILABILITY"] = pd.to_numeric(rows.AVAILABILITY, errors="coerce")
            if rows.empty:
                raise ValueError(f"{year}-{month:02d}: no target availability in requested month")
            rows.to_parquet(checkpoint, compression="zstd", index=False)
            del rows
        rows = pd.read_parquet(checkpoint)
        rows = rows.drop_duplicates(["SETTLEMENTDATE", "DUID"], keep="last")
        wide = rows.pivot(index="SETTLEMENTDATE", columns="DUID", values="AVAILABILITY")
        values.loc[wide.index, wide.columns] = wide.astype("float32")
        log.info("%s-%02d: checkpoint loaded, %d plants", year, month, len(wide.columns))
        del rows, wide
        # Old NEMOSIS caches may retain CSVs even when keep_csv=False. The
        # completed Parquet caches suffice for subsequent month-boundary reads.
        for csv in raw_dir.glob("*.CSV"):
            parquet = csv.with_suffix(".parquet")
            if parquet.exists():
                pq.read_metadata(parquet)
                csv.unlink()

    audit = []
    availability_dir = cache_dir / "availability"
    availability_dir.mkdir(parents=True, exist_ok=True)
    for row in registry.itertuples():
        series = values[row.duid].copy()
        series.index = series.index - pd.Timedelta(minutes=5)
        metrics = build_year_summary(series, row.capacity_registered_mw, year)
        passed, reasons = apply_gate(metrics, year, row.first_power_date)
        if not np.isfinite(series.dropna()).all() or (series.dropna() < 0).any():
            passed, reasons = False, reasons + "; invalid availability values"
        target = availability_dir / f"{row.duid}_{year}.parquet"
        checksum = None
        if passed:
            pd.DataFrame({"availability": values[row.duid].to_numpy()}).to_parquet(
                target, compression="zstd", index=False)
            checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        audit.append({"duid": row.duid, "year": year, "eligible": passed,
                      "coverage": metrics["coverage"], "reject_reasons": reasons,
                      "sha256": checksum})
    audit_dir = cache_dir / "source"
    audit_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(audit).to_csv(audit_dir / f"uigf_eligibility_{year}.csv", index=False)
    log.info("%d: accepted %d / %d plant profiles", year, sum(r["eligible"] for r in audit), len(audit))
    if not any(row["eligible"] for row in audit):
        raise ValueError(f"{year}: no eligible plant profiles; refusing to publish")

    prices_checkpoint = checkpoint_dir / "prices.parquet"
    if not prices_checkpoint.exists():
        prices = pull_price_all_regions(year, raw_dir)
        prices.to_parquet(prices_checkpoint, compression="zstd", index=False)
    prices = pd.read_parquet(prices_checkpoint)
    for region in NEM_REGIONS:
        rows = prices[prices.REGIONID == region]
        expected = pd.date_range(f"{year}-01-01", periods=nem_data.expected_intervals(year), freq="5min")
        series = rows.drop_duplicates("SETTLEMENTDATE").set_index("SETTLEMENTDATE").RRP.reindex(expected)
        if not np.isfinite(series).all():
            raise ValueError(f"{region} {year}: missing or invalid prices; refusing to publish")
        ok, message = write_price_for_region(region, rows, year, cache_dir, overwrite=False)
        if not ok:
            raise ValueError(message)
        log.info(message)
    metadata = {"year": year, "nemosis_version": version("nemosis"),
                "acquired_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "availability_table": "DISPATCHLOAD", "value": "AVAILABILITY",
                "intervention": 0, "clock": "AEST (UTC+10), interval ending in source",
                "price_table": "DISPATCHPRICE", "source": "https://nemweb.com.au/",
                "registry_sha256": hashlib.sha256(nem_data.registry_path(cache_dir).read_bytes()).hexdigest(),
                "accepted_profiles": sum(r["eligible"] for r in audit), "target_profiles": len(audit)}
    (audit_dir / f"uigf_{year}.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=list(range(2020, 2025)))
    parser.add_argument("--cache-dir", type=Path, default=nem_data.NEM_CACHE_DIR)
    parser.add_argument("--work-dir", type=Path, default=Path("nemosis_cache/history"))
    parser.add_argument("--raw-cache-dir", type=Path, default=Path("nemosis_cache/raw"))
    parser.add_argument("--clear-raw-between-years", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for year in args.years:
        acquire_year(year, args.cache_dir, args.work_dir, args.raw_cache_dir)
        if args.clear_raw_between_years and args.raw_cache_dir.exists():
            shutil.rmtree(args.raw_cache_dir)
    build_manifest(args.cache_dir)


if __name__ == "__main__":
    main()

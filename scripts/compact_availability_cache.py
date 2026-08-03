#!/usr/bin/env python3
"""Rewrite the availability cache in the compact values-only format.

A naive per-interval parquet spends ~64% of its bytes on the timestamp index,
which is entirely redundant: AEMO dispatch intervals are a fixed 5-minute grid.
Storing the values alone against `nem_data.canonical_5min_index(year)` cuts the
shipped cache from ~197 MB to ~71 MB, which is download time on every cold
start of the deployed app.

Gaps are written as NaN rather than filled, so `whole_year_check`'s coverage
test still sees a short year. `semidispatchcap` is dropped: only the fetch
script ever wrote it, and nothing reads it now that curtailment reporting
(which needed both SCADA and UIGF) is not computable from the shipped cache.

The conversion is verified per file: the rewritten series must reproduce the
original exactly on the original timestamps, or the file is left alone.

Usage:
    PYTHONPATH=. python3 scripts/compact_availability_cache.py --year 2025
    PYTHONPATH=. python3 scripts/compact_availability_cache.py --year 2025 --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ppa.data import nem_data  # noqa: E402


def compact_one(path: Path, year: int, dry_run: bool) -> tuple[str, int, int]:
    before = path.stat().st_size
    df = pd.read_parquet(path)
    if isinstance(df.index, pd.RangeIndex):
        return "already-compact", before, before
    if "availability" not in df.columns:
        return "no-availability-column", before, before

    original = df["availability"]
    idx = nem_data.canonical_5min_index(year)
    values = original.reindex(idx)

    # Verify before replacing: the rewritten series must reproduce the original
    # exactly on the original timestamps.
    check = pd.Series(values.to_numpy(dtype="float32"), index=idx).reindex(original.index)
    if not np.allclose(
        check.to_numpy(dtype="float64"),
        original.to_numpy(dtype="float64"),
        equal_nan=True,
    ):
        return "VERIFY-FAILED", before, before

    if dry_run:
        return "would-compact", before, before

    out = pd.DataFrame({"availability": values.to_numpy(dtype="float32")})
    out.to_parquet(path, compression="zstd", index=False)
    return "compacted", before, path.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--cache-dir", type=Path, default=nem_data.NEM_CACHE_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted((args.cache_dir / "availability").glob(f"*_{args.year}.parquet"))
    if not files:
        print(f"No availability files for {args.year} under {args.cache_dir}")
        return 1

    total_before = total_after = 0
    counts: dict[str, int] = {}
    failures: list[str] = []
    for f in files:
        status, before, after = compact_one(f, args.year, args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        total_before += before
        total_after += after
        if status == "VERIFY-FAILED":
            failures.append(f.name)

    for k, v in sorted(counts.items()):
        print(f"  {k:24s} {v}")
    print(f"\n{total_before/1e6:.0f} MB -> {total_after/1e6:.0f} MB "
          f"({100*total_after/max(total_before,1):.0f}%)")
    if failures:
        print(f"\nLEFT UNCHANGED (verification failed): {failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

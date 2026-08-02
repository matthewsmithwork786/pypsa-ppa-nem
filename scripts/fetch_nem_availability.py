#!/usr/bin/env python3
"""U4 acquisition: pull AEMO 5-minute *unconstrained availability* via `nemosis`.

Why this exists
---------------
The SCADA traces in `data/cache/nem/scada/` are **constrained** output: what a
plant actually sent out, after network constraints and after any economic
curtailment its own offtake contract incentivised. Using that as the capacity
factor for a *new* build charges curtailment twice -- once baked into the
trace, and again when the sizing LP curtails against prices and limits.

It also cannot be corrected with a uniform uplift, because whether a plant
curtails into negative prices depends on its individual PPA. Some are
incentivised to, some are not.

`DISPATCHLOAD.AVAILABILITY` avoids both problems. For semi-scheduled units it
is AEMO's Unconstrained Intermittent Generation Forecast (UIGF): the plant's
*physically available* output for that interval, independent of network
constraints and of any contractual curtailment incentive.

`SEMIDISPATCHCAP` is carried alongside as the constraint flag (1 = a dispatch
cap was binding in that interval), so downstream code can measure how much
curtailment a trace embeds rather than inferring it.

Output
------
`data/cache/nem/availability/<DUID>_<year>.parquet` with columns
`availability` (MW) and `semidispatchcap` (0/1), on a 5-minute
`SETTLEMENTDATE` index -- deliberately the same shape and cadence as the SCADA
cache so `ppa/data/nem_data.py` can read it with the existing loader.

INTERVENTION filtering
----------------------
DISPATCHLOAD carries both normal (`INTERVENTION=0`) and intervention
(`INTERVENTION=1`) runs for intervened intervals. We keep `INTERVENTION=0`,
the standard non-intervention dispatch, matching how DISPATCH_UNIT_SCADA is
already treated.

Usage
-----
    pip install -r scripts/requirements-acquisition.txt
    python3 scripts/fetch_nem_availability.py --year 2025 \
        --duid-list data/cache/nem/registry/nem_plant_registry.parquet

Network: reaches nemweb.com.au (AEMO's public MMS archive) via nemosis.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "cache" / "nem"
AVAILABILITY_SUBDIR = "availability"

TABLE = "DISPATCHLOAD"
SELECT_COLUMNS = [
    "SETTLEMENTDATE",
    "DUID",
    "INTERVENTION",
    "AVAILABILITY",
    "SEMIDISPATCHCAP",
]

log = logging.getLogger("fetch_nem_availability")


def _dynamic_data_compiler(*args, **kwargs):
    """Import nemosis lazily so --help works without it installed."""
    try:
        from nemosis import dynamic_data_compiler as _compiler
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SystemExit(
            "Could not import `dynamic_data_compiler` from `nemosis`. Install it with "
            "`pip install -r scripts/requirements-acquisition.txt`."
        ) from exc
    return _compiler(*args, **kwargs)


def _read_duid_list(path: Path) -> list[str]:
    """DUIDs from a registry parquet / csv / newline-delimited text file."""
    if not path.exists():
        raise FileNotFoundError(f"--duid-list path does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(path)
        if "duid" not in df.columns:
            raise ValueError(f"{path} has no `duid` column (columns: {list(df.columns)})")
        return sorted(set(df["duid"].astype(str).str.strip().str.upper()))
    if suffix == ".csv":
        df = pd.read_csv(path)
        col = "duid" if "duid" in df.columns else df.columns[0]
        return sorted(set(df[col].astype(str).str.strip().str.upper()))
    return sorted({ln.strip().upper() for ln in path.read_text().splitlines() if ln.strip()})


def pull_availability(
    year: int, raw_cache_dir: Path, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Pull DISPATCHLOAD availability for the whole year (or a sub-window)."""
    raw_cache_dir.mkdir(parents=True, exist_ok=True)
    start_time = start or f"{year}/01/01 00:00:00"
    end_time = end or f"{year + 1}/01/01 00:00:00"
    log.info(
        "nemosis: fetching %s %s -> %s (raw cache %s). This table is large; "
        "the first run downloads a full year of MMS archives.",
        TABLE, start_time, end_time, raw_cache_dir,
    )
    df = _dynamic_data_compiler(
        start_time=start_time,
        end_time=end_time,
        table_name=TABLE,
        raw_data_location=str(raw_cache_dir),
        select_columns=SELECT_COLUMNS,
        fformat="parquet",
    )
    if df is None or len(df) == 0:
        raise RuntimeError(f"nemosis returned no rows for {TABLE} {start_time}..{end_time}")
    return df


def write_availability_for_duid(
    duid: str, rows: pd.DataFrame, year: int, out_dir: Path, overwrite: bool
) -> tuple[bool, str]:
    """Write one DUID's availability parquet. Partial years are written with a
    warning, matching the SCADA writer: downstream eligibility checks need to
    see a partial file rather than nothing at all."""
    out_file = out_dir / AVAILABILITY_SUBDIR / f"{duid}_{year}.parquet"
    if out_file.exists() and not overwrite:
        return True, f"SKIP (exists): {out_file}"
    if rows.empty:
        return False, f"FAIL (no rows for {duid} in {TABLE} {year})"

    rows = rows.set_index("SETTLEMENTDATE").sort_index()
    rows = rows[~rows.index.duplicated(keep="last")]
    out = pd.DataFrame(
        {
            "availability": pd.to_numeric(rows["AVAILABILITY"], errors="coerce"),
            "semidispatchcap": pd.to_numeric(
                rows.get("SEMIDISPATCHCAP", 0), errors="coerce"
            ).fillna(0).astype("int8"),
        }
    )
    out["availability"] = out["availability"].astype("float64")

    expected = 366 * 288 if pd.Timestamp(year=year, month=1, day=1).is_leap_year else 365 * 288
    coverage = 100.0 * len(out) / expected

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_file)
    if coverage < 99.0:
        return True, f"WARN (partial, {coverage:.1f}%): wrote {out_file} ({len(out)} rows)"
    return True, f"OK ({coverage:.1f}% coverage): wrote {out_file} ({len(out)} rows)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--raw-cache-dir", type=Path, default=Path("./nemosis_cache"))
    parser.add_argument(
        "--duid-list", type=Path,
        default=DEFAULT_OUT_DIR / "registry" / "nem_plant_registry.parquet",
        help="Restrict to these DUIDs (registry parquet / csv / text). Default: the plant registry.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--month", type=int, default=None,
        help="Fetch a single month (1-12) instead of the whole year -- useful for a "
             "cheap connectivity/schema check before committing to a full-year pull.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    duids = _read_duid_list(args.duid_list)
    log.info("Target DUIDs: %d (from %s)", len(duids), args.duid_list)

    # Month-by-month rather than one full-year pull. DISPATCHLOAD is ~4.5M rows
    # per month across all DUIDs, so a year in one frame is several GB before
    # filtering -- enough to get the process OOM-killed on a modest machine.
    # Pulling per month and keeping only the target DUIDs holds ~1/25th of that.
    months = [args.month] if args.month else list(range(1, 13))
    target = set(duids)
    parts: dict[str, list[pd.DataFrame]] = {}

    for month in months:
        start = f"{args.year}/{month:02d}/01 00:00:00"
        nxt_y, nxt_m = (args.year + 1, 1) if month == 12 else (args.year, month + 1)
        end = f"{nxt_y}/{nxt_m:02d}/01 00:00:00"

        df = pull_availability(args.year, args.raw_cache_dir, start=start, end=end)
        before = len(df)

        if "INTERVENTION" in df.columns:
            df = df[pd.to_numeric(df["INTERVENTION"], errors="coerce").fillna(0) == 0]

        df["DUID"] = df["DUID"].astype(str).str.strip().str.upper()
        df = df[df["DUID"].isin(target)]
        log.info(
            "%04d-%02d: %d rows -> %d after INTERVENTION=0 + DUID filter (%d distinct)",
            args.year, month, before, len(df), df["DUID"].nunique(),
        )

        for duid, rows in df.groupby("DUID", sort=False):
            parts.setdefault(str(duid), []).append(rows)
        del df

    log.info("Collected %d DUIDs across %d month(s)", len(parts), len(months))

    ok = fail = 0
    for duid in sorted(parts):
        rows = pd.concat(parts.pop(duid), ignore_index=True)
        success, msg = write_availability_for_duid(
            str(duid), rows, args.year, args.out_dir, args.overwrite
        )
        log.info(msg)
        ok += success
        fail += (not success)

    log.info("Done: %d written/skipped, %d failed", ok, fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

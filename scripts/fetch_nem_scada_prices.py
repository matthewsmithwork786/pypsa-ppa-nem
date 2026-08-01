#!/usr/bin/env python3
"""
One-time acquisition script: pull AEMO 5-minute SCADA and dispatch-price data via `nemosis`.

*** NOT PART OF THE STREAMLIT APP. NEVER IMPORTED BY `ppa/` OR `ui/`. ***

Run this in a SEPARATE environment with real network access to AEMO/NEMWEB
(this repo's dev sandbox blocks those domains at the egress-policy level).
Copy/commit the resulting output files into `data/cache/nem/` in this repo
afterwards -- that is what the Streamlit app actually reads at runtime
(see `ppa/data/nem_data.py`, which is cache-only and has no network imports).

Approach, confirmed to work per the user's own `nemosis` notebook
(`nemosis_march_2025_timeseries_mdavis_guide.ipynb`, whose output already lives
at `data/march_2025_pypsa_timeseries.csv` using DUIDs BODWF1/COLEASF1):

    from nemosis import dynamic_data_compiler

nemosis downloads AEMO's public MMS tables (DUDETAILSUMMARY, DISPATCH_UNIT_SCADA,
DISPATCHPRICE) into a local "raw cache" directory (parquet/feather under the hood,
managed by nemosis itself), then we re-load the cached tables with pandas and
reshape them into this repo's cache layout:

    data/cache/nem/scada/{DUID}_{year}.parquet   # index=5-min settlementdate, col 'scadavalue'
    data/cache/nem/price/rrp_{REGION}_{year}.parquet  # index=5-min settlementdate, col 'rrp'

TIMEZONE NOTE: all timestamps written by this script are tz-NAIVE NEM standard
time (AEST, UTC+10, no daylight saving) -- this is AEMO's native convention and
is NOT the same as the tz-aware UTC caches produced by `ppa/data/renewables_ninja.py`
and `ppa/data/entsoe_client.py`. Whoever wires `ppa/data/nem_data.py` up to
`ppa/data/timeseries_utils.py::_align_to_index` (which assigns positionally) must
account for this offset explicitly -- do not assume these indices are UTC.

LIMITATION (read before relying on the DUID auto-selection):
DUDETAILSUMMARY does NOT carry a fuel-type field, only DISPATCHTYPE ("GENERATOR"/
"LOAD"), region, and (sometimes) unit capacity fields depending on nemosis version.
It cannot tell wind/solar apart from coal/gas on its own. This script's default
DUID selection is therefore just "all GENERATOR DUIDs with reported capacity above
--min-capacity-mw" -- NOT filtered to wind/solar. To get an actually-correct
wind/solar DUID list, either:
  (a) pass --duid-list pointing at a CSV/text file of DUIDs -- e.g. the
      `duid` column written by `scripts/fetch_nem_plant_registry.py`
      (data/cache/nem/registry/nem_plant_registry.parquet), or your own AEMO
      "Generation Information" spreadsheet download, or
  (b) accept the fallback and post-filter later by cross-referencing the
      registry parquet before feeding DUIDs into the app.
This caveat is logged loudly at runtime (see `select_target_duids()`).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_nem_scada_prices")

NEM_REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]
EXPECTED_HOURLY_ROWS_NON_LEAP = 8760  # QA check: NEM has no DST, so this must hold exactly
EXPECTED_HOURLY_ROWS_LEAP = 8784
EXPECTED_5MIN_ROWS_NON_LEAP = 105_120  # 365 * 24 * 12
EXPECTED_5MIN_ROWS_LEAP = 105_408  # 366 * 24 * 12

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "cache" / "nem"


def _dynamic_data_compiler(*args, **kwargs):
    """
    Thin wrapper around nemosis's `dynamic_data_compiler`, the documented entry point
    for pulling AEMO MMS tables (start_time, end_time, table_name, raw_data_location,
    fformat=..., select_columns=...). Imported lazily so the rest of this script, and
    any --help invocation, works even if nemosis isn't installed.

    NOTE: an earlier version of this shim also tried falling back to `cache_compiler`,
    but that function's signature does not match how this script calls the compiler
    (it is nemosis's lower-level cache-refresh helper, not a drop-in replacement) --
    that fallback was silently wrong and has been removed. If `dynamic_data_compiler`
    is renamed in a future nemosis release, update this function to match the new
    entry point rather than reintroducing a mismatched fallback.
    """
    try:
        from nemosis import dynamic_data_compiler as _compiler
    except ImportError as exc:
        raise ImportError(
            "Could not import `dynamic_data_compiler` from `nemosis`. Install it with "
            "`pip install -r scripts/requirements-acquisition.txt` in a networked "
            "environment. If nemosis has renamed its API, check "
            "https://github.com/UNSW-CEEM/NEMOSIS for the current entry point."
        ) from exc
    return _compiler(*args, **kwargs)


def _pull_table(
    table_name: str,
    start_time: str,
    end_time: str,
    raw_cache_dir: Path,
    select_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Cache (if needed) and load one AEMO MMS table for the given window via nemosis."""
    raw_cache_dir.mkdir(parents=True, exist_ok=True)
    log.info("nemosis: fetching %s for %s -> %s (raw cache: %s)", table_name, start_time, end_time, raw_cache_dir)
    kwargs = dict(
        start_time=start_time,
        end_time=end_time,
        table_name=table_name,
        raw_data_location=str(raw_cache_dir),
        fformat="parquet",
    )
    if select_columns is not None:
        kwargs["select_columns"] = select_columns
    df = _dynamic_data_compiler(**kwargs)
    if df is None or len(df) == 0:
        raise RuntimeError(f"nemosis returned no rows for table={table_name} window={start_time}..{end_time}")
    return df


def select_target_duids(
    year: int,
    raw_cache_dir: Path,
    min_capacity_mw: float,
    duid_list_path: Path | None,
) -> list[str]:
    """
    Return the list of DUIDs to pull SCADA for.

    If --duid-list is supplied, it wins outright. Accepted formats:
      - `.parquet` with a `duid` column (e.g. the output of
        `fetch_nem_plant_registry.py`, `data/cache/nem/registry/nem_plant_registry.parquet`)
      - `.csv` with a `duid` column (falls back to the first column if none named `duid`)
      - plain newline-delimited text file of DUIDs
    Otherwise falls back to "every GENERATOR DUID in DUDETAILSUMMARY for this year
    with capacity >= threshold", which is NOT wind/solar-filtered (see module
    docstring) -- this is logged as an explicit caveat so it's not mistaken for a
    real fuel-type filter.
    """
    if duid_list_path is not None:
        duid_list_path = Path(duid_list_path)
        if not duid_list_path.exists():
            raise FileNotFoundError(f"--duid-list path does not exist: {duid_list_path}")
        suffix = duid_list_path.suffix.lower()
        if suffix == ".parquet":
            df = pd.read_parquet(duid_list_path)
            if "duid" not in df.columns:
                raise ValueError(
                    f"--duid-list parquet {duid_list_path} has no `duid` column "
                    f"(columns: {list(df.columns)})."
                )
            duids = sorted(set(df["duid"].astype(str).str.strip().str.upper()))
        elif suffix == ".csv":
            df = pd.read_csv(duid_list_path)
            col = "duid" if "duid" in df.columns else df.columns[0]
            duids = sorted(set(df[col].astype(str).str.strip().str.upper()))
        else:
            text = duid_list_path.read_text()
            duids = sorted({line.strip().upper() for line in text.splitlines() if line.strip()})
        log.info("Using explicit --duid-list override: %d DUIDs from %s", len(duids), duid_list_path)
        return duids

    log.warning(
        "No --duid-list supplied. Falling back to 'all GENERATOR DUIDs in DUDETAILSUMMARY "
        "for %d with capacity >= %.0f MW'. NOTE: DUDETAILSUMMARY has no fuel-type field, so "
        "this list is NOT restricted to wind/solar -- it will include coal/gas/hydro/battery "
        "DUIDs too. Cross-reference against scripts/fetch_nem_plant_registry.py's output "
        "(data/cache/nem/registry/nem_plant_registry.parquet) to narrow this down, or pass "
        "--duid-list explicitly.",
        year,
        min_capacity_mw,
    )
    start_time = f"{year}/01/01 00:00:00"
    end_time = f"{year}/01/02 00:00:00"  # DUDETAILSUMMARY is a slowly-changing dim table; one day is enough
    dudetail = _pull_table("DUDETAILSUMMARY", start_time, end_time, raw_cache_dir)
    dudetail.columns = [c.upper() for c in dudetail.columns]

    if "DISPATCHTYPE" in dudetail.columns:
        dudetail = dudetail[dudetail["DISPATCHTYPE"].astype(str).str.upper() == "GENERATOR"]
    else:
        log.warning("DUDETAILSUMMARY has no DISPATCHTYPE column in this nemosis version -- skipping that filter.")

    capacity_col = None
    for candidate in ("MAXCAPACITY", "REGISTEREDCAPACITY", "MAX CAPACITY"):
        if candidate in dudetail.columns:
            capacity_col = candidate
            break
    if capacity_col is not None:
        dudetail = dudetail[pd.to_numeric(dudetail[capacity_col], errors="coerce") >= min_capacity_mw]
    else:
        log.warning(
            "No recognised capacity column found in DUDETAILSUMMARY (columns: %s) -- "
            "cannot apply --min-capacity-mw filter to the fallback DUID list.",
            list(dudetail.columns),
        )

    duid_col = "DUID" if "DUID" in dudetail.columns else dudetail.columns[0]
    duids = sorted(set(dudetail[duid_col].astype(str).str.strip().str.upper()))
    log.info("Fallback DUID selection: %d DUIDs (unfiltered by fuel type -- see caveat above)", len(duids))
    return duids


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _shift_to_interval_start_and_slice(df: pd.DataFrame, settlementdate_col: str, year: int) -> pd.DataFrame:
    """Convert AEMO's interval-ENDING `SETTLEMENTDATE` to interval-BEGINNING and slice to `year`.

    AEMO's SETTLEMENTDATE for 5-minute dispatch tables is interval-ENDING (e.g. the
    row timestamped 00:05:00 covers the interval [00:00:00, 00:05:00)). Naively
    pulling `{year}/01/01 00:00:00` -> `{year+1}/01/01 00:00:00` and using
    SETTLEMENTDATE as-is yields 8761 hourly bins (it includes the interval ending
    exactly at the year boundary from the previous year, AND the one at the start
    of next year), not 8760. Shifting back by one 5-minute interval converts to
    interval-BEGINNING semantics, after which a strict `[year-01-01, (year+1)-01-01)`
    slice gives exactly the right full-year window.
    """
    out = df.copy()
    out[settlementdate_col] = pd.to_datetime(out[settlementdate_col]) - pd.Timedelta("5min")
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year + 1}-01-01")
    mask = (out[settlementdate_col] >= start) & (out[settlementdate_col] < end)
    return out[mask]


def _assert_full_year_hourly(df: pd.DataFrame, value_col: str, year: int, label: str) -> None:
    """QA gate: hourly resample must yield exactly 8760 (non-leap) / 8784 (leap) rows.

    This catches interval-boundary/timezone handling bugs early (NEM has no DST so
    any deviation from the expected count means the index is wrong, not that the
    market actually behaves that way).
    """
    expected = EXPECTED_HOURLY_ROWS_LEAP if _is_leap(year) else EXPECTED_HOURLY_ROWS_NON_LEAP
    hourly = df[value_col].resample("1h").mean()
    if len(hourly) != expected:
        raise AssertionError(
            f"{label}: expected exactly {expected} hourly rows for "
            f"{'leap' if _is_leap(year) else 'non-leap'} year {year}, got {len(hourly)}. "
            f"This usually means an interval-boundary/timezone bug or a truncated pull -- "
            f"do not write this file."
        )


def _coverage_pct(df: pd.DataFrame, year: int) -> float:
    """Log-friendly coverage: rows present vs. expected 5-min intervals for the full year."""
    expected = EXPECTED_5MIN_ROWS_LEAP if _is_leap(year) else EXPECTED_5MIN_ROWS_NON_LEAP
    return 100.0 * len(df) / expected


def _coerce_numeric(df: pd.DataFrame, col: str, label: str) -> pd.DataFrame:
    """Coerce `col` to numeric, warning if coercion introduces NaNs that weren't already there."""
    raw = df[col]
    was_na = raw.isna()
    coerced = pd.to_numeric(raw, errors="coerce")
    new_na = coerced.isna() & ~was_na
    if new_na.any():
        log.warning(
            "%s: pd.to_numeric coercion on column '%s' introduced %d new NaN value(s) "
            "(were non-null, non-numeric before coercion) -- this may indicate a dtype "
            "problem returned by nemosis. Sample bad values: %s",
            label,
            col,
            int(new_na.sum()),
            raw[new_na].unique()[:5].tolist(),
        )
    out = df.copy()
    out[col] = coerced
    return out


def pull_scada_all_duids(year: int, raw_cache_dir: Path) -> pd.DataFrame:
    """Pull DISPATCH_UNIT_SCADA for the FULL year ONCE (not once per DUID -- see B2).

    Returns a DataFrame with columns SETTLEMENTDATE (interval-beginning, sliced to
    `year`), DUID, SCADAVALUE.
    """
    start_time = f"{year}/01/01 00:00:00"
    end_time = f"{year + 1}/01/01 00:00:00"
    scada = _pull_table(
        "DISPATCH_UNIT_SCADA",
        start_time,
        end_time,
        raw_cache_dir,
        select_columns=["SETTLEMENTDATE", "DUID", "SCADAVALUE"],
    )
    scada.columns = [c.upper() for c in scada.columns]
    scada = _shift_to_interval_start_and_slice(scada, "SETTLEMENTDATE", year)
    return scada


def pull_price_all_regions(year: int, raw_cache_dir: Path) -> pd.DataFrame:
    """Pull DISPATCHPRICE (INTERVENTION==0) for the FULL year ONCE (not once per region -- see B2)."""
    start_time = f"{year}/01/01 00:00:00"
    end_time = f"{year + 1}/01/01 00:00:00"
    price = _pull_table(
        "DISPATCHPRICE",
        start_time,
        end_time,
        raw_cache_dir,
        select_columns=["SETTLEMENTDATE", "REGIONID", "RRP", "INTERVENTION"],
    )
    price.columns = [c.upper() for c in price.columns]
    if "INTERVENTION" in price.columns:
        price = price[pd.to_numeric(price["INTERVENTION"], errors="coerce") == 0]
    else:
        log.warning("DISPATCHPRICE has no INTERVENTION column in this nemosis version -- cannot filter it.")
    price = _shift_to_interval_start_and_slice(price, "SETTLEMENTDATE", year)
    return price


def write_scada_for_duid(
    duid: str,
    duid_rows: pd.DataFrame,
    year: int,
    out_dir: Path,
    overwrite: bool,
) -> tuple[bool, str]:
    """Write the per-DUID SCADA parquet from an already-pulled+filtered group.

    Unlike prices, a partial year for a single DUID is a LEGITIMATE, expected
    outcome (e.g. a plant commissioned mid-year) -- so QA failure here is a WARNING,
    not a hard failure: the file is still written so that downstream,
    `ppa/data/nem_data.py`'s "generated for the whole year" eligibility check can
    see the (partial) cache file and correctly exclude the DUID, rather than the
    DUID silently having no cache file at all (which looks the same as "never
    fetched" and defeats that check).
    """
    out_file = out_dir / "scada" / f"{duid}_{year}.parquet"
    if out_file.exists() and not overwrite:
        return True, f"SKIP (exists): {out_file}"

    if duid_rows.empty:
        return False, f"FAIL (no rows for DUID {duid} in DISPATCH_UNIT_SCADA {year})"

    duid_rows = duid_rows.set_index("SETTLEMENTDATE").sort_index()
    series_df = duid_rows[["SCADAVALUE"]].rename(columns={"SCADAVALUE": "scadavalue"})
    series_df = series_df[~series_df.index.duplicated(keep="last")]
    series_df = _coerce_numeric(series_df, "scadavalue", f"SCADA[{duid}]")

    coverage = _coverage_pct(series_df, year)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        _assert_full_year_hourly(series_df, "scadavalue", year, f"SCADA[{duid}]")
    except AssertionError as exc:
        log.warning(
            "SCADA[%s]: partial-year data (coverage %.1f%% of expected 5-min intervals) -- "
            "writing anyway so the downstream 'generated for the whole year' eligibility "
            "check in ppa/data/nem_data.py can see and exclude this DUID. Detail: %s",
            duid,
            coverage,
            exc,
        )
        series_df.to_parquet(out_file)
        return True, f"WARN (partial, {coverage:.1f}% coverage): wrote {out_file} ({len(series_df)} rows)"

    series_df.to_parquet(out_file)
    return True, f"OK (full year, {coverage:.1f}% coverage): wrote {out_file} ({len(series_df)} rows)"


def write_price_for_region(
    region: str,
    region_rows: pd.DataFrame,
    year: int,
    out_dir: Path,
    overwrite: bool,
) -> tuple[bool, str]:
    """Write the per-region price parquet from an already-pulled+filtered group.

    Unlike SCADA, an incomplete year for a region's RRP series indicates a real
    data-quality problem (every region should have a full year of dispatch prices)
    -- so QA failure here HARD-FAILS (does not write).
    """
    out_file = out_dir / "price" / f"rrp_{region}_{year}.parquet"
    if out_file.exists() and not overwrite:
        return True, f"SKIP (exists): {out_file}"

    if region_rows.empty:
        return False, f"FAIL (no rows for region {region} in DISPATCHPRICE {year})"

    region_rows = region_rows.set_index("SETTLEMENTDATE").sort_index()
    series_df = region_rows[["RRP"]].rename(columns={"RRP": "rrp"})
    series_df = series_df[~series_df.index.duplicated(keep="last")]
    series_df = _coerce_numeric(series_df, "rrp", f"RRP[{region}]")

    coverage = _coverage_pct(series_df, year)
    try:
        _assert_full_year_hourly(series_df, "rrp", year, f"RRP[{region}]")
    except AssertionError as exc:
        return False, f"FAIL (QA, {coverage:.1f}% coverage): {exc}"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    series_df.to_parquet(out_file)
    return True, f"OK (full year, {coverage:.1f}% coverage): wrote {out_file} ({len(series_df)} rows)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch AEMO 5-minute SCADA (per-DUID) and DISPATCHPRICE (per-region) data via "
            "nemosis and cache it into this repo's data/cache/nem/ layout. Must be run with "
            "real network access to AEMO/NEMWEB -- see scripts/README.md."
        )
    )
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--min-capacity-mw", type=float, default=30.0)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--raw-cache-dir", type=Path, default=Path("./nemosis_cache"))
    parser.add_argument(
        "--duid-list",
        type=Path,
        default=None,
        help=(
            "Optional .parquet (with a `duid` column, e.g. "
            "data/cache/nem/registry/nem_plant_registry.parquet), CSV (with a `duid` column), "
            "or newline-delimited text file listing the exact DUIDs to pull SCADA for. If "
            "omitted, falls back to 'all GENERATOR DUIDs above --min-capacity-mw' (NOT "
            "wind/solar filtered -- see module docstring)."
        ),
    )
    parser.add_argument("--overwrite", action="store_true", help="Re-write output files even if they already exist.")
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    raw_cache_dir: Path = args.raw_cache_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("=== Step 1: selecting target DUIDs ===")
    duids = select_target_duids(args.year, raw_cache_dir, args.min_capacity_mw, args.duid_list)
    if not duids:
        log.error("No DUIDs selected -- nothing to do.")
        return 1
    log.info("Target DUIDs (%d): %s", len(duids), ", ".join(duids[:20]) + (" ..." if len(duids) > 20 else ""))

    log.info("=== Step 2: SCADA for %d (single bulk pull, then split per DUID -- see B2) ===", args.year)
    scada_all = pull_scada_all_duids(args.year, raw_cache_dir)
    scada_groups = {
        duid_val: group for duid_val, group in scada_all.assign(
            DUID=scada_all["DUID"].astype(str).str.strip().str.upper()
        ).groupby("DUID")
    }
    scada_results: list[tuple[str, bool, str]] = []
    full_year_duids: list[str] = []
    partial_duids: list[str] = []
    for duid in duids:
        group = scada_groups.get(duid, pd.DataFrame(columns=["SETTLEMENTDATE", "DUID", "SCADAVALUE"]))
        ok, msg = write_scada_for_duid(duid, group, args.year, out_dir, args.overwrite)
        scada_results.append((duid, ok, msg))
        if ok and msg.startswith("OK"):
            full_year_duids.append(duid)
        elif ok and msg.startswith("WARN"):
            partial_duids.append(duid)
        log.info("%s -> %s", duid, msg)
    log.info(
        "SCADA QA: %d/%d DUIDs full-year, %d partial-year (written with a warning; see "
        "eligibility filtering note in ppa/data/nem_data.py), %d failed outright.",
        len(full_year_duids),
        len(duids),
        len(partial_duids),
        len(duids) - len(full_year_duids) - len(partial_duids),
    )

    log.info("=== Step 3: DISPATCHPRICE for %d (single bulk pull, then split per region -- see B2) ===", args.year)
    price_all = pull_price_all_regions(args.year, raw_cache_dir)
    price_groups = {
        region_val: group for region_val, group in price_all.assign(
            REGIONID=price_all["REGIONID"].astype(str).str.strip().str.upper()
        ).groupby("REGIONID")
    }
    price_results: list[tuple[str, bool, str]] = []
    for region in NEM_REGIONS:
        group = price_groups.get(region, pd.DataFrame(columns=["SETTLEMENTDATE", "REGIONID", "RRP"]))
        ok, msg = write_price_for_region(region, group, args.year, out_dir, args.overwrite)
        price_results.append((region, ok, msg))
        log.info("%s -> %s", region, msg)

    # --- Summary ---
    scada_ok = [d for d, ok, _ in scada_results if ok]
    scada_failed = [(d, m) for d, ok, m in scada_results if not ok]
    price_ok = [r for r, ok, _ in price_results if ok]
    price_failed = [(r, m) for r, ok, m in price_results if not ok]

    print("\n" + "=" * 70)
    print("FETCH SUMMARY")
    print("=" * 70)
    print(f"Year: {args.year}")
    print(f"SCADA: {len(scada_ok)}/{len(duids)} DUIDs OK -> {out_dir / 'scada'}")
    print(f"  Full-year: {len(full_year_duids)}, partial-year (written w/ warning): {len(partial_duids)}")
    if scada_failed:
        print(f"  FAILED DUIDs ({len(scada_failed)}):")
        for d, m in scada_failed:
            print(f"    - {d}: {m}")
    print(f"Prices: {len(price_ok)}/{len(NEM_REGIONS)} regions OK -> {out_dir / 'price'}")
    if price_failed:
        print(f"  FAILED regions ({len(price_failed)}):")
        for r, m in price_failed:
            print(f"    - {r}: {m}")
    print("=" * 70)
    print(f"Next step: copy/commit '{out_dir}' into this repo's data/cache/nem/ directory.")

    return 0 if not scada_failed and not price_failed else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
One-time acquisition script: pull AER's free quarterly "base futures price"
chart-data CSV and normalize it into this repo's hedge-price cache schema.

*** NOT PART OF THE STREAMLIT APP. NEVER IMPORTED BY `ppa/` OR `ui/`. ***

Run this in a SEPARATE environment with real network access (this repo's dev
sandbox blocks `aer.gov.au`). Copy/commit the resulting
`aer_base_futures_{year}.parquet` into `data/cache/nem/hedge/` in this repo --
that's what `ppa/data/aer_futures.py` actually reads at runtime (cache-only,
no network imports).

Background page (human-readable, chart + "export data" style controls):
    https://www.aer.gov.au/wholesale-markets/wholesale-statistics/quarterly-base-futures-prices-and-volume-traded

UNVERIFIED: the exact CSV download URL/format was NOT directly fetchable from
this sandboxed planning session (aer.gov.au is blocked here). `CSV_URL` below
is a best-effort placeholder pointing at the human-readable page -- open that
page in a browser, find the actual "download data"/"export CSV" link or XHR
endpoint it calls (inspect network tab, or look for a direct .csv link), and
update the `CSV_URL` constant accordingly before running this script for real.
It is deliberately kept as a single clearly-labeled constant near the top so
that's a one-line fix.
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_aer_futures")

# --- UPDATE THIS before running for real (see module docstring) ---
# Best-effort placeholder; the real quarterly base-futures CSV export URL
# should be confirmed on the live AER wholesale-charts page, since this
# sandboxed session could not reach aer.gov.au to inspect it directly.
CSV_URL = (
    "https://www.aer.gov.au/wholesale-markets/wholesale-statistics/"
    "quarterly-base-futures-prices-and-volume-traded"
)
# -------------------------------------------------------------------

HUMAN_PAGE_URL = (
    "https://www.aer.gov.au/wholesale-markets/wholesale-statistics/"
    "quarterly-base-futures-prices-and-volume-traded"
)

NEM_REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "cache" / "nem" / "hedge"

# Candidate column names the raw AER CSV might use, tried in order.
# UNVERIFIED -- confirm against the real downloaded file and adjust.
_REGION_COL_CANDIDATES = ["region", "Region", "NEM Region", "REGIONID"]
_QUARTER_COL_CANDIDATES = ["quarter", "Quarter", "Delivery Quarter", "delivery_period"]
_PRICE_COL_CANDIDATES = ["price", "Price", "Base Futures Price", "price_aud_mwh", "AUD/MWh"]
_AS_AT_COL_CANDIDATES = ["as_at_date", "As at", "Trade Date", "date", "Date"]


class AerFetchError(RuntimeError):
    """Raised when the AER download/parse doesn't match expectations -- fail loudly, don't guess."""


def _first_matching_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for candidate in candidates:
        actual = lower_map.get(candidate.lower().strip())
        if actual is not None:
            return actual
    return None


def download_raw_csv(url: str = CSV_URL, timeout: int = 60) -> bytes:
    """Download the raw CSV bytes from AER. Fails loudly on any HTTP error."""
    log.info("Downloading AER base-futures data from %s", url)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AerFetchError(
            f"Failed to download AER base-futures CSV from {url}: {exc}\n"
            f"Open {HUMAN_PAGE_URL} in a browser, find the current CSV export "
            f"link/endpoint, and update CSV_URL at the top of this script."
        ) from exc

    content_type = resp.headers.get("Content-Type", "")
    content_type_lower = content_type.lower()
    if "text/html" in content_type_lower:
        raise AerFetchError(
            f"Response Content-Type ('{content_type}') is text/html -- CSV_URL is almost "
            f"certainly pointing at the human-readable landing page rather than a direct CSV/"
            f"data export. Open {HUMAN_PAGE_URL} in a browser, find the actual 'download data'/"
            f"'export CSV' link or XHR endpoint it calls, and update the CSV_URL constant at "
            f"the top of this script."
        )
    if "csv" not in content_type_lower and "text" not in content_type_lower and "octet-stream" not in content_type_lower:
        log.warning(
            "Response Content-Type ('%s') doesn't look like a CSV -- the URL may be pointing at "
            "an unexpected resource. Will attempt to parse it anyway, but expect this to fail; "
            "if so, update CSV_URL (see module docstring).",
            content_type,
        )
    return resp.content


def parse_raw_csv(raw_bytes: bytes, year: int) -> pd.DataFrame:
    """Parse the raw AER CSV into the normalized hedge-cache schema for a given year.

    Raises AerFetchError with a clear message (rather than silently writing bad
    data) if the expected columns can't be located.
    """
    try:
        raw_df = pd.read_csv(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise AerFetchError(
            f"Could not parse the downloaded content as CSV: {exc}\n"
            f"This usually means CSV_URL is pointing at an HTML page instead of a raw data "
            f"export. Open {HUMAN_PAGE_URL}, locate the real 'export'/'download' link or the "
            f"underlying data endpoint it calls, and update CSV_URL at the top of this script."
        ) from exc

    if raw_df.empty:
        raise AerFetchError(
            "Downloaded AER CSV parsed but contained zero rows. This usually means CSV_URL is "
            f"pointing at the wrong resource (e.g. an empty export, or a differently-scoped "
            f"endpoint). Open {HUMAN_PAGE_URL}, locate the real 'export'/'download' link or the "
            f"underlying data endpoint it calls, and update CSV_URL at the top of this script."
        )

    region_col = _first_matching_column(raw_df, _REGION_COL_CANDIDATES)
    quarter_col = _first_matching_column(raw_df, _QUARTER_COL_CANDIDATES)
    price_col = _first_matching_column(raw_df, _PRICE_COL_CANDIDATES)
    as_at_col = _first_matching_column(raw_df, _AS_AT_COL_CANDIDATES)

    missing = [
        name
        for name, col in [
            ("region", region_col),
            ("quarter", quarter_col),
            ("price", price_col),
        ]
        if col is None
    ]
    if missing:
        raise AerFetchError(
            f"Could not locate expected column(s) {missing} in the downloaded AER CSV. "
            f"Actual columns found: {list(raw_df.columns)}. The AER export format has likely "
            f"changed or CSV_URL is wrong -- inspect {HUMAN_PAGE_URL} manually, re-export the "
            f"data, and either adjust the *_COL_CANDIDATES lists in this script or hand-adapt "
            f"the export to the schema documented in this script's docstring."
        )

    df = raw_df.copy()
    df["region"] = df[region_col].astype(str).str.strip().str.upper()

    unknown_regions = sorted(set(df["region"].unique()) - set(NEM_REGIONS))
    if unknown_regions:
        log.warning(
            "Parsed region value(s) %s do not match any of the known 5 NEM regions (%s). "
            "AER's export may use a different naming convention (e.g. 'NSW' instead of "
            "'NSW1') that will NOT join cleanly against price/rrp_{REGION}_%s.parquet "
            "filenames downstream -- inspect the raw CSV's region column and add a mapping "
            "here (e.g. a NSW->NSW1 style normalisation) before relying on this output.",
            unknown_regions,
            NEM_REGIONS,
            year,
        )

    df["quarter_label"] = df[quarter_col].astype(str).str.strip()
    df["price_aud_mwh"] = pd.to_numeric(df[price_col], errors="coerce")
    df["product"] = "Base"
    if as_at_col is not None:
        df["as_at_date"] = pd.to_datetime(df[as_at_col], errors="coerce")
    else:
        log.warning(
            "No 'as at' / trade-date column found in the AER export -- stamping as_at_date with "
            "today's download date instead. Consider updating _AS_AT_COL_CANDIDATES if the real "
            "column has a different header."
        )
        df["as_at_date"] = pd.Timestamp(date.today())

    # Keep only rows plausibly belonging to the requested year (by quarter_label containing
    # the year, e.g. "Q1-2025") -- best-effort since the raw label format is unverified.
    year_mask = df["quarter_label"].str.contains(str(year))
    if not year_mask.any():
        raise AerFetchError(
            f"No rows in the downloaded AER data matched year {year} via a substring match on "
            f"quarter_label (sample labels seen: {df['quarter_label'].unique()[:10].tolist()}). "
            f"The quarter-label format is unverified against the live export -- inspect it and "
            f"adjust the year filter in this script if needed."
        )
    df = df[year_mask]

    result = df[["region", "quarter_label", "product", "price_aud_mwh", "as_at_date"]].reset_index(drop=True)

    if result["price_aud_mwh"].isna().all():
        raise AerFetchError(
            f"All parsed price_aud_mwh values are NaN after numeric coercion (source column "
            f"'{price_col}'). The price column likely contains formatting (e.g. '$', commas) "
            f"that needs stripping -- inspect the raw CSV and adjust parse_raw_csv()."
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download AER's free quarterly base-futures price data and cache it into "
            "data/cache/nem/hedge/. Must be run with real network access to aer.gov.au -- "
            "see scripts/README.md. Fails loudly (non-zero exit) rather than writing bad data "
            "if the download/parse doesn't match expectations."
        )
    )
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-write the output parquet even if it already exists (default: skip fetch).",
    )
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"aer_base_futures_{args.year}.parquet"

    if out_file.exists() and not args.overwrite:
        log.info("%s already exists and --overwrite was not passed -- skipping fetch.", out_file)
        print(f"SKIP (exists): {out_file}")
        return 0

    try:
        raw_bytes = download_raw_csv(CSV_URL)
        df = parse_raw_csv(raw_bytes, args.year)
    except AerFetchError as exc:
        log.error("%s", exc)
        print("\n" + "=" * 70)
        print("AER FUTURES FETCH FAILED")
        print("=" * 70)
        print(str(exc))
        print(f"\nManual fallback: open {HUMAN_PAGE_URL} in a browser, export/copy the base "
              f"futures price data by hand into a CSV matching the schema in this script's "
              f"docstring (region, quarter_label, product, price_aud_mwh, as_at_date), and "
              f"convert it to parquet at {out_file} yourself.")
        print("=" * 70)
        return 1

    df.to_parquet(out_file, index=False)

    print("\n" + "=" * 70)
    print("AER FUTURES FETCH SUMMARY")
    print("=" * 70)
    print(f"Year: {args.year}")
    print(f"Rows written: {len(df)}")
    print(f"Regions covered: {sorted(df['region'].unique().tolist())}")
    print(f"Quarters covered: {sorted(df['quarter_label'].unique().tolist())}")
    print(f"Output file: {out_file}")
    print("=" * 70)
    print(f"Next step: copy/commit '{out_file}' into this repo's data/cache/nem/hedge/ directory.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

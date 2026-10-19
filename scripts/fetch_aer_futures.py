#!/usr/bin/env python3
"""Download AER quarterly base-futures data and write the hedge cache schema.

Requires the separate acquisition environment; see `scripts/README.md`. The script
follows AER's download link where available and normalises published regions and
quarters into `data/cache/nem/hedge/aer_base_futures_<year>.parquet`."""
from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_aer_futures")

HUMAN_PAGE_URL = (
    "https://www.aer.gov.au/industry/registers/charts/"
    "quarterly-base-futures-prices-and-volume-traded"
)

# The CSV filename carries a publication timestamp and changes each release, so
# it is discovered from the "Download CSV" link on HUMAN_PAGE_URL rather than
# hard-coded. This is the value seen on 2026-08-05, kept only as a fallback.
CSV_URL = (
    "https://www.aer.gov.au/sites/default/files/2026-04/"
    "AER_Contract%20prices_Quarterly%20base%20future%20prices%20and%20"
    "volume%20traded%20DATA_2_20260407173757.CSV"
)

# Wide-format region columns -> NEM region ids. AER publishes no Tasmanian
# series (ASX Energy lists no TAS base future), so TAS1 is absent by design.
_WIDE_REGION_PREFIXES = {
    "queensland": "QLD1",
    "new south wales": "NSW1",
    "victoria": "VIC1",
    "south australia": "SA1",
}

NEM_REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "cache" / "nem" / "hedge"

DEFAULT_PRODUCT = "Base"

_QUARTER_COL_CANDIDATES = ["quarter", "Quarter", "Delivery Quarter", "delivery_period"]


class AerFetchError(RuntimeError):
    """Raised when the AER download/parse doesn't match expectations -- fail loudly, don't guess."""


def _first_matching_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for candidate in candidates:
        actual = lower_map.get(candidate.lower().strip())
        if actual is not None:
            return actual
    return None


def _discover_csv_url(page_html_locator) -> str | None:
    """Find the current 'Download CSV' href on the chart page."""
    for a in page_html_locator.locator("a").all():
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        if href.lower().endswith(".csv"):
            if href.startswith("/"):
                return "https://www.aer.gov.au" + href
            return href
    return None


def download_raw_csv(url: str | None = None, timeout: int = 180) -> "tuple[bytes, str]":
    """Download the raw CSV bytes from AER via headless Chromium.

    aer.gov.au is behind Akamai bot verification: a plain HTTP GET returns a
    JavaScript interstitial, not data, so the CSV is pulled inside a browser
    context that has cleared the check. The download link carries a publication
    timestamp, so it is discovered from the page rather than assumed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment problem
        raise AerFetchError(
            "This script needs Playwright to get past aer.gov.au's bot check: "
            "`pip install playwright && playwright install chromium`."
        ) from exc

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(user_agent=ua)
            page = ctx.new_page()
            log.info("Opening %s to clear the bot check", HUMAN_PAGE_URL)
            page.goto(HUMAN_PAGE_URL, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(15000)

            csv_url = url or _discover_csv_url(page) or CSV_URL
            log.info("Downloading AER base-futures CSV from %s", csv_url)
            resp = ctx.request.get(csv_url, timeout=timeout * 1000)
            if resp.status != 200:
                raise AerFetchError(
                    f"AER returned HTTP {resp.status} for {csv_url}. Open "
                    f"{HUMAN_PAGE_URL} and check the 'Download CSV' link."
                )
            body = resp.body()
        finally:
            browser.close()

    if b"<html" in body[:2000].lower():
        raise AerFetchError(
            "Downloaded content looks like HTML (bot check not cleared). Re-run; "
            f"if it persists, open {HUMAN_PAGE_URL} and grab the CSV by hand."
        )
    return body, csv_url


def parse_raw_csv(raw_bytes: bytes, year: int, as_at: date) -> pd.DataFrame:
    """Normalise AER's wide export into the hedge-cache schema.

    Input has one row per quarter and one column per region, e.g.
    ``Quarter, Queensland price ($ per megawatt hour), ... , Queensland volume``.
    Output is long: region / quarter_label / product / price_aud_mwh /
    as_at_date. Volume columns are dropped -- only the price series is in scope
    (`cal_hedge_fraction` is a separate user input, never derived from AER).

    `year` names the output cache file, it does NOT filter the quarters: AER
    publishes forward quarters (2026..2029), while `year` is the app's NEM
    weather year. Filtering on it would discard every row.
    """
    try:
        raw_df = pd.read_csv(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise AerFetchError(
            f"Could not parse the downloaded content as CSV: {exc}\n"
            f"Open {HUMAN_PAGE_URL} and confirm the 'Download CSV' link."
        ) from exc

    if raw_df.empty:
        raise AerFetchError("Downloaded AER CSV parsed but contained zero rows.")

    quarter_col = _first_matching_column(raw_df, _QUARTER_COL_CANDIDATES)
    if quarter_col is None:
        raise AerFetchError(
            f"No quarter column in the AER export. Columns: {list(raw_df.columns)}."
        )

    price_cols: dict[str, str] = {}
    for col in raw_df.columns:
        low = str(col).strip().lower()
        if "price" not in low:
            continue
        for prefix, region in _WIDE_REGION_PREFIXES.items():
            if low.startswith(prefix):
                price_cols[region] = col
    if not price_cols:
        raise AerFetchError(
            "Found no '<region> price' columns in the AER export. Columns: "
            f"{list(raw_df.columns)}. The export format has changed -- update "
            "_WIDE_REGION_PREFIXES."
        )

    missing_regions = sorted(set(_WIDE_REGION_PREFIXES.values()) - set(price_cols))
    if missing_regions:
        log.warning("No price column for %s in this export", missing_regions)
    log.info("Regions parsed: %s (AER publishes no TAS1 series)", sorted(price_cols))

    frames = []
    for region, col in sorted(price_cols.items()):
        part = pd.DataFrame(
            {
                "region": region,
                "quarter_label": raw_df[quarter_col].astype(str).str.strip(),
                "product": DEFAULT_PRODUCT,
                "price_aud_mwh": pd.to_numeric(raw_df[col], errors="coerce"),
            }
        )
        frames.append(part)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["price_aud_mwh"].notna()].reset_index(drop=True)

    if df.empty:
        raise AerFetchError(
            "Every parsed price was NaN -- the price columns likely carry "
            "formatting that needs stripping. Inspect the raw CSV."
        )

    # AER's quote date comes from the source page, not the CSV publication timestamp.
    df["as_at_date"] = pd.Timestamp(as_at)

    return df[["region", "quarter_label", "product", "price_aud_mwh", "as_at_date"]]

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
    parser.add_argument("--input-csv", type=Path, help="Import an already downloaded AER CSV without network access.")
    parser.add_argument("--as-at", type=date.fromisoformat, help="Price observation/settlement date stated on the AER source page (YYYY-MM-DD), not the file publication date.")
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

    if args.as_at is None:
        parser.error("--as-at is required when refreshing prices; use the price date stated on the AER source page.")

    try:
        raw_bytes = args.input_csv.read_bytes() if args.input_csv else download_raw_csv()[0]
        df = parse_raw_csv(raw_bytes, args.year, args.as_at)
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

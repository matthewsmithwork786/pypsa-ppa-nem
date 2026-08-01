"""Cache-only reader for AER quarterly "indicative base futures price" data.

Mirrors `ppa.data.nem_data`'s no-network-import discipline exactly: this
module NEVER makes a network call and NEVER imports `requests`/`urllib`/
`httpx`/`nemosis`/`socket`/`streamlit`. It only reads a parquet file that was
produced offline by `scripts/fetch_aer_futures.py` (run in a separate,
non-sandboxed environment with real access to aer.gov.au) and copied into
`data/cache/nem/hedge/`.

Scope note (important, read before "completing" anything here): AER publishes
a *price* series (this module). `cal_hedge_fraction` on `ppa.scenario.Scenario`
is a completely separate, pure user input describing what share of the
offtaker's own load is hedged at the forward price -- it has no AER analogue
and this module must never compute or default it. Only `cal_forward_price`
(via `forward_price_for_scenario`) is in scope here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ppa.data.nem_data import DEFAULT_REGION, DEFAULT_YEAR, NEM_CACHE_DIR, NEM_REGIONS

# ── Constants ────────────────────────────────────────────────────────────────

HEDGE_SUBDIR = "hedge"
FUTURES_FILENAME = "aer_base_futures_{year}.parquet"
AER_COLUMNS = ["region", "quarter_label", "product", "price_aud_mwh", "as_at_date"]
DEFAULT_PRODUCT = "Base"
_REGION_ALIASES = {"NSW": "NSW1", "QLD": "QLD1", "SA": "SA1", "TAS": "TAS1", "VIC": "VIC1"}
DISCLAIMER_TEMPLATE = "Indicative only — AER published base futures, non-tradable, as at {as_at}."
DISCLAIMER_UNKNOWN_DATE = "Indicative only — AER published base futures, non-tradable (as-at date unknown)."
SOURCE_MANUAL = "manual"
SOURCE_AER = "aer_indicative"

_QUARTER_RE = re.compile(
    r"(?:Q(?P<q1>[1-4])[\s\-]*(?P<y1>\d{4})|(?P<y2>\d{4})[\s\-]*Q(?P<q2>[1-4]))",
    re.IGNORECASE,
)


# ── Path helpers ─────────────────────────────────────────────────────────────

def hedge_dir(cache_dir: Path = NEM_CACHE_DIR) -> Path:
    return Path(cache_dir) / HEDGE_SUBDIR


def futures_path(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    return hedge_dir(cache_dir) / FUTURES_FILENAME.format(year=year)


def has_futures_cache(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> bool:
    return futures_path(year, cache_dir).exists()


def list_cached_futures_years(cache_dir: Path = NEM_CACHE_DIR) -> list:
    d = hedge_dir(cache_dir)
    if not d.exists():
        return []
    years = []
    prefix, suffix = "aer_base_futures_", ".parquet"
    for p in d.glob(f"{prefix}*{suffix}"):
        stem = p.name[len(prefix): -len(suffix)]
        if stem.isdigit():
            years.append(int(stem))
    return sorted(years)


# ── Loader ───────────────────────────────────────────────────────────────────

def load_aer_base_futures(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    """Read + normalize the AER hedge cache parquet for `year`.

    Normalizes region (strip/upper + alias map), quarter_label (strip),
    product (strip/title), price_aud_mwh (numeric, NaN rows dropped),
    as_at_date (datetime, coerced). Dedupes on (region, parsed quarter,
    product) -- using `parse_quarter_label`'s (year, quarter) tuple rather
    than the raw label string, so two different spellings of the same
    quarter (e.g. "Q2-2025" and "2025 Q2") are recognized as the SAME period
    and don't both survive to double-count that period in `quarterly_average`
    -- keeping the row with the latest as_at_date. Genuinely unparseable
    labels fall back to deduping by exact raw string among themselves.
    """
    path = futures_path(year, cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached AER base-futures data at {path}. Run "
            f"`python scripts/fetch_aer_futures.py --year {year}` in a "
            "non-sandboxed environment and copy the output parquet into "
            f"{hedge_dir(cache_dir)}/."
        )
    df = pd.read_parquet(path)

    missing = [c for c in AER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"AER futures cache at {path} is missing required column(s): {missing}. "
            f"Present columns: {list(df.columns)}"
        )

    df = df.copy()
    df["region"] = df["region"].astype(str).str.strip().str.upper()
    df["region"] = df["region"].replace(_REGION_ALIASES)
    df["quarter_label"] = df["quarter_label"].astype(str).str.strip()
    df["product"] = df["product"].astype(str).str.strip().str.title()
    df["price_aud_mwh"] = pd.to_numeric(df["price_aud_mwh"], errors="coerce")
    df["as_at_date"] = pd.to_datetime(df["as_at_date"], errors="coerce")

    df = df[df["price_aud_mwh"].notna()].copy()

    # Dedupe on (region, PARSED quarter, product), keeping the latest
    # as_at_date. Rows with NaT as_at_date sort first (nan is "oldest"/least
    # preferred) so a comparable real date always wins over an unknown one.
    # The parsed-quarter key (falling back to the raw label only when
    # unparseable) is what makes "Q2-2025" and "2025 Q2" collide as the same
    # dedup group instead of surviving as two separate rows.
    df["_orig_order"] = range(len(df))
    df["_quarter_key"] = df["quarter_label"].map(
        lambda lbl: parse_quarter_label(lbl) or ("unparsed", lbl)
    )
    # Ties in as_at_date (including two genuinely-simultaneous NaT rows) break
    # by original row order, keeping the EARLIER-appearing row -- i.e. sort so
    # the earlier row ends up last (`keep="last"`) within a tied group.
    df = df.sort_values(["as_at_date", "_orig_order"], ascending=[True, False], na_position="first")
    df = df.drop_duplicates(subset=["region", "_quarter_key", "product"], keep="last")
    df = df.drop(columns=["_quarter_key", "_orig_order"])
    df = df.reset_index(drop=True)
    return df


# ── Quarter-label parsing ────────────────────────────────────────────────────

def parse_quarter_label(label: str) -> "tuple[int, int] | None":
    """Tolerant parse of Q1-2025 / Q1 2025 / 2025 Q1 / 2025-Q1 / 2025Q1 formats.

    Returns (year, quarter) or None if unparseable (never raises).
    """
    if label is None:
        return None
    m = _QUARTER_RE.search(str(label))
    if not m:
        return None
    if m.group("q1") is not None:
        return int(m.group("y1")), int(m.group("q1"))
    return int(m.group("y2")), int(m.group("q2"))


# ── Query helpers ────────────────────────────────────────────────────────────

def available_regions(df: pd.DataFrame) -> list:
    return sorted(df["region"].unique().tolist())


def available_quarters(df: pd.DataFrame, region: "str | None" = None, product: str = DEFAULT_PRODUCT) -> list:
    """Raw quarter_label strings for `region`+`product`, chronologically sorted
    via `parse_quarter_label` (unparseable labels sorted last, stable order).
    """
    subset = df[df["product"].str.casefold() == product.casefold()]
    if region is not None:
        subset = subset[subset["region"] == region.strip().upper()]
    labels = subset["quarter_label"].unique().tolist()

    def _key(label):
        parsed = parse_quarter_label(label)
        if parsed is None:
            return (1, 0, 0)
        year, q = parsed
        return (0, year, q)

    return sorted(labels, key=_key)


def quarterly_average(
    df: pd.DataFrame, region: str = DEFAULT_REGION, quarters=None, product: str = DEFAULT_PRODUCT,
) -> float:
    """Unweighted arithmetic mean of price_aud_mwh over `quarters` (default: ALL
    available quarters for `region`+`product`) -- represents a full-year CAL
    hedge strip.
    """
    region_norm = region.strip().upper()
    all_regions = available_regions(df)
    if region_norm not in all_regions:
        raise KeyError(
            f"Unknown region '{region}'. Available regions: {all_regions}"
        )

    subset = df[(df["region"] == region_norm) & (df["product"].str.casefold() == product.casefold())]
    if subset.empty:
        raise ValueError(
            f"No rows for region '{region_norm}' and product '{product}' in the AER futures data."
        )

    available = available_quarters(df, region=region_norm, product=product)
    if quarters is None:
        quarters = available
    else:
        quarters = list(quarters)
        unknown = [q for q in quarters if q not in available]
        if unknown:
            raise ValueError(
                f"Quarter(s) {unknown} not available for region '{region_norm}'/product "
                f"'{product}'. Available quarters: {available}"
            )

    selected = subset[subset["quarter_label"].isin(quarters)]
    if selected.empty:
        raise ValueError(
            f"No rows matched quarters {quarters} for region '{region_norm}'/product '{product}'."
        )
    return float(selected["price_aud_mwh"].mean())


def latest_as_at(df: pd.DataFrame, region=None, quarters=None) -> "pd.Timestamp | None":
    subset = df
    if region is not None:
        subset = subset[subset["region"] == region.strip().upper()]
    if quarters is not None:
        subset = subset[subset["quarter_label"].isin(list(quarters))]
    dates = subset["as_at_date"].dropna()
    if dates.empty:
        return None
    return dates.max()


def disclaimer_text(as_at: "pd.Timestamp | None") -> str:
    if as_at is None or pd.isna(as_at):
        return DISCLAIMER_UNKNOWN_DATE
    try:
        as_at_str = pd.Timestamp(as_at).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - defensive, never let this crash the UI
        return DISCLAIMER_UNKNOWN_DATE
    return DISCLAIMER_TEMPLATE.format(as_at=as_at_str)


# ── Scenario-facing adapter ──────────────────────────────────────────────────

@dataclass(frozen=True)
class AerForwardQuote:
    price_aud_mwh: float
    region: str
    year: int
    quarters: tuple
    as_at_date: "pd.Timestamp | None"
    disclaimer: str


def forward_price_for_scenario(scenario, quarters=None, cache_dir: Path = NEM_CACHE_DIR) -> AerForwardQuote:
    """Duck-typed scenario access only (getattr) -- do NOT import ppa.scenario here."""
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION) or DEFAULT_REGION
    year = getattr(scenario, "nem_year", DEFAULT_YEAR) or DEFAULT_YEAR

    df = load_aer_base_futures(year, cache_dir)
    if quarters is None:
        quarters_used = tuple(available_quarters(df, region=region))
    else:
        quarters_used = tuple(quarters)

    price = quarterly_average(df, region=region, quarters=quarters_used)
    as_at = latest_as_at(df, region=region, quarters=quarters_used)
    return AerForwardQuote(
        price_aud_mwh=price,
        region=region,
        year=int(year),
        quarters=quarters_used,
        as_at_date=as_at,
        disclaimer=disclaimer_text(as_at),
    )

"""Fetch and cache European day-ahead prices from the ENTSO-E Transparency Platform.

Prices are cached per bidding zone and year (``da_prices_{zone}_{year}.parquet``),
so switching the scenario to a different zone triggers a fresh download rather
than silently reusing another zone's prices.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# Re-exported for backwards compatibility; these helpers now live in
# ppa.data.timeseries_utils (they are market-agnostic).
from ppa.data.timeseries_utils import (  # noqa: F401
    escalate_prices,
    get_prices_for_sim_year,
    _shift_to_year,
)

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "entsoe"
DE_LU = "DE_LU"

# Historical years available — matches renewables.ninja CF range
AVAILABLE_YEARS: list[int] = [2018, 2019, 2020, 2021, 2022, 2023, 2024]


def fetch_day_ahead_prices(
    year: int,
    token: str,
    country_code: str = DE_LU,
    cache_dir: Path = CACHE_DIR,
) -> pd.Series:
    """
    Return hourly day-ahead prices (€/MWh) for a full calendar year.

    Results are cached to disk as Parquet to avoid repeated API calls.
    The returned Series has a UTC DatetimeIndex.
    """
    cache_file = cache_dir / f"da_prices_{country_code}_{year}.parquet"
    if cache_file.exists():
        series = pd.read_parquet(cache_file)["price"]
        return series.ffill().bfill()

    from entsoe import EntsoePandasClient  # deferred to avoid import error when token absent

    client = EntsoePandasClient(api_key=token)
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Berlin")
    end = pd.Timestamp(f"{year+1}-01-01", tz="Europe/Berlin")

    prices = client.query_day_ahead_prices(country_code, start=start, end=end)
    prices.index = prices.index.tz_convert("UTC")
    prices = prices.resample("h").mean()
    prices = prices.ffill().bfill()  # fill any DST-gap NaN
    prices.name = "price"

    cache_dir.mkdir(parents=True, exist_ok=True)
    prices.to_frame().to_parquet(cache_file)
    return prices


def list_cached_years(country_code: str = DE_LU, cache_dir: Path = CACHE_DIR) -> list[int]:
    return sorted(
        y for y in AVAILABLE_YEARS
        if (cache_dir / f"da_prices_{country_code}_{y}.parquet").exists()
    )


def is_cached(year: int, country_code: str = DE_LU, cache_dir: Path = CACHE_DIR) -> bool:
    return (cache_dir / f"da_prices_{country_code}_{year}.parquet").exists()

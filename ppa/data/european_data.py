"""Assemble a full-year hourly timeseries for one simulation year from cached CF + price data."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ppa.data.entsoe_client import CACHE_DIR as ENTSOE_CACHE, DE_LU
from ppa.data.renewables_ninja import AVAILABLE_YEARS, CACHE_DIR as NINJA_CACHE

# Re-exported for backwards compatibility; these helpers now live in
# ppa.data.timeseries_utils (they are market-agnostic).
from ppa.data.timeseries_utils import (  # noqa: F401
    build_year_timeseries,
    pick_weather_year,
    _hours_in_year,
    _align_to_index,
)


def load_illustration_ts(
    year: int = 2023,
    lat: float = 51.5,
    lon: float = 10.0,
    zone: str = DE_LU,
    wind_lat: float | None = None,
    wind_lon: float | None = None,
) -> pd.DataFrame | None:
    """Assemble a representative European hourly timeseries from cached data.

    Reads cached ENTSO-E day-ahead prices for bidding zone ``zone`` and
    renewables.ninja wind/solar capacity factors for ``year``. ``lat``/``lon``
    locate the PV asset (central Germany by default); the wind asset defaults
    to the same spot unless ``wind_lat``/``wind_lon`` are given. Returns a
    DataFrame with ``ts_MktPrice``, ``ts_WindGen`` and ``ts_PVGen`` on a common
    hourly index. Cache-only (no network); returns ``None`` if the required
    files are not present so callers can degrade gracefully."""
    w_lat = wind_lat if wind_lat is not None else lat
    w_lon = wind_lon if wind_lon is not None else lon
    price_file = Path(ENTSOE_CACHE) / f"da_prices_{zone}_{year}.parquet"
    pv_file = Path(NINJA_CACHE) / f"pv_{lat:.2f}_{lon:.2f}_{year}.parquet"
    wind_file = Path(NINJA_CACHE) / f"wind_{w_lat:.2f}_{w_lon:.2f}_{year}.parquet"
    if not (price_file.exists() and pv_file.exists() and wind_file.exists()):
        return None

    price = pd.read_parquet(price_file)["price"]
    pv = pd.read_parquet(pv_file)["cf"]
    wind = pd.read_parquet(wind_file)["cf"]

    # Align all three on a clean hourly index for the year (positional align is
    # robust to small index/timezone differences between the two sources).
    n = min(len(price), len(pv), len(wind))
    index = pd.date_range(f"{year}-01-01", periods=n, freq="h", name="snapshot")
    return pd.DataFrame(
        {
            "ts_MktPrice": price.to_numpy()[:n],
            "ts_WindGen": wind.to_numpy()[:n],
            "ts_PVGen": pv.to_numpy()[:n],
        },
        index=index,
    )


def load_reference_month_ts(
    year: int = 2023,
    month: int = 3,
    lat: float = 51.5,
    lon: float = 10.0,
    zone: str = DE_LU,
    wind_lat: float | None = None,
    wind_lon: float | None = None,
) -> pd.DataFrame | None:
    """A single representative European month for the single-day reference run.

    Slices one month out of :func:`load_illustration_ts` (zonal ENTSO-E prices +
    renewables.ninja CFs) so the reference LP stays quick (~one month of hours)
    while using European market data. Returns ``None`` if the cache is missing."""
    ts = load_illustration_ts(year, lat, lon, zone=zone, wind_lat=wind_lat, wind_lon=wind_lon)
    if ts is None:
        return None
    return ts[ts.index.month == month]


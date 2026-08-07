"""Source-agnostic timeseries helpers shared by all data adapters.

Nothing here is specific to any particular market or data provider: these
functions take plain CF/price ``pd.Series`` plus a load-profile name and build
the hourly ``ts`` DataFrame the dispatch/sizing LPs consume.
"""
from __future__ import annotations

import pandas as pd

from ppa.industrial_profiles import get_load_series


def escalate_prices(
    base_prices: pd.Series,
    from_year: int,
    to_year: int,
    rate: float,
) -> pd.Series:
    """Apply compound annual price escalation from `from_year` to `to_year`."""
    factor = (1.0 + rate) ** (to_year - from_year)
    return base_prices * factor


def build_year_timeseries(
    sim_year: int,
    weather_year: int,
    ppa_load_mw: float,
    pv_cf_by_year: dict[int, pd.Series],
    wind_cf_by_year: dict[int, pd.Series],
    prices_by_year: dict[int, pd.Series],
    price_escalation_rate: float,
    load_profile: str = "flat",
    load_mw_by_year: dict[int, pd.Series] | None = None,
) -> pd.DataFrame:
    """
    Build a full-year hourly timeseries ready for build_network / solve.

    Both CF profiles and market prices are drawn from `weather_year` so that
    price–weather correlations are preserved (e.g. 2021: high prices + low wind).
    Prices are then escalated from that historical year to sim_year.
    """
    pv_cf = pv_cf_by_year[weather_year]
    wind_cf = wind_cf_by_year[weather_year]
    base_prices = prices_by_year[weather_year]

    # Build the canonical hourly index for this simulation year (UTC)
    year_index = pd.date_range(
        start=f"{sim_year}-01-01",
        periods=_hours_in_year(sim_year),
        freq="h",
        tz="UTC",
    )

    pv_series = _align_to_index(pv_cf, year_index, fill_value=0.0)
    wind_series = _align_to_index(wind_cf, year_index, fill_value=0.0)

    escalated = escalate_prices(base_prices, from_year=weather_year, to_year=sim_year, rate=price_escalation_rate)
    price_series = _align_to_index(escalated, year_index, fill_value=float(escalated.median()))

    # PyPSA requires timezone-naive snapshots; strip UTC tz while keeping UTC semantics
    naive_index = year_index.tz_localize(None)

    if load_mw_by_year is not None:
        load_values = _align_to_index(load_mw_by_year[weather_year], year_index, fill_value=0.0).values
    else:
        profile = get_load_series(load_profile, naive_index)
        load_values = (profile * ppa_load_mw).values

    ts = pd.DataFrame(
        {
            "ts_PVGen": pv_series.values,
            "ts_WindGen": wind_series.values,
            "ts_MktPrice": price_series.values,
            "ppaload_mw": load_values,
        },
        index=naive_index,
    )
    ts.index.name = "snapshot"
    return ts


def pick_weather_year(sim_year_idx: int, available_years: list[int]) -> int:
    """Cycle over available historical weather years for simulation year index (0-based)."""
    return available_years[sim_year_idx % len(available_years)]


def _hours_in_year(year: int) -> int:
    import calendar
    return 8784 if calendar.isleap(year) else 8760


def _align_to_index(series: pd.Series, target_index: pd.DatetimeIndex, fill_value: float) -> pd.Series:
    """
    Assign CF values positionally onto target_index (hour-of-year semantics, not calendar date).

    This is intentional: a 2018 CF profile assigned to a 2025 target index simply
    replays the same hourly weather pattern under a new set of timestamps.

    When `series` is SHORTER than `target_index` (e.g. an 8760-row source lined up
    against an 8784-hour leap-year target, or a partial-span custom CSV upload
    lined up against a full simulated year), the shortfall is padded by tiling the
    ENTIRE source series end-to-end (wrapping around) until the target length is
    reached. This matters most for short custom uploads: a 48-hour upload becomes
    ~182.5 repetitions of that 48-hour pattern rather than 365 repetitions of just
    its last 24 hours, which is a far more honest reflection of "the user only
    gave us 2 days of data". For the NEM path this code path is only
    ever reached by the (harmless) leap-year 8760→8784 gap, where tiling the whole
    source is equivalent-in-spirit to tiling the last day (both replay real
    historical hours) but strictly more correct in the general case.
    """
    import numpy as np

    n_src = len(series)
    n_tgt = len(target_index)

    if n_src >= n_tgt:
        values = series.values[:n_tgt]
    else:
        # Source shorter than target: tile the whole source end-to-end to pad
        # out the remainder (wrapping around, not just repeating the last day).
        extra_len = n_tgt - n_src
        reps = extra_len // n_src + 1
        pad = np.tile(series.values, reps)[:extra_len]
        values = np.concatenate([series.values, pad])

    return pd.Series(values, index=target_index, name=series.name)

"""W13: verify the "1-year sizing horizon" claim.

`weather_cycle_years(15, n_weather=1, n_price=1)` must return `(1, note)` because
`math.lcm(1, 1) = 1`, so `build_sizing_timeseries` runs with `n_sizing_years=1`
and the sizing LP really is one year — the extra runtime in a 15-year run comes
from the subsequent 15 full hourly dispatch solves, not from sizing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ppa.sizing import build_sizing_timeseries, weather_cycle_years


def _full_year_series(year: int = 2025, value: float = 0.5) -> pd.Series:
    idx = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
    return pd.Series(float(value), index=idx)


def _dicts():
    return (
        {2025: _full_year_series(value=0.3)},
        {2025: _full_year_series(value=0.4)},
        {2025: _full_year_series(value=80.0)},
    )


def test_weather_cycle_years_15_with_single_cached_year_is_one():
    years, note = weather_cycle_years(15, n_weather_years=1, n_price_years=1)
    assert years == 1
    assert note is not None


def test_weather_cycle_years_note_explains_dispatch_still_full_horizon():
    _, note = weather_cycle_years(15, n_weather_years=1, n_price_years=1)
    assert "full simulation still runs all 15 year(s)" in note


def test_weather_cycle_years_matches_available_cycles():
    # 3 weather × 2 price years -> lcm(3,2)=6-cycle cap
    years, _ = weather_cycle_years(15, n_weather_years=3, n_price_years=2)
    assert years == 6
    # Requested <= cycle -> unchanged
    years_unclamped, note_unclamped = weather_cycle_years(6, n_weather_years=3, n_price_years=2)
    assert years_unclamped == 6
    assert note_unclamped is None


def test_build_sizing_timeseries_single_year_rows():
    from ppa.scenario import Scenario

    pv, wind, prices = _dicts()
    scn = Scenario(simulation_years=15, first_sim_year=2025)
    ts = build_sizing_timeseries(scn, pv, wind, prices, n_sizing_years=1)
    assert len(ts) <= 8784


def test_single_cached_year_produces_single_year_sizing_horizon():
    """A 15-year scenario with exactly one cached weather + one price year must
    be sized on a single concatenated year (the cycle cap), not 15."""
    from ppa.scenario import Scenario

    pv, wind, prices = _dicts()
    scn = Scenario(simulation_years=15, first_sim_year=2025)
    n_sizing_years, note = weather_cycle_years(scn.simulation_years, 1, 1)
    assert n_sizing_years == 1
    assert note is not None

    ts = build_sizing_timeseries(scn, pv, wind, prices, n_sizing_years=n_sizing_years)
    assert len(ts) == 8760

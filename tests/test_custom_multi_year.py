"""Tests for load_mw_by_year plumbing through build_year_timeseries /
run_multi_year / build_sizing_timeseries (Phase 3 custom-CSV load override)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ppa.data.timeseries_utils import build_year_timeseries


def _flat_series(index, value):
    return pd.Series(value, index=index, dtype=float)


@pytest.fixture()
def base_cf_and_price():
    idx_2024 = pd.date_range("2024-01-01", periods=8784, freq="h")  # leap year source
    idx_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")

    pv_cf_by_year = {2024: _flat_series(idx_2024, 0.3), 2025: _flat_series(idx_2025, 0.35)}
    wind_cf_by_year = {2024: _flat_series(idx_2024, 0.4), 2025: _flat_series(idx_2025, 0.45)}
    prices_by_year = {2024: _flat_series(idx_2024, 80.0), 2025: _flat_series(idx_2025, 85.0)}
    return pv_cf_by_year, wind_cf_by_year, prices_by_year


def test_build_year_timeseries_falls_back_to_profile_when_no_load_override(base_cf_and_price):
    """Regression: existing profile-based synthesis is unchanged when
    load_mw_by_year is not passed."""
    pv_cf_by_year, wind_cf_by_year, prices_by_year = base_cf_and_price

    ts = build_year_timeseries(
        sim_year=2026,
        weather_year=2025,
        ppa_load_mw=100.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=0.025,
        load_profile="flat",
    )
    assert (ts["ppaload_mw"] == 100.0).all()


def test_build_year_timeseries_respects_load_mw_by_year_override(base_cf_and_price):
    pv_cf_by_year, wind_cf_by_year, prices_by_year = base_cf_and_price

    idx_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")
    distinctive_load = pd.Series(
        50.0 + 3.0 * (np.arange(8760) % 7), index=idx_2025, dtype=float
    )
    load_mw_by_year = {2025: distinctive_load}

    ts = build_year_timeseries(
        sim_year=2026,
        weather_year=2025,
        ppa_load_mw=100.0,  # must be ignored when an override is supplied
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=0.025,
        load_profile="flat",
        load_mw_by_year=load_mw_by_year,
    )

    np.testing.assert_array_equal(ts["ppaload_mw"].to_numpy(), distinctive_load.to_numpy())
    # Definitely not the flat profile*ppa_load_mw synthesis
    assert ts["ppaload_mw"].nunique() > 1


def test_leap_year_padding_of_8760_row_override_onto_leap_sim_year(base_cf_and_price):
    """An 8760-row (non-leap) load override assigned to a leap simulation year
    (8784 hours) must be padded by tiling the WHOLE source series (wrapping
    around), matching `_align_to_index`'s "tile full span" convention."""
    pv_cf_by_year, wind_cf_by_year, prices_by_year = base_cf_and_price

    idx_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")
    load_2025 = pd.Series(np.arange(8760, dtype=float), index=idx_2025)
    load_mw_by_year = {2025: load_2025}

    ts = build_year_timeseries(
        sim_year=2028,  # leap year -> 8784 snapshots
        weather_year=2025,
        ppa_load_mw=100.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=0.0,
        load_profile="flat",
        load_mw_by_year=load_mw_by_year,
    )

    assert len(ts) == 8784
    # First 8760 values pass straight through
    np.testing.assert_array_equal(ts["ppaload_mw"].to_numpy()[:8760], load_2025.to_numpy())
    # Extra 24 hours wrap around and are tiled from the START of the source
    # series (the whole 8760-hour span is repeated end-to-end), not just its
    # last 24 hours.
    np.testing.assert_array_equal(
        ts["ppaload_mw"].to_numpy()[8760:], load_2025.to_numpy()[:24]
    )


def test_short_custom_upload_tiles_full_span_not_just_last_day(base_cf_and_price):
    """Regression for the Phase-3 review bug: a 48-hour custom upload tiled
    across a full simulated year must replay the WHOLE 48-hour pattern
    end-to-end (~182.5 repetitions), not just its final 24 hours repeated 365
    times. Assert multiple distinct days from the original upload appear
    spread across the resulting year."""
    pv_cf_by_year, wind_cf_by_year, prices_by_year = base_cf_and_price

    idx_48h = pd.date_range("2025-01-01", periods=48, freq="h")
    # Day 0 (hours 0-23) and Day 1 (hours 24-47) have distinct, recognizable
    # load values so we can tell which "day" reappears where in the tiled year.
    day0_values = 100.0 + np.arange(24, dtype=float)
    day1_values = 500.0 + np.arange(24, dtype=float)
    load_48h = pd.Series(np.concatenate([day0_values, day1_values]), index=idx_48h)
    load_mw_by_year = {2025: load_48h}

    ts = build_year_timeseries(
        sim_year=2026,
        weather_year=2025,
        ppa_load_mw=999.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=0.0,
        load_profile="flat",
        load_mw_by_year=load_mw_by_year,
    )

    values = ts["ppaload_mw"].to_numpy()
    assert len(values) == 8760

    # Hour-of-year 0..23 == day0 pattern (first repetition, unchanged).
    np.testing.assert_array_equal(values[:24], day0_values)
    # Hour-of-year 24..47 == day1 pattern (first repetition, unchanged).
    np.testing.assert_array_equal(values[24:48], day1_values)

    # The bug this guards against: with "tile only the last 24h" padding, every
    # hour beyond the first 48 would be a repeat of ONLY day1_values. Instead,
    # the full 48h pattern must wrap, so hour-of-year 48..71 (the third "day"
    # of the simulated year) must reproduce day0_values again, not day1_values.
    np.testing.assert_array_equal(values[48:72], day0_values)

    # Across the whole year, BOTH distinctive values (100-123 range and
    # 500-523 range) must appear many times -- i.e. multiple distinct days
    # from the original 48h upload are spread across the year, not just one.
    n_day0_repeats = int(np.isin(values, day0_values).sum()) // 24
    n_day1_repeats = int(np.isin(values, day1_values).sum()) // 24
    assert n_day0_repeats > 100
    assert n_day1_repeats > 100
    # 8760 / 48 = 182.5 repetitions of the full 48h pattern, so each 24h half
    # (day0, day1) individually appears ~182-183 times.
    assert n_day0_repeats == pytest.approx(182, abs=2)
    assert n_day1_repeats == pytest.approx(182, abs=2)


def test_price_escalation_starts_from_first_sim_year_no_double_counting(base_cf_and_price):
    pv_cf_by_year, wind_cf_by_year, prices_by_year = base_cf_and_price
    rate = 0.025

    ts_year0 = build_year_timeseries(
        sim_year=2025,
        weather_year=2025,
        ppa_load_mw=100.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=rate,
        load_profile="flat",
    )
    # sim_year == weather_year -> no escalation yet
    assert ts_year0["ts_MktPrice"].iloc[0] == pytest.approx(85.0)

    ts_year1 = build_year_timeseries(
        sim_year=2026,
        weather_year=2025,
        ppa_load_mw=100.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=rate,
        load_profile="flat",
    )
    assert ts_year1["ts_MktPrice"].iloc[0] == pytest.approx(85.0 * (1 + rate))

    ts_year2 = build_year_timeseries(
        sim_year=2027,
        weather_year=2025,
        ppa_load_mw=100.0,
        pv_cf_by_year=pv_cf_by_year,
        wind_cf_by_year=wind_cf_by_year,
        prices_by_year=prices_by_year,
        price_escalation_rate=rate,
        load_profile="flat",
    )
    assert ts_year2["ts_MktPrice"].iloc[0] == pytest.approx(85.0 * (1 + rate) ** 2)
    # Confirms compounding, not additive double-counting
    assert ts_year2["ts_MktPrice"].iloc[0] != pytest.approx(85.0 * (1 + 2 * rate))

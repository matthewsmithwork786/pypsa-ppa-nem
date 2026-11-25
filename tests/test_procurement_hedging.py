import pandas as pd
import pytest

from ppa.procurement_hedging import Cap, Swap, historical_cap_prices, settle_hedges


def test_caps_pay_on_interval_spikes_and_swaps_apply_to_the_fixed_contract_mw():
    hours = pd.date_range("2025-01-01 21:00", periods=4, freq="h")
    index = pd.date_range(hours[0], periods=48, freq="5min")
    prices = pd.Series([1200.0] + [0.0] * 11 + [150.0] * 12 + [-50.0] * 12 + [200.0] * 12,
                       index=index)
    dispatch = pd.DataFrame({
        "Load": 100.0,
        "Spot price": prices.resample("h").mean(),
        "Spot purchases": [40.0, 30.0, 0.0, 20.0],
        "Excess sold": [0.0, 0.0, 20.0, 0.0],
    }, index=hours)
    caps = {"$300 cap": Cap(70, 300, 10), "$600 cap": Cap(10, 600, 15)}
    swaps = {"Baseload": Swap(10, 110), "Overnight": Swap(5, 125, 22, 1)}
    hourly, contracts, overhedged_hours = settle_hedges(dispatch, caps, swaps, prices)
    assert hourly["Before hedges"].tolist() == [4000, 4500, 1000, 4000]
    assert contracts["Premium or fixed leg"].tolist() == [2800, 600, 4400, 1875]
    assert contracts["Floating receipt"].tolist() == [5250, 500, 4000, 1500]
    assert hourly["After hedges"].sum() == pytest.approx(
        hourly["Before hedges"].sum() + contracts["Net hedge cost"].sum()
    )
    assert overhedged_hours == pytest.approx(1 + 1 / 12)
    with pytest.raises(ValueError, match="reconcile"):
        settle_hedges(dispatch, caps, swaps, prices * 2)


def test_historical_cap_prices_use_half_hour_averages_only_before_cutover():
    index = pd.date_range("2021-09-30 23:30", periods=12, freq="5min")
    prices = pd.Series([600] + [0] * 5 + [600] + [0] * 5, index=index)
    result = historical_cap_prices(prices)
    assert result.tolist() == [100] * 6 + [600] + [0] * 5

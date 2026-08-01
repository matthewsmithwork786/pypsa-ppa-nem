"""Resolution-awareness regression coverage for build_network/extract_results.

Encoding the same real period at a finer resolution (e.g. duplicating each
hourly row into two half-hour rows) must produce IDENTICAL summary MWh/$
totals once resolution_h is threaded through correctly -- these numbers used
to be silently overstated at any resolution other than exactly 1h/row (see
ppa/results.py::extract_results docstring). BESS is disabled here so the
solver has no arbitrage-timing degrees of freedom to introduce incidental
differences between the two encodings.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ppa.data_loader import prepare_timeseries
from ppa.network import build_network
from ppa.results import extract_results
from ppa.scenario import Scenario
from ppa.solver import solve


def _scenario() -> Scenario:
    return Scenario(
        include_bess=False,
        onsw_mw=50.0,
        pv_mw=30.0,
        ppaload_mw=20.0,
        load_profile="flat",
        ppa_price=80.0,
        enable_market_buy=True,
        enable_market_sell=True,
        enable_shortfall=True,
        enable_penalty=True,
        run_financial_analysis=False,
        enable_counterfactual=False,
    )


def _hourly_ts() -> pd.DataFrame:
    idx = pd.date_range("2025-03-01", periods=4, freq="h")
    return pd.DataFrame(
        {
            "ts_PVGen": [0.0, 0.6, 0.9, 0.3],
            "ts_WindGen": [0.4, 0.2, 0.5, 0.7],
            "ts_MktPrice": [40.0, -10.0, 120.0, 60.0],
        },
        index=idx,
    )


def _upsampled_30min(ts_hourly: pd.DataFrame) -> pd.DataFrame:
    idx = pd.date_range(ts_hourly.index[0], periods=len(ts_hourly) * 2, freq="30min")
    return pd.DataFrame(
        {col: [v for v in ts_hourly[col] for _ in range(2)] for col in ts_hourly.columns},
        index=idx,
    )


def _solve(ts: pd.DataFrame, scenario: Scenario, resolution_h: float):
    ts_prep = prepare_timeseries(ts, scenario)
    n = build_network(ts_prep, scenario, resolution_h=resolution_h)
    status, condition = solve(n, scenario, ts_prep)
    assert status == "ok", f"solver failed: {status}/{condition}"
    return extract_results(n, scenario, ts_prep, status, condition, resolution_h=resolution_h)


def test_hourly_vs_upsampled_30min_give_identical_totals():
    scenario = _scenario()
    ts_hourly = _hourly_ts()
    ts_30min = _upsampled_30min(ts_hourly)

    result_hourly = _solve(ts_hourly, scenario, resolution_h=1.0)
    result_30min = _solve(ts_30min, scenario, resolution_h=0.5)

    assert result_hourly.n_period_hours == pytest.approx(4.0)
    assert result_30min.n_period_hours == pytest.approx(4.0)

    s_h, s_30 = result_hourly.summary, result_30min.summary
    for field in (
        "total_load_mwh", "ppa_delivered_mwh", "market_buy_to_ppa_mwh",
        "allowed_shortfall_mwh", "penalty_mwh", "sold_to_market_mwh",
        "wind_generation_mwh", "pv_generation_mwh", "fulfilled_share",
    ):
        assert getattr(s_h, field) == pytest.approx(getattr(s_30, field), abs=1e-6), field

    r_h, r_30 = result_hourly.revenue, result_30min.revenue
    for field in ("ppa_revenue", "excess_revenue", "market_purchase_cost", "penalty_cost", "net_revenue"):
        assert getattr(r_h, field) == pytest.approx(getattr(r_30, field), abs=1e-6), field


def test_default_resolution_h_is_hourly():
    """Omitting resolution_h on extract_results must still assume 1.0 -- no
    behavior change for every existing (always-hourly) caller."""
    scenario = _scenario()
    ts_prep = prepare_timeseries(_hourly_ts(), scenario)
    n = build_network(ts_prep, scenario)
    status, condition = solve(n, scenario, ts_prep)
    result_default = extract_results(n, scenario, ts_prep, status, condition)
    result_explicit = extract_results(n, scenario, ts_prep, status, condition, resolution_h=1.0)
    assert result_default.summary == result_explicit.summary
    assert result_default.n_period_hours == pytest.approx(4.0)

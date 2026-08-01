"""Tests for ppa/counterfactuals.py::compute_counterfactuals.

Regression coverage for the Phase-3 review finding: counterfactual costs must
be computed against the actual hourly load series (`ts["ppaload_mw"]`), not a
flat load at the scalar `scenario.ppaload_mw` (which, since Phase 3, holds the
uploaded/contracted PEAK MW rather than the average consumption).
"""
from __future__ import annotations

import pandas as pd
import pytest

from ppa.counterfactuals import compute_counterfactuals
from ppa.results import DispatchSeries, OptimizationResult, SummaryVolumes, RevenueBreakdown
from ppa.scenario import Scenario


def _make_result(scenario: Scenario, ppa_delivery: pd.Series) -> OptimizationResult:
    idx = ppa_delivery.index
    zeros = pd.Series(0.0, index=idx)
    dispatch = DispatchSeries(
        wind_gen=zeros, pv_gen=zeros, market_buy=zeros,
        allowed_shortfall=zeros, penalty_gen=zeros, market_sell=zeros,
        bess_dispatch=zeros, bess_store=zeros, soc=zeros,
        ppa_delivery=ppa_delivery,
    )
    summary = SummaryVolumes(
        total_load_mwh=0.0, ppa_delivered_mwh=0.0, renewable_and_storage_to_ppa_mwh=0.0,
        market_buy_to_ppa_mwh=0.0, allowed_shortfall_mwh=0.0, penalty_mwh=0.0,
        sold_to_market_mwh=0.0, wind_generation_mwh=0.0, pv_generation_mwh=0.0,
        bess_dispatch_mwh=0.0, bess_charge_mwh=0.0, fulfilled_share=0.0,
        allowed_shortfall_share_actual=0.0, buy_share_of_ppa_delivery=0.0,
        penalty_share_of_load=0.0,
    )
    revenue = RevenueBreakdown(
        ppa_revenue=0.0, excess_revenue=0.0, market_purchase_cost=0.0,
        penalty_cost=0.0, net_revenue=0.0, effective_capture_price=0.0,
    )
    return OptimizationResult(
        scenario=scenario, dispatch=dispatch, summary=summary, revenue=revenue,
        solver_status="ok", solver_condition="optimal", n_period_hours=len(idx),
    )


def _base_ts(n_hours: int, load_values, price: float = 50.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    return pd.DataFrame(
        {
            "ts_MktPrice": price,
            "ppaload_mw": load_values,
        },
        index=idx,
    )


def test_uses_actual_hourly_load_series_not_flat_scalar():
    """Core regression: total_load_mwh and spot_cost must reflect ts['ppaload_mw'],
    not a flat load at scenario.ppaload_mw (the uploaded PEAK MW)."""
    n_hours = 24
    # Peak MW is 100 (matches scenario.ppaload_mw), but the profile spends most
    # hours far below peak -- a spiky industrial-style load.
    load_values = [100.0] + [10.0] * (n_hours - 1)
    ts = _base_ts(n_hours, load_values, price=50.0)

    scenario = Scenario(ppaload_mw=100.0, ppa_price=80.0)
    ppa_delivery = pd.Series(load_values, index=ts.index)  # fully delivered
    result = _make_result(scenario, ppa_delivery)

    cf = compute_counterfactuals(ts, scenario, result)

    actual_total_load = sum(load_values)  # 100 + 23*10 = 330
    flat_total_load_old_bug = 100.0 * n_hours  # 2400 -- what the OLD buggy code would give

    assert cf.total_load_mwh == pytest.approx(actual_total_load)
    assert cf.total_load_mwh < flat_total_load_old_bug

    expected_spot_cost = sum(v * 50.0 for v in load_values)
    assert cf.spot_cost == pytest.approx(expected_spot_cost)


def test_non_flat_profile_gives_different_totals_than_flat_profile_would():
    """A non-flat load profile must produce different (lower, more accurate)
    totals than a flat load at the same peak MW would have given."""
    n_hours = 24
    peak_mw = 100.0

    spiky_load = [peak_mw] + [5.0] * (n_hours - 1)
    flat_load = [peak_mw] * n_hours

    scenario = Scenario(ppaload_mw=peak_mw, ppa_price=80.0)

    ts_spiky = _base_ts(n_hours, spiky_load, price=60.0)
    ts_flat = _base_ts(n_hours, flat_load, price=60.0)

    result_spiky = _make_result(scenario, pd.Series(spiky_load, index=ts_spiky.index))
    result_flat = _make_result(scenario, pd.Series(flat_load, index=ts_flat.index))

    cf_spiky = compute_counterfactuals(ts_spiky, scenario, result_spiky)
    cf_flat = compute_counterfactuals(ts_flat, scenario, result_flat)

    assert cf_spiky.total_load_mwh < cf_flat.total_load_mwh
    assert cf_spiky.spot_cost < cf_flat.spot_cost
    # The flat-load case should exactly match the old (pre-fix) flat-scalar
    # formula: load_mw * n_hours * dt.
    assert cf_flat.total_load_mwh == pytest.approx(peak_mw * n_hours)


def test_flat_load_matches_scalar_formula_exactly():
    """Sanity: when ts['ppaload_mw'] IS flat and equal to scenario.ppaload_mw,
    results must match what the old scalar-based formula produced (no
    regression for flat-profile / non-custom-CSV scenarios)."""
    n_hours = 48
    load_mw = 75.0
    ts = _base_ts(n_hours, [load_mw] * n_hours, price=90.0)
    scenario = Scenario(ppaload_mw=load_mw, ppa_price=70.0, cal_forward_price=85.0, cal_hedge_fraction=0.5)
    ppa_delivery = pd.Series([load_mw] * n_hours, index=ts.index)
    result = _make_result(scenario, ppa_delivery)

    cf = compute_counterfactuals(ts, scenario, result)

    assert cf.total_load_mwh == pytest.approx(load_mw * n_hours)
    assert cf.spot_cost == pytest.approx(90.0 * load_mw * n_hours)
    assert cf.cal_cost == pytest.approx(85.0 * load_mw * n_hours)
    assert cf.ppa_offtaker_cost == pytest.approx(70.0 * load_mw * n_hours)


def test_undelivered_load_uses_actual_hourly_load_not_flat_peak():
    """Undelivered = max(load - ppa_delivery, 0) must use the actual hourly
    load, not the flat peak scalar -- otherwise undelivered volumes (and their
    spot-cost top-up) would be overstated whenever load < peak."""
    n_hours = 4
    load_values = [20.0, 20.0, 20.0, 20.0]  # well below the 100 MW peak
    ts = _base_ts(n_hours, load_values, price=100.0)
    scenario = Scenario(ppaload_mw=100.0, ppa_price=50.0)
    # IPP delivers 15 MW every hour -- under the actual load (20) but nowhere
    # near the flat peak (100), so undelivered should be small (5 MW/h), not
    # huge (85 MW/h) as the old flat-scalar bug would have computed.
    ppa_delivery = pd.Series([15.0] * n_hours, index=ts.index)
    result = _make_result(scenario, ppa_delivery)

    cf = compute_counterfactuals(ts, scenario, result)

    # PPA cost = ppa_price * delivered + spot_price * undelivered
    expected_ppa_cost = (50.0 * 15.0 * n_hours) + (100.0 * 5.0 * n_hours)
    assert cf.ppa_offtaker_cost == pytest.approx(expected_ppa_cost)

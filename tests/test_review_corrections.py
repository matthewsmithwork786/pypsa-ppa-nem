"""Accounting and UI regressions from the October screenshot review."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import brentq
from streamlit.testing.v1 import AppTest

from ppa.financial_model import energy_inputs_from_result, project_finance_inputs_from_scenario, run_project_finance
from ppa.financials import run_multi_year_financial_analysis
from ppa.results import bess_utilisation, merchant_sales_frame
from ppa.scenario import Scenario
from tests.test_finance_dispatch_accounting import _dispatch_result


@pytest.mark.parametrize('minutes', [60, 30, 15, 5])
def test_merchant_buckets_reconcile_to_dispatch_revenue(minutes):
    r = _dispatch_result(minutes)
    prices = r.market_prices.copy()
    prices.loc[(prices.index.hour >= 9) & (prices.index.hour < 17)] = 200
    r = replace(r, market_prices=prices)
    frame = merchant_sales_frame(r)
    assert frame['Energy sold (MWh)'].sum() == pytest.approx(r.summary.sold_to_market_mwh)
    assert frame.iloc[0]['Spot capture (A$/MWh)'] == 200
    assert frame.iloc[1]['Spot capture (A$/MWh)'] == 100
    assert frame.iloc[0]['Net capture incl. LGC (A$/MWh)'] == 203
    assert frame['Merchant revenue (A$)'].sum() == pytest.approx(8*3*(200+6-3) + 16*3*(100+6-3))
    original = merchant_sales_frame(_dispatch_result(minutes))
    assert original['Merchant revenue (A$)'].sum() == pytest.approx(r.revenue.excess_revenue)


def test_no_sales_reports_no_achieved_capture_price():
    r = _dispatch_result()
    r = replace(r, dispatch=replace(r.dispatch, market_sell=r.dispatch.market_sell * 0))
    frame = merchant_sales_frame(r)
    assert frame['Spot capture (A$/MWh)'].isna().all()
    assert frame['Merchant revenue (A$)'].sum() == 0


@pytest.mark.parametrize('minutes', [60, 30, 5])
def test_cycles_measure_internal_throughput_against_usable_capacity(minutes):
    r = _dispatch_result(minutes)
    b = bess_utilisation(r)
    assert b['BESS equivalent full cycles'] == pytest.approx(96/(240*.9))
    assert b['BESS cycles per day'] == b['BESS equivalent full cycles']
    assert b['BESS charge (GWh)'] == .096
    faded = replace(r, scenario=replace(r.scenario, bess_mwh=120))
    assert bess_utilisation(faded)['BESS equivalent full cycles'] == pytest.approx(2*b['BESS equivalent full cycles'])
    disabled = replace(r, scenario=replace(r.scenario, include_bess=False))
    assert bess_utilisation(disabled)['BESS equivalent full cycles'] == 0


def test_delivery_aggregate_weights_actual_load_and_exports_battery_metrics():
    first = _dispatch_result()
    second = replace(first, summary=replace(first.summary, total_load_mwh=100, ppa_delivered_mwh=50, fulfilled_share=.5))
    scenario = replace(first.scenario, dispatch_mode='full_multi_year', simulation_years=2)
    f = run_multi_year_financial_analysis(scenario, [first, second])
    assert f.delivery_share == pytest.approx((288+50)/(312+100))
    assert f.yearly[0].bess_discharge_gwh == .096
    projected = run_multi_year_financial_analysis(first.scenario, [first])
    assert len(projected.yearly) == 25
    assert projected.delivery_share == pytest.approx(first.summary.fulfilled_share)
    assert projected.yearly[-1].bess_cycles == projected.yearly[0].bess_cycles


def test_equity_irr_is_independent_cashflow_root_and_responds_to_debt():
    r = _dispatch_result()
    e = energy_inputs_from_result(r)
    p = project_finance_inputs_from_scenario(r.scenario)
    actual = run_project_finance(p, e)
    cf = actual.schedule['fcfe']
    root = brentq(lambda rate: np.sum(cf/(1+rate)**np.arange(len(cf))), -.9, 2)
    assert actual.equity_irr == pytest.approx(root, abs=1e-9)
    assert actual.equity_irr != pytest.approx(r.scenario.target_irr, abs=1e-5)
    no_debt = run_project_finance(replace(p, max_gearing_contracted=0, max_gearing_uncontracted=0), e)
    assert no_debt.total_debt == 0
    assert no_debt.equity_irr != pytest.approx(actual.equity_irr)


def test_penalty_disabled_means_zero_charge_in_both_models():
    s = Scenario(enable_penalty=False)
    assert s.penalty_price == 0
    assert project_finance_inputs_from_scenario(s).penalty_tariff == 0


def test_disabled_counterfactual_shows_guidance_without_computing():
    app = AppTest.from_string('''
from ppa.scenario import Scenario
from ui.tabs.results_deep_dive import _render_multi_year_counterfactuals
_render_multi_year_counterfactuals([], None, Scenario(enable_counterfactual=False))
''').run()
    assert not app.exception
    assert 'Enable' in app.info[0].value


@pytest.mark.parametrize('fraction', [0.0, 0.5, 1.0])
def test_counterfactual_hedge_controls_match_manual_price_and_fraction(fraction):
    from ppa.counterfactuals import compute_counterfactuals
    r = _dispatch_result(minutes=30)
    load = r.dispatch.ppa_delivery + r.dispatch.penalty_gen
    ts = pd.DataFrame({'ppaload_mw': load, 'ts_MktPrice': r.market_prices})
    s = replace(r.scenario, cal_forward_price=85, cal_hedge_fraction=fraction)
    cf = compute_counterfactuals(ts, s, r, dt=.5)
    assert cf.cal_cost == pytest.approx(312*85)
    assert cf.spot_cost == pytest.approx(312*100)
    assert cf.blended_cost == pytest.approx(312*(fraction*85+(1-fraction)*100))
    assert cf.ppa_offtaker_cost == pytest.approx(288*s.ppa_price+24*100)

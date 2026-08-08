"""Phase 2 regression tests: AUD conversion + devex-as-single-bullet-at-FID.

Covers ppa/financial_model.py, ppa/scenario.py and ppa/financials.py. Deliberately
avoids importing anything from `ui/` (streamlit isn't guaranteed to be installed
in every environment these tests run in).
"""
from __future__ import annotations

import dataclasses
import os

import numpy as np
import pandas as pd
import pytest

from ppa.financial_model import (
    EnergyInputs,
    ProjectFinanceInputs,
    run_project_finance,
    project_finance_inputs_from_scenario,
)
from ppa.scenario import CASE_STUDIES, validate_scenario, load_case_study
from ppa.financials import run_multi_year_financial_analysis
from ppa.results import DispatchSeries, OptimisationResult, SummaryVolumes, RevenueBreakdown


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. ProjectFinanceInputs defaults match the new AUD values ────────────────


def test_defaults_match_aud_benchmarks():
    p = ProjectFinanceInputs()
    assert p.onsw_build_cost == pytest.approx(2.9)
    assert p.pv_build_cost == pytest.approx(1.7186)
    assert p.bess_build_cost == pytest.approx(0.2765)
    assert p.onsw_connection_cost == pytest.approx(0.15)
    assert p.pv_connection_cost == pytest.approx(0.15)
    assert p.bess_connection_cost == pytest.approx(0.0225)
    assert p.onsw_devex == pytest.approx(0.29)
    assert p.pv_devex == pytest.approx(0.17186)
    assert p.bess_devex == pytest.approx(0.02765)
    # Wind fixed O&M: Gohdes (2026) AJARE Table 2, A$28,512/MW/yr (was A$28,000
    # -- 2% of the legacy Aus247RE_FM build cost) -- TASK_financial_assumptions
    # _refactor.md Phase 2. PV/BESS are unchanged, still-unverified legacy values.
    assert p.onsw_fixed_om == pytest.approx(0.028512)
    assert p.pv_fixed_om == pytest.approx(0.012)
    assert p.bess_fixed_om == pytest.approx(0.0105)
    assert p.cost_inflation == pytest.approx(0.025)
    assert p.ppa_indexation == pytest.approx(0.025)
    assert p.nonsolar_price_inflation == pytest.approx(0.025)

    # Unchanged defaults
    assert p.ancillary_pct == pytest.approx(0.01)
    assert p.model_duration == 40
    assert p.development_start == 1
    assert p.onsw_constr_years == 2
    assert p.pv_constr_years == 1
    assert p.bess_constr_years == 1
    assert p.operating_life == 30
    assert p.ppa_tenor == 15
    assert p.lgc_price == pytest.approx(5.0)
    assert p.solar_price_inflation == pytest.approx(0.01)
    assert p.debt_tenor == 15
    # 5.75% blended-over-life proxy for Gohdes (2026)'s refinancing path (was
    # 6.50%) -- TASK_financial_assumptions_refactor.md Phase 3. Do not "correct"
    # this to the paper's raw 5.55% 5-year rate; see ppa/assumptions.py.
    assert p.debt_rate == pytest.approx(0.0575)
    assert p.dscr_contracted == pytest.approx(1.35)
    assert p.dscr_uncontracted == pytest.approx(2.40)
    assert p.max_gearing_contracted == pytest.approx(0.80)
    assert p.max_gearing_uncontracted == pytest.approx(0.50)
    assert p.book_depreciation_rate == pytest.approx(0.04)
    assert p.tax_depreciation_rate == pytest.approx(0.10)
    assert p.corp_tax_rate == pytest.approx(0.30)
    assert p.discount_rate == pytest.approx(0.08)
    assert p.ppa_tariff == pytest.approx(100.0)
    assert p.penalty_multiple == pytest.approx(1.5)
    assert not hasattr(p, "indexation_offset_years")  # retired by the year-0 base
    assert p.escalate_merchant_prices is True


# ── 2. Removed dev-years fields ────────────────────────────────────────────


def test_dev_years_fields_removed():
    p = ProjectFinanceInputs()
    for attr in ("onsw_dev_years", "pv_dev_years", "bess_dev_years"):
        assert not hasattr(p, attr)
        with pytest.raises(AttributeError):
            getattr(p, attr)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _wind_only_energy(mw: float = 100.0) -> EnergyInputs:
    return EnergyInputs(
        onsw_mw=mw,
        pv_mw=0.0,
        bess_mw=0.0,
        bess_mwh=0.0,
        load_mw=50.0,
        ppa_gwh=300.0,
        excess_solar_gwh=0.0,
        excess_nonsolar_gwh=50.0,
        penalty_gwh=5.0,
        total_solar_gwh=0.0,
        total_nonsolar_gwh=350.0,
        sell_solar_price=60.0,
        sell_nonsolar_price=55.0,
        purchase_price=70.0,
        marketbuy_gwh=2.0,
        name="wind-only test",
    )


# ── 3. Single devex bullet at FID, one-tech scenario ───────────────────────


def test_devex_single_bullet_at_fid_default_start():
    p = ProjectFinanceInputs(onsw_devex=0.29, pv_devex=0.0, bess_devex=0.0)
    e = _wind_only_energy(mw=100.0)
    result = run_project_finance(p, e)
    devex = result.schedule["devex"]

    nonzero_idx = np.flatnonzero(devex)
    assert len(nonzero_idx) == 1
    assert nonzero_idx[0] == p.development_start - 1  # FID is the 0-based year

    # Year 0 is the base year, so the FID bullet carries no indexation
    # (multiplier = (1+rate)**0 = 1.0).
    cost_idx_at_fid = (1.0 + p.cost_inflation) ** (p.development_start - 1)
    expected = 29.0 * cost_idx_at_fid
    assert devex[nonzero_idx[0]] == pytest.approx(expected)
    assert expected == pytest.approx(29.0)

    # Un-indexed devex total recovers the raw devex input (29.0 = 0.29 * 100 MW)
    undexed_total = devex.sum() / cost_idx_at_fid
    assert undexed_total == pytest.approx(29.0)


# ── 4. Construction spans max(constr_years), ops_start offset ─────────────


def test_construction_window_and_ops_start():
    p = ProjectFinanceInputs(onsw_constr_years=2, pv_constr_years=1, bess_constr_years=1)
    e = _wind_only_energy()
    result = run_project_finance(p, e)
    capex = result.schedule["capex"]

    max_constr = max(p.onsw_constr_years, p.pv_constr_years, p.bess_constr_years)
    nonzero_idx = np.flatnonzero(capex)
    expected_periods = set(
        range(p.development_start - 1, p.development_start - 1 + max_constr)
    )
    assert set(nonzero_idx.tolist()) == expected_periods
    assert len(nonzero_idx) == max_constr

    ops_flag = result.schedule["ops_flag"]
    first_ops_period = int(result.periods[np.flatnonzero(ops_flag)][0])
    # 0-based years: ops begin the year after construction ends.
    assert first_ops_period == p.development_start - 1 + max_constr


# ── 5. Non-default development_start still produces one correctly offset bullet ──


def test_devex_bullet_with_nondefault_fid():
    p = ProjectFinanceInputs(development_start=4, onsw_devex=0.29, pv_devex=0.0, bess_devex=0.0)
    e = _wind_only_energy(mw=100.0)
    result = run_project_finance(p, e)
    devex = result.schedule["devex"]

    nonzero_idx = np.flatnonzero(devex)
    assert len(nonzero_idx) == 1
    assert nonzero_idx[0] == p.development_start - 1 == 3


# ── 6. All CASE_STUDIES validate and produce finite project IRR / NPV ─────


@pytest.mark.parametrize("cs", CASE_STUDIES, ids=[cs.id for cs in CASE_STUDIES])
def test_case_studies_validate_and_produce_finite_results(cs):
    scenario = load_case_study(cs)
    errors = validate_scenario(scenario)
    assert errors == []

    pf_inputs = project_finance_inputs_from_scenario(scenario)
    energy = _wind_only_energy(mw=scenario.onsw_mw or 100.0)
    result = run_project_finance(pf_inputs, energy)

    assert np.isfinite(result.project_irr)
    assert not np.isnan(result.npv_project)
    assert np.isfinite(result.npv_project)


# ── 7. run_multi_year_financial_analysis cashflow bullet includes devex ────


def test_run_multi_year_financial_analysis_cashflow_includes_devex():
    scenario = CASE_STUDIES[0]
    s = load_case_study(scenario)
    s = dataclasses.replace(s, devex_pct_of_capex=0.10)

    summary = SummaryVolumes(
        total_load_mwh=1_000_000.0,
        ppa_delivered_mwh=750_000.0,
        renewable_and_storage_to_ppa_mwh=700_000.0,
        market_buy_to_ppa_mwh=50_000.0,
        allowed_shortfall_mwh=100_000.0,
        penalty_mwh=50_000.0,
        sold_to_market_mwh=100_000.0,
        wind_generation_mwh=600_000.0,
        pv_generation_mwh=200_000.0,
        bess_dispatch_mwh=50_000.0,
        bess_charge_mwh=50_000.0,
        fulfilled_share=0.75,
        allowed_shortfall_share_actual=0.10,
        buy_share_of_ppa_delivery=0.07,
        penalty_share_of_load=0.05,
    )
    revenue = RevenueBreakdown(
        ppa_revenue=75_000_000.0,
        excess_revenue=5_000_000.0,
        market_purchase_cost=3_000_000.0,
        penalty_cost=1_000_000.0,
        transmission_cost=500_000.0,
        net_revenue=75_500_000.0,
        effective_capture_price=80.0,
    )
    empty = pd.Series(dtype=float)
    dispatch = DispatchSeries(
        wind_gen=empty, pv_gen=empty, market_buy=empty, allowed_shortfall=empty,
        penalty_gen=empty, market_sell=empty, bess_dispatch=empty, bess_store=empty,
        soc=empty, ppa_delivery=empty,
    )
    result = OptimisationResult(
        scenario=s, dispatch=dispatch, summary=summary, revenue=revenue,
        solver_status="ok", solver_condition="optimal", n_period_hours=8760,
    )

    fin = run_multi_year_financial_analysis(s, [result], first_sim_year=s.first_sim_year)

    expected_bullet = -(fin.capex.capex_total + fin.capex.devex_total)
    assert fin.capex.devex_total == pytest.approx(fin.capex.capex_total * 0.10)
    assert fin.capex.total_investment == pytest.approx(
        fin.capex.capex_total + fin.capex.devex_total
    )
    # simple_payback is total_investment / avg_cf when avg_cf > 0
    year0_cf = fin.yearly[0].net_cashflow
    if year0_cf > 0:
        assert fin.simple_payback == pytest.approx(
            fin.capex.total_investment / year0_cf
        )
    assert expected_bullet == pytest.approx(-fin.capex.total_investment)


# ── 8. No stray € characters outside the explicitly EUR-denominated files ─


# AUD-model files only. The deleted European-data files are intentionally out
# of scope (they no longer exist).
#
# Note: ui/tabs/optimisation.py is deliberately excluded from this list — it is
# under concurrent active development elsewhere and out of scope for this check.
CHECKED_FILES = [
    os.path.join(REPO_ROOT, "ppa", "financial_model.py"),
    os.path.join(REPO_ROOT, "ppa", "financial_model_excel.py"),
    os.path.join(REPO_ROOT, "ppa", "financials.py"),
    os.path.join(REPO_ROOT, "ppa", "scenario.py"),
    os.path.join(REPO_ROOT, "ppa", "sensitivity.py"),
    os.path.join(REPO_ROOT, "ppa", "network.py"),
    os.path.join(REPO_ROOT, "ppa", "sizing.py"),
    os.path.join(REPO_ROOT, "ui", "tabs", "financial_model.py"),
    os.path.join(REPO_ROOT, "ui", "tabs", "results_deep_dive.py"),
    os.path.join(REPO_ROOT, "ui", "tabs", "sensitivity_analysis.py"),
    os.path.join(REPO_ROOT, "ui", "scenario_form.py"),
    os.path.join(REPO_ROOT, "ui", "charts.py"),
    os.path.join(REPO_ROOT, "ui", "tabs", "welcome.py"),
]


@pytest.mark.parametrize(
    "path", sorted(set(os.path.normpath(p) for p in CHECKED_FILES if os.path.isfile(p)))
)
def test_no_euro_symbol_outside_allowlist(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "€" not in content, f"unexpected € in {path}"

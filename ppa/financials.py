from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ppa.financial_model import _irr, _npv
from ppa.scenario import Scenario
from ppa.results import OptimisationResult

HOURS_PER_YEAR = 8_760


@dataclass
class CapexBreakdown:
    capex_wind: float
    capex_pv: float
    capex_bess: float
    capex_total: float
    devex_total: float
    total_investment: float
    annual_opex: float


def build_capex(scenario: Scenario) -> CapexBreakdown:
    s = scenario
    capex_wind = s.wind_capex_per_kw * s.onsw_mw * 1_000
    capex_pv = s.pv_capex_per_kw * s.pv_mw * 1_000
    capex_bess = s.bess_capex_per_kwh * s.effective_bess_mwh * 1_000
    capex_total = capex_wind + capex_pv + capex_bess
    devex_total = capex_total * s.devex_pct_of_capex
    total_investment = capex_total + devex_total
    annual_opex = capex_total * s.opex_rate

    return CapexBreakdown(
        capex_wind=capex_wind,
        capex_pv=capex_pv,
        capex_bess=capex_bess,
        capex_total=capex_total,
        devex_total=devex_total,
        total_investment=total_investment,
        annual_opex=annual_opex,
    )


# ── Multi-year financial analysis ─────────────────────────────────────────────


@dataclass
class YearlyFinancials:
    year: int
    ppa_revenue: float
    merch_revenue: float
    market_buy_cost: float
    penalty_cost: float
    net_revenue: float
    opex: float
    net_cashflow: float
    fulfilled_share: float
    wind_gen_mwh: float
    pv_gen_mwh: float
    transmission_cost: float = 0.0


@dataclass
class MultiYearFinancialResult:
    capex: CapexBreakdown
    annual_opex: float

    yearly: list[YearlyFinancials] = field(default_factory=list)

    # Aggregate KPIs
    npv: float = 0.0
    irr: float = float("nan")
    lcoe: float = float("nan")
    simple_payback: float = float("inf")
    total_lifetime_revenue: float = 0.0
    total_lifetime_generation_mwh: float = 0.0

    # Running NPV series (index = year number 1..N, value = cumulative NPV)
    cumulative_npv: list[float] = field(default_factory=list)


def run_multi_year_financial_analysis(
    scenario: Scenario,
    year_results: list[OptimisationResult],
    first_sim_year: int = 2025,
) -> MultiYearFinancialResult:
    """
    Compute project-level financials from per-year LP results.

    Each year's revenue is computed from the actual optimised dispatch.
    CAPEX is invested at year 0; OPEX is charged each year.
    """
    s = scenario

    # ── CAPEX / OPEX ──────────────────────────────────────────────────────────
    capex = build_capex(s)
    total_investment = capex.total_investment
    annual_opex = capex.annual_opex

    yearly: list[YearlyFinancials] = []
    cashflows: list[float] = [-total_investment]
    total_revenue = 0.0
    total_gen_mwh = 0.0

    for idx, res in enumerate(year_results):
        rev = res.revenue
        summ = res.summary
        net_rev = rev.net_revenue
        net_cf = net_rev - annual_opex

        yearly.append(
            YearlyFinancials(
                year=first_sim_year + idx,
                ppa_revenue=rev.ppa_revenue,
                merch_revenue=rev.excess_revenue,
                market_buy_cost=rev.market_purchase_cost,
                penalty_cost=rev.penalty_cost,
                transmission_cost=rev.transmission_cost,
                net_revenue=net_rev,
                opex=annual_opex,
                net_cashflow=net_cf,
                fulfilled_share=summ.fulfilled_share,
                wind_gen_mwh=summ.wind_generation_mwh,
                pv_gen_mwh=summ.pv_generation_mwh,
            )
        )
        cashflows.append(net_cf)
        total_revenue += net_rev
        total_gen_mwh += summ.wind_generation_mwh + summ.pv_generation_mwh + summ.bess_dispatch_mwh

    # Extend cashflows to project_life_yrs if fewer years were simulated.
    # The average of the simulated years is used for the remaining periods so that
    # NPV/IRR always reflect the full project life regardless of simulation_years.
    n_sim = len(year_results)
    n_life = s.project_life_yrs
    if n_sim < n_life:
        avg_simulated_cf = sum(cashflows[1:]) / n_sim
        cashflows.extend([avg_simulated_cf] * (n_life - n_sim))

    # ── NPV / IRR ──────────────────────────────────────────────────────────────
    cashflows_arr = np.array(cashflows)
    npv = _npv(s.discount_rate, cashflows_arr)
    irr = _irr(cashflows_arr)

    # ── LCOE (using WACC annuity over project life) ───────────────────────────
    annuity_wacc = (1 - (1 + s.discount_rate) ** -s.project_life_yrs) / s.discount_rate
    avg_annual_gen = total_gen_mwh / len(year_results) if year_results else 0.0
    lcoe = (
        (total_investment / annuity_wacc + annual_opex) / avg_annual_gen
        if avg_annual_gen > 0
        else float("nan")
    )

    # ── Simple payback ────────────────────────────────────────────────────────
    avg_cf = sum(c for c in cashflows[1:]) / len(cashflows[1:]) if len(cashflows) > 1 else 0.0
    simple_payback = total_investment / avg_cf if avg_cf > 0 else float("inf")

    # ── Cumulative NPV series ─────────────────────────────────────────────────
    cumulative_npv: list[float] = []
    running = -total_investment
    for t, cf in enumerate(cashflows[1:], start=1):
        running += cf / (1 + s.discount_rate) ** t
        cumulative_npv.append(running)

    return MultiYearFinancialResult(
        capex=capex,
        annual_opex=annual_opex,
        yearly=yearly,
        npv=npv,
        irr=irr,
        lcoe=lcoe,
        simple_payback=simple_payback,
        total_lifetime_revenue=total_revenue,
        total_lifetime_generation_mwh=total_gen_mwh,
        cumulative_npv=cumulative_npv,
    )

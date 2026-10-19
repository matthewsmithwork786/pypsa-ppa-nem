from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ppa import assumptions as A
from ppa.financial_model import _irr, _npv
from ppa.scenario import Scenario
from ppa.results import OptimisationResult, bess_utilisation

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
    # Fixed O&M plus Gohdes (2026) maintenance capex.
    annual_opex = (
        A.WIND_FIXED_OM_AUD_MW_YR * s.onsw_mw
        + A.PV_FIXED_OM_AUD_MW_YR * s.pv_mw
        + A.BESS_FIXED_OM_AUD_MWH_YR * s.effective_bess_mwh
        + A.MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA * capex_total
    )

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
    total_load_mwh: float = 0.0
    ppa_delivered_mwh: float = 0.0
    bess_charge_gwh: float = 0.0
    bess_discharge_gwh: float = 0.0
    bess_usable_mwh: float = 0.0
    bess_cycles: float = 0.0
    bess_cycles_per_day: float = 0.0


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
    breakeven_ppa_price: float = float("nan")

    @property
    def delivery_share(self) -> float:
        """Load-weighted delivered share across the financial projection."""
        load = sum(getattr(y, "total_load_mwh", 0.0) for y in self.yearly)
        if load > 0:
            return sum(y.ppa_delivered_mwh for y in self.yearly) / load
        return sum(y.fulfilled_share for y in self.yearly) / len(self.yearly) if self.yearly else 0.0

    # Running NPV series (index = year number 1..N, value = cumulative NPV)
    cumulative_npv: list[float] = field(default_factory=list)


def run_multi_year_financial_analysis(
    scenario: Scenario,
    year_results: list[OptimisationResult],
    first_sim_year: int = 2025,
) -> MultiYearFinancialResult:
    """Compute project-level cash flows from annual dispatch results."""
    s = scenario

    # ── CAPEX / OPEX ──────────────────────────────────────────────────────────
    capex = build_capex(s)
    total_investment = capex.total_investment
    annual_opex = capex.annual_opex

    yearly: list[YearlyFinancials] = []
    cashflows: list[float] = [-total_investment]
    total_revenue = 0.0
    total_gen_mwh = 0.0

    representative = (
        getattr(s, "dispatch_mode", "full_multi_year") == "representative_year"
        and len(year_results) == 1
        and s.simulation_years > 1
    )
    n_records = s.simulation_years if representative else len(year_results)
    for idx in range(n_records):
        res = year_results[0] if representative else year_results[idx]
        rev = res.revenue
        summ = res.summary
        merch_revenue = rev.excess_revenue
        market_buy_cost = rev.market_purchase_cost
        if representative:
            # The dispatch LP escalates spot prices, while the bid-offer spread
            # and certificate price remain fixed. Apply the same convention to
            # repeated dispatch, preserving its certificate/spread cashflows.
            factor = (1.0 + s.price_escalation_rate) ** idx
            spot_sales = rev.excess_revenue - summ.sold_to_market_mwh * (
                s.lgc_price_aud_mwh - s.market_spread
            )
            spot_purchases = rev.market_purchase_cost - summ.market_buy_to_ppa_mwh * (
                s.market_spread + s.lgc_price_aud_mwh
            )
            merch_revenue += (factor - 1.0) * spot_sales
            market_buy_cost += (factor - 1.0) * spot_purchases
        net_rev = (
            rev.ppa_revenue + merch_revenue - market_buy_cost
            - rev.penalty_cost - rev.transmission_cost
        )
        net_cf = net_rev - annual_opex

        battery = bess_utilisation(res)
        yearly.append(
            YearlyFinancials(
                year=first_sim_year + idx,
                ppa_revenue=rev.ppa_revenue,
                merch_revenue=merch_revenue,
                market_buy_cost=market_buy_cost,
                penalty_cost=rev.penalty_cost,
                transmission_cost=rev.transmission_cost,
                net_revenue=net_rev,
                opex=annual_opex,
                net_cashflow=net_cf,
                fulfilled_share=summ.fulfilled_share,
                total_load_mwh=summ.total_load_mwh,
                ppa_delivered_mwh=summ.ppa_delivered_mwh,
                bess_charge_gwh=battery["BESS charge (GWh)"],
                bess_discharge_gwh=battery["BESS discharge (GWh)"],
                bess_usable_mwh=battery["BESS usable capacity (MWh)"],
                bess_cycles=battery["BESS equivalent full cycles"],
                bess_cycles_per_day=battery["BESS cycles per day"],
                wind_gen_mwh=summ.wind_generation_mwh,
                pv_gen_mwh=summ.pv_generation_mwh,
            )
        )
        cashflows.append(net_cf)
        total_revenue += net_rev
        # Battery discharge shifts previously generated energy; it is not new
        # generation and must not increase the lifetime generation/LCOE base.
        total_gen_mwh += summ.wind_generation_mwh + summ.pv_generation_mwh

    # Extend short simulations with average cash flow, up to the financial life.
    n_sim = n_records
    n_life = (
        s.simulation_years
        if getattr(s, "dispatch_mode", "full_multi_year") == "representative_year"
        and len(year_results) == 1
        else s.project_life_yrs
    )
    if n_sim and n_sim < n_life:
        avg_simulated_cf = sum(cashflows[1:]) / n_sim
        cashflows.extend([avg_simulated_cf] * (n_life - n_sim))

    # ── NPV / IRR ──────────────────────────────────────────────────────────────
    cashflows_arr = np.array(cashflows)
    npv = _npv(s.discount_rate, cashflows_arr)
    irr = _irr(cashflows_arr)

    # ── LCOE (using WACC annuity over project life) ───────────────────────────
    annuity_wacc = (1 - (1 + s.discount_rate) ** -n_life) / s.discount_rate
    avg_annual_gen = total_gen_mwh / n_records if n_records else 0.0
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

    # ── Breakeven PPA price for target IRR ────────────────────────────────────
    # Each year's cashflow is linear in the PPA price: net_cf(p) = net_cf(s.ppa_price)
    # + (p - s.ppa_price) * delivered_volume, since only ppa_revenue depends on
    # price. Solve for the price that makes lifetime IRR == target_irr, holding
    # delivered volumes (and every other cost/revenue) fixed at what the
    # dispatch actually did. Bisection (not scipy) to match _irr's own approach.
    breakeven_ppa_price = float("nan")
    if s.ppa_price > 0 and yearly:
        sim_volumes = [y.ppa_revenue / s.ppa_price for y in yearly]
        volumes = list(sim_volumes)
        if n_sim < n_life:
            avg_volume = sum(sim_volumes) / n_sim
            volumes.extend([avg_volume] * (n_life - n_sim))

        base_cfs = cashflows[1:]  # length n_life, at s.ppa_price

        def _npv_at_price(p: float) -> float:
            adj_cfs = [cf + (p - s.ppa_price) * vol for cf, vol in zip(base_cfs, volumes)]
            return _npv(s.target_irr, np.array([-total_investment] + adj_cfs))

        lo, hi = 0.0, 2000.0
        flo, fhi = _npv_at_price(lo), _npv_at_price(hi)
        if flo * fhi <= 0:
            for _ in range(200):
                mid = (lo + hi) / 2
                fmid = _npv_at_price(mid)
                if abs(fmid) < 1e-6:
                    break
                if flo * fmid < 0:
                    hi = mid
                else:
                    lo, flo = mid, fmid
            breakeven_ppa_price = (lo + hi) / 2

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
        breakeven_ppa_price=breakeven_ppa_price,
        cumulative_npv=cumulative_npv,
    )

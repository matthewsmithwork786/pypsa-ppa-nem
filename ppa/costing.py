"""Shared capital-cost annualisation for sizing and diagnostics."""
from __future__ import annotations


def crf(rate: float, life: int) -> float:
    """Capital recovery factor: annual payment per $1 of capex over `life`
    years at `rate`. Falls back to straight-line (1/life) at rate == 0, since
    the standard CRF formula divides by zero there."""
    return rate / (1 - (1 + rate) ** -life) if rate > 0 else 1.0 / life


def annualised_cost_per_mw(
    capex_per_mw: float,
    rate: float,
    life: int,
    fixed_om_per_mw_yr: float,
    devex_pct: float = 0.0,
) -> float:
    """Return annual capital recovery plus fixed O&M in A$/MW/year.

    Inputs must use A$/MW and A$/MW/year. Devex applies to capex only; callers
    scale this annual amount by the modelled horizon when building the LP.
    """
    return capex_per_mw * (1.0 + devex_pct) * crf(rate, life) + fixed_om_per_mw_yr

"""Shared capital-cost annualisation.

Used by both the sizing LP's objective (`ppa.network.build_network`, which
needs the total capital charge over the whole modelled horizon) and the
sizing diagnostics table that explains the LP's decision to a user
(`ppa.sizing.sizing_diagnostics`, which needs a per-year A$/MW/yr figure). A
single source for the formula is what keeps "the LP builds one fleet and the
UI explains a different one" from happening again — see AGENTS.md §5.1 and
docs/sizing_experiments.md for a case where exactly that drift, caused by a
capex change landing between two runs, invalidated a published finding.
"""
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
    opex_rate: float,
    devex_pct: float = 0.0,
) -> float:
    """Per-year annualised capital + fixed-O&M charge per MW (A$/MW/yr) --
    equally usable per MWh of BESS energy or per MW of connection capacity,
    since it takes a plain already-converted `capex_per_mw` and multiplies.

    `capex_per_mw` must already be in A$/MW: callers convert an A$/kW input
    (×1,000) themselves, since which figure needs converting varies by call
    site (BESS also needs ×`bess_max_hours` to go from A$/kWh to A$/MW).

    `rate` is the discount rate used for capital recovery. Pass it in
    explicitly rather than letting this function pick — the sizing LP uses
    `target_irr` while sizing (so the optimiser only builds capacity that
    clears the project hurdle rate) or `discount_rate` otherwise, and forcing
    each call site to say which keeps that choice visible instead of the two
    call sites happening to agree by construction.

    `devex_pct` defaults to 0.0 (no devex uplift) — correct for connection
    costs, which are never devex'd. Generation/storage capex call sites pass
    `scenario.devex_pct_of_capex` explicitly.

    This is a *per-year* figure. A caller integrating it over a sizing
    horizon longer than one year (`ppa.network.build_network`'s LP objective
    needs the total capital charge summed over all modelled hours, to sit on
    the same additive basis as revenue terms spanning that same horizon)
    multiplies the result by the horizon in years itself.
    """
    return capex_per_mw * (1.0 + devex_pct) * (crf(rate, life) + opex_rate)

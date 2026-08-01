"""W12 regression: capacity sizing must not under-build because the transport
links are hard-capped at the (disabled) slider MW.

Root cause reproduced in the plan: `ppa/network.py` computes `wind_link_mw` /
`pvbess_link_mw` / `sell_link_mw` but the `link_defs` list ignores them and
passes `s.onsw_mw`, `s.pv_mw + s.effective_bess_mw`, `s.maxsell_mw` instead — so
in sizing mode the optimiser may "build" up to 1000 MW but can only ever
*deliver* the slider-capped link MW, and therefore never builds more.

W12 turns the three transport links into extendable investment variables with a
positive (connection-cost) capital cost, keeps the offtake link fixed, and
aligns the LP's cost basis with the financial model (devex + target_irr).
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from ppa.network import build_network
from ppa.scenario import Scenario
from ppa.sizing import apply_sizing, optimize_capacities
from ppa.solver import solve

TRANSPORT_LINKS = [
    "OnshoreWind_to_IPPGeneration",
    "PVBESS_to_IPPGeneration",
    "IPPGen_to_SellToMarket",
]
OFFTAKE_LINK = "IPPGen_to_PPAOfftake"
MARKET_BUY_LINK = "BuyFromMarket_to_IPPGeneration"


def _toy_ts(n_hours: int = 72, load_mw: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    pv = np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85
    wind = np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0)
    price = np.asarray(70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24))
    price[::11] = -20.0  # guaranteed negative-price hours
    return pd.DataFrame(
        {
            "ts_PVGen": pv,
            "ts_WindGen": wind,
            "ts_MktPrice": price,
            "ppaload_mw": float(load_mw),
        },
        index=idx,
    )


def _toy_scenario(**overrides) -> Scenario:
    base = dict(
        name="sizing-network toy",
        optimize_capacity=True,
        onsw_mw=50.0,
        pv_mw=50.0,
        bess_mw=0.0,
        bess_mwh=0.0,
        include_bess=False,
        max_build_wind_mw=2000.0,
        max_build_pv_mw=2000.0,
        max_build_bess_mw=0.0,
        wind_capex_per_kw=100.0,   # deliberately cheap so the LP wants to build
        pv_capex_per_kw=100.0,
        bess_capex_per_kwh=50.0,
        sizing_resolution_h=1,
        simulation_years=1,
    )
    base.update(overrides)
    return Scenario(**base)


def _peak_link_flow(n) -> dict[str, float]:
    """Absolute peak flow (MW) on each link over the horizon."""
    return {name: float(n.links.dynamic.p[name].abs().max()) for name in n.links.static.index}


# ── (a) transport links extendable in sizing mode, fixed in dispatch mode ────

def test_links_extendable_in_sizing_mode():
    ts = _toy_ts()
    n = build_network(ts, _toy_scenario())
    ext = n.links.static.p_nom_extendable
    for name in TRANSPORT_LINKS:
        assert bool(ext[name]), f"transport link {name} must be extendable in sizing mode"


def test_links_fixed_in_dispatch_mode():
    ts = _toy_ts()
    scn = dataclasses.replace(_toy_scenario(), optimize_capacity=False)
    n = build_network(ts, scn)
    ext = n.links.static.p_nom_extendable
    assert not ext.any(), "no link may be extendable in dispatch mode"
    assert n.links.static.p_nom["OnshoreWind_to_IPPGeneration"] == pytest.approx(50.0)
    assert n.links.static.p_nom["PVBESS_to_IPPGeneration"] == pytest.approx(50.0)


def test_offtake_and_market_buy_links_never_extendable():
    ts = _toy_ts()
    n = build_network(ts, _toy_scenario())
    ext = n.links.static.p_nom_extendable
    assert not bool(ext[OFFTAKE_LINK]), "PPA offtake link is contractual, never extendable"
    assert not bool(ext[MARKET_BUY_LINK]), "market-buy link is a contract cap, never extendable"


# ── (a) sized link MW equals realised peak flow ──────────────────────────────

def test_link_pnom_opt_matches_peak_flow():
    """With a strictly-positive capital cost the extendable link's `p_nom_opt`
    is pinned to the realised peak flow (no degenerate over-build)."""
    ts = _toy_ts()
    n = build_network(ts, _toy_scenario())
    status, condition = solve(n, _toy_scenario(), ts)
    assert status.lower() in ("ok", "optimal"), f"solve failed: {status} {condition}"
    peaks = _peak_link_flow(n)
    for name in TRANSPORT_LINKS:
        sized = float(n.links.static.p_nom_opt[name])
        assert sized > 0, f"{name} should be built"
        assert peaks[name] <= sized * (1.0 + 1e-4) + 1e-6, (
            f"{name}: peak {peaks[name]:.2f} MW exceeds sized {sized:.2f} MW"
        )
        assert sized <= peaks[name] * (1.0 + 1e-3) + 1e-3, (
            f"{name}: sized {sized:.2f} MW far above peak {peaks[name]:.2f} MW "
            "(capital cost not pinning p_nom_opt to peak flow?)"
        )


# ── (c) LP cost basis includes devex and uses target_irr ─────────────────────

def test_generator_capital_cost_includes_devex_and_target_irr():
    ts = _toy_ts()
    scn = _toy_scenario()
    n = build_network(ts, scn)
    horizon_years = len(ts) / 8760.0

    def crf(rate: float) -> float:
        life = scn.project_life_yrs
        return rate / (1 - (1 + rate) ** -life)

    expected_wind = (
        scn.wind_capex_per_kw
        * 1000
        * (1 + scn.devex_pct_of_capex)
        * (crf(scn.target_irr) + scn.opex_rate)
        * horizon_years
    )
    actual_wind = float(n.generators.static.capital_cost["Gen_OnshoreWind"])
    assert actual_wind == pytest.approx(expected_wind, rel=1e-6)

    expected_pv = (
        scn.pv_capex_per_kw
        * 1000
        * (1 + scn.devex_pct_of_capex)
        * (crf(scn.target_irr) + scn.opex_rate)
        * horizon_years
    )
    actual_pv = float(n.generators.static.capital_cost["Gen_PV"])
    assert actual_pv == pytest.approx(expected_pv, rel=1e-6)


# ── (b) merchant haircut applies to positive prices only ─────────────────────

def test_merchant_negative_price_hours_undiscounted():
    """`sizing_merchant_value_share` haircuts positive prices only; negative
    hours keep their full disincentive so the LP curtails rather than sells."""
    ts = _toy_ts()
    scn = dataclasses.replace(_toy_scenario(), sizing_merchant_value_share=0.5)
    n = build_network(ts, scn)
    mc = n.generators.dynamic.marginal_cost["Gen_SellToMarket"]
    prices = ts["ts_MktPrice"]
    neg_mask = prices < 0
    assert neg_mask.any(), "test requires negative-price hours in the fixture"

    # Positive-price hours: revenue credited at 50 % of (price - spread).
    pos = ~neg_mask
    expected_pos = -(prices[pos] * 0.5 - scn.market_spread)
    np.testing.assert_allclose(mc[pos].to_numpy(), expected_pos.to_numpy(), rtol=1e-9)

    # Negative-price hours: full (un-halved) cost, so selling is never subsidised.
    expected_neg = -(prices[neg_mask] - scn.market_spread)
    np.testing.assert_allclose(mc[neg_mask].to_numpy(), expected_neg.to_numpy(), rtol=1e-9)


# ── Acceptance: the toy LP must build MORE than the (disabled) slider values ─

def test_toy_lp_builds_more_than_slider_values():
    """With cheap capex and generous caps the sizing LP must size the transport
    links well beyond the (disabled) slider caps (wind 50 + PV 50 = 100 MW).
    The bug pins the delivered/connection capacity at the slider values, so the
    reported sized link MW are the direct acceptance signal for W12."""
    ts = _toy_ts()
    sized = optimize_capacities(ts, _toy_scenario())
    sized_link_total = sized.wind_link_mw + sized.pvbess_link_mw
    assert sized_link_total > 150.0, (
        f"sizing LP sized only {sized_link_total:.1f} MW of transport/connection "
        f"capacity (wind {sized.wind_link_mw:.1f} + pv+bess {sized.pvbess_link_mw:.1f}) "
        "— links still pinned to the slider caps?"
    )


# ── apply_sizing carries the sized connection MW into dispatch ───────────────

def test_apply_sizing_carries_link_mw_into_dispatch():
    ts = _toy_ts()
    scn = _toy_scenario()
    sized = optimize_capacities(ts, scn)
    sim = apply_sizing(scn, sized)
    # The dispatch scenario must pin the transport links to the sized MW, not
    # re-derive them from nameplate, so the simulation matches what the LP sized.
    assert sim.wind_link_mw == pytest.approx(round(sized.wind_link_mw, 1))
    assert sim.pvbess_link_mw == pytest.approx(round(sized.pvbess_link_mw, 1))
    assert sim.sell_link_mw == pytest.approx(round(sized.sell_link_mw, 1))
    assert not sim.optimize_capacity

    n = build_network(ts, sim)
    assert not n.links.static.p_nom_extendable.any()
    assert n.links.static.p_nom["OnshoreWind_to_IPPGeneration"] == pytest.approx(round(sized.wind_link_mw, 1))
    assert n.links.static.p_nom["PVBESS_to_IPPGeneration"] == pytest.approx(round(sized.pvbess_link_mw, 1))
    assert n.links.static.p_nom["IPPGen_to_SellToMarket"] == pytest.approx(round(sized.sell_link_mw, 1))


# ── grid_connection_max_mw caps the sized links ──────────────────────────────

def test_grid_connection_cap_limits_link_builds():
    ts = _toy_ts()
    scn = dataclasses.replace(_toy_scenario(), grid_connection_max_mw=120.0)
    n = build_network(ts, scn)
    assert n.links.static.p_nom_extendable["OnshoreWind_to_IPPGeneration"]
    assert n.links.static.p_nom_max["OnshoreWind_to_IPPGeneration"] == pytest.approx(120.0)
    sized = optimize_capacities(ts, scn)
    assert sized.wind_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3
    assert sized.pvbess_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3
    assert sized.sell_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3


# ── sizing diagnostics (W12e) ────────────────────────────────────────────────

def test_sizing_diagnostics_reports_costs_and_binding():
    from ppa.sizing import sizing_diagnostics

    ts = _toy_ts()
    scn = _toy_scenario()
    sized = optimize_capacities(ts, scn)
    diag = sizing_diagnostics(sized, scn, ts)
    assert len(diag["tech_rows"]) == 2  # wind + solar
    assert len(diag["link_rows"]) == 3  # three transport links
    assert diag["ppa_price"] == pytest.approx(scn.ppa_price)
    assert diag["penalty_price"] == pytest.approx(scn.penalty_price)
    # Cheap capex with generous caps → the caps bind (the LP builds to them).
    assert diag["tech_rows"][0]["Sized (MW)"] == pytest.approx(round(sized.onsw_mw, 1))
    assert diag["tech_rows"][0]["Implied LCOE (A$/MWh)"] is not None
    assert diag["avg_spot"] is not None
    # Binding flags: with max_build_wind_mw=2000 and cheap capex the wind cap binds.
    assert any(r["Max-build cap binding"] == "Yes" for r in diag["tech_rows"])

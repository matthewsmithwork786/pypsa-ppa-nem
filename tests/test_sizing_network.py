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
from ppa.sizing import apply_sizing, optimise_capacities
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
        optimise_capacity=True,
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
        sizing_method="full_hourly",  # W14 default is tsam; these LP tests use the exact-hourly path
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
    scn = dataclasses.replace(_toy_scenario(), optimise_capacity=False)
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
    hours keep their full disincentive so the LP curtails rather than sells.

    Surplus also earns an LGC (U2). The certificate is added at full value in
    both regimes: the haircut represents capture-price/MLF/curtailment risk in
    the *energy* market and does not apply to the certificate market.
    """
    ts = _toy_ts()
    scn = dataclasses.replace(
        _toy_scenario(), sizing_merchant_value_share=0.5, lgc_price_aud_mwh=5.0
    )
    n = build_network(ts, scn)
    mc = n.generators.dynamic.marginal_cost["Gen_SellToMarket"]
    prices = ts["ts_MktPrice"]
    neg_mask = prices < 0
    assert neg_mask.any(), "test requires negative-price hours in the fixture"

    # Positive-price hours: energy credited at 50 % of price, LGC at full value.
    pos = ~neg_mask
    expected_pos = -(prices[pos] * 0.5 + scn.lgc_price_aud_mwh - scn.market_spread)
    np.testing.assert_allclose(mc[pos].to_numpy(), expected_pos.to_numpy(), rtol=1e-9)

    # Negative-price hours: full (un-halved) energy cost, so selling is never
    # subsidised -- the LGC credit must not flip that into a subsidy.
    expected_neg = -(prices[neg_mask] + scn.lgc_price_aud_mwh - scn.market_spread)
    np.testing.assert_allclose(mc[neg_mask].to_numpy(), expected_neg.to_numpy(), rtol=1e-9)

    # The property that matters: deeply negative hours stay a genuine cost to
    # sell into, LGC notwithstanding.
    deep = prices < -scn.lgc_price_aud_mwh
    if deep.any():
        assert (mc[deep] > 0).all(), (
            "selling into deeply negative prices must remain costly even with "
            "the LGC credit"
        )


def test_lgc_credited_on_surplus_not_on_delivery():
    """LGC revenue attaches to market sales only.

    The PPA is bundled, so certificates on delivered MWh transfer to the
    offtaker inside `ppa_price`. Crediting them on the offtake link as well
    would double-count the tariff.
    """
    ts = _toy_ts()
    base = dataclasses.replace(_toy_scenario(), lgc_price_aud_mwh=0.0)
    with_lgc = dataclasses.replace(base, lgc_price_aud_mwh=30.0)

    n0, n1 = build_network(ts, base), build_network(ts, with_lgc)

    # Market-sell generator gains exactly the LGC price in revenue per MWh.
    mc0 = n0.generators.dynamic.marginal_cost["Gen_SellToMarket"]
    mc1 = n1.generators.dynamic.marginal_cost["Gen_SellToMarket"]
    np.testing.assert_allclose((mc0 - mc1).to_numpy(), 30.0, rtol=1e-9)

    # The PPA offtake link is untouched -- no certificate revenue on delivery.
    assert (
        float(n0.links.static.marginal_cost["IPPGen_to_PPAOfftake"])
        == float(n1.links.static.marginal_cost["IPPGen_to_PPAOfftake"])
    )


# ── Acceptance: the toy LP must build MORE than the (disabled) slider values ─

def test_toy_lp_builds_more_than_slider_values():
    """With cheap capex and generous caps the sizing LP must size the transport
    links well beyond the (disabled) slider caps (wind 50 + PV 50 = 100 MW).
    The bug pins the delivered/connection capacity at the slider values, so the
    reported sized link MW are the direct acceptance signal for W12."""
    ts = _toy_ts()
    sized = optimise_capacities(ts, _toy_scenario())
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
    sized = optimise_capacities(ts, scn)
    sim = apply_sizing(scn, sized)
    # The dispatch scenario must pin the transport links to the sized MW, not
    # re-derive them from nameplate, so the simulation matches what the LP sized.
    assert sim.wind_link_mw == pytest.approx(round(sized.wind_link_mw, 1))
    assert sim.pvbess_link_mw == pytest.approx(round(sized.pvbess_link_mw, 1))
    assert sim.sell_link_mw == pytest.approx(round(sized.sell_link_mw, 1))
    assert not sim.optimise_capacity

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
    sized = optimise_capacities(ts, scn)
    assert sized.wind_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3
    assert sized.pvbess_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3
    assert sized.sell_link_mw <= 120.0 * (1.0 + 1e-3) + 1e-3


# ── sizing diagnostics (W12e) ────────────────────────────────────────────────

def test_sizing_diagnostics_reports_costs_and_binding():
    from ppa.sizing import sizing_diagnostics

    ts = _toy_ts()
    scn = _toy_scenario()
    sized = optimise_capacities(ts, scn)
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


# ── U3: hard minimum-delivery constraint ─────────────────────────────────────

def test_hard_min_delivery_raises_delivery_share():
    """With the constraint on, the sizing LP must actually meet the SLA.

    Off, the delivery requirement is only a price signal — the LP compares the
    penalty against the cost of building and buys out of the SLA when that is
    cheaper. On, it becomes binding.
    """
    ts = _toy_ts()
    # Expensive build so the penalty is the cheaper escape valve, which is the
    # regime where the constraint changes the answer.
    scn = dataclasses.replace(
        _toy_scenario(),
        wind_capex_per_kw=6000.0,
        pv_capex_per_kw=4000.0,
        required_delivery_share=0.9,
        enable_shortfall=True,
    )

    soft = optimise_capacities(ts, dataclasses.replace(scn, enforce_min_delivery=False))
    hard = optimise_capacities(ts, dataclasses.replace(scn, enforce_min_delivery=True))

    assert soft.status == "ok" and hard.status == "ok"
    assert hard.sizing_delivery_share >= 0.9 - 1e-6, (
        f"hard constraint must reach the required share, got "
        f"{hard.sizing_delivery_share:.3%}"
    )
    assert hard.sizing_delivery_share > soft.sizing_delivery_share
    assert (hard.onsw_mw + hard.pv_mw) > (soft.onsw_mw + soft.pv_mw), (
        "meeting the SLA must require a larger build"
    )


def test_hard_min_delivery_infeasible_reports_why():
    """An unreachable SLA must say which limit is blocking, not just fail."""
    ts = _toy_ts()
    scn = dataclasses.replace(
        _toy_scenario(),
        enforce_min_delivery=True,
        required_delivery_share=0.9,
        max_build_wind_mw=1.0,      # nowhere near enough to serve the load
        max_build_pv_mw=1.0,
        max_build_bess_mw=0.0,
        enable_market_buy=False,
    )
    sized = optimise_capacities(ts, scn)
    assert sized.status != "ok"
    assert "minimum-delivery" in sized.condition, (
        f"infeasibility should name the blocking constraint, got: {sized.condition!r}"
    )


def test_hard_min_delivery_is_sizing_only():
    """The constraint must not appear in a fixed-capacity dispatch solve."""
    ts = _toy_ts()
    scn = dataclasses.replace(
        _toy_scenario(), optimise_capacity=False, enforce_min_delivery=True
    )
    n = build_network(ts, scn)
    solve(n, scn, ts)
    assert not any("MinDelivery" in str(name) for name in n.model.constraints)

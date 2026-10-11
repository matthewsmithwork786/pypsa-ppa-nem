"""WP9: SLA reporting and post-solve verification.

`extract_results` computes per-period (monthly / daily) PPA delivery share
D/L from the dispatch, mirroring the solver's `MinDelivery_Limit_M*`/`_D*`
constraints. This is the post-solve check of AGENTS.md §5.2: if the solver
constraint and this measure ever disagree by more than solver tolerance, that
disagreement is the bug signal. `min_*_delivery_share` and `n_*_below_sla`
surface the worst period and how often the target was missed, and a 0.5pp
shortfall against an enabled tier raises a `warnings` entry.
"""
from __future__ import annotations

import dataclasses
import pickle

import numpy as np
import pandas as pd
import pytest

from ppa.network import build_network
from ppa.results import (
    DispatchSeries,
    OptimisationResult,
    RevenueBreakdown,
    SummaryVolumes,
    extract_results,
)
from ppa.scenario import Scenario
from ppa.solver import solve


def _toy_ts(
    n_hours: int = 72,
    load_mw: float = 100.0,
    poor_month: int | None = None,
) -> pd.DataFrame:
    """Synthetic hourly timeseries, same shape as tests/test_sla_constraints.py.

    `poor_month` carves a near-zero-resource period (wind 0.01, PV 0) out of the
    profile so a tiered SLA has something to bind against.
    """
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    pv = np.asarray(np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85, dtype=float)
    wind = np.asarray(np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0), dtype=float)
    price = np.asarray(70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24))
    price[::11] = -20.0  # guaranteed negative-price hours
    if poor_month is not None:
        bad = idx.month == poor_month
        pv = pv.copy()
        wind = wind.copy()
        pv[bad] = 0.0
        wind[bad] = 0.01
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
        name="sla-reporting toy",
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
        sizing_method="full_hourly",
        simulation_years=1,
    )
    base.update(overrides)
    return Scenario(**base)


def _two_month_ts(load_mw: float = 100.0) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    """A full January + February 2025, for a two-month hand-computable window."""
    idx = pd.date_range("2025-01-01", periods=31 * 24, freq="h").append(
        pd.date_range("2025-02-01", periods=28 * 24, freq="h")
    )
    ts = pd.DataFrame(
        {
            "ts_PVGen": np.full(len(idx), 0.5),
            "ts_WindGen": np.full(len(idx), 0.5),
            "ts_MktPrice": np.full(len(idx), 50.0),
            "ppaload_mw": float(load_mw),
        },
        index=idx,
    )
    return idx, ts


def _zero_dispatch(n, idx: pd.DatetimeIndex) -> None:
    """Overwrite every dispatch series extract_results reads with a known value
    (zeros here), so the computed shares depend only on what the test sets on
    the offtake link."""
    for col in [
        "Gen_OnshoreWind", "Gen_PV", "Gen_BuyFromMarket", "Gen_AllowedShortfall",
        "Gen_Penalty", "Gen_SellToMarket",
    ]:
        n.generators.dynamic.p[col] = pd.Series(np.zeros(len(idx)), index=idx)
    n.storage_units.dynamic.p_dispatch["SU_BESS"] = pd.Series(np.zeros(len(idx)), index=idx)
    n.storage_units.dynamic.p_store["SU_BESS"] = pd.Series(np.zeros(len(idx)), index=idx)
    n.storage_units.dynamic.state_of_charge["SU_BESS"] = pd.Series(np.zeros(len(idx)), index=idx)


# ── 1. Shares from a known synthetic dispatch match a hand-computed answer ────

def test_period_delivery_shares_match_hand_computed():
    """Hand-set the offtake link flow to a known pattern (75 MW in Jan, 40 MW in
    Feb against a flat 100 MW load) and check extract_results reports exactly
    D/L per month/day, indexed by the period end."""
    idx, ts = _two_month_ts()
    scn = _toy_scenario(
        optimise_capacity=False,
        sla_monthly_enabled=True, sla_monthly_share=0.8,
        sla_daily_enabled=True, sla_daily_share=0.8,
    )
    n = build_network(ts, scn)
    # Negative p1 = supplying bus1; extract_results negates to positive delivery.
    delivery = pd.Series(np.where(idx.month == 1, 75.0, 40.0), index=idx)
    n.links.dynamic.p1["IPPGen_to_PPAOfftake"] = -delivery
    _zero_dispatch(n, idx)

    result = extract_results(n, scn, ts, "ok", "optimal", resolution_h=1.0)

    # Hand-computed answer straight from the raw series.
    expected_monthly = delivery.groupby(idx.to_period("M")).sum() / ts["ppaload_mw"].groupby(
        idx.to_period("M")
    ).sum()
    expected_daily = delivery.groupby(idx.to_period("D")).sum() / ts["ppaload_mw"].groupby(
        idx.to_period("D")
    ).sum()

    assert result.monthly_delivery_share is not None
    assert np.allclose(result.monthly_delivery_share.values, expected_monthly.values)
    # Index = period end.
    assert list(result.monthly_delivery_share.index) == [
        pd.Timestamp("2025-01-31 23:59:59.999999999"),
        pd.Timestamp("2025-02-28 23:59:59.999999999"),
    ]
    assert np.allclose(result.daily_delivery_share.values, expected_daily.values)
    assert len(result.daily_delivery_share) == 31 + 28

    assert result.summary.min_monthly_delivery_share == pytest.approx(0.40)
    assert result.summary.min_daily_delivery_share == pytest.approx(0.40)
    # Jan (0.75) and Feb (0.40) both sit below the 0.8 target; every day is
    # below it (0.75/0.40 < 0.8 across all 59 days).
    assert result.summary.n_months_below_sla == 2
    assert result.summary.n_days_below_sla == 59


# ── 2. Round-trip: min monthly share meets the requirement when it binds ──────

def test_min_monthly_delivery_share_meets_requirement_when_constraint_binds():
    """A deliberately weak February with the monthly min-delivery floor on forces
    that month onto the floor; extract_results must report the minimum at (not
    below) the required share — the AGENTS.md §5.2 regression."""
    ts = _toy_ts(60 * 24, poor_month=2)
    scn = _toy_scenario(
        required_delivery_share=0.0,       # annual floor never binds
        enforce_min_delivery=True,         # hard per-period delivery floors
        sla_monthly_enabled=True,
        sla_monthly_share=0.8,
        enable_market_buy=True,            # lets the zero-resource month buy its way to 0.8
        market_buy_share=1.0,
        ppa_price=0.05,                    # no incentive to deliver beyond the floor
    )
    n = build_network(ts, scn)
    status, cond = solve(n, scn, ts)
    assert status == "ok", cond

    names = {str(k) for k in n.model.constraints if "MinDelivery_Limit_M" in str(k)}
    assert "MinDelivery_Limit_M2025-02" in names

    result = extract_results(n, scn, ts, status, cond, resolution_h=1.0)
    assert result.summary.n_months_below_sla == 0
    # At the floor, not comfortably above it — the constraint actually binds.
    assert 0.8 - 1e-5 <= result.summary.min_monthly_delivery_share <= 0.8 + 1e-3


# ── 3. Old-shape pickled results still load ──────────────────────────────────

def test_old_shape_result_unpickles_with_sane_defaults():
    """A run_store payload pickled before WP9 (no SLA fields in __dict__) must
    still unpickle, and every new field must fall back to a sane default — in
    particular an unused tier must never read as a violation."""
    empty = pd.Series(dtype=float)
    dispatch = DispatchSeries(
        wind_gen=empty, pv_gen=empty, market_buy=empty, allowed_shortfall=empty,
        penalty_gen=empty, market_sell=empty, bess_dispatch=empty, bess_store=empty,
        soc=empty, ppa_delivery=empty,
    )
    summary = SummaryVolumes(
        total_load_mwh=100.0, ppa_delivered_mwh=90.0,
        renewable_and_storage_to_ppa_mwh=90.0, market_buy_to_ppa_mwh=0.0,
        allowed_shortfall_mwh=5.0, penalty_mwh=5.0, sold_to_market_mwh=0.0,
        wind_generation_mwh=90.0, pv_generation_mwh=0.0,
        bess_dispatch_mwh=0.0, bess_charge_mwh=0.0,
        fulfilled_share=0.9, allowed_shortfall_share_actual=0.05,
        buy_share_of_ppa_delivery=0.0, penalty_share_of_load=0.05,
    )
    revenue = RevenueBreakdown(
        ppa_revenue=9000.0, excess_revenue=0.0, market_purchase_cost=0.0,
        penalty_cost=0.0, net_revenue=9000.0, effective_capture_price=100.0,
    )
    result = OptimisationResult(
        scenario=_toy_scenario(optimise_capacity=False),
        dispatch=dispatch, summary=summary, revenue=revenue,
        solver_status="ok", solver_condition="optimal", n_period_hours=1.0,
    )
    # Simulate a pre-WP9 pickled payload: the new fields never entered __dict__.
    for name in ("monthly_delivery_share", "daily_delivery_share", "warnings"):
        result.__dict__.pop(name, None)
    for name in (
        "min_monthly_delivery_share", "min_daily_delivery_share",
        "n_months_below_sla", "n_days_below_sla",
    ):
        result.summary.__dict__.pop(name, None)

    loaded = pickle.loads(pickle.dumps(result))

    assert loaded.monthly_delivery_share is None
    assert loaded.daily_delivery_share is None
    assert loaded.summary.min_monthly_delivery_share == 1.0
    assert loaded.summary.min_daily_delivery_share == 1.0
    assert loaded.summary.n_months_below_sla == 0
    assert loaded.summary.n_days_below_sla == 0
    # `warnings` uses a default_factory (no class attribute), so old payloads
    # lack it entirely — the UI must read it via getattr, never directly.
    assert getattr(loaded, "warnings", None) is None


# ── 4. The 0.5pp verification warning ────────────────────────────────────────

def test_05pp_warning_fires_below_target_and_not_on_target():
    """When the monthly SLA is enabled but the achieved minimum sits more than
    0.5pp below the requirement, extract_results attaches a warning; on-target or
    within 0.5pp it must not. The dispatch is solved with the tier off (so the
    poor month genuinely under-delivers) and only the *reporting* scenario's SLA
    tier is switched on."""
    ts = _toy_ts(60 * 24, poor_month=2)
    scn = _toy_scenario(required_delivery_share=0.0)  # annual cap never binds; SLA off
    n = build_network(ts, scn)
    status, cond = solve(n, scn, ts)
    assert status == "ok"

    report = dataclasses.replace(scn, sla_monthly_enabled=True, sla_monthly_share=0.99)
    base = extract_results(n, report, ts, status, cond, resolution_h=1.0)
    min_achieved = base.summary.min_monthly_delivery_share
    assert 0.0 < min_achieved < 0.99, "the poor month should under-deliver"

    # On target → no warning.
    on_target = extract_results(
        n, dataclasses.replace(report, sla_monthly_share=min_achieved),
        ts, status, cond, resolution_h=1.0,
    )
    assert on_target.warnings == []

    # 0.49pp below → within tolerance → no warning.
    inside = extract_results(
        n, dataclasses.replace(report, sla_monthly_share=min_achieved + 0.0049),
        ts, status, cond, resolution_h=1.0,
    )
    assert inside.warnings == []

    # 0.51pp below → more than 0.5pp → warning fires, naming the tier.
    below = extract_results(
        n, dataclasses.replace(report, sla_monthly_share=min_achieved + 0.0051),
        ts, status, cond, resolution_h=1.0,
    )
    assert len(below.warnings) == 1
    assert "Monthly SLA" in below.warnings[0]
    assert below.summary.n_months_below_sla == 1

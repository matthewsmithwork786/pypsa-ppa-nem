"""WP8: tiered (monthly / daily) SLA constraints in the sizing LP.

The solver's annual allowed-shortfall and (opt-in) minimum-delivery constraints
can also be applied per calendar month and/or per calendar day. The daily tier
is the sharpest: one low-resource day that no portfolio within the build caps
can cover makes the whole LP infeasible, where an annual SLA would just spread
the shortfall.

The weighting test (test 4) is the regression for the historical 85.6% bug
(AGENTS.md §5.2): a hard 90% delivery constraint landed at 85.6% because a
`.sum()` was not multiplied by `n.snapshot_weightings["objective"]`.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from ppa.network import build_network
from ppa.scenario import Scenario
from ppa.sizing import optimise_capacities
from ppa.solver import solve

OFFTAKE_LINK = "IPPGen_to_PPAOfftake"


def _toy_ts(
    n_hours: int = 72,
    load_mw: float = 100.0,
    poor_month: int | None = None,
    poor_day: int | None = None,
    flat_price: bool = False,
) -> pd.DataFrame:
    """Synthetic hourly timeseries, same shape as tests/test_sizing_network.py.

    `poor_month` / `poor_day` carve a near-zero-resource period (wind 0.01,
    PV 0) out of the profile so the tiered SLA has something to bind against.
    """
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    pv = np.asarray(np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85, dtype=float)
    wind = np.asarray(np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0), dtype=float)
    if flat_price:
        price = np.full(n_hours, 50.0)
    else:
        price = np.asarray(70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24))
        price[::11] = -20.0  # guaranteed negative-price hours
    if poor_month is not None:
        bad = idx.month == poor_month
        pv = pv.copy()
        wind = wind.copy()
        pv[bad] = 0.0
        wind[bad] = 0.01
    if poor_day is not None:
        bad = idx.day == poor_day
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
        name="sla toy",
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


def _custom_constraint_names(n) -> set[str]:
    """Solver-added SLA / delivery constraint names (not pypsa's own)."""
    return {
        str(k)
        for k in n.model.constraints
        if str(k).startswith(("AllowedShortfall_", "MinDelivery_", "BuyFromMarket_"))
    }


def _shortfall_share(n, snaps: pd.Index) -> float:
    """Achieved weighted shortfall share `Σ w·S / Σ w·L` over `snaps`."""
    w = n.snapshot_weightings["objective"].loc[snaps]
    s = n.generators.dynamic.p["Gen_AllowedShortfall"].loc[snaps]
    load = n.loads.dynamic.p_set["Load_PPAOfftake"].loc[snaps]
    return float((s * w).sum()) / float((load * w).sum())


def _delivered_share(n, snaps: pd.Index) -> float:
    """Achieved weighted delivery share `Σ w·D / Σ w·L` over `snaps`."""
    w = n.snapshot_weightings["objective"].loc[snaps]
    d = n.links.dynamic.p[OFFTAKE_LINK].loc[snaps]
    load = n.loads.dynamic.p_set["Load_PPAOfftake"].loc[snaps]
    return float((d * w).sum()) / float((load * w).sum())


# ── 1. Off by default is a no-op ─────────────────────────────────────────────

def test_sla_disabled_is_noop():
    """With both tiers disabled the model carries exactly the same custom
    constraint set as the pre-WP8 code: the annual shortfall cap plus the
    annual minimum-delivery floor. Nothing named `_M` or `_D` may appear."""
    ts = _toy_ts()
    scn = _toy_scenario(enforce_min_delivery=True)
    n = build_network(ts, scn)
    status, _ = solve(n, scn, ts)
    assert status == "ok"

    names = _custom_constraint_names(n)
    assert names == {"AllowedShortfall_Limit", "MinDelivery_Limit"}
    assert not any("_M" in nm or "_D" in nm for nm in names)


# ── 2. Monthly cap binds ─────────────────────────────────────────────────────

def test_monthly_shortfall_cap_binds():
    """February has near-zero resource. Monthly SLA off → that month's
    delivered share falls below the target; on at 0.8 → every month's achieved
    shortfall share is ≤ 1 − sla_monthly_share."""
    ts = _toy_ts(60 * 24, poor_month=2)
    feb = ts.index[ts.index.month == 2]
    scn = _toy_scenario(required_delivery_share=0.0)  # annual cap never binds

    off = dataclasses.replace(scn)
    n_off = build_network(ts, off)
    status, _ = solve(n_off, off, ts)
    assert status == "ok"
    assert _delivered_share(n_off, feb) < 0.8, (
        "poor-resource month should under-deliver with the monthly SLA off"
    )

    on = dataclasses.replace(scn, sla_monthly_enabled=True, sla_monthly_share=0.8)
    n_on = build_network(ts, on)
    status, _ = solve(n_on, on, ts)
    assert status == "ok"
    names = _custom_constraint_names(n_on)
    assert "AllowedShortfall_Limit_M2025-02" in names
    for month in pd.unique(ts.index.month):
        snaps = ts.index[ts.index.month == month]
        assert _shortfall_share(n_on, snaps) <= (1.0 - 0.8) + 1e-6, (
            f"month {month} shortfall share violates the monthly cap"
        )
    assert _shortfall_share(n_on, feb) > 0.19, "monthly cap should bind in Feb"


# ── 3. Daily cap binds ───────────────────────────────────────────────────────

def test_daily_shortfall_cap_binds():
    """Day 15 has near-zero resource. Daily SLA off → that day's delivered
    share falls below the target; on at 0.8 → every day's achieved shortfall
    share is ≤ 1 − sla_daily_share."""
    ts = _toy_ts(30 * 24, poor_day=15)
    bad_day = ts.index[ts.index.day == 15]
    scn = _toy_scenario(required_delivery_share=0.0)  # annual cap never binds

    off = dataclasses.replace(scn)
    n_off = build_network(ts, off)
    status, _ = solve(n_off, off, ts)
    assert status == "ok"
    assert _delivered_share(n_off, bad_day) < 0.8, (
        "poor-resource day should under-deliver with the daily SLA off"
    )

    on = dataclasses.replace(scn, sla_daily_enabled=True, sla_daily_share=0.8)
    n_on = build_network(ts, on)
    status, _ = solve(n_on, on, ts)
    assert status == "ok"
    names = _custom_constraint_names(n_on)
    assert "AllowedShortfall_Limit_D2025-01-15" in names
    for day in pd.unique(ts.index.date):
        snaps = ts.index[ts.index.date == day]
        assert _shortfall_share(n_on, snaps) <= (1.0 - 0.8) + 1e-6, (
            f"day {day} shortfall share violates the daily cap"
        )
    assert _shortfall_share(n_on, bad_day) > 0.19, "daily cap should bind on the bad day"


# ── 4. Weighting — the 85.6% bug regression ──────────────────────────────────

def test_sla_share_identical_under_nonuniform_weights():
    """The hard minimum-delivery floor must land on the SAME achieved share
    with uniform weights and with deliberately non-uniform snapshot_weightings
    summing to the same total. This is the test that would have caught the
    historical bug where a hard 90% delivery constraint landed at 85.6% because
    a `.sum()` skipped the weighting (AGENTS.md §5.2)."""
    ts = _toy_ts(30 * 24)
    # ppa_price below the marginal cost of delivery: the LP only delivers what
    # the constraint forces, so every daily floor binds at exactly `share` and
    # the two runs must agree to solver tolerance.
    scn = _toy_scenario(
        required_delivery_share=0.0,
        enforce_min_delivery=True,
        sla_daily_enabled=True,
        sla_daily_share=0.8,
        ppa_price=0.05,
        wind_capex_per_kw=8000.0,
        pv_capex_per_kw=6000.0,
    )

    n_uniform = build_network(ts, scn)
    status, _ = solve(n_uniform, scn, ts)
    assert status == "ok"

    w = pd.Series(
        np.where(np.arange(len(ts)) % 2 == 0, 0.5, 1.5), index=ts.index, dtype=float
    )
    assert abs(float(w.sum()) - len(ts)) < 1e-9, "weightings must sum to the same total"
    n_nonuniform = build_network(ts, scn, snapshot_weightings=w)
    status, _ = solve(n_nonuniform, scn, ts)
    assert status == "ok"

    for day in pd.unique(ts.index.date):
        snaps = ts.index[ts.index.date == day]
        uniform = _delivered_share(n_uniform, snaps)
        nonuniform = _delivered_share(n_nonuniform, snaps)
        assert abs(uniform - nonuniform) <= 1e-6, (
            f"day {day}: uniform {uniform:.6f} vs non-uniform {nonuniform:.6f}"
        )
        assert nonuniform >= 0.8 - 1e-6, (
            f"day {day}: non-uniform weights must still meet the floor, got {nonuniform:.6f}"
        )


# ── 5. tsam: daily SLA holds per representative-day block ────────────────────

def test_tsam_daily_sla_holds_per_representative_day():
    """With tsam clustering, the daily SLA is applied to each
    (cluster, day-within-period) block, and `period_labels` covers every
    snapshot exactly once."""
    tsam = pytest.importorskip("tsam")
    from ppa.sizing_tsam import cluster_typical_periods

    clustered, weights = cluster_typical_periods(_toy_ts(52 * 168), n_periods=8)
    labels = weights.attrs["period_labels"]
    assert labels is not None
    # Labels cover every clustered snapshot exactly once, in 24 h blocks.
    assert len(labels) == len(clustered)
    assert labels.index.equals(clustered.index)
    assert labels.notna().all()
    assert (labels.value_counts() == 24).all(), (
        "every period-block must be exactly one representative day (24 h)"
    )

    scn = _toy_scenario(required_delivery_share=0.0, sla_daily_enabled=True, sla_daily_share=0.8)
    n = build_network(clustered, scn, snapshot_weightings=weights)
    status, _ = solve(n, scn, clustered, period_labels=labels)
    assert status == "ok"
    names = _custom_constraint_names(n)
    assert any("_D" in nm for nm in names), "tsam daily SLA must add per-block constraints"

    for k in sorted(labels.unique()):
        snaps = labels[labels == k].index
        assert _shortfall_share(n, snaps) <= (1.0 - 0.8) + 1e-6, (
            f"representative-day block {k} violates the daily cap"
        )


# ── 6. tsam monthly is refused (belt-and-braces) ─────────────────────────────

def test_tsam_monthly_sla_refused():
    """`solve` must refuse a monthly SLA whenever the snapshots are clustered
    (signalled by `period_labels`), even bypassing `validate_scenario`."""
    ts = _toy_ts(72)
    # A synthetic clustered snapshot set: period_labels not None is exactly the
    # signal the clusterer produces, so the guard fires without needing tsam.
    fake_labels = pd.Series(np.arange(len(ts)) // 24, index=ts.index)
    scn = _toy_scenario(sla_monthly_enabled=True, sla_monthly_share=0.8)
    n = build_network(ts, scn)
    with pytest.raises(ValueError, match="monthly SLA"):
        solve(n, scn, ts, period_labels=fake_labels)


# ── 7. Infeasibility is reported, not swallowed ──────────────────────────────

def test_infeasible_daily_sla_reports_hint():
    """A 100% daily SLA with no market buy and a solar-only portfolio is
    infeasible, and the condition must name the daily SLA with the hint text."""
    ts = _toy_ts(30 * 24, poor_day=15)
    scn = _toy_scenario(
        sla_daily_enabled=True,
        sla_daily_share=1.0,
        enforce_min_delivery=True,
        required_delivery_share=0.0,
        max_build_wind_mw=0.0,
        max_build_pv_mw=1.0,
        max_build_bess_mw=0.0,
    )
    sized = optimise_capacities(ts, scn)
    assert sized.status != "ok"
    assert "daily SLA of 100%" in sized.condition, (
        f"infeasibility must name the daily SLA, got: {sized.condition!r}"
    )


# ── 8. Monotonicity: tightening the daily share never shrinks the BESS ───────

def test_tightening_daily_share_never_decreases_bess():
    """Delivering a larger share of the zero-resource day's load requires
    strictly more storage. A violation means the constraint has the wrong sign."""
    ts = _toy_ts(30 * 24, poor_day=15, flat_price=True)
    base = _toy_scenario(
        required_delivery_share=0.0,
        enforce_min_delivery=True,
        sla_daily_enabled=True,
        include_bess=True,
        max_build_bess_mw=800.0,
    )
    s40 = optimise_capacities(ts, dataclasses.replace(base, sla_daily_share=0.4))
    s90 = optimise_capacities(ts, dataclasses.replace(base, sla_daily_share=0.9))
    assert s40.status == "ok", f"0.4 daily SLA failed: {s40.condition}"
    assert s90.status == "ok", f"0.9 daily SLA failed: {s90.condition}"
    assert s90.bess_mw >= s40.bess_mw - 1e-6, (
        f"tightening the daily SLA shrank the BESS ({s40.bess_mw:.1f} → {s90.bess_mw:.1f} MW); "
        "constraint sign wrong?"
    )

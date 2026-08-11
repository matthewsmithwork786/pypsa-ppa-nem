"""Resolution-awareness regression coverage for build_network/extract_results.

Encoding the same real period at a finer resolution (e.g. duplicating each
hourly row into two half-hour rows) must produce IDENTICAL summary MWh/$
totals once resolution_h is threaded through correctly -- these numbers used
to be silently overstated at any resolution other than exactly 1h/row (see
ppa/results.py::extract_results docstring). BESS is disabled here so the
solver has no arbitrage-timing degrees of freedom to introduce incidental
differences between the two encodings.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from ppa.data.timeseries_utils import build_year_timeseries
from ppa.data_loader import prepare_timeseries
from ppa.multi_year import run_multi_year
from ppa.network import build_network
from ppa.results import extract_results
from ppa.scenario import Scenario
from ppa.sizing import clamp_sizing_years, optimise_capacities
from ppa.solver import solve


def _scenario() -> Scenario:
    return Scenario(
        include_bess=False,
        onsw_mw=50.0,
        pv_mw=30.0,
        ppaload_mw=20.0,
        load_profile="flat",
        ppa_price=80.0,
        enable_market_buy=True,
        enable_market_sell=True,
        enable_shortfall=True,
        enable_penalty=True,
        run_financial_analysis=False,
        enable_counterfactual=False,
    )


def _hourly_ts() -> pd.DataFrame:
    idx = pd.date_range("2025-03-01", periods=4, freq="h")
    return pd.DataFrame(
        {
            "ts_PVGen": [0.0, 0.6, 0.9, 0.3],
            "ts_WindGen": [0.4, 0.2, 0.5, 0.7],
            "ts_MktPrice": [40.0, -10.0, 120.0, 60.0],
        },
        index=idx,
    )


def _upsampled_30min(ts_hourly: pd.DataFrame) -> pd.DataFrame:
    idx = pd.date_range(ts_hourly.index[0], periods=len(ts_hourly) * 2, freq="30min")
    return pd.DataFrame(
        {col: [v for v in ts_hourly[col] for _ in range(2)] for col in ts_hourly.columns},
        index=idx,
    )


def _solve(ts: pd.DataFrame, scenario: Scenario, resolution_h: float):
    ts_prep = prepare_timeseries(ts, scenario)
    n = build_network(ts_prep, scenario, resolution_h=resolution_h)
    status, condition = solve(n, scenario, ts_prep)
    assert status == "ok", f"solver failed: {status}/{condition}"
    return extract_results(n, scenario, ts_prep, status, condition, resolution_h=resolution_h)


def test_hourly_vs_upsampled_30min_give_identical_totals():
    scenario = _scenario()
    ts_hourly = _hourly_ts()
    ts_30min = _upsampled_30min(ts_hourly)

    result_hourly = _solve(ts_hourly, scenario, resolution_h=1.0)
    result_30min = _solve(ts_30min, scenario, resolution_h=0.5)

    assert result_hourly.n_period_hours == pytest.approx(4.0)
    assert result_30min.n_period_hours == pytest.approx(4.0)

    s_h, s_30 = result_hourly.summary, result_30min.summary
    for field in (
        "total_load_mwh", "ppa_delivered_mwh", "market_buy_to_ppa_mwh",
        "allowed_shortfall_mwh", "penalty_mwh", "sold_to_market_mwh",
        "wind_generation_mwh", "pv_generation_mwh", "fulfilled_share",
    ):
        assert getattr(s_h, field) == pytest.approx(getattr(s_30, field), abs=1e-6), field

    r_h, r_30 = result_hourly.revenue, result_30min.revenue
    for field in ("ppa_revenue", "excess_revenue", "market_purchase_cost", "penalty_cost", "net_revenue"):
        assert getattr(r_h, field) == pytest.approx(getattr(r_30, field), abs=1e-6), field


def test_default_resolution_h_is_hourly():
    """Omitting resolution_h on extract_results must still assume 1.0 -- no
    behavior change for every existing (always-hourly) caller."""
    scenario = _scenario()
    ts_prep = prepare_timeseries(_hourly_ts(), scenario)
    n = build_network(ts_prep, scenario)
    status, condition = solve(n, scenario, ts_prep)
    result_default = extract_results(n, scenario, ts_prep, status, condition)
    result_explicit = extract_results(n, scenario, ts_prep, status, condition, resolution_h=1.0)
    assert result_default.summary == result_explicit.summary
    assert result_default.n_period_hours == pytest.approx(4.0)


# ── WP6: build_year_timeseries at sub-hourly resolution ───────────────────────

def test_30min_source_against_30min_target_is_noop():
    """`_align_to_index` is positional and length-driven, so a full-length 30-min
    source lined up against a 30-min target year must pass through unchanged --
    no truncation, no tiling."""
    idx30 = pd.date_range("2025-01-01", periods=17_520, freq="30min")
    n = len(idx30)
    pv = pd.Series(np.linspace(0.0, 0.9, n), index=idx30)
    wind = pd.Series(np.full(n, 0.5), index=idx30)
    price = pd.Series(np.full(n, 60.0), index=idx30)
    ts = build_year_timeseries(
        sim_year=2025,
        weather_year=2025,
        ppa_load_mw=10.0,
        pv_cf_by_year={2025: pv},
        wind_cf_by_year={2025: wind},
        prices_by_year={2025: price},
        price_escalation_rate=0.0,
        load_profile="flat",
        resolution_minutes=30,
    )
    assert len(ts) == 17_520
    assert ts["ts_PVGen"].to_numpy() == pytest.approx(pv.to_numpy())
    assert ts["ts_WindGen"].to_numpy() == pytest.approx(wind.to_numpy())
    assert ts["ppaload_mw"].to_numpy() == pytest.approx(np.full(n, 10.0))


def test_hourly_source_tiles_onto_30min_target():
    """An hourly source is shorter than a 30-min target year, so the whole
    source is tiled end-to-end -- at the new 30-min resolution."""
    idx_h = pd.date_range("2025-01-01", periods=24, freq="h")
    pv = pd.Series(np.arange(24, dtype=float), index=idx_h)
    wind = pd.Series(np.full(24, 0.5), index=idx_h)
    price = pd.Series(np.full(24, 60.0), index=idx_h)
    ts = build_year_timeseries(
        sim_year=2025,
        weather_year=2025,
        ppa_load_mw=10.0,
        pv_cf_by_year={2025: pv},
        wind_cf_by_year={2025: wind},
        prices_by_year={2025: price},
        price_escalation_rate=0.0,
        load_profile="flat",
        resolution_minutes=30,
    )
    assert len(ts) == 17_520
    tiled = np.tile(np.arange(24, dtype=float), 2)
    assert ts["ts_PVGen"].to_numpy()[:48] == pytest.approx(tiled)


# ── WP6: resolution_h threaded through the multi-year runner ──────────────────

def _full_year_dicts():
    """Deterministic hourly 2025 CF/price series shared by both resolutions."""
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    hour = idx.hour
    pv = pd.Series(np.maximum(0.0, np.sin(2 * np.pi * hour / 24)) * 0.8, index=idx)
    wind = pd.Series(0.4 + 0.2 * np.sin(2 * np.pi * hour / 24 + 1.0), index=idx)
    price = pd.Series(60 + 30 * np.sin(2 * np.pi * (hour - 16) / 24), index=idx)
    return pv, wind, price


def test_30min_and_60min_runs_conserve_energy():
    """The 30-min and 60-min encodings of the same underlying data must produce
    the same total load MWh. Summing MW samples only equals MWh when each sample
    is one hour, so this is exactly the check that catches a missing
    resolution_h: a 30-min run would otherwise come out ~2x the energy."""
    scenario = dataclasses.replace(_scenario(), simulation_years=1)
    pv, wind, price = _full_year_dicts()
    kwargs = dict(
        pv_cf_by_year={2025: pv},
        wind_cf_by_year={2025: wind},
        prices_by_year={2025: price},
        first_sim_year=2025,
        max_workers=1,
    )
    r60 = run_multi_year(scenario=scenario, resolution_h=1.0, **kwargs)[0]
    r30 = run_multi_year(scenario=scenario, resolution_h=0.5, **kwargs)[0]

    # n_period_hours is the real period length regardless of resolution
    assert r60.n_period_hours == pytest.approx(8760.0, abs=1.0)
    assert r30.n_period_hours == pytest.approx(8760.0, abs=1.0)

    for field in (
        "total_load_mwh", "ppa_delivered_mwh", "sold_to_market_mwh",
        "wind_generation_mwh", "pv_generation_mwh",
    ):
        assert getattr(r30.summary, field) == pytest.approx(
            getattr(r60.summary, field), rel=0.005
        ), field


# ── WP6: sizing at sub-hourly resolution ──────────────────────────────────────

def _toy_sizing_ts(resolution_minutes: int) -> pd.DataFrame:
    n_rows = 72 * 60 // resolution_minutes
    idx = pd.date_range("2025-01-01", periods=n_rows, freq=f"{resolution_minutes}min")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    pv = np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85
    wind = np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0)
    price = 70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24)
    return pd.DataFrame(
        {
            "ts_PVGen": pv,
            "ts_WindGen": wind,
            "ts_MktPrice": price,
            "ppaload_mw": 100.0,
        },
        index=idx,
    )


def _sizing_scenario(resolution_minutes: int) -> Scenario:
    return Scenario(
        optimise_capacity=True,
        include_bess=False,
        onsw_mw=50.0,
        pv_mw=50.0,
        bess_mw=0.0,
        bess_mwh=0.0,
        max_build_wind_mw=2000.0,
        max_build_pv_mw=2000.0,
        max_build_bess_mw=0.0,
        wind_capex_per_kw=100.0,
        pv_capex_per_kw=100.0,
        bess_capex_per_kwh=50.0,
        sizing_method="full_hourly",
        simulation_years=1,
        nem_resolution_minutes=resolution_minutes,
    )


def test_sized_mw_agrees_between_30min_and_60min():
    """Sizing the same real period at 60-min and 30-min must agree on sized MW
    (within 10%). n_years is derived from len(ts) / (8760 * 60 / resolution),
    so a sub-hourly frame must NOT be treated as that many more years of hourly
    data -- that would silently multiply every capex term via horizon_years."""
    s60 = optimise_capacities(_toy_sizing_ts(60), _sizing_scenario(60))
    s30 = optimise_capacities(_toy_sizing_ts(30), _sizing_scenario(30))
    assert s60.status == "ok" and s30.status == "ok"
    assert s60.sizing_years_used == 1
    assert s30.sizing_years_used == 1
    assert s60.resolution_h == pytest.approx(1.0)
    assert s30.resolution_h == pytest.approx(0.5)
    for field in ("onsw_mw", "pv_mw", "bess_mw"):
        assert getattr(s30, field) == pytest.approx(getattr(s60, field), rel=0.10), field


# ── WP6: clamp_sizing_years direction ─────────────────────────────────────────

def test_clamp_sizing_years_finer_resolution_clamps_lower(monkeypatch):
    """Finer resolution means more snapshots per year and therefore more memory
    per simulated year, so the horizon clamp must shrink as resolution_h goes
    from 1.0 (hourly) down to 0.5 (30 min) and 1/12 (5 min) -- never grow."""
    from ppa import sizing

    monkeypatch.setattr(sizing, "_available_memory_mb", lambda: 10_000.0)
    years_hourly, _ = clamp_sizing_years(10, resolution_h=1.0)
    years_30min, _ = clamp_sizing_years(10, resolution_h=0.5)
    years_5min, _ = clamp_sizing_years(10, resolution_h=5 / 60)

    assert years_hourly < 10, "the clamp must actually bite for the test to mean anything"
    assert years_30min < years_hourly, (
        f"30-min resolution must clamp to fewer years than hourly "
        f"(got {years_30min} vs {years_hourly})"
    )
    assert years_5min <= years_30min

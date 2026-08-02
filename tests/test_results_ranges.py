"""W16 regression: Results-tab date-range filtering, 24 h average profiles and
link (connection) MW reporting.

`build_24h_avg` already exists; W16 adds sub-hourly cadence grouping, an
inclusive date-range filter, and per-link sized/peak/utilisation reporting on the
OptimisationResult.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ppa.results import build_24h_avg


def _dispatch_frame() -> pd.DataFrame:
    """Two distinctive days, hourly, values = hour-of-day (so means are easy)."""
    idx = pd.date_range("2025-03-01", periods=48, freq="h")
    return pd.DataFrame(
        {"Wind": np.arange(48) % 24, "PV (direct)": np.arange(48) % 24, "hour": idx.hour},
        index=idx,
    )


def _dispatch_frame_30min() -> pd.DataFrame:
    """Two days of 30-min data: 48 rows, hour+minute cadence."""
    idx = pd.date_range("2025-03-01", periods=96, freq="30min")
    hours = idx.hour * 2 + (idx.minute // 30)
    return pd.DataFrame(
        {"Wind": np.arange(96) % 96, "PV (direct)": np.arange(96) % 96, "hour": idx.hour},
        index=idx,
    )


# ── 16.2 build_24h_avg over a known frame ────────────────────────────────────

def test_build_24h_avg_returns_24_rows_with_correct_means():
    avg = build_24h_avg(_dispatch_frame())
    assert len(avg) == 24
    # Wind == hour-of-day in both days, so each hour's mean == its hour value.
    np.testing.assert_allclose(avg["Wind"].to_numpy(), np.arange(24), rtol=1e-9)


def test_build_24h_avg_sub_hourly_cadence_gives_48_rows():
    """30-min data must average onto its own cadence (24 h × 2 slots), not
    collapse onto 24 hourly points."""
    from ppa.results import build_24h_avg

    avg = build_24h_avg(_dispatch_frame_30min())
    assert len(avg) == 48


# ── 16.1 inclusive date-range filter ─────────────────────────────────────────

def test_range_filter_inclusive_of_both_endpoints():
    from ppa.results import filter_dispatch_range

    df = _dispatch_frame()
    start = pd.Timestamp("2025-03-01 06:00")
    end = pd.Timestamp("2025-03-02 06:00")  # inclusive: both edges kept
    sliced = filter_dispatch_range(df, start, end)
    assert sliced.index[0] == start
    assert sliced.index[-1] == end
    assert len(sliced) == 25  # 24-hour span + inclusive second edge


def test_range_filter_defaults_to_chosen_day_window():
    from ppa.results import filter_dispatch_range

    df = _dispatch_frame()
    sliced = filter_dispatch_range(df, chosen_day="2025-03-01")
    assert len(sliced) <= 7 * 24  # 7-day default window


# ── 16.3 link (connection) MW reporting ──────────────────────────────────────

def test_extract_results_reports_link_sized_and_peak_mw():
    """A dispatch solve must yield, per link, sized MW (p_nom_opt, falling back
    to p_nom) and the realised peak flow — the table used to spot a binding
    `grid_connection_max_mw`."""
    from ppa.network import build_network
    from ppa.results import extract_results
    from ppa.scenario import Scenario
    from ppa.solver import solve

    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    ts = pd.DataFrame(
        {
            "ts_PVGen": 0.3,
            "ts_WindGen": 0.4,
            "ts_MktPrice": 60.0,
            "ppaload_mw": 100.0,
        },
        index=idx,
    )
    scn = Scenario(onsw_mw=100.0, pv_mw=100.0, bess_mw=0.0, bess_mwh=0.0, include_bess=False)
    n = build_network(ts, scn, resolution_h=1.0)
    status, condition = solve(n, scn, ts)
    assert status.lower() in ("ok", "optimal")

    result = extract_results(n, scn, ts, status, condition, resolution_h=1.0)
    table = result.link_utilisation
    assert set(table.index) == {
        "OnshoreWind_to_IPPGeneration",
        "PVBESS_to_IPPGeneration",
        "BuyFromMarket_to_IPPGeneration",
        "IPPGen_to_SellToMarket",
        "IPPGen_to_PPAOfftake",
    }
    assert {"sized_mw", "peak_flow", "utilisation"}.issubset(table.columns)
    for name, row in table.iterrows():
        assert row["peak_flow"] <= row["sized_mw"] * (1.0 + 1e-6) + 1e-3, (
            f"link {name}: peak flow {row['peak_flow']:.2f} MW exceeds sized "
            f"{row['sized_mw']:.2f} MW"
        )

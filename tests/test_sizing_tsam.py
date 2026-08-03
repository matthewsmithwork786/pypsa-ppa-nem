"""W14 regression: tsam typical-period clustering for the sizing LP.

`cluster_typical_periods` must represent the hourly year with representative
days that preserve the energy of PV/wind/load (within 2 %) and the load peak
(within 5 %), and return per-snapshot weightings that sum to ≈ 8760 so the LP
integrates costs and storage over real hours. The tests skip when the optional
`tsam` package is not installed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

tsam = pytest.importorskip("tsam")

from ppa.sizing_tsam import cluster_typical_periods  # noqa: E402


@pytest.fixture()
def hourly_year() -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=8760, freq="h")
    t = np.arange(8760)
    pv = np.maximum(0, np.sin((t % 24) / 24 * 2 * np.pi)) * (0.9 + 0.1 * np.sin(t / 8760 * 2 * np.pi))
    wind = 0.35 + 0.15 * np.cos(t / 200)
    load = 100 + 25 * np.maximum(0, np.sin((t - 8) % 24 / 24 * 2 * np.pi)) + 15 * np.sin(t / 8760 * 2 * np.pi)
    return pd.DataFrame(
        {
            "ts_PVGen": pv,
            "ts_WindGen": wind,
            "ts_MktPrice": 50 + 30 * np.cos((t % 24) / 24 * 2 * np.pi),
            "ppaload_mw": load,
        },
        index=idx,
    )


def test_cluster_returns_typical_weeks_and_weightings(hourly_year):
    """Periods are WEEKS (168 h) by default, not days.

    Measured against the exact LP, typical weeks with mean representation land
    the fleet within ~10% and the BESS within ~4%, where typical days were +46%
    and +164% (docs/sizing_experiments.md E11).
    """
    clustered, weights = cluster_typical_periods(hourly_year, n_periods=12)
    # 12 periods x 168 h, plus any extreme periods tsam appends.
    assert len(weights) == len(clustered)
    assert {"ts_PVGen", "ts_WindGen", "ts_MktPrice", "ppaload_mw"}.issubset(clustered.columns)
    assert 12 * 168 <= len(clustered) <= 20 * 168
    assert len(clustered) % 168 == 0, "clustered length must be a whole number of weeks"
    # Weightings sum to the total hours modelled (one year).
    assert abs(float(weights.sum()) - 8760.0) <= 1.0


def test_cluster_preserves_annual_energy_within_2pct(hourly_year):
    clustered, weights = cluster_typical_periods(hourly_year, n_periods=12)
    for col in ["ts_PVGen", "ts_WindGen", "ts_MktPrice", "ppaload_mw"]:
        orig = float(hourly_year[col].sum())
        weighted = float((clustered[col] * weights).sum())
        assert abs((weighted - orig) / orig) <= 0.02, (
            f"{col}: clustered energy {weighted:.1f} vs original {orig:.1f} "
            f"({100 * (weighted - orig) / orig:+.2f}%)"
        )


def test_cluster_preserves_load_peak_within_5pct(hourly_year):
    clustered, weights = cluster_typical_periods(hourly_year, n_periods=8)
    orig_peak = float(hourly_year["ppaload_mw"].max())
    clustered_peak = float(clustered["ppaload_mw"].max())
    assert abs(clustered_peak - orig_peak) / orig_peak <= 0.05, (
        f"clustered load peak {clustered_peak:.1f} vs original {orig_peak:.1f}"
    )


# ── U8 step 4: validation must compare sized MW, not delivery share ──────────

def _sizing_scenario(**overrides):
    """Cheap-capex toy scenario so the LP actually builds something."""
    import dataclasses

    from ppa.scenario import Scenario

    base = dict(
        name="tsam-validate toy", optimise_capacity=True,
        onsw_mw=50.0, pv_mw=50.0, include_bess=True, bess_mw=20.0, bess_mwh=80.0,
        max_build_wind_mw=2000.0, max_build_pv_mw=2000.0, max_build_bess_mw=2000.0,
        wind_capex_per_kw=100.0, pv_capex_per_kw=100.0, bess_capex_per_kwh=50.0,
        simulation_years=1, sizing_resolution_h=1,
    )
    base.update(overrides)
    return Scenario(**base)


def test_validate_sizing_representation_compares_sized_mw(hourly_year):
    """The clustering check must measure the sizing decision itself.

    W14's original metric compared the sizing LP's delivery share against the
    full simulation's, but that stayed within 2-4 pp even when the sized fleet
    was ~19% wrong (docs/sizing_experiments.md E2) — delivery share is simply
    not sensitive to the sizing decision. This compares sized MW per technology
    against the exact hourly LP.
    """
    from ppa.sizing import validate_sizing_representation

    ts = hourly_year
    scn = _sizing_scenario(sizing_method="tsam", sizing_n_periods=8)
    report = validate_sizing_representation(ts, scn, tolerance=0.05)

    assert {r["Technology"] for r in report["rows"]} == {"Wind", "Solar", "BESS"}
    assert report["method"] == "tsam"
    assert report["exact_status"] == "ok" and report["chosen_status"] == "ok"
    assert report["reference_seconds"] > 0
    assert isinstance(report["within_tolerance"], bool)
    # within_tolerance must follow max_abs_delta against the stated tolerance.
    assert report["within_tolerance"] == (report["max_abs_delta"] <= report["tolerance"])


def test_validate_sizing_representation_exact_method_matches_itself(hourly_year):
    """full_hourly against full_hourly must be a perfect match."""
    from ppa.sizing import validate_sizing_representation

    ts = hourly_year
    scn = _sizing_scenario(sizing_method="full_hourly")
    report = validate_sizing_representation(ts, scn)

    assert report["max_abs_delta"] == pytest.approx(0.0, abs=1e-6)
    assert report["within_tolerance"]


def test_validate_handles_zero_build_without_dividing_by_zero(hourly_year):
    """A technology the exact LP does not build reports None, not inf/NaN."""
    from ppa.sizing import validate_sizing_representation

    ts = hourly_year
    # No BESS allowed, so the exact LP builds 0 MW of it.
    scn = _sizing_scenario(
        sizing_method="full_hourly", include_bess=False,
        bess_mw=0.0, bess_mwh=0.0, max_build_bess_mw=0.0,
    )
    report = validate_sizing_representation(ts, scn)
    bess = next(r for r in report["rows"] if r["Technology"] == "BESS")
    assert bess["Difference"] is None
    assert report["max_abs_delta"] == report["max_abs_delta"]  # not NaN


# ── Regression: occurrence count must not be used as the storage timestep ────

def test_store_weighting_is_intra_period_not_occurrence_count(hourly_year):
    """`snapshot_weightings["stores"]` is the dt in the storage energy balance.

    Occurrence counts (5-55 h on a 12-period year) scale cost and energy to the
    represented year, but they are NOT the elapsed time between consecutive
    snapshots. Using them as the storage dt makes one step span up to ~55 h, and
    a 4-hour battery cannot shift anything across that -- which sized storage to
    exactly zero under every typical-period configuration. Inside a
    representative period the snapshots are 1 h apart.
    """
    from ppa.network import build_network
    from ppa.scenario import Scenario

    clustered, weights = cluster_typical_periods(hourly_year, n_periods=8)
    assert weights.max() > 5.0, "fixture must have non-uniform occurrence counts"

    n = build_network(
        clustered,
        Scenario(optimise_capacity=True, sizing_method="tsam"),
        snapshot_weightings=weights,
    )

    # Cost/energy scaling keeps the occurrence counts ...
    np.testing.assert_allclose(
        n.snapshot_weightings["objective"].to_numpy(), weights.to_numpy(), rtol=1e-9
    )
    # ... but the storage timestep must be the real intra-period step.
    assert (n.snapshot_weightings["stores"] == 1.0).all(), (
        "storage dt must be the intra-period hour, not the occurrence count"
    )


def test_storage_is_buildable_under_typical_periods(hourly_year):
    """A battery must be able to shift energy in a clustered LP at all.

    Guards the class of bug rather than a specific number: with the occurrence
    count as the storage dt the LP could never build storage, whatever the
    economics.
    """
    from ppa.sizing import optimise_capacities

    scn = _sizing_scenario(
        sizing_method="tsam", sizing_n_periods=8,
        bess_capex_per_kwh=5.0,      # deliberately cheap: if storage can ever
        max_build_bess_mw=500.0,     # be built, it must be built here
    )
    sized = optimise_capacities(hourly_year, scn)
    assert sized.status == "ok"
    assert sized.bess_mw > 1.0, (
        "clustered sizing built no storage even at throwaway capex -- the "
        "storage timestep is probably wrong again"
    )

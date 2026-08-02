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


def test_cluster_returns_typical_days_and_weightings(hourly_year):
    clustered, weights = cluster_typical_periods(hourly_year, n_periods=12)
    # Representative days are hourly rows (12 periods × 24 h, plus any extreme
    # periods tsam appends) over the same columns.
    assert len(weights) == len(clustered)
    assert {"ts_PVGen", "ts_WindGen", "ts_MktPrice", "ppaload_mw"}.issubset(clustered.columns)
    assert 12 * 24 <= len(clustered) <= 40 * 24
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

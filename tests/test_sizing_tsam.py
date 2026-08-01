"""W14: typical-period sizing representation via `tsam`.

Requires the optional `tsam` dependency; the whole module skips when it is not
installed. When W14 lands and `tsam` is present, these tests validate that
clustering preserves the annual energy of PV/wind/load and the load peak, and
that the returned snapshot weightings sum to ≈ 8760.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

tsam = pytest.importorskip("tsam")


def _synthetic_ts(n_days: int = 365) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n_days * 24, freq="h")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    pv = np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85
    wind = np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0)
    price = 70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24)
    load = 100.0 + 30.0 * np.sin(2 * np.pi * idx.hour / 24)
    return pd.DataFrame(
        {"ts_PVGen": pv, "ts_WindGen": wind, "ts_MktPrice": price, "ppaload_mw": load},
        index=idx,
    )


def test_cluster_preserves_annual_energy_within_two_percent():
    from ppa.sizing_tsam import cluster_typical_periods

    ts = _synthetic_ts()
    clustered, weights = cluster_typical_periods(ts, n_periods=12, hours_per_period=24)

    for col in ("ts_PVGen", "ts_WindGen", "ppaload_mw"):
        original_energy = float(ts[col].sum())
        clustered_energy = float((clustered[col] * weights).sum())
        assert clustered_energy == pytest.approx(original_energy, rel=0.02), (
            f"annual energy of {col} drifted by more than 2% "
            f"({clustered_energy:.0f} vs {original_energy:.0f})"
        )


def test_cluster_preserves_load_peak_within_five_percent():
    from ppa.sizing_tsam import cluster_typical_periods

    ts = _synthetic_ts()
    clustered, _ = cluster_typical_periods(ts, n_periods=12, hours_per_period=24)
    assert clustered["ppaload_mw"].max() == pytest.approx(ts["ppaload_mw"].max(), rel=0.05)


def test_snapshot_weightings_sum_to_8760():
    from ppa.sizing_tsam import cluster_typical_periods

    ts = _synthetic_ts()
    _, weights = cluster_typical_periods(ts, n_periods=12, hours_per_period=24)
    assert float(np.asarray(weights).sum()) == pytest.approx(8760, abs=1)


def test_hours_per_period_168_supported():
    from ppa.sizing_tsam import cluster_typical_periods

    ts = _synthetic_ts(n_days=365)
    clustered, weights = cluster_typical_periods(ts, n_periods=6, hours_per_period=168)
    assert int(clustered.shape[0]) == 6 * 168
    assert float(np.asarray(weights).sum()) == pytest.approx(8760, abs=1)

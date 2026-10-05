"""Extremes must not shorten or shift the cluster occurrence vector."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("tsam")

from ppa.sizing_tsam import cluster_typical_periods


def _series():
    # Seasonally smooth data creates singleton clusters whose members may be
    # entirely reassigned to an extreme, leaving a zero-occurrence base ID.
    t = np.arange(52 * 168 + 24)
    return pd.DataFrame({
        "ts_PVGen": np.maximum(0, np.sin(t % 24 / 24 * 2 * np.pi))
                    * (0.9 + 0.1 * np.sin(t / 8760 * 2 * np.pi)),
        "ts_WindGen": 0.35 + 0.15 * np.cos(t / 200),
        "ts_MktPrice": 50 + 30 * np.cos(t % 24 / 24 * 2 * np.pi),
        "ppaload_mw": 100 + 25 * np.maximum(0, np.sin((t - 8) % 24 / 24 * 2 * np.pi))
                     + 15 * np.sin(t / 8760 * 2 * np.pi),
    }, index=pd.date_range("2025-01-01", periods=len(t), freq="h"))


@pytest.mark.parametrize("n_periods", [4, 8, 16, 40])
def test_extremes_preserve_weights_peaks_and_annual_totals(n_periods):
    ts = _series()
    clustered, weights = cluster_typical_periods(ts, n_periods=n_periods)
    assert np.isfinite(weights).all()
    assert (weights > 0).all()
    assert weights.sum() == pytest.approx(len(ts))
    assert weights.attrs["period_labels"].index.equals(clustered.index)
    for column in ["ts_PVGen", "ts_WindGen", "ppaload_mw"]:
        assert clustered[column].max() == pytest.approx(ts[column].max(), rel=0.005)
    for column in ts:
        assert float((clustered[column] * weights).sum()) == pytest.approx(
            float(ts[column].sum()), rel=0.02
        )


def test_medoid_is_not_silently_changed_to_mean():
    ts = _series()
    mean, _ = cluster_typical_periods(ts, n_periods=4, representation="mean")
    medoid, weights = cluster_typical_periods(ts, n_periods=4, representation="medoid")
    assert not np.allclose(mean, medoid)
    assert weights.sum() == pytest.approx(len(ts))


def test_unaffected_configuration_matches_upstream_aggregate():
    import tsam
    from tsam.config import ClusterConfig, ExtremeConfig

    ts = _series()
    expected = tsam.aggregate(
        ts, n_clusters=40, period_duration=168,
        cluster=ClusterConfig(method="hierarchical", representation="mean"),
        extremes=ExtremeConfig(method="new_cluster",
                               max_value=["ppaload_mw", "ts_PVGen", "ts_WindGen"],
                               min_value=["ts_PVGen", "ts_WindGen"]),
    )
    actual, weights = cluster_typical_periods(ts, n_periods=40)
    representatives = expected.cluster_representatives[actual.columns]
    np.testing.assert_allclose(actual.to_numpy(), representatives.to_numpy(), rtol=1e-12)
    expected_weights = [expected.cluster_weights[cid]
                        for cid in representatives.index.get_level_values(0)]
    np.testing.assert_allclose(weights, expected_weights, rtol=1e-12)

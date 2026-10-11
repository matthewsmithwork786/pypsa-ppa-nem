"""Typical-period representation for the capacity-sizing LP.

Typical weeks retain consecutive daily resource patterns while reducing the
LP size. Storage sizing can differ from the exact hourly result; use
`ppa.sizing.validate_sizing_representation` to quantify that difference.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd


def tsam_available() -> bool:
    try:
        import tsam  # noqa: F401
        return True
    except ImportError:
        return False


def _aggregate_with_aligned_occurrences(data, n_periods, hours_per_period,
                                        representation, extreme_periods):
    """Keep cluster IDs aligned when tsam rescales occurrence counts."""
    from tsam.config import REPRESENTATION_MAPPING
    from tsam.exceptions import LegacyAPIWarning
    from tsam.timeseriesaggregation import TimeSeriesAggregation

    class AlignedAggregation(TimeSeriesAggregation):
        def _rescaleClusterPeriods(self, clusterOrder, clusterPeriods, extremeClusterIdx):
            counts = self._clusterPeriodNoOccur
            self._clusterPeriodNoOccur = {
                i: counts.get(i, 0) for i in range(len(clusterPeriods))
            }
            return super()._rescaleClusterPeriods(
                clusterOrder, clusterPeriods, extremeClusterIdx
            )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LegacyAPIWarning)
        aggregation = AlignedAggregation(
            timeSeries=data, noTypicalPeriods=n_periods,
            hoursPerPeriod=hours_per_period, clusterMethod="hierarchical",
            representationMethod=REPRESENTATION_MAPPING[representation],
            extremePeriodMethod="new_cluster_center" if extreme_periods else "None",
            addPeakMax=["ppaload_mw", "ts_PVGen", "ts_WindGen"] if extreme_periods else [],
            addPeakMin=["ts_PVGen", "ts_WindGen"] if extreme_periods else [],
        )
        representatives = aggregation.createTypicalPeriods()
    return representatives, aggregation.clusterPeriodNoOccur


def cluster_typical_periods(
    ts: pd.DataFrame,
    n_periods: int = 16,
    hours_per_period: int = 168,
    extreme_periods: bool = True,
    representation: str = "mean",
) -> tuple[pd.DataFrame, pd.Series]:
    """Cluster the sizing timeseries into representative typical periods.

    Returns `(clustered_ts, weights)`:
    - `clustered_ts`: `n_periods × hours_per_period` hourly rows (plus any
      extreme periods appended by tsam) over the same columns as `ts`.
    - `weights`: per-snapshot weighting (`pd.Series` aligned to the clustered
      index) that sum to the total hours modelled (≈ 8760 for one year). Feed
      it to `build_network(..., snapshot_weightings=weights)` so costs, energy
      and storage integrate over real hours.

    The period-block labels ride along as `weights.attrs["period_labels"]`: a
    `pd.Series` of one integer per clustered snapshot identifying its
    `(cluster_id, day_within_period)` block (extra extreme periods at the end
    are labelled too). `ppa.solver.solve` uses these — NOT the synthetic
    calendar index, which corresponds to nothing real — to build daily SLA
    groups. `None` when there was nothing to cluster (window shorter than one
    period).

    `extreme_periods=True` preserves peak-load and dark-lull periods (via
    tsam's `addPeakMax`/`addPeakMin`) so the sized fleet must still cover the
    hours that matter instead of optimising only for the average day.

    Storage capacity can differ from the exact hourly result.
    """
    if not tsam_available():
        raise ImportError(
            "Typical-days sizing needs the optional 'tsam' package. "
            "Install it (`pip install tsam`)."
        )

    # Limit clusters to the available periods; short reference windows may
    # contain fewer periods than the requested default representation.
    available_periods = len(ts) // max(1, hours_per_period)
    if available_periods < 2:
        # Nothing to cluster — hand back the window itself, weighted one hour
        # per row so downstream cost/energy integration is unchanged.
        weights = pd.Series(1.0, index=ts.index, dtype=float)
        weights.attrs["period_labels"] = None
        return ts.copy(), weights
    n_periods = max(1, min(int(n_periods), available_periods))

    cols = ["ts_PVGen", "ts_WindGen", "ts_MktPrice", "ppaload_mw"]
    data = ts[cols]

    # Mean weeks retain consecutive resource patterns. Extreme periods retain
    # peak load and generation conditions that affect connection sizing.
    reps, occ = _aggregate_with_aligned_occurrences(
        data, n_periods, hours_per_period, representation, extreme_periods
    )
    # Empty base clusters no longer represent any historical hours. Keeping
    # their rows would create daily SLA groups with zero total load/weight.
    # Their members are already assigned to retained extreme clusters.
    reps = reps.loc[reps.index.get_level_values(0).map(lambda cid: occ[cid] > 0)]
    cluster_ids = reps.index.get_level_values(0)

    # Normalise column order to the input's (tsam v3 sorts alphabetically; v4
    # follows input order — make it deterministic either way). Give the clustered
    # frame a real hourly DatetimeIndex (starting at the input's year) so
    # downstream code that reads `ts.index.year` (e.g. per-year sizing caps in
    # `ppa.solver`) keeps working.
    clustered = reps[cols].reset_index(drop=True)
    start_year = pd.DatetimeIndex(ts.index).year[0]
    clustered.index = pd.date_range(f"{start_year}-01-01", periods=len(clustered), freq="h")
    clustered.index.name = "snapshot"
    weights = pd.Series([float(occ[c]) for c in cluster_ids], dtype=float)
    weights.index = clustered.index

    # Label representative days for daily SLA constraints. Non-integral-day
    # periods use one group per period.
    cids = np.asarray(cluster_ids, dtype=int)
    hour_in_period = pd.Series(cluster_ids).groupby(cluster_ids).cumcount()
    days_per_period = hours_per_period // 24
    if hours_per_period % 24 == 0:
        labels = cids * days_per_period + (hour_in_period // 24).to_numpy()
    else:
        labels = cids
    period_labels = pd.Series(labels, index=clustered.index, dtype=int)
    weights.attrs["period_labels"] = period_labels
    return clustered, weights

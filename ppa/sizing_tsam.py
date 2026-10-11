"""W14: better sizing representation via `tsam` typical periods.

The sizing LP (`ppa.sizing.optimise_capacities`) can run over the full hourly
year, or — with the optional `tsam` dependency — typical periods at full
hourly resolution. Typical weeks preserve intra-day variability while
keeping the LP ~2 orders of magnitude smaller than the full year.

STORAGE NOTE. Clustered sizing under-sizes storage against the exact LP
(207 vs 299 MW on the Corporate PPA under a hard SLA), which is ordinary
clustering error -- use `ppa.sizing.validate_sizing_representation` to measure
it for a given scenario.

It used to size storage to *exactly zero* under every configuration. That was
not a clustering limitation but a units error: the occurrence count was being
used as `snapshot_weightings["stores"]`, i.e. as the dt in the storage energy
balance, making one snapshot span up to ~55 h so that no short-duration battery
could shift anything (docs/sizing_experiments.md E9). Fixed in
`ppa.network.build_network` via `intra_period_hours`. W14's earlier diagnosis
(cyclic SoC within a representative day) and its proposed remedy (typical
weeks) were both wrong -- weeks were tested and changed nothing.
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
    """Keep zero-occurrence cluster IDs aligned during tsam 3.4.2 rescaling.

    Moving the sole member of a base cluster into an extreme cluster leaves
    the base representative in place but removes its ID from the occurrence
    dictionary. Upstream rescaling indexes that shortened dictionary as a
    dense array, corrupting weights or raising IndexError. Fill the missing
    IDs with zero before calling the unchanged upstream rescaler. This local
    subclass affects only our aggregation; extremes, representation, partial
    final-period weights and column-mean preservation are unchanged.
    """
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

    BESS caveat: clustered sizing under-sizes storage relative to the exact LP
    (ordinary clustering error, not the structural zero it used to be — see the
    module docstring).
    """
    if not tsam_available():
        raise ImportError(
            "Typical-days sizing needs the optional 'tsam' package. "
            "Install it (`pixi add --pypi tsam` or `pip install tsam`)."
        )

    # You cannot ask for more clusters than there are periods to cluster. The
    # period-reference path solves a user-chosen window (a calendar month is
    # ~4.3 weeks), so the 16-week default exceeded the sample count and tsam
    # raised "cannot extract more clusters than samples". Clamp instead: a
    # window with fewer periods than clusters is already its own best
    # representation.
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

    # Typical WEEKS with MEAN representation, measured against the exact hourly
    # LP (docs/sizing_experiments.md E11). The two settings matter jointly:
    #
    #   16 weeks, mean     RMSE 11.8   fleet +10.5%   BESS  -4%    15 s
    #   26 weeks, mean     RMSE  7.2   fleet  +8.8%   BESS +10%    47 s
    #    8 weeks, medoid   RMSE 16.9   fleet -11.8%   BESS -82%     4 s
    #   12 days,  medoid   RMSE 39.0   fleet +46.0%   BESS +164%    2 s   (old default)
    #
    # Medoid picks one real period per cluster, and a single real week is a poor
    # proxy for storage stress -- it under-sizes the BESS by 47-90%. Averaging
    # the cluster keeps the weekly load/generation shape while smoothing the
    # period-to-period noise the battery is not sized against.
    #
    # Weeks beat days because a 168 h period contains the real day-to-day
    # sequence, so the LP sees consecutive poor-resource days rather than
    # 24 h fragments stitched together by cyclic state of charge.
    # Peak generation is retained alongside load/dark-lull extremes: it sets
    # the connection capacity required by the subsequent full hourly solve.
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

    # WP8 period-block labels for daily SLA groups in `ppa.solver.solve`. tsam
    # returns each cluster's rows in chronological order, so a per-cluster
    # cumulative count is the hour-within-period. days_per_period is derived
    # from hours_per_period (168 -> 7) rather than hard-coded; a period that is
    # not a whole number of days cannot be sliced into meaningful representative
    # days, so it falls back to one group per period (an approximation, but a
    # bounded one). Extreme periods appended by tsam are extra clusters and are
    # labelled exactly like the ordinary ones.
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

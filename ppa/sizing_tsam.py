"""W14: better sizing representation via `tsam` typical periods.

The sizing LP (`ppa.sizing.optimize_capacities`) can run over the full hourly
year, coarse block averages, or — with the optional `tsam` dependency —
typical periods at full hourly resolution. Typical days preserve intra-day
variability (which the 3-hourly blocks smooth away) while keeping the LP ~2
orders of magnitude smaller than the full year.

Typical-period caveat for storage: with typical *days* and
`cyclic_state_of_charge=True` the battery cycles within each representative
day, so multi-day storage is mis-modelled. This is acceptable for a 2-4 h BESS;
for longer durations use `hours_per_period=168` (typical weeks) or implement
inter-period SoC linking. The caveat is called out in the UI help and the
`cluster_typical_periods` docstring.
"""
from __future__ import annotations

import warnings

import pandas as pd


def tsam_available() -> bool:
    try:
        import tsam  # noqa: F401
        return True
    except ImportError:
        return False


def cluster_typical_periods(
    ts: pd.DataFrame,
    n_periods: int = 12,
    hours_per_period: int = 24,
    extreme_periods: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """Cluster the sizing timeseries into representative typical periods.

    Returns `(clustered_ts, weights)`:
    - `clustered_ts`: `n_periods × hours_per_period` hourly rows (plus any
      extreme periods appended by tsam) over the same columns as `ts`.
    - `weights`: per-snapshot weighting (`pd.Series` aligned to the clustered
      index) that sum to the total hours modelled (≈ 8760 for one year). Feed
      it to `build_network(..., snapshot_weightings=weights)` so costs, energy
      and storage integrate over real hours.

    `extreme_periods=True` preserves peak-load and dark-lull periods (via
    tsam's `addPeakMax`/`addPeakMin`) so the sized fleet must still cover the
    hours that matter instead of optimising only for the average day.

    BESS caveat: typical *days* with `cyclic_state_of_charge=True` cycle the
    battery within each representative day — fine for a 2-4 h BESS, wrong for
    multi-day storage. Use `hours_per_period=168` (typical weeks) or implement
    inter-period SoC linking for long-duration storage.
    """
    if not tsam_available():
        raise ImportError(
            "Typical-days sizing needs the optional 'tsam' package. "
            "Install it (`pixi add --pypi tsam` or `pip install tsam`)."
        )
    import tsam
    from tsam.config import ClusterConfig, ExtremeConfig

    cols = ["ts_PVGen", "ts_WindGen", "ts_MktPrice", "ppaload_mw"]
    data = ts[cols]

    cluster = ClusterConfig(method="hierarchical")
    extremes = (
        ExtremeConfig(
            method="new_cluster",
            max_value=["ppaload_mw"],
            min_value=["ts_PVGen", "ts_WindGen"],
        )
        if extreme_periods
        else None
    )

    # tsam v3 sorts the result columns alphabetically and warns that v4 will
    # follow input order; we reorder to `cols` below regardless, so silence it.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=FutureWarning,
            message=".*sorted alphabetically.*",
        )
        result = tsam.aggregate(
            data,
            n_clusters=n_periods,
            period_duration=hours_per_period,
            cluster=cluster,
            extremes=extremes,
        )

    reps = result.cluster_representatives
    cluster_ids = reps.index.get_level_values(0)
    occ = result.cluster_weights

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
    return clustered, weights

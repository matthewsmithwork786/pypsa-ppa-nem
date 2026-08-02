"""W14: better sizing representation via `tsam` typical periods.

The sizing LP (`ppa.sizing.optimise_capacities`) can run over the full hourly
year, coarse block averages, or — with the optional `tsam` dependency —
typical periods at full hourly resolution. Typical days preserve intra-day
variability (which the 3-hourly blocks smooth away) while keeping the LP ~2
orders of magnitude smaller than the full year.

STORAGE CAVEAT (measured, docs/sizing_experiments.md E7) — clustering
systematically sizes storage to ZERO, and this cannot be configured away.

W14 assumed the cause was `cyclic_state_of_charge=True` forcing the battery
back to its starting SoC within each representative day, and proposed
`hours_per_period=168` (typical weeks) as the remedy. **Both the diagnosis and
the remedy are wrong.** Typical weeks were tested and still size the BESS to
zero (-100% against the exact LP at every period count).

The actual cause is that clustering destroys intraday PRICE VOLATILITY, which
is precisely what storage arbitrage monetises. Representing 365 distinct daily
price shapes with 12-26 representatives roughly halves the mean intraday
spread (A$432/MWh -> A$202/MWh at 12 periods, -53%), so the arbitrage revenue
that justifies a battery largely disappears from the LP's view. Energy, the
annual mean and the load peak are all preserved exactly -- only volatility is
lost, which is why the usual aggregation checks pass while the storage
decision is destroyed.

Neither of tsam's representation options fixes this: `mean` loses 33% of the
spread and `medoid` (the hierarchical default this module uses) loses 53%.
More periods help only weakly (-41% at 24 days).

Consequence: do not use `sizing_method="tsam"` when storage is economically
relevant. `ppa.scenario.validate_scenario` warns on that combination, and
`ppa.sizing.validate_sizing_representation` measures the error for a given
scenario against the exact LP.
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

    BESS caveat: clustering halves the intraday price spread, so the sizing LP
    sizes storage to zero regardless of `hours_per_period` — see the module
    docstring. Typical weeks do NOT fix it.
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

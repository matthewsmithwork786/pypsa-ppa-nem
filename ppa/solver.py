from __future__ import annotations

import logging

import pandas as pd
import xarray as xr
import pypsa

logger = logging.getLogger(__name__)

pypsa.options.general.allow_network_requests = False
pypsa.options.params.statistics.drop_zero = True
pypsa.options.params.statistics.round = 2
pypsa.options.params.optimize.log_to_console = False
pypsa.options.params.optimize.include_objective_constant = False
pypsa.options.api.new_components_api = True

from ppa.scenario import Scenario


def _period_groups(index: pd.DatetimeIndex, granularity: str) -> list[tuple[str, pd.Index]]:
    """(suffix, snapshots) pairs for 'annual' | 'monthly' | 'daily'.

    'monthly' groups by calendar month, 'daily' by calendar date. Both read the
    real calendar off `index`, which is correct for the dispatch LP and for the
    full_hourly sizing LP. It is NOT correct for tsam-clustered snapshots — see
    the tsam handling in `solve` (daily groups come from `period_labels`, and a
    monthly SLA is refused outright).
    """
    index = pd.DatetimeIndex(index)
    if granularity == "annual":
        return [("", index)]
    if granularity == "monthly":
        keys = index.strftime("%Y-%m")
    elif granularity == "daily":
        keys = index.strftime("%Y-%m-%d")
    else:  # pragma: no cover - internal misuse
        raise ValueError(f"granularity must be 'annual', 'monthly' or 'daily', got {granularity!r}")
    groups = []
    for k in pd.unique(keys):
        groups.append((str(k), index[keys == k]))
    return groups


def _delivery_share_for(kind: str, s: Scenario) -> float:
    """SLA share that the minimum-delivery constraint must reach for `kind`.

    Annual uses `required_delivery_share` (the existing contract share); the
    monthly/daily tiers use their own configurable shares. The shortfall cap's
    right-hand side is `1 - share` for the same `kind`, so the two forms are
    mirror images of each other.
    """
    if kind == "monthly":
        return s.sla_monthly_share
    if kind == "daily":
        return s.sla_daily_share
    return s.required_delivery_share


def _label_groups(labels: pd.Series) -> list[tuple[str, pd.Index]]:
    """Snapshot groups keyed by a tsam period-block label.

    `labels` maps every snapshot to the integer `(cluster_id, day_within_period)`
    block it belongs to (built by `ppa.sizing_tsam.cluster_typical_periods`, the
    only code that knows the true period boundaries). The calendar is meaningless
    on the synthetic clustered index, so daily SLA groups come from here instead.
    """
    if labels.isna().any():
        raise ValueError(
            "period_labels must cover every snapshot exactly once; got NaN for "
            f"{int(labels.isna().sum())} snapshot(s)."
        )
    groups = []
    for k in sorted(labels.unique()):
        groups.append((f"_D{k}", labels[labels == k].index))
    return groups


def solve(
    n: pypsa.Network,
    scenario: Scenario,
    ts: pd.DataFrame,
    solver_name: str = "highs",
    solver_options: dict | None = None,
    period_labels: pd.Series | None = None,
) -> tuple[str, str]:
    """Add custom Linopy constraints and solve the network. Returns (status, condition).

    `solver_options` is passed through to `solve_model` (e.g. `{"solver": "ipm"}`
    or `{"solver": "hipo"}`). Default `{}` keeps the stock dual-simplex HiGHS.

    `period_labels` is an optional per-snapshot block label (one integer per
    snapshot identifying its `(cluster_id, day_within_period)`), produced by
    `ppa.sizing_tsam.cluster_typical_periods`. When given, daily SLA groups are
    built from these labels instead of the calendar — the clustered index is a
    synthetic date range whose dates correspond to nothing real.
    """
    s = scenario

    # Two-step workflow: create_model() → inject constraints → solve_model()
    m = n.optimize.create_model(
        include_objective_constant=True,
    )

    gen_p = m.variables["Generator-p"]
    link_p = m.variables["Link-p"]

    load = n.loads.dynamic.p_set["Load_PPAOfftake"]

    # In a multi-year sizing LP the caps must bind per calendar year — one
    # aggregate constraint over 25 years would let the optimiser concentrate all
    # shortfall/buys into the worst weather years. Single-year runs keep the
    # original single aggregate constraint (identical behaviour).
    years = pd.Index(ts.index.year)
    if s.optimise_capacity and years.nunique() > 1:
        annual_groups = [(f"_{y}", ts.index[years == y]) for y in years.unique()]
    else:
        annual_groups = _period_groups(ts.index, "annual")

    # WP8 tiered SLA: the annual shortfall/delivery constraint can be tightened
    # per calendar month and/or per calendar day. Each group gets its own
    # shortfall cap and (when enabled) its own minimum-delivery floor.
    #
    # Semantics (AGENTS.md §3): the shortfall cap is "the offtaker's free
    # allowance per period is limited"; the min-delivery form is "the portfolio
    # must actually deliver that share". They are NOT equivalent, because
    # Gen_Penalty sits on Bus_PPAOfftake and bypasses the offtake link, so a
    # shortfall cap alone can be satisfied with penalty energy while
    # fulfilled_share (which counts only the offtake link flow) stays low.
    #
    # The tuple is (suffix, snapshots, shortfall RHS share, kind). The suffix
    # becomes part of the constraint name, so it must be unique AND greppable:
    #   AllowedShortfall_Limit_M{YYYY}-{MM} / AllowedShortfall_Limit_D{YYYY}-{MM}-{DD}
    # and the same pattern under MinDelivery_Limit_.
    if s.sla_monthly_enabled and period_labels is not None:
        raise ValueError(
            "A monthly SLA cannot be enforced on tsam-clustered snapshots: the "
            "synthetic clustered index has no calendar months, so grouping it by "
            ".month would build a constraint over hours that share nothing but a "
            "label. Use the full_hourly sizing path, a daily/annual SLA, or turn "
            "the monthly SLA off. (validate_scenario should have caught this "
            "earlier — this is a belt-and-braces guard.)"
        )

    groups: list[tuple[str, pd.Index, float, str]] = []  # (suffix, snaps, shortfall_share, kind)
    groups += [
        (sfx, snaps, s.allowed_shortfall_share, "annual")
        for sfx, snaps in annual_groups
    ]
    if s.sla_monthly_enabled:
        groups += [
            (f"_M{k}", snaps, 1.0 - s.sla_monthly_share, "monthly")
            for k, snaps in _period_groups(ts.index, "monthly")
        ]
    if s.sla_daily_enabled:
        if period_labels is not None:
            # Approximate: a representative week sliced into 24 h blocks stands
            # for `occ` real days each, so "on a representative day of this
            # type, the daily SLA holds" — not literally every calendar day.
            daily_groups = _label_groups(period_labels.reindex(n.snapshots))
        else:
            daily_groups = [
                (f"_D{k}", snaps) for k, snaps in _period_groups(ts.index, "daily")
            ]
        groups += [
            (sfx, snaps, 1.0 - s.sla_daily_share, "daily")
            for sfx, snaps in daily_groups
        ]

    # Energy constraints must integrate over REAL hours, not snapshot counts.
    # With uniform weightings (full-hourly = 1 h, coarse = resolution_h) an
    # unweighted sum is proportional on both sides and the ratios come out
    # right, so this was invisible until tsam. Typical-period clustering gives
    # each representative hour a different weight (occurrence counts ranged
    # 5-55 h on a 12-period year), and an unweighted sum then constrains the
    # wrong quantity entirely — a "hard 90% delivery" constraint was landing at
    # 85.6% of actual load. Weight every energy term explicitly.
    weights = n.snapshot_weightings["objective"]

    def _w(snaps):
        """Snapshot weightings as an xarray coefficient aligned to `snaps`."""
        return xr.DataArray(
            weights.loc[snaps].to_numpy(dtype=float),
            dims=["snapshot"],
            coords={"snapshot": snaps},
        )

    n_sla_constraints = 0
    for suffix, snaps, shortfall_share, kind in groups:
        w = _w(snaps)
        # Constraint 1 — allowed shortfall cap (aggregate over period)
        period_load_mwh = float((load.loc[snaps] * weights.loc[snaps]).sum())
        # Defensive: a clustered or partial period with no load makes the
        # constraint `0 ≤ 0`, harmless but noisy — skip it.
        if period_load_mwh <= 0:
            continue
        allowed_shortfall_expr = (gen_p.loc[snaps, "Gen_AllowedShortfall"] * w).sum()
        m.add_constraints(
            allowed_shortfall_expr <= shortfall_share * period_load_mwh,
            name=f"AllowedShortfall_Limit{suffix}",
        )
        n_sla_constraints += 1

        # Constraint 3 — hard minimum PPA delivery (sizing only, opt-in).
        #
        # Without this the delivery requirement is only a *price* signal: the LP
        # compares the penalty (ppa_price x pen_mult) against the cost of
        # building, and where the penalty is cheaper it rationally buys its way
        # out of the SLA. On the Corporate PPA defaults the penalty is
        # A$126/MWh against a wind LCOE of A$162, so delivery settles around
        # 50-65% however much merchant value is credited (see
        # docs/sizing_experiments.md E1).
        #
        # Enabling this makes the contractual share a genuine constraint, so the
        # LP must build (or buy, within market_buy_share) enough to meet it. It
        # can render the problem infeasible when the build caps bind — that is
        # informative, not a failure, and `solve` reports it as such.
        #
        # Matches ppa.results.fulfilled_share exactly: delivered MWh is the flow
        # on IPPGen_to_PPAOfftake (efficiency 1.0), over total load.
        if s.optimise_capacity and s.enforce_min_delivery:
            delivered_expr = (link_p.loc[snaps, "IPPGen_to_PPAOfftake"] * w).sum()
            m.add_constraints(
                delivered_expr >= _delivery_share_for(kind, s) * period_load_mwh,
                name=f"MinDelivery_Limit{suffix}",
            )
            n_sla_constraints += 1

        # Constraint 2 — market buy cap relative to PPA delivery (only when enabled)
        if s.enable_market_buy and s.market_buy_share > 0:
            buy_expr = (gen_p.loc[snaps, "Gen_BuyFromMarket"] * w).sum()
            delivery_expr = (link_p.loc[snaps, "IPPGen_to_PPAOfftake"] * w).sum()
            m.add_constraints(
                buy_expr <= s.market_buy_share * delivery_expr,
                name=f"BuyFromMarket_Limit{suffix}",
            )

    # A daily SLA is the most common way to multiply the constraint count
    # (25 years x 365 days = 9,125 daily groups over 2.6M 5-minute snapshots).
    # Log it at DEBUG so a surprising build time can be traced to the SLA tier
    # rather than blamed on the solver.
    logger.debug(
        "Added %d SLA constraint(s) over %d snapshot group(s) (kinds: %s)",
        n_sla_constraints,
        len(groups),
        ",".join(sorted({k for _, _, _, k in groups})),
    )
    n.meta["sla_constraint_count"] = n_sla_constraints

    # io_api="direct": hand the problem to HiGHS in memory instead of writing an
    # LP file and reading it back. Identical optimum, but ~265 MB less peak RSS
    # (~1000 → ~735 MB per solve) and faster — matters on the ~1 GB Streamlit
    # Cloud tier. assign_all_duals is left at its default (False): duals are never
    # consumed anywhere in the app, so materialising 300k+ of them is dead work.
    # Solver algorithm note (W15, measured with scripts/bench_solver.py):
    #   full-year hourly sizing LP (306,611 rows × 122,646 cols, 1-yr synthetic):
    #     dual simplex       15.9 s   ← fastest
    #     ipm                26.1 s   (24.6 s without crossover)
    #     hipo               26.3 s   (25.6 s without crossover)
    #   tsam typical-days sizing LP (11,771 rows × 4,710 cols, 12 periods):
    #     dual simplex        1.4 s   ← fastest
    #     ipm / hipo         ~1.5 s
    #   Both IPM variants lose, so no sizing-specific algorithm override is
    #   applied: HiPO (highspy-extras) is left optional, and dispatch solves
    #   (small, re-solved many times) stay on the default simplex too.
    status, condition = n.optimize.solve_model(
        solver_name=solver_name,
        io_api="direct",
        **(solver_options or {}),
    )
    return status, condition

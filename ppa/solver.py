from __future__ import annotations

import pandas as pd
import pypsa

pypsa.options.general.allow_network_requests = False
pypsa.options.params.statistics.drop_zero = True
pypsa.options.params.statistics.round = 2
pypsa.options.params.optimize.log_to_console = False
pypsa.options.params.optimize.include_objective_constant = False
pypsa.options.api.new_components_api = True

from ppa.scenario import Scenario


def solve(
    n: pypsa.Network,
    scenario: Scenario,
    ts: pd.DataFrame,
    solver_name: str = "highs",
    solver_options: dict | None = None,
) -> tuple[str, str]:
    """Add custom Linopy constraints and solve the network. Returns (status, condition).

    `solver_options` is passed through to `solve_model` (e.g. `{"solver": "ipm"}`
    or `{"solver": "hipo"}`). Default `{}` keeps the stock dual-simplex HiGHS.
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
    # aggregate constraint over 25 years would let the optimizer concentrate all
    # shortfall/buys into the worst weather years. Single-year runs keep the
    # original single aggregate constraint (identical behavior).
    years = pd.Index(ts.index.year)
    if s.optimize_capacity and years.nunique() > 1:
        snapshot_groups = [(f"_{y}", ts.index[years == y]) for y in years.unique()]
    else:
        snapshot_groups = [("", ts.index)]

    for suffix, snaps in snapshot_groups:
        # Constraint 1 — allowed shortfall cap (aggregate over period)
        period_load_mwh = float(load.loc[snaps].sum())
        allowed_shortfall_expr = gen_p.loc[snaps, "Gen_AllowedShortfall"].sum()
        m.add_constraints(
            allowed_shortfall_expr <= s.allowed_shortfall_share * period_load_mwh,
            name=f"AllowedShortfall_Limit{suffix}",
        )

        # Constraint 2 — market buy cap relative to PPA delivery (only when enabled)
        if s.enable_market_buy and s.market_buy_share > 0:
            buy_expr = gen_p.loc[snaps, "Gen_BuyFromMarket"].sum()
            delivery_expr = link_p.loc[snaps, "IPPGen_to_PPAOfftake"].sum()
            m.add_constraints(
                buy_expr <= s.market_buy_share * delivery_expr,
                name=f"BuyFromMarket_Limit{suffix}",
            )

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

from __future__ import annotations

import dataclasses

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ppa.financial_model import (
    EnergyInputs,
    ProjectFinanceInputs,
    energy_inputs_from_results,
    per_year_energy_inputs,
    project_finance_inputs_from_scenario,
    run_project_finance,
)
from ppa.sensitivity import (
    PARAMS,
    PARAM_BY_FIELD,
    run_tornado,
    run_what_if,
    tornado_to_dataframe,
)
from ui import state


# ── Base inputs ────────────────────────────────────────────────────────────────


def _get_base() -> tuple[EnergyInputs | None, ProjectFinanceInputs | None, list[EnergyInputs] | None]:
    """Derive base energy, finance inputs, and real per-year energy from session state.

    Prefers an already-run Financial Model result so the user's edited
    assumptions (and the per-year data it ran with) carry over; falls back to
    raw optimisation results. `annual_energy` (3rd element) is real per-year
    data for a multi-year run -- see run_project_finance's `annual_energy`
    param -- so sensitivity results perturb the same baseline the Financial
    Model tab shows rather than a flattened averaged year.
    """
    pf = state.get_project_finance() if state.has_project_finance() else None
    if pf is not None:
        return pf.energy, pf.inputs, pf.annual_energy

    energy: EnergyInputs | None = None
    annual_energy: list[EnergyInputs] | None = None
    results = []
    if state.has_multi_year_results():
        results = [r for r in state.get_multi_year_results() if r is not None]
        if results:
            energy = energy_inputs_from_results(results)
            if len(results) > 1:
                annual_energy = per_year_energy_inputs(results)

    if energy is None:
        return None, None, None

    scenario = results[0].scenario if results else None
    finance = project_finance_inputs_from_scenario(scenario) if scenario else ProjectFinanceInputs()
    return energy, finance, annual_energy


# ── Metric helpers ─────────────────────────────────────────────────────────────

METRIC_OPTIONS = {
    "project_irr": "Project IRR",
    "equity_irr": "Equity IRR",
    "npv_project": "NPV (A$m)",
    "gearing": "Gearing",
    "lcoe": "LCOE (A$/MWh)",
    "total_capex": "Total capex (A$m)",
    "total_debt": "Total debt (A$m)",
    "min_dscr": "Min DSCR",
}
PCT_METRICS = {"project_irr", "equity_irr", "gearing"}


def _fmt(v: float, metric: str) -> str:
    if metric in PCT_METRICS:
        return f"{v:.1%}" if v == v else "n/a"
    return f"{v:,.2f}" if v == v else "n/a"


def _scale(metric: str) -> float:
    return 100.0 if metric in PCT_METRICS else 1.0


def _unit(metric: str) -> str:
    return "%" if metric in PCT_METRICS else ""


# ── What-if panel ──────────────────────────────────────────────────────────────


def _num(label: str, key: str, default: float, *, step: float | None = None, fmt: str | None = None, pct: bool = False):
    """With ``pct=True`` the model value is a decimal fraction (e.g. 0.065) but is
    displayed and edited in percent (6.5); ``step``/``fmt`` are in percent terms."""
    scale = 100.0 if pct else 1.0
    if key not in st.session_state:
        st.session_state[key] = float(default) * scale
    kw: dict = {}
    if step is not None:
        kw["step"] = step
    if fmt is not None:
        kw["format"] = fmt
    val = st.number_input(label, key=key, **kw)
    return val / scale if pct else val


def _what_if_panel(
    base_energy: EnergyInputs,
    base_finance: ProjectFinanceInputs,
    annual_energy: list[EnergyInputs] | None = None,
) -> None:
    with st.expander("What-if analysis", expanded=False):
        st.caption(
            "Adjust any combination of financial parameters and see the result instantly. "
            "Parameters that require a PyPSA re-run (capacities, delivery share, BESS efficiency) "
            "are in the Optimisation tab."
        )

        pf = "wi_"
        groups: dict[str, list] = {}
        for p in PARAMS:
            groups.setdefault(p.group, []).append(p)

        # PARAMS is the single source (ppa/sensitivity.py) -- a field added there
        # appears here automatically, so the what-if form can no longer drift
        # from the tornado catalogue the way it had (three devex fields the
        # catalogue didn't carry).
        n_cols = 4
        cols = st.columns(n_cols)
        overrides: dict[str, float | int] = {}
        for i, (group, group_params) in enumerate(groups.items()):
            with cols[i % n_cols]:
                st.markdown(f"**{group}**")
                for p in group_params:
                    default = getattr(base_finance, p.field)
                    val = _num(
                        p.label, pf + p.field, default,
                        step=p.step, fmt=p.widget_fmt, pct=p.pct_display,
                    )
                    overrides[p.field] = int(val) if isinstance(default, int) else val

        wi_finance = dataclasses.replace(base_finance, **overrides)

    base_result = run_project_finance(base_finance, base_energy, annual_energy=annual_energy)
    wi_result   = run_project_finance(wi_finance,   base_energy, annual_energy=annual_energy)

    with st.expander("Base results", expanded=True):
        cols = st.columns(6)
        kpis = [
            ("Project IRR", "project_irr", True),
            ("Equity IRR",  "equity_irr",  True),
            ("Gearing",     "gearing",     True),
            ("NPV (A$m)",    "npv_project", False),
            ("Total capex (A$m)", "total_capex", False),
            ("Min DSCR",    "min_dscr",    False),
        ]
        for col, (label, attr, is_pct) in zip(cols, kpis):
            bv = getattr(base_result, attr)
            wv = getattr(wi_result,   attr)
            if is_pct:
                col.metric(label, f"{wv:.2%}") # , delta=f"{(wv - bv) * 100:+.2f} pp")
            else:
                col.metric(label, f"{wv:,.2f}") # , delta=f"{wv - bv:+,.2f}")


# ── Tornado chart ──────────────────────────────────────────────────────────────


def _tornado_panel(
    base_energy: EnergyInputs,
    base_finance: ProjectFinanceInputs,
    annual_energy: list[EnergyInputs] | None = None,
) -> None:
    tab_chart1, tab_chart2 = st.tabs([
        "| Tornado chart — one-at-a-time sensitivity", 
        "| Data table",
    ])
    with tab_chart1:
    # with st.expander("Tornado chart — one-at-a-time sensitivity", expanded=True):
        cols = st.columns([3, 1])
        with cols[1]:
            metric_key = st.selectbox(
                "Metric",
                options=list(METRIC_OPTIONS),
                format_func=lambda x: METRIC_OPTIONS[x],
                key="sa_t_metric",
            )
            top_n = st.number_input(
                "Show top N parameters",
                min_value=3, max_value=len(PARAMS), value=12, step=1,
                key="sa_t_topn",
            )

        with st.spinner("Computing sensitivity…"):
            rows, base_val, zero_rows = run_tornado(
                base_energy, base_finance, metric=metric_key, min_swing_fraction=0.01,
                annual_energy=annual_energy,
            )

        if zero_rows:
            names = ", ".join(r.param for r in zero_rows)
            st.caption(
                f"**{len(zero_rows)} parameter(s) hidden** (as of negligible effect on "
                f"{METRIC_OPTIONS[metric_key]} in this scenario): {names}."
            )

        rows = rows[: int(top_n)]
        scale = _scale(metric_key)
        unit = _unit(metric_key)
        base_scaled = base_val * scale

        # ── Tornado figure ──
        fig = go.Figure()

        for row in reversed(rows):
            lo = row.low_metric * scale
            hi = row.high_metric * scale

            # Which end is "down" vs "up"?
            col_down = "#EF6C00"   # orange  — parameter decrease → lower metric
            col_up   = "#1565C0"   # blue    — parameter increase → higher metric
            # If increasing the parameter increases the metric: hi > lo
            if hi >= lo:
                col_lo_bar, col_hi_bar = col_down, col_up
            else:
                col_lo_bar, col_hi_bar = col_up, col_down

            # Lower half bar (from base to left)
            fig.add_trace(go.Bar(
                name="Low",
                y=[row.param],
                x=[min(lo, hi) - base_scaled],
                base=base_scaled,
                orientation="h",
                marker_color=col_lo_bar,
                showlegend=False,
                hovertemplate=(
                    f"<b>{row.param}</b><br>"
                    f"Low ({row.low_val:.4g}): {lo:.2f}{unit}<br>"
                    f"Base ({row.base_val:.4g}): {base_scaled:.2f}{unit}<br>"
                    f"Delta: {(base_scaled-lo):.2f}{unit}<extra></extra>"
                ),
            ))
            # Upper half bar (from base to right)
            fig.add_trace(go.Bar(
                name="High",
                y=[row.param],
                x=[max(lo, hi) - base_scaled],
                base=base_scaled,
                orientation="h",
                marker_color=col_hi_bar,
                showlegend=False,
                hovertemplate=(
                    f"<b>{row.param}</b><br>"
                    f"High ({row.high_val:.4g}): {hi:.2f}{unit}<br>"
                    f"Base ({row.base_val:.4g}): {base_scaled:.2f}{unit}<br>"
                    f"Delta: {(base_scaled-hi):.2f}{unit}<extra></extra>"
                ),
            ))

        # Label above the plot area (yref="paper", y>1) so it never overlaps
        # the top bar.
        fig.add_vline(
            x=base_scaled,
            line_dash="dash",
            line_color="black",
            annotation=dict(
                text=f"Base {base_scaled:.2f}{unit}",
                yref="paper",
                y=1.0,
                yanchor="bottom",
                xanchor="center",
                showarrow=False,
            ),
        )

        metric_label = METRIC_OPTIONS[metric_key]
        if metric_key in PCT_METRICS:
            metric_label += " (%)"

        fig.update_layout(
            barmode="overlay",
            height=max(350, len(rows) * 32 + 80),
            margin=dict(t=50, b=50, l=10, r=40),
            xaxis_title=metric_label,
            yaxis=dict(automargin=True),
            plot_bgcolor="white",
            paper_bgcolor="white",
        )
        with cols[0]:
            st.plotly_chart(fig, width='stretch')

    with tab_chart2:
    # with st.expander("Data table", expanded=False):
        df = tornado_to_dataframe(rows, base_val, metric_key)
        st.dataframe(df.set_index("Parameter"), width='stretch', height="content")


# ── Tab entry point ────────────────────────────────────────────────────────────


def render() -> None:
    st.header("Sensitivity Analysis")
    st.caption(
        "Financial-parameter sensitivity — no PyPSA re-run required. "
        "For capacity or dispatch changes (wind/solar/BESS MW, delivery share, "
        "BESS round-trip efficiency) run a new optimisation in the Optimisation tab."
    )

    base_energy, base_finance, annual_energy = _get_base()
    if base_energy is None:
        st.info(
            "Run an optimisation first (Optimisation tab), then return here. "
            "For richer results, run the Financial Model tab first — "
            "its edited assumptions will be used as the base case."
        )
        return

    _what_if_panel(base_energy, base_finance, annual_energy)
    # st.markdown("---")
    _tornado_panel(base_energy, base_finance, annual_energy)

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ppa.financial_model import (
    ProjectFinanceInputs,
    EnergyInputs,
    run_project_finance,
    energy_inputs_from_results,
    per_year_energy_inputs,
    project_finance_inputs_from_scenario,
)
from ppa.financial_model_excel import export_financial_model, export_hourly_timeseries
from ui import state


# ── Energy interface ──────────────────────────────────────────────────────────


def _energy_source() -> tuple[EnergyInputs | None, list, bool]:
    """Energy inputs, the underlying per-year results, and a multi-year flag.

    The same result set drives both ``EnergyInputs`` (averaged) and the per-year
    hourly rows stacked on the combined Hourly sheet, so the workbook's rolled-up
    totals match the model exactly.
    The multi-year flag tells the model whether merchant prices are already
    escalated per year (so it should not escalate them again)."""
    if state.has_multi_year_results():
        results = [r for r in state.get_multi_year_results() if r is not None]
        if len(results) > 1:
            return energy_inputs_from_results(results), results, True
        if results:
            return energy_inputs_from_results(results), results, False
    return None, [], False


# ── Input widgets ──────────────────────────────────────────────────────────────


def _num(label: str, key: str, default, *, step=None, fmt=None, pct=False, help=None, label_visibility="visible"):
    """Number input that persists its own default into session state once.

    With ``pct=True`` the model value is a decimal fraction (e.g. 0.065) but is
    displayed and edited in percent (6.5), and the return value is converted back
    to a fraction. ``step`` and ``fmt`` are then given in percent terms."""
    scale = 100.0 if pct else 1.0
    if key not in st.session_state:
        st.session_state[key] = float(default) * scale if not isinstance(default, int) else default
    kwargs = {}
    if step is not None:
        kwargs["step"] = step
    if fmt is not None:
        kwargs["format"] = fmt
    val = st.number_input(label, key=key, help=help, label_visibility=label_visibility, **kwargs, )
    return val / scale if pct else val


def _collect_inputs(seed: ProjectFinanceInputs, multi_year: bool) -> ProjectFinanceInputs:
    """Render the editable assumption form and return a ProjectFinanceInputs."""
    f = "fm_"

    with st.expander("💰 Costs (build, connection, devex, O&M)", expanded=False):
        cols = st.columns(4, vertical_alignment="bottom")
        cols[1].markdown("**Onshore wind**")
        cols[2].markdown("**Solar PV**")
        cols[3].markdown("**BESS**")

        cols = st.columns(4, vertical_alignment="bottom")
        cols[0].markdown(r"**Investment** (A\$M/MW, A\$M/MWh):")

        with cols[1]:
            onsw_build = _num("**Onshore wind**", f + "onsw_build", seed.onsw_build_cost, step=0.05, fmt="%.3f", label_visibility="collapsed")

        with cols[2]:
            pv_build = _num("**Solar PV**", f + "pv_build", seed.pv_build_cost, step=0.05, fmt="%.3f", label_visibility="collapsed")

        with cols[3]:
            bess_build = _num("**BESS**", f + "bess_build", seed.bess_build_cost, step=0.05, fmt="%.3f", label_visibility="collapsed")

        cols = st.columns(4)
        cols[0].markdown(r"**Connection** (A\$M/MW, A\$M/MWh):")

        with cols[1]:
            onsw_conn = _num("Onshore wind ", f + "onsw_conn", seed.onsw_connection_cost, step=0.01, fmt="%.3f", label_visibility="collapsed")

        with cols[2]:
            pv_conn = _num("Solar PV ", f + "pv_conn", seed.pv_connection_cost, step=0.01, fmt="%.3f", label_visibility="collapsed")

        with cols[3]:
            bess_conn = _num("BESS ", f + "bess_conn", seed.bess_connection_cost, step=0.01, fmt="%.3f", label_visibility="collapsed")

        cols = st.columns(4)
        cols[0].markdown(r"**Devex** (A\$M/MW, A\$M/MWh):")

        with cols[1]:
            onsw_devex = _num("Onshore wind  ", f + "onsw_devex", seed.onsw_devex, step=0.01, fmt="%.3f", label_visibility="collapsed")

        with cols[2]:
            pv_devex = _num("Solar PV  ", f + "pv_devex", seed.pv_devex, step=0.01, fmt="%.3f", label_visibility="collapsed")

        with cols[3]:
            bess_devex = _num("BESS  ", f + "bess_devex", seed.bess_devex, step=0.01, fmt="%.3f", label_visibility="collapsed")

        cols = st.columns(4)
        cols[0].markdown(r"**Fixed O&M** (A\$M/MW, A\$M/MWh p.a.)")

        with cols[1]:
            onsw_om = _num("Onshore wind   ", f + "onsw_om", seed.onsw_fixed_om, step=0.005, fmt="%.3f", label_visibility="collapsed")

        with cols[2]:
            pv_om = _num("Solar PV   ", f + "pv_om", seed.pv_fixed_om, step=0.005, fmt="%.3f", label_visibility="collapsed")

        with cols[3]:
            bess_om = _num("BESS   ", f + "bess_om", seed.bess_fixed_om, step=0.005, fmt="%.3f", label_visibility="collapsed")

        cols = st.columns(4)
        cols[0].markdown("**Ancillary** (% of revenue)")

        with cols[1]:
            anc = _num("Ancillary (% of revenue)", f + "anc", seed.ancillary_pct, step=0.1, fmt="%.2f", pct=True, label_visibility="collapsed")

    with st.expander("📅 Timing (construction, operating life)", expanded=False):
        st.caption("Devex is paid as a single bullet at FID — the first construction period.")
        cols = st.columns(4, vertical_alignment="bottom")
        with cols[0]:
            st.markdown("**Overall Settings (yrs)**")
        with cols[1]:
            dev_start = int(_num("FID / financial close period", f + "dev_start", seed.development_start, step=1))
        with cols[2]:
            duration = int(_num("Model duration (yrs)", f + "duration", seed.model_duration, step=1))
        with cols[3]:
            life = int(_num("Operating life (yrs)", f + "life", seed.operating_life, step=1))

        cols = st.columns(4, vertical_alignment="bottom")
        cols[1].markdown("**Wind**")
        cols[2].markdown("**Solar PV**")
        cols[3].markdown("**BESS**")

        cols = st.columns(4, vertical_alignment="bottom")
        with cols[0]:
            st.markdown("**Construction (yrs)**")
        with cols[1]:
            onsw_con = int(_num("Onshore wind ", f + "onsw_con", seed.onsw_constr_years, step=1, label_visibility="collapsed"))
        with cols[2]:
            pv_con = int(_num("Solar PV ", f + "pv_con", seed.pv_constr_years, step=1, label_visibility="collapsed"))
        with cols[3]:
            bess_con = int(_num("BESS ", f + "bess_con", seed.bess_constr_years, step=1, label_visibility="collapsed"))

    with st.expander("💰 Revenue & indexation", expanded=False):
        cols = st.columns(4)
        with cols[0]:
            tenor = int(_num("PPA contract tenor (yrs)", f + "tenor", seed.ppa_tenor, step=1))
            tariff = _num("PPA tariff (A$/MWh)", f + "tariff", seed.ppa_tariff, step=1.0)
        with cols[1]:
            pen = _num("Penalty multiple (×)", f + "pen", seed.penalty_multiple, step=0.1, fmt="%.2f")
            lgc = _num("LGC / GO price (A$/MWh)", f + "lgc", seed.lgc_price, step=1.0)
        with cols[2]:
            cost_infl = _num("Cost inflation (%/yr)", f + "cost_infl", seed.cost_inflation, step=0.1, fmt="%.2f", pct=True)
        with cols[3]:
            ppa_idx = _num("PPA & LGC indexation (%/yr)", f + "ppa_idx", seed.ppa_indexation, step=0.1, fmt="%.2f", pct=True)
            solar_infl = _num("Solar-hour price infl. (%/yr)", f + "solar_infl", seed.solar_price_inflation, step=0.1, fmt="%.2f", pct=True)
            nonsolar_infl = _num("Non-solar price infl. (%/yr)", f + "nonsolar_infl", seed.nonsolar_price_inflation, step=0.1, fmt="%.2f", pct=True)
        esc_key = f + "esc_merch"
        if esc_key not in st.session_state:
            st.session_state[esc_key] = not multi_year
        escalate_merchant = st.checkbox(
            "Escalate merchant prices over the project life",
            key=esc_key,
            help=(
                "Leave OFF when the energy inputs come from a multi-year optimisation that "
                "already escalates market prices each year (avoids double-counting price "
                "growth). Turn ON for a single base-year snapshot. The solar-hour / non-solar "
                "price inflation rates above only apply when this is ON."
            ),
        )
        if multi_year and escalate_merchant:
            st.caption(
                "⚠️ Merchant prices are already escalated by the multi-year energy run — "
                "leaving this on double-counts price growth."
            )

    with st.expander("🏦 Debt, depreciation & tax", expanded=True):
        cols = st.columns(4)
        with cols[0]:
            st.markdown("**Debt**")
            debt_tenor = int(_num("Repayment tenor (yrs)", f + "debt_tenor", seed.debt_tenor, step=1))
            debt_rate = _num("Debt rate (%)", f + "debt_rate", seed.debt_rate, step=0.1, fmt="%.2f", pct=True)
            wacc = _num("Discount rate / WACC (%)", f + "wacc", seed.discount_rate, step=0.1, fmt="%.2f", pct=True)
        with cols[1]:
            st.markdown("**DSCR**")
            dscr_c = _num("DSCR — contracted", f + "dscr_c", seed.dscr_contracted, step=0.05, fmt="%.2f")
            dscr_u = _num("DSCR — uncontracted", f + "dscr_u", seed.dscr_uncontracted, step=0.05, fmt="%.2f")
        with cols[2]:
            st.markdown("**Gearing**")
            gear_c = _num("Max gearing — contracted (%)", f + "gear_c", seed.max_gearing_contracted, step=1.0, fmt="%.1f", pct=True)
            gear_u = _num("Max gearing — uncontracted (%)", f + "gear_u", seed.max_gearing_uncontracted, step=1.0, fmt="%.1f", pct=True)
        with cols[3]:
            st.markdown("**Depreciation & tax**")
            book_dep = _num("Book depreciation (%/yr)", f + "book_dep", seed.book_depreciation_rate, step=0.1, fmt="%.2f", pct=True)
            tax_dep = _num("Tax depreciation (%/yr)", f + "tax_dep", seed.tax_depreciation_rate, step=0.1, fmt="%.2f", pct=True)
            tax_rate = _num("Corporate tax rate (%)", f + "tax_rate", seed.corp_tax_rate, step=1.0, fmt="%.1f", pct=True)

    return ProjectFinanceInputs(
        onsw_build_cost=onsw_build, pv_build_cost=pv_build, bess_build_cost=bess_build,
        onsw_connection_cost=onsw_conn, pv_connection_cost=pv_conn, bess_connection_cost=bess_conn,
        onsw_devex=onsw_devex, pv_devex=pv_devex, bess_devex=bess_devex,
        onsw_fixed_om=onsw_om, pv_fixed_om=pv_om, bess_fixed_om=bess_om, ancillary_pct=anc,
        model_duration=duration, development_start=dev_start,
        onsw_constr_years=onsw_con, pv_constr_years=pv_con, bess_constr_years=bess_con,
        operating_life=life,
        ppa_tenor=tenor, ppa_tariff=tariff, penalty_multiple=pen, lgc_price=lgc,
        cost_inflation=cost_infl, ppa_indexation=ppa_idx,
        solar_price_inflation=solar_infl, nonsolar_price_inflation=nonsolar_infl,
        escalate_merchant_prices=escalate_merchant,
        debt_tenor=debt_tenor, debt_rate=debt_rate,
        dscr_contracted=dscr_c, dscr_uncontracted=dscr_u,
        max_gearing_contracted=gear_c, max_gearing_uncontracted=gear_u,
        book_depreciation_rate=book_dep, tax_depreciation_rate=tax_dep, corp_tax_rate=tax_rate,
        discount_rate=wacc,
    )


# ── Results display ────────────────────────────────────────────────────────────


def _render_results(r) -> None:
    with st.expander("**Key results**", expanded=True):
        cols = st.columns(4)
        irr = lambda v: f"{v:.1%}" if v == v else "n/a"
        cols[0].metric("Project IRR", irr(r.project_irr), help="Unlevered FCFF return")
        cols[1].metric("Equity IRR", irr(r.equity_irr), help="Levered FCFE return")
        cols[2].metric("Gearing", f"{r.gearing:.1%}")
        cols[3].metric("NPV @ WACC", f"A${r.npv_project:,.0f}m")

        cols = st.columns(4)
        cols[0].metric("Total funding (incl. IDC)", f"A${r.total_capex:,.0f}m")
        cols[1].metric("Debt / Equity", rf"A\${r.total_debt:,.0f}m / A\${r.total_equity:,.0f}m")
        cols[2].metric("Min / Avg DSCR", f"{r.min_dscr:.2f} / {r.avg_dscr:.2f}")
        pb = f"{r.payback_years:.1f} yrs" if r.payback_years < 1e8 else "n/a"
        cols[3].metric("Equity payback / LCOE", f"{pb} · A${r.lcoe:,.0f}/MWh")

    sc = r.schedule
    periods = r.periods
    ops = sc["ops_flag"].astype(bool)

    # st.markdown("---")
    cols = st.columns(2)

    with st.expander("**Annual results**", expanded=True):
        tab_chart1, tab_chart2, tab_chart3, tab_chart4 = st.tabs([
            "| Cumulative equity cash flow (FCFE)", 
            "| Revenue: contracted vs uncontracted", 
            "| Debt service & DSCR",
            "| Annual schedule table",
        ])
        with tab_chart1:
            # Cumulative equity cash flow
            st.markdown("**Cumulative equity cash flow (FCFE)**")
            cum = np.cumsum(sc["fcfe"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=periods, y=cum, mode="lines", name="Cumulative FCFE",
                                    line=dict(color="#2E7D32", width=2), fill="tozeroy",
                                    fillcolor="rgba(46,125,50,0.08)"))
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            fig.update_layout(height=400, margin=dict(t=10, b=30), xaxis_title="Period",
                            yaxis_title="A$m")
            st.plotly_chart(fig, width="stretch")

        with tab_chart2:
            # Revenue split
            st.markdown("**Revenue: contracted vs uncontracted**")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=periods[ops], y=sc["net_contracted_rev"][ops],
                                name="Contracted", marker_color="#1565C0"))
            fig.add_trace(go.Bar(x=periods[ops], y=sc["net_uncontracted_rev"][ops],
                                name="Uncontracted (merchant + LGC)", marker_color="#90CAF9"))
            fig.update_layout(barmode="stack", height=400, margin=dict(t=10, b=30),
                            xaxis_title="Period", yaxis_title="A$m",
                            legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, width="stretch")

        with tab_chart3:
            # Debt balance & DSCR
            st.markdown("**Debt service & DSCR**")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=periods[ops], y=sc["interest"][ops], name="Interest", marker_color="#EF6C00"))
            fig.add_trace(go.Bar(x=periods[ops], y=sc["loan_repay"][ops], name="Principal", marker_color="#FFB74D"))
            dscr = sc["dscr"]
            fig.add_trace(go.Scatter(x=periods[ops], y=dscr[ops], name="DSCR", yaxis="y2",
                                    mode="lines+markers", line=dict(color="#1B5E20", width=2)))
            fig.update_layout(barmode="stack", height=400, margin=dict(t=10, b=30),
                            xaxis_title="Period", yaxis_title="A$m",
                            yaxis2=dict(title="DSCR", overlaying="y", side="right", showgrid=False),
                            legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, width="stretch")

        with tab_chart4:
            # Annual schedule table
            df = pd.DataFrame({
                "Period": periods.astype(int),
                "Net contracted rev": sc["net_contracted_rev"],
                "Net uncontracted rev": sc["net_uncontracted_rev"],
                "Opex": -sc["opex"],
                "EBITDA": sc["ebitda"],
                "Interest": -sc["interest"],
                "Loan repay": -sc["loan_repay"],
                "Book dep": -sc["book_dep"],
                "Tax": -sc["tax"],
                "PAT": sc["pat"],
                "FCFF": sc["fcff"],
                "FCFE": sc["fcfe"],
                "DSCR": sc["dscr"],
            })
            df = df[(df["Period"] >= 1)].round(2)
            st.dataframe(df.set_index("Period"), width="stretch", height="content")


# ── Tab entry point ────────────────────────────────────────────────────────────


def render() -> None:
    st.title("🏦 Financial Model")
    st.caption(
        "A streamlined project-finance appraisal layered on the energy-model results: "
        "indexed PPA + merchant revenue, DSCR-sculpted debt, depreciation, tax → "
        "Project & Equity IRR. Run it here, or export a live Excel workbook."
    )

    energy, results_list, multi_year = _energy_source()
    if energy is None:
        st.info(
            "No energy results yet. Run an optimisation in the **Optimisation** tab first — "
            "its generation, PPA delivery and merchant volumes feed this model.",
            icon="⚙️",
        )
        return

    # Real per-year data (degradation, weather-year cycling, actual escalation)
    # rather than replaying the single averaged `energy` for every operating
    # year -- see ppa.financial_model.run_project_finance's `annual_energy`.
    annual_energy = per_year_energy_inputs(results_list) if multi_year else None

    scenario = state.get_scenario()
    seed = (
        project_finance_inputs_from_scenario(scenario)
        if scenario is not None else ProjectFinanceInputs()
    )

    # ── Energy interface (pre-filled, from PyPSA) ─────────────────────────────
    with st.expander("⚡ Energy inputs from PyPSA (pre-filled)", expanded=False):
        if annual_energy:
            st.caption(
                f"Averages shown below; the model itself uses each of the "
                f"**{len(annual_energy)}** simulated years' real figures — "
                f"source: **{energy.name}**."
            )
        else:
            st.caption(f"Representative operating year derived from: **{energy.name}**")
        cols = st.columns(4)
        with cols[0]:
            st.metric("PPA delivered",
                      f"{energy.ppa_gwh:,.0f} GWh")
            st.metric("Penalty volume",
                      f"{energy.penalty_gwh:,.0f} GWh")

        with cols[1]:
            st.metric("Total gen (solar / non-solar)",
                      f"{energy.total_solar_gwh:,.0f} / {energy.total_nonsolar_gwh:,.0f} GWh")
            st.metric("Capacity (Wind)",
                      f"{energy.onsw_mw:,.0f} MW")

        with cols[2]:
            st.metric("Excess sold (solar / non-solar)",
                      f"{energy.excess_solar_gwh:,.0f} / {energy.excess_nonsolar_gwh:,.0f} GWh")
            st.metric("Capacity (PV)",
                      f"{energy.pv_mw:,.0f} MW")

        with cols[3]:
            st.metric("Merchant capture (solar / non-solar)",
                      rf"A\${energy.sell_solar_price:,.0f} / A\${energy.sell_nonsolar_price:,.0f}")
            st.metric("Capacity (BESS)",
                      f"{energy.bess_mw:,.0f} MW / {energy.bess_mwh:,.0f} MWh")

    # ── Editable financial assumptions ────────────────────────────────────────
    st.subheader("Financial assumptions")
    inputs = _collect_inputs(seed, multi_year)

    # ── Run ───────────────────────────────────────────────────────────────────
    run = st.button("▶️ Run financial model", type="primary", width="stretch")
    if run:
        try:
            result = run_project_finance(inputs, energy, annual_energy=annual_energy)
            state.set_project_finance(result)
        except Exception as exc:  # surface modelling errors rather than crash the tab
            st.error(f"Financial model failed: {exc}")
            return

    result = state.get_project_finance()
    if result is None:
        st.info("Set your assumptions above and click **Run financial model**.", icon="▶️")
        return

    # st.markdown("---")
    _render_results(result)

    # ── Export ────────────────────────────────────────────────────────────────
    # st.markdown("---")
    with st.expander("⚡ Export financial model as Excel(R) file", expanded=False):
        # st.subheader("Export")
        n_years = len(results_list)
        _stem = (result.energy.name or "scenario").replace(" ", "_")
        _mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        st.caption(
            "Two separate downloads. The **financial model** is the live workbook — "
            "revenue→tax→cash-flow chain and IRRs as formulas — with the annual energy "
            "figures as hard values on its Energy tab. The **hourly timeseries** is the "
            f"raw dispatch for all {n_years} simulated years, stacked under a Year column. "
            f"The hours are a separate file because {n_years} × 8 760 rows is slow to "
            "build and heavy to move, and the finance workbook does not need them."
        )

        _cols = st.columns(2)
        with _cols[0]:
            try:
                xlsx = export_financial_model(result.inputs, result.energy, result)
                st.download_button(
                    "⬇️ Download financial model",
                    data=xlsx,
                    file_name=f"financial_model_{_stem}.xlsx",
                    mime=_mime,
                    width="stretch",
                    type="primary",
                )
            except Exception as exc:
                st.error(f"Excel export failed: {exc}")

        with _cols[1]:
            # Built only on demand: this is the expensive one.
            if st.session_state.get("fm_hourly_xlsx_for") != _stem:
                if st.button(
                    f"🕒 Prepare hourly timeseries ({n_years} yr)",
                    width="stretch",
                    help="Builds the raw hourly dispatch workbook — takes a moment for "
                         "a long horizon.",
                ):
                    with st.spinner(f"Building {n_years} × 8 760 hourly rows..."):
                        try:
                            st.session_state["fm_hourly_xlsx"] = export_hourly_timeseries(results_list)
                            st.session_state["fm_hourly_xlsx_for"] = _stem
                        except Exception as exc:
                            st.error(f"Hourly export failed: {exc}")
                    st.rerun()
            else:
                st.download_button(
                    "⬇️ Download hourly timeseries",
                    data=st.session_state["fm_hourly_xlsx"],
                    file_name=f"hourly_timeseries_{_stem}.xlsx",
                    mime=_mime,
                    width="stretch",
                )

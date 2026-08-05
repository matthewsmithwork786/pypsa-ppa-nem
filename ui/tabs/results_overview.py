from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import state
from ui.charts import year_axis


def _render_multi_year_overview(fin) -> None:
    # Effective scenario: after a sizing run this carries the optimised
    # capacities the results were actually produced with, not the slider values.
    s = state.get_effective_scenario()

    # ── Lifetime KPIs ─────────────────────────────────────────────────────────
    st.subheader("Lifetime KPIs")
    st.info(
        "Based on a simplified unlevered model (CAPEX, OPEX, NPV/IRR). For a full "
        "levered project-finance appraisal incl. e.g., debt sizing, depreciation, "
        "tax, Equity IRR, and an Excel export see the **Financial Model** tab."
    )
    cols = st.columns(5)
    irr_str = f"{fin.irr:.1%}" if fin.irr == fin.irr else "N/A"
    lcoe_str = f"A${fin.lcoe:.1f}/MWh" if fin.lcoe == fin.lcoe else "N/A"
    payback_str = f"{fin.simple_payback:.1f} yrs" if fin.simple_payback < 1e8 else "N/A"
    cols[0].metric("NPV", f"A${fin.npv / 1e6:.1f}M")
    cols[1].metric("Project IRR", irr_str)
    cols[2].metric("LCOE", lcoe_str)
    cols[3].metric("Simple Payback", payback_str)
    cols[4].metric("Lifetime Net Revenue", f"A${fin.total_lifetime_revenue / 1e6:.1f}M")

    # ── CAPEX breakdown ───────────────────────────────────────────────────────
    # st.markdown("---")
    st.subheader("CAPEX & OPEX")
    cols = st.columns(2)
    with cols[0]:
        capex_rows = [
            ("Onshore wind", f"A${fin.capex.capex_wind / 1e6:.1f}M"),
            ("Solar PV", f"A${fin.capex.capex_pv / 1e6:.1f}M"),
            ("BESS", f"A${fin.capex.capex_bess / 1e6:.1f}M"),
            ("Devex", f"A${fin.capex.devex_total / 1e6:.1f}M"),
            ("Total CAPEX", f"A${fin.capex.capex_total / 1e6:.1f}M"),
            ("Total investment", f"A${fin.capex.total_investment / 1e6:.1f}M"),
            ("Annual OPEX", f"A${fin.annual_opex / 1e6:.2f}M/yr"),
        ]
        st.dataframe(
            pd.DataFrame(capex_rows, columns=["Item", "Value"]),
            hide_index=True,
            width="stretch",
        )
    with cols[1]:
        avg_delivery = sum(y.fulfilled_share for y in fin.yearly) / len(fin.yearly) if fin.yearly else 0.0
        total_gen_gwh = fin.total_lifetime_generation_mwh / 1e3
        avg_wind_gwh = sum(y.wind_gen_mwh for y in fin.yearly) / len(fin.yearly) / 1e3 if fin.yearly else 0.0
        avg_pv_gwh = sum(y.pv_gen_mwh for y in fin.yearly) / len(fin.yearly) / 1e3 if fin.yearly else 0.0
        gen_rows = [
            ("Avg annual PPA delivery rate", f"{avg_delivery:.1%}"),
            ("Total lifetime generation", f"{total_gen_gwh:.0f} GWh"),
            ("Avg annual wind generation", f"{avg_wind_gwh:.1f} GWh"),
            ("Avg annual solar generation", f"{avg_pv_gwh:.1f} GWh"),
        ]
        st.dataframe(
            pd.DataFrame(gen_rows, columns=["Metric", "Value"]),
            hide_index=True,
            width="stretch",
        )

    # ── Cumulative NPV chart ──────────────────────────────────────────────────
    # st.markdown("---")
    st.subheader("Cumulative NPV")
    years = [y.year for y in fin.yearly]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=[v / 1e6 for v in fin.cumulative_npv],
        mode="lines+markers", name="Cumulative NPV",
        line=dict(color="#2196F3", width=2),
        fill="tozeroy",
        fillcolor="rgba(33,150,243,0.08)",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        xaxis_title="Year", yaxis_title="NPV (A$M)", height=400,
        margin=dict(t=10, b=40),
        xaxis=year_axis(years),
    )
    st.plotly_chart(fig, width="stretch")

    # ── Year-by-year table ────────────────────────────────────────────────────
    # st.markdown("---")
    st.subheader("Year-by-year results")
    rows = [
        {
            "Year": y.year,
            "PPA Revenue (A$M)": round(y.ppa_revenue / 1e6, 2),
            "Merchant Revenue (A$M)": round(y.merch_revenue / 1e6, 2),
            "Market Buy Cost (A$M)": round(y.market_buy_cost / 1e6, 2),
            "Penalty Cost (A$M)": round(y.penalty_cost / 1e6, 2),
            "OPEX (A$M)": round(y.opex / 1e6, 2),
            "Net Cash Flow (A$M)": round(y.net_cashflow / 1e6, 2),
            "Delivery Rate (%)": round(y.fulfilled_share * 100, 1),
            "Wind Gen (GWh)": round(y.wind_gen_mwh / 1e3, 1),
            "PV Gen (GWh)": round(y.pv_gen_mwh / 1e3, 1),
        }
        for y in fin.yearly
    ]
    st.dataframe(pd.DataFrame(rows).set_index("Year"), width="stretch")
    st.caption("Detailed hourly dispatch analysis is available in **Results Deep Dive**.")


def render() -> None:
    st.title("📊 Results Overview")

    if state.has_multi_year_financial():
        n = len(state.get_multi_year_financial().yearly)
        mode = f"{n}-year optimisation" if n > 1 else "single-year optimisation"
        st.caption(f"Showing results from last run: **{mode}**.")
        _render_multi_year_overview(state.get_multi_year_financial())

    else:
        st.info(
            "No results yet. Do **Run optimisation** in the **Optimisation** tab "
            "to see lifetime financial results here.",
            icon="⚙️",
        )

"""Shared 'configuration used' summary for results tabs and Excel exports —
the portfolio MW (including PPA load and connection links) and, for NEM
scenarios, the actual plant names behind the run."""
from __future__ import annotations

import streamlit as st
from ppa.scenario import Scenario


def render_config_summary(s: Scenario, expanded: bool = False) -> None:
    """Render the fleet MW (incl. load & links) and plant names actually used."""
    with st.expander("⚙️ Configuration used", expanded=expanded):
        cols = st.columns(4)
        with cols[0]:
            st.markdown("**Portfolio (MW)**")
            rows = [
                ("Onshore wind", f"{s.onsw_mw:.0f} MW"),
                ("Solar PV", f"{s.pv_mw:.0f} MWac"),
                ("BESS", f"{s.effective_bess_mw:.0f} MW / {s.effective_bess_mwh:.0f} MWh"
                 if s.include_bess else "disabled"),
                ("PPA offtake load", f"{s.ppaload_mw:.0f} MW"),
            ]
            for label, val in rows:
                st.markdown(f"- {label}: **{val}**")
        with cols[1]:
            st.markdown("**Connection links (MW)**")
            st.markdown(f"- Wind link: **{s.wind_link_mw:.0f} MW**" if s.wind_link_mw else "- Wind link: *unconstrained*")
            st.markdown(f"- PV+BESS link: **{s.pvbess_link_mw:.0f} MW**" if s.pvbess_link_mw else "- PV+BESS link: *unconstrained*")
            st.markdown(f"- Export/sell link: **{s.sell_link_mw:.0f} MW**" if s.sell_link_mw else "- Export/sell link: *unconstrained*")
        with cols[2]:
            st.markdown("**Plant identity**")
            if s.is_nem:
                from ppa.data.nem_data import plant_name_for_duid

                if s.nem_wind_duid:
                    st.markdown(f"- Wind: **{plant_name_for_duid(s.nem_wind_duid)}** ({s.nem_wind_duid})")
                if s.nem_pv_duid:
                    st.markdown(f"- Solar: **{plant_name_for_duid(s.nem_pv_duid)}** ({s.nem_pv_duid})")
                st.markdown(f"- Price region: **{s.nem_price_region}**, year **{s.nem_year}**")
            else:
                st.markdown(f"- Data source: **{s.data_source}**")
        with cols[3]:
            st.markdown("**Simulation & SLA**")
            st.markdown(f"- Dispatch: **{s.dispatch_years} solved year(s)**")
            if s.dispatch_mode == "representative_year":
                st.markdown(f"- Financial projection: **{s.simulation_years} years** (fixed volumes)")
            years = ", ".join(str(y) for y in s.nem_years)
            if years:
                st.markdown(f"- NEM data years: **{years}**")
            st.markdown(f"- Resolution: **{s.nem_resolution_minutes} min**")
            if s.optimise_capacity:
                st.markdown(f"- Sizing year: **{s.capacity_sizing_year}**")
            st.markdown(f"- Annual delivery: **{s.required_delivery_share:.0%}**")
            if s.sla_monthly_enabled:
                st.markdown(f"- Monthly minimum: **{s.sla_monthly_share:.0%}**")
            if s.sla_daily_enabled:
                st.markdown(f"- Daily minimum: **{s.sla_daily_share:.0%}**")
            if not (s.sla_monthly_enabled or s.sla_daily_enabled):
                st.markdown("- Tiered SLA: *none*")

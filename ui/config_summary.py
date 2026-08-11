"""Shared 'configuration used' summary for results tabs and Excel exports —
the portfolio MW (including PPA load and connection links) and, for NEM
scenarios, the actual plant names behind the run."""
from __future__ import annotations

import streamlit as st


def render_config_summary(s, expanded: bool = False) -> None:
    """Render the fleet MW (incl. load & links) and plant names actually used."""
    with st.expander("⚙️ Configuration used", expanded=expanded):
        cols = st.columns(3)
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
            wind_link = getattr(s, "wind_link_mw", None)
            pvbess_link = getattr(s, "pvbess_link_mw", None)
            sell_link = getattr(s, "sell_link_mw", None)
            st.markdown(f"- Wind link: **{wind_link:.0f} MW**" if wind_link else "- Wind link: *unconstrained*")
            st.markdown(f"- PV+BESS link: **{pvbess_link:.0f} MW**" if pvbess_link else "- PV+BESS link: *unconstrained*")
            st.markdown(f"- Export/sell link: **{sell_link:.0f} MW**" if sell_link else "- Export/sell link: *unconstrained*")
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

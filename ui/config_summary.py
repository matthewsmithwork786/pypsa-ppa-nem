"""Shared scenario summary for optimisation and detailed results."""
from __future__ import annotations

import streamlit as st


def render_scenario_summary(s, portfolio=None) -> None:
    with st.expander("Scenario summary", expanded=False):
        fleet = portfolio or s
        cols = st.columns(4)
        with cols[0]:
            st.markdown("**Portfolio**")
            if s.optimise_capacity:
                st.markdown("- Mode: **co-optimised sizing** ⚡")
                st.markdown(
                    f"- Max build: wind **{s.max_build_wind_mw:.0f}** / "
                    f"solar **{s.max_build_pv_mw:.0f}** / "
                    f"BESS **{s.max_build_bess_mw:.0f} MW**"
                )
                representation = f"{s.sizing_n_periods} typical weeks" if s.sizing_method == "tsam" else "full hourly"
                st.markdown(f"- Sizing LP representation: **{representation}**")
                if s.include_bess:
                    st.markdown(f"- BESS duration: **{s.bess_max_hours:.1f} h** (fixed)")
                else:
                    st.markdown("- BESS: *disabled*")
            else:
                st.markdown(f"- Wind: **{s.onsw_mw:.0f} MW**")
                st.markdown(f"- Solar: **{s.pv_mw:.0f} MWac**")
                if s.include_bess:
                    st.markdown(f"- BESS: **{s.effective_bess_mw:.0f} MW / {s.effective_bess_mwh:.0f} MWh**")
                else:
                    st.markdown("- BESS: *disabled*")

            if s.optimise_capacity and portfolio is not None:
                st.markdown(
                    f"- Sized fleet: wind **{fleet.onsw_mw:.0f} MW** / "
                    f"solar **{fleet.pv_mw:.0f} MWac** / "
                    f"BESS **{fleet.effective_bess_mw:.0f} MW / {fleet.effective_bess_mwh:.0f} MWh**"
                )
            st.markdown("**Connection links**")
            for label, mw in (
                ("Wind", fleet.wind_link_mw),
                ("PV+BESS", fleet.pvbess_link_mw),
                ("Export/sell", fleet.sell_link_mw),
            ):
                limit = f"**{mw:.0f} MW**" if mw else "*unconstrained*"
                st.markdown(f"- {label}: {limit}")

        with cols[1]:
            st.markdown("**PPA contract**")
            st.markdown(f"- Offtake: **{s.ppaload_mw:.0f} MW** flat")
            st.markdown(f"- Tariff: **A${s.ppa_price:.0f}/MWh**")
            st.markdown(f"- Delivery target: **{s.required_delivery_share:.0%}**")
            if s.sla_monthly_enabled:
                st.markdown(f"- Monthly minimum: **{s.sla_monthly_share:.0%}**")
            if s.sla_daily_enabled:
                st.markdown(f"- Daily minimum: **{s.sla_daily_share:.0%}**")
            if not (s.sla_monthly_enabled or s.sla_daily_enabled):
                st.markdown("- Tiered SLA: *none*")
            if s.enable_penalty:
                st.markdown(f"- Penalty: **{s.pen_mult:.1f}×** = A${s.penalty_price:.0f}/MWh")
            else:
                st.markdown("- Penalty: *disabled*")

        with cols[2]:
            st.markdown("**Market interaction**")
            if s.enable_market_buy:
                st.markdown(f"- Buy cap: **{s.market_buy_share:.0%}** of delivery")
            else:
                st.markdown("- Market buy: *disabled*")
            if s.enable_market_sell:
                st.markdown(f"- Sell: enabled (max {s.maxsell_mw:.0f} MW)")
            else:
                st.markdown("- Market sell: *disabled*")
            if s.enable_shortfall:
                st.markdown(f"- Shortfall: **{s.allowed_shortfall_share:.0%}** of load")
            else:
                st.markdown("- Shortfall: *disabled*")

        with cols[3]:
            st.markdown("**Plants & simulation**")
            if s.is_nem:
                from ppa.data.nem_data import plant_name_for_duid

                for label, duid in (("Wind", s.nem_wind_duid), ("Solar", s.nem_pv_duid)):
                    if duid:
                        st.markdown(f"- {label}: **{plant_name_for_duid(duid)}** ({duid})")
                st.markdown(f"- Price region: **{s.nem_price_region}**, year **{s.nem_year}**")
                years = ", ".join(str(y) for y in s.nem_years)
                if years:
                    st.markdown(f"- NEM data years: **{years}**")
                st.markdown(f"- Resolution: **{s.nem_resolution_minutes} min**")
            else:
                st.markdown(f"- Data source: **{s.data_source}**")
            if s.optimise_capacity:
                st.markdown(f"- Sizing year: **{s.capacity_sizing_year}**")
            if s.transmission_cost_aud_mwh > 0:
                st.markdown(f"- Transmission: **A${s.transmission_cost_aud_mwh:.1f}/MWh** delivered")
            if s.dispatch_mode == "representative_year":
                st.markdown(f"- Mode: **1 solved year → {s.simulation_years}-year financial projection**")
                st.caption("Fixed energy volumes; no later-year degradation or dispatch re-solve.")
            elif s.simulation_years == 1:
                st.markdown(f"- Mode: **1-year** ({s.first_sim_year})")
            else:
                st.markdown(
                    f"- Mode: **{s.simulation_years}-year** "
                    f"({s.first_sim_year}–{s.first_sim_year + s.simulation_years - 1})"
                )
            st.markdown(f"- Price escalation: **{s.price_escalation_rate:.1%}/yr**")
            if s.dispatch_mode == "full_multi_year":
                st.markdown(
                    f"- Degradation: PV {s.pv_degradation_rate:.1%} | "
                    f"Wind {s.wind_degradation_rate:.1%} | "
                    f"BESS {s.bess_degradation_rate:.1%}"
                )

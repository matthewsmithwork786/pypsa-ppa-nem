"""Case Setup — customise all scenario parameters."""
from __future__ import annotations

import dataclasses

import streamlit as st

from ppa.scenario import BASE_SCENARIO
from ui import state
from ui.scenario_form import render_scenario_form


def render() -> None:
    st.title("🔬 Set Terms")
    st.markdown(
        "Choose plants in **Pick Plants**, set the PPA and portfolio assumptions here, then click "
        "**Apply changes**. Head to **Run** to start the model."
    )

    if not state.has_scenario():
        state.set_scenario(BASE_SCENARIO)

    current = state.get_scenario()
    updated = render_scenario_form(current)

    if dataclasses.asdict(updated) != dataclasses.asdict(current):
        st.warning(
            "⚠️ You have unapplied changes — click **Apply changes** below, "
            "otherwise Run will keep using the previous "
            "settings (e.g. an unapplied PPA tariff or tsam-weeks edit)."
        )

    cols = st.columns(2)
    with cols[0]:
        if st.button("Apply changes", type="primary", width="stretch"):
            state.set_scenario(updated)
            state.clear_run_outputs()
            st.success("Scenario updated. Head to Run to start the model.")
    with cols[1]:
        if st.button("Reset to base defaults", type="secondary", width="stretch"):
            state.set_scenario(BASE_SCENARIO)
            state.clear_custom_upload()
            state.clear_run_outputs()
            st.rerun()

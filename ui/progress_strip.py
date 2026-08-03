"""A three-step progress strip shown above the tabs.

The tab bar alone does not convey that the steps are ordered or that each has
a prerequisite: a scenario means nothing before plants are chosen, and a run
means nothing before both. The strip states the order and marks each step done
so a first-time user knows where they are and what is missing.
"""
from __future__ import annotations

import streamlit as st

from ui import state


def _steps() -> list[tuple[str, str, bool, str]]:
    """(number, label, done, hint) for each step, evaluated against state."""
    scn = state.get_scenario() if state.has_scenario() else None

    has_plants = bool(
        scn is not None
        and (getattr(scn, "nem_pv_duid", "") or getattr(scn, "nem_wind_duid", ""))
    ) or state.has_custom_upload()

    # "Terms set" means a case study was loaded or the form was applied --
    # a scenario exists that is not merely the untouched default.
    has_terms = scn is not None and has_plants

    has_result = state.has_multi_year_results() or state.has_result()

    return [
        ("①", "Pick plants", has_plants, "choose wind/solar on the map"),
        ("②", "Set terms", has_terms, "load a case study or adjust parameters"),
        ("③", "Run", has_result, "solve the portfolio"),
    ]


def render() -> None:
    cols = st.columns(3)
    for col, (num, label, done, hint) in zip(cols, _steps()):
        with col:
            if done:
                col.success(f"{num} **{label}** ✓", icon=None)
            else:
                col.info(f"{num} **{label}** — {hint}", icon=None)

import streamlit as st

st.set_page_config(
    page_title="PyPSA based PPA explorer - Australia",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from ui.tabs import (
    welcome,
    introduction,
    case_study,
    nem_map,
    custom_data,
    optimisation,
    results_deep_dive,
    sensitivity_analysis,
    financial_model,
)

optimisation.restore_from_query_params()

st.markdown(
    """
    # PyPSA based PPA explorer - Australia
    """
)

tabs = st.tabs(
    ["👋 Welcome", "① 📡 Pick Plants", "② 🔬 Set Terms", "③ ⚙️ Run", "📊 Results", "📖 Help"],
    on_change="rerun",
)

if tabs[0].open:
    with tabs[0]:
        welcome.render()

if tabs[1].open:
    with tabs[1]:
        nem_map.render()
        with st.expander("📤 Advanced: upload your own timeseries", expanded=False):
            custom_data.render()

if tabs[2].open:
    with tabs[2]:
        case_study.render()

if tabs[3].open:
    with tabs[3]:
        optimisation.render()

if tabs[4].open:
    with tabs[4]:
        dispatch_tab, finance_tab, sensitivity_tab = st.tabs(
            ["Dispatch & delivery", "Financial model", "Sensitivity"]
        )
        with dispatch_tab:
            results_deep_dive.render()
        with finance_tab:
            financial_model.render()
        with sensitivity_tab:
            sensitivity_analysis.render()

if tabs[5].open:
    with tabs[5]:
        introduction.render()

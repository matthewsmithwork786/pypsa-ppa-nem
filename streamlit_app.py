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
with st.popover("Disclaimer", width="stretch", icon="⚠️"):
    st.write(
        """
        This open-source explorer is for testing and scenario analysis. Historical
        profiles and model assumptions do not constitute a forecast or investment
        advice. Check the dispatch mode, data coverage and commercial assumptions
        before using the results. Fast mode repeats one solved operating year;
        it does not verify future-year delivery or model technology degradation.
        """
    )

# Six tabs, ordered by the ACTUAL dependency: plants must be chosen before a
# scenario means anything, so Get Data comes first. Custom Data folded into
# Get Data and Sensitivity Analysis into Results -- both are downstream of a
# result rather than peers of it.
tabs = st.tabs([
    "| 👋 Welcome",
    "| ① 📡 Pick Plants",
    "| ② 🔬 Set Terms",
    "| ③ ⚙️ Run",
    "| 📊 Results",
    "| 📖 Help",
], on_change="rerun")

i = 0
if tabs[i].open:
    with tabs[i]:
        welcome.render()

i += 1
if tabs[i].open:
    with tabs[i]:
        nem_map.render()
        st.divider()
        with st.expander("📤 Advanced: upload your own timeseries instead", expanded=False):
            custom_data.render()

i += 1
if tabs[i].open:
    with tabs[i]:
        case_study.render()

i += 1
if tabs[i].open:
    with tabs[i]:
        optimisation.render()

i += 1
if tabs[i].open:
    with tabs[i]:
        _res, _fin, _sens = st.tabs(
            ["| Dispatch & delivery", "| Financial model", "| Sensitivity"]
        )
        with _res:
            results_deep_dive.render()
        with _fin:
            financial_model.render()
        with _sens:
            sensitivity_analysis.render()

i += 1
if tabs[i].open:
    with tabs[i]:
        introduction.render()

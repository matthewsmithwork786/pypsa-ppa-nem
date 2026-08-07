import streamlit as st

st.set_page_config(
    page_title="PyPSA PPA Explorer",
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
    # PyPSA-based PPA Explorer
    """
)
with st.popover("Disclaimer", width="stretch", icon="⚠️"):
    st.write(
        """
        The content of this document/web page is intended for the exclusive use of **Open Energy Transition (OET)**'s client and other contractually agreed recipients.
        It may only be made available in whole or in part to third parties with the client's consent and on a non-reliance basis.
        **Open Energy Transition** is not liable to third parties for the completeness and accuracy of the information provided therein.
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

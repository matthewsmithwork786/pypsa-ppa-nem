from __future__ import annotations

import streamlit as st


def render() -> None:
    st.markdown(
        """
# 👋 Welcome to the PyPSA PPA Toolkit

An open-source tool for exploring how wind, solar and battery portfolios operate under different
**Power Purchase Agreement (PPA)** terms. PyPSA models how the portfolio is dispatched against the contract.

## How to use this toolkit
Three steps, left to right. Each one needs the step before it.

### ① 📡 Pick Plants
Choose Australian NEM wind and/or solar plants on the map, or upload your own
timeseries.

### ② 🔬 Set Terms
Set portfolio capacity, PPA terms, market assumptions and financial inputs.
Apply your changes before running the model.

### ③ ⚙️ Run
By default, **Fast** mode solves one operating year, then repeats its energy
volumes over the **25-year financial projection**. Financial prices and costs
can escalate; later-year degradation and changing dispatch are not simulated.
Choose **Full** mode to solve every operating year. Capacity sizing can take
substantially longer than fixed-capacity dispatch.

### 📊 Results
Dispatch and delivery, the full project-finance model (CAPEX, LCOE, IRR, NPV,
breakeven PPA price), and sensitivity to individual parameters.

### 📖 Help
Key concepts and terminology for PPAs and PyPSA.
        """
    )

    with st.expander("Main packages and data sources", expanded=False):
        st.markdown(
            """
Developed using the open-source work of [Open Energy Transition (OET)](https://openenergytransition.org/)
and the PyPSA community.

- [PyPSA](https://pypsa.readthedocs.io) — energy system modelling
- [HiGHS](https://highs.dev) — LP solver
- [Streamlit](https://streamlit.io) — web UI
- [Plotly](https://plotly.com) — interactive charts
- and using *historical* Australian data from
  - AEMO NEM UIGF (5-minute unconstrained availability) for wind & solar output, and
  - AEMO NEM regional spot prices (the AER base-futures series for hedges).

**UIGF** is unconstrained generation availability, used as the upper bound on
wind and solar output. It is not metered generation: the optimiser applies its
own economic curtailment and modelled connection limits. Historical network
constraints are not reproduced by UIGF alone.
            """
        )

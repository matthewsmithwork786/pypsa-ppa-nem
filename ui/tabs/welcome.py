from __future__ import annotations

import streamlit as st


def render() -> None:
    st.markdown(
        """
# 👋 Welcome to the PyPSA PPA Toolkit!

**Interactive, full flexible and open-source toolkit** for modelling renewable portfolios under different 
**Power Purchase Agreement (PPA)** assumptions.
**PyPSA** — an open-source energy system optimisation framework — is used to optimise how a renewable portfolio
(wind, solar, battery storage) should be dispatched when bound by the commercial terms of a PPA.

## How to use this toolkit
Three steps, left to right. Each one needs the step before it.

### ① 📡 Pick Plants
Choose real Australian NEM wind and/or solar plants on the map. Their 2025
generation profiles drive everything downstream, so nothing else means much
until this is done. Your own timeseries can be uploaded here instead.

### ② 🔬 Set Terms
Start from one of the four predefined case studies — each poses a concrete
question, and loading one fills in a complete, sensible scenario. Adjust from
there only if you want to:

* *Portfolio*: wind + solar + battery co-located at a single connection point.
* *PPA contract*: offtake load and profile, delivery obligation, shortfall cap, penalty multiplier.
* *Market interaction*: spot buy/sell with caps and bid-offer spread.
* *Financial assumptions*: capex, discount rate, target IRR, project life.
* *Simulation*: horizon, price escalation, technology degradation.

Leave **capacity co-optimisation** on to have the optimiser size wind, solar and
storage for you; turn it off to test a fleet you specify yourself.

### ③ ⚙️ Run
Solve the model. A fixed-capacity run takes a few seconds per simulated year;
adding capacity sizing costs roughly 20 seconds more.

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
- [PyPSA](https://pypsa.readthedocs.io) — energy system modelling
- [HiGHS](https://highs.dev) — LP solver
- [Streamlit](https://streamlit.io) — web UI
- [Plotly](https://plotly.com) — interactive charts
- and using *historical* Australian data from
  - AEMO NEM UIGF (5-minute unconstrained availability) for wind & solar output, and
  - AEMO NEM regional spot prices (the AER base-futures series for hedges).
            """
        )

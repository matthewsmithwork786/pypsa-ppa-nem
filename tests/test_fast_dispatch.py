"""Fast app runs keep the financial horizon without solving/copying it."""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from ppa.scenario import Scenario, validate_scenario
from ppa.simulation import scenario_for_dispatch, solve_slot


def test_dispatch_mode_keeps_financial_horizon():
    user = Scenario(simulation_years=25)
    dispatch = scenario_for_dispatch(user)
    assert dispatch.simulation_years == 1
    assert user.simulation_years == 25
    assert dispatch.project_life_yrs == user.project_life_yrs
    full = dataclasses.replace(user, dispatch_mode="full_multi_year")
    assert scenario_for_dispatch(full).simulation_years == 25


def test_invalid_dispatch_mode_blocks_run():
    assert any("dispatch" in e for e in validate_scenario(Scenario(dispatch_mode="unknown")))


def test_shared_solve_slot_rejects_overlap_and_releases_after_failure():
    with pytest.raises(ValueError):
        with solve_slot():
            with pytest.raises(RuntimeError, match="Another optimisation"):
                with solve_slot():
                    pytest.fail("Concurrent solve acquired the slot")
            raise ValueError("failed solver")
    with solve_slot():
        pass


def test_app_solves_one_year_and_builds_25_financial_records(monkeypatch, tmp_path):
    from ppa import multi_year, run_store
    from ppa.data import nem_data
    from ppa.network import build_network
    from ppa.results import extract_results
    from ppa.solver import solve

    s = Scenario(
        name="Fast test", simulation_years=25, data_source="custom_csv",
        onsw_mw=10, pv_mw=0, ppaload_mw=5, include_bess=False,
        price_escalation_rate=0, enable_counterfactual=False,
    )
    ts = pd.DataFrame({
        "ts_PVGen": [0., 0.], "ts_WindGen": [1., 1.],
        "ts_MktPrice": [50., 50.], "ppaload_mw": [5., 5.],
    }, index=pd.date_range("2025-01-01", periods=2, freq="h"))
    n = build_network(ts, s)
    status, condition = solve(n, s, ts)
    result = extract_results(n, s, ts, status, condition)
    solved_horizons = []

    def fake_run(**kwargs):
        solved_horizons.append(kwargs["scenario"].simulation_years)
        return [dataclasses.replace(result, scenario=kwargs["scenario"])]

    monkeypatch.setattr(multi_year, "run_multi_year", fake_run)
    monkeypatch.setattr(run_store, "_STORE_DIR", tmp_path)
    # NEM selection bypasses the upload prerequisite, with tiny in-memory inputs.
    inputs = {2025: pd.Series(np.ones(2), index=ts.index)}
    monkeypatch.setattr(nem_data, "get_timeseries_dicts", lambda s: (inputs, inputs, inputs))
    app = AppTest.from_string('''
import streamlit as st
from ppa.scenario import Scenario
from ui import state
from ui.tabs.optimisation import _run_simulation
s = Scenario(name="Fast test", simulation_years=25, onsw_mw=10, pv_mw=0,
             ppaload_mw=5, include_bess=False, price_escalation_rate=0)
state.set_scenario(s)
_run_simulation(s, 1, "012345abcdef")
st.write(len(state.get_multi_year_results()))
st.write(len(state.get_multi_year_financial().yearly))
''', default_timeout=30).run()
    assert not app.exception
    assert solved_horizons == [1]
    assert len(app.session_state["multi_year_results"]) == 1
    assert len(app.session_state["multi_year_financial"].yearly) == 25
    payload = run_store.load("012345abcdef")
    assert payload["scenario"].simulation_years == 25
    assert len(payload["results"]) == 1


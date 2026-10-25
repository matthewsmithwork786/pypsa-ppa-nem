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

    restored = AppTest.from_string('''
import streamlit as st
from ui.tabs.optimisation import restore_from_query_params
st.query_params["run"] = "012345abcdef"
restore_from_query_params()
''', default_timeout=30).run()
    assert not restored.exception
    assert len(restored.session_state["multi_year_financial"].yearly) == 25

    # Carry the persisted one-year result through the same finance, workbook,
    # and sensitivity interfaces the Results tabs consume.
    from io import BytesIO

    import openpyxl

    from ppa.financial_model import (
        ProjectFinanceInputs,
        energy_inputs_from_result,
        run_project_finance,
    )
    from ppa.financial_model_excel import export_financial_model
    from ppa.sensitivity import run_what_if

    energy = energy_inputs_from_result(payload["results"][0])
    finance_inputs = ProjectFinanceInputs(operating_life=25)
    finance = run_project_finance(finance_inputs, energy)
    workbook_bytes = export_financial_model(finance_inputs, energy, finance)
    workbook = openpyxl.load_workbook(BytesIO(workbook_bytes), data_only=False)
    assert {"Model", "Energy"}.issubset(workbook.sheetnames)
    assert run_what_if(energy, finance_inputs, ppa_tariff=finance_inputs.ppa_tariff + 1).project_irr != finance.project_irr

    from dataclasses import fields

    def render_deep_dive(results, fin, scenario):
        deep_dive = AppTest.from_string('''
from ui.tabs.results_deep_dive import render
render()
''', default_timeout=30)
        deep_dive.session_state["scenario"] = dataclasses.replace(scenario, enable_counterfactual=True)
        deep_dive.session_state["multi_year_results"] = results
        deep_dive.session_state["multi_year_financial"] = fin
        return deep_dive.run()

    representative = render_deep_dive(
        payload["results"], payload["fin"], payload["scenario"],
    )
    assert not representative.exception
    assert representative.selectbox[0].options == ["2025"]
    assert any(
        "Counterfactuals use solved dispatch years only" in caption.value
        for caption in representative.caption
    )
    assert any(
        "Solved-year effective price" in frame.value.columns
        and "Total cost over solved years" in frame.value.columns
        for frame in representative.table
    )
    for year in representative.selectbox[0].options:
        representative.selectbox[0].select(year).run()
        assert not representative.exception

    result = payload["results"][0]
    shifted_dispatch = dataclasses.replace(
        result.dispatch,
        **{
            field.name: getattr(result.dispatch, field.name).set_axis(
                getattr(result.dispatch, field.name).index + pd.DateOffset(years=1)
            )
            for field in fields(result.dispatch)
        },
    )
    second_year = dataclasses.replace(
        result,
        scenario=dataclasses.replace(result.scenario, first_sim_year=2026),
        dispatch=shifted_dispatch,
        market_prices=result.market_prices.set_axis(
            result.market_prices.index + pd.DateOffset(years=1)
        ),
    )
    two_year_finance = dataclasses.replace(payload["fin"], yearly=payload["fin"].yearly[:2])
    full_multi_year = dataclasses.replace(
        payload["scenario"], dispatch_mode="full_multi_year", simulation_years=2,
    )
    full = render_deep_dive(
        [result, second_year], two_year_finance, full_multi_year,
    )
    assert not full.exception
    assert full.selectbox[0].options == ["2025", "2026"]
    for year in full.selectbox[0].options:
        full.selectbox[0].select(year).run()
        assert not full.exception


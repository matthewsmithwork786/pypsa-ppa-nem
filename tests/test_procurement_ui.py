from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_five_page_workflow_runs_and_invalidates_edited_results():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"),
                            default_timeout=360).run()
    assert not app.exception
    assert len(app.tabs) == 5
    assert not app.tabs[0].info
    assert len(app.tabs[0].markdown) == 1
    assert "PyPSA" in app.tabs[0].markdown[0].value
    assert "selected historical year" in app.tabs[0].markdown[0].value
    assert app.number_input(key="simple_util").value == 75
    assert app.radio(key="simple_capacity_mode").value == "Find optimal capacity"
    assert app.radio(key="simple_requirement_mode").value == "NSW"
    keys = {x.key for x in app.number_input}
    assert "simple_nsw_annual" in keys
    assert "simple_aemo_annual" not in keys
    assert "simple_fixed_pv" not in keys
    assert app.slider(key="simple_cf_min_wind").value == 0
    assert app.slider(key="simple_cf_min_pv").value == 0
    assert app.multiselect(key="simple_map_regions").value
    assert app.session_state["simple_PV_plant"] is not None
    assert app.number_input(key="simple_pv_price").value == 80
    assert app.number_input(key="simple_wind_price").value == 130
    assert app.number_input(key="simple_bess_price").value == 6000
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert {m.label for m in app.metric} >= {"Spot-only cost", "Portfolio net cost", "Savings versus spot", "Effective procurement cost"}
    result = app.session_state["simple_result"][1]
    d = result["dispatch"]
    assert app.metric[3].label == "Effective procurement cost"
    assert app.metric[3].value == f"A${result['net_cost'] / d.Load.sum():,.2f}/MWh"
    assert d.Load.sum() == 75 * 8760
    assert d["Excess sold"].sum() > 0
    app.radio(key="simple_requirement_mode").set_value("AEMO").run()
    assert not app.exception
    assert not app.metric
    assert any("Inputs have changed" in x.value for x in app.info)
    keys = {x.key for x in app.number_input}
    assert "simple_nsw_annual" not in keys
    assert "simple_aemo_annual" in keys
    assert "simple_fixed_pv" not in keys
    assert [app.number_input(key=f"simple_band_{i}").value for i in range(4)] == [20, 40, 40, 20]
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    result = app.session_state["simple_result"][1]
    assert (result["compliance"].Achieved >= result["compliance"].Target - 1e-5).all()
    dispatch = result["dispatch"]
    assert not ((dispatch["Battery charge"] > 1e-5) & (dispatch["Battery discharge"] > 1e-5)).any()
    app.radio(key="simple_capacity_mode").set_value("Manually choose capacity").run()
    keys = {x.key for x in app.number_input}
    assert "simple_nsw_annual" not in keys
    assert "simple_aemo_annual" not in keys
    assert "simple_fixed_pv" in keys
    assert "simple_requirement_mode" not in {x.key for x in app.radio}
    app.number_input(key="simple_fixed_pv").set_value(120)
    app.number_input(key="simple_fixed_wind").set_value(80)
    app.number_input(key="simple_fixed_bess").set_value(10)
    app.number_input(key="simple_fixed_bess_energy").set_value(60.0).run()
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    result = app.session_state["simple_result"][1]
    assert result["capacities"] == {"PV": 120, "Wind": 80, "BESS": 10}
    assert result["assumptions"].storage_duration_h == 6
    assert len(result["matching"]) == 8
    assert result["compliance"].empty


def test_capacity_factor_filters_apply_to_dropdowns_and_allow_empty_results():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"),
                            default_timeout=30).run()
    assert not app.exception
    wind_count = len(app.selectbox(key="simple_Wind_plant").options)
    pv_count = len(app.selectbox(key="simple_PV_plant").options)
    app.slider(key="simple_cf_min_wind").set_value(100).run()
    assert not app.exception
    assert wind_count > len(app.selectbox(key="simple_Wind_plant").options) == 1
    assert len(app.selectbox(key="simple_PV_plant").options) == pv_count
    assert app.session_state["simple_Wind_plant"] is None
    app.slider(key="simple_cf_min_pv").set_value(100).run()
    assert not app.exception
    assert len(app.selectbox(key="simple_PV_plant").options) == 1
    assert app.session_state["simple_PV_plant"] is None
    app.slider(key="simple_cf_min_wind").set_value(0)
    app.slider(key="simple_cf_min_pv").set_value(0).run()
    assert not app.exception
    assert len(app.selectbox(key="simple_Wind_plant").options) == wind_count
    assert len(app.selectbox(key="simple_PV_plant").options) == pv_count

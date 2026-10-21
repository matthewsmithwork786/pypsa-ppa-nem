from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_five_page_workflow_runs_and_invalidates_edited_results():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"),
                            default_timeout=180).run()
    assert not app.exception
    assert len(app.tabs) == 5
    assert app.number_input(key="simple_pv_price").value == 80
    assert app.number_input(key="simple_wind_price").value == 130
    assert app.number_input(key="simple_bess_price").value == 2000
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert {m.label for m in app.metric} >= {"Spot-only cost", "Portfolio net cost", "Savings versus spot"}
    app.radio(key="simple_mode").set_value("AEMO proposed requirements").run()
    assert not app.exception
    assert not app.metric
    assert any("Inputs have changed" in x.value for x in app.info)
    assert [app.number_input(key=f"simple_band_{i}").value for i in range(4)] == [20, 40, 40, 20]
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    result = app.session_state["simple_result"][1]
    assert (result["compliance"].Achieved >= result["compliance"].Target - 1e-5).all()
    dispatch = result["dispatch"]
    assert not ((dispatch["Battery charge"] > 1e-5) & (dispatch["Battery discharge"] > 1e-5)).any()

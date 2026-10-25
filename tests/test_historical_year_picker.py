"""Exercise the actual Streamlit year widgets and their returned selections."""
from streamlit.testing.v1 import AppTest


def test_single_year_picker_and_optional_range_use_only_published_years():
    app = AppTest.from_string('''
import streamlit as st
from ui.tabs.nem_map import _select_historical_years
from ppa.scenario import BASE_SCENARIO
st.session_state['selected_years'] = _select_historical_years([2020, 2022, 2025], BASE_SCENARIO)
''').run()
    assert not app.exception
    assert app.session_state["selected_years"] == (2025,)
    app.selectbox(key="nm_model_year").select(2020).run()
    assert not app.exception
    assert app.session_state["selected_years"] == (2020,)
    app.checkbox(key="nm_use_year_range").check().run()
    app.select_slider(key="nm_year_range").set_range(2020, 2025).run()
    assert not app.exception
    assert app.session_state["selected_years"] == (2020, 2022, 2025)


def test_year_picker_replaces_a_selection_that_is_no_longer_available():
    app = AppTest.from_string('''
import streamlit as st
from ui.tabs.nem_map import _select_historical_years
from ppa.scenario import BASE_SCENARIO
st.session_state['nm_model_year'] = 2019
st.session_state['selected_years'] = _select_historical_years([2020, 2025], BASE_SCENARIO)
''').run()
    assert not app.exception
    assert app.session_state["selected_years"] == (2025,)

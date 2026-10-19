"""Capacity-factor floors must filter each technology independently."""
import pandas as pd
from streamlit.testing.v1 import AppTest

from ui.tabs.nem_map import _filter_capacity_factors, _selectable_duids


def _plants():
    return pd.DataFrame([
        {'duid': 'WIND_LOW', 'fuel_tech': 'Wind', 'cuf': .25, 'simulation_ready': True},
        {'duid': 'WIND_HIGH', 'fuel_tech': 'Wind', 'cuf': .40, 'simulation_ready': True},
        {'duid': 'SOLAR_LOW', 'fuel_tech': 'Solar', 'cuf': .17, 'simulation_ready': True},
        {'duid': 'SOLAR_HIGH', 'fuel_tech': 'Solar', 'cuf': .25, 'simulation_ready': True},
        {'duid': 'UNKNOWN', 'fuel_tech': 'Wind', 'cuf': None, 'simulation_ready': False},
    ])


def test_floors_are_independent_inclusive_and_feed_dropdowns():
    plants = _plants()
    filtered = _filter_capacity_factors(plants, 40, 20)
    assert filtered.duid.tolist() == ['WIND_HIGH', 'SOLAR_HIGH']
    assert _selectable_duids(filtered, 'Wind') == ['WIND_HIGH']
    assert _selectable_duids(filtered, 'Solar') == ['SOLAR_HIGH']
    assert _filter_capacity_factors(plants, 30, 0).duid.tolist() == ['WIND_HIGH', 'SOLAR_LOW', 'SOLAR_HIGH']
    assert _filter_capacity_factors(plants, 0, 20).duid.tolist() == ['WIND_LOW', 'WIND_HIGH', 'SOLAR_HIGH', 'UNKNOWN']
    pd.testing.assert_frame_equal(_filter_capacity_factors(plants, 0, 0), plants)
    assert _filter_capacity_factors(plants, 100, 100).empty


def test_selected_year_mean_precedes_annual_cf_and_invalid_values_fall_back():
    plants = _plants()
    plants['mean_cuf_selected_years'] = [.45, .20, float('inf'), None, None]
    plants.loc[2, 'mean_cf'] = .22
    plants['cuf'] = plants['cuf'].astype(object)
    plants.loc[2, 'cuf'] = 'missing'
    assert _filter_capacity_factors(plants, 40, 20).duid.tolist() == ['WIND_LOW', 'SOLAR_LOW', 'SOLAR_HIGH']


def test_live_picker_handles_filters_that_hide_selected_or_all_plants():
    app = AppTest.from_string('''
from ui.tabs.nem_map import render
render()
''', default_timeout=30).run()
    assert not app.exception
    app.selectbox(key='nm_wind_duid').select('BANGOWF1').run()
    assert not app.exception
    app.slider(key='nm_cuf_min_wind').set_value(100).run()
    assert not app.exception
    assert app.selectbox(key='nm_wind_duid').value == ''
    assert len(app.selectbox(key='nm_pv_duid').options) > 1
    app.slider(key='nm_cuf_min_solar').set_value(100).run()
    assert not app.exception
    assert app.selectbox(key='nm_wind_duid').options == ['(none)']
    assert app.selectbox(key='nm_pv_duid').options == ['(none)']
    assert app.button(key='nm_use_plants').disabled
    assert any('No plants match these filters' in message.value for message in app.info)
    app.slider(key='nm_cuf_min_wind').set_value(0).run()
    assert not app.exception
    assert len(app.selectbox(key='nm_wind_duid').options) > 1

import numpy as np
import pandas as pd
import pytest

from ppa.offtaker import run_of_plant_costs


def _case(dt=1.0):
    index = pd.date_range('2025-01-01', periods=3, freq='h')
    load, wind, solar, spot = [pd.Series(x, index=index, dtype=float) for x in (
        [10, 10, 10], [15, 0, 0], [0, 0, 15], [-20, 100, 40],
    )]
    return run_of_plant_costs(load, wind, solar, spot, 50, 30, dt)


def test_settlement_pays_all_generation_and_retains_negative_surplus_revenue():
    df = _case()
    assert df['Wind PPA cost (A$)'].sum() == 750
    assert df['Solar PPA cost (A$)'].sum() == 450
    assert df['Shortfall cost (A$)'].sum() == 1000
    assert df['Surplus revenue (A$)'].tolist() == [-100, 0, 200]
    assert df['Negative-price surplus cost (A$)'].sum() == 100
    assert df['Net procurement cost (A$)'].sum() == 2100
    assert df['Spot-only cost (A$)'].sum() == 1200
    # Equal annual generation and load still leaves both surplus and shortfalls.
    assert df['Matched (MWh)'].sum() == 20
    assert df['Shortfall (MWh)'].sum() == df['Surplus (MWh)'].sum() == 10
    np.testing.assert_allclose(
        df['Load (MWh)'], df['Matched (MWh)'] + df['Shortfall (MWh)'],
    )
    np.testing.assert_allclose(
        df['Wind (MWh)'] + df['Solar (MWh)'], df['Matched (MWh)'] + df['Surplus (MWh)'],
    )
    # Equivalent CfD accounting: load at spot plus fixed-minus-spot on all output.
    alternative = df['Spot-only cost (A$)'] + df['Wind PPA cost (A$)'] + df['Solar PPA cost (A$)'] - df['Wind spot value (A$)'] - df['Solar spot value (A$)']
    np.testing.assert_allclose(df['Net procurement cost (A$)'], alternative)


def test_resolution_scales_every_energy_and_cost():
    pd.testing.assert_frame_equal(_case(0.5), _case() * 0.5)


@pytest.mark.parametrize('generation', [0, 10, 20])
def test_zero_exact_and_excess_generation(generation):
    idx = pd.date_range('2025-01-01', periods=2, freq='h')
    def series(value):
        return pd.Series(float(value), index=idx)
    df = run_of_plant_costs(series(10), series(generation), series(0), series(100), 50, 30)
    assert df['Net procurement cost (A$)'].sum() == 2000 - generation * 100


def test_zero_load_can_have_net_surplus_revenue():
    idx = pd.date_range('2025-01-01', periods=1, freq='h')
    series = lambda value: pd.Series(float(value), index=idx)
    df = run_of_plant_costs(series(0), series(10), series(0), series(100), 50, 30)
    assert df['Net procurement cost (A$)'].sum() == -500


def test_rejects_misaligned_or_missing_data():
    s = pd.Series([1., 2.])
    with pytest.raises(ValueError, match='same non-empty index'):
        run_of_plant_costs(s, s.iloc[::-1], s, s, 50, 30)
    with pytest.raises(ValueError, match='finite'):
        run_of_plant_costs(s, s, s, pd.Series([1., np.nan]), 50, 30)
    with pytest.raises(ValueError, match='positive'):
        run_of_plant_costs(s, s, s, s, 50, 30, dt=0)


def test_streamlit_offtaker_section_reprices_without_a_solve():
    from streamlit.testing.v1 import AppTest

    script = '''
import pandas as pd
from types import SimpleNamespace
from ui.tabs.results_deep_dive import _render_offtaker
idx = pd.date_range('2025-01-01', periods=3, freq='h')
def series(values):
    return pd.Series(values, index=idx, dtype=float)
d = SimpleNamespace(wind_gen=series([15,0,0]), pv_gen=series([0,0,15]),
    ppa_delivery=series([10,10,10]), allowed_shortfall=series([0,0,0]), penalty_gen=series([0,0,0]))
r = SimpleNamespace(dispatch=d, market_prices=series([-20,100,40]), n_period_hours=3,
    scenario=SimpleNamespace(ppa_price=50.0))
_render_offtaker(r)
'''
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert app.metric[1].value == 'A$80.00/MWh'
    assert app.dataframe[0].value['A$ million'].iloc[4] == pytest.approx(.0024)
    assert app.dataframe[1].value['GWh'].iloc[0] == pytest.approx(.03)
    labels = [p.proto.popover.label for p in app.get('popover')]
    assert '? How run-of-plant procurement works' in labels
    assert '? Monthly load matching and surplus' in labels
    assert '? Cumulative procurement cost' in labels
    app.number_input[1].set_value(30).run()
    assert not app.exception
    assert app.metric[1].value == 'A$70.00/MWh'
    assert len(app.get('plotly_chart')) == 2

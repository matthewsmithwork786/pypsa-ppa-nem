import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from ui.result_display import aggregate_units, number_table


def test_aggregate_units_scales_values_and_labels_without_changing_source():
    raw = pd.DataFrame({'Load (MWh)': [876000.], 'Net cost (A$)': [88304904.52], 'Price (A$/MWh)': [100.8]})
    display = aggregate_units(raw)
    assert display['Load (GWh)'][0] == 876
    assert display['Net cost (A$ million)'][0] == pytest.approx(88.30490452)
    assert display['Price (A$/MWh)'][0] == 100.8
    assert raw['Load (MWh)'][0] == 876000


def test_comma_separators_preserve_numeric_table_values():
    raw = pd.DataFrame({'Value': [1234567.89, -12345.6]})
    styled = number_table(raw)
    html = styled.to_html()
    assert '1,234,568' in html
    assert '-12,346' in html
    pd.testing.assert_frame_equal(styled.data, raw)


def test_year_financial_summary_shows_penalty_without_changing_net_cash_flow():
    app = AppTest.from_string('''
from types import SimpleNamespace
from ui.tabs.results_deep_dive import _render_financial_summary
_render_financial_summary(SimpleNamespace(ppa_revenue=100e6, merch_revenue=10e6,
    penalty_cost=2.5e6, net_cashflow=80e6, fulfilled_share=.75,
    wind_gen_mwh=800000, pv_gen_mwh=300000), 2025)
''').run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics['Penalty cost'] == 'A$2M'
    assert metrics['Net cash flow'] == 'A$80M'
    assert metrics['Wind + solar generation'] == '1,100 GWh'


def test_project_finance_view_uses_existing_penalty_schedule_in_millions():
    app = AppTest.from_string('''
from ppa.financial_model import EnergyInputs, ProjectFinanceInputs, run_project_finance
from ui.tabs.financial_model import _render_results
e = EnergyInputs(onsw_mw=100, pv_mw=0, bess_mw=0, bess_mwh=0,
    load_mw=50, ppa_gwh=300, excess_solar_gwh=0, excess_nonsolar_gwh=50,
    penalty_gwh=5, total_solar_gwh=0, total_nonsolar_gwh=350,
    sell_solar_price=60, sell_nonsolar_price=55, purchase_price=70)
r = run_project_finance(ProjectFinanceInputs(), e)
_render_results(r)
''').run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics['Lifetime penalty cost'].startswith('A$')
    assert metrics['Lifetime penalty cost'].endswith('M')
    table = app.dataframe[0].value
    assert 'Penalty cost (A$ million)' in table
    assert (table['Penalty cost (A$ million)'] < 0).any()
    assert len(app.get('plotly_chart')) == 3

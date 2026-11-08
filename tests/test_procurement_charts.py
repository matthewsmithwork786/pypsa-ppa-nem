import numpy as np
import pandas as pd

from ui.procurement_charts import COLOURS, generation_duration_png, hourly_chart, monthly_chart
from ui.procurement_plants import apply_map_selection
from ui.tabs.nem_map import _tooltip


def dispatch():
    index = pd.date_range("2025-01-01", periods=48, freq="h")
    d = pd.DataFrame({"PV": 40., "Wind": 30., "Battery discharge": 10., "Battery charge": 5.,
                      "PV to load": 30., "Wind to load": 20., "BESS to load": 10.,
                      "Spot purchases": 30., "Load": 90.}, index=index)
    return d


def test_monthly_columns_share_positions_and_colours():
    fig = monthly_chart(dispatch())
    assert fig.layout.xaxis.domain == fig.layout.xaxis2.domain
    assert fig.layout.xaxis.matches == "x2"
    assert fig.layout.barmode == "relative"
    for trace in fig.data:
        assert trace.type == "bar"
        assert list(trace.x) == ["Jan"]
        assert trace.marker.color == COLOURS[trace.name]
    charge = next(t for t in fig.data if t.name == "Battery charge")
    assert all(y < 0 for y in charge.y)


def test_hourly_columns_show_negative_charging_and_range_control():
    d = dispatch()
    fig = hourly_chart(d)
    assert fig.layout.xaxis.rangeslider.visible
    assert fig.layout.barmode == "relative"
    bars = {t.name: t for t in fig.data if t.type == "bar"}
    assert set(bars) == {"PV", "Wind", "Battery discharge", "Battery charge", "Spot purchases"}
    np.testing.assert_allclose(bars["Battery charge"].y, -d["Battery charge"])
    assert fig.data[-1].name == "Load"


def test_generation_duration_ranks_the_stack_and_load_together(monkeypatch):
    d = dispatch().iloc[:4].copy()
    d["PV"] = [10, 0, 20, 10]
    d["Wind"] = [10, 5, 10, 0]
    d["Battery discharge"] = [0, 30, 0, 10]
    d["Spot purchases"] = [0, 0, 0, 50]
    d["Load"] = [55, 60, 65, 70]
    figures = []
    monkeypatch.setattr("ui.procurement_charts.Figure.savefig",
                        lambda figure, *args, **kwargs: figures.append(figure))

    d["PV supply"] = d["PV"] - [0, 0, 12, 0]
    d["Wind supply"] = d["Wind"] - [0, 0, 3, 0]
    d["BESS supply"] = d["Battery discharge"]
    d["Battery charge"] = [0, 0, 15, 0]
    d["Excess sold"] = [0, 0, 0, 0]
    d["Renewable delivery"] = [20, 35, 15, 20]
    generation_duration_png(d, 25)

    ax = figures[0].axes[0]
    assert [patch.get_label() for patch in ax.patches] == ["PV", "Wind", "Battery discharge", "Battery charge"]
    np.testing.assert_array_equal(ax.patches[2].get_data().values, [35, 20, 20, 15])
    np.testing.assert_array_equal(ax.patches[2].get_data().edges, [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(ax.patches[3].get_data().values, [0, 0, 0, -15])
    assert ax.get_ylim()[0] == -25
    assert [label.get_text() for label in ax.texts] == ["0.00 GWh excess sold", "36.0% of load met"]
    np.testing.assert_array_equal(ax.lines[-1].get_ydata(), [60, 55, 70, 65])


def test_map_click_selects_correct_technology_and_does_not_override_later_dropdown():
    plants = pd.DataFrame([dict(duid="SOL1", station_name="Solar A", fuel_tech="Solar", region="NSW1",
                               capacity_registered_mw=100),
                           dict(duid="WND1", station_name="Wind A", fuel_tech="Wind", region="VIC1",
                                capacity_registered_mw=200)])
    state = {}
    tooltip = _tooltip(plants.iloc[0])
    apply_map_selection(state, tooltip, plants, 2025)
    assert state["simple_PV_plant"] == "SOL1"
    state["simple_PV_plant"] = None
    apply_map_selection(state, tooltip, plants, 2025)
    assert state["simple_PV_plant"] is None
    apply_map_selection(state, _tooltip(plants.iloc[1]), plants, 2025)
    assert state["simple_Wind_plant"] == "WND1"
    apply_map_selection(state, "Unknown marker", plants, 2025)
    assert state["simple_Wind_plant"] == "WND1"

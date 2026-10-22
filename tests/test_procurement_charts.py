import numpy as np
import pandas as pd

from ui.procurement_charts import COLOURS, hourly_chart, monthly_chart
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

"""Map clicks select the same cached profiles as the plant dropdowns."""
import folium
import streamlit as st
from streamlit_folium import st_folium

from ui.procurement_charts import COLOURS
from ui.tabs.nem_map import _duid_from_tooltip, _marker_radius, _tooltip


def apply_map_selection(state, tooltip, plants, year):
    """Consume each click once, before constructing dropdown widgets."""
    token = (year, tooltip)
    if not tooltip or state.get("simple_map_handled") == token:
        return
    state["simple_map_handled"] = token
    duid = _duid_from_tooltip(tooltip, plants)
    if duid is not None:
        tech = plants.set_index("duid").loc[duid, "fuel_tech"]
        state[f"simple_{'PV' if tech == 'Solar' else 'Wind'}_plant"] = duid


def plant_map(plants, year):
    regions = sorted(plants.region.unique())
    selected = st.multiselect("Plant regions", regions, default=regions, key="simple_map_regions")
    subset = plants[plants.region.isin(selected)]
    st.caption("Click a yellow PV or green wind marker to select its generation profile. You can also use the dropdowns below. Only cached, simulation-ready plants are shown.")
    fmap = folium.Map(location=[-29, 140], zoom_start=4, tiles="OpenStreetMap")
    for _, row in subset.dropna(subset=["lat", "lon"]).iterrows():
        colour = COLOURS["PV" if row.fuel_tech == "Solar" else "Wind"]
        folium.CircleMarker(location=[row.lat, row.lon], radius=_marker_radius(row.capacity_registered_mw),
                            color=colour, fill=True, fill_color=colour, fill_opacity=0.8,
                            tooltip=_tooltip(row)).add_to(fmap)
    clicked = st_folium(fmap, height=420, use_container_width=True, key=f"simple_map_{year}",
                         returned_objects=["last_object_clicked_tooltip"])
    apply_map_selection(st.session_state, clicked.get("last_object_clicked_tooltip"), subset, year)
    return subset

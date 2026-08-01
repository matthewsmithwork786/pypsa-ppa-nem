"""Get Data (NEM Plant Map) — pick a real wind/solar plant to drive the optimizer.

Pure helper functions (no Streamlit calls) live at module level so they're
independently unit-testable; `render()` wires them into the Streamlit UI,
mirroring the click-to-place idiom in `ui/scenario_form.py` and the
cache-status expander layout used across the tabs.
"""
from __future__ import annotations

import dataclasses
import html
import math

import pandas as pd
import streamlit as st

from ppa.data import nem_data
from ui import state
from ui.nem_cache_status import cached_cache_status

FUEL_COLORS = {
    "Wind": "#2E7D32",   # green, matches existing convention
    "Solar": "#F9A825",  # yellow, matches existing convention
}
DEFAULT_COLOR = "#757575"

STATUS_LABELS = {
    "ready": "Simulation-ready ✓",
    "incomplete": "SCADA cached but incomplete",
    "no_scada": "No SCADA cached",
    "unreadable": "SCADA cache unreadable",
    "unchecked": "Not checked",
}


# ── Pure helpers (no Streamlit) ──────────────────────────────────────────────

def _marker_radius(capacity_mw: float) -> float:
    """Strictly increasing marker radius, proportional to sqrt(capacity)."""
    return 3.0 + 0.85 * math.sqrt(max(0.0, capacity_mw))


CUF_FALLBACK = "—"


def _format_cuf(value) -> str:
    """Format a CUF fraction (e.g. 0.384) as a percentage string, or the
    em-dash fallback when unknown."""
    try:
        cuf = float(value)
    except (TypeError, ValueError):
        return CUF_FALLBACK
    if not math.isfinite(cuf):
        return CUF_FALLBACK
    return f"{cuf * 100:.1f}%"


def _format_first_power(value) -> str:
    """Format first-power date as YYYY-MM-DD, or the em-dash fallback when unknown."""
    if value is None:
        return CUF_FALLBACK
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "na"}:
        return CUF_FALLBACK
    return text


def _tooltip(row) -> str:
    """Unique tooltip string: station name + DUID (disambiguates duplicated
    station names across multiple DUIDs) + capacity + region + CUF + first power.
    HTML-escaped since station names may contain special characters.

    CUF prefers the strict `cuf` field (energy ÷ nameplate × hours-in-year)
    from `nem_data.scada_summary`, falling back to `mean_cf` (mean of the
    clipped 5-min CF series). First power prefers the registry's
    `first_power_date` (labelled "1st power"); a SCADA-derived date (2025-only
    cache) is labelled "first 2025 output" per the plan. Either shows '—' when
    unknown.
    """
    station = html.escape(str(row["station_name"]))
    duid = html.escape(str(row["duid"]))
    region = html.escape(str(row["region"]))
    capacity = float(row["capacity_registered_mw"])

    cuf_val = row.get("cuf")
    if not _finite_value(cuf_val):
        cuf_val = row.get("mean_cf")
    cuf = _format_cuf(cuf_val)

    first_power_label, first_power = _first_power_parts(row)
    return (
        f"{station} [{duid}] · {capacity:.0f} MW · {region} · "
        f"CUF {cuf} · {first_power_label} {first_power}"
    )


def _finite_value(value) -> bool:
    """True when value is a usable float (not None / NaN / inf)."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _first_power_parts(row) -> "tuple[str, str]":
    """Return (label, formatted-date) for the first-power portion of the tooltip.

    The registry's `first_power_date` is true first power ("1st power"); the
    SCADA-derived `first_output_date` is 2025-only and therefore labelled
    "first 2025 output". Falls back to ('1st power', '—') when neither exists.
    """
    registry_date = row.get("first_power_date")
    if _finite_value(registry_date) or (
        registry_date is not None and str(registry_date).lower() not in {"nan", "nat", "none", "na", ""}
    ):
        return "1st power", _format_first_power(registry_date)
    scada_date = row.get("first_output_date")
    if _finite_value(scada_date) or (
        scada_date is not None and str(scada_date).lower() not in {"nan", "nat", "none", "na", ""}
    ):
        return "first 2025 output", _format_first_power(scada_date)
    return "1st power", CUF_FALLBACK


def _duid_from_tooltip(tooltip: str, plants_df: "pd.DataFrame") -> "str | None":
    """Exact reverse lookup by matching the tooltip string, not nearest-coordinate."""
    if tooltip is None or plants_df is None or plants_df.empty:
        return None
    for _, row in plants_df.iterrows():
        if _tooltip(row) == tooltip:
            return str(row["duid"])
    return None


def _marker_style(row) -> dict:
    """Color/fill_opacity/weight by data_status: solid tech-color for "ready",
    hollow/dashed tech-color outline for "incomplete", gray hollow dashed for
    "no_scada"/"unreadable"/"unchecked".
    """
    color = FUEL_COLORS.get(str(row.get("fuel_tech", "")), DEFAULT_COLOR)
    status = row.get("data_status", "unchecked")
    if status == "ready":
        return {"color": color, "fill": True, "fill_color": color, "fill_opacity": 0.85,
                "weight": 2, "dash_array": None}
    if status == "incomplete":
        return {"color": color, "fill": False, "fill_color": color, "fill_opacity": 0.0,
                "weight": 2, "dash_array": "4"}
    return {"color": DEFAULT_COLOR, "fill": False, "fill_color": DEFAULT_COLOR, "fill_opacity": 0.0,
            "weight": 2, "dash_array": "2,4"}


def _plant_label(row) -> str:
    status = STATUS_LABELS.get(row.get("data_status", "unchecked"), row.get("data_status", ""))
    return (
        f"{row['station_name']} [{row['duid']}] · {row['capacity_registered_mw']:.0f} MW "
        f"· {row['region']} · {status}"
    )


def _selectable_duids(plants_df: "pd.DataFrame", fuel_tech: str, allow_unready: bool) -> list:
    if plants_df is None or plants_df.empty:
        return []
    df = plants_df[plants_df["fuel_tech"] == fuel_tech]
    if not allow_unready:
        df = df[df["simulation_ready"]]
    return list(df["duid"])


# ── Streamlit render ─────────────────────────────────────────────────────────

@st.cache_data
def _cached_eligible_plants(year: int, fingerprint: tuple) -> "pd.DataFrame":
    return nem_data.list_eligible_plants(year=year, check_whole_year=True)


def _label_for_duid(duid: str, plants_df: "pd.DataFrame") -> str:
    if not duid:
        return "(none)"
    row = plants_df.loc[plants_df["duid"] == duid]
    if row.empty:
        return duid
    return _plant_label(row.iloc[0])


def render() -> None:
    st.title("📡 Get Data")
    st.markdown(
        "Pick real Australian wind and/or solar plants (2025 5-minute AEMO SCADA) "
        "to drive the optimizer."
    )

    year = nem_data.DEFAULT_YEAR

    cols = st.columns([1, 3])
    with cols[0]:
        if st.button("🔄 Re-scan cache", key="nm_rescan"):
            st.cache_data.clear()
            st.rerun()

    try:
        fingerprint = nem_data.cache_fingerprint(year)
        plants_df = _cached_eligible_plants(year, fingerprint)
    except FileNotFoundError:
        st.error(
            "NEM plant registry not found. Run `python scripts/fetch_nem_plant_registry.py` "
            "in a non-sandboxed environment and copy the output into "
            "`data/cache/nem/registry/nem_plant_registry.parquet`."
        )
        return

    status = cached_cache_status(year)

    with st.expander("**NEM cache status**", expanded=(status["n_simulation_ready"] == 0)):
        c = st.columns(4)
        c[0].metric("Registry plants", status["n_registry_plants"])
        c[1].metric("SCADA cached", status["n_scada_cached"])
        c[2].metric("Simulation-ready", status["n_simulation_ready"])
        c[3].metric("Price regions cached", f"{len(status['price_regions_cached'])}/{len(nem_data.NEM_REGIONS)}")

        if status["missing_price_regions"]:
            st.caption(f"Missing price regions: {', '.join(status['missing_price_regions'])}")

        if status["n_scada_cached"] == 0:
            st.warning(
                "No SCADA/price data cached yet. Run the acquisition script in a "
                "non-sandboxed environment with network access, then copy the output "
                "into `data/cache/nem/{scada,price}/`:"
            )
            st.code(f"python scripts/fetch_nem_scada_prices.py --year {year}", language="bash")

    if plants_df.empty:
        st.info("No plants match the current filters.")
        return

    # ── Filters ───────────────────────────────────────────────────────────────
    regions_present = sorted(plants_df["region"].unique())
    region_filter = st.multiselect(
        "Region filter", options=regions_present, default=regions_present, key="nm_region_filter",
    )
    allow_unready = st.toggle(
        "Allow selecting plants without complete SCADA (not simulation-ready)",
        value=False, key="nm_allow_unready",
    )

    filtered = plants_df[plants_df["region"].isin(region_filter)] if region_filter else plants_df

    # ── Apply pending map click BEFORE rendering the selectboxes ───────────────
    click_state = st.session_state.get("nm_map", {}) or {}
    clicked_tooltip = click_state.get("last_object_clicked_tooltip")
    if clicked_tooltip and st.session_state.get("_nm_handled_tooltip") != clicked_tooltip:
        st.session_state["_nm_handled_tooltip"] = clicked_tooltip
        clicked_duid = _duid_from_tooltip(clicked_tooltip, filtered)
        if clicked_duid is not None:
            row = filtered.loc[filtered["duid"] == clicked_duid].iloc[0]
            if row["simulation_ready"] or allow_unready:
                if row["fuel_tech"] == "Wind":
                    st.session_state["nm_wind_duid"] = clicked_duid
                elif row["fuel_tech"] == "Solar":
                    st.session_state["nm_pv_duid"] = clicked_duid

    wind_options = [""] + _selectable_duids(filtered, "Wind", allow_unready)
    pv_options = [""] + _selectable_duids(filtered, "Solar", allow_unready)

    cols = st.columns(2)
    with cols[0]:
        wind_duid = st.selectbox(
            "Wind plant", options=wind_options,
            format_func=lambda d: _label_for_duid(d, filtered),
            key="nm_wind_duid",
        )
    with cols[1]:
        pv_duid = st.selectbox(
            "Solar plant", options=pv_options,
            format_func=lambda d: _label_for_duid(d, filtered),
            key="nm_pv_duid",
        )

    price_region = st.selectbox(
        "Price region", options=nem_data.NEM_REGIONS,
        index=nem_data.NEM_REGIONS.index(nem_data.DEFAULT_REGION),
        key="nm_price_region",
    )
    price_region_cached = price_region in status["price_regions_cached"]
    if not price_region_cached:
        st.warning(
            f"No cached price data for region {price_region}. Run "
            f"`python scripts/fetch_nem_scada_prices.py --year {year}` to fetch it."
        )

    # ── Map ──────────────────────────────────────────────────────────────────
    try:
        import folium
        from streamlit_folium import st_folium
    except ImportError:
        st.map(
            filtered.rename(columns={"lat": "lat", "lon": "lon"})[["lat", "lon"]],
            zoom=4, height=400,
        )
        st.caption(
            "Install `streamlit-folium` to see colored/sized markers and click-to-select."
        )
    else:
        center_lat = float(filtered["lat"].mean())
        center_lon = float(filtered["lon"].mean())
        fmap = folium.Map(location=(center_lat, center_lon), zoom_start=4, tiles="CartoDB positron")
        for _, row in filtered.iterrows():
            style = _marker_style(row)
            folium.CircleMarker(
                (float(row["lat"]), float(row["lon"])),
                radius=_marker_radius(float(row["capacity_registered_mw"])),
                color=style["color"],
                weight=style["weight"],
                dash_array=style["dash_array"],
                fill=style["fill"],
                fill_color=style["fill_color"],
                fill_opacity=style["fill_opacity"],
                tooltip=_tooltip(row),
            ).add_to(fmap)
        st_folium(
            fmap, height=420, use_container_width=True,
            key="nm_map", returned_objects=["last_object_clicked_tooltip"],
        )
        st.caption(
            "🟢 Wind · 🟡 Solar · solid = simulation-ready · dashed outline = incomplete/no SCADA. "
            "Click a marker or use the selectboxes above (selectboxes are authoritative)."
        )
        st.caption(
            "Marker tooltip: CUF = energy ÷ (nameplate × hours-in-year, 2025 SCADA); "
            "“1st power” = registry commissioning date, “first 2025 output” = first sustained "
            "SCADA output, “—” = not available."
        )

    # ── Native 5-min CF inspection for the selected plants ──────────────────────
    for label, duid in (("Wind", wind_duid), ("Solar", pv_duid)):
        if not duid:
            continue
        row = filtered.loc[filtered["duid"] == duid]
        if row.empty or not bool(row.iloc[0]["simulation_ready"]):
            continue
        with st.expander(f"{label} plant CF — {duid}", expanded=False):
            cf = nem_data.capacity_factor_for_duid(duid, year=year, registry=filtered)
            monthly = cf.groupby(cf.index.month).mean()
            st.line_chart(cf.iloc[:: max(1, len(cf) // 2000)])
            st.bar_chart(monthly)

    # ── Action buttons ───────────────────────────────────────────────────────
    any_ready = any(
        bool(filtered.loc[filtered["duid"] == d, "simulation_ready"].iloc[0])
        for d in (wind_duid, pv_duid) if d
    )
    use_disabled = not (any_ready and price_region_cached and (wind_duid or pv_duid))

    if st.button(
        "✅ Use these plants", type="primary", width="stretch",
        key="nm_use_plants", disabled=use_disabled,
    ):
        current = state.get_scenario()
        if current is None:
            from ppa.scenario import BASE_SCENARIO
            current = BASE_SCENARIO
        updated = dataclasses.replace(
            current,
            data_source="nem_map",
            nem_pv_duid=pv_duid,
            nem_wind_duid=wind_duid,
            nem_price_region=price_region,
            nem_year=year,
        )
        state.set_scenario(updated)
        state.set_nem_selection({
            "pv_duid": pv_duid, "wind_duid": wind_duid,
            "price_region": price_region, "year": year,
        })
        state.clear_custom_upload()
        state.clear_run_outputs()
        st.rerun()

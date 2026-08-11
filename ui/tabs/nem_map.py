"""Get Data (NEM Plant Map) — pick a real wind/solar plant to drive the optimiser.

Pure helper functions (no Streamlit calls) live at module level so they're
independently unit-testable; `render()` wires them into the Streamlit UI,
mirroring the click-to-place idiom in `ui/scenario_form.py` and the
cache-status expander layout used across the tabs.
"""
from __future__ import annotations

import dataclasses
import html
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from ppa.data import nem_data
from ui import state
from ui.constants import NEM_RESOLUTION_MINUTES
from ui.nem_cache_status import cached_cache_status, price_years_covered

FUEL_COLORS = {
    "Wind": "#2E7D32",   # green, matches existing convention
    "Solar": "#F9A825",  # yellow, matches existing convention
}
DEFAULT_COLOR = "#757575"

STATUS_LABELS = {
    "ready": "Simulation-ready ✓",
    "incomplete": "UIGF cached but incomplete year",
    "no_scada": "No UIGF data available",
    "unreadable": "UIGF cache unreadable",
    "unchecked": "Not checked",
}

UIGF_EXPLAINER = (
    "**UIGF — Unconstrained Intermittent Generation Forecast.** AEMO's estimate, "
    "recorded per semi-scheduled unit in the `DISPATCHLOAD` table for every "
    "5-minute dispatch interval, of the output that unit could produce given the "
    "prevailing weather, before any network constraint is applied."
)


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

    CUF prefers the `mean_cuf_selected_years` field (mean annual CUF over the
    selected multi-year range, from the plant-years manifest) when present,
    falling back to the strict `cuf` field (energy ÷ nameplate × hours-in-year,
    from `nem_data.scada_summary`), then `mean_cf` (mean of the clipped 5-min CF
    series). First power prefers the registry's `first_power_date` (labelled
    "1st power"); a generation-derived date (2025-only cache) is labelled
    "first 2025 output" per the plan. Either shows '—' when unknown.
    """
    station = html.escape(str(row["station_name"]))
    duid = html.escape(str(row["duid"]))
    region = html.escape(str(row["region"]))
    capacity = float(row["capacity_registered_mw"])

    cuf_label = "CUF"
    cuf_val = row.get("mean_cuf_selected_years")
    if _finite_value(cuf_val):
        cuf_label = "mean CUF (selected years)"
    else:
        cuf_val = row.get("cuf")
        if not _finite_value(cuf_val):
            cuf_val = row.get("mean_cf")
    cuf = _format_cuf(cuf_val)

    first_power_label, first_power = _first_power_parts(row)
    return (
        f"{station} [{duid}] · {capacity:.0f} MW · {region} · "
        f"{cuf_label} {cuf} · {first_power_label} {first_power}"
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
    The generation-derived `first_output_date` is 2025-only and therefore labelled
    "first 2025 output". Falls back to ('1st power', '—') when neither exists.
    """
    registry_date = row.get("first_power_date")
    if _finite_value(registry_date) or (
        registry_date is not None and str(registry_date).lower() not in {"nan", "nat", "none", "na", ""}
    ):
        return "1st power", _format_first_power(registry_date)
    output_date = row.get("first_output_date")
    if _finite_value(output_date) or (
        output_date is not None and str(output_date).lower() not in {"nan", "nat", "none", "na", ""}
    ):
        return "first 2025 output", _format_first_power(output_date)
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
    "no_scada" (no UIGF)/"unreadable"/"unchecked".
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


def _selectable_duids(plants_df: "pd.DataFrame", fuel_tech: str) -> list:
    if plants_df is None or plants_df.empty:
        return []
    df = plants_df[plants_df["fuel_tech"] == fuel_tech]
    # Only simulation-ready plants (a complete year of UIGF) are selectable.
    df = df[df["simulation_ready"]]
    return list(df["duid"])


# ── Streamlit render ─────────────────────────────────────────────────────────

@st.cache_data
def _cached_eligible_plants(year: int, fingerprint: tuple) -> "pd.DataFrame":
    return nem_data.list_eligible_plants(year=year, check_whole_year=True)


@st.cache_data
def _cached_operational_plants(years: tuple, fingerprint: tuple) -> "pd.DataFrame":
    return nem_data.plants_operational_for_years(years)


def _operational_fingerprint(years: tuple, cache_dir: Path = nem_data.NEM_CACHE_DIR) -> tuple:
    """Cache-invalidation token for the operational-plants frame: the sorted
    selected years plus the mtimes of the plant-years manifest and registry, so
    a republished manifest invalidates the cache even when the year selection is
    unchanged.
    """
    files = [nem_data.plant_years_path(cache_dir), nem_data.registry_path(cache_dir)]
    mtimes = tuple(f.stat().st_mtime_ns for f in files if f.exists())
    return (tuple(sorted(int(y) for y in years)), mtimes)


def _plants_for_map(eligible: "pd.DataFrame", operational: "pd.DataFrame") -> "pd.DataFrame":
    """Combine the fleet frame with the multi-year operational subset.

    Every row in `operational` is, by construction, fully operational in ALL
    selected years: tag it ``data_status='ready'`` / ``simulation_ready=True``
    so it gets a solid tech-colour marker and becomes selectable, and carry its
    ``mean_cuf_selected_years`` through for the tooltip. Rows only in `eligible`
    stay exactly as the single-year whole-year check reported them (grey dashed
    markers, not selectable).

    When `operational` is empty (manifest not yet published, or an empty year
    selection), the frame is returned unchanged so the single-year eligibility
    cache still drives the picker -- do not leave the map with zero plants.
    """
    if operational is None or operational.empty:
        return eligible
    df = eligible.copy()
    op_duids = set(operational["duid"])
    mask = df["duid"].isin(op_duids)
    df.loc[~mask, "data_status"] = "unchecked"
    df.loc[~mask, "simulation_ready"] = False
    df.loc[mask, "data_status"] = "ready"
    df.loc[mask, "simulation_ready"] = True
    cuf_map = operational.drop_duplicates("duid").set_index("duid")["mean_cuf_selected_years"]
    df["mean_cuf_selected_years"] = df["duid"].map(cuf_map)
    return df


def _label_for_duid(duid: str, plants_df: "pd.DataFrame") -> str:
    if not duid:
        return "(none)"
    row = plants_df.loc[plants_df["duid"] == duid]
    if row.empty:
        return duid
    return _plant_label(row.iloc[0])


def _use_disabled(selected_years, filtered, wind_duid, pv_duid) -> bool:
    """The \"Use these plants\" button is disabled when no years are selected,
    when no plant is chosen, or when any chosen plant is not simulation-ready."""
    if not selected_years:
        return True
    duids = [d for d in (wind_duid, pv_duid) if d]
    if not duids:
        return True
    for duid in duids:
        rows = filtered.loc[filtered["duid"] == duid]
        if rows.empty or not bool(rows.iloc[0]["simulation_ready"]):
            return True
    return False


def render() -> None:
    st.title("📡 Get Data")
    head = st.columns([6, 1])
    with head[0]:
        st.markdown(
            "Pick real Australian wind and/or solar plants to drive the optimiser. "
            "Generation profiles are AEMO UIGF (unconstrained availability) at "
            "native 5-minute resolution."
        )
    with head[1]:
        with st.popover("❓ UIGF", width="stretch"):
            st.markdown(UIGF_EXPLAINER)

    cols = st.columns([1, 3])
    with cols[0]:
        if st.button("🔄 Re-scan cache", key="nm_rescan"):
            st.cache_data.clear()
            st.rerun()

    current = state.get_scenario()
    if current is None:
        from ppa.scenario import BASE_SCENARIO
        current = BASE_SCENARIO

    # ── Year range slider ──────────────────────────────────────────────────────
    years_available = nem_data.available_years()
    if not years_available:
        years_available = [nem_data.DEFAULT_YEAR]
        st.caption(
            "No multi-year availability manifest is published yet, so only "
            f"{nem_data.DEFAULT_YEAR} (the single shipped year) is available. "
            "Once the manifest is published the full range will be offered here."
        )
    lo, hi = st.slider(
        "Historical years to use",
        min_value=min(years_available),
        max_value=max(years_available),
        value=(max(years_available) - 4, max(years_available)),
        key="nm_year_range",
        help="The dispatch simulation cycles through these years in chronological "
             "order, repeating, for as many simulation years as you run.",
    )
    selected_years = tuple(y for y in years_available if lo <= y <= hi)

    # ── Snapshot resolution ────────────────────────────────────────────────────
    resolution_labels = list(NEM_RESOLUTION_MINUTES)
    default_resolution = int(getattr(current, "nem_resolution_minutes", 60))
    default_label = next(
        (k for k, v in NEM_RESOLUTION_MINUTES.items() if v == default_resolution),
        "1 hour",
    )
    resolution_label = st.selectbox(
        "Snapshot resolution",
        options=resolution_labels,
        index=resolution_labels.index(default_label),
        key="nm_resolution",
        help="The resolution the generation/price series are downsampled to for "
             "the LP. The cache always stores native 5-minute data.",
    )
    resolution_minutes = int(NEM_RESOLUTION_MINUTES[resolution_label])
    if resolution_minutes < 60:
        st.caption(
            f"Sub-hourly resolution multiplies LP size and memory by "
            f"60/{resolution_minutes} — 15 and 5 minutes are only practical for "
            f"short simulation horizons."
        )

    # ── Capacity-sizing year ───────────────────────────────────────────────────
    sizing_year = int(getattr(current, "capacity_sizing_year", nem_data.DEFAULT_YEAR))
    if bool(getattr(current, "optimise_capacity", False)):
        sizing_options = list(selected_years)
        sizing_index = (
            sizing_options.index(sizing_year)
            if sizing_year in sizing_options
            else len(sizing_options) - 1
        )
        sizing_year = st.selectbox(
            "Capacity-sizing year",
            options=sizing_options,
            index=sizing_index,
            key="nm_sizing_year",
        )
        st.caption(
            "The capacity optimisation solves against this single year. The "
            "dispatch simulation still uses all selected years."
        )

    # ── Plant list driven by the manifest ──────────────────────────────────────
    fallback_year = selected_years[-1] if selected_years else nem_data.DEFAULT_YEAR
    try:
        eligible = _cached_eligible_plants(
            fallback_year, nem_data.cache_fingerprint(fallback_year)
        )
        operational_plants = _cached_operational_plants(
            selected_years, _operational_fingerprint(selected_years)
        )
    except FileNotFoundError:
        st.error(
            "NEM plant registry not found. Run `python scripts/fetch_nem_plant_registry.py` "
            "in a non-sandboxed environment and copy the output into "
            "`data/cache/nem/registry/nem_plant_registry.parquet`."
        )
        return

    plants_df = _plants_for_map(eligible, operational_plants)

    status = cached_cache_status(fallback_year)

    with st.expander("**NEM cache status**", expanded=(status["n_simulation_ready"] == 0)):
        c = st.columns(4)
        c[0].metric("Years available", len(years_available))
        c[1].metric("Plants operational (all years)", len(operational_plants))
        c[2].metric("Simulation-ready (single year)", status["n_simulation_ready"])
        c[3].metric("Price regions cached", f"{len(status['price_regions_cached'])}/{len(nem_data.NEM_REGIONS)}")

        if status["missing_price_regions"]:
            st.caption(f"Missing price regions: {', '.join(status['missing_price_regions'])}")

        if status["n_scada_cached"] == 0:
            st.warning(
                "No generation/price data cached yet. Run the acquisition scripts in a "
                "non-sandboxed environment with network access, then copy the output "
                "into `data/cache/nem/{availability,price}/`:"
            )
            st.code(
                f"python scripts/fetch_nem_availability.py --year {fallback_year}\n"
                f"python scripts/fetch_nem_scada_prices.py --year {fallback_year}  # prices",
                language="bash",
            )

    if plants_df.empty:
        st.info("No plants match the current filters.")
        return

    # ── Filters ───────────────────────────────────────────────────────────────
    regions_present = sorted(plants_df["region"].unique())
    region_filter = st.multiselect(
        "Region filter", options=regions_present, default=regions_present, key="nm_region_filter",
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
            if row["simulation_ready"]:
                if row["fuel_tech"] == "Wind":
                    st.session_state["nm_wind_duid"] = clicked_duid
                elif row["fuel_tech"] == "Solar":
                    st.session_state["nm_pv_duid"] = clicked_duid

    wind_options = [""] + _selectable_duids(filtered, "Wind")
    pv_options = [""] + _selectable_duids(filtered, "Solar")

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
    covered_price_years = price_years_covered(price_region, selected_years)
    missing_price_years = [y for y in selected_years if y not in covered_price_years]
    if missing_price_years:
        st.warning(
            f"No cached price data for region {price_region} in "
            f"{', '.join(map(str, missing_price_years))}. Run "
            f"`python scripts/fetch_nem_scada_prices.py --year {fallback_year}` to fetch it."
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
            "🟢 Wind · 🟡 Solar · solid = operational in all selected years · "
            "grey dashed = not fully operational in every selected year (not selectable). "
            "Click a marker or use the selectboxes above (selectboxes are authoritative)."
        )
        st.caption(
            "Marker tooltip: CUF = energy ÷ (nameplate × hours-in-year, UIGF, AC); "
            "with a multi-year range the mean across the selected years is shown. "
            "“1st power” = registry commissioning date, “first 2025 output” = first "
            "sustained UIGF availability, “—” = not available."
        )

    # ── Native 5-min CF inspection for the selected plants ──────────────────────
    for label, duid in (("Wind", wind_duid), ("Solar", pv_duid)):
        if not duid:
            continue
        row = filtered.loc[filtered["duid"] == duid]
        if row.empty or not bool(row.iloc[0]["simulation_ready"]):
            continue
        with st.expander(f"{label} plant CF — {duid}", expanded=False):
            cf = nem_data.capacity_factor_for_duid(duid, year=fallback_year, registry=filtered)
            monthly = cf.groupby(cf.index.month).mean()
            st.line_chart(cf.iloc[:: max(1, len(cf) // 2000)])
            st.bar_chart(monthly)

    # ── Action buttons ───────────────────────────────────────────────────────
    use_disabled = _use_disabled(selected_years, filtered, wind_duid, pv_duid)

    if st.button(
        "✅ Use these plants", type="primary", width="stretch",
        key="nm_use_plants", disabled=use_disabled,
    ):
        from ppa.data import remote_cache

        duids = [d for d in (wind_duid, pv_duid) if d]
        missing = remote_cache.missing_plant_years(duids, selected_years)
        if missing:
            bar = st.progress(0.0, text="Downloading generation data ...")
            try:
                remote_cache.ensure_plant_years(
                    duids, selected_years,
                    progress=lambda f, msg: bar.progress(f, text=msg),
                )
                remote_cache.ensure_price_years([price_region], selected_years)
            except remote_cache.RemoteFetchError as exc:
                st.error(f"Could not download the plant data: {exc}")
                return
        updated = dataclasses.replace(
            current,
            data_source="nem_map",
            nem_pv_duid=pv_duid,
            nem_wind_duid=wind_duid,
            nem_price_region=price_region,
            nem_years=selected_years,
            capacity_sizing_year=int(sizing_year),
            nem_resolution_minutes=int(resolution_minutes),
        )
        state.set_scenario(updated)
        state.set_nem_selection({
            "pv_duid": pv_duid, "wind_duid": wind_duid,
            "price_region": price_region,
            "years": list(selected_years),
            "capacity_sizing_year": int(sizing_year),
            "resolution_minutes": int(resolution_minutes),
        })
        state.clear_custom_upload()
        state.clear_run_outputs()
        st.rerun()

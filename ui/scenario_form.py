from __future__ import annotations

import dataclasses

import pandas as pd
import streamlit as st

from ppa.data_loader import coerce_chosen_day
from ppa.industrial_profiles import PROFILE_INFO, PROFILE_KEYS
from ppa.scenario import Scenario
from ui import state

max_cap_per_technology = 500
max_bes_hours = 8

# PPA offtake load (MW) number_input bounds. Raised well above
# max_cap_per_technology-scale single-tech limits since a co-optimized
# wind+solar+BESS portfolio (each individually up to several thousand MW via
# the sizing max-build inputs) can plausibly serve a load in the same range,
# and a custom-CSV upload's peak MW must always fit under this ceiling or the
# Case Setup tab crashes with StreamlitValueAboveMaxError on the next render.
PPALOAD_MW_MAX = 10_000.0


def _seed_aer_applied_from_scenario(
    session_state, cal_forward_source: str, cal_forward_price: float, cal_forward_note: str,
) -> None:
    """If `session_state` has no `_sf_aer_applied` yet and the ALREADY-SAVED
    scenario's own provenance says an AER quote is active
    (`cal_forward_source == "aer_indicative"`), re-seed `_sf_aer_applied` from
    the scenario's `cal_forward_price`/`cal_forward_note`.

    Must run before `_apply_pending_aer` / the `cal_forward_price` widget.
    Without this, `_sf_aer_applied` (which `ui.state.set_scenario` pops on
    every save) would have no record of the active AER quote on the very next
    render, so the next "Apply changes" click -- even with no other changes --
    would silently overwrite `cal_forward_source` back to "manual" and wipe
    `cal_forward_note`.

    Pure w.r.t. Streamlit, like `_apply_pending_aer` -- works against any
    mutable-mapping-like object.
    """
    from ppa.data import aer_futures

    if cal_forward_source == aer_futures.SOURCE_AER:
        session_state.setdefault(
            "_sf_aer_applied",
            {"price_aud_mwh": float(cal_forward_price), "disclaimer": cal_forward_note},
        )


def _resolve_aer_provenance(session_state, cal_forward_price: float) -> tuple[str, str]:
    """Decide `(cal_forward_source, cal_forward_note)` for this render, given
    the current `cal_forward_price` widget value and any `_sf_aer_applied`
    state.

    If the widget value still matches the previously-applied AER quote,
    provenance stays "aer_indicative". Otherwise a manual edit has been
    detected: `_sf_aer_applied` is POPPED entirely (not just ignored for this
    render) so that later re-typing the same numeric value is correctly
    treated as a fresh manual entry rather than "the AER quote is still
    active".

    Pure w.r.t. Streamlit -- works against any mutable-mapping-like object.
    """
    from ppa.data import aer_futures

    applied = session_state.get("_sf_aer_applied")
    if applied is not None and abs(float(cal_forward_price) - float(applied["price_aud_mwh"])) < 1e-9:
        return aer_futures.SOURCE_AER, applied["disclaimer"]
    if applied is not None:
        session_state.pop("_sf_aer_applied", None)
    return aer_futures.SOURCE_MANUAL, ""


def _apply_pending_aer(session_state) -> bool:
    """Pop `_sf_aer_pending` from `session_state` and seed `sf_cal_forward_price` +
    `_sf_aer_applied` if present. Must run before the `cal_forward_price` widget
    renders. Returns True iff a pending quote was applied. Idempotent -- a
    second call with nothing pending is a no-op.

    Pure w.r.t. Streamlit: works against any mutable-mapping-like object
    (plain dict in tests, `st.session_state` at runtime) so it's testable
    without a running Streamlit app.
    """
    pending = session_state.pop("_sf_aer_pending", None)
    if pending is None:
        return False
    session_state["sf_cal_forward_price"] = float(pending["price_aud_mwh"])
    session_state["_sf_aer_applied"] = pending
    return True


def render_scenario_form(initial: Scenario) -> Scenario:
    """Render all scenario controls and return a new Scenario from widget values."""
    st.subheader("Feature toggles")

    cols = st.columns(4)
    
    include_bess = cols[0].toggle("Include BESS", value=initial.include_bess, key="sf_include_bess")
    enable_market_buy = cols[1].toggle("Enable market buy", value=initial.enable_market_buy, key="sf_enable_market_buy")
    enable_market_sell = cols[2].toggle("Enable market sell", value=initial.enable_market_sell, key="sf_enable_market_sell")
    enable_shortfall = cols[3].toggle("Enable shortfall allowance", value=initial.enable_shortfall, key="sf_enable_shortfall")
    
    cols = st.columns(4)
    enable_penalty = cols[0].toggle("Enable penalty regime", value=initial.enable_penalty, key="sf_enable_penalty")
    run_financial_analysis = cols[1].toggle("Run financial analysis", value=initial.run_financial_analysis, key="sf_run_financial_analysis")
    optimize_capacity = cols[2].toggle(
        "Co-optimize capacities & dispatch",
        value=initial.optimize_capacity,
        key="sf_optimize_capacity",
        help=(
            "Let PyPSA size wind, solar and BESS together with dispatch "
            "(least-cost portfolio to serve the PPA). The fixed MW values below "
            "are ignored; set per-technology max build limits instead."
        ),
    )

    with st.expander("Portfolio assets", expanded=True):
        if optimize_capacity:
            st.info(
                "⚡ **Capacity co-optimization is ON** — the sliders below are ignored. "
                "The optimizer sizes each technology up to its max build limit; "
                "BESS duration is fixed at the MWh/MW ratio below."
            )
            cols = st.columns(4)
            max_build_wind_mw = cols[0].number_input(
                "Max wind build (MW)", 0.0, 10_000.0, float(initial.max_build_wind_mw),
                50.0, key="sf_max_build_wind",
            )
            max_build_pv_mw = cols[1].number_input(
                "Max solar build (MW)", 0.0, 10_000.0, float(initial.max_build_pv_mw),
                50.0, key="sf_max_build_pv",
            )
            max_build_bess_mw = cols[2].number_input(
                "Max BESS build (MW)", 0.0, 10_000.0, float(initial.max_build_bess_mw),
                50.0, key="sf_max_build_bess",
            )
            _res_options = [1, 2, 3, 4, 6]
            _res_idx = (
                _res_options.index(int(initial.sizing_resolution_h))
                if int(initial.sizing_resolution_h) in _res_options
                else _res_options.index(3)
            )
            sizing_resolution_h = cols[3].selectbox(
                "Sizing LP resolution (h)", _res_options, index=_res_idx,
                key="sf_sizing_resolution",
                help=(
                    "Time resolution of the capacity-sizing LP only. Coarser "
                    "blocks (e.g. 3h) solve much faster and use less memory; the "
                    "sized portfolio is then always re-simulated at hourly "
                    "resolution for dispatch and financials."
                ),
            )
        else:
            max_build_wind_mw = initial.max_build_wind_mw
            max_build_pv_mw = initial.max_build_pv_mw
            max_build_bess_mw = initial.max_build_bess_mw
            sizing_resolution_h = initial.sizing_resolution_h

        cols = st.columns(4)
        onsw_mw = cols[0].slider("Onshore wind (MW)", 0, max_cap_per_technology, int(initial.onsw_mw), step=10, key="sf_onsw_mw",
                                 disabled=optimize_capacity)
        pv_mw = cols[1].slider("Solar PV (MWac)", 0, max_cap_per_technology, int(initial.pv_mw), step=10, key="sf_pv_mw",
                               disabled=optimize_capacity)
        bess_mw = cols[2].slider(
            "BESS power (MW)", 0, max_cap_per_technology, int(initial.bess_mw), step=10,
            key="sf_bess_mw", disabled=optimize_capacity,
        )
        bess_mwh = cols[3].slider(
            "BESS energy (MWh)", 0, max_cap_per_technology*max_bes_hours, int(initial.bess_mwh), step=20,
            key="sf_bess_mwh",
            help="With co-optimization on, only the MWh/MW ratio (duration) is used." if optimize_capacity else None,
        )

    with st.expander("PPA contract terms", expanded=True):
        cols = st.columns(4)
        ppaload_mw = cols[0].number_input("PPA offtake load (MW)", min_value=1.0, max_value=PPALOAD_MW_MAX,
                                           value=float(initial.ppaload_mw), step=10.0, key="sf_ppaload_mw",
                                           help="Peak rated MW. The load profile shapes how much of this is demanded each hour.")
        ppa_price = cols[1].number_input("PPA tariff (A$/MWh)", min_value=1.0, max_value=500.0,
                                          value=float(initial.ppa_price), step=5.0, key="sf_ppa_price")
        required_delivery_share = cols[2].slider(
            "Required delivery share (%)", 50, 100, int(initial.required_delivery_share * 100),
            step=1, format="%d%%",
            help="Fraction of total contracted load that must be delivered on average.",
            key="sf_required_delivery_share",
        ) / 100.0
        pen_mult = cols[3].number_input(
            "Penalty multiplier (×tariff)", min_value=1.0, max_value=5.0,
            value=float(initial.pen_mult), step=0.1,
            key="sf_pen_mult",
        )

        # ── Load profile selector ─────────────────────────────────────────────
        st.markdown("**Offtaker load profile**")
        _custom_active = initial.data_source == "custom_csv"
        _profile_labels = [f"{PROFILE_INFO[k]['icon']} {PROFILE_INFO[k]['label']}" for k in PROFILE_KEYS]
        _current_idx = PROFILE_KEYS.index(initial.load_profile) if initial.load_profile in PROFILE_KEYS else 0

        cols = st.columns([1, 3])
        _selected_label = cols[0].selectbox(
            "Profile type",
            options=_profile_labels,
            index=_current_idx,
            key="sf_load_profile",
            label_visibility="collapsed",
            disabled=_custom_active,
        )
        load_profile = PROFILE_KEYS[_profile_labels.index(_selected_label)]
        if _custom_active:
            cols[1].caption(
                "Overridden by the active **custom CSV upload**'s `ts_LoadMW` column. "
                "Go to the **Custom Data** tab and clear the upload to re-enable a synthetic profile."
            )
        else:
            _info = PROFILE_INFO[load_profile]
            cols[1].caption(
                f"**Typical load factor: {_info['typical_lf']}** — {_info['description']}"
            )

    with st.expander("Market interaction", expanded=True):
        cols = st.columns(4)
        market_buy_share = cols[0].slider(
            "Market buy cap (% of delivery)", 0, 100,
            int(initial.market_buy_share * 100), step=1, format="%d%%",
            key="sf_market_buy_share",
        ) / 100.0
        market_spread = cols[1].number_input(
            "Bid-offer spread (A$/MWh)", min_value=0.0, max_value=10.0,
            value=float(initial.market_spread), step=0.05, key="sf_market_spread",
        )

    with st.expander("Financial assumptions", expanded=True):
        cols = st.columns(4)
        wind_capex_per_kw = cols[0].number_input("Wind CAPEX ($/kW)", 500.0, 5000.0,
                                                   float(initial.wind_capex_per_kw), 50.0, key="sf_wind_capex")
        pv_capex_per_kw = cols[1].number_input("PV CAPEX ($/kW)", 200.0, 3000.0,
                                              float(initial.pv_capex_per_kw), 50.0, key="sf_pv_capex")
        bess_capex_per_kwh = cols[2].number_input("BESS CAPEX ($/kWh)", 100.0, 2000.0,
                                                float(initial.bess_capex_per_kwh), 25.0,
                                                key="sf_bess_capex")
        opex_rate = cols[3].number_input("Annual OPEX (% of CAPEX)", 0.5, 10.0,
                                       float(initial.opex_rate * 100), 0.1, format="%.1f",
                                       key="sf_opex_rate") / 100.0
        cols = st.columns(4)
        discount_rate = cols[0].number_input("Discount rate / WACC (%)", 1.0, 30.0,
                                           float(initial.discount_rate * 100), 0.5, format="%.1f",
                                           key="sf_discount_rate") / 100.0
        target_irr = cols[1].number_input("Target IRR (%)", 1.0, 40.0,
                                        float(initial.target_irr * 100), 0.5, format="%.1f",
                                        key="sf_target_irr") / 100.0
        devex_pct = cols[2].number_input("Devex (% of CAPEX)", 0.0, 50.0,
                                       float(initial.devex_pct_of_capex * 100), 0.5, format="%.1f",
                                       key="sf_devex_pct")
        project_life_yrs = cols[3].number_input("Project life (years)", 5, 40,
                                            int(initial.project_life_yrs), 1, key="sf_project_life")

    with st.expander("Project Locations & Market Zone", expanded=True):
        from ppa.data.bidding_zones import SUPPORTED_ZONES, bidding_zone_for, zone_label

        # Seed the coordinate widgets once from the scenario; afterwards their
        # session-state keys are the single source of truth so a map click can
        # update them (widget state must be written BEFORE the widget renders).
        _seed = {
            "sf_lat": float(initial.lat),
            "sf_lon": float(initial.lon),
            "sf_pv_lat": float(initial.pv_lat if initial.pv_lat is not None else initial.lat),
            "sf_pv_lon": float(initial.pv_lon if initial.pv_lon is not None else initial.lon),
            "sf_wind_lat": float(initial.wind_lat if initial.wind_lat is not None else initial.lat),
            "sf_wind_lon": float(initial.wind_lon if initial.wind_lon is not None else initial.lon),
        }
        for _k, _v in _seed.items():
            st.session_state.setdefault(_k, _v)

        # Apply a map click from the previous rerun (st_folium stores its state
        # under its widget key). Rounded to 0.01° — the CF cache granularity.
        _click = (st.session_state.get("sf_loc_map") or {}).get("last_clicked")
        if _click:
            _sig = (round(_click["lat"], 6), round(_click["lng"], 6))
            if st.session_state.get("_sf_handled_click") != _sig:
                st.session_state["_sf_handled_click"] = _sig
                _target = st.session_state.get("sf_map_target", "🔵 Offtaker")
                # A stale PV/Wind target (its "own location" toggle since
                # switched off) falls back to placing the offtaker.
                if _target == "🟡 PV" and not st.session_state.get("sf_pv_separate", False):
                    _target = "🔵 Offtaker"
                if _target == "🟢 Wind" and not st.session_state.get("sf_wind_separate", False):
                    _target = "🔵 Offtaker"
                _target_keys = {
                    "🔵 Offtaker": ("sf_lat", "sf_lon"),
                    "🟡 PV": ("sf_pv_lat", "sf_pv_lon"),
                    "🟢 Wind": ("sf_wind_lat", "sf_wind_lon"),
                }[_target]
                st.session_state[_target_keys[0]] = round(_click["lat"], 2)
                st.session_state[_target_keys[1]] = round(_click["lng"], 2)

        cols = st.columns([1, 1, 2])
        with cols[0]:
            st.markdown("**Offtaker (consumer)**")
            lat = st.number_input(
                "Latitude", -90.0, 90.0, step=0.01, format="%.2f", key="sf_lat",
                help="Decimal degrees N. The offtaker location sets the bidding zone "
                     "whose ENTSO-E day-ahead prices are used.",
            )
            lon = st.number_input(
                "Longitude", -180.0, 180.0, step=0.01, format="%.2f", key="sf_lon",
                help="Decimal degrees E.",
            )
            auto_zone = bidding_zone_for(lat, lon)
            _zone_options = ["auto"] + SUPPORTED_ZONES
            _initial_zone = initial.bidding_zone_override or "auto"
            _zone_idx = _zone_options.index(_initial_zone) if _initial_zone in _zone_options else 0
            zone_choice = st.selectbox(
                "Bidding zone (prices)",
                options=_zone_options,
                index=_zone_idx,
                format_func=lambda z: (
                    f"Auto — {auto_zone} ({zone_label(auto_zone)})" if z == "auto"
                    else f"{z} ({zone_label(z)})"
                ),
                key="sf_bidding_zone",
                help="Derived from the offtaker location (nearest-zone approximation) — "
                     "override it if the site is close to a zone border.",
            )
            bidding_zone_override = "" if zone_choice == "auto" else zone_choice

            transmission_cost_aud_mwh = st.number_input(
                "Transmission cost (A$/MWh delivered)", 0.0, 200.0,
                float(initial.transmission_cost_aud_mwh), 0.5, format="%.1f",
                key="sf_transmission_cost",
                help="Combined transmission / grid-use charge across all network levels between "
                    "the generation sites and the offtaker, applied to every MWh delivered under "
                    "the PPA. Enter the total (combined) value — it is charged regardless of "
                    "whether assets and offtaker are in the same bidding zone or different ones.",
            )

        with cols[1]:
            st.markdown("**Generation assets**")
            pv_separate = st.toggle(
                "PV at its own location", value=initial.pv_lat is not None, key="sf_pv_separate",
            )
            if pv_separate:
                pv_lat = st.number_input(
                    "PV latitude", -90.0, 90.0, step=0.01, format="%.2f", key="sf_pv_lat", value=st.session_state.get("sf_pv_lat", initial.pv_lat if initial.pv_lat is not None else lat)
                )
                pv_lon = st.number_input(
                    "PV longitude", -180.0, 180.0, step=0.01, format="%.2f", key="sf_pv_lon", value=st.session_state.get("sf_pv_lon", initial.pv_lon if initial.pv_lon is not None else lon)
                )
            else:
                pv_lat, pv_lon = None, None

            wind_separate = st.toggle(
                "Wind at its own location", value=initial.wind_lat is not None, key="sf_wind_separate",
            )
            if wind_separate:
                wind_lat = st.number_input(
                    "Wind latitude", -90.0, 90.0, step=0.01, format="%.2f", key="sf_wind_lat", value=st.session_state.get("sf_wind_lat", initial.wind_lat if initial.wind_lat is not None else lat)
                )
                wind_lon = st.number_input(
                    "Wind longitude", -180.0, 180.0, step=0.01, format="%.2f", key="sf_wind_lon", value=st.session_state.get("sf_wind_lon", initial.wind_lon if initial.wind_lon is not None else lon)
                )
            else:
                wind_lat, wind_lon = None, None

        with cols[2]:
            _markers = [("🔵 Offtaker", lat, lon, "#1565C0")]
            if pv_separate:
                _markers.append(("🟡 PV", pv_lat, pv_lon, "#F9A825"))
            if wind_separate:
                _markers.append(("🟢 Wind", wind_lat, wind_lon, "#2E7D32"))

            try:
                import folium
                from streamlit_folium import st_folium
            except ImportError:
                st.map(
                    pd.DataFrame(
                        [{"lat": la, "lon": lo, "color": c} for _, la, lo, c in _markers]
                    ),
                    zoom=5, height=300, color="color",
                )
                st.caption(
                    "🔵 Offtaker · 🟡 PV · 🟢 Wind — install `streamlit-folium` "
                    "to place locations by clicking the map."
                )
            else:
                _target_options = [name for name, *_ in _markers]
                if st.session_state.get("sf_map_target") not in _target_options:
                    st.session_state["sf_map_target"] = _target_options[0]
                st.radio(
                    "Clicking the map places:", _target_options, horizontal=True,
                    key="sf_map_target",
                    help="Choose which location a map click sets, then click the map. "
                         "Coordinates snap to 0.01°.",
                )
                fmap = folium.Map(location=(lat, lon), zoom_start=5, tiles="CartoDB positron")
                for name, la, lo, color in _markers:
                    folium.CircleMarker(
                        (la, lo), radius=9, color=color, fill=True,
                        fill_color=color, fill_opacity=0.9, tooltip=name,
                    ).add_to(fmap)
                st_folium(
                    fmap, height=320, use_container_width=True,
                    key="sf_loc_map", returned_objects=["last_clicked"],
                )

    with st.expander("Simulation", expanded=True):
        cols = st.columns(4)
        simulation_years = int(cols[0].number_input(
            "Simulation years", 1, 40, int(initial.simulation_years), 1, key="sf_sim_years",
            help="1 = single full-year run; >1 = multi-year parallel simulation.",
        ))
        first_sim_year = int(cols[1].number_input(
            "First simulation year", 2024, 2040, int(initial.first_sim_year), 1,
            key="sf_first_sim_year",
        ))
        price_escalation_rate = cols[2].number_input(
            "Price escalation (%/yr)", 0.0, 10.0,
            float(initial.price_escalation_rate * 100), 0.1, format="%.1f",
            key="sf_escalation",
            help="Annual compound escalation applied to 2024 ENTSO-E base prices.",
        ) / 100.0

        st.caption("Technology degradation (compound annual, applied from year 2 onward)")
        cols = st.columns(4)
        pv_degradation_rate = cols[0].number_input(
            "PV (%/yr)", 0.0, 5.0, float(initial.pv_degradation_rate * 100),
            0.05, format="%.2f", key="sf_pv_deg",
        ) / 100.0
        wind_degradation_rate = cols[1].number_input(
            "Wind (%/yr)", 0.0, 5.0, float(initial.wind_degradation_rate * 100),
            0.05, format="%.2f", key="sf_wind_deg",
        ) / 100.0
        bess_degradation_rate = cols[2].number_input(
            "BESS (%/yr)", 0.0, 10.0, float(initial.bess_degradation_rate * 100),
            0.1, format="%.1f", key="sf_bess_deg",
        ) / 100.0

    with st.expander("Market data source", expanded=False):
        from ppa.data import nem_data
        from ppa.scenario import default_data_source
        from ui.nem_cache_status import cached_cache_status

        _is_map_or_custom = initial.data_source in ("nem_map", "custom_csv")
        if _is_map_or_custom:
            st.info(
                f"Data source is currently **{initial.data_source}**, set from the "
                f"{'NEM Plant Map' if initial.data_source == 'nem_map' else 'Custom Data'} tab. "
                "Choose 'european' or 'nem_default' below to override it here."
            )

        _status = cached_cache_status(int(initial.nem_year))
        _price_cache_present = bool(_status.get("price_regions_cached"))
        _has_nem_duid = bool(initial.nem_pv_duid or initial.nem_wind_duid)
        _seed_choice = default_data_source(initial.data_source, _price_cache_present, _has_nem_duid)
        if _seed_choice not in ("european", "nem_default"):
            _seed_choice = "european"
        st.session_state.setdefault("sf_data_source", _seed_choice)
        st.session_state.setdefault("_sf_data_source_touched", False)

        cols = st.columns([2, 1, 1])
        data_source_radio = cols[0].radio(
            "Data source", options=["european", "nem_default"],
            key="sf_data_source", horizontal=True,
            help=(
                "Legacy European (ENTSO-E day-ahead prices + renewables.ninja CFs) vs. "
                "the NEM 2025 default (real NEM regional spot prices). A plant selected "
                "on the NEM Plant Map tab, or an active Custom Data upload, stays "
                "authoritative until you explicitly pick a value here."
            ),
        )
        if data_source_radio != _seed_choice:
            st.session_state["_sf_data_source_touched"] = True
        if _is_map_or_custom and not st.session_state["_sf_data_source_touched"]:
            data_source = initial.data_source
        else:
            data_source = data_source_radio

        def _region_label(r):
            return f"{r} ✓" if r in _status.get("price_regions_cached", []) else r

        _region_options = nem_data.NEM_REGIONS
        _region_idx = (
            _region_options.index(initial.nem_price_region)
            if initial.nem_price_region in _region_options else 0
        )
        nem_price_region = cols[1].selectbox(
            "NEM price region", options=_region_options,
            index=_region_idx, format_func=_region_label,
            key="sf_nem_price_region",
        )

        _cached_years = nem_data.list_cached_price_years()
        _year_options = sorted(set(_cached_years) | {int(initial.nem_year), nem_data.DEFAULT_YEAR})
        _year_idx = _year_options.index(int(initial.nem_year)) if int(initial.nem_year) in _year_options else 0
        nem_year = cols[2].selectbox(
            "NEM data year", options=_year_options, index=_year_idx, key="sf_nem_year",
        )

    with st.expander("Counterfactual sourcing", expanded=True):
        cols = st.columns(4)
        enable_counterfactual = cols[0].toggle(
            "Compare to counterfactual strategies",
            value=initial.enable_counterfactual,
            key="sf_enable_counterfactual",
            help="Compute spot-only and CAL Y+1 forward costs for the offtaker after each run.",
        )
        _seed_aer_applied_from_scenario(
            st.session_state, initial.cal_forward_source, initial.cal_forward_price, initial.cal_forward_note,
        )
        st.session_state.setdefault("sf_cal_forward_price", float(initial.cal_forward_price))
        _apply_pending_aer(st.session_state)
        cal_forward_price = cols[1].number_input(
            "CAL Y+1 forward price (A$/MWh)",
            min_value=0.0, max_value=500.0,
            step=5.0,
            key="sf_cal_forward_price",
            help="Indicative baseload forward price for the next calendar year — use the "
                 "AER quote below or enter your own estimate.",
        )
        cal_hedge_fraction = cols[2].slider(
            "Hedge fraction (%)", 0, 100,
            int(initial.cal_hedge_fraction * 100),
            step=5, format="%d%%",
            key="sf_cal_hedge_fraction",
            help="Share of load hedged at CAL Y+1; remainder sourced at spot.",
        ) / 100.0

        st.markdown("**AER indicative hedge price**")
        from ppa.data import aer_futures

        _aer_year = int(nem_year)
        if not aer_futures.has_futures_cache(_aer_year):
            st.caption(
                f"No cached AER base-futures data for {_aer_year}. Run "
                f"`python scripts/fetch_aer_futures.py --year {_aer_year}` in a "
                "non-sandboxed environment and copy the output parquet into "
                "`data/cache/nem/hedge/`."
            )
        else:
            try:
                _aer_df = aer_futures.load_aer_base_futures(_aer_year)
                _aer_regions = aer_futures.available_regions(_aer_df)
                _default_aer_region = (
                    initial.nem_price_region if initial.nem_price_region in _aer_regions
                    else (_aer_regions[0] if _aer_regions else aer_futures.DEFAULT_REGION)
                )
                st.session_state.setdefault("sf_aer_region", _default_aer_region)
                _aer_region_idx = (
                    _aer_regions.index(st.session_state["sf_aer_region"])
                    if st.session_state["sf_aer_region"] in _aer_regions else 0
                )
                aer_cols = st.columns([1, 2, 1])
                aer_region = aer_cols[0].selectbox(
                    "AER region", options=_aer_regions, index=_aer_region_idx, key="sf_aer_region",
                )
                _aer_quarters_available = aer_futures.available_quarters(_aer_df, region=aer_region)
                _stored_quarters = st.session_state.get("sf_aer_quarters")
                if _stored_quarters is not None and any(
                    q not in _aer_quarters_available for q in _stored_quarters
                ):
                    # Stale selection from a previously-selected region (e.g.
                    # one that doesn't have this quarter) -- reset to a fresh,
                    # valid list rather than letting it propagate into
                    # `quarterly_average`'s ValueError below.
                    st.session_state["sf_aer_quarters"] = list(_aer_quarters_available)
                else:
                    st.session_state.setdefault("sf_aer_quarters", list(_aer_quarters_available))
                aer_quarters = aer_cols[1].multiselect(
                    "Quarters", options=_aer_quarters_available, key="sf_aer_quarters",
                )
                if aer_quarters:
                    _aer_avg = aer_futures.quarterly_average(_aer_df, region=aer_region, quarters=aer_quarters)
                    _aer_as_at = aer_futures.latest_as_at(_aer_df, region=aer_region, quarters=aer_quarters)
                    _aer_disclaimer = aer_futures.disclaimer_text(_aer_as_at)
                    st.caption(f"Average: **A${_aer_avg:.2f}/MWh** — {_aer_disclaimer}")
                    if aer_cols[2].button("Use AER indicative average", key="sf_aer_apply"):
                        st.session_state["_sf_aer_pending"] = {
                            "price_aud_mwh": _aer_avg,
                            "disclaimer": _aer_disclaimer,
                        }
                        st.rerun()
                else:
                    st.caption("Select at least one quarter to preview an average.")
            except (FileNotFoundError, ValueError) as exc:
                st.warning(f"Could not load AER futures data: {exc}")

        cal_forward_source, cal_forward_note = _resolve_aer_provenance(st.session_state, cal_forward_price)
        if cal_forward_source == aer_futures.SOURCE_AER:
            st.caption(cal_forward_note)

    with st.expander("Reference day selection", expanded=True):
        cols = st.columns(4)
        # Read-only: the day is reconciled against the user-selected reference
        # period on the Optimisation tab (coerce_chosen_day), so the two can
        # never diverge. Display the coerced day here.
        ts = state.get_timeseries()
        if ts is not None:
            chosen_day = coerce_chosen_day(ts, initial.chosen_day)
        else:
            chosen_day = initial.chosen_day
        cols[0].write(f"Reference day for daily charts: **{chosen_day}**")

    return dataclasses.replace(
        initial,
        optimize_capacity=optimize_capacity,
        max_build_wind_mw=float(max_build_wind_mw),
        max_build_pv_mw=float(max_build_pv_mw),
        max_build_bess_mw=float(max_build_bess_mw),
        sizing_resolution_h=int(sizing_resolution_h),
        include_bess=include_bess,
        enable_market_buy=enable_market_buy,
        enable_market_sell=enable_market_sell,
        enable_shortfall=enable_shortfall,
        enable_penalty=enable_penalty,
        run_financial_analysis=run_financial_analysis,
        enable_counterfactual=enable_counterfactual,
        cal_forward_price=float(cal_forward_price),
        cal_hedge_fraction=float(cal_hedge_fraction),
        cal_forward_source=cal_forward_source,
        cal_forward_note=cal_forward_note,
        data_source=data_source,
        nem_price_region=nem_price_region,
        nem_year=int(nem_year),
        onsw_mw=float(onsw_mw),
        pv_mw=float(pv_mw),
        bess_mw=float(bess_mw) if include_bess else 0.0,
        bess_mwh=float(bess_mwh) if include_bess else 0.0,
        ppaload_mw=float(ppaload_mw),
        load_profile=load_profile,
        ppa_price=float(ppa_price),
        required_delivery_share=float(required_delivery_share),
        pen_mult=float(pen_mult),
        market_buy_share=float(market_buy_share),
        market_spread=float(market_spread),
        wind_capex_per_kw=float(wind_capex_per_kw),
        pv_capex_per_kw=float(pv_capex_per_kw),
        bess_capex_per_kwh=float(bess_capex_per_kwh),
        opex_rate=float(opex_rate),
        devex_pct_of_capex=float(devex_pct) / 100.0,
        discount_rate=float(discount_rate),
        target_irr=float(target_irr),
        project_life_yrs=int(project_life_yrs),
        chosen_day=str(chosen_day),
        lat=float(lat),
        lon=float(lon),
        pv_lat=float(pv_lat) if pv_lat is not None else None,
        pv_lon=float(pv_lon) if pv_lon is not None else None,
        wind_lat=float(wind_lat) if wind_lat is not None else None,
        wind_lon=float(wind_lon) if wind_lon is not None else None,
        bidding_zone_override=bidding_zone_override,
        transmission_cost_aud_mwh=float(transmission_cost_aud_mwh),
        simulation_years=simulation_years,
        first_sim_year=first_sim_year,
        price_escalation_rate=float(price_escalation_rate),
        pv_degradation_rate=float(pv_degradation_rate),
        wind_degradation_rate=float(wind_degradation_rate),
        bess_degradation_rate=float(bess_degradation_rate),
    )

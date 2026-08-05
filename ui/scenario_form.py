from __future__ import annotations

import dataclasses

import streamlit as st

from ppa.data_loader import coerce_chosen_day
from ppa.industrial_profiles import PROFILE_INFO, PROFILE_KEYS
from ppa.scenario import Scenario
from ui import state

max_cap_per_technology = 500
max_bes_hours = 8

# PPA offtake load (MW) number_input bounds. Raised well above
    # max_cap_per_technology-scale single-tech limits since a co-optimised
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


def _default_aer_seed_for_scenario(scenario, cache_dir=None):
    """Return ``(cal_forward_price, cal_forward_source, cal_forward_note)``
    seeded from the AER base-futures cache for ``scenario``'s region/year, or
    ``None`` when no usable cache exists.

    AER data becomes the *default* forward-price seed (W4): when a futures
    cache is present for ``nem_year`` and the user has not yet made a manual
    choice, the forward price is pre-filled from the full-year quarterly
    average with ``cal_forward_source = "aer_indicative"`` so the disclaimer
    and provenance are carried through -- the manual-opt-in click becomes
    unnecessary in the common case. ``None`` keeps the previous "manual"
    default for region/year combinations without cached data.

    Duck-typed ``scenario`` access only (``getattr``), matching
    ``ppa.data.aer_futures.forward_price_for_scenario``.
    """
    from ppa.data import aer_futures

    year = getattr(scenario, "nem_year", aer_futures.DEFAULT_YEAR) or aer_futures.DEFAULT_YEAR
    cache_dir = cache_dir if cache_dir is not None else aer_futures.NEM_CACHE_DIR
    if not aer_futures.has_futures_cache(year, cache_dir=cache_dir):
        return None
    try:
        quote = aer_futures.forward_price_for_scenario(scenario, cache_dir=cache_dir)
    except (FileNotFoundError, ValueError, KeyError):
        return None
    return (quote.price_aud_mwh, aer_futures.SOURCE_AER, quote.disclaimer)


def render_scenario_form(initial: Scenario) -> Scenario:
    """Render all scenario controls and return a new Scenario from widget values."""
    # The single most consequential choice in the app, so it is asked first and
    # in plain language rather than buried among the feature toggles. Whether
    # the optimiser sizes the fleet decides what every control below means.
    st.subheader("Portfolio capacity")
    _capacity_mode = st.radio(
        "How should the fleet be sized?",
        ["🔍 Find optimal capacity", "✏️ Set capacity manually"],
        index=0 if initial.optimise_capacity else 1,
        key="sf_capacity_mode",
        horizontal=True,
        label_visibility="collapsed",
        help=(
            "**Find optimal capacity** lets the optimiser choose the least-cost "
            "wind, solar and storage mix that serves the PPA, within the build "
            "limits you set. **Set capacity manually** simulates a fleet you "
            "specify."
        ),
    )
    optimise_capacity = _capacity_mode.startswith("🔍")

    st.subheader("Feature toggles")

    cols = st.columns(4)
    
    include_bess = cols[0].toggle("Include BESS", value=initial.include_bess, key="sf_include_bess")
    enable_market_buy = cols[1].toggle("Enable market buy", value=initial.enable_market_buy, key="sf_enable_market_buy")
    enable_market_sell = cols[2].toggle("Enable market sell", value=initial.enable_market_sell, key="sf_enable_market_sell")
    enable_shortfall = cols[3].toggle("Enable shortfall allowance", value=initial.enable_shortfall, key="sf_enable_shortfall")
    
    cols = st.columns(4)
    enable_penalty = cols[0].toggle("Enable penalty regime", value=initial.enable_penalty, key="sf_enable_penalty")
    run_financial_analysis = cols[1].toggle("Run financial analysis", value=initial.run_financial_analysis, key="sf_run_financial_analysis")

    with st.expander("Portfolio assets", expanded=True):
        if optimise_capacity:
            st.caption(
                "The optimiser sizes each technology up to the limits below."
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
            enforce_min_delivery = st.checkbox(
                f"Enforce the {initial.required_delivery_share:.0%} delivery share as a "
                "hard constraint",
                value=bool(initial.enforce_min_delivery),
                key="sf_enforce_min_delivery",
                help=(
                    "By default the delivery requirement is only a *price* signal: the "
                    "sizing LP weighs the penalty (PPA price x penalty multiplier) "
                    "against the cost of building, and buys its way out of the SLA "
                    "whenever the penalty is cheaper. At current NEM costs it usually "
                    r"is — penalty A\$126/MWh against a wind LCOE near A\$162/MWh — so "
                    "sized portfolios settle around 50-65% delivery. Tick this to make "
                    "the contractual share binding, so the LP must build enough to meet "
                    "it. If no portfolio within the build caps can, the LP reports "
                    "infeasible and says which limit is blocking."
                ),
            )
            with st.expander("⚙️ Advanced sizing settings", expanded=False):
              _method_labels = {
                  "full_hourly": "Full year hourly",
                  "coarse": "Coarse resolution (legacy)",
                  "tsam": "Typical weeks (tsam)",
              }
              _method_idx = list(_method_labels).index(
                  initial.sizing_method if initial.sizing_method in _method_labels
                  else "full_hourly"
              )
              sizing_method = st.radio(
                  "Sizing representation",
                  list(_method_labels.values()),
                  index=_method_idx,
                  key="sf_sizing_method",
                  help=(
                      "How the sizing LP represents the year. **Full year hourly** is "
                      "exact and the default, but slowest. **Coarse resolution** "
                      "block-averages to the legacy 1-6 h resolution (~4% smaller fleet, "
                      "~8x faster). **Typical weeks (tsam)** clusters the year into "
                      "representative 168-hour weeks and is ~11x faster, landing the fleet "
                      "within ~10% and the BESS within a few percent of the exact answer. "
                      "The sized portfolio is always re-simulated at hourly resolution "
                      "afterwards."
                  ),
                  horizontal=True,
              )
              sizing_method = {v: k for k, v in _method_labels.items()}[sizing_method]
              if sizing_method == "tsam":
                  _n_periods_idx = max(4, min(40, int(initial.sizing_n_periods)))
                  sizing_n_periods = st.slider(
                      "Typical weeks (tsam)", 4, 40, _n_periods_idx,
                      key="sf_sizing_n_periods",
                      help=(
                          "Number of representative **weeks** (168 h each) to cluster the "
                          "year into. Measured against the exact hourly LP: 16 weeks lands "
                          "the fleet within ~10% and the BESS within ~4% in about 15 s; "
                          "26 weeks is more accurate (RMSE 7.2 vs 11.8) but takes ~47 s. "
                          "Weeks beat days because a 168 h period keeps the real "
                          "day-to-day sequence, so the optimiser sees consecutive "
                          "poor-resource days rather than stitched-together fragments."
                      ),
                  )
                  sizing_resolution_h = initial.sizing_resolution_h
              elif sizing_method == "full_hourly":
                  sizing_n_periods = initial.sizing_n_periods
                  sizing_resolution_h = 1
              else:  # coarse
                  _res_options = [1, 2, 3, 4, 6]
                  _res_idx = (
                      _res_options.index(int(initial.sizing_resolution_h))
                      if int(initial.sizing_resolution_h) in _res_options
                      else _res_options.index(3)
                  )
                  sizing_n_periods = initial.sizing_n_periods
                  sizing_resolution_h = st.selectbox(
                      "Sizing LP resolution (h)", _res_options, index=_res_idx,
                      key="sf_sizing_resolution",
                      help=(
                          "Block-average resolution of the capacity-sizing LP only. "
                          "Coarser blocks (e.g. 3h) solve faster and use less memory; "
                          "the sized portfolio is then always re-simulated at hourly "
                          "resolution for dispatch and financials."
                      ),
                  )
            cols = st.columns(4)
            grid_connection = cols[0].text_input(
                "Grid connection limit (MW)",
                value="" if initial.grid_connection_max_mw == float("inf") else str(initial.grid_connection_max_mw),
                key="sf_grid_connection",
                help=(
                    "Hard cap on the transport/connection links in the sizing LP. "
                    "Blank = unlimited. A real NEM project has a physical "
                    "connection limit; when it binds it curtails the build."
                ),
            )
            cols[1].caption("")
            merchant_share = cols[2].slider(
                "Merchant value share", 0.0, 1.0, float(initial.sizing_merchant_value_share),
                0.05, key="sf_merchant_share",
                help=(
                    "Merchant sales earn this fraction of positive historic spot "
                    "in the sizing LP (a haircut for capture-price cannibalisation, "
                    "MLF and curtailment). Negative-price hours are never "
                    "discounted, so the LP curtails rather than sells into them."
                ),
            )
        else:
            max_build_wind_mw = initial.max_build_wind_mw
            max_build_pv_mw = initial.max_build_pv_mw
            max_build_bess_mw = initial.max_build_bess_mw
            sizing_resolution_h = initial.sizing_resolution_h
            sizing_method = initial.sizing_method
            sizing_n_periods = initial.sizing_n_periods
            grid_connection = "" if initial.grid_connection_max_mw == float("inf") else str(initial.grid_connection_max_mw)
            merchant_share = float(initial.sizing_merchant_value_share)
            enforce_min_delivery = bool(initial.enforce_min_delivery)

        # When capacity co-optimisation is on the MW values are ignored entirely,
        # so the sliders are hidden rather than greyed out -- a disabled control
        # that still shows a number invites the reader to believe it matters.
        # Only the BESS duration (MWh/MW) survives, because the sizing LP holds
        # duration fixed and sizes power.
        if optimise_capacity:
            onsw_mw, pv_mw, bess_mw = initial.onsw_mw, initial.pv_mw, initial.bess_mw
            _hours = st.slider(
                "BESS duration (hours)", 1, max_bes_hours, int(round(initial.bess_max_hours)),
                key="sf_bess_hours",
                help="The optimiser sizes BESS power; duration is held fixed at this "
                     "many hours of storage per MW.",
            )
            bess_mwh = float(bess_mw) * _hours if bess_mw else float(_hours)
        else:
            cols = st.columns(4)
            onsw_mw = cols[0].slider("Onshore wind (MW)", 0, max_cap_per_technology, int(initial.onsw_mw), step=10, key="sf_onsw_mw")
            pv_mw = cols[1].slider("Solar PV (MWac)", 0, max_cap_per_technology, int(initial.pv_mw), step=10, key="sf_pv_mw")
            bess_mw = cols[2].slider(
                "BESS power (MW)", 0, max_cap_per_technology, int(initial.bess_mw), step=10,
                key="sf_bess_mw",
            )
            bess_mwh = cols[3].slider(
                "BESS energy (MWh)", 0, max_cap_per_technology*max_bes_hours, int(initial.bess_mwh), step=20,
                key="sf_bess_mwh",
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
        transmission_cost_aud_mwh = cols[2].number_input(
            "Transmission cost (A$/MWh delivered)", 0.0, 200.0,
            float(initial.transmission_cost_aud_mwh), 0.5, format="%.1f",
            key="sf_transmission_cost",
            help="Combined transmission / grid-use charge across all network levels between "
                "the generation sites and the offtaker, applied to every MWh delivered under "
                "the PPA. Enter the total (combined) value — it is charged regardless of "
                "whether assets and offtaker are in the same bidding zone or different ones.",
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
            help="Annual compound escalation applied to base market prices.",
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
        from ui.nem_cache_status import cached_cache_status

        _status = cached_cache_status(int(initial.nem_year))

        def _region_label(r):
            return f"{r} ✓" if r in _status.get("price_regions_cached", []) else r

        cols = st.columns(3)
        _region_options = nem_data.NEM_REGIONS
        _region_idx = (
            _region_options.index(initial.nem_price_region)
            if initial.nem_price_region in _region_options else 0
        )
        nem_price_region = cols[0].selectbox(
            "NEM price region", options=_region_options,
            index=_region_idx, format_func=_region_label,
            key="sf_nem_price_region",
        )

        _cached_years = nem_data.list_cached_price_years()
        _year_options = sorted(set(_cached_years) | {int(initial.nem_year), nem_data.DEFAULT_YEAR})
        _year_idx = _year_options.index(int(initial.nem_year)) if int(initial.nem_year) in _year_options else 0
        nem_year = cols[1].selectbox(
            "NEM data year", options=_year_options, index=_year_idx, key="sf_nem_year",
        )
        use_unconstrained_cf = cols[2].toggle(
            "Use unconstrained output (UIGF)",
            value=bool(initial.use_unconstrained_cf),
            key="sf_use_unconstrained_cf",
            help=(
                "**On by default — this is the correct input for sizing a new build.** "
                "Models each plant's physically available output (AEMO's UIGF, from "
                "DISPATCHLOAD) instead of what it actually sent out.\n\n"
                "The optimiser treats the profile as an upper bound and applies its own "
                "curtailment. The historical SCADA trace is already reduced by another "
                "plant's network constraints and by whatever curtailment *that plant's* "
                "offtake contract incentivised, so using it would count curtailment "
                "twice — and per-plant curtailment ranges from ~0% to 71%, so it cannot "
                "be corrected with a flat factor.\n\n"
                "Switch off only to reproduce older results, or to model taking offtake from "
                "a specific **existing** plant, where its actual metered energy is what "
                "you would receive — this needs a local SCADA cache, which the shipped "
                "app does not carry. See the ❓ UIGF explainer on the Get Data tab."
            ),
        )

    with st.expander("Counterfactual sourcing", expanded=True):
        cols = st.columns(4)
        enable_counterfactual = cols[0].toggle(
            "Compare to counterfactual strategies",
            value=initial.enable_counterfactual,
            key="sf_enable_counterfactual",
            help="Compute spot-only and base-futures hedge costs for the offtaker after each run.",
        )
        _seed_aer_applied_from_scenario(
            st.session_state, initial.cal_forward_source, initial.cal_forward_price, initial.cal_forward_note,
        )
        # AER as the *default* seed (W4): when a futures cache exists for the
        # scenario's year and the user has not yet made a manual choice
        # (sf_cal_forward_price unset), pre-fill the forward price from the
        # full-year quarterly average with aer_indicative provenance so the
        # disclaimer carries through. A pending manual quote (Apply button)
        # always wins afterwards.
        if "sf_cal_forward_price" not in st.session_state:
            _aer_seed = _default_aer_seed_for_scenario(initial)
            if _aer_seed is not None:
                _seed_price, _seed_source, _seed_note = _aer_seed
                st.session_state["sf_cal_forward_price"] = float(_seed_price)
                st.session_state["_sf_aer_applied"] = {
                    "price_aud_mwh": float(_seed_price), "disclaimer": _seed_note,
                }
        st.session_state.setdefault("sf_cal_forward_price", float(initial.cal_forward_price))
        _apply_pending_aer(st.session_state)
        cal_forward_price = cols[1].number_input(
            "Base futures — calendar year (A$/MWh)",
            min_value=0.0, max_value=500.0,
            step=5.0,
            key="sf_cal_forward_price",
            help="Indicative baseload base-futures price for the next calendar year — use the "
                 "AER quote below or enter your own estimate.",
        )
        cal_hedge_fraction = cols[2].slider(
            "Hedge fraction (%)", 0, 100,
            int(initial.cal_hedge_fraction * 100),
            step=5, format="%d%%",
            key="sf_cal_hedge_fraction",
            help="Share of load hedged at the base-futures price; remainder sourced at spot.",
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
                # The field this feeds is labelled "calendar year", so default to
                # the first complete calendar year rather than the whole published
                # strip -- AER lists ~4 years ahead, and averaging all 16 quarters
                # is a multi-year strip price, not a calendar-year one (NSW1:
                # A$105.76 across 2026-29 vs A$101.72 for CY2026).
                _aer_default_quarters = aer_futures.first_full_calendar_year(
                    _aer_quarters_available
                )
                _stored_quarters = st.session_state.get("sf_aer_quarters")
                if _stored_quarters is not None and any(
                    q not in _aer_quarters_available for q in _stored_quarters
                ):
                    # Stale selection from a previously-selected region (e.g.
                    # one that doesn't have this quarter) -- reset to a fresh,
                    # valid list rather than letting it propagate into
                    # `quarterly_average`'s ValueError below.
                    st.session_state["sf_aer_quarters"] = list(_aer_default_quarters)
                else:
                    st.session_state.setdefault("sf_aer_quarters", list(_aer_default_quarters))
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
        optimise_capacity=optimise_capacity,
        max_build_wind_mw=float(max_build_wind_mw),
        max_build_pv_mw=float(max_build_pv_mw),
        max_build_bess_mw=float(max_build_bess_mw),
        sizing_resolution_h=int(sizing_resolution_h),
        sizing_method=str(sizing_method),
        sizing_n_periods=int(sizing_n_periods),
        grid_connection_max_mw=(
            float("inf") if not str(grid_connection).strip() else float(str(grid_connection).strip())
        ),
        sizing_merchant_value_share=float(merchant_share),
        enforce_min_delivery=bool(enforce_min_delivery),
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
        data_source=initial.data_source,
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
        transmission_cost_aud_mwh=float(transmission_cost_aud_mwh),
        simulation_years=simulation_years,
        first_sim_year=first_sim_year,
        price_escalation_rate=float(price_escalation_rate),
        pv_degradation_rate=float(pv_degradation_rate),
        wind_degradation_rate=float(wind_degradation_rate),
        bess_degradation_rate=float(bess_degradation_rate),
    )

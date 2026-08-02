"""Optimisation tab — run simulation or single-day reference optimisation."""
from __future__ import annotations

import dataclasses
import gc

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ppa.scenario import BASE_SCENARIO, validate_scenario
from ui import state
from ui.constants import NEM_RESOLUTION_MINUTES


def _sized_banner_text(sized) -> str:
    """'Optimised portfolio' summary including the sized connection (link) MW."""
    return (
        f"Wind **{sized.onsw_mw:.0f} MW** · Solar **{sized.pv_mw:.0f} MW** · "
        f"BESS **{sized.bess_mw:.0f} MW / {sized.bess_mwh:.0f} MWh** · "
        f"Wind link **{sized.wind_link_mw:.0f} MW** · PV+BESS link **{sized.pvbess_link_mw:.0f} MW** · "
        f"Export link **{sized.sell_link_mw:.0f} MW**"
    )


def _sizing_method_label(sized) -> str:
    """Human-readable sizing representation for the success message."""
    labels = {
        "tsam": "typical-day clustering (tsam)",
        "full_hourly": "full hourly year",
        "coarse": f"{getattr(sized, 'resolution_h', 1)}h coarse resolution",
    }
    return labels.get(getattr(sized, "sizing_method", "coarse"), "sizing LP")


def _render_sizing_diagnostics() -> None:
    """Sizing diagnostics expander (plan W12e): per-technology economics and
    which caps bind, so "strange sizing results" become an explainable answer."""
    if not state.has_sizing_diagnostics():
        return
    diag = state.get_sizing_diagnostics()
    avg_spot = diag.get("avg_spot")
    avg_spot_text = f"A${avg_spot:.1f}/MWh" if avg_spot is not None else "n/a"
    with st.expander("🔎 Sizing diagnostics", expanded=False):
        st.caption(
            "LP cost basis: annualised at **target_IRR** incl. devex; merchant revenue "
            f"credited at **{float(diag.get('sizing_merchant_value_share', 0.5)):.0%}** of "
            f"positive spot. Reference prices: PPA **A${diag['ppa_price']:.0f}/MWh** · "
            f"penalty **A${diag['penalty_price']:.0f}/MWh** · avg spot **{avg_spot_text}**."
        )
        st.markdown("**Per technology**")
        st.dataframe(diag["tech_rows"], width="stretch")
        st.markdown("**Connection links**")
        st.dataframe(diag["link_rows"], width="stretch")
        _delivery_sizing = diag.get("sizing_delivery_share")
        _delivery_full = diag.get("delivery_share_full")
        if _delivery_sizing is not None and _delivery_full is not None:
            _gap = (_delivery_full - _delivery_sizing) * 100
            _gap_note = (
                " — the full hourly year delivers materially more than the "
                "sizing representation suggested, so the clustering dropped "
                "some scarcity hours; try more typical periods."
                if _gap > 2.0
                else ""
            )
            st.caption(
                f"PPA delivery share: sizing LP **{_delivery_sizing:.1%}** "
                f"({diag.get('sizing_method', 'coarse')} representation) vs full "
                f"hourly simulation **{_delivery_full:.1%}** ({_gap:+.1f}pp){_gap_note}."
            )
        st.caption(
            "“Max-build cap binding / Connection limit binding = Yes” means that cap is what "
            "stopped the LP building more — the binding constraint is the real sizing decision. "
            "A single cached weather year (2025 SCADA) means the sized fleet is tuned to 2025 "
            "weather; multi-year SCADA is a TODO (see README)."
        )

# ── timeseries loader (NEM period path) ───────────────────────────────────────

@st.cache_data
def _cached_nem_period_ts(
    pv_duid: str, wind_duid: str, region: str, year: int,
    start: str, end: str, resolution_minutes: int,
):
    from ppa.data import nem_data

    class _FakeScenario:
        nem_pv_duid = pv_duid
        nem_wind_duid = wind_duid
        nem_price_region = region
        nem_year = year

    return nem_data.period_ts(_FakeScenario(), start, end, resolution_minutes=resolution_minutes)


# ── NEM period + resolution controls (single-day/period reference path) ────────

_NEM_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _render_snapshot_warning(n_snapshots: int) -> None:
    if n_snapshots <= 2_000:
        st.caption(f"≈{n_snapshots:,} snapshots — should solve in a few seconds.")
    elif n_snapshots <= 10_000:
        st.warning(
            f"≈{n_snapshots:,} snapshots — may take tens of seconds and use noticeably more "
            "memory. Close to the practical ceiling on memory-limited hosts (e.g. Streamlit "
            "Community Cloud's free 1 GB tier)."
        )
    else:
        st.error(
            f"≈{n_snapshots:,} snapshots — this is a large LP. It may take minutes and risks "
            "exceeding memory limits on constrained hosts (e.g. Streamlit Community Cloud's "
            "free 1 GB tier). Consider a shorter period and/or coarser resolution."
        )


def _render_nem_period_controls(s) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    """Period (calendar month or custom range) + resolution picker for the NEM
    single-day/period reference path. Returns (start, end [exclusive], resolution_minutes).
    """
    year = s.nem_year
    year_start = pd.Timestamp(year=year, month=1, day=1).date()
    year_end = pd.Timestamp(year=year, month=12, day=31).date()

    cols = st.columns([1, 2, 1])
    with cols[0]:
        mode = st.radio(
            "Period", ["Calendar month", "Custom range"], key="opt_nem_period_mode",
        )
    with cols[1]:
        if mode == "Calendar month":
            month = st.selectbox(
                "Month", options=list(range(1, 13)), index=2,  # default March, matches prior behaviour
                format_func=lambda m: _NEM_MONTH_NAMES[m - 1], key="opt_nem_month",
            )
            start_ts = pd.Timestamp(year=year, month=month, day=1)
            end_ts = start_ts + pd.DateOffset(months=1)
        else:
            default_range = (pd.Timestamp(year=year, month=3, day=1).date(),
                              (pd.Timestamp(year=year, month=3, day=1) + pd.DateOffset(months=1)
                               - pd.Timedelta(days=1)).date())
            picked = st.date_input(
                "Date range (inclusive)", value=default_range,
                min_value=year_start, max_value=year_end, key="opt_nem_date_range",
            )
            if isinstance(picked, tuple) and len(picked) == 2:
                start_ts = pd.Timestamp(picked[0])
                end_ts = pd.Timestamp(picked[1]) + pd.Timedelta(days=1)
            else:
                # Only the start date picked so far (mid-selection) -- show a single day.
                single = picked[0] if isinstance(picked, tuple) else picked
                start_ts = pd.Timestamp(single)
                end_ts = start_ts + pd.Timedelta(days=1)
    with cols[2]:
        resolution_label = st.selectbox(
            "Resolution", options=list(NEM_RESOLUTION_MINUTES.keys()), index=0,
            key="opt_nem_resolution",
        )
        resolution_minutes = NEM_RESOLUTION_MINUTES[resolution_label]

    n_snapshots = int((end_ts - start_ts) / pd.Timedelta(minutes=resolution_minutes))
    _render_snapshot_warning(n_snapshots)

    return start_ts, end_ts, resolution_minutes


def _get_timeseries(scenario):
    if scenario.data_source == "custom_csv":
        upload = state.get_custom_upload()
        if upload is None:
            # Mirror the multi-year path's error handling (ppa.tabs.optimisation
            # ._run_simulation): don't silently fall through to the NEM reference
            # cache below -- that would run the LP against the
            # wrong data source with no indication anything is amiss.
            raise RuntimeError(
                "Data source is 'custom_csv' but no uploaded file is active. "
                "Go to the Custom Data tab and apply one."
            )
        ts = upload["ts"]
        state.set_timeseries(ts)
        return ts
    # NEM (scenario.is_nem) is handled separately in render() -- it needs the
    # user-selected period/resolution, which this scenario-only signature has
    # no way to receive; see _cached_nem_period_ts / _render_nem_period_controls.
    if state.has_timeseries():
        return state.get_timeseries()
    return None


# ── scenario summary ──────────────────────────────────────────────────────────

def _render_scenario_summary(s) -> None:
    with st.expander("Scenario summary", expanded=False):
        cols = st.columns(4)
        with cols[0]:
            st.markdown("**Portfolio**")
            if s.optimise_capacity:
                st.markdown("- Mode: **co-optimised sizing** ⚡")
                st.markdown(
                    f"- Max build: wind **{s.max_build_wind_mw:.0f}** / "
                    f"solar **{s.max_build_pv_mw:.0f}** / "
                    f"BESS **{s.max_build_bess_mw:.0f} MW**"
                )
                st.markdown(f"- Sizing LP resolution: **{s.sizing_resolution_h}h**")
                if s.include_bess:
                    st.markdown(f"- BESS duration: **{s.bess_max_hours:.1f} h** (fixed)")
                else:
                    st.markdown("- BESS: *disabled*")
            else:
                st.markdown(f"- Wind: **{s.onsw_mw:.0f} MW**")
                st.markdown(f"- Solar: **{s.pv_mw:.0f} MWac**")
                if s.include_bess:
                    st.markdown(f"- BESS: **{s.effective_bess_mw:.0f} MW / {s.effective_bess_mwh:.0f} MWh**")
                else:
                    st.markdown("- BESS: *disabled*")

        with cols[1]:
            st.markdown("**PPA contract**")
            st.markdown(f"- Offtake: **{s.ppaload_mw:.0f} MW** flat")
            st.markdown(f"- Tariff: **A${s.ppa_price:.0f}/MWh**")
            st.markdown(f"- Required delivery: **{s.required_delivery_share:.0%}**")
            if s.enable_penalty:
                st.markdown(f"- Penalty: **{s.pen_mult:.1f}×** = A${s.penalty_price:.0f}/MWh")
            else:
                st.markdown("- Penalty: *disabled*")

        with cols[2]:
            st.markdown("**Market interaction**")
            if s.enable_market_buy:
                st.markdown(f"- Buy cap: **{s.market_buy_share:.0%}** of delivery")
            else:
                st.markdown("- Market buy: *disabled*")
            if s.enable_market_sell:
                st.markdown(f"- Sell: enabled (max {s.maxsell_mw:.0f} MW)")
            else:
                st.markdown("- Market sell: *disabled*")
            if s.enable_shortfall:
                st.markdown(f"- Shortfall: **{s.allowed_shortfall_share:.0%}** of load")
            else:
                st.markdown("- Shortfall: *disabled*")

        with cols[3]:
            st.markdown("**Simulation**")
            if s.transmission_cost_aud_mwh > 0:
                st.markdown(f"- Transmission: **A${s.transmission_cost_aud_mwh:.1f}/MWh** delivered")
            if s.simulation_years == 1:
                st.markdown(f"- Mode: **1-year** ({s.first_sim_year})")
            else:
                st.markdown(
                    f"- Mode: **{s.simulation_years}-year** "
                    f"({s.first_sim_year}–{s.first_sim_year + s.simulation_years - 1})"
                )
            st.markdown(f"- Price escalation: **{s.price_escalation_rate:.1%}/yr**")
            st.markdown(
                f"- Degradation: PV {s.pv_degradation_rate:.1%} | "
                f"Wind {s.wind_degradation_rate:.1%} | "
                f"BESS {s.bess_degradation_rate:.1%}"
            )


# ── data status (compact) ─────────────────────────────────────────────────────

def _render_nem_data_status(s) -> tuple[bool, bool]:
    from ppa.data import nem_data
    from ui.nem_cache_status import cached_cache_status

    status = cached_cache_status(s.nem_year)
    cols = st.columns(2)
    with cols[0]:
        if status["n_simulation_ready"] > 0:
            st.success(f"NEM SCADA: {status['n_simulation_ready']} simulation-ready plant(s) cached ✓")
        else:
            st.warning(
                "No simulation-ready NEM SCADA cached — go to **Get Data** tab "
                f"(`python scripts/fetch_nem_scada_prices.py --year {s.nem_year}`)."
            )
    prices_ok = s.nem_price_region in status["price_regions_cached"]
    with cols[1]:
        if prices_ok:
            st.success(f"NEM prices ({s.nem_price_region}): cached ✓")
        else:
            st.warning(
                f"No cached NEM price data for region {s.nem_price_region} — "
                f"run `python scripts/fetch_nem_scada_prices.py --year {s.nem_year}`."
            )

    # Applies identically to "nem_map" and "nem_default": both silently drive
    # zero renewable generation if the scenario's own DUIDs are empty/not
    # ready, regardless of whether *some* plant elsewhere in the cache is
    # simulation-ready.
    cf_ok, problems = nem_data.nem_generation_ready(
        s.data_source, s.nem_pv_duid, s.nem_wind_duid, year=s.nem_year
    )
    for problem in problems:
        st.error(problem)
    return prices_ok, cf_ok


def _render_custom_data_status(s) -> tuple[bool, bool]:
    upload = state.get_custom_upload()
    if upload is None:
        st.warning(
            "Data source is **custom_csv** but no upload is active — go to the "
            "**Custom Data** tab, download the template, fill it in, and click "
            "**Use this data**."
        )
        return False, False
    ts = upload["ts"]
    st.success(
        f"Custom upload active: **{upload['name']}** ({len(ts)} rows) — "
        "drives both CF/price and the offtaker load."
    )
    return True, True


def _render_data_status(s) -> tuple[bool, bool]:
    if s.data_source == "custom_csv":
        return _render_custom_data_status(s)
    if s.is_nem:
        return _render_nem_data_status(s)
    return False, False


# ── Simulation runner ────────────────────────────────────────────────

def _run_simulation(scenario, max_workers: int) -> None:
    import time

    from ppa.multi_year import run_multi_year
    from ppa.financials import run_multi_year_financial_analysis

    if scenario.data_source == "custom_csv":
        from ppa.data_loader import custom_timeseries_dicts

        upload = state.get_custom_upload()
        if upload is None:
            raise RuntimeError(
                "Data source is 'custom_csv' but no uploaded file is active. "
                "Go to the Custom Data tab and apply one."
            )
        pv_by_year, wind_by_year, prices_by_year, load_by_year = custom_timeseries_dicts(
            upload["ts"], year=scenario.first_sim_year
        )
    elif scenario.is_nem:
        from ppa.data import nem_data

        pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(scenario)
        load_by_year = None
    else:
        raise RuntimeError(
            f"Unknown data source '{scenario.data_source}' for the simulation runner."
        )

    progress_bar = st.progress(0, text="Starting optimisation ...")
    status_text = st.empty()

    # ── Capacity co-optimisation pre-step ─────────────────────────────────────
    sizing_seconds = None
    if scenario.optimise_capacity:
        from ppa.sizing import (
            apply_sizing,
            build_sizing_timeseries,
            clamp_sizing_years,
            sizing_memory_advice,
            run_sizing_subprocess,
            sizing_diagnostics,
            weather_cycle_years,
        )

        n_sizing_years, cycle_note = weather_cycle_years(
            scenario.simulation_years, len(pv_by_year), len(prices_by_year)
        )
        if cycle_note:
            st.info(cycle_note)
        n_sizing_years, notice = clamp_sizing_years(
            n_sizing_years, scenario.sizing_resolution_h
        )
        if notice:
            st.warning(notice)
        # Warn BEFORE the solve: an out-of-memory kill arrives as a silent
        # SIGKILL with no traceback, which is impossible to diagnose from the UI.
        mem_advice = sizing_memory_advice(scenario)
        if mem_advice:
            st.warning(mem_advice)
        progress_bar.progress(
            0.0,
            text=(
                f"Sizing portfolio (co-optimising capacities, {n_sizing_years}-year LP "
                f"at {scenario.sizing_resolution_h}h resolution)..."
            ),
        )
        sizing_ts = build_sizing_timeseries(
            scenario, pv_by_year, wind_by_year, prices_by_year, n_sizing_years,
            load_mw_by_year=load_by_year,
        )

        _t0 = time.monotonic()

        def _sizing_heartbeat() -> None:
            status_text.text(
                f"Solving the sizing LP in a background process... "
                f"{time.monotonic() - _t0:.0f}s elapsed. Press Stop to cancel."
            )

        sized = run_sizing_subprocess(sizing_ts, scenario, heartbeat=_sizing_heartbeat)
        sizing_seconds = time.monotonic() - _t0
        if sized.status != "ok":
            raise RuntimeError(
                f"Capacity sizing LP failed: {sized.status} / {sized.condition}"
            )
        # Keep the sized scenario local to this run: the user's scenario keeps
        # optimise_capacity=True so re-runs re-size; the optimised fleet is
        # surfaced via state.set_optimised_sizes.
        scenario = apply_sizing(scenario, sized)
        state.set_optimised_sizes(sized)
        state.set_sizing_diagnostics(
            sizing_diagnostics(sized, scenario, sizing_ts)
        )
        # Release the full-year sizing frame before run_multi_year forks its
        # workers. Fork is copy-on-write, but CPython refcounting dirties nearly
        # every page a child touches, so whatever is still resident here is paid
        # for once per worker. `sized` (a small dataclass) and the diagnostics
        # are all that is needed from here on.
        del sizing_ts
        gc.collect()
        horizon_msg = (
            f"Sizing LP: {sized.sizing_years_used} year(s). The subsequent hourly "
            f"dispatch simulation still solves all {scenario.simulation_years} "
            "year(s) — that is where most of the runtime goes."
            if sized.horizon_clamped
            else ""
        )
        status_text.success(
            f"Optimised portfolio — {_sized_banner_text(sized)} "
            f"(sized over {sized.sizing_years_used} year(s), "
            f"{_sizing_method_label(sized)} in {sizing_seconds:.0f}s) — "
            f"running hourly dispatch... {horizon_msg}"
        )

    def _on_progress(done: int, total: int, sim_year: int) -> None:
        progress_bar.progress(done / total, text=f"Year {sim_year} ({done}/{total})")
        status_text.text(f"Solved {done} of {total} year(s)...")

    _t_dispatch = time.monotonic()
    results = run_multi_year(
        scenario=scenario,
        pv_cf_by_year=pv_by_year,
        wind_cf_by_year=wind_by_year,
        prices_by_year=prices_by_year,
        load_mw_by_year=load_by_year,
        first_sim_year=scenario.first_sim_year,
        max_workers=max_workers,
        progress_callback=_on_progress,
    )
    dispatch_seconds = time.monotonic() - _t_dispatch
    state.set_multi_year_results(results)

    # Compare the sizing LP's delivery share against the full hourly simulation
    # of the sized portfolio (plan W14 item 6): a large gap means the typical-
    # period / coarse representation dropped something the hourly year has.
    if sizing_seconds is not None and state.has_sizing_diagnostics():
        diag = state.get_sizing_diagnostics()
        diag["delivery_share_full"] = float(
            np.mean([r.summary.fulfilled_share for r in results])
        )
        state.set_sizing_diagnostics(diag)

    fin = run_multi_year_financial_analysis(
        scenario, results, first_sim_year=scenario.first_sim_year
    )
    state.set_multi_year_financial(fin)

    progress_bar.progress(1.0, text="Optimisation complete!")
    timing = f" (sizing {sizing_seconds:.0f}s + dispatch {dispatch_seconds:.0f}s)" if sizing_seconds is not None else f" ({dispatch_seconds:.0f}s)"
    status_text.success(f"Completed {scenario.simulation_years} year(s) successfully{timing}.")


# ── multi-year results display ────────────────────────────────────────────────

def _render_results(fin, n_years: int) -> None:
    with st.expander("Optimisation results", expanded=True):
        cols = st.columns(5)
        irr_str = f"{fin.irr:.1%}" if fin.irr == fin.irr else "N/A"
        lcoe_str = f"A${fin.lcoe:.1f}/MWh" if fin.lcoe == fin.lcoe else "N/A"
        payback_str = f"{fin.simple_payback:.1f} yrs" if fin.simple_payback < 1e8 else "N/A"
        cols[0].metric("NPV", f"A${fin.npv/1e6:.1f}M")
        cols[1].metric("Project IRR", irr_str)
        cols[2].metric("LCOE", lcoe_str)
        cols[3].metric("Simple Payback", payback_str)
        cols[4].metric("Lifetime Net Revenue", f"A${fin.total_lifetime_revenue/1e6:.1f}M")

        if n_years == 1:
            y = fin.yearly[0]
            st.caption(
                f"Year {y.year} — PPA revenue A${y.ppa_revenue/1e6:.2f}M | "
                f"Merchant A${y.merch_revenue/1e6:.2f}M | "
                f"Delivery {y.fulfilled_share:.1%} | "
                f"Net CF A${y.net_cashflow/1e6:.2f}M"
            )
            return

    # st.markdown("---")
    with st.expander("Charts & data tables", expanded=True):
        tab_charts, tab_table = st.tabs([
            "| Charts", 
            "| Year-by-Year Table"
        ])
        with tab_charts:
            tab_chart1, tab_chart2, tab_chart3 = st.tabs([
                "| Cumulative NPV", 
                "| Annual Revenue Breakdown", 
                "| PPA Delivery Rate"
            ])
            with tab_chart1:
                _render_npv_chart(fin)
            with tab_chart2:
                _render_revenue_chart(fin)
            with tab_chart3:
                _render_delivery_chart(fin)

        with tab_table:
            _render_yearly_table(fin)


def _render_npv_chart(fin) -> None:
    years = [y.year for y in fin.yearly]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=[round(v / 1e6, 2) for v in fin.cumulative_npv],
        mode="lines+markers", name="Cumulative NPV",
        line=dict(color="#2196F3", width=2),
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="Cumulative NPV over Project Life",
        xaxis_title="Year", yaxis_title="NPV (A$M)", height=400,
    )
    st.plotly_chart(fig, width="stretch")


def _render_revenue_chart(fin) -> None:
    years = [y.year for y in fin.yearly]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=[round(y.ppa_revenue / 1e6, 2) for y in fin.yearly], name="PPA revenue"))
    fig.add_trace(go.Bar(x=years, y=[round(y.merch_revenue / 1e6, 2) for y in fin.yearly], name="Merchant revenue"))
    fig.add_trace(go.Bar(x=years, y=[round(-y.market_buy_cost / 1e6, 2) for y in fin.yearly], name="Market buy cost"))
    fig.add_trace(go.Bar(x=years, y=[round(-y.penalty_cost / 1e6, 2) for y in fin.yearly], name="Penalty cost"))
    fig.add_trace(go.Bar(x=years, y=[round(-y.transmission_cost / 1e6, 2) for y in fin.yearly], name="Transmission cost"))
    fig.add_trace(go.Bar(x=years, y=[round(-y.opex / 1e6, 2) for y in fin.yearly], name="OPEX"))
    fig.update_layout(
        barmode="relative", title="Annual Revenue Breakdown",
        xaxis_title="Year", yaxis_title="A$M", height=400,
    )
    st.plotly_chart(fig, width="stretch")


def _render_delivery_chart(fin) -> None:
    years = [y.year for y in fin.yearly]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=[round(y.fulfilled_share,3) * 100 for y in fin.yearly],
        mode="lines+markers", name="PPA Delivery Rate",
        line=dict(color="#4CAF50", width=2),
    ))
    fig.update_layout(
        title="PPA Delivery Rate by Year",
        xaxis_title="Year", yaxis_title="Delivery Rate (%)",
        yaxis=dict(range=[0, 105]), height=400,
    )
    st.plotly_chart(fig, width="stretch")


def _render_yearly_table(fin) -> None:
    rows = [
        {
            "Year": y.year,
            "PPA Revenue (A$M)": round(y.ppa_revenue / 1e6, 2),
            "Merchant Revenue (A$M)": round(y.merch_revenue / 1e6, 2),
            "Market Buy Cost (A$M)": round(y.market_buy_cost / 1e6, 2),
            "Penalty Cost (A$M)": round(y.penalty_cost / 1e6, 2),
            "Transmission Cost (A$M)": round(y.transmission_cost / 1e6, 2),
            "OPEX (A$M)": round(y.opex / 1e6, 2),
            "Net Cash Flow (A$M)": round(y.net_cashflow / 1e6, 2),
            "Delivery Rate (%)": round(y.fulfilled_share * 100, 1),
            "Wind Gen (GWh)": round(y.wind_gen_mwh / 1e3, 1),
            "PV Gen (GWh)": round(y.pv_gen_mwh / 1e3, 1),
        }
        for y in fin.yearly
    ]
    st.dataframe(pd.DataFrame(rows).set_index("Year"), width="stretch", height="content")


# ── main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.title("⚙️ Optimisation")

    if not state.has_scenario():
        state.set_scenario(BASE_SCENARIO)
    s = state.get_scenario()

    _render_scenario_summary(s)
    # st.markdown("---")

    # ── Simulation ───────────────────────────────────────────────────
    # st.subheader("Optimisation")
    with st.expander("Optimisation", expanded=True):
        prices_ok, cf_ok = _render_data_status(s)
        data_ready = prices_ok and cf_ok

        cols = st.columns([1, 1, 2], vertical_alignment="bottom")
        with cols[0]:
            model_run = st.button(
                "▶ Run Optimisation",
                type="primary",
                width="stretch",
                key="opt_run_eu",
                disabled=not data_ready,
            )
        with cols[1]:
            max_workers = st.selectbox(
                "Parallel workers", [1, 2, 4, 8, 16, 24, 30], index=2, key="opt_max_workers",
                help=(
                    "Max parallel year-solves. Automatically capped to the available "
                    "CPU and RAM (~1.2 GB per worker), so memory-limited hosts like "
                    "Streamlit Cloud fall back to serial regardless of this value. "
                    "Ignored for single-year runs."
                ),
            )
        with cols[2]:
            if not data_ready:
                st.warning("Download data first (see **Get Data** tab).")
            elif state.has_multi_year_results():
                n_done = len(state.get_multi_year_results())
                st.success(f"Last run: {n_done} year(s) solved.")

    if model_run and data_ready:
        try:
            _run_simulation(s, int(max_workers))
        except Exception as exc:
            st.error(f"Optimisation failed: {exc}")
        else:
            st.rerun()

    if state.has_multi_year_financial():
        # st.markdown("---")
        if s.optimise_capacity and state.has_optimised_sizes():
            sized = state.get_optimised_sizes()
            st.info(
                f"⚡ **Optimised portfolio** — {_sized_banner_text(sized)} "
                f"(sized over {sized.sizing_years_used} year(s) "
                f"at {getattr(sized, 'resolution_h', 1)}h resolution; dispatch & financials run hourly)"
            )
            _render_sizing_diagnostics()
        _render_results(state.get_multi_year_financial(), s.simulation_years)

    # ── Single-day reference optimisation ──────────────────────────────────────
    # st.markdown("---")
    if s.data_source == "custom_csv":
        _single_day_title = "Single-day reference optimisation (custom CSV upload)"
        _single_day_caption = (
            "Runs the LP over the active **custom CSV upload** (prepared to hourly, "
            "with the uploaded `ts_LoadMW` driving the offtake load directly). Pick "
            "the day to inspect under **Reference day selection**. Results feed the "
            "Results, and Analysis tabs."
        )
    else:
        _single_day_title = "Period reference optimisation (NEM data)"
        _single_day_caption = (
            f"Runs the LP over the selected NEM plants/prices (region {s.nem_price_region}, "
            f"{s.nem_year}) for any month or custom date range you pick below, at hourly, "
            "30-minute, or native 5-minute resolution. Pick the day to inspect under "
            "**Reference day selection**. Results feed the Results, and Analysis tabs."
        )

    with st.expander(_single_day_title, expanded=False):
        st.caption(_single_day_caption)

        resolution_h = 1.0
        _ts_error_shown = False
        try:
            if s.is_nem:
                period_start, period_end, resolution_minutes = _render_nem_period_controls(s)
                resolution_h = resolution_minutes / 60.0
                ts = _cached_nem_period_ts(
                    s.nem_pv_duid, s.nem_wind_duid, s.nem_price_region, s.nem_year,
                    period_start.isoformat(), period_end.isoformat(), resolution_minutes,
                )
                state.set_timeseries(ts)
            else:
                ts = _get_timeseries(s)
        except (RuntimeError, FileNotFoundError, ValueError, KeyError) as exc:
            st.error(str(exc))
            ts = None
            _ts_error_shown = True
        if ts is None:
            if s.data_source not in ("custom_csv",) and not s.is_nem and not _ts_error_shown:
                st.error("Could not load the reference timeseries from `data/cache/`.")
        else:
            from ppa.data_loader import coerce_chosen_day, get_available_days

            # Reconcile the sticky reference day with the period actually loaded,
            # so day and period can never diverge: coerce (don't block) and
            # surface the move as info rather than an error.
            available_days = get_available_days(ts)
            if available_days:
                if s.chosen_day not in available_days:
                    st.info(
                        f"Reference day moved to **{coerce_chosen_day(ts, s.chosen_day)}** — "
                        f"*{s.chosen_day}* is outside the selected period."
                    )
                reference_day = st.selectbox(
                    "Reference day for daily charts", available_days,
                    index=available_days.index(coerce_chosen_day(ts, s.chosen_day)),
                    key="opt_reference_day",
                )
                s = dataclasses.replace(s, chosen_day=str(reference_day))

            # The UI must not block on chosen_day (it is reconciled above); the
            # check stays in validate_scenario for direct API callers.
            errors = [e for e in validate_scenario(s, available_days=available_days)
                      if "chosen_day" not in e]
            if errors:
                for err in errors:
                    st.error(err)
                st.warning("Fix the above issues in **Case Study Definition** before running.")
            else:
                cols = st.columns([1, 3])
                with cols[0]:
                    single_run = st.button("▶ Run Single-Day", type="secondary", width="stretch", key="opt_run_single")
                with cols[1]:
                    if state.has_result():
                        r = state.get_result()
                        st.success(f"Last run: **{r.solver_status}** / **{r.solver_condition}**")

                if single_run:
                    with st.spinner("Solving... (typically 5–15 s)"):
                        try:
                            from ppa.data_loader import prepare_timeseries
                            from ppa.network import build_network
                            from ppa.solver import solve
                            from ppa.results import extract_results
                            from ppa.financials import run_financial_analysis
                            from ppa.counterfactuals import compute_counterfactuals

                            ts_prep = prepare_timeseries(ts, s)

                            # Capacity co-optimisation pre-step (reference period → fast;
                            # optimise_capacities coarsens internally via
                            # scenario.sizing_resolution_h regardless of ts_prep's own
                            # resolution, so it needs no resolution_h argument here).
                            if s.optimise_capacity:
                                from ppa.sizing import apply_sizing, optimise_capacities, sizing_diagnostics

                                sized = optimise_capacities(ts_prep, s)
                                if sized.status != "ok":
                                    raise RuntimeError(
                                        f"Capacity sizing LP failed: {sized.status} / {sized.condition}"
                                    )
                                s = apply_sizing(s, sized)
                                state.set_optimised_sizes(sized)
                                state.set_sizing_diagnostics(sizing_diagnostics(sized, s, ts_prep))
                                st.info(
                                    f"Optimised portfolio — {_sized_banner_text(sized)} "
                                    "(sized on the reference period)"
                                )

                            n = build_network(ts_prep, s, resolution_h=resolution_h)
                            status, condition = solve(n, s, ts_prep)
                            result = extract_results(n, s, ts_prep, status, condition, resolution_h=resolution_h)
                            state.set_result(result)

                            if s.run_financial_analysis:
                                fin = run_financial_analysis(s, result.summary, result.revenue, result.n_period_hours)
                                state.set_financial(fin)
                            if s.enable_counterfactual:
                                cf = compute_counterfactuals(ts_prep, s, result, dt=resolution_h)
                                state.set_counterfactual(cf)
                        except Exception as exc:
                            st.error(f"Optimisation failed: {exc}")
                        else:
                            st.success(f"Complete — {status} / {condition}. See Results tabs.")
                            _render_sizing_diagnostics()

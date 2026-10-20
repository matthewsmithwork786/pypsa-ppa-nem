"""Optimisation tab — run the multi-year simulation."""
from __future__ import annotations

import gc

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ppa.results import merchant_sales_frame, bess_utilisation
from ppa.scenario import BASE_SCENARIO
from ui import state
from ui.result_display import chart_help, number_table, result_chart, allocation_details
from ui.charts import year_axis
from ui.config_summary import render_scenario_summary as _render_scenario_summary
from ui.exports import csv_download_button


def restore_from_query_params() -> None:
    """Restore the run named in the URL after a page refresh."""
    run_id = st.query_params.get("run")
    if not run_id or state.has_multi_year_results():
        return

    from ppa import run_registry, run_store

    if not run_store.valid_run_id(run_id):
        st.query_params.pop("run", None)
        return
    run_registry.touch(run_id)

    payload = run_store.load(run_id)
    if payload is None:
        return
    state.set_scenario(payload["scenario"])
    state.set_multi_year_results(payload["results"])
    state.set_multi_year_financial(payload["fin"])
    if payload.get("sized") is not None:
        state.set_optimised_sizes(payload["sized"])
    if payload.get("diagnostics") is not None:
        state.set_sizing_diagnostics(payload["diagnostics"])
    st.toast("Restored your last completed run after refresh.", icon="🔄")


def _sized_banner_text(sized) -> str:
    """'Optimised portfolio' summary including the sized connection (link) MW."""
    return (
        f"Wind **{sized.onsw_mw:.0f} MW** · Solar **{sized.pv_mw:.0f} MW** · "
        f"BESS **{sized.bess_mw:.0f} MW / {sized.bess_mwh:.0f} MWh** · "
        f"Wind link **{sized.wind_link_mw:.0f} MW** · PV+BESS link **{sized.pvbess_link_mw:.0f} MW** · "
        f"Export link **{sized.sell_link_mw:.0f} MW**"
    )


def _sizing_method_label(sized) -> str:
    labels = {"tsam": "typical-day clustering (tsam)", "full_hourly": "full hourly year"}
    return labels.get(sized.sizing_method, "sizing LP")


def _sizing_method_caption(scenario) -> str:
    if scenario.sizing_method == "tsam":
        return f"{scenario.sizing_n_periods} typical weeks"
    return "full hourly"


def _render_sizing_diagnostics() -> None:
    """Show sizing economics, binding limits, and SLA diagnostics."""
    if not state.has_sizing_diagnostics():
        return
    diag = state.get_sizing_diagnostics()
    avg_spot = diag.get("avg_spot")
    avg_spot_text = rf"A\${avg_spot:.1f}/MWh" if avg_spot is not None else "n/a"
    with st.expander("🔎 Sizing diagnostics", expanded=False):
        # Dollar signs are escaped: two unescaped `$` in one markdown string make
        # Streamlit render everything between them as LaTeX.
        st.caption(
            "LP cost basis: annualised at **target_IRR** incl. devex; merchant revenue "
            rf"credited at **{float(diag.get('sizing_merchant_value_share', 0.5)):.0%}** of "
            rf"positive spot. Reference prices: PPA **A\${diag['ppa_price']:.0f}/MWh** · "
            rf"penalty **A\${diag['penalty_price']:.0f}/MWh** · avg spot **{avg_spot_text}**."
        )
        st.markdown("**Per technology**")
        st.dataframe(diag["tech_rows"], width="stretch")
        st.markdown("**Connection links**")
        st.dataframe(diag["link_rows"], width="stretch")
        scn = state.get_effective_scenario()
        sla_constraints = int(diag.get("sla_constraint_count", 0))
        tiers = [
            ("Monthly", scn.sla_monthly_enabled, scn.sla_monthly_share),
            ("Daily", scn.sla_daily_enabled, scn.sla_daily_share),
            ("Min-delivery floor", scn.enforce_min_delivery, scn.required_delivery_share),
        ]
        sla_rows = [
            {
                "SLA tier": name,
                "Active": "Yes" if enabled else "No",
                "Target share": f"{share:.0%}" if enabled else "—",
            }
            for name, enabled, share in tiers
        ]
        sla_rows.append({"SLA tier": "Constraints added", "Active": "—", "Target share": str(sla_constraints)})
        st.markdown("**SLA tiers**")
        st.dataframe(pd.DataFrame(sla_rows), width="stretch")
        if sla_constraints > 0:
            st.caption(
                f"**{sla_constraints}** SLA/delivery constraint(s) were live in the sizing LP — "
                "each Active tier adds one shortfall cap (+ one min-delivery floor) per period, "
                "so the daily tier alone multiplies the constraint count by the number of days. "
                "Whether a tier binds at the optimum is visible on the Deep Dive tab: the "
                "achieved share sitting at the target means the constraint is what stopped the "
                "LP doing something cheaper."
            )
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
                f"({diag.get('sizing_method', 'tsam')} representation) vs full "
                f"hourly simulation **{_delivery_full:.1%}** ({_gap:+.1f}pp){_gap_note}."
            )
        st.caption(
            "“Max-build cap binding / Connection limit binding = Yes” means that cap is what "
            "stopped the LP building more — the binding constraint is the real sizing decision. "
            "A single cached weather year (2025) means the sized fleet is tuned to 2025 "
            "weather; multi-year data is a TODO (see README)."
        )


def _render_nem_data_status(s) -> tuple[bool, bool]:
    from ppa.data import nem_data
    from ui.nem_cache_status import cached_cache_status

    status = cached_cache_status(s.nem_year)
    cols = st.columns(2)
    with cols[0]:
        if status["n_simulation_ready"] > 0:
            st.success(f"NEM UIGF: {status['n_simulation_ready']} simulation-ready plant(s) cached ✓")
        else:
            st.warning(
                "No simulation-ready NEM generation data cached — go to **Get Data** tab "
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


def _run_simulation(scenario, max_workers: int, run_id: str) -> None:
    import time

    from ppa.multi_year import run_multi_year
    from ppa.financials import run_multi_year_financial_analysis

    orig_scenario = scenario  # pre-sizing, user-facing scenario -- persisted below
    from ppa.simulation import REPRESENTATIVE_YEAR_NOTE, scenario_for_dispatch

    if scenario.dispatch_mode == "representative_year":
        st.info(REPRESENTATIVE_YEAR_NOTE)

    res_min = scenario.nem_resolution_minutes
    resolution_h = res_min / 60.0
    if res_min < 30 and scenario.dispatch_years > 2:
        raise RuntimeError(
            f"{res_min}-minute resolution over {scenario.simulation_years} years builds an "
            f"LP roughly {60 // res_min}x the hourly size and will exhaust memory. "
            "Reduce the simulation years to 2 or fewer, or use 30- or 60-minute resolution."
        )

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

    sizing_seconds = None
    if scenario.optimise_capacity:
        from ppa.sizing import (
            apply_sizing,
            build_sizing_timeseries,
            clamp_sizing_years,
            sizing_memory_advice,
            run_sizing_subprocess,
            sizing_diagnostics,
        )

        sizing_year = scenario.capacity_sizing_year if scenario.is_nem else min(pv_by_year)
        sizing_pv = {sizing_year: pv_by_year[sizing_year]}
        sizing_wind = {sizing_year: wind_by_year[sizing_year]}
        sizing_prices = {sizing_year: prices_by_year[sizing_year]}
        sizing_load = {sizing_year: load_by_year[sizing_year]} if load_by_year else None
        n_sizing_years = 1
        n_sizing_years, notice = clamp_sizing_years(n_sizing_years, resolution_h)
        if notice:
            st.warning(notice)
        mem_advice = sizing_memory_advice(scenario)
        if mem_advice:
            st.warning(mem_advice)
        progress_bar.progress(
            0.0,
            text=(
                f"Sizing portfolio (co-optimising capacities, {n_sizing_years}-year LP, "
                f"{_sizing_method_caption(scenario)})..."
            ),
        )
        sizing_ts = build_sizing_timeseries(
            scenario, sizing_pv, sizing_wind, sizing_prices, n_sizing_years,
            load_mw_by_year=sizing_load,
            resolution_minutes=res_min,
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
        scenario = apply_sizing(scenario, sized)
        state.set_optimised_sizes(sized)
        state.set_sizing_diagnostics(
            sizing_diagnostics(sized, scenario, sizing_ts)
        )
        # Free the sizing frame before dispatch workers fork, to limit memory use.
        del sizing_ts
        gc.collect()
        horizon_msg = (
            f"Sizing LP: {sized.sizing_years_used} year(s). The subsequent hourly "
            f"dispatch simulation solves {scenario.dispatch_years} "
            "year(s)."
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
        scenario=scenario_for_dispatch(scenario),
        pv_cf_by_year=pv_by_year,
        wind_cf_by_year=wind_by_year,
        prices_by_year=prices_by_year,
        load_mw_by_year=load_by_year,
        first_sim_year=scenario.first_sim_year,
        max_workers=max_workers,
        progress_callback=_on_progress,
        run_id=run_id,
        resolution_h=resolution_h,
    )
    dispatch_seconds = time.monotonic() - _t_dispatch
    state.set_multi_year_results(results)

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

    from ppa import run_store

    run_store.save(run_id, {
        "scenario": orig_scenario,
        "results": results,
        "fin": fin,
        "sized": state.get_optimised_sizes() if state.has_optimised_sizes() else None,
        "diagnostics": state.get_sizing_diagnostics() if state.has_sizing_diagnostics() else None,
    })

    progress_bar.progress(1.0, text="Optimisation complete!")
    timing = f" (sizing {sizing_seconds:.0f}s + dispatch {dispatch_seconds:.0f}s)" if sizing_seconds is not None else f" ({dispatch_seconds:.0f}s)"
    if scenario.dispatch_mode == "representative_year":
        status_text.success(
            f"Solved 1 dispatch year; prepared {scenario.simulation_years} projected financial years{timing}."
        )
    else:
        status_text.success(f"Completed {scenario.simulation_years} year(s) successfully{timing}.")


# Multi-year results display

def _render_results(fin, n_years: int) -> None:
    s = state.get_effective_scenario()
    if s.dispatch_mode == "representative_year":
        st.caption(f"Financial projection: {n_years} operating years based on one solved dispatch year.")
    with st.expander("Optimisation results", expanded=True):
        avg_delivery = fin.delivery_share
        cols = st.columns(6)
        cols[0].metric("Peak demand", f"{s.ppaload_mw:,.0f} MW")
        cols[1].metric(
            "PPA delivery (projection, load-weighted)", f"{avg_delivery:.1%}",
            help="Total delivered MWh divided by total load MWh across projected years. "
                 "In Full mode, degradation and changing dispatch can reduce later-year delivery.",
        )
        cols[2].metric(
            "PPA price", f"A${s.ppa_price:,.0f}/MWh",
            help=f"Delivery target: {s.required_delivery_share:.0%}; penalties and hard delivery floors are separate settings.",
        )
        cols[3].metric(
            "Merchant sell share", f"{s.sizing_merchant_value_share:.0%}",
            help="Share of merchant revenue credited to the sizing LP.",
        )
        cols[4].metric("BESS", f"{s.bess_mw:,.0f} MW / {s.bess_mwh:,.0f} MWh")
        if fin.yearly:
            y0 = fin.yearly[0]
            cols[5].metric(
                "First-year PPA revenue", f"A${y0.ppa_revenue/1e6:.1f}M",
                help=f"Year {y0.year}: PPA revenue from delivered volumes at the contracted price.",
            )
        else:
            cols[5].metric("First-year PPA revenue", "—")

        results = state.get_multi_year_results() or []
        if results:
            _render_first_year_energy_tables(results[0], s)
        else:
            st.info("Energy volume tables need the per-year results; re-run the optimisation.")

        if n_years == 1:
            return

    with st.expander("Charts & data tables", expanded=True):
        tab_charts, tab_table = st.tabs([
            "| Charts", 
            "| Year-by-Year Table"
        ])
        with tab_charts:
            tab_chart1, tab_chart2 = st.tabs([
                "| Annual Revenue & Merchant Prices", 
                "| PPA Delivery Rate"
            ])
            with tab_chart1:
                _render_revenue_chart(fin)
            with tab_chart2:
                _render_delivery_chart(fin)

        with tab_table:
            _render_yearly_table(fin)


def _render_first_year_energy_tables(result, s) -> None:
    """Energy-first view of the first simulated year: PPA volumes, merchant
    volumes split by solar vs non-solar hours, and penalty volumes. Deliberately
    no financial metrics -- those are computed once the financial modelling
    options are set."""
    allocation_details(result, "dl_allocation_run")
    summ = result.summary
    merchant = merchant_sales_frame(result)
    solar, nonsolar = merchant.iloc[0], merchant.iloc[1]
    def price(value):
        return f"A${value:,.2f}/MWh" if pd.notna(value) else "n/a (no sales)"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**First-year PPA volumes**")
        st.dataframe(
            pd.DataFrame({
                "PPA load": f"{summ.total_load_mwh/1e3:,.1f} GWh",
                "PPA delivered": f"{summ.ppa_delivered_mwh/1e3:,.1f} GWh",
                "  · from renewables & storage": f"{summ.renewable_and_storage_to_ppa_mwh/1e3:,.1f} GWh",
                "  · via market purchase": f"{summ.market_buy_to_ppa_mwh/1e3:,.1f} GWh",
                "Allowed shortfall": f"{summ.allowed_shortfall_mwh/1e3:,.1f} GWh",
            }, index=["Volume"]).T,
            width="stretch", height="content",
        )
    with c2:
        st.markdown("**First-year merchant volumes (solar / non-solar)**")
        st.dataframe(
            pd.DataFrame({
                "Sold to market — solar hours": f"{solar['Energy sold (MWh)']/1e3:,.1f} GWh",
                "Sold to market — non-solar hours": f"{nonsolar['Energy sold (MWh)']/1e3:,.1f} GWh",
                "Total sold to market": f"{summ.sold_to_market_mwh/1e3:,.1f} GWh",
                "Solar-hour spot capture": price(solar["Spot capture (A$/MWh)"]),
                "Non-solar-hour spot capture": price(nonsolar["Spot capture (A$/MWh)"]),
                "Solar-hour net capture incl. LGC": price(solar["Net capture incl. LGC (A$/MWh)"]),
                "Non-solar-hour net capture incl. LGC": price(nonsolar["Net capture incl. LGC (A$/MWh)"]),
                "Merchant revenue": f"A${merchant['Merchant revenue (A$)'].sum()/1e6:,.2f}M",
            }, index=["Volume"]).T,
            width="stretch", height="content",
        )
    with c3:
        st.markdown("**First-year penalty volumes**")
        st.dataframe(
            pd.DataFrame({
                "Uncovered load beyond allowance": f"{summ.penalty_mwh/1e3:,.1f} GWh",
                "Share of PPA load": f"{summ.penalty_share_of_load:.1%}",
                "Shortfall (allowed)": f"{summ.allowed_shortfall_mwh/1e3:,.1f} GWh",
                "Penalty price (zero when disabled)": f"A${s.penalty_price:,.0f}/MWh",
            }, index=["Volume"]).T,
            width="stretch", height="content",
        )

    st.caption(
        "Capture prices are weighted by energy sold, not average market prices. "
        "Solar hours are 09:00–17:00; NEM inputs use AEST and custom inputs use their supplied timestamps. "
        "Net capture adds LGC revenue and deducts the A$/MWh spread. The optimiser can "
        "divert energy from the PPA or storage into high-price sales."
    )
    csv_download_button(merchant, "first_year_merchant_sales.csv", key="dl_merchant_sales")
    if result.scenario.effective_bess_mwh > 0:
        st.markdown("**First-year BESS utilisation**")
        battery = pd.DataFrame([bess_utilisation(result)])
        st.dataframe(number_table(battery), hide_index=True, width="stretch")
        st.caption(
            "Equivalent full cycles = discharged MWh ÷ (usable MWh × discharge efficiency). "
            "This measures internal battery energy throughput; usable capacity includes modelled fade. "
            "Cycles per day use the actual solved period."
        )



def _yearly_results_frame(fin) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Year": year.year,
            "PPA Revenue (A$M)": year.ppa_revenue / 1e6,
            "Merchant Revenue (A$M)": year.merch_revenue / 1e6,
            "Market Buy Cost (A$M)": year.market_buy_cost / 1e6,
            "Penalty Cost (A$M)": year.penalty_cost / 1e6,
            "Transmission Cost (A$M)": year.transmission_cost / 1e6,
            "OPEX (A$M)": year.opex / 1e6,
            "Net Cash Flow (A$M)": year.net_cashflow / 1e6,
            "Delivery Rate (%)": year.fulfilled_share * 100,
            "Wind Gen (GWh)": year.wind_gen_mwh / 1e3,
            "PV Gen (GWh)": year.pv_gen_mwh / 1e3,
            "BESS charge (GWh)": year.bess_charge_gwh,
            "BESS discharge (GWh)": year.bess_discharge_gwh,
            "BESS usable capacity (MWh)": year.bess_usable_mwh,
            "BESS equivalent full cycles": year.bess_cycles,
            "BESS cycles per day": year.bess_cycles_per_day,
        }
        for year in fin.yearly
    ]).set_index("Year")


def _render_revenue_chart(fin) -> None:
    yearly = _yearly_results_frame(fin)
    years = yearly.index
    fig = go.Figure()
    series = [
        ("PPA Revenue (A$M)", "PPA revenue", 1),
        ("Merchant Revenue (A$M)", "Merchant revenue", 1),
        ("Market Buy Cost (A$M)", "Market buy cost", -1),
        ("Penalty Cost (A$M)", "Penalty cost", -1),
        ("Transmission Cost (A$M)", "Transmission cost", -1),
        ("OPEX (A$M)", "OPEX", -1),
    ]
    for column, label, sign in series:
        fig.add_trace(go.Bar(x=years, y=sign * yearly[column].round(2), name=label))
    fig.update_layout(
        barmode="relative", title="Annual Revenue Breakdown",
        xaxis_title="Year", yaxis_title="A$M", height=400,
        xaxis=year_axis(years),
    )
    chart_help("Annual revenue and costs", "Positive bars are PPA and merchant revenue; negative bars are market purchases, penalties, transmission costs and operating costs. Values are A$ million. Their net is operating cash flow before project financing and tax. In Fast mode later years repeat solved volumes with the selected escalations, rather than new dispatch forecasts.")
    result_chart(fig, width="stretch")
    export = yearly.rename(columns={
        "PPA Revenue (A$M)": "ppa_revenue_aud_m",
        "Merchant Revenue (A$M)": "merchant_revenue_aud_m",
        "Market Buy Cost (A$M)": "market_buy_cost_aud_m",
        "Penalty Cost (A$M)": "penalty_cost_aud_m",
        "Transmission Cost (A$M)": "transmission_cost_aud_m",
        "OPEX (A$M)": "opex_aud_m",
    }).rename_axis("year").reset_index()
    csv_download_button(
        export[["year", "ppa_revenue_aud_m", "merchant_revenue_aud_m", "market_buy_cost_aud_m",
                "penalty_cost_aud_m", "transmission_cost_aud_m", "opex_aud_m"]],
        "annual_revenue_breakdown.csv", key="dl_revenue",
    )


def _render_delivery_chart(fin) -> None:
    yearly = _yearly_results_frame(fin)
    years = yearly.index
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=(yearly["Delivery Rate (%)"] / 100).round(3) * 100,
        mode="lines+markers", name="PPA Delivery Rate",
        line=dict(color="#4CAF50", width=2),
    ))
    fig.update_layout(
        title="PPA Delivery Rate by Year",
        xaxis_title="Year", yaxis_title="Delivery Rate (%)",
        yaxis=dict(range=[0, 105]), height=400,
        xaxis=year_axis(years),
    )
    chart_help("Annual delivery rate", "Delivered PPA energy divided by contracted load for each year, shown as a percentage. A high annual rate does not guarantee delivery in every interval or compliance with monthly/daily SLAs. In Fast mode energy volumes repeat from one solved year.")
    result_chart(fig, width="stretch")
    csv_download_button(
        (yearly["Delivery Rate (%)"] / 100).round(3).mul(100)
        .rename("delivery_rate_pct").rename_axis("year").reset_index(),
        "ppa_delivery_rate.csv", key="dl_delivery",
    )


def _render_yearly_table(fin) -> None:
    yearly = _yearly_results_frame(fin)
    st.dataframe(
        number_table(yearly),
        width="stretch",
        height="content",
    )


def render() -> None:
    st.title("⚙️ Optimisation")

    if not state.has_scenario():
        state.set_scenario(BASE_SCENARIO)
    s = state.get_scenario()

    _render_scenario_summary(s, state.get_effective_scenario() if state.has_multi_year_results() else None)
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
                "Parallel workers", [1, 2, 4, 8, 16, 24, 30], index=0, key="opt_max_workers",
                disabled=s.dispatch_mode == "representative_year",
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
        from ppa import run_registry

        run_id = run_registry.new_run_id()
        try:
            from ppa.simulation import solve_slot

            with solve_slot():
                state.clear_run_outputs()
                st.query_params["run"] = run_id
                _run_simulation(s, int(max_workers), run_id)
        except Exception as exc:
            st.query_params.pop("run", None)
            st.error(f"Optimisation failed: {exc}")
        else:
            st.rerun()

    if state.has_multi_year_financial():
        if s.optimise_capacity and state.has_optimised_sizes():
            sized = state.get_optimised_sizes()
            st.info(
                f"⚡ **Optimised portfolio** — {_sized_banner_text(sized)} "
                f"(sized over {sized.sizing_years_used} year(s) "
                f"at {sized.resolution_h}h resolution; dispatch & financials run hourly)"
            )
            _render_sizing_diagnostics()
        _render_results(state.get_multi_year_financial(), s.simulation_years)

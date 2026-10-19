from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ppa.counterfactuals import compute_counterfactuals
from ppa.offtaker import run_of_plant_costs
from ppa.results import (
    build_24h_avg,
    build_24h_band,
    build_supply_mix_df,
    filter_dispatch_range,
)
from ui import state
from ui.charts import (
    make_multi_year_counterfactual_chart,
    make_price_24h_chart,
    make_price_series_chart,
    make_soc_24h_chart,
    make_soc_chart,
    make_supply_mix_24h_chart,
)
from ui.config_summary import render_config_summary
from ui.exports import csv_download_button


def _fmt_m(v: float) -> str:
    return f"A${v / 1e6:,.2f}M"


_MAX_DISPLAY_POINTS = 5000


def _downsample_series(series: pd.Series) -> pd.Series:
    """Cap displayed points so a full-year 5-min upload (105k rows) does not
    hang the browser. Keeps every `n`th sample while preserving the range."""
    n = len(series)
    if n <= _MAX_DISPLAY_POINTS:
        return series
    step = max(1, int(np.ceil(n / _MAX_DISPLAY_POINTS)))
    return series.iloc[::step]


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _month_filtered(obj, month: int | None):
    """Restrict a Series/DataFrame to one calendar month, or return it as-is
    for 'All months' (month=None)."""
    if month is None:
        return obj
    return obj[obj.index.month == month]


def _render_dispatch_section(result, s, chosen_day: str) -> None:
    supply_mix = build_supply_mix_df(result.dispatch)
    ts_index = supply_mix.index
    first_day = ts_index.normalize().min()
    last_day = ts_index.normalize().max()
    default_start = min(pd.Timestamp(chosen_day), last_day)
    default_end = min(default_start + pd.Timedelta(days=7), last_day)

    # The month filter scopes the average-day chart; the date range scopes detail.
    months_present = sorted(ts_index.month.unique())
    avg_month = st.selectbox(
        "Average 24h view — restrict to month",
        options=[None] + months_present,
        format_func=lambda m: "All months (full year)" if m is None else _MONTH_NAMES[m - 1],
        key="dd_avg_month",
    )

    if last_day > first_day:
        start, end = st.slider(
            "Time series view — date range to inspect",
            min_value=first_day.date(),
            max_value=last_day.date(),
            value=(default_start.date(), default_end.date()),
            format="DD MMM",
        )
        start, end = pd.Timestamp(start), pd.Timestamp(end)
    else:
        start = end = first_day

    range_mix = filter_dispatch_range(supply_mix, start, end)
    window_label = f"{start.strftime('%d %b')} – {end.strftime('%d %b')}"
    avg_mix = _month_filtered(supply_mix, avg_month)
    avg_label = "All year" if avg_month is None else _MONTH_NAMES[avg_month - 1]

    tab_chart1, tab_chart2, tab_chart3 = st.tabs([
        "| Actual hourly supply mix",
        "| Market spot price",
        "| BESS SoC",
    ])
    with tab_chart1:
        tab_avg, tab_ts = st.tabs(["| Average 24 h", "| Time series"])
        with tab_avg:
            fig = make_supply_mix_24h_chart(build_24h_avg(avg_mix), s.ppaload_mw)
            fig.update_layout(title=f"Average supply mix by time of day — {avg_label}")
            st.plotly_chart(fig, width="stretch", height=400)
            csv_download_button(build_24h_avg(avg_mix), "supply_mix_avg_24h.csv", key="dl_mix_avg")
        with tab_ts:
            fig = make_supply_mix_24h_chart(
                range_mix.assign(slot=range_mix.index),
                s.ppaload_mw,
                rangeslider=True,
            )
            fig.update_layout(title=f"Supply mix — {window_label}")
            st.plotly_chart(fig, width="stretch", height=400)
            csv_download_button(range_mix, "supply_mix_timeseries.csv", key="dl_mix_ts")

    with tab_chart2:
        if getattr(result, "market_prices", None) is not None:
            price_range = filter_dispatch_range(result.market_prices, start, end)
            avg_prices = _month_filtered(result.market_prices, avg_month)
            tab_avg, tab_ts = st.tabs(["| Average 24 h", "| Time series"])
            with tab_avg:
                fig_price = make_price_24h_chart(build_24h_band(avg_prices))
                fig_price.update_layout(title=f"Average day-ahead price — {avg_label}")
                st.plotly_chart(fig_price, width="stretch", height=400)
                csv_download_button(build_24h_band(avg_prices), "price_avg_24h.csv", key="dl_price_avg")
            with tab_ts:
                fig_price = make_price_series_chart(
                    _downsample_series(price_range),
                    title=f"Day-ahead price — {window_label}",
                    rangeslider=True,
                )
                st.plotly_chart(fig_price, width="stretch", height=400)
                csv_download_button(price_range, "price_timeseries.csv", key="dl_price_ts")
        else:
            st.info("No market price data available for this scenario.")

    with tab_chart3:
        if s.include_bess and s.effective_bess_mwh > 0:
            soc_range = filter_dispatch_range(result.dispatch.soc, start, end)
            avg_soc = _month_filtered(result.dispatch.soc, avg_month)
            tab_avg, tab_ts = st.tabs(["| Average 24 h", "| Time series"])
            with tab_avg:
                fig_soc = make_soc_24h_chart(
                    build_24h_band(avg_soc), s.effective_bess_mwh
                )
                fig_soc.update_layout(title=f"Average BESS SoC by time of day — {avg_label}")
                st.plotly_chart(fig_soc, width="stretch", height=400)
                csv_download_button(build_24h_band(avg_soc), "bess_soc_avg_24h.csv", key="dl_soc_avg")
            with tab_ts:
                fig_soc = make_soc_chart(
                    _downsample_series(soc_range),
                    s.effective_bess_mwh,
                    rangeslider=True,
                )
                st.plotly_chart(fig_soc, width="stretch", height=400)
                csv_download_button(soc_range, "bess_soc_timeseries.csv", key="dl_soc_ts")
        else:
            st.info("No BESS included in this scenario.")


def _render_sla_compliance(result, s) -> None:
    """Show period delivery against each enabled SLA target."""
    tiers = [
        ("Monthly", s.sla_monthly_enabled, getattr(result, "monthly_delivery_share", None),
         s.sla_monthly_share, result.summary.n_months_below_sla,
         result.summary.min_monthly_delivery_share, "bar", "month(s)", "Month"),
        ("Daily", s.sla_daily_enabled, getattr(result, "daily_delivery_share", None),
         s.sla_daily_share, result.summary.n_days_below_sla,
         result.summary.min_daily_delivery_share, "line", "day(s)", ""),
    ]
    active_tiers = [tier for tier in tiers if tier[1] and tier[2] is not None]
    warnings = getattr(result, "warnings", None) or []
    if not active_tiers:
        return

    st.subheader("SLA compliance")
    for msg in warnings:
        st.warning(msg)

    for label, _, series, target, below, minimum, chart_type, period_label, x_title in active_tiers:
        fig = go.Figure()
        values = (series * 100).values
        if chart_type == "bar":
            fig.add_trace(go.Bar(x=series.index, y=values, name=f"{label} delivery share", marker_color="#4CAF50"))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=values, mode="lines", name=f"{label} delivery share",
                line=dict(color="#1565C0", width=1),
            ))
        fig.add_hline(
            y=target * 100,
            line_dash="dash",
            line_color="#B71C1C",
            line_width=1.5,
            annotation_text=f"SLA target ({target:.0%})",
            annotation_position="top left",
            annotation_font_size=10,
        )
        fig.update_layout(
            title=f"{label} PPA delivery share vs SLA target",
            xaxis_title=x_title,
            yaxis_title="Delivery share (%)",
            yaxis=dict(range=[0, 105]),
            height=320,
        )
        st.plotly_chart(fig, width="stretch", height=320)
        st.caption(
            f"{below} of {len(series)} {period_label} below the {target:.0%} target — "
            f"minimum achieved {minimum:.1%}."
        )


def _render_gen_stats(result, s) -> None:
    summary = result.summary
    n_hours = result.n_period_hours
    wind_cf = summary.wind_generation_mwh / (s.onsw_mw * n_hours) if s.onsw_mw > 0 else 0.0
    pv_cf = summary.pv_generation_mwh / (s.pv_mw * n_hours) if s.pv_mw > 0 else 0.0
    bess_cycles = summary.bess_dispatch_mwh / s.effective_bess_mwh if s.include_bess and s.effective_bess_mwh > 0 else 0.0
    avg_soc = float(result.dispatch.soc.mean()) if s.include_bess else 0.0

    stats_df = pd.DataFrame(
        [
            ("Wind capacity factor", f"{wind_cf:.1%}", f"{summary.wind_generation_mwh:,.0f} MWh"),
            ("PV capacity factor", f"{pv_cf:.1%}", f"{summary.pv_generation_mwh:,.0f} MWh"),
            ("BESS equivalent cycles", f"{bess_cycles:.1f}", f"over {n_hours} h"),
            ("BESS average SoC", f"{avg_soc:.1f} MWh",
             f"{avg_soc / s.effective_bess_mwh:.1%} of capacity" if s.effective_bess_mwh > 0 else ""),
            ("Sold to spot market", f"{summary.sold_to_market_mwh:,.0f} MWh", ""),
        ],
        columns=["Metric", "Value", "Detail"],
    )
    cols = st.columns(2)
    cols[0].dataframe(stats_df, hide_index=True, width="stretch")

    lu = getattr(result, "link_utilisation", None)
    if lu is not None and not lu.empty:
        with cols[1]:
            st.markdown("**Connection (link) capacity**")
            st.dataframe(
                lu.reset_index()
                .rename(columns={"link": "Link"})
                .assign(
                    sized_mw=lambda d: d["sized_mw"].round(1),
                    peak_flow=lambda d: d["peak_flow"].round(1),
                    utilisation=lambda d: (d["utilisation"] * 100).round(1),
                )
                .rename(columns={"utilisation": "utilisation %"}),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Utilisation near 100 % on a link signals a binding "
                "`grid_connection_max_mw` is curtailing the build."
            )


def _render_multi_year_counterfactuals(results, fin, s) -> None:
    # Older saved runs may not include market prices.
    if not any(getattr(r, "market_prices", None) is not None for r in results):
        return

    st.caption(
        "Compares the offtaker's all-in cost under the PPA versus alternative sourcing strategies. "
        "The base-futures price is escalated year-on-year at the same rate as market prices. "
        "Counterfactuals use solved dispatch years only, which may be a subset of the financial projection."
    )
    if s.cal_forward_source == "aer_indicative":
        st.caption(s.cal_forward_note)

    yearly_cfs = []
    for idx, (yf, res) in enumerate(zip(fin.yearly, results)):
        prices = getattr(res, "market_prices", None)
        if prices is None:
            continue
        cal_price = s.cal_forward_price * (1 + s.price_escalation_rate) ** idx
        year_scenario = dataclasses.replace(res.scenario, cal_forward_price=cal_price)
        # Rebuild load from dispatch; the original input timeseries is not retained.
        load_mw = (
            res.dispatch.ppa_delivery
            + res.dispatch.allowed_shortfall
            + res.dispatch.penalty_gen
        )
        ts_mock = pd.DataFrame({"ts_MktPrice": prices, "ppaload_mw": load_mw})
        dt = res.n_period_hours / len(load_mw) if len(load_mw) else 1.0
        cf = compute_counterfactuals(ts_mock, year_scenario, res, dt=dt)
        yearly_cfs.append((yf.year, cf, cal_price))

    if not yearly_cfs:
        st.info("Re-run the optimisation to see counterfactual data.")
        return

    years = [y for y, _, _ in yearly_cfs]
    ppa_prices = [cf.ppa_effective_price for _, cf, _ in yearly_cfs]
    spot_prices = [cf.spot_avg_price for _, cf, _ in yearly_cfs]
    cal_prices = [cf.cal_avg_price for _, cf, _ in yearly_cfs]
    blended_prices = [cf.blended_avg_price for _, cf, _ in yearly_cfs]

    st.plotly_chart(
        make_multi_year_counterfactual_chart(years, ppa_prices, spot_prices, cal_prices, blended_prices),
        width="stretch",
    )

    total_load = sum(cf.total_load_mwh for _, cf, _ in yearly_cfs)
    total_spot = sum(cf.spot_cost for _, cf, _ in yearly_cfs)
    total_cal = sum(cf.cal_cost for _, cf, _ in yearly_cfs)
    total_blended = sum(cf.blended_cost for _, cf, _ in yearly_cfs)
    total_ppa = sum(cf.ppa_offtaker_cost for _, cf, _ in yearly_cfs)

    def _eff(cost): return cost / total_load if total_load > 0 else 0.0
    def _em(cost): return f"A${cost / 1e6:.2f}M"

    tbl = pd.DataFrame([
        ("PPA (offtaker)", f"A${_eff(total_ppa):.2f}/MWh", _em(total_ppa), "—"),
        ("Spot-only", f"A${_eff(total_spot):.2f}/MWh", _em(total_spot),
         f"A${(total_spot - total_ppa) / 1e6:+.2f}M vs PPA"),
        ("Base futures (escalated)", f"A${_eff(total_cal):.2f}/MWh", _em(total_cal),
         f"A${(total_cal - total_ppa) / 1e6:+.2f}M vs PPA"),
        ("Blended", f"A${_eff(total_blended):.2f}/MWh", _em(total_blended),
         f"A${(total_blended - total_ppa) / 1e6:+.2f}M vs PPA"),
    ], columns=["Strategy", "Solved-year effective price", "Total cost over solved years", "vs PPA"])
    st.dataframe(tbl, hide_index=True, width="stretch")


def _render_offtaker(result) -> None:
    prices = getattr(result, "market_prices", None)
    if prices is None:
        st.info("Re-run the optimisation to include market prices for offtaker analysis.")
        return
    st.subheader("Offtaker: run-of-plant wind and solar")
    st.caption(
        "Selected dispatch year only. The offtaker pays for all solved wind and solar generation, "
        "uses what coincides with load, buys shortfalls at spot and sells surplus at spot. "
        "Prices below apply to this year and default to its modelled PPA price. "
        "Changing them recalculates settlement costs without another optimisation."
    )
    st.info(
        "This is a settlement comparison using existing solved generation, including its curtailment. "
        "It does not re-optimise generation for a run-of-plant contract or treat available-but-curtailed "
        "energy as produced. Battery services are excluded: all wind and solar output is allocated "
        "directly against load before spot settlement. A battery-managed portfolio requires separate dispatch."
    )
    cols = st.columns(2)
    year = result.dispatch.wind_gen.index[0].year
    wind_price = cols[0].number_input(
        "Wind run-of-plant price (A$/MWh)", value=float(result.scenario.ppa_price),
        step=1.0, key=f"offtaker_wind_price_{year}",
    )
    solar_price = cols[1].number_input(
        "Solar run-of-plant price (A$/MWh)", value=float(result.scenario.ppa_price),
        step=1.0, key=f"offtaker_solar_price_{year}",
    )
    d = result.dispatch
    load = d.ppa_delivery + d.allowed_shortfall + d.penalty_gen
    dt = result.n_period_hours / len(load)
    hourly = run_of_plant_costs(load, d.wind_gen, d.pv_gen, prices, wind_price, solar_price, dt)
    hourly["Delivered PPA cost (A$)"] = (
        d.ppa_delivery * result.scenario.ppa_price
        + (load - d.ppa_delivery).clip(lower=0) * prices
    ) * dt
    totals = hourly.sum()
    energy = totals["Load (MWh)"]
    generation = totals["Wind (MWh)"] + totals["Solar (MWh)"]

    def display_ratio(numerator, denominator, *, percent=False):
        if denominator <= 0:
            return "n/a"
        value = numerator / denominator
        return f"{value:.1%}" if percent else f"A${value:,.2f}/MWh"

    net = totals["Net procurement cost (A$)"]
    spot_cost = totals["Spot-only cost (A$)"]
    cols = st.columns(4)
    cols[0].metric("Net procurement cost", _fmt_m(net))
    cols[1].metric("Cost per MWh of load", display_ratio(net, energy))
    cols[2].metric("Savings vs spot-only", _fmt_m(spot_cost - net))
    cols[3].metric("Load matched at each interval", display_ratio(totals["Matched (MWh)"], energy, percent=True))

    costs = pd.DataFrame([
        ("Wind PPA payments", totals["Wind PPA cost (A$)"]),
        ("Solar PPA payments", totals["Solar PPA cost (A$)"]),
        ("Shortfall spot purchases", totals["Shortfall cost (A$)"]),
        ("Surplus spot sales (deducted)", -totals["Surplus revenue (A$)"]),
        ("Net procurement cost", net),
        ("Spot-only benchmark", spot_cost),
        ("Existing delivered PPA + spot top-up", totals["Delivered PPA cost (A$)"]),
    ], columns=["Cost component", "A$"])
    cols = st.columns(2)
    cols[0].dataframe(costs, hide_index=True, width="stretch")
    volumes = pd.DataFrame([
        ("Load", energy), ("Contracted wind", totals["Wind (MWh)"]),
        ("Contracted solar", totals["Solar (MWh)"]),
        ("Generation matched to load", totals["Matched (MWh)"]),
        ("Shortfall purchased", totals["Shortfall (MWh)"]),
        ("Surplus sold", totals["Surplus (MWh)"]),
    ], columns=["Energy flow", "MWh"])
    cols[1].dataframe(volumes, hide_index=True, width="stretch")
    exposure = pd.DataFrame([
        ("Generation / load", display_ratio(generation, energy, percent=True)),
        ("Surplus / generation", display_ratio(totals["Surplus (MWh)"], generation, percent=True)),
        ("Wind capture price", display_ratio(totals["Wind spot value (A$)"], totals["Wind (MWh)"])),
        ("Solar capture price", display_ratio(totals["Solar spot value (A$)"], totals["Solar (MWh)"])),
        ("Shortfall purchase price", display_ratio(totals["Shortfall cost (A$)"], totals["Shortfall (MWh)"])),
        ("Surplus sale price", display_ratio(totals["Surplus revenue (A$)"], totals["Surplus (MWh)"])),
        ("Negative-price surplus cost (included above)", _fmt_m(totals["Negative-price surplus cost (A$)"])),
    ], columns=["Exposure metric", "Value"])
    st.dataframe(exposure, hide_index=True, width="stretch")
    st.caption(
        "Matching is calculated interval by interval, before storage; annual generation / load can "
        "exceed 100% while shortfalls remain. Capture prices are generation-weighted spot prices. "
        "Negative surplus revenue increases procurement cost. Undefined ratios display as n/a. "
        "Uses one common modelled spot price; excludes regional basis, losses, network charges, "
        "retailer/sleeving fees, certificates, taxes and contractual curtailment or negative-price clauses."
    )
    monthly = hourly.resample("MS").sum()
    fig = go.Figure()
    for label in ["Matched (MWh)", "Shortfall (MWh)", "Surplus (MWh)"]:
        fig.add_bar(x=monthly.index, y=monthly[label], name=label)
    fig.update_layout(title="Monthly load matching and surplus", barmode="group", yaxis_title="MWh")
    st.plotly_chart(fig, width="stretch")
    fig = go.Figure()
    for label in ["Net procurement cost (A$)", "Delivered PPA cost (A$)", "Spot-only cost (A$)"]:
        series = _downsample_series(hourly[label].cumsum())
        fig.add_scatter(x=series.index, y=series / 1e6, name=label, mode="lines")
    fig.update_layout(title="Cumulative procurement cost", yaxis_title="A$ million")
    st.plotly_chart(fig, width="stretch")
    csv_download_button(hourly, "offtaker_run_of_plant_intervals.csv", key="dl_offtaker_intervals")
    csv_download_button(monthly, "offtaker_run_of_plant_monthly.csv", key="dl_offtaker_monthly")


def _render_multi_year_deep_dive() -> None:
    results = state.get_multi_year_results()
    fin = state.get_multi_year_financial()
    # Use the sized capacities that produced these results.
    s = state.get_effective_scenario()

    render_config_summary(s)

    year_options = [result.dispatch.wind_gen.index[0].year for result in results]
    cols = st.columns(4)
    selected_year = cols[0].selectbox("**Dispatch year to analyse:**", year_options, key="dd_year")
    year_idx = year_options.index(selected_year)
    result = results[year_idx]

    available_days = sorted(result.dispatch.wind_gen.index.normalize().unique().strftime("%Y-%m-%d"))

    with st.expander("Detailed results", expanded=True):
        tab_chart1, tab_chart2, tab_chart3, tab_offtaker = st.tabs([
            "| Hourly dispatch",
            "| Generation statistics",
            "| Counterfactual procurement comparison",
            "| Offtaker run-of-plant",
        ])
        with tab_chart1:
            cols = st.columns(4)
            chosen_day = cols[0].selectbox("Day to inspect", available_days, index=0, key="dd_chosen_day1")
            _render_dispatch_section(result, result.scenario, chosen_day)
        with tab_chart2:
            st.caption(f"High-level generation statistics for {selected_year}")
            _render_gen_stats(result, result.scenario)
        with tab_chart3:
            _render_multi_year_counterfactuals(results, fin, s)
        with tab_offtaker:
            _render_offtaker(result)

    _render_sla_compliance(result, result.scenario)

    with st.expander(f"Financial summary for {selected_year}", expanded=False):
        yf = fin.yearly[year_idx]
        cols = st.columns(5)
        cols[0].metric("PPA Revenue", f"A${yf.ppa_revenue / 1e6:.2f}M")
        cols[1].metric("Merchant Revenue", f"A${yf.merch_revenue / 1e6:.2f}M")
        cols[2].metric("Net Cash Flow", f"A${yf.net_cashflow / 1e6:.2f}M")
        cols[3].metric("Delivery Rate", f"{yf.fulfilled_share:.1%}")
        cols[4].metric("Wind+PV Gen", f"{(yf.wind_gen_mwh + yf.pv_gen_mwh) / 1e3:.0f} GWh")

    with st.expander("Lifetime project economics", expanded=False):
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**CAPEX & OPEX**")
            capex_df = pd.DataFrame(
                [
                    ("Onshore wind", _fmt_m(fin.capex.capex_wind), f"{s.onsw_mw:.0f} MW × A${s.wind_capex_per_kw:,.0f}/kW"),
                    ("Solar PV", _fmt_m(fin.capex.capex_pv), f"{s.pv_mw:.0f} MW × A${s.pv_capex_per_kw:,.0f}/kW"),
                    ("BESS", _fmt_m(fin.capex.capex_bess), f"{s.effective_bess_mwh:.0f} MWh × A${s.bess_capex_per_kwh:,.0f}/kWh"),
                    ("Devex", _fmt_m(fin.capex.devex_total), f"{s.devex_pct_of_capex:.0%} of CAPEX"),
                    ("Total CAPEX", _fmt_m(fin.capex.capex_total), ""),
                    ("Total investment", _fmt_m(fin.capex.total_investment), ""),
                    ("Annual OPEX", _fmt_m(fin.annual_opex), "per-technology fixed O&M + maintenance capex"),
                ],
                columns=["Component", "Value", "Basis"],
            )
            st.dataframe(capex_df, hide_index=True, width="stretch")
        with cols[1]:
            st.markdown("**Project economics**")
            irr_str = f"{fin.irr:.1%}" if fin.irr == fin.irr else "n/a"
            lcoe_str = f"A${fin.lcoe:.2f}/MWh" if fin.lcoe == fin.lcoe else "n/a"
            payback_str = f"{fin.simple_payback:.1f} yrs" if fin.simple_payback < 1e8 else "n/a"
            avg_delivery = sum(y.fulfilled_share for y in fin.yearly) / len(fin.yearly) if fin.yearly else 0.0
            econ_df = pd.DataFrame(
                [
                    ("NPV", _fmt_m(fin.npv), f"at {s.discount_rate:.0%} WACC"),
                    ("Project IRR", irr_str, f"{s.project_life_yrs}-yr life"),
                    ("LCOE", lcoe_str, f"at {s.discount_rate:.0%} WACC"),
                    ("Simple payback", payback_str, ""),
                    ("Total lifetime revenue", _fmt_m(fin.total_lifetime_revenue), ""),
                    ("Total lifetime generation", f"{fin.total_lifetime_generation_mwh / 1e3:.0f} GWh", ""),
                    ("Achieved PPA delivery (avg)", f"{avg_delivery:.1%}",
                     f"vs {s.required_delivery_share:.0%} required"),
                    ("Breakeven PPA for target IRR",
                     f"A${fin.breakeven_ppa_price:.2f}/MWh" if fin.breakeven_ppa_price == fin.breakeven_ppa_price else "n/a",
                     f"at {s.target_irr:.0%} vs A${s.ppa_price:.0f}/MWh contracted"),
                ],
                columns=["Metric", "Value", "Note"],
            )
            st.dataframe(econ_df, hide_index=True, width="stretch")


def render() -> None:
    st.title("🔍 Detailed Results")

    if state.has_multi_year_results() and state.has_multi_year_financial():
        n = len(state.get_multi_year_financial().yearly)
        n_solved = len(state.get_multi_year_results())
        st.caption(f"Showing {n_solved} solved dispatch year(s) and {n} annual financial records.")
        s = state.get_effective_scenario()
        if s.dispatch_mode == "representative_year":
            from ppa.simulation import REPRESENTATIVE_YEAR_NOTE

            st.info(REPRESENTATIVE_YEAR_NOTE)
        _render_multi_year_deep_dive()
    else:
        st.info(
            "No results yet. Run the **Run optimisation** in the **Optimisation** tab "
            "to explore hourly dispatch for any day in any simulated year.",
            icon="⚙️",
        )

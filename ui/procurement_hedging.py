"""Streamlit view for hedging residual wholesale exposure."""
import math

import pandas as pd
import streamlit as st

from ppa.data import nem_data
from ppa.procurement_hedging import (
    FIVE_MINUTE_SETTLEMENT, Cap, Swap, daily_residual_cost, historical_cap_prices, settle_hedges,
)


@st.cache_data(show_spinner=False)
def _nsw_cap_prices(year):
    return historical_cap_prices(nem_data.load_regional_price("NSW1", year))


def render_hedging(result, uploaded_prices):
    dispatch = result["dispatch"]
    year = dispatch.index[0].year
    st.header("Residual load hedging")
    st.write(f"Price caps and fixed-price swaps on the {year} portfolio’s spot purchases less excess sales.")
    st.caption("Enter quoted prices to test a hedge book. Starting prices are illustrative. "
               "Set the contracted MW to zero to exclude a product.")

    cap_col, swap_col = st.columns(2)
    caps = {}
    swap_terms = {}
    with cap_col:
        st.subheader("Caps")
        for strike, example in [(300, 20.0), (600, 15.0)]:
            st.markdown(f"**A${strike}/MWh strike**")
            size, cost = st.columns(2)
            mw = size.number_input("Contracted MW", min_value=0.0, step=1.0, key=f"hedge_cap_mw_{strike}")
            premium = cost.number_input("Premium (A$/MWh)", min_value=0.0, value=example,
                                        key=f"hedge_cap_premium_{strike}",
                                        help="Paid on contracted MW in every hour of the model year.")
            caps[f"${strike} cap"] = Cap(mw, strike, premium)
    with swap_col:
        st.subheader("Fixed-price swaps")
        for key, label in [("base", "Baseload swap"), ("block", "Daily block swap")]:
            st.markdown(f"**{label}**")
            size, cost = st.columns(2)
            mw = size.number_input("Contracted MW", min_value=0.0, step=1.0, key=f"hedge_swap_mw_{key}")
            fixed = cost.number_input("Fixed price (A$/MWh)", value=100.0, key=f"hedge_swap_price_{key}")
            swap_terms[key] = (mw, fixed)
        start, end = st.columns(2)
        start_hour = start.selectbox("Block starts (AEST)", range(24), index=16,
                                     format_func=lambda h: f"{h:02d}:00")
        end_hour = end.selectbox("Block ends (AEST)", range(25), index=22,
                                 format_func=lambda h: f"{h:02d}:00")
    swaps = {
        "Baseload swap": Swap(*swap_terms["base"]),
        "Daily block swap": Swap(*swap_terms["block"], start_hour, end_hour),
    }
    if swaps["Daily block swap"].mw and start_hour == end_hour:
        st.error("Choose different start and end hours for the block swap.")
        return
    if uploaded_prices is not None:
        prices = uploaded_prices
        minutes = (prices.index[1] - prices.index[0]) / pd.Timedelta(minutes=1)
        price_note = f"Caps use the uploaded {minutes:g}-minute spot prices."
        if minutes == 60:
            price_note += " Hourly averages can miss short price spikes."
    else:
        prices = _nsw_cap_prices(year)
        price_note = "Caps use five-minute NSW spot prices."
        if year <= FIVE_MINUTE_SETTLEMENT.year:
            price_note = ("Caps use half-hourly averages of NSW dispatch prices as a proxy before "
                          "1 October 2021 and five-minute prices from that date." if year == 2021 else
                          "Caps use half-hourly averages of NSW dispatch prices as a proxy for historical settlement prices.")
    try:
        hourly, contracts, overhedged_hours = settle_hedges(dispatch, caps, swaps, prices)
    except ValueError:
        hourly, contracts, overhedged_hours = settle_hedges(dispatch, caps, swaps, dispatch["Spot price"])
        price_note = ("Caps use hourly scenario prices because the finer price series does not "
                      "reconcile to all dispatch hours. Hourly averages can miss short price spikes.")

    hedge_cost = contracts["Net hedge cost"].sum()
    annual_load = hourly.Load.sum()
    net_cost = result["net_cost"]
    st.subheader("Annual cost")
    before, after, difference = st.columns(3)
    before.metric("Portfolio before hedges", f"A${net_cost / annual_load:,.2f}/MWh")
    after.metric("Portfolio after hedges", f"A${(net_cost + hedge_cost) / annual_load:,.2f}/MWh")
    difference.metric("Net hedge cost", f"A${hedge_cost / annual_load:,.2f}/MWh",
                      help="Positive increases the portfolio cost. Negative reduces it.")

    active_contracts = [label for label, contract in {**caps, **swaps}.items() if contract.mw > 0]
    if active_contracts:
        with st.expander("Contract cash flows (A$)", expanded=True):
            active = contracts.loc[contracts.Contract.isin(active_contracts)]
            st.dataframe(active.style.format({name: "{:,.0f}" for name in active if name != "Contract"}),
                         hide_index=True, width="stretch")
            st.caption("Positive net hedge cost is a payment by the customer. Floating swap receipts can be negative.")
        st.caption(f"Hedge volume exceeded positive net spot exposure for {overhedged_hours:,.1f} equivalent hours. "
                   "This includes swaps in their delivery window and caps while the spot price is above their strike.")

    st.subheader("Residual spot cost and risk")
    daily = daily_residual_cost(hourly)
    tail_days = math.ceil(len(daily) * 0.05)
    risk = pd.DataFrame({
        "Annual residual cost (A$)": hourly[daily.columns].sum(),
        "Annual residual cost (A$/MWh of load)": hourly[daily.columns].sum() / annual_load,
        "Average of worst 5% of days (A$/MWh of load)": daily.apply(lambda cost: cost.nlargest(tail_days).mean()),
        "Worst day (A$/MWh of load)": daily.max(),
    }).rename_axis("Scenario").reset_index()
    st.dataframe(risk.style.format({name: "{:,.2f}" for name in risk if name != "Scenario"}),
                 hide_index=True, width="stretch")
    st.caption("Daily risk measures cover residual spot costs and hedge cash flows per MWh of customer load. "
               "Days with zero customer load are excluded from the daily measures.")
    with st.expander("Daily residual cost and worst day"):
        st.line_chart(daily, y_label="A$/MWh of load")
        worst_day = hourly.loc[str(daily["Before hedges"].idxmax().date())]
        st.markdown(f"**Worst day before hedging · {worst_day.index[0]:%d %b %Y}**")
        st.line_chart(worst_day[["Before hedges", "After hedges"]], y_label="A$ per hour")
    st.caption(f"{price_note} Dispatch stays hourly. The block swap runs daily in NEM time and can cross midnight.")

"""Five-page customer procurement workflow."""
from dataclasses import asdict, replace
import hashlib

import pandas as pd
import streamlit as st

from ppa.data import nem_data
from ppa.data_loader import build_upload_template, load_custom_upload, prepare_custom_timeseries
from ppa.procurement import (
    BANDS, BESS_EFFICIENCY_STORE, BESS_EFFICIENCY_DISPATCH,
    Procurement, optimise_procurement, validate_inputs,
)
from ppa.procurement_excel import export_procurement
from ppa.procurement_reporting import SUBTOTALS, cost_breakdown, financial_summary
from ui.procurement_charts import monthly_chart, hourly_chart, storage_chart
from ui.procurement_plants import plant_map


@st.cache_data
def _plants(year):
    plants = nem_data.list_eligible_plants(year=year, require_simulation_ready=True)
    if not nem_data.load_plant_years().empty:
        operational = nem_data.plants_operational_for_years((year,))
        plants = plants[plants.duid.isin(operational.duid)]
    return plants[plants.duid.map(lambda d: nem_data.has_availability(d, year))]


@st.cache_data
def _timeseries(pv, wind, year, load):
    solar, onshore = nem_data.get_cf_dicts(pv, wind, years=(year,))
    price = nem_data.get_price_dict("NSW1", years=(year,))[year]
    return pd.DataFrame({"ts_PVGen": solar[year], "ts_WindGen": onshore[year],
                         "ts_MktPrice": price, "ts_LoadMW": load})


def _percent(label, default, key, maximum=100.0):
    return st.number_input(label, min_value=0.0, max_value=float(maximum), value=float(default),
                           step=5.0, key=key) / 100


def _requirements():
    st.header("Procurement requirements")
    left, right = st.columns(2)
    with left:
        peak = st.number_input("Peak load (MW)", min_value=0.1, value=100.0, step=10.0, key="simple_peak")
    with right:
        utilisation = st.number_input("Load utilisation (%)", min_value=0.1, max_value=100.0,
                                      value=75.0, step=5.0, key="simple_util") / 100
    st.caption("Option to set custom load profile via CSV upload in next page")
    s = Procurement(peak_mw=peak, utilisation=utilisation)
    capacity_mode = st.radio("Capacity selection", ["Find optimal capacity", "Manually choose capacity"],
                             horizontal=True, key="simple_capacity_mode")
    if capacity_mode == "Manually choose capacity":
        with st.container(border=True):
            st.subheader("Manually choose capacity")
            cols = st.columns(4)
            capacities = []
            for col, label, default, key in zip(cols, ["PV capacity (MW)", "Wind capacity (MW)", "BESS power (MW)", "BESS energy (MWh)"],
                                               [200, 100, 25, 100.0], ["pv", "wind", "bess", "bess_energy"]):
                with col:
                    capacities.append(st.number_input(label, min_value=0.0 if key == "bess_energy" else 0, value=default,
                                                      step=5.0 if key == "bess_energy" else 1, key=f"simple_fixed_{key}"))
            st.caption("The selected capacities stay fixed. The model optimises hourly dispatch and reports the matching achieved; NSW and AEMO minimum targets are not imposed. PV and wind use MW; BESS uses both MW and MWh.")
            if capacities[2] > 0:
                st.caption(f"Chosen storage duration: {capacities[3] / capacities[2]:g} hours.")
        return replace(s, mode="Fixed", pv_mw=capacities[0], wind_mw=capacities[1],
                       bess_mw=capacities[2], bess_mwh=capacities[3])
    requirement_mode = st.radio("Procurement requirements", ["NSW", "AEMO"], horizontal=True,
                                key="simple_requirement_mode")
    if requirement_mode == "NSW":
        with st.container(border=True):
            st.subheader("New South Wales regulatory requirement")
            annual = _percent("Annual renewables volume matching requirement (%)", 100, "simple_nsw_annual", 300)
            wind = _percent("Wind share of contracted renewable energy (%)", 40, "simple_wind")
            storage = _percent("Peak load to be covered by storage (%)", 25, "simple_storage")
            duration = st.number_input("Storage duration (hours)", min_value=0.5, max_value=24.0,
                                       value=4.0, step=0.5, key="simple_nsw_duration")
            st.caption("Annual renewable generation, after curtailment, must meet the selected share of annual load. The selected wind share splits the annual volume target into minimum wind and PV volumes. Whole-MW sizing may slightly change the achieved mix; the results show the actual share. Storage is a minimum power reservation, with energy capacity = MW × hours.")
        return replace(s, mode="NSW", annual_match=annual, wind_share=wind, storage_share=storage, duration_h=duration)
    with st.container(border=True):
        st.subheader("AEMO proposed requirements")
        cols = st.columns(4)
        shares = []
        for i, (label, default) in enumerate(zip(BANDS, [20, 40, 40, 20])):
            with cols[i]:
                shares.append(_percent(f"{label} renewable coverage (%)", default, f"simple_band_{i}"))
        annual = _percent("Annual renewables volume matching requirement (%)", 100, "simple_aemo_annual", 300)
        duration = st.number_input("Storage duration (hours)", min_value=0.5, max_value=24.0,
                                   value=4.0, step=0.5, key="simple_aemo_duration")
        st.caption("Each target is the share of load served by renewables, including renewable-charged storage, within that time band aggregated over the year. Targets do not need to sum to 100%. Times use AEST, the NEM data clock. Storage is optional and sized when needed; no wind split is imposed.")
    return replace(s, mode="AEMO", annual_match=annual, band_shares=tuple(shares), duration_h=duration)


def _select_plants(s):
    st.header("Select plants & PPA tariffs")
    source = st.radio("Generation profiles", ["Cached NEM plants", "Upload my own template"], horizontal=True, key="simple_source")
    ts, description = None, ""
    try:
        if source == "Cached NEM plants":
            years = [year for year in nem_data.available_years()
                     if nem_data.price_path("NSW1", year).exists()]
            if not years and nem_data.load_plant_years().empty:
                years = nem_data.list_cached_price_years()
            if not years:
                st.info("No cached price year is available. Upload a template to continue.")
            else:
                year = st.selectbox("Historical model year", years, index=years.index(2025) if 2025 in years else len(years)-1, key="simple_year",
                                    help="Choose the year for both plant UIGF profiles and NSW spot prices. Only plants with a full operating year are selectable.")
                st.caption(f"Showing plants with a full operating year of UIGF data in {year}. Changing year refreshes the plant choices and capacity factors.")
                plants = plant_map(_plants(year), year)
                selected = {}
                for tech, label in [("Solar", "PV"), ("Wind", "Wind")]:
                    subset = plants[plants.fuel_tech == tech].sort_values(["region", "station_name"])
                    options = [None, *subset.duid.tolist()]
                    names = subset.set_index("duid")
                    nsw = subset[subset.region == "NSW1"].duid.tolist()
                    default = options.index(nsw[0]) if nsw else min(1, len(options)-1)
                    def display(duid, names=names):
                        if duid is None:
                            return "None"
                        row = names.loc[duid]
                        return f"{row.station_name} [{duid}] · {row.region}"
                    widget_key = f"simple_{label}_plant"
                    if widget_key not in st.session_state or st.session_state[widget_key] not in options:
                        st.session_state[widget_key] = options[default]
                    selected[label] = st.selectbox(f"{label} generation profile", options, index=0,
                                                    format_func=display, key=f"simple_{label}_plant")
                if any(selected.values()) or (s.mode == "Fixed" and s.pv_mw == 0 and s.wind_mw == 0):
                    ts = _timeseries(selected["PV"], selected["Wind"], year, s.peak_mw * s.utilisation)
                    description = f"{year} · PV: {selected['PV'] or 'none'} · Wind: {selected['Wind'] or 'none'} · NSW spot prices"
                    st.caption("Plant profiles use unconstrained UIGF availability and scale to the optimised contracted MW. All energy is settled at the NSW spot price; regional basis and transmission constraints are excluded.")
                else:
                    st.info("Select at least one generation profile.")
        else:
            st.download_button("Download hourly CSV template", build_upload_template(load_mw=s.peak_mw * s.utilisation),
                               "procurement_template_2025.csv", "text/csv")
            st.caption("Upload one full calendar year. Columns: timestamp, ts_PVGen and ts_WindGen (capacity factors 0–1), ts_LoadMW (MW), ts_MktPrice (A$/MWh). The template contains illustrative profiles; replace them with your data. Sub-hourly inputs are averaged to hourly data.")
            upload = st.file_uploader("Upload completed template", type="csv", key="simple_upload")
            if upload is not None:
                raw = load_custom_upload(upload)
                if raw.attrs.get("n_duplicate_timestamps", 0):
                    raise ValueError("Remove duplicate timestamps before uploading.")
                steps = raw.index.to_series().diff().dropna()
                if len(steps) == 0 or steps.nunique() != 1 or steps.iloc[0] > pd.Timedelta(hours=1):
                    raise ValueError("Use a continuous hourly or sub-hourly profile, with no timestamp gaps.")
                minutes = steps.iloc[0] / pd.Timedelta(minutes=1)
                if minutes <= 0 or 60 % minutes != 0:
                    raise ValueError("The input interval must divide one hour evenly.")
                ts = prepare_custom_timeseries(raw)
                description = f"Custom upload · {upload.name}"
    except (ValueError, FileNotFoundError, KeyError) as exc:
        st.error(str(exc))
        ts = None
    columns = st.columns(3)
    with columns[0]:
        pv = st.number_input("PV PPA tariff (A$/MWh)", min_value=0.01, value=80.0, step=5.0, key="simple_pv_price")
    with columns[1]:
        wind = st.number_input("Wind PPA tariff (A$/MWh)", min_value=0.01, value=130.0, step=5.0, key="simple_wind_price")
    with columns[2]:
        bess = st.number_input("BESS tolling (A$/MWh/month)", min_value=0.01, value=6000.0, step=100.0, key="simple_bess_price",
                               help="A fixed monthly fee per MWh of storage energy capacity, regardless of energy throughput.")
    st.caption(f"At {s.storage_duration_h:g} hours, the BESS fee is AUD {bess * s.storage_duration_h * 12:,.0f} per MW per year.")
    st.caption(f"PPA fees cover the full contracted profile, including deemed generation if curtailed. BESS round-trip efficiency is {BESS_EFFICIENCY_STORE * BESS_EFFICIENCY_DISPATCH:.0%} ({BESS_EFFICIENCY_STORE:.0%} charging × {BESS_EFFICIENCY_DISPATCH:.0%} discharging); charging is renewable-only. Prices are constant for this one-year comparison.")
    return replace(s, pv_tariff=pv, wind_tariff=wind, storage_tariff=bess), ts, description


def _summary(s, ts, description):
    load = float(ts.ts_LoadMW.sum()) if ts is not None else None
    entries = {"Portfolio construction": {"NSW": "New South Wales", "AEMO": "AEMO proposed", "Fixed": "Manually choose capacity"}[s.mode],
               "Data": description or "Select plants or upload data", "Peak load": f"{s.peak_mw:,.1f} MW",
               "Load utilisation": f"{(ts.ts_LoadMW.mean() / s.peak_mw if ts is not None else s.utilisation):.1%}",
               "Annual load": f"{load / 1000:,.1f} GWh" if load is not None else "—",
               "PV / wind tariffs": f"A${s.pv_tariff:,.0f} / A${s.wind_tariff:,.0f} per MWh",
               "BESS fee": f"A${s.storage_tariff:,.0f}/MWh capacity/month", "Storage duration": f"{s.storage_duration_h:g} hours"}
    if s.mode != "Fixed":
        entries["Annual renewable target"] = f"{s.annual_match:.0%}"
    if s.mode == "NSW":
        entries.update({"Wind energy share": f"{s.wind_share:.0%}",
                        "Minimum storage": f"{s.peak_mw * s.storage_share:,.1f} MW / {s.peak_mw * s.storage_share * s.duration_h:,.1f} MWh"})
    elif s.mode == "AEMO":
        entries.update({f"Coverage {band}": f"{share:.0%}" for band, share in zip(BANDS, s.band_shares)})
    else:
        entries.update({"PV capacity": f"{s.pv_mw:,.0f} MW", "Wind capacity": f"{s.wind_mw:,.0f} MW",
                        "BESS capacity": f"{s.bess_mw:,.0f} MW / {s.bess_mwh:,.2f} MWh"})
    st.dataframe(pd.DataFrame(entries.items(), columns=["Assumption", "Value"]), hide_index=True, width="stretch")


def _results(result):
    st.header("Results · data centre perspective")
    cols = st.columns(4)
    for col, label, value in zip(cols[:3], ["Spot-only cost", "Portfolio net cost", "Savings versus spot"],
                                  [result["spot_cost"], result["net_cost"], result["savings"]]):
        col.metric(label, f"A${value / 1e6:,.2f}m")
    caps, d, s = result["capacities"], result["dispatch"], result["assumptions"]
    cols[3].metric("Effective procurement cost", f"A${result['net_cost'] / d.Load.sum():,.2f}/MWh",
                   help="Net annual procurement cost divided by annual customer load served, including spot purchases for shortfalls. Exports and battery charging are excluded from the MWh denominator.")
    st.dataframe(pd.DataFrame({"Portfolio": ["PV", "Wind", "BESS power", "BESS energy"],
                               "Capacity": [caps["PV"], caps["Wind"], caps["BESS"], caps["BESS"] * s.storage_duration_h],
                               "Unit": ["MW", "MW", "MW", "MWh"]}), hide_index=True,
                 column_config={"Capacity": st.column_config.NumberColumn(format="localized")})
    if s.mode == "NSW":
        gross = float((d.PV + d.Wind).sum())
        st.caption(f"Achieved wind share of contracted renewable energy: {d.Wind.sum() / gross if gross else 0:.2%}. Capacities are whole MW.")
    st.subheader("Generation & load")
    st.plotly_chart(monthly_chart(d), width="stretch")
    with st.expander("Hourly generation & storage", expanded=False):
        first, last = d.index[0].date(), d.index[-1].date()
        start, end = st.slider("Time series view — date range to inspect", min_value=first, max_value=last,
                               value=(first, min(first + pd.Timedelta(days=7), last)), format="DD MMM",
                               key=f"simple_result_range_{d.index[0].year}")
        part = d.loc[str(start):str(end)]
        st.plotly_chart(hourly_chart(part), width="stretch")
        st.plotly_chart(storage_chart(part), width="stretch")
        st.caption("Charging is shown below zero. Generation includes energy used for charging or sold as excess; load is the dotted line. Drag the range slider to zoom further.")
    with st.expander("Requirements achieved", expanded=True):
        st.dataframe(result["matching"].style.format({"Achieved": "{:.1%}"}, na_rep="—"), hide_index=True, width="stretch")
        if not result["compliance"].empty:
            st.markdown("**Scenario targets**")
            st.dataframe(result["compliance"].style.format({"Target": "{:.1%}", "Achieved": "{:.1%}"}), hide_index=True, width="stretch")
        st.caption("Annual volume matching is generation after curtailment divided by annual load; it includes exports and counts stored energy only once. Wind share here is contracted wind generation divided by annual load, rather than total renewable generation. Time matching is the energy-weighted share of load served by renewables in the same hour, including renewable-charged BESS, aggregated over each band or the whole year. BESS percentage uses power (MW) divided by the specified peak load. A dash means there is no load in that band.")
    with st.expander("Financial analysis · procurement versus spot", expanded=True):
        summary = financial_summary(result).rename(columns={"A$": "A$ million"})
        summary["A$ million"] /= 1e6
        def emphasise(row):
            return ["font-weight: bold; border-top: 1px solid #A6B8C8" if row["Annual cash flow"] in SUBTOTALS else ""] * len(row)
        st.dataframe(summary.style.format({"A$ million": "{:,.2f}"}).apply(emphasise, axis=1), hide_index=True, width="stretch")
        st.markdown("**Net cost = PV PPA + wind PPA + BESS tolling + spot shortfalls − excess sales revenue.**")
        st.caption("Spot-only cost uses the same hourly load and spot prices. Negative-price purchases reduce cost. This compares wholesale energy costs for one historical year; network charges, retail margins, taxes, separate certificate purchases and financing are excluded.")
        st.markdown("**Procurement cost breakdown**")
        bridge = cost_breakdown(result)
        st.dataframe(bridge.style.format({"Quantity": "{:,.2f}", "Rate": "{:,.2f}", "Annual cost (A$)": "{:,.2f}"}),
                     hide_index=True, width="stretch")
        st.caption("Energy: GWh × 1,000 × tariff or volume-weighted spot price (A$/MWh). BESS: MWh capacity × monthly tolling × 12. Excess revenue is shown as a negative cost. Landed excess prices use the scenario's NSW spot prices without additional charges. PV/wind charging, curtailment and excess sales are allocated pro rata. BESS discharge is reserved for load, so BESS excess sales are zero.")
    st.download_button("Download results workbook", _workbook(result), "procurement_results.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("One workbook: expanded annual financials and matching statistics, plus hourly dispatch and source allocations.")


@st.cache_data(show_spinner=False)
def _workbook(result):
    return export_procurement(result)


def render():
    st.title("Data centre renewable procurement")
    intro, requirements, plants, run, results = st.tabs(["① Introduction", "② Requirements", "③ Plants & tariffs", "④ Optimisation", "⑤ Results"])
    with intro:
        st.write("Model the wind, solar and storage procurement needed to meet proposed government procurement requirements, optimised using the energy modelling software PyPSA and real operating plant generation profiles from the selected historical year.")
    with requirements:
        s = _requirements()
    with plants:
        s, ts, description = _select_plants(s)
    fingerprint = hashlib.sha256(b"procurement-v4" + repr(asdict(s)).encode() + description.encode() +
                                  (pd.util.hash_pandas_object(ts).values.tobytes() if ts is not None else b"")).hexdigest()
    with run:
        st.header("Optimisation")
        st.subheader("Scenario summary")
        _summary(s, ts, description)
        error = None
        if ts is not None:
            try:
                validate_inputs(ts, s)
            except ValueError as exc:
                error = str(exc)
                st.error(error)
        else:
            st.info("Choose generation profiles or upload data on Plants & tariffs.")
        if st.button("Run optimisation", type="primary", disabled=ts is None or error is not None):
            try:
                with st.spinner("Dispatching the chosen capacities over the full year…" if s.mode == "Fixed" else "Sizing the portfolio and dispatching the full year…"):
                    result = optimise_procurement(ts, s)
                st.session_state["simple_result"] = (fingerprint, result)
                st.success("Optimisation complete. Open Results to review the portfolio.")
            except ValueError as exc:
                st.error(str(exc))
    with results:
        saved = st.session_state.get("simple_result")
        if saved and saved[0] == fingerprint:
            _results(saved[1])
        elif saved:
            st.info("Inputs have changed. Run optimisation again to update the results.")
        else:
            st.info("Run optimisation to see the portfolio, generation charts and financial analysis.")

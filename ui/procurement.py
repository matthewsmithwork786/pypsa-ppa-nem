"""Five-page customer procurement workflow."""
from dataclasses import asdict, replace
import hashlib

import pandas as pd
import plotly.express as px
import streamlit as st

from ppa.data import nem_data
from ppa.data_loader import build_upload_template, load_custom_upload, prepare_custom_timeseries
from ppa.procurement import BANDS, Procurement, optimise_procurement, validate_inputs


@st.cache_data
def _plants(year):
    plants = nem_data.list_eligible_plants(year=year, require_simulation_ready=True)
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
    st.caption("Editable scenario definitions supplied for this model; the labels do not certify compliance with a regulation or proposal.")
    left, right = st.columns(2)
    with left:
        peak = st.number_input("Peak load (MW)", min_value=0.1, value=100.0, step=10.0, key="simple_peak")
    with right:
        utilisation = st.number_input("Load utilisation (%)", min_value=0.1, max_value=100.0,
                                      value=90.0, step=5.0, key="simple_util") / 100
    st.caption(f"Default load is flat at {peak * utilisation:,.1f} MW. A custom upload replaces this with its hourly load profile.")
    mode = st.radio("Construct the portfolio using", ["New South Wales regulatory requirement", "AEMO proposed requirements"], key="simple_mode")
    with st.expander("Option 1 · New South Wales regulatory requirement", expanded=mode.startswith("New")):
        annual_nsw = _percent("Annual renewables volume matching requirement (%)", 100, "simple_nsw_annual", 300)
        wind = _percent("Wind share of contracted renewable energy (%)", 40, "simple_wind")
        storage = _percent("Peak load to be covered by storage (%)", 25, "simple_storage")
        nsw_duration = st.number_input("Storage duration (hours)", min_value=0.5, max_value=24.0,
                                       value=4.0, step=0.5, key="simple_nsw_duration")
        st.caption("Annual renewable generation, after curtailment, must meet the selected share of annual load. Wind share refers to energy, not installed MW. Storage is a minimum power reservation, with energy capacity = MW × hours.")
    with st.expander("Option 2 · AEMO proposed requirements", expanded=mode.startswith("AEMO")):
        cols = st.columns(4)
        shares = []
        for i, (label, default) in enumerate(zip(BANDS, [20, 40, 40, 20])):
            with cols[i]:
                shares.append(_percent(f"{label} renewable coverage (%)", default, f"simple_band_{i}"))
        annual_aemo = _percent("Annual renewables volume matching requirement (%)", 100, "simple_aemo_annual", 300)
        aemo_duration = st.number_input("Storage duration (hours)", min_value=0.5, max_value=24.0,
                                        value=4.0, step=0.5, key="simple_aemo_duration")
        st.caption("Each target is the share of load served by renewables, including renewable-charged storage, within that time band aggregated over the year. Targets do not need to sum to 100%. Times use AEST, the NEM data clock. Storage is optional and sized when needed; no wind split is imposed.")
    return Procurement(mode="NSW" if mode.startswith("New") else "AEMO", peak_mw=peak,
                       utilisation=utilisation, annual_match=annual_nsw if mode.startswith("New") else annual_aemo,
                       wind_share=wind, storage_share=storage, band_shares=tuple(shares),
                       duration_h=nsw_duration if mode.startswith("New") else aemo_duration)


def _select_plants(s):
    st.header("Select plants & PPA tariffs")
    source = st.radio("Generation profiles", ["Cached NEM plants", "Upload my own template"], horizontal=True, key="simple_source")
    ts, description = None, ""
    try:
        if source == "Cached NEM plants":
            years = nem_data.list_cached_price_years()
            if not years:
                st.info("No cached price year is available. Upload a template to continue.")
            else:
                year = st.selectbox("Historical model year", years, index=years.index(2025) if 2025 in years else len(years)-1, key="simple_year")
                plants = _plants(year)
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
                    selected[label] = st.selectbox(f"{label} generation profile", options, index=default,
                                                    format_func=display, key=f"simple_{label}_plant")
                if any(selected.values()):
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
        bess = st.number_input("BESS fee (A$/MWh capacity/month)", min_value=0.01, value=2000.0, step=100.0, key="simple_bess_price",
                               help="A fixed monthly fee per MWh of storage energy capacity, regardless of energy throughput.")
    st.caption(f"At {s.duration_h:g} hours, the BESS fee is A${bess * s.duration_h * 12:,.0f} per MW per year. A$2,000 is a provisional input, not a market quote.")
    st.caption("PPA fees cover the full contracted profile, including deemed generation if curtailed. BESS round-trip efficiency is 90.25%; charging is renewable-only. Prices are constant for this one-year comparison.")
    return replace(s, pv_tariff=pv, wind_tariff=wind, storage_tariff=bess), ts, description


def _summary(s, ts, description):
    load = float(ts.ts_LoadMW.sum()) if ts is not None else None
    entries = {"Requirements": "New South Wales" if s.mode == "NSW" else "AEMO proposed",
               "Data": description or "Select plants or upload data", "Peak load": f"{s.peak_mw:,.1f} MW",
               "Load utilisation": f"{(ts.ts_LoadMW.mean() / s.peak_mw if ts is not None else s.utilisation):.1%}",
               "Annual load": f"{load / 1000:,.1f} GWh" if load is not None else "—",
               "Annual renewable target": f"{s.annual_match:.0%}",
               "PV / wind tariffs": f"A${s.pv_tariff:,.0f} / A${s.wind_tariff:,.0f} per MWh",
               "BESS fee": f"A${s.storage_tariff:,.0f}/MWh capacity/month", "Storage duration": f"{s.duration_h:g} hours"}
    if s.mode == "NSW":
        entries.update({"Wind energy share": f"{s.wind_share:.0%}",
                        "Minimum storage": f"{s.peak_mw * s.storage_share:,.1f} MW / {s.peak_mw * s.storage_share * s.duration_h:,.1f} MWh"})
    else:
        entries.update({f"Coverage {band}": f"{share:.0%}" for band, share in zip(BANDS, s.band_shares)})
    st.dataframe(pd.DataFrame(entries.items(), columns=["Assumption", "Value"]), hide_index=True, width="stretch")


def _results(result):
    st.header("Results · data centre perspective")
    cols = st.columns(3)
    for col, label, value in zip(cols, ["Spot-only cost", "Portfolio net cost", "Savings versus spot"],
                                  [result["spot_cost"], result["net_cost"], result["savings"]]):
        col.metric(label, f"A${value / 1e6:,.2f}m")
    caps, d, s = result["capacities"], result["dispatch"], result["assumptions"]
    st.dataframe(pd.DataFrame({"Portfolio": ["PV", "Wind", "BESS power", "BESS energy"],
                               "Capacity": [caps["PV"], caps["Wind"], caps["BESS"], caps["BESS"] * s.duration_h],
                               "Unit": ["MW", "MW", "MW", "MWh"]}), hide_index=True,
                 column_config={"Capacity": st.column_config.NumberColumn(format="localized")})
    st.subheader("Generation & load")
    monthly = d[["PV", "Wind", "Load", "Renewable delivery", "Spot purchases", "Excess sold"]].resample("MS").sum() / 1000
    monthly.index = monthly.index.strftime("%b")
    st.plotly_chart(px.bar(monthly[["PV", "Wind"]], barmode="stack", labels={"value": "Generation (GWh)", "index": "Month", "variable": "Source"}), width="stretch")
    st.plotly_chart(px.bar(monthly[["Renewable delivery", "Spot purchases"]], barmode="stack", labels={"value": "Load served (GWh)", "index": "Month", "variable": "Source"}), width="stretch")
    with st.expander("Hourly generation & storage", expanded=False):
        day = st.date_input("Inspect a day", value=d.index[0].date(), min_value=d.index[0].date(), max_value=d.index[-1].date(), key=f"simple_result_day_{d.index[0].year}")
        part = d.loc[str(day)]
        st.plotly_chart(px.line(part[["Load", "PV", "Wind", "Battery charge", "Battery discharge", "Spot purchases", "Excess sold"]], labels={"value": "Power (MW)", "index": "AEST"}), width="stretch")
        st.plotly_chart(px.line(part[["Stored energy"]], labels={"value": "Energy (MWh)", "index": "AEST"}), width="stretch")
    with st.expander("Requirements achieved", expanded=True):
        st.dataframe(result["compliance"].style.format({"Target": "{:.1%}", "Achieved": "{:.1%}"}), hide_index=True, width="stretch")
        st.caption("Annual matching counts generation after curtailment, including exported energy; it is not hourly load coverage. Battery discharge is excluded from that generation total. Time-band coverage counts only renewable energy delivered to load.")
    with st.expander("Financial analysis · procurement versus spot", expanded=True):
        fees = result["fees"]
        rows = [(name, -value if name == "Excess revenue" else value) for name, value in fees.items()]
        rows += [("Portfolio net cost", result["net_cost"]), ("Spot-only counterfactual", result["spot_cost"]), ("Savings versus spot", result["savings"])]
        st.dataframe(pd.DataFrame([(name, value / 1e6) for name, value in rows], columns=["Annual cash flow", "A$ million"]).style.format({"A$ million": "{:,.3f}"}), hide_index=True, width="stretch")
        st.markdown("**Net cost = PV PPA + wind PPA + BESS fee − excess sales revenue + spot purchases for shortfalls.**")
        st.caption("Spot-only cost uses the same hourly load and spot prices. Negative-price purchases reduce cost. This compares wholesale energy costs for one historical year; network charges, retail margins, taxes, separate certificate purchases and financing are excluded.")
        st.metric("Effective procurement cost", f"A${result['net_cost'] / d.Load.sum():,.2f}/MWh")
    with st.expander("Download results"):
        st.download_button("Hourly dispatch CSV", d.to_csv().encode(), "procurement_hourly.csv", "text/csv")
        st.download_button("Annual financial analysis CSV", pd.DataFrame(rows, columns=["Annual cash flow", "A$"]).to_csv(index=False).encode(), "procurement_financials.csv", "text/csv")


def render():
    st.title("Data centre renewable procurement")
    intro, requirements, plants, run, results = st.tabs(["① Introduction", "② Requirements", "③ Plants & tariffs", "④ Optimisation", "⑤ Results"])
    with intro:
        st.header("Build a renewable procurement portfolio")
        st.write("Set the data centre's load and renewable requirements, choose wind and solar profiles, and enter procurement tariffs. The model sizes the contracted portfolio and compares its annual cost with buying all load from the spot market.")
        st.markdown("1. Choose the NSW or AEMO scenario and set its targets.\n2. Select cached plants or upload your hourly template; set tariffs.\n3. Review the scenario and run optimisation.\n4. Explore generation, storage, compliance and customer costs.")
        st.info("The model first minimises annual contracted PPA and storage fees while satisfying your requirements. It then fixes those capacities and dispatches storage to minimise net spot-market costs. This makes procurement requirements, rather than speculative export revenue, drive portfolio size.")
    with requirements:
        s = _requirements()
    with plants:
        s, ts, description = _select_plants(s)
    fingerprint = hashlib.sha256(repr(asdict(s)).encode() + description.encode() +
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
                with st.spinner("Sizing the portfolio and dispatching the full year…"):
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

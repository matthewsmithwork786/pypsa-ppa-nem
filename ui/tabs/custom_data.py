"""Custom Data — upload a user-supplied CSV timeseries to drive the optimizer.

Pure helper functions (no Streamlit calls) live at module level so they're
independently unit-testable; `render()` wires them into the Streamlit UI,
mirroring the style established in `ui/tabs/nem_map.py`.
"""
from __future__ import annotations

import dataclasses
import io
import math

import pandas as pd
import streamlit as st

from ppa import data_loader
from ui import state
from ui.scenario_form import PPALOAD_MW_MAX

_PRICE_MAX_SANE = 20_000.0
_PRICE_MIN_SANE = -1_000.0


# ── Pure helpers (no Streamlit) ────────────────────────────────────────────────

def _warnings_for(diag: dict) -> list[tuple[str, str]]:
    """Return a list of (level, message) tuples for the given diagnostics dict.

    level is one of "warning" or "info".
    """
    out: list[tuple[str, str]] = []

    if diag.get("is_sub_hourly"):
        out.append((
            "warning",
            f"Sub-hourly data detected (modal step {diag['modal_step']}) — "
            "it will be resampled to hourly means before dispatch.",
        ))
    elif not diag.get("is_hourly"):
        out.append((
            "warning",
            f"Super-hourly data detected (modal step {diag['modal_step']}) — "
            "each row will be treated as one dispatch snapshot.",
        ))

    if diag.get("n_gaps", 0) > 0:
        out.append((
            "warning",
            f"{diag['n_gaps']} gap(s) found in the timestamp index at the modal "
            f"cadence ({diag['modal_step']}).",
        ))

    if diag.get("n_duplicate_timestamps", 0) > 0:
        out.append((
            "warning",
            f"{diag['n_duplicate_timestamps']} duplicate timestamp(s) found — "
            "the last value for each was kept.",
        ))

    if not diag.get("is_full_year", False):
        out.append((
            "warning",
            f"Uploaded data spans {diag.get('span_days', 0)} day(s), not a full "
            "year — a multi-year run will repeat this full uploaded pattern "
            "end-to-end to fill out the year, not just the most recent day.",
        ))

    price_max = diag.get("price_max", 0.0)
    price_min = diag.get("price_min", 0.0)
    if price_max > _PRICE_MAX_SANE or price_min < _PRICE_MIN_SANE:
        out.append((
            "warning",
            f"Price range (${price_min:.0f} to ${price_max:.0f}/MWh) is outside "
            f"a sane NEM range (${_PRICE_MIN_SANE:.0f} to ${_PRICE_MAX_SANE:.0f}/MWh) "
            "— double check the units.",
        ))

    if diag.get("negative_price_hours", 0) > 0:
        out.append((
            "info",
            f"{diag['negative_price_hours']} hour(s) of negative prices — "
            "legitimate in the NEM, no action needed.",
        ))

    return out


# ── Streamlit render ───────────────────────────────────────────────────────────

def render() -> None:
    st.title("📤 Custom Data")
    st.markdown(
        "Upload your own timeseries to drive the optimizer instead of European "
        "or NEM plant data. Required columns:\n\n"
        "- `timestamp` — hourly (or finer) datetime\n"
        "- `ts_PVGen`, `ts_WindGen` — capacity factors in **[0, 1]**\n"
        "- `ts_LoadMW` — offtaker load in **absolute MW** (not a 0–1 profile)\n"
        "- `ts_MktPrice` — market price in **A$/MWh** (negative values allowed)\n\n"
        "Applying an upload **overrides both the NEM Plant Map selection and the "
        "load-profile selector** — the uploaded `ts_LoadMW` column is used directly."
    )

    if state.has_custom_upload():
        upload = state.get_custom_upload()
        st.success(
            f"Active custom upload: **{upload['name']}** "
            f"({len(upload['ts'])} rows)"
        )

    # ── Template ────────────────────────────────────────────────────────────
    st.subheader("1. Download the template")
    template_bytes = data_loader.build_upload_template()
    st.download_button(
        "⬇️ Download CSV template",
        data=template_bytes,
        file_name="ppa_custom_timeseries_template.csv",
        mime="text/csv",
        key="cd_template_dl",
    )
    preview = pd.read_csv(io.BytesIO(template_bytes)).head(5)
    st.dataframe(preview, width="stretch")

    # ── Upload ──────────────────────────────────────────────────────────────
    st.subheader("2. Upload your filled-in CSV")
    uploaded_file = st.file_uploader("Upload a filled-in CSV", type=["csv"], key="cd_uploader")

    if uploaded_file is None:
        return

    try:
        raw_ts = data_loader.load_custom_upload(uploaded_file)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state["cd_raw_ts"] = raw_ts
    st.session_state["cd_upload_name"] = uploaded_file.name

    diag = data_loader.describe_custom_timeseries(raw_ts)

    st.subheader("3. Review")
    cols = st.columns(6)
    cols[0].metric("Rows", diag["n_rows"])
    cols[1].metric("Date range", f"{diag['first'].date()} → {diag['last'].date()}")
    cols[2].metric("PV CF mean", f"{diag['pv_cf_mean']:.2f}")
    cols[3].metric("Wind CF mean", f"{diag['wind_cf_mean']:.2f}")
    cols[4].metric("Load mean / peak MW", f"{diag['load_mw_mean']:.0f} / {diag['load_mw_peak']:.0f}")
    cols[5].metric("Price mean", f"A${diag['price_mean']:.1f}/MWh")

    for level, msg in _warnings_for(diag):
        if level == "info":
            st.info(msg)
        else:
            st.warning(msg)

    st.markdown("**Preview (first 2 weeks)**")
    preview_n = min(336, len(raw_ts))
    st.line_chart(raw_ts[["ts_PVGen", "ts_WindGen"]].iloc[:preview_n])
    st.line_chart(raw_ts[["ts_LoadMW"]].iloc[:preview_n])
    st.line_chart(raw_ts[["ts_MktPrice"]].iloc[:preview_n])

    st.markdown("**Raw data (first 24 rows)**")
    st.dataframe(raw_ts.head(24), width="stretch")

    # ── Actions ─────────────────────────────────────────────────────────────
    cols = st.columns(2)
    with cols[0]:
        if st.button("✅ Use this data", type="primary", width="stretch", key="cd_use"):
            prepared = data_loader.prepare_custom_timeseries(raw_ts)
            peak_mw = max(1.0, math.ceil(prepared["ppaload_mw"].max()))
            if peak_mw > PPALOAD_MW_MAX:
                # Defense in depth: the Case Setup form's number_input caps at
                # PPALOAD_MW_MAX. Clamping here (rather than crashing that tab
                # on its next render) trades a hard crash for a clearly-flagged
                # LP-feasibility risk the user can see immediately.
                st.warning(
                    f"Uploaded peak load ({peak_mw:,.0f} MW) exceeds the maximum "
                    f"supported PPA offtake load ({PPALOAD_MW_MAX:,.0f} MW). "
                    "Clamping to the maximum — the optimization may become "
                    "infeasible at some hours if actual load exceeds this cap."
                )
                peak_mw = PPALOAD_MW_MAX
            mid_idx = prepared.index[len(prepared) // 2]
            chosen_day = str(mid_idx.date())

            current = state.get_scenario()
            if current is None:
                from ppa.scenario import BASE_SCENARIO
                current = BASE_SCENARIO
            updated = dataclasses.replace(
                current,
                data_source="custom_csv",
                ppaload_mw=peak_mw,
                chosen_day=chosen_day,
            )

            state.set_scenario(updated)
            state.clear_nem_selection()
            state.clear_run_outputs()
            state.set_custom_upload({
                "name": st.session_state.get("cd_upload_name", "uploaded.csv"),
                "ts": prepared,
                "diagnostics": diag,
            })
            state.set_timeseries(prepared)
            st.rerun()
    with cols[1]:
        if st.button("↩️ Clear custom data", width="stretch", key="cd_clear"):
            current = state.get_scenario()
            if current is not None:
                new_source = "nem_map" if state.get_nem_selection() else "european"
                state.set_scenario(dataclasses.replace(current, data_source=new_source))
            state.clear_custom_upload()
            state.clear_run_outputs()
            st.session_state.pop("cd_raw_ts", None)
            st.session_state.pop("cd_upload_name", None)
            st.rerun()

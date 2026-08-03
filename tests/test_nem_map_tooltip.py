"""W7 regression: the NEM map tooltip must include the 2025 CUF % and the date
of first power (or a documented '—' fallback), stay unique per DUID, be
deterministic across reruns (no bare `nan`), and keep `_duid_from_tooltip`
round-tripping.
"""
from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("streamlit")  # nem_map imports streamlit at module load time

from ui.tabs import nem_map

CUF_FALLBACK = "—"


def _plants_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"duid": "WINDA", "station_name": "Windy Hill", "region": "NSW1",
             "fuel_tech": "Wind", "capacity_registered_mw": 100.0,
             "mean_cf": 0.384, "first_power_date": "2018-03-12"},
            {"duid": "WINDB", "station_name": "Windy Hill", "region": "NSW1",
             "fuel_tech": "Wind", "capacity_registered_mw": 120.0,
             "mean_cf": 0.412, "first_power_date": "2020-01-01"},
            {"duid": "SOLAR1", "station_name": "Sunny Park", "region": "QLD1",
             "fuel_tech": "Solar", "capacity_registered_mw": 200.0,
             "mean_cf": None, "first_power_date": None},
        ]
    )


def test_tooltip_contains_cuf_percent_and_first_power():
    df = _plants_df()
    tip = nem_map._tooltip(df.iloc[0])
    assert "CUF 38.4%" in tip
    assert "1st power 2018-03-12" in tip
    # format must match the plan's spec: NN.N% and a date (or '—')
    assert "2025 CUF" not in tip  # label is plain "CUF", year is implicit


def test_tooltip_fallback_for_missing_cuf_or_first_power():
    df = _plants_df()
    tip = nem_map._tooltip(df.iloc[2])  # mean_cf None, first_power None
    assert CUF_FALLBACK in tip  # documented fallback, not bare nan
    assert "nan" not in tip.lower()


def test_tooltip_labels_scada_derived_date_as_first_2025_output():
    # No registry first_power_date, but a SCADA-derived first-output date:
    # must be labelled "first 2025 output", not "1st power".
    df = _plants_df()
    df.at[0, "first_power_date"] = None
    df.at[0, "first_output_date"] = "2025-01-01"
    tip = nem_map._tooltip(df.iloc[0])
    assert "first 2025 output 2025-01-01" in tip
    assert "1st power" not in tip


def test_tooltip_unique_per_duid_and_deterministic():
    df = _plants_df()
    tips = [nem_map._tooltip(row) for _, row in df.iterrows()]
    assert len(tips) == len(set(tips)), "tooltips must stay unique per DUID"
    # Deterministic across reruns (no NaN formatting / unstable floats)
    tips_again = [nem_map._tooltip(row) for _, row in df.iterrows()]
    assert tips == tips_again


def test_duid_from_tooltip_roundtrips_new_format():
    df = _plants_df()
    for _, row in df.iterrows():
        assert nem_map._duid_from_tooltip(nem_map._tooltip(row), df) == row["duid"]


# ── UIGF terminology + explainer (post-SCADA-removal) ────────────────────────

def test_no_stale_scada_labels_in_map_ui():
    """User-facing map text must say UIGF, not SCADA.

    The shipped cache carries UIGF only, so a SCADA label names a data source
    the app no longer has. Real code identifiers and script names are exempt:
    scada_summary is a function, and fetch_nem_scada_prices.py still fetches
    the regional PRICE series, which has nothing to do with generation.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "ui" / "tabs" / "nem_map.py"
    allowed = ("no_scada", "n_scada_cached", "scada_summary", "fetch_nem_scada_prices")
    hits = [
        f"{i}: {ln.strip()}"
        for i, ln in enumerate(src.read_text().splitlines(), 1)
        if re.search(r"scada", ln, re.I) and not any(a in ln for a in allowed)
    ]
    assert not hits, "stale SCADA wording in user-facing map text:\n" + "\n".join(hits)


def test_uigf_explainer_states_what_it_is_and_where_it_comes_from():
    """Deliberately one factual line, not an essay.

    It has to answer "what is this" at the point of confusion: the expansion of
    the acronym, that it is AEMO's, the table it is recorded in, the interval,
    and that it precedes network constraints.
    """
    from ui.tabs.nem_map import UIGF_EXPLAINER

    lower = UIGF_EXPLAINER.lower()
    assert "unconstrained intermittent generation forecast" in lower
    assert "aemo" in lower
    assert "dispatchload" in lower
    assert "5-minute" in lower
    assert "before any network constraint" in lower
    assert len(UIGF_EXPLAINER) < 400, "explainer should stay a single line"


def test_plants_without_uigf_are_not_simulation_ready():
    """The 5 pre-semi-scheduling wind farms have no UIGF and must not be
    offered as selectable plants."""
    from ppa.data import nem_data

    try:
        df = nem_data.list_eligible_plants(year=2025, check_whole_year=True)
    except FileNotFoundError:
        pytest.skip("NEM registry cache not present")

    no_uigf = {"CAPTL_WF", "CHALLHWF", "PORTWF", "WAUBRAWF", "WOOLNTH1"}
    offered = set(df.loc[df["simulation_ready"], "duid"])
    assert not (no_uigf & offered), (
        f"plants with no UIGF are still being offered: {sorted(no_uigf & offered)}"
    )

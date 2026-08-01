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


@pytest.mark.xfail(strict=True, reason="W7: tooltip lacks CUF % and first-power date")
def test_tooltip_contains_cuf_percent_and_first_power():
    df = _plants_df()
    tip = nem_map._tooltip(df.iloc[0])
    assert "CUF 38.4%" in tip
    assert "1st power 2018-03-12" in tip
    # format must match the plan's spec: NN.N% and a date (or '—')
    assert "2025 CUF" not in tip  # label is plain "CUF", year is implicit


@pytest.mark.xfail(strict=True, reason="W7: tooltip lacks CUF/first-power fallback handling")
def test_tooltip_fallback_for_missing_cuf_or_first_power():
    df = _plants_df()
    tip = nem_map._tooltip(df.iloc[2])  # mean_cf None, first_power None
    assert CUF_FALLBACK in tip  # documented fallback, not bare nan
    assert "nan" not in tip.lower()


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

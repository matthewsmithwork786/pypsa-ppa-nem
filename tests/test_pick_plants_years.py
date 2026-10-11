"""WP7 tests: the NEM map plant picker driven by the multi-year availability
manifest, with the single-year eligibility-cache fallback.

The module under test imports streamlit, so every test uses the importorskip
pattern established in test_nem_map_tab.py. All tests here are pure-helper
tests; none depend on the real multi-year manifest (which may not exist yet).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")  # nem_map imports streamlit at module load time

from ui.tabs import nem_map
from ppa.data import nem_data

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_REGISTRY_DIR = REPO_ROOT / "data" / "cache" / "nem"


@pytest.fixture()
def real_registry():
    path = REAL_REGISTRY_DIR / "registry" / "nem_plant_registry.parquet"
    if not path.exists():
        pytest.skip("Real NEM registry parquet not present in this checkout.")
    return nem_data.load_plant_registry(REAL_REGISTRY_DIR)


def _eligible_frame() -> pd.DataFrame:
    """Two Wind plants, both single-year-ready, one of them the multi-year
    operational one in the synthetic manifest tests below."""
    return pd.DataFrame([
        {"duid": "WINDA", "station_name": "Windy A", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "status": "operating",
         "lat": -33.0, "lon": 150.0,
         "simulation_ready": True, "data_status": "ready", "cuf": 0.40},
        {"duid": "WINDB", "station_name": "Windy B", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 120.0, "status": "operating",
         "lat": -33.5, "lon": 150.5,
         "simulation_ready": True, "data_status": "ready", "cuf": 0.35},
    ])


def test_year_range_excludes_plant_not_operational_in_every_year(monkeypatch):
    """A plant missing from the manifest for any one selected year must be
    excluded from the operational set (and hence unselectable), even though it
    is single-year-ready. Synthetic manifest via monkeypatching."""
    def fake_plants_operational_for_years(years, cache_dir=None, registry=None):
        rows = [
            {"duid": "WINDA", "mean_cuf_selected_years": 0.40},
            {"duid": "WINDB", "mean_cuf_selected_years": 0.35},
        ]
        if 2024 in years:  # WINDB not operational in 2024
            rows = [r for r in rows if r["duid"] != "WINDB"]
        return pd.DataFrame(rows)

    monkeypatch.setattr(nem_map.nem_data, "plants_operational_for_years",
                        fake_plants_operational_for_years)
    nem_map._cached_operational_plants.clear()

    operational = nem_map._cached_operational_plants((2025,), (1,))
    assert set(operational["duid"]) == {"WINDA", "WINDB"}

    operational_2024_25 = nem_map._cached_operational_plants((2024, 2025), (2,))
    assert list(operational_2024_25["duid"]) == ["WINDA"], \
        "WINDB must be excluded: not operational in every selected year"

    combined = nem_map._plants_for_map(_eligible_frame(), operational_2024_25)
    ready = set(combined.loc[combined["simulation_ready"], "duid"])
    assert ready == {"WINDA"}
    assert list(nem_map._selectable_duids(combined, "Wind")) == ["WINDA"]
    # WINDB still visible on the map, but grey-dashed and unselectable.
    assert (combined.loc[combined["duid"] == "WINDB", "data_status"] == "unchecked").all()
    assert (combined.loc[combined["duid"] == "WINDB", "simulation_ready"] == False).all()


def test_empty_year_selection_disables_use_button():
    """With no selected years the picker cannot commit a scenario: the button
    must be disabled even when a (single-year-ready) plant is selected."""
    df = _eligible_frame()
    assert nem_map._use_disabled((), df, "WINDA", "") is True
    assert nem_map._use_disabled((), df, "", "WINDA") is True
    # Sanity: a non-empty selection with a ready plant is not disabled.
    assert nem_map._use_disabled((2025,), df, "WINDA", "") is False
    # No plant chosen at all disables too.
    assert nem_map._use_disabled((2025,), df, "", "") is True
    # A chosen plant that is not simulation-ready disables.
    not_ready = pd.DataFrame([{"duid": "WINDA", "simulation_ready": False}])
    assert nem_map._use_disabled((2025,), not_ready, "WINDA", "") is True


def test_empty_operational_manifest_falls_back_to_single_year(monkeypatch):
    """When the multi-year manifest is absent (or returns an empty frame), the
    picker must fall back to the single-year eligibility cache unchanged rather
    than showing zero plants or crashing."""
    nem_map._cached_operational_plants.clear()
    monkeypatch.setattr(nem_map.nem_data, "plants_operational_for_years",
                        lambda years, cache_dir=None, registry=None: pd.DataFrame())

    operational = nem_map._cached_operational_plants((2025,), (3,))
    assert operational.empty

    eligible = _eligible_frame()
    combined = nem_map._plants_for_map(eligible, operational)
    pd.testing.assert_frame_equal(combined, eligible)
    # The single-year selectability survives the fallback.
    assert list(nem_map._selectable_duids(combined, "Wind")) == ["WINDA", "WINDB"]


def test_tooltip_uses_mean_cuf_selected_years_when_present():
    df = pd.DataFrame([
        {"duid": "WINDA", "station_name": "Windy A", "region": "NSW1",
         "capacity_registered_mw": 100.0,
         "mean_cuf_selected_years": 0.32, "cuf": 0.40},
    ])
    tip = nem_map._tooltip(df.iloc[0])
    assert "mean CUF (selected years) 32.0%" in tip
    assert "CUF 32.0%" not in tip


def test_tooltip_falls_back_to_single_year_cuf():
    """The fallback path (no mean_cuf_selected_years column) must keep the
    existing single-year "CUF" label and value."""
    df = pd.DataFrame([
        {"duid": "WINDA", "station_name": "Windy A", "region": "NSW1",
         "capacity_registered_mw": 100.0, "cuf": 0.40},
    ])
    tip = nem_map._tooltip(df.iloc[0])
    assert "CUF 40.0%" in tip
    assert "selected years" not in tip


def test_tooltip_unique_and_roundtrip_on_real_registry(real_registry):
    """The tooltip contract (unique per DUID, _duid_from_tooltip round-trip)
    must hold on the real registry even with the multi-year CUF fallback."""
    df = real_registry.copy()
    df["data_status"] = "ready"
    df["simulation_ready"] = True

    tooltips = [nem_map._tooltip(row) for _, row in df.iterrows()]
    assert len(tooltips) == len(set(tooltips)), "tooltips must be unique across all registry rows"

    for (_, row), tooltip in zip(df.iterrows(), tooltips):
        resolved = nem_map._duid_from_tooltip(tooltip, df)
        assert resolved == row["duid"]

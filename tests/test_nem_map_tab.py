"""Tests for the pure (Streamlit-free) helper functions in ui/tabs/nem_map.py."""
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


def test_marker_radius_strictly_increasing():
    caps = [0.0, 1.0, 10.0, 30.0, 100.0, 500.0, 1000.0]
    radii = [nem_map._marker_radius(c) for c in caps]
    assert all(r2 > r1 for r1, r2 in zip(radii, radii[1:]))


def test_tooltip_roundtrip_and_unique_on_real_registry(real_registry):
    df = real_registry.copy()
    df["data_status"] = "ready"
    df["simulation_ready"] = True

    tooltips = [nem_map._tooltip(row) for _, row in df.iterrows()]
    assert len(tooltips) == len(set(tooltips)), "tooltips must be unique across all registry rows"

    for (_, row), tooltip in zip(df.iterrows(), tooltips):
        resolved = nem_map._duid_from_tooltip(tooltip, df)
        assert resolved == row["duid"]


def test_tooltip_disambiguates_duplicate_station_names(real_registry):
    df = real_registry
    dup_names = df["station_name"][df["station_name"].duplicated(keep=False)]
    assert len(dup_names) > 0, "expected some duplicated station names in the real registry"
    dup_name = dup_names.iloc[0]
    subset = df[df["station_name"] == dup_name]
    tooltips = [nem_map._tooltip(row) for _, row in subset.iterrows()]
    assert len(tooltips) == len(set(tooltips))


def test_duid_from_tooltip_returns_none_for_unknown_tooltip(real_registry):
    assert nem_map._duid_from_tooltip("not a real tooltip", real_registry) is None


def test_marker_style_differs_by_data_status():
    base = {"fuel_tech": "Wind", "capacity_registered_mw": 100.0}
    ready = dict(base, data_status="ready")
    no_scada = dict(base, data_status="no_scada")
    incomplete = dict(base, data_status="incomplete")

    style_ready = nem_map._marker_style(ready)
    style_no_scada = nem_map._marker_style(no_scada)
    style_incomplete = nem_map._marker_style(incomplete)

    assert style_ready["fill"] is True
    assert style_no_scada["fill"] is False
    assert style_incomplete["fill"] is False
    assert style_ready != style_no_scada
    assert style_no_scada != style_incomplete


def test_selectable_duids_respects_allow_unready():
    df = pd.DataFrame([
        {"duid": "A", "fuel_tech": "Wind", "simulation_ready": True},
        {"duid": "B", "fuel_tech": "Wind", "simulation_ready": False},
        {"duid": "C", "fuel_tech": "Solar", "simulation_ready": True},
    ])
    ready_only = nem_map._selectable_duids(df, "Wind", allow_unready=False)
    assert ready_only == ["A"]

    all_wind = nem_map._selectable_duids(df, "Wind", allow_unready=True)
    assert set(all_wind) == {"A", "B"}


# ── Regression: @st.cache_data cache-key bug (leading-underscore param) ──────

def test_cached_eligible_plants_keys_on_fingerprint_not_just_year(monkeypatch):
    """`_cached_eligible_plants` must re-execute when `fingerprint` changes even
    though `year` stays the same. Before the fix, the parameter was named
    `_fingerprint` -- Streamlit's `@st.cache_data` never hashes parameters whose
    names start with `_`, so the cache was effectively keyed on `year` alone and
    two calls with different fingerprints would wrongly return the same
    (stale) cached result.
    """
    calls: list = []

    def fake_list_eligible_plants(year, check_whole_year=True):
        calls.append((year, check_whole_year))
        return pd.DataFrame({"call_index": [len(calls)]})

    monkeypatch.setattr(nem_map.nem_data, "list_eligible_plants", fake_list_eligible_plants)
    nem_map._cached_eligible_plants.clear()

    df1 = nem_map._cached_eligible_plants(2025, (1, 1, 100))
    df2 = nem_map._cached_eligible_plants(2025, (2, 1, 200))  # same year, different fingerprint

    assert len(calls) == 2, "different fingerprints (same year) must both trigger real execution"
    assert df1["call_index"].iloc[0] != df2["call_index"].iloc[0]

    # Same (year, fingerprint) as the first call -> cache hit, no new execution.
    df3 = nem_map._cached_eligible_plants(2025, (1, 1, 100))
    assert len(calls) == 2, "identical (year, fingerprint) should hit the cache, not re-execute"
    assert df3["call_index"].iloc[0] == df1["call_index"].iloc[0]


# ── Regression: run-readiness must check the SPECIFIC selected DUIDs ────────

def test_check_selected_duids_ready_rejects_when_one_of_two_duids_not_ready(tmp_path):
    """Reachable failure this guards against: with "allow unready" toggled on,
    a user selects one ready plant (FULLWF1) and one not-ready plant (GAPSF1,
    which fails the whole-year coverage check) and commits the scenario. The
    old `status["n_simulation_ready"] > 0 and duids_selected` gate would
    incorrectly report ready (some plant somewhere is ready). The fix must
    check each selected DUID individually and reject the whole selection.
    """
    from tests.fixtures.nem_fixtures import build_nem_fixture_cache

    fixture_cache = build_nem_fixture_cache(tmp_path / "nem_cache")

    all_ready, problems = nem_data.check_selected_duids_ready(
        pv_duid="GAPSF1", wind_duid="FULLWF1", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready is False
    assert any("GAPSF1" in p for p in problems)

    # Both individually ready -> overall ready.
    all_ready_ok, problems_ok = nem_data.check_selected_duids_ready(
        pv_duid="", wind_duid="FULLWF1", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready_ok is True
    assert problems_ok == ()

    # A DUID absent from the registry entirely is treated as not ready.
    all_ready_missing, problems_missing = nem_data.check_selected_duids_ready(
        pv_duid="NOSUCHDUID1", wind_duid="", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready_missing is False
    assert any("NOSUCHDUID1" in p for p in problems_missing)


# ── Regression: Run-button gate must refuse "nem_default" with empty DUIDs ──

@pytest.mark.parametrize("data_source", ["nem_map", "nem_default"])
def test_nem_generation_ready_rejects_empty_duids_for_both_nem_sources(data_source, tmp_path):
    """[HIGH bug fix] Before this fix, the Optimization tab's Run-button gate
    fell back to `cache_status()["n_simulation_ready"] > 0` for 'nem_default'
    scenarios -- true as long as SOME plant anywhere in the cache was ready,
    even if the scenario's own nem_pv_duid/nem_wind_duid were both empty (i.e.
    zero renewable generation). `nem_generation_ready` must refuse to report
    ready for EITHER 'nem_map' or 'nem_default' when no DUID is selected, even
    though the cache itself has ready plants.
    """
    from tests.fixtures.nem_fixtures import build_nem_fixture_cache

    fixture_cache = build_nem_fixture_cache(tmp_path / "nem_cache")

    all_ready, problems = nem_data.nem_generation_ready(
        data_source, pv_duid="", wind_duid="", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready is False
    assert problems  # a human-readable reason must be present


@pytest.mark.parametrize("data_source", ["nem_map", "nem_default"])
def test_nem_generation_ready_rejects_not_ready_duid(data_source, tmp_path):
    from tests.fixtures.nem_fixtures import build_nem_fixture_cache

    fixture_cache = build_nem_fixture_cache(tmp_path / "nem_cache")

    all_ready, problems = nem_data.nem_generation_ready(
        data_source, pv_duid="GAPSF1", wind_duid="", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready is False
    assert any("GAPSF1" in p for p in problems)


@pytest.mark.parametrize("data_source", ["nem_map", "nem_default"])
def test_nem_generation_ready_accepts_a_ready_duid(data_source, tmp_path):
    from tests.fixtures.nem_fixtures import build_nem_fixture_cache

    fixture_cache = build_nem_fixture_cache(tmp_path / "nem_cache")

    all_ready, problems = nem_data.nem_generation_ready(
        data_source, pv_duid="", wind_duid="FULLWF1", year=2025, cache_dir=fixture_cache,
    )
    assert all_ready is True
    assert problems == ()


def test_nem_generation_ready_non_nem_source_always_ready():
    all_ready, problems = nem_data.nem_generation_ready("european", pv_duid="", wind_duid="")
    assert all_ready is True
    assert problems == ()

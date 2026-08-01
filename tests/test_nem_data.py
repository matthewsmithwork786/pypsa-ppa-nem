"""Tests for ppa/data/nem_data.py — cache-only NEM reader/adapter."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ppa.data import nem_data
from tests.fixtures.nem_fixtures import build_nem_fixture_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_REGISTRY_DIR = REPO_ROOT / "data" / "cache" / "nem"


@pytest.fixture()
def fixture_cache(tmp_path) -> Path:
    return build_nem_fixture_cache(tmp_path / "nem_cache")


# ── Static import-surface guarantee ──────────────────────────────────────────

def test_no_network_imports_in_source():
    source = (REPO_ROOT / "ppa" / "data" / "nem_data.py").read_text()
    tree = ast.parse(source)
    forbidden = {"requests", "urllib", "httpx", "nemosis", "socket", "streamlit"}
    found_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.add(node.module.split(".")[0])
    bad = found_modules & forbidden
    assert not bad, f"nem_data.py imports forbidden network-capable modules: {bad}"


# ── Real registry (present in repo) ──────────────────────────────────────────

def test_real_registry_loads_with_required_columns():
    if not (REAL_REGISTRY_DIR / "registry" / "nem_plant_registry.parquet").exists():
        pytest.skip("Real NEM registry parquet not present in this checkout.")
    df = nem_data.load_plant_registry(REAL_REGISTRY_DIR)
    for col in nem_data.REGISTRY_COLUMNS:
        assert col in df.columns
    assert df["lat"].notna().all()
    assert df["lon"].notna().all()
    assert (df["capacity_registered_mw"] > 30).all()


# ── expected_intervals / expected_hours ──────────────────────────────────────

def test_expected_intervals_non_leap_and_leap():
    assert nem_data.expected_intervals(2025) == 105_120
    assert nem_data.expected_intervals(2024) == 105_408  # leap year


def test_expected_hours_non_leap_and_leap():
    assert nem_data.expected_hours(2025) == 8760
    assert nem_data.expected_hours(2024) == 8784


# ── whole_year_check against each synthetic fixture plant ───────────────────

def test_whole_year_check_fullwf1_passes_all(fixture_cache):
    scada = nem_data.load_scada("FULLWF1", 2025, fixture_cache)
    check = nem_data.whole_year_check(scada, 100.0, 2025, duid="FULLWF1")
    assert check.coverage_ok
    assert check.span_ok
    assert check.monthly_output_ok
    assert check.passed


def test_whole_year_check_gapsf1_fails_coverage_only(fixture_cache):
    scada = nem_data.load_scada("GAPSF1", 2025, fixture_cache)
    check = nem_data.whole_year_check(scada, 200.0, 2025, duid="GAPSF1")
    assert not check.coverage_ok
    assert check.span_ok
    assert check.monthly_output_ok
    assert not check.passed


def test_whole_year_check_latecomsf1_fails_all_three(fixture_cache):
    scada = nem_data.load_scada("LATECOMSF1", 2025, fixture_cache)
    check = nem_data.whole_year_check(scada, 150.0, 2025, duid="LATECOMSF1")
    assert not check.coverage_ok
    assert not check.span_ok
    assert not check.monthly_output_ok
    assert set(check.weak_months) == {1, 2, 3, 4, 5, 6}
    assert not check.passed


def test_whole_year_check_mothballwf1_fails_monthly_only(fixture_cache):
    scada = nem_data.load_scada("MOTHBALLWF1", 2025, fixture_cache)
    check = nem_data.whole_year_check(scada, 80.0, 2025, duid="MOTHBALLWF1")
    assert check.coverage_ok
    assert check.span_ok
    assert not check.monthly_output_ok
    assert set(check.weak_months) == {6, 7, 8}
    assert not check.passed


def test_scada_summary_nodatawf1_is_no_scada(fixture_cache):
    summary = nem_data.scada_summary("NODATAWF1", 120.0, 2025, fixture_cache)
    assert summary.status == "no_scada"
    assert summary.check is None


# ── Two-tier eligibility ─────────────────────────────────────────────────────

def test_list_eligible_plants_matches_expected_five(fixture_cache):
    df = nem_data.list_eligible_plants(year=2025, cache_dir=fixture_cache)
    assert set(df["duid"]) == {"FULLWF1", "GAPSF1", "LATECOMSF1", "MOTHBALLWF1", "NODATAWF1"}
    # TINYSF1 excluded by capacity, COALX1 excluded by fuel_tech
    assert "TINYSF1" not in set(df["duid"])
    assert "COALX1" not in set(df["duid"])

    status_by_duid = dict(zip(df["duid"], df["data_status"]))
    assert status_by_duid["FULLWF1"] == "ready"
    assert status_by_duid["GAPSF1"] == "incomplete"
    assert status_by_duid["LATECOMSF1"] == "incomplete"
    assert status_by_duid["MOTHBALLWF1"] == "incomplete"
    assert status_by_duid["NODATAWF1"] == "no_scada"


def test_list_simulation_ready_plants_only_fullwf1(fixture_cache):
    df = nem_data.list_simulation_ready_plants(year=2025, cache_dir=fixture_cache)
    assert set(df["duid"]) == {"FULLWF1"}


def test_list_eligible_plants_cheap_path_skips_parquet_reads(fixture_cache):
    df = nem_data.list_eligible_plants(year=2025, cache_dir=fixture_cache, check_whole_year=False)
    assert set(df["duid"]) == {"FULLWF1", "GAPSF1", "LATECOMSF1", "MOTHBALLWF1", "NODATAWF1"}
    assert (df["data_status"] == "unchecked").all()
    assert not df["simulation_ready"].any()


def test_cache_status_reports_missing_vic1(fixture_cache):
    status = nem_data.cache_status(2025, fixture_cache)
    assert status["registry_present"]
    assert status["n_registry_plants"] == 7
    assert status["n_simulation_ready"] == 1
    assert "NSW1" in status["price_regions_cached"]
    assert "VIC1" in status["missing_price_regions"]


# ── Optimizer-facing adapters ────────────────────────────────────────────────

def test_get_cf_dicts_shape_and_no_nan(fixture_cache):
    pv_by_year, wind_by_year = nem_data.get_cf_dicts(
        "GAPSF1", "FULLWF1", years=(2025,), cache_dir=fixture_cache
    )
    assert set(pv_by_year.keys()) == {2025}
    assert set(wind_by_year.keys()) == {2025}
    for series in (pv_by_year[2025], wind_by_year[2025]):
        assert len(series) == nem_data.expected_hours(2025)
        assert not series.isna().any()
        assert (series >= 0.0).all() and (series <= 1.0).all()


def test_get_cf_dicts_none_duid_yields_all_zero(fixture_cache):
    pv_by_year, wind_by_year = nem_data.get_cf_dicts(
        None, "FULLWF1", years=(2025,), cache_dir=fixture_cache
    )
    assert len(pv_by_year[2025]) == nem_data.expected_hours(2025)
    assert (pv_by_year[2025] == 0.0).all()

    pv_by_year2, wind_by_year2 = nem_data.get_cf_dicts(
        "GAPSF1", "", years=(2025,), cache_dir=fixture_cache
    )
    assert (wind_by_year2[2025] == 0.0).all()


def test_get_price_dict_shape(fixture_cache):
    prices = nem_data.get_price_dict("NSW1", years=(2025,), cache_dir=fixture_cache)
    assert set(prices.keys()) == {2025}
    series = prices[2025]
    assert len(series) == nem_data.expected_hours(2025)
    assert not series.isna().any()


def test_get_price_dict_uncached_region_raises(fixture_cache):
    with pytest.raises(FileNotFoundError):
        nem_data.get_price_dict("VIC1", years=(2025,), cache_dir=fixture_cache)


def test_to_hourly_matches_manual_resample(fixture_cache):
    scada = nem_data.load_scada("FULLWF1", 2025, fixture_cache)
    hourly = nem_data.to_hourly(scada, 2025)
    manual = scada.resample("h").mean()
    # Compare over the overlapping index (to_hourly reindexes/ffills to a
    # canonical index; over the manual resample's own index they must match).
    common = manual.index.intersection(hourly.index)
    assert len(common) > 0
    pd.testing.assert_series_equal(
        hourly.loc[common].astype(float), manual.loc[common].astype(float), check_names=False
    )


def test_get_timeseries_dicts_duck_types_scenario(fixture_cache):
    class FakeScenario:
        nem_pv_duid = "GAPSF1"
        nem_wind_duid = "FULLWF1"
        nem_price_region = "NSW1"
        nem_year = 2025

    pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(
        FakeScenario(), cache_dir=fixture_cache
    )
    assert set(pv_by_year.keys()) == {2025}
    assert set(wind_by_year.keys()) == {2025}
    assert set(prices_by_year.keys()) == {2025}


def test_missing_registry_raises_filenotfounderror(tmp_path):
    with pytest.raises(FileNotFoundError):
        nem_data.load_plant_registry(tmp_path / "does_not_exist")


# ── reference_month_ts (single-day reference path) ───────────────────────────

def test_reference_month_ts_shape_and_no_nan(fixture_cache):
    class FakeScenario:
        nem_pv_duid = "GAPSF1"
        nem_wind_duid = "FULLWF1"
        nem_price_region = "NSW1"
        nem_year = 2025

    ts = nem_data.reference_month_ts(FakeScenario(), month=3, cache_dir=fixture_cache)
    assert set(ts.columns) == {"ts_PVGen", "ts_WindGen", "ts_MktPrice"}
    assert isinstance(ts.index, pd.DatetimeIndex)
    # Hourly index: every step exactly one hour apart.
    deltas = ts.index.to_series().diff().dropna().unique()
    assert list(deltas) == [pd.Timedelta(hours=1)]
    assert (ts.index.month == 3).all()
    assert not ts.isna().any().any()


def test_reference_month_ts_matches_expected_hours_in_march(fixture_cache):
    class FakeScenario:
        nem_pv_duid = "GAPSF1"
        nem_wind_duid = "FULLWF1"
        nem_price_region = "NSW1"
        nem_year = 2025

    ts = nem_data.reference_month_ts(FakeScenario(), month=3, cache_dir=fixture_cache)
    assert len(ts) == 31 * 24

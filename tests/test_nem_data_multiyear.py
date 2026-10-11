"""Multi-year tests for ppa.data.nem_data (WP3).

Covers the generalised resolution adapter (`to_resolution`), the multi-year
CF/price adapters, the two-directory path resolution (`_resolve`), and the
per-(duid, year) operational manifest readers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ppa.data import nem_data
from tests.fixtures.nem_fixtures import build_nem_fixture_cache


@pytest.fixture(scope="module")
def multiyear_cache(tmp_path_factory) -> Path:
    return build_nem_fixture_cache(tmp_path_factory.mktemp("nem_cache"))


# ── to_resolution ────────────────────────────────────────────────────────────

def test_to_resolution_60_matches_to_hourly_exactly(multiyear_cache):
    scada = nem_data.load_scada("FULLWF1", 2024, multiyear_cache)  # leap year
    pd.testing.assert_series_equal(
        nem_data.to_resolution(scada, 2024, 60),
        nem_data.to_hourly(scada, 2024),
    )


def test_to_resolution_row_counts(multiyear_cache):
    scada = nem_data.load_scada("FULLWF1", 2024, multiyear_cache)
    assert len(nem_data.to_resolution(scada, 2024, 5)) == nem_data.expected_intervals(2024)
    assert len(nem_data.to_resolution(scada, 2024, 30)) == 2 * nem_data.expected_hours(2024)


def test_to_resolution_30min_matches_manual_resample(multiyear_cache):
    scada = nem_data.load_scada("FULLWF1", 2023, multiyear_cache)
    r30 = nem_data.to_resolution(scada, 2023, 30)
    manual = scada.resample("30min").mean()
    common = manual.index.intersection(r30.index)
    pd.testing.assert_series_equal(
        r30.loc[common].astype(float), manual.loc[common].astype(float), check_names=False
    )


def test_to_resolution_invalid_resolution_raises(multiyear_cache):
    scada = nem_data.load_scada("FULLWF1", 2023, multiyear_cache)
    with pytest.raises(ValueError):
        nem_data.to_resolution(scada, 2023, 7)  # not a multiple of 5
    with pytest.raises(ValueError):
        nem_data.to_resolution(scada, 2023, 25)  # multiple of 5 but not a divisor of 1440


# ── plant-years manifest ─────────────────────────────────────────────────────

def test_load_plant_years_reads_synthetic_manifest(multiyear_cache):
    df = nem_data.load_plant_years(multiyear_cache)
    assert list(df.columns) == nem_data.PLANT_YEARS_COLUMNS
    assert set(df["year"]) == {2023, 2024, 2025}
    assert df["fully_operational"].dtype == bool


def test_load_plant_years_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(nem_data, "RUNTIME_CACHE_DIR", tmp_path / "runtime")
    df = nem_data.load_plant_years(tmp_path / "cache")
    assert list(df.columns) == nem_data.PLANT_YEARS_COLUMNS
    assert df.empty


def test_available_years_sorted(multiyear_cache):
    assert nem_data.available_years(multiyear_cache) == [2023, 2024, 2025]


def test_plants_operational_for_years_intersection(multiyear_cache):
    df = nem_data.plants_operational_for_years([2023, 2024], cache_dir=multiyear_cache)
    duids = set(df["duid"])
    assert "FULLWF1" in duids
    assert "GAPSF1" in duids
    assert "MOTHBALLWF1" not in duids  # not fully operational in 2023
    assert "mean_cuf_selected_years" in df.columns
    assert not df.loc[df["duid"] == "FULLWF1", "mean_cuf_selected_years"].isna().any()


def test_plants_operational_for_years_drops_year_with_outage(multiyear_cache):
    df = nem_data.plants_operational_for_years([2023, 2024, 2025], cache_dir=multiyear_cache)
    duids = set(df["duid"])
    assert "GAPSF1" not in duids  # offline during 2025
    assert "FULLWF1" in duids


def test_plants_operational_for_years_empty_years(multiyear_cache):
    df = nem_data.plants_operational_for_years([], cache_dir=multiyear_cache)
    assert df.empty


# ── _resolve two-directory path resolution ───────────────────────────────────

def test_resolve_prefers_packaged_path(tmp_path, monkeypatch):
    monkeypatch.setattr(nem_data, "RUNTIME_CACHE_DIR", tmp_path / "runtime")
    packaged = tmp_path / "pkg"
    f = packaged / "registry" / "x.parquet"
    f.parent.mkdir(parents=True)
    f.touch()
    assert nem_data._resolve(Path("registry") / "x.parquet", packaged) == f


def test_resolve_falls_back_to_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(nem_data, "RUNTIME_CACHE_DIR", tmp_path / "runtime")
    packaged = tmp_path / "pkg"
    f = tmp_path / "runtime" / "registry" / "x.parquet"
    f.parent.mkdir(parents=True)
    f.touch()
    assert nem_data._resolve(Path("registry") / "x.parquet", packaged) == f


def test_resolve_returns_packaged_when_neither_exists(tmp_path):
    packaged = tmp_path / "pkg"
    assert (
        nem_data._resolve(Path("registry") / "x.parquet", packaged)
        == packaged / "registry" / "x.parquet"
    )


# ── Multi-year CF / price adapters ───────────────────────────────────────────

def test_get_cf_dicts_multi_years_and_lengths(multiyear_cache):
    pv_by_year, wind_by_year = nem_data.get_cf_dicts_multi(
        "GAPSF1", "FULLWF1", years=(2023, 2024, 2025), cache_dir=multiyear_cache
    )
    assert set(pv_by_year.keys()) == {2023, 2024, 2025}
    assert set(wind_by_year.keys()) == {2023, 2024, 2025}
    for year in (2023, 2024, 2025):
        assert len(pv_by_year[year]) == nem_data.expected_hours(year)
        assert len(wind_by_year[year]) == nem_data.expected_hours(year)
        assert not pv_by_year[year].isna().any()
        assert not wind_by_year[year].isna().any()


def test_get_cf_dicts_multi_resolution(multiyear_cache):
    _, wind_by_year = nem_data.get_cf_dicts_multi(
        "", "FULLWF1", years=(2023, 2024), resolution_minutes=30, cache_dir=multiyear_cache
    )
    for year in (2023, 2024):
        assert len(wind_by_year[year]) == 2 * nem_data.expected_hours(year)


def test_get_price_dict_multi_keys(multiyear_cache):
    prices = nem_data.get_price_dict_multi(
        "NSW1", years=(2023, 2024, 2025), cache_dir=multiyear_cache
    )
    assert set(prices.keys()) == {2023, 2024, 2025}
    for year in (2023, 2024, 2025):
        assert len(prices[year]) == nem_data.expected_hours(year)
        assert not prices[year].isna().any()


def test_get_price_dict_multi_missing_year_names_region_and_year(multiyear_cache):
    with pytest.raises(FileNotFoundError) as ei:
        nem_data.get_price_dict_multi("NSW1", years=(2025, 2099), cache_dir=multiyear_cache)
    msg = str(ei.value)
    assert "NSW1" in msg and "2099" in msg


def test_get_timeseries_dicts_reads_nem_years(multiyear_cache):
    class FakeMultiYearScenario:
        nem_pv_duid = "GAPSF1"
        nem_wind_duid = "FULLWF1"
        nem_price_region = "NSW1"
        nem_years = [2023, 2024]
        nem_resolution_minutes = 60

    pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(
        FakeMultiYearScenario(), cache_dir=multiyear_cache
    )
    assert set(pv_by_year.keys()) == {2023, 2024}
    assert set(wind_by_year.keys()) == {2023, 2024}
    assert set(prices_by_year.keys()) == {2023, 2024}

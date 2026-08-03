"""U4: unconstrained (UIGF) capacity factors from AEMO DISPATCHLOAD.

The SCADA trace is *constrained* output -- reduced by network constraints and
by whatever economic curtailment each plant's own offtake contract
incentivised. Using it as the capacity factor for a NEW build charges that
curtailment twice. `DISPATCHLOAD.AVAILABILITY` is the physically available
output, independent of both.

The availability cache is optional, so every path here must degrade to SCADA
rather than fail when it is absent.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from ppa.data import nem_data
from ppa.scenario import Scenario


def _write_5min(path, col, value, year=2025):
    idx = pd.date_range(f"{year}-01-01", periods=288 * 5, freq="5min")
    pd.DataFrame({col: np.full(len(idx), value)}, index=idx).to_parquet(path)


@pytest.fixture()
def fake_cache(tmp_path):
    """Minimal cache: one DUID with SCADA at 20 MW and availability at 50 MW."""
    (tmp_path / "scada").mkdir(parents=True)
    (tmp_path / "availability").mkdir(parents=True)
    _write_5min(tmp_path / "scada" / "TESTWF1_2025.parquet", "scadavalue", 20.0)
    _write_5min(tmp_path / "availability" / "TESTWF1_2025.parquet", "availability", 50.0)
    return tmp_path


def test_has_availability_detects_cache(fake_cache):
    assert nem_data.has_availability("TESTWF1", 2025, fake_cache)
    assert not nem_data.has_availability("ABSENT1", 2025, fake_cache)


def test_availability_is_higher_than_scada(fake_cache):
    scada = nem_data.load_scada("TESTWF1", 2025, fake_cache)
    avail = nem_data.load_availability("TESTWF1", 2025, fake_cache)
    assert float(scada.mean()) == pytest.approx(20.0)
    assert float(avail.mean()) == pytest.approx(50.0)


def test_missing_availability_raises_with_instructions(fake_cache):
    with pytest.raises(FileNotFoundError, match="fetch_nem_availability"):
        nem_data.load_availability("ABSENT1", 2025, fake_cache)


def test_cf_dict_uses_availability_only_when_requested(fake_cache, monkeypatch):
    monkeypatch.setattr(nem_data, "plant_capacity_mw", lambda *a, **k: 100.0)

    constrained = nem_data._cf_dict_for_duid("TESTWF1", (2025,), fake_cache, None, False)
    unconstrained = nem_data._cf_dict_for_duid("TESTWF1", (2025,), fake_cache, None, True)

    assert float(constrained[2025].mean()) == pytest.approx(0.20, abs=1e-6)
    assert float(unconstrained[2025].mean()) == pytest.approx(0.50, abs=1e-6)


def test_falls_back_to_scada_when_availability_absent(tmp_path, monkeypatch):
    """A missing availability cache must degrade silently, not raise.

    The cache is optional; an install without it has to keep working exactly as
    before rather than breaking when the flag is on.
    """
    (tmp_path / "scada").mkdir(parents=True)
    _write_5min(tmp_path / "scada" / "TESTWF1_2025.parquet", "scadavalue", 20.0)
    monkeypatch.setattr(nem_data, "plant_capacity_mw", lambda *a, **k: 100.0)

    result = nem_data._cf_dict_for_duid("TESTWF1", (2025,), tmp_path, None, True)
    assert float(result[2025].mean()) == pytest.approx(0.20, abs=1e-6)


def test_scenario_flag_defaults_on_and_is_plumbed():
    """UIGF is the correct input for a new build, so it is the default, not a
    variant. SCADA remains selectable for the existing-plant-offtake case."""
    assert Scenario().use_unconstrained_cf is True
    assert not dataclasses.replace(Scenario(), use_unconstrained_cf=False).use_unconstrained_cf


def test_get_timeseries_dicts_tolerates_scenario_without_the_field(fake_cache, monkeypatch):
    """Duck-typed access: fake scenarios in other tests lack this attribute.

    They must get the same default as a real Scenario (UIGF), not a different
    code path.
    """
    monkeypatch.setattr(nem_data, "plant_capacity_mw", lambda *a, **k: 100.0)
    monkeypatch.setattr(nem_data, "get_price_dict", lambda *a, **k: {2025: pd.Series([1.0])})
    monkeypatch.setattr(nem_data, "load_plant_registry", lambda *a, **k: pd.DataFrame())

    class _Bare:
        nem_pv_duid = ""
        nem_wind_duid = "TESTWF1"
        nem_price_region = "NSW1"
        nem_year = 2025

    pv, wind, _ = nem_data.get_timeseries_dicts(_Bare(), cache_dir=fake_cache)
    # No use_unconstrained_cf attribute -> defaults to UIGF, same as Scenario().
    assert float(wind[2025].mean()) == pytest.approx(0.50, abs=1e-6)


def test_period_ts_uses_the_same_source_as_the_sizing_path(fake_cache, monkeypatch):
    """Every path that builds capacity factors must make the same UIGF choice.

    `period_ts` (the Optimisation tab's reference-period path) previously called
    `load_scada` directly, so a single scenario meant constrained output in one
    tab and unconstrained output in the simulation it fed.
    """
    monkeypatch.setattr(nem_data, "plant_capacity_mw", lambda *a, **k: 100.0)
    monkeypatch.setattr(nem_data, "load_plant_registry", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        nem_data, "load_regional_price",
        lambda *a, **k: pd.Series(
            50.0, index=pd.date_range("2025-01-01", periods=288 * 5, freq="5min")
        ),
    )

    class _Scn:
        nem_pv_duid = ""
        nem_wind_duid = "TESTWF1"
        nem_price_region = "NSW1"
        nem_year = 2025

        def __init__(self, unconstrained):
            self.use_unconstrained_cf = unconstrained

    window = ("2025-01-01", "2025-01-03")
    con = nem_data.period_ts(_Scn(False), *window, resolution_minutes=60, cache_dir=fake_cache)
    unc = nem_data.period_ts(_Scn(True), *window, resolution_minutes=60, cache_dir=fake_cache)

    # Fixture: SCADA 20 MW, availability 50 MW, capacity 100 MW.
    assert float(con["ts_WindGen"].mean()) == pytest.approx(0.20, abs=1e-6)
    assert float(unc["ts_WindGen"].mean()) == pytest.approx(0.50, abs=1e-6)


def test_generation_series_falls_back_per_duid(fake_cache):
    """The shared chooser degrades to SCADA when a DUID has no availability."""
    got = nem_data._generation_series("TESTWF1", 2025, fake_cache, unconstrained=True)
    assert float(got.mean()) == pytest.approx(50.0)

    # SCADA-only DUID: write one with no availability sibling.
    _write_5min(fake_cache / "scada" / "SCADAONLY_2025.parquet", "scadavalue", 33.0)
    got = nem_data._generation_series("SCADAONLY", 2025, fake_cache, unconstrained=True)
    assert float(got.mean()) == pytest.approx(33.0)


# ── Compact values-only cache format ─────────────────────────────────────────

def test_canonical_index_is_interval_ending():
    """AEMO stamps intervals by their END, so a year runs 00:05 -> next 00:00.

    Getting this backwards silently drops the final interval of the year and
    shifts every value by 5 minutes.
    """
    idx = nem_data.canonical_5min_index(2025)
    assert len(idx) == nem_data.expected_intervals(2025)
    assert idx[0] == pd.Timestamp("2025-01-01 00:05")
    assert idx[-1] == pd.Timestamp("2026-01-01 00:00")


def test_compact_format_round_trips_exactly(tmp_path):
    """Values-only storage must reproduce the timestamped series exactly.

    The timestamp index is ~64% of a naive per-interval parquet and is fully
    redundant on a fixed 5-minute grid; dropping it took the shipped cache from
    206 MB to 45 MB. That is only safe if the round-trip is lossless.
    """
    year = 2025
    idx = nem_data.canonical_5min_index(year)
    rng = np.random.default_rng(0)
    values = rng.random(len(idx)).astype("float32") * 100.0
    values[5:9] = np.nan          # gaps must survive as gaps, not be filled

    d = tmp_path / "availability"
    d.mkdir(parents=True)
    path = d / f"TESTWF1_{year}.parquet"
    pd.DataFrame({"availability": values}).to_parquet(path, index=False)

    got = nem_data.load_availability("TESTWF1", year, tmp_path)
    # Loader shifts to interval-beginning for modelling.
    assert len(got) == len(idx)
    assert got.index[0] == pd.Timestamp(f"{year}-01-01 00:00")
    np.testing.assert_allclose(got.to_numpy(), values, equal_nan=True)
    assert int(got.isna().sum()) == 4, "gaps must remain NaN, not be filled"


def test_legacy_timestamped_format_still_loads(tmp_path):
    """Caches written before the compact format must keep working."""
    year = 2025
    d = tmp_path / "availability"
    d.mkdir(parents=True)
    idx = pd.date_range(f"{year}-01-01 00:05", periods=288, freq="5min")
    pd.DataFrame({"availability": np.full(len(idx), 7.0)}, index=idx).to_parquet(
        d / f"LEGACY1_{year}.parquet"
    )
    got = nem_data.load_availability("LEGACY1", year, tmp_path)
    assert float(got.mean()) == pytest.approx(7.0)

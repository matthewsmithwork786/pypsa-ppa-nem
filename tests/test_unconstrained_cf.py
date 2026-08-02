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

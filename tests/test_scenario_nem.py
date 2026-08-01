"""Tests for the Phase 1 data-source additions to ppa/scenario.py."""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from ppa.scenario import DATA_SOURCES, Scenario, validate_scenario


def test_scenario_defaults():
    s = Scenario()
    assert s.data_source == "nem_default"
    assert s.nem_price_region == "NSW1"
    assert s.nem_pv_duid == ""
    assert s.nem_wind_duid == ""
    assert s.nem_year == 2025
    assert s.is_nem is True


@pytest.mark.parametrize("source,expected", [
    ("nem_map", True),
    ("nem_default", True),
    ("custom_csv", False),
])
def test_is_nem_property(source, expected):
    s = dataclasses.replace(Scenario(), data_source=source)
    assert s.is_nem is expected


def test_validate_scenario_rejects_unknown_data_source():
    s = dataclasses.replace(Scenario(), data_source="not_a_real_source")
    errors = validate_scenario(s)
    assert any("data source" in e.lower() for e in errors)


def test_validate_scenario_rejects_unknown_nem_region():
    s = dataclasses.replace(
        Scenario(), data_source="nem_default", nem_price_region="ZZZ1", nem_wind_duid="X",
    )
    errors = validate_scenario(s)
    assert any("nem region" in e.lower() for e in errors)


def test_validate_scenario_rejects_bad_nem_year():
    s = dataclasses.replace(
        Scenario(), data_source="nem_default", nem_year=1800, nem_wind_duid="X",
    )
    errors = validate_scenario(s)
    assert any("out of range" in e.lower() for e in errors)


def test_validate_scenario_requires_a_duid_for_nem_map():
    s = dataclasses.replace(Scenario(), data_source="nem_map", nem_pv_duid="", nem_wind_duid="")
    errors = validate_scenario(s)
    assert any("no nem plant selected" in e.lower() for e in errors)


def test_validate_scenario_nem_map_ok_with_one_duid():
    s = dataclasses.replace(Scenario(), data_source="nem_map", nem_wind_duid="FULLWF1")
    errors = validate_scenario(s)
    assert not any("no nem plant selected" in e.lower() for e in errors)


def test_validate_scenario_rejects_nem_default_with_both_duids_empty():
    """[HIGH bug fix] 'nem_default' with no wind/solar DUIDs would otherwise
    solve the LP with an all-zero capacity-factor series for both
    technologies (see ppa.data.nem_data._cf_dict_for_duid) -- zero renewable
    generation, no error. validate_scenario must catch this exactly like it
    already does for 'nem_map'.
    """
    s = dataclasses.replace(Scenario(), data_source="nem_default", nem_pv_duid="", nem_wind_duid="")
    errors = validate_scenario(s)
    assert any("no nem plant selected" in e.lower() for e in errors)


def test_validate_scenario_nem_default_ok_with_one_duid():
    s = dataclasses.replace(Scenario(), data_source="nem_default", nem_wind_duid="FULLWF1")
    errors = validate_scenario(s)
    assert not any("no nem plant selected" in e.lower() for e in errors)


def test_data_sources_tuple_contains_expected_values():
    assert set(DATA_SOURCES) == {"nem_map", "nem_default", "custom_csv"}


def test_validate_scenario_is_filesystem_free_for_non_nem_scenario(monkeypatch):
    def _boom(self, *args, **kwargs):
        raise AssertionError("validate_scenario must not touch the filesystem for non-NEM scenarios")

    monkeypatch.setattr(pathlib.Path, "exists", _boom)
    s = Scenario(data_source="custom_csv")  # non-NEM source
    # Must not raise despite Path.exists being poisoned.
    errors = validate_scenario(s)
    assert isinstance(errors, list)


def test_validate_scenario_nem_checks_do_not_touch_filesystem(monkeypatch):
    """NEM region/year checks only test string/int membership, not cache file presence."""
    def _boom(self, *args, **kwargs):
        raise AssertionError("validate_scenario's NEM checks must not touch the filesystem")

    monkeypatch.setattr(pathlib.Path, "exists", _boom)
    s = dataclasses.replace(Scenario(), data_source="nem_default", nem_wind_duid="X")
    errors = validate_scenario(s)
    assert isinstance(errors, list)

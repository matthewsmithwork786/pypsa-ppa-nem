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
        Scenario(), data_source="nem_default", nem_years=(1800,), nem_wind_duid="X",
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


# ── WP5: nem_years / capacity_sizing_year / resolution / tiered SLA ──────────

def test_scenario_defaults_include_new_fields():
    s = Scenario()
    assert s.nem_years == (2025,)
    assert s.capacity_sizing_year == 2025
    assert s.nem_resolution_minutes == 60
    assert s.sla_monthly_enabled is False
    assert s.sla_monthly_share == 0.0
    assert s.sla_daily_enabled is False
    assert s.sla_daily_share == 0.0


def test_nem_year_property_returns_first_selected_year():
    s = Scenario(nem_years=(2020, 2021))
    assert s.nem_year == 2020
    s = Scenario(nem_years=(2021, 2022, 2023))
    assert s.nem_year == 2021
    empty = Scenario(nem_years=())
    assert empty.nem_year == 2025


def test_scenario_asdict_contains_nem_years_not_nem_year():
    d = dataclasses.asdict(Scenario(nem_years=(2021, 2022)))
    assert d["nem_years"] == (2021, 2022)
    assert "nem_year" not in d


def test_dataclasses_replace_with_nem_years():
    s = dataclasses.replace(Scenario(), nem_years=(2020, 2021))
    assert s.nem_years == (2020, 2021)
    assert s.nem_year == 2020


def test_dataclasses_replace_with_nem_year_raises():
    """nem_year is now a property, not a field: replace(nem_year=...) must raise."""
    with pytest.raises(TypeError):
        dataclasses.replace(Scenario(), nem_year=2024)


def test_validate_scenario_rejects_empty_nem_years():
    s = dataclasses.replace(Scenario(), nem_years=())
    errors = validate_scenario(s)
    assert any("select at least one nem data year" in e.lower() for e in errors)


def test_validate_scenario_rejects_out_of_range_year_in_nem_years():
    s = dataclasses.replace(Scenario(), nem_years=(1800, 2025))
    errors = validate_scenario(s)
    assert any("out of range" in e.lower() for e in errors)


def test_validate_scenario_rejects_capacity_sizing_year_not_in_nem_years():
    s = dataclasses.replace(
        Scenario(), optimise_capacity=True, capacity_sizing_year=2024,
    )
    errors = validate_scenario(s)
    assert any("capacity-sizing year must be one of the selected" in e.lower() for e in errors)


def test_validate_scenario_accepts_capacity_sizing_year_in_nem_years():
    s = dataclasses.replace(
        Scenario(), optimise_capacity=True, capacity_sizing_year=2025,
    )
    errors = validate_scenario(s)
    assert not any("capacity-sizing year" in e.lower() for e in errors)


def test_validate_scenario_rejects_invalid_resolution():
    s = dataclasses.replace(Scenario(), nem_resolution_minutes=7)
    errors = validate_scenario(s)
    assert any("snapshot resolution must be 5, 15, 30 or 60 minutes" in e.lower() for e in errors)


@pytest.mark.parametrize("resolution", [5, 15, 30, 60])
def test_validate_scenario_accepts_each_valid_resolution(resolution):
    s = dataclasses.replace(Scenario(), nem_resolution_minutes=resolution)
    errors = validate_scenario(s)
    assert not any("snapshot resolution" in e.lower() for e in errors)


def test_validate_scenario_rejects_monthly_sla_with_bad_share():
    s = dataclasses.replace(Scenario(), sla_monthly_enabled=True, sla_monthly_share=0.0)
    errors = validate_scenario(s)
    assert any("monthly sla share must be between 0 and 1" in e.lower() for e in errors)


def test_validate_scenario_accepts_monthly_sla_with_good_share():
    s = dataclasses.replace(Scenario(), sla_monthly_enabled=True, sla_monthly_share=0.5)
    errors = validate_scenario(s)
    assert not any("monthly sla share" in e.lower() for e in errors)


def test_validate_scenario_rejects_daily_sla_with_bad_share():
    s = dataclasses.replace(Scenario(), sla_daily_enabled=True, sla_daily_share=1.5)
    errors = validate_scenario(s)
    assert any("daily sla share must be between 0 and 1" in e.lower() for e in errors)


def test_validate_scenario_accepts_daily_sla_with_good_share():
    s = dataclasses.replace(Scenario(), sla_daily_enabled=True, sla_daily_share=0.25)
    errors = validate_scenario(s)
    assert not any("daily sla share" in e.lower() for e in errors)


def test_validate_scenario_rejects_monthly_sla_with_tsam_sizing():
    s = dataclasses.replace(
        Scenario(), optimise_capacity=True, sla_monthly_enabled=True,
        sla_monthly_share=0.5, sizing_method="tsam",
    )
    errors = validate_scenario(s)
    assert any("monthly sla cannot be enforced" in e.lower() for e in errors)


def test_validate_scenario_accepts_monthly_sla_with_full_hourly_sizing():
    s = dataclasses.replace(
        Scenario(), optimise_capacity=True, sla_monthly_enabled=True,
        sla_monthly_share=0.5, sizing_method="full_hourly",
    )
    errors = validate_scenario(s)
    assert not any("monthly sla cannot be enforced" in e.lower() for e in errors)


def test_validate_scenario_default_produces_no_errors():
    """Regression guard: the WP5 fields must not make a default Scenario
    unrunnable out of the box. The pre-existing 'no NEM plant selected' check
    requires one DUID before a default scenario is fully clean, so supply the
    one the app would have selected by the time the user hits Run.
    """
    s = dataclasses.replace(Scenario(), nem_wind_duid="FULLWF1")
    errors = validate_scenario(s)
    assert errors == []


def test_validate_scenario_nem_year_checks_are_scoped_to_nem_data_source():
    """A custom_csv scenario must not be blocked by NEM-only checks (year
    range, capacity-sizing-year membership, resolution) even if nem_years /
    capacity_sizing_year hold stale values from a prior NEM selection."""
    s = dataclasses.replace(
        Scenario(),
        data_source="custom_csv",
        nem_years=(),
        capacity_sizing_year=1900,
        nem_resolution_minutes=7,
        onsw_mw=1.0,
    )
    errors = validate_scenario(s)
    assert not any("nem data year" in e.lower() for e in errors)
    assert not any("capacity-sizing year" in e.lower() for e in errors)
    assert not any("snapshot resolution" in e.lower() for e in errors)

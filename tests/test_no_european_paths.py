"""W6 acceptance gate: the European data path is gone from the app.

- `ppa/data/european_data.py`, `entsoe_client.py`, `renewables_ninja.py`,
  `bidding_zones.py` are deleted.
- `Scenario.DATA_SOURCES` has no `"european"` and the default `data_source` is a
  NEM source.
- `streamlit_app.py` no longer imports `ui.tabs.data_download`.
- The legacy European cache directories are removed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ppa.scenario import DATA_SOURCES, Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]

_EUROPEAN_MODULES = [
    "ppa.data.european_data",
    "ppa.data.entsoe_client",
    "ppa.data.renewables_ninja",
    "ppa.data.bidding_zones",
]


@pytest.mark.xfail(strict=True, reason="W6: European data modules still present")
def test_european_data_modules_deleted():
    for mod in _EUROPEAN_MODULES:
        assert importlib.util.find_spec(mod) is None, f"{mod} still importable — W6 deletion incomplete"


@pytest.mark.xfail(strict=True, reason="W6: DATA_SOURCES still includes 'european'")
def test_data_sources_has_no_european():
    assert "european" not in DATA_SOURCES


@pytest.mark.xfail(strict=True, reason="W6: default data_source is still 'european'")
def test_default_data_source_is_nem():
    assert Scenario().data_source in ("nem_map", "nem_default", "custom_csv")


@pytest.mark.xfail(strict=True, reason="W6: streamlit_app.py still imports data_download")
def test_streamlit_app_does_not_import_data_download():
    source = (REPO_ROOT / "streamlit_app.py").read_text()
    assert "data_download" not in source


@pytest.mark.xfail(strict=True, reason="W6: legacy European cache dirs still present")
def test_european_cache_dirs_removed():
    for d in (REPO_ROOT / "data" / "cache" / "entsoe", REPO_ROOT / "data" / "cache" / "renewables_ninja"):
        assert not d.exists(), f"legacy European cache dir still present: {d}"


@pytest.mark.xfail(strict=True, reason="W6: nem-default error still references 'European'")
def test_validate_scenario_accepts_nem_default_without_duid_error_message():
    """The 'switch the data source back to European' hint must be gone from the
    nem-default no-DUID error (it references a now-deleted source)."""
    from ppa.scenario import validate_scenario

    scn = Scenario(data_source="nem_default", nem_pv_duid="", nem_wind_duid="")
    errors = validate_scenario(scn)
    assert any("No NEM plant selected" in e for e in errors)
    assert not any("European" in e for e in errors)

"""Tests for the pure helper(s) in ui/tabs/custom_data.py, mirroring the style
of tests/test_nem_map_tab.py."""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")  # custom_data imports streamlit at module load time

from ui.tabs import custom_data

REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_DATA_SRC = REPO_ROOT / "ui" / "tabs" / "custom_data.py"


def _base_diag(**overrides) -> dict:
    diag = {
        "n_rows": 8760,
        "first": pd.Timestamp("2025-01-01"),
        "last": pd.Timestamp("2025-12-31 23:00"),
        "span_days": 364,
        "modal_step": pd.Timedelta(hours=1),
        "is_hourly": True,
        "is_sub_hourly": False,
        "n_gaps": 0,
        "n_duplicate_timestamps": 0,
        "is_full_year": True,
        "year": 2025,
        "pv_cf_mean": 0.25,
        "wind_cf_mean": 0.4,
        "load_mw_mean": 100.0,
        "load_mw_peak": 120.0,
        "price_mean": 85.0,
        "price_min": -20.0,
        "price_max": 300.0,
        "negative_price_hours": 5,
    }
    diag.update(overrides)
    return diag


def test_warnings_empty_for_clean_full_year_hourly_data():
    diag = _base_diag(negative_price_hours=0)
    warnings = custom_data._warnings_for(diag)
    assert warnings == []


def test_warnings_sub_hourly():
    diag = _base_diag(is_hourly=False, is_sub_hourly=True, modal_step=pd.Timedelta(minutes=5))
    warnings = custom_data._warnings_for(diag)
    assert any(level == "warning" and "resampled to hourly" in msg for level, msg in warnings)


def test_warnings_super_hourly():
    diag = _base_diag(is_hourly=False, is_sub_hourly=False, modal_step=pd.Timedelta(hours=3))
    warnings = custom_data._warnings_for(diag)
    assert any(level == "warning" and "one dispatch snapshot" in msg for level, msg in warnings)


def test_warnings_gaps():
    diag = _base_diag(n_gaps=7)
    warnings = custom_data._warnings_for(diag)
    assert any(level == "warning" and "gap(s)" in msg for level, msg in warnings)


def test_warnings_duplicates():
    diag = _base_diag(n_duplicate_timestamps=2)
    warnings = custom_data._warnings_for(diag)
    assert any(level == "warning" and "duplicate timestamp" in msg for level, msg in warnings)


def test_warnings_not_full_year():
    diag = _base_diag(is_full_year=False, span_days=30)
    warnings = custom_data._warnings_for(diag)
    assert any(
        level == "warning" and "repeat this full uploaded pattern" in msg
        for level, msg in warnings
    )


def test_warnings_price_out_of_sane_range():
    diag = _base_diag(price_max=25_000.0)
    warnings = custom_data._warnings_for(diag)
    assert any(level == "warning" and "sane NEM range" in msg for level, msg in warnings)

    diag2 = _base_diag(price_min=-5_000.0)
    warnings2 = custom_data._warnings_for(diag2)
    assert any(level == "warning" and "sane NEM range" in msg for level, msg in warnings2)


def test_warnings_negative_price_is_info_not_warning():
    diag = _base_diag(negative_price_hours=10)
    warnings = custom_data._warnings_for(diag)
    info_msgs = [msg for level, msg in warnings if level == "info"]
    warning_msgs = [msg for level, msg in warnings if level == "warning"]
    assert any("negative prices" in msg for msg in info_msgs)
    assert not any("negative prices" in msg for msg in warning_msgs)


# ── Session-state key naming convention: all cd_-prefixed ────────────────────

def test_all_session_state_keys_are_cd_prefixed():
    """AST scan of custom_data.py: every string literal passed as `key=...` to
    a Streamlit widget call must start with `cd_`."""
    tree = ast.parse(CUSTOM_DATA_SRC.read_text())
    bad_keys = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "key" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if not kw.value.value.startswith("cd_"):
                        bad_keys.append(kw.value.value)

    assert bad_keys == [], f"non cd_-prefixed session-state keys found: {bad_keys}"

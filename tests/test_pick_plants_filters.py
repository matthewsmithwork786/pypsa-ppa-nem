"""Filters on the Pick Plants (NEM map) tab.

The per-technology capacity-factor floor sliders were removed from the map tab
(they were `nm_cuf_min_wind` / `nm_cuf_min_solar` widgets); only the region
filter remains. `filtered` is now just the region filter, and the CUF column is
kept for tooltips only.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NEM_MAP = REPO_ROOT / "ui" / "tabs" / "nem_map.py"


def test_no_cuf_filter_widgets():
    source = NEM_MAP.read_text()
    assert "nm_cuf_min" not in source, (
        "the CUF floor sliders were removed from the map tab — their widget keys "
        "(`nm_cuf_min_wind` / `nm_cuf_min_solar`) must not reappear"
    )

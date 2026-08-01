"""W8 regression: the Custom Data template must be a date-range + periodicity
generated full-year (or sub-range) file — not a fixed 48-hour stub.

Default must produce 8760 hourly rows for 2025; (2025-03-01 → 2025-03-31,
30 min) must produce 1488 rows; the full-year 5-min template must produce
105 120 rows; timestamps strictly increasing and all within 2025; and the
generated CSV must round-trip through `load_custom_upload`.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from ppa.data_loader import build_upload_template, load_custom_upload


def _load(template_bytes: bytes) -> pd.DataFrame:
    return load_custom_upload(io.BytesIO(template_bytes))


@pytest.mark.xfail(strict=True, reason="W8: template is still a fixed 48-hour stub, not a 2025 full year")
def test_default_template_is_full_year_hourly():
    ts = _load(build_upload_template())
    assert len(ts) == 8760
    assert ts.index[0] == pd.Timestamp("2025-01-01 00:00")
    assert ts.index[-1] == pd.Timestamp("2025-12-31 23:00")


@pytest.mark.xfail(strict=True, reason="W8: build_upload_template takes (hours, start, load_mw), not start/end/freq_minutes")
def test_month_range_30min_has_1488_rows():
    ts = _load(build_upload_template(start="2025-03-01", end="2025-03-31", freq_minutes=30))
    assert len(ts) == 1488  # 31 days × 48 half-hour slots
    assert ts.index[0] == pd.Timestamp("2025-03-01 00:00")
    assert ts.index[-1] == pd.Timestamp("2025-03-31 23:30")


@pytest.mark.xfail(strict=True, reason="W8: build_upload_template takes (hours, start, load_mw), not start/end/freq_minutes")
def test_full_year_5min_has_105120_rows():
    ts = _load(
        build_upload_template(start="2025-01-01", end="2025-12-31", freq_minutes=5)
    )
    assert len(ts) == 105_120  # 365 days × 288 five-minute slots


def test_template_timestamps_strictly_increasing_within_2025():
    ts = _load(build_upload_template())
    assert ts.index.is_monotonic_increasing
    assert len(ts.index) == len(ts.index.unique())
    assert ts.index.min().year == 2025 and ts.index.max().year == 2025


def test_template_has_deterministic_shapes_and_valid_ranges():
    ts = _load(build_upload_template())
    assert ts["ts_PVGen"].between(0.0, 1.0).all()
    assert ts["ts_WindGen"].between(0.0, 1.0).all()
    assert (ts["ts_LoadMW"] == 100.0).all()
    assert ts["ts_MktPrice"].notna().all()

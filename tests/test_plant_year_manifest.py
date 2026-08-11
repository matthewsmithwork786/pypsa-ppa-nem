"""Tests for scripts/build_plant_year_manifest.py.

Exercises the `fully_operational` gate on the synthetic manifest fixture:
a clean year, a 20-day gap, March commissioning, October ramp-down, 96%
coverage (passes the old 95% gate, fails the new 98%), and the registry
`first_power_date` age gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.fixtures.nem_fixtures import build_manifest_fixture_cache

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_plant_year_manifest as manifest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CACHE_DIR = REPO_ROOT / "data" / "cache" / "nem"

EXPECTED_COLUMNS = [
    "duid", "year", "station_name", "region", "fuel_tech",
    "capacity_registered_mw_used", "n_intervals", "expected_intervals",
    "coverage", "first_ts", "last_ts", "longest_gap_hours",
    "longest_zero_run_hours", "monthly_peak_ratio_min",
    "monthly_peak_ratio_by_month", "annual_cuf", "cuf_vs_plant_median",
    "first_power_date", "fully_operational", "reject_reasons",
]


@pytest.fixture()
def manifest_cache(tmp_path) -> Path:
    return build_manifest_fixture_cache(tmp_path / "nem_cache")


@pytest.fixture()
def manifest_df(manifest_cache, tmp_path) -> pd.DataFrame:
    out = tmp_path / "out" / "plant_years.parquet"
    return manifest.build_manifest(manifest_cache, out)


# ── Schema ───────────────────────────────────────────────────────────────────

def test_manifest_has_all_expected_columns(manifest_df):
    missing = [c for c in EXPECTED_COLUMNS if c not in manifest_df.columns]
    assert not missing, f"manifest missing columns: {missing}"


def test_manifest_has_one_row_per_duid_year(manifest_df):
    assert len(manifest_df) == 6
    keys = list(zip(manifest_df["duid"], manifest_df["year"]))
    assert len(keys) == len(set(keys))
    assert set(manifest_df["duid"]) == {
        "CLEANYR1", "GAP20D1", "MARCOMM1", "OCTRAMP1", "COV96SF1", "NEWLY1",
    }


def test_manifest_writes_parquet(manifest_cache, tmp_path):
    out = tmp_path / "out" / "plant_years.parquet"
    manifest.build_manifest(manifest_cache, out)
    assert out.exists()
    reread = pd.read_parquet(out)
    assert len(reread) == 6


# ── Individual gates ─────────────────────────────────────────────────────────

def _row(df: pd.DataFrame, duid: str) -> pd.Series:
    return df.loc[df["duid"] == duid].iloc[0]


def test_clean_year_is_fully_operational(manifest_df):
    row = _row(manifest_df, "CLEANYR1")
    assert row["fully_operational"]
    assert row["reject_reasons"] == ""
    assert row["coverage"] == pytest.approx(1.0)
    assert row["longest_gap_hours"] == 0.0
    assert row["longest_zero_run_hours"] == 0.0
    assert row["monthly_peak_ratio_min"] >= 0.60
    assert row["monthly_peak_ratio_by_month"] == pytest.approx(
        [1.0] * 12, abs=0.05
    )
    assert row["first_ts"] == "2025-01-01 00:00:00"
    assert row["last_ts"] == "2025-12-31 23:55:00"
    # Wind pattern mean ~0.45; single passing year -> ratio 1.0 by construction.
    assert row["annual_cuf"] == pytest.approx(0.45, abs=0.01)
    assert row["cuf_vs_plant_median"] == pytest.approx(1.0)


def test_20_day_gap_is_rejected(manifest_df):
    row = _row(manifest_df, "GAP20D1")
    assert not row["fully_operational"]
    # 20 days = 480 h; coverage drops to ~94.5%.
    assert row["longest_gap_hours"] == pytest.approx(480.0)
    assert row["coverage"] == pytest.approx((105120 - 20 * 288) / 105120)
    assert "longest data gap 480 h" in row["reject_reasons"]
    assert "coverage 94.5%" in row["reject_reasons"]


def test_march_commissioning_is_rejected(manifest_df):
    row = _row(manifest_df, "MARCOMM1")
    assert not row["fully_operational"]
    assert row["monthly_peak_ratio_min"] < 0.60
    assert "monthly peak ratio" in row["reject_reasons"]
    assert "Jan, Feb" in row["reject_reasons"]
    # Registry first_power_date is also too recent -> a separate reason.
    assert "first power" in row["reject_reasons"]


def test_october_ramp_down_is_rejected(manifest_df):
    row = _row(manifest_df, "OCTRAMP1")
    assert not row["fully_operational"]
    assert row["monthly_peak_ratio_min"] == pytest.approx(0.30, abs=0.01)
    assert "Oct, Nov, Dec" in row["reject_reasons"]


def test_96_percent_coverage_fails_new_98_gate(manifest_df):
    row = _row(manifest_df, "COV96SF1")
    assert not row["fully_operational"]
    # Would have passed the old 95% gate...
    assert row["coverage"] >= 0.95
    # ...but the only reject reason is the tightened coverage threshold.
    assert row["coverage"] < 0.98
    assert "coverage 96.0%" in row["reject_reasons"]
    assert "gap" not in row["reject_reasons"]
    assert "zero" not in row["reject_reasons"]
    assert "monthly peak" not in row["reject_reasons"]


def test_first_power_date_age_gate(manifest_df):
    row = _row(manifest_df, "NEWLY1")
    assert not row["fully_operational"]
    assert row["first_power_date"] == "2025-06-01"
    assert "first power 2025-06-01" in row["reject_reasons"]
    # Clean year otherwise -- the age gate is the only reason.
    assert row["coverage"] == pytest.approx(1.0)
    assert row["longest_gap_hours"] == 0.0


# ── Real-cache smoke check ───────────────────────────────────────────────────

def test_real_2025_cache_builds_and_contains_known_duids(tmp_path):
    if not (REAL_CACHE_DIR / "availability").exists():
        pytest.skip("real availability cache not present in this checkout")
    out = tmp_path / "plant_years.parquet"
    df = manifest.build_manifest(REAL_CACHE_DIR, out)
    by_duid = dict(zip(df["duid"], df["fully_operational"]))
    reasons = dict(zip(df["duid"], df["reject_reasons"]))
    assert "MCINTYR1" in by_duid
    assert not by_duid["MCINTYR1"]
    for duid in ("COLWF01", "SUNRSF1", "HALLWF1"):
        assert duid in by_duid, f"expected {duid} in manifest"
        assert by_duid[duid], f"{duid} should be fully operational"
    # LKBONNY2 (Lake Bonney 2, 159 MW) is a documented disagreement with the
    # old eligibility cache: its January 2025 monthly peak was 87 MW = 54% of
    # its own annual peak because January 2025 was genuinely wind-poor, not
    # because of commissioning/outage. The old best-of-Jan/Feb rule admitted
    # it; the new all-12-months rule rejects it. Recorded, not waved through.
    if "LKBONNY2" in by_duid:
        assert not by_duid["LKBONNY2"]
        assert "monthly peak ratio" in reasons["LKBONNY2"]

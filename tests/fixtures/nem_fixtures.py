"""Deterministic synthetic NEM cache fixture for testing `ppa.data.nem_data`.

Builds a small `data/cache/nem`-shaped tree under a temp directory covering the
whole-year heuristic edge cases without needing real 2025 AEMO data.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

YEAR = 2025
N_INTERVALS = 105_120  # 365 * 288, non-leap 2025


def _full_year_index() -> pd.DatetimeIndex:
    return pd.date_range(start=f"{YEAR}-01-01 00:00", periods=N_INTERVALS, freq="5min")


def _wind_pattern(index: pd.DatetimeIndex, capacity_mw: float) -> pd.Series:
    """Deterministic wind-like shape: always comfortably above the 5% monthly floor."""
    t = pd.Series(range(len(index)), index=index)
    values = capacity_mw * (0.45 + 0.35 * ((t * 2 * math.pi / 288).apply(math.sin)))
    return values.clip(lower=0.0, upper=capacity_mw)


def _solar_pattern(index: pd.DatetimeIndex, capacity_mw: float) -> pd.Series:
    """Deterministic solar-like diurnal shape: zero at night, peaked at midday."""
    minutes_of_day = index.hour * 60 + index.minute
    frac_of_day = minutes_of_day / (24 * 60)
    shape = pd.Series(
        [max(0.0, math.sin(math.pi * (f - 0.25) / 0.5)) if 0.25 <= f <= 0.75 else 0.0 for f in frac_of_day],
        index=index,
    )
    return (capacity_mw * 0.9 * shape).clip(lower=0.0, upper=capacity_mw)


def _write_series(path: Path, series: pd.Series, col: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({col: series.values}, index=series.index)
    df.to_parquet(path)


def build_nem_fixture_cache(root_dir) -> Path:
    """Build a synthetic NEM cache tree under `root_dir` and return the cache root.

    `root_dir` should be a fresh temp directory; the returned Path is suitable
    for passing directly as `cache_dir=` to any `ppa.data.nem_data` function
    (it contains `registry/`, `scada/`, `price/` subdirectories).
    """
    cache_dir = Path(root_dir)
    scada_dir = cache_dir / "scada"
    price_dir = cache_dir / "price"
    registry_dir = cache_dir / "registry"
    scada_dir.mkdir(parents=True, exist_ok=True)
    price_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)

    full_index = _full_year_index()

    # ── FULLWF1: complete, passes all checks ────────────────────────────────
    fullwf1 = _wind_pattern(full_index, 100.0)
    _write_series(scada_dir / "FULLWF1_2025.parquet", fullwf1, "scadavalue")

    # ── GAPSF1: full span, ~6% of intervals missing (fails coverage only) ──
    gapsf1_full = _solar_pattern(full_index, 200.0)
    keep_mask = [i % 17 != 0 for i in range(len(full_index))]  # drops ~5.9% of rows
    gapsf1 = gapsf1_full[keep_mask]
    _write_series(scada_dir / "GAPSF1_2025.parquet", gapsf1, "scadavalue")

    # ── LATECOMSF1: no data before July 1 (fails coverage, span, monthly) ──
    latecom_index = pd.date_range(start=f"{YEAR}-07-01 00:00", end=f"{YEAR}-12-31 23:55", freq="5min")
    latecomsf1 = _solar_pattern(latecom_index, 150.0)
    _write_series(scada_dir / "LATECOMSF1_2025.parquet", latecomsf1, "scadavalue")

    # ── MOTHBALLWF1: complete span+coverage, but zero output Jun-Aug ───────
    mothball = _wind_pattern(full_index, 80.0)
    zero_mask = (mothball.index.month >= 6) & (mothball.index.month <= 8)
    mothball = mothball.copy()
    mothball[zero_mask] = 0.0
    _write_series(scada_dir / "MOTHBALLWF1_2025.parquet", mothball, "scadavalue")

    # ── NODATAWF1: registry entry only, NO scada file ──────────────────────
    # (intentionally no file written)

    # ── TINYSF1: below capacity threshold, still gets full good data ───────
    tinysf1 = _solar_pattern(full_index, 25.0)
    _write_series(scada_dir / "TINYSF1_2025.parquet", tinysf1, "scadavalue")

    # ── COALX1: wrong fuel_tech, gets full good data too ───────────────────
    coalx1 = _wind_pattern(full_index, 700.0)
    _write_series(scada_dir / "COALX1_2025.parquet", coalx1, "scadavalue")

    # ── Price: NSW1 complete deterministic diurnal shape, VIC1 omitted ─────
    minutes_of_day = full_index.hour * 60 + full_index.minute
    price_shape = 40.0 + 30.0 * (minutes_of_day / (24 * 60) * 2 * math.pi).map(math.sin)
    price_series = pd.Series(price_shape.values, index=full_index)
    _write_series(price_dir / "rrp_NSW1_2025.parquet", price_series, "rrp")
    # rrp_VIC1_2025.parquet deliberately omitted

    # ── Registry ────────────────────────────────────────────────────────────
    rows = [
        {"duid": "FULLWF1", "station_name": "Fullyear Wind Farm", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating"},
        {"duid": "GAPSF1", "station_name": "Gaps Solar Farm", "region": "QLD1",
         "fuel_tech": "Solar", "capacity_registered_mw": 200.0, "lat": -23.5, "lon": 151.0,
         "status": "operating"},
        {"duid": "LATECOMSF1", "station_name": "Latecomer Solar Farm", "region": "VIC1",
         "fuel_tech": "Solar", "capacity_registered_mw": 150.0, "lat": -37.5, "lon": 144.5,
         "status": "operating"},
        {"duid": "MOTHBALLWF1", "station_name": "Mothballed Wind Farm", "region": "SA1",
         "fuel_tech": "Wind", "capacity_registered_mw": 80.0, "lat": -34.9, "lon": 138.6,
         "status": "operating"},
        {"duid": "NODATAWF1", "station_name": "Nodata Wind Farm", "region": "TAS1",
         "fuel_tech": "Wind", "capacity_registered_mw": 120.0, "lat": -42.0, "lon": 147.0,
         "status": "operating"},
        {"duid": "TINYSF1", "station_name": "Tiny Solar Farm", "region": "NSW1",
         "fuel_tech": "Solar", "capacity_registered_mw": 25.0, "lat": -33.5, "lon": 148.0,
         "status": "operating"},
        {"duid": "COALX1", "station_name": "Coal Power Station X", "region": "NSW1",
         "fuel_tech": "Black Coal", "capacity_registered_mw": 700.0, "lat": -32.5, "lon": 150.8,
         "status": "operating"},
    ]
    registry = pd.DataFrame(rows)
    registry.to_parquet(registry_dir / "nem_plant_registry.parquet")

    return cache_dir

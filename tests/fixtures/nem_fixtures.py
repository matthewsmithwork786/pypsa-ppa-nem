"""Deterministic synthetic NEM cache fixture for testing `ppa.data.nem_data`.

Builds a small `data/cache/nem`-shaped tree under a temp directory covering the
whole-year heuristic edge cases without needing real AEMO data. Covers three
years (2023-2025, including the leap year 2024) and ships a synthetic
`plant_years.parquet` operational manifest matching the schema produced by
`scripts/build_plant_year_manifest.py`, so multi-year tests have a deterministic
manifest to read.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

YEAR = 2025
YEARS = [2023, 2024, 2025]
N_INTERVALS = 105_120  # 365 * 288, non-leap 2025

PLANT_YEARS_COLUMNS = [
    "duid", "year", "station_name", "region", "fuel_tech",
    "capacity_registered_mw_used", "n_intervals", "expected_intervals",
    "coverage", "first_ts", "last_ts", "longest_gap_hours",
    "longest_zero_run_hours", "monthly_peak_ratio_min",
    "monthly_peak_ratio_by_month", "annual_cuf", "cuf_vs_plant_median",
    "first_power_date", "fully_operational", "reject_reasons",
]


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _n_intervals(year: int) -> int:
    return (366 if _is_leap(year) else 365) * 288


def _n_hours(year: int) -> int:
    return 8784 if _is_leap(year) else 8760


def _full_year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(start=f"{year}-01-01 00:00", periods=_n_intervals(year), freq="5min")


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


def _build_plant_years_manifest(registry: pd.DataFrame) -> pd.DataFrame:
    """Synthetic per-(duid, year) operational manifest.

    FULLWF1/GAPSF1 are fully operational through 2023-24; GAPSF1 is offline
    during 2025 (major outage). MOTHBALLWF1 is operational only from 2024.
    The remainder are non-operational across all years so the intersection
    tests exercise both keeping plants and dropping them.
    """
    spec = {
        "FULLWF1": {2023: True, 2024: True, 2025: True},
        "GAPSF1": {2023: True, 2024: True, 2025: False},
        "LATECOMSF1": {2023: False, 2024: False, 2025: False},
        "MOTHBALLWF1": {2023: False, 2024: True, 2025: True},
        "NODATAWF1": {2023: False, 2024: False, 2025: False},
        "TINYSF1": {2023: True, 2024: True, 2025: True},
        "COALX1": {2023: True, 2024: True, 2025: True},
    }
    annual_cuf = {
        "FULLWF1": 0.45, "GAPSF1": 0.25, "LATECOMSF1": None, "MOTHBALLWF1": 0.30,
        "NODATAWF1": None, "TINYSF1": 0.20, "COALX1": 0.60,
    }
    first_power = {
        "FULLWF1": "2018-01-01", "GAPSF1": "2019-01-01", "LATECOMSF1": "2025-07-01",
        "MOTHBALLWF1": "2020-01-01", "NODATAWF1": None, "TINYSF1": "2017-01-01",
        "COALX1": "2001-01-01",
    }

    rows = []
    for _, reg in registry.iterrows():
        duid = reg["duid"]
        for year in YEARS:
            operational = spec[duid][year]
            n_exp = _n_intervals(year)
            if operational:
                reject = ""
            elif duid == "GAPSF1":
                reject = "major outage"
            else:
                reject = "still commissioning"
            rows.append({
                "duid": duid,
                "year": year,
                "station_name": reg["station_name"],
                "region": reg["region"],
                "fuel_tech": reg["fuel_tech"],
                "capacity_registered_mw_used": reg["capacity_registered_mw"],
                "n_intervals": n_exp,
                "expected_intervals": n_exp,
                "coverage": 1.0 if operational else 0.0,
                "first_ts": f"{year}-01-01 00:00:00" if operational else None,
                "last_ts": f"{year}-12-31 23:55:00" if operational else None,
                "longest_gap_hours": 0.0 if operational else 24.0,
                "longest_zero_run_hours": 0.0,
                "monthly_peak_ratio_min": 0.9,
                "monthly_peak_ratio_by_month": json.dumps({m: 1.0 for m in range(1, 13)}),
                "annual_cuf": annual_cuf[duid],
                "cuf_vs_plant_median": 1.0 if operational else 0.5,
                "first_power_date": first_power[duid],
                "fully_operational": bool(operational),
                "reject_reasons": reject,
            })
    return pd.DataFrame(rows, columns=PLANT_YEARS_COLUMNS)


def build_nem_fixture_cache(root_dir) -> Path:
    """Build a synthetic NEM cache tree under `root_dir` and return the cache root.

    `root_dir` should be a fresh temp directory; the returned Path is suitable
    for passing directly as `cache_dir=` to any `ppa.data.nem_data` function
    (it contains `registry/`, `scada/`, `price/` subdirectories, and a
    `registry/plant_years.parquet` operational manifest).
    """
    cache_dir = Path(root_dir)
    scada_dir = cache_dir / "scada"
    price_dir = cache_dir / "price"
    registry_dir = cache_dir / "registry"
    scada_dir.mkdir(parents=True, exist_ok=True)
    price_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)

    for year in YEARS:
        full_index = _full_year_index(year)

        # ── FULLWF1: complete, passes all checks ────────────────────────────
        _write_series(scada_dir / f"FULLWF1_{year}.parquet", _wind_pattern(full_index, 100.0), "scadavalue")

        # ── GAPSF1: full span, ~6% of intervals missing (fails coverage only) ──
        gapsf1_full = _solar_pattern(full_index, 200.0)
        keep_mask = [i % 17 != 0 for i in range(len(full_index))]  # drops ~5.9% of rows
        _write_series(scada_dir / f"GAPSF1_{year}.parquet", gapsf1_full[keep_mask], "scadavalue")

        # ── LATECOMSF1: no data before July 1 (fails coverage, span, monthly) ──
        latecom_index = pd.date_range(start=f"{year}-07-01 00:00", end=f"{year}-12-31 23:55", freq="5min")
        _write_series(scada_dir / f"LATECOMSF1_{year}.parquet", _solar_pattern(latecom_index, 150.0), "scadavalue")

        # ── MOTHBALLWF1: complete span+coverage, but zero output Jun-Aug ───────
        mothball = _wind_pattern(full_index, 80.0)
        zero_mask = (mothball.index.month >= 6) & (mothball.index.month <= 8)
        mothball = mothball.copy()
        mothball[zero_mask] = 0.0
        _write_series(scada_dir / f"MOTHBALLWF1_{year}.parquet", mothball, "scadavalue")

        # ── TINYSF1: below capacity threshold, still gets full good data ───────
        _write_series(scada_dir / f"TINYSF1_{year}.parquet", _solar_pattern(full_index, 25.0), "scadavalue")

        # ── COALX1: wrong fuel_tech, gets full good data too ───────────────────
        _write_series(scada_dir / f"COALX1_{year}.parquet", _wind_pattern(full_index, 700.0), "scadavalue")

        # ── Price: NSW1 complete deterministic diurnal shape, VIC1 omitted ─────
        minutes_of_day = full_index.hour * 60 + full_index.minute
        price_shape = 40.0 + 30.0 * (minutes_of_day / (24 * 60) * 2 * math.pi).map(math.sin)
        price_series = pd.Series(price_shape.values, index=full_index)
        _write_series(price_dir / f"rrp_NSW1_{year}.parquet", price_series, "rrp")
        # rrp_VIC1_<year>.parquet deliberately omitted

    # ── NODATAWF1: registry entry only, NO scada file ──────────────────────
    # (intentionally no file written)

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

    # ── Per-(duid, year) operational manifest ───────────────────────────────
    manifest = _build_plant_years_manifest(registry)
    manifest.to_parquet(registry_dir / "plant_years.parquet")

    return cache_dir


# ── Manifest (operational-year determination) fixture ────────────────────────

def _compact_availability_values(series: pd.Series, year: int) -> pd.Series:
    """Reindex a generated 5-min series onto the interval-ENDING canonical grid
    used by the real availability cache (values-only RangeIndex parquets), so
    `ppa.data.nem_data.load_availability` reads it back via the compact path.

    The generated series is labelled interval-BEGINNING (value for [t, t+5min)).
    Storing `series.shift(freq="5min")` places the same value at the matching
    interval-END label, so `load_availability`'s shift back to interval-beginning
    round-trips the series exactly -- no trailing NaN and no phase shift.
    """
    idx = pd.date_range(
        start=f"{year}-01-01 00:05",
        periods=expected_intervals(year),
        freq="5min",
    )
    return series.shift(freq="5min").reindex(idx)


def expected_intervals(year: int) -> int:
    """288 dispatch intervals per day (5 minutes each), leap-aware."""
    return (366 if pd.Timestamp(year, 12, 31).is_leap_year else 365) * 288


def _write_compact_availability(
    avail_dir: Path, duid: str, year: int, series: pd.Series
) -> None:
    """Write a values-only RangeIndex parquet, the real cache format."""
    reindexed = _compact_availability_values(series, year)
    df = pd.DataFrame({"availability": reindexed.to_numpy(dtype="float64")})
    df.to_parquet(avail_dir / f"{duid}_{year}.parquet")


def build_manifest_fixture_cache(root_dir) -> Path:
    """Build a synthetic availability cache + registry for
    `scripts/build_plant_year_manifest.py` and return the cache root.

    Mirrors the real `data/cache/nem/` layout: `<DUID>_2025.parquet` files in
    `availability/` (compact values-only format, NaN for gaps) and a registry
    carrying `first_power_date`. Covers every branch of the `fully_operational`
    gate:

    - CLEANYR1  full clean year                                -> operational
    - GAP20D1   full year with a contiguous 20-day NaN gap     -> gap + coverage
    - MARCOMM1  zero output until 1 Mar, then full year        -> commissioning
    - OCTRAMP1  full output Jan-Sep, then output collapses     -> ramp-down
    - COV96SF1  96% coverage (scattered isolated gaps)         -> coverage only
    - NEWLY1    clean year but first_power_date Jun 2025       -> age gate
    """
    cache_dir = Path(root_dir)
    avail_dir = cache_dir / "availability"
    registry_dir = cache_dir / "registry"
    avail_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)

    full_index = _full_year_index()
    capacity = 100.0

    clean = _wind_pattern(full_index, capacity)

    # GAP20D1: 20 contiguous days of NaN starting 30 May (day 149).
    gap20 = clean.copy()
    gap_start = pd.Timestamp(f"{YEAR}-05-30 00:00")
    gap20[(gap20.index >= gap_start) & (gap20.index < gap_start + pd.Timedelta(days=20))] = np.nan

    # MARCOMM1: zero until 1 Mar, then the full pattern.
    marcomm = clean.copy()
    marcomm[marcomm.index < pd.Timestamp(f"{YEAR}-03-01 00:00")] = 0.0

    # OCTRAMP1: full pattern Jan-Sep, then at 30% (ramping down toward retirement).
    octramp = clean.copy()
    octramp[octramp.index.month >= 10] = octramp[octramp.index.month >= 10] * 0.3

    # COV96SF1: drop every 25th interval (~4% scattered, single-interval gaps).
    cov96 = clean.copy()
    drop_mask = [i % 25 == 0 for i in range(len(full_index))]
    cov96[dropped_index := full_index[drop_mask]] = np.nan

    for duid, series in [
        ("CLEANYR1", clean),
        ("GAP20D1", gap20),
        ("MARCOMM1", marcomm),
        ("OCTRAMP1", octramp),
        ("COV96SF1", cov96),
        ("NEWLY1", clean),
    ]:
        _write_compact_availability(avail_dir, duid, YEAR, series)

    rows = [
        {"duid": "CLEANYR1", "station_name": "Clean Year Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2000-01-01"},
        {"duid": "GAP20D1", "station_name": "Gap Twenty Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2000-01-01"},
        {"duid": "MARCOMM1", "station_name": "March Commission Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2025-03-01"},
        {"duid": "OCTRAMP1", "station_name": "October Rampdown Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2000-01-01"},
        {"duid": "COV96SF1", "station_name": "Coverage Ninety Six Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2000-01-01"},
        {"duid": "NEWLY1", "station_name": "Newly Built Wind", "region": "NSW1",
         "fuel_tech": "Wind", "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 147.0,
         "status": "operating", "first_power_date": "2025-06-01"},
    ]
    registry = pd.DataFrame(rows)
    registry.to_parquet(registry_dir / "nem_plant_registry.parquet")

    return cache_dir

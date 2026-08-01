"""Deterministic (no RNG) CSV-bytes builders for custom-upload tests.

Mirrors the style of tests/fixtures/nem_fixtures.py: pure functions, no
Streamlit dependency, everything reproducible.
"""
from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["timestamp", "ts_PVGen", "ts_WindGen", "ts_LoadMW", "ts_MktPrice"]


def _pv_series(idx: pd.DatetimeIndex) -> np.ndarray:
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0
    return np.round(np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85, 4)


def _wind_series(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.round(np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0), 4)


def _price_series(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.round(70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24), 2)


def build_valid_full_year_df(year: int = 2025, load_mw: float = 100.0) -> pd.DataFrame:
    """A full (non-leap) year of hourly data, all columns valid."""
    n_hours = 8760 if not _is_leap(year) else 8784
    idx = pd.date_range(f"{year}-01-01 00:00", periods=n_hours, freq="h")
    return pd.DataFrame({
        "timestamp": idx.strftime("%Y-%m-%d %H:%M"),
        "ts_PVGen": _pv_series(idx),
        "ts_WindGen": _wind_series(idx),
        "ts_LoadMW": load_mw,
        "ts_MktPrice": _price_series(idx),
    })


def _is_leap(year: int) -> bool:
    import calendar
    return calendar.isleap(year)


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def build_valid_full_year_csv(year: int = 2025, load_mw: float = 100.0) -> bytes:
    return df_to_csv_bytes(build_valid_full_year_df(year=year, load_mw=load_mw))


def build_missing_column_csv(missing_col: str, hours: int = 48) -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    df = df.drop(columns=[missing_col])
    return df_to_csv_bytes(df)


def build_out_of_range_cf_csv(hours: int = 48, col: str = "ts_PVGen") -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    df.loc[df.index[0], col] = 1.5  # out of [0, 1]
    return df_to_csv_bytes(df)


def build_percent_scale_cf_csv(hours: int = 48) -> bytes:
    """CFs expressed as 0-100 percentages instead of 0-1 fractions."""
    df = build_valid_full_year_df().head(hours).copy()
    df["ts_PVGen"] = df["ts_PVGen"] * 100.0
    df["ts_WindGen"] = df["ts_WindGen"] * 100.0
    return df_to_csv_bytes(df)


def build_negative_load_csv(hours: int = 48) -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    df.loc[df.index[3], "ts_LoadMW"] = -10.0
    return df_to_csv_bytes(df)


def build_non_numeric_junk_csv(hours: int = 48, col: str = "ts_MktPrice") -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    df[col] = df[col].astype(object)
    df.loc[df.index[5], col] = "not_a_number"
    return df_to_csv_bytes(df)


def build_bad_timestamp_csv(hours: int = 48) -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    df.loc[df.index[2], "timestamp"] = "not-a-date"
    return df_to_csv_bytes(df)


def build_sub_hourly_csv(hours: int = 2, load_mw: float = 100.0) -> bytes:
    """5-minute resolution data."""
    idx = pd.date_range("2025-01-01 00:00", periods=hours * 12, freq="5min")
    df = pd.DataFrame({
        "timestamp": idx.strftime("%Y-%m-%d %H:%M"),
        "ts_PVGen": _pv_series(idx),
        "ts_WindGen": _wind_series(idx),
        "ts_LoadMW": load_mw,
        "ts_MktPrice": _price_series(idx),
    })
    return df_to_csv_bytes(df)


def build_super_hourly_csv(periods: int = 16, load_mw: float = 100.0) -> bytes:
    """3-hour resolution data."""
    idx = pd.date_range("2025-01-01 00:00", periods=periods, freq="3h")
    df = pd.DataFrame({
        "timestamp": idx.strftime("%Y-%m-%d %H:%M"),
        "ts_PVGen": _pv_series(idx),
        "ts_WindGen": _wind_series(idx),
        "ts_LoadMW": load_mw,
        "ts_MktPrice": _price_series(idx),
    })
    return df_to_csv_bytes(df)


def build_gapped_csv(hours: int = 48, drop_hours: tuple = (5, 6, 20)) -> bytes:
    """Regular hourly cadence but with some timestamps missing entirely."""
    df = build_valid_full_year_df().head(hours).copy()
    df = df.drop(df.index[list(drop_hours)]).reset_index(drop=True)
    return df_to_csv_bytes(df)


def build_duplicate_timestamps_csv(hours: int = 48) -> bytes:
    df = build_valid_full_year_df().head(hours).copy()
    dup_row = df.iloc[[10]].copy()
    dup_row["ts_MktPrice"] = dup_row["ts_MktPrice"] + 1.0  # distinguishable "last" value
    df = pd.concat([df, dup_row], ignore_index=True)
    return df_to_csv_bytes(df)


def build_distinctive_load_shape_csv(hours: int = 48) -> bytes:
    """Non-flat, distinctive load shape to prove load passthrough isn't
    accidentally matching a coincidental flat default."""
    idx = pd.date_range("2025-01-01 00:00", periods=hours, freq="h")
    # A sawtooth-like shape, deterministic, clearly not flat and not any of the
    # synthetic industrial profiles.
    load = 50.0 + 3.0 * (np.arange(hours) % 7)
    df = pd.DataFrame({
        "timestamp": idx.strftime("%Y-%m-%d %H:%M"),
        "ts_PVGen": _pv_series(idx),
        "ts_WindGen": _wind_series(idx),
        "ts_LoadMW": load,
        "ts_MktPrice": _price_series(idx),
    })
    return df_to_csv_bytes(df)


def csv_bytes_to_file(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)

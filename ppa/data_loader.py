from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ppa.industrial_profiles import get_load_series
from ppa.scenario import Scenario

REQUIRED_COLUMNS = ["timestamp", "ts_PVGen", "ts_WindGen", "ts_NSWPrice"]

_DEFAULT_CSV_CANDIDATES = [
    Path(__file__).parent.parent / "data" / "march_2025_pypsa_timeseries.csv",
]

# ── Custom CSV upload (Phase 3) ────────────────────────────────────────────────

CUSTOM_UPLOAD_COLUMNS = ["timestamp", "ts_PVGen", "ts_WindGen", "ts_LoadMW", "ts_MktPrice"]
CUSTOM_DATA_COLUMNS = ["ts_PVGen", "ts_WindGen", "ts_LoadMW", "ts_MktPrice"]
CUSTOM_CF_COLUMNS = ("ts_PVGen", "ts_WindGen")
TEMPLATE_HOURS = 48
TEMPLATE_START = "2025-01-01 00:00"
TEMPLATE_LOAD_MW = 100.0


def find_default_csv() -> Path | None:
    for p in _DEFAULT_CSV_CANDIDATES:
        if p.exists():
            return p
    return None


def load_timeseries(csv_path: str | Path) -> pd.DataFrame:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Timeseries CSV not found: {csv_path}")

    ts = pd.read_csv(csv_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in ts.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    ts["timestamp"] = pd.to_datetime(ts["timestamp"])
    ts = ts.sort_values("timestamp").set_index("timestamp")
    ts.index.name = "snapshot"
    return ts


def prepare_timeseries(ts: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    if getattr(scenario, "data_source", "european") == "custom_csv" and "ts_LoadMW" in ts.columns:
        return prepare_custom_timeseries(ts)

    ts = ts.copy()
    # European data already carries ts_MktPrice; the legacy CSV exposes ts_NSWPrice.
    if "ts_MktPrice" not in ts.columns and "ts_NSWPrice" in ts.columns:
        ts["ts_MktPrice"] = ts["ts_NSWPrice"]
    profile = get_load_series(scenario.load_profile, ts.index)
    ts["ppaload_mw"] = (profile * scenario.ppaload_mw).values
    return ts


def get_available_days(ts: pd.DataFrame) -> list[str]:
    return sorted({str(d.date()) for d in ts.index})


def coerce_chosen_day(ts: pd.DataFrame, chosen_day: str) -> str:
    """Return *chosen_day* if it is one of the days in *ts*, otherwise the
    nearest available day (or the middle day when *chosen_day* is unparseable).

    Pure helper so the UI can reconcile a stale ``Scenario.chosen_day`` with the
    user-selected reference period instead of hard-blocking the run with
    "chosen_day … is not present in the timeseries data".
    """
    available = get_available_days(ts)
    if chosen_day in available:
        return chosen_day
    if not available:
        return chosen_day
    try:
        target = pd.Timestamp(chosen_day)
    except (ValueError, TypeError):
        return available[len(available) // 2]
    return min(available, key=lambda d: abs((pd.Timestamp(d) - target).days))


# ── Template generation ────────────────────────────────────────────────────────

def build_upload_template(
    hours: int = TEMPLATE_HOURS,
    start: str = TEMPLATE_START,
    load_mw: float = TEMPLATE_LOAD_MW,
) -> bytes:
    """Return a realistic, deterministic (no RNG) example CSV as UTF-8 bytes."""
    idx = pd.date_range(start, periods=hours, freq="h")
    minutes_of_day = idx.hour * 60 + idx.minute
    frac = minutes_of_day / 1440.0

    pv = np.maximum(0.0, np.sin(np.pi * (frac - 0.25) / 0.5)) * 0.85
    wind = np.clip(0.35 + 0.25 * np.sin(2 * np.pi * idx.hour / 24 + 1.0), 0.0, 1.0)
    price = 70 + 40 * np.sin(2 * np.pi * (idx.hour - 16) / 24)

    df = pd.DataFrame(
        {
            "timestamp": idx.strftime("%Y-%m-%d %H:%M"),
            "ts_PVGen": np.round(pv, 4),
            "ts_WindGen": np.round(wind, 4),
            "ts_LoadMW": load_mw,
            "ts_MktPrice": np.round(price, 2),
        }
    )
    return df.to_csv(index=False).encode("utf-8")


# ── Upload validation / loading ───────────────────────────────────────────────

def _load_custom_upload_raw(file) -> tuple[pd.DataFrame, int]:
    """Parse + validate the raw upload. Returns (indexed_df, n_duplicates_dropped)."""
    try:
        ts = pd.read_csv(file)
    except Exception as exc:  # noqa: BLE001 - re-raised with a clear message
        raise ValueError(f"Could not parse the uploaded file as CSV: {exc}") from exc

    missing = [c for c in CUSTOM_UPLOAD_COLUMNS if c not in ts.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    if ts.empty:
        raise ValueError("Uploaded CSV contains no data rows.")

    try:
        ts["timestamp"] = pd.to_datetime(ts["timestamp"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not parse the 'timestamp' column as datetimes: {exc}") from exc

    for c in CUSTOM_DATA_COLUMNS:
        original = ts[c]
        coerced = pd.to_numeric(original, errors="coerce")
        # NaN after coercion means either the source cell was already empty/NaN
        # or it contained non-numeric junk that couldn't be parsed — either way
        # it's an invalid data value.
        bad_mask = coerced.isna()
        if bad_mask.any():
            n = int(bad_mask.sum())
            # `ts.index` here is still the 0-based pandas RangeIndex position
            # (this runs before `set_index("timestamp")`). A user opening the
            # raw CSV in a spreadsheet sees row 1 = header, row 2 = first data
            # row, so the 0-based position must be offset by 2 to match what
            # they'd see on screen.
            first_row = int(ts.index[bad_mask][0]) + 2
            raise ValueError(
                f"Column '{c}' contains {n} non-numeric or empty value(s) "
                f"(first at CSV row {first_row})."
            )
        ts[c] = coerced

    ts = ts.sort_values("timestamp")
    n_duplicates = int(ts["timestamp"].duplicated(keep="last").sum())
    ts = ts.drop_duplicates(subset="timestamp", keep="last")
    ts = ts.set_index("timestamp")
    ts.index.name = "snapshot"

    for c in CUSTOM_CF_COLUMNS:
        lo, hi = float(ts[c].min()), float(ts[c].max())
        if lo < 0.0 or hi > 1.0:
            raise ValueError(
                f"Column '{c}' must be a capacity factor in [0, 1] — found min {lo:.3f}, "
                f"max {hi:.3f}. (If the values are percentages, divide them by 100.)"
            )

    lo_load = float(ts["ts_LoadMW"].min())
    if lo_load < 0:
        raise ValueError(f"Column 'ts_LoadMW' must be >= 0 MW — found min {lo_load:.3f}.")

    return ts, n_duplicates


def load_custom_upload(file) -> pd.DataFrame:
    """Load and validate a user-supplied custom timeseries CSV.

    Accepts a path, string, or file-like object (Streamlit's ``UploadedFile``,
    ``io.BytesIO``/``io.StringIO`` for tests).
    """
    ts, n_duplicates = _load_custom_upload_raw(file)
    ts.attrs["n_duplicate_timestamps"] = n_duplicates
    return ts


def describe_custom_timeseries(ts: pd.DataFrame, n_duplicate_timestamps: int | None = None) -> dict:
    """Pure diagnostics summary of a loaded (pre-``prepare``) custom timeseries."""
    if n_duplicate_timestamps is None:
        n_duplicate_timestamps = int(ts.attrs.get("n_duplicate_timestamps", 0))

    n_rows = len(ts)
    first = ts.index[0]
    last = ts.index[-1]
    span_days = (last - first).days

    deltas = ts.index.to_series().diff().dropna()
    if len(deltas) > 0:
        modal_step = deltas.value_counts().idxmax()
    else:
        modal_step = pd.Timedelta(hours=1)

    is_hourly = modal_step == pd.Timedelta(hours=1)
    is_sub_hourly = modal_step < pd.Timedelta(hours=1)

    if modal_step > pd.Timedelta(0):
        expected_slots = int((last - first) / modal_step) + 1
    else:
        expected_slots = n_rows
    n_gaps = max(0, expected_slots - n_rows)

    is_full_year = n_rows in (8760, 8784) and span_days >= 364
    year = int(first.year)

    negative_price_hours = int((ts["ts_MktPrice"] < 0).sum())

    return {
        "n_rows": n_rows,
        "first": first,
        "last": last,
        "span_days": span_days,
        "modal_step": modal_step,
        "is_hourly": is_hourly,
        "is_sub_hourly": is_sub_hourly,
        "n_gaps": n_gaps,
        "n_duplicate_timestamps": n_duplicate_timestamps,
        "is_full_year": is_full_year,
        "year": year,
        "pv_cf_mean": float(ts["ts_PVGen"].mean()),
        "wind_cf_mean": float(ts["ts_WindGen"].mean()),
        "load_mw_mean": float(ts["ts_LoadMW"].mean()),
        "load_mw_peak": float(ts["ts_LoadMW"].max()),
        "price_mean": float(ts["ts_MktPrice"].mean()),
        "price_min": float(ts["ts_MktPrice"].min()),
        "price_max": float(ts["ts_MktPrice"].max()),
        "negative_price_hours": negative_price_hours,
    }


def prepare_custom_timeseries(ts: pd.DataFrame) -> pd.DataFrame:
    """Prepare a validated custom upload for the dispatch/sizing LP.

    Sub-hourly data is resampled to hourly means; super-hourly data (each row
    already one snapshot) is left as-is. ``ts_LoadMW`` (absolute MW) passes
    straight through as ``ppaload_mw``, bypassing the profile×ppaload_mw
    synthesis used for the European/NEM paths.
    """
    ts = ts.copy()

    deltas = ts.index.to_series().diff().dropna()
    if len(deltas) > 0:
        modal_step = deltas.value_counts().idxmax()
        if modal_step < pd.Timedelta(hours=1):
            ts = ts.resample("h").mean()

    ts["ppaload_mw"] = ts["ts_LoadMW"]
    ts.index.name = "snapshot"
    return ts


def custom_timeseries_dicts(ts: pd.DataFrame, year: int) -> tuple:
    """Wrap one prepared custom-upload frame into the per-year dicts
    ``run_multi_year``/``build_sizing_timeseries`` expect."""
    return (
        {year: ts["ts_PVGen"]},
        {year: ts["ts_WindGen"]},
        {year: ts["ts_MktPrice"]},
        {year: ts["ppaload_mw"]},
    )

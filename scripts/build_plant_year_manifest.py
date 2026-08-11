#!/usr/bin/env python3
"""Build the operational-year determination manifest (`plant_years.parquet`).

*** Reads only the already-committed `data/cache/nem/` files -- no network
access needed, unlike the `fetch_*` scripts. Safe to run in this sandbox. ***

For every `<DUID>_<year>.parquet` file in `data/cache/nem/availability/` this
script computes a row of structural and utilisation metrics for that (duid,
year) pair and applies the `fully_operational` gate that the multi-year sizing
path will use to decide which calendar years a plant is a fair representation
of the finished asset. The registry (`data/cache/nem/registry/
nem_plant_registry.parquet`) is denormalised onto each row so the output is
self-contained for the plant picker.

The gate generalises the single-year heuristics in `ppa/data/nem_data.py`
(`whole_year_check` + `commissioning_ramp_check`) in three ways:

- `monthly_peak_ratio_min` applies the commissioning-ramp discriminator to
  **all 12 months** instead of only Jan/Feb, so mid-year commissioning,
  ramp-down before retirement and month-long outages are all caught
  symmetrically. The ratio is each month's peak divided by the plant's own
  annual peak, which keeps it robust to AC clipping at ~0.80 of nameplate and
  to heavy curtailment late in the year (same design rationale as
  `nem_data.COMMISSIONING_MIN_PEAK_FRACTION`).
- Coverage/span thresholds are tightened (98% / 3 Jan-29 Dec) because a
  multi-year archive can afford to reject an 18-day hole that the old 95% /
  15 Jan-15 Dec rule admitted.
- `longest_gap_hours` and `longest_zero_run_hours` bound single outages in
  absolute terms, and the registry's `first_power_date` (when present) must
  predate the year start by 60 days.

`annual_cuf`, `cuf_vs_plant_median` and `monthly_peak_ratio_by_month` are
recorded for diagnosis and tuning but are NOT part of the gate.

    python scripts/build_plant_year_manifest.py
"""
from __future__ import annotations

import argparse
import calendar
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ppa.data import nem_data  # noqa: E402

# ── Gate thresholds ──────────────────────────────────────────────────────────

MIN_COVERAGE = 0.98
FIRST_TS_LATEST = (1, 3, 23, 55)          # need data by {year}-01-03 23:55
LAST_TS_EARLIEST = (12, 29, 0, 0)         # need data through {year}-12-29 00:00
MIN_MONTHLY_PEAK_RATIO = 0.60             # mirrors nem_data.COMMISSIONING_MIN_PEAK_FRACTION
MAX_GAP_HOURS = 336.0                     # 14 days of consecutive NaN intervals
MAX_ZERO_RUN_HOURS = 336.0                # 14 days of consecutive zero intervals
MIN_AGE_BEFORE_YEAR_DAYS = 60             # first_power_date lead time before 1 Jan

MANIFEST_FILENAME = "plant_years.parquet"
MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_availability_years(cache_dir: Path) -> dict:
    """Map year -> sorted DUID list from the `<DUID>_<year>.parquet` filenames.

    Works for however many years are present -- nothing is hardcoded to 2025.
    """
    avail_dir = Path(cache_dir) / "availability"
    by_year: dict[int, list[str]] = {}
    if not avail_dir.exists():
        return by_year
    for p in sorted(avail_dir.glob("*.parquet")):
        parts = p.stem.rsplit("_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        year = int(parts[1])
        by_year.setdefault(year, []).append(parts[0].strip().upper())
    for year in by_year:
        by_year[year].sort()
    return by_year


def _longest_run_intervals(mask: np.ndarray) -> int:
    """Length of the longest run of True in a boolean array."""
    if not mask.any():
        return 0
    padded = np.concatenate(([0], mask.astype(np.int8), [0]))
    diff = np.diff(padded)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return int((ends - starts).max())


# ── Per-(duid, year) metrics ─────────────────────────────────────────────────

def build_year_summary(
    series: pd.Series, capacity_mw: float, year: int
) -> dict:
    """Structural + utilisation metrics for one (duid, year) from the 5-min
    availability series.

    `series` may be in either cache format (compact values-only on the canonical
    grid, or an explicit DatetimeIndex). It is reindexed onto the canonical
    interval-beginning 5-min grid so that absent rows become NaN gaps and the
    coverage/gap metrics are measured against the full year.
    """
    expected = nem_data.expected_intervals(year)
    canonical = pd.date_range(f"{year}-01-01", periods=expected, freq="5min")
    series = series.reindex(canonical)
    values = series.to_numpy(dtype="float64")

    non_null = series.dropna()
    n_intervals = int(non_null.shape[0])
    coverage = n_intervals / expected if expected else 0.0
    first_ts = non_null.index.min() if n_intervals else None
    last_ts = non_null.index.max() if n_intervals else None

    longest_gap_intervals = _longest_run_intervals(np.isnan(values))
    # NaN breaks a zero run (an unknown interval is not a zero interval).
    longest_zero_intervals = _longest_run_intervals(values == 0.0)

    annual_max = float(non_null.max()) if n_intervals else 0.0
    monthly_max = (
        non_null.groupby(non_null.index.month).max() if n_intervals else pd.Series(dtype=float)
    )
    monthly_ratios = []
    for m in range(1, 13):
        m_max = float(monthly_max.loc[m]) if m in monthly_max.index else 0.0
        monthly_ratios.append(m_max / annual_max if annual_max > 0 else 0.0)

    if capacity_mw and capacity_mw > 0:
        energy_mwh = float(non_null.sum() * (nem_data.INTERVAL_MINUTES / 60.0))
        annual_cuf = energy_mwh / (float(capacity_mw) * nem_data.expected_hours(year))
    else:
        annual_cuf = None

    return {
        "n_intervals": n_intervals,
        "expected_intervals": expected,
        "coverage": coverage,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "longest_gap_hours": longest_gap_intervals * nem_data.INTERVAL_MINUTES / 60.0,
        "longest_zero_run_hours": longest_zero_intervals * nem_data.INTERVAL_MINUTES / 60.0,
        "monthly_peak_ratio_min": min(monthly_ratios),
        "monthly_peak_ratio_by_month": monthly_ratios,
        "annual_cuf": annual_cuf,
    }


def apply_gate(
    metrics: dict, year: int, first_power_date: "str | None"
) -> "tuple[bool, str]":
    """Return (fully_operational, human-readable reject reasons).

    `first_power_date` is a 'YYYY-MM-DD' string or None (the registry may not
    have the column). All six rules must pass:
      1. coverage >= 0.98
      2. data spans 3 Jan 23:55 .. 29 Dec 00:00
      3. min monthly peak ratio >= 0.60
      4. longest NaN gap < 336 h
      5. longest zero run < 336 h
      6. first_power_date <= 1 Jan minus 60 days (when known)
    """
    reasons: list[str] = []

    if metrics["coverage"] < MIN_COVERAGE:
        reasons.append(
            f"coverage {metrics['coverage']:.1%} of 5-min intervals "
            f"(need >= {MIN_COVERAGE:.0%})"
        )

    first_ts = metrics["first_ts"]
    last_ts = metrics["last_ts"]
    first_limit = pd.Timestamp(year, *FIRST_TS_LATEST)
    last_limit = pd.Timestamp(year, *LAST_TS_EARLIEST)
    if first_ts is None or last_ts is None:
        reasons.append("no usable data present")
    else:
        if first_ts > first_limit:
            reasons.append(
                f"record starts {first_ts.date()} (need on/before {first_limit.date()})"
            )
        if last_ts < last_limit:
            reasons.append(
                f"record ends {last_ts.date()} (need on/after {last_limit.date()})"
            )

    if metrics["monthly_peak_ratio_min"] < MIN_MONTHLY_PEAK_RATIO:
        reasons.append(
            f"min monthly peak ratio {metrics['monthly_peak_ratio_min']:.2f} of own "
            f"annual peak (need >= {MIN_MONTHLY_PEAK_RATIO:.2f}); weakest months: "
            + ", ".join(
                MONTH_NAMES[m - 1]
                for m, ratio in enumerate(metrics["monthly_peak_ratio_by_month"], start=1)
                if ratio < MIN_MONTHLY_PEAK_RATIO
            )
        )

    if metrics["longest_gap_hours"] >= MAX_GAP_HOURS:
        reasons.append(
            f"longest data gap {metrics['longest_gap_hours']:.0f} h "
            f"(need < {MAX_GAP_HOURS:.0f} h)"
        )
    if metrics["longest_zero_run_hours"] >= MAX_ZERO_RUN_HOURS:
        reasons.append(
            f"longest zero-output run {metrics['longest_zero_run_hours']:.0f} h "
            f"(need < {MAX_ZERO_RUN_HOURS:.0f} h)"
        )

    if first_power_date is not None:
        earliest_allowed = pd.Timestamp(year, 1, 1) - pd.Timedelta(
            days=MIN_AGE_BEFORE_YEAR_DAYS
        )
        first_power = pd.Timestamp(first_power_date)
        if first_power > earliest_allowed:
            reasons.append(
                f"first power {first_power.date()} later than {earliest_allowed.date()} "
                f"(need >= {MIN_AGE_BEFORE_YEAR_DAYS} days before 1 Jan)"
            )

    return (bool(len(reasons) == 0), "; ".join(reasons))


# ── Assembling the manifest ──────────────────────────────────────────────────

def build_manifest(cache_dir: Path, out_path: Path | None = None) -> pd.DataFrame:
    """Read availability cache + registry and write `plant_years.parquet`.

    Returns the manifest DataFrame (one row per (duid, year)) and writes it to
    `out_path` (default: `data/cache/nem/registry/plant_years.parquet`).
    """
    cache_dir = Path(cache_dir)
    registry = nem_data.load_plant_registry(cache_dir)
    by_year = discover_availability_years(cache_dir)

    rows: list[dict] = []
    for year, duids in sorted(by_year.items()):
        for duid in duids:
            reg_row = registry.loc[registry["duid"] == duid]
            if reg_row.empty:
                station_name = region = fuel_tech = None
                capacity_mw = None
            else:
                reg_row = reg_row.iloc[0]
                station_name = str(reg_row["station_name"])
                region = str(reg_row["region"])
                fuel_tech = str(reg_row["fuel_tech"])
                capacity_mw = float(reg_row["capacity_registered_mw"])
            first_power_date = reg_row["first_power_date"] if not reg_row.empty else None

            try:
                series = nem_data.load_availability(duid, year, cache_dir)
                metrics = build_year_summary(series, capacity_mw, year)
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the whole build
                metrics = {
                    "n_intervals": 0,
                    "expected_intervals": nem_data.expected_intervals(year),
                    "coverage": 0.0,
                    "first_ts": None,
                    "last_ts": None,
                    "longest_gap_hours": float("nan"),
                    "longest_zero_run_hours": float("nan"),
                    "monthly_peak_ratio_min": 0.0,
                    "monthly_peak_ratio_by_month": [0.0] * 12,
                    "annual_cuf": None,
                }
                fully_operational = False
                reject_reasons = f"unreadable cache: {exc}"
            else:
                if capacity_mw is None:
                    fully_operational, reject_reasons = False, "not in registry"
                else:
                    fully_operational, reject_reasons = apply_gate(
                        metrics, year, first_power_date
                    )

            rows.append({
                "duid": duid,
                "year": year,
                "station_name": station_name,
                "region": region,
                "fuel_tech": fuel_tech,
                "capacity_registered_mw_used": capacity_mw,
                "n_intervals": metrics["n_intervals"],
                "expected_intervals": metrics["expected_intervals"],
                "coverage": metrics["coverage"],
                "first_ts": (
                    metrics["first_ts"].strftime("%Y-%m-%d %H:%M:%S")
                    if metrics["first_ts"] is not None else None
                ),
                "last_ts": (
                    metrics["last_ts"].strftime("%Y-%m-%d %H:%M:%S")
                    if metrics["last_ts"] is not None else None
                ),
                "longest_gap_hours": metrics["longest_gap_hours"],
                "longest_zero_run_hours": metrics["longest_zero_run_hours"],
                "monthly_peak_ratio_min": metrics["monthly_peak_ratio_min"],
                "monthly_peak_ratio_by_month": metrics["monthly_peak_ratio_by_month"],
                "annual_cuf": metrics["annual_cuf"],
                "first_power_date": first_power_date,
                "fully_operational": fully_operational,
                "reject_reasons": reject_reasons,
            })

    df = pd.DataFrame(rows)
    df = add_cuf_vs_plant_median(df)
    df = df.sort_values(["year", "station_name", "duid"]).reset_index(drop=True)

    out_path = Path(out_path) if out_path is not None else Path(cache_dir) / "registry" / MANIFEST_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    n_op = int(df["fully_operational"].sum())
    print(
        f"Wrote {out_path} — {len(df)} (duid, year) rows across "
        f"{sorted(by_year)}; {n_op} fully operational."
    )
    return df


def add_cuf_vs_plant_median(df: pd.DataFrame) -> pd.DataFrame:
    """Add `cuf_vs_plant_median`: a plant's annual CUF relative to the median
    of its OWN years that passed the gate. Diagnostic only (not in the gate).
    A plant with no passing year gets NaN; a single-year plant scores 1.0.
    """
    passing = df[df["fully_operational"] & df["annual_cuf"].notna()]
    median_by_plant = passing.groupby("duid")["annual_cuf"].median()
    merged = df.merge(
        median_by_plant.rename("_plant_median_cuf"),
        left_on="duid", right_index=True, how="left",
    )
    ok = merged["annual_cuf"].notna() & merged["_plant_median_cuf"].notna()
    df["cuf_vs_plant_median"] = np.where(
        ok, merged["annual_cuf"] / merged["_plant_median_cuf"], np.nan
    )
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir", type=Path, default=nem_data.NEM_CACHE_DIR,
        help="Path to the data/cache/nem directory (default: repo cache).",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output parquet path (default: <cache-dir>/registry/plant_years.parquet).",
    )
    args = parser.parse_args()
    build_manifest(args.cache_dir, args.out)

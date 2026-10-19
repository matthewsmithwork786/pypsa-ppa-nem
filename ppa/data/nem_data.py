"""Cache-only readers and adapters for NEM registry, generation, and price data.

All acquisition runs offline in `scripts/`; this module does not make network calls.
"""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ────────────────────────────────────────────────────────────────

NEM_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "nem"

# Optional runtime cache for local installations where the packaged cache is
# read-only. Packaged data remains the source of truth and is checked first.
RUNTIME_CACHE_DIR = Path(
    os.environ.get(
        "PPA_RUNTIME_CACHE_DIR",
        Path.home() / ".cache" / "pypsa-ppa-nem" / "nem",
    )
)
REGISTRY_FILENAME = "nem_plant_registry.parquet"
NEM_REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]
DEFAULT_REGION = "NSW1"
DEFAULT_YEAR = 2025
DEFAULT_FUEL_TECHS = ("Wind", "Solar")
MIN_CAPACITY_MW = 30.0
OPERATING_STATUS = "operating"
REGISTRY_COLUMNS = [
    "duid", "station_name", "region", "fuel_tech",
    "capacity_registered_mw", "lat", "lon", "status",
]
INTERVALS_PER_DAY = 288
INTERVAL_MINUTES = 5
WHOLE_YEAR_MIN_COVERAGE = 0.95
WHOLE_YEAR_FIRST_TS_LATEST = (1, 15)
WHOLE_YEAR_LAST_TS_EARLIEST = (12, 15)
WHOLE_YEAR_MONTHLY_MAX_FRACTION = 0.05
# Compare early-year peak to the plant's own annual peak, tolerating AC clipping
# and later curtailment while catching assets still commissioning.
COMMISSIONING_EARLY_MONTHS = (1, 2)
COMMISSIONING_MIN_PEAK_FRACTION = 0.60


# ── Path helpers ─────────────────────────────────────────────────────────────

def _resolve(relative: Path, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    """Resolve from the requested cache, then the optional runtime cache."""
    packaged = Path(cache_dir) / relative
    if packaged.exists():
        return packaged
    runtime = RUNTIME_CACHE_DIR / relative
    if runtime.exists():
        return runtime
    return packaged


def registry_path(cache_dir: Path = NEM_CACHE_DIR) -> Path:
    return _resolve(Path("registry") / REGISTRY_FILENAME, cache_dir)


def eligibility_cache_path(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    """Path to the optional precomputed per-DUID eligibility summary."""
    return _resolve(Path("registry") / f"eligibility_{year}.parquet", cache_dir)


def scada_path(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    duid = duid.strip().upper()
    return _resolve(Path("scada") / f"{duid}_{year}.parquet", cache_dir)


def price_path(region: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    region = region.strip().upper()
    return _resolve(Path("price") / f"rrp_{region}_{year}.parquet", cache_dir)


def expected_intervals(year: int) -> int:
    return expected_hours(year) * 12


def expected_hours(year: int) -> int:
    return (366 if calendar.isleap(year) else 365) * 24


# ── Readers ──────────────────────────────────────────────────────────────────

def load_plant_registry(cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    path = registry_path(cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"NEM plant registry not found at {path}. Run "
            "`python scripts/fetch_nem_plant_registry.py` in a non-sandboxed "
            "environment with network access, then copy the output parquet "
            "into this cache directory."
        )
    df = pd.read_parquet(path)

    missing = [c for c in REGISTRY_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"NEM plant registry at {path} is missing required column(s): {missing}. "
            f"Present columns: {list(df.columns)}"
        )

    df = df.copy()
    df["duid"] = df["duid"].astype(str).str.strip().str.upper()
    df["region"] = df["region"].astype(str).str.strip().str.upper()
    df["fuel_tech"] = df["fuel_tech"].astype(str).str.strip().str.title()
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    for col in ("capacity_registered_mw", "lat", "lon"):
        df[col] = df[col].astype(float)

    # Older registry caches may omit this optional field.
    if "first_power_date" in df.columns:
        df["first_power_date"] = pd.to_datetime(
            df["first_power_date"], errors="coerce", format="mixed"
        ).dt.strftime("%Y-%m-%d")
    else:
        df["first_power_date"] = None

    df = df.drop_duplicates(subset="duid", keep="first")
    df = df.sort_values(["station_name", "duid"]).reset_index(drop=True)
    return df


# ── Multi-year operational manifest ──────────────────────────────────────────

PLANT_YEARS_FILENAME = "plant_years.parquet"
PLANT_YEARS_COLUMNS = [
    "duid", "year", "station_name", "region", "fuel_tech",
    "capacity_registered_mw_used", "n_intervals", "expected_intervals",
    "coverage", "first_ts", "last_ts", "longest_gap_hours",
    "longest_zero_run_hours", "monthly_peak_ratio_min",
    "monthly_peak_ratio_by_month", "annual_cuf", "cuf_vs_plant_median",
    "first_power_date", "fully_operational", "reject_reasons",
]


def plant_years_path(cache_dir: Path = NEM_CACHE_DIR) -> Path:
    """The per-(duid, year) operational manifest built by
    scripts/build_plant_year_manifest.py, resolved packaged-first then runtime."""
    return _resolve(Path("registry") / PLANT_YEARS_FILENAME, cache_dir)


def load_plant_years(cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    """Load the optional per-plant/year manifest, or an empty schema if absent."""
    path = plant_years_path(cache_dir)
    if not path.exists():
        return pd.DataFrame(columns=PLANT_YEARS_COLUMNS)
    df = pd.read_parquet(path)
    missing = [c for c in PLANT_YEARS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"NEM plant-years manifest at {path} is missing required column(s): {missing}. "
            f"Present columns: {list(df.columns)}"
        )
    df = df.copy()
    df["duid"] = df["duid"].astype(str).str.strip().str.upper()
    df["year"] = df["year"].astype(int)
    df["fully_operational"] = df["fully_operational"].fillna(False).astype(bool)
    return df


def available_years(cache_dir: Path = NEM_CACHE_DIR) -> list:
    """Sorted distinct years present in the manifest."""
    manifest = load_plant_years(cache_dir)
    if manifest.empty:
        return []
    return sorted(int(y) for y in manifest["year"].dropna().unique())


def plants_operational_for_years(
    years,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """Registry rows for plants marked fully_operational in EVERY year in
    `years` (intersection, not union). Adds `mean_cuf_selected_years` (mean of
    annual_cuf over the selected years) for display. Empty `years` -> empty
    frame."""
    years = [int(y) for y in years]
    if registry is None:
        registry = load_plant_registry(cache_dir)
    out_columns = list(registry.columns) + ["mean_cuf_selected_years"]
    if not years:
        return pd.DataFrame(columns=out_columns)

    manifest = load_plant_years(cache_dir)
    if manifest.empty:
        return pd.DataFrame(columns=out_columns)

    op = manifest[manifest["fully_operational"] & manifest["year"].isin(years)]
    counts = op.groupby("duid")["year"].apply(lambda s: set(int(y) for y in s))
    keep = [d for d, yrs in counts.items() if set(years).issubset(yrs)]
    if not keep:
        return pd.DataFrame(columns=out_columns)

    selected = manifest[manifest["duid"].isin(keep) & manifest["year"].isin(years)]
    mean_cuf = selected.groupby("duid")["annual_cuf"].mean()
    result = registry[registry["duid"].isin(keep)].copy()
    result["mean_cuf_selected_years"] = result["duid"].map(mean_cuf)
    result = result.sort_values(["station_name", "duid"]).reset_index(drop=True)
    return result


def _to_interval_beginning(index: pd.DatetimeIndex, year: int) -> pd.DatetimeIndex:
    """Shift interval-ending timestamps back when the series includes year-end."""
    if len(index) and index.max() >= pd.Timestamp(f"{year + 1}-01-01"):
        return index - pd.Timedelta(minutes=INTERVAL_MINUTES)
    return index


def _load_5min_series(path: Path, value_col_candidates: list[str], series_name: str,
                       year: int, missing_msg: str) -> pd.Series:
    if not path.exists():
        raise FileNotFoundError(missing_msg)
    df = pd.read_parquet(path)
    lower_map = {str(c).lower(): c for c in df.columns}
    value_col = next((lower_map[c.lower()] for c in value_col_candidates if c.lower() in lower_map), None)
    if value_col is None:
        raise ValueError(
            f"Could not find a value column {value_col_candidates} in {path}. "
            f"Present columns: {list(df.columns)}"
        )

    if isinstance(df.index, pd.DatetimeIndex):
        idx = pd.DatetimeIndex(df.index)
        values = df[value_col]
    else:
        # Value column plus a datetime-like column (first non-value column).
        dt_col = next((c for c in df.columns if c != value_col), df.columns[0])
        idx = pd.DatetimeIndex(pd.to_datetime(df[dt_col]))
        values = df[value_col]

    series = pd.Series(values.values, index=idx, name=series_name)
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="last")]

    new_index = _to_interval_beginning(series.index, year)
    series.index = new_index
    if series.index.tz is not None:
        series.index = series.index.tz_localize(None)
    series.index.name = None
    return series


def load_scada(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> pd.Series:
    duid = duid.strip().upper()
    path = scada_path(duid, year, cache_dir)
    msg = (
        f"No cached SCADA data for DUID '{duid}' at {path}. Run "
        f"`python scripts/fetch_nem_scada_prices.py --year {year}` in a "
        "non-sandboxed environment and copy the output into this cache."
    )
    return _load_5min_series(path, ["scadavalue"], "scadavalue", year, msg)


def availability_path(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    duid = duid.strip().upper()
    return _resolve(Path("availability") / f"{duid}_{year}.parquet", cache_dir)


def has_availability(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> bool:
    """True when an unconstrained-availability (UIGF) cache exists for `duid`."""
    return availability_path(duid, year, cache_dir).exists()


def canonical_5min_index(year: int) -> pd.DatetimeIndex:
    """Canonical AEMO interval-ending timestamps for `year`."""
    return pd.date_range(
        f"{year}-01-01 00:05", periods=expected_intervals(year), freq="5min"
    )


def _read_5min_values(path: Path, column: str, year: int) -> "pd.Series | None":
    """Read a values-only parquet on the canonical grid, or return None."""
    df = pd.read_parquet(path)
    if column not in df.columns or not isinstance(df.index, pd.RangeIndex):
        return None
    idx = canonical_5min_index(year)
    if len(df) != len(idx):
        return None
    return pd.Series(df[column].to_numpy(), index=idx, name=column)


def load_availability(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> pd.Series:
    """Read 5-minute unconstrained availability (MW) from DISPATCHLOAD.

    UIGF is the availability bound, before network and economic curtailment.
    Missing data raises an acquisition hint; SCADA is never substituted.
    """
    duid = duid.strip().upper()
    path = availability_path(duid, year, cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached availability data for DUID '{duid}' at {path}. Run "
            f"`python scripts/fetch_nem_availability.py --year {year}` in a "
            "non-sandboxed environment and copy the output into this cache."
        )
    compact = _read_5min_values(path, "availability", year)
    if compact is not None:
        # Stored on the interval-ENDING grid, same as the raw AEMO series.
        compact.index = _to_interval_beginning(compact.index, year)
        return compact.rename("availability")
    msg = f"Could not read availability data for DUID '{duid}' at {path}."
    return _load_5min_series(path, ["availability"], "availability", year, msg)


def load_regional_price(
    region: str = DEFAULT_REGION, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR
) -> pd.Series:
    region = region.strip().upper()
    path = price_path(region, year, cache_dir)
    msg = (
        f"No cached regional price data for region '{region}' at {path}. Run "
        f"`python scripts/fetch_nem_scada_prices.py --year {year}` in a "
        "non-sandboxed environment and copy the output into this cache."
    )
    return _load_5min_series(path, ["rrp"], "rrp", year, msg)


# ── Derived series ───────────────────────────────────────────────────────────

def capacity_factor_series(scada: pd.Series, capacity_mw: float) -> pd.Series:
    if capacity_mw <= 0:
        raise ValueError(f"capacity_mw must be > 0, got {capacity_mw}")
    return (scada / capacity_mw).clip(0.0, 1.0).rename("cf")


def plant_capacity_mw(
    duid: str, registry: pd.DataFrame | None = None, cache_dir: Path = NEM_CACHE_DIR
) -> float:
    duid = duid.strip().upper()
    if registry is None:
        registry = load_plant_registry(cache_dir)
    row = registry.loc[registry["duid"] == duid]
    if row.empty:
        raise KeyError(f"DUID '{duid}' not found in the NEM plant registry.")
    return float(row.iloc[0]["capacity_registered_mw"])


def plant_name_for_duid(
    duid: str, registry: pd.DataFrame | None = None, cache_dir: Path = NEM_CACHE_DIR
) -> str:
    """Station name for a DUID, falling back to the DUID itself if the plant
    registry is unavailable or the DUID is not found -- callers show this in
    results/exports and must not crash a run over a missing name."""
    if not duid:
        return ""
    duid = duid.strip().upper()
    try:
        if registry is None:
            registry = load_plant_registry(cache_dir)
        row = registry.loc[registry["duid"] == duid]
        if not row.empty:
            return str(row.iloc[0]["station_name"])
    except (FileNotFoundError, KeyError):
        pass
    return duid


def capacity_factor_for_duid(
    duid: str,
    year: int = DEFAULT_YEAR,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: pd.DataFrame | None = None,
) -> pd.Series:
    """Native 5-min CF for on-screen inspection.

    Goes through `_generation_series` so the on-screen CF is the same series the
    plant is modelled with; the shipped cache carries UIGF availability only, so
    reading SCADA directly here raised FileNotFoundError for every plant.
    """
    capacity_mw = plant_capacity_mw(duid, registry=registry, cache_dir=cache_dir)
    generation = _generation_series(duid, year, cache_dir)
    return capacity_factor_series(generation, capacity_mw)


def to_hourly(series: pd.Series, year: int) -> pd.Series:
    """Return hourly means on the canonical year grid, filling edge gaps."""
    return to_resolution(series, year, 60)


def _validate_resolution(resolution_minutes: int) -> None:
    if resolution_minutes <= 0 or resolution_minutes % INTERVAL_MINUTES != 0:
        raise ValueError(
            f"resolution_minutes must be a positive multiple of {INTERVAL_MINUTES}, "
            f"got {resolution_minutes}"
        )
    if 1440 % resolution_minutes != 0:
        raise ValueError(
            f"resolution_minutes must divide evenly into 1440, got {resolution_minutes}"
        )


def _resolution_periods(year: int, resolution_minutes: int) -> int:
    return expected_intervals(year) // (resolution_minutes // INTERVAL_MINUTES)


def to_resolution(series: pd.Series, year: int, resolution_minutes: int) -> pd.Series:
    """Return block means on the canonical yearly grid, filling edge gaps."""
    _validate_resolution(resolution_minutes)
    freq = f"{resolution_minutes}min"
    resampled = series.resample(freq).mean()
    canonical_index = pd.date_range(
        start=f"{year}-01-01", periods=_resolution_periods(year, resolution_minutes), freq=freq
    )
    resampled = resampled.reindex(canonical_index)
    resampled = resampled.ffill().bfill()
    resampled.name = series.name
    return resampled


# ── Whole-year heuristic ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class WholeYearCheck:
    duid: str
    year: int
    n_intervals: int
    expected_intervals: int
    coverage: float
    first_ts: "pd.Timestamp | None"
    last_ts: "pd.Timestamp | None"
    months_present: int
    weak_months: tuple
    coverage_ok: bool
    span_ok: bool
    monthly_output_ok: bool
    reject_reasons: tuple

    @property
    def passed(self) -> bool:
        return self.coverage_ok and self.span_ok and self.monthly_output_ok


_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def whole_year_check(
    scada: pd.Series, capacity_mw: float, year: int, duid: str = ""
) -> WholeYearCheck:
    scada = scada[scada.index.year == year]
    non_null = scada.dropna()
    n_intervals = int(len(non_null))
    exp_intervals = expected_intervals(year)
    coverage = n_intervals / exp_intervals if exp_intervals else 0.0
    coverage_ok = coverage >= WHOLE_YEAR_MIN_COVERAGE

    first_ts = non_null.index.min() if n_intervals else None
    last_ts = non_null.index.max() if n_intervals else None

    if first_ts is not None and last_ts is not None:
        span_ok = (
            first_ts <= pd.Timestamp(year, *WHOLE_YEAR_FIRST_TS_LATEST, 23, 55)
            and last_ts >= pd.Timestamp(year, *WHOLE_YEAR_LAST_TS_EARLIEST)
        )
    else:
        span_ok = False

    if n_intervals:
        monthly_max = non_null.groupby(non_null.index.month).max()
    else:
        monthly_max = pd.Series(dtype=float)

    cutoff = WHOLE_YEAR_MONTHLY_MAX_FRACTION * capacity_mw
    weak_months_t = tuple(
        month for month in range(1, 13)
        if month not in monthly_max.index or monthly_max.loc[month] < cutoff
    )
    monthly_output_ok = len(weak_months_t) == 0

    reasons: list[str] = []
    if not coverage_ok:
        reasons.append(
            f"only {coverage:.1%} of 5-min intervals present (need >=95%)"
        )
    if not span_ok:
        if first_ts is None or last_ts is None:
            reasons.append("no data present")
        else:
            if first_ts > pd.Timestamp(year, *WHOLE_YEAR_FIRST_TS_LATEST, 23, 55):
                reasons.append(f"record starts {first_ts.date()} (need on/before 15 Jan)")
            if last_ts < pd.Timestamp(year, *WHOLE_YEAR_LAST_TS_EARLIEST):
                reasons.append(f"record ends {last_ts.date()} (need on/after 15 Dec)")
    if not monthly_output_ok:
        month_names = ", ".join(_MONTH_NAMES[m - 1] for m in weak_months_t)
        reasons.append(f"no output >=5% of nameplate in months: {month_names}")

    return WholeYearCheck(
        duid=duid,
        year=year,
        n_intervals=n_intervals,
        expected_intervals=exp_intervals,
        coverage=coverage,
        first_ts=first_ts,
        last_ts=last_ts,
        months_present=int(monthly_max.shape[0]),
        weak_months=weak_months_t,
        coverage_ok=coverage_ok,
        span_ok=span_ok,
        monthly_output_ok=monthly_output_ok,
        reject_reasons=tuple(reasons),
    )


def commissioning_ramp_check(
    series: pd.Series, year: int
) -> "tuple[bool, float | None]":
    """Compare the best early-month peak with the annual peak; return status and ratio."""
    s = series[series.index.year == year].dropna()
    if s.empty:
        return False, None
    annual_peak = float(s.max())
    if annual_peak <= 0:
        return False, None
    monthly_max = s.groupby(s.index.month).max()
    early = monthly_max.reindex(COMMISSIONING_EARLY_MONTHS).dropna()
    if early.empty:
        return False, 0.0
    ratio = float(early.max()) / annual_peak
    return ratio >= COMMISSIONING_MIN_PEAK_FRACTION, ratio


def _first_sustained_output_date(
    cf: "pd.Series", min_intervals: int = 6, threshold: float = 0.01
) -> "str | None":
    """First timestamp where output stays above ``threshold`` × nameplate for
    ``min_intervals`` consecutive 5-min intervals.

    Returns a YYYY-MM-DD string, or None when the series never meets the bar.
    This is the SCADA-derived fallback for the registry's ``first_power_date``:
    it reflects when the plant first produced meaningfully, not just any
    non-zero interval (which would catch a single noisy reading).
    """
    above = cf > threshold
    count = 0
    for idx, flag in above.items():
        count = count + 1 if flag else 0
        if count >= min_intervals:
            return pd.Timestamp(idx).strftime("%Y-%m-%d")
    return None


@dataclass(frozen=True)
class ScadaSummary:
    duid: str
    year: int
    status: str  # "ready" | "commissioning" | "no_scada" | "incomplete" | "unreadable"
    check: "WholeYearCheck | None"
    mean_cf: "float | None"
    reject_reasons: str
    cuf: "float | None" = None
    first_output_date: "str | None" = None
    # Commissioning-ramp detection: False when the plant was still being built
    # out during `year`, so its capacity factor understates the finished asset.
    fully_operational: bool = True
    early_peak_ratio: "float | None" = None


def scada_summary(
    duid: str, capacity_mw: float, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR
) -> ScadaSummary:
    """Eligibility summary for a DUID, assessed on the series it is modelled
    with (UIGF availability; SCADA only if that is what is cached)."""
    duid = duid.strip().upper()
    path = availability_path(duid, year, cache_dir)
    if not path.exists():
        path = scada_path(duid, year, cache_dir)
    if not path.exists():
        return ScadaSummary(
            duid=duid, year=year, status="no_scada", check=None, mean_cf=None,
            reject_reasons="",
        )
    try:
        scada = _generation_series(duid, year, cache_dir)
        cf = capacity_factor_series(scada, capacity_mw)
        check = whole_year_check(scada, capacity_mw, year, duid=duid)
        mean_cf = float(cf.mean())
        # CUF (capacity utilisation factor): energy ÷ (nameplate × hours in
        # year) -- the stricter definition than the mean CF. scada is the raw
        # 5-min MW series; each interval is 5 minutes = 5/60 h.
        energy_mwh = float(scada.sum() * (INTERVAL_MINUTES / 60.0))
        cuf = energy_mwh / (float(capacity_mw) * expected_hours(year)) if capacity_mw > 0 else None
        # The ramp check runs on the same series the plant is modelled with
        # (UIGF), so a heavily curtailed plant is not mistaken for one still
        # being commissioned.
        fully_operational, early_peak_ratio = commissioning_ramp_check(scada, year)

        reasons_list = list(check.reject_reasons)
        if not fully_operational:
            reasons_list.append(
                f"still commissioning in {year}: early-year peak is only "
                f"{early_peak_ratio:.0%} of its own annual peak "
                f"(need >={COMMISSIONING_MIN_PEAK_FRACTION:.0%})"
                if early_peak_ratio is not None else
                f"still commissioning in {year}"
            )
        status = "ready" if (check.passed and fully_operational) else (
            "commissioning" if check.passed else "incomplete"
        )
        reasons = "; ".join(reasons_list)
        first_output_date = _first_sustained_output_date(cf, min_intervals=6)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, surfaced to the UI
        return ScadaSummary(
            duid=duid, year=year, status="unreadable", check=None, mean_cf=None,
            reject_reasons=str(exc),
        )
    return ScadaSummary(
        duid=duid, year=year, status=status, check=check, mean_cf=mean_cf,
        reject_reasons=reasons, cuf=cuf, first_output_date=first_output_date,
        fully_operational=fully_operational, early_peak_ratio=early_peak_ratio,
    )


def check_selected_duids_ready(
    pv_duid: str = "",
    wind_duid: str = "",
    year: int = DEFAULT_YEAR,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> tuple[bool, tuple[str, ...]]:
    """Check that each non-empty selected DUID is INDIVIDUALLY simulation-ready.

    Unlike `cache_status()["n_simulation_ready"] > 0` (which only tells you
    *some* plant somewhere in the whole cache is ready), this checks the
    specific DUIDs a scenario actually references. Returns
    `(all_ready, problems)` where `problems` is a tuple of human-readable
    reasons (empty iff `all_ready` is True). A DUID not present in the
    registry at all is treated as not ready.
    """
    duids = [d for d in (pv_duid, wind_duid) if d]
    if not duids:
        return True, ()

    if registry is None:
        try:
            registry = load_plant_registry(cache_dir)
        except FileNotFoundError:
            registry = None

    problems: list[str] = []
    for duid in duids:
        duid_norm = duid.strip().upper()
        row = registry.loc[registry["duid"] == duid_norm] if registry is not None else None
        if row is None or row.empty:
            problems.append(f"DUID '{duid}' not found in the NEM plant registry.")
            continue
        capacity_mw = float(row.iloc[0]["capacity_registered_mw"])
        summary = scada_summary(duid_norm, capacity_mw, year, cache_dir)
        if summary.status != "ready":
            reason = summary.reject_reasons or summary.status
            problems.append(f"DUID '{duid}' is not simulation-ready ({reason}).")

    return (len(problems) == 0, tuple(problems))


def nem_generation_ready(
    data_source: str,
    pv_duid: str = "",
    wind_duid: str = "",
    year: int = DEFAULT_YEAR,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> tuple[bool, tuple[str, ...]]:
    """Whether a NEM-backed scenario's OWN selected DUIDs are simulation-ready.

    Applies identically to 'nem_map' AND 'nem_default': naively checking
    `cache_status()["n_simulation_ready"] > 0` only tells you *some* plant
    somewhere in the whole cache is ready, not that THIS scenario's DUIDs are
    -- and for both data sources, empty pv/wind DUIDs mean `get_cf_dicts`
    silently returns all-zero capacity-factor series (zero renewable
    generation, no error). So an empty selection is treated as not-ready here,
    with a human-readable reason, rather than falling back to "is anything at
    all cached". Non-NEM data sources always report ready (nothing to check).
    """
    if data_source not in ("nem_map", "nem_default"):
        return True, ()
    if not (pv_duid or wind_duid):
        return False, (
            "No NEM plant selected -- pick a wind and/or solar plant on the Get Data "
            "tab. Without one, the simulation would run with zero renewable generation.",
        )
    return check_selected_duids_ready(pv_duid, wind_duid, year, cache_dir, registry)


# ── Two-tier eligibility ─────────────────────────────────────────────────────

def _load_eligibility_cache(year: int, cache_dir: Path) -> "pd.DataFrame | None":
    path = eligibility_cache_path(year, cache_dir)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        # Corrupt/unreadable cache degrades to "recompute everything live",
        # same as a missing cache -- never a hard failure.
        return None


def list_eligible_plants(
    min_capacity_mw: float = MIN_CAPACITY_MW,
    year: int = DEFAULT_YEAR,
    fuel_techs=DEFAULT_FUEL_TECHS,
    regions=None,
    statuses=(OPERATING_STATUS,),
    require_simulation_ready: bool = False,
    check_whole_year: bool = True,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    if registry is None:
        registry = load_plant_registry(cache_dir)

    df = registry[
        (registry["capacity_registered_mw"] > min_capacity_mw)
        & (registry["fuel_tech"].isin(fuel_techs))
        & (registry["status"].isin(statuses))
    ].copy()
    if regions:
        regions_upper = {r.strip().upper() for r in regions}
        df = df[df["region"].isin(regions_upper)]

    if check_whole_year:
        eligibility_cols = [
            "has_scada", "simulation_ready", "data_status", "reject_reasons",
            "coverage", "mean_cf", "cuf", "first_output_date",
        ]
        cached = _load_eligibility_cache(year, cache_dir)
        cached_duids = set(cached["duid"]) if cached is not None else set()
        # Plants missing from the precomputed cache (new since it was last
        # built, or the cache is absent entirely) still get computed live --
        # slower for those rows only, correct either way.
        to_compute = df[~df["duid"].isin(cached_duids)] if cached is not None else df

        computed_rows = []
        for _, row in to_compute.iterrows():
            duid = row["duid"]
            summary = scada_summary(duid, float(row["capacity_registered_mw"]), year, cache_dir)
            computed_rows.append({
                "duid": duid,
                "has_scada": availability_path(duid, year, cache_dir).exists()
                or scada_path(duid, year, cache_dir).exists(),
                "simulation_ready": summary.status == "ready",
                "data_status": summary.status,
                "reject_reasons": summary.reject_reasons,
                "coverage": summary.check.coverage if summary.check is not None else np.nan,
                "mean_cf": summary.mean_cf,
                "cuf": summary.cuf,
                "first_output_date": summary.first_output_date,
            })
        computed = pd.DataFrame(computed_rows, columns=["duid", *eligibility_cols])
        combined = (
            pd.concat([cached[["duid"] + eligibility_cols], computed], ignore_index=True)
            if cached is not None else computed
        )
        df = df.merge(combined, on="duid", how="left")
        for col in ("has_scada", "simulation_ready"):
            df[col] = df[col].infer_objects(copy=False).fillna(False).astype(bool)
        df["data_status"] = df["data_status"].fillna("unchecked")
        df["reject_reasons"] = df["reject_reasons"].fillna("")
    else:
        df["has_scada"] = False
        df["simulation_ready"] = False
        df["data_status"] = "unchecked"
        df["reject_reasons"] = ""
        df["coverage"] = np.nan
        df["mean_cf"] = np.nan
        df["cuf"] = np.nan
        df["first_output_date"] = None

    if require_simulation_ready:
        df = df[df["simulation_ready"]]

    df = df.sort_values(["station_name", "duid"]).reset_index(drop=True)
    return df


def list_simulation_ready_plants(**kwargs) -> pd.DataFrame:
    return list_eligible_plants(require_simulation_ready=True, **kwargs)


def list_cached_scada_duids(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> list:
    scada_dir = Path(cache_dir) / "scada"
    if not scada_dir.exists():
        return []
    suffix = f"_{year}.parquet"
    return sorted(
        p.name[: -len(suffix)] for p in scada_dir.glob(f"*{suffix}")
    )


def list_cached_price_regions(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> list:
    price_dir = Path(cache_dir) / "price"
    if not price_dir.exists():
        return []
    prefix = "rrp_"
    suffix = f"_{year}.parquet"
    regions = []
    for p in price_dir.glob(f"{prefix}*{suffix}"):
        name = p.name[len(prefix): -len(suffix)]
        regions.append(name)
    return sorted(regions)


def list_cached_price_years(cache_dir: Path = NEM_CACHE_DIR) -> list:
    """Distinct years present in the price cache, across all regions."""
    price_dir = Path(cache_dir) / "price"
    if not price_dir.exists():
        return []
    return sorted({
        int(year) for path in price_dir.glob("rrp_*.parquet")
        if (year := path.stem.split("_")[-1]).isdigit()
    })


def cache_fingerprint(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> tuple:
    """(n_scada_files, n_price_files, max_mtime_ns) -- hashable cache-invalidation token."""
    cache_dir = Path(cache_dir)
    scada_dir = cache_dir / "scada"
    price_dir = cache_dir / "price"
    registry_file = registry_path(cache_dir)

    scada_files = list(scada_dir.glob(f"*_{year}.parquet")) if scada_dir.exists() else []
    price_files = list(price_dir.glob(f"*_{year}.parquet")) if price_dir.exists() else []
    files = scada_files + price_files
    if registry_file.exists():
        files.append(registry_file)

    max_mtime_ns = max((f.stat().st_mtime_ns for f in files), default=0)
    return (len(scada_files), len(price_files), max_mtime_ns)


def cache_status(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> dict:
    registry_present = registry_path(cache_dir).exists()
    n_registry_plants = 0
    n_simulation_ready = 0
    if registry_present:
        try:
            registry = load_plant_registry(cache_dir)
            n_registry_plants = len(registry)
            ready = list_simulation_ready_plants(year=year, cache_dir=cache_dir, registry=registry)
            n_simulation_ready = len(ready)
        except Exception:
            pass

    price_regions_cached = list_cached_price_regions(year, cache_dir)
    missing_price_regions = [r for r in NEM_REGIONS if r not in price_regions_cached]

    return {
        "registry_present": registry_present,
        "n_registry_plants": n_registry_plants,
        "n_scada_cached": len(list_cached_scada_duids(year, cache_dir)),
        "n_simulation_ready": n_simulation_ready,
        "price_regions_cached": price_regions_cached,
        "missing_price_regions": missing_price_regions,
    }


# ── Optimiser-facing adapters ────────────────────────────────────────────────

def _generation_series(duid: str, year: int, cache_dir: Path) -> pd.Series:
    """UIGF availability is the generation bound; never substitute sent-out SCADA."""
    return load_availability(duid, year, cache_dir)


def _cf_dict_for_duid(
    duid: str | None, years, cache_dir: Path, registry: "pd.DataFrame | None",
    resolution_minutes: int = 60,
) -> dict:
    result: dict = {}
    for year in years:
        if not duid:
            periods = _resolution_periods(year, resolution_minutes)
            freq = f"{resolution_minutes}min"
            idx = pd.date_range(start=f"{year}-01-01", periods=periods, freq=freq)
            result[year] = pd.Series(np.zeros(periods), index=idx, name="cf")
            continue
        capacity_mw = plant_capacity_mw(duid, registry=registry, cache_dir=cache_dir)
        series = _generation_series(duid, year, cache_dir)
        cf_5min = capacity_factor_series(series, capacity_mw)
        result[year] = to_resolution(cf_5min, year, resolution_minutes)
    return result


def get_cf_dicts(
    pv_duid, wind_duid, years=(DEFAULT_YEAR,), cache_dir=NEM_CACHE_DIR, registry=None,
) -> tuple:
    if registry is None and (pv_duid or wind_duid):
        registry = load_plant_registry(cache_dir)
    pv_cf_by_year = _cf_dict_for_duid(pv_duid, years, cache_dir, registry)
    wind_cf_by_year = _cf_dict_for_duid(wind_duid, years, cache_dir, registry)
    return pv_cf_by_year, wind_cf_by_year


def get_price_dict(region=DEFAULT_REGION, years=(DEFAULT_YEAR,), cache_dir=NEM_CACHE_DIR) -> dict:
    result: dict = {}
    for year in years:
        prices_5min = load_regional_price(region, year, cache_dir)
        result[year] = to_hourly(prices_5min, year)
    return result


def get_cf_dicts_multi(
    pv_duid, wind_duid, years, resolution_minutes=60,
    cache_dir=NEM_CACHE_DIR, registry=None,
) -> tuple:
    """Like `get_cf_dicts` but across multiple years at a non-hourly
    resolution: returns `(pv_cf_by_year, wind_cf_by_year)`, each a dict keyed
    by year of block-mean CF series on the canonical `resolution_minutes` grid.
    """
    _validate_resolution(resolution_minutes)
    if registry is None and (pv_duid or wind_duid):
        registry = load_plant_registry(cache_dir)
    pv_cf_by_year = _cf_dict_for_duid(
        pv_duid, years, cache_dir, registry, resolution_minutes
    )
    wind_cf_by_year = _cf_dict_for_duid(
        wind_duid, years, cache_dir, registry, resolution_minutes
    )
    return pv_cf_by_year, wind_cf_by_year


def get_price_dict_multi(
    region, years, resolution_minutes=60, cache_dir=NEM_CACHE_DIR
) -> dict:
    """Like `get_price_dict` but across multiple years at a non-hourly
    resolution. Raises a FileNotFoundError naming the missing region AND year
    when no price cache exists for a requested (region, year) pair.
    """
    _validate_resolution(resolution_minutes)
    result: dict = {}
    for year in years:
        path = price_path(region, year, cache_dir)
        if not path.exists():
            raise FileNotFoundError(
                f"No cached price data for region '{region}' in {year} at {path}. Run "
                f"`python scripts/fetch_nem_scada_prices.py --year {year}` in a "
                "non-sandboxed environment and copy the output into this cache."
            )
        prices_5min = load_regional_price(region, year, cache_dir)
        result[year] = to_resolution(prices_5min, year, resolution_minutes)
    return result


def reference_month_ts(scenario, month: int = 3, cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    """Build a one-month reference DataFrame (`ts_PVGen`, `ts_WindGen`,
    `ts_MktPrice` columns, hourly index) for the single-day reference path,
    from the scenario's NEM DUIDs/region/year.

    Duck-typed scenario access only (getattr), consistent with
    `get_timeseries_dicts` -- do not import `ppa.scenario` here.
    """
    pv_duid = getattr(scenario, "nem_pv_duid", "")
    wind_duid = getattr(scenario, "nem_wind_duid", "")
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION)
    year = getattr(scenario, "nem_year", DEFAULT_YEAR)

    pv_by_year, wind_by_year = get_cf_dicts(
        pv_duid, wind_duid, years=(year,), cache_dir=cache_dir
    )
    prices_by_year = get_price_dict(region, years=(year,), cache_dir=cache_dir)

    pv_hourly = pv_by_year[year]
    wind_hourly = wind_by_year[year]
    price_hourly = prices_by_year[year]

    month_mask = pv_hourly.index.month == month
    if not month_mask.any():
        # Requested month has no rows in this year's canonical index (should
        # not happen for 1-12, defensive only) -- fall back to the first month
        # actually present.
        month = int(pv_hourly.index.month.min())
        month_mask = pv_hourly.index.month == month

    idx = pv_hourly.index[month_mask]
    ts = pd.DataFrame(
        {
            "ts_PVGen": pv_hourly.loc[idx].to_numpy(),
            "ts_WindGen": wind_hourly.loc[idx].to_numpy(),
            "ts_MktPrice": price_hourly.loc[idx].to_numpy(),
        },
        index=idx,
    )
    ts.index.name = "snapshot"
    return ts


def period_ts(
    scenario,
    start,
    end,
    resolution_minutes: int = 60,
    cache_dir: Path = NEM_CACHE_DIR,
) -> pd.DataFrame:
    """Build a `ts_PVGen`/`ts_WindGen`/`ts_MktPrice` DataFrame for an arbitrary
    `[start, end)` window at the requested resolution, from the scenario's NEM
    DUIDs/region/year -- the general form of `reference_month_ts` (which stays
    untouched, fixed at hourly/one-month, since it's covered by existing tests
    and callers).

    `resolution_minutes` must be a multiple of the native SCADA/price interval
    (`INTERVAL_MINUTES` = 5): 5 reads the cache at native resolution, 30/60
    block-average it. Values in `[start, end)` outside a series' own cached
    span (e.g. a plant commissioned mid-year, or a window outside `year`)
    produce gaps; small gaps are ffill/bfill'd consistent with `to_hourly`,
    but a window with no cached data at all for a selected DUID/region raises
    `RuntimeError` rather than silently returning an all-zero/NaN series.

    Duck-typed scenario access only (getattr), consistent with
    `get_timeseries_dicts` -- do not import `ppa.scenario` here.
    """
    if resolution_minutes % INTERVAL_MINUTES != 0:
        raise ValueError(
            f"resolution_minutes must be a multiple of {INTERVAL_MINUTES}, got {resolution_minutes}"
        )

    pv_duid = getattr(scenario, "nem_pv_duid", "")
    wind_duid = getattr(scenario, "nem_wind_duid", "")
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION)
    year = getattr(scenario, "nem_year", DEFAULT_YEAR)

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if end_ts <= start_ts:
        raise ValueError(f"end ({end_ts}) must be after start ({start_ts})")

    freq = f"{resolution_minutes}min"
    canonical_idx = pd.date_range(start_ts, end_ts, freq=freq, inclusive="left")
    if len(canonical_idx) == 0:
        raise ValueError(f"Window [{start_ts}, {end_ts}) at {resolution_minutes}min resolution has no snapshots.")

    registry = load_plant_registry(cache_dir) if (pv_duid or wind_duid) else None

    def _resampled(series: pd.Series, label: str) -> pd.Series:
        windowed = series[(series.index >= start_ts) & (series.index < end_ts)]
        resampled = windowed.resample(freq).mean().reindex(canonical_idx)
        resampled = resampled.ffill().bfill()
        if resampled.isna().any():
            raise RuntimeError(
                f"No cached {label} data at all within [{start_ts}, {end_ts}) -- "
                "pick a different period, plant, or region."
            )
        return resampled

    if pv_duid:
        capacity_mw = plant_capacity_mw(pv_duid, registry=registry, cache_dir=cache_dir)
        pv_native = capacity_factor_series(
            _generation_series(pv_duid, year, cache_dir), capacity_mw
        )
        pv_r = _resampled(pv_native, f"PV generation for {pv_duid}")
    else:
        pv_r = pd.Series(0.0, index=canonical_idx)

    if wind_duid:
        capacity_mw = plant_capacity_mw(wind_duid, registry=registry, cache_dir=cache_dir)
        wind_native = capacity_factor_series(
            _generation_series(wind_duid, year, cache_dir), capacity_mw
        )
        wind_r = _resampled(wind_native, f"Wind generation for {wind_duid}")
    else:
        wind_r = pd.Series(0.0, index=canonical_idx)

    price_native = load_regional_price(region, year, cache_dir)
    price_r = _resampled(price_native, f"price for region {region}")

    ts = pd.DataFrame(
        {
            "ts_PVGen": pv_r.to_numpy(),
            "ts_WindGen": wind_r.to_numpy(),
            "ts_MktPrice": price_r.to_numpy(),
        },
        index=canonical_idx,
    )
    ts.index.name = "snapshot"
    return ts


def get_timeseries_dicts(scenario, cache_dir=NEM_CACHE_DIR) -> tuple:
    """Convenience: reads scenario.nem_pv_duid, scenario.nem_wind_duid,
    scenario.nem_price_region, scenario.nem_years (falling back to
    scenario.nem_year), and scenario.nem_resolution_minutes (default 60) --
    duck-typed attribute access only, do NOT import ppa.scenario here -- and
    returns (pv_by_year, wind_by_year, prices_by_year).
    """
    pv_duid = getattr(scenario, "nem_pv_duid", "")
    wind_duid = getattr(scenario, "nem_wind_duid", "")
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION)
    single_year = getattr(scenario, "nem_year", DEFAULT_YEAR)
    years = tuple(getattr(scenario, "nem_years", (single_year,)))
    resolution_minutes = int(getattr(scenario, "nem_resolution_minutes", 60))

    pv_by_year, wind_by_year = get_cf_dicts_multi(
        pv_duid, wind_duid, years, resolution_minutes=resolution_minutes,
        cache_dir=cache_dir,
    )
    prices_by_year = get_price_dict_multi(
        region, years, resolution_minutes=resolution_minutes, cache_dir=cache_dir
    )
    return pv_by_year, wind_by_year, prices_by_year


def load_illustration_ts(
    region: str = DEFAULT_REGION,
    year: int = DEFAULT_YEAR,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> "pd.DataFrame | None":
    """Assemble a representative NEM hourly timeseries for the intro/help charts.

    Reads the cached regional spot price and the capacity factors of the first
    operating wind and solar plant in the registry. Returns a DataFrame with
    ``ts_MktPrice``, ``ts_WindGen`` and ``ts_PVGen`` on a common hourly index.
    Cache-only (no network); returns ``None`` if the required files are not
    present so callers can degrade gracefully.
    """
    if registry is None:
        registry = load_plant_registry(cache_dir=cache_dir)
    try:
        price = load_regional_price(region, year, cache_dir)
        operating = registry[registry["status"] == OPERATING_STATUS]
        solar_duid = str(operating.loc[operating["fuel_tech"] == "Solar", "duid"].iloc[0])
        wind_duid = str(operating.loc[operating["fuel_tech"] == "Wind", "duid"].iloc[0])
        pv_cf = capacity_factor_for_duid(solar_duid, year, cache_dir, registry)
        wind_cf = capacity_factor_for_duid(wind_duid, year, cache_dir, registry)
    except (FileNotFoundError, KeyError, IndexError, ValueError):
        return None

    price_hourly = to_hourly(price, year)
    idx = price_hourly.index
    return pd.DataFrame(
        {
            "ts_MktPrice": price_hourly.to_numpy(),
            "ts_WindGen": to_hourly(wind_cf, year).to_numpy(),
            "ts_PVGen": to_hourly(pv_cf, year).to_numpy(),
        },
        index=idx,
    )

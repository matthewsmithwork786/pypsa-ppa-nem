"""Cache-only reader/adapter for Australian NEM plant registry, SCADA and price data.

This module NEVER makes a network call. It only reads parquet files that were
produced offline by the acquisition scripts in `scripts/` (run in a separate,
non-sandboxed environment with real access to AEMO/NEMWEB/OpenNEM) and copied
into `data/cache/nem/`. Import surface is intentionally restricted to
`pathlib`/`dataclasses`/`functools`/`calendar`/`pandas`/`numpy` so this module
stays importable in a bare pytest process with no `streamlit` installed and is
trivially auditable (no `requests`/`urllib`/`httpx`/`nemosis`/`socket`).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ────────────────────────────────────────────────────────────────

NEM_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "nem"
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


# ── Path helpers ─────────────────────────────────────────────────────────────

def registry_path(cache_dir: Path = NEM_CACHE_DIR) -> Path:
    return Path(cache_dir) / "registry" / REGISTRY_FILENAME


def scada_path(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    duid = duid.strip().upper()
    return Path(cache_dir) / "scada" / f"{duid}_{year}.parquet"


def price_path(region: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    region = region.strip().upper()
    return Path(cache_dir) / "price" / f"rrp_{region}_{year}.parquet"


def expected_intervals(year: int) -> int:
    return (366 if calendar.isleap(year) else 365) * 288


def expected_hours(year: int) -> int:
    return 8784 if calendar.isleap(year) else 8760


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

    df = df.drop_duplicates(subset="duid", keep="first")
    df = df.sort_values(["station_name", "duid"]).reset_index(drop=True)
    return df


def _to_interval_beginning(index: pd.DatetimeIndex, year: int) -> pd.DatetimeIndex:
    """AEMO SETTLEMENTDATE is interval-ENDING. If the index's max falls in year+1
    (i.e. the series still has its trailing interval-ending timestamp), shift the
    whole index back by one 5-min interval so hourly resampling yields exactly
    8760/8784 clean bins instead of 8761/8785.
    """
    if len(index) and index.max() >= pd.Timestamp(f"{year + 1}-01-01"):
        return index - pd.Timedelta(minutes=INTERVAL_MINUTES)
    return index


def _load_5min_series(path: Path, value_col_candidates: list[str], series_name: str,
                       year: int, missing_msg: str) -> pd.Series:
    if not path.exists():
        raise FileNotFoundError(missing_msg)
    df = pd.read_parquet(path)

    # Locate the value column, tolerating case variants.
    lower_map = {c.lower(): c for c in df.columns}
    value_col = None
    for cand in value_col_candidates:
        if cand.lower() in lower_map:
            value_col = lower_map[cand.lower()]
            break
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


def capacity_factor_for_duid(
    duid: str,
    year: int = DEFAULT_YEAR,
    cache_dir: Path = NEM_CACHE_DIR,
    registry: pd.DataFrame | None = None,
) -> pd.Series:
    """Native 5-min CF for on-screen inspection."""
    capacity_mw = plant_capacity_mw(duid, registry=registry, cache_dir=cache_dir)
    scada = load_scada(duid, year, cache_dir)
    return capacity_factor_series(scada, capacity_mw)


def to_hourly(series: pd.Series, year: int) -> pd.Series:
    """5-min series -> hourly mean, reindexed onto a canonical hour-beginning
    DatetimeIndex spanning exactly expected_hours(year) rows, ffill/bfill so no
    NaN reaches downstream code.
    """
    hourly = series.resample("h").mean()
    canonical_index = pd.date_range(
        start=f"{year}-01-01", periods=expected_hours(year), freq="h"
    )
    hourly = hourly.reindex(canonical_index)
    hourly = hourly.ffill().bfill()
    hourly.name = series.name
    return hourly


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

    weak_months: list[int] = []
    for m in range(1, 13):
        if m not in monthly_max.index or monthly_max.loc[m] < WHOLE_YEAR_MONTHLY_MAX_FRACTION * capacity_mw:
            weak_months.append(m)
    weak_months_t = tuple(weak_months)
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


@dataclass(frozen=True)
class ScadaSummary:
    duid: str
    year: int
    status: str  # "ready" | "no_scada" | "incomplete" | "unreadable"
    check: "WholeYearCheck | None"
    mean_cf: "float | None"
    reject_reasons: str


def scada_summary(
    duid: str, capacity_mw: float, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR
) -> ScadaSummary:
    duid = duid.strip().upper()
    path = scada_path(duid, year, cache_dir)
    if not path.exists():
        return ScadaSummary(
            duid=duid, year=year, status="no_scada", check=None, mean_cf=None,
            reject_reasons="",
        )
    try:
        scada = load_scada(duid, year, cache_dir)
        cf = capacity_factor_series(scada, capacity_mw)
        check = whole_year_check(scada, capacity_mw, year, duid=duid)
        status = "ready" if check.passed else "incomplete"
        mean_cf = float(cf.mean())
        reasons = "; ".join(check.reject_reasons)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, surfaced to the UI
        return ScadaSummary(
            duid=duid, year=year, status="unreadable", check=None, mean_cf=None,
            reject_reasons=str(exc),
        )
    return ScadaSummary(
        duid=duid, year=year, status=status, check=check, mean_cf=mean_cf,
        reject_reasons=reasons,
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
            "No NEM plant selected -- pick a wind and/or solar plant on the NEM Plant Map "
            "tab. Without one, the simulation would run with zero renewable generation.",
        )
    return check_selected_duids_ready(pv_duid, wind_duid, year, cache_dir, registry)


# ── Two-tier eligibility ─────────────────────────────────────────────────────

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
        has_scada_list = []
        sim_ready_list = []
        data_status_list = []
        reject_reasons_list = []
        coverage_list = []
        mean_cf_list = []
        for _, row in df.iterrows():
            duid = row["duid"]
            capacity_mw = float(row["capacity_registered_mw"])
            path = scada_path(duid, year, cache_dir)
            has_scada = path.exists()
            summary = scada_summary(duid, capacity_mw, year, cache_dir)
            has_scada_list.append(has_scada)
            sim_ready_list.append(summary.status == "ready")
            data_status_list.append(summary.status)
            reject_reasons_list.append(summary.reject_reasons)
            coverage_list.append(summary.check.coverage if summary.check is not None else np.nan)
            mean_cf_list.append(summary.mean_cf if summary.mean_cf is not None else np.nan)
        df["has_scada"] = has_scada_list
        df["simulation_ready"] = sim_ready_list
        df["data_status"] = data_status_list
        df["reject_reasons"] = reject_reasons_list
        df["coverage"] = coverage_list
        df["mean_cf"] = mean_cf_list
    else:
        df["has_scada"] = False
        df["simulation_ready"] = False
        df["data_status"] = "unchecked"
        df["reject_reasons"] = ""
        df["coverage"] = np.nan
        df["mean_cf"] = np.nan

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
    years = set()
    for p in price_dir.glob("rrp_*.parquet"):
        parts = p.stem.split("_")
        if len(parts) >= 3 and parts[-1].isdigit():
            years.add(int(parts[-1]))
    return sorted(years)


def cache_fingerprint(year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> tuple:
    """(n_scada_files, n_price_files, max_mtime_ns) -- hashable cache-invalidation token."""
    cache_dir = Path(cache_dir)
    scada_dir = cache_dir / "scada"
    price_dir = cache_dir / "price"
    registry_file = registry_path(cache_dir)

    files: list[Path] = []
    if scada_dir.exists():
        files.extend(scada_dir.glob(f"*_{year}.parquet"))
    if price_dir.exists():
        files.extend(price_dir.glob(f"*_{year}.parquet"))
    if registry_file.exists():
        files.append(registry_file)

    n_scada = len(list(scada_dir.glob(f"*_{year}.parquet"))) if scada_dir.exists() else 0
    n_price = len(list(price_dir.glob(f"*_{year}.parquet"))) if price_dir.exists() else 0
    max_mtime_ns = max((f.stat().st_mtime_ns for f in files), default=0)
    return (n_scada, n_price, max_mtime_ns)


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


# ── Optimizer-facing adapters ────────────────────────────────────────────────

def _cf_dict_for_duid(
    duid: str | None, years, cache_dir: Path, registry: "pd.DataFrame | None"
) -> dict:
    result: dict = {}
    for year in years:
        if not duid:
            hours = expected_hours(year)
            idx = pd.date_range(start=f"{year}-01-01", periods=hours, freq="h")
            result[year] = pd.Series(np.zeros(hours), index=idx, name="cf")
            continue
        capacity_mw = plant_capacity_mw(duid, registry=registry, cache_dir=cache_dir)
        scada = load_scada(duid, year, cache_dir)
        cf_5min = capacity_factor_series(scada, capacity_mw)
        result[year] = to_hourly(cf_5min, year)
    return result


def get_cf_dicts(
    pv_duid, wind_duid, years=(DEFAULT_YEAR,), cache_dir=NEM_CACHE_DIR, registry=None
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

    pv_by_year, wind_by_year = get_cf_dicts(pv_duid, wind_duid, years=(year,), cache_dir=cache_dir)
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


def get_timeseries_dicts(scenario, cache_dir=NEM_CACHE_DIR) -> tuple:
    """Convenience: reads scenario.nem_pv_duid, scenario.nem_wind_duid,
    scenario.nem_price_region, scenario.nem_year (duck-typed attribute access
    only -- do NOT import ppa.scenario here) and returns
    (pv_by_year, wind_by_year, prices_by_year).
    """
    pv_duid = getattr(scenario, "nem_pv_duid", "")
    wind_duid = getattr(scenario, "nem_wind_duid", "")
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION)
    year = getattr(scenario, "nem_year", DEFAULT_YEAR)

    pv_by_year, wind_by_year = get_cf_dicts(pv_duid, wind_duid, years=(year,), cache_dir=cache_dir)
    prices_by_year = get_price_dict(region, years=(year,), cache_dir=cache_dir)
    return pv_by_year, wind_by_year, prices_by_year

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

    # first_power_date is optional (older caches don't have it). Normalise to a
    # YYYY-MM-DD string or None when present so downstream tooltips can rely on
    # a single format. It is deliberately NOT in REGISTRY_COLUMNS.
    if "first_power_date" in df.columns:
        df["first_power_date"] = pd.to_datetime(
            df["first_power_date"], errors="coerce", format="mixed"
        ).dt.strftime("%Y-%m-%d")
    else:
        df["first_power_date"] = None

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


def availability_path(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    duid = duid.strip().upper()
    return Path(cache_dir) / "availability" / f"{duid}_{year}.parquet"


def has_availability(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> bool:
    """True when an unconstrained-availability (UIGF) cache exists for `duid`."""
    return availability_path(duid, year, cache_dir).exists()


def load_availability(duid: str, year: int = DEFAULT_YEAR, cache_dir: Path = NEM_CACHE_DIR) -> pd.Series:
    """5-min UNCONSTRAINED availability (MW) from AEMO DISPATCHLOAD.

    For semi-scheduled units this is AEMO's Unconstrained Intermittent
    Generation Forecast: the plant's physically available output, before
    network constraints and before any economic curtailment its own offtake
    contract incentivised. `load_scada` returns what was actually sent out,
    i.e. *after* both.

    Which one to model with matters. Measured over the 2025 cache
    (docs/sizing_experiments.md E8) the fleet gap is wind 27.7% -> 30.7% and
    solar 16.9% -> 20.4%, but it is very unevenly distributed: solar
    curtailment has a median of 21.6% and a maximum of 71.3%, because it
    depends on each plant's own contract and network position. That is exactly
    why a flat uplift factor would be wrong and this series is needed.

    Optional cache: raises FileNotFoundError with acquisition instructions when
    absent, so existing installs without it keep working.
    """
    duid = duid.strip().upper()
    path = availability_path(duid, year, cache_dir)
    msg = (
        f"No cached availability data for DUID '{duid}' at {path}. Run "
        f"`python scripts/fetch_nem_availability.py --year {year}` in a "
        "non-sandboxed environment and copy the output into this cache."
    )
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
    status: str  # "ready" | "no_scada" | "incomplete" | "unreadable"
    check: "WholeYearCheck | None"
    mean_cf: "float | None"
    reject_reasons: str
    cuf: "float | None" = None
    first_output_date: "str | None" = None


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
        # CUF (capacity utilisation factor): energy ÷ (nameplate × hours in
        # year) -- the stricter definition than the mean CF. scada is the raw
        # 5-min MW series; each interval is 5 minutes = 5/60 h.
        energy_mwh = float(scada.sum() * (INTERVAL_MINUTES / 60.0))
        cuf = energy_mwh / (float(capacity_mw) * expected_hours(year)) if capacity_mw > 0 else None
        reasons = "; ".join(check.reject_reasons)
        first_output_date = _first_sustained_output_date(cf, min_intervals=6)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, surfaced to the UI
        return ScadaSummary(
            duid=duid, year=year, status="unreadable", check=None, mean_cf=None,
            reject_reasons=str(exc),
        )
    return ScadaSummary(
        duid=duid, year=year, status=status, check=check, mean_cf=mean_cf,
        reject_reasons=reasons, cuf=cuf, first_output_date=first_output_date,
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
        cuf_list = []
        first_output_date_list = []
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
            cuf_list.append(summary.cuf if summary.cuf is not None else np.nan)
            first_output_date_list.append(summary.first_output_date)
        df["has_scada"] = has_scada_list
        df["simulation_ready"] = sim_ready_list
        df["data_status"] = data_status_list
        df["reject_reasons"] = reject_reasons_list
        df["coverage"] = coverage_list
        df["mean_cf"] = mean_cf_list
        df["cuf"] = cuf_list
        df["first_output_date"] = first_output_date_list
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


# ── Optimiser-facing adapters ────────────────────────────────────────────────

def _cf_dict_for_duid(
    duid: str | None, years, cache_dir: Path, registry: "pd.DataFrame | None",
    unconstrained: bool = True,
) -> dict:
    result: dict = {}
    for year in years:
        if not duid:
            hours = expected_hours(year)
            idx = pd.date_range(start=f"{year}-01-01", periods=hours, freq="h")
            result[year] = pd.Series(np.zeros(hours), index=idx, name="cf")
            continue
        capacity_mw = plant_capacity_mw(duid, registry=registry, cache_dir=cache_dir)
        # UNCONSTRAINED availability (UIGF) is the correct input and the
        # default. The LP treats the CF series as p_max_pu -- an upper bound it
        # then curtails against itself (negative prices, connection limits,
        # satisfied offtake). Feeding it constrained SCADA would bound the new
        # build by *another* plant's network constraints and by whatever
        # economic curtailment that plant's own offtake contract incentivised,
        # and then curtail again on top: a double-count, and one that varies
        # ~0-71% per plant so it cannot be corrected with a flat factor.
        #
        # SCADA remains the fallback for the handful of DUIDs with no UIGF
        # (older wind farms predating semi-scheduling: 5 of 184 in the 2025
        # cache) and for installs without the optional availability cache.
        series = None
        if unconstrained and has_availability(duid, year, cache_dir):
            series = load_availability(duid, year, cache_dir)
        if series is None:
            series = load_scada(duid, year, cache_dir)
        cf_5min = capacity_factor_series(series, capacity_mw)
        result[year] = to_hourly(cf_5min, year)
    return result


def get_cf_dicts(
    pv_duid, wind_duid, years=(DEFAULT_YEAR,), cache_dir=NEM_CACHE_DIR, registry=None,
    unconstrained: bool = True,
) -> tuple:
    if registry is None and (pv_duid or wind_duid):
        registry = load_plant_registry(cache_dir)
    pv_cf_by_year = _cf_dict_for_duid(pv_duid, years, cache_dir, registry, unconstrained)
    wind_cf_by_year = _cf_dict_for_duid(wind_duid, years, cache_dir, registry, unconstrained)
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

    # Duck-typed like every other attribute here, so callers without the field
    # (tests, fake scenarios) keep working.
    unconstrained = bool(getattr(scenario, "use_unconstrained_cf", True))

    pv_by_year, wind_by_year = get_cf_dicts(
        pv_duid, wind_duid, years=(year,), cache_dir=cache_dir, unconstrained=unconstrained
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
        pv_native = capacity_factor_series(load_scada(pv_duid, year, cache_dir), capacity_mw)
        pv_r = _resampled(pv_native, f"PV SCADA for {pv_duid}")
    else:
        pv_r = pd.Series(0.0, index=canonical_idx)

    if wind_duid:
        capacity_mw = plant_capacity_mw(wind_duid, registry=registry, cache_dir=cache_dir)
        wind_native = capacity_factor_series(load_scada(wind_duid, year, cache_dir), capacity_mw)
        wind_r = _resampled(wind_native, f"Wind SCADA for {wind_duid}")
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
    scenario.nem_price_region, scenario.nem_year (duck-typed attribute access
    only -- do NOT import ppa.scenario here) and returns
    (pv_by_year, wind_by_year, prices_by_year).
    """
    pv_duid = getattr(scenario, "nem_pv_duid", "")
    wind_duid = getattr(scenario, "nem_wind_duid", "")
    region = getattr(scenario, "nem_price_region", DEFAULT_REGION)
    year = getattr(scenario, "nem_year", DEFAULT_YEAR)

    # Duck-typed like every other attribute here, so callers without the field
    # (tests, fake scenarios) keep working.
    unconstrained = bool(getattr(scenario, "use_unconstrained_cf", True))

    pv_by_year, wind_by_year = get_cf_dicts(
        pv_duid, wind_duid, years=(year,), cache_dir=cache_dir, unconstrained=unconstrained
    )
    prices_by_year = get_price_dict(region, years=(year,), cache_dir=cache_dir)
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

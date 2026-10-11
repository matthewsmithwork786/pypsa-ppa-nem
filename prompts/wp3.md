### WP3 — `nem_data`: multi-year support and two-directory cache search

**Owns:** `ppa/data/nem_data.py`, `tests/test_nem_data.py`,
`tests/fixtures/nem_fixtures.py`, new `tests/test_nem_data_multiyear.py`.

**Difficulty:** medium. Must not break the network-free import discipline —
`ppa/data/nem_data.py` must never import `requests`/`urllib`/`httpx`/`nemosis`/
`socket`/`streamlit`. `tests/test_nem_data.py` already enforces this; keep it
passing.

Note: `data/cache/nem/registry/plant_years.parquet` (the manifest this WP reads)
does not exist yet in this checkout — another work package is building the
script that produces it, in parallel. That is fine and expected: `load_plant_years`
below must degrade gracefully (return an empty frame with the right columns) when
the file is absent, so write and test against **synthetic fixtures**, not the
real file. Do not wait for it and do not try to build it yourself.

#### 3.1 Runtime cache directory

Add near the existing `NEM_CACHE_DIR`:

```python
import os

# Packaged cache: committed in the repo, read-only in a deployed container.
NEM_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "nem"

# Runtime cache: where ppa.data.remote_cache materialises files fetched from
# Zenodo. Separate because the packaged cache may be read-only and is version
# controlled; we never want downloads landing in a git working tree.
RUNTIME_CACHE_DIR = Path(
    os.environ.get(
        "PPA_RUNTIME_CACHE_DIR",
        Path.home() / ".cache" / "pypsa-ppa-nem" / "nem",
    )
)
```

`os` is a stdlib import and does not violate the discipline test. Confirm by
re-running `tests/test_nem_data.py`.

#### 3.2 Two-directory path resolution

Introduce one helper and route **every** existing `*_path()` function through it:

```python
def _resolve(relative: Path, cache_dir: Path = NEM_CACHE_DIR) -> Path:
    """Packaged cache first, then the runtime cache.

    Returns the packaged path when the file exists there, the runtime path when
    it exists there, and otherwise the packaged path (so error messages keep
    naming the canonical location). `cache_dir` is still honoured explicitly so
    every existing test that passes a temp dir keeps working unchanged.
    """
    packaged = Path(cache_dir) / relative
    if packaged.exists():
        return packaged
    runtime = RUNTIME_CACHE_DIR / relative
    if runtime.exists():
        return runtime
    return packaged
```

Apply to `availability_path`, `scada_path`, `price_path`, `registry_path`,
`eligibility_cache_path`. **Do not change their signatures** — `cache_dir` must
stay the first-choice root so the existing fixture-based tests are unaffected.

#### 3.3 Multi-year eligibility from the manifest

Add:

```python
PLANT_YEARS_FILENAME = "plant_years.parquet"

def plant_years_path(cache_dir: Path = NEM_CACHE_DIR) -> Path: ...

def load_plant_years(cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    """The per-(duid, year) operational manifest built by
    scripts/build_plant_year_manifest.py. Returns an EMPTY DataFrame with the
    right columns when absent, so installs without it degrade to the legacy
    single-year eligibility cache rather than raising (optional caches must
    degrade, not fail)."""

def available_years(cache_dir=NEM_CACHE_DIR) -> list[int]:
    """Sorted distinct years present in the manifest."""

def plants_operational_for_years(
    years: "Sequence[int]", cache_dir=NEM_CACHE_DIR,
    registry: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """Registry rows for plants marked fully_operational in EVERY year in
    `years` (intersection, not union). Adds `mean_cuf_selected_years` (mean of
    annual_cuf over the selected years) for display. Empty `years` -> empty
    frame."""
```

`plants_operational_for_years` is the function the new picker (a later, separate
work package) will call instead of `list_eligible_plants`. Leave
`list_eligible_plants` in place and untouched — it is used by `cache_status()` and
by existing tests.

The manifest schema (columns your synthetic fixtures should produce) is:
`duid, year, station_name, region, fuel_tech, capacity_registered_mw_used,
n_intervals, expected_intervals, coverage, first_ts, last_ts, longest_gap_hours,
longest_zero_run_hours, monthly_peak_ratio_min, monthly_peak_ratio_by_month,
annual_cuf, cuf_vs_plant_median, first_power_date, fully_operational, reject_reasons`.

#### 3.4 Multi-year, multi-resolution series adapters

Generalise the existing adapters. **Add new functions rather than changing
signatures** of `to_hourly` / `get_cf_dicts` / `get_price_dict`, which have
callers and tests:

```python
def to_resolution(series: pd.Series, year: int, resolution_minutes: int) -> pd.Series:
    """5-min series -> block mean at `resolution_minutes`, reindexed onto a
    canonical index spanning exactly the year, ffill/bfill so no NaN escapes.
    `resolution_minutes` must divide into 1440 and be a multiple of 5.
    resolution_minutes == 60 must return EXACTLY what to_hourly() returns —
    assert this in a test."""

def get_cf_dicts_multi(
    pv_duid, wind_duid, years, resolution_minutes=60,
    cache_dir=NEM_CACHE_DIR, registry=None, unconstrained=True,
) -> tuple[dict, dict]: ...

def get_price_dict_multi(region, years, resolution_minutes=60, cache_dir=NEM_CACHE_DIR) -> dict: ...
```

Then rewrite `get_timeseries_dicts(scenario, ...)` to read `scenario.nem_years`
(falling back to `(scenario.nem_year,)` via `getattr`, keeping the duck-typing
discipline) and `scenario.nem_resolution_minutes` (default 60), and delegate to
the `_multi` functions.

**Price data caveat:** the shipped price cache is 2025 only
(`data/cache/nem/price/rrp_<REGION>_2025.parquet`). Selecting a year with no price
file must raise a clear `FileNotFoundError`-derived error, not something cryptic —
that is expected/acceptable behaviour for now (a separate work package handles
fetching multi-year prices); just make sure the error message names the missing
region/year so it's diagnosable.

#### 3.5 Tests

Extend `tests/fixtures/nem_fixtures.py` to build a **three-year** synthetic cache
plus a synthetic `plant_years.parquet` matching the schema above. Add
`tests/test_nem_data_multiyear.py`:

- `to_resolution(s, y, 60)` is elementwise-identical to `to_hourly(s, y)`.
- `to_resolution(..., 5)` returns `expected_intervals(year)` rows; `..., 30`
  returns `2 × expected_hours(year)`.
- `plants_operational_for_years([2023, 2024])` returns the intersection, and
  adding a year in which one plant is non-operational drops that plant.
- `get_cf_dicts_multi` returns a dict keyed by every requested year, each of the
  right length.
- `_resolve` prefers the packaged path, falls back to the runtime path, and
  returns the packaged path when neither exists.
- `load_plant_years` returns an empty-but-correctly-shaped frame when
  `plant_years.parquet` does not exist (this is the real situation in this
  checkout right now — verify it doesn't raise).
- The existing import-discipline test still passes.

**Done when:** the whole existing suite is green *and* the new tests pass.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.

# NEM data acquisition

How NEM data gets into `data/cache/nem/` and how the app decides which calendar
years a plant is a fair representation of the finished asset.

## 1. What is committed today

| Cache | Path | Contents |
|---|---|---|
| Availability (UIGF) | `data/cache/nem/availability/` | `<DUID>_2025.parquet`, 5-min `DISPATCHLOAD.AVAILABILITY` for 179 DUIDs — **one year only (2025)** |
| SCADA | `data/cache/nem/scada/` | 5-min sent-out output per DUID/year |
| Prices | `data/cache/nem/price/` | 5-min RRP per region/year |
| Registry | `data/cache/nem/registry/` | `nem_plant_registry.parquet`, `eligibility_2025.parquet` |

`data/cache/nem/` is committed deliberately: the deployed app cannot run `nemosis`,
so without it there would be no generation data at all (see `docs/DEPLOYMENT.md`).

## 2. The operational-year manifest (`plant_years.parquet`)

**Built by:** `scripts/build_plant_year_manifest.py` (new in this work package).

**What it does:** for every `<DUID>_<year>.parquet` found in
`data/cache/nem/availability/`, it computes structural and utilisation metrics for
that (duid, year) pair and applies a `fully_operational` gate. It denormalises the
plant registry onto each row so the plant picker can read one self-contained table.
The script discovers years from filenames — nothing is hardcoded to 2025 — so it
keeps working as more years are added.

**How the app consumes it:** `ppa/data/nem_data.py` reads it via
`load_plant_years`/`available_years`/`plants_operational_for_years`. It is network
free; callers arrange the data first via `remote_cache.ensure_plant_years`, then read.

**Current status: it is a manual step.** `plant_years.parquet` is **not** in the
committed repo cache yet (only `eligibility_2025.parquet` and
`nem_plant_registry.parquet` are). The script is not wired into any automated
pipeline. To build it:

```bash
PYTHONPATH=. python3 scripts/build_plant_year_manifest.py
# writes data/cache/nem/registry/plant_years.parquet
```

Until it is run (and committed), the UI's Pick Plants tab falls back to `[2025]`
with a caption explaining that only the single shipped year is available.

### 2.1 The gate rule in full

The gate generalises the single-year heuristics in `ppa/data/nem_data.py`
(`whole_year_check` + `commissioning_ramp_check`) to a stricter, symmetric rule for
a multi-year archive. Constants live at the top of the script; a row is
`fully_operational` only if **all six** rules pass:

1. **Coverage** `>= 0.98` of 5-min intervals over the full year
   (`MIN_COVERAGE`).
2. **Span:** data must start on/before `{year}-01-03 23:55` and end on/after
   `{year}-12-29 00:00` (`FIRST_TS_LATEST`/`LAST_TS_EARLIEST`) — an 18-day
   hole that the old 95% / 15 Jan–15 Dec rule admitted is now rejected.
3. **Monthly peak ratio** `>= 0.60` for **all 12 months**
   (`MIN_MONTHLY_PEAK_RATIO`): each month's peak divided by the plant's own annual
   peak. This is the commissioning-ramp discriminator applied to every month, so
   mid-year commissioning, pre-retirement ramp-down and month-long outages are all
   caught symmetrically. Using the plant's *own* annual peak keeps the test robust
   to AC clipping at ~0.80 of nameplate and to heavy curtailment late in the year.
4. **Longest NaN gap** `< 336 h` (`MAX_GAP_HOURS`, 14 days).
5. **Longest zero-output run** `< 336 h` (`MAX_ZERO_RUN_HOURS`, 14 days).
6. **First-power lead time:** when the registry has a `first_power_date`, it must
   predate `1 Jan` of the year by at least 60 days (`MIN_AGE_BEFORE_YEAR_DAYS`).

`annual_cuf`, `cuf_vs_plant_median` and `monthly_peak_ratio_by_month` are recorded
for diagnosis and tuning but are **not** part of the gate. Reject reasons are written
to a `reject_reasons` column so a non-passing plant explains itself.

### 2.2 Known outcome: LKBONNY2 fails the gate

The original design assumed `LKBONNY2` would pass, but validation against the real
2025 cache found it fails the stricter all-12-months rule (rule 3): its **January
2025 monthly peak was genuinely only 54% of its own annual peak**. This was
verified against the raw availability data and is an explained physical/record
outcome, not a bug — a January of very low output for that plant is real. Record it
as expected: the gate is doing its job. Do not "fix" it by loosening the rule or
by special-casing the plant.

### 2.3 What the UI surfaces

- Pick Plants tab: year-range slider ("Historical years to use") driven by
  `available_years()`; a "Capacity-sizing year" selectbox (only when capacity
  optimisation is on) solving against one selected year; plant list filtered by
  `plants_operational_for_years`.
- When no manifest exists, the year controls fall back to `[2025]` with an
  explanatory caption rather than failing.

## 3. Runtime fetch from Zenodo (planned — not yet live)

`ppa/data/remote_cache.py` is the **only** runtime module permitted network access
(`nem_data.py` stays network-free — enforced by `tests/test_nem_data.py`). It
fetches pinned files into `RUNTIME_CACHE_DIR` (default `~/.cache/pypsa-ppa-nem/nem`),
using HTTP range requests (fsspec) so reading a few plants out of a wide per-year
parquet transfers a small fraction of the file, and falling back to a full download
when the server does not support ranges. Callers run `ensure_plant_years` /
`ensure_price_years` **before** asking `nem_data.py` to read anything.

The record is pinned by `ppa/data/zenodo_manifest.json`. **No real Zenodo record
has been published yet** — the manifest holds a placeholder
`record_id` (`PLACEHOLDER_NOT_YET_PUBLISHED`). Publishing is a manual step owned by
Hanan (his own Zenodo account); until he does, any real fetch attempt raises
`RemoteFetchError`, which the UI surfaces as a clean `st.error`, not a crash.

## 4. Not yet built — pick up here

Two pieces of the original plan are **deferred to a future work package**. They are
NOT implemented; do not imply otherwise.

1. **Multi-year extension to `scripts/fetch_nem_availability.py`.** The current
   script fetches a single year (`--year`, plus `--month` for a cheap single-month
   connectivity/schema check). The deferred extension adds:
   - `--year-start` / `--year-end` for a full multi-year pull;
   - checkpoint/resume, so a long job can be restarted without re-downloading
     finished years;
   - disk-safe streaming that deletes each month's raw AEMO archive immediately
     after extraction (today the raw `nemosis_cache/` archives are left on disk —
     a multi-year pull would accumulate many GB).
2. **`scripts/build_zenodo_dataset.py`** — the wide-per-year parquet builder that
   produces the real Zenodo upload (the wide layout that `remote_cache.py`'s
   column-pruned range reads are designed for).

Blocked on: Hanan setting up a Zenodo account, and a machine (this VM or another)
running a genuinely multi-day, network-heavy acquisition job.

## 5. Disk hygiene

`/tmp` on this VM is a small tmpfs — never stage large downloads there (a full-year
`DISPATCHLOAD` pull filled it and killed two jobs). Stage raw acquisition data under
the gitignored `nemosis_cache/` on `/` (the repo's default), and delete raw AEMO
archives once the per-DUID parquets are written. Do not retain large raw
intermediate files.

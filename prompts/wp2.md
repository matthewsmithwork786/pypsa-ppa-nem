### WP2 — Offline acquisition: multi-year UIGF + operational-year determination

**Owns:** `scripts/fetch_nem_availability.py`, new
`scripts/build_plant_year_manifest.py`, new `scripts/build_zenodo_dataset.py`,
`docs/DATA_ACQUISITION.md`.

**Runs offline only.** Nothing here is imported by the app.

**SCOPE NOTE FOR THIS SESSION — read before doing anything:** the full historical
pull (potentially 15-20 years × 12 months of AEMO data) is a multi-day network job
and will be launched separately, deliberately, and supervised by the orchestrator
once your code is reviewed — **do not attempt to run the full multi-year pull
yourself in this session.** Your job in this session is:
1. Write/extend all three scripts per the spec below, correctly implementing the
   disk-safe streaming behaviour in §2.1a.
2. Prove `scripts/build_plant_year_manifest.py` (§2.2) is correct by running it
   against the **already-committed 2025 cache** at `data/cache/nem/availability/`
   — this needs no network access at all.
3. Prove `scripts/fetch_nem_availability.py`'s new streaming/checkpoint behaviour
   works by running it for a **small scale test only**: ONE year, TWO months
   (pick a recent year/months where you expect data to exist), and confirm (a) the
   `.done` sentinel files appear per (year, month), (b) no raw CSV/MMS archive is
   left on disk after each month completes, (c) disk usage never exceeds ~2 GB
   above baseline during the run. Report peak disk usage you observed
   (`df -h /` before/during/after).
4. Do NOT run `scripts/build_zenodo_dataset.py` end-to-end against real multi-year
   data yet (there isn't any) — instead write it and unit-test the round-trip
   logic against synthetic fixtures.
5. Stop and report. The orchestrator will review your code, then decide when to
   launch the real multi-year pull as its own long-running background job.

#### 2.1 Extend `scripts/fetch_nem_availability.py`

- Add `--year-start` / `--year-end` (defaulting to the existing `--year` behaviour when
  only `--year` is given).
- Loop years outermost, months innermost (the existing per-month loop is there because a
  full year of `DISPATCHLOAD` is several GB before filtering).
- **Checkpoint and resume.** Write a `.done` sentinel per (year, month) into the raw
  cache dir; skip completed ones on restart. A 20-year pull will be interrupted.
- Write raw output under the gitignored `nemosis_cache/` on a partition with space, not
  `/tmp` (6 GB tmpfs).
- Log per-year row counts and distinct DUIDs so a partial year is obvious.

##### 2.1a Disk-safe streaming (amended for this machine — 36 GB free on `/`, not the
    ~952 GB the original plan assumed)

This is a **hard requirement**, not a nice-to-have: this VM cannot hold more than
one or two months of raw AEMO archive at a time.

- After each (year, month)'s raw `DISPATCHLOAD` data is pulled and filtered down to
  the compact per-DUID 5-minute values format, **delete the raw CSV/MMS archive for
  that month immediately** (nemosis's own local cache directory for that month too,
  if nemosis keeps one) before moving to the next month. Do not wait until end of
  year.
- Append each month's compact values into the running per-DUID-per-year accumulator
  (e.g. write month slices to a temp per-(duid,year) buffer, or append-then-rewrite
  the parquet — whichever keeps peak disk low; do not hold a full year of raw data
  in memory or on disk simultaneously across all DUIDs).
- Log disk usage (`shutil.disk_usage("/")`) at the start and end of each month's
  processing; abort with a clear error (not a silent hang) if free space drops below
  a configurable floor (default 3 GB).

Expect the useful span to start around 2009–2010 for wind and 2016 for solar — discover
it, do not assume 20 years. Record the real per-plant first/last year with data.

#### 2.2 Operational-year determination — `scripts/build_plant_year_manifest.py`

This is the part Hanan flagged as critical. Produce
`data/cache/nem/registry/plant_years.parquet`, one row per (duid, year), with **all**
of these columns so the rule is auditable and tunable without re-running the pull:

| column | meaning |
|---|---|
| `duid`, `year` | key |
| `station_name`, `region`, `fuel_tech` | from registry, denormalised for the picker |
| `capacity_registered_mw_used` | the capacity the CF was computed against |
| `n_intervals`, `expected_intervals`, `coverage` | data completeness |
| `first_ts`, `last_ts` | span |
| `longest_gap_hours` | longest run of consecutive NaN intervals |
| `longest_zero_run_hours` | longest run of consecutive zero-availability intervals |
| `monthly_peak_ratio_min` | min over the 12 months of (monthly max ÷ annual max) |
| `monthly_peak_ratio_by_month` | 12 floats (list column) — for diagnosis |
| `annual_cuf` | Σ(cf)×(5/60) ÷ hours_in_year |
| `cuf_vs_plant_median` | `annual_cuf` ÷ median of the plant's own passing years |
| `first_power_date` | from registry, when available |
| `fully_operational` | the boolean the app uses |
| `reject_reasons` | joined human-readable string |

**The rule.** `fully_operational = True` iff **all** of:

1. `coverage >= 0.98`
   *(tightened from today's 0.95 — with a 20-year archive we can afford to be strict,
   and 95% permits an 18-day hole.)*
2. `first_ts <= {year}-01-03 23:55` and `last_ts >= {year}-12-29 00:00`
   *(tightened from 15 Jan / 15 Dec.)*
3. `monthly_peak_ratio_min >= 0.60`
   *(this is the generalisation of the existing `commissioning_ramp_check`, which only
   looks at Jan/Feb. Applying it to **all 12 months** makes it symmetric, so it catches
   mid-year commissioning, ramp-down before retirement, and month-long outages — not
   just the ramp-up case. The 0.60 threshold and the normalise-by-own-annual-peak design
   are carried over unchanged, because they are the reason it is robust to AC clipping
   at 0.80 of nameplate and to plants that are heavily curtailed later in the year. See
   `nem_data.COMMISSIONING_MIN_PEAK_FRACTION`.)*
4. `longest_gap_hours < 336` (14 days)
5. `longest_zero_run_hours < 336` (14 days)
6. When `first_power_date` is known: `first_power_date <= {year}-01-01 minus 60 days`

`cuf_vs_plant_median` is recorded but **not** part of the gate — it is a diagnostic for
review, because a year at 0.6× the plant's own median is suspicious even when it passes
every structural test.

**Validation set — the acceptance criterion for this WP.** Run the script against the
existing committed 2025 cache and reproduce known answers:

- `MCINTYR1` (MacIntyre, 923 MW) for 2025 → `fully_operational == False`, with
  `monthly_peak_ratio_min` well below 0.60. *This is the canonical commissioning reject
  and the check exists because of it.*
- Long-established plants with a full 2025 year (e.g. `COLWF01`, `SUNRSF1`, `HALLWF1`,
  `LKBONNY2`) → `fully_operational == True`.
- Cross-check the whole 2025 column against
  `data/cache/nem/registry/eligibility_2025.parquet`'s `simulation_ready`. Report every
  disagreement with its reason. **Disagreements are expected** (the rule is stricter) —
  but each one must be individually explainable in your report, not waved through.

Add `tests/test_plant_year_manifest.py` with synthetic fixtures (extend
`tests/fixtures/nem_fixtures.py`) covering: a clean year; a year with a 20-day gap; a
year commissioning in March; a year ramping down in October; a year with 96% coverage.

#### 2.3 `scripts/build_zenodo_dataset.py`

Reads the per-DUID-per-year availability parquets produced by 2.1, and writes to an
output directory:

- `uigf_cf_5min_<year>.parquet` for each year — rows = `canonical_5min_index(year)`
  positionally (values-only, `RangeIndex`, same convention as today's per-DUID files),
  columns = DUID (sorted alphabetically), values = **capacity factor, float32, clipped
  [0, 1]**, zstd compressed. Include **every** DUID with any data for that year, not
  just the operational ones — the manifest is what gates selection.
- Row group size: **one row group per calendar month** (~8,640 rows). This is what makes
  a partial-year read cheap later; do not use the pyarrow default.
- `plant_years.parquet` (copy of the manifest) and `nem_plant_registry.parquet`.
- `MANIFEST.json` — per file: name, byte size, md5, row count, column count, year, and
  the dataset schema version.
- `README.md` for the eventual Zenodo record: provenance (AEMO MMS
  `DISPATCHLOAD.AVAILABILITY`, `INTERVENTION=0`), the CF definition, the operational-year
  rule from 2.2 stated in full, licence, and citation.

Verification logic to write (test against synthetic fixtures for now — see scope note
above): for N sampled (DUID, year) pairs, re-read the column out of the wide file and
assert it round-trips **exactly** against the source per-DUID parquet
(`np.allclose(..., equal_nan=True)`). Follow the pattern in
`scripts/compact_availability_cache.py`, which already does exactly this.

**Done when:** all three scripts exist and match spec; the manifest validation set (2.2)
passes against the real committed 2025 cache with disagreements explained; the small-scale
disk-safe streaming test (scope note step 3) passes with peak disk usage reported;
`tests/test_plant_year_manifest.py` and any `build_zenodo_dataset.py` unit tests pass;
`docs/DATA_ACQUISITION.md` documents the pipeline end-to-end (even though the full
historical pull hasn't run yet) including the disk-safe streaming design.

---
Do not modify files outside the "Files owned" list above. If you believe you need to,
stop and report why.

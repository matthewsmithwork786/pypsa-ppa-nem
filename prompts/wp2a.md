### WP2a — Operational-year determination manifest (offline, no network needed)

**Owns:** new `scripts/build_plant_year_manifest.py`, new
`tests/test_plant_year_manifest.py`, `tests/fixtures/nem_fixtures.py` (additions only).

**IMPORTANT — do not explore the `nemosis` package, do not make any network calls,
do not probe AEMO URLs.** This work package needs none of that: it operates
entirely on the availability parquet files **already committed** in
`data/cache/nem/availability/` and the registry parquets already in
`data/cache/nem/registry/`. A previous attempt at a broader version of this task
burned its whole session exploring `nemosis` internals and network requests and
never wrote any code — do not repeat that. Read at most 3-4 existing files for
context (`ppa/data/nem_data.py`, `scripts/build_nem_eligibility_cache.py`,
`tests/fixtures/nem_fixtures.py`), then start writing
`scripts/build_plant_year_manifest.py`. Budget your exploration to well under a
quarter of your session; spend the rest writing code, running it, and iterating.

#### The task

Produce `scripts/build_plant_year_manifest.py`, a script that reads the existing
per-DUID availability parquet files (currently only `<DUID>_2025.parquet`, but
write it to work for however many years of `<DUID>_<year>.parquet` files exist in
the directory — don't hardcode 2025) plus `data/cache/nem/registry/nem_plant_registry.parquet`,
and writes `data/cache/nem/registry/plant_years.parquet`, one row per (duid, year),
with **all** of these columns so the rule is auditable and tunable without
re-running any data pull:

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
| `first_power_date` | from registry, when available (registry may not have this column — check, and leave null if absent, do not error) |
| `fully_operational` | the boolean the app uses |
| `reject_reasons` | joined human-readable string |

**The rule.** `fully_operational = True` iff **all** of:

1. `coverage >= 0.98`
   *(tightened from today's 0.95 — with a multi-year archive we can afford to be
   strict, and 95% permits an 18-day hole.)*
2. `first_ts <= {year}-01-03 23:55` and `last_ts >= {year}-12-29 00:00`
   *(tightened from 15 Jan / 15 Dec.)*
3. `monthly_peak_ratio_min >= 0.60`
   *(this is the generalisation of the existing `commissioning_ramp_check` in
   `ppa/data/nem_data.py`, which only looks at Jan/Feb. Applying it to **all 12
   months** makes it symmetric, so it catches mid-year commissioning, ramp-down
   before retirement, and month-long outages — not just the ramp-up case. The 0.60
   threshold and the normalise-by-own-annual-peak design are carried over
   unchanged, because they are the reason it is robust to AC clipping at 0.80 of
   nameplate and to plants that are heavily curtailed later in the year. See
   `nem_data.COMMISSIONING_MIN_PEAK_FRACTION`.)*
4. `longest_gap_hours < 336` (14 days)
5. `longest_zero_run_hours < 336` (14 days)
6. When `first_power_date` is known: `first_power_date <= {year}-01-01 minus 60 days`

`cuf_vs_plant_median` is recorded but **not** part of the gate — it is a diagnostic,
because a year at 0.6× the plant's own median is suspicious even when it passes
every structural test.

**Validation set — the acceptance criterion for this WP.** Run the script against
the existing committed 2025 cache (this is the ONLY data you have — that's fine,
one year is enough to validate the logic) and reproduce known answers:

- `MCINTYR1` (MacIntyre, 923 MW) for 2025 → `fully_operational == False`, with
  `monthly_peak_ratio_min` well below 0.60. This is the canonical commissioning
  reject and the check exists because of it.
- Long-established plants with a full 2025 year (e.g. `COLWF01`, `SUNRSF1`,
  `HALLWF1`, `LKBONNY2`) → `fully_operational == True`.
- Cross-check the whole 2025 column against
  `data/cache/nem/registry/eligibility_2025.parquet`'s `simulation_ready`. Report
  every disagreement with its reason in your final report. Disagreements are
  expected (this rule is stricter) — but each one must be individually
  explainable, not waved through.

Add `tests/test_plant_year_manifest.py` with synthetic fixtures (extend
`tests/fixtures/nem_fixtures.py` if useful) covering: a clean year; a year with a
20-day gap; a year commissioning in March; a year ramping down in October; a year
with 96% coverage (should fail the 98% gate but would have passed the old 95%
one — assert it now fails).

**Done when:** the script runs successfully against the real committed 2025
cache, the validation set above passes with disagreements explained, and
`tests/test_plant_year_manifest.py` passes.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.

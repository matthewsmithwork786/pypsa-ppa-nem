# Implementation plan — multi-year Zenodo plant data + tiered SLA constraints

Target repo: `matthewsmithwork786/pypsa-ppa-nem`
Base branch: `feature/energy-first-results-deploy`
Proposed working branch: `feature/multiyear-plants-and-tiered-sla`

This document is written to be pasted into an **opencode** session as the master plan.
It is split into **work packages (WPs)** that can be farmed out to separate opencode
sub-sessions. Each WP states: the files it owns, the exact change, the tests it must
add, and a hard "done when" gate. WPs that touch the same file are sequenced, not
parallelised — see the dependency graph in §3.

---

## §0. How to drive this with opencode

### 0.1 Environment facts every sub-session must be told

Prepend this block verbatim to every sub-session prompt:

```
Repo: pypsa-ppa-nem. Python binary is `python3` — there is NO `python`.
Tests: MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider
Scripts need PYTHONPATH=. .
Read AGENTS.md before you touch anything. Sections 2, 3 and 5 are hard constraints.
Australian English is enforced by tests/test_spelling_en_au.py — write "optimise",
"analyse", "behaviour", "modelling". Never rename third-party APIs (n.optimize,
scipy.optimize, pandas .normalize()).
ppa/data/nem_data.py and ppa/data/aer_futures.py MUST NOT import requests, urllib,
httpx, nemosis, socket or streamlit. tests/test_nem_data.py and
tests/test_aer_futures.py enforce this. All network access lives in scripts/ or in
the new ppa/data/remote_cache.py (which is explicitly exempted — see WP4).
validate_scenario() returns BLOCKING errors only. Warnings go in ui/scenario_form.py
next to the control they concern.
Do not commit new data blobs. .git is already ~400 MB.
Report back with: files changed, tests added, the exact pytest command you ran, and
its output. Do not claim a test passes without pasting the output.
```

### 0.2 Spawning sub-sessions

```bash
cd /home/hanan/projects/pypsa-ppa-nem && opencode run --auto \
  --model <model> \
  --title "WP<N> <short name>" \
  "$(cat prompts/wp<N>.md)"
```

- `--auto` is required non-interactively.
- Run opencode with the sandbox **disabled** (it writes to `~/.local/share/opencode/log/`).
- Model guidance per WP is in §3. Free `opencode/deepseek-v4-flash-free` is fine for the
  mechanical WPs (W1, W2, W3, W11). Use a stronger paid model for W4, W6, W8, W9 —
  those carry real modelling risk.
- Give each WP its **own git branch** off the working branch and merge them in the order
  of §3. One WP per branch makes the review in §5 tractable.

### 0.3 Prompt files

Create `prompts/wp<N>.md` containing: the §0.1 block, then the WP's section from this
document copied verbatim, then "Do not modify files outside the 'Files owned' list. If
you believe you need to, stop and report why."

---

## §1. What the code currently does (facts a sub-session will otherwise get wrong)

Read this before writing any prompt. These are the load-bearing details.

### 1.1 Data layer

- `ppa/data/nem_data.py` is a **cache-only reader**. It never touches the network.
  Everything is read from `data/cache/nem/{registry,availability,price,hedge}/`.
- Generation data shipped today is **UIGF availability only**, one year (2025), one
  file per DUID: `data/cache/nem/availability/<DUID>_2025.parquet`.
- The availability files use a **compact values-only format**: a `RangeIndex` DataFrame
  with a single `availability` float32 column, positionally aligned to
  `nem_data.canonical_5min_index(year)` (interval-**ending**, 00:05 on 1 Jan through
  00:00 on 1 Jan of the following year). `_read_5min_values()` reads it;
  `_to_interval_beginning()` then shifts to interval-beginning. Gaps are NaN, not filled.
  This format is ~2.8× smaller than a naive timestamped parquet. **Keep it.**
- `data/cache/nem/registry/eligibility_2025.parquet` is a precomputed per-DUID
  eligibility/CUF summary built by `scripts/build_nem_eligibility_cache.py`. Without it
  the plant picker re-scans a full year per plant on cold start.
- Everything downstream consumes **hourly** series. `nem_data.to_hourly()` resamples
  5-min → hourly mean and reindexes onto a canonical 8760/8784-row index.

### 1.2 Eligibility ("is this plant usable for this year")

Two independent checks, both in `nem_data.py`, combined in `scada_summary()`:

- `whole_year_check()` — coverage ≥ 95% of expected 5-min intervals; first timestamp on
  or before 15 Jan and last on or after 15 Dec; every calendar month has at least one
  interval ≥ 5% of nameplate.
- `commissioning_ramp_check()` — best of the **January/February** monthly peaks divided
  by the plant's own **annual** peak must be ≥ 0.60. This is deliberately a *ratio*, not
  an absolute level, because solar clips at ~0.80 of nameplate and curtailed plants dip
  mid-year. MacIntyre (923 MW) is the canonical reject: it ran a 9.8% CF through 2025
  with its monthly peak climbing 0.09 → 0.53.

`status` is `"ready"` only when both pass. The picker only offers `simulation_ready`
plants.

### 1.3 Scenario / solver / sizing

- `ppa/scenario.py::Scenario` is a flat dataclass. `nem_year: int` is referenced at **38
  sites** across `ppa/`, `ui/`, `scripts/` and `tests/`. `nem_pv_duid` / `nem_wind_duid`
  are single DUIDs (one wind + one solar maximum).
- `ppa/solver.py::solve()` adds three custom Linopy constraints on top of PyPSA's model:
  1. `AllowedShortfall_Limit` — `Σ w·Gen_AllowedShortfall ≤ allowed_shortfall_share ×
     Σ w·load`, aggregated over the whole horizon (or per calendar year when
     `optimise_capacity` and >1 year).
  2. `MinDelivery_Limit` — only when `optimise_capacity and enforce_min_delivery`.
     `Σ w·link_p[IPPGen_to_PPAOfftake] ≥ required_delivery_share × Σ w·load`.
  3. `BuyFromMarket_Limit`.
- **Every energy sum must be multiplied by `n.snapshot_weightings["objective"]`.** With
  uniform weights this is invisible; with tsam typical periods it is the difference
  between a 90% constraint landing at 90% and landing at 85.6%. See AGENTS.md §3.
- `Gen_AllowedShortfall` (marginal cost 0.001) and `Gen_Penalty` (marginal cost
  `ppa_price × pen_mult`) both sit on `Bus_PPAOfftake` and **bypass the offtake link**.
  `fulfilled_share` counts only flow on `IPPGen_to_PPAOfftake`. So capping shortfall is
  NOT the same as requiring delivery — penalty energy can fill the gap. This distinction
  drives WP8's design.
- Sizing (`ppa/sizing.py`) defaults to `sizing_method="tsam"`:
  `ppa/sizing_tsam.py::cluster_typical_periods()` clusters the horizon into
  `n_periods` × 168-hour typical weeks, then **rebuilds a synthetic hourly index**
  (`pd.date_range(f"{start_year}-01-01", periods=len(clustered), freq="h")`) and returns
  per-snapshot occurrence weights. **Calendar months and calendar days are destroyed by
  this step.** This is the single most important constraint on WP8.
- `build_network(..., snapshot_weightings=w, intra_period_hours=1.0)`: `objective` and
  `generators` get the occurrence counts, `stores` gets `intra_period_hours` because
  that column is the *dt in the storage energy balance*. Setting `stores` to the
  occurrence count silently sizes storage to zero.
- `ppa/multi_year.py::run_multi_year()` forks a `ProcessPoolExecutor`. Fork + CPython
  refcounting means each worker costs roughly the parent's whole RSS. Free large frames
  before the fork. A silent SIGKILL with no traceback is the OOM killer.

### 1.4 UI

- `streamlit_app.py` — title, page config, six tabs.
- Tab ① Pick Plants = `ui/tabs/nem_map.py` (+ `ui/tabs/custom_data.py` in an expander).
- Tab ② Set Terms = `ui/tabs/case_study.py`, which renders four grey preset cards then
  wraps `ui/scenario_form.py::render_scenario_form()` in a collapsed
  "Customise parameters" expander.
- `ui/state.py::_SCENARIO_FORM_KEYS` is the list of widget keys popped whenever the
  scenario is replaced. **Any new `sf_*` or `nm_*` widget key must be added here**, or
  the widget will silently keep a stale value across scenario changes.
- `tests/test_ui_static_checks.py` catches `cols[i]` where `i >= N` for a preceding
  `cols = st.columns(N)`. It is a linear scan, so it sees rebinding.

---

## §2. Design decisions

### 2.1 Locked decisions (implement these; do not re-litigate)

**D1 — Metadata local, timeseries remote.** The per-(DUID, year) eligibility manifest
ships committed in the repo (small: ~300 plants × ~20 years ≈ 6,000 rows). Only the
5-minute timeseries come from Zenodo. The plant picker therefore renders instantly with
zero network calls, and the network is touched exactly once: when the user clicks
"Use these plants".

**D2 — Zenodo layout: one wide parquet per year.** `uigf_cf_5min_<year>.parquet`, rows =
`canonical_5min_index(year)` positionally (values-only, `RangeIndex`, same convention as
today's per-DUID files), columns = DUID, values = **capacity factor, float32, clipped
[0, 1]**, zstd compressed. ~20 files total.

Rationale: Zenodo advises against records with hundreds of files. A wide-per-year file
plus **parquet column pruning over HTTP range requests** gives the same "download only
the selected plants" behaviour — reading 2 columns from a 40–60 MB year file transfers
only those columns' chunks (~200–400 KB each), not the file. One file per (DUID, year)
would be ~6,000 files; one file per year is 20.

Store CF rather than MW: it is what the model consumes and it is immune to later
registry capacity revisions. The manifest records `capacity_registered_mw_used` per
(DUID, year) so MW is recoverable as `cf × capacity`.

**D3 — `ppa/data/remote_cache.py` is the single runtime network module.** It is the only
runtime module permitted to import `requests`/`fsspec`/`urllib`. `nem_data.py` stays
network-free and its import-discipline test is unchanged. The UI calls
`remote_cache.ensure_plant_years(...)` **before** calling `nem_data`, which then reads
plain local files as it does today. Never add a lazy network hook inside `nem_data`.

**D4 — Downloaded files land in a writable runtime cache, materialised in today's
format.** `remote_cache` writes `<runtime_cache>/availability/<DUID>_<year>.parquet` in
exactly the existing compact values-only format, so every existing reader works
unmodified. `nem_data` gains a two-directory search (packaged repo cache first, then
runtime cache) — see WP3.

**D5 — `Scenario.nem_year` stays, as a derived property.** Add `nem_years: tuple[int, ...]`
as the real field and make `nem_year` a `@property` returning `nem_years[0]`. This keeps
all 38 existing call sites (AER futures, `reference_month_ts`, `cache_status`, config
summary, tests) working without a sweeping rename. Add `capacity_sizing_year: int` and
`nem_resolution_minutes: int` as new fields.

Caveat a sub-session will hit: `Scenario` is a plain `@dataclass` and
`dataclasses.asdict`/`dataclasses.replace` are used in `multi_year.py`, `sizing.py`,
`run_store.py` and `ui/state.py`. A property is not a field, so it is simply absent from
`asdict()` — that is fine and desirable. But **`dataclasses.replace(s, nem_year=...)`
will raise**. Grep for it and convert those sites to `nem_years=(y,)`.

**D6 — Monthly SLA is incompatible with tsam sizing; make that a blocking error.**
`cluster_typical_periods` returns a synthetic index with no calendar-month meaning.
A monthly constraint on it would be silently wrong. When
`optimise_capacity and sla_monthly_enabled and sizing_method == "tsam"`,
`validate_scenario` returns a blocking error directing the user to "Full year hourly".
Daily constraints *are* applied under tsam, per representative 24-hour block, with an
explicit caption stating it is an approximation.

**D7 — Monthly/daily SLA are implemented as free-shortfall caps by default**, mirroring
`AllowedShortfall_Limit`, because that is what "limit the free shortfall to the allowed
month and daily hours" asks for. A *matching* per-period minimum-delivery constraint is
added only when `enforce_min_delivery` is on (sizing mode). Both are specified in WP8.

**D8 — Sub-hourly resolution is guarded, not free.** 60 and 30 minutes are unrestricted.
15 and 5 minutes multiply LP size by 4× and 12×; allow them but hard-cap the run
(WP6.5) and warn in the form.

### 2.2 Open questions — answer these before starting WP2/WP4

Put the answers at the top of the working branch's `docs/PLAN_multiyear_sla.md`.

1. **How far back does the data actually go?** AEMO's semi-scheduled category (and hence
   a meaningful `DISPATCHLOAD.AVAILABILITY`/UIGF for wind and solar) dates from roughly
   2009, and large-scale solar from ~2016. "20 years" will in practice be
   ~2010–2025 for wind and ~2016–2025 for solar, with a per-plant span that varies. WP2
   must discover the true span empirically and record it; do not hard-code 20.
2. **Zenodo record: new record or new version of an existing one?** A DOI is minted per
   version. The runtime manifest pins a specific record ID, so re-uploading creates a new
   ID that must be committed. Confirm you want a versioned record and who owns it.
3. **Do you want more than one wind + one solar plant?** The current `Scenario` has
   exactly `nem_pv_duid` and `nem_wind_duid`. "the selected plants" (plural) may mean a
   portfolio. This plan keeps the 1+1 structure — extending to N plants is a separate,
   larger change (it touches `build_network`'s generator set, `results.py`, the financial
   model and every chart). **Confirm 1+1 is acceptable for this round.**
4. **Storage budget.** ~20 wide-year files at 40–60 MB is ~1 GB on Zenodo (well within
   the 50 GB limit), but the raw nemosis pull for 20 years of `DISPATCHLOAD` is several
   hundred GB of intermediate MMS archives. Confirm disk headroom on the acquisition
   machine (AGENTS.md notes `/` has ~952 GB free; `/tmp` is a 6 GB tmpfs and must not be
   used).

---

## §3. Work packages and dependency graph

```
W1  Title + CUF filter removal + preset-card removal   [mechanical]  ──┐
                                                                       │
W2  Offline: multi-year acquisition + operational flags [offline]  ──┐ │
W3  nem_data: multi-year + two-dir cache search        [core]      ─┼─┤
W4  remote_cache: Zenodo column-pruned fetch           [core]      ─┘ │
W5  Scenario: nem_years / sizing year / resolution     [core]  ────────┤
W6  Resolution plumbing (5/15/30/60 min)               [core]  ────────┤
W7  Pick Plants UI rebuild                             [ui]    ────────┤
W8  SLA constraints in solver + sizing                 [MODEL]  ───────┤
W9  SLA reporting + verification                       [MODEL]  ───────┤
W10 Set Terms UI: SLA box                              [ui]    ────────┤
W11 Docs + AGENTS + deployment notes                   [docs]  ────────┤
W12 Integration pass + full test run                   [review] ───────┘
```

Ordering constraints:

- `W1` is independent — start it immediately, merge first, it de-risks nothing but clears
  the diff.
- `W2` is an offline batch job measured in **days** of wall-clock download. Start it
  first, in the background, before anything else. Everything from `W4` onward depends on
  its output existing.
- `W3` → `W4` → `W7` is a strict chain (each consumes the previous one's API).
- `W5` must land before `W6` and `W7`.
- `W8` → `W9` → `W10` is a strict chain.
- `W8` is independent of `W2`–`W7`. **Run the W8/W9/W10 chain in parallel with the
  W2–W7 chain** — they touch disjoint files except `ppa/scenario.py` and
  `ui/scenario_form.py`. To avoid conflicts: **W5 lands both sets of new Scenario fields
  in one commit** (the multi-year ones and the SLA ones), before either chain proceeds.
- `W12` is last and is done by you, not a sub-agent.

Model guidance: `W1`, `W3`, `W7`, `W10`, `W11` → cheap model. `W2`, `W4`, `W5`, `W6` →
mid. `W8`, `W9` → strongest model available; these are where a wrong answer looks right.

---

## §4. Work packages in detail

---

### WP1 — Title, capacity-factor filter removal, preset-card removal

**Owns:** `streamlit_app.py`, `ui/tabs/nem_map.py`, `ui/tabs/case_study.py`,
`ui/state.py`, `ppa/scenario.py` (deletions only), `tests/test_nem_map_tab.py`.

**Difficulty:** mechanical. Cheap model.

#### 1.1 Title

In `streamlit_app.py`:
- `page_title="PyPSA PPA Explorer"` → `page_title="PyPSA based PPA explorer - Australia"`
- The H1 markdown `# PyPSA-based PPA Explorer` → `# PyPSA based PPA explorer - Australia`

Leave `ui/tabs/welcome.py`'s "PyPSA PPA Toolkit" heading alone unless instructed
otherwise — it is a different string and a different screen.

#### 1.2 Remove the capacity-factor filter

In `ui/tabs/nem_map.py::render()`, delete:
- the entire `cuf_min = {}` / `cuf_filters = st.columns(2)` block and its `for i, tech in
  enumerate(("Wind", "Solar"))` loop;
- the `for tech, floor in cuf_min.items():` filtering loop that follows.

`filtered` then becomes just the region filter:
```python
filtered = plants_df[plants_df["region"].isin(region_filter)] if region_filter else plants_df
```

Remove the widget keys `nm_cuf_min_wind` and `nm_cuf_min_solar` from anywhere they are
referenced. Keep the `cuf` **column** and its use in `_tooltip()` — only the filter goes.

#### 1.3 Remove the preset "grey boxes"

In `ui/tabs/case_study.py`:
- Delete `_render_case_study_card()` entirely.
- Delete the `st.subheader("Predefined case studies")` block and its `for col, cs in
  zip(cols, CASE_STUDIES)` loop.
- **Un-nest the form.** Remove the `with st.expander("Customise parameters",
  expanded=False):` wrapper so `render_scenario_form()` and the Apply/Reset buttons are
  the tab's main content. Re-indent accordingly.
- Update the tab's `st.markdown()` blurb: it currently says "Choose a predefined
  scenario…". Replace with something like "Set the commercial terms of the PPA and the
  portfolio, then click **Apply changes**."
- Keep the "Reset to base defaults" button.

Then run:
```bash
grep -rn "CASE_STUDIES\|CaseStudy\|load_case_study\|active_case_study" --include=*.py .
```
- If the only remaining references are in `ppa/scenario.py` and `ui/state.py`, delete
  `CaseStudy`, `CASE_STUDIES`, `CASE_STUDIES_BY_ID`, `load_case_study` from
  `ppa/scenario.py`, and `ACTIVE_CASE_STUDY_KEY` /
  `get_active_case_study_id` / `set_active_case_study_id` from `ui/state.py`.
- If any test or other module still imports them, **stop and report** rather than
  breaking it.

#### 1.4 Tests

- `tests/test_ui_static_checks.py` must still pass (it will catch bad re-indentation and
  column-index drift).
- Add `tests/test_pick_plants_filters.py::test_no_cuf_filter_widgets` — a source scan
  asserting `"nm_cuf_min"` does not appear in `ui/tabs/nem_map.py`.
- Add `tests/test_ui_static_checks.py::test_app_title` — assert
  `"PyPSA based PPA explorer - Australia"` appears in `streamlit_app.py`.

**Done when:** full suite green; `grep -rn "nm_cuf_min\|_render_case_study_card" ui/`
returns nothing.

---

### WP2 — Offline acquisition: multi-year UIGF + operational-year determination

**Owns:** `scripts/fetch_nem_availability.py`, new
`scripts/build_plant_year_manifest.py`, new `scripts/build_zenodo_dataset.py`,
`docs/DATA_ACQUISITION.md`.

**Difficulty:** medium code, long runtime. Run on the acquisition machine with network
access, sandbox disabled. **Start this first; it gates WP4.**

**Runs offline only.** Nothing here is imported by the app.

#### 2.1 Extend `scripts/fetch_nem_availability.py`

- Add `--year-start` / `--year-end` (defaulting to the existing `--year` behaviour when
  only `--year` is given).
- Loop years outermost, months innermost (the existing per-month loop is there because a
  full year of `DISPATCHLOAD` is several GB before filtering).
- **Checkpoint and resume.** Write a `.done` sentinel per (year, month) into the raw
  cache dir; skip completed ones on restart. A 20-year pull will be interrupted.
- Write raw output under the gitignored `nemosis_cache/` on a partition with space, not
  `/tmp` (6 GB tmpfs — AGENTS.md §1).
- Log per-year row counts and distinct DUIDs so a partial year is obvious.

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
the review in §5, because a year at 0.6× the plant's own median is suspicious even when
it passes every structural test.

**Validation set — the acceptance criterion for this WP.** The script must be run
against the existing committed 2025 cache and reproduce known answers:

- `MCINTYR1` (MacIntyre, 923 MW) for 2025 → `fully_operational == False`, with
  `monthly_peak_ratio_min` well below 0.60. *This is the canonical commissioning reject
  and the check exists because of it.*
- Long-established plants with a full 2025 year (e.g. `COLWF01`, `SUNRSF1`, `HALLWF1`,
  `LKBONNY2`) → `fully_operational == True`.
- Cross-check the whole 2025 column against
  `data/cache/nem/registry/eligibility_2025.parquet`'s `simulation_ready`. Report every
  disagreement with its reason. **Disagreements are expected** (the rule is stricter) —
  but each one must be individually explainable in the WP report, not waved through.

Add `tests/test_plant_year_manifest.py` with synthetic fixtures (extend
`tests/fixtures/nem_fixtures.py`) covering: a clean year; a year with a 20-day gap; a
year commissioning in March; a year ramping down in October; a year with 96% coverage.

#### 2.3 `scripts/build_zenodo_dataset.py`

Reads the per-DUID-per-year availability parquets produced by 2.1, and writes to an
output directory:

- `uigf_cf_5min_<year>.parquet` for each year — see D2 for the exact layout. Columns
  sorted alphabetically by DUID. Include **every** DUID with any data for that year, not
  just the operational ones — the manifest is what gates selection, and shipping the rest
  costs nothing in a column-pruned read.
- Row group size: **one row group per calendar month** (~8,640 rows). This is what makes
  a partial-year read cheap later; do not use the pyarrow default.
- `plant_years.parquet` (copy of the manifest) and `nem_plant_registry.parquet`.
- `MANIFEST.json` — per file: name, byte size, md5, row count, column count, year, and
  the dataset schema version. This is what WP4 pins against.
- `README.md` for the Zenodo record: provenance (AEMO MMS `DISPATCHLOAD.AVAILABILITY`,
  `INTERVENTION=0`), the CF definition, the operational-year rule from 2.2 stated in
  full, licence, and citation.

Verification step, run before upload: for 20 randomly sampled (DUID, year) pairs,
re-read the column out of the wide file and assert it round-trips **exactly** against
the source per-DUID parquet (`np.allclose(..., equal_nan=True)`). Follow the pattern in
`scripts/compact_availability_cache.py`, which already does exactly this.

**Done when:** the dataset directory exists, the round-trip verification passes on all
20 samples, the MacIntyre/known-good validation set gives the expected answers, and
`docs/DATA_ACQUISITION.md` records the real year span discovered, the total dataset
size, and the wall-clock time the pull took.

Upload to Zenodo, publish, and record the **record ID and the per-file download URLs**
in the WP report. WP4 needs them.

---

### WP3 — `nem_data`: multi-year support and two-directory cache search

**Owns:** `ppa/data/nem_data.py`, `tests/test_nem_data.py`,
`tests/fixtures/nem_fixtures.py`.

**Difficulty:** medium. Must not break the network-free import discipline.

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

`os` is a stdlib import and does not violate the discipline test (which forbids
`requests`, `urllib`, `httpx`, `nemosis`, `socket`, `streamlit`). Confirm by re-running
`tests/test_nem_data.py`.

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
`eligibility_cache_path`. **Do not change their signatures** — `cache_dir` must stay the
first-choice root so the existing fixture-based tests are unaffected.

#### 3.3 Multi-year eligibility from the manifest

Add:

```python
PLANT_YEARS_FILENAME = "plant_years.parquet"

def plant_years_path(cache_dir: Path = NEM_CACHE_DIR) -> Path: ...

def load_plant_years(cache_dir: Path = NEM_CACHE_DIR) -> pd.DataFrame:
    """The per-(duid, year) operational manifest built by
    scripts/build_plant_year_manifest.py. Returns an EMPTY DataFrame with the
    right columns when absent, so installs without it degrade to the legacy
    single-year eligibility cache rather than raising (AGENTS.md §2: optional
    caches must degrade, not fail)."""

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

`plants_operational_for_years` is the function the new picker calls instead of
`list_eligible_plants`. Leave `list_eligible_plants` in place and untouched — it is used
by `cache_status()` and by tests.

#### 3.4 Multi-year, multi-resolution series adapters

Generalise the existing adapters. **Add new functions rather than changing signatures**
of `to_hourly` / `get_cf_dicts` / `get_price_dict`, which have callers and tests:

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

Then rewrite `get_timeseries_dicts(scenario, ...)` to read
`scenario.nem_years` (falling back to `(scenario.nem_year,)` via `getattr`, keeping the
duck-typing discipline) and `scenario.nem_resolution_minutes` (default 60), and delegate
to the `_multi` functions.

**Price data caveat:** the shipped price cache is 2025 only
(`data/cache/nem/price/rrp_<REGION>_2025.parquet`). Selecting 2015–2020 will raise
`FileNotFoundError` from `load_regional_price`. Two options — implement **(a)**:

  (a) WP2 also pulls multi-year regional prices via
      `scripts/fetch_nem_scada_prices.py` and ships them in the Zenodo record
      (`rrp_<REGION>_<year>.parquet`, ~20 × 5 small files); `remote_cache` fetches the
      needed region-years alongside the plant data.
  (b) Fall back to the nearest available price year with a loud UI warning.

Add `rrp_<REGION>_<year>.parquet` to WP2's dataset build and WP4's fetch list. Flag this
to Hanan if the price pull is not feasible in the same batch.

#### 3.5 Tests

Extend `tests/fixtures/nem_fixtures.py` to build a **three-year** synthetic cache plus a
`plant_years.parquet`. Add `tests/test_nem_data_multiyear.py`:

- `to_resolution(s, y, 60)` is elementwise-identical to `to_hourly(s, y)`.
- `to_resolution(..., 5)` returns `expected_intervals(year)` rows;
  `..., 30` returns `2 × expected_hours(year)`.
- `plants_operational_for_years([2023, 2024])` returns the intersection, and adding a
  year in which one plant is non-operational drops that plant.
- `get_cf_dicts_multi` returns a dict keyed by every requested year, each of the right
  length.
- `_resolve` prefers the packaged path, falls back to the runtime path, and returns the
  packaged path when neither exists.
- The existing import-discipline test still passes.

**Done when:** the whole existing suite is green *and* the new tests pass.

---

### WP4 — `ppa/data/remote_cache.py`: Zenodo fetch with parquet column pruning

**Owns:** new `ppa/data/remote_cache.py`, new
`ppa/data/zenodo_manifest.json`, `requirements.txt`,
`tests/test_remote_cache.py`, `tests/test_nem_data.py` (import-discipline allowlist).

**Difficulty:** high. Strong model. Depends on WP2's published record and WP3's
`RUNTIME_CACHE_DIR`.

#### 4.1 The pinned manifest

`ppa/data/zenodo_manifest.json`, committed, generated from WP2's `MANIFEST.json`:

```json
{
  "schema_version": 1,
  "record_id": "<ZENODO RECORD ID>",
  "doi": "10.5281/zenodo.<...>",
  "base_url": "https://zenodo.org/records/<RECORD_ID>/files/",
  "files": {
    "uigf_cf_5min_2016.parquet": {"size": 48210934, "md5": "...", "year": 2016},
    "rrp_NSW1_2016.parquet": {"size": 1204221, "md5": "...", "year": 2016, "region": "NSW1"}
  }
}
```

Pinning by record ID (not `latest`) means the app is reproducible and a re-upload cannot
silently change results.

#### 4.2 Module API

```python
"""Fetches NEM timeseries from the pinned Zenodo record into the runtime cache.

This is the ONLY runtime module in the package permitted to make network calls.
ppa/data/nem_data.py stays cache-only and network-free; callers must invoke
`ensure_plant_years` BEFORE asking nem_data to read anything, never the other
way round. Do not add a lazy download hook inside nem_data.
"""

class RemoteFetchError(RuntimeError): ...

def manifest() -> dict: ...

def available_years() -> list[int]: ...

def missing_plant_years(duids, years, cache_dir=None) -> list[tuple[str, int]]:
    """(duid, year) pairs not already present in packaged or runtime cache."""

def ensure_plant_years(
    duids: "Sequence[str]",
    years: "Sequence[int]",
    progress: "Callable[[float, str], None] | None" = None,
    cache_dir: "Path | None" = None,
) -> list[Path]:
    """Fetch every missing (duid, year) CF column and materialise it as
    <runtime_cache>/availability/<DUID>_<year>.parquet in the compact
    values-only format nem_data._read_5min_values expects.

    Idempotent: already-present files are skipped. Raises RemoteFetchError with
    an actionable message on network failure — never a bare requests exception.
    """

def ensure_price_years(regions, years, progress=None, cache_dir=None) -> list[Path]: ...
```

#### 4.3 The fetch itself

```python
import fsspec
import pyarrow.parquet as pq

fs = fsspec.filesystem("http")
with fs.open(url, block_size=1 << 20) as fh:
    pf = pq.ParquetFile(fh)
    table = pf.read(columns=list(wanted_duids))
```

`fsspec`'s HTTP filesystem issues HTTP range requests; `pq.ParquetFile.read(columns=...)`
reads the footer, then only the column chunks it needs. Reading 2 columns from a 50 MB
year file should transfer well under 1 MB.

**Verify this empirically and record the numbers in the WP report.** Wrap the file
handle in a counter that accumulates bytes read, and assert in a test that fetching two
columns transfers less than 10% of the file size. If the server does not honour range
requests (`Accept-Ranges` absent), fall back to a full download with a caption saying so
— but do not fail.

Materialisation, mirroring `scripts/compact_availability_cache.py`:

```python
out = pd.DataFrame({"availability": cf_values.astype("float32") * capacity_mw})
out.to_parquet(path, compression="zstd", index=False)
```

Two things to get right here:
- The stored dataset holds **CF** (D2); `nem_data.load_availability` returns **MW** and
  `capacity_factor_series` divides by capacity. So multiply back by
  `capacity_registered_mw_used` from `plant_years.parquet` on write. Do not change
  `nem_data`'s contract. Assert round-trip in a test.
- Row count must equal `nem_data.expected_intervals(year)` exactly, or
  `_read_5min_values` silently returns `None` and falls through to the slow path.

Add integrity checks: md5 the downloaded bytes against the manifest when a full file was
downloaded; on a column-pruned read, assert row count and dtype. Write to a `.tmp` file
and `os.replace` so a killed process never leaves a truncated parquet that later reads
as valid.

#### 4.4 Dependencies and discipline

- Add `fsspec` and `aiohttp` (fsspec's HTTP backend) to `requirements.txt`, pinned.
  `requests` is already an indirect dependency of streamlit but pin it explicitly if you
  use it.
- `tests/test_nem_data.py`'s forbidden-import check scans `nem_data` and `aer_futures`
  only — it does **not** need changing. Instead add a new positive assertion:
  `tests/test_remote_cache.py::test_nem_data_does_not_import_remote_cache`, asserting
  `"remote_cache"` does not appear in `ppa/data/nem_data.py`. That is the invariant D3
  actually protects.

#### 4.5 Tests

All offline — no test may hit the network.

- Build a small wide parquet in a temp dir, serve it via `fsspec`'s `file://` or a
  `pytest-httpserver`/`http.server` fixture on localhost.
- `ensure_plant_years` writes a file readable by `nem_data.load_availability` and
  round-tripping to the source CF within float32 tolerance.
- Idempotency: a second call performs zero reads.
- Bytes-transferred assertion (4.3).
- `RemoteFetchError` is raised, with a message naming the record ID and the file, when
  the URL 404s.

**Done when:** tests pass offline, and a manual smoke run against the real Zenodo record
fetches two DUIDs × five years in under ~30 s with the transferred-bytes figure recorded
in the WP report.

---

### WP5 — `Scenario`: new fields for years, sizing year, resolution and SLA

**Owns:** `ppa/scenario.py`, `ui/state.py`, `tests/test_scenario_nem.py`, plus mechanical
fixes at any `dataclasses.replace(..., nem_year=...)` site.

**Difficulty:** medium. **This WP must land before both downstream chains proceed** —
it is the shared file. Do it in one commit and merge it before forking W6/W7 and
W8/W9/W10.

#### 5.1 New fields

```python
    # ── NEM data selection ───────────────────────────────────────────────────
    # Historical years to cycle through in the multi-year dispatch simulation,
    # in order, repeating. Chronological order is the meaningful one, so this is
    # kept sorted ascending. `nem_year` (below) remains as a derived property so
    # the ~38 existing single-year call sites keep working.
    nem_years: tuple[int, ...] = (2025,)
    # The ONE historical year the capacity-sizing LP optimises against. Sizing
    # over the full multi-year horizon is possible but is what makes the LP
    # large; the user picks a representative year instead.
    capacity_sizing_year: int = 2025
    # Snapshot resolution in minutes for the generation/price series: 60, 30, 15
    # or 5. Sub-hourly multiplies LP size (and memory) by 60/resolution, so 15
    # and 5 are guarded — see ui/scenario_form.py and ppa/multi_year.py.
    nem_resolution_minutes: int = 60

    # ── Tiered SLA ───────────────────────────────────────────────────────────
    # `required_delivery_share` above is the ANNUAL obligation. These add
    # tighter obligations at monthly and daily granularity. Both are OFF by
    # default: with them off the model is bit-identical to today's.
    sla_monthly_enabled: bool = False
    sla_monthly_share: float = 0.0
    sla_daily_enabled: bool = False
    sla_daily_share: float = 0.0
```

Replace the `nem_year: int = 2025` **field** with:

```python
    @property
    def nem_year(self) -> int:
        """First (earliest) selected NEM data year.

        Kept as a property, not a field, so the many single-year call sites
        (AER futures, reference_month_ts, cache_status, config summary) keep
        working. It is deliberately absent from dataclasses.asdict(), and
        dataclasses.replace(s, nem_year=...) will now raise — use
        nem_years=(y,).
        """
        return int(self.nem_years[0]) if self.nem_years else 2025
```

Then:

```bash
grep -rn "nem_year=" --include=*.py . | grep -v nem_years
```
Fix every hit to `nem_years=(y,)`. Expect hits in `ui/tabs/nem_map.py`,
`ui/scenario_form.py`, `ui/tabs/optimisation.py` and several tests.

#### 5.2 `ui/state.py`

Add to `_SCENARIO_FORM_KEYS`:
```
"sf_nem_years", "sf_capacity_sizing_year", "sf_nem_resolution",
"sf_sla_monthly_enabled", "sf_sla_monthly_share",
"sf_sla_daily_enabled", "sf_sla_daily_share",
"nm_year_range", "nm_resolution",
```
(Removing `sf_nem_year` if the widget is gone.) A missing key here means a widget keeps
a stale value across scenario changes — a silent, confusing bug.

Note that `state.get_scenario()` already rebuilds a stale `Scenario` from
`dataclasses.asdict`, dropping removed fields and defaulting new ones. Because
`nem_year` becomes a property it will not appear in `asdict()` and a session holding an
old scenario will get `nem_years=(2025,)` by default. Acceptable — but state it in the
WP report so nobody is surprised by a reset after deploy.

#### 5.3 `validate_scenario` additions

Blocking errors only:

```python
if not s.nem_years:
    errors.append("Select at least one NEM data year on the Pick Plants tab.")
for y in s.nem_years:
    if not (2000 <= int(y) <= 2100):
        errors.append(f"NEM data year {y} is out of range.")
if s.optimise_capacity and int(s.capacity_sizing_year) not in tuple(s.nem_years):
    errors.append(
        "The capacity-sizing year must be one of the selected NEM data years."
    )
if int(s.nem_resolution_minutes) not in (5, 15, 30, 60):
    errors.append("Snapshot resolution must be 5, 15, 30 or 60 minutes.")

if s.sla_monthly_enabled and not (0.0 < s.sla_monthly_share <= 1.0):
    errors.append("Monthly SLA share must be between 0 and 1 when monthly SLA is on.")
if s.sla_daily_enabled and not (0.0 < s.sla_daily_share <= 1.0):
    errors.append("Daily SLA share must be between 0 and 1 when daily SLA is on.")
# D6: tsam destroys calendar structure, so a monthly constraint on the clustered
# representation would be silently meaningless rather than merely approximate.
if s.optimise_capacity and s.sla_monthly_enabled and s.sizing_method == "tsam":
    errors.append(
        "A monthly SLA cannot be enforced with the 'Typical weeks (tsam)' sizing "
        "representation: clustering replaces the calendar with representative "
        "weeks, so calendar months no longer exist in the sizing LP. Switch the "
        "sizing representation to 'Full year hourly', or turn the monthly SLA off."
    )
```

Do **not** add "monthly share is looser than annual" here — that is a warning and belongs
in `ui/scenario_form.py` (AGENTS.md §2).

Update the existing `if not (2000 <= int(s.nem_year) <= 2100)` check to iterate
`nem_years` instead.

#### 5.4 Tests

`tests/test_scenario_nem.py`: `nem_year` property returns `nem_years[0]`;
`asdict()` contains `nem_years` and not `nem_year`;
`dataclasses.replace(s, nem_years=(2020, 2021))` works;
each new validation rule fires and each is silent at defaults;
**a default `Scenario()` produces an empty `validate_scenario()` result** (regression
guard against accidentally making the app unrunnable out of the box).

**Done when:** full suite green with no behavioural change at default settings.

---

### WP6 — Resolution plumbing (5 / 15 / 30 / 60 minutes)

**Owns:** `ppa/data/timeseries_utils.py`, `ppa/multi_year.py`, `ppa/sizing.py`,
`ui/tabs/optimisation.py`, `tests/test_resolution_h.py`.

**Difficulty:** medium-high. Depends on WP3 and WP5.

Today everything is hourly: `build_year_timeseries` hardcodes `freq="h"` and
`_hours_in_year`, and `run_multi_year` never passes `resolution_h`. But
`build_network(..., resolution_h=...)` and `extract_results(..., resolution_h=...)`
already accept it — the plumbing is half there.

#### 6.1 `build_year_timeseries`

Add `resolution_minutes: int = 60`. Replace:

```python
year_index = pd.date_range(start=f"{sim_year}-01-01", periods=_hours_in_year(sim_year),
                           freq="h", tz="UTC")
```
with a `freq=f"{resolution_minutes}min"` index of
`_hours_in_year(sim_year) * 60 // resolution_minutes` periods.

`_align_to_index` is positional and length-driven, so it works unchanged — **but** its
tiling behaviour now tiles at the new resolution. Add a test asserting a 30-min source
against a 30-min target is a no-op.

`get_load_series(load_profile, naive_index)` in `ppa/industrial_profiles.py` must be
checked: if it assumes hourly (e.g. indexes by `.hour` only) it is fine; if it assumes
8760 rows it is not. **Read it and report which.**

#### 6.2 Thread `resolution_h` through the runners

- `run_multi_year(..., resolution_h: float = 1.0)`; pass `resolution_minutes` into
  `build_year_timeseries`; pass `resolution_h` into `build_network` and
  `extract_results` inside `_solve_one_year` (it must cross the process boundary — add
  it as an explicit argument, not on the scenario dict, to keep `_solve_one_year`'s
  signature honest).
- `ppa/solver.py::solve` needs no change: it already reads
  `n.snapshot_weightings["objective"]` (WP8 depends on this staying true).
- `build_sizing_timeseries(..., resolution_minutes)` likewise.
- `ppa/sizing.py::optimise_capacities` computes `n_years` from `len(ts) / 8760` in the
  `full_hourly` branch — that is now wrong at sub-hourly resolution. Change to
  `len(ts) / (8760 * 60 / resolution_minutes)`. **This is an easy miss with a large
  blast radius** (it scales every capex term via `horizon_years`).
- `clamp_sizing_years(requested_years, resolution_h)` already takes `resolution_h` but is
  called with the default. Pass the real one — note the existing formula *divides* memory
  by `resolution_h`, which is right for coarse resolutions (>1 h) and correspondingly
  right for fine ones (`resolution_h = 0.0833` at 5 min → 12× the memory). Verify the
  arithmetic rather than assuming.

#### 6.3 The guard

In `ui/tabs/optimisation.py::_run_simulation`, before solving:

```python
res_min = int(getattr(scenario, "nem_resolution_minutes", 60))
if res_min < 30 and scenario.simulation_years > 2:
    raise RuntimeError(
        f"{res_min}-minute resolution over {scenario.simulation_years} years builds an "
        f"LP roughly {60 // res_min}x the hourly size and will exhaust memory. "
        "Reduce the simulation years to 2 or fewer, or use 30- or 60-minute resolution."
    )
```
Plus a matching **warning** (not an error) in `ui/scenario_form.py` next to the control.

#### 6.4 Tests

Extend `tests/test_resolution_h.py`:
- A 30-min run and a 60-min run over the same underlying data produce total load MWh
  within 0.5% of each other. *This is the check that catches a missing `resolution_h`:
  summing MW samples only equals MWh when each sample is one hour.*
- `n_period_hours` on the result equals `8760` (±) regardless of resolution.
- Sized MW at 60 min vs 30 min agree within 10%.

**Done when:** the 30-vs-60-minute energy-conservation test passes, and the full suite is
green with `nem_resolution_minutes=60` producing **byte-identical** results to before
(run a before/after comparison on one scenario and paste both numbers).

---

### WP7 — Pick Plants UI rebuild

**Owns:** `ui/tabs/nem_map.py`, `ui/constants.py`, `ui/nem_cache_status.py`,
`tests/test_nem_map_tab.py`, new `tests/test_pick_plants_years.py`.

**Difficulty:** medium. Depends on WP1, WP3, WP4, WP5.

#### 7.1 New controls, in this order

1. **Year range slider.**
   ```python
   years_available = nem_data.available_years()          # from the manifest
   lo, hi = st.slider(
       "Historical years to use", min_value=min(years_available),
       max_value=max(years_available),
       value=(max(years_available) - 4, max(years_available)),
       key="nm_year_range",
       help="The dispatch simulation cycles through these years in chronological "
            "order, repeating, for as many simulation years as you run.",
   )
   selected_years = tuple(y for y in years_available if lo <= y <= hi)
   ```
   Guard the case where the manifest has gaps inside `[lo, hi]` — the intersection above
   handles it, but caption which years were actually found.

2. **Resolution selectbox.** Extend `ui/constants.NEM_RESOLUTION_MINUTES` to
   `{"1 hour": 60, "30 minutes": 30, "15 minutes": 15, "5 minutes": 5}` and render it
   with `key="nm_resolution"`. Caption the memory implication for 15/5 min.

3. **Capacity-sizing year selectbox**, options = `selected_years`, default = the latest.
   Only shown when the current scenario has `optimise_capacity=True`; otherwise carry the
   scenario's existing value through. Caption: "The capacity optimisation solves against
   this single year. The dispatch simulation still uses all selected years."

4. Region filter — unchanged.

5. **No CUF filter** (removed in WP1).

#### 7.2 Plant list driven by the manifest

Replace the `_cached_eligible_plants(year, fingerprint)` call with:

```python
@st.cache_data
def _cached_operational_plants(years: tuple, fingerprint: tuple) -> "pd.DataFrame":
    return nem_data.plants_operational_for_years(years)
```

Every plant in the returned frame is by construction operational in **all** selected
years, so `simulation_ready` is uniformly `True` and `_selectable_duids` simplifies.
Keep `_marker_style` but drive it from a `data_status` column set to `"ready"` for these
rows; render non-qualifying registry plants as the existing grey dashed markers so the
map still shows the whole fleet, with a caption explaining why they are unselectable
("not fully operational in every selected year").

Update `_tooltip` to show the mean CUF **across the selected years**
(`mean_cuf_selected_years`) and to label it as such. Update
`tests/test_nem_map_tooltip.py` and `tests/test_nem_map_tab.py` accordingly — they assert
tooltip uniqueness and round-trip via `_duid_from_tooltip`, which must keep working.

Update the cache-status expander: replace "UIGF cached / Simulation-ready for 2025" with
"years available", "plants operational in all selected years", and whether the price
years are covered.

#### 7.3 "Use these plants" — the download step

```python
if st.button("✅ Use these plants", type="primary", ...):
    from ppa.data import remote_cache

    duids = [d for d in (wind_duid, pv_duid) if d]
    missing = remote_cache.missing_plant_years(duids, selected_years)
    if missing:
        bar = st.progress(0.0, text="Downloading generation data ...")
        try:
            remote_cache.ensure_plant_years(
                duids, selected_years,
                progress=lambda f, msg: bar.progress(f, text=msg),
            )
            remote_cache.ensure_price_years([price_region], selected_years, ...)
        except remote_cache.RemoteFetchError as exc:
            st.error(f"Could not download the plant data: {exc}")
            return
    updated = dataclasses.replace(
        current,
        data_source="nem_map",
        nem_pv_duid=pv_duid, nem_wind_duid=wind_duid,
        nem_price_region=price_region,
        nem_years=selected_years,
        capacity_sizing_year=int(sizing_year),
        nem_resolution_minutes=int(resolution_minutes),
    )
    ...
```

Note `nem_year=year` is gone (WP5, D5) — it is now a property.

Downsampling to the selected resolution happens in `nem_data.get_timeseries_dicts` at
run time (WP3.4), **not** here. The cache always stores native 5-minute data; storing a
downsampled copy would mean re-downloading whenever the user changes resolution.

#### 7.4 Tests

Pure helpers only (the module imports streamlit; existing tests use `importorskip`):
- selecting a year range where a plant is non-operational in one year excludes it;
- an empty year selection disables the button;
- tooltip uniqueness and `_duid_from_tooltip` round-trip still hold on the real registry;
- `tests/test_ui_static_checks.py` still passes.

**Done when:** manual smoke test — pick a wind and a solar plant over a 3-year range at
30-minute resolution, click Use these plants, confirm files land in the runtime cache and
that the Run tab reports data ready.

---

### WP8 — Tiered SLA constraints in the solver and the sizing LP

**Owns:** `ppa/solver.py`, `ppa/sizing_tsam.py` (returns extra metadata),
`ppa/sizing.py` (passes it through), new `tests/test_sla_constraints.py`.

**Difficulty:** highest in the plan. Use the strongest model. Depends on WP5 only, so it
can run in parallel with WP6/WP7.

**Read AGENTS.md §3 and §5.2 before writing a line.** "A constraint that is missed is not
a constraint" — the last time an energy constraint was added here, a hard 90% delivery
requirement landed at 85.6% because a `.sum()` was not weighted.

#### 8.1 The semantics, stated precisely

Let `w_t = n.snapshot_weightings["objective"][t]`, `L_t` = load, `S_t` =
`Gen_AllowedShortfall` dispatch, `D_t` = `link_p["IPPGen_to_PPAOfftake"]`.

For any snapshot group `P` (a calendar month, or a calendar day, or the whole horizon):

- **Shortfall cap (the default form, per D7):**
  `Σ_{t∈P} w_t·S_t  ≤  (1 − share_P) · Σ_{t∈P} w_t·L_t`
- **Minimum delivery (added only when `optimise_capacity and enforce_min_delivery`):**
  `Σ_{t∈P} w_t·D_t  ≥  share_P · Σ_{t∈P} w_t·L_t`

These are **not** equivalent, because `Gen_Penalty` also sits on `Bus_PPAOfftake` and
bypasses the offtake link, so a shortfall cap alone can be satisfied with penalty energy
while `fulfilled_share` (which counts only `D_t`) stays low. Capping shortfall is
"the offtaker's free allowance is limited"; the min-delivery form is "the portfolio must
actually deliver". Implement both, exactly as above, and say which is which in the
constraint names.

Constraint names must be unique and greppable:
`AllowedShortfall_Limit_M{YYYY}-{MM}`, `AllowedShortfall_Limit_D{YYYY}-{MM}-{DD}`,
and `MinDelivery_Limit_M...` / `MinDelivery_Limit_D...`.

#### 8.2 Snapshot grouping

Refactor the existing `snapshot_groups` block in `solve()` into a helper:

```python
def _period_groups(index: pd.DatetimeIndex, granularity: str) -> list[tuple[str, pd.Index]]:
    """(suffix, snapshots) pairs for 'annual' | 'monthly' | 'daily'.

    'monthly' groups by calendar month, 'daily' by calendar date. Both read the
    real calendar off `index`, which is correct for the dispatch LP and for the
    full_hourly sizing LP. It is NOT correct for tsam-clustered snapshots — see
    _tsam_daily_groups.
    """
```

Then in `solve()`:

```python
groups: list[tuple[str, pd.Index, float, str]] = []   # (suffix, snaps, share, kind)
groups += [(sfx, snaps, s.allowed_shortfall_share, "annual") for sfx, snaps in existing_annual_groups]
if s.sla_monthly_enabled:
    groups += [(f"_M{k}", snaps, 1.0 - s.sla_monthly_share, "monthly") ...]
if s.sla_daily_enabled:
    groups += [(f"_D{k}", snaps, 1.0 - s.sla_daily_share, "daily") ...]
```

Keep the existing per-calendar-year annual grouping for multi-year sizing exactly as it
is.

Skip groups whose `Σ w_t·L_t` is zero (defensive: a clustered or partial period with no
load makes the constraint `0 ≤ 0`, harmless but noisy).

#### 8.3 The tsam problem (D6) — the part that will be got wrong

`cluster_typical_periods` returns a synthetic `pd.date_range(f"{start_year}-01-01", ...)`
index. Grouping *that* by `.month` produces groups that correspond to nothing.

Required changes:

1. **Monthly under tsam is already blocked** by `validate_scenario` (WP5.3). Add a
   belt-and-braces `raise ValueError` in `solve()` if it somehow gets through, rather
   than silently constructing a meaningless constraint.
2. **Daily under tsam** is meaningful *within* a representative period. Each typical week
   is 168 consecutive hourly snapshots; slicing it into 7 blocks of 24 gives 7
   representative days, each standing for `occ` real days. Applying the daily constraint
   to each such block, with the `w_t` weights carried through, means "on a representative
   day of this type, the daily SLA holds". State that this is an approximation.

   Implement by having `cluster_typical_periods` **additionally return a period-block
   label array** — one integer per clustered snapshot identifying its
   `(cluster_id, day_within_period)` — and pass it through `optimise_capacities` into
   `solve()` via a new optional `period_labels: pd.Series | None = None` argument on
   `solve()`. When `period_labels` is given, daily groups come from it; otherwise from
   the calendar.

   Do **not** try to infer the blocks from the synthetic index inside `solve()`. The
   label must come from the clusterer, which is the only thing that knows the true
   period boundaries (including any appended extreme periods, which are extra periods at
   the end and must be labelled too).

3. `hours_per_period` is a parameter (168 default). Derive `days_per_period =
   hours_per_period // 24` rather than hard-coding 7, and handle a non-multiple-of-24
   period by falling back to one group per period with a caption.

#### 8.4 Feasibility

Daily constraints are far more likely to make the LP infeasible than an annual one:
a single dark, still winter day cannot meet an 80% daily SLA at any build size unless
storage or market buy covers it. Extend `ppa/sizing.py::_infeasibility_hint` to name the
daily/monthly constraint when it is enabled:

```
"A daily SLA of 85% requires at least that share of EVERY day's load to be
delivered. A single low-resource day that no portfolio within the build caps can
cover makes the whole LP infeasible. Lower the daily share, enable market buy,
raise the BESS cap, or turn the daily SLA off and rely on the monthly/annual one."
```

Also count constraints: daily over 25 years at 5-minute resolution is 9,125 constraints
over 2.6M snapshots. Log the constraint count at DEBUG and mention it in the sizing
diagnostics.

#### 8.5 Tests — `tests/test_sla_constraints.py`

These are the acceptance criteria. Build small synthetic networks (follow
`tests/test_sizing_network.py` for the pattern).

1. **Off by default is a no-op.** With `sla_monthly_enabled=False,
   sla_daily_enabled=False`, the set of constraint names on the model is *identical* to
   the current code's. Snapshot it.
2. **Monthly cap binds.** Construct a scenario where one month has poor resource. With
   the monthly SLA off, that month's delivered share falls below the monthly target;
   with it on at that target, **every month's** achieved shortfall share is ≤
   `1 − sla_monthly_share + 1e-6`. Assert on the extracted dispatch, not on the model.
3. **Daily cap binds.** Same, per day.
4. **Weighting.** Run the same scenario at 60-minute uniform weights and with
   deliberately non-uniform `snapshot_weightings` summing to the same total; the achieved
   shares must agree to 1e-6. *This is the test that would have caught the 85.6% bug.*
5. **tsam daily.** With `sizing_method="tsam"` and a daily SLA, the LP solves, and every
   representative-day block satisfies the constraint. Assert `period_labels` covers every
   snapshot exactly once.
6. **tsam monthly is refused.** `validate_scenario` returns the D6 error, and `solve()`
   raises if called directly.
7. **Infeasibility is reported, not swallowed.** A daily SLA of 100% with no market buy
   and a solar-only portfolio returns `status != "ok"` with the hint text present.
8. **Monotonicity.** Tightening the daily share never *decreases* sized capacity
   (`bess_mw` in particular). A violation means the constraint has the wrong sign.

**Done when:** all eight pass, plus the pre-existing `tests/test_sizing_*.py` suite
unchanged.

---

### WP9 — SLA reporting and post-solve verification

**Owns:** `ppa/results.py`, `ui/tabs/results_deep_dive.py`, `ui/tabs/optimisation.py`
(diagnostics only), `tests/test_sla_reporting.py`.

**Difficulty:** medium. Depends on WP8.

#### 9.1 Results

Add to `SummaryVolumes` (with defaults so unpickling an old `run_store` payload does not
break):

```python
    min_monthly_delivery_share: float = 1.0
    min_daily_delivery_share: float = 1.0
    n_months_below_sla: int = 0
    n_days_below_sla: int = 0
```

And to `OptimisationResult`:
```python
    monthly_delivery_share: pd.Series = None   # index = period end, value = D/L
    daily_delivery_share: pd.Series = None
```

Compute them in `extract_results` by grouping `ppa_delivery` and `ts["ppaload_mw"]` by
`ts.index.to_period("M")` / `.normalize()`, applying `resolution_h`, and dividing.

#### 9.2 Verification, per AGENTS.md §5.2

In `extract_results`, after computing the shares, when the corresponding SLA is enabled
and the achieved minimum falls more than 0.5 pp below the requirement, attach a warning
string to the result and surface it in the UI as `st.warning`. Do **not** raise — an
infeasible-adjacent solve should still be inspectable — but make it impossible to miss.
This is exactly the signal that caught the weighting bug last time.

#### 9.3 UI

- `ui/tabs/results_deep_dive.py`: a "SLA compliance" section with a bar chart of monthly
  delivery share and a line of daily delivery share, each with a horizontal target line,
  and the count of periods below target. Follow the existing chart conventions in
  `ui/charts.py` (note the `A\$` LaTeX-escaping convention for dollar signs in
  `st.caption`).
- `ui/tabs/optimisation.py::_render_sizing_diagnostics`: add rows for which SLA tiers
  were active and which bound.

#### 9.4 Tests

- Monthly/daily shares computed from a known synthetic dispatch match a hand-computed
  answer.
- `min_monthly_delivery_share` ≥ the requirement in a solve where the constraint is on
  (the round-trip of WP8 test 2, but through `extract_results`).
- Old `run_store` payloads without the new fields still load.

---

### WP10 — Set Terms UI: the SLA box

**Owns:** `ui/scenario_form.py`, `ui/state.py`, `ui/tabs/optimisation.py`
(`_render_scenario_summary` only), `ui/config_summary.py`.

**Difficulty:** low-medium. Depends on WP5, WP8, WP9.

#### 10.1 Answer to "is the switch to force optimal capacity to meet the SLA still there?"

**Yes.** It is `Scenario.enforce_min_delivery`, rendered in `ui/scenario_form.py` as a
`st.checkbox` labelled *"Enforce the {x}% delivery share as a hard constraint"*, inside
the "PPA contract terms" expander, and **only when `optimise_capacity` is True** (the
`else:` branch just passes `initial.enforce_min_delivery` through). It is easy to miss
because it is nested and conditional. This WP moves it into the new SLA box, where it
belongs.

#### 10.2 New "Service level agreement (SLA)" expander

Move out of "PPA contract terms": `required_delivery_share` and `enforce_min_delivery`.
Leave `ppaload_mw`, `ppa_price`, `pen_mult` and the load-profile selector where they are.

```python
with st.expander("Service level agreement (SLA)", expanded=True):
    st.caption(
        "The annual obligation is always active. Monthly and daily obligations are "
        "optional and stack on top: each limits the free shortfall allowance within "
        "that period, so a good year cannot pay for a bad month."
    )

    cols = st.columns(3)
    required_delivery_share = cols[0].slider(
        "Annual delivery share (%)", 50, 100, int(initial.required_delivery_share * 100),
        step=1, format="%d%%", key="sf_required_delivery_share",
        help="Share of total annual contracted load that must be delivered.",
    ) / 100.0

    sla_monthly_enabled = cols[1].toggle(
        "Monthly minimum", value=bool(initial.sla_monthly_enabled),
        key="sf_sla_monthly_enabled",
    )
    if sla_monthly_enabled:
        sla_monthly_share = cols[1].slider(
            "Monthly delivery share (%)", 0, 100,
            int((initial.sla_monthly_share or initial.required_delivery_share) * 100),
            step=1, format="%d%%", key="sf_sla_monthly_share",
        ) / 100.0
    else:
        sla_monthly_share = float(initial.sla_monthly_share)

    # ... same shape for daily, in cols[2] ...

    if optimise_capacity:
        enforce_min_delivery = st.checkbox(
            f"Force the optimised capacity to meet the {required_delivery_share:.0%} "
            "annual share (hard constraint)",
            value=bool(initial.enforce_min_delivery), key="sf_enforce_min_delivery",
            help=(...keep the existing long help text, which explains that without it "
                  "the SLA is only a price signal and delivery settles at 50-65%...),
        )
    else:
        enforce_min_delivery = bool(initial.enforce_min_delivery)
```

#### 10.3 Warnings (in the form, not `validate_scenario`)

- Monthly or daily share **below** the annual share → *"A {m}% monthly minimum is looser
  than the {a}% annual obligation, so it will never bind. Raise it above the annual
  share for it to have any effect."*
- Daily share ≥ 90% → *"Daily obligations above ~90% are frequently infeasible: a single
  low-resource day cannot be covered by a portfolio of any size without storage or
  market purchases."*
- `optimise_capacity and sla_monthly_enabled and sizing_method == "tsam"` → repeat the
  D6 explanation next to the sizing-method radio, so the user sees it before the blocking
  error.
- `nem_resolution_minutes < 30 and simulation_years > 2` → the WP6.3 memory warning.

#### 10.4 Wire-up

Add all four new fields to the `dataclasses.replace(initial, ...)` return. Add the widget
keys to `ui/state.py::_SCENARIO_FORM_KEYS` (WP5.2 already lists them — verify).

Update `ui/tabs/optimisation.py::_render_scenario_summary` "PPA contract" column and
`ui/config_summary.py` to show the active SLA tiers and the selected years / resolution.

**Done when:** `tests/test_ui_static_checks.py` passes (it will catch the column-index
mistakes this layout invites) and a manual pass shows all four warnings firing on
demand.

---

### WP11 — Documentation

**Owns:** `README.md`, `AGENTS.md`, `docs/DEPLOYMENT.md`, `docs/DATA_ACQUISITION.md`,
`docs/UAT_checklist.md`, `docs/PLAN_multiyear_sla.md`.

- `AGENTS.md` §2: add the new invariant — *"`ppa/data/remote_cache.py` is the only
  runtime module permitted network access. `nem_data.py` must never import it; callers
  fetch first, then read."*
- `AGENTS.md` §3: add a domain trap — *"tsam clustering destroys the calendar. Monthly
  constraints are meaningless on clustered snapshots and are blocked in
  `validate_scenario`; daily constraints are applied per representative day block and are
  an approximation."*
- `AGENTS.md` §6: update the repo-size note — the single-year availability cache may now
  be reduced or removed from the working tree in favour of Zenodo, but **deleting it does
  not reclaim git history**. Decide before pushing.
- `docs/DEPLOYMENT.md`: new §on the runtime cache — Streamlit Cloud containers are
  ephemeral, so `PPA_RUNTIME_CACHE_DIR` refills on each cold start; note the expected
  first-fetch latency, and that the app now needs outbound HTTPS to `zenodo.org`.
- `docs/DATA_ACQUISITION.md` (new): the WP2 pipeline end to end, the operational-year
  rule in full with its thresholds and rationale, the Zenodo record ID and DOI, and how
  to publish a new version.
- `docs/UAT_checklist.md`: add the manual checks from WP7 and WP10.

---

### WP12 — Integration and full-suite verification (do this yourself)

Not a sub-agent task. See §5.

---

## §5. Review and merge sign-off

Do not merge on a sub-agent's self-report. AGENTS.md §4: *"It self-reports success and is
often right, but a report is not evidence."*

### 5.1 Gate per WP branch

For each WP branch, before merging into the working branch:

1. `git diff --stat` against the working branch. Every changed file must be in that WP's
   "Owns" list. Anything else is a scope breach — investigate before merging.
2. Run the full suite yourself:
   ```bash
   MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider
   ```
   Not the subset the agent ran.
3. `git diff` the tests specifically. A green suite where the agent weakened an assertion
   is worse than a red one. Look for changed thresholds, added `pytest.skip`, and
   `assert True`.
4. Check for new `# type: ignore`, bare `except Exception: pass`, and any commented-out
   code left behind.

### 5.2 Numerical regression gate — run before and after the whole stack

The one thing that matters: **at default settings the model must be unchanged.**

```
Scenario: default Scenario(), optimise_capacity=True, sizing_method="tsam",
          simulation_years=3, nem_years=(2025,), nem_resolution_minutes=60,
          sla_monthly_enabled=False, sla_daily_enabled=False
```

Record, on the base branch and on the merged branch:

| metric | base | merged | tolerance |
|---|---|---|---|
| sized wind MW | | | exact |
| sized solar MW | | | exact |
| sized BESS MW / MWh | | | exact |
| sized link MW (×3) | | | exact |
| year-1 delivered GWh | | | exact |
| year-1 `fulfilled_share` | | | exact |
| project IRR | | | exact |
| sizing wall-clock s | | | ±25% |
| peak RSS (`scripts/measure_peak_rss.py`) | | | ±10% |

Any non-exact difference in the first seven rows means something in WP5/WP6/WP8 changed
the model when it should not have. Find it before merging. Record the table in
`docs/sizing_experiments.md` — AGENTS.md §5.6: *"Record results where they survive."*

### 5.3 Feature acceptance checks

Multi-year data:
- [ ] Pick a wind and a solar plant over a 5-year range. Confirm only plants operational
      in **all five** years are offered, and spot-check one exclusion against
      `plant_years.parquet`'s `reject_reasons`.
- [ ] Confirm the download transfers roughly (plants × years × ~250 KB), not whole-year
      files. Paste the measured bytes.
- [ ] Run 10 simulation years over 5 selected weather years and confirm from the
      per-year output that years cycle **1,2,3,4,5,1,2,3,4,5** in chronological order.
- [ ] Change the resolution to 30 min and confirm total annual load MWh is within 0.5% of
      the 60-min run.
- [ ] Confirm the sizing LP used only `capacity_sizing_year` (check the sizing
      diagnostics horizon and the LP row count).

SLA:
- [ ] With monthly SLA at 85% and annual at 75%, confirm `min_monthly_delivery_share` ≥
      0.85 in the results, and that at least one month was below 0.85 with the constraint
      off. *A constraint that does not change the answer has not been tested.*
- [ ] Same for daily.
- [ ] Confirm the tsam + monthly combination is blocked with the D6 message, and that
      switching to Full year hourly unblocks it.
- [ ] Confirm a 99% daily SLA reports infeasible **with the explanatory hint**, not a
      bare "infeasible".
- [ ] Confirm the `enforce_min_delivery` checkbox is present in the new SLA box and still
      only appears in Find-optimal-capacity mode.

UI:
- [ ] Title reads "PyPSA based PPA explorer - Australia" in both the browser tab and the
      page heading.
- [ ] No capacity-factor slider on Pick Plants.
- [ ] No grey preset cards on Set Terms; the form is the tab's main content.

### 5.4 Deploy gate

- [ ] `requirements.txt` still resolves (`tsam==3.4.2` ⇄ `highspy==1.15.0` — do not let a
      sub-agent bump highspy; `pip install -r requirements.txt` must not
      `ResolutionImpossible`).
- [ ] The app starts with an **empty** runtime cache and no committed availability files
      — i.e. a fresh container can fetch what it needs.
- [ ] The app degrades gracefully with **no network**: the picker still renders from the
      committed manifest, and "Use these plants" fails with the `RemoteFetchError`
      message rather than a stack trace.
- [ ] Peak RSS at the deployed defaults still fits ~1 GB.

### 5.5 Suggested merge order

`W1` → `W5` → (`W3` → `W4` → `W7`) ∥ (`W6`) ∥ (`W8` → `W9` → `W10`) → `W11` → `W12`.

Merge `W5` on its own and re-run the suite before forking, because it is the only file
both chains touch.

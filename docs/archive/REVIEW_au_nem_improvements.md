# Reviewer Notes — Australian NEM Improvements (Plan vs Implementation)

**Repo:** `pypsa-ppa-nem` (working dir `/home/hanan/projects/pypsa-ppa-nem`)
**Branch:** `feature/au-nem-cleanup` — HEAD `9bd3eea`
**Plan:** `PLAN_au_nem_improvements.md` (731 lines)
**Test status (final):** `237 passed` — run as
`MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider` (~24 s). No skips, no xfails.

This document walks the reviewer through every work item in the plan, the commit that
implements it, the evidence it satisfies the plan's assertions, and any deviations or
open items. Commit hashes are full-SHA prefixes from this branch.

---

## 0. Ground rules and definitions of done

Plan §0 and §4 set the acceptance criteria. Status:

- **All new tests in §1.1 pass; original 186 still pass.** ✅ 237 passed (see per-item sections;
  the original baseline of 186 is inside the 237).
- **`docs/UAT_checklist.md` walked by reviewer with no failures.** ⏳ Manual — `docs/UAT_checklist.md`
  was created in W2 and is included below (§2) but requires a human pass.
- **Exported `financial_model_*.xlsx` opens in Excel with no repair prompt.** ✅ automated by
  `tests/test_excel_export_integrity.py` (W11) which unpacks the xlsx and asserts no stray `<f>`
  elements; the W11 root-cause (a note string starting with `=` being written as a formula) is fixed.
- **Sizing diagnostics explain the fleet; link-cap bug visible as a larger build; connection MW
  reported + utilisation shown.** ✅ W12 + W16 (below).
- **Benchmarks recorded in the PR body.** ⏳ The numbers are recorded **in commit messages and code
  comments** (W12 merchant sweep → §W12b, W14 method comparison → §W14, W15 solver comparison →
  §W15); they are not yet copied into a PR body because no PR has been opened.
- **Blocked items listed.** ✅ The AER download (W4) fell back to bundled data; the live NEM registry
  fetch is 403-blocked (W7 first-power-date caveat). See §Open items.

---

## 1. Commit map (plan §3 order)

| Plan item | Commit | Subject | Plan line |
|---|---|---|---|
| W1 | `3c698a2` | branch + baseline (186 passed) | §95 |
| W2 | `60401ee` | test scaffolding + UAT checklist (11 strict xfail modules) | §100 |
| W11 | `1fae748` | Excel note-as-formula fix (`_text()` guard) | §363 |
| W5 | `e889cf0` | default `chosen_day` 2025-03-15 | §172 |
| W9 | `6816563` | `coerce_chosen_day` + UI reconciliation | §301 |
| W3 | `93439a1` | remove Project Locations & Market Zone | §107 |
| W6 | `0443149` | remove European data path; Get Data rename | §186 |
| W7 | `2f21127` | map hover CUF % + first-power date | §237 |
| W8 | `c79e6db` | Custom Data date-range template | §271 |
| W4 | `8eef785` | AER futures default seed; de-Europeanise | §128 |
| W12 | `55d8202` | sizing overhaul (a/b/c/e/f) | §401 |
| W16 | `3908ca4` | results date-range / 24 h tabs / link MW | §654 |
| W13 | `ddaf550` | verify 1-year sizing horizon claim | §550 |
| W14 | `9b01aa3` | tsam typical-days sizing (optional dep) | §573 |
| W15 | `1209521` | HiGHS HiPO benchmark → keep simplex | §611 |
| W10a | `1782931` | Aus English — text/docstrings/comments | §330 |
| W10b | `9bd3eea` | Aus English — identifiers + un-xfail gate | §330 |

---

## 2. W1–W11 (foundation and bug fixes)

### W1 — Branch + baseline (`3c698a2`)
Created `feature/au-nem-cleanup` from the previous state and recorded the baseline:
**186 passed** (~14 s). No code changed; the plan document itself was the only addition.

### W2 — Test scaffolding (`60401ee`)
Added `docs/UAT_checklist.md` (70 lines, manual walkthrough) and **11 test modules**,
each `@pytest.mark.xfail(strict=True)` (strict xfail = an unexpected pass *fails* the build,
so each item must genuinely land):

`test_aer_counterfactual.py`, `test_chosen_day.py`, `test_custom_template.py`,
`test_excel_export_integrity.py`, `test_nem_map_tooltip.py`, `test_no_european_paths.py`,
`test_results_ranges.py`, `test_sizing_horizon.py`, `test_sizing_network.py`,
`test_sizing_tsam.py`, `test_spelling_en_au.py`.

`docs/UAT_checklist.md` is a 19-item manual checklist (map tooltip, Excel export, sizing
diagnostics, result charts, NEM-only data path). It requires a human pass.

### W11 — Excel export corruption (`1fae748`)
Root cause confirmed exactly as the plan described: the **Inputs** sheet contained a note string
(`= FID; devex bullet and construction both start here`) that openpyxl treated as a formula →
`Removed Records: Formula` repair prompt. Fix: a `_text()` guard applied to **all** label/note/unit
writes in `ppa/financial_model_excel.py`, so any cell content that is a bare string (even one
starting with `=`) is written with an explicit text type. `test_excel_export_integrity.py` asserts
the workbook has no `<f>` elements and passes.

### W5 — Reference-day default (`e889cf0`)
Default `chosen_day` changed from the European date to **2025-03-15** in `Scenario` and in
`scenario_from_excel`. One xfail line removed from `test_chosen_day.py`.

### W9 — `chosen_day` reconciliation (`6816563`)
Added `coerce_chosen_day` in `ppa/data_loader.py`: when the requested `chosen_day` is not present
in the loaded data (e.g. cached years don't include it), coerce to the nearest available date
instead of raising the "not present" block. The Optimisation tab uses it for both the NEM period
controls and the single-day reference run; two `index=14` fallbacks in `ui/scenario_form.py` were
fixed. Removed the corresponding xfail lines from `test_chosen_day.py`.

### W3 — Case Setup clean-up (`93439a1`)
Removed **Project Locations** and **Market Zone** from Case Setup (‑165 lines in
`ui/scenario_form.py`). Transmission cost moved into the **Market interaction** section of the
same form.

### W6 — Remove the European path; rename tab (`0443149`)
Deleted the ENTSO-E day-ahead price caches and renewables.ninja PV/wind caches under
`data/cache/entsoe/` and `data/cache/renewables_ninja/` (European data), removed the code paths
that read them, and renamed the NEM map tab to **"Get Data"** (tab order preserved).
`test_no_european_paths.py` greps the codebase to prove no European data path remains.

### W7 — Map hover tooltip (`2f21127`)
`ui/tabs/nem_map.py` now shows **CUF % (2025)** and the plant's **first-power date** on the map
marker hover. `ppa/data/nem_data.py` gained first-power-date parsing from the plant registry;
`scripts/fetch_nem_plant_registry.py` extended to capture it.

> ⚠️ **Deviation/blocker:** the live NEM registry re-fetch returns **HTTP 403** in this
> environment, so `first_power_date` is absent from the committed registry parquet. The feature
> falls back to SCADA availability; tests pass on the SCADA fallback. See §Open items.

### W8 — Custom Data template (`c79e6db`)
The Custom Data template is now generated from a **date range + periodicity** (start/end/frequency)
rather than a fixed 48-hour stub. `ui/constants.py` holds the template bounds
(`TEMPLATE_START/END/FREQ_MINUTES`), `ui/tabs/custom_data.py` builds the dated CSV, and the upload
path accepts it. `test_custom_template.py` validates the generated template.

### W4 — AER futures (`8eef785`)
AER base-futures are now the **default forward seed** in the counterfactual scenario, replacing the
European forward. `scripts/fetch_aer_futures.py` + `scripts/README.md` document the acquisition
(offline AER CSV → repo `hedge-price` parquet cache). The counterfactual copy is de-Europeanised.
`test_aer_counterfactual.py` verifies the default seed path.

> ⚠️ The live AER download is blocked (external service), but the cache path is exercised by tests.

---

## 3. W12 — Capacity sizing under-builds and ~2 % IRR (`55d8202`)

The plan's biggest item: **(a) confirmed bug + (b)/(c)/(e)/(f) design fixes**. (d) was explicitly
"no change required" and is documented, not implemented.

### (a) Transport links were hard-capped at slider MW — FIXED
The old code computed `wind_link_mw`/`pvbess_link_mw`/`sell_link_mw` in sizing mode but then
ignored them in `link_defs`, pinning links to `s.onsw_mw`, `s.pv_mw + effective_bess_mw`,
`s.maxsell_mw`. A 250/210/460 MW toy network confirmed the diagnosis: the LP could "build" 1000 MW
but only ever *deliver* through the 250/210 MW links, so it never built beyond them.

Fix in `ppa/network.py`: the **three transport links**
(`OnshoreWind_to_IPPGeneration`, `PVBESS_to_IPPGeneration`, `IPPGen_to_SellToMarket`) are now
extendable investment variables in sizing mode with:
- `p_nom_max = s.grid_connection_max_mw` (new field, default `inf`; UI "Grid connection limit (MW)", blank = unlimited),
- `capital_cost` = the real connection cost from `ProjectFinanceInputs` (`onsw/pv/bess_connection_cost`, A$M/MW) annualised the same way as generation capex (`×(crf + opex_rate) × horizon_years`) — a **strictly positive** cost, which pins `p_nom_opt` to the realised peak flow and removes the zero-cost degeneracy,
- fixed (`p_nom` = contractual) in dispatch mode.

The **PPA offtake link stays fixed** at `ppaload_mw` (contractual revenue carrier) and
`BuyFromMarket_to_IPPGeneration` stays at `maxbuy_mw` (contract cap) — only the three transport
links become extendable.

**Carry-through:** `SizedCapacities` gained `wind_link_mw`, `pvbess_link_mw`, `sell_link_mw`;
`apply_sizing` writes them into the dispatch scenario so the hourly simulation uses the sized
connection MW (no phase disagreement). `test_sizing_network.py` asserts:
- links extendable in sizing mode / fixed in dispatch mode,
- offtake link never extendable,
- `p_nom_opt` ≈ peak flow per link (tolerance),
- cheap-capex toy LP builds **> slider values** (the acceptance signal — `test_toy_lp_builds_more_than_slider_values`, `sized_link_total > 150 MW`),
- `grid_connection_max_mw` caps the build.

### (b) Merchant revenue zeroed in sizing — now credited at a haircut
`Gen_SellToMarket.marginal_cost` is now set from `ts["ts_MktPrice"]` times
`Scenario.sizing_merchant_value_share` (default **0.5**), applied **to positive prices only** —
negative hours keep their full disincentive (`merch_price.where(price<=0, price*share)`), so the LP
still curtails instead of dumping at negative spot. `sizing_merchant_value_share` is validated in
`[0,1]`.

### (c) LP cost basis ≠ financial model — now hurdle-rate based
In sizing mode `build_network` annualises with **`crf(target_irr)`** (default 10 %, not
`discount_rate`) and **`×(1 + devex_pct_of_capex)`** (default 10 %), so the LP only builds capacity
that clears the hurdle rate — matching the fuller financial model. Documented in the sizing
docstring.

### (d) Allowed shortfall — no change (as the plan instructs)
The plan concluded the current formulation already prices shortfall at the forgone PPA margin
structurally. **Not implemented**, by design. The `0.001` epsilon stays.

### (e) Penalty cost — no change; explain via diagnostics
The penalty design (bus-bypass ⇒ forgoes PPA revenue + pays `ppa_price × pen_mult`) is left as-is.
Added the **sizing diagnostics expander** in the Optimisation tab (`ui/tabs/optimization.py`,
`ppa/sizing.py::sizing_diagnostics`) showing, per technology: annualised A$/MW/yr, achieved CF,
implied LCOE, tariff/penalty/avg-spot for comparison; plus binding flags (`p_nom_opt` vs
`p_nom_max` per generator, link `p_nom_opt` vs `grid_connection_max_mw`, shortfall/market-buy
tightness). `test_sizing_diagnostics_reports_costs_and_binding` asserts the rows and the binding
flags.

### (f) One weather year — noted
README caveat about 2025-only SCADA + a TODO. No new SCADA acquisition (out of scope).

### W12b — merchant-share sweep
The plan asks for a `share ∈ {0, 0.25, 0.5, 0.75, 1.0}` sweep recorded in the PR body. The
implemented numbers were recorded in the commit message (the run on the toy Corporate-style inputs
showed the links build beyond the old slider caps once merchant value is credited and the caps bind).
This should be re-run on a real case study and pasted into the PR body when the PR is opened
(§Open items).

**Tests:** `tests/test_sizing_network.py` — 10 tests, all green.

---

## 4. W16 — Results: date-range selection, 24 h average tabs, connection MW (`3908ca4`)

### 16.1 — Date-range selection
- `ui/tabs/results_deep_dive.py` `_render_dispatch_section` now takes a `(start, end)` range
  controlled by an `st.slider` over the result's datetime index (default = 7 days from the
  coerced `chosen_day`), instead of the single-day slice.
- `fig.update_xaxes(rangeslider_visible=True)` added to the supply-mix and price charts (the
  draggable "selection bar" underneath each chart); slider + rangeslider compose.
- Downsampling guard: `ui/charts.py`/result paths use `.iloc[::n]` when the window exceeds
  ~5000 points (protects the 105 120-point 5-min full-year custom upload case).
- `ppa/results.py::filter_dispatch_range` performs the range filter (inclusive of both endpoints).

### 16.2 — "Average 24 h" tabs
- Supply mix → `build_24h_avg` on the **range-filtered** frame → `make_supply_mix_24h_chart`.
- Spot price → new `make_price_24h_chart(prices_avg)` with a **P10–P90 band** across the days in
  range.
- BESS SoC → new `make_soc_24h_chart(soc_avg, bess_mwh)`.
- `build_24h_avg` slots by fractional hour-of-day (hour + minute), so 5-min/30-min data averages
  onto its own cadence, not 24 collapsed points.
- Nested tab layout per chart group: `["| Time series", "| Average 24 h"]` inside
  `["| Actual hourly supply mix", "| Market spot price", "| BESS SoC"]`.

### 16.3 — Connection (link) MW
- `ppa/results.py::_extract_link_utilisation` extracts per-link `p_nom_opt` (falling back to
  `p_nom`) plus realised peak flow; `OptimizationResult` gained `link_utilisation`.
- Displayed (a) in the **"Optimised portfolio"** banner (`Wind link A MW · PV+BESS link B MW ·
  Export link C MW`) and (b) as a **link table** in the Results tab: sized MW, peak MW,
  utilisation (`peak/sized`). Utilisation ≈100 % on the export link is the `grid_connection_max_mw`
  binding signal.
- 4 previous xfails in `test_results_ranges.py` flipped to real assertions (24-row means, 48-row
  30-min grouping, inclusive endpoints, `peak ≤ sized`).

**Tests:** `tests/test_results_ranges.py` — green (previously strict-xfail).

---

## 5. W13 — Verify the 1-year sizing horizon claim (`ddaf550`)

Plan concern: the Optimisation tab's success message claimed the sizing LP solved "1 year" while
the hourly dispatch still solves all N years — a potentially misleading "1 year" message.

`ui/tabs/optimization.py::_run_simulation` is instrumented with `time.monotonic()` timing for the
sizing vs dispatch phases and now states clearly:

> "Sizing LP: 1 year(s). The subsequent hourly dispatch simulation still solves all N year(s) —
> that is where most of the runtime goes."

Final success line: `Completed N year(s) successfully (sizing Xs + dispatch Ys)`.
`tests/test_sizing_horizon.py` (5 tests) validates horizon/`n_years` handling.

---

## 6. W14 — Better sizing representation via `tsam` (`9b01aa3`)

Plan §573. Default sizing method is now **typical days via the optional `tsam` package**; the full
hourly year and legacy coarse-block modes remain selectable.

**New/changed:**
- `ppa/sizing_tsam.py` — `cluster_typical_periods(ts, n_periods=12, hours_per_period=24,
  extreme_periods=True) -> (clustered_df, weights_Series)`. Uses tsam's new `aggregate()` API
  (`tsam.config.ClusterConfig(method="hierarchical")` + `ExtremeConfig` preserving peak-load and
  dark-lull days). Returns a clustered frame with a real hourly `DatetimeIndex` (so
  `solver.py`'s per-year caps keep working) and per-snapshot weights (occurrence counts summing
  to ≈8760) as the PyPSA snapshot weighting. `tsam_available()` guards the import.
- `ppa/network.py::build_network(..., snapshot_weightings: pd.Series | None)` — overrides the
  uniform `resolution_h` weighting; `horizon_years` derived from the weight sum. (Note: adapted to
  pypsa 1.2.4's snapshot-weightings columns — `objective/stores/generators`.)
- `ppa/scenario.py` — `sizing_method: str = "tsam"` + `sizing_n_periods: int = 12`, validated
  (method ∈ {tsam, full_hourly, coarse}; 4 ≤ periods ≤ 36; error when tsam selected but not
  installed).
- `ppa/sizing.py::optimise_capacities` — dispatches on method; `SizedCapacities` gains
  `sizing_method` and `sizing_delivery_share`; `sizing_diagnostics` returns
  `sizing_method`/`sizing_delivery_share`/`delivery_share_full`. The delivery share is computed
  **weighted by snapshot weightings** (p MW × weighting hours) so clustering losses show up as a
  gap vs the full hourly simulation.
- `ui/scenario_form.py` — sizing-method radio (`Typical days (tsam)` / `Full year hourly` /
  `Coarse resolution (legacy)`); tsam branch adds a 4–36 typical-period slider.
- `ui/tabs/optimization.py` — delivery-share comparison caption in the diagnostics
  ("sizing LP X% (tsam rep) vs full hourly simulation Y% (+/-pp)", flags a >2 pp gap with a note to
  try more periods).
- Deps: `tsam = ">=3.4"` (pixi) / `tsam==3.4.2` (requirements), documented as optional/guarded.
  tsam was system-installed for verification.

**Verification performed:**
- 12 clusters + extremes → 360 hourly rows, weights sum 8760, energy preservation ≤0.00%, load peak
  preserved (139.99975/140).
- `optimise_capacities(..., tsam, 8 periods)` on a synthetic 60-day frame solves OK and reports
  `sizing_method=tsam`, `sizing_delivery_share≈0.77`.
- W12's `test_sizing_network.py` toy tests pinned to `sizing_method="full_hourly"` (they exercise
  the exact-hourly LP, not clustering).

**Tests:** `tests/test_sizing_tsam.py` (3, `importorskip("tsam")`): cluster shape, weights sum ≈
8760, annual energy within 2 %, load peak within 5 %. tsam's v3 column-order FutureWarning is
suppressed and the output column order is normalised.

---

## 7. W15 — HiGHS HiPO benchmark → keep dual simplex (`1209521`)

Plan §611. Verified in this environment and measured, not assumed:

**Findings:**
- `highspy 1.15.1` is pinned. HiPO is available only with the separate `highspy-extras` wheel
  (Apache-2.0; HiGHS core MIT). With plain highspy, `solver=hipo` errors
  ("features unavailable: amd, blas, metis, rcm"). After installing `highspy-extras` + matching
  highspy 1.15.1, `solver=hipo` works.
- ⚠️ **Dependency tension:** `tsam 3.4.2` pins `highspy<=1.15.0`, while `highspy-extras` is 1.15.1.
  Both work together at runtime (verified: tsam clustering + HiPO under highspy 1.15.1), but a
  **hard** `highspy-extras==1.15.1` dependency would conflict with tsam's resolver pin. The plan
  says extras must be optional — so it is **documented, not added** to the dependency files
  (`pixi.toml`, `requirements.txt`, `README.md`).

**Actions:**
1. `ppa/solver.py::solve(..., solver_options: dict | None = None)` → `solve_model(..., **solver_options)`, default `{}`.
2. `scripts/bench_solver.py` — self-contained benchmark (deterministic synthetic year; no NEM data
   needed) that builds the sizing LP for a chosen method (`tsam`/`full_hourly`/`coarse`) and times
   `{simplex, ipm, ipm-no-crossover, hipo, hipo-no-crossover}`, printing model dimensions (from the
   linopy log) and a "FASTER >25%" verdict vs dual simplex. HiPO rows are skipped gracefully when
   `highspy-extras` is absent.
3. **Decision rule applied and recorded** (comment block in `ppa/solver.py`):

   | Sizing LP | simplex | ipm | hipo |
   |---|---|---|---|
   | full-year hourly (306,611 × 122,646) | **15.9 s** | 26.1 s (24.6 no-xover) | 26.3 s (25.6 no-xover) |
   | tsam 12 typical days (11,771 × 4,710) | **1.4 s** | ~1.5 s | ~1.5 s |

   HiPO loses to dual simplex on both the largest and the new (W14) typical-day LPs — **no
   algorithm override is applied anywhere**; dispatch solves stay on simplex (small, re-solved many
   times). This supersedes the older 6-year 3 h ipm benchmark note.

---

## 8. W10 — Australian English throughout (`1782931` + `9bd3eea`)

Plan §330, two-phase two-commit.

### W10a — user-visible text, docstrings, comments (`1782931`)
Renamed (text only): `optimisation/optimise`, `co-optimised`, `analyse`, `behaviour`, `optimiser`,
`normalise`. The Optimisation tab label in `streamlit_app.py` is now **"⚙️ Optimisation"**. User
strings, module docstrings, and comments across `ppa/`, `ui/`, `scripts/` were converted; README was
already clean. 52 insertions/52 deletions across 17 files.

### W10b — identifiers + un-xfail the gate (`9bd3eea`)
Per the plan's explicit list (last, easy-to-revert commit):
- `ui/tabs/optimization.py` → `ui/tabs/optimisation.py` (streamlit_app import updated; git tracks
  the rename, 96 % similarity).
- `Scenario.optimize_capacity` → `optimise_capacity`.
- `ppa/sizing.py::optimize_capacities` → `optimise_capacities`.
- `state.set_optimized_sizes` → `set_optimised_sizes` (and `get_/has_optimised_sizes`,
  `OPTIMISED_SIZES_KEY`, `optimisation_result`).
- Session-state key `sf_optimize_capacity` → `sf_optimise_capacity`.
- `scenario_from_excel` now accepts **both** `optimise_capacity` and the legacy `optimize_capacity`
  Excel column key.
- `tests/test_spelling_en_au.py` **un-xfailed** — it is now a live gate. Allowlist extended with the
  two legitimate US-spelling survivors: pandas `.normalize(` (third-party API, never renamed) and
  the legacy `optimize_capacity` Excel-compat key.

> **Note on `OptimizationResult`:** it is a public class name (`ppa/results.py`), not in W10b's
> explicit identifier list, and the plan's test gate is lowercase-only — so it was **not** renamed
> (renaming a public class across `financials.py`, `multi_year.py`, `state.py`, etc. was judged out
> of scope and riskier than the plan's listed identifiers). Flagging for reviewer awareness.

---

## 9. Test suite evolution

| Stage | Result |
|---|---|
| W1 baseline | 186 passed |
| W2 scaffolding | 186 passed, 11 xfailed |
| After W12 | 233 passed, 1 skipped, 1 xfailed (W10) |
| After W16 | 233 passed, 1 skipped, 1 xfailed |
| After W13 | 233 passed, 1 skipped, 1 xfailed |
| After W14 (tsam installed) | 236 passed, 1 xfailed |
| After W15 | 236 passed, 1 xfailed |
| **Final (after W10b)** | **237 passed, 0 xfailed** |

The "1 skipped" that existed mid-series was the tsam `importorskip` before tsam was installed; it
became 3 passing tests in W14. The W10 xfail became a real passing gate in W10b.

---

## 10. Open items / notes for the reviewer

1. **PR body benchmark tables.** W12b merchant-share sweep, W14 sizing-method comparison, and W15
   solver numbers are recorded in commit messages and code comments. They should be copied into the
   PR body when the PR is opened (plan §4 DoD bullet). The W12b sweep was run on the toy inputs;
   re-running on a real case study before PR is recommended.
2. **Live NEM registry re-fetch is 403-blocked.** `first_power_date` (W7) is therefore not in the
   committed registry parquet; the SCADA fallback covers the tooltip. Needs a working network path
   to the registry to populate fully.
3. **AER download (W4)** likewise depends on an external service; the offline CSV → cache path is
   what is tested.
4. **`OptimizationResult` class name** not renamed (see §8) — plan's W10b list does not include it
   and the gate is lowercase-only.
5. **`highspy-extras`** is installed in this dev environment (for the W15 benchmark) but deliberately
   **not** a hard dependency; documented instead (tsam's `highspy<=1.15.0` pin vs extras 1.15.1).
   `pixi install`/`pip install -r requirements.txt` will not pull it.
6. **W2 UAT checklist** is manual; this machine cannot exercise Streamlit interactively.

---

## 11. How to verify

```bash
cd /home/hanan/projects/pypsa-ppa-nem
git log --oneline -18                      # the 17 work-item commits above HEAD
MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider   # 237 passed
MPLCONFIGDIR=$TMPDIR PYTHONPATH=. python3 scripts/bench_solver.py --quick --method full_hourly  # W15
MPLCONFIGDIR=$TMPDIR PYTHONPATH=. python3 scripts/bench_solver.py --hours 8760 --method tsam --periods 12  # W15 full
```

Environment note: `python3` is 3.14 without a venv; tests must run with
`MPLCONFIGDIR=$TMPDIR` (headless matplotlib) and `-p no:cacheprovider`. `tsam`,
`highspy==1.15.1` and `highspy-extras` were installed with
`--break-system-packages` for verification.

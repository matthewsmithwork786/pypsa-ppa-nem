# Implementation plan — Australian NEM cleanup, sizing overhaul & UX fixes

**Repo:** `/home/hanan/projects/pypsa-ppa-nem`
**Base commit:** `662b110` (`Add flexible NEM optimization period + sub-hourly resolution`), branch `main`, clean working tree.
**Baseline test state:** `186 passed` (`python3 -m pytest -q`, ~15 s).
**Audience:** the implementing model. Read §0 and §1 before touching code.

---

## 0. Ground rules

1. **Work on a branch.**
   ```bash
   cd /home/hanan/projects/pypsa-ppa-nem
   git checkout -b feature/au-nem-cleanup
   ```
   Commit **once per work item** (W1…W15) using the item ID in the subject, e.g.
   `W11: fix Excel Inputs-sheet note written as a formula`. This makes any single item
   revertable with `git revert <sha>` without unpicking the rest.

2. **Never break the existing 186 tests.** Run `python3 -m pytest -q -p no:cacheprovider`
   after every item (`-p no:cacheprovider` avoids the read-only `.pytest_cache` warning
   seen in this environment). If an existing test *should* change (e.g. European removal),
   change it deliberately in the same commit and say so in the message.

3. **Two root causes in this plan were reproduced and confirmed** (W11 Excel, W12a sizing
   links). Do not re-litigate them; do verify the fix.

4. **Environment notes**
   - Python is `python3` (3.14, user site-packages). There is no `python` alias, no active venv.
   - Set `MPLCONFIGDIR=$TMPDIR` when running anything that imports matplotlib to silence the cache warning.
   - `ppa/data/nem_data.py` and `ppa/data/aer_futures.py` have a **no-network import discipline**
     (no `requests`/`urllib`/`httpx`/`nemosis`/`socket`/`streamlit`). Preserve it. All network
     access lives in `scripts/`.

5. **Scope discipline.** Items are independent. If one is blocked (e.g. no network for AER),
   complete everything else and record the blocker in the final report — do not silently drop it.

---

## 1. Test suite (build this FIRST, in W2)

The reviewer needs to judge success without reading every diff. Deliver:

### 1.1 Automated — new test files

| File | Covers | Key assertions |
|---|---|---|
| `tests/test_excel_export_integrity.py` | W11 | Every `<f>` element in every `xl/worksheets/sheetN.xml` is a syntactically plausible formula (first char in `A-Z(+-@'0-9` **and** the source string was intentionally a formula); no cell whose Python value is a plain note/label is stored with `data_type == "f"`; explicit regression on the `development_start` note text |
| `tests/test_sizing_network.py` | W12 | Transport links are `p_nom_extendable` in sizing mode, fixed in dispatch mode, and the offtake link is never extendable; each link's `p_nom_opt` equals its peak flow; capital cost includes devex and uses `target_irr`; merchant haircut leaves negative-price hours undiscounted; a toy LP with cheap capex builds **more** than the (disabled) slider values |
| `tests/test_results_ranges.py` | W16 | `build_24h_avg` returns 24 rows (48 for 30-min data) with correct means; range filtering is endpoint-inclusive; every link reports `peak flow ≤ sized MW` |
| `tests/test_sizing_horizon.py` | W13 | `weather_cycle_years(15, 1, 1) == (1, note)`; `build_sizing_timeseries(..., n_sizing_years=1)` returns ≤ 8784 rows; a 15-year scenario with 1 cached weather year produces a **single-year** sizing timeseries |
| `tests/test_sizing_tsam.py` | W14 | `pytest.importorskip("tsam")`; clustering preserves annual energy of PV/wind/load within 2 % and preserves the load peak within 5 %; snapshot weightings sum to 8760 ± 1 |
| `tests/test_chosen_day.py` | W5, W9 | `Scenario().chosen_day` starts with `2025-`; `coerce_chosen_day()` returns an in-range day for an out-of-range input and is idempotent; `validate_scenario` no longer emits the `chosen_day … not present` error once coercion is applied |
| `tests/test_custom_template.py` | W8 | Default template = **8760 rows** hourly; `(2025-03-01 → 2025-03-31, 30 min)` = 1488 rows; 5-min full-year = 105 120 rows; timestamps strictly increasing, all within 2025; round-trips through `data_loader.load_custom_upload` without error |
| `tests/test_nem_map_tooltip.py` | W7 | Tooltip string contains the CUF as `NN.N%` and a first-power date (or the documented `—` fallback); tooltip stays unique per DUID; `_duid_from_tooltip` round-trips the new format |
| `tests/test_aer_counterfactual.py` | W4 | With a fixture AER parquet, the seeded `cal_forward_price` equals the quarterly average and `cal_forward_source == "aer_indicative"`; no string in `ppa/`+`ui/` mentions `EUR`/`€`/`ENTSO`/`CAL Y+1` in counterfactual code paths |
| `tests/test_no_european_paths.py` | W6 | `ppa.data.european_data`/`entsoe_client`/`renewables_ninja`/`bidding_zones` are gone; `DATA_SOURCES` has no `"european"`; `streamlit_app.py` does not import `data_download`; `Scenario().data_source` is a NEM source |
| `tests/test_spelling_en_au.py` | W3/W10 | Repo-wide scan (`ppa/`, `ui/`, `scripts/`, `streamlit_app.py`, `README.md`) for the American-spelling regex list, with an explicit allowlist for third-party APIs (see W10). Fails with file:line for each hit |

Reuse the existing conventions: pure helpers at module level, `pytest.importorskip("streamlit")`
for UI modules, synthetic cache fixtures under `tests/fixtures/` (see `nem_fixtures.py`).
Add `tests/fixtures/aer_fixtures.py` usage rather than new parquet blobs in git where possible.

### 1.2 Manual — reviewer UAT checklist

Add `docs/UAT_checklist.md` with a tick-box list the reviewer runs against
`streamlit run streamlit_app.py`:

1. Tab bar reads: Welcome · 1. Case Setup · **2. Get Data** (the plant map) · 2b. Custom Data ·
   3. Optimisation · 4. Results · 5. Financial Model · 6. Sensitivity Analysis · 7. HELP.
   No "European"/"NEM Plant Map"/"Download Data" tab remains.
2. Case Setup has **no** "Project Locations & Market Zone" section, and the transmission-cost
   input is still reachable (moved, not deleted).
3. Hovering a map marker shows station, DUID, MW, region, **2025 CUF %**, **first power** date.
4. Custom Data: pick 1 Mar–31 Mar 2025 + 30 min → downloaded CSV has 1488 rows; default
   selection yields 8760 rows.
5. Optimisation → "Period reference optimisation": pressing **Run** works immediately after a
   fresh load with no `chosen_day … is not present` error, for both the Calendar-month and
   Custom-range modes.
6. Results → Actual hourly supply mix has a working date-range control **and** an
   "Average 24 h profile" tab; the same for Market spot price and BESS SoC. The sized
   connection (link) MW and their utilisation are shown in the Optimisation banner and the
   Results statistics table.
7. Financial Model → export XLSX → opens in Excel with **no repair dialog**.
8. Capacity sizing on a 15-year scenario: the status line reports a 1-year sizing LP, the
   sizing phase itself completes in the logged time, and the sized MW are no longer pinned
   to the slider values.
9. Every visible string uses Australian spelling.

---

## 2. Work items

### W1 — Branch + baseline
Create the branch, run the suite, record the baseline (`186 passed`) in the commit message.

---

### W2 — Test scaffolding
Create the files in §1.1 as **failing/xfail-marked** tests where the behaviour does not exist yet
(`@pytest.mark.xfail(strict=True, reason="W12")`), then flip them as each item lands. This gives
the reviewer a running score. Commit the UAT checklist here too.

---

### W3 — Case Setup: remove Project locations & Market Zone

**File:** `ui/scenario_form.py` (lines ~259–406), `ui/tabs/case_study.py`, `ui/tabs/optimization.py`.

- Delete the entire `with st.expander("Project Locations & Market Zone", …)` block: the
  lat/lon number inputs, the PV/Wind "own location" toggles, the bidding-zone selectbox, the
  folium click-to-place map and its `sf_loc_map` / `sf_map_target` / `_sf_handled_click`
  session-state handling.
- **Do not lose the transmission-cost input** — it currently lives inside that expander.
  Move `transmission_cost_aud_mwh` into the **"Market interaction"** expander.
- In the returned `dataclasses.replace(...)`, drop `lat`, `lon`, `pv_lat`, `pv_lon`,
  `wind_lat`, `wind_lon`, `bidding_zone_override` (they keep their dataclass defaults).
- Remove the location/zone lines from `_render_scenario_summary` in `ui/tabs/optimization.py`
  (the `Offtaker: …°N …°E — zone …`, `PV site`, `Wind site` markdown lines).
- Update the intro copy in `ui/tabs/case_study.py` (`render()`) which currently promises
  "including **project location**".
- Field removal from `Scenario` itself happens in **W6** (they are only meaningful for the
  European path). Keep them in the dataclass until then so this commit stays small.

---

### W4 — AER indicative hedge prices; remove the European forward

The reader/UI plumbing already exists (`ppa/data/aer_futures.py`, `scripts/fetch_aer_futures.py`,
`ui/scenario_form.py` "AER indicative hedge price" block, `tests/test_aer_futures.py`).
**What is missing is the cached data file** — `data/cache/nem/hedge/` does not exist.

1. **Acquire the data.**
   ```bash
   pip install -r scripts/requirements-acquisition.txt
   python3 scripts/fetch_aer_futures.py --year 2025
   python3 scripts/fetch_aer_futures.py --year 2026
   ```
   Output goes to `data/cache/nem/hedge/aer_base_futures_<year>.parquet`
   (columns `region, quarter_label, product, price_aud_mwh, as_at_date`).
   Source page: AER *Quarterly base futures prices and volume traded*
   (`https://www.aer.gov.au/wholesale-markets/wholesale-statistics/quarterly-base-futures-prices-and-volume-traded`).
   **Blocker note:** `aer.gov.au` is not on this environment's network allowlist. If the download
   fails, run it outside the sandbox (or have the user run it) and copy the parquet in. Do not
   fabricate the file; if it cannot be obtained, land everything else and flag it.
2. **Equivalent futures sources** — document these in `scripts/README.md` as fallbacks, and
   implement one only if AER fails:
   - **ASX Energy 24 electricity futures** (base-load quarterly & calendar-year settlement
     prices) — the actual traded instrument AER's series is derived from.
   - **AEMO Quarterly Energy Dynamics** — published quarterly averages, good for cross-checking.
   - **OpenElectricity API** (already used for the plant registry) — spot/derived series.
   Keep the same normalised parquet schema whichever source is used, so `aer_futures.py`
   needs no change.
3. **Make AER the default, not a manual opt-in.** In `ui/scenario_form.py`, when a futures
   cache exists for `nem_year` and the user has not manually edited the price, seed
   `cal_forward_price` from `quarterly_average(region=nem_price_region, quarters=all)` with
   `cal_forward_source = "aer_indicative"` on first render (reuse `_apply_pending_aer`
   /`_seed_aer_applied_from_scenario` — they already handle provenance correctly).
4. **De-Europeanise the language.** `cal_forward_price` / "CAL Y+1" is EEX/European
   terminology. Rename the *user-facing labels* (and the counterfactual chart/table labels in
   `ui/charts.py`, `ui/tabs/results_deep_dive.py`) to
   **"Base futures — calendar year (A$/MWh)"** and the strategy row to
   **"Base futures hedge"**. Keep the dataclass field names for now; rename them in W10 if you
   do the identifier pass. Remove any `EUR`/`€`/ENTSO-E wording from the counterfactual copy.
   `ppa/counterfactuals.py` logic itself is currency-agnostic and needs no change.
5. `cal_hedge_fraction` stays a pure user input — the module docstring in `aer_futures.py`
   is explicit about this. Do not wire AER data into it.

---

### W5 — Reference-day default must be 2025

- `ppa/scenario.py`: `chosen_day: str = "2023-03-15"` → **`"2025-03-15"`** (matches the
  default reference period, which is March of `nem_year=2025`).
- `ppa/scenario.py::scenario_from_excel`: same default in
  `str(params.get("chosen_day", "2023-03-15"))` → `"2025-03-15"`.
- Grep for other `2023-`/`2024-` literals in `ppa/`, `ui/`, `data/PPA_scenario_definition.xlsx`
  helper text, and the case-study overrides (none currently set `chosen_day` — leave it that way,
  the base default now covers them).
- Related fallback: `ui/scenario_form.py` and `ui/tabs/results_deep_dive.py` both use a bare
  `index = 14` when `chosen_day` is not in `available_days`. Replace with the W9 coercion helper.

---

### W6 — Delete the Get Data tab; rename the NEM map tab to "Get Data"; remove the European path

This is the largest mechanical item. Do it as one commit; the test in `tests/test_no_european_paths.py`
is the acceptance gate.

**Delete:**
- `ui/tabs/data_download.py`
- `ppa/data/european_data.py`, `ppa/data/entsoe_client.py`, `ppa/data/renewables_ninja.py`,
  `ppa/data/bidding_zones.py`
- `data/cache/entsoe/`, `data/cache/renewables_ninja/`
- `entsoe-py` from `pixi.toml` `[pypi-dependencies]`

**`streamlit_app.py`:** drop the `data_download` import and its `i += 1` block; retitle tabs to
`"| 2. 📡 Get Data"` for `nem_map` and renumber `custom_data` to `"| 2b. 📤 Custom Data"`.
Keep the `tabs[i].open` pattern intact — indices shift by one.

**`ui/tabs/nem_map.py`:** `st.title("🗺️ NEM Plant Map")` → `st.title("📡 Get Data")`; rewrite the
subtitle to drop "instead of the European renewables.ninja profiles"; delete the
**"↩️ Revert to European data"** button (replace the two-button row with a single full-width
"Use these plants").

**`ppa/scenario.py`:**
- `DATA_SOURCES = ("nem_map", "nem_default", "custom_csv")`
- `data_source: str = "nem_default"`
- Delete `lat/lon/pv_lat/pv_lon/wind_lat/wind_lon/bidding_zone_override`, the
  `pv_location`/`wind_location`/`bidding_zone` properties, and the `bidding_zone_override`
  branch in `validate_scenario`.
- `default_data_source()` — the "european upgrades to nem_default" logic becomes moot; delete
  the function and its call site, or reduce it to a pass-through. Read its long docstring first:
  the invariant it protects (**never let a NEM source run with empty DUIDs**) must survive in
  `validate_scenario` / `nem_generation_ready`, which already enforce it.

**`ui/scenario_form.py`:** delete the `"Market data source"` radio (`european` vs `nem_default`)
and the `_sf_data_source_touched` machinery; keep the NEM region + year selectboxes.

**`ui/tabs/optimization.py`:** delete `_cached_reference_ts`, the European branch of
`_get_timeseries`, the European branch of `_render_data_status`, the European branch of
`_run_simulation`, and the `_single_day_title`/`_single_day_caption` European variant.
`_get_timeseries` then handles only `custom_csv` (NEM is already handled separately in `render()`).

**`ppa/data_loader.py`:** `REQUIRED_COLUMNS` mentions `ts_NSWPrice` and there is a legacy
`data/march_2025_pypsa_timeseries.csv` path — check whether `load_timeseries`/`find_default_csv`
still have callers after the above; delete if orphaned, keep if the Custom Data path uses them.

**Tests to update:** `test_scenario_nem.py`, `test_custom_upload.py`, `test_nem_map_tab.py`,
`test_financial_model_phase2.py`, `test_custom_multi_year.py` all reference `"european"`.

**`ui/tabs/welcome.py` / `introduction.py` / `README.md`:** rewrite the European framing.

---

### W7 — Map hover: 2025 CUF % and date of first power

**CUF** is already computed: `nem_data.list_eligible_plants()` returns a `mean_cf` column
(mean of the clipped 5-min CF series). Use `mean_cf * 100` and label it **"2025 CUF"**.
If you want the stricter definition (energy ÷ nameplate × hours-in-year), compute it in
`scada_summary` as `scada.sum() * (5/60) / (capacity_mw * expected_hours(year))` and add a
`cuf` field — prefer this, and say which definition you used in the tooltip help text.

**First power** is *not* in the registry (`duid, station_name, region, fuel_tech,
capacity_registered_mw, lat, lon, status` — 187 rows). Two-tier approach:

1. **Preferred:** extend `scripts/fetch_nem_plant_registry.py` to carry a `first_power_date`
   from the OpenElectricity facilities API (look for `data_first_seen` / commencement-date style
   fields in the unit records — the script already has a `_first_present(record, candidates)`
   helper for exactly this kind of key-name tolerance). Re-run the script and refresh
   `data/cache/nem/registry/nem_plant_registry.parquet`.
2. **Fallback (works offline, do this regardless):** derive from the cached SCADA —
   first timestamp with output > 1 % of nameplate sustained for ≥ 6 consecutive 5-min intervals.
   Because the SCADA cache is 2025-only, a plant commissioned earlier will return 1 Jan 2025.
   So **label the fallback differently**: `"first 2025 output"` vs `"first power"`, and show `—`
   when neither is available.

**Back-compat:** `nem_data.REGISTRY_COLUMNS` is a *required* column list that raises on missing
columns. Add `first_power_date` as an **optional** column (`if "first_power_date" in df.columns`),
never to `REGISTRY_COLUMNS`, so an old cached parquet keeps working.

**`ui/tabs/nem_map.py::_tooltip`** — extend to
`"{station} [{duid}] · {cap:.0f} MW · {region} · CUF {cuf:.1f}% · 1st power {date}"`.
`_duid_from_tooltip` does an exact string match against `_tooltip(row)`, so it keeps working —
but the tooltip must stay **unique per DUID** and deterministic across reruns
(`f"{nan}"` formatting will break that — guard NaNs explicitly).

---

### W8 — Custom Data: date range + periodicity → generated template

**`ppa/data_loader.py::build_upload_template`** — change the signature from
`(hours=48, start="2025-01-01 00:00", load_mw=100.0)` to
`(start="2025-01-01", end="2025-12-31", freq_minutes=60, load_mw=100.0)`, building
`pd.date_range(start, end + 1 day, freq=f"{freq_minutes}min", inclusive="left")`.
Keep the existing deterministic (no-RNG) PV/wind/price shapes; make the PV shape use
`minute-of-day` so it is correct at 5-min resolution (the current code already computes
`minutes_of_day`, so this is mostly free).
**Default must produce 8760 rows** (full-year 2025 hourly) — update `TEMPLATE_HOURS`
accordingly or replace it with `TEMPLATE_START`/`TEMPLATE_END` constants.

**`ui/tabs/custom_data.py::render()`** — before the download button, add:
- `st.date_input("Date range (within 2025)", value=(2025-01-01, 2025-12-31), min_value=2025-01-01, max_value=2025-12-31)`
  (handle the mid-selection single-date tuple case exactly as
  `ui/tabs/optimization.py::_render_nem_period_controls` already does).
- `st.selectbox("Periodicity", ["1 hour", "30 minutes", "5 minutes"], index=0)` mapping to
  `{60, 30, 5}` — reuse/lift `_NEM_RESOLUTION_MINUTES` into a shared constant rather than
  duplicating it.
- A caption showing the resulting row count, and a `st.warning` above ~50 000 rows
  (5-min full year = **105 120 rows**, ~8 MB CSV — usable but slow in the browser).
- The generated filename should encode the selection:
  `ppa_template_2025-03-01_2025-03-31_30min.csv`.
- Keep the 5-row `st.dataframe` preview.

Downstream is already fine: `describe_custom_timeseries` flags sub-hourly data and
`prepare_custom_timeseries` resamples to hourly for dispatch.

---

### W9 — Fix "chosen_day … is not present" in the Optimisation tab

**Root cause.** `ui/tabs/optimization.py::render()` builds the NEM period timeseries from the
user's period picker (default: **March** `nem_year`), then calls
`validate_scenario(s, available_days=get_available_days(ts))`. `Scenario.chosen_day` is a
*separate*, sticky value set in Case Setup's "Reference day selection" (and by the case studies /
custom uploads). Any mismatch — a default of `2023-03-15`, or a stale `2025-02-15` after the user
picks a March window — hard-blocks the run with
`Fix the above issues in Case Study Definition before running.`

**Fix (do all four):**
1. Add `ppa/data_loader.py::coerce_chosen_day(ts, chosen_day) -> str` — returns `chosen_day` if
   present in `get_available_days(ts)`, else the **nearest** available day (parse both as dates;
   fall back to the middle day if `chosen_day` is unparseable). Pure, unit-testable.
2. In `ui/tabs/optimization.py`, after loading `ts`, coerce **before** validating, and surface it
   as an `st.info` ("Reference day moved to *2025-03-01* — *2025-02-15* is outside the selected
   period") rather than an error. Pass the coerced scenario to `build_network`/`solve`.
3. Move the reference-day selectbox **into the period expander**, immediately under the
   period/resolution controls, so day and period can never diverge. Delete the
   "Reference day selection" expander from `ui/scenario_form.py` (or leave it read-only showing
   the coerced value).
4. Drop `chosen_day` from the blocking `validate_scenario` errors (keep the check available for
   direct API callers, but the UI must not block on it).

Also fix the two `index = 14` fallbacks (`ui/scenario_form.py:599`,
`ui/tabs/results_deep_dive.py:302`) — they crash-by-luck on a short period with < 15 days.

---

### W10 — Australian English throughout

Rule: **rename our own strings and identifiers; never rename third-party APIs.**

**Must NOT be touched** (allowlist for `tests/test_spelling_en_au.py`):
`pypsa` `n.optimize`, `n.optimize.create_model`, `optimize.solve_model`, `p_nom_opt`,
`pypsa.options.params.optimize.*`, `linopy` attribute names, `matplotlib`/`plotly` kwargs
(`color`, `marker_color`, `line_color`, `fillcolor`, `colorway`), CSS/HTML attributes
(`color:`, `background-color`), `scipy.optimize`, and `str.center`.

**Two-phase, two commits:**

- **W10a — user-visible text (mandatory).** Every `st.title/markdown/caption/info/warning/error/
  help=/label` string, every docstring and comment, `README.md`, `scripts/README.md`, the tab
  labels in `streamlit_app.py`. `optimization → optimisation`, `optimize → optimise`,
  `co-optimized → co-optimised`, `analyze → analyse`, `normalize → normalise`,
  `maximize/minimize → maximise/minimise`, `behavior → behaviour`, `customise`, `summarise`,
  `organise`, `labelled`, `modelled` (already correct in places), `fulfilment`.
  Note `color` inside f-string HTML/CSS and plotly kwargs stays.

- **W10b — identifiers (do last, separate commit, easy to revert).**
  `ui/tabs/optimization.py` → `ui/tabs/optimisation.py` (update `streamlit_app.py`);
  `Scenario.optimize_capacity` → `optimise_capacity`; `ppa/sizing.py::optimize_capacities` →
  `optimise_capacities`; `state.set_optimized_sizes` → `set_optimised_sizes`; session-state keys
  `sf_optimize_capacity` → `sf_optimise_capacity`.
  **Watch out:** `Scenario` is serialised via `dataclasses.asdict` into subprocesses
  (`ppa/sizing.py::run_sizing_subprocess`, `ppa/multi_year.py`) and read back with
  `Scenario(**fields)` — a rename is safe there because both ends change together, but
  `scenario_from_excel` reads **Excel column keys** and `data/PPA_scenario_definition.xlsx`
  may contain `optimize_capacity`. Accept both spellings in `scenario_from_excel`.

---

### W11 — Excel export corruption *(root cause confirmed)*

**Reproduced.** `xl/worksheets/sheet2.xml` is the **Inputs** sheet (workbook order:
`Outputs, Inputs, Energy, Model, Notes`, then the Hourly sheets). It contains exactly one
`<f>` element:

```xml
<f> FID; devex bullet and construction both start here</f>
```

That is the *note* string from `ppa/financial_model_excel.py:145`:

```python
field("Development start period", "development_start", p.development_start, "period",
      "= FID; devex bullet and construction both start here")
```

openpyxl treats any string starting with `=` as a formula, so Excel finds an unparseable
formula and reports exactly the user's error:
`Removed Records: Formula from /xl/worksheets/sheet2.xml part`.

**Fix:**
1. Change the note text to `"FID — devex bullet and construction both start here"` (no leading `=`).
2. Add a guard so this cannot recur — a `_text(value)` helper used by every label/note/unit write
   in `_write_inputs`, `_write_energy`, `_write_model`, `_write_outputs`, `_write_notes`:
   ```python
   def _text(v):
       """Write a string as literal text, never as a formula."""
       return f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v
   ```
   (Or set `cell.data_type = "s"` after assignment — pick one and apply it consistently.)
3. Regression test per §1.1 `tests/test_excel_export_integrity.py`. Verify by regenerating the
   workbook and asserting `re.findall(r"<f>(.*?)</f>", sheet2_xml)` contains only intended formulas.
4. While there: `_write_notes` and `_write_model` write long literal strings — re-scan all sheets
   for other leading-`=`/`-`/`+`/`@` text (the automated test does this).

---

### W12 — Why capacity sizing under-builds and returns ~2 % IRR

Investigate and fix in this order. **(a) is a confirmed bug; the rest are design issues** — fix
(a)+(d)+(c) at minimum, and expose (b) as an option.

**(a) CONFIRMED BUG — transport links are hard-capped at the slider MW in sizing mode.**
`ppa/network.py:158-160` computes
```python
wind_link_mw = build_cap_sum if sizing else s.onsw_mw
pvbess_link_mw = build_cap_sum if sizing else (s.pv_mw + s.effective_bess_mw)
sell_link_mw   = build_cap_sum if sizing else s.maxsell_mw
```
…and then **never uses them** — `link_defs` (lines 162-170) passes `s.onsw_mw`,
`s.pv_mw + s.effective_bess_mw`, `s.maxsell_mw` directly. Verified on a toy sizing network:
```
OnshoreWind_to_IPPGeneration  p_nom=250.0   # the disabled slider value
PVBESS_to_IPPGeneration       p_nom=210.0
IPPGen_to_SellToMarket        p_nom=460.0
Gen_OnshoreWind p_nom_max=1000  Gen_PV p_nom_max=1000
```
So the optimiser may "build" up to 1000 MW but can only ever *deliver* 250/210 MW — any
capacity beyond the link cap is worthless, and the LP therefore never builds it. **This alone
explains a severely undersized portfolio.**

**Fix — make the links extendable investment variables** (preferred over pinning them to
`build_cap_sum`, because it turns connection capacity into a reported model output):

```python
n.add("Link", name, bus0=..., bus1=...,
      p_nom=0.0 if sizing else p_nom_fixed,
      p_nom_extendable=sizing,
      p_nom_max=s.grid_connection_max_mw if sizing else float("inf"),
      capital_cost=link_cc if sizing else 0.0,
      ...)
```

Three things to get right:

- **Degeneracy.** With `capital_cost = 0`, any `p_nom ≥ peak flow` is optimal, so the reported
  MW would be arbitrary (whatever vertex HiGHS lands on). Give each link a **strictly positive**
  capital cost so `p_nom_opt` is pinned to the actual peak flow. Use a real number rather than an
  epsilon: `ProjectFinanceInputs` already carries `onsw_connection_cost`, `pv_connection_cost`,
  `bess_connection_cost` (A$M/MW) — reuse those, annualised the same way as generation capex
  (`× (crf + opex_rate) × horizon_years`). That makes connection cost a genuine part of the
  sizing trade-off *and* aligns the LP with the financial model (see (c)). If you cannot wire the
  connection costs cleanly, fall back to an epsilon (~A$1/MW/yr) and say so — but then the
  reported link MW is "peak flow", not "economically sized connection".
- **The offtake link must stay fixed** at `s.ppaload_mw`. It carries the PPA revenue
  (`marginal_cost = transmission - ppa_price`) and is a contractual quantity, not an investment
  decision. Only the three transport links (`OnshoreWind_to_IPPGeneration`,
  `PVBESS_to_IPPGeneration`, `IPPGen_to_SellToMarket`) become extendable.
  `BuyFromMarket_to_IPPGeneration` stays at `s.maxbuy_mw` (a contract cap).
- **New scenario field** `grid_connection_max_mw: float = float("inf")` (UI: "Grid connection
  limit (MW)", blank = unlimited) — a real constraint in NEM projects and a natural cap now that
  the links are free to grow.
- **Carry the result through.** `SizedCapacities` gains `wind_link_mw`, `pvbess_link_mw`,
  `sell_link_mw`; `apply_sizing` writes them into the scenario so the **dispatch** simulation uses
  the sized connection MW instead of re-deriving link caps from nameplate. Without this the
  dispatch run is looser than the LP assumed (harmless — never infeasible — but the two phases
  would disagree about connection capacity, and the reported MW would not be what was simulated).

Assertions for `tests/test_sizing_network.py`: transport links are `p_nom_extendable` in sizing
mode and fixed in dispatch mode; the offtake link is never extendable; `p_nom_opt` for each link
equals the peak flow on that link to within tolerance; a toy LP with cheap capex builds more than
the (disabled) slider values.

**(b) Merchant revenue is zeroed in sizing but earned in the simulation.**
`ppa/network.py:116` sets `Gen_SellToMarket.marginal_cost = 0.0` when sizing (deliberate — the
docstring explains it prevents build-to-the-cap merchant behaviour). But `run_multi_year` +
`ppa/financials.py` *do* credit merchant sales, so the sizing LP is optimising a strictly poorer
objective than the one the IRR is later measured against → systematic under-build.

**Fix: `Scenario.sizing_merchant_value_share: float = 0.5`** — i.e. credit merchant sales in the
sizing LP at **50 % of historic spot**, as a haircut for capture-price cannibalisation, MLF and
curtailment risk. Two implementation details that matter:

- **Apply the haircut to positive prices only.** A flat `price × 0.5` also halves the *cost* of
  selling into negative prices, which biases the LP toward dumping energy at negative spot. NEM
  midday negative prices are common in the 2025 data, so this is not hypothetical. Use:
  ```python
  merch_price = ts["ts_MktPrice"].where(ts["ts_MktPrice"] <= 0,
                                        ts["ts_MktPrice"] * share)
  marginal_cost = -(merch_price - s.market_spread)
  ```
  Negative hours keep their full disincentive, so the LP still curtails rather than sells.
- **`share` is a conservatism dial, not the fix.** Any `share > 0` means that once capture price
  exceeds marginal cost the LP builds to whichever cap binds — so `max_build_*` and the new
  `grid_connection_max_mw` become the real sizing decision. Report which cap binds (see (e)), and
  run the sensitivity sweep below before settling on a default.

**Sweep to run and record in the PR body:** `share ∈ {0, 0.25, 0.5, 0.75, 1.0}` on one case study,
reporting sized MW, delivery share, and the full-simulation IRR for each. That shows whether 0.5
is the right default or merely a plausible one — 0.5 is a reasonable prior, not a derived value.

**(c) The LP's cost basis ≠ the financial model's.** `build_network` annualises with
`capex × (crf(discount_rate) + opex_rate)` only. The financial model additionally charges
devex (`devex_pct_of_capex`, default **10 %**), construction timing, tax, degradation and
replacement. So the LP sizes to *its* breakeven — where marginal build cost ≈ the PPA tariff —
and the fuller model then scores that same portfolio well below the WACC. **An IRR near
`discount_rate` minus the omitted costs is the expected outcome of the current formulation, and
2 % is consistent with it.** Fix: in sizing mode use
`capex × (1 + devex_pct_of_capex)` and `crf` evaluated at **`target_irr`** (default 10 %), not
`discount_rate`, so the LP only builds capacity that clears the hurdle rate. Document the change
in the sizing docstring.

**(d) Allowed shortfall — NO CHANGE REQUIRED. The current formulation is already correct.**
*(An earlier draft of this plan called for pricing shortfall at the forgone PPA margin. That was
wrong — do not do it. Recorded here so the idea is not re-proposed.)*

`Gen_AllowedShortfall` (`marginal_cost = 0.001`, capped at `(1 − required_delivery_share) × load`)
and `Gen_Penalty` (`marginal_cost = ppa_price × pen_mult`) both sit on **`Bus_PPAOfftake`**
(verified: `ppa/network.py:121,130`). Load served from either generator therefore **bypasses
`IPPGen_to_PPAOfftake`**, the only link carrying PPA revenue
(`marginal_cost = transmission_cost − ppa_price`, i.e. −A$100/MWh at defaults). So a shortfall
MWh **already** forgoes the full PPA tariff in the objective — the opportunity cost is structural,
not missing. Adding `+ ppa_price` to the shortfall marginal cost would **double-count** it and
push the LP to over-build. Leave the `0.001` epsilon (it only breaks ties between shortfall and
penalty) exactly as it is.

**(e) The penalty is expensive — and doubly so. Not a cause of under-building.**
At the default `pen_mult = 1.5` on a A$90 tariff, penalty energy costs **A$135/MWh** *and* earns
no PPA revenue (same bus-bypass as above), so its true objective cost is A$135/MWh against a
delivered MWh's net cost of `LCOE − 90`. The LP will pay penalties only in genuinely extreme
hours. The order of the escape valves it actually uses is: **deliver → allowed shortfall (up to
the contractual band, revenue forgone) → penalty (last resort)** — which is the correct
merit order for a PPA.

What *does* remain is that the project is genuinely marginal at these cost assumptions:
`wind_capex_per_kw = 2900` A$/kW with `crf(8 %, 30 y) ≈ 0.0888` and `opex_rate = 0.02` gives
≈ A$316 k/MW/yr, or ≈ **A$103/MWh** at a 35 % CF — *above* the A$90–105 tariffs in the case
studies. Combined with (a) and (c), that is the honest explanation for a small build. Do not
"fix" it by hiding it — add a **sizing diagnostics expander** to the Optimisation tab showing,
per technology: annualised A$/MW/yr, achieved CF from the loaded profile, implied LCOE, and the
tariff / penalty price / average spot for comparison; plus which constraints bind (`p_nom_opt`
vs `p_nom_max` per generator, each link's `p_nom_opt` vs `grid_connection_max_mw`, and whether
the shortfall and market-buy constraints are tight). That turns "strange results" into an
explainable answer.

**(f) One weather year = overfitting.** With a single cached 2025 SCADA year the sized portfolio
is tuned to 2025 weather. Note it in the UI; fetching more SCADA years is out of scope here but
worth a `TODO` and a line in the README.

**Acceptance:** with a toy scenario (cheap capex, generous caps) the sizing LP must build
*more* than the slider values; with the Corporate PPA case study the resulting IRR must be
reported alongside the diagnostics so the reviewer can see *why* it lands where it does.
Do not target a specific IRR number — target explainability plus the (a)/(c)/(d) fixes.

---

### W13 — Verify the 1-year sizing horizon claim

**The message is accurate; the runtime complaint has a different cause.**
`weather_cycle_years(15, n_weather=1, n_price=1)` returns `(1, note)` because
`math.lcm(1,1) = 1`, so `build_sizing_timeseries` is called with `n_sizing_years=1` and the
sizing LP really is one year. The extra time in a 15-year run comes from
`ppa/multi_year.py::run_multi_year` afterwards: **15 full hourly dispatch solves** (which the
note already says will happen). Actions:

1. **Prove it.** Add `tests/test_sizing_horizon.py` per §1.1, and instrument
   `ui/tabs/optimization.py::_run_simulation` to log/display wall-clock for the sizing phase and
   the dispatch phase separately (`time.monotonic()` around each; the sizing heartbeat already
   tracks elapsed — surface the final number in the success message).
2. **Fix the messaging** so it can't be read as "the whole run is 1 year": append
   *"Sizing LP: 1 year. The subsequent hourly dispatch simulation still solves all 15 years —
   that is where most of the runtime goes."*
3. Check `clamp_sizing_years` isn't silently doing something surprising on the user's machine
   (it reads available RAM via `_available_memory_mb`); log the value it computed.
4. If the sizing LP itself is genuinely slow (report the measured seconds), that is a solver
   question → W15.

---

### W14 — Better sizing representation via `tsam`

Goal, in the user's words: *typical periods at hourly resolution beat everything at 3-hourly.*

1. Add optional dependency `tsam` (`pixi.toml` `[pypi-dependencies]`, `requirements.txt`,
   guarded import — the app must still run without it).
2. New module `ppa/sizing_tsam.py`:
   - `cluster_typical_periods(ts, n_periods=12, hours_per_period=24, extreme_periods=True)`
     wrapping `tsam.timeseriesaggregation.TimeSeriesAggregation` over
     `ts_PVGen, ts_WindGen, ts_MktPrice, ppaload_mw`.
   - Use `extremePeriodMethod="new_cluster_center"` with `addPeakMax=["ppaload_mw"]` and
     `addPeakMin=["ts_PVGen","ts_WindGen"]` so peak-load and dark-lull periods survive
     clustering — otherwise the sized fleet under-covers exactly the hours that matter.
   - Return `(clustered_ts, weights)` where `weights` are the occurrence counts; feed them to
     `n.snapshot_weightings` (must sum to ≈ 8760).
3. `ppa/network.py::build_network` already accepts a scalar `resolution_h` for weightings —
   generalise it to accept a **Series** of per-snapshot weights, keeping the scalar path intact.
4. **BESS caveat — call it out in the docstring and the UI help.** With typical *days* and
   `cyclic_state_of_charge=True`, the battery cycles within each representative day: fine for a
   2–4 h BESS, wrong for multi-day storage. If longer durations are wanted, either use
   `hours_per_period=168` (typical weeks, fewer clusters) or implement inter-period SoC linking;
   document the limitation rather than silently mis-modelling it.
5. **UI:** replace the "Sizing LP resolution (h)" selectbox in `ui/scenario_form.py` with a
   sizing-method radio:
   - `Typical days (tsam)` — default, `n_periods` slider (4–36), hourly resolution inside each day
   - `Full year hourly` — exact, slowest
   - `Coarse resolution (legacy)` — the existing 1/2/3/4/6 h selectbox, kept for comparison
   Persist as `Scenario.sizing_method: str = "tsam"` + `sizing_n_periods: int = 12`
   (keep `sizing_resolution_h` for the legacy path). Update `validate_scenario`.
6. **Validation step (important for trust):** after sizing, re-simulate the sized portfolio on the
   *full hourly year* (this already happens) and report `delivery share (sizing estimate)` vs
   `delivery share (full simulation)`. A large gap means the clustering dropped something —
   show it in the diagnostics expander from W12(e).
7. Benchmark and record in the PR body: solve time and sized MW for
   `{full hourly, tsam 8/12/24 days, legacy 3 h}` on one case study.

---

### W15 — HiGHS HiPO: availability and when to use it

**Findings (verified in this environment):**
- `highspy 1.15.1` is installed (pinned in `requirements.txt` and `pixi.toml`).
- HiPO is the new interior-point LP/QP solver added in HiGHS **v1.12**, extended to convex QP in
  v1.14, and **exposed to Python in v1.15**.
- It is **not available out of the box**. Running
  `Highs().setOptionValue("solver", "hipo")` here returns:
  ```
  ERROR: The HiPO solver was requested via the "solver" option.
  The following features are unavailable: amd, blas, metis, rcm
  → HighsStatus.kError
  ```
  Those dependencies ship in the separate **`highspy-extras`** wheel
  (`pip install highspy-extras`, or `pip install highspy[extras]`; also on conda-forge).
  Licence note: the extras are Apache-2.0 while HiGHS core is MIT.
- Upstream guidance: *"HiPO can enhance performance on many large problem instances. It is not
  very well suited for smaller or easier LPs."*

**Actions:**
1. Add `highspy-extras` as an **optional** dependency (document it; do not hard-require it — the
   app must keep working on the default simplex).
2. Plumb solver options through: `ppa/solver.py::solve(..., solver_options: dict | None = None)`
   → `n.optimize.solve_model(solver_name=..., io_api="direct", **solver_options)`. Default `{}`.
3. Add `scripts/bench_solver.py` that builds the sizing LP for a given scenario and times
   `{default (dual simplex), "solver": "ipm", "solver": "hipo" (+ run_crossover on/off)}`,
   printing rows/cols/nonzeros and wall-clock.
4. **Decision rule** to apply and record: adopt HiPO for the *sizing* LP only if it beats dual
   simplex by > 25 % on the full-year hourly LP (≈ 8760 snapshots). Note the existing comment in
   `ppa/solver.py:68-71`: parallel IPM was already benchmarked on the 6-year 3 h sizing LP and
   **lost** (~180 s vs ~80 s). If W14 lands, the typical-day LP gets *smaller*, which pushes
   further toward simplex — measure, don't assume. Keep dispatch solves on simplex (they are
   small and re-solved many times).
5. Record the numbers in the PR body; update the comment block in `ppa/solver.py` with the new
   measurements either way.

Sources:
[HiPO in Python — HiGHS docs](https://ergo-code.github.io/HiGHS/stable/interfaces/python/hipo-in-python/) ·
[HiGHS releases](https://github.com/ERGO-Code/HiGHS/releases) ·
[highspy on PyPI](https://pypi.org/project/highspy/)

---

### W16 — Results: date-range selection, 24 h average tabs, and connection MW

**File:** `ui/tabs/results_deep_dive.py` (`_render_dispatch_section`, both the multi-year and
single-day callers), `ui/charts.py`, `ppa/results.py`.

**16.1 — Date-range selection for the dispatch charts.**
`_render_dispatch_section(result, s, chosen_day)` currently slices a **single day**
(`supply_mix.index.strftime("%Y-%m-%d") == chosen_day`) for all three charts. Change it to take a
`(start, end)` range:
- Add a range control above the chart tabs — `st.slider` over the result's own datetime index
  (`value=(first_day, first_day + 7 days)`, `format="DD MMM"`) is the best fit here: it gives the
  "selection bar" feel the user asked for, works for any simulated year, and returns real
  timestamps. A `st.date_input` range is the fallback if the slider proves awkward with datetimes.
- **Also** set `fig.update_xaxes(rangeslider_visible=True)` on the supply-mix and price charts so
  there is a draggable mini-map directly underneath each chart (this is the literal "selection bar
  underneath the chart"). The two controls compose: the slider selects the data window, the
  rangeslider zooms within it.
- Default window: 7 days starting at `chosen_day` (coerced via W9), so the first render is not a
  full-year plot of 8760 points.
- Guard performance: above ~5000 points in the window, downsample for display
  (`.iloc[::n]`) or switch to `go.Scattergl`. A 5-min custom upload over a full year is 105 120
  points and will otherwise hang the browser.

**16.2 — "Average 24 h profile" tab for each chart.** Half of this already exists:
`ppa/results.py::build_24h_avg(supply_mix_df)` (groupby `hour`, mean) and
`ui/charts.py::make_supply_mix_24h_chart(avg_24h, ppaload_mw)`. Wire them up and add the two
missing equivalents:
- Supply mix → `build_24h_avg` on the **range-filtered** frame (not the whole year), then
  `make_supply_mix_24h_chart`.
- Spot price → new `make_price_24h_chart(prices_avg)`: mean price by hour-of-day over the selected
  range, with a P10–P90 band across the days in the range (cheap to compute, and it is what makes
  an average profile trustworthy rather than misleading).
- BESS SoC → new `make_soc_24h_chart(soc_avg, bess_mwh)`: same treatment.

Tab layout per chart group becomes `["| Time series", "| Average 24 h"]` nested inside the
existing `["| Actual hourly supply mix", "| Market spot price", "| BESS SoC"]` tabs.
Note for sub-hourly data: group by `index.hour` **and** `index.minute` (or by
`index.time`) so a 5-min or 30-min run averages onto its own cadence rather than collapsing to
24 points.

**16.3 — Show the connection (link) MW.** Depends on W12(a).
- `ppa/results.py`: extract `n.links.static.p_nom_opt` (falling back to `p_nom` when the links
  were not extendable) into the result object alongside the existing dispatch series, and record
  the realised peak flow per link.
- Display in two places: the **"Optimised portfolio"** info banner in the Optimisation tab
  (`Wind X MW · Solar Y MW · BESS Z MW/MWh · Wind link A MW · PV+BESS link B MW · Export link C MW`)
  and a small table in the Results tab's generation-statistics section showing, per link:
  sized MW, peak flow MW, and utilisation (`peak / sized`). Utilisation near 100 % on the export
  link is the signal that `grid_connection_max_mw` is binding and curtailing the build.

**Tests** (`tests/test_results_ranges.py`): `build_24h_avg` over a known 2-day frame returns 24
rows with the correct means; the hour-and-minute grouping returns 48 rows for 30-min data;
range filtering is inclusive of both endpoints; the link table reports `peak ≤ sized` for every
link.

---

## 3. Suggested commit order

`W1 → W2 → W11 → W5 → W9 → W3 → W6 → W7 → W8 → W4 → W12 → W16 → W13 → W14 → W15 → W10a → W10b`

(W16 lands right after W12 because its link-MW display depends on the extendable-link change.)

Rationale: land the two confirmed bug fixes (W11, W9/W5) early so they are independently
revertable; do the big European deletion (W6) before the cosmetic work; do the spelling pass
**last** so it does not create conflicts with every other item.

## 4. Definition of done

- [ ] All new tests in §1.1 pass; the original 186 still pass.
- [ ] `docs/UAT_checklist.md` walked end-to-end by the reviewer with no failures.
- [ ] Exported `financial_model_*.xlsx` opens in Excel with no repair prompt.
- [ ] Sizing diagnostics explain the sized fleet; the link-cap bug fix is visible as a larger
      build on the same inputs; sized connection MW are reported and their utilisation shown.
- [ ] Benchmarks recorded in the PR body: merchant-share sweep (W12b), sizing method
      comparison (W14) and solver comparison (W15).
- [ ] Any blocked item (most likely the AER download, W4) is explicitly listed in the final
      report with what is needed to unblock it.

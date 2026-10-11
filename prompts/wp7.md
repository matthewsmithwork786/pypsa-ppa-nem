### WP7 — Pick Plants UI rebuild

**Owns:** `ui/tabs/nem_map.py`, `ui/constants.py`, `ui/nem_cache_status.py`,
`tests/test_nem_map_tab.py`, new `tests/test_pick_plants_years.py`.

**Difficulty:** medium. All the functions you need already exist and are
merged: `ppa.data.nem_data.available_years()`, `.plants_operational_for_years()`,
`ppa.data.remote_cache.missing_plant_years()` / `.ensure_plant_years()` /
`.ensure_price_years()` / `.RemoteFetchError`, and `Scenario.nem_years` /
`.capacity_sizing_year` / `.nem_resolution_minutes`. Do not re-derive or
re-implement any of these — import and call them. Read their docstrings/
signatures in `ppa/data/nem_data.py` and `ppa/data/remote_cache.py` briefly
(they are short), do not re-read the whole files.

The CUF filter and preset cards are already removed (an earlier, merged work
package). `ui/tabs/nem_map.py::render()` currently offers only a region filter
plus the plant picker.

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
   `years_available` may currently be an empty list (the real
   `plant_years.parquet` manifest hasn't been published yet — a separate work
   package is still producing it) — handle that gracefully: fall back to
   offering just `[2025]` (today's only shipped year) with a caption explaining
   why the range is narrow, rather than crashing on `min()`/`max()` of an empty
   sequence.

2. **Resolution selectbox.** Extend `ui/constants.NEM_RESOLUTION_MINUTES` to
   `{"1 hour": 60, "30 minutes": 30, "15 minutes": 15, "5 minutes": 5}` and
   render it with `key="nm_resolution"`. Caption the memory implication for
   15/5 min (sub-hourly multiplies LP size).

3. **Capacity-sizing year selectbox**, options = `selected_years`, default = the
   latest. Only shown when the current scenario has `optimise_capacity=True`;
   otherwise carry the scenario's existing value through. Caption: "The
   capacity optimisation solves against this single year. The dispatch
   simulation still uses all selected years."

4. Region filter — unchanged, keep as is.

#### 7.2 Plant list driven by the manifest

Replace the existing eligible-plants call with:

```python
@st.cache_data
def _cached_operational_plants(years: tuple, fingerprint: tuple) -> "pd.DataFrame":
    return nem_data.plants_operational_for_years(years)
```

Every plant in the returned frame is by construction operational in **all**
selected years. When `plants_operational_for_years` returns an empty frame
(manifest not yet published, or empty year selection), fall back to the
existing single-year `list_eligible_plants`/eligibility-cache behaviour for
whichever year is selected — do not leave the picker with zero plants when a
perfectly good single-year cache exists. This fallback is important right now
since the multi-year manifest genuinely doesn't exist in this checkout yet.

Keep `_marker_style` but drive it from a `data_status` column set to `"ready"`
for qualifying rows; render non-qualifying registry plants as the existing grey
dashed markers so the map still shows the whole fleet, with a caption
explaining why they are unselectable ("not fully operational in every selected
year").

Update `_tooltip` to show the mean CUF **across the selected years**
(`mean_cuf_selected_years` column, when present — fall back to the existing
single-year `cuf` column when using the fallback path above) and label it as
such. `tests/test_nem_map_tooltip.py` (not in your Owns list, but read it) checks
tooltip uniqueness and round-trip via `_duid_from_tooltip` — do not break that
contract; if your change to `_tooltip` risks it, run that test file explicitly
and confirm it still passes even though you don't own it.

Update the cache-status expander in `ui/nem_cache_status.py`: replace "UIGF
cached / Simulation-ready for 2025" wording with "years available", "plants
operational in all selected years", and whether the price years are covered
(use `ppa.data.nem_data.price_path` existence checks per selected year/region —
do not add new network code here, this module must stay display-only).

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

Note `nem_year=year` is gone — it is now a derived property (already merged),
do not set it directly or `dataclasses.replace` will raise.

Since the real Zenodo record doesn't exist yet (`record_id` in
`ppa/data/zenodo_manifest.json` is a placeholder), `missing_plant_years` will
currently either return everything-missing or the fetch will raise
`RemoteFetchError` — that's fine and expected; the `st.error` path above is
exactly what should show. Do not special-case the placeholder record; the code
should behave correctly once a real record is published without any further
change here.

Downsampling to the selected resolution happens in
`nem_data.get_timeseries_dicts` at run time (already merged), **not** here. The
cache always stores native 5-minute data.

#### 7.4 Tests

Pure helpers only (the module imports streamlit; existing tests use
`importorskip` — follow that pattern):
- selecting a year range where a plant is non-operational in one year excludes
  it (use a synthetic manifest via monkeypatching `nem_data.plants_operational_for_years`,
  don't depend on the real one existing);
- an empty year selection disables the button;
- the empty-manifest fallback path (§7.2) is exercised and does not crash;
- tooltip uniqueness and `_duid_from_tooltip` round-trip still hold on the real
  registry;
- `tests/test_ui_static_checks.py` (not owned — just run it) still passes.

**Done when:** the full existing suite is green, your new tests pass, and a
manual smoke test description is in your final report: pick a wind and solar
plant, change resolution, click "Use these plants", and describe what happens
given there is no real Zenodo record yet (should be a clean `st.error`, not a
crash).

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.

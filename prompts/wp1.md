### WP1 — Title, capacity-factor filter removal, preset-card removal

**Owns:** `streamlit_app.py`, `ui/tabs/nem_map.py`, `ui/tabs/case_study.py`,
`ui/state.py`, `ppa/scenario.py` (deletions only), `tests/test_nem_map_tab.py`.

**Difficulty:** mechanical.

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
Do not modify files outside the "Files owned" list above. If you believe you need to,
stop and report why.

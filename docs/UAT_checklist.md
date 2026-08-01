# Reviewer UAT checklist — Australian NEM cleanup

Walk these against `streamlit run streamlit_app.py` in the `feature/au-nem-cleanup`
branch. Tick each box only when the behaviour is observed end-to-end. Any failure
should be reported with the tab, the step, and a screenshot/console trace.

## 1. Tab bar

- [ ] Tab bar reads: Welcome · 1. Case Setup · **2. Get Data** (the plant map) ·
      2b. Custom Data · 3. Optimisation · 4. Results · 5. Financial Model ·
      6. Sensitivity Analysis · 7. HELP.
- [ ] No "European" tab, no "NEM Plant Map" title, no "Download Data" tab remains.

## 2. Case Setup

- [ ] Case Setup has **no** "Project Locations & Market Zone" section.
- [ ] The transmission-cost input is still reachable (moved into "Market interaction",
      not deleted).

## 3. NEM map hover

- [ ] Hovering a map marker shows: station, DUID, MW, region, **2025 CUF %**,
      **first power** date (or `—` when unavailable).
- [ ] Tooltip stays unique per DUID and identical across reruns (no `nan`).

## 4. Custom Data template

- [ ] Custom Data → pick 1 Mar – 31 Mar 2025 + **30 minutes** → downloaded CSV has
      **1488 rows**.
- [ ] Default selection (full 2025, hourly) → downloaded CSV has **8760 rows**.
- [ ] A `st.warning` appears for the 5-min full-year case (~105 120 rows).

## 5. Optimisation tab — reference day

- [ ] Optimisation → "Period reference optimisation": pressing **Run** works
      immediately after a fresh load with **no `chosen_day … is not present` error**,
      for both Calendar-month and Custom-range modes.
- [ ] An `st.info` ("Reference day moved to …") appears instead when the stored day
      is outside the selected period.

## 6. Results — ranges, 24 h averages, connection MW

- [ ] Results → Actual hourly supply mix has a working date-range control **and** an
      "Average 24 h profile" tab; the same for Market spot price and BESS SoC.
- [ ] The sized connection (link) MW and their utilisation appear in the Optimisation
      banner **and** the Results statistics table.
- [ ] Utilisation near 100 % on the export link is visible when
      `grid_connection_max_mw` binds.

## 7. Excel export

- [ ] Financial Model → export XLSX → opens in Excel with **no repair dialog**
      ("Removed Records: Formula …" is gone).

## 8. Capacity sizing (15-year scenario)

- [ ] The status line reports a **1-year sizing LP** and that the subsequent hourly
      dispatch still solves all 15 years.
- [ ] The sizing phase completes in the logged time.
- [ ] The sized MW are **no longer pinned to the slider values** (build exceeds the
      disabled sliders on cheap-capex inputs).
- [ ] Sizing diagnostics explain the sized fleet (annualised A$/MW/yr, achieved CF,
      implied LCOE vs tariff/spot, binding caps).

## 9. Language

- [ ] Every visible string uses Australian spelling (optimisation, analyse,
      normalise, maximise/minimise, behaviour, customise, summarise, organise,
      fulfilment; "Base futures — calendar year (A$/MWh)" / "Base futures hedge").
- [ ] No `EUR`/`€`/`ENTSO`/`CAL Y+1` wording anywhere in the counterfactual copy.

# Reviewer UAT checklist — Australian NEM cleanup

Walk these against `streamlit run streamlit_app.py` from the current worktree.
Tick each box only when the behaviour is observed end-to-end. Any failure
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

## 10. Multi-year plant picking (Pick Plants)

- [ ] The **Pick Plants** tab has a **"Historical years to use"** range slider. While
      `plant_years.parquet` is not committed, only `[2025]` is offered, with a
      caption explaining that the full range arrives when the manifest is published.
- [ ] A **"Snapshot resolution"** selectbox offers 5/15/30/60-minute options; the
      default is 1 hour, and a sub-hourly pick shows the LP-size/memory caption.
- [ ] With capacity optimisation on, a **"Capacity-sizing year"** selectbox appears,
      listing the selected years, with a caption noting the sizing LP solves against
      that single year while the dispatch still cycles all selected years.
- [ ] Pick a wind plant and a solar plant over a 2-year range; the plant list and the
      map markers reflect the selected range, and running a 2-year simulation
      succeeds.

## 11. SLA terms (Set Terms)

- [ ] **Set Terms** has a dedicated **"Service level agreement (SLA)"** expander
      (separate from "PPA contract terms") with **"Monthly minimum"** and
      **"Daily minimum"** toggles and share sliders.
- [ ] Setting a monthly share *below* the annual obligation shows the warning that
      the monthly minimum is looser than the annual obligation and will never bind.
- [ ] Setting a daily share *below* the annual obligation shows the equivalent
      warning for the daily minimum.
- [ ] Setting a daily share **>= 90 %** shows the "frequently infeasible" warning
      about a single low-resource day.
- [ ] With capacity optimisation on and sizing set to **Typical weeks (tsam)**,
      enabling the monthly SLA shows the warning that a monthly SLA cannot be
      enforced under tsam.

## 12. SLA enforcement — tsam + monthly is blocked

- [ ] With sizing set to **Typical weeks (tsam)**, monthly SLA on, and capacity
      optimisation on, **Run** is refused with a blocking error ("A monthly SLA
      cannot be enforced with the 'Typical weeks (tsam)' sizing representation…
      Switch the sizing representation to 'Full year hourly', or turn the monthly
      SLA off."). This must be a hard block, not a warning the app runs past.
- [ ] Switching the sizing representation to **Full year hourly** (or turning the
      monthly SLA off) clears the block and the run proceeds.

## 13. SLA compliance in Results (Deep Dive)

- [ ] After a run with monthly SLA on, the **Deep Dive** tab shows an
      **"SLA compliance"** section with a "Monthly PPA delivery share vs SLA target"
      chart, annotated with the target share.
- [ ] After a run with daily SLA on, the same section shows a "Daily PPA delivery
      share vs SLA target" chart.
- [ ] With no tiered SLA enabled, the SLA compliance section renders no charts
      (not blank/erroring charts).
- [ ] The **Optimisation** tab's SLA tiers table reports which tiers were live in
      the sizing LP and how many SLA/delivery constraints were added.

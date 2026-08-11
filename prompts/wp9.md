### WP9 — SLA reporting and post-solve verification

**Owns:** `ppa/results.py`, `ui/tabs/results_deep_dive.py`, `ui/tabs/optimisation.py`
(diagnostics only — a narrow addition, do not touch `_run_simulation` or
`_render_scenario_summary`, both owned by other work packages), new
`tests/test_sla_reporting.py`.

**Difficulty:** medium. `Scenario.sla_monthly_enabled/sla_monthly_share/
sla_daily_enabled/sla_daily_share` already exist (merged). The solver already
adds `AllowedShortfall_Limit_M...`/`_D...` and `MinDelivery_Limit_M...`/`_D...`
constraints when those are on (merged, in `ppa/solver.py` — read-only, not in
your Owns list, but look at the constraint-naming convention and the
`sla_constraint_count` it stores in `n.meta` so your diagnostics addition can
reference the same field).

#### 9.1 Results

Add to `SummaryVolumes` (find it in `ppa/results.py`; give every new field a
default so unpickling an old `run_store` payload does not break):

```python
    min_monthly_delivery_share: float = 1.0
    min_daily_delivery_share: float = 1.0
    n_months_below_sla: int = 0
    n_days_below_sla: int = 0
```

And to `OptimisationResult` (also in `ppa/results.py`):
```python
    monthly_delivery_share: pd.Series = None   # index = period end, value = D/L
    daily_delivery_share: pd.Series = None
```

Compute them in `extract_results` (same file) by grouping the delivered energy
(`ppa_delivery` or equivalent column already used elsewhere in `extract_results`
for the annual `fulfilled_share` — read that computation and mirror its exact
column names and weighting, do not invent new ones) and `ts["ppaload_mw"]` by
`ts.index.to_period("M")` / `.normalize()`, applying `resolution_h` (already an
`extract_results` parameter, merged), and dividing delivered/load per group.
`min_monthly_delivery_share` / `min_daily_delivery_share` are the min over
those per-period series (default 1.0 when the corresponding SLA tier is off or
there's only one period, so an unused field never LOOKS like a violation).
`n_months_below_sla` / `n_days_below_sla` count periods where the achieved
share is below the scenario's target share for that tier (0 when the tier is
off).

#### 9.2 Verification, per AGENTS.md §5.2

In `extract_results`, after computing the shares, when the corresponding SLA
is enabled (`scenario.sla_monthly_enabled` / `.sla_daily_enabled`) and the
achieved minimum falls more than 0.5 percentage points below the requirement
(`scenario.sla_monthly_share` / `.sla_daily_share`), attach a warning string to
the result (add a `warnings: list[str]` field to `OptimisationResult` if one
doesn't already exist — check first) and surface it in the UI as `st.warning`
in `ui/tabs/results_deep_dive.py`. Do **not** raise — an infeasible-adjacent
solve should still be inspectable — but make it impossible to miss. This is
exactly the signal that would have caught the historical weighting bug
(AGENTS.md §5.2, §3): if the solver constraint and the post-solve check ever
disagree by more than solver tolerance, that disagreement itself is the bug
signal.

#### 9.3 UI

- `ui/tabs/results_deep_dive.py`: add a "SLA compliance" section with a bar
  chart of monthly delivery share and a line chart of daily delivery share,
  each with a horizontal target line, and the count of periods below target.
  Only render this section when the corresponding SLA tier was enabled on the
  scenario that produced the result (don't show an empty/meaningless chart for
  a run that never turned tiered SLA on). Follow the existing chart
  conventions in `ui/charts.py` (read it briefly — note the `A\$` LaTeX-escaping
  convention for dollar signs in `st.caption`, used elsewhere in this file).
- `ui/tabs/optimisation.py::_render_sizing_diagnostics` (a different function
  from `_run_simulation`/`_render_scenario_summary` — only touch this one): add
  rows for which SLA tiers were active and whether they bound (use
  `sized.sla_constraint_count` from `ppa.sizing.SizedCapacities`, already
  merged).

#### 9.4 Tests — `tests/test_sla_reporting.py`

- Monthly/daily shares computed from a known synthetic dispatch match a
  hand-computed answer (construct a tiny synthetic `n`/`ts`/`scenario` — or
  reuse the `_toy_ts`/`_toy_scenario` pattern from `tests/test_sizing_network.py`
  if that's the fastest path, adapting as needed for a multi-day window).
- `min_monthly_delivery_share` ≥ the requirement in a solve where the monthly
  SLA constraint is on (round-trip through `extract_results` of a scenario
  where the constraint actually binds — construct one with a deliberately weak
  month, similar to the pattern in `tests/test_sla_constraints.py`, which you
  should read for the synthetic-network pattern even though you don't own it).
- Old-shape `run_store`/pickled results without the new fields still load (pickle
  a plain object with only the pre-existing fields, unpickle as the new
  dataclass, confirm the new fields default sanely — check how existing
  `run_store` backward-compat is tested elsewhere in the test suite and mirror
  that pattern).
- The 0.5pp warning fires when the achieved share is deliberately pushed just
  below target, and does not fire when it's on-target.

**Done when:** the full existing suite is green, your new tests pass, and a
manual description in your final report of what the SLA compliance section
looks like for a run with monthly SLA on vs off.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.

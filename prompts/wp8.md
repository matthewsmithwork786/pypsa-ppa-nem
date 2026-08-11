### WP8 — Tiered SLA constraints in the solver and the sizing LP

**Owns:** `ppa/solver.py`, `ppa/sizing_tsam.py` (returns extra metadata),
`ppa/sizing.py` (passes it through), new `tests/test_sla_constraints.py`.

**Difficulty:** highest in the plan. This is the work package most likely to
produce a subtly wrong-looking-right answer. Take your time, read the referenced
sections of `AGENTS.md` (especially §3 and §5.2) before writing a line, and do not
skip the weighting test (8.5 item 4) — it is the test that would have caught a
real historical bug where a hard 90% delivery requirement landed at 85.6% because
a `.sum()` was not multiplied by `n.snapshot_weightings["objective"]`.

`Scenario` already has the fields this WP needs (`sla_monthly_enabled`,
`sla_monthly_share`, `sla_daily_enabled`, `sla_daily_share`), added by an earlier,
already-merged work package. Do not modify `ppa/scenario.py`.

#### 8.1 The semantics, stated precisely

Let `w_t = n.snapshot_weightings["objective"][t]`, `L_t` = load, `S_t` =
`Gen_AllowedShortfall` dispatch, `D_t` = `link_p["IPPGen_to_PPAOfftake"]`.

For any snapshot group `P` (a calendar month, or a calendar day, or the whole
horizon):

- **Shortfall cap (the default form):**
  `Σ_{t∈P} w_t·S_t  ≤  (1 − share_P) · Σ_{t∈P} w_t·L_t`
- **Minimum delivery (added only when `optimise_capacity and enforce_min_delivery`):**
  `Σ_{t∈P} w_t·D_t  ≥  share_P · Σ_{t∈P} w_t·L_t`

These are **not** equivalent, because `Gen_Penalty` also sits on `Bus_PPAOfftake`
and bypasses the offtake link, so a shortfall cap alone can be satisfied with
penalty energy while `fulfilled_share` (which counts only `D_t`) stays low.
Capping shortfall is "the offtaker's free allowance is limited"; the min-delivery
form is "the portfolio must actually deliver". Implement both, exactly as above,
and say which is which in the constraint names.

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
    the tsam handling in §8.3.
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

Keep the existing per-calendar-year annual grouping for multi-year sizing exactly
as it is.

Skip groups whose `Σ w_t·L_t` is zero (defensive: a clustered or partial period
with no load makes the constraint `0 ≤ 0`, harmless but noisy).

#### 8.3 The tsam problem — the part most likely to be got wrong

`ppa/sizing_tsam.py::cluster_typical_periods` returns a synthetic
`pd.date_range(f"{start_year}-01-01", ...)` index. Grouping *that* by `.month`
produces groups that correspond to nothing real.

Required changes:

1. **Monthly under tsam is blocked upstream** by `validate_scenario` (already
   merged — it raises a blocking error before the solver ever runs). Add a
   belt-and-braces `raise ValueError` in `solve()` if it somehow gets through
   anyway, rather than silently constructing a meaningless constraint.
2. **Daily under tsam** is meaningful *within* a representative period. Each
   typical week is 168 consecutive hourly snapshots; slicing it into 7 blocks of
   24 gives 7 representative days, each standing for `occ` real days. Applying
   the daily constraint to each such block, with the `w_t` weights carried
   through, means "on a representative day of this type, the daily SLA holds".
   State in a comment that this is an approximation.

   Implement by having `cluster_typical_periods` **additionally return a
   period-block label array** — one integer per clustered snapshot identifying
   its `(cluster_id, day_within_period)` — and pass it through
   `optimise_capacities` into `solve()` via a new optional
   `period_labels: pd.Series | None = None` argument on `solve()`. When
   `period_labels` is given, daily groups come from it; otherwise from the
   calendar.

   Do **not** try to infer the blocks from the synthetic index inside `solve()`.
   The label must come from the clusterer, which is the only thing that knows
   the true period boundaries (including any appended extreme periods, which are
   extra periods at the end and must be labelled too).

3. `hours_per_period` is a parameter (168 default). Derive
   `days_per_period = hours_per_period // 24` rather than hard-coding 7, and
   handle a non-multiple-of-24 period by falling back to one group per period
   with a caption/comment explaining why.

#### 8.4 Feasibility

Daily constraints are far more likely to make the LP infeasible than an annual
one: a single dark, still winter day cannot meet an 80% daily SLA at any build
size unless storage or market buy covers it. Extend
`ppa/sizing.py::_infeasibility_hint` to name the daily/monthly constraint when it
is enabled:

```
"A daily SLA of 85% requires at least that share of EVERY day's load to be
delivered. A single low-resource day that no portfolio within the build caps can
cover makes the whole LP infeasible. Lower the daily share, enable market buy,
raise the BESS cap, or turn the daily SLA off and rely on the monthly/annual one."
```

Also count constraints: daily over 25 years at 5-minute resolution is 9,125
constraints over 2.6M snapshots. Log the constraint count at DEBUG and mention it
in the sizing diagnostics.

#### 8.5 Tests — `tests/test_sla_constraints.py`

These are the acceptance criteria. Build small synthetic networks (follow
`tests/test_sizing_network.py` for the pattern).

1. **Off by default is a no-op.** With `sla_monthly_enabled=False,
   sla_daily_enabled=False`, the set of constraint names on the model is
   *identical* to the current code's. Snapshot it.
2. **Monthly cap binds.** Construct a scenario where one month has poor
   resource. With the monthly SLA off, that month's delivered share falls below
   the monthly target; with it on at that target, **every month's** achieved
   shortfall share is ≤ `1 − sla_monthly_share + 1e-6`. Assert on the extracted
   dispatch, not on the model.
3. **Daily cap binds.** Same, per day.
4. **Weighting.** Run the same scenario at 60-minute uniform weights and with
   deliberately non-uniform `snapshot_weightings` summing to the same total; the
   achieved shares must agree to 1e-6. *This is the test that would have caught
   the 85.6% bug.*
5. **tsam daily.** With `sizing_method="tsam"` and a daily SLA, the LP solves,
   and every representative-day block satisfies the constraint. Assert
   `period_labels` covers every snapshot exactly once.
6. **tsam monthly is refused.** `solve()` raises if called directly with a
   monthly-SLA scenario and tsam-clustered snapshots (belt-and-braces check from
   §8.3.1) even bypassing `validate_scenario`.
7. **Infeasibility is reported, not swallowed.** A daily SLA of 100% with no
   market buy and a solar-only portfolio returns `status != "ok"` with the hint
   text present.
8. **Monotonicity.** Tightening the daily share never *decreases* sized capacity
   (`bess_mw` in particular). A violation means the constraint has the wrong
   sign.

**Done when:** all eight pass, plus the pre-existing `tests/test_sizing_*.py`
suite unchanged.

---
Do not modify files outside the "Files owned" list above. If you believe you need
to, stop and report why.

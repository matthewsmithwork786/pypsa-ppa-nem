# Sizing experiments — measured results

Running log of every sizing experiment, per `PLAN_sizing_underbuild.md` §2 rule 4.
**A result that is not written down here did not happen.**

Environment: `python3` 3.14, no venv. All runs prefixed with
`MPLCONFIGDIR=$TMPDIR PYTHONPATH=.`.

---

## E1 — W12b merchant-share sweep (baseline, before any Part B change)

**Date:** 2026-08-02
**Command:** `python3 scripts/bench_merchant_share.py --workers 2`
**Case:** `corporate_ppa` · 15 simulation years · 1-year full-hourly sizing LP
**Fleet the sliders would have used (disabled during sizing):** wind 280 MW · PV 200 MW ·
BESS 90 MW/360 MWh · PPA A$105/MWh · required delivery 90%
**Build caps:** wind 1000 / PV 1000 / BESS 1000 MW · grid connection limit ∞

| share | wind MW | PV MW | BESS MW | export link MW | delivery | IRR | sizing s | dispatch s | binding cap |
|---|---|---|---|---|---|---|---|---|---|
| 0.00 | 89 | 104 | 0 | 0 | 49.5% | n/a | 58.2 | 20.0 | none |
| 0.25 | 89 | 104 | 4 | 4 | 49.3% | n/a | 140.7 | 27.4 | none |
| 0.50 | 93 | 117 | 19 | 50 | 51.3% | **−6.7%** | 135.7 | 27.4 | none |
| 0.75 | 95 | 146 | 69 | 103 | 55.6% | **+0.8%** | 96.7 | 29.2 | none |
| 1.00 | 91 | 261 | 251 | 348 | 65.3% | **+7.1%** | 47.2 | 29.5 | none |

`n/a` IRR = no sign change in the cashflow, so `brentq` cannot solve — the project never
turns cash-positive.

### Findings

1. **Wind is completely insensitive to merchant share** — flat at 89–95 MW across the whole
   sweep. Its LCOE (A$162/MWh) exceeds even *undiscounted* mean spot (A$103/MWh), so no
   merchant credit makes marginal wind worth building. The ~90 MW it does build is the
   amount justified by PPA delivery alone.
2. **PV and BESS are highly sensitive** — PV 104 → 261 MW and BESS 0 → 251 MW as the share
   goes 0 → 1. This *corrects a prediction made in an earlier draft of the plan*, which
   expected the whole sweep to be flat. The PV+BESS pair captures materially more than
   average spot (charging in the 12.3% negative-price hours and the midday solar trough,
   discharging into the evening peak), so its effective merchant revenue is well above the
   flat-average LCOE comparison in `PLAN_sizing_underbuild.md` §1.1.
3. **No cap ever binds, at any share.** The build is limited purely by economics, never by
   `max_build_*` or `grid_connection_max_mw`. This contradicts the W12(b) expectation that
   "any share > 0 means the LP builds to whichever cap binds" — that only holds when the
   technology is in-the-money, which none of these are at the margin.
4. **Delivery never approaches the 90% requirement** (49.5% → 65.3%). Consistent with
   §1.2: penalty energy at A$126/MWh is cheaper than wind (A$162) or solar (A$136), so the
   LP buys out of the SLA rather than building to it.
5. **IRR only clears zero at share ≥ 0.75, and never reaches the 10% `target_irr`.**

### Verdict

The default `sizing_merchant_value_share = 0.5` produces a portfolio with a **negative IRR
(−6.7%) and 51% delivery against a 90% contractual requirement**. It is not a defensible
default on this case study. But raising the share is *not* the fix — a higher share
improves IRR only by assuming away the capture-price/MLF/curtailment risk the haircut
exists to represent. The real problems are the ones in `PLAN_sizing_underbuild.md` §1:
plant selection (U1), missing LGC revenue (U2), and a penalty priced below build cost (U3).

**Status of the plan's W12b obligation: discharged.** This is the sweep
`PLAN_au_nem_improvements.md` §4 required and that the previous round of work reported as
"recorded in the commit message" when it had never been run.

---

## E2 — W14 sizing-method comparison

**Date:** 2026-08-02
**Command:** `python3 scripts/bench_sizing_method.py --workers 2`
**Case:** `corporate_ppa` · 1-year sizing LP over 8,760 hourly snapshots ·
merchant share 0.5 · PPA A$105/MWh · required delivery 90%

`Δ fleet` is total sized MW against the exact full-hourly row. `gap` is the sizing LP's own
delivery estimate minus the full hourly simulation's (the W14 item-6 validation metric).

| method | sizing s | wind | PV | BESS | total | Δ fleet | LP deliv | full deliv | gap |
|---|---|---|---|---|---|---|---|---|---|
| full hourly (exact) | 136.9 | 93 | 117 | **19** | 228 | ref | 54.1% | 51.3% | +2.8 pp |
| tsam 8 typical days | **1.6** | 98 | 118 | **0** | 216 | −5.3% | 56.3% | 52.3% | +4.0 pp |
| **tsam 12 (current default)** | **1.6** | 101 | 102 | **0** | 203 | **−11.2%** | 54.8% | 51.9% | +2.9 pp |
| tsam 24 typical days | 1.9 | 93 | 92 | **0** | 185 | **−19.1%** | 50.3% | 48.2% | +2.1 pp |
| coarse 3h (legacy) | 12.9 | 93 | 116 | 8 | 218 | −4.5% | 53.7% | 50.9% | +2.8 pp |

### Findings

1. **tsam is ~85× faster** than the exact LP (1.6 s vs 136.9 s) and ~8× faster than the
   legacy 3 h coarse path. The speed claim in W14 holds emphatically.
2. **tsam sizes the BESS to exactly zero at every period count**, while the exact LP builds
   19 MW and the legacy coarse path builds 8 MW. This is precisely the failure mode the W14
   plan warned about in item 4: with typical *days* and `cyclic_state_of_charge=True` the
   battery must return to its starting SoC within each representative day, which destroys
   most of the arbitrage value that justifies building it.
3. **Accuracy gets monotonically WORSE with more typical periods** — −5.3% at 8 days,
   −11.2% at 12, −19.1% at 24. This is backwards: more clusters should approximate the
   year better. It suggests a defect in the weighting or extreme-period configuration
   rather than an inherent limitation of clustering — as cluster count rises, the
   `addPeakMax`/`addPeakMin` extreme periods carry proportionally less weight, so peak-load
   and dark-lull coverage *degrades*. **Investigate before trusting any tsam row.**
4. **The W14 item-6 validation metric does not catch any of this.** The LP-vs-simulation
   delivery gap is 2–4 pp for every method, including the 24-day run whose fleet is 19%
   wrong. A small delivery gap is therefore *not* evidence that clustering preserved the
   sizing decision — the metric is much weaker than W14 assumed.

### Verdict

**The current default (`sizing_method="tsam"`, `sizing_n_periods=12`) under-sizes the fleet
by 11% and zeroes the BESS.** That default was set in W14 and, per the review, never
benchmarked. It is the worst kind of wrong — fast, plausible-looking and consistently
biased. See `PLAN_sizing_underbuild.md` **U8**.

### Caveat on the BESS result (test this before concluding)

At merchant share 0.5 the BESS is marginal anyway (19 MW at full hourly), so "tsam → 0" is
a small absolute change. E1 shows BESS reaches **251 MW at share 1.0**. Re-run this
comparison at `--shares 1.0`-equivalent conditions before concluding how badly clustering
damages storage sizing:

```bash
# add a --merchant-share flag to bench_sizing_method.py, then:
PYTHONPATH=. python3 scripts/bench_sizing_method.py --workers 2 --merchant-share 1.0
```

**Status of the plan's W14 obligation: discharged.**

---

## E3 — Memory / process-death fix (Part A, M1)

**Symptom.** `scripts/bench_merchant_share.py` was SIGKILLed twice with no traceback and
no exit code, always immediately after the sizing LP solved and `run_multi_year` forked
its workers.

**Cause.** The script called `optimise_capacities(...)` **in-process**, so the parent still
held the 306,611 × 122,646 sizing LP when `run_multi_year` forked. Fork is copy-on-write,
but CPython refcounting dirties nearly every page the child touches, so each worker
duplicated the parent's multi-GB heap. On this machine (11.9 GB total, ~5 GB free) that is
an immediate OOM kill.

**Why the app was not affected the same way:** `ui/tabs/optimisation.py` uses
`run_sizing_subprocess`, whose docstring states it exists so that killing the child
"returns the LP's multi-GB memory to the OS immediately instead of leaving it in the app
process". The benchmark script bypassed that protection. This also explains why fixed-
capacity multi-year runs have always worked: with no sizing phase the parent is small, so
forking is cheap.

**Fix (M1).** Both bench scripts now call `run_sizing_subprocess` and `del`/`gc.collect()`
the full-year sizing frame before dispatch.

**Result.** The 15-year, 5-share sweep above ran to completion, exit code 0. **Confirmed
fixed.**

### M2–M6 landed

Measured with `scripts/measure_peak_rss.py` (M6), Corporate PPA, 2 years, 2 workers,
coarse sizing (chosen because it solves in ~10 s; the full-hourly LP peaks far higher):

| Phase | Peak RSS |
|---|---|
| Baseline, after data load | 352 MB |
| **Sizing** (coarse LP, in a child process) | **778 MB** |
| Parent immediately after `del sizing_ts; gc.collect()` | **352 MB** — fully released |
| **Dispatch** (2 forked workers) | **1,767 MB** |

The parent returning exactly to its 352 MB baseline is the direct evidence that **M2**
works: nothing from the sizing phase is resident when `run_multi_year` forks. The dispatch
peak of ~1.77 GB for two workers over a 352 MB parent shows the per-worker cost is well
above the old flat 1200 MB assumption once the parent is non-trivial — which is what **M3**
now accounts for.

- **M2** — `ui/tabs/optimisation.py` releases the sizing frame before forking.
- **M3** — `_safe_worker_count` budgets `max(_PER_WORKER_MEM_MB, parent RSS)` per worker and
  keeps an `_RESERVE_MEM_MB` (800 MB) headroom.
- **M4** — `BrokenProcessPool` is caught; the run falls back to the in-process serial path
  and re-solves only the years that never returned, with an `st.warning` explaining why it
  slowed down. Previously an OOM-killed worker took the whole process with it, silently.
- **M5** — README "Memory" section documents the mechanisms and the two env knobs.
- **M6** — `scripts/measure_peak_rss.py` samples self + all descendants.

Covered by `tests/test_multi_year_memory.py` (7 tests), including a simulated
`BrokenProcessPool` that asserts every year is still solved via the serial fallback.

---

## E4 — Are the low capacity factors a trace-model error? (No.)

**Question raised:** Collector 28.4% and Sunraysia 20.0% look low. Is the generation
trace model wrong?

**Answer: no defect.** `load_scada -> capacity_factor_series -> to_hourly` was read and
checked against raw SCADA. `capacity_factor_series` is `(scada / capacity).clip(0, 1)` and
`to_hourly` is a resample mean onto a canonical index — both correct, and the 5-min and
hourly means agree exactly. Data quality is clean: 0.000% NaN across all 184 cached plants,
negligible negative values, and 105,119 of 105,120 expected intervals.

### 2025 fleet capacity factors (cached NEM plants, non-operational plants excluded)

| | n | capacity-weighted | min | p25 | median | p75 | max |
|---|---|---|---|---|---|---|---|
| **Wind** | 89 | **27.9%** | 6.4% | 24.6% | 30.6% | 35.0% | 40.5% |
| **Solar** | 91 | **16.8%** | 6.1% | 13.4% | 16.3% | 19.2% | 26.8% |

Four plants were excluded as non-operational/commissioning (CF < 5%): `CRWARP1`, `CUSF1`,
`GOESF1`, `GUSF1`. `GOESF1` in particular is 348.6 MW registered with a 0.04% CF.

### Why the numbers look low

1. **These are AC CUFs** — output against *registered AC* capacity, not DC panel rating.
   Utility PV is routinely quoted on DC, which is materially higher. Solar daylight-only
   (07:00-17:00) CF has a median of **35.8%**, which is the more intuitive figure.
2. **Curtailment is widespread and real.** 62 of 91 solar plants show >8% zero-output
   intervals *during daylight*. Sunraysia is a heavy case (14.9% daylight zeros, West
   Murray constraints); Moree (1.6%) and Western Downs (2.3%) are not.
3. **Curtailment is contract-dependent.** Whether a plant curtails into negative prices
   depends on its own PPA — some are incentivised to, some are not. So a uniform
   "unconstrained uplift" factor would be wrong.

> **Method note / correction.** An earlier pass in this session tried to infer curtailment
> two ways, and both were wrong. (a) Comparing CF in negative-price hours against
> positive-price hours showed *higher* output when prices are negative — but that is
> confounded, because high solar output is what *causes* NEM midday negative prices.
> (b) A "p95 CF by time-of-day" clear-sky proxy implied 40-53% curtailment, but that
> proxy mostly measures seasonal and cloud variation, not curtailment. Neither number
> should be used. The daylight-zero-share metric above is the defensible one.

**Consequence for U4.** The right data source is AEMO's `DISPATCHLOAD.AVAILABILITY`
(the UIGF for semi-scheduled units) — physical unconstrained potential, independent of any
plant's contractual curtailment incentives. It is reachable via `nemosis`, the same path
`scripts/fetch_nem_scada_prices.py` already uses for `DISPATCH_UNIT_SCADA`. Do not
substitute a heuristic uplift.

**Consequence for plant choice.** Sunraysia at 20.0% is the **79th percentile** of solar —
it is above the fleet median. The whole solar fleet reads low; Sunraysia is not an outlier.
Collector at 28.4% is the 36th percentile of wind, so there is real headroom there.

---

## E5 — U5: capex benchmarked against CSIRO GenCost 2025-26

**Source:** `GenCost_2025-26_Final_Report_20260715.pdf` (CSIRO, 15 July 2026), Apx Table
B.1 (generation, "Current policies" scenario, 2025 row) and Apx Table B.5 (storage,
4-hour, total cost basis). Real 2025 A$.

| | repo (before) | **GenCost 2025-26** | change |
|---|---|---|---|
| Onshore wind | 2900 A$/kW | **3248 A$/kW** | **+12%** |
| Large-scale solar PV | 1718.6 A$/kW | **1621 A$/kW** | −6% |
| BESS (4 h) | 276.5 A$/kWh | **385 A$/kWh** | **+39%** |

**Basis check — no double-count.** GenCost p.96 lists "Connection costs" and "Marginal
loss factors" among the parameters it explicitly **excludes**, and p.97 confirms the
figures are *overnight* capital costs (interest during construction excluded). The repo
charges connection separately (`connection_cost_aud_mw` in the LP,
`onsw/pv/bess_connection_cost` in the financial model) and applies construction timing in
the financial model, so both are compatible with GenCost's basis.

**Finding: U5 makes the under-build worse, not better.** The plan hypothesised that the
repo's capex was above benchmark and that correcting it would help. The opposite is true
for two of three technologies — wind is 12% cheap and BESS 39% cheap in the repo. Adopting
GenCost lowers the baseline sized fleet from 228 MW to 189 MW and drops BESS to zero.

---

## E6 — Combined effect of U1 + U3 + U5 (+ U2)

Corporate PPA, 1-year coarse-3h sizing LP, GenCost capex applied throughout.

| variant | wind | PV | BESS | total | LP delivery |
|---|---|---|---|---|---|
| GenCost capex, original plants | 64 | 126 | 0 | 189 | 47.1% |
| + better NSW1 plants (`GULLRWF2` + `MOREESF1`) | 90 | 116 | 10 | 216 | **68.3%** |
| + hard 90% SLA (`enforce_min_delivery`) | 103 | 243 | 155 | **501** | **90.0%** |

### Findings

1. **Plant selection is worth ~21 pp of delivery on its own** (47.1% → 68.3%) at almost no
   extra capacity — a better site converts the same MW into far more delivered energy.
2. **The hard SLA constraint does what the penalty price could not.** Delivery goes to
   exactly the contracted 90%, and the fleet grows to 501 MW. Under the price-based
   formulation no merchant share reached even 66% (E1).
3. **Better plants make the SLA much cheaper to meet.** On the pre-GenCost capex with the
   original plants, the hard constraint needed 773 MW (161/364/248) to reach 90%. With
   better plants and *more expensive* GenCost capex it needs 501 MW — a 35% smaller fleet
   despite higher unit costs.

### Verdict

The under-build is now explained and addressable without weakening the model: it was a
combination of poor sites and a delivery requirement that was only ever a price signal.
`enforce_min_delivery` is off by default (it changes the contract's meaning), but it is the
right setting whenever the penalty is cheaper than building — which, at GenCost capex, it
almost always is.

---

## E7 — RETRACTED: "tsam cannot size storage" (superseded by E9)

> **This section's conclusion was wrong.** It attributed tsam sizing storage to zero to an
> inherent property of clustering (loss of intraday price spread) and declared it
> unfixable. E9 shows the real cause was a **units error** — the occurrence count was being
> used as the storage timestep — and that fixing it makes clustered sizing build storage
> normally. The spread-loss measurement below is real but second-order; it is not why the
> BESS was zero.
>
> Kept for the record because the reasoning error is instructive: every aggregation check
> passed (energy, mean, peak all preserved), which made a mechanical fault look like an
> economic one. The tell that should have been followed sooner: under a *hard* 90%
> delivery constraint tsam still built no storage and instead over-built wind to 484 MW —
> economics cannot explain a technology being refused when it is the cheapest way to meet
> a binding constraint.

## E7 (original text) — why tsam sizes storage to zero (the W14 diagnosis was wrong)

### What was measured

Corporate PPA, 1-year sizing LP. **BESS capex is the confounder** — at GenCost's
385 A$/kWh storage is uneconomic in every representation, so the defect is invisible.
Holding everything else fixed and varying only BESS capex:

| BESS capex | method | wind | PV | **BESS** |
|---|---|---|---|---|
| 276.5 (old) | full hourly | 70 | 132 | **22** |
| 276.5 (old) | tsam 12 | 72 | 114 | **0** |
| 385 (GenCost) | full hourly | 64 | 120 | 0 |
| 385 (GenCost) | tsam 12 | 72 | 114 | 0 |

**Clustering zeroes a battery the exact LP builds.** The defect is real but *latent* at
current capex. E1 shows BESS reaching 251 MW at merchant share 1.0, so the regime where it
bites is well within normal use.

### The W14 diagnosis and its proposed fix are both wrong

W14 attributed this to `cyclic_state_of_charge=True` forcing the battery back to its
starting SoC within each representative day, and proposed `hours_per_period=168` (typical
weeks). Tested at the capex where BESS is economic (exact LP = 22 MW):

| representation | BESS | vs exact |
|---|---|---|
| tsam 8 / 12 / 24 × **day** | 0 / 0 / 0 | −100% |
| tsam 4 / 8 / 12 × **week** (168 h) | 0 / 0 / 0 | **−100%** |

Typical weeks do not help at all. The cyclic-SoC explanation is therefore not the cause.

### The actual cause: clustering destroys intraday price volatility

| | mean intraday price spread | vs original |
|---|---|---|
| Original 365 days | A$431.8/MWh | — |
| tsam 8 days | A$202.9/MWh | −53% |
| tsam 12 days | A$201.6/MWh | −53% |
| tsam 24 days | A$255.4/MWh | −41% |

Battery arbitrage revenue scales with the intraday spread. Halving the spread removes most
of the revenue that justifies storage, so the LP builds none. **Energy, the annual mean and
the load peak are all preserved exactly** — which is why the standard aggregation checks
(and the W14 item-6 delivery-share metric) all pass while the storage decision is destroyed.

Neither of tsam's representation options fixes it:

| representation | spread loss | negative-price hours retained |
|---|---|---|
| `mean` (centroid) | −33% | 2.7% (of 12.3%) |
| `medoid` (real day, the hierarchical default in use) | −53% | 9.3% (of 12.3%) |

`mean` retains more spread but destroys the negative-price hours; `medoid` retains
negative hours but less spread. Neither is close. This is inherent to representing 365
distinct daily price shapes with 12–26 — not a configuration mistake.

### Actions taken

- Default stays `full_hourly` (U8 step 1, already landed).
- `ui/scenario_form.py` warns when `tsam` is selected with a BESS in play. A warning, not
  a block — tsam's ~85× speed-up is still worth having for a generation-only screen.
- `ppa/sizing_tsam.py` module docstring rewritten: it previously stated the wrong cause and
  recommended a fix that does not work.
- `ppa.sizing.validate_sizing_representation()` (U8 step 4) compares **sized MW per
  technology** against the exact LP, since the delivery-share metric provably cannot detect
  this (2–4 pp gap even when the fleet was 19% wrong).

### Correction to E2

E2's other two headline claims do **not** survive re-measurement under GenCost capex:

- *"Fleet 11.2% low, degrading monotonically (−5.3/−11.2/−19.1%)"* — now non-monotonic and
  within roughly ±5% (+4.9%, +1.4%, −7.5% at 8/12/24; −5.8%, −3.6% at 48/96). Ordinary
  clustering error, not systematic bias. **tsam 12 is the most accurate setting at +1.4%.**
- The extreme-period weighting hypothesis is disproven outright: weights sum to exactly
  8760 h, PV and wind energy are preserved to 0.00%, and the load peak is retained at every
  period count.

The storage finding is the one that survives.

---

## E8 — U4: unconstrained (UIGF) capacity factors

**Acquired.** `scripts/fetch_nem_availability.py` pulls `DISPATCHLOAD.AVAILABILITY` and
`SEMIDISPATCHCAP` via `nemosis`, month by month, into
`data/cache/nem/availability/<DUID>_2025.parquet`. 179 DUIDs written, 171 at >99% coverage,
221 MB.

Curtailment = `1 − constrained CF / unconstrained CF`, over 175 plants matched to the
registry:

| | constrained (SCADA) | unconstrained (UIGF) | curtailment | SEMIDISPATCHCAP=1 |
|---|---|---|---|---|
| **Wind** (n=84) | 27.7% | **30.7%** | **9.7%** | 15.1% |
| **Solar** (n=90) | 16.9% | **20.4%** | **17.3%** | 19.5% |

Per-plant, solar curtailment has a median of 21.6%, p90 of 38.1% and a **maximum of 71.3%**:

| DUID | cap | constrained | unconstrained | curtailed |
|---|---|---|---|---|
| `MANSLR1` | 50 MW | 6.1% | 21.3% | **71.3%** |
| `MOLNGSF1` | 36 MW | 8.9% | 21.7% | 59.0% |
| `MUWAWF1` (wind) | 232 MW | 22.9% | 36.2% | 36.8% |

### Findings

1. **This confirms curtailment is contract- and location-specific, not a fleet-wide
   factor.** The spread from ~0% to 71% is why a uniform uplift would have been wrong.
2. **AC CUF remains the dominant explanation for low solar CFs, not curtailment.** Even
   unconstrained, fleet solar is only 20.4%. Curtailment explains ~3.5 points of the gap.
3. **The case study's own plants are barely curtailed**, so this lever changes little for
   them specifically:

   | DUID | constrained | unconstrained | curtailed |
   |---|---|---|---|
   | `COLWF01` | 28.4% | 28.6% | 0.9% |
   | `GULLRWF2` | 39.6% | 40.4% | 2.1% |
   | `MOREESF1` | 26.8% | 26.9% | 0.4% |
   | `SUNRSF1` | 20.0% | 22.0% | 9.1% |

   The value of U4 is in modelling *other* plants honestly — a 71%-curtailed site looks
   uninvestable on SCADA and reasonable on UIGF.

**These numbers supersede the two bad curtailment estimates flagged in E4.** Both were
inferred; these are measured against AEMO's own unconstrained forecast.

### Wiring

`Scenario.use_unconstrained_cf` (default **off**) selects the UIGF trace. It falls back to
SCADA per DUID when the cache is missing, so installs without it are unaffected. Exposed as
a toggle in Case Setup next to the NEM region/year selectors.

---

## E9 — Two real bugs in the weighted (typical-period) LP

Prompted by a domain challenge: *"the BESS should be used to meet the PPA hurdle. As the
penalty is a fixed 1.5x PPA tariff the intraday price spread shouldn't really affect the
BESS build out. Something is definitely strange though because some BESS should be built."*

Both halves were right, and following them found two genuine defects.

### The delivery economics dominate, as claimed

In the LP a delivered MWh earns `ppa_price` (the offtake link carries `-ppa_price`), while
shortfall and penalty generators sit on `Bus_PPAOfftake` and bypass that link entirely. So
shifting a MWh into a deficit hour is worth **A$105–231/MWh** — an order of magnitude more
than any intraday arbitrage spread. Confirmed by forcing the SLA: BESS goes **22 → 298 MW**
on the exact LP. Storage here is a delivery instrument, not an arbitrage instrument.

### Bug 1 — energy constraints ignored snapshot weightings

`ppa/solver.py` summed all three energy constraints unweighted:

```python
period_load_mwh = float(load.loc[snaps].sum())            # no weights
allowed_shortfall_expr = gen_p.loc[snaps, "..."].sum()    # no weights
```

With uniform weightings (full hourly = 1 h, coarse = `resolution_h`) both sides scale
together and the ratios are correct, so this was invisible. tsam gives each representative
hour a different weight (5–55 h), so the constraints bound the wrong quantity: a **hard 90%
delivery constraint was landing at 85.6% of actual load**.

Affects `AllowedShortfall_Limit` and `BuyFromMarket_Limit` as well as `MinDelivery_Limit` —
i.e. **pre-existing since W14**, not introduced with the hard-SLA work. Every tsam run to
date solved with a mis-scaled shortfall cap and market-buy cap. Fixed: tsam now hits
exactly 90.0%.

### Bug 2 — the occurrence count was used as the storage timestep

```python
n.snapshot_weightings.loc[:, :] = w   # sets objective, generators AND stores
```

The `stores` column is the **dt in the storage energy balance** — elapsed time between
consecutive snapshots — not an occurrence count. Setting it to the occurrence count makes
one snapshot span up to ~55 h, and a 4-hour battery cannot shift anything across a 55-hour
step. The LP was correct to build none.

Isolated cleanly (constrained SCADA data and old BESS capex held fixed; only the storage
`dt` varies):

| storage dt | hard SLA | wind | PV | **BESS** | delivery |
|---|---|---|---|---|---|
| occurrence count (bug) | off | 71 | 117 | **0** | 48.9% |
| occurrence count (bug) | on | **484** | 353 | **0** | 90.0% |
| 1 h intra-period (fixed) | off | 81 | 165 | **79** | 65.6% |
| 1 h intra-period (fixed) | on | **94** | 284 | **222** | 90.0% |

Note the wind over-build collapsing from 484 → 94 MW: with storage available the LP no
longer has to brute-force the SLA with generation. Soft-SLA delivery also improves from
48.9% to 65.6% at lower cost.

**tsam typical-period sizing has been structurally incapable of building storage since
W14** — a units error, not a clustering limitation.

### Post-fix comparison (UIGF data, old BESS capex)

| method | hard SLA | wind | PV | BESS | delivery |
|---|---|---|---|---|---|
| full hourly | off | 57 | 139 | 35 | 50.5% |
| full hourly | on | 128 | 379 | 299 | 90.0% |
| tsam 12 | off | 85 | 158 | **94** | 68.5% |
| tsam 12 | on | 124 | 201 | **207** | 90.0% |
| coarse 3h | off | 53 | 136 | 17 | 48.7% |
| coarse 3h | on | 131 | 371 | 258 | 90.0% |

tsam still under-sizes storage against the exact LP (207 vs 299 MW under a hard SLA), which
is ordinary clustering error and is what `validate_sizing_representation()` exists to
measure. It is no longer a structural zero.

### Regression cover

`tests/test_sizing_tsam.py` asserts that `snapshot_weightings["stores"]` is the
intra-period hour while `objective`/`generators` keep the occurrence counts, and that a
battery is buildable at throwaway capex under clustering — guarding the class of bug, not a
specific number.

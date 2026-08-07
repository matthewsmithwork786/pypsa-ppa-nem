# Implementation plan — sizing under-build & multi-year process deaths

**Repo:** `/home/hanan/projects/pypsa-ppa-nem`
**Branch to start from:** `feature/au-nem-cleanup` (HEAD `9bd3eea` + the review fixes on top)
**Test baseline:** `MPLCONFIGDIR=$TMPDIR python3 -m pytest -q -p no:cacheprovider` → **237 passed**
**Audience:** the implementing model. Read §0, §1 and §2 before touching code.

---

## 0. What this plan is, and what it is NOT

Two separate problems, in this order:

- **Part A (§4) — the multi-year run kills the process.** A blocker: you cannot measure
  anything until this is fixed. Root cause is diagnosed below; the fix is small.
- **Part B (§5) — the capacity optimiser "under-builds".** Seven candidate approaches to
  test, each with a hypothesis, an experiment and an acceptance criterion.

> ### ⚠️ Read this before you "fix" the optimiser
>
> **The LP is not broken. The under-build is arithmetic, and it is currently the correct
> answer to the question being asked.** On the Corporate PPA case study the delivered
> technologies cost far more per MWh than any revenue available to them (§1). A correctly
> formulated LP facing those numbers *must* build very little.
>
> Do **not** "fix" this by weakening the cost basis, adding a fudge factor, removing the
> hurdle rate, or relaxing caps until the build looks bigger. That converts an honest
> answer into a flattering one. Every approach in Part B is either (a) correcting an input
> to a defensible real-world value, (b) adding a revenue stream that genuinely exists and
> is currently missing, or (c) making the result explainable. If an approach does not fit
> one of those three, it does not belong in this plan.
>
> **A legitimate outcome of this work is "the Corporate PPA case study is uneconomic at
> A$105/MWh with these plants, and here is the tariff/site/cost combination at which it
> clears."** That is a *result*, not a failure.

---

## 1. The diagnosis (measured, not assumed)

All figures below were computed from the repo's own cached 2025 NEM data and the
`corporate_ppa` case study. Reproduce with the snippets in §3.

### 1.1 Costs vastly exceed revenues

Annualised cost = `capex × 1000 × (1 + devex) × (crf(rate, life) + opex_rate)`,
matching `ppa/network.py:74-78`. At the defaults (`target_irr=10%`, `life=30`,
`opex=2%`, `devex=10%`):

| Technology | Plant (case study) | 2025 CF | Capex | Annualised | **Implied LCOE** |
|---|---|---|---|---|---|
| Wind | `COLWF01` Collector | **28.4%** | A$2900/kW | A$402,193/MW/yr | **A$162/MWh** |
| Solar | `SUNRSF1` Sunraysia | **20.0%** | A$1719/kW | A$238,348/MW/yr | **A$136/MWh** |

Against the revenues actually available to the LP:

| Revenue stream | Value |
|---|---|
| PPA tariff (delivered MWh) | **A$105/MWh** |
| Mean 2025 spot (NSW1) | A$103/MWh |
| Merchant surplus, after the 50% haircut | **≈ A$52/MWh** |
| Blended best case (90% delivered + 10% merchant) | **≈ A$100/MWh** |

**Wind costs 62% more than its best possible revenue. Solar costs 36% more.**

> **Important caveat — this flat-average LCOE comparison is only decisive for wind.**
> The sweep in `docs/sizing_experiments.md` E1 (run *after* this section was drafted)
> shows wind is indeed flat at 89–95 MW across every merchant share, but **PV and BESS
> respond strongly** (PV 104 → 261 MW, BESS 0 → 251 MW as the share goes 0 → 1). A
> PV+BESS pair captures well above *average* spot — it charges through the 12.3% of hours
> at negative prices and the midday trough, and discharges into the evening peak — so
> comparing its cost to a flat annual mean understates its revenue. Use the LCOE table to
> reason about **wind**, and the measured sweep to reason about **PV and BESS**.

### 1.2 Paying the penalty is cheaper than building — this is the delivery-share bug

`penalty_price = ppa_price × pen_mult = 105 × 1.2 =` **A$126/MWh**.

| | A$/MWh |
|---|---|
| Penalty energy | **126** |
| Wind LCOE | 162 |
| Solar LCOE | 136 |

**Both technologies cost more than the penalty.** The LP is therefore *economically correct*
to under-build and pay penalties. This explains the measured delivery share of
**49.5%–65.3% against a 90% contractual requirement** across the whole merchant sweep
(`docs/sizing_experiments.md` E1) — the escape valve is cheaper than the obligation, so the
requirement never binds.

This is the single most important finding in this document. Any `pen_mult` that puts the
penalty below the build cost makes the delivery requirement non-binding and the SLA
meaningless.

### 1.3 LGC revenue exists in the financial model but not in the LP

`ppa/financial_model.py:116` defines `lgc_price: float = 5.0` ("A$/MWh green-certificate
revenue on excess") and it flows through `financial_model_excel.py:558-567`. **The sizing
LP in `ppa/network.py` credits no LGC/green-certificate revenue at all.** So the LP and the
financial model are scoring different revenue stacks — exactly the class of mismatch W12(c)
set out to eliminate, still present on the revenue side. The A$5/MWh default is also far
below Australian market levels.

### 1.4 The case study's plants are poor, and much better ones are already cached

`list_simulation_ready_plants()` over the 85 cached DUIDs:

| | Case study uses | CF | Best cached | CF |
|---|---|---|---|---|
| Wind | `COLWF01` Collector | 28.4% | **`DUNDWF3` Dundonnell (VIC1)** | **40.5%** |
| Solar | `SUNRSF1` Sunraysia | 20.0% | **`MOREESF1` Moree (NSW1)** | **26.8%** |

Sunraysia in particular sits in the West Murray zone and its 2025 SCADA embeds heavy
network curtailment — using constrained historical output as the CF for a *new* build
charges that curtailment twice (see U4).

### 1.5 Lever sensitivity — what each change is worth

Implied LCOE (A$/MWh), cumulative down the rows:

| Lever | Wind | Solar |
|---|---|---|
| Baseline (case-study plants) | 162 | 136 |
| \+ best cached site (40.5% / 26.8%) | **113** | **102** |
| \+ `crf` at `discount_rate` 8% rather than `target_irr` 10% | 98 | 88 |
| \+ GenCost-style capex (wind A$2500/kW, PV A$1450/kW) | 84 | 74 |

Against the A$105/MWh tariff: **site selection alone very nearly closes the gap for solar
and gets wind within ~8%.** This makes U1 the highest-value approach by a wide margin.

---

## 2. Ground rules

1. **Branch:** `git checkout -b fix/sizing-underbuild`. Commit once per work item
   (`M1…M5`, `U1…U7`) with the item ID in the subject.
2. **Never break the 237 tests.** Run the suite after every item.
3. **Part A before Part B.** Every Part B experiment needs a working multi-year run.
4. **Record every experiment's numbers** in `docs/sizing_experiments.md` (create it). A
   result you did not write down did not happen — that was the main gap in the previous
   round of work.
5. **Do not change these without saying so explicitly in the commit message:** `pen_mult`
   semantics, `required_delivery_share` semantics, the `Gen_AllowedShortfall` epsilon
   (`0.001` — plan W12(d) explains at length why it is correct; do not re-litigate it).
6. **Environment:** `python3` (3.14, no venv). Always prefix with `MPLCONFIGDIR=$TMPDIR`
   and use `PYTHONPATH=.` for scripts. Use `-p no:cacheprovider` with pytest.
7. **`ppa/data/nem_data.py` and `ppa/data/aer_futures.py` keep their no-network import
   discipline.** All network access lives in `scripts/`.

---

## 3. Reproducing the §1 numbers

Keep these to hand; they are the fastest feedback loop in this plan and need no LP solve.

```bash
cd /home/hanan/projects/pypsa-ppa-nem

# LCOE vs revenue for the case study's plants
MPLCONFIGDIR=$TMPDIR PYTHONPATH=. python3 -c "
from ppa.scenario import CASE_STUDIES_BY_ID, load_case_study
from ppa.data import nem_data
s = load_case_study(CASE_STUDIES_BY_ID['corporate_ppa'])
pv, wind, prices = nem_data.get_timeseries_dicts(s); yr = sorted(pv)[0]
def crf(r, l): return r / (1 - (1 + r) ** -l)
for tech, capex, cf in (('wind', s.wind_capex_per_kw, wind[yr]), ('PV', s.pv_capex_per_kw, pv[yr])):
    ann = capex * 1000 * (1 + s.devex_pct_of_capex) * (crf(s.target_irr, s.project_life_yrs) + s.opex_rate)
    print(f'{tech}: CF {cf.mean():.1%} -> A\${ann/(cf.mean()*8760):.0f}/MWh')
print(f'PPA A\${s.ppa_price:.0f} | penalty A\${s.ppa_price*s.pen_mult:.0f} | spot A\${prices[yr].mean():.0f}')
"

# Best cached plants by capacity factor
MPLCONFIGDIR=$TMPDIR PYTHONPATH=. python3 -c "
from ppa.data import nem_data as nd
df = nd.list_simulation_ready_plants()
c = 'cuf' if 'cuf' in df.columns else 'mean_cf'
for t in ('Wind','Solar'):
    d = df[df.fuel_tech.str.contains(t, case=False, na=False)].sort_values(c, ascending=False).head(5)
    print(f'--- {t} ---')
    for _, r in d.iterrows():
        print(f'  {r.duid:10s} {str(r.station_name)[:24]:26s} {r.capacity_registered_mw:6.0f}MW CF={r[c]:.1%} {r.region}')
"
```

---

## 4. PART A — stop the process dying

### A.0 Root cause (confirmed)

`ppa/sizing.py:393` `run_sizing_subprocess` exists precisely for this, and its docstring
says so:

> *"Killing the child also returns the LP's multi-GB memory to the OS immediately instead
> of leaving it in the app process."*

The full-year sizing LP is **306,611 rows × 122,646 cols**. Held in a process, that is
multiple GB of linopy/xarray objects.

`ppa/multi_year.py::run_multi_year` then parallelises with a **`fork`** context
(`multi_year.py:236-240` explains why fork and not spawn). Fork is copy-on-write, **but
CPython's reference counting writes to every object header it touches**, so the child
materialises a private copy of essentially the parent's whole heap. Therefore:

```
peak RSS  ≈  parent_RSS × (1 + n_workers)
```

If the parent is still holding the sizing LP when `run_multi_year` forks, each worker
duplicates it. On this machine (11.9 GB total, ~5 GB free) that is an instant OOM kill —
**silent SIGKILL, no traceback, no exit code**, exactly what was observed.

**This is why it works with fixed capacities:** no sizing LP was ever built in the parent,
so the parent is small and forking is cheap. Nothing regressed in the multi-year code — the
sizing phase is what changed the memory profile it forks from.

**A second, independent growth source:** each worker returns a full `OptimisationResult`
(several 8760-row series) and the parent accumulates **all N years** of them
(`state.set_multi_year_results(results)`). At 15 years that is a large, permanent parent
allocation that also gets COW-duplicated by any later fork.

### A.1 — M1: benchmark/analysis scripts must use the subprocess path ✅ DONE

**This was the actual cause of the crashes during review.** `scripts/bench_merchant_share.py`
calls `optimise_capacities(...)` **directly, in-process**, then calls `run_multi_year` —
bypassing the very protection the app uses.

- In `scripts/bench_merchant_share.py` and `scripts/bench_sizing_method.py`, replace
  `optimise_capacities(sizing_ts, scn)` with
  `run_sizing_subprocess(sizing_ts, scn)` (import from `ppa.sizing`).
- After sizing, drop the reference to the sizing timeseries before dispatch:
  ```python
  del sizing_ts_for_this_share
  import gc; gc.collect()
  ```
- **Acceptance:** `PYTHONPATH=. python3 scripts/bench_merchant_share.py --workers 2`
  completes all five shares without dying.

### A.2 — M2: make the app free the sizing memory before forking ✅ DONE

In `ui/tabs/optimisation.py::_run_simulation`, between the sizing block and
`run_multi_year`:

- `del sizing_ts` (it is a full-year frame and is not needed again), then `gc.collect()`.
- Keep `sized` (a small dataclass) and the diagnostics.
- Do this **after** `sizing_diagnostics(...)` is computed, since that consumes `sizing_ts`.

### A.3 — M3: size the worker pool from the *parent's* footprint, not just free RAM ✅ DONE

`ppa/multi_year.py:85-94` currently does:

```python
mem_cap = max(1, int(mem_mb // _PER_WORKER_MEM_MB))   # _PER_WORKER_MEM_MB = 1200
```

That budgets a flat 1200 MB per worker and ignores what the parent is already holding —
but under fork+refcounting the parent's RSS is very close to the *incremental* cost of each
worker. Change to:

```python
def _parent_rss_mb() -> float | None:
    """Resident size of this process, MB. None when unavailable."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None

# fork is copy-on-write, but CPython refcounting dirties nearly every page the child
# touches, so budget each worker at the parent's own footprint (plus the solver's own
# working set) rather than a flat constant.
parent_mb = _parent_rss_mb() or 0.0
per_worker = max(_PER_WORKER_MEM_MB, parent_mb + _SOLVER_HEADROOM_MB)
mem_cap = max(1, int((mem_mb - _RESERVE_MB) // per_worker))
```

with `_SOLVER_HEADROOM_MB = 400` and `_RESERVE_MB = 800` (leave headroom for the OS and
the Streamlit process itself), both overridable by env var like `_PER_WORKER_MEM_MB`.

**Acceptance:** a new unit test in `tests/test_multi_year_memory.py` asserts
`_safe_worker_count` returns 1 when `_available_memory_mb` is small relative to parent RSS,
and more than 1 when memory is plentiful (monkeypatch both helpers).

### A.4 — M4: never die silently — detect worker death and fall back to serial ✅ DONE

A SIGKILLed worker surfaces as `concurrent.futures.process.BrokenProcessPool`. Today that
either propagates as an opaque error or the whole process is already gone.

In `run_multi_year`, wrap the parallel path:

```python
try:
    ... existing ProcessPoolExecutor path ...
except BrokenProcessPool:
    logger.warning(
        "Parallel dispatch workers were killed (most likely out of memory). "
        "Falling back to the serial path — this is slower but uses ~1/%d the memory.",
        workers,
    )
    return _run_serial(...)   # the existing memory-safe serial path
```

The serial path already exists as an **in-process** branch taken when `workers == 1`
(`ppa/multi_year.py:210-227` — read its comment, it explains that in-process is
deliberate for memory-constrained hosts). Factor that inline block into `_run_serial(...)`
so both the `workers == 1` branch and the `BrokenProcessPool` handler call it.

**Also surface it in the UI:** `ui/tabs/optimisation.py` should show an `st.warning` when
the fallback triggers, so a slow run is explained rather than mysterious.

### A.5 — M5: document and expose the escape hatches ✅ DONE

- `README.md`: a short "Memory" note — `PPA_WORKER_MEM_MB` raises the per-worker budget
  (forcing fewer workers); the sizing LP is the memory peak; long horizons + capacity
  sizing are the expensive combination.
- Add `--workers` to any script that runs `run_multi_year` (both bench scripts already
  have it).

### A.6 — M6: measure, so the fix is provable ✅ DONE

Add `scripts/measure_peak_rss.py`: runs `{sizing only, dispatch only, sizing+dispatch}` for
a given case study and `--years`, sampling `/proc/self/statm` plus children every 0.5 s,
printing peak RSS per phase.

**Acceptance for Part A:** the Corporate PPA case study, 15 years, capacity sizing on,
completes on this machine at default worker count; `measure_peak_rss.py` shows peak RSS
below available memory; and the numbers before/after M2+M3 are recorded in
`docs/sizing_experiments.md`.

---

## 5. PART B — seven approaches to the under-build

Each approach: **hypothesis → change → experiment → acceptance**. Run U0 first, then U1,
then the rest in any order. Record every result in `docs/sizing_experiments.md`.

### U0 — Establish the baseline properly (DONE — read the results, do not re-run blindly)

**The W12b merchant sweep has already been run.** Its full table, findings and verdict are
in **`docs/sizing_experiments.md` E1**. Read that before doing anything else in Part B.

Headline: at the default `sizing_merchant_value_share = 0.5` the sized portfolio has a
**−6.7% IRR and 51% delivery against a 90% requirement**; wind is flat at ~90 MW across
every share while PV and BESS scale strongly; and **no cap binds at any share** — the build
is limited purely by economics.

- The W14 sizing-method comparison is the remaining baseline item; record it in E2.
- Re-run the merchant sweep **after** each Part B change (U1, U2, U3…) and add a new
  numbered experiment to `docs/sizing_experiments.md` each time, so every approach's effect
  is attributable:
  ```bash
  PYTHONPATH=. python3 scripts/bench_merchant_share.py --workers 2
  PYTHONPATH=. python3 scripts/bench_sizing_method.py --workers 2
  ```
- **Do not raise the merchant share to make the IRR look better.** A higher share improves
  IRR only by assuming away the capture-price/MLF/curtailment risk the haircut exists to
  represent. Fix the causes (U1, U2, U3) instead.

### U1 — Plant selection (highest value; see §1.5)

**Hypothesis.** The case study's under-build is mostly a *site* problem. Collector at 28.4%
and Sunraysia at 20.0% are poor; the cache holds 40.5% wind and 26.8% solar. This lever
alone moves wind A$162 → A$113 and solar A$136 → A$102.

**Change.** In `ppa/scenario.py::CASE_STUDIES`, repoint `corporate_ppa` to
`nem_wind_duid="DUNDWF3"`, `nem_pv_duid="MOREESF1"`. Review the other three case studies
the same way. **Add a comment recording each plant's 2025 CF** so the choice is auditable.

**Watch out.** Dundonnell is VIC1 and Moree is NSW1 while `nem_price_region` is NSW1 — that
is a *deliberate* cross-region portfolio, but it means the wind CF and the price series come
from different regions. Either keep the region consistent (pick the best NSW1 wind,
`GULLRWF2` Biala at 39.7%) or state explicitly that MLF/basis risk is out of scope.
**Prefer `GULLRWF2` for the default case study** to keep region consistency; use Dundonnell
only in a sensitivity.

**Experiment.** Re-run U0's sweep. **Acceptance:** report sized MW, delivery share and IRR
before/after. Expect a materially larger build; solar should now clear the tariff.

### U2 — Add LGC / green-certificate revenue to the sizing LP

**Hypothesis.** §1.3 — LGC revenue is in the financial model but absent from the LP, so the
LP under-values every generated MWh relative to the model that later scores it.

**Change.**
- Add `Scenario.lgc_price_aud_mwh: float = 30.0` (document the source; A$5 in
  `ProjectFinanceInputs` is not a market level — cross-check current LGC spot and note the
  RET wind-down to 2030 in the docstring).
- In `ppa/network.py`, credit it on **generation**, not delivery: subtract
  `lgc_price_aud_mwh` from `Gen_OnshoreWind` / `Gen_PV` `marginal_cost` in sizing mode
  (they currently carry token costs of `0.1` / `0.01`).
- **Keep the LP and the financial model consistent** — either drive both from the same
  scenario field, or state in the docstring why they differ.

**Watch out.** Do not credit LGCs on market-buy or penalty energy — only on own generation.
A negative marginal cost on a generator lets the LP generate purely to earn certificates;
that is correct behaviour for LGCs, but confirm curtailment still happens at negative spot
(the U2 test must assert this).

**Acceptance.** New test in `tests/test_sizing_network.py`: LGC revenue raises the sized
fleet; negative-price hours are still curtailed; `lgc_price=0` reproduces the old result.

### U3 — Reconcile `pen_mult` with the build cost (the delivery-share fix)

**Hypothesis.** §1.2 — at `pen_mult=1.2` the penalty (A$126) is below both LCOEs, so the
LP buys its way out of the SLA. Any contract where penalty < build cost has a
non-binding delivery requirement.

**Change.** Do **not** silently raise `pen_mult`. Instead:
- Add to the sizing diagnostics a **first-class comparison**: `penalty price` vs
  `cheapest marginal build LCOE`, with an explicit warning when the penalty is lower —
  *"the penalty is cheaper than building; the delivery requirement will not bind and the
  sized fleet will under-deliver by design."*
- Surface the same warning in the Optimisation tab.
- Then run the sensitivity: `pen_mult ∈ {1.2, 1.5, 2.0, 3.0}` and report the delivery share
  achieved at each.

**Acceptance.** The diagnostics warn on the current defaults; the sweep shows delivery share
rising as `pen_mult` crosses the build cost. Recommend a default in the write-up, with
reasoning — do not just pick one.

### U4 — Constrained vs unconstrained capacity factors

**Hypothesis.** §1.4 — historical SCADA is *constrained* output. Sunraysia's 20.0% embeds
West Murray curtailment. Using it as the CF for a new build charges that curtailment twice:
once in the CF, and again when the LP curtails against prices/limits.

**Change (investigate before implementing).**
- Check whether the OpenElectricity / AEMO data offers an availability or unconstrained
  series (`scripts/fetch_nem_scada_prices.py`; the registry fetch has a `_first_present`
  helper for tolerant field naming).
- If available, add an **optional** `ts_WindAvail`/`ts_PVAvail` column and a scenario flag
  `use_unconstrained_cf: bool = False`. **Optional and off by default** — it must not break
  existing caches (mirror the `first_power_date` back-compat pattern in
  `nem_data.py:94-102`: never add to `REGISTRY_COLUMNS`).
- If unavailable, document it as a known limitation and move on. **Do not synthesise an
  uplift factor** — a made-up number is worse than a documented gap.

**Acceptance.** Either a working optional unconstrained path with a test, or a written
finding that the data is not obtainable and why.

### U5 — Capex benchmarking against published Australian sources

**Hypothesis.** Wind at A$2900/kW and PV at A$1719/kW may be above current benchmarks.
§1.5 shows GenCost-style capex is worth roughly A$14-28/MWh.

**Change.** Cross-check against **CSIRO GenCost 2024-25** and the **AEMO ISP** input
assumptions. Update the defaults **only if** the published figures support it, and record
the citation and vintage in a comment next to each constant in `ppa/scenario.py:124-126`.

**Watch out.** GenCost quotes several bases (overnight vs installed, with/without
connection). The repo already charges connection separately via
`connection_cost_aud_mw` — **do not double-count it**. State which basis you used.

**Acceptance.** Each capex constant carries a sourced comment; the sweep is re-run and the
delta recorded.

### U6 — Cost-basis sensitivity: `target_irr` vs `discount_rate`

**Hypothesis.** W12(c) moved the LP's `crf` from `discount_rate` (8%) to `target_irr` (10%)
so the LP only builds what clears the hurdle. That was defensible, but §1.5 shows it costs
~A$15/MWh — and it is worth confirming it is not an unintended *double* hurdle (LP builds
only ≥10% projects, then the financial model discounts at 10% again for NPV).

**Change.** No code change initially — **measure first**. Run the sweep at
`crf_rate ∈ {discount_rate, target_irr}` and compare sized MW, delivery share and the
*resulting IRR*. If the achieved IRR under the `discount_rate` basis still clears
`target_irr`, the stricter basis is over-conservative and should be reconsidered.

**Acceptance.** A recorded comparison and a recommendation with reasoning. If you change
the default, update the W12(c) rationale in the `build_network` docstring — do not leave
the docstring describing the old behaviour.

### U7 — Missing BESS revenue (FCAS / firming)

**Hypothesis.** `SU_BESS` earns only energy arbitrage. Real NEM batteries earn a large
share of revenue from FCAS, which is entirely absent — so BESS is systematically
under-built (19 MW in the smoke run).

**Change.** This is the **most speculative** item; scope it carefully.
- Simplest defensible version: a scenario field `bess_fcas_revenue_aud_mw_yr: float = 0.0`
  (a fixed A$/MW/yr credit, **default 0 so nothing changes silently**), subtracted from the
  BESS annualised capital cost in sizing mode.
- Document hard that this is a *scalar proxy*, not an FCAS market model, and that co-optimising
  energy and FCAS properly is out of scope.

**Watch out.** Do not model FCAS as a per-MWh energy revenue — FCAS is paid for *enablement*
(availability), not throughput. A per-MWh credit would wrongly push the battery to cycle.

**Acceptance.** Default 0 reproduces current results exactly; a non-zero value increases
sized BESS MW; a test covers both.

### U8 — The `tsam` default under-sizes the fleet and zeroes the BESS (**measured; high priority**)

**This is a confirmed defect, not a hypothesis.** See `docs/sizing_experiments.md` E2.

The current default `sizing_method="tsam"`, `sizing_n_periods=12` produces a fleet **11.2%
smaller** than the exact LP and sizes the **BESS to zero** (exact LP: 19 MW). Accuracy gets
*worse* as periods increase (−5.3% at 8, −11.2% at 12, −19.1% at 24), which is backwards
and points at a defect rather than an inherent limit. tsam is genuinely ~85× faster
(1.6 s vs 136.9 s), so the goal is to fix it, not to abandon it.

**Do these in order:**

1. **Change the default now, as a holding action.** Set `Scenario.sizing_method` to
   `"full_hourly"` (or `"coarse"`, which is only −4.5% and 8× faster than exact). A
   knowingly-biased default should not ship while the cause is unknown. Note the change and
   the reason in the field's comment.
2. **Diagnose the monotonic degradation.** In `ppa/sizing_tsam.py::cluster_typical_periods`,
   check how `ExtremeConfig` (`addPeakMax=["ppaload_mw"]`, `addPeakMin=["ts_PVGen",
   "ts_WindGen"]`) interacts with cluster count. Hypothesis to test first: extreme periods
   are appended as *additional* clusters with occurrence weight ~1, so as `n_periods` rises
   their share of the 8760 h weighting falls and peak/dark-lull coverage degrades. Verify by
   printing each cluster's weight and checking whether the extreme periods' weights shrink
   with `n_periods`.
3. **Fix the BESS representation.** With `hours_per_period=24` and
   `cyclic_state_of_charge=True` the battery must return to its starting SoC every
   representative day — W14 item 4 predicted exactly this. Options, in order of preference:
   - `hours_per_period=168` (typical weeks) with proportionally fewer clusters;
   - inter-period SoC linking (tsam supports segment/period linking; PyPSA needs the
     storage to see a linked SoC across representative periods);
   - if neither is workable, **document that tsam must not be used when `include_bess`**
     and have `validate_scenario` reject that combination rather than silently returning a
     BESS of zero.
4. **Replace the W14 item-6 validation metric.** The LP-vs-simulation delivery gap is 2–4 pp
   for *every* method including the 19%-wrong one, so it cannot detect clustering damage.
   Add a stronger check: re-solve the **exact** LP and compare **sized MW per technology**,
   warning when any technology differs by more than ~5%. Run it as an opt-in "validate my
   clustering" action, not on every solve (it costs a full-hourly solve).

**Acceptance.** Default is no longer a knowingly-biased method; the cause of the
monotonic degradation is identified in `docs/sizing_experiments.md`; the BESS is either
sized sanely under clustering or the combination is rejected with a clear message; and a
test covers whichever resolution is chosen.

---

## 6. Suggested order and decision tree

```
M1..M6  ✅ ALL DONE  (Part A — process no longer dies)
        ↓
U8 step 1 ✅ DONE (default flipped to full_hourly); steps 2-4 outstanding
     (diagnose the degradation, fix BESS under clustering, stronger validation)
        ↓
U0  (baseline sweeps — E1/E2 already recorded; re-run after each change)
        ↓
U1  (plant selection — biggest single lever)
        ↓
U3  (penalty diagnostics — explains delivery share)
        ↓
U2  (LGC revenue — biggest missing revenue stream)
        ↓
U5, U6  (input/cost-basis benchmarking; measure before changing)
        ↓
U4, U7  (investigate; may end as documented limitations)
```

**Decision points.**

- After **U1 + U2**, re-check §1.1's arithmetic. If wind LCOE is now below the blended
  revenue and the LP *still* builds almost nothing, there **is** a formulation bug — go
  hunting (start with degradation possibly being applied twice: `build_sizing_timeseries`
  bakes it into the CF columns while `multi_year._degraded_scenario` scales `p_nom`; the
  docstring at `sizing.py:127-131` claims these are equivalent — **verify that claim
  numerically**, it is the most likely remaining defect).
- If after **U1, U2, U5** the case study still does not clear, the honest conclusion is that
  a 90%-SLA firmed data-centre PPA at A$105/MWh is uneconomic on 2025 NEM costs. **Write
  that up and state the clearing tariff.** Consider raising the case study's `ppa_price`
  to a realistic firmed-PPA level (A$110-140/MWh in the 2024-25 NEM) and say why.

---

## 7. Definition of done

- [ ] The Corporate PPA case study runs 15 years with capacity sizing enabled without the
      process dying, at default worker count.
- [ ] `_safe_worker_count` is parent-RSS aware, and a killed pool degrades to the serial path
      with a visible warning instead of a silent death (`tests/test_multi_year_memory.py`).
- [ ] `docs/sizing_experiments.md` records, for every approach attempted: the change, sized
      MW, delivery share, IRR, and a one-line verdict.
- [x] The W12b merchant-share sweep and the W14 sizing-method comparison are recorded
      (`docs/sizing_experiments.md` E1/E2) — the outstanding obligation from
      `PLAN_au_nem_improvements.md` §4 is discharged.
- [ ] The default sizing method is not a knowingly-biased one (U8).
- [ ] The sizing diagnostics warn when the penalty is cheaper than building.
- [ ] Every changed default (capex, plant, LGC price, `pen_mult`) carries a sourced comment.
- [ ] 237 tests still pass, plus new tests for each landed approach.
- [ ] A short write-up states plainly whether the case study is economic, and if not, at
      what tariff/site/cost it becomes so.

---

## 8. Things that are already correct — do not change them

Re-litigating these wastes time; each was investigated and confirmed.

- **`Gen_AllowedShortfall` marginal cost `0.001`.** Shortfall already forgoes the full PPA
  tariff structurally, because shortfall and penalty generators sit on `Bus_PPAOfftake` and
  bypass `IPPGen_to_PPAOfftake`, the only link carrying PPA revenue. Adding `+ ppa_price`
  would double-count. See `PLAN_au_nem_improvements.md` W12(d).
- **The extendable transport links** (W12a) are correctly implemented and tested.
- **The merchant haircut applying to positive prices only** — halving negative prices would
  bias the LP toward dumping energy at negative spot.
- **The capacity-factor pipeline.** `load_scada` → `capacity_factor_series` → `to_hourly`
  was verified against raw SCADA: 28.4% and 20.0% are the genuine 2025 achieved factors,
  not a computation error.
- **`fork` rather than `spawn`** in `run_multi_year` — spawn re-imports `__main__`, which
  under Streamlit re-executes the whole app in every worker.

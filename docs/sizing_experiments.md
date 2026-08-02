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

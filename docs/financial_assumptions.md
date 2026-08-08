# Financial assumptions — consolidation and re-benchmark

Tracks `TASK_financial_assumptions_refactor.md`: unifying the two independent
financial-assumption sets (`ppa/scenario.py` vs `ppa/financial_model.py`) into a
single source of truth, retiring `%`-of-capex opex for per-technology fixed O&M,
updating the debt rate, and checking every constant against the AEMO 2025-26 IASR
and Gohdes (2026).

Reference paper: Gohdes, N. (2026), "Alternative Contract Design: Analysing the
Bankability of Fungible Derivative Contracts in Energy-Only Markets", *AJARE*,
doi:10.1111/1467-8489.70149 (Tables 2–3), whose inputs are stated as aligned to the
AEMO 2025–26 Inputs, Assumptions and Scenarios Report (IASR).

---

## Baseline (pre-refactor)

Captured on `main` (commit `5629ad4`) before any assumption changes, via a one-off
script (`scripts/financial_assumptions_baseline.py`, committed for reproducibility). Test suite: `334 passed` (`MPLCONFIGDIR=$TMPDIR python3 -m pytest
-q -p no:cacheprovider`).

### Sizing LP — default `Scenario()`, `optimise_capacity=True`, tsam (16 typical
weeks), real cached NEM data (wind `COLWF01`, solar `SUNRSF1`, region `NSW1`, 2025)

| Technology | Sized capacity |
|---|---|
| Wind | 122.99 MW |
| Solar PV | 129.82 MW |
| BESS | 12.16 MW / 48.65 MWh |
| Wind link | 100.00 MW |
| PV+BESS link | 94.01 MW |
| Export/sell link | 47.73 MW |

### `EnergyInputs` used for the financial-model baseline below

Derived from a 1-year hourly dispatch of the sized fleet above (`energy_inputs_from_result`,
year 1). Held **fixed** across Phases 2–3 so later deltas isolate the cost-assumption
change, not sizing-LP or dispatch noise (AGENTS.md §5.1):

```
onsw_mw=122.877  pv_mw=129.475  bess_mw=12.2  bess_mwh=48.112  load_mw=100.0
ppa_gwh=503.893  excess_solar_gwh=23.642  excess_nonsolar_gwh=12.587  penalty_gwh=153.107
total_solar_gwh=274.087  total_nonsolar_gwh=253.379
sell_solar_price=139.384  sell_nonsolar_price=362.228  purchase_price=0.923  marketbuy_gwh=25.195
```

### Levered financial model — default `ProjectFinanceInputs()` (**not** seeded from
`Scenario`, against the `EnergyInputs` above)

| Metric | Value |
|---|---|
| Project IRR | 8.01% |
| Equity IRR | 8.14% |
| NPV @ WACC | A$0.818m |
| Gearing | 31.88% |
| Total capex (incl. IDC) | A$728.907m |
| Total debt | A$232.389m |
| Total equity | A$496.518m |
| Min DSCR | 1.4875 |
| Avg DSCR | 1.4921 |
| Simple payback | 17.89 yrs |
| LCOE | A$128.55/MWh |

Note the disagreement this whole refactor exists to fix: this run used the
*standalone* `ProjectFinanceInputs()` defaults (Aus247RE_FM capex: wind A$2,900/kW),
not the GenCost capex (A$3,248/kW) the sizing LP above actually used via `Scenario`.
`project_finance_inputs_from_scenario()` would override capex but was never called
here — this is precisely the "which opex/capex applies depends on the code path" bug
described in the task background.

### Unlevered multi-year model (`ppa.financials.run_multi_year_financial_analysis`,
same sized fleet, `Scenario` capex/opex, 1 simulated year)

| Metric | Value |
|---|---|
| NPV | -A$436.129m |
| IRR | -0.10% |
| LCOE | A$140.28/MWh |
| Simple payback | 30.49 yrs |
| Total capex | A$628.621m |
| Total investment (incl. devex) | A$691.483m |
| Annual opex | A$12.572m |

(Negative NPV/IRR here is expected: a 1-year dispatch understates lifetime revenue
against a full capex bullet at year 0 with no residual value credited — this row
exists only to be diffed against Phase 2/3, not read as a standalone appraisal.)

---

## Phase 1 — single source of truth

See `ppa/assumptions.py` and `tests/test_assumptions_single_source.py`. Pure
refactor: no values changed. Phase 0 numbers above must reproduce bit-for-bit
(verified by re-running `scripts/financial_assumptions_baseline.py`).

## Phase 2 — retire `%`-of-capex opex

`Scenario.opex_rate` (2% of capex) is retired. The sizing LP
(`ppa.network.build_network`), the sizing diagnostics table
(`ppa.sizing.sizing_diagnostics`) and the unlevered multi-year model
(`ppa.financials.build_capex`) now all charge absolute per-technology fixed O&M
from `ppa.assumptions` (`WIND/PV/BESS_FIXED_OM_AUD_MW(H)_YR`), plus Gohdes
(2026) Table 2's maintenance capex (0.05% of capex p.a., all technologies).
Wind variable O&M is nil per the same table (no code change needed). The
levered `ProjectFinanceInputs.onsw_fixed_om` default moves from 0.028 (legacy,
28,000 A$/MW/yr) to 0.028512 (28,512 A$/MW/yr, Gohdes Table 2); PV and BESS
fixed O&M are unchanged (still `# UNVERIFIED`, flagged for Phase 4). The
connection asset gets $0 fixed O&M — no published figure exists for it, and
network-use-of-system charges are already modelled separately via
`Scenario.transmission_cost_aud_mwh` (see `CONNECTION_FIXED_OM_AUD_MW_YR`'s
docstring in `ppa/assumptions.py`).

Acceptance: `opex_rate` appears nowhere in `ppa/`, `ui/` or `scripts/`
(verified by `grep -rn opex_rate ppa/ ui/ scripts/`). Full suite green (339
passed).

**Headline LCOE delta**: unlevered multi-year LCOE (same sized-fleet pipeline
as the Phase 0 baseline, real NEM data, 1-year dispatch) moves from
**A$140.28/MWh to A$130.63/MWh** (-6.9%) — annual opex fell from A$12.572m to
A$6.652m because the sized fleet's actual fixed-O&M-plus-maintenance charge is
materially below the old flat 2%-of-capex assumption, on top of a larger sized
fleet (opex is now cheaper per MW, so the LP also builds more of it). Levered
model LCOE (default `ProjectFinanceInputs()`, same fixed `EnergyInputs`
baseline snapshot) moves from A$128.55/MWh to **A$131.00/MWh** (+1.9%) — this
one *rises* because wind fixed O&M rose (28,000 → 28,512 A$/MW/yr) while PV/
BESS were untouched, and the levered model's `EnergyInputs` are fixed rather
than re-sized, so it only sees the small per-MW increase, not any offsetting
fleet-size effect.

**Sizing LP (real data)**

| Metric | Unit | Phase 0 | Phase 2 | Δ |
|---|---|---|---|---|
| Wind | MW | 122.99 | 137.37 | +11.7% |
| Solar PV | MW | 129.82 | 135.29 | +4.2% |
| BESS | MW / MWh | 12.16 / 48.65 | 18.21 / 72.83 | +49.7% / +49.7% |

**Unlevered (1-yr dispatch)**

| Metric | Unit | Phase 0 | Phase 2 | Δ |
|---|---|---|---|---|
| NPV | A$M | -436.13 | -341.09 | +95.0 |
| IRR | % | -0.10% | 2.71% | +2.81pp |
| LCOE | A$/MWh | 140.28 | 130.63 | -6.9% |
| Annual opex | A$M | 12.572 | 6.652 | -47.1% |

**Levered (default PFI, fixed EnergyInputs)**

| Metric | Unit | Phase 0 | Phase 2 | Δ |
|---|---|---|---|---|
| Project IRR | % | 8.01% | 8.41% | +0.40pp |
| Equity IRR | % | 8.14% | 8.67% | +0.53pp |
| Gearing | % | 31.88% | 36.63% | +4.75pp |
| Min DSCR | x | 1.4875 | 1.4918 | +0.0043 |
| LCOE | A$/MWh | 128.55 | 131.00 | +1.9% |

The sizing LP builds materially more of everything (wind/PV +4-12%, BESS
+50%) because cheaper opex makes every technology's annualised A$/MW/yr cost
lower, so the LP's economics clear at a larger fleet against the same PPA
delivery target — an expected, not surprising, direction per the task's own
prediction ("Wind opex falls by roughly 56%[for the LP/unlevered side]...
sizing LP will build a different fleet").

## Phase 3 — debt rate

`ProjectFinanceInputs.debt_rate` moves from the legacy 6.50% to **5.75%**, a
blended-over-life proxy for Gohdes (2026)'s refinancing path (555bp 5-year
Facility A stepping up to 563bp Facility B for years 6-15, plus an allowance
for refinancing fees) rather than the paper's raw 555bp — copying 555bp
directly into this repo's single 15-year, no-refinancing debt structure would
understate the true cost of a 15-year fixed rate. See the derivation comment
on `ppa.assumptions.DEBT_RATE`. Single value changed; no other assumption
touched this phase. Full suite green (339 passed, one absolute-value
assertion updated to 0.0575 by hand).

Sizing LP and unlevered-model figures are unaffected (debt rate only enters
the levered `ProjectFinanceInputs` model) — reproduced bit-for-bit against
Phase 2. **Headline LCOE delta: none** (LCOE is a pre-financing capex/opex
figure; debt pricing does not move it).

| Metric | Unit | Phase 2 | Phase 3 | Δ |
|---|---|---|---|---|
| Project IRR | % | 8.41% | 8.40% | -0.01pp |
| Equity IRR | % | 8.67% | 8.89% | +0.22pp |
| Gearing | % | 36.63% | 38.70% | +2.07pp |
| Total debt | A$M | 294.6 | 310.4 | +5.4% |
| Min DSCR | x | 1.4918 | 1.4918 | unchanged |
| LCOE | A$/MWh | 131.00 | 131.00 | unchanged |

Cheaper debt raises Equity IRR and supports more gearing at the same DSCR
target (DSCR is a fixed multiple, unaffected by the rate) — the expected
direction, not a surprise.

**Optional, out of scope for this pass**: the repo already splits DSCR
(1.35x / 2.40x) and gearing (80% / 50%) between contracted/uncontracted
tranches; debt pricing is the one place it does not, while the paper spreads
140bp vs 200bp. Splitting `debt_rate` into `debt_rate_contracted` /
`debt_rate_uncontracted` would mirror the existing structure — proposed here,
not implemented without separate sign-off.

## Phase 4 — verify against the IASR

Sources fetched and searched directly (not from memory):

1. AEMO, *2025 Inputs, Assumptions and Scenarios Report* (August 2025) — the
   document that feeds the 2026 ISP and is referred to throughout this
   repo/task as "the 2025-26 IASR" — `aemo.com.au/.../2025-inputs-assumptions-
   and-scenarios-report.pdf` (235 pages, text-extracted and grepped in full).
2. CSIRO/GHD, *GenCost 2025-26 consultation draft* (Dec 2025) —
   `csiro.au/-/media/Energy/GenCost-2025-26-Draft/GenCost_2025-26_
   consultation-draft_accessible-report.txt` — the actual primary source this
   repo cites for its capex figures ("GenCost 2025-26 Final Report"; the draft
   is the closest publicly available edition, the Final not yet published at
   time of writing).

The IASR's own capital-cost trajectories are in turn sourced from **GenCost
2024-25 Final Report** ("AEMO's generator and storage capital cost
trajectories are informed by the GenCost publication series", IASR p.144) — one
edition behind what this repo cites. Per-technology dollar tables not covered
by GenCost (MLF, per-project auxiliary load) live in AEMO's companion *Input
and Assumptions Workbook* (.xlsx) and *Marginal Loss Factors Report*, neither
of which was reachable this session (the IASR *consultation page* hosting
those links 403s on both `curl` and fetch, unlike the final-docs PDF itself).

**Capex — confirmed exact**, straight from GenCost 2025-26's own tables (Apx
Table B.1, "Current policies" scenario, 2025 column; Apx Table B.5, "Battery
storage (4 hrs)", "Current policies", 2025):

| Technology | Repo (`ppa/assumptions.py`) | GenCost 2025-26, 2025, Current policies | Agreement |
|---|---|---|---|
| Wind capex | A$3,248/kW | **A$3,248/kW** | **Exact match.** |
| Large-scale solar PV capex | A$1,621/kW | **A$1,621/kW** | **Exact match.** |
| BESS capex (4 hr, total) | A$385/kWh (265 battery + 120 BOP) | **A$385/kWh (265 + 120)** | **Exact match.** |

**Fixed O&M — GenCost's own LCOE-assumptions table (Apx Table B.9)**, not
previously checked against a primary source at all:

| Technology | Repo | GenCost 2025-26 Table B.9 | Agreement |
|---|---|---|---|
| PV fixed O&M | A$12,000/MW/yr | **A$12.0/kW/yr = A$12,000/MW/yr** | **Exact match** — the `# UNVERIFIED` flag on `PV_FIXED_OM_AUD_MW_YR` can be lifted. |
| Wind fixed O&M | A$28,512/MW/yr (Gohdes 2026 Table 2, adopted in Phase 2) | A$29.0/kW/yr = A$29,000/MW/yr | **Close, not identical** (1.7% apart) — two independent sources (Gohdes and GenCost) landing within 2% of each other is corroborating evidence for the Phase-2 figure, not a reason to change it; kept as-is. |
| BESS fixed O&M | A$10,500/MWh/yr | Not published in Table B.9 (battery storage has no LCOE row — no capacity factor/fuel cost concept applies) | **Still unverified** — GenCost simply does not carry this figure in the table checked; a different GenCost table or the Input and Assumptions Workbook may, but neither was found this session. |

**New finding — economic life, not previously modelled as technology-specific**:
GenCost's own Table B.9 gives **wind onshore economic life = 25 years**, distinct
from **large-scale solar PV = 30 years**. This repo's `PROJECT_LIFE_YRS = 30`
applies uniformly to both technologies (and to `ProjectFinanceInputs.
operating_life`) — separately, the IASR states "the technical life assumed for
new wind and solar projects is 30 years" (p.155), so AEMO itself carries two
different life figures for wind depending on purpose: 25 years for GenCost's
LCOE annuitisation, 30 years for ISP retirement/technical-life modelling. This
repo's single flat `PROJECT_LIFE_YRS` cannot represent both purposes or a
wind/solar split at once — recorded as a real, sourced finding and a candidate
for a future per-technology project-life split, **not implemented in this
pass** (changing it would move Phase 0's baseline and needs its own
before/after measurement, per AGENTS.md §5.1).

**WACC / discount rate** (IASR Table 30, "Step Change" scenario, pre-tax real):
onshore wind 7.5%, large-scale solar PV 7.0%, battery storage (any duration)
8.0%; system-wide ISP discount rate (for CBA NPV, distinct from per-technology
WACC) 7.0% (Oxford Economics Australia recommendation, IASR p.158). This
repo's flat `DISCOUNT_RATE = 0.08` for both `Scenario` and `ProjectFinanceInputs`,
applied uniformly across technologies, sits within 0.5–1.0pp of every
IASR figure and matches none exactly. A technology-differentiated WACC would
be a real modelling improvement but is out of scope for this refactor —
recorded as a follow-up.

**Items confirmed as genuinely unavailable from AEMO, not merely unsearched**:

| Item | Repo | Finding |
|---|---|---|
| Auxiliary load | Unmodelled | AEMO derives it **per-plant from participant survey data** (IASR p.54), not a published system-wide percentage — the paper's 3.0% is its own wind-fleet assumption, not an AEMO input. Left unmodelled rather than inventing a number neither source states. |
| MLF | Unmodelled (folded into `sizing_merchant_value_share`'s 0.5 haircut) | Region/connection-point-specific, published per financial year in AEMO's separate *Marginal Loss Factors Report*, not a single flat value. Gohdes' 1.00 is his own simplification. Do not model MLF explicitly without correspondingly reducing the merchant-value haircut (see that field's docstring on the double-count risk). |
| Tax depreciation | `tax_depreciation_rate = 0.10`, no stated source | **Not an AEMO/IASR input at all** — statutory tax depreciation is an ATO effective-life determination, outside AEMO's remit. Whether to adopt the paper's 30-year straight-line (~3.33%/yr) figure is a modelling decision for a future pass with an ATO reference in hand, not something checkable against the IASR. |
| Gearing (contracted/uncontracted) | 80% / 50% | **Not an AEMO/IASR input** — AEMO sets technology WACCs, not project gearing ratios. The repo's fixed DSCR (1.35x/2.40x) versus the paper's *dynamic* DSCR solve against PoE99 CFADS is why the repo cannot reproduce Gohdes' 72.1%/71.7%/68.3% gearing outputs — a modelling-approach difference, not a lookup. A dynamic DSCR solve is separate follow-up work. |
| Lock-up ratio | Unmodelled | Not an AEMO input (a project-finance covenant). Gohdes: 1.10x CFADS. Scope a follow-up issue, per the task's own instruction not to implement it here. |

**No code or assumption *value* changed in this phase** (the wind-vs-PV
economic-life split and the technology-differentiated WACC are real findings
but deliberately not implemented here — either would move the Phase 0
baseline and needs its own measured phase). Full suite green (339 passed,
unchanged from Phase 3).

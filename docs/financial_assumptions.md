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
script (`scripts/_baseline_phase0.py`, not committed — reproducible from the
commands below). Test suite: `334 passed` (`MPLCONFIGDIR=$TMPDIR python3 -m pytest
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
(verified by re-running `scripts/_baseline_phase0.py`).

## Phase 2 — retire `%`-of-capex opex

## Phase 3 — debt rate

## Phase 4 — verify against the IASR

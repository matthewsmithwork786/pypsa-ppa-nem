"""Single source of truth for financial/technical benchmark constants shared by
`ppa.scenario.Scenario` (drives the LP + the scenario form) and
`ppa.financial_model.ProjectFinanceInputs` (drives the levered PF waterfall + the
Excel export). Module-level constants only — no dataclass, no imports from
`ppa.scenario` or `ppa.financial_model`, to avoid an import cycle.

Every constant names its source document, table and year. See
`docs/financial_assumptions.md` for the audit trail (baseline outputs, per-phase
deltas, and the IASR verification table) — TASK_financial_assumptions_refactor.md
governs the phased rollout this module was introduced for.

Two technologies' worth of capex are carried here on purpose: `Scenario`'s LP and
seeding path uses GenCost; `ProjectFinanceInputs`' standalone defaults (i.e. not
seeded from a `Scenario`) still use the legacy Aus247RE_FM figures they always
have. Phase 1 is a pure refactor — it must not move any value — so the PFI_*
constants below are *not* set equal to the GenCost ones; reconciling that
disagreement is a Phase 4 question (see docs/financial_assumptions.md).
"""
from __future__ import annotations

# ── Generation/storage capex — GenCost 2025-26 Final Report (15 July 2026) ──────
# Apx Table B.1 (generation) / Apx Table B.5 (storage), "Current policies"
# scenario, 2025 row, real 2025 A$. This is what `Scenario` (the sizing LP and
# `project_finance_inputs_from_scenario`, i.e. the normal app flow) uses.
#
# Basis check (GenCost p.96-97): these are *overnight* capital costs and
# EXCLUDE connection cost and marginal loss factor — see CONNECTION_COST_AUD_MW
# below, charged separately so there is no double-count.
WIND_CAPEX_AUD_KW: float = 3_248.0    # GenCost T.B1 "Wind" 2025
PV_CAPEX_AUD_KW: float = 1_621.0      # GenCost T.B1 "Large scale solar PV" 2025
BESS_CAPEX_AUD_KWH: float = 385.0     # GenCost T.B5 4-hour total (battery 265 + BOP 120)

# ── Generation/storage capex — legacy Aus247RE_FM reference model ──────────────
# `ProjectFinanceInputs`' own standalone defaults (used whenever it is
# constructed directly, not via `project_finance_inputs_from_scenario`). These
# disagree with the GenCost figures above — that disagreement is exactly the bug
# TASK_financial_assumptions_refactor.md exists to surface; Phase 1 must not
# change either side's *value*, only give each a named, documented home.
PFI_WIND_BUILD_COST_AUD_M_MW: float = 2.9        # A$m/MW  (2,900 A$/kW)
PFI_PV_BUILD_COST_AUD_M_MW: float = 1.7186       # A$m/MW  (1,718.6 A$/kW)
PFI_BESS_BUILD_COST_AUD_M_MWH: float = 0.2765    # A$m/MWh (276.5 A$/kWh)

# ── Connection cost ─────────────────────────────────────────────────────────────
# A$/MW, charged on the sizing LP's extendable transport links (a strictly
# positive value is what stops a link over-building beyond its realised peak
# flow) and, equivalently, on `ProjectFinanceInputs`' per-technology connection
# cost fields.
CONNECTION_COST_AUD_MW: float = 150_000.0
PFI_ONSW_CONNECTION_COST_AUD_M_MW: float = 0.15
PFI_PV_CONNECTION_COST_AUD_M_MW: float = 0.15
PFI_BESS_CONNECTION_COST_AUD_M_MWH: float = 0.0225

# ── Development cost (devex) ────────────────────────────────────────────────────
# Scenario applies this as a flat rate on total build capex. ProjectFinanceInputs
# instead carries a precomputed per-technology devex figure (= DEVEX_PCT_OF_CAPEX
# x the corresponding PFI_*_BUILD_COST constant above) — kept as its own literal
# here rather than computed, so Phase 1 cannot introduce a floating-point-rounding
# difference against the Phase 0 baseline.
DEVEX_PCT_OF_CAPEX: float = 0.10
PFI_WIND_DEVEX_AUD_M_MW: float = 0.29          # = 0.10 x 2.9
PFI_PV_DEVEX_AUD_M_MW: float = 0.17186         # = 0.10 x 1.7186
PFI_BESS_DEVEX_AUD_M_MWH: float = 0.02765      # = 0.10 x 0.2765

# ── Fixed O&M — absolute A$/MW (A$/MWh for BESS) p.a. ───────────────────────────
# Used directly by the sizing LP (ppa.network.build_network), the unlevered
# multi-year model (ppa.financials.build_capex) and ProjectFinanceInputs'
# per-technology defaults (in A$m, so /1e6 at the point of use) -- one number,
# three consumers, per TASK_financial_assumptions_refactor.md Phase 2.
#
# Wind: Gohdes, N. (2026), AJARE, Table 2 (aligned to the AEMO 2025-26 IASR).
# Supersedes the legacy Aus247RE_FM-derived 28,000 (2% of Aus247RE's 2,900 A$/kW
# build cost) that the retired `%`-of-capex opex rate produced on both the
# Scenario and ProjectFinanceInputs sides before this phase — see
# docs/financial_assumptions.md Phase 2 for the delta.
#
# PV and BESS: the paper is wind-only and gives no figure for either — these
# retain the legacy Aus247RE_FM values (2% of the legacy PFI_* build costs) and
# are UNVERIFIED against any published source. Flagged for Phase 4.
WIND_FIXED_OM_AUD_MW_YR: float = 28_512.0     # Gohdes (2026) Table 2
PV_FIXED_OM_AUD_MW_YR: float = 12_000.0       # UNVERIFIED — legacy Aus247RE_FM
BESS_FIXED_OM_AUD_MWH_YR: float = 10_500.0    # UNVERIFIED — legacy Aus247RE_FM
ANCILLARY_PCT_OF_REVENUE: float = 0.01        # Gohdes (2026) Table 2
# Additional O&M items from Gohdes (2026) Table 2. Modelled from Phase 2 onward
# in the unlevered multi-year model and the sizing LP only (both work in
# absolute A$ terms already); the levered ProjectFinanceInputs model has no
# maintenance-capex or variable-O&M line yet -- out of scope for this refactor.
MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA: float = 0.0005   # 0.05% of capex p.a., Gohdes Table 2
WIND_VARIABLE_OM_AUD_MWH: float = 0.0               # Gohdes Table 2 (wind: nil)
# No published O&M figure exists for a bare connection/transport asset (the
# GenCost/Gohdes sources are generation-technology cost tables). Treated as
# zero: ongoing network-use-of-system charges are already modelled separately
# via Scenario.transmission_cost_aud_mwh, so a $0 fixed-O&M assumption here
# avoids double-counting rather than guessing a number with no source.
CONNECTION_FIXED_OM_AUD_MW_YR: float = 0.0

# ── Price escalation / indexation ───────────────────────────────────────────────
# Shared by Scenario.price_escalation_rate (multi-year dispatch/merchant prices)
# and ProjectFinanceInputs' cost_inflation / ppa_indexation / nonsolar_price_inflation
# defaults (already numerically identical pre-refactor; this just gives the
# shared value one name instead of three coincidentally-equal literals).
PRICE_ESCALATION_RATE: float = 0.025

# ── Green certificate (LGC) price ───────────────────────────────────────────────
# Shared by Scenario.lgc_price_aud_mwh and ProjectFinanceInputs.lgc_price.
# Inherited from the same Aus247RE_FM figure on both sides, NOT a market quote —
# see Scenario.lgc_price_aud_mwh's docstring on the RET winding down to 2030.
LGC_PRICE_AUD_MWH: float = 5.0

# ── Discount rate / project life ────────────────────────────────────────────────
# Shared by Scenario.discount_rate / project_life_yrs and ProjectFinanceInputs.
# discount_rate / operating_life (already numerically identical pre-refactor).
DISCOUNT_RATE: float = 0.08
PROJECT_LIFE_YRS: int = 30
# Scenario-only: sizing LP hurdle rate (the LP only builds capacity that clears
# this return). No ProjectFinanceInputs equivalent.
TARGET_IRR: float = 0.10

# ── Debt ─────────────────────────────────────────────────────────────────────────
# ProjectFinanceInputs-only (no Scenario equivalent — Scenario has no debt model).
DEBT_TENOR_YRS: int = 15
# Gohdes (2026) prices a 5-year Facility A (415bp swap + 140bp contracted
# credit spread = 555bp) that then refinances every 5 years, against a 25-year
# notional amortisation profile. This repo models a single 15-year tenor with
# no refinancing, so copying the paper's 555bp directly would understate the
# cost of a 15-year fixed rate (which legitimately prices above a 5-year one)
# -- a modelling error, not a correction. DEBT_RATE (5.75%, down from the
# legacy 6.50%) is instead a blended-over-life proxy approximating the paper's
# refinancing path: 555bp for years 1-5, then a step-up to Facility B's 563bp
# (433 + 130bp) for years 6-15, plus an allowance for refinancing fees
# amortised over the 15-year tenor. This is a judgement call, not an exact
# recomputation of the paper's schedule -- do not "correct" it back to 5.55%;
# see docs/financial_assumptions.md Phase 3 for the full reasoning.
DEBT_RATE: float = 0.0575
DSCR_CONTRACTED: float = 1.35
DSCR_UNCONTRACTED: float = 2.40
MAX_GEARING_CONTRACTED: float = 0.80
MAX_GEARING_UNCONTRACTED: float = 0.50

# ── Depreciation & tax ────────────────────────────────────────────────────────────
# ProjectFinanceInputs-only. See docs/financial_assumptions.md Phase 4 on the
# paper's straight-line 30-year tax life versus this repo's tax_depreciation_rate.
BOOK_DEPRECIATION_RATE: float = 0.04
TAX_DEPRECIATION_RATE: float = 0.10
CORP_TAX_RATE: float = 0.30

"""Shared financial and technical benchmark constants.

Source notes are in ``docs/financial_assumptions.md``. Scenario defaults use
GenCost capex; standalone ProjectFinanceInputs retain legacy Aus247RE_FM values.
Keep those sets separate until the discrepancy is reconciled.
"""
from __future__ import annotations

# ── GenCost 2025-26 Final Report, Appendix Tables B.1 and B.5 ────────────────
# Current-policies 2025 values in real 2025 A$. Overnight costs exclude
# connection, which is represented separately below.
WIND_CAPEX_AUD_KW: float = 3_248.0    # GenCost T.B1 "Wind" 2025
PV_CAPEX_AUD_KW: float = 1_621.0      # GenCost T.B1 "Large scale solar PV" 2025
BESS_CAPEX_AUD_KWH: float = 385.0     # GenCost T.B5 4-hour total (battery 265 + BOP 120)

# ── Legacy Aus247RE_FM ProjectFinanceInputs defaults ────────────────────────
PFI_WIND_BUILD_COST_AUD_M_MW: float = 2.9        # A$m/MW  (2,900 A$/kW)
PFI_PV_BUILD_COST_AUD_M_MW: float = 1.7186       # A$m/MW  (1,718.6 A$/kW)
PFI_BESS_BUILD_COST_AUD_M_MWH: float = 0.2765    # A$m/MWh (276.5 A$/kWh)

# ── Connection cost ─────────────────────────────────────────────────────────
# Charged separately from overnight generation capex.
CONNECTION_COST_AUD_MW: float = 150_000.0
PFI_ONSW_CONNECTION_COST_AUD_M_MW: float = 0.15
PFI_PV_CONNECTION_COST_AUD_M_MW: float = 0.15
PFI_BESS_CONNECTION_COST_AUD_M_MWH: float = 0.0225

# ── Development cost (devex) ─────────────────────────────────────────────────
# PFI values remain literals to preserve their original defaults exactly.
DEVEX_PCT_OF_CAPEX: float = 0.10
PFI_WIND_DEVEX_AUD_M_MW: float = 0.29          # = 0.10 x 2.9
PFI_PV_DEVEX_AUD_M_MW: float = 0.17186         # = 0.10 x 1.7186
PFI_BESS_DEVEX_AUD_M_MWH: float = 0.02765      # = 0.10 x 0.2765

# ── Fixed O&M — A$/MW (A$/MWh for BESS) p.a. ─────────────────────────────────
# Shared by the sizing LP, unlevered model and ProjectFinanceInputs defaults.
# Wind: Gohdes (2026), AJARE Table 2. PV: GenCost 2025-26 Table B.9.
# BESS retains the legacy Aus247RE_FM value; no published source is verified.
WIND_FIXED_OM_AUD_MW_YR: float = 28_512.0     # Gohdes (2026) Table 2
PV_FIXED_OM_AUD_MW_YR: float = 12_000.0       # confirmed: GenCost 2025-26 Table B.9
BESS_FIXED_OM_AUD_MWH_YR: float = 10_500.0    # UNVERIFIED — legacy Aus247RE_FM
ANCILLARY_PCT_OF_REVENUE: float = 0.01        # Gohdes (2026) Table 2
# Additional Gohdes (2026) Table 2 costs used by the LP and unlevered model.
# The levered model has no corresponding maintenance-capex or variable-O&M lines.
MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA: float = 0.0005   # 0.05% of capex p.a., Gohdes Table 2
WIND_VARIABLE_OM_AUD_MWH: float = 0.0               # Gohdes Table 2 (wind: nil)
# No verified fixed-O&M source exists for a connection asset. Network charges
# are modelled separately as transmission costs.
CONNECTION_FIXED_OM_AUD_MW_YR: float = 0.0

# ── Price escalation / indexation ────────────────────────────────────────────
PRICE_ESCALATION_RATE: float = 0.025

# ── Green certificate (LGC) price ────────────────────────────────────────────
# Aus247RE_FM assumption, not a market quote.
LGC_PRICE_AUD_MWH: float = 5.0

# ── Discount rate / project life ────────────────────────────────────────────
DISCOUNT_RATE: float = 0.08
PROJECT_LIFE_YRS: int = 30
# Scenario-only: sizing LP hurdle rate (the LP only builds capacity that clears
# this return). No ProjectFinanceInputs equivalent.
TARGET_IRR: float = 0.10

# ── Debt ────────────────────────────────────────────────────────────────────
# ProjectFinanceInputs-only; Scenario has no debt model.
DEBT_TENOR_YRS: int = 15
# ProjectFinanceInputs-only. Gohdes (2026) uses five-year refinancing; this
# model's single 15-year tenor uses a blended proxy with rate step-up and fees.
# See docs/financial_assumptions.md for the calculation.
DEBT_RATE: float = 0.0575
DSCR_CONTRACTED: float = 1.35
DSCR_UNCONTRACTED: float = 2.40
MAX_GEARING_CONTRACTED: float = 0.80
MAX_GEARING_UNCONTRACTED: float = 0.50

# ── Depreciation & tax ────────────────────────────────────────────────────────────
# ProjectFinanceInputs-only. Tax depreciation differs from the 30-year
# straight-line book depreciation assumption.
BOOK_DEPRECIATION_RATE: float = 0.04
TAX_DEPRECIATION_RATE: float = 0.10
CORP_TAX_RATE: float = 0.30

"""Phase 1 acceptance test (TASK_financial_assumptions_refactor.md): once
`Scenario` and `ProjectFinanceInputs` both read their shared constants from
`ppa.assumptions`, `project_finance_inputs_from_scenario(Scenario())` must not
silently disagree with a bare `ProjectFinanceInputs()` on any field the two
dataclasses are meant to represent identically -- this is what stops the two
drifting apart again.

Capex and fixed-O&M are deliberately excluded from the "aligned fields" check:
GenCost (what `Scenario` uses) and the legacy Aus247RE_FM figures (`Project
FinanceInputs`' own standalone defaults) genuinely still disagree. That is the
documented, flagged state TASK_financial_assumptions_refactor.md Phase 4 exists
to resolve -- asserting them equal here would just paper over the bug this
whole refactor was written to surface. See docs/financial_assumptions.md.
"""
from __future__ import annotations

from ppa import assumptions as A
from ppa.financial_model import ProjectFinanceInputs, project_finance_inputs_from_scenario
from ppa.scenario import Scenario

# (ProjectFinanceInputs field, Scenario field) pairs that represent the same
# real-world assumption and must always agree once both dataclasses default to
# ppa.assumptions constants.
_ALIGNED_FIELD_PAIRS = [
    ("operating_life", "project_life_yrs"),
    ("discount_rate", "discount_rate"),
    ("ppa_tariff", "ppa_price"),
    ("penalty_multiple", "pen_mult"),
    ("cost_inflation", "price_escalation_rate"),
    ("ppa_indexation", "price_escalation_rate"),
    ("nonsolar_price_inflation", "price_escalation_rate"),
    ("lgc_price", "lgc_price_aud_mwh"),
]


def test_bare_defaults_agree_on_every_aligned_field():
    """The two dataclasses' own bare defaults, with no seeding involved."""
    s = Scenario()
    p = ProjectFinanceInputs()
    for pfi_field, scenario_field in _ALIGNED_FIELD_PAIRS:
        assert getattr(p, pfi_field) == getattr(s, scenario_field), (
            f"ProjectFinanceInputs.{pfi_field} ({getattr(p, pfi_field)!r}) != "
            f"Scenario.{scenario_field} ({getattr(s, scenario_field)!r})"
        )


def test_seeded_pfi_agrees_with_bare_defaults_on_every_aligned_field():
    """`project_finance_inputs_from_scenario` must not introduce drift either."""
    seeded = project_finance_inputs_from_scenario(Scenario())
    bare = ProjectFinanceInputs()
    for pfi_field, _ in _ALIGNED_FIELD_PAIRS:
        assert getattr(seeded, pfi_field) == getattr(bare, pfi_field), pfi_field


def test_scenario_and_pfi_both_read_the_single_source_module():
    """Not just equal by coincidence -- both sides reference the same named
    constant, so a future edit to one cannot silently drift from the other."""
    s = Scenario()
    p = ProjectFinanceInputs()

    assert s.discount_rate == A.DISCOUNT_RATE
    assert s.project_life_yrs == A.PROJECT_LIFE_YRS
    assert s.target_irr == A.TARGET_IRR
    assert s.opex_rate == A.OPEX_RATE
    assert s.devex_pct_of_capex == A.DEVEX_PCT_OF_CAPEX
    assert s.connection_cost_aud_mw == A.CONNECTION_COST_AUD_MW
    assert s.wind_capex_per_kw == A.WIND_CAPEX_AUD_KW
    assert s.pv_capex_per_kw == A.PV_CAPEX_AUD_KW
    assert s.bess_capex_per_kwh == A.BESS_CAPEX_AUD_KWH
    assert s.price_escalation_rate == A.PRICE_ESCALATION_RATE
    assert s.lgc_price_aud_mwh == A.LGC_PRICE_AUD_MWH

    assert p.discount_rate == A.DISCOUNT_RATE
    assert p.operating_life == A.PROJECT_LIFE_YRS
    assert p.debt_tenor == A.DEBT_TENOR_YRS
    assert p.debt_rate == A.DEBT_RATE
    assert p.dscr_contracted == A.DSCR_CONTRACTED
    assert p.dscr_uncontracted == A.DSCR_UNCONTRACTED
    assert p.max_gearing_contracted == A.MAX_GEARING_CONTRACTED
    assert p.max_gearing_uncontracted == A.MAX_GEARING_UNCONTRACTED
    assert p.book_depreciation_rate == A.BOOK_DEPRECIATION_RATE
    assert p.tax_depreciation_rate == A.TAX_DEPRECIATION_RATE
    assert p.corp_tax_rate == A.CORP_TAX_RATE
    assert p.ancillary_pct == A.ANCILLARY_PCT_OF_REVENUE
    assert p.lgc_price == A.LGC_PRICE_AUD_MWH


def test_capex_and_fixed_om_disagreement_is_documented_not_silent():
    """Known, flagged disagreement (GenCost vs legacy Aus247RE_FM) -- not a
    regression. If this ever starts failing (the two sides converge), update
    docs/financial_assumptions.md Phase 4 and move the field into
    _ALIGNED_FIELD_PAIRS above."""
    seeded = project_finance_inputs_from_scenario(Scenario())
    bare = ProjectFinanceInputs()
    assert seeded.onsw_build_cost != bare.onsw_build_cost
    assert seeded.pv_build_cost != bare.pv_build_cost
    assert seeded.bess_build_cost != bare.bess_build_cost
    assert seeded.onsw_fixed_om != bare.onsw_fixed_om

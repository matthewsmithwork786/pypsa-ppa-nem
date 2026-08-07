"""Sensitivity analysis helpers for the project-finance model.

All parameters here are pure financial-model inputs — no PyPSA re-run is
needed. Parameters that would require a new optimisation (capacities,
delivery share, BESS round-trip efficiency) are intentionally excluded.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ppa.financial_model import (
    EnergyInputs,
    ProjectFinanceInputs,
    ProjectFinanceResult,
    run_project_finance,
)


# ── Tornado parameter catalogue ────────────────────────────────────────────────

@dataclass(frozen=True)
class SensParam:
    """Specification of one sensitivity parameter.

    Also the single source for the "What-if" form in
    ui/tabs/sensitivity_analysis.py, which used to hand-write a `st.number_input`
    and a `dataclasses.replace()` keyword per field -- three parallel lists that
    had already drifted (the tab exposed devex fields this catalogue didn't
    carry). `step`/`widget_fmt`/`pct` are exactly what that form needs and
    nothing it doesn't: everything about how the field displays lives here once.
    """
    label: str              # human-readable name for charts/tables and the what-if form
    field: str               # field name on ProjectFinanceInputs
    group: str                # grouping for display (CAPEX, OPEX, Debt, Revenue, …)
    pct: float = 25.0        # default ±% range around the base value, for the tornado
    fmt: str = ".2f"         # numeric format for tornado/table display (Python format-spec)
    step: float = 0.1        # st.number_input step, for the what-if form
    widget_fmt: "str | None" = None   # st.number_input format (printf-style), or None for its default
    pct_display: bool = False  # what-if form: edit as a percent (value is a 0-1 fraction)


# Full catalogue — ordered by group then impact
PARAMS: list[SensParam] = [
    # CAPEX
    SensParam("Wind build cost (A$m/MW)",  "onsw_build_cost",   "CAPEX", step=0.05, widget_fmt="%.3f"),
    SensParam("Solar build cost (A$m/MW)", "pv_build_cost",     "CAPEX", step=0.05, widget_fmt="%.3f"),
    SensParam("BESS build cost (A$m/MWh)", "bess_build_cost",   "CAPEX", step=0.05, widget_fmt="%.3f"),
    # OPEX
    SensParam("Wind fixed O&M (A$m/MW)",   "onsw_fixed_om",     "OPEX", step=0.005, widget_fmt="%.4f"),
    SensParam("Solar fixed O&M (A$m/MW)",  "pv_fixed_om",       "OPEX", step=0.005, widget_fmt="%.4f"),
    SensParam("BESS fixed O&M (A$m/MWh)",  "bess_fixed_om",     "OPEX", step=0.005, widget_fmt="%.4f"),
    SensParam("Ancillary cost (% rev)",   "ancillary_pct",     "OPEX", step=0.1, widget_fmt="%.2f", pct_display=True),
    # Revenue
    SensParam("PPA tariff (A$/MWh)",       "ppa_tariff",        "Revenue", step=1.0),
    SensParam("Penalty multiple (×)",     "penalty_multiple",  "Revenue", pct=30, step=0.1, widget_fmt="%.2f"),
    SensParam("LGC / GO price (A$/MWh)",   "lgc_price",         "Revenue", pct=50, step=0.5),
    # Devex (uplift on capex, at FID)
    SensParam("Wind devex (× capex)",      "onsw_devex",        "Devex", step=0.01, widget_fmt="%.3f"),
    SensParam("Solar devex (× capex)",     "pv_devex",          "Devex", step=0.01, widget_fmt="%.3f"),
    SensParam("BESS devex (× capex)",      "bess_devex",        "Devex", step=0.01, widget_fmt="%.3f"),
    # Indexation
    SensParam("PPA indexation (%/yr)",    "ppa_indexation",    "Indexation", pct=50, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("Cost inflation (%/yr)",    "cost_inflation",    "Indexation", pct=50, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("Solar price infl. (%/yr)", "solar_price_inflation",   "Indexation", pct=50, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("Non-solar price infl. (%/yr)", "nonsolar_price_inflation", "Indexation", pct=50, step=0.1, widget_fmt="%.2f", pct_display=True),
    # Debt & sizing
    SensParam("Debt rate (%)",            "debt_rate",         "Debt", pct=20, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("Debt tenor (yrs)",         "debt_tenor",        "Debt", pct=20, step=1),
    SensParam("DSCR — contracted",        "dscr_contracted",   "Debt", pct=20, step=0.05, widget_fmt="%.2f"),
    SensParam("DSCR — uncontracted",      "dscr_uncontracted", "Debt", pct=20, step=0.05, widget_fmt="%.2f"),
    SensParam("Max gearing — contracted", "max_gearing_contracted",   "Debt", pct=15, step=1.0, widget_fmt="%.1f", pct_display=True),
    SensParam("Max gearing — uncontracted", "max_gearing_uncontracted", "Debt", pct=20, step=1.0, widget_fmt="%.1f", pct_display=True),
    # Tax & depreciation
    SensParam("Corporate tax rate",       "corp_tax_rate",     "Tax / Dep.", pct=25, step=1.0, widget_fmt="%.1f", pct_display=True),
    SensParam("Book depreciation rate",   "book_depreciation_rate", "Tax / Dep.", pct=25, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("Tax depreciation rate",    "tax_depreciation_rate",  "Tax / Dep.", pct=25, step=0.1, widget_fmt="%.2f", pct_display=True),
    SensParam("WACC / discount rate",     "discount_rate",     "Tax / Dep.", pct=20, step=0.1, widget_fmt="%.2f", pct_display=True),
]

PARAM_BY_FIELD: dict[str, SensParam] = {p.field: p for p in PARAMS}


# ── Core helpers ───────────────────────────────────────────────────────────────

def run_what_if(
    base_energy: EnergyInputs,
    base_finance: ProjectFinanceInputs,
    annual_energy: list[EnergyInputs] | None = None,
    **overrides: Any,
) -> ProjectFinanceResult:
    """Run the financial model with *overrides* applied to *base_finance*.

    Keys of *overrides* must be valid field names on :class:`ProjectFinanceInputs`.
    No PyPSA re-run is required — only financial parameters are modified.
    `annual_energy`, if given, is passed straight through to
    `run_project_finance` so sensitivity results are perturbations around the
    same real per-year baseline the Financial Model tab shows, not a
    different (averaged-year) one.
    """
    finance = dataclasses.replace(base_finance, **overrides)
    return run_project_finance(finance, base_energy, annual_energy=annual_energy)


@dataclass
class TornadoRow:
    param: str
    field: str
    group: str
    base_val: float
    low_val: float
    high_val: float
    low_metric: float
    high_metric: float

    @property
    def swing(self) -> float:
        return abs(self.high_metric - self.low_metric)


def run_tornado(
    base_energy: EnergyInputs,
    base_finance: ProjectFinanceInputs,
    params: list[SensParam] | None = None,
    metric: str = "project_irr",
    min_swing_fraction: float = 0.001,
    annual_energy: list[EnergyInputs] | None = None,
) -> tuple[list[TornadoRow], float, list[TornadoRow]]:
    """Vary each parameter independently and collect *metric* at low and high.

    For each param the range is ``base_value ± param.pct%``.

    Returns ``(active_rows, base_metric, zero_swing_rows)`` where:
    - *active_rows*: sorted by swing descending, swing >= min_swing_fraction * base
    - *zero_swing_rows*: params with negligible swing (wrong metric or constraint not binding)
    """
    if params is None:
        params = PARAMS

    base_result = run_project_finance(base_finance, base_energy, annual_energy=annual_energy)
    base_val = float(getattr(base_result, metric))
    threshold = abs(base_val) * min_swing_fraction

    rows: list[TornadoRow] = []
    zero_rows: list[TornadoRow] = []

    for p in params:
        bv = float(getattr(base_finance, p.field))
        delta = bv * p.pct / 100.0
        lo = bv - delta
        hi = bv + delta
        field_type = type(getattr(base_finance, p.field))
        if field_type is int:
            lo = max(int(round(lo)), 1)
            hi = int(round(hi))

        r_lo = run_what_if(base_energy, base_finance, annual_energy, **{p.field: lo})
        r_hi = run_what_if(base_energy, base_finance, annual_energy, **{p.field: hi})
        row = TornadoRow(
            param=p.label,
            field=p.field,
            group=p.group,
            base_val=bv,
            low_val=lo,
            high_val=hi,
            low_metric=float(getattr(r_lo, metric)),
            high_metric=float(getattr(r_hi, metric)),
        )
        if row.swing >= threshold:
            rows.append(row)
        else:
            zero_rows.append(row)

    rows.sort(key=lambda r: r.swing, reverse=True)
    return rows, base_val, zero_rows


def tornado_to_dataframe(rows: list[TornadoRow], base_val: float, metric: str) -> pd.DataFrame:
    """Tidy DataFrame suitable for display or export."""
    is_pct = metric in ("project_irr", "equity_irr", "gearing")
    scale = 100.0 if is_pct else 1.0
    unit = "%" if is_pct else ""
    records = []
    for r in rows:
        records.append({
            "Group": r.group,
            "Parameter": r.param,
            "Base": r.base_val,
            "Low (−)": r.low_val,
            "High (+)": r.high_val,
            f"Result @ low {unit}".strip(): r.low_metric * scale,
            f"Result @ high {unit}".strip(): r.high_metric * scale,
            f"Swing {unit}".strip(): r.swing * scale,
        })
    return pd.DataFrame(records)

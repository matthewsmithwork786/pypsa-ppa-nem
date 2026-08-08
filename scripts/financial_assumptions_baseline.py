#!/usr/bin/env python3
"""Reproduces the sizing + dispatch + financial-model pipeline used to record
every phase's before/after numbers in docs/financial_assumptions.md
(TASK_financial_assumptions_refactor.md). Not part of the app; run by hand
after any change to ppa/assumptions.py to regenerate the comparison, per
AGENTS.md §5.6 ("record results where they survive" -- a prior round reported
benchmarks as "recorded in the commit message" when they had never been run).

    python3 scripts/financial_assumptions_baseline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import dataclasses

from ppa.scenario import Scenario
from ppa.data import nem_data
from ppa.sizing import build_sizing_timeseries, optimise_capacities, weather_cycle_years
from ppa.multi_year import run_multi_year
from ppa.financials import run_multi_year_financial_analysis
from ppa.financial_model import (
    ProjectFinanceInputs,
    energy_inputs_from_result,
    run_project_finance,
)

# Default Scenario(), pointed at real cached NEM data (same DUIDs the case
# studies use) so the sizing LP has real generation/price series to work with.
scn = dataclasses.replace(
    Scenario(),
    optimise_capacity=True,
    data_source="nem_map",
    nem_wind_duid="COLWF01",
    nem_pv_duid="SUNRSF1",
    nem_price_region="NSW1",
    simulation_years=1,
)

pv_by_year, wind_by_year, prices_by_year = nem_data.get_timeseries_dicts(scn)
n_sizing_years, _ = weather_cycle_years(scn.simulation_years, len(pv_by_year), len(prices_by_year))
sizing_ts = build_sizing_timeseries(scn, pv_by_year, wind_by_year, prices_by_year, n_sizing_years)

print("=== Sizing LP (default Scenario(), tsam, real NEM data) ===")
sized = optimise_capacities(sizing_ts, scn)
print(f"status={sized.status} condition={sized.condition}")
print(f"wind_mw={sized.onsw_mw:.2f}")
print(f"pv_mw={sized.pv_mw:.2f}")
print(f"bess_mw={sized.bess_mw:.2f} bess_mwh={sized.bess_mwh:.2f}")
print(f"wind_link_mw={sized.wind_link_mw:.2f} pvbess_link_mw={sized.pvbess_link_mw:.2f} sell_link_mw={sized.sell_link_mw:.2f}")

from ppa.sizing import apply_sizing

scn_sized = apply_sizing(scn, sized)

print("\n=== Dispatch (1 year, sized fleet) ===")
results = run_multi_year(
    scenario=scn_sized,
    pv_cf_by_year=pv_by_year,
    wind_cf_by_year=wind_by_year,
    prices_by_year=prices_by_year,
    load_mw_by_year=None,
    first_sim_year=scn_sized.first_sim_year,
    max_workers=1,
)
fin = run_multi_year_financial_analysis(scn_sized, results, first_sim_year=scn_sized.first_sim_year)
print(f"npv={fin.npv/1e6:.3f}m irr={fin.irr:.4f} lcoe={fin.lcoe:.2f} simple_payback={fin.simple_payback:.2f}")
print(f"capex_total={fin.capex.capex_total/1e6:.3f}m total_investment={fin.capex.total_investment/1e6:.3f}m annual_opex={fin.annual_opex/1e6:.4f}m")

energy = energy_inputs_from_result(results[0])
print("\n=== EnergyInputs (from dispatch, year 1) ===")
for f in dataclasses.fields(energy):
    print(f"  {f.name} = {getattr(energy, f.name)!r}")

print("\n=== Levered financial model: default ProjectFinanceInputs() (NOT seeded from Scenario) ===")
p = ProjectFinanceInputs()
result = run_project_finance(p, energy)
print(f"project_irr={result.project_irr:.4f}")
print(f"equity_irr={result.equity_irr:.4f}")
print(f"npv_project={result.npv_project:.3f}m")
print(f"gearing={result.gearing:.4f}")
print(f"total_capex={result.total_capex:.3f}m")
print(f"total_debt={result.total_debt:.3f}m")
print(f"total_equity={result.total_equity:.3f}m")
print(f"min_dscr={result.min_dscr:.4f} avg_dscr={result.avg_dscr:.4f}")
print(f"payback_years={result.payback_years:.2f}")
print(f"lcoe={result.lcoe:.2f}")

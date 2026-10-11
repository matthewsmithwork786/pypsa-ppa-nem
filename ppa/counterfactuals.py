from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ppa.results import OptimisationResult
from ppa.scenario import Scenario


@dataclass
class CounterfactualResult:
    total_load_mwh: float

    # Period all-in costs ($)
    spot_cost: float
    cal_cost: float
    blended_cost: float
    ppa_offtaker_cost: float

    # Effective $/MWh for each strategy
    spot_avg_price: float
    cal_avg_price: float
    blended_avg_price: float
    ppa_effective_price: float

    # Savings relative to PPA (positive = that strategy is more expensive than PPA)
    ppa_saving_vs_spot: float
    ppa_saving_vs_blended: float

    # Hourly cumulative cost series (aligned to ts index, for time-series chart)
    cumulative_spot: pd.Series
    cumulative_ppa: pd.Series
    cumulative_cal: pd.Series


def compute_counterfactuals(
    ts: pd.DataFrame,
    scenario: Scenario,
    result: OptimisationResult,
    dt: float = 1.0,
) -> CounterfactualResult:
    """Compare modelled-period PPA costs with spot and calendar-futures costs.

    All strategies use the actual hourly load in ``ts['ppaload_mw']``. Costs
    cover the modelled period and are not annualised.
    """
    spot_price = ts["ts_MktPrice"]
    load_mw = ts["ppaload_mw"]
    # dt = hours per row of ts; must match whatever build_network(resolution_h=)
    # used for this same ts, or every $/MWh figure below is scaled wrong.
    total_load_mwh = float((load_mw * dt).sum())

    # --- Spot-only ---
    hourly_spot_cost = spot_price * load_mw * dt
    spot_cost = float(hourly_spot_cost.sum())

    # --- Base-futures fully hedged ---
    cal_cost = scenario.cal_forward_price * total_load_mwh

    # --- Blended (hedge_fraction at the base-futures price, remainder at spot) ---
    # cal_hedge_fraction = 0 → pure spot; = 1 → fully hedged at base futures
    blended_cost = (
        scenario.cal_hedge_fraction * cal_cost
        + (1.0 - scenario.cal_hedge_fraction) * spot_cost
    )

    # --- PPA (offtaker view) ---
    # Pay ppa_price for each MWh delivered; cover any undelivered load at real-time spot.
    ppa_delivery = result.dispatch.ppa_delivery  # hourly MW delivered by IPP
    undelivered = (load_mw - ppa_delivery).clip(lower=0.0)
    hourly_ppa_cost = (
        scenario.ppa_price * ppa_delivery * dt
        + spot_price * undelivered * dt
    )
    ppa_offtaker_cost = float(hourly_ppa_cost.sum())

    # --- Effective $/MWh ---
    spot_avg_price = spot_cost / total_load_mwh if total_load_mwh > 0 else 0.0
    blended_avg_price = blended_cost / total_load_mwh if total_load_mwh > 0 else 0.0
    ppa_effective_price = ppa_offtaker_cost / total_load_mwh if total_load_mwh > 0 else 0.0

    # --- Savings vs PPA (positive = that strategy costs more than PPA) ---
    ppa_saving_vs_spot = spot_cost - ppa_offtaker_cost
    ppa_saving_vs_blended = blended_cost - ppa_offtaker_cost

    # --- Cumulative cost series ---
    hourly_cal_cost = scenario.cal_forward_price * load_mw * dt
    cumulative_spot = hourly_spot_cost.cumsum()
    cumulative_ppa = hourly_ppa_cost.cumsum()
    cumulative_cal = hourly_cal_cost.cumsum()

    return CounterfactualResult(
        total_load_mwh=total_load_mwh,
        spot_cost=spot_cost,
        cal_cost=cal_cost,
        blended_cost=blended_cost,
        ppa_offtaker_cost=ppa_offtaker_cost,
        spot_avg_price=spot_avg_price,
        cal_avg_price=scenario.cal_forward_price,
        blended_avg_price=blended_avg_price,
        ppa_effective_price=ppa_effective_price,
        ppa_saving_vs_spot=ppa_saving_vs_spot,
        ppa_saving_vs_blended=ppa_saving_vs_blended,
        cumulative_spot=cumulative_spot,
        cumulative_ppa=cumulative_ppa,
        cumulative_cal=cumulative_cal,
    )

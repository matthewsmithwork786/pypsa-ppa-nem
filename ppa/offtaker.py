from __future__ import annotations

import numpy as np
import pandas as pd


def run_of_plant_costs(
    load: pd.Series,
    wind: pd.Series,
    solar: pd.Series,
    spot: pd.Series,
    wind_price: float,
    solar_price: float,
    dt: float = 1.0,
) -> pd.DataFrame:
    """Settle all contracted generation, with residual load and surplus at spot.

    Inputs are MW and A$/MWh; output energy and costs cover each interval.
    Generation must be sent-out production, not unconstrained availability.
    Battery services and changes to generation dispatch are outside this view.
    """
    inputs = [load, wind, solar, spot]
    if load.empty or any(not s.index.equals(load.index) for s in inputs):
        raise ValueError("Load, generation and prices must have the same non-empty index.")
    if not np.isfinite([wind_price, solar_price, dt]).all() or dt <= 0:
        raise ValueError("Contract prices must be finite and interval duration must be positive.")
    if any(not np.isfinite(s.to_numpy(dtype=float)).all() for s in inputs):
        raise ValueError("Load, generation and prices must be finite.")
    if any((s < -1e-6).any() for s in [load, wind, solar]):
        raise ValueError("Load and generation must be non-negative.")

    df = pd.DataFrame({
        "Load (MWh)": load.clip(lower=0) * dt,
        "Wind (MWh)": wind.clip(lower=0) * dt,
        "Solar (MWh)": solar.clip(lower=0) * dt,
    })
    generation = df["Wind (MWh)"] + df["Solar (MWh)"]
    df["Matched (MWh)"] = np.minimum(df["Load (MWh)"], generation)
    df["Shortfall (MWh)"] = (df["Load (MWh)"] - generation).clip(lower=0)
    df["Surplus (MWh)"] = (generation - df["Load (MWh)"]).clip(lower=0)
    df["Wind PPA cost (A$)"] = df["Wind (MWh)"] * wind_price
    df["Solar PPA cost (A$)"] = df["Solar (MWh)"] * solar_price
    df["Shortfall cost (A$)"] = df["Shortfall (MWh)"] * spot
    df["Surplus revenue (A$)"] = df["Surplus (MWh)"] * spot
    df["Negative-price surplus cost (A$)"] = -df["Surplus revenue (A$)"].clip(upper=0)
    df["Net procurement cost (A$)"] = (
        df["Wind PPA cost (A$)"] + df["Solar PPA cost (A$)"]
        + df["Shortfall cost (A$)"] - df["Surplus revenue (A$)"]
    )
    df["Spot-only cost (A$)"] = df["Load (MWh)"] * spot
    df["Wind spot value (A$)"] = df["Wind (MWh)"] * spot
    df["Solar spot value (A$)"] = df["Solar (MWh)"] * spot
    return df

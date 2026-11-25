"""Financial settlement over a completed hourly procurement run."""
from dataclasses import dataclass

import numpy as np
import pandas as pd


FIVE_MINUTE_SETTLEMENT = pd.Timestamp("2021-10-01")


@dataclass(frozen=True)
class Cap:
    mw: float
    strike: float
    premium: float


@dataclass(frozen=True)
class Swap:
    mw: float
    fixed_price: float
    start_hour: int = 0
    end_hour: int = 24

    def applies(self, hours):
        if self.start_hour < self.end_hour:
            return (hours >= self.start_hour) & (hours < self.end_hour)
        return (hours >= self.start_hour) | (hours < self.end_hour)


def historical_cap_prices(prices: pd.Series) -> pd.Series:
    """Use half-hourly dispatch-price averages as a proxy before five-minute settlement."""
    earlier = prices.loc[prices.index < FIVE_MINUTE_SETTLEMENT]
    if earlier.empty:
        return prices
    half_hour_average = earlier.groupby(earlier.index.floor("30min")).transform("mean")
    return pd.concat([half_hour_average, prices.loc[prices.index >= FIVE_MINUTE_SETTLEMENT]])


def settle_hedges(dispatch, caps, swaps, cap_prices):
    """Return hourly residual costs, contract cash flows and overhedge exposure."""
    hourly = dispatch[["Load", "Spot price", "Spot purchases", "Excess sold"]].copy()
    hourly["Net spot exposure (MW)"] = hourly["Spot purchases"] - hourly["Excess sold"]
    hourly["Before hedges"] = hourly["Net spot exposure (MW)"] * hourly["Spot price"]
    hourly["Net hedge cost"] = 0.0

    # Constant hourly dispatch is the reference for exposure within the hour.
    # Swap cash flows are linear in price and can settle on the hourly mean.
    interval_hours = len(hourly) / len(cap_prices)
    expected = pd.date_range(hourly.index[0], periods=len(cap_prices),
                             freq=pd.Timedelta(hours=interval_hours))
    cap_hour = cap_prices.index.floor("h")
    if (not cap_prices.index.as_unit("ns").equals(expected.as_unit("ns"))
            or not np.isfinite(cap_prices.to_numpy()).all()
            or not np.allclose(cap_prices.groupby(cap_hour).mean().reindex(hourly.index),
                               hourly["Spot price"], rtol=1e-6, atol=1e-5)):
        raise ValueError("Cap prices must cover the dispatch hours and reconcile to their hourly averages.")

    price_sensitive_mw = np.zeros(len(cap_prices))
    cash_flows = []
    for label, cap in caps.items():
        payout_rate = (cap_prices - cap.strike).clip(lower=0)
        payout = (payout_rate * cap.mw * interval_hours).groupby(cap_hour).sum()
        premium = cap.premium * cap.mw
        hourly["Net hedge cost"] += premium - payout
        price_sensitive_mw += cap.mw * (payout_rate > 0).to_numpy()
        cash_flows.append((label, premium * len(hourly), payout.sum()))

    swap_mw = pd.Series(0.0, index=hourly.index)
    for label, swap in swaps.items():
        if swap.mw and swap.start_hour == swap.end_hour:
            raise ValueError("Choose different start and end hours for the block swap.")
        volume = swap.mw * swap.applies(hourly.index.hour)
        fixed_payment = volume * swap.fixed_price
        floating_receipt = volume * hourly["Spot price"]
        hourly["Net hedge cost"] += fixed_payment - floating_receipt
        swap_mw += volume
        cash_flows.append((label, fixed_payment.sum(), floating_receipt.sum()))

    hourly["After hedges"] = hourly["Before hedges"] + hourly["Net hedge cost"]
    price_sensitive_mw += swap_mw.reindex(cap_hour).to_numpy()
    residual_mw = hourly["Net spot exposure (MW)"].clip(lower=0).reindex(cap_hour).to_numpy()
    overhedged = (price_sensitive_mw > 0) & (price_sensitive_mw - residual_mw > 1e-6)
    contracts = pd.DataFrame(cash_flows, columns=["Contract", "Premium or fixed leg", "Floating receipt"])
    contracts["Net hedge cost"] = contracts["Premium or fixed leg"] - contracts["Floating receipt"]
    return hourly, contracts, float(overhedged.sum() * interval_hours)


def daily_residual_cost(hourly):
    """Daily residual costs per MWh of customer load for days with positive load."""
    daily = hourly[["Load", "Before hedges", "After hedges"]].resample("D").sum()
    daily = daily.loc[daily.Load > 0]
    return daily[["Before hedges", "After hedges"]].div(daily.Load, axis=0)

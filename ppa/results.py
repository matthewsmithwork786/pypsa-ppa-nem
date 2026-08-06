from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pypsa

pypsa.options.general.allow_network_requests = False
pypsa.options.params.statistics.drop_zero = True
pypsa.options.params.statistics.round = 2
pypsa.options.params.optimize.log_to_console = False
pypsa.options.params.optimize.include_objective_constant = False
pypsa.options.api.new_components_api = True

from ppa.scenario import Scenario


@dataclass
class DispatchSeries:
    wind_gen: pd.Series
    pv_gen: pd.Series
    market_buy: pd.Series
    allowed_shortfall: pd.Series
    penalty_gen: pd.Series
    market_sell: pd.Series
    bess_dispatch: pd.Series
    bess_store: pd.Series
    soc: pd.Series
    ppa_delivery: pd.Series


@dataclass
class SummaryVolumes:
    total_load_mwh: float
    ppa_delivered_mwh: float
    renewable_and_storage_to_ppa_mwh: float
    market_buy_to_ppa_mwh: float
    allowed_shortfall_mwh: float
    penalty_mwh: float
    sold_to_market_mwh: float
    wind_generation_mwh: float
    pv_generation_mwh: float
    bess_dispatch_mwh: float
    bess_charge_mwh: float
    fulfilled_share: float
    allowed_shortfall_share_actual: float
    buy_share_of_ppa_delivery: float
    penalty_share_of_load: float


@dataclass
class RevenueBreakdown:
    ppa_revenue: float
    excess_revenue: float
    market_purchase_cost: float
    penalty_cost: float
    net_revenue: float
    effective_capture_price: float
    transmission_cost: float = 0.0


@dataclass
class OptimisationResult:
    scenario: Scenario
    dispatch: DispatchSeries
    summary: SummaryVolumes
    revenue: RevenueBreakdown
    solver_status: str
    solver_condition: str
    n_period_hours: float
    market_prices: pd.Series = None  # type: ignore[assignment]
    link_utilisation: pd.DataFrame = None  # type: ignore[assignment]


def _extract_link_utilisation(n: pypsa.Network) -> pd.DataFrame:
    static = n.links.static
    if static.empty:
        return pd.DataFrame()
    if "p_nom_opt" in static.columns:
        sized = static["p_nom_opt"].fillna(0.0)
    else:
        sized = static["p_nom"]
    rows = []
    for name in static.index:
        cap = float(sized[name]) if name in sized.index else 0.0
        if cap <= 0:
            cap = float(static.at[name, "p_nom"]) if "p_nom" in static.columns else 0.0
        peak = 0.0
        if name in n.links.dynamic.p.columns:
            peak = float(n.links.dynamic.p[name].abs().max())
        rows.append((name, cap, peak))
    table = pd.DataFrame(rows, columns=["link", "sized_mw", "peak_flow"]).set_index("link")
    table["utilisation"] = (table["peak_flow"] / table["sized_mw"].replace(0, np.nan)).fillna(0.0)
    return table


def extract_results(
    n: pypsa.Network,
    scenario: Scenario,
    ts: pd.DataFrame,
    solver_status: str,
    solver_condition: str,
    resolution_h: float = 1.0,
) -> OptimisationResult:
    """`resolution_h` is the hours each row of `ts`/`n`'s snapshots represents
    (must match what was passed to `build_network`). All `.sum()`-based MW ->
    MWh/`$` conversions below need it: summing MW samples only equals MWh when
    each sample is exactly 1 hour, so at 30-/5-min resolution every volume and
    $-flow figure would be overstated by 2x/12x without this factor.
    """
    s = scenario

    # ── Dispatch series ───────────────────────────────────────────────────────
    wind_gen = n.generators.dynamic.p["Gen_OnshoreWind"]
    pv_gen = n.generators.dynamic.p["Gen_PV"]
    market_buy = n.generators.dynamic.p["Gen_BuyFromMarket"]
    allowed_shortfall = n.generators.dynamic.p["Gen_AllowedShortfall"]
    penalty_gen = n.generators.dynamic.p["Gen_Penalty"]
    market_sell = n.generators.dynamic.p["Gen_SellToMarket"]

    bess_dispatch = n.storage_units.dynamic.p_dispatch["SU_BESS"]
    bess_store = n.storage_units.dynamic.p_store["SU_BESS"]
    soc = n.storage_units.dynamic.state_of_charge["SU_BESS"]

    # Links: p1 is negative when supplying to bus1 — negate for positive delivered MW
    ppa_delivery = -n.links.dynamic.p1["IPPGen_to_PPAOfftake"]

    dispatch = DispatchSeries(
        wind_gen=wind_gen,
        pv_gen=pv_gen,
        market_buy=market_buy,
        allowed_shortfall=allowed_shortfall,
        penalty_gen=penalty_gen,
        market_sell=market_sell,
        bess_dispatch=bess_dispatch,
        bess_store=bess_store,
        soc=soc,
        ppa_delivery=ppa_delivery,
    )

    # ── Volumes ───────────────────────────────────────────────────────────────
    total_load_mwh = float(ts["ppaload_mw"].sum()) * resolution_h
    ppa_delivered_mwh = float(ppa_delivery.sum()) * resolution_h
    market_buy_to_ppa_mwh = float(market_buy.sum()) * resolution_h
    renewable_and_storage_to_ppa_mwh = float((ppa_delivery - market_buy).clip(lower=0).sum()) * resolution_h
    allowed_shortfall_mwh = float(allowed_shortfall.sum()) * resolution_h
    penalty_mwh = float(penalty_gen.sum()) * resolution_h
    sold_to_market_mwh = float(market_sell.sum()) * resolution_h
    wind_generation_mwh = float(wind_gen.sum()) * resolution_h
    pv_generation_mwh = float(pv_gen.sum()) * resolution_h
    bess_dispatch_mwh = float(bess_dispatch.sum()) * resolution_h
    bess_charge_mwh = float(bess_store.sum()) * resolution_h

    fulfilled_share = ppa_delivered_mwh / total_load_mwh if total_load_mwh > 0 else 0.0
    allowed_shortfall_share_actual = allowed_shortfall_mwh / total_load_mwh if total_load_mwh > 0 else 0.0
    buy_share_of_ppa_delivery = (
        market_buy_to_ppa_mwh / ppa_delivered_mwh if ppa_delivered_mwh > 0 else 0.0
    )
    penalty_share_of_load = penalty_mwh / total_load_mwh if total_load_mwh > 0 else 0.0

    summary = SummaryVolumes(
        total_load_mwh=total_load_mwh,
        ppa_delivered_mwh=ppa_delivered_mwh,
        renewable_and_storage_to_ppa_mwh=renewable_and_storage_to_ppa_mwh,
        market_buy_to_ppa_mwh=market_buy_to_ppa_mwh,
        allowed_shortfall_mwh=allowed_shortfall_mwh,
        penalty_mwh=penalty_mwh,
        sold_to_market_mwh=sold_to_market_mwh,
        wind_generation_mwh=wind_generation_mwh,
        pv_generation_mwh=pv_generation_mwh,
        bess_dispatch_mwh=bess_dispatch_mwh,
        bess_charge_mwh=bess_charge_mwh,
        fulfilled_share=fulfilled_share,
        allowed_shortfall_share_actual=allowed_shortfall_share_actual,
        buy_share_of_ppa_delivery=buy_share_of_ppa_delivery,
        penalty_share_of_load=penalty_share_of_load,
    )

    # ── Revenue ───────────────────────────────────────────────────────────────
    ppa_revenue = ppa_delivered_mwh * s.ppa_price
    excess_revenue = float((market_sell * ts["ts_MktPrice"]).sum()) * resolution_h
    market_purchase_cost = float((market_buy * ts["ts_MktPrice"]).sum()) * resolution_h
    penalty_cost = penalty_mwh * s.penalty_price
    transmission_cost = ppa_delivered_mwh * s.transmission_cost_aud_mwh
    net_revenue = (
        ppa_revenue + excess_revenue - market_purchase_cost - penalty_cost - transmission_cost
    )

    total_gen_mwh = wind_generation_mwh + pv_generation_mwh + bess_dispatch_mwh
    effective_capture_price = net_revenue / total_gen_mwh if total_gen_mwh > 0 else 0.0

    revenue = RevenueBreakdown(
        ppa_revenue=ppa_revenue,
        excess_revenue=excess_revenue,
        market_purchase_cost=market_purchase_cost,
        penalty_cost=penalty_cost,
        net_revenue=net_revenue,
        effective_capture_price=effective_capture_price,
        transmission_cost=transmission_cost,
    )

    return OptimisationResult(
        scenario=scenario,
        dispatch=dispatch,
        summary=summary,
        revenue=revenue,
        solver_status=solver_status,
        solver_condition=solver_condition,
        n_period_hours=len(ts) * resolution_h,
        market_prices=ts["ts_MktPrice"],
        link_utilisation=_extract_link_utilisation(n),
    )


def build_supply_mix_df(dispatch: DispatchSeries, ts: pd.DataFrame | None = None) -> pd.DataFrame:
    pv_direct = dispatch.pv_gen - dispatch.bess_store
    idx = ts.index if ts is not None else dispatch.wind_gen.index
    # Actual hourly PPA load, reconstructed from the dispatch balance at
    # Bus_PPAOfftake (ppa_delivery + allowed_shortfall + penalty_gen == load) --
    # the shaped load profile, not the flat contracted peak MW.
    load_mw = dispatch.ppa_delivery + dispatch.allowed_shortfall + dispatch.penalty_gen
    df = pd.DataFrame(
        {
            "Wind": dispatch.wind_gen.values,
            "PV (direct)": pv_direct.clip(lower=0).values,
            "BESS discharge": dispatch.bess_dispatch.values,
            "Buy from market": dispatch.market_buy.values,
            "BESS charging": (-dispatch.bess_store).values,
            "Load (MW)": load_mw.values,
        },
        index=idx,
    )
    df["hour"] = df.index.hour
    return df


def _time_slot(series: "pd.Series | pd.Index") -> pd.Series:
    """0..23 hour-of-day for hourly data; fractional hour for 30/5-min data
    (e.g. 07:30 -> 7.5), so a 30-min run averages onto 48 slots instead of
    collapsing onto 24 hourly points."""
    idx = pd.DatetimeIndex(series)
    return pd.Series(idx.hour + idx.minute / 60.0 + idx.second / 3600.0, index=idx)


def filter_dispatch_range(
    obj: "pd.Series | pd.DataFrame",
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    chosen_day: str | None = None,
) -> "pd.Series | pd.DataFrame":
    """Inclusive [start, end] slice of `obj` by its datetime index. With no
    explicit range and no `chosen_day`, defaults to a 7-day window from the
    first timestamp."""
    if start is None or end is None:
        if chosen_day is None:
            chosen_day = pd.Timestamp(obj.index[0]).strftime("%Y-%m-%d")
        start = pd.Timestamp(chosen_day)
        end = start + pd.Timedelta(days=7)
    return obj.loc[(obj.index >= start) & (obj.index <= end)]


def build_24h_avg(supply_mix_df: pd.DataFrame) -> pd.DataFrame:
    avg = supply_mix_df.groupby(_time_slot(supply_mix_df.index)).mean()
    avg.index.name = "slot"
    return avg.reset_index()


def build_24h_band(series: "pd.Series") -> pd.DataFrame:
    """Mean + P10/P90 by time-of-day slot for a single series."""
    g = pd.DataFrame({"slot": _time_slot(series.index), "value": series.values}).groupby("slot")["value"]
    return pd.DataFrame({"mean": g.mean(), "p10": g.quantile(0.10), "p90": g.quantile(0.90)}).reset_index()


def build_ops_day_df(dispatch: DispatchSeries, chosen_day: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PPA delivery (MW)": dispatch.ppa_delivery.loc[chosen_day].round(1),
            "Sell to market (MW)": dispatch.market_sell.loc[chosen_day].round(1),
            "Allowed shortfall (MW)": dispatch.allowed_shortfall.loc[chosen_day].round(1),
            "Penalty (MW)": dispatch.penalty_gen.loc[chosen_day].round(1),
            "BESS SoC (MWh)": dispatch.soc.loc[chosen_day].round(1),
        }
    )

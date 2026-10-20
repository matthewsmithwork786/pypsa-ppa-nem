from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    # An unused SLA tier defaults to a non-violating share of 1.0.
    min_monthly_delivery_share: float = 1.0
    min_daily_delivery_share: float = 1.0
    n_months_below_sla: int = 0
    n_days_below_sla: int = 0


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
    # Per-period delivered share D/L (index = period end, value = delivery share).
    # None when the corresponding SLA tier was off on the scenario that produced
    # this result. `warnings` carries post-solve SLA verification messages.
    monthly_delivery_share: pd.Series = None  # type: ignore[assignment]
    daily_delivery_share: pd.Series = None  # type: ignore[assignment]
    warnings: list[str] = field(default_factory=list)
    raw_dispatch: DispatchSeries | None = None
    raw_summary: SummaryVolumes | None = None
    raw_revenue: RevenueBreakdown | None = None


def merchant_sales_frame(result: OptimisationResult) -> pd.DataFrame:
    """Interval-weighted capture prices and revenue, in NEM local time (09–17 solar)."""
    d, s = result.dispatch, result.scenario
    sales = d.market_sell.clip(lower=0.0)
    dt = result.n_period_hours / len(sales) if len(sales) else 1.0
    solar = (sales.index.hour >= 9) & (sales.index.hour < 17)
    rows = []
    for label, mask in (("Solar hours (09:00–17:00)", solar), ("Non-solar hours", ~solar)):
        energy = sales.where(mask, 0.0) * dt
        mwh = float(energy.sum())
        spot_revenue = float((energy * result.market_prices).sum())
        net_revenue = spot_revenue + mwh * (s.lgc_price_aud_mwh - s.market_spread)
        rows.append({
            "Period": label, "Energy sold (MWh)": mwh,
            "Spot capture (A$/MWh)": spot_revenue / mwh if mwh > 1e-9 else float("nan"),
            "Net capture incl. LGC (A$/MWh)": net_revenue / mwh if mwh > 1e-9 else float("nan"),
            "Merchant revenue (A$)": net_revenue,
        })
    return pd.DataFrame(rows).set_index("Period")


def bess_utilisation(result: OptimisationResult) -> dict[str, float]:
    """Cycles use internal discharged MWh / usable MWh, allowing for discharge losses."""
    s, v = result.scenario, result.summary
    capacity = s.effective_bess_mwh
    cycles = (v.bess_dispatch_mwh / (capacity * s.bess_efficiency_dispatch)
              if capacity > 0 and s.bess_efficiency_dispatch > 0 else 0.0)
    days = result.n_period_hours / 24.0
    return {
        "BESS charge (GWh)": v.bess_charge_mwh / 1000.0,
        "BESS discharge (GWh)": v.bess_dispatch_mwh / 1000.0,
        "BESS usable capacity (MWh)": capacity,
        "BESS equivalent full cycles": cycles,
        "BESS cycles per day": cycles / days if days > 0 else 0.0,
    }


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
    """Extract dispatch, energy, delivery and revenue metrics from a solved network.

    `resolution_h` must match `build_network` and converts summed MW samples
    into MWh for sub-hourly runs.
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

    raw = _summarise_dispatch(dispatch, s, ts, solver_status, solver_condition, resolution_h)
    raw.link_utilisation = _extract_link_utilisation(n)
    if not s.customer_first_allocation:
        return raw
    allocated = customer_first_dispatch(dispatch, ts["ppaload_mw"], s)
    result = _summarise_dispatch(allocated, s, ts, solver_status, solver_condition, resolution_h)
    return replace(result, link_utilisation=raw.link_utilisation,
                   raw_dispatch=dispatch, raw_summary=raw.summary, raw_revenue=raw.revenue)


def customer_first_dispatch(d: DispatchSeries, load: pd.Series, s: Scenario) -> DispatchSeries:
    """Reallocate existing exports without changing physical plant operation.

    The remaining free-shortfall allowance is shared proportionally across gaps.
    Daily, monthly and annual budgets are nested, matching the solver's caps.
    Uniform interval duration cancels in these allocation ratios.
    """
    gap = (load - d.ppa_delivery).clip(lower=0)
    transfer = pd.concat([d.market_sell.clip(lower=0), gap], axis=1).min(axis=1)
    delivery = d.ppa_delivery + transfer
    gap = (load - delivery).clip(lower=0)
    day = gap.index.normalize()
    daily_gap = gap.groupby(day).sum()
    daily_load = load.groupby(day).sum()
    allowance = daily_gap.copy()
    if s.sla_daily_enabled:
        allowance = np.minimum(allowance, daily_load * (1 - s.sla_daily_share))
    if s.sla_monthly_enabled:
        month = daily_gap.index.to_period("M")
        totals = allowance.groupby(month).transform("sum")
        caps = daily_load.groupby(month).transform("sum") * (1 - s.sla_monthly_share)
        allowance *= (caps / totals.replace(0, np.nan)).clip(upper=1).fillna(0)
    year = daily_gap.index.year
    totals = allowance.groupby(year).transform("sum")
    caps = daily_load.groupby(year).transform("sum") * s.allowed_shortfall_share
    allowance *= (caps / totals.replace(0, np.nan)).clip(upper=1).fillna(0)
    fraction = (allowance / daily_gap.replace(0, np.nan)).fillna(0)
    allowed = gap * fraction.reindex(day).to_numpy()
    return replace(d, ppa_delivery=delivery, market_sell=(d.market_sell - transfer).clip(lower=0),
                   allowed_shortfall=allowed, penalty_gen=(gap - allowed).clip(lower=0))


def _summarise_dispatch(
    dispatch: DispatchSeries, s: Scenario, ts: pd.DataFrame,
    solver_status: str, solver_condition: str, resolution_h: float,
) -> OptimisationResult:
    wind_gen, pv_gen = dispatch.wind_gen, dispatch.pv_gen
    market_buy, market_sell = dispatch.market_buy, dispatch.market_sell
    allowed_shortfall, penalty_gen = dispatch.allowed_shortfall, dispatch.penalty_gen
    bess_dispatch, bess_store = dispatch.bess_dispatch, dispatch.bess_store
    ppa_delivery = dispatch.ppa_delivery

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

    # Per-period delivered share, matching the solver's minimum-delivery measure.
    delivered_mwh = ppa_delivery * resolution_h
    load_mwh = ts["ppaload_mw"] * resolution_h

    def _period_delivery_share(periods: pd.Index) -> pd.Series:
        """Delivered/load per period, indexed by the period-end timestamp."""
        deliv = delivered_mwh.groupby(periods).sum()
        load = load_mwh.groupby(periods).sum()
        share = deliv / load.replace(0.0, np.nan)
        share = share.dropna()
        share.index = share.index.to_timestamp(how="end")
        return share

    monthly_delivery_share: pd.Series | None = None
    daily_delivery_share: pd.Series | None = None
    min_monthly_delivery_share = 1.0
    min_daily_delivery_share = 1.0
    n_months_below_sla = 0
    n_days_below_sla = 0
    warnings: list[str] = []

    # A single period leaves the minimum at its non-violating default of 1.0.
    if s.sla_monthly_enabled:
        monthly_delivery_share = _period_delivery_share(ts.index.to_period("M"))
        if len(monthly_delivery_share) > 1:
            min_monthly_delivery_share = float(monthly_delivery_share.min())
            n_months_below_sla = int(
                (monthly_delivery_share < s.sla_monthly_share - 1e-6).sum()
            )
    if s.sla_daily_enabled:
        daily_delivery_share = _period_delivery_share(ts.index.to_period("D"))
        if len(daily_delivery_share) > 1:
            min_daily_delivery_share = float(daily_delivery_share.min())
            n_days_below_sla = int(
                (daily_delivery_share < s.sla_daily_share - 1e-6).sum()
            )

    # Report a warning instead of raising so results remain inspectable. This
    # also supports post-hoc reporting when no hard delivery floor was active.
    if monthly_delivery_share is not None and len(monthly_delivery_share) > 1:
        if min_monthly_delivery_share < s.sla_monthly_share - 0.005:
            warnings.append(
                f"Monthly SLA: minimum achieved delivery share "
                f"{min_monthly_delivery_share:.1%} is more than 0.5pp below the "
                f"required {s.sla_monthly_share:.0%} "
                f"({n_months_below_sla} of {len(monthly_delivery_share)} month(s) below target)."
            )
    if daily_delivery_share is not None and len(daily_delivery_share) > 1:
        if min_daily_delivery_share < s.sla_daily_share - 0.005:
            warnings.append(
                f"Daily SLA: minimum achieved delivery share "
                f"{min_daily_delivery_share:.1%} is more than 0.5pp below the "
                f"required {s.sla_daily_share:.0%} "
                f"({n_days_below_sla} of {len(daily_delivery_share)} day(s) below target)."
            )

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
        min_monthly_delivery_share=min_monthly_delivery_share,
        min_daily_delivery_share=min_daily_delivery_share,
        n_months_below_sla=n_months_below_sla,
        n_days_below_sla=n_days_below_sla,
    )

    # ── Revenue ───────────────────────────────────────────────────────────────
    ppa_revenue = ppa_delivered_mwh * s.ppa_price
    excess_revenue = float((market_sell * (
        ts["ts_MktPrice"] + s.lgc_price_aud_mwh - s.market_spread
    )).sum()) * resolution_h
    market_purchase_cost = float((market_buy * (
        ts["ts_MktPrice"] + s.market_spread + s.lgc_price_aud_mwh
    )).sum()) * resolution_h
    penalty_cost = penalty_mwh * s.penalty_price
    transmission_cost = ppa_delivered_mwh * s.transmission_cost_aud_mwh
    net_revenue = (
        ppa_revenue + excess_revenue - market_purchase_cost - penalty_cost - transmission_cost
    )

    total_gen_mwh = wind_generation_mwh + pv_generation_mwh
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
        scenario=s,
        dispatch=dispatch,
        summary=summary,
        revenue=revenue,
        solver_status=solver_status,
        solver_condition=solver_condition,
        n_period_hours=len(ts) * resolution_h,
        market_prices=ts["ts_MktPrice"],
        monthly_delivery_share=monthly_delivery_share,
        daily_delivery_share=daily_delivery_share,
        warnings=warnings,
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

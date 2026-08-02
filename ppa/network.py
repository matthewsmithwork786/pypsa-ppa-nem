from __future__ import annotations

import pandas as pd
import pypsa

pypsa.options.general.allow_network_requests = False
pypsa.options.params.statistics.drop_zero = True
pypsa.options.params.statistics.round = 2
pypsa.options.params.optimize.log_to_console = False
pypsa.options.params.optimize.include_objective_constant = False
pypsa.options.api.new_components_api = True

from ppa.scenario import Scenario


def build_network(
    ts: pd.DataFrame,
    scenario: Scenario,
    resolution_h: float = 1.0,
    snapshot_weightings: "pd.Series | None" = None,
) -> pypsa.Network:
    """Build an unsolved PyPSA network from prepared timeseries and scenario.

    When `scenario.optimise_capacity` is True, wind/PV/BESS capacities and the
    three transport links become extendable investment variables. Generation is
    bounded by the per-tech max-build caps; the links are bounded by
    `scenario.grid_connection_max_mw` and priced at the connection cost, so the
    LP co-sizes connection capacity with generation instead of being pinned to
    the (disabled) slider MW. Capital costs are annualised at `target_irr`
    (the project hurdle rate) and include devex, matching the financial model
    the IRR is later measured against. Merchant sales earn revenue at
    `scenario.sizing_merchant_value_share` of positive spot prices only, so the
    LP sizes to a realistic capture price while still curtaining negative hours.

    `resolution_h` is the hours each snapshot represents (>1 for the coarse
    sizing LP). It sets the snapshot weightings so marginal costs and storage
    state-of-charge integrate over real hours, not snapshot counts.

    `snapshot_weightings` is an optional per-snapshot weighting Series (e.g.
    tsam typical-period occurrence counts, which sum to ≈ 8760). When given it
    overrides the uniform `resolution_h` weighting so costs and storage
    integrate over the real hours each snapshot represents. `horizon_years` is
    derived from its sum (total modelled hours ÷ 8760).
    """
    s = scenario
    n = pypsa.Network()
    n.set_snapshots(ts.index)
    if snapshot_weightings is not None:
        w = snapshot_weightings.to_numpy(dtype=float)
        n.snapshot_weightings.loc[:, :] = w.reshape(-1, 1)
        total_hours = float(snapshot_weightings.sum())
    else:
        if resolution_h != 1.0:
            n.snapshot_weightings.loc[:, :] = float(resolution_h)
        total_hours = len(ts) * resolution_h

    sizing = s.optimise_capacity
    # Annualized A$/MW/yr (or A$/MW-of-BESS/yr via fixed duration), scaled by the
    # fraction of a year the LP covers so capex and operational costs are summed
    # over the same horizon. crf annualizes overnight capex; opex_rate adds fixed O&M.
    horizon_years = total_hours / 8760.0

    def _crf(rate: float, life: int) -> float:
        return rate / (1 - (1 + rate) ** -life) if rate > 0 else 1.0 / life

    # In sizing mode the LP sizes to the project's hurdle rate (target_irr) and
    # includes devex, so its cost basis matches the financial model (plan W12c):
    # the optimiser only builds capacity that clears the hurdle rate. Dispatch
    # mode carries no capex anyway (fixed capacities), so the rate choice is
    # inert there.
    crf_rate = s.target_irr if sizing else s.discount_rate
    crf = _crf(crf_rate, s.project_life_yrs)
    devex = (1.0 + s.devex_pct_of_capex) if sizing else 1.0
    wind_cc = s.wind_capex_per_kw * 1_000 * devex * (crf + s.opex_rate) * horizon_years
    pv_cc = s.pv_capex_per_kw * 1_000 * devex * (crf + s.opex_rate) * horizon_years
    bess_cc = s.bess_capex_per_kwh * 1_000 * s.bess_max_hours * devex * (crf + s.opex_rate) * horizon_years
    # Connection capital cost annualised the same way as generation capex
    # (plan W12a): strictly positive, so each extendable transport link's
    # p_nom_opt is pinned to its realised peak flow instead of degenerating.
    link_cc = s.connection_cost_aud_mw * (crf + s.opex_rate) * horizon_years
    grid_connection_cap = float(s.grid_connection_max_mw) if s.grid_connection_max_mw is not None else float("inf")
    # Generous transport bound so links never constrain optimised builds
    build_cap_sum = s.max_build_wind_mw + s.max_build_pv_mw + s.max_build_bess_mw

    # ── Carriers ─────────────────────────────────────────────────────────────────
    n.add("Carrier", "AC")

    # ── Buses ─────────────────────────────────────────────────────────────────
    for bus_name in [
        "Bus_OnshoreWind",
        "Bus_PVBESS",
        "Bus_IPPGeneration",
        "Bus_BuyFromMarket",
        "Bus_SellToMarket",
        "Bus_PPAOfftake",
    ]:
        n.add(
            "Bus", 
            bus_name, 
            carrier="AC"
        )

    # ── Load ──────────────────────────────────────────────────────────────────
    n.add(
        "Load",
        "Load_PPAOfftake",
        bus="Bus_PPAOfftake",
        p_set=ts["ppaload_mw"],
    )

    # ── Generators ────────────────────────────────────────────────────────────
    n.add(
        "Generator",
        "Gen_OnshoreWind",
        bus="Bus_OnshoreWind",
        p_nom=0.0 if sizing else s.onsw_mw,
        p_nom_extendable=sizing,
        p_nom_max=s.max_build_wind_mw if sizing else float("inf"),
        capital_cost=wind_cc if sizing else 0.0,
        p_max_pu=ts["ts_WindGen"],
        marginal_cost=0.1,
    )

    n.add(
        "Generator",
        "Gen_PV",
        bus="Bus_PVBESS",
        p_nom=0.0 if sizing else s.pv_mw,
        p_nom_extendable=sizing,
        p_nom_max=s.max_build_pv_mw if sizing else float("inf"),
        capital_cost=pv_cc if sizing else 0.0,
        p_max_pu=ts["ts_PVGen"],
        marginal_cost=0.01,
    )

    n.add(
        "Generator",
        "Gen_BuyFromMarket",
        bus="Bus_BuyFromMarket",
        p_nom=s.maxbuy_mw,
        p_max_pu=1.0,
        marginal_cost=ts["ts_MktPrice"] + s.market_spread,
    )

    # sign=-1: acts as a sink at Bus_SellToMarket; negative marginal_cost = revenue.
    # In sizing mode merchant revenue is credited at `sizing_merchant_value_share`
    # of historic spot (a haircut for capture-price cannibalisation, MLF and
    # curtailment) — applied to POSITIVE prices only, so negative-price hours
    # keep their full disincentive and the LP curtails rather than sells into
    # them (plan W12b). The old code zeroed merchant revenue entirely, which made
    # the sizing LP optimise a strictly poorer objective than the IRR later
    # measures and caused systematic under-build.
    if sizing:
        merch_price = ts["ts_MktPrice"].where(
            ts["ts_MktPrice"] <= 0, ts["ts_MktPrice"] * s.sizing_merchant_value_share
        )
        sell_marginal_cost = -(merch_price - s.market_spread)
    else:
        sell_marginal_cost = -(ts["ts_MktPrice"] - s.market_spread)
    n.add(
        "Generator",
        "Gen_SellToMarket",
        bus="Bus_SellToMarket",
        p_nom=build_cap_sum if sizing else s.maxsell_mw,
        p_max_pu=1.0,
        sign=-1.0,
        marginal_cost=sell_marginal_cost,
    )

    n.add(
        "Generator",
        "Gen_Penalty",
        bus="Bus_PPAOfftake",
        p_nom=s.ppaload_mw,
        p_max_pu=1.0,
        marginal_cost=s.penalty_price,
    )

    n.add(
        "Generator",
        "Gen_AllowedShortfall",
        bus="Bus_PPAOfftake",
        p_nom=s.ppaload_mw,
        p_max_pu=1.0,
        marginal_cost=0.001,
    )

    # ── Storage ───────────────────────────────────────────────────────────────
    # In sizing mode BESS power is optimised at fixed duration (max_hours);
    # energy = optimised MW × max_hours, priced via bess_cc (A$/kWh × hours).
    n.add(
        "StorageUnit",
        "SU_BESS",
        bus="Bus_PVBESS",
        p_nom=0.0 if sizing else s.effective_bess_mw,
        p_nom_extendable=sizing,
        p_nom_max=s.max_build_bess_mw if sizing else float("inf"),
        capital_cost=bess_cc if sizing else 0.0,
        max_hours=s.bess_max_hours,
        efficiency_store=s.bess_efficiency_store,
        efficiency_dispatch=s.bess_efficiency_dispatch,
        cyclic_state_of_charge=True,
        marginal_cost=0.0,
    )

    # ── Links ─────────────────────────────────────────────────────────────────
    # Transport links (wind→IPP, PV+BESS→IPP, IPP→market): in sizing mode these
    # are extendable investment variables bounded by `grid_connection_max_mw`
    # and priced at the connection cost, so the LP can size connection capacity
    # freely (the W12a bug pinned them to the disabled slider MW, capping how
    # much built generation could ever be delivered). In dispatch mode they use
    # the sized connection MW carried on the scenario by `apply_sizing`, falling
    # back to the nameplate-derived caps when not set.
    if sizing:
        wind_pnom, wind_ext, wind_cap_max, wind_link_cc = 0.0, True, grid_connection_cap, link_cc
        pvbess_pnom, pvbess_ext, pvbess_cap_max, pvbess_link_cc = 0.0, True, grid_connection_cap, link_cc
        sell_pnom, sell_ext, sell_cap_max, sell_link_cc = 0.0, True, grid_connection_cap, link_cc
    else:
        wind_pnom = s.wind_link_mw if s.wind_link_mw is not None else s.onsw_mw
        pvbess_pnom = s.pvbess_link_mw if s.pvbess_link_mw is not None else (s.pv_mw + s.effective_bess_mw)
        sell_pnom = s.sell_link_mw if s.sell_link_mw is not None else s.maxsell_mw
        wind_ext = pvbess_ext = sell_ext = False
        wind_cap_max = pvbess_cap_max = sell_cap_max = float("inf")
        wind_link_cc = pvbess_link_cc = sell_link_cc = 0.0

    # The offtake link stays fixed at load size: it carries the PPA revenue
    # (marginal_cost = transmission - ppa_price) and is a contractual quantity,
    # not an investment decision. The market-buy link is a contract cap too.
    link_defs = [
        ("OnshoreWind_to_IPPGeneration",   "Bus_OnshoreWind",   "Bus_IPPGeneration",
         wind_pnom, wind_ext, wind_cap_max, wind_link_cc, 0.0),
        ("PVBESS_to_IPPGeneration",        "Bus_PVBESS",        "Bus_IPPGeneration",
         pvbess_pnom, pvbess_ext, pvbess_cap_max, pvbess_link_cc, 0.0),
        ("BuyFromMarket_to_IPPGeneration", "Bus_BuyFromMarket", "Bus_IPPGeneration",
         s.maxbuy_mw, False, float("inf"), 0.0, 0.0),
        ("IPPGen_to_SellToMarket",         "Bus_IPPGeneration", "Bus_SellToMarket",
         sell_pnom, sell_ext, sell_cap_max, sell_link_cc, 0.0),
        # Delivery earns the PPA tariff but pays the combined transmission /
        # grid-use charge per MWh, whatever the source (RE, BESS or market buy).
        ("IPPGen_to_PPAOfftake",           "Bus_IPPGeneration", "Bus_PPAOfftake",
         s.ppaload_mw, False, float("inf"), 0.0, s.transmission_cost_aud_mwh - s.ppa_price),
    ]

    for name, bus0, bus1, p_nom, p_nom_extendable, p_nom_max, capital_cost, marginal_cost in link_defs:
        n.add(
            "Link",
            name,
            bus0=bus0,
            bus1=bus1,
            p_nom=p_nom,
            p_nom_extendable=p_nom_extendable,
            p_nom_max=p_nom_max,
            capital_cost=capital_cost,
            efficiency=1.0,
            marginal_cost=marginal_cost,
        )

    n.consistency_check()
    return n

from __future__ import annotations

import pandas as pd
import pypsa

pypsa.options.general.allow_network_requests = False
pypsa.options.params.statistics.drop_zero = True
pypsa.options.params.statistics.round = 2
pypsa.options.params.optimize.log_to_console = False
pypsa.options.params.optimize.include_objective_constant = False
pypsa.options.api.new_components_api = True

from ppa import assumptions as A
from ppa.costing import annualised_cost_per_mw
from ppa.scenario import Scenario


def build_network(
    ts: pd.DataFrame,
    scenario: Scenario,
    resolution_h: float = 1.0,
    snapshot_weightings: "pd.Series | None" = None,
    intra_period_hours: float = 1.0,
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

    `resolution_h` is the hours each snapshot represents. It sets the
    snapshot weightings so marginal costs and storage state-of-charge
    integrate over real hours, not snapshot counts.

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
        # Occurrence counts scale objective and generation energy. Storage
        # weights are elapsed hours between representative snapshots instead.
        n.snapshot_weightings["objective"] = w
        n.snapshot_weightings["generators"] = w
        n.snapshot_weightings["stores"] = float(intra_period_hours)
        total_hours = float(snapshot_weightings.sum())
    else:
        if resolution_h != 1.0:
            n.snapshot_weightings.loc[:, :] = float(resolution_h)
        total_hours = len(ts) * resolution_h

    sizing = s.optimise_capacity
    # Annualised cost per MW, scaled to the LP horizon. Fixed O&M is an
    # absolute per-technology amount.
    horizon_years = total_hours / 8760.0

    # Sizing uses the project hurdle rate and devex; dispatch has fixed capacity.
    crf_rate = s.target_irr if sizing else s.discount_rate
    devex_pct = s.devex_pct_of_capex if sizing else 0.0
    # Fixed O&M plus annual maintenance as a share of capex.
    wind_capex_per_mw = s.wind_capex_per_kw * 1_000
    pv_capex_per_mw = s.pv_capex_per_kw * 1_000
    bess_capex_per_mw = s.bess_capex_per_kwh * 1_000 * s.bess_max_hours
    wind_cc = annualised_cost_per_mw(
        wind_capex_per_mw, crf_rate, s.project_life_yrs,
        A.WIND_FIXED_OM_AUD_MW_YR + A.MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA * wind_capex_per_mw, devex_pct,
    ) * horizon_years
    pv_cc = annualised_cost_per_mw(
        pv_capex_per_mw, crf_rate, s.project_life_yrs,
        A.PV_FIXED_OM_AUD_MW_YR + A.MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA * pv_capex_per_mw, devex_pct,
    ) * horizon_years
    bess_cc = annualised_cost_per_mw(
        bess_capex_per_mw, crf_rate, s.project_life_yrs,
        A.BESS_FIXED_OM_AUD_MWH_YR * s.bess_max_hours
        + A.MAINTENANCE_CAPEX_PCT_OF_CAPEX_PA * bess_capex_per_mw,
        devex_pct,
    ) * horizon_years
    # Connection costs use the same annualisation and have no devex uplift.
    link_cc = annualised_cost_per_mw(
        s.connection_cost_aud_mw, crf_rate, s.project_life_yrs, A.CONNECTION_FIXED_OM_AUD_MW_YR
    ) * horizon_years
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
        # The PPA is bundled: imported energy needs a purchased certificate.
        # Paying the same LGC rate credited on exports removes the artificial
        # profit from buying power solely to resell it with a free certificate.
        marginal_cost=ts["ts_MktPrice"] + s.market_spread + s.lgc_price_aud_mwh,
    )

    # price/MLF/curtailment risk in the *energy* market, which does not apply
    # Negative marginal cost represents export revenue. Sizing discounts
    # positive spot prices by `sizing_merchant_value_share`; negative prices
    # retain their full disincentive. LGC revenue applies to exports, not PPA
    # deliveries, whose certificates are bundled into the tariff.
    if sizing:
        merch_price = ts["ts_MktPrice"].where(
            ts["ts_MktPrice"] <= 0, ts["ts_MktPrice"] * s.sizing_merchant_value_share
        )
        sell_marginal_cost = -(merch_price + s.lgc_price_aud_mwh - s.market_spread)
    else:
        sell_marginal_cost = -(ts["ts_MktPrice"] + s.lgc_price_aud_mwh - s.market_spread)
    n.add(
        "Generator",
        "Gen_SellToMarket",
        bus="Bus_SellToMarket",
        p_nom=(build_cap_sum if sizing else s.maxsell_mw) if s.enable_market_sell else 0.0,
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
    # Sizing co-optimises connection links within the grid cap. Dispatch uses
    # capacities carried by `apply_sizing`, or nameplate-derived caps.
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

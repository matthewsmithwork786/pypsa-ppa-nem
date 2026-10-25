"""One-year procurement sizing and settlement from the customer's perspective."""
from dataclasses import dataclass
import gc

import numpy as np
import pandas as pd
import pypsa

pypsa.options.general.allow_network_requests = False

BESS_EFFICIENCY_STORE = 0.90
BESS_EFFICIENCY_DISPATCH = 0.90

BANDS = ("00:00–06:00", "06:00–12:00", "12:00–18:00", "18:00–24:00")


@dataclass(frozen=True)
class Procurement:
    mode: str = "NSW"
    peak_mw: float = 100.0
    utilisation: float = 0.75
    annual_match: float = 1.0
    wind_share: float = 0.4
    storage_share: float = 0.25
    duration_h: float = 4.0
    band_shares: tuple = (0.2, 0.4, 0.4, 0.2)
    pv_tariff: float = 80.0
    wind_tariff: float = 130.0
    storage_tariff: float = 6000.0  # A$/MWh of energy capacity/month
    pv_mw: float = 200.0
    wind_mw: float = 100.0
    bess_mw: float = 25.0
    bess_mwh: float = 100.0

    @property
    def storage_duration_h(self):
        if self.mode == "Fixed":
            return self.bess_mwh / self.bess_mw if self.bess_mw else 0.0
        return self.duration_h


def validate_inputs(ts: pd.DataFrame, s: Procurement) -> None:
    if s.mode not in {"NSW", "AEMO", "Fixed"}:
        raise ValueError("Choose NSW, AEMO or Choose capacity.")
    scalars = [s.peak_mw, s.utilisation, s.annual_match, s.wind_share,
               s.storage_share, s.duration_h, s.pv_tariff, s.wind_tariff, s.storage_tariff,
               s.pv_mw, s.wind_mw, s.bess_mw, s.bess_mwh]
    if not np.isfinite(scalars).all() or not np.isfinite(s.band_shares).all():
        raise ValueError("All assumptions must be finite numbers.")
    if s.peak_mw <= 0 or not 0 < s.utilisation <= 1 or s.duration_h <= 0:
        raise ValueError("Peak load and duration must be positive; utilisation must be in (0, 100%].")
    if s.annual_match < 0 or any(not 0 <= x <= 1 for x in
            (s.wind_share, s.storage_share, *s.band_shares)) or len(s.band_shares) != 4:
        raise ValueError("Matching percentages must be non-negative; shares must be between 0% and 100%.")
    if min(s.pv_tariff, s.wind_tariff, s.storage_tariff) <= 0:
        raise ValueError("Procurement tariffs must be positive.")
    if s.mode == "Fixed":
        if min(s.pv_mw, s.wind_mw, s.bess_mw, s.bess_mwh) < 0:
            raise ValueError("Chosen capacities must be non-negative.")
        if any(not float(v).is_integer() for v in (s.pv_mw, s.wind_mw, s.bess_mw)):
            raise ValueError("PV, wind and BESS power must be whole MW.")
        if (s.bess_mw == 0) != (s.bess_mwh == 0):
            raise ValueError("Set both BESS MW and MWh to positive values, or both to zero.")
    required = ["ts_PVGen", "ts_WindGen", "ts_LoadMW", "ts_MktPrice"]
    if not set(required).issubset(ts.columns):
        raise ValueError("Timeseries must contain PV, wind, load and spot-price columns.")
    if not isinstance(ts.index, pd.DatetimeIndex) or ts.index.tz is not None or ts.index.hasnans:
        raise ValueError("Use local NEM time (AEST), without a timezone suffix.")
    if ts.index.year.nunique() != 1:
        raise ValueError("Upload one complete calendar year.")
    expected = pd.date_range(f"{ts.index[0].year}-01-01", f"{ts.index[0].year+1}-01-01", freq="h", inclusive="left")
    if not ts.index.equals(expected):
        raise ValueError("Use a complete calendar year of consecutive hourly data, with no gaps or duplicate timestamps.")
    if not np.isfinite(ts[required].to_numpy(dtype=float)).all():
        raise ValueError("Timeseries contain missing or non-finite values.")
    if not ts[["ts_PVGen", "ts_WindGen"]].apply(lambda x: x.between(0, 1).all()).all():
        raise ValueError("PV and wind must be capacity factors between 0 and 1.")
    if (ts.ts_LoadMW < 0).any() or ts.ts_LoadMW.sum() <= 0:
        raise ValueError("Load must be non-negative and have positive annual consumption.")
    if ts.ts_LoadMW.max() > s.peak_mw + 1e-6:
        raise ValueError("Uploaded load exceeds the peak load on the Requirements page.")
    if s.mode == "Fixed":
        for label, column, capacity in [("PV", "ts_PVGen", s.pv_mw), ("Wind", "ts_WindGen", s.wind_mw)]:
            if capacity > 0 and ts[column].sum() == 0:
                raise ValueError(f"Select a {label} profile with generation for the chosen {label} capacity.")


def _network(ts, s, capacities=None):
    n = pypsa.Network()
    n.set_snapshots(ts.index.rename("snapshot"))
    n.add("Carrier", "electricity")
    for bus in ["renewables", "customer"]:
        n.add("Bus", bus, carrier="electricity")
    n.add("Load", "load", bus="customer", p_set=ts.ts_LoadMW)
    sizing = capacities is None
    for name, column, tariff in [("PV", "ts_PVGen", s.pv_tariff), ("Wind", "ts_WindGen", s.wind_tariff)]:
        cf = ts[column]
        n.add("Generator", name, bus="renewables", carrier="electricity",
              p_nom_extendable=sizing, p_nom=0 if sizing else capacities[name],
              p_nom_mod=1.0 if sizing else 0.0,
              p_nom_max=float("inf") if cf.sum() else 0,
              p_min_pu=cf, p_max_pu=cf,
              capital_cost=float(cf.sum()) * tariff if sizing else 0)
    n.add("Generator", "Spot", bus="customer", p_nom=float(ts.ts_LoadMW.max()),
          marginal_cost=0 if sizing else ts.ts_MktPrice)
    # These sinks cannot import energy. Curtailment avoids forced negative-price sales.
    for name, cost in [("Export", 0 if sizing else -ts.ts_MktPrice), ("Curtailment", 0)]:
        n.add("Generator", name, bus="renewables", sign=-1, p_nom=1e9, marginal_cost=cost)
    n.add("Link", "Delivery", bus0="renewables", bus1="customer",
          p_nom=float(ts.ts_LoadMW.max()), p_max_pu=ts.ts_LoadMW / ts.ts_LoadMW.max(),
          marginal_cost=0 if sizing else -1e-5)
    n.add("StorageUnit", "BESS", bus="renewables", carrier="electricity",
          p_nom_extendable=sizing, p_nom=0 if sizing else capacities["BESS"],
          p_nom_mod=1.0 if sizing else 0.0,
          p_nom_min=s.peak_mw * s.storage_share if sizing and s.mode == "NSW" else 0,
          max_hours=s.storage_duration_h or 1.0, efficiency_store=BESS_EFFICIENCY_STORE, efficiency_dispatch=BESS_EFFICIENCY_DISPATCH,
          cyclic_state_of_charge=True, marginal_cost=0.01,
          capital_cost=s.storage_duration_h * 12 * s.storage_tariff if sizing else 0)
    return n


def _solve(n, ts, s):
    m = n.optimize.create_model(include_objective_constant=False)
    g = m.variables["Generator-p"]
    delivery = m.variables["Link-p"].sel(name="Delivery")
    weight = n.snapshot_weightings.generators
    # Net renewable production excludes curtailment and counts stored energy only once.
    renewable = g.sel(name="PV") + g.sel(name="Wind") - g.sel(name="Curtailment")
    if s.mode != "Fixed":
        m.add_constraints((renewable * weight).sum() >= s.annual_match * float(ts.ts_LoadMW.sum()), name="Annual_matching")
    if s.mode == "NSW":
        wind = (g.sel(name="Wind") * weight).sum()
        pv = (g.sel(name="PV") * weight).sum()
        target = s.annual_match * float(ts.ts_LoadMW.sum())
        # Exact energy-share equality is generally incompatible with whole-MW builds.
        m.add_constraints(wind >= s.wind_share * target, name="Wind_energy_target")
        m.add_constraints(pv >= (1 - s.wind_share) * target, name="PV_energy_target")
    elif s.mode == "AEMO":
        for band, share in enumerate(s.band_shares):
            idx = n.snapshots[n.snapshots.hour // 6 == band]
            m.add_constraints((delivery.sel(snapshot=idx) * weight.loc[idx]).sum() >=
                              share * float(ts.loc[idx, "ts_LoadMW"].sum()), name=f"Band_{band}")
    # Renewable-only charging; storage is reserved for the customer's load, not export arbitrage.
    discharge = m.variables["StorageUnit-p_dispatch"].sel(name="BESS")
    m.add_constraints(discharge <= delivery, name="Storage_to_customer")
    storage = n.components.storage_units.static
    if not storage.p_nom_extendable.BESS and storage.p_nom.BESS > 0:
        # Negative spot prices can otherwise reward simultaneous charge/discharge.
        charging = m.add_variables(binary=True, coords=[n.snapshots], name="BESS_charging")
        power = float(storage.p_nom.BESS)
        m.add_constraints(m.variables["StorageUnit-p_store"].sel(name="BESS") <= power * charging,
                          name="Charge_mode")
        m.add_constraints(discharge <= power * (1 - charging), name="Discharge_mode")
    status, condition = n.optimize.solve_model(solver_name="highs", io_api="direct",
                                               log_to_console=False, threads=1)
    if status != "ok" or condition != "optimal":
        raise ValueError(f"No optimal portfolio found ({condition}). Check the selected profiles and matching targets.")


def optimise_procurement(ts: pd.DataFrame, s: Procurement) -> dict:
    validate_inputs(ts, s)
    # Cache profiles may be float32; settle all cash flows at double precision.
    ts = ts.astype({c: "float64" for c in ["ts_PVGen", "ts_WindGen", "ts_LoadMW", "ts_MktPrice"]})
    ts.index = ts.index.rename("snapshot")
    if s.mode == "Fixed":
        capacities = {"PV": s.pv_mw, "Wind": s.wind_mw, "BESS": s.bess_mw}
    else:
        # Size on contracted fees, so export revenue cannot drive speculative builds.
        n = _network(ts, s)
        _solve(n, ts, s)
        capacities = {name: float(n.components.generators.static.p_nom_opt[name]) for name in ["PV", "Wind"]}
        capacities["BESS"] = float(n.components.storage_units.static.p_nom_opt["BESS"])
        capacities = {name: int(round(value)) for name, value in capacities.items()}
        del n
        gc.collect()
    n = _network(ts, s, capacities)
    _solve(n, ts, s)
    g = n.components.generators.dynamic.p
    storage = n.components.storage_units.dynamic
    dispatch = pd.DataFrame({
        "Load": ts.ts_LoadMW, "PV": g.PV, "Wind": g.Wind,
        "Battery charge": storage.p_store.BESS, "Battery discharge": storage.p_dispatch.BESS,
        "Renewable delivery": n.components.links.dynamic.p0.Delivery, "Spot purchases": g.Spot,
        "Excess sold": g.Export, "Curtailed": g.Curtailment,
        "Stored energy": storage.state_of_charge.BESS, "Spot price": ts.ts_MktPrice,
    })
    from ppa.procurement_reporting import allocate_sources
    dispatch = allocate_sources(dispatch)
    fees = {"PV PPA": capacities["PV"] * ts.ts_PVGen.sum() * s.pv_tariff,
            "Wind PPA": capacities["Wind"] * ts.ts_WindGen.sum() * s.wind_tariff,
            "BESS fee": capacities["BESS"] * s.storage_duration_h * 12 * s.storage_tariff,
            "Spot shortfalls": float((dispatch["Spot purchases"] * ts.ts_MktPrice).sum()),
            "Excess revenue": float((dispatch["Excess sold"] * ts.ts_MktPrice).sum())}
    total = fees["PV PPA"] + fees["Wind PPA"] + fees["BESS fee"] + fees["Spot shortfalls"] - fees["Excess revenue"]
    counterfactual = float((ts.ts_LoadMW * ts.ts_MktPrice).sum())
    compliance = []
    generated = float((dispatch.PV + dispatch.Wind - dispatch.Curtailed).sum())
    annual_load = float(dispatch.Load.sum())
    if s.mode != "Fixed":
        compliance.append(("Annual renewable matching", s.annual_match, generated / annual_load))
    if s.mode == "AEMO":
        for band, share in enumerate(s.band_shares):
            part = dispatch.loc[dispatch.index.hour // 6 == band]
            compliance.append((BANDS[band], share, part["Renewable delivery"].sum() / part.Load.sum()
                               if part.Load.sum() else float("nan")))
    elif s.mode == "NSW":
        compliance.extend([("Wind volume / annual load", s.wind_share * s.annual_match,
                            float(dispatch.Wind.sum()) / annual_load),
                           ("PV volume / annual load", (1 - s.wind_share) * s.annual_match,
                            float(dispatch.PV.sum()) / annual_load),
                           ("Storage / peak load", s.storage_share, capacities["BESS"] / s.peak_mw)])
    matching = [("Annual renewable volume matching", generated / annual_load),
                ("Wind generation / annual load", float(dispatch.Wind.sum()) / annual_load),
                ("BESS power / peak load", capacities["BESS"] / s.peak_mw),
                ("Time matching · whole year", float(dispatch["Renewable delivery"].sum()) / annual_load)]
    for band, label in enumerate(BANDS):
        part = dispatch.loc[dispatch.index.hour // 6 == band]
        matching.append((f"Time matching · {label}", float(part["Renewable delivery"].sum()) / float(part.Load.sum())
                         if part.Load.sum() else float("nan")))
    return {"capacities": capacities, "dispatch": dispatch, "fees": fees, "net_cost": total,
            "spot_cost": counterfactual, "savings": counterfactual - total,
            "compliance": pd.DataFrame(compliance, columns=["Requirement", "Target", "Achieved"]),
            "matching": pd.DataFrame(matching, columns=["Metric", "Achieved"]),
            "assumptions": s}

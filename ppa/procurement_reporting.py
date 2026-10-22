"""Source attribution and the customer's annual procurement cost bridge."""
import pandas as pd

SOURCES = ("PV", "Wind", "BESS")
SUBTOTALS = {"Total PPA payments", "Total procurement cost", "Total excess revenue",
             "Portfolio net cost", "Spot-only counterfactual", "Savings versus spot"}


def allocate_sources(dispatch):
    """Allocate renewable exports pro rata; reserved BESS discharge serves load."""
    d = dispatch.copy()
    generation = d.PV + d.Wind
    for source in ("PV", "Wind"):
        share = d[source].div(generation.where(generation > 0)).fillna(0)
        d[f"{source} charged"] = d["Battery charge"] * share
        d[f"{source} curtailed"] = d.Curtailed * share
        d[f"{source} supply"] = (d[source] - d[f"{source} charged"] - d[f"{source} curtailed"]).clip(lower=0)
    d["BESS supply"] = d["Battery discharge"].clip(lower=0)
    supply = d["PV supply"] + d["Wind supply"]
    export_share = d["Excess sold"].div(supply.where(supply > 0)).fillna(0).clip(0, 1)
    for source in ("PV", "Wind"):
        d[f"{source} excess"] = d[f"{source} supply"] * export_share
        d[f"{source} to load"] = d[f"{source} supply"] - d[f"{source} excess"]
    # The model reserves all BESS discharge for the customer's load.
    d["BESS excess"] = 0.0
    d["BESS to load"] = d["BESS supply"]
    return d


def cost_breakdown(result):
    d, s, fees = result["dispatch"], result["assumptions"], result["fees"]
    rows = []
    for source, tariff in [("PV", s.pv_tariff), ("Wind", s.wind_tariff)]:
        rows.append((f"{source} PPA", float(d[source].sum()) / 1000, "GWh", tariff,
                     "A$/MWh", float(fees[f"{source} PPA"])))
    rows.append(("BESS tolling", result["capacities"]["BESS"] * s.storage_duration_h,
                 "MWh capacity", s.storage_tariff, "A$/MWh/month", float(fees["BESS fee"])))
    purchases = float(d["Spot purchases"].sum())
    rows.append(("Spot shortfalls", purchases / 1000, "GWh",
                 fees["Spot shortfalls"] / purchases if purchases else 0, "A$/MWh", float(fees["Spot shortfalls"])))
    for source in ("Wind", "PV", "BESS"):
        energy = float(d[f"{source} excess"].sum())
        revenue = float((d[f"{source} excess"] * d["Spot price"]).sum())
        rows.append((f"{source} excess revenue", energy / 1000, "GWh",
                     revenue / energy if energy else 0, "A$/MWh", -revenue))
    return pd.DataFrame(rows, columns=["Component", "Quantity", "Quantity unit", "Rate", "Rate unit", "Annual cost (A$)"])


def financial_summary(result):
    f = result["fees"]
    ppa = float(f["PV PPA"] + f["Wind PPA"])
    rows = [("PV PPA", f["PV PPA"]), ("Wind PPA", f["Wind PPA"]), ("Total PPA payments", ppa),
            ("BESS tolling", f["BESS fee"]), ("Spot shortfalls", f["Spot shortfalls"]),
            ("Total procurement cost", ppa + f["BESS fee"] + f["Spot shortfalls"]),
            ("Total excess revenue", -f["Excess revenue"]), ("Portfolio net cost", result["net_cost"]),
            ("Spot-only counterfactual", result["spot_cost"]), ("Savings versus spot", result["savings"])]
    return pd.DataFrame(rows, columns=["Annual cash flow", "A$"])

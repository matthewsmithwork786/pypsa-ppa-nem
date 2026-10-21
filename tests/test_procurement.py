from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from ppa.procurement import Procurement, optimise_procurement, validate_inputs


def year_data(wind=0.4):
    index = pd.date_range("2025-01-01", periods=8760, freq="h")
    return pd.DataFrame({"ts_PVGen": np.where((index.hour >= 6) & (index.hour < 18), 0.8, 0),
                         "ts_WindGen": wind, "ts_LoadMW": 90.0,
                         "ts_MktPrice": np.where(index.hour < 6, -20.0, 150.0)}, index=index)


@pytest.fixture(scope="module")
def nsw_result():
    return optimise_procurement(year_data(), Procurement())


@pytest.fixture(scope="module")
def aemo_result():
    return optimise_procurement(year_data(wind=0), Procurement(mode="AEMO"))


def test_nsw_energy_mix_storage_and_annual_matching(nsw_result):
    r = nsw_result
    d = r["dispatch"]
    assert d.Wind.sum() / (d.Wind.sum() + d.PV.sum()) == pytest.approx(0.4)
    assert (d.Wind + d.PV - d.Curtailed).sum() >= d.Load.sum() - 1e-4
    assert r["capacities"]["BESS"] == pytest.approx(25)
    assert r["fees"]["BESS fee"] == pytest.approx(25 * 4 * 2000 * 12)


def test_aemo_nighttime_targets_require_storage_without_wind(aemo_result):
    r = aemo_result
    assert r["capacities"]["Wind"] == pytest.approx(0)
    assert r["capacities"]["BESS"] > 0
    assert (r["compliance"].Achieved >= r["compliance"].Target - 1e-6).all()
    d = r["dispatch"]
    # Charging cannot originate from the grid, and the year is energy-neutral in SOC.
    assert (d["Battery charge"] <= d.PV + d.Wind + 1e-4).all()
    assert d["Battery discharge"].sum() == pytest.approx(d["Battery charge"].sum() * 0.95**2)


@pytest.mark.parametrize("fixture", ["nsw_result", "aemo_result"])
def test_energy_balances_and_customer_cashflows(fixture, request):
    r = request.getfixturevalue(fixture)
    d, fees = r["dispatch"], r["fees"]
    assert not ((d["Battery charge"] > 1e-5) & (d["Battery discharge"] > 1e-5)).any()
    np.testing.assert_allclose(d["Renewable delivery"] + d["Spot purchases"], d.Load, atol=1e-5)
    np.testing.assert_allclose(d.PV + d.Wind + d["Battery discharge"],
                               d["Renewable delivery"] + d["Battery charge"] + d["Excess sold"] + d.Curtailed, atol=1e-5)
    assert r["spot_cost"] == pytest.approx((d.Load * d["Spot price"]).sum())
    assert fees["Spot shortfalls"] == pytest.approx((d["Spot purchases"] * d["Spot price"]).sum())
    assert fees["Excess revenue"] == pytest.approx((d["Excess sold"] * d["Spot price"]).sum())
    expected = fees["PV PPA"] + fees["Wind PPA"] + fees["BESS fee"] + fees["Spot shortfalls"] - fees["Excess revenue"]
    assert r["net_cost"] == pytest.approx(expected)
    assert r["savings"] == pytest.approx(r["spot_cost"] - expected)


def test_infeasible_wind_share_is_reported():
    with pytest.raises(ValueError, match="No optimal portfolio"):
        optimise_procurement(year_data(wind=0), Procurement())


@pytest.mark.parametrize("problem", ["gap", "nan", "infinity", "cf", "peak", "duplicate"])
def test_invalid_uploads_are_blocked(problem):
    ts = year_data()
    if problem == "gap":
        ts = ts.iloc[1:]
    elif problem in {"nan", "infinity"}:
        ts.iloc[0, 0] = np.nan if problem == "nan" else np.inf
    elif problem == "cf":
        ts.iloc[0, 0] = 1.1
    elif problem == "peak":
        ts.iloc[0, 2] = 101
    else:
        ts = pd.concat([ts, ts.iloc[-1:]])
    with pytest.raises(ValueError):
        validate_inputs(ts, Procurement())


def test_leap_year_is_supported():
    ts = year_data().reindex(pd.date_range("2024-01-01", periods=8784, freq="h")).fillna(
        {"ts_PVGen": 0.2, "ts_WindGen": 0.4, "ts_LoadMW": 90, "ts_MktPrice": 80})
    validate_inputs(ts, Procurement())


def test_bad_assumptions_are_blocked():
    with pytest.raises(ValueError):
        validate_inputs(year_data(), replace(Procurement(), band_shares=(0.2, 0.4)))

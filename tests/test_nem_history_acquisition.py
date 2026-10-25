"""Historical acquisition must not publish commissioning or misaligned profiles."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backfill_nem_history as history
from ppa.data import nem_data


@pytest.fixture
def acquisition(tmp_path, monkeypatch):
    cache = tmp_path / "nem"
    (cache / "registry").mkdir(parents=True)
    pd.DataFrame([
        {"duid": duid, "station_name": duid, "region": "NSW1", "fuel_tech": "Wind",
         "capacity_registered_mw": 100.0, "lat": -33.0, "lon": 150.0,
         "status": "operating"} for duid in ["CLEANWF1", "NEWWF1"]
    ]).to_parquet(cache / "registry/nem_plant_registry.parquet", index=False)

    def availability(year, raw_dir, start, end, duids):
        # Include the preceding interval to verify month-boundary exclusion.
        index = pd.date_range(start, end, freq="5min")
        return pd.concat([
            pd.DataFrame({"SETTLEMENTDATE": index, "DUID": duid,
                          "AVAILABILITY": np.where((index.month < 7) & (duid == "NEWWF1"), 0.0, 50.0)})
            for duid in duids
        ], ignore_index=True)

    def prices(year, raw_dir):
        index = nem_data.canonical_5min_index(year) - pd.Timedelta(minutes=5)
        return pd.concat([pd.DataFrame({"SETTLEMENTDATE": index, "REGIONID": region,
                                       "RRP": 80.0}) for region in history.NEM_REGIONS])

    monkeypatch.setattr(history, "pull_availability", availability)
    monkeypatch.setattr(history, "pull_price_all_regions", prices)
    return cache, tmp_path / "work", tmp_path / "raw"


@pytest.mark.parametrize("year", [2020, 2023, 2024])
def test_backfill_publishes_only_full_operating_years_and_resumes(acquisition, monkeypatch, year):
    cache, work, raw = acquisition
    history.acquire_year(year, cache, work, raw)
    available = nem_data.load_availability("CLEANWF1", year, cache)
    assert len(available) == nem_data.expected_intervals(year)
    assert available.index[0] == pd.Timestamp(year, 1, 1)
    assert available.index[-1] == pd.Timestamp(year + 1, 1, 1) - pd.Timedelta(minutes=5)
    assert available.eq(50).all()
    assert not (cache / "availability" / f"NEWWF1_{year}.parquet").exists()
    audit = pd.read_csv(cache / "source" / f"uigf_eligibility_{year}.csv").set_index("duid")
    assert audit.loc["CLEANWF1", "eligible"]
    assert not audit.loc["NEWWF1", "eligible"]
    assert "monthly peak" in audit.loc["NEWWF1", "reject_reasons"]
    assert len(audit.loc["CLEANWF1", "sha256"]) == 64
    price = nem_data.load_regional_price("NSW1", year, cache)
    pd.testing.assert_index_equal(price.index, available.index)

    def unexpected_download(*args, **kwargs):
        raise AssertionError("A resumed run must reuse completed checkpoints")

    monkeypatch.setattr(history, "pull_availability", unexpected_download)
    monkeypatch.setattr(history, "pull_price_all_regions", unexpected_download)
    history.acquire_year(year, cache, work, raw)


def test_missing_prices_block_publication(acquisition, monkeypatch):
    original = history.pull_price_all_regions
    monkeypatch.setattr(history, "pull_price_all_regions", lambda *args: original(*args).iloc[1:])
    with pytest.raises(ValueError, match="missing or invalid prices"):
        history.acquire_year(2024, *acquisition)
    assert not (acquisition[0] / "source/uigf_2024.json").exists()

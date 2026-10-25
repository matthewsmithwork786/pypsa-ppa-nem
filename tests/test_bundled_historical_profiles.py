"""Validate published historical data against its acquisition audit trail."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ppa.data import nem_data

CACHE = Path(__file__).resolve().parents[1] / "data/cache/nem"


@pytest.mark.parametrize("year", range(2020, 2025))
def test_bundled_history_has_only_audited_operating_profiles_and_matching_prices(year):
    metadata_path = CACHE / "source" / f"uigf_{year}.json"
    if not metadata_path.exists():
        pytest.skip(f"{year} history has not been acquired in this checkout")
    metadata = json.loads(metadata_path.read_text())
    audit = pd.read_csv(CACHE / "source" / f"uigf_eligibility_{year}.csv")
    accepted = audit[audit.eligible]
    assert len(accepted) == metadata["accepted_profiles"] > 0
    actual = {p.name.rsplit("_", 1)[0] for p in (CACHE / "availability").glob(f"*_{year}.parquet")}
    assert actual == set(accepted.duid)
    expected = pd.date_range(f"{year}-01-01", periods=nem_data.expected_intervals(year), freq="5min")
    for row in accepted.itertuples():
        path = CACHE / "availability" / f"{row.duid}_{year}.parquet"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256
        series = nem_data.load_availability(row.duid, year, CACHE)
        pd.testing.assert_index_equal(series.index, expected, check_names=False)
        assert series.notna().mean() >= 0.98
        assert np.isfinite(series.dropna()).all()
        assert (series.dropna() >= 0).all()
    for region in nem_data.NEM_REGIONS:
        prices = nem_data.load_regional_price(region, year, CACHE)
        pd.testing.assert_index_equal(prices.index, expected, check_names=False)
        assert np.isfinite(prices).all()

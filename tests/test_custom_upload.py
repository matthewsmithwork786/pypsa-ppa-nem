"""Tests for the custom-CSV-upload additions in ppa/data_loader.py."""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from ppa import data_loader
from ppa.scenario import Scenario
from tests.fixtures import custom_csv_fixtures as fx


# ── Template ────────────────────────────────────────────────────────────────

def test_template_header_and_parseable():
    raw = data_loader.build_upload_template()
    df = pd.read_csv(io.BytesIO(raw))
    assert list(df.columns) == data_loader.CUSTOM_UPLOAD_COLUMNS
    assert len(df) == 8760  # default template is the full 2025 year at hourly resolution


def test_template_values_are_realistic():
    raw = data_loader.build_upload_template()
    df = pd.read_csv(io.BytesIO(raw))
    assert (df["ts_PVGen"] >= 0).all() and (df["ts_PVGen"] <= 1).all()
    assert (df["ts_WindGen"] >= 0).all() and (df["ts_WindGen"] <= 1).all()
    assert (df["ts_LoadMW"] == data_loader.TEMPLATE_LOAD_MW).all()
    # Night hours should have zero PV
    night_mask = pd.to_datetime(df["timestamp"]).dt.hour.isin([0, 1, 2, 3])
    assert (df.loc[night_mask, "ts_PVGen"] == 0).all()


# ── Valid upload ────────────────────────────────────────────────────────────

def test_valid_upload_accepted_with_correct_index_and_dtype():
    raw = fx.build_valid_full_year_csv()
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    assert ts.index.name == "snapshot"
    assert isinstance(ts.index, pd.DatetimeIndex)
    for c in data_loader.CUSTOM_DATA_COLUMNS:
        assert pd.api.types.is_float_dtype(ts[c])
    assert len(ts) == 8760


# ── Missing columns ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("missing_col", data_loader.CUSTOM_UPLOAD_COLUMNS)
def test_missing_column_rejected_by_name(missing_col):
    raw = fx.build_missing_column_csv(missing_col)
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    assert "Missing required columns" in str(exc_info.value)
    assert missing_col in str(exc_info.value)


# ── Range / sign checks ──────────────────────────────────────────────────────

def test_out_of_range_cf_rejected():
    raw = fx.build_out_of_range_cf_csv(col="ts_PVGen")
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    msg = str(exc_info.value)
    assert "ts_PVGen" in msg
    assert "capacity factor in [0, 1]" in msg


def test_percent_scale_cf_gives_percent_hint():
    raw = fx.build_percent_scale_cf_csv()
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    msg = str(exc_info.value)
    assert "divide them by 100" in msg


def test_negative_load_rejected():
    raw = fx.build_negative_load_csv()
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    msg = str(exc_info.value)
    assert "ts_LoadMW" in msg
    assert ">= 0 MW" in msg


def test_negative_price_is_allowed():
    df = fx.build_valid_full_year_df().head(48).copy()
    df.loc[df.index[0], "ts_MktPrice"] = -500.0
    ts = data_loader.load_custom_upload(io.BytesIO(fx.df_to_csv_bytes(df)))
    assert (ts["ts_MktPrice"] < 0).any()


# ── Malformed data ───────────────────────────────────────────────────────────

def test_non_numeric_junk_rejected():
    raw = fx.build_non_numeric_junk_csv(col="ts_MktPrice")
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    msg = str(exc_info.value)
    assert "ts_MktPrice" in msg
    assert "non-numeric or empty" in msg


def test_non_numeric_junk_reports_1_based_spreadsheet_row_number():
    """The reported CSV row must match what a user counting lines in the raw
    file (header = row 1, first data row = row 2) would see -- not the 0-based
    pandas RangeIndex position."""
    raw = fx.build_non_numeric_junk_csv(col="ts_MktPrice")  # bad value at df index 5

    # Independently verify the true 1-based line number in the raw file.
    text = raw.decode("utf-8")
    lines = text.splitlines()
    true_line_no = next(i for i, line in enumerate(lines, start=1) if "not_a_number" in line)

    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    msg = str(exc_info.value)

    assert f"CSV row {true_line_no}" in msg
    # Sanity: this is index (5) + 2, i.e. header row + 1-based offset.
    assert true_line_no == 7


def test_bad_timestamp_rejected():
    raw = fx.build_bad_timestamp_csv()
    with pytest.raises(ValueError) as exc_info:
        data_loader.load_custom_upload(io.BytesIO(raw))
    assert "timestamp" in str(exc_info.value)


def test_empty_csv_rejected():
    df = fx.build_valid_full_year_df().head(0)
    with pytest.raises(ValueError, match="no data rows"):
        data_loader.load_custom_upload(io.BytesIO(fx.df_to_csv_bytes(df)))


def test_unparseable_csv_rejected():
    with pytest.raises(ValueError, match="Could not parse the uploaded file as CSV"):
        data_loader.load_custom_upload(io.BytesIO(b""))


# ── describe_custom_timeseries ───────────────────────────────────────────────

def test_describe_modal_step_and_full_year_hourly():
    raw = fx.build_valid_full_year_csv(year=2025)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert diag["modal_step"] == pd.Timedelta(hours=1)
    assert diag["is_hourly"] is True
    assert diag["is_sub_hourly"] is False
    assert diag["is_full_year"] is True
    assert diag["year"] == 2025
    assert diag["n_gaps"] == 0
    assert diag["n_duplicate_timestamps"] == 0


def test_describe_full_year_leap():
    raw = fx.build_valid_full_year_csv(year=2024)  # leap year
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert len(ts) == 8784
    assert diag["is_full_year"] is True


def test_describe_gaps_detected():
    raw = fx.build_gapped_csv(hours=48, drop_hours=(5, 6, 20))
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert diag["n_gaps"] == 3


def test_describe_duplicates_detected():
    raw = fx.build_duplicate_timestamps_csv(hours=48)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert diag["n_duplicate_timestamps"] == 1
    # keep="last" during dedup -> the higher (duplicate + 1.0) price should survive
    assert len(ts) == 48


def test_describe_sub_hourly_flagged():
    raw = fx.build_sub_hourly_csv(hours=2)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert diag["is_sub_hourly"] is True
    assert diag["is_hourly"] is False


def test_describe_super_hourly_flagged():
    raw = fx.build_super_hourly_csv(periods=16)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    diag = data_loader.describe_custom_timeseries(ts)
    assert diag["is_hourly"] is False
    assert diag["is_sub_hourly"] is False


# ── prepare_custom_timeseries ────────────────────────────────────────────────

def test_prepare_resamples_sub_hourly_to_hourly_mean():
    raw = fx.build_sub_hourly_csv(hours=4)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    prepared = data_loader.prepare_custom_timeseries(ts)

    expected = ts.resample("h").mean()
    pd.testing.assert_series_equal(
        prepared["ts_PVGen"], expected["ts_PVGen"], check_names=False,
    )
    assert len(prepared) == 4


def test_prepare_leaves_super_hourly_as_is():
    raw = fx.build_super_hourly_csv(periods=10)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    prepared = data_loader.prepare_custom_timeseries(ts)
    assert len(prepared) == len(ts)


def test_prepare_load_passthrough_is_exact_and_not_flat():
    """Critical: ppaload_mw must equal the raw ts_LoadMW exactly, and must NOT
    equal what profile-based (flat) synthesis would produce for a non-flat
    upload."""
    raw = fx.build_distinctive_load_shape_csv(hours=48)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    prepared = data_loader.prepare_custom_timeseries(ts)

    np.testing.assert_array_equal(
        prepared["ppaload_mw"].to_numpy(), ts["ts_LoadMW"].to_numpy()
    )
    assert "ts_LoadMW" in prepared.columns

    # A flat-profile synthesis would give a constant series at whatever
    # ppaload_mw scalar was configured -- the uploaded shape is not constant,
    # so it cannot match a flat/constant series.
    assert prepared["ppaload_mw"].nunique() > 1


# ── prepare_timeseries dispatch ──────────────────────────────────────────────

def test_prepare_timeseries_dispatches_to_custom_for_custom_csv_source():
    raw = fx.build_distinctive_load_shape_csv(hours=48)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    scenario = Scenario(data_source="custom_csv")
    prepared = data_loader.prepare_timeseries(ts, scenario)
    np.testing.assert_array_equal(
        prepared["ppaload_mw"].to_numpy(), ts["ts_LoadMW"].to_numpy()
    )


def test_prepare_timeseries_default_csv_path_unchanged():
    """The non-custom path (default/NEM CSV) keeps the flat load synthesis."""
    default_csv = data_loader.find_default_csv()
    assert default_csv is not None, "march_2025_pypsa_timeseries.csv fixture must exist"
    ts = data_loader.load_timeseries(default_csv)
    scenario = Scenario(data_source="nem_default", load_profile="flat", ppaload_mw=123.0)
    prepared = data_loader.prepare_timeseries(ts, scenario)
    assert (prepared["ppaload_mw"] == 123.0).all()


# ── Round-trip ────────────────────────────────────────────────────────────────

def test_round_trip_template_download_fill_reupload():
    raw = data_loader.build_upload_template()
    df = pd.read_csv(io.BytesIO(raw))

    # "Fill in" with the same synthetic deterministic values (simulating a user
    # editing then re-saving) and re-export.
    df["ts_MktPrice"] = df["ts_MktPrice"].round(2)
    reexported = df.to_csv(index=False).encode("utf-8")

    ts = data_loader.load_custom_upload(io.BytesIO(reexported))

    expected = df.copy()
    expected["timestamp"] = pd.to_datetime(expected["timestamp"])
    expected = expected.set_index("timestamp")
    expected.index.name = "snapshot"

    pd.testing.assert_frame_equal(
        ts[data_loader.CUSTOM_DATA_COLUMNS],
        expected[data_loader.CUSTOM_DATA_COLUMNS],
        check_dtype=False,
    )


# ── custom_timeseries_dicts ──────────────────────────────────────────────────

def test_custom_timeseries_dicts_shape():
    raw = fx.build_valid_full_year_csv(year=2025)
    ts = data_loader.load_custom_upload(io.BytesIO(raw))
    prepared = data_loader.prepare_custom_timeseries(ts)
    pv, wind, prices, load = data_loader.custom_timeseries_dicts(prepared, year=2025)

    assert set(pv) == {2025}
    assert set(wind) == {2025}
    assert set(prices) == {2025}
    assert set(load) == {2025}
    pd.testing.assert_series_equal(pv[2025], prepared["ts_PVGen"], check_names=False)
    pd.testing.assert_series_equal(wind[2025], prepared["ts_WindGen"], check_names=False)
    pd.testing.assert_series_equal(prices[2025], prepared["ts_MktPrice"], check_names=False)
    pd.testing.assert_series_equal(load[2025], prepared["ppaload_mw"], check_names=False)

"""Regressions for public run links, intentional edits and shipped NEM data."""
import dataclasses
import pickle

import pandas as pd
import pytest

from ppa import run_store
from ppa.financials import CapexBreakdown, MultiYearFinancialResult
from ppa.results import DispatchSeries, OptimisationResult, RevenueBreakdown, SummaryVolumes
from ppa.scenario import Scenario
from ui import state
from ui.tabs.nem_map import _has_cached_generation, _year_range_default


def _payload():
    scenario = Scenario()
    dispatch = DispatchSeries(**{
        field.name: pd.Series([0.0]) for field in dataclasses.fields(DispatchSeries)
    })
    summary = SummaryVolumes(**{
        field.name: 0.0 for field in dataclasses.fields(SummaryVolumes)
    })
    revenue = RevenueBreakdown(0, 0, 0, 0, 0, 0)
    result = OptimisationResult(scenario, dispatch, summary, revenue, "ok", "optimal", 1.0)
    financial = MultiYearFinancialResult(CapexBreakdown(0, 0, 0, 0, 0, 0, 0), 0)
    return {"scenario": scenario, "results": [result], "fin": financial}


@pytest.fixture
def store_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(run_store, "_STORE_DIR", tmp_path / "runs")
    return tmp_path / "runs"


@pytest.mark.parametrize("run_id", ["../escape", "/tmp/file", "0123456789ab/../../x", "x" * 12, "", None])
def test_untrusted_run_ids_cannot_read_or_write(store_dir, run_id):
    assert run_store.load(run_id) is None
    with pytest.raises(ValueError, match="Invalid run ID"):
        run_store.save(run_id, _payload())
    assert not store_dir.exists()


def test_completed_run_round_trip(store_dir):
    run_store.save("0123456789ab", _payload())
    restored = run_store.load("0123456789ab")
    assert restored["scenario"] == Scenario()
    assert restored["results"][0].solver_condition == "optimal"


@pytest.mark.parametrize("contents", [b"", b"not a pickle", pickle.dumps([]), pickle.dumps({"scenario": "old"})])
def test_corrupt_or_incomplete_cache_does_not_break_page_startup(store_dir, contents):
    store_dir.mkdir()
    (store_dir / "0123456789ab.pkl").write_bytes(contents)
    assert run_store.load("0123456789ab") is None


def test_expired_run_is_removed(store_dir, monkeypatch):
    run_store.save("0123456789ab", _payload())
    import time
    now = time.time()
    monkeypatch.setattr(run_store.time, "time", lambda: now + 7 * 3600)
    assert run_store.load("0123456789ab") is None
    assert not (store_dir / "0123456789ab.pkl").exists()


def test_symlink_run_cache_is_not_loaded(store_dir, tmp_path):
    store_dir.mkdir()
    target = tmp_path / "untrusted.pkl"
    target.write_bytes(pickle.dumps(_payload()))
    (store_dir / "0123456789ab.pkl").symlink_to(target)
    assert run_store.load("0123456789ab") is None


def test_scenario_edit_clears_restore_url_and_all_derived_outputs(monkeypatch):
    session = {state.SCENARIO_KEY: Scenario(), "fm_ppa_price": 100,
               state.MULTI_YEAR_RESULTS_KEY: [], state.PROJECT_FINANCE_KEY: object(),
               state.RESULT_KEY: object(), state.TIMESERIES_KEY: object()}
    query = {"run": "0123456789ab", "other": "keep"}
    monkeypatch.setattr(state.st, "session_state", session)
    monkeypatch.setattr(state.st, "query_params", query)
    state.clear_run_outputs()
    assert session == {state.SCENARIO_KEY: Scenario()}
    assert query == {"other": "keep"}


def test_published_year_picker_preserves_selection_and_never_invents_years():
    assert _year_range_default([2025], (2020, 2025)) == (2025, 2025)
    assert _year_range_default([2020, 2022, 2025], (2022, 2025)) == (2022, 2025)
    assert _year_range_default([2022, 2025], (2019,)) == (2022, 2025)


def test_uigf_cache_is_generation_data_without_scada(tmp_path):
    availability = tmp_path / "availability"
    availability.mkdir()
    (availability / "WIND1_2025.parquet").touch()
    assert _has_cached_generation(2025, {"n_scada_cached": 0}, tmp_path)
    assert not _has_cached_generation(2024, {"n_scada_cached": 0}, tmp_path)

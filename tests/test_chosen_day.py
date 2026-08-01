"""W5/W9 regression: the reference day must default to 2025 and the Optimisation
tab must coerce an out-of-range `chosen_day` instead of hard-blocking the run
with "chosen_day … is not present in the timeseries data".
"""
from __future__ import annotations

import pandas as pd
import pytest

from ppa.data_loader import get_available_days
from ppa.scenario import Scenario, validate_scenario


def _march_ts() -> pd.DataFrame:
    idx = pd.date_range("2025-03-01", periods=31 * 24, freq="h")
    return pd.DataFrame(
        {"ts_PVGen": 0.3, "ts_WindGen": 0.4, "ts_MktPrice": 60.0, "ppaload_mw": 100.0},
        index=idx,
    )


# ── W5: default reference day must be 2025 ───────────────────────────────────

def test_scenario_default_chosen_day_is_2025():
    assert Scenario().chosen_day.startswith("2025-")


# ── W9: coercion returns an in-range day and is idempotent ───────────────────

def test_coerce_chosen_day_present_unchanged():
    from ppa.data_loader import coerce_chosen_day

    ts = _march_ts()
    assert coerce_chosen_day(ts, "2025-03-15") == "2025-03-15"


def test_coerce_chosen_day_out_of_range_returns_nearest_available_day():
    from ppa.data_loader import coerce_chosen_day

    ts = _march_ts()
    coerced = coerce_chosen_day(ts, "2025-02-15")  # outside the March window
    assert coerced in get_available_days(ts)


def test_coerce_chosen_day_is_idempotent():
    from ppa.data_loader import coerce_chosen_day

    ts = _march_ts()
    once = coerce_chosen_day(ts, "2025-02-15")
    twice = coerce_chosen_day(ts, once)
    assert twice == once


def test_coerce_chosen_day_unparseable_falls_back_to_middle_day():
    from ppa.data_loader import coerce_chosen_day

    ts = _march_ts()
    coerced = coerce_chosen_day(ts, "not-a-date")
    assert coerced in get_available_days(ts)


def test_validate_scenario_clean_after_coercion():
    """The UI's flow (coerce then validate) must produce no `chosen_day` error."""
    from ppa.data_loader import coerce_chosen_day

    ts = _march_ts()
    available = get_available_days(ts)
    scn = Scenario(chosen_day="2025-02-15")
    coerced = coerce_chosen_day(ts, scn.chosen_day)
    scn = Scenario(chosen_day=coerced)
    errors = validate_scenario(scn, available_days=available)
    assert not any("chosen_day" in e for e in errors), errors


# ── W9: index=14 fallbacks must not crash on a <15-day period ────────────────

def test_fallback_index_survives_short_period():
    """The two `index = 14` fallbacks in the UI must be replaced by coercion;
    here we assert the coercion helper itself handles a period shorter than 15
    days (which is what index=14 would have crashed on)."""
    from ppa.data_loader import coerce_chosen_day

    idx = pd.date_range("2025-03-01", periods=7 * 24, freq="h")
    ts = pd.DataFrame(
        {"ts_PVGen": 0.3, "ts_WindGen": 0.4, "ts_MktPrice": 60.0, "ppaload_mw": 100.0},
        index=idx,
    )
    coerced = coerce_chosen_day(ts, "2025-02-15")
    assert coerced in get_available_days(ts)

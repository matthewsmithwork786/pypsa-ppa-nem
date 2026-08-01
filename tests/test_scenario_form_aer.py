"""Tests for the pure `_apply_pending_aer` helper in ui/scenario_form.py."""
from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from ppa.data import aer_futures
from ui.scenario_form import _apply_pending_aer, _resolve_aer_provenance, _seed_aer_applied_from_scenario


# ── _seed_aer_applied_from_scenario / _resolve_aer_provenance (MEDIUM bug fix) ──
# Regression: `_sf_aer_applied` is popped by `ui.state.set_scenario` on every
# save, so without re-seeding it from the saved scenario's own
# cal_forward_source/cal_forward_price/cal_forward_note, the very next
# "Apply changes" click (even with zero other changes) would overwrite
# cal_forward_source back to "manual" and wipe cal_forward_note.

def test_seed_aer_applied_reseeds_when_scenario_is_aer_sourced_and_state_empty():
    session_state = {}
    _seed_aer_applied_from_scenario(session_state, aer_futures.SOURCE_AER, 104.0, "Indicative only — as at 2025-06-30.")
    assert session_state["_sf_aer_applied"] == {
        "price_aud_mwh": 104.0, "disclaimer": "Indicative only — as at 2025-06-30.",
    }


def test_seed_aer_applied_noop_when_scenario_is_manual():
    session_state = {}
    _seed_aer_applied_from_scenario(session_state, aer_futures.SOURCE_MANUAL, 85.0, "")
    assert "_sf_aer_applied" not in session_state


def test_seed_aer_applied_does_not_clobber_existing_state():
    session_state = {"_sf_aer_applied": {"price_aud_mwh": 999.0, "disclaimer": "already set"}}
    _seed_aer_applied_from_scenario(session_state, aer_futures.SOURCE_AER, 104.0, "different")
    assert session_state["_sf_aer_applied"]["price_aud_mwh"] == 999.0


def test_apply_changes_after_save_preserves_aer_provenance_end_to_end():
    """The full regression scenario: a scenario was already saved with
    cal_forward_source="aer_indicative" (as if `state.set_scenario` had just
    popped `_sf_aer_applied`, simulated here by starting from an empty
    session_state). Re-seeding from the scenario, then resolving provenance
    with the SAME price (as if the user made no edits and just clicked
    "Apply changes" again), must still yield "aer_indicative", not "manual".
    """
    session_state = {}
    saved_price, saved_note = 104.0, "Indicative only — as at 2025-06-30."
    _seed_aer_applied_from_scenario(session_state, aer_futures.SOURCE_AER, saved_price, saved_note)
    # Simulate the cal_forward_price widget re-rendering with its saved value
    # (no edit made).
    source, note = _resolve_aer_provenance(session_state, saved_price)
    assert source == aer_futures.SOURCE_AER
    assert note == saved_note


# ── _resolve_aer_provenance: manual-edit clears stored state (LOW bug fix) ───

def test_resolve_aer_provenance_manual_edit_clears_applied_state():
    session_state = {"_sf_aer_applied": {"price_aud_mwh": 104.0, "disclaimer": "d"}}
    source, note = _resolve_aer_provenance(session_state, 90.0)  # user typed a different value
    assert source == aer_futures.SOURCE_MANUAL
    assert note == ""
    assert "_sf_aer_applied" not in session_state


def test_resolve_aer_provenance_retyping_same_value_after_edit_stays_manual():
    """[LOW bug fix] Once a manual edit is detected, `_sf_aer_applied` must be
    cleared entirely -- so if the user later happens to retype the exact same
    numeric value the AER quote had, it's treated as a fresh manual entry,
    not as "the AER quote is still active".
    """
    session_state = {"_sf_aer_applied": {"price_aud_mwh": 104.0, "disclaimer": "d"}}
    # First: an edit away from the AER price.
    source1, _ = _resolve_aer_provenance(session_state, 90.0)
    assert source1 == aer_futures.SOURCE_MANUAL
    assert "_sf_aer_applied" not in session_state
    # Then: coincidentally retyping the original AER price value.
    source2, note2 = _resolve_aer_provenance(session_state, 104.0)
    assert source2 == aer_futures.SOURCE_MANUAL
    assert note2 == ""


def test_resolve_aer_provenance_no_applied_state_is_manual():
    session_state = {}
    source, note = _resolve_aer_provenance(session_state, 85.0)
    assert source == aer_futures.SOURCE_MANUAL
    assert note == ""


def test_apply_pending_aer_applies_correctly_when_pending_exists():
    session_state = {
        "_sf_aer_pending": {"price_aud_mwh": 104.0, "disclaimer": "Indicative only — as at 2025-06-30."},
    }
    applied = _apply_pending_aer(session_state)
    assert applied is True
    assert session_state["sf_cal_forward_price"] == 104.0
    assert session_state["_sf_aer_applied"]["price_aud_mwh"] == 104.0
    assert "_sf_aer_pending" not in session_state


def test_apply_pending_aer_noop_when_nothing_pending():
    session_state = {"sf_cal_forward_price": 85.0}
    applied = _apply_pending_aer(session_state)
    assert applied is False
    assert session_state["sf_cal_forward_price"] == 85.0
    assert "_sf_aer_applied" not in session_state


def test_apply_pending_aer_idempotent_on_repeat_calls():
    session_state = {
        "_sf_aer_pending": {"price_aud_mwh": 95.0, "disclaimer": "d"},
    }
    first = _apply_pending_aer(session_state)
    second = _apply_pending_aer(session_state)
    assert first is True
    assert second is False
    assert session_state["sf_cal_forward_price"] == 95.0

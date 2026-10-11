"""Dispatch mode selection and a shared solve slot for a small hosted app."""
from __future__ import annotations

import dataclasses
import threading
from contextlib import contextmanager

from ppa.scenario import Scenario

_SOLVE_SLOT = threading.Lock()

REPRESENTATIVE_YEAR_NOTE = (
    "One operating year is solved in PyPSA. Its delivery, merchant volumes and "
    "shortfalls are repeated for the financial horizon; prices and costs follow "
    "the financial assumptions. Generation and battery degradation, different "
    "weather years and changes in dispatch are not simulated in this mode. "
    "Use full multi-year dispatch to assess those effects."
)


def scenario_for_dispatch(scenario: Scenario) -> Scenario:
    """Keep the financial horizon on the user's scenario, solve only its mode."""
    return dataclasses.replace(scenario, simulation_years=scenario.dispatch_years)


@contextmanager
def solve_slot():
    """One solve per app process; refuse a second run without a long queue."""
    if not _SOLVE_SLOT.acquire(blocking=False):
        raise RuntimeError("Another optimisation is running. Please try again when it finishes.")
    try:
        yield
    finally:
        _SOLVE_SLOT.release()

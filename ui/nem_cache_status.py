"""Shared, Streamlit-cached wrapper around `ppa.data.nem_data.cache_status`.

`nem_data.cache_status()` calls `list_simulation_ready_plants()` internally,
which does a full whole-year-check read of every registry plant's 5-minute
SCADA parquet file (~187 real plants -> several seconds). Both
`ui/tabs/nem_map.py` and `ui/tabs/optimization.py` need this status on every
rerun, so it's cached here, keyed on `(year, cache_fingerprint(year, cache_dir))`
so it invalidates whenever the on-disk SCADA/price/registry cache changes.

This module intentionally lives in the UI layer (imports `streamlit`) --
`ppa/data/nem_data.py` must stay Streamlit-free per its no-network/pure-python
design constraint.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ppa.data import nem_data


@st.cache_data
def _cached_cache_status(year: int, fingerprint: tuple, cache_dir: Path) -> dict:
    return nem_data.cache_status(year, cache_dir)


def cached_cache_status(year: int = nem_data.DEFAULT_YEAR, cache_dir: Path = nem_data.NEM_CACHE_DIR) -> dict:
    """`nem_data.cache_status(year, cache_dir)`, cached and keyed on the on-disk
    cache fingerprint so it re-executes only when the cache actually changes.
    """
    fingerprint = nem_data.cache_fingerprint(year, cache_dir)
    return _cached_cache_status(year, fingerprint, cache_dir)

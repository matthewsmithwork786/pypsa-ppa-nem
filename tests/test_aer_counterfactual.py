"""W4 regression: AER indicative hedge prices become the default forward-price
seed, and the counterfactual UI drops European ("EUR"/"€"/"ENTSO"/"CAL Y+1")
language in favour of "Base futures — calendar year (A$/MWh)".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from ppa.data import aer_futures
from tests.fixtures.aer_fixtures import build_aer_fixture_cache

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files whose counterfactual copy must be de-Europeanised (W4 step 4).
_COUNTERFACTUAL_FILES = [
    REPO_ROOT / "ppa" / "counterfactuals.py",
    REPO_ROOT / "ui" / "charts.py",
    REPO_ROOT / "ui" / "tabs" / "results_deep_dive.py",
]
_EURO_PATTERN = re.compile(r"EUR|\u20ac|\bENTSO\b|CAL Y\+1")


def test_forward_price_matches_quarterly_average_from_fixture(tmp_path):
    cache_dir = build_aer_fixture_cache(tmp_path / "nem_cache")
    scenario = type("S", (), {"nem_price_region": "NSW1", "nem_year": 2025})()
    quote = aer_futures.forward_price_for_scenario(scenario, cache_dir=cache_dir)
    assert quote.price_aud_mwh == pytest.approx(104.0)
    assert "Indicative only" in quote.disclaimer


def test_default_seed_sets_aer_indicative_source_when_cache_exists(tmp_path):
    from ui.scenario_form import _default_aer_seed_for_scenario

    cache_dir = build_aer_fixture_cache(tmp_path / "nem_cache")
    scenario = type("S", (), {"nem_price_region": "VIC1", "nem_year": 2025})()
    seed = _default_aer_seed_for_scenario(scenario, cache_dir=cache_dir)
    assert seed is not None
    price, source, note = seed
    assert price == pytest.approx(95.0)
    assert source == aer_futures.SOURCE_AER
    assert note  # the disclaimer


def test_default_seed_none_when_no_cache(tmp_path):
    from ui.scenario_form import _default_aer_seed_for_scenario

    scenario = type("S", (), {"nem_price_region": "NSW1", "nem_year": 2025})()
    assert _default_aer_seed_for_scenario(scenario, cache_dir=tmp_path) is None


def test_no_european_language_in_counterfactual_paths():
    """No string in the counterfactual code paths may mention EUR/€/ENTSO/
    "CAL Y+1" — the strategy is "Base futures hedge" at "Base futures — calendar
    year (A$/MWh)" now."""
    hits: list[str] = []
    for path in _COUNTERFACTUAL_FILES:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _EURO_PATTERN.search(line):
                hits.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not hits, "European forward language still present in counterfactual code paths:\n" + "\n".join(hits)

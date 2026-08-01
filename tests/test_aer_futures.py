"""Tests for ppa/data/aer_futures.py — cache-only AER hedge/forward reader."""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pandas as pd
import pytest

from ppa.data import aer_futures
from tests.fixtures.aer_fixtures import build_aer_fixture_cache, build_bad_schema_futures

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fixture_cache(tmp_path) -> Path:
    return build_aer_fixture_cache(tmp_path / "nem_cache")


# ── Static import-surface guarantee ──────────────────────────────────────────

def test_no_network_imports_in_source():
    source = (REPO_ROOT / "ppa" / "data" / "aer_futures.py").read_text()
    tree = ast.parse(source)
    forbidden = {"requests", "urllib", "httpx", "nemosis", "socket", "streamlit"}
    found_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.add(node.module.split(".")[0])
    bad = found_modules & forbidden
    assert not bad, f"aer_futures.py imports forbidden network-capable modules: {bad}"


# ── Loader: validation / normalization / dedup ───────────────────────────────

def test_load_missing_file_raises_with_helpful_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_aer_futures"):
        aer_futures.load_aer_base_futures(2025, tmp_path)


def test_load_missing_column_raises_valueerror(tmp_path):
    build_bad_schema_futures(tmp_path)
    with pytest.raises(ValueError, match="price_aud_mwh"):
        aer_futures.load_aer_base_futures(2025, tmp_path)


def test_load_drops_nan_price_rows(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    assert "QLD1" not in set(df["region"])


def test_load_dedup_stale_duplicate_loses_to_later_dated_row(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    nsw_q2 = df[(df["region"] == "NSW1") & (df["quarter_label"] == "Q2-2025") & (df["product"] == "Base")]
    assert len(nsw_q2) == 1
    assert float(nsw_q2.iloc[0]["price_aud_mwh"]) == 96.0
    assert nsw_q2.iloc[0]["as_at_date"] == pd.Timestamp("2025-06-30")


def test_load_dedup_uses_parsed_quarter_not_raw_label(fixture_cache):
    """The VIC1 "2025 Q2" row must dedupe against "Q2-2025" (same parsed
    (year, quarter)) rather than surviving as a separate row.
    """
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    vic1 = df[df["region"] == "VIC1"]
    assert len(vic1) == 4  # Q1-Q4 only -- the alt-format Q2 duplicate is gone
    assert "2025 Q2" not in set(vic1["quarter_label"])


def test_load_dedup_unparseable_labels_fall_back_to_raw_string(tmp_path):
    """Two genuinely unparseable labels must not collide with each other (or
    crash) just because `parse_quarter_label` returns None for both.
    """
    hedge_dir = tmp_path / "hedge"
    hedge_dir.mkdir(parents=True)
    df = pd.DataFrame([
        {"region": "NSW1", "quarter_label": "banana", "product": "Base",
         "price_aud_mwh": 50, "as_at_date": pd.Timestamp("2025-06-30")},
        {"region": "NSW1", "quarter_label": "not a quarter", "product": "Base",
         "price_aud_mwh": 60, "as_at_date": pd.Timestamp("2025-06-30")},
    ])
    df.to_parquet(hedge_dir / "aer_base_futures_2025.parquet", index=False)
    loaded = aer_futures.load_aer_base_futures(2025, tmp_path)
    assert len(loaded) == 2
    assert set(loaded["quarter_label"]) == {"banana", "not a quarter"}


def test_load_region_aliasing(tmp_path):
    hedge_dir = tmp_path / "hedge"
    hedge_dir.mkdir(parents=True)
    df = pd.DataFrame([
        {"region": "nsw", "quarter_label": "Q1-2025", "product": "base",
         "price_aud_mwh": 100, "as_at_date": pd.Timestamp("2025-06-30")},
    ])
    df.to_parquet(hedge_dir / "aer_base_futures_2025.parquet", index=False)
    loaded = aer_futures.load_aer_base_futures(2025, tmp_path)
    assert loaded.iloc[0]["region"] == "NSW1"
    assert loaded.iloc[0]["product"] == "Base"


def test_cap_product_excluded_from_base_average(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(df, region="NSW1", quarters=["Q1-2025"], product="Base")
    assert avg == 120.0  # not the Cap row's 300


def test_has_futures_cache_and_list_cached_years(fixture_cache):
    assert aer_futures.has_futures_cache(2025, fixture_cache)
    assert not aer_futures.has_futures_cache(2024, fixture_cache)
    assert aer_futures.list_cached_futures_years(fixture_cache) == [2025]


# ── available_quarters / parse_quarter_label ─────────────────────────────────

@pytest.mark.parametrize("label,expected", [
    ("Q1-2025", (2025, 1)),
    ("Q1 2025", (2025, 1)),
    ("2025 Q1", (2025, 1)),
    ("2025-Q1", (2025, 1)),
    ("2025Q1", (2025, 1)),
])
def test_parse_quarter_label_formats(label, expected):
    assert aer_futures.parse_quarter_label(label) == expected


@pytest.mark.parametrize("garbage", ["not a quarter", "", "Q5-2025", "banana", None])
def test_parse_quarter_label_garbage_returns_none(garbage):
    assert aer_futures.parse_quarter_label(garbage) is None


def test_available_quarters_ordering_and_alt_format(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    quarters = aer_futures.available_quarters(df, region="VIC1")
    assert quarters[0] == "Q1-2025"
    assert quarters[-1] == "Q4-2025"
    # The alt-format "2025 Q2" row is the SAME period as "Q2-2025" and is
    # deduped away in load_aer_base_futures (parsed-quarter dedup key) --
    # only the canonical "Q2-2025" label survives, exactly once.
    assert quarters.count("Q2-2025") == 1
    assert "2025 Q2" not in quarters


def test_available_regions(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    regions = aer_futures.available_regions(df)
    assert set(regions) == {"NSW1", "VIC1", "SA1"}  # QLD1 dropped (NaN), TAS1 absent


# ── quarterly_average arithmetic ─────────────────────────────────────────────

def test_quarterly_average_nsw1_full_year(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(
        df, region="NSW1", quarters=["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"]
    )
    assert avg == pytest.approx(104.0)


def test_quarterly_average_vic1_full_year(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(
        df, region="VIC1", quarters=["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"]
    )
    assert avg == pytest.approx(95.0)


def test_quarterly_average_vic1_default_all_quarters_does_not_double_count(fixture_cache):
    """Regression: before the parsed-quarter dedup fix, VIC1's Q2 survived
    TWICE under two different raw label spellings ("Q2-2025" and "2025 Q2"),
    so the default "all available quarters" mean (no explicit `quarters=`)
    double-counted that period and gave 94.0 instead of the mathematically
    correct 95.0.
    """
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(df, region="VIC1")  # default: ALL quarters
    assert avg == pytest.approx(95.0)


def test_quarterly_average_sa1_missing_quarters(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(df, region="SA1", quarters=["Q1-2025", "Q3-2025"])
    assert avg == pytest.approx(125.0)


def test_quarterly_average_single_quarter_subset(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    avg = aer_futures.quarterly_average(df, region="NSW1", quarters=["Q3-2025"])
    assert avg == pytest.approx(88.0)


def test_quarterly_average_unknown_region_raises_keyerror(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    with pytest.raises(KeyError, match="NSW1"):
        aer_futures.quarterly_average(df, region="TAS1")


def test_quarterly_average_unknown_quarter_raises_valueerror(fixture_cache):
    df = aer_futures.load_aer_base_futures(2025, fixture_cache)
    with pytest.raises(ValueError):
        aer_futures.quarterly_average(df, region="NSW1", quarters=["Q1-2099"])


# ── forward_price_for_scenario ────────────────────────────────────────────────

def test_forward_price_for_scenario_fully_populated(fixture_cache):
    scenario = types.SimpleNamespace(nem_price_region="NSW1", nem_year=2025)
    quote = aer_futures.forward_price_for_scenario(
        scenario, quarters=["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"], cache_dir=fixture_cache,
    )
    assert isinstance(quote, aer_futures.AerForwardQuote)
    assert quote.price_aud_mwh == pytest.approx(104.0)
    assert quote.region == "NSW1"
    assert quote.year == 2025
    assert "Indicative only" in quote.disclaimer


def test_forward_price_for_scenario_bare_object_falls_back_to_defaults(fixture_cache):
    scenario = object()
    quote = aer_futures.forward_price_for_scenario(scenario, cache_dir=fixture_cache)
    assert quote.region == aer_futures.DEFAULT_REGION
    assert quote.year == aer_futures.DEFAULT_YEAR
    assert quote.price_aud_mwh == pytest.approx(104.0)


# ── disclaimer_text ───────────────────────────────────────────────────────────

def test_disclaimer_text_with_date():
    text = aer_futures.disclaimer_text(pd.Timestamp("2025-06-30"))
    assert "2025-06-30" in text
    assert "non-tradable" in text


def test_disclaimer_text_none_graceful():
    text = aer_futures.disclaimer_text(None)
    assert "date unknown" in text.lower()


def test_disclaimer_text_nat_graceful():
    text = aer_futures.disclaimer_text(pd.NaT)
    assert "date unknown" in text.lower()

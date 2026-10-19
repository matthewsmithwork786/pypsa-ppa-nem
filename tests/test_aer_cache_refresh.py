"""The bundled futures snapshot must reproduce the archived AER CSV exactly."""
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from ppa.data.aer_futures import forward_price_for_scenario, load_aer_base_futures

ROOT = Path(__file__).resolve().parents[1]
HEDGE = ROOT / 'data/cache/nem/hedge'
SOURCE = HEDGE / 'source/aer_base_futures_20260807.csv'


def test_cached_prices_match_every_source_quarter_and_region():
    raw = pd.read_csv(SOURCE)
    cached = load_aer_base_futures()
    columns = {
        'NSW1': 'New South Wales price ($ per megawatt hour)',
        'QLD1': 'Queensland price ($ per megawatt hour)',
        'VIC1': 'Victoria price ($ per megawatt hour)',
        'SA1': 'South Australia price ($ per megawatt hour)',
    }
    assert len(cached) == len(raw) * len(columns)
    assert not cached.duplicated(['region', 'quarter_label', 'product']).any()
    for region, column in columns.items():
        expected = raw.set_index('Quarter')[column].astype(float).sort_index()
        actual = cached[cached.region == region].set_index('quarter_label').price_aud_mwh.sort_index()
        pd.testing.assert_series_equal(actual, expected, check_names=False)
    metadata = json.loads((HEDGE / 'source/provenance.json').read_text())
    assert cached.as_at_date.eq(pd.Timestamp(metadata['price_as_at'])).all()
    assert metadata['price_as_at'] != metadata['csv_publication_date']


def test_default_uses_complete_2027_not_partial_2026_or_whole_strip():
    scenario = type('S', (), {'nem_price_region': 'NSW1', 'nem_year': 2025})()
    quote = forward_price_for_scenario(scenario)
    assert quote.quarters == tuple(f'2027 Q{q}' for q in range(1, 5))
    assert quote.price_aud_mwh == pytest.approx(84.275)
    assert quote.as_at_date == pd.Timestamp('2026-06-30')


def test_offline_import_reproduces_the_cache_with_explicit_quote_date(tmp_path):
    completed = subprocess.run([
        sys.executable, str(ROOT / 'scripts/fetch_aer_futures.py'),
        '--year', '2025', '--input-csv', str(SOURCE),
        '--as-at', '2026-06-30', '--out-dir', str(tmp_path), '--overwrite',
    ], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    pd.testing.assert_frame_equal(
        pd.read_parquet(tmp_path / 'aer_base_futures_2025.parquet'),
        pd.read_parquet(HEDGE / 'aer_base_futures_2025.parquet'),
    )

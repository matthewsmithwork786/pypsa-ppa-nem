"""Deterministic synthetic AER base-futures cache fixture for testing
`ppa.data.aer_futures`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_aer_fixture_cache(root_dir, year: int = 2025) -> Path:
    """Writes `root_dir/hedge/aer_base_futures_{year}.parquet` and returns
    `root_dir` (usable directly as `cache_dir=` for `ppa.data.aer_futures`
    functions and for `ppa.data.nem_data.NEM_CACHE_DIR`-shaped callers).
    """
    root_dir = Path(root_dir)
    hedge_dir = root_dir / "hedge"
    hedge_dir.mkdir(parents=True, exist_ok=True)

    real_as_at = pd.Timestamp("2025-06-30")
    stale_as_at = pd.Timestamp("2025-03-31")

    rows = [
        # NSW1 Q1-Q4 2025 (Base) -- mean exactly 104.0
        {"region": "NSW1", "quarter_label": "Q1-2025", "product": "Base", "price_aud_mwh": 120, "as_at_date": real_as_at},
        {"region": "NSW1", "quarter_label": "Q2-2025", "product": "Base", "price_aud_mwh": 96, "as_at_date": real_as_at},
        {"region": "NSW1", "quarter_label": "Q3-2025", "product": "Base", "price_aud_mwh": 88, "as_at_date": real_as_at},
        {"region": "NSW1", "quarter_label": "Q4-2025", "product": "Base", "price_aud_mwh": 112, "as_at_date": real_as_at},

        # VIC1 Q1-Q4 2025 (Base) -- mean exactly 95.0
        {"region": "VIC1", "quarter_label": "Q1-2025", "product": "Base", "price_aud_mwh": 110, "as_at_date": real_as_at},
        {"region": "VIC1", "quarter_label": "Q2-2025", "product": "Base", "price_aud_mwh": 90, "as_at_date": real_as_at},
        {"region": "VIC1", "quarter_label": "Q3-2025", "product": "Base", "price_aud_mwh": 80, "as_at_date": real_as_at},
        {"region": "VIC1", "quarter_label": "Q4-2025", "product": "Base", "price_aud_mwh": 100, "as_at_date": real_as_at},
        # VIC1 alternate-label-format row for the SAME underlying period (Q2)
        # as the "Q2-2025" row above -- a different raw string
        # ("2025 Q2" vs. "Q2-2025") that `parse_quarter_label` recognizes as
        # the same (year, quarter). This is the regression case for the
        # parsed-quarter dedup: both spellings must collapse to a single row
        # (same as_at_date, same price here) rather than both surviving and
        # double-counting this quarter in `quarterly_average`'s default
        # "all quarters" mean.
        {"region": "VIC1", "quarter_label": "2025 Q2", "product": "Base", "price_aud_mwh": 90, "as_at_date": real_as_at},

        # SA1 Q1, Q3 2025 (Base) -- mean 125.0 (missing-quarter case)
        {"region": "SA1", "quarter_label": "Q1-2025", "product": "Base", "price_aud_mwh": 150, "as_at_date": real_as_at},
        {"region": "SA1", "quarter_label": "Q3-2025", "product": "Base", "price_aud_mwh": 100, "as_at_date": real_as_at},

        # NSW1 Q1-2025 Cap product -- must be excluded by product filter
        {"region": "NSW1", "quarter_label": "Q1-2025", "product": "Cap", "price_aud_mwh": 300, "as_at_date": real_as_at},

        # NSW1 Q2-2025 stale duplicate -- must lose to the real Q2 row above
        # (later as_at_date wins).
        {"region": "NSW1", "quarter_label": "Q2-2025", "product": "Base", "price_aud_mwh": 9999, "as_at_date": stale_as_at},

        # QLD1 Q1-2025 NaN price -- must be dropped
        {"region": "QLD1", "quarter_label": "Q1-2025", "product": "Base", "price_aud_mwh": float("nan"), "as_at_date": real_as_at},

        # TAS1: no rows at all -- missing-region case (nothing written)
    ]

    df = pd.DataFrame(rows)
    df.to_parquet(hedge_dir / f"aer_base_futures_{year}.parquet", index=False)
    return root_dir


def build_bad_schema_futures(root_dir) -> Path:
    """Writes a parquet missing the `price_aud_mwh` column, for the
    `ValueError` schema-validation test.
    """
    root_dir = Path(root_dir)
    hedge_dir = root_dir / "hedge"
    hedge_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [{"region": "NSW1", "quarter_label": "Q1-2025", "product": "Base", "as_at_date": pd.Timestamp("2025-06-30")}]
    )
    df.to_parquet(hedge_dir / "aer_base_futures_2025.parquet", index=False)
    return root_dir

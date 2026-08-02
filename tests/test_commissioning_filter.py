"""Plants still being commissioned must not appear as operational.

A plant being built out during the year produces in every month and passes
every coverage/span check, but its capacity factor describes a half-built
asset. MacIntyre (923 MW) ran at a 9.8% CF through 2025 while its monthly peak
climbed from 0.09 to 0.53 of nameplate.

The discriminator is the RAMP, not the level: an operational plant reaches
roughly its own annual peak within the first months; a commissioning one does
not. Normalising by the plant's own peak keeps this robust to AC clipping
(solar tops out near 0.80 of registered AC) and to heavy curtailment later in
the year.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ppa.data.nem_data import (
    COMMISSIONING_MIN_PEAK_FRACTION,
    commissioning_ramp_check,
)

YEAR = 2025


def _series(monthly_peak_fraction: dict[int, float], capacity: float = 100.0) -> pd.Series:
    """Synthetic 5-min year whose monthly peak matches the given fractions."""
    idx = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31 23:55", freq="5min")
    s = pd.Series(0.0, index=idx)
    for month, frac in monthly_peak_fraction.items():
        mask = s.index.month == month
        # A ramp within the month so the max is the stated fraction.
        s.loc[mask] = np.linspace(0, frac * capacity, int(mask.sum()))
    return s


def test_steady_plant_is_fully_operational():
    """Near-constant monthly peaks (a normal operating plant)."""
    ok, ratio = commissioning_ramp_check(_series({m: 0.95 for m in range(1, 13)}), YEAR)
    assert ok
    assert ratio == pytest.approx(1.0, abs=1e-6)


def test_commissioning_ramp_is_rejected():
    """MacIntyre's shape: 0.09 climbing to 0.53 across the year."""
    profile = {1: 0.09, 2: 0.15, 3: 0.16, 4: 0.16, 5: 0.33, 6: 0.32,
               7: 0.47, 8: 0.47, 9: 0.40, 10: 0.46, 11: 0.48, 12: 0.53}
    ok, ratio = commissioning_ramp_check(_series(profile), YEAR)
    assert not ok
    assert ratio < COMMISSIONING_MIN_PEAK_FRACTION


def test_mid_year_curtailment_is_not_mistaken_for_commissioning():
    """Limondale's shape: starts high, dips mid-year from curtailment.

    This is the false positive that a naive "monthly peak must always be high"
    rule would produce, and the reason the check anchors on the EARLY months.
    """
    profile = {1: 0.73, 2: 0.80, 3: 0.80, 4: 0.77, 5: 0.67, 6: 0.53,
               7: 0.67, 8: 0.40, 9: 0.81, 10: 0.75, 11: 0.81, 12: 0.81}
    ok, ratio = commissioning_ramp_check(_series(profile), YEAR)
    assert ok, f"operational plant with mid-year curtailment rejected (ratio {ratio:.2f})"


def test_single_month_january_outage_is_tolerated():
    """Using the best of Jan/Feb means one bad month does not disqualify."""
    profile = {1: 0.05, **{m: 0.95 for m in range(2, 13)}}
    ok, _ = commissioning_ramp_check(_series(profile), YEAR)
    assert ok


def test_ac_clipped_solar_is_not_penalised():
    """Solar tops out near 0.80 of registered AC all year -- still operational.

    Normalising by the plant's own annual peak rather than nameplate is what
    makes this pass.
    """
    ok, ratio = commissioning_ramp_check(_series({m: 0.80 for m in range(1, 13)}), YEAR)
    assert ok
    assert ratio == pytest.approx(1.0, abs=1e-6)


def test_empty_or_dead_series_is_not_operational():
    idx = pd.date_range(f"{YEAR}-01-01", periods=100, freq="5min")
    ok, ratio = commissioning_ramp_check(pd.Series(0.0, index=idx), YEAR)
    assert not ok and ratio is None

    ok, ratio = commissioning_ramp_check(pd.Series(dtype=float, index=pd.DatetimeIndex([])), YEAR)
    assert not ok and ratio is None


def test_summary_reports_commissioning_status(tmp_path):
    """scada_summary must surface the status and an explanatory reason."""
    from ppa.data import nem_data

    (tmp_path / "scada").mkdir(parents=True)
    ramp = _series({1: 0.09, **{m: 0.10 * m for m in range(2, 13)}})
    pd.DataFrame({"scadavalue": ramp.to_numpy()}, index=ramp.index).to_parquet(
        tmp_path / "scada" / "RAMP1_2025.parquet"
    )

    summary = nem_data.scada_summary("RAMP1", 100.0, YEAR, tmp_path)
    assert summary.status != "ready"
    assert not summary.fully_operational
    assert "commissioning" in summary.reject_reasons

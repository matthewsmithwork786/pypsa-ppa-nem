"""Lockstep test: the live Excel export must match the Phase 2 devex-at-FID model.

Ensures ``ppa/financial_model_excel.py`` stays in sync with ``ppa/financial_model.py``:
no leftover per-technology "dev years" rows on the Inputs sheet, and exactly one
"Devex" row on the Model sheet whose formula is keyed off the live
``development_start`` input cell (single bullet at FID, not a multi-period spread).
"""
from __future__ import annotations

import re
from io import BytesIO

import openpyxl

from ppa.financial_model import (
    EnergyInputs,
    ProjectFinanceInputs,
    run_project_finance,
)
from ppa.financial_model_excel import export_financial_model

_DEV_YEAR_RE = re.compile(r"dev.*year", re.IGNORECASE)


def _simple_energy() -> EnergyInputs:
    return EnergyInputs(
        onsw_mw=100.0,
        pv_mw=0.0,
        bess_mw=0.0,
        bess_mwh=0.0,
        load_mw=50.0,
        ppa_gwh=300.0,
        excess_solar_gwh=0.0,
        excess_nonsolar_gwh=50.0,
        penalty_gwh=5.0,
        total_solar_gwh=0.0,
        total_nonsolar_gwh=350.0,
        sell_solar_price=60.0,
        sell_nonsolar_price=55.0,
        purchase_price=70.0,
        marketbuy_gwh=2.0,
        name="excel-export test",
    )


def test_excel_export_matches_devex_single_bullet_at_fid():
    p = ProjectFinanceInputs()
    e = _simple_energy()
    result = run_project_finance(p, e)

    xlsx_bytes = export_financial_model(p, e, result)
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), data_only=False)

    # (a) No "dev...year(s)" labels remain on the Inputs sheet (the removed
    # per-technology dev-year rows must not have crept back in).
    inputs_ws = wb["Inputs"]
    label_col = 2  # column B holds row labels throughout this workbook
    for row in inputs_ws.iter_rows(min_col=label_col, max_col=label_col):
        for cell in row:
            if isinstance(cell.value, str):
                assert not _DEV_YEAR_RE.search(cell.value), (
                    f"unexpected dev-year label {cell.value!r} at {cell.coordinate}"
                )

    # Locate the "Development start period" input cell so we can confirm the
    # Model sheet's devex formula references it.
    dev_start_cell = None
    for row in inputs_ws.iter_rows(min_col=label_col, max_col=label_col):
        for cell in row:
            if cell.value == "Development start period":
                dev_start_cell = f"$C${cell.row}"
                break
        if dev_start_cell:
            break
    assert dev_start_cell is not None, "could not find 'Development start period' input row"

    # (b) Exactly one "Devex" row on the Model sheet.
    model_ws = wb["Model"]
    devex_rows = [
        cell.row
        for row in model_ws.iter_rows(min_col=label_col, max_col=label_col)
        for cell in row
        if cell.value == "Devex"
    ]
    assert len(devex_rows) == 1, f"expected exactly one 'Devex' row, found {devex_rows}"

    # (c) That row's formula references the live development_start cell (single
    # bullet at FID), not a multi-period spread.
    devex_row = devex_rows[0]
    formula_cells = [
        cell
        for cell in model_ws[devex_row]
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert formula_cells, "Devex row has no formula cells"
    for cell in formula_cells:
        assert dev_start_cell in cell.value, (
            f"Devex formula at {cell.coordinate} does not reference "
            f"{dev_start_cell} ({cell.value!r})"
        )

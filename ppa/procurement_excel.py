"""Runtime XLSX export using the application's existing Excel export dependency."""
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from ppa.procurement_reporting import cost_breakdown

NUMBER = '#,##0.00;(#,##0.00);"—"'
PERCENT = '0.00%;(0.00%);"—"'


def _cached_formulas(data, values):
    """Keep formulas editable and provide their verified current values to viewers."""
    out = BytesIO()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(BytesIO(data)) as source, ZipFile(out, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(content)
                for cell in root.iter(f"{{{ns}}}c"):
                    address = cell.get("r")
                    if address in values and cell.find(f"{{{ns}}}f") is not None:
                        value = cell.find(f"{{{ns}}}v")
                        if value is None:
                            value = ET.SubElement(cell, f"{{{ns}}}v")
                        value.text = repr(float(values[address]))
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
    return out.getvalue()


def export_procurement(result):
    d, s = result["dispatch"], result["assumptions"]
    wb = Workbook()
    annual = wb.active
    annual.title = "Annual financials"
    hourly = wb.create_sheet("Hourly dispatch")
    columns = list(d.columns)
    hourly.append(["Timestamp (AEST)", *[f"{c} ({'A$/MWh' if c == 'Spot price' else 'MWh' if c == 'Stored energy' else 'MW'})" for c in columns]])
    for timestamp, row in d.iterrows():
        hourly.append([timestamp.to_pydatetime(), *[float(v) for v in row]])
    last = len(d) + 1
    hourly.freeze_panes = "B2"
    hourly.auto_filter.ref = f"A1:{get_column_letter(len(columns)+1)}{last}"
    hourly.column_dimensions["A"].width = 23
    for i in range(2, len(columns) + 2):
        hourly.column_dimensions[get_column_letter(i)].width = 21
    for row in hourly.iter_rows(min_row=2):
        row[0].number_format = "yyyy-mm-dd hh:mm"
        for cell in row[1:]:
            cell.number_format = NUMBER

    def span(name):
        col = get_column_letter(columns.index(name) + 2)
        return f"'Hourly dispatch'!{col}2:{col}{last}"

    cached = {}

    def formula(address, expression, value):
        annual[address] = "=" + expression
        cached[address] = value

    annual.merge_cells("A1:F1")
    annual["A1"] = f"Data centre procurement · {d.index[0].year}"
    annual["A1"].font = Font(size=16, bold=True, color="17365D")
    annual["A3"] = "Annual load (GWh)"
    formula("B3", f"SUM({span('Load')})/1000", d.Load.sum() / 1000)
    annual["A4"], annual["B4"] = "Peak load (MW)", s.peak_mw
    for row, label, rate in [(5, "PV PPA tariff (A$/MWh)", s.pv_tariff),
                              (6, "Wind PPA tariff (A$/MWh)", s.wind_tariff),
                              (7, "BESS tolling (A$/MWh/month)", s.storage_tariff)]:
        annual.cell(row, 1, label)
        annual.cell(row, 4, rate).font = Font(color="0000FF")
    annual["A9"], annual["E9"] = "Annual financial summary", "Annual cost (A$)"
    f = result["fees"]
    summary = [(10, "Total PPA payments", "SUM(E22:E23)", f["PV PPA"] + f["Wind PPA"]),
               (11, "BESS tolling", "E24", f["BESS fee"]),
               (12, "Spot shortfalls", "E25", f["Spot shortfalls"]),
               (13, "Total procurement cost", "SUM(E22:E25)", f["PV PPA"] + f["Wind PPA"] + f["BESS fee"] + f["Spot shortfalls"]),
               (14, "Total excess revenue", "SUM(E26:E28)", -f["Excess revenue"]),
               (15, "Portfolio net cost", "E13+E14", result["net_cost"]),
               (16, "Spot-only counterfactual", f"SUMPRODUCT({span('Load')},{span('Spot price')})", result["spot_cost"]),
               (17, "Savings versus spot", "E16-E15", result["savings"]),
               (18, "Effective procurement cost (A$/MWh)", "E15/(B3*1000)", result["net_cost"] / d.Load.sum())]
    for row, label, expression, value in summary:
        annual.cell(row, 1, label)
        formula(f"E{row}", expression, value)
    annual.append([])
    headers = ["Cost bridge", "Quantity", "Unit", "Rate (A$/unit)", "Annual cost (A$)", "Rate basis"]
    for col, value in enumerate(headers, 1):
        annual.cell(21, col, value)
    detail = cost_breakdown(result)
    for i, row in enumerate(detail.itertuples(index=False, name=None), 22):
        label, quantity, unit, rate, rate_unit, amount = row
        annual.cell(i, 1, label)
        annual.cell(i, 3, unit)
        annual.cell(i, 6, rate_unit)
        if i in (22, 23):
            source = "PV" if i == 22 else "Wind"
            formula(f"B{i}", f"SUM({span(source)})/1000", quantity)
            formula(f"D{i}", "D5" if i == 22 else "D6", rate)
        elif i == 24:
            annual.cell(i, 2, quantity)
            formula(f"D{i}", "D7", rate)
        else:
            name = "Spot purchases" if i == 25 else f"{label.split()[0]} excess"
            formula(f"B{i}", f"SUM({span(name)})/1000", quantity)
            formula(f"D{i}", f"IF(SUM({span(name)})=0,0,SUMPRODUCT({span(name)},{span('Spot price')})/SUM({span(name)}))", rate)
        factor = "12" if i == 24 else "1000"
        sign = "-" if i >= 26 else ""
        formula(f"E{i}", f"{sign}B{i}*{factor}*D{i}", amount)
    annual["A30"] = "Matching achieved"
    for row, (metric, value) in enumerate(result["matching"].itertuples(index=False, name=None), 31):
        annual.cell(row, 1, metric)
        if value == value:
            annual.cell(row, 2, float(value)).number_format = PERCENT
    notes = ["GWh × 1,000 × A$/MWh; BESS MWh capacity × 12 months × monthly tolling rate.",
             "Excess cash flows are negative costs; negative landed prices make excess sales a cost.",
             "PV/wind charging, curtailment and excess sales are allocated pro rata. BESS discharge is reserved for load, so BESS excess sales are zero.",
             "Landed excess prices are volume-weighted scenario spot prices; no network, retail or tax adjustments.",
             "Hourly power values are MW over one-hour intervals, numerically equal to MWh; stored energy is MWh."]
    for i, note in enumerate(notes, 41):
        annual.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        annual.cell(i, 1, note).alignment = Alignment(wrap_text=True, vertical="top")
        annual.row_dimensions[i].height = 42
    annual.freeze_panes = "B10"
    widths = {"A": 48, "B": 20, "C": 20, "D": 22, "E": 25, "F": 22}
    for col, width in widths.items():
        annual.column_dimensions[col].width = width
    for row in annual.iter_rows(min_row=3, max_row=28):
        for cell in row[1:]:
            if cell.data_type == "f" or isinstance(cell.value, (int, float)):
                cell.number_format = NUMBER
                cell.alignment = Alignment(horizontal="right")
    fill = PatternFill("solid", fgColor="17365D")
    for sheet, rows in [(hourly, [1]), (annual, [9, 21, 30])]:
        sheet.sheet_view.showGridLines = False
        for index in rows:
            for cell in sheet[index]:
                cell.fill = fill
                cell.font = Font(bold=True, color="FFFFFF")
    for row in [10, 13, 14, 15, 16, 17, 18]:
        for cell in annual[row][:6]:
            cell.font = Font(bold=True, color="17365D")
            cell.border = Border(top=Side(style="thin", color="A6B8C8"))
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.calcMode = "auto"
    buffer = BytesIO()
    wb.save(buffer)
    return _cached_formulas(buffer.getvalue(), cached)

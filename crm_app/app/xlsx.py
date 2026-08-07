"""
app/xlsx.py
------------
A very small SpreadsheetML (.xlsx) writer - just enough to emit the tabular
documents this app generates in Excel format alongside their printable HTML.

WHY HAND-ROLLED. Same reasoning as app/docx.py: an .xlsx is an OPC package, a
ZIP of XML parts, and the sheets we emit are a bold header row over a grid of
text. Adding `openpyxl` would pull in a dependency tree for that, against a
list that is otherwise Flask plus the standard library, and `zipfile` is
already used here.

WHAT IT DOES NOT DO. Not a general spreadsheet library: one worksheet, no
formulas, no merged cells, no number formats, no charts. Bold header, column
widths and frozen header row are all it knows. If a future document needs
more, take the dependency instead of growing this.

EVERY CELL IS WRITTEN AS TEXT, deliberately. These sheets carry shipping bill
numbers, container numbers and times - identifiers, not quantities - and
Excel's helpfulness with them is destructive: leading zeros vanish and
anything that looks like a date is silently converted. Text round-trips.

Usage:

    data = build_sheet("E-Seal", ["A", "B"], [["1", "2"]], widths=[18, 24])
"""

import io
import zipfile
from typing import List, Optional, Sequence
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"\
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"\
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml"\
 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"\
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"\
 Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"\
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"\
 Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2"\
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"\
 Target="styles.xml"/>
</Relationships>"""

# Style 0 is plain, style 1 is bold - all the header row needs. Excel rejects
# a styles part with fewer than two fills, hence the unused gray125.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

# Excel's own limits on a sheet name; a caller passing something longer or
# containing these would produce a file Excel refuses to open.
_ILLEGAL_SHEET_CHARS = set(r"[]:*?/\'")


def _safe_sheet_name(name: str) -> str:
    cleaned = "".join(" " if ch in _ILLEGAL_SHEET_CHARS else ch for ch in (name or "Sheet1"))
    return cleaned.strip()[:31] or "Sheet1"


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell(reference: str, value, style: int) -> str:
    """An inline-string cell - see the module docstring on why everything is
    text. Inline strings also avoid needing a sharedStrings part."""
    text = "" if value is None else str(value)
    style_attr = f' s="{style}"' if style else ""
    if not text:
        return f'<c r="{reference}"{style_attr}/>'
    return f'<c r="{reference}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(header: Sequence, rows: Sequence[Sequence], widths: Optional[Sequence[float]]) -> str:
    columns = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 1

    cols_xml = ""
    if widths:
        entries = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths[:columns])
        )
        cols_xml = f"<cols>{entries}</cols>"

    body = []
    number = 1
    if header:
        cells = "".join(_cell(f"{column_letter(i)}{number}", v, 1) for i, v in enumerate(header))
        body.append(f'<row r="{number}">{cells}</row>')
        number += 1
    for row in rows:
        cells = "".join(_cell(f"{column_letter(i)}{number}", v, 0) for i, v in enumerate(row))
        body.append(f'<row r="{number}">{cells}</row>')
        number += 1

    # Keep the header visible when the container list is long.
    freeze = (
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>" if header else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'{freeze}{cols_xml}<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def build_sheet(sheet_name: str, header: Sequence, rows: Sequence[Sequence],
                widths: Optional[Sequence[float]] = None) -> bytes:
    """A one-worksheet .xlsx as bytes, ready to send as a file."""
    name = _safe_sheet_name(sheet_name)
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(header, rows, widths))
    return buffer.getvalue()

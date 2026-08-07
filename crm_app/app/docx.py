"""
app/docx.py
------------
A very small WordprocessingML (.docx) writer - just enough to emit the
documents this app generates in Word format alongside their printable HTML.

WHY HAND-ROLLED. A .docx is an OPC package: a ZIP of XML parts. The two
parts a reader needs are a content-type map and `word/document.xml`, both of
which are short and entirely mechanical for the fixed layouts we emit. Adding
`python-docx` would pull in `lxml` (a C extension) for that, against a
dependency list that is otherwise Flask plus the standard library - and
`zipfile` is already used elsewhere (BackupService bundles the DB backup).

WHAT IT DOES NOT DO. This is not a general Word library: no images, no
styles part, no headers/footers, no numbering. Borders, shading, alignment,
bold, column widths, colspan and rowspan are all it knows, because that is
all the sheets need. If a future document needs more than that, take the
dependency instead of growing this.

Usage:

    doc = DocxDocument()
    doc.heading("BL DRAFT")
    doc.table([[Cell("A", bold=True), Cell("B")],
               [Cell("1"), Cell("2")]],
              widths=[60, 40])
    data = doc.render()          # -> bytes, ready to send as a file
"""

import io
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Union
from xml.sax.saxutils import escape

# One A4 portrait page with 0.5" margins, in twentieths of a point (twips).
_PAGE_WIDTH = 11906
_PAGE_HEIGHT = 16838
_MARGIN = 720
# The usable width every table is laid out inside.
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"\
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"\
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"\
 Target="word/document.xml"/>
</Relationships>"""


@dataclass
class Cell:
    """One table cell.

    `text` may be a single string or a list of lines - a list becomes one
    paragraph per line, which is how the BL draft stacks an address or a
    description block inside a single bordered box.

    `rowspan` is written as Word's vertical merge: this cell opens the merge
    and `rowspan - 1` continuation cells are emitted on the rows below, which
    `DocxDocument.table` does for you.
    """
    text: Union[str, Sequence[str]] = ""
    bold: bool = False
    align: str = "left"          # left | center | right
    colspan: int = 1
    rowspan: int = 1
    shade: Optional[str] = None  # hex fill, e.g. "D9E2D0"
    size: Optional[int] = None   # half-points; None = the document default

    @property
    def lines(self) -> List[str]:
        if isinstance(self.text, str):
            return self.text.split("\n")
        return list(self.text) or [""]


@dataclass
class _Table:
    rows: List[List[Cell]]
    widths: Optional[Sequence[float]] = None  # relative, any scale; None = even
    border: bool = True
    font_size: int = 16                       # half-points, i.e. 8pt


@dataclass
class _Paragraph:
    text: str = ""
    bold: bool = False
    align: str = "left"
    size: int = 20                            # half-points, i.e. 10pt
    space_after: int = 60


def _t(text: str) -> str:
    """A run's text element, preserving the leading/trailing spaces Word would
    otherwise collapse."""
    return f'<w:t xml:space="preserve">{escape(text or "")}</w:t>'


def _paragraph_xml(text: str, bold: bool, align: str, size: int, space_after: int = 0) -> str:
    props = [f'<w:spacing w:before="0" w:after="{space_after}" w:line="240" w:lineRule="auto"/>']
    if align != "left":
        props.append(f'<w:jc w:val="{align}"/>')
    run_props = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
    return (
        f"<w:p><w:pPr>{''.join(props)}{run_props}</w:pPr>"
        f"<w:r>{run_props}{_t(text)}</w:r></w:p>"
    )


def _borders_xml() -> str:
    edges = ("top", "left", "bottom", "right", "insideH", "insideV")
    return "<w:tblBorders>" + "".join(
        f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="000000"/>' for e in edges
    ) + "</w:tblBorders>"


def _cell_xml(cell: Cell, width: int, font_size: int, vmerge: Optional[str] = None) -> str:
    props = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
    if cell.colspan > 1:
        props.append(f'<w:gridSpan w:val="{cell.colspan}"/>')
    if vmerge == "restart":
        props.append('<w:vMerge w:val="restart"/>')
    elif vmerge == "continue":
        props.append("<w:vMerge/>")
    if cell.shade:
        props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{cell.shade}"/>')
    props.append('<w:vAlign w:val="center"/>')
    body = "" if vmerge == "continue" else "".join(
        _paragraph_xml(line, cell.bold, cell.align, cell.size or font_size)
        for line in cell.lines
    )
    # Word requires at least one paragraph in every cell, merged ones included.
    return f"<w:tc><w:tcPr>{''.join(props)}</w:tcPr>{body or _paragraph_xml('', False, 'left', font_size)}</w:tc>"


class DocxDocument:
    """Builds a single-section A4 portrait document."""

    def __init__(self):
        self._blocks: List[Union[_Table, _Paragraph]] = []

    # ---- content ---------------------------------------------------------
    def heading(self, text: str, size: int = 28) -> None:
        self._blocks.append(_Paragraph(text, bold=True, align="center", size=size, space_after=120))

    def paragraph(self, text: str = "", bold: bool = False, align: str = "left",
                  size: int = 20, space_after: int = 60) -> None:
        self._blocks.append(_Paragraph(text, bold, align, size, space_after))

    def table(self, rows: Sequence[Sequence[Cell]], widths: Optional[Sequence[float]] = None,
              font_size: int = 16) -> None:
        self._blocks.append(_Table([list(r) for r in rows], widths, font_size=font_size))

    # ---- rendering -------------------------------------------------------
    def _column_widths(self, table: _Table) -> List[int]:
        """Relative weights scaled to the printable width, in twips. Any
        rounding drift is given to the last column so the grid still sums to
        exactly the page width and Word doesn't re-flow it."""
        columns = max(sum(c.colspan for c in row) for row in table.rows)
        weights = list(table.widths or [1] * columns)
        weights = (weights + [1] * columns)[:columns]
        total = sum(weights) or 1
        widths = [int(_CONTENT_WIDTH * w / total) for w in weights]
        widths[-1] += _CONTENT_WIDTH - sum(widths)
        return widths

    def _table_xml(self, table: _Table) -> str:
        widths = self._column_widths(table)
        # A cell with rowspan leaves continuation cells on the rows below.
        # Keyed by the column the span STARTS at and carrying its colspan,
        # because a continuation must repeat the opener's gridSpan exactly -
        # emitting one continuation per column instead would break the merge.
        pending: dict = {}   # start column -> {"rows": remaining, "colspan": n}
        rows_xml = []
        for row in table.rows:
            cells_xml = []
            column = 0
            cursor = iter(row)
            used = []
            opened = {}
            while column < len(widths):
                carried = pending.get(column)
                if carried:
                    width = sum(widths[column:column + carried["colspan"]])
                    cells_xml.append(_cell_xml(Cell(colspan=carried["colspan"]), width,
                                               table.font_size, vmerge="continue"))
                    used.append(column)
                    column += carried["colspan"]
                    continue
                cell = next(cursor, None)
                if cell is None:
                    break
                width = sum(widths[column:column + cell.colspan])
                cells_xml.append(_cell_xml(cell, width, table.font_size,
                                           vmerge="restart" if cell.rowspan > 1 else None))
                if cell.rowspan > 1:
                    opened[column] = {"rows": cell.rowspan - 1, "colspan": cell.colspan}
                column += cell.colspan
            rows_xml.append(f"<w:tr>{''.join(cells_xml)}</w:tr>")
            # Only the spans actually continued on THIS row count down; a span
            # opened on this row starts counting from the next one.
            for start in used:
                pending[start]["rows"] -= 1
                if pending[start]["rows"] <= 0:
                    del pending[start]
            pending.update(opened)
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        props = (
            f'<w:tblPr><w:tblW w:w="{_CONTENT_WIDTH}" w:type="dxa"/>'
            f'<w:tblLayout w:type="fixed"/>{_borders_xml() if table.border else ""}</w:tblPr>'
        )
        return f"<w:tbl>{props}<w:tblGrid>{grid}</w:tblGrid>{''.join(rows_xml)}</w:tbl>"

    def _document_xml(self) -> str:
        body = []
        previous_was_table = False
        for block in self._blocks:
            if isinstance(block, _Table):
                # Word merges adjacent tables into one; an empty paragraph
                # between them keeps them apart.
                if previous_was_table:
                    body.append(_paragraph_xml("", False, "left", 12))
                body.append(self._table_xml(block))
                previous_was_table = True
            else:
                body.append(_paragraph_xml(block.text, block.bold, block.align,
                                           block.size, block.space_after))
                previous_was_table = False
        section = (
            f'<w:sectPr><w:pgSz w:w="{_PAGE_WIDTH}" w:h="{_PAGE_HEIGHT}"/>'
            f'<w:pgMar w:top="{_MARGIN}" w:right="{_MARGIN}" w:bottom="{_MARGIN}" w:left="{_MARGIN}"'
            f' w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W_NS}"><w:body>{"".join(body)}{section}</w:body></w:document>'
        )

    def render(self) -> bytes:
        """The finished .docx as bytes."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
            archive.writestr("_rels/.rels", _ROOT_RELS)
            archive.writestr("word/document.xml", self._document_xml())
        return buffer.getvalue()

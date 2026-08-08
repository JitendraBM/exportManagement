"""
app/routes/bl_drafts.py
------------------------
The BL DRAFT - the draft bill of lading handed to the shipping line.

Like the Export Packing List and Annexure-C it is a pure read-only
attachment with no identity of its own: the header comes from the parent
Export Invoice and the container table from that invoice's Export Packing
List, so there is nothing to create, edit or delete.

It is the one document that goes out in two formats. The printable HTML view
is the PDF route (print to PDF, as every other sheet here does), and the same
assembled data is written to a .docx by `app/docx.py` for the shipping line's
own editing.

Both formats are built from ONE `_draft(...)` call, so the Word file can
never drift from the printed sheet.
"""

from flask import Blueprint, render_template, current_app, g, abort, send_file
import io

from app.docx import Cell, DocxDocument
from app.exceptions import NotFoundError
from app.utils import login_required

bl_drafts_bp = Blueprint("bl_drafts", __name__, url_prefix="/bl-drafts")

# Word's .docx media type, used for the download response.
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The container table's columns, shared by the sheet and the Word file.
_CONTAINER_COLUMNS = [
    "Container No", "Line Seal No", "Packing", "Qty", "Alt. Qty",
    "Gross Weight(KG)", "Net Weight(KG)",
]


def _dmy(value) -> str:
    """dd-mm-yyyy, matching the reference's date cells."""
    parts = (str(value or "")[:10]).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else (value or "-")


def _num(value, places: int = 2) -> str:
    return f"{(value or 0):,.{places}f}"


def _block(text) -> list:
    """An address as the up-to-five lines the reference boxes allow."""
    return [line for line in str(text or "").splitlines() if line.strip()]


def _draft(invoice, company, packing_list) -> dict:
    """Everything both outputs print, derived once.

    Container rows come from the packing list's split when there is one; the
    weight totals fall back to the invoice's own typed figures when it has no
    split yet, exactly as the export invoice sheet does.
    """
    rows = packing_list.container_rows if packing_list and packing_list.items else []
    totals = {
        "pallets": sum(r["pallets"] for r in rows),
        "quantity_boxes": sum(r["quantity_boxes"] for r in rows),
        "quantity_value": sum(r["quantity_value"] for r in rows),
        "gross_weight_kg": sum(r["gross_weight_kg"] for r in rows),
        "net_weight_kg": sum(r["net_weight_kg"] for r in rows),
    }
    gross = totals["gross_weight_kg"] or invoice.total_gross_weight_kg or 0
    net = totals["net_weight_kg"] or invoice.total_net_weight_kg or 0

    # "20 X 20FT FCL CONTAINER , SAID TO CONTAIN" - one line per container type
    # booked, off the invoice's own Container Details list.
    said_to_contain = [
        f"{int(c.get('container_count') or 0)} X {(c.get('container_type') or '').upper()} CONTAINER"
        f" , SAID TO CONTAIN"
        for c in invoice.containers if int(c.get("container_count") or 0)
    ]
    goods = []
    for item in invoice.items:
        goods.append((item.product_name or "").upper())
        if item.dimension_mm:
            goods.append(item.dimension_mm.upper())

    description = said_to_contain + goods + [
        f"INVOICE NO : {invoice.export_invoice_number} DATE : {_dmy(invoice.invoice_date)}",
    ]
    if invoice.shipping_bill_no:
        description.append(
            f"SHIPPING BILL NO : {invoice.shipping_bill_no} DATE : {_dmy(invoice.shipping_bill_date)}")
    description += [
        f"TOTAL GROSS WEIGHT : {_num(gross)} KGS",
        f"TOTAL NET WEIGHT : {_num(net)} KGS",
    ]

    return {
        "shipper": [company.company_name.upper()] + _block(company.address) if company else [],
        "consignee": [(invoice.consignee_name or "").upper()] + _block(invoice.consignee_address),
        "notify": ([(invoice.notify_name or "").upper()] + _block(invoice.notify_address)
                   if (invoice.notify_name or invoice.notify_address) else ["-"]),
        "booking_no": invoice.booking_no or "-",
        "vessel_voyage_no": (invoice.vessel_voyage_no or "-").upper(),
        "place_of_receipt": (invoice.place_of_receipt or "-").upper(),
        "port_of_loading": (invoice.port_of_loading or "-").upper(),
        "port_of_discharge": (invoice.port_of_discharge or "-").upper(),
        "place_of_delivery": (invoice.final_destination or "-").upper(),
        "marks": [
            f"TOTAL {_num(totals['pallets'], 0)} PACKING",
            f"TOTAL {_num(totals['quantity_boxes'], 0)} QTY",
        ],
        "description": description,
        "gross_weight_label": f"{_num(gross)} KGS",
        "rows": rows,
        "totals": totals,
    }


def _load(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    return invoice, company, packing_list


@bl_drafts_bp.route("/")
@login_required
def list_bl_drafts():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("bl_drafts/list.html", invoices=invoices)


@bl_drafts_bp.route("/<int:export_invoice_id>")
@login_required
def view_bl_draft(export_invoice_id):
    invoice, company, packing_list = _load(export_invoice_id)
    return render_template(
        "bl_drafts/print.html", invoice=invoice, company=company,
        draft=_draft(invoice, company, packing_list), columns=_CONTAINER_COLUMNS,
    )


@bl_drafts_bp.route("/<int:export_invoice_id>/docx")
@login_required
def download_bl_draft_docx(export_invoice_id):
    """The same draft as a Word file. Built from the same `_draft` dict the
    sheet renders, so the two cannot disagree."""
    invoice, company, packing_list = _load(export_invoice_id)
    draft = _draft(invoice, company, packing_list)
    data = _build_docx(invoice, draft)
    return send_file(
        io.BytesIO(data), mimetype=_DOCX_MIME, as_attachment=True,
        download_name=f"BL-DRAFT-{invoice.export_invoice_number}.docx",
    )


def _field(label: str, value) -> list:
    """A label stacked on the value it names, as one cell's lines - no ruled
    line between them, matching the reference's plain header fields."""
    lines = value if isinstance(value, list) else [value]
    return [label, ""] + list(lines)


def _build_docx(invoice, draft) -> bytes:
    """The Word rendering of the sheet: the same two tables, same order."""
    doc = DocxDocument()
    doc.heading("BL DRAFT")

    label = {"bold": True, "size": 16}
    # Header box. Four columns so the three-way Port of Loading / Port of
    # Discharge / Place of Delivery row lines up with the two-way rows above.
    header = [
        # The booking number sits in one box spanning the shipper/consignee
        # rows, its label stacked above the number.
        [Cell(_field("SHIPPER (MAX 5 LINE)", draft["shipper"]), colspan=2),
         Cell(["BOOKING NO", "", draft["booking_no"]], colspan=2, rowspan=2,
              align="center", bold=True, size=16)],
        [Cell(_field("Consignee", draft["consignee"]), colspan=2)],
        [Cell(_field("Notify party", draft["notify"]), colspan=2),
         Cell(_field("Notify party (MAX 5 LINE)", [""]), colspan=2)],
        [Cell(_field("VESSEL AND VOYAGE NO:", draft["vessel_voyage_no"]), colspan=2),
         Cell(_field("PLACE OF RECEIPT:", draft["place_of_receipt"]), colspan=2)],
        [Cell(_field("PORT OF LOADING:", draft["port_of_loading"])),
         Cell(_field("PORT OF DISCHARGE", draft["port_of_discharge"])),
         Cell(_field("PLACE OF DELIVERY:", draft["place_of_delivery"]), colspan=2)],
        [Cell("CONTAINER NOS. MARKS & NUMBERS", **label),
         Cell(["NUMBER AND KIND OF PACKAGES", "DESCRIPTION OF GOODS"], colspan=2, **label),
         Cell("GROSS WEIGHT", align="center", **label)],
        [Cell(draft["marks"]), Cell(draft["description"], colspan=2),
         Cell(draft["gross_weight_label"], align="center")],
    ]
    doc.table(header, widths=[26, 26, 24, 24])

    # Container table.
    body = [[Cell(c, bold=True, align="center", shade="D9E2D0") for c in _CONTAINER_COLUMNS]]
    for row in draft["rows"]:
        body.append([
            Cell(row["container_no"] or "", align="center"),
            Cell(row["seal_no"] or "", align="center"),
            Cell(_num(row["pallets"], 0), align="center"),
            Cell(_num(row["quantity_boxes"], 0), align="center"),
            Cell(_num(row["quantity_value"]), align="center"),
            Cell(_num(row["gross_weight_kg"]), align="center"),
            Cell(_num(row["net_weight_kg"]), align="center"),
        ])
    totals = draft["totals"]
    body.append([
        Cell("", colspan=2),
        Cell(_num(totals["pallets"], 0), bold=True, align="center"),
        Cell(_num(totals["quantity_boxes"], 0), bold=True, align="center"),
        Cell(_num(totals["quantity_value"]), bold=True, align="center"),
        Cell(_num(totals["gross_weight_kg"]), bold=True, align="center"),
        Cell(_num(totals["net_weight_kg"]), bold=True, align="center"),
    ])
    doc.table(body, widths=[16, 16, 11, 11, 14, 16, 16])
    return doc.render()

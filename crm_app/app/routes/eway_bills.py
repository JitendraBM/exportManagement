"""
app/routes/eway_bills.py
-------------------------
The EWAYBILL MULTI VEHICLE sheet - the per-vehicle breakdown filed against a
single e-way bill when one consignment moves on many trucks.

Entirely derived, with nothing to type: the e-way bill number and date come
off the parent Export Invoice, each row's vehicle and LR number off the
section-11B row for that container, and the goods description, HSN and
quantities off the Export Packing List's split - which is already "N boxes of
goods line X went into container C", exactly the row this sheet wants.

A container carrying two products therefore prints two rows against the same
vehicle, which is what the e-way bill portal expects.
"""

from flask import Blueprint, render_template, current_app, g, abort

from app.exceptions import NotFoundError
from app.utils import login_required

eway_bills_bp = Blueprint("eway_bills", __name__, url_prefix="/eway-bills")

_COLUMNS = ["VEHICLE NUMBER", "LR NO", "Description Of Goods", "HSNC", "Packing", "Qty", "Alt. Qty"]


def _dmy(value) -> str:
    """dd-mm-yyyy, the date style the other export sheets print."""
    parts = (str(value or "")[:10]).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else ""


def _rows(invoice, packing_list) -> list:
    """One row per (container x goods line) allocation, in packing list order.

    The packing list item already carries the goods description, HSN and the
    quantities that went into its container; the 11B row for that container
    supplies the road leg (vehicle, LR).
    """
    details_by_sr = {(d.get("sr_no") or i + 1): d
                     for i, d in enumerate(invoice.container_details)}

    rows = []
    for item in (packing_list.items if packing_list else []):
        detail = details_by_sr.get(item.container_sr_no, {})
        rows.append({
            "vehicle_no": detail.get("vehicle_no") or "",
            "lr_no": detail.get("lr_no") or "",
            "description": item.product_name or "",
            "hsn_code": item.hsn_code or "",
            "pallets": item.pallets or 0,
            "quantity_boxes": item.quantity_boxes or 0,
            "quantity_value": item.quantity_value or 0,
        })
    return rows


def _totals(rows) -> dict:
    return {
        "pallets": sum(r["pallets"] for r in rows),
        "quantity_boxes": sum(r["quantity_boxes"] for r in rows),
        "quantity_value": sum(r["quantity_value"] for r in rows),
    }


@eway_bills_bp.route("/")
@login_required
def list_eway_bills():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("eway_bills/list.html", invoices=invoices)


@eway_bills_bp.route("/<int:export_invoice_id>")
@login_required
def view_eway_bill(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    rows = _rows(invoice, packing_list)
    return render_template(
        "eway_bills/print.html", invoice=invoice, rows=rows, totals=_totals(rows),
        columns=_COLUMNS, eway_bill_date=_dmy(invoice.eway_bill_date),
    )

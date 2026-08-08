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

_COLUMNS = ["VEHICLE NUMBER", "LR NO", "Packing", "Qty", "Alt. Qty"]


def _dmy(value) -> str:
    """dd-mm-yyyy, the date style the other export sheets print."""
    parts = (str(value or "")[:10]).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else ""


def _rows(invoice, packing_list) -> list:
    """One row per container, summing every goods line allocated to it.

    The 11B row for that container supplies the road leg (vehicle, LR); the
    packing list items allocated to the same container_sr_no are summed into
    a single packing/qty/alt-qty line, since the e-way bill sheet is filed
    per vehicle, not per goods line.
    """
    details_by_sr = {(d.get("sr_no") or i + 1): d
                     for i, d in enumerate(invoice.container_details)}

    totals_by_sr = {}
    order = []
    for item in (packing_list.items if packing_list else []):
        sr_no = item.container_sr_no
        if sr_no not in totals_by_sr:
            totals_by_sr[sr_no] = {"pallets": 0, "quantity_boxes": 0, "quantity_value": 0}
            order.append(sr_no)
        agg = totals_by_sr[sr_no]
        agg["pallets"] += item.pallets or 0
        agg["quantity_boxes"] += item.quantity_boxes or 0
        agg["quantity_value"] += item.quantity_value or 0

    rows = []
    for sr_no in order:
        detail = details_by_sr.get(sr_no, {})
        agg = totals_by_sr[sr_no]
        rows.append({
            "vehicle_no": detail.get("vehicle_no") or "",
            "lr_no": detail.get("lr_no") or "",
            "pallets": agg["pallets"],
            "quantity_boxes": agg["quantity_boxes"],
            "quantity_value": agg["quantity_value"],
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

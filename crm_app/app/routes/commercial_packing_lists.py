"""
app/routes/commercial_packing_lists.py
---------------------------------------
The COMMERCIAL INVOICE PACKING LIST - the packing list that travels with the
commercial invoice.

It pairs that invoice's header (consigner, consignee, notify, routing,
containers, bank) with the Export Packing List's container split: a row per
container and goods line carrying the container/seal/RFID numbers, the goods,
the quantities and that container's own net and gross weight.

All of that is derived, so the only two cells it asks for are the bill of
lading number and date, typed on a small edit form that shows the export
invoice's own number and date for reference.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.repositories import ExportInvoiceRepository
from app.utils import login_required

commercial_packing_lists_bp = Blueprint("commercial_packing_lists", __name__,
                                        url_prefix="/commercial-packing-lists")

_EDITABLE = ExportInvoiceRepository.PACKING_LIST_FIELDS


def _rows(invoice, packing_list) -> list:
    """A row per (container x goods line) allocation, in packing list order.

    The packing list item already carries the container identity it was
    snapshotted with, the goods and every quantity - this sheet prints them
    as they are.
    """
    return [
        {
            "container_no": item.container_no or "",
            "seal_no": item.seal_no or "",
            "rfid_seal_no": item.rfid_seal_no or "",
            "description": item.product_name or "",
            "hsn_code": item.hsn_code or "",
            "pallets": item.pallets or 0,
            "quantity_boxes": item.quantity_boxes or 0,
            "quantity_value": item.quantity_value or 0,
            "net_weight_kg": item.net_weight_kg or 0,
            "gross_weight_kg": item.gross_weight_kg or 0,
        }
        for item in (packing_list.items if packing_list else [])
    ]


def _totals(rows) -> dict:
    keys = ("pallets", "quantity_boxes", "quantity_value", "net_weight_kg", "gross_weight_kg")
    return {key: sum(row[key] for row in rows) for key in keys}


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


@commercial_packing_lists_bp.route("/")
@login_required
def list_commercial_packing_lists():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("commercial_packing_lists/list.html", invoices=invoices)


@commercial_packing_lists_bp.route("/<int:export_invoice_id>")
@login_required
def view_commercial_packing_list(export_invoice_id):
    invoice, company, packing_list = _load(export_invoice_id)
    rows = _rows(invoice, packing_list)
    return render_template(
        "commercial_packing_lists/print.html", invoice=invoice, company=company,
        rows=rows, totals=_totals(rows),
    )


@commercial_packing_lists_bp.route("/<int:export_invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_commercial_packing_list(export_invoice_id):
    """Everything on this sheet except the bill of lading number and date is
    derived, so those two are the only inputs - the export invoice's own
    number and date are shown beside them for reference, read-only."""
    invoice, company, _ = _load(export_invoice_id)

    if request.method == "POST":
        fields = {name: request.form.get(name, "") for name in _EDITABLE}
        try:
            current_app.container.export_invoice_service.update_packing_list_details(
                g.user, export_invoice_id, fields)
        except (ValidationError, PermissionDeniedError) as exc:
            flash(str(exc), "error")
            return render_template("commercial_packing_lists/form.html",
                                   invoice=invoice, data=fields), 400
        flash("Bill of lading details saved.", "success")
        return redirect(url_for("commercial_packing_lists.view_commercial_packing_list",
                                export_invoice_id=export_invoice_id))

    data = {name: getattr(invoice, name) or "" for name in _EDITABLE}
    return render_template("commercial_packing_lists/form.html", invoice=invoice, data=data)

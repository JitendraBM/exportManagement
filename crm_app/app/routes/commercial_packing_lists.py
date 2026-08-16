"""
app/routes/commercial_packing_lists.py
---------------------------------------
The COMMERCIAL INVOICE PACKING LIST - the packing list that travels with the
commercial invoice.

It pairs that invoice's header (consigner, consignee, notify, routing,
containers, bank) with the Export Packing List's container split: one block
of rows per physical container, its Container No / Line Seal No printed once
(rowspan-ed) alongside each goods line's own description, HSN and
quantities/weights.

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


def _row(item) -> dict:
    return {
        "description": item.product_name or "",
        "hsn_code": item.hsn_code or "",
        "pallets": item.pallets or 0,
        "quantity_boxes": item.quantity_boxes or 0,
        "quantity_value": item.quantity_value or 0,
        "net_weight_kg": item.net_weight_kg or 0,
        "gross_weight_kg": item.gross_weight_kg or 0,
    }


def _containers(packing_list) -> list:
    """One entry per physical container, each holding the flat run of goods
    rows printed beside its (rowspan-ed) Container No / Line Seal No cells -
    same grouping as the Export Packing List's own printed_containers, minus
    the RFID Seal No column this sheet doesn't print."""
    return [
        {
            "container_no": c["container_no"] or "",
            "seal_no": c["seal_no"] or "",
            "rowspan": c["rowspan"],
            "rows": [_row(r["item"]) for r in c["rows"]],
        }
        for c in (packing_list.printed_containers if packing_list else [])
    ]


def _totals(packing_list) -> dict:
    keys = ("pallets", "quantity_boxes", "quantity_value", "net_weight_kg", "gross_weight_kg")
    rows = [_row(item) for item in (packing_list.items if packing_list else [])]
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
    return render_template(
        "commercial_packing_lists/print.html", invoice=invoice, company=company,
        containers=_containers(packing_list), totals=_totals(packing_list),
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
                g.user, export_invoice_id, fields,
                pdf_file=request.files.get("bill_of_lading_pdf"),
                remove_pdf=bool(request.form.get("remove_bill_of_lading_pdf")),
            )
        except (ValidationError, PermissionDeniedError) as exc:
            flash(str(exc), "error")
            return render_template("commercial_packing_lists/form.html",
                                   invoice=invoice, data=fields), 400
        flash("Bill of lading details saved.", "success")
        return redirect(url_for("commercial_packing_lists.view_commercial_packing_list",
                                export_invoice_id=export_invoice_id))

    data = {name: getattr(invoice, name) or "" for name in _EDITABLE}
    return render_template("commercial_packing_lists/form.html", invoice=invoice, data=data)

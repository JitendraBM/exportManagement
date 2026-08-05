"""
app/routes/export_annexures.py
-------------------------------
The ANNEXURE-C: "Examination Report For Self Sealed Container For Export",
the customs form that accompanies an Export Invoice's self-sealed shipment.

Like the Export Packing List, this is a pure read-only report - it has no
identity of its own (not even a generated number). Every field it prints
already lives on the parent Export Invoice or on that invoice's Export
Packing List (container_rows), so there is nothing to create, edit, or
delete here: just a list (of export invoices, since there's no annexure
table of its own to list) and a single detail view.
"""

from flask import Blueprint, render_template, current_app, g, abort

from app.exceptions import NotFoundError
from app.utils import login_required

export_annexures_bp = Blueprint("export_annexures", __name__, url_prefix="/export-annexures")


@export_annexures_bp.route("/")
@login_required
def list_annexures():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("export_annexures/list.html", invoices=invoices)


@export_annexures_bp.route("/<int:export_invoice_id>")
@login_required
def view_annexure(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    packing_list = container.export_packing_list_service.get_for_invoice(export_invoice_id, g.user.company_id)
    return render_template(
        "export_annexures/print.html", invoice=invoice, company=company, packing_list=packing_list,
    )

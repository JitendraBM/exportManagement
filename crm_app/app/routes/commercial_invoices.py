"""
app/routes/commercial_invoices.py
----------------------------------
The COMMERCIAL INVOICE (the copy filed for the BRC - Bank Realisation
Certificate).

Another pure read-only attachment: it carries the parent Export Invoice's own
number and date, its header and money come from that invoice, and the weight
totals from its Export Packing List. There is nothing to create, edit or
delete - not even an edit form, unlike the tax invoice.

It is the export invoice restated for the buyer's bank: the same goods at the
same prices in the invoice's own currency, without the customs apparatus
(no Export Under block, no purchase details, no LUT/IGST panel, no
registration numbers beyond the contact details).
"""

from flask import Blueprint, render_template, current_app, g, abort

from app.exceptions import NotFoundError
from app.utils import login_required

commercial_invoices_bp = Blueprint("commercial_invoices", __name__,
                                   url_prefix="/commercial-invoices")


@commercial_invoices_bp.route("/")
@login_required
def list_commercial_invoices():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("commercial_invoices/list.html", invoices=invoices)


@commercial_invoices_bp.route("/<int:export_invoice_id>")
@login_required
def view_commercial_invoice(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    # Supplies the net/gross weight totals when a split has been built, the
    # same preference the export invoice's own sheet applies.
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    return render_template(
        "commercial_invoices/print.html", invoice=invoice, company=company,
        packing_list=packing_list,
    )

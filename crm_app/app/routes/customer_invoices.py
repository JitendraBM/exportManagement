"""
app/routes/customer_invoices.py
--------------------------------
The COMMERCIAL INVOICE (customer's copy).

The same document as the BRC commercial invoice, priced the other way round:
the Rate column quotes the FOB rate and each line's Total Amount follows from
it, so the goods column adds up to the FOB value. The totals block then runs
UPWARDS - the charges and the discount are added back on to reach the CIF
total - rather than downwards from CIF as every other sheet does.

Read-only, like the BRC copy: it carries the parent Export Invoice's own
number and date, its header and money come from that invoice, and the weight
totals from its Export Packing List. Nothing here is typed or editable.

The repricing itself lives on ExportInvoice.fob_priced_lines, next to the
money ladder it inverts.
"""

from flask import Blueprint, render_template, current_app, g, abort

from app.exceptions import NotFoundError
from app.utils import login_required

customer_invoices_bp = Blueprint("customer_invoices", __name__,
                                 url_prefix="/customer-invoices")


@customer_invoices_bp.route("/")
@login_required
def list_customer_invoices():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("customer_invoices/list.html", invoices=invoices)


@customer_invoices_bp.route("/<int:export_invoice_id>")
@login_required
def view_customer_invoice(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    return render_template(
        "customer_invoices/print.html", invoice=invoice, company=company,
        packing_list=packing_list, lines=invoice.fob_priced_lines(),
    )

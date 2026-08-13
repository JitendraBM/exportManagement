"""
app/routes/customer_invoices.py
--------------------------------
The COMMERCIAL INVOICE (customer's copy). Matches a real reference printout
(EXP/001/26-27) field for field - see the sheet template.

The Rate column is each line's typed FOB price, straight off item.price_usd.
The totals block runs upwards from that goods total (FOB Value), adding the
charges and taking off the discount to close on Total CIF/CFR Invoice Value.

Read-only, like the BRC copy: it carries the parent Export Invoice's own
number and date, its header and money come from that invoice, and the weight
totals from its Export Packing List. Nothing here is typed or editable.
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
        packing_list=packing_list,
    )

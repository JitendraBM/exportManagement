"""
app/routes/vgm_attachments.py
------------------------------
The VGM ATTACHMENT - the Verified Gross Mass declaration handed to the
shipping line, one row per physical container.

Almost every cell is already known: the booking number and each container's
number, size, max permitted weight and tare weight come off the parent Export
Invoice (its section-11B rows and its Container Details list), the cargo
weight off that container's Export Packing List total, and Total VGM Weight
is simply tare + cargo.

That leaves two facts nobody else in the app has - the weighbridge that did
the weighing, and its slip number - so unlike the other export attachments
this one is an editable table: each row shows its derived figures and takes
those two inputs beside them, saved onto the 11B row they belong to.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.utils import login_required
from app.vgm import container_rows

vgm_attachments_bp = Blueprint("vgm_attachments", __name__, url_prefix="/vgm-attachments")


def _load(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    return invoice, packing_list


@vgm_attachments_bp.route("/")
@login_required
def list_vgm_attachments():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("vgm_attachments/list.html", invoices=invoices)


@vgm_attachments_bp.route("/<int:export_invoice_id>", methods=["GET", "POST"])
@login_required
def view_vgm_attachment(export_invoice_id):
    """The sheet and its form are the same page: the derived figures are shown
    read-only and the two typed cells are inputs on the same row."""
    invoice, packing_list = _load(export_invoice_id)

    if request.method == "POST":
        submitted = [
            {"sr_no": sr_no,
             "weighbridge_name": name,
             "weighing_slip_no": slip}
            for sr_no, name, slip in zip(
                request.form.getlist("vgm_sr_no[]"),
                request.form.getlist("vgm_weighbridge_name[]"),
                request.form.getlist("vgm_weighing_slip_no[]"),
            )
        ]
        try:
            current_app.container.export_invoice_service.update_vgm_details(
                g.user, export_invoice_id, submitted)
        except (ValidationError, PermissionDeniedError) as exc:
            flash(str(exc), "error")
        else:
            flash("VGM details saved.", "success")
        return redirect(url_for("vgm_attachments.view_vgm_attachment",
                                export_invoice_id=export_invoice_id))

    company = current_app.container.company_service.get(g.user.company_id)
    return render_template(
        "vgm_attachments/print.html", invoice=invoice, company=company,
        rows=container_rows(invoice, packing_list),
    )

"""
app/routes/vgm_declarations.py
-------------------------------
The VGM declaration - "INFORMATION ABOUT VERIFIED GROSS MASS OF CONTAINER",
the shipper-level form that goes to the line alongside the per-container VGM
attachment.

Where the attachment is a row per container, this is a single 16-row list of
particulars. Most of them are already known: booking number and shipper
details from the parent Export Invoice and Our Company, and six per-container
cells from the very same rows the attachment prints.

Those six are the interesting part. The declaration has ONE cell per field,
not one row per container, so only a single-container shipment can be quoted
inline; with more the cell simply reads "VGM ATTACHMENT". So that the reader
does not then have to go and fetch a second document, the attachment table is
printed underneath this sheet - up to ten containers, past which it is long
enough to stand on its own. See `app/vgm.py:declaration_cell` and
`app/vgm.py:append_attachment`.

The remaining five cells are the shaded ones on the reference - facts nobody
else in the app holds - and are typed on this document's own small edit form.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.repositories import ExportInvoiceRepository
from app.utils import login_required
from app.vgm import append_attachment, container_rows, declaration_cell

vgm_declarations_bp = Blueprint("vgm_declarations", __name__, url_prefix="/vgm-declarations")

_EDITABLE = ExportInvoiceRepository.VGM_DECLARATION_FIELDS


def _dmy(value) -> str:
    parts = (str(value or "")[:10]).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else (value or "-")


def _particulars(invoice, company, rows) -> list:
    """The sheet's 16 rows, in the reference's order. `manual` marks the ones
    typed on this document's edit form (the shaded rows of the reference), so
    the sheet can show at a glance which are entered rather than derived."""
    phone = next((c for c in (company.contact_details or []) if c.get("type") == "phone"), None) if company else None

    def cell(key):
        return declaration_cell(rows, key)

    return [
        ("Booking No", invoice.booking_no or "-", False),
        ("Name of the Shipper", (company.company_name if company else "") or "-", False),
        ("Shipper Registration / License No (IEC No / CIN No)",
         (company.iec if company and company.iec else "-"), False),
        ("Name & Designation of the Official of the Shipper authorized to Sign document",
         invoice.vgm_signatory_printed(company), True),
        ("24 x 7 Contact details of Authorized official of Shipper",
         invoice.vgm_contact_24x7 or (phone.get("value") if phone else None) or "-", True),
        ("Container No", cell("container_no"), False),
        ("Container Size (TEU/FUE/Others)", cell("container_size"), False),
        ("Maximum permissible weight of container as per the CSC Plate",
         cell("max_permitted_weight"), False),
        ("Weighbridge address", cell("weighbridge_name"), False),
        ("Weighing Method (Method-1 /Method-2)", invoice.vgm_weighing_method_printed, True),
        ("Verified Gross Mass of the Container", cell("total_vgm_weight"), False),
        ("Unit Of Measure (KG / MT / LBS)", "KGS", False),
        # Not one of the reference's shaded cells, so it is derived rather than
        # typed - the invoice's own date is the only date this document knows.
        ("Date & Time of Weighing", _dmy(invoice.invoice_date), False),
        ("Weighing Slip No.", cell("weighing_slip_no"), False),
        ("Type (Normal / Reefer / Hazardous / Others)", invoice.vgm_cargo_type_printed, True),
        ("If Hazardous, UN No, IMDG Class", invoice.vgm_hazardous_details_printed, True),
    ]


def _load(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    packing_list = container.export_packing_list_service.get_for_invoice(
        export_invoice_id, g.user.company_id)
    return invoice, company, container_rows(invoice, packing_list)


@vgm_declarations_bp.route("/")
@login_required
def list_vgm_declarations():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("vgm_declarations/list.html", invoices=invoices)


@vgm_declarations_bp.route("/<int:export_invoice_id>")
@login_required
def view_vgm_declaration(export_invoice_id):
    invoice, company, rows = _load(export_invoice_id)
    return render_template(
        "vgm_declarations/print.html", invoice=invoice, company=company,
        particulars=_particulars(invoice, company, rows), container_count=len(rows),
        rows=rows, show_attachment=append_attachment(rows),
    )


@vgm_declarations_bp.route("/<int:export_invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_vgm_declaration(export_invoice_id):
    """Asks only for the reference's shaded cells - everything else on the
    sheet is derived from the invoice, its containers or Our Company."""
    invoice, company, _ = _load(export_invoice_id)

    if request.method == "POST":
        fields = {name: request.form.get(name, "") for name in _EDITABLE}
        try:
            current_app.container.export_invoice_service.update_vgm_declaration(
                g.user, export_invoice_id, fields)
        except (ValidationError, PermissionDeniedError) as exc:
            flash(str(exc), "error")
            return render_template("vgm_declarations/form.html", invoice=invoice,
                                   company=company, data=fields), 400
        flash("VGM declaration saved.", "success")
        return redirect(url_for("vgm_declarations.view_vgm_declaration",
                                export_invoice_id=export_invoice_id))

    data = {name: getattr(invoice, name) or "" for name in _EDITABLE}
    return render_template("vgm_declarations/form.html", invoice=invoice, company=company, data=data)

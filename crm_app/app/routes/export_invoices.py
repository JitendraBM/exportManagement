"""
app/routes/export_invoices.py
-----------------------------
Export Invoice generation: the customer/customs-facing document at the buyer
end of the pipeline (Quotation -> Proforma Invoice -> Purchase Order ->
Purchase Invoice). Unlike the other document types it references MANY
proforma invoices at once (a single buyer order fulfilled across several
PIs/suppliers), so it is normally started from one or more PIs via
`?proforma_invoice_ids=` (a comma-separated or repeated query arg) which
prefills the goods lines and imports EPCG / export-under / supplier-exemption
details by walking each PI's purchase orders to their purchase invoices. The
invoice number is auto-generated EXPINV{YYYYMMDD}{seq} and never editable.
Mirrors app/routes/proforma_invoices.py, plus an optional Shipping Bill PDF
upload (same idiom as purchase_invoices.py).
"""

from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError
from app.utils import login_required, admin_required, verify_delete_password

export_invoices_bp = Blueprint("export_invoices", __name__, url_prefix="/export-invoices")

# The container types a user can pick from for the Container Details list.
CONTAINER_TYPES = ["20FT FCL", "40FT FCL", "20FT LCL", "40FT LCL", "40FT HC"]

_HEADER_FIELDS = [
    "invoice_date", "lead_id", "consignee_name", "consignee_address", "notify_name", "notify_address",
    "country_of_origin", "country_of_destination", "place_of_receipt", "pre_carriage_by",
    "port_of_loading", "port_of_discharge", "final_destination", "nature_of_contract", "payment_terms",
    "buyer_order_no", "buyer_order_date",
    "export_under", "epcg_number", "epcg_date", "loading_type", "tax_mode", "exchange_rate",
    "sea_freight", "insurance", "certification", "other_charges", "discount_amount", "fob_value", "cnf_value",
    "bank_name", "bank_account_number", "bank_ifsc_code", "bank_swift_code", "bank_branch", "bank_address",
    "authorised_person_name", "authorised_person_designation", "self_sealing_declaration",
    "examination_date", "location_code_08b", "booking_no", "issuing_authority", "issuing_authority_address",
    "permission_no", "permission_date", "permission_expiry", "manufacturer_name", "manufacturer_address",
    "remarks",
]


def _extract_fields(form) -> dict:
    """The scalar header fields plus the parsed child lists, in the shape
    ExportInvoiceService.create/update expect (all lists live inside the
    `fields` dict alongside the scalars)."""
    fields = {key: form.get(key, "") for key in _HEADER_FIELDS}
    fields["proforma_invoice_ids"] = form.getlist("proforma_invoice_ids[]")
    fields["containers"] = _extract_containers(form)
    fields["container_details_list"] = _extract_container_details(form)
    fields["purchase_details"] = _extract_purchase_details(form)
    return fields


def _extract_items(form) -> list:
    product_ids = form.getlist("item_product_id[]")
    product_names = form.getlist("item_product_name[]")
    hsn_codes = form.getlist("item_hsn_code[]")
    surfaces = form.getlist("item_surface[]")
    pallets = form.getlist("item_pallets[]")
    boxes = form.getlist("item_quantity_boxes[]")
    values = form.getlist("item_quantity_value[]")
    units = form.getlist("item_unit[]")
    prices = form.getlist("item_price_usd[]")
    items = []
    for i in range(len(product_names)):
        items.append({
            "product_id": product_ids[i] if i < len(product_ids) else "",
            "product_name": product_names[i],
            "hsn_code": hsn_codes[i] if i < len(hsn_codes) else "",
            "surface": surfaces[i] if i < len(surfaces) else "",
            "pallets": pallets[i] if i < len(pallets) else "",
            "quantity_boxes": boxes[i] if i < len(boxes) else "",
            "quantity_value": values[i] if i < len(values) else "",
            "unit": units[i] if i < len(units) else "SQM",
            "price_usd": prices[i] if i < len(prices) else "",
        })
    return items


def _extract_containers(form) -> list:
    types = form.getlist("container_type[]")
    counts = form.getlist("container_count[]")
    rows = []
    for i in range(max(len(types), len(counts))):
        rows.append({
            "container_type": types[i] if i < len(types) else "",
            "container_count": counts[i] if i < len(counts) else "",
        })
    return rows


def _extract_container_details(form) -> list:
    nos = form.getlist("cd_container_no[]")
    line_seals = form.getlist("cd_line_seal_no[]")
    rfids = form.getlist("cd_rfid_seal_no[]")
    vehicles = form.getlist("cd_vehicle_no[]")
    n = max(len(nos), len(line_seals), len(rfids), len(vehicles))
    rows = []
    for i in range(n):
        rows.append({
            "container_no": nos[i] if i < len(nos) else "",
            "line_seal_no": line_seals[i] if i < len(line_seals) else "",
            "rfid_seal_no": rfids[i] if i < len(rfids) else "",
            "vehicle_no": vehicles[i] if i < len(vehicles) else "",
        })
    return rows


def _extract_purchase_details(form) -> list:
    gstins = form.getlist("pd_supplier_gstin[]")
    inv_nos = form.getlist("pd_supplier_invoice_no[]")
    rows = []
    for i in range(max(len(gstins), len(inv_nos))):
        rows.append({
            "supplier_gstin": gstins[i] if i < len(gstins) else "",
            "supplier_invoice_no": inv_nos[i] if i < len(inv_nos) else "",
        })
    return rows


def _form_context():
    container = current_app.container
    leads = container.lead_service.list_for_dashboard(g.user)
    proforma_invoices = container.proforma_invoice_service.list_all(g.user.company_id)
    company = container.company_service.get(g.user.company_id)
    permits = container.permit_service.list_all(g.user.company_id)
    return leads, proforma_invoices, company, permits


def _render_form(invoice, form_data, form_items, containers=None,
                 container_details=None, purchase_details=None, status_code=200):
    leads, proforma_invoices, company, permits = _form_context()
    html = render_template(
        "export_invoices/form.html", invoice=invoice, leads=leads, proforma_invoices=proforma_invoices,
        company=company, permits=permits, container_types=CONTAINER_TYPES, form_data=form_data, form_items=form_items,
        form_containers=containers,
        form_container_details=container_details, form_purchase_details=purchase_details,
        today=date.today().isoformat(),
    )
    return (html, status_code) if status_code != 200 else html


@export_invoices_bp.route("/")
@login_required
def list_export_invoices():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("export_invoices/list.html", invoices=invoices)


@export_invoices_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_export_invoice():
    container = current_app.container
    if request.method == "POST":
        try:
            invoice = container.export_invoice_service.create(
                current_user=g.user, fields=_extract_fields(request.form),
                raw_items=_extract_items(request.form), pdf_file=request.files.get("shipping_bill_pdf"),
            )
            flash(f"Export invoice {invoice.export_invoice_number} created.", "success")
            return redirect(url_for("export_invoices.view_export_invoice", export_invoice_id=invoice.id))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            fields = _extract_fields(request.form)
            return _render_form(
                None, request.form, _extract_items(request.form),
                containers=fields["containers"],
                container_details=fields["container_details_list"], purchase_details=fields["purchase_details"],
                status_code=400,
            )

    # GET: optionally prefill from one or more proforma invoices.
    prefill = None
    form_items = None
    containers = container_details = purchase_details = None
    raw_ids = request.args.get("proforma_invoice_ids") or request.args.get("proforma_invoice_id")
    proforma_ids = [p for p in (raw_ids.split(",") if raw_ids else []) if p.strip()]
    if proforma_ids:
        built = container.export_invoice_service.build_prefill_from_proformas(proforma_ids, g.user.company_id)
        prefill = dict(built["fields"])
        prefill["invoice_date"] = date.today().isoformat()
        form_items = built["items"]
        purchase_details = built["purchase_details"]
    return _render_form(None, prefill, form_items, containers=containers,
                        container_details=container_details, purchase_details=purchase_details)


@export_invoices_bp.route("/<int:export_invoice_id>")
@login_required
def view_export_invoice(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    return render_template("export_invoices/print.html", invoice=invoice, company=company)


@export_invoices_bp.route("/<int:export_invoice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_export_invoice(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)

    if request.method == "POST":
        try:
            container.export_invoice_service.update(
                current_user=g.user, invoice_id=export_invoice_id, fields=_extract_fields(request.form),
                raw_items=_extract_items(request.form), pdf_file=request.files.get("shipping_bill_pdf"),
                remove_pdf=bool(request.form.get("remove_shipping_bill_pdf")),
            )
            flash(f"Export invoice {invoice.export_invoice_number} updated.", "success")
            return redirect(url_for("export_invoices.view_export_invoice", export_invoice_id=export_invoice_id))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            fields = _extract_fields(request.form)
            return _render_form(
                invoice, request.form, _extract_items(request.form),
                containers=fields["containers"],
                container_details=fields["container_details_list"], purchase_details=fields["purchase_details"],
                status_code=400,
            )

    return _render_form(
        invoice, None, None,
        containers=invoice.containers,
        container_details=invoice.container_details, purchase_details=invoice.purchase_details,
    )


@export_invoices_bp.route("/<int:export_invoice_id>/delete", methods=["POST"])
@login_required
def delete_export_invoice(export_invoice_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Export invoice not deleted.", "error")
        return redirect(url_for("export_invoices.view_export_invoice", export_invoice_id=export_invoice_id))
    try:
        invoice = current_app.container.export_invoice_service.get(export_invoice_id, g.user.company_id)
        current_app.container.export_invoice_service.delete(g.user, export_invoice_id)
        flash(f"Export invoice {invoice.export_invoice_number} deleted.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("export_invoices.list_export_invoices"))


@export_invoices_bp.route("/<int:export_invoice_id>/versions")
@admin_required
def export_invoice_versions(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    versions = container.document_version_service.list_for_document("export_invoice", export_invoice_id)
    rows = [
        {
            "version_number": v.version_number,
            "created_at": v.created_at,
            "changed_by_name": v.changed_by_name,
            "url": url_for("export_invoices.view_export_invoice", export_invoice_id=export_invoice_id) if i == 0 else
                   url_for("export_invoices.view_export_invoice_version",
                           export_invoice_id=export_invoice_id, version_number=v.version_number),
        }
        for i, v in enumerate(versions)
    ]
    return render_template(
        "document_versions/list.html", document_number=invoice.export_invoice_number, versions=rows,
        back_url=url_for("export_invoices.view_export_invoice", export_invoice_id=export_invoice_id),
    )


@export_invoices_bp.route("/<int:export_invoice_id>/versions/<int:version_number>")
@admin_required
def view_export_invoice_version(export_invoice_id, version_number):
    container = current_app.container
    try:
        container.export_invoice_service.get(export_invoice_id, g.user.company_id)  # tenant-scope check
        historical_invoice, version = container.document_version_service.get_version(
            "export_invoice", export_invoice_id, version_number
        )
    except NotFoundError:
        abort(404)
    company = container.company_service.get(g.user.company_id)
    return render_template(
        "export_invoices/print.html", invoice=historical_invoice, company=company, historical_version=version,
    )

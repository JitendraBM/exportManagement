"""
app/routes/transporters.py
---------------------------
HTTP layer for Transporters - the haulier directory. Unlike Buyer
(app/routes/parties.py) and Supplier (app/routes/suppliers.py), a transporter
never comes from a lead and has no status pipeline or payments/
communications/documents feed, so this is a plain admin-gated CRUD over a
profile plus buyer-shaped contact persons.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort
)

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError
from app.utils import login_required, admin_required, verify_delete_password

transporters_bp = Blueprint("transporters", __name__, url_prefix="/transporters")

_FIELDS = ["name", "address", "gstin_transporter_no", "pan_no", "cin_llp_no", "email"]


def _extract_fields(form) -> dict:
    return {key: form.get(key, "") for key in _FIELDS}


def _extract_contacts(form) -> list:
    """Same parallel-array shape as parties._extract_contacts -
    contact_name[]/contact_phone[]/contact_email[] plus a single index marked
    primary."""
    names = form.getlist("contact_name[]")
    phones = form.getlist("contact_phone[]")
    emails = form.getlist("contact_email[]")
    primary_index = form.get("primary_contact_index", "0")
    contacts = []
    for i, contact_name in enumerate(names):
        contacts.append({
            "name": contact_name,
            "phone": phones[i] if i < len(phones) else "",
            "email": emails[i] if i < len(emails) else "",
            "is_primary": str(i) == primary_index,
        })
    return contacts


def _contact_rows(form, transporter) -> list:
    """What the repeatable contact block should render: whatever was just
    typed when a POST bounced back, the saved rows when editing, and a single
    blank row on a fresh form."""
    if form is not None:
        return _extract_contacts(form)
    if transporter and transporter.contacts:
        return [
            {"name": c.name, "phone": c.phone or "", "email": c.email or "", "is_primary": c.is_primary}
            for c in transporter.contacts
        ]
    return [{"name": "", "phone": "", "email": "", "is_primary": True}]


@transporters_bp.route("/")
@login_required
def list_transporters():
    transporters = current_app.container.transporter_service.list_all(g.user.company_id)
    return render_template("transporters/list.html", transporters=transporters)


@transporters_bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_transporter():
    container = current_app.container
    if request.method == "POST":
        try:
            transporter = container.transporter_service.create(
                g.user, _extract_fields(request.form), _extract_contacts(request.form),
            )
            flash(f"Transporter '{transporter.name}' added.", "success")
            return redirect(url_for("transporters.view_transporter", transporter_id=transporter.id))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            return render_template(
                "transporters/form.html", transporter=None, form_data=request.form,
                contact_rows=_contact_rows(request.form, None),
            ), 400

    return render_template(
        "transporters/form.html", transporter=None, form_data=None,
        contact_rows=_contact_rows(None, None),
    )


@transporters_bp.route("/<int:transporter_id>")
@login_required
def view_transporter(transporter_id):
    try:
        transporter = current_app.container.transporter_service.get(transporter_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    return render_template("transporters/detail.html", transporter=transporter)


@transporters_bp.route("/<int:transporter_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_transporter(transporter_id):
    container = current_app.container
    try:
        transporter = container.transporter_service.get(transporter_id, g.user.company_id)
    except NotFoundError:
        abort(404)

    if request.method == "POST":
        try:
            container.transporter_service.update(
                transporter_id, g.user, _extract_fields(request.form), _extract_contacts(request.form),
            )
            flash("Transporter details updated.", "success")
            return redirect(url_for("transporters.view_transporter", transporter_id=transporter_id))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            return render_template(
                "transporters/form.html", transporter=transporter, form_data=request.form,
                contact_rows=_contact_rows(request.form, transporter),
            ), 400

    return render_template(
        "transporters/form.html", transporter=transporter, form_data=None,
        contact_rows=_contact_rows(None, transporter),
    )


@transporters_bp.route("/<int:transporter_id>/delete", methods=["POST"])
@admin_required
def delete_transporter(transporter_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Transporter not deleted.", "error")
        return redirect(url_for("transporters.view_transporter", transporter_id=transporter_id))
    try:
        transporter = current_app.container.transporter_service.delete(transporter_id, g.user)
        flash(f"Transporter '{transporter.name}' deleted.", "success")
    except PermissionDeniedError as e:
        flash(str(e), "error")
        return redirect(url_for("transporters.view_transporter", transporter_id=transporter_id))
    except NotFoundError:
        abort(404)
    return redirect(url_for("transporters.list_transporters"))

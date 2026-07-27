"""
app/routes/permits.py
-----------------------
"PERMISSIONS" - the permits a company holds, managed as a tab under the
"Our Company" area. Admin-only, like the rest of that area. Each permit is
tied to one supplier, records the issuing authority + place of stuffing, is
either valid until an expiry date OR a one-time permit, and can carry an
uploaded PDF (stored like a purchase invoice's supplier PDF).
"""

from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError
from app.utils import admin_required, verify_delete_password

permits_bp = Blueprint("permits", __name__, url_prefix="/permits")

_FIELDS = [
    "stuffing_place_name", "place_of_stuffing", "permission_number", "date_of_issue",
    "issuing_authority", "issuing_authority_address", "validity_type", "date_of_expiry",
]


def _extract_fields(form) -> dict:
    return {key: form.get(key, "") for key in _FIELDS}


@permits_bp.route("/")
@admin_required
def list_permits():
    permits = current_app.container.permit_service.list_all(g.user.company_id)
    return render_template("permits/list.html", permits=permits)


@permits_bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_permit():
    container = current_app.container
    if request.method == "POST":
        try:
            permit = container.permit_service.create(
                current_user=g.user, fields=_extract_fields(request.form),
                pdf_file=request.files.get("permit_pdf"),
            )
            flash(f"Permit {permit.permission_number} added.", "success")
            return redirect(url_for("permits.list_permits"))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            return render_template(
                "permits/form.html", permit=None,
                form_data=request.form, today=date.today().isoformat(),
            ), 400

    return render_template(
        "permits/form.html", permit=None,
        form_data=None, today=date.today().isoformat(),
    )


@permits_bp.route("/<int:permit_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_permit(permit_id):
    container = current_app.container
    try:
        permit = container.permit_service.get(permit_id, g.user.company_id)
    except NotFoundError:
        abort(404)

    if request.method == "POST":
        try:
            updated = container.permit_service.update(
                current_user=g.user, permit_id=permit_id, fields=_extract_fields(request.form),
                pdf_file=request.files.get("permit_pdf"),
                remove_pdf=bool(request.form.get("remove_permit_pdf")),
            )
            flash(f"Permit {updated.permission_number} updated.", "success")
            return redirect(url_for("permits.list_permits"))
        except (ValidationError, PermissionDeniedError) as e:
            flash(str(e), "error")
            return render_template(
                "permits/form.html", permit=permit,
                form_data=request.form, today=date.today().isoformat(),
            ), 400

    return render_template(
        "permits/form.html", permit=permit,
        form_data=None, today=date.today().isoformat(),
    )


@permits_bp.route("/<int:permit_id>/delete", methods=["POST"])
@admin_required
def delete_permit(permit_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Permit not deleted.", "error")
        return redirect(url_for("permits.list_permits"))
    try:
        permit = current_app.container.permit_service.get(permit_id, g.user.company_id)
        current_app.container.permit_service.delete(g.user, permit_id)
        flash(f"Permit {permit.permission_number} deleted.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("permits.list_permits"))

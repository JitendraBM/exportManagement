"""
app/routes/misc.py
-------------------
"MISCELLANEOUS" - the hand-maintained drop lists behind the app's
dropdowns, managed as a tab under Administration. Admin-only.

The list types are CURRENCY (name of currency + currency symbol), NATURE OF
CONTRACT (a name), which fills the delivery-terms field on every document
whatever that document calls it, and PORT OF LOADING (a port name + that
port's PIN code). Editing happens inline on the single page rather than
through separate form pages, since a list row is only a field or two.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, g, abort

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError
from app.utils import admin_required, verify_delete_password

misc_bp = Blueprint("misc", __name__, url_prefix="/misc")


def _fields(form) -> dict:
    return {
        "name": form.get("name", ""),
        "symbol": form.get("symbol", ""),
        "pin_code": form.get("pin_code", ""),
    }


@misc_bp.route("/")
@admin_required
def index():
    service = current_app.container.misc_list_service
    return render_template(
        "misc/list.html",
        currencies=service.list_currencies(g.user.company_id),
        fallback_currencies=service.currency_options(g.user.company_id),
        nature_of_contracts=service.list_nature_of_contracts(g.user.company_id),
        ports_of_loading=service.list_ports_of_loading(g.user.company_id),
        edit_id=request.args.get("edit", type=int),
        edit_list=request.args.get("list", ""),
    )


@misc_bp.route("/currencies", methods=["POST"])
@admin_required
def add_currency():
    try:
        currency = current_app.container.misc_list_service.create_currency(g.user, _fields(request.form))
        flash(f"Currency {currency.name} added.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    return redirect(url_for("misc.index"))


@misc_bp.route("/currencies/<int:currency_id>/edit", methods=["POST"])
@admin_required
def edit_currency(currency_id):
    try:
        currency = current_app.container.misc_list_service.update_currency(
            g.user, currency_id, _fields(request.form),
        )
        flash(f"Currency {currency.name} updated.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))


@misc_bp.route("/currencies/<int:currency_id>/delete", methods=["POST"])
@admin_required
def delete_currency(currency_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Currency not deleted.", "error")
        return redirect(url_for("misc.index"))
    try:
        currency = current_app.container.misc_list_service.delete_currency(g.user, currency_id)
        flash(f"Currency {currency.name} deleted.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))


@misc_bp.route("/nature-of-contracts", methods=["POST"])
@admin_required
def add_nature_of_contract():
    try:
        entry = current_app.container.misc_list_service.create_nature_of_contract(g.user, _fields(request.form))
        flash(f"Nature of contract {entry.name} added.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    return redirect(url_for("misc.index"))


@misc_bp.route("/nature-of-contracts/<int:entry_id>/edit", methods=["POST"])
@admin_required
def edit_nature_of_contract(entry_id):
    try:
        entry = current_app.container.misc_list_service.update_nature_of_contract(
            g.user, entry_id, _fields(request.form),
        )
        flash(f"Nature of contract {entry.name} updated.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))


@misc_bp.route("/nature-of-contracts/<int:entry_id>/delete", methods=["POST"])
@admin_required
def delete_nature_of_contract(entry_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Nature of contract not deleted.", "error")
        return redirect(url_for("misc.index"))
    try:
        entry = current_app.container.misc_list_service.delete_nature_of_contract(g.user, entry_id)
        flash(f"Nature of contract {entry.name} deleted.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))


@misc_bp.route("/ports-of-loading", methods=["POST"])
@admin_required
def add_port_of_loading():
    try:
        entry = current_app.container.misc_list_service.create_port_of_loading(g.user, _fields(request.form))
        flash(f"Port of loading {entry.name} added.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    return redirect(url_for("misc.index"))


@misc_bp.route("/ports-of-loading/<int:entry_id>/edit", methods=["POST"])
@admin_required
def edit_port_of_loading(entry_id):
    try:
        entry = current_app.container.misc_list_service.update_port_of_loading(
            g.user, entry_id, _fields(request.form),
        )
        flash(f"Port of loading {entry.name} updated.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))


@misc_bp.route("/ports-of-loading/<int:entry_id>/delete", methods=["POST"])
@admin_required
def delete_port_of_loading(entry_id):
    if not verify_delete_password(g.user, request.form):
        flash("Incorrect password. Port of loading not deleted.", "error")
        return redirect(url_for("misc.index"))
    try:
        entry = current_app.container.misc_list_service.delete_port_of_loading(g.user, entry_id)
        flash(f"Port of loading {entry.name} deleted.", "success")
    except (ValidationError, PermissionDeniedError) as e:
        flash(str(e), "error")
    except NotFoundError:
        abort(404)
    return redirect(url_for("misc.index"))

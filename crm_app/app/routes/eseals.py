"""
app/routes/eseals.py
---------------------
The E-SEAL sheet - the self-sealing declaration filed against a shipping
bill, one row per physical container.

Everything on it is already known except when the seal went on: the shipping
bill number and date and the e-way bill number come off the parent Export
Invoice, and the vehicle number, container number and e-seal number off that
container's own section-11B row (the e-seal IS the RFID seal recorded there).

So, like the VGM attachment, this sheet is its own form: each row shows its
derived cells and takes the sealing time and date beside them.

It goes out in two formats. The printable HTML view is the PDF route (print to
PDF, as every other sheet here does), and the same rows are written to an
.xlsx by `app/xlsx.py` for the filing portal. Both come from one `_rows(...)`
call, so the spreadsheet can never drift from the printed sheet.
"""

import io

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   current_app, g, abort, send_file)

from app.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.utils import login_required
from app.xlsx import build_sheet

eseals_bp = Blueprint("eseals", __name__, url_prefix="/e-seals")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The sheet's columns, shared by the printed table and the spreadsheet, paired
# with the row key each one reads and a sensible Excel column width.
_COLUMNS = [
    ("SHIPPING_BILL_NO", "shipping_bill_no", 18),
    ("SHIPPING BILL DATE", "shipping_bill_date", 18),
    ("VEHICLE NUMBER", "vehicle_no", 18),
    ("CONTAINER NUMBER", "container_no", 18),
    ("ESEAL NUMBER", "eseal_no", 18),
    ("SEALING TIME", "sealing_time", 14),
    ("SEALING DATE", "sealing_date_printed", 16),
    ("E-Way Bill No", "eway_bill_no", 18),
]


def _dmy(value) -> str:
    """dd/mm/yyyy - this sheet's own date format, slashes and all, because it
    is filed against forms that ask for exactly that."""
    parts = (str(value or "")[:10]).split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else ""


def _rows(invoice) -> list:
    """One row per physical container, off the invoice's own 11B rows."""
    shipping_bill_date = _dmy(invoice.shipping_bill_date)
    rows = []
    for index, detail in enumerate(invoice.container_details):
        rows.append({
            "sr_no": detail.get("sr_no") or index + 1,
            "shipping_bill_no": invoice.shipping_bill_no or "",
            "shipping_bill_date": shipping_bill_date,
            "vehicle_no": detail.get("vehicle_no") or "",
            "container_no": detail.get("container_no") or "",
            # The e-seal is the RFID seal captured on the 11B row.
            "eseal_no": detail.get("rfid_seal_no") or "",
            "sealing_time": detail.get("sealing_time") or "",
            "sealing_date": detail.get("sealing_date") or "",
            "sealing_date_printed": _dmy(detail.get("sealing_date")),
            "eway_bill_no": invoice.eway_bill_no or "",
        })
    return rows


def _load(export_invoice_id):
    try:
        return current_app.container.export_invoice_service.get(
            export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)


@eseals_bp.route("/")
@login_required
def list_eseals():
    invoices = current_app.container.export_invoice_service.list_all(g.user.company_id)
    return render_template("eseals/list.html", invoices=invoices)


@eseals_bp.route("/<int:export_invoice_id>", methods=["GET", "POST"])
@login_required
def view_eseal(export_invoice_id):
    """The sheet and its form are the same page: derived cells read-only, the
    sealing time/date as inputs on the same row."""
    invoice = _load(export_invoice_id)

    if request.method == "POST":
        submitted = [
            {"sr_no": sr_no, "sealing_time": time, "sealing_date": date}
            for sr_no, time, date in zip(
                request.form.getlist("eseal_sr_no[]"),
                request.form.getlist("eseal_sealing_time[]"),
                request.form.getlist("eseal_sealing_date[]"),
            )
        ]
        try:
            current_app.container.export_invoice_service.update_eseal_details(
                g.user, export_invoice_id, submitted)
        except (ValidationError, PermissionDeniedError) as exc:
            flash(str(exc), "error")
        else:
            flash("Sealing details saved.", "success")
        return redirect(url_for("eseals.view_eseal", export_invoice_id=export_invoice_id))

    return render_template("eseals/print.html", invoice=invoice, rows=_rows(invoice),
                           columns=[label for label, _, _ in _COLUMNS])


@eseals_bp.route("/<int:export_invoice_id>/xlsx")
@login_required
def download_eseal_xlsx(export_invoice_id):
    """The same sheet as an Excel file, for the filing portal. Built from the
    same `_rows` the printed sheet renders, so the two cannot disagree."""
    invoice = _load(export_invoice_id)
    rows = _rows(invoice)
    data = build_sheet(
        "E-Seal",
        [label for label, _, _ in _COLUMNS],
        [[row[key] for _, key, _ in _COLUMNS] for row in rows],
        widths=[width for _, _, width in _COLUMNS],
    )
    return send_file(
        io.BytesIO(data), mimetype=_XLSX_MIME, as_attachment=True,
        download_name=f"E-SEAL-{invoice.export_invoice_number}.xlsx",
    )

"""
app/routes/export_designs_packing_lists.py
-------------------------------------------
The DESIGNS PACKING LIST: the Export Packing List's container split, broken
one level finer into the catalog designs each line's boxes actually are.

Like the Export Packing List and Annexure-C this hangs off an export invoice
rather than being authored on its own - the invoice number/date and the whole
container split are read-only here, taken straight off the parent. The one
thing this page owns is the per-line design breakdown
(export_packing_list_item_designs), saved one line at a time and required to
add up to exactly that line's boxes.

Each line also shows what was actually bought in, by design, for its product
(from the purchase orders' own packing lists) as a guide to what can
legitimately be allocated.
"""

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, g, abort

from app.exceptions import NotFoundError, ValidationError
from app.utils import login_required

export_designs_packing_lists_bp = Blueprint(
    "export_designs_packing_lists", __name__, url_prefix="/export-designs-packing-lists"
)

# Same tolerance the service balances boxes with - a line counts as filled
# when its designs add up to its own box count.
_BOX_TOLERANCE = 0.001


def _is_filled(item) -> bool:
    allocated = sum(d.quantity_boxes or 0 for d in item.designs)
    return bool(item.designs) and abs(allocated - (item.quantity_boxes or 0)) <= _BOX_TOLERANCE


def _next_anchor(packing_list) -> str:
    """The line to land on after a save: the first one still unfilled, so
    saving container 2 carries you straight to container 3 instead of back
    to the top of the page. Empty once every container is done, which lands
    at the top where the Create button is."""
    for item in sorted(packing_list.items, key=lambda i: (i.container_sr_no, i.sr_no)):
        if not _is_filled(item):
            return f"#line-{item.invoice_item_sr_no}-{item.container_sr_no}"
    return ""


@export_designs_packing_lists_bp.route("/")
@login_required
def list_designs_packing_lists():
    """Every export invoice, with its designs packing list's number against
    it once one has been created - so the list doubles as "which invoices
    still need their designs split"."""
    container = current_app.container
    invoices = container.export_invoice_service.list_all(g.user.company_id)
    docs = {d.export_invoice_id: d
            for d in container.export_packing_list_service.list_designs_documents(g.user.company_id)}
    return render_template("export_designs_packing_lists/list.html", invoices=invoices, docs=docs)


@export_designs_packing_lists_bp.route("/<int:export_invoice_id>")
@login_required
def view_designs_packing_list(export_invoice_id):
    container = current_app.container
    try:
        invoice = container.export_invoice_service.get(export_invoice_id, g.user.company_id)
    except NotFoundError:
        abort(404)
    packing_list = container.export_packing_list_service.get_for_invoice(export_invoice_id, g.user.company_id)
    if not packing_list:
        # Same fallback the export packing list itself uses: an invoice saved
        # before packing lists existed gets one the moment it is saved again.
        return redirect(url_for("export_invoices.edit_export_invoice", export_invoice_id=export_invoice_id))

    document = container.export_packing_list_service.get_designs_document(
        export_invoice_id, g.user.company_id
    )
    # Once the document exists, arriving here from an export invoice means
    # "show me the designs packing list" - so preview the issued sheet, the
    # same way the regular Export Packing List link does. Editing it is a
    # deliberate step from there (?edit=1), not what a plain click lands on.
    if document and not request.args.get("edit"):
        return redirect(url_for(
            "export_designs_packing_lists.print_designs_packing_list", export_invoice_id=export_invoice_id
        ))

    # Per-line design rows, scoped to the purchase orders that actually fed
    # this invoice and already carrying what this line holds / what is left.
    reference = container.export_packing_list_service.reference_designs(
        g.user.company_id, packing_list
    )

    # Containers are filled one at a time, in order: a line only offers its
    # designs once every line before it is fully allocated. Until then the
    # later containers stay closed, so whatever is left over always lands on
    # the next container rather than being spread around by hand.
    lines, active_taken = [], False
    for item in sorted(packing_list.items, key=lambda i: (i.container_sr_no, i.sr_no)):
        allocated = sum(d.quantity_boxes or 0 for d in item.designs)
        if _is_filled(item):
            state = "done"
        elif not active_taken:
            state, active_taken = "active", True
        else:
            state = "locked"
        lines.append({
            "item": item,
            "designs": reference.get((item.invoice_item_sr_no, item.container_sr_no), []),
            "state": state,
            "allocated": allocated,
        })

    return render_template(
        "export_designs_packing_lists/form.html",
        invoice=invoice, packing_list=packing_list, lines=lines,
        document=document,
        all_filled=all(l["state"] == "done" for l in lines) if lines else False,
    )


@export_designs_packing_lists_bp.route("/<int:export_invoice_id>/create", methods=["POST"])
@login_required
def create_designs_packing_list(export_invoice_id):
    """Turns the filled-in allocation into the document proper - its own
    DSGPL number and date, and a printable sheet."""
    try:
        doc = current_app.container.export_packing_list_service.create_designs_document(
            g.user, export_invoice_id
        )
        flash(f"Designs packing list {doc.packing_list_number} created.", "success")
        return redirect(url_for(
            "export_designs_packing_lists.print_designs_packing_list", export_invoice_id=export_invoice_id
        ))
    except (ValidationError, NotFoundError) as e:
        flash(str(e), "error")
    return redirect(url_for(
        "export_designs_packing_lists.view_designs_packing_list",
        export_invoice_id=export_invoice_id, edit=1,
    ))


@export_designs_packing_lists_bp.route("/<int:export_invoice_id>/print")
@login_required
def print_designs_packing_list(export_invoice_id):
    """The printable sheet: the container split restated design by design."""
    container = current_app.container
    doc = container.export_packing_list_service.get_designs_document(export_invoice_id, g.user.company_id)
    if not doc:
        flash("This designs packing list hasn't been created yet.", "error")
        return redirect(url_for(
            "export_designs_packing_lists.view_designs_packing_list",
            export_invoice_id=export_invoice_id, edit=1,
        ))
    company = container.company_service.get(g.user.company_id)
    return render_template(
        "export_designs_packing_lists/print.html",
        doc=doc, invoice=doc.invoice, packing_list=doc.packing_list, company=company,
    )


@export_designs_packing_lists_bp.route("/<int:export_invoice_id>/line", methods=["POST"])
@login_required
def save_line(export_invoice_id):
    """Saves ONE container-split line's design breakdown. Line-by-line rather
    than one form for the whole invoice, because the balance rule is itself
    per line - a message about one line's shortfall shouldn't discard every
    other line's typing."""
    container = current_app.container
    packing_list = container.export_packing_list_service.get_for_invoice(export_invoice_id, g.user.company_id)
    if not packing_list:
        abort(404)
    try:
        invoice_item_sr_no = int(request.form.get("invoice_item_sr_no"))
        container_sr_no = int(request.form.get("container_sr_no"))
    except (TypeError, ValueError):
        abort(400)

    # Only ticked designs submit a design_id[] value (an unticked checkbox
    # sends nothing), and each row's boxes are posted under a name carrying
    # its own design id - so the two can never fall out of step the way
    # parallel arrays would when some rows are unticked.
    raw_rows = [
        {"design_id": design_id, "quantity_boxes": request.form.get(f"design_boxes_{design_id}", "")}
        for design_id in request.form.getlist("design_id[]")
    ]
    anchor = ""
    try:
        container.export_packing_list_service.save_design_allocation(
            g.user.company_id, packing_list.id, invoice_item_sr_no, container_sr_no, raw_rows,
        )
        flash("Designs saved.", "success")
        # Carry on where the work is: the next container still to be split.
        # On a rejected save we deliberately stay at the top instead, since
        # that is where the message explaining the refusal is shown.
        anchor = _next_anchor(
            container.export_packing_list_service.get_for_invoice(export_invoice_id, g.user.company_id)
        )
    except (ValidationError, NotFoundError) as e:
        flash(str(e), "error")
    # Back to the edit form explicitly - a save is mid-edit, so it must not
    # bounce off to the printed sheet even once the document exists.
    return redirect(url_for(
        "export_designs_packing_lists.view_designs_packing_list",
        export_invoice_id=export_invoice_id, edit=1,
    ) + anchor)

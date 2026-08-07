"""
app/vgm.py
-----------
The per-container VGM figures, derived once and shared by both VGM
documents: the VGM ATTACHMENT (which prints every container as a row, and is
where the weighbridge and slip number are typed) and the VGM declaration
(INFORMATION ABOUT VERIFIED GROSS MASS OF CONTAINER), which quotes the same
figures back for small shipments and otherwise just points at the attachment.

Kept out of both route modules so neither has to import the other, and so
the two sheets can never disagree about a container's weight.
"""

from typing import List, Optional

# How many containers the declaration will spell out inline. Past this the
# sheet says "VGM ATTACHMENT" instead and the reader goes to that sheet -
# the declaration has one cell per field, not one row per container.
INLINE_CONTAINER_LIMIT = 3

# What that cell says when there are too many containers to list.
SEE_ATTACHMENT = "VGM ATTACHMENT"


def to_number(value) -> Optional[float]:
    """11B weights are stored as free text (they are transcribed off a
    weighbridge ticket), so anything unparseable comes back as None and
    simply doesn't take part in a sum."""
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def container_rows(invoice, packing_list) -> List[dict]:
    """One row per physical container: the export invoice's section-11B row,
    its size from the booked Container Details list (same position, same
    ordering), and its cargo weight from the packing list's split for that
    container. Total VGM Weight is tare + cargo; a container missing either
    has no total rather than a wrong one.
    """
    cargo_by_sr = {}
    if packing_list and packing_list.items:
        cargo_by_sr = {r["sr_no"]: r["gross_weight_kg"] for r in packing_list.container_rows}
    sizes = invoice.container_types_expanded

    rows = []
    for index, detail in enumerate(invoice.container_details):
        sr_no = detail.get("sr_no") or index + 1
        tare = to_number(detail.get("tare_weight"))
        cargo = cargo_by_sr.get(sr_no)
        rows.append({
            "sr_no": sr_no,
            "booking_no": invoice.booking_no or "",
            "container_no": detail.get("container_no") or "",
            "container_size": sizes[index] if index < len(sizes) else "",
            "max_permitted_weight": to_number(detail.get("max_permitted_weight")),
            "max_permitted_weight_raw": detail.get("max_permitted_weight") or "",
            "tare_weight": tare,
            "tare_weight_raw": detail.get("tare_weight") or "",
            "cargo_weight": cargo,
            "total_vgm_weight": (tare + cargo) if (tare is not None and cargo is not None) else None,
            "weighbridge_name": detail.get("weighbridge_name") or "",
            "weighing_slip_no": detail.get("weighing_slip_no") or "",
        })
    return rows


def _format(value) -> str:
    return f"{value:,.2f}" if isinstance(value, float) else str(value or "").strip()


def declaration_cell(rows: List[dict], key: str) -> str:
    """What the declaration prints in a per-container field.

    Up to INLINE_CONTAINER_LIMIT containers it lists the values themselves,
    one per line; beyond that it prints SEE_ATTACHMENT, because the
    declaration has a single cell per field and a long list would not fit -
    the attachment sheet is where every container is enumerated.
    """
    if not rows:
        return "-"
    if len(rows) > INLINE_CONTAINER_LIMIT:
        return SEE_ATTACHMENT
    values = [_format(row.get(key)) for row in rows]
    return "\n".join(v for v in values if v) or "-"

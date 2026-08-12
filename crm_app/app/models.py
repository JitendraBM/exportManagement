"""
app/models.py
-------------
Plain data classes that mirror the tables in schema.sql.

These objects carry data only - no SQL, no Flask, no business rules. That
separation is what makes the Repository layer swappable and the Service
layer unit-testable without a real database.

Each class also knows how to build itself `from_row(sqlite3.Row)`. That's a
small convenience, not a violation of Single Responsibility - it's still
just "how do I represent myself", not "how do I persist myself".
"""

import json
from dataclasses import dataclass, field
from typing import Optional, List

from app.utils import is_fob_terms


@dataclass
class Tenant:
    """A company/business using this CRM, picked on the login screen before
    username/password. NOT the same thing as OurCompany below - a Tenant is
    the workspace/login concept, OurCompany is one specific tenant's own
    business profile (GSTIN/PAN/bank details) shown on its quotations."""
    id: Optional[int]
    name: str
    slug: str
    is_active: bool = True
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "Tenant":
        return Tenant(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )


@dataclass
class User:
    id: Optional[int]
    company_id: int
    username: str
    password_hash: str
    full_name: str
    role: str  # 'admin' | 'employee'
    is_active: bool = True
    created_at: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @staticmethod
    def from_row(row) -> "User":
        return User(
            id=row["id"],
            company_id=row["company_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )


@dataclass
class ContactPerson:
    """Used for lead_contacts and client_contacts - identical shape, so one
    class serves both (Interface Segregation without needless duplication)."""
    id: Optional[int]
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    is_primary: bool = False

    @staticmethod
    def from_row(row) -> "ContactPerson":
        return ContactPerson(
            id=row["id"],
            name=row["name"],
            phone=row["phone"],
            email=row["email"],
            is_primary=bool(row["is_primary"]),
        )


@dataclass
class Communication:
    id: Optional[int]
    parent_type: str  # 'lead' | 'buyer' | 'supplier' | 'exporter'
    parent_id: int
    employee_id: int
    comm_date: str
    mode: str
    description: str
    follow_up_date: Optional[str] = None
    created_at: Optional[str] = None
    employee_name: Optional[str] = None  # populated by joined queries only

    @staticmethod
    def from_row(row) -> "Communication":
        return Communication(
            id=row["id"],
            parent_type=row["parent_type"],
            parent_id=row["parent_id"],
            employee_id=row["employee_id"],
            comm_date=row["comm_date"],
            mode=row["mode"],
            description=row["description"],
            follow_up_date=row["follow_up_date"],
            created_at=row["created_at"],
            employee_name=row["employee_name"] if "employee_name" in row.keys() else None,
        )


LEAD_STATUSES = [
    ("new", "New"),
    ("in_communication", "In Communication"),
    ("in_follow_up", "In Follow Up"),
    ("long_follow_up", "Long Follow Up"),
    ("quotation_submission_pending", "Quotation Submission Pending"),
    ("in_client", "In Client"),
]

CLIENT_STATUSES = [
    ("proforma_invoice_submission_pending", "Proforma Invoice Submission Pending"),
    ("purchase_order_submission_pending", "Purchase Order Submission Pending"),
    ("purchase_invoice_submission_pending", "Purchase Invoice Submission Pending"),
    ("export_invoice_submission_pending", "Export Invoice Submission Pending"),
    ("commercial_invoice_submission_pending", "Commercial Invoice Submission Pending"),
]

# Maps a document type just generated for a client -> the CLIENT_STATUSES
# stage that becomes pending once it's done (i.e. what's next). Document
# services call services.advance_client_status(...) with their key after
# create/update so client status auto-advances - adding a future document
# type (Purchase Order, Purchase Invoice, Export Invoice, Commercial
# Invoice) only requires registering it here, no other wiring. Packing List
# is deliberately absent: it doesn't correspond to any CLIENT_STATUSES stage.
CLIENT_STATUS_ADVANCE_ON = {
    "proforma_invoice": "purchase_order_submission_pending",
    "purchase_order": "purchase_invoice_submission_pending",
    "purchase_invoice": "export_invoice_submission_pending",
    "export_invoice": "commercial_invoice_submission_pending",
}

CLIENT_TYPES = ["Supplier", "Exporter", "Buyer"]  # the lead-conversion picker only; each type now lives in its own table

COMMUNICATION_MODES = ["WhatsApp", "WeChat", "Call", "Email", "In Person", "Other"]

# What a product's quantity is measured in. One shared list drives the
# product form, the Unit dropdowns on quotation/proforma/packing-list lines,
# and the service-side fallback - so the choices can't drift apart.
PRODUCT_UNITS = ["SQM", "LM", "PCS", "KG", "SET"]

# What a purchase order is bought under - it decides the GST rate applied to
# the whole order (see PurchaseOrderService._tax_percentages):
#   full_tax  - the ordinary rate, taken from the catalog products on the lines
#   exemption - the concessional rate for supplies meant for export (0.1% total)
PURCHASE_TYPES = {"full_tax": "Full Tax Purchase", "exemption": "Exemption"}
DEFAULT_PURCHASE_TYPE = "full_tax"
# The whole-order rate under Exemption: 0.1% inter-state, split into
# 0.05% + 0.05% when it's an intra-state purchase (same halving rule the
# catalog products follow for their own rates).
EXEMPTION_IGST_PERCENT = 0.1


@dataclass
class Lead:
    id: Optional[int]
    company_id: int
    company_name: str
    phone: str
    email: str
    facebook: Optional[str]
    instagram: Optional[str]
    other_social: Optional[str]
    status: str
    created_by: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_converted: bool = False
    converted_client_type: Optional[str] = None  # 'Buyer' | 'Supplier' | 'Exporter' - says which table converted_client_id names
    converted_client_id: Optional[int] = None
    # populated by joins / repository convenience methods, not stored columns
    created_by_name: Optional[str] = None
    contacts: List[ContactPerson] = field(default_factory=list)

    @staticmethod
    def from_row(row) -> "Lead":
        return Lead(
            id=row["id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            phone=row["phone"],
            email=row["email"],
            facebook=row["facebook"],
            instagram=row["instagram"],
            other_social=row["other_social"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_converted=bool(row["is_converted"]),
            converted_client_type=row["converted_client_type"] if "converted_client_type" in row.keys() else None,
            converted_client_id=row["converted_client_id"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
        )

    @property
    def status_label(self) -> str:
        return dict(LEAD_STATUSES).get(self.status, self.status)


@dataclass
class Party:
    """A Buyer or Exporter record - the two are treated as having identical
    data/documentation structure for now (see CLIENT_TYPES), so one
    dataclass and one table shape serves both; only which table a row lives
    in (`buyers` vs `exporters`) says which type it is. Supplier has since
    diverged into its own shape (see Supplier below), modeled on OurCompany
    instead of on a lead."""
    id: Optional[int]
    company_id: int
    lead_id: Optional[int]
    company_name: str
    phone: str
    email: str
    facebook: Optional[str]
    instagram: Optional[str]
    other_social: Optional[str]
    status: str
    created_by: int
    address: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    contacts: List[ContactPerson] = field(default_factory=list)

    @staticmethod
    def from_row(row) -> "Party":
        return Party(
            id=row["id"],
            company_id=row["company_id"],
            lead_id=row["lead_id"],
            company_name=row["company_name"],
            phone=row["phone"],
            email=row["email"],
            facebook=row["facebook"],
            instagram=row["instagram"],
            other_social=row["other_social"],
            status=row["status"],
            created_by=row["created_by"],
            address=row["address"] if "address" in row.keys() else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def status_label(self) -> str:
        return dict(CLIENT_STATUSES).get(self.status, self.status)


@dataclass
class Supplier:
    """Also "graduates" from an approved lead, but its data mirrors
    OurCompany's own profile shape (GSTIN/PAN/IEC/bank/contacts) instead of
    a Party's lead-shaped fields - company logo, BIN and LUT are
    deliberately not carried. Document types for suppliers aren't defined
    yet; `status` is borrowed from the same CLIENT_STATUSES pipeline as
    Buyer/Exporter for now and may change once that's specified."""
    id: Optional[int]
    company_id: int
    lead_id: Optional[int]
    company_name: str
    status: str
    created_by: int
    address: Optional[str] = None
    gstin: Optional[str] = None
    cin_llp_no: Optional[str] = None  # optional: CIN (company) or LLPIN (LLP) registration number
    pan_no: Optional[str] = None
    iec: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    contact_details: List[dict] = field(default_factory=list)  # [{type, value, is_primary}]
    contact_persons: List[dict] = field(default_factory=list)  # [{name, is_primary}]
    bank_details: List[dict] = field(default_factory=list)  # [{bank_name, account_number, ifsc_code, swift_code, branch, bank_address, is_primary}]

    @staticmethod
    def from_row(row) -> "Supplier":
        return Supplier(
            id=row["id"],
            company_id=row["company_id"],
            lead_id=row["lead_id"],
            company_name=row["company_name"],
            status=row["status"],
            created_by=row["created_by"],
            address=row["address"],
            gstin=row["gstin"],
            cin_llp_no=row["cin_llp_no"] if "cin_llp_no" in row.keys() else None,
            pan_no=row["pan_no"],
            iec=row["iec"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def status_label(self) -> str:
        return dict(CLIENT_STATUSES).get(self.status, self.status)


@dataclass
class Transporter:
    """The haulier a consignment moves with. Unlike Buyer/Supplier/Exporter
    this one never comes from a lead - nobody prospects a transporter, we
    just keep the registration details that have to be quoted on the
    paperwork - so there's no lead_id and no status pipeline, and none of
    the payments/communications/documents satellites apply either. Contact
    persons are the same name/phone/email shape a Party uses."""
    id: Optional[int]
    company_id: int
    name: str
    created_by: int
    address: Optional[str] = None
    gstin_transporter_no: Optional[str] = None  # GSTIN / Transporter No. - one and the same cell
    pan_no: Optional[str] = None
    cin_llp_no: Optional[str] = None  # optional: CIN (company) or LLPIN (LLP) registration number
    email: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    contacts: List[ContactPerson] = field(default_factory=list)

    @staticmethod
    def from_row(row) -> "Transporter":
        return Transporter(
            id=row["id"],
            company_id=row["company_id"],
            name=row["name"],
            created_by=row["created_by"],
            address=row["address"],
            gstin_transporter_no=row["gstin_transporter_no"],
            pan_no=row["pan_no"],
            cin_llp_no=row["cin_llp_no"],
            email=row["email"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PaymentEntry:
    id: Optional[int]
    parent_type: str  # 'buyer' | 'supplier' | 'exporter'
    parent_id: int
    account_name: str
    payment_datetime: str
    amount_original: float
    currency_code: str
    conversion_rate: float
    amount_inr: float
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "PaymentEntry":
        return PaymentEntry(
            id=row["id"],
            parent_type=row["parent_type"],
            parent_id=row["parent_id"],
            account_name=row["account_name"],
            payment_datetime=row["payment_datetime"],
            amount_original=row["amount_original"],
            currency_code=row["currency_code"],
            conversion_rate=row["conversion_rate"],
            amount_inr=row["amount_inr"],
            created_at=row["created_at"],
        )


@dataclass
class DocumentEntry:
    """Metadata-only placeholder for now (see the hint on the buyer/
    supplier/exporter detail page) - a future update will auto-generate and
    file-store these the same way Quotation already works. When that
    happens, give the new document type its own optional `lead_id` (like
    Quotation.lead_id) instead of a parent link - a party has no document
    link of its own; QuotationRepository.list_for_lead shows the pattern: a
    converted party's documents are found via `party.lead_id`, so anything
    created against the lead (before OR after conversion) stays visible on
    the party automatically, with nothing to copy or keep in sync by hand."""
    id: Optional[int]
    parent_type: str  # 'buyer' | 'supplier' | 'exporter'
    parent_id: int
    document_name: str
    document_type: str
    document_date: str
    notes: Optional[str] = None
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "DocumentEntry":
        return DocumentEntry(
            id=row["id"],
            parent_type=row["parent_type"],
            parent_id=row["parent_id"],
            document_name=row["document_name"],
            document_type=row["document_type"],
            document_date=row["document_date"],
            notes=row["notes"],
            created_at=row["created_at"],
        )


@dataclass
class OurCompany:
    id: int
    company_id: int
    company_name: str
    gstin: Optional[str]
    pan_no: Optional[str]
    iec: Optional[str]
    bin: Optional[str] = None
    branch_code: Optional[str] = None  # IEC branch code, printed on the Export Invoice annexure (section 2B)
    address: Optional[str] = None
    logo_path: Optional[str] = None  # relative to static/, shown in the app sidebar and on generated documents
    self_sealing_declaration: Optional[str] = None  # printed on the Export Invoice's declaration block
    government_schemes: Optional[str] = None  # default for the Export Annexure's section 13 and the Export Invoice's "Export under" text
    updated_at: Optional[str] = None
    contact_details: List[dict] = field(default_factory=list)  # [{type, value, is_primary}]
    contact_persons: List[dict] = field(default_factory=list)  # [{name, designation, is_primary}]
    bank_details: List[dict] = field(default_factory=list)  # [{bank_name, account_number, ifsc_code, branch, is_primary}]
    lut_details: List[dict] = field(default_factory=list)  # [{lut_number, financial_year, is_primary}]
    rcmc_details: List[dict] = field(default_factory=list)  # [{registration_number, registration_date, valid_until, organisation_name, organisation_address, contact_number, email_address, is_primary}]

    @staticmethod
    def from_row(row) -> "OurCompany":
        return OurCompany(
            id=row["id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            gstin=row["gstin"],
            pan_no=row["pan_no"],
            iec=row["iec"],
            bin=row["bin"] if "bin" in row.keys() else None,
            branch_code=row["branch_code"] if "branch_code" in row.keys() else None,
            address=row["address"] if "address" in row.keys() else None,
            logo_path=row["logo_path"] if "logo_path" in row.keys() else None,
            self_sealing_declaration=row["self_sealing_declaration"] if "self_sealing_declaration" in row.keys() else None,
            government_schemes=row["government_schemes"] if "government_schemes" in row.keys() else None,
            updated_at=row["updated_at"],
        )


@dataclass
class MiscCurrency:
    """One row of the CURRENCY drop list maintained under Administration ->
    Miscellaneous. Every currency dropdown in the app is filled from these."""
    id: Optional[int]
    company_id: int
    name: str        # "name of currency", e.g. USD
    symbol: str      # "currency symbol", e.g. $
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def label(self) -> str:
        """How the currency reads on a printed sheet: `USD [ $ ]`."""
        return f"{self.name} [ {self.symbol} ]"

    @staticmethod
    def from_row(row) -> "MiscCurrency":
        return MiscCurrency(
            id=row["id"], company_id=row["company_id"],
            name=row["name"], symbol=row["symbol"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


@dataclass
class MiscNatureOfContract:
    """One row of the NATURE OF CONTRACT drop list maintained under
    Administration -> Miscellaneous. The same list fills the delivery-terms
    field on every document, whatever that document calls it ("Nature of
    contract", "Shipping terms", "Terms of delivery")."""
    id: Optional[int]
    company_id: int
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "MiscNatureOfContract":
        return MiscNatureOfContract(
            id=row["id"], company_id=row["company_id"], name=row["name"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


@dataclass
class MiscPortOfLoading:
    """One row of the PORT OF LOADING drop list maintained under
    Administration -> Miscellaneous: the port a shipment leaves from and that
    port's PIN code (the figure the GST/e-way-bill paperwork asks for)."""
    id: Optional[int]
    company_id: int
    name: str        # "Port of Loading", e.g. MUNDRA
    pin_code: str    # "Port of loading Pincode", e.g. 370421
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def label(self) -> str:
        """How the port reads where both halves are shown: `MUNDRA - 370421`."""
        return f"{self.name} - {self.pin_code}"

    @staticmethod
    def from_row(row) -> "MiscPortOfLoading":
        return MiscPortOfLoading(
            id=row["id"], company_id=row["company_id"],
            name=row["name"], pin_code=row["pin_code"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


# The currency dropdowns used to be a hard-coded list on the payment form.
# Until an admin adds a row under Administration -> Miscellaneous, that same
# list is what the dropdowns fall back to, so nothing breaks on upgrade.
DEFAULT_CURRENCIES = [
    ("USD", "$"), ("EUR", "€"), ("GBP", "£"),
    ("AED", "د.إ"), ("CNY", "¥"), ("SAR", "﷼"),
]


def currency_display(code: Optional[str], symbol: Optional[str],
                     default_code: str = "USD", default_symbol: str = "$") -> tuple:
    """(name, prefix, label) for a document's stored currency, e.g.
    ("USD", "$", "USD [ $ ]"). A document saved before the currency became a
    picked field has neither, and falls back to whatever its sheet used to
    hard-code - so nothing already printed changes."""
    if not code:
        return default_code, default_symbol, f"{default_code} [ {default_symbol} ]"
    prefix = symbol or ""
    return code, prefix, (f"{code} [ {prefix} ]" if prefix else code)


@dataclass
class Permit:
    """One "permission" the company holds, managed under the Our Company
    area. It records a stuffing-place name + place of stuffing, the issuing
    authority, is either valid until an expiry date OR a one-time permit
    (validity_type), and can carry an uploaded PDF."""
    id: Optional[int]
    company_id: int
    permission_number: str
    created_by: int
    stuffing_place_name: Optional[str] = None
    place_of_stuffing: Optional[str] = None
    date_of_issue: Optional[str] = None
    issuing_authority: Optional[str] = None
    issuing_authority_address: Optional[str] = None
    validity_type: str = "expiry"  # 'expiry' | 'one_time'
    date_of_expiry: Optional[str] = None  # only when validity_type == 'expiry'
    pdf_path: Optional[str] = None  # relative to static/
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_one_time(self) -> bool:
        return self.validity_type == "one_time"

    @staticmethod
    def from_row(row) -> "Permit":
        return Permit(
            id=row["id"],
            company_id=row["company_id"],
            permission_number=row["permission_number"],
            created_by=row["created_by"],
            stuffing_place_name=row["stuffing_place_name"],
            place_of_stuffing=row["place_of_stuffing"],
            date_of_issue=row["date_of_issue"],
            issuing_authority=row["issuing_authority"],
            issuing_authority_address=row["issuing_authority_address"],
            validity_type=row["validity_type"],
            date_of_expiry=row["date_of_expiry"],
            pdf_path=row["pdf_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Category:
    """A folder at the catalog root that groups products. `parent_id=None`
    means it sits at the catalog's top level; categories nest to any depth
    via self-reference, the same way sub categories (ProductFolder) nest
    inside a product. Products with category_id=NULL sit directly at the
    root, the same way a design can sit directly under a product."""
    id: Optional[int]
    company_id: int
    name: str
    parent_id: Optional[int] = None
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "Category":
        return Category(
            id=row["id"],
            company_id=row["company_id"],
            name=row["name"],
            parent_id=row["parent_id"] if "parent_id" in row.keys() else None,
            created_at=row["created_at"],
        )


@dataclass
class Product:
    """Second level of the catalog (inside a category, or at the root when
    category_id is None): the tax/HSN identity AND the physical packing spec
    (pallet types, quantity, alternate quantity, unit) that
    quotations, proforma invoices and packing lists all read from - every
    design under a product shares the same packing spec. Sub categories and
    designs live underneath it; price and photos belong to the Design.
    IGST is the only tax input - SGST and CGST are always stored as half of
    it (recalculated by ProductService on every save)."""
    id: Optional[int]
    company_id: int
    product_name: str
    category_id: Optional[int] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    igst_percent: Optional[float] = None
    sgst_percent: Optional[float] = None
    cgst_percent: Optional[float] = None
    quantity_unit: str = "PCS"  # what `quantity` is measured in
    quantity: Optional[str] = None  # per-box quantity (e.g. pcs per box)
    alternate_quantity_unit: str = "SQM"  # what `alternate_quantity` is measured in; prefills document lines' Unit column
    alternate_quantity: Optional[str] = None  # per-box quantity, drives the Boxes x AltQty auto-calc
    net_weight_kg: Optional[float] = None    # net weight per box (KG) - drives the packing list's Boxes x weight auto-calc
    gross_weight_kg: Optional[float] = None  # gross weight per box (KG) - same auto-calc as net_weight_kg
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "Product":
        return Product(
            id=row["id"],
            company_id=row["company_id"],
            product_name=row["product_name"],
            category_id=row["category_id"] if "category_id" in row.keys() else None,
            description=row["description"],
            hsn_code=row["hsn_code"],
            igst_percent=row["igst_percent"],
            sgst_percent=row["sgst_percent"],
            cgst_percent=row["cgst_percent"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            quantity=row["quantity"] if "quantity" in row.keys() else None,
            alternate_quantity_unit=row["alternate_quantity_unit"] if "alternate_quantity_unit" in row.keys() else "SQM",
            alternate_quantity=row["alternate_quantity"] if "alternate_quantity" in row.keys() else None,
            net_weight_kg=row["net_weight_kg"] if "net_weight_kg" in row.keys() else None,
            gross_weight_kg=row["gross_weight_kg"] if "gross_weight_kg" in row.keys() else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ProductPalletType:
    """One named pallet storage option of a product (e.g. "pine pallet"
    holding 31 boxes). A product can carry any number of these; every
    product ALSO implicitly offers "loose" (goods sold unpalletised, zero
    pallets), which is never stored. The alternate quantity one pallet
    holds is always derived - boxes_per_pallet x the product's per-box
    alternate_quantity - so it can't drift when the product spec changes."""
    id: Optional[int]
    company_id: int
    product_id: int
    name: str
    boxes_per_pallet: float
    sort_order: int = 0
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "ProductPalletType":
        return ProductPalletType(
            id=row["id"],
            company_id=row["company_id"],
            product_id=row["product_id"],
            name=row["name"],
            boxes_per_pallet=row["boxes_per_pallet"],
            sort_order=row["sort_order"],
            created_at=row["created_at"],
        )


@dataclass
class ProductFolder:
    """A sub category inside a product (shown as "Sub Category" in the UI;
    the table keeps its historical product_folders name). `parent_id=None`
    means it sits at the product's top level; sub categories can nest to any
    depth via self-reference, but always belong to exactly one product."""
    id: Optional[int]
    company_id: int
    product_id: int
    name: str
    parent_id: Optional[int] = None
    created_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "ProductFolder":
        return ProductFolder(
            id=row["id"],
            company_id=row["company_id"],
            product_id=row["product_id"],
            name=row["name"],
            parent_id=row["parent_id"],
            created_at=row["created_at"],
        )


@dataclass
class Design:
    """The sellable leaf of the catalog: one concrete design (finish/color
    variant) of a product, carrying its own price and photos. Packing,
    quantity and weight are shared across every design of the same product,
    so they live on Product instead. `folder_id=None` means it sits directly
    under the product."""
    id: Optional[int]
    company_id: int
    product_id: int
    design_name: str
    folder_id: Optional[int] = None
    description: Optional[str] = None
    surface: Optional[str] = None  # optional finish, e.g. GLOSSY / MATT / CHROME
    price_usd: Optional[float] = None
    photo_path: Optional[str] = None
    dimension_photo_path: Optional[str] = None
    alt_text: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @staticmethod
    def from_row(row) -> "Design":
        return Design(
            id=row["id"],
            company_id=row["company_id"],
            product_id=row["product_id"],
            folder_id=row["folder_id"],
            design_name=row["design_name"],
            description=row["description"],
            surface=row["surface"] if "surface" in row.keys() else None,
            price_usd=row["price_usd"],
            photo_path=row["photo_path"],
            dimension_photo_path=row["dimension_photo_path"],
            alt_text=row["alt_text"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class CifMoneyLadder:
    """The money ladder shared by every document that quotes a buyer a price:
    Quotation, Proforma Invoice and Export Invoice.

    Every price in this app is a CIF price - the carriage, insurance and
    handling are already inside the per-unit rate the buyer is quoted, they are
    NOT charges added on top of it. So the ladder runs DOWNWARDS from the goods
    total, and the printed sheets all read:

        CIF Value       goods total (the sum of the line totals)
      - Discount
      = Invoice Value   what the buyer actually pays; the figure spelled out
                        in words at the foot of the sheet
      - Insurance
      - Sea Freight
      - Certification
      - Other
      = FOB Value       the goods on their own, which is the figure customs
                        and the export incentives are computed against

    Kept in one mixin (rather than repeated on each dataclass) so a change to
    the arithmetic can't leave one document type disagreeing with another.
    A mixin adds no fields, so the dataclasses that inherit it keep their exact
    field list - which matters, because document version history round-trips
    them through `dataclasses.asdict` / `cls(**data)`.

    Implementors supply `subtotal_usd` (the goods total, sometimes precomputed
    by a list query) plus the five charge/discount fields.
    """

    @property
    def round_off(self) -> float:
        """The cent or two the printed lines can't carry - see Quotation's own
        `round_off` field, which shadows this. Zero for every document that
        prices the plain way, where the lines ARE the CIF value."""
        return 0.0

    @property
    def cif_value_usd(self) -> float:
        """CIF Value - the goods total, i.e. what used to print as SUBTOTAL."""
        return self.subtotal_usd + self.round_off

    @property
    def invoice_value_usd(self) -> float:
        """CIF less the discount: the amount payable."""
        return self.cif_value_usd - self.discount_amount

    @property
    def charges_total(self) -> float:
        """The four charges that sit between FOB and CIF. The discount is NOT
        one of them - it comes off ABOVE the invoice value line."""
        return self.insurance + self.sea_freight + self.certification + self.other_charges

    @property
    def fob_value_usd(self) -> float:
        """The invoice value with the carriage/handling charges stripped back
        out. Under FOB terms sea freight and insurance are already held at zero
        (the buyer carries the ocean leg), so they drop out of the subtraction
        on their own and need no special case here."""
        return (self.invoice_value_usd - self.insurance - self.sea_freight
                - self.certification - self.other_charges)


@dataclass
class QuotationItem:
    id: Optional[int]
    quotation_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    dimension_mm: Optional[str] = None
    hsn_code: Optional[str] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    pallets: Optional[float] = None
    quantity_value: float = 0
    unit: str = "SQM"
    price_usd: float = 0
    total_usd: float = 0
    # Unused (kept so an old row still loads) - quotations no longer have an
    # FOB-typed-price mode; price_usd is always the absolute price the user
    # typed. See Quotation.fob_pricing / Quotation.cif_adjust_usd.
    fob_price_usd: Optional[float] = None

    @staticmethod
    def from_row(row) -> "QuotationItem":
        return QuotationItem(
            id=row["id"],
            quotation_id=row["quotation_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            dimension_mm=row["dimension_mm"],
            hsn_code=row["hsn_code"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            pallets=row["pallets"] if "pallets" in row.keys() else None,
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            price_usd=row["price_usd"],
            total_usd=row["total_usd"],
            fob_price_usd=row["fob_price_usd"] if "fob_price_usd" in row.keys() else None,
        )


@dataclass
class Quotation(CifMoneyLadder):
    id: Optional[int]
    company_id: int
    quotation_number: str
    quotation_date: str
    buyer_name: str
    created_by: int
    lead_id: Optional[int] = None
    buyer_address: Optional[str] = None
    buyer_reference_no: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    final_destination: Optional[str] = None
    packing_details: Optional[str] = None
    container_details: Optional[str] = None
    shipping_mode: Optional[str] = None
    shipping_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    price_validity_days: int = 30
    remarks: Optional[str] = None
    sea_freight: float = 0
    insurance: float = 0
    certification: float = 0
    other_charges: float = 0
    discount_amount: float = 0
    # Unused (kept so an old quotation's row still loads): quotations no
    # longer have an FOB-typed-price mode - price_usd is always the absolute
    # price the user typed. See cif_adjust_usd and cif_value_usd below.
    fob_pricing: bool = False
    round_off: float = 0
    # The manual gap between what the CIF value field was typed as and what
    # the ladder computes (goods total + charges) - see cif_value_usd below
    # and the form's subtotal-input handler. Unlike price_usd this IS the one
    # place a manual adjustment is allowed to land; 0 on a quotation whose CIF
    # value was never overridden.
    cif_adjust_usd: float = 0
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc_code: Optional[str] = None
    bank_swift_code: Optional[str] = None
    bank_branch: Optional[str] = None
    bank_address: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    # Currency the document is written in, picked from the Administration ->
    # Miscellaneous list and snapshotted so a later edit of that list can't
    # rewrite an issued sheet. Display information only - no conversion.
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    items: List[QuotationItem] = field(default_factory=list)
    computed_subtotal_usd: Optional[float] = None  # precomputed by list queries that don't load items

    @staticmethod
    def from_row(row) -> "Quotation":
        return Quotation(
            id=row["id"],
            company_id=row["company_id"],
            quotation_number=row["quotation_number"],
            quotation_date=row["quotation_date"],
            lead_id=row["lead_id"] if "lead_id" in row.keys() else None,
            buyer_name=row["buyer_name"],
            buyer_address=row["buyer_address"],
            buyer_reference_no=row["buyer_reference_no"],
            port_of_loading=row["port_of_loading"],
            port_of_discharge=row["port_of_discharge"],
            final_destination=row["final_destination"] if "final_destination" in row.keys() else None,
            packing_details=row["packing_details"],
            container_details=row["container_details"],
            shipping_mode=row["shipping_mode"],
            shipping_terms=row["shipping_terms"],
            payment_terms=row["payment_terms"],
            price_validity_days=row["price_validity_days"],
            remarks=row["remarks"],
            sea_freight=row["sea_freight"] if "sea_freight" in row.keys() else 0,
            insurance=row["insurance"] if "insurance" in row.keys() else 0,
            certification=row["certification"] if "certification" in row.keys() else 0,
            other_charges=row["other_charges"] if "other_charges" in row.keys() else 0,
            discount_amount=row["discount_amount"],
            fob_pricing=bool(row["fob_pricing"]) if "fob_pricing" in row.keys() else False,
            round_off=(row["round_off"] if "round_off" in row.keys() else 0) or 0,
            cif_adjust_usd=(row["cif_adjust_usd"] if "cif_adjust_usd" in row.keys() else 0) or 0,
            bank_name=row["bank_name"],
            bank_account_number=row["bank_account_number"],
            bank_ifsc_code=row["bank_ifsc_code"],
            bank_swift_code=row["bank_swift_code"],
            bank_branch=row["bank_branch"],
            bank_address=row["bank_address"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
            computed_subtotal_usd=row["items_total"] if "items_total" in row.keys() else None,
            currency_code=row["currency_code"] if "currency_code" in row.keys() else None,
            currency_symbol=row["currency_symbol"] if "currency_symbol" in row.keys() else None,
        )

    @property
    def currency_name(self) -> str:
        """The currency's name, e.g. `USD` - what the money column headings
        and typed-amount labels read."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[0]

    @property
    def currency_prefix(self) -> str:
        """The symbol printed in front of an amount, e.g. `$`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[1]

    @property
    def currency_label(self) -> str:
        """The Currency cell on a printed sheet, e.g. `USD [ $ ]`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[2]

    @property
    def subtotal_usd(self) -> float:
        if self.computed_subtotal_usd is not None:
            return self.computed_subtotal_usd
        return sum(item.total_usd for item in self.items)

    @property
    def cif_value_usd(self) -> float:
        """Overrides CifMoneyLadder.cif_value_usd - a quotation's typed price
        is always the absolute FOB price (quantity_value * price_usd summed
        across every line, i.e. subtotal_usd, is the FOB invoice total), so
        unlike every other document here CIF is built UPWARDS from FOB by
        adding the charges rather than being the goods total on its own. This
        holds regardless of the shipping terms chosen - the terms only decide
        which charge fields are non-zero (see drops_sea_freight/drops_insurance),
        never the FOB total itself. cif_adjust_usd is the one place a manual
        typed-CIF-value override is allowed to land (see the form's
        subtotal-input handler); it is 0 on a quotation that was never
        overridden that way."""
        return self.subtotal_usd + self.charges_total + self.cif_adjust_usd

    @property
    def fob_value_usd(self) -> float:
        """Overrides CifMoneyLadder.fob_value_usd. A quotation's FOB value is
        simply the goods total (quantity x price, summed across every line) -
        it is never reduced by the discount or built up with charges, unlike
        the base ladder's version. The discount only ever comes off between
        CIF and invoice value (see invoice_value_usd); it does not touch this
        figure. This holds for every shipping term: under FOB terms nothing
        is added on top of it (sea freight/insurance are held at zero and CIF
        is not shown), and under CIF/CFR terms it is still the ex-charges
        value of the goods themselves."""
        return self.subtotal_usd


@dataclass
class PurchaseOrderItem:
    """One product line of a purchase order. Prices are INR - typically the
    ex-factory rate per BOX (price_per='BOX'), but a row can also price per
    its quantity unit (price_per=<unit>). total_inr is derived at save time
    from whichever basis the row uses."""
    id: Optional[int]
    purchase_order_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    hsn_code: Optional[str] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    quantity_value: float = 0
    unit: str = "SQM"
    price_inr: float = 0
    price_per: str = "BOX"
    total_inr: float = 0

    @staticmethod
    def from_row(row) -> "PurchaseOrderItem":
        return PurchaseOrderItem(
            id=row["id"],
            purchase_order_id=row["purchase_order_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            hsn_code=row["hsn_code"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            price_inr=row["price_inr"],
            price_per=row["price_per"],
            total_inr=row["total_inr"],
        )


@dataclass
class PurchaseOrder:
    """The next document after the Proforma Invoice in the client pipeline.
    Unlike the other documents, OUR company is the BUYER here and a supplier
    is the SELLER - so the header carries seller details instead of a
    consignee, and amounts are INR. Tax percentages are stored; every amount
    (tax, round-off, order value) is derived, never stored. The percentages
    themselves aren't typed in either - they follow from `purchase_type` plus
    the seller's GSTIN state code (see PurchaseOrderService._tax_percentages)."""
    id: Optional[int]
    company_id: int
    po_number: str
    po_date: str
    seller_name: str
    created_by: int
    lead_id: Optional[int] = None
    proforma_invoice_id: Optional[int] = None
    seller_supplier_id: Optional[int] = None
    seller_address: Optional[str] = None
    seller_pan: Optional[str] = None
    seller_gstin: Optional[str] = None
    seller_ref_no: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    container_details: Optional[str] = None
    delivery_time: Optional[str] = None
    advance_percent: Optional[str] = None
    payment_terms: Optional[str] = None
    remarks: Optional[str] = None
    igst_percent: float = 0
    cgst_percent: float = 0
    sgst_percent: float = 0
    purchase_type: str = DEFAULT_PURCHASE_TYPE  # key of PURCHASE_TYPES
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    proforma_invoice_number: Optional[str] = None  # populated by joined queries only
    # Currency the document is written in, picked from the Administration ->
    # Miscellaneous list and snapshotted so a later edit of that list can't
    # rewrite an issued sheet. Display information only - no conversion.
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    items: List[PurchaseOrderItem] = field(default_factory=list)
    computed_subtotal_inr: Optional[float] = None  # precomputed by list queries that don't load items

    @staticmethod
    def from_row(row) -> "PurchaseOrder":
        return PurchaseOrder(
            id=row["id"],
            company_id=row["company_id"],
            po_number=row["po_number"],
            po_date=row["po_date"],
            lead_id=row["lead_id"],
            proforma_invoice_id=row["proforma_invoice_id"],
            seller_supplier_id=row["seller_supplier_id"],
            seller_name=row["seller_name"],
            seller_address=row["seller_address"],
            seller_pan=row["seller_pan"],
            seller_gstin=row["seller_gstin"],
            seller_ref_no=row["seller_ref_no"],
            port_of_loading=row["port_of_loading"],
            port_of_discharge=row["port_of_discharge"],
            container_details=row["container_details"],
            delivery_time=row["delivery_time"],
            advance_percent=row["advance_percent"],
            payment_terms=row["payment_terms"],
            remarks=row["remarks"],
            igst_percent=row["igst_percent"],
            cgst_percent=row["cgst_percent"],
            sgst_percent=row["sgst_percent"],
            purchase_type=(row["purchase_type"] if "purchase_type" in row.keys() else None) or DEFAULT_PURCHASE_TYPE,
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
            proforma_invoice_number=row["proforma_invoice_number"] if "proforma_invoice_number" in row.keys() else None,
            computed_subtotal_inr=row["items_total"] if "items_total" in row.keys() else None,
            currency_code=row["currency_code"] if "currency_code" in row.keys() else None,
            currency_symbol=row["currency_symbol"] if "currency_symbol" in row.keys() else None,
        )

    @property
    def currency_name(self) -> str:
        """The currency's name, e.g. `USD` - what the money column headings
        and typed-amount labels read."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[0]

    @property
    def currency_prefix(self) -> str:
        """The symbol printed in front of an amount, e.g. `$`."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[1]

    @property
    def currency_label(self) -> str:
        """The Currency cell on a printed sheet, e.g. `USD [ $ ]`."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[2]

    @property
    def total_boxes(self) -> float:
        return sum(item.quantity_boxes or 0 for item in self.items)

    @property
    def total_quantity(self) -> float:
        return sum(item.quantity_value or 0 for item in self.items)

    @property
    def subtotal_inr(self) -> float:
        if self.computed_subtotal_inr is not None and not self.items:
            return self.computed_subtotal_inr
        return sum(item.total_inr for item in self.items)

    @property
    def igst_amount(self) -> float:
        return round(self.subtotal_inr * (self.igst_percent or 0) / 100, 2)

    @property
    def cgst_amount(self) -> float:
        return round(self.subtotal_inr * (self.cgst_percent or 0) / 100, 2)

    @property
    def sgst_amount(self) -> float:
        return round(self.subtotal_inr * (self.sgst_percent or 0) / 100, 2)

    @property
    def order_value_inr(self) -> float:
        """The final order value, rounded to the whole rupee (the round-off
        line on the printed PO bridges the difference)."""
        return float(round(self.subtotal_inr + self.igst_amount + self.cgst_amount + self.sgst_amount))

    @property
    def round_off_inr(self) -> float:
        gross = self.subtotal_inr + self.igst_amount + self.cgst_amount + self.sgst_amount
        return round(self.order_value_inr - gross, 2)


@dataclass
class PurchaseInvoiceItem:
    """One product line of a purchase invoice - same shape as
    PurchaseOrderItem, copied in from the linked purchase order at creation
    time so the invoice stays a self-contained record even if that
    purchase order is later edited or deleted."""
    id: Optional[int]
    purchase_invoice_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    hsn_code: Optional[str] = None
    quantity_boxes: Optional[float] = None
    quantity_value: float = 0
    unit: str = "SQM"
    price_inr: float = 0
    price_per: str = "BOX"
    total_inr: float = 0

    @staticmethod
    def from_row(row) -> "PurchaseInvoiceItem":
        return PurchaseInvoiceItem(
            id=row["id"],
            purchase_invoice_id=row["purchase_invoice_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            hsn_code=row["hsn_code"],
            quantity_boxes=row["quantity_boxes"],
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            price_inr=row["price_inr"],
            price_per=row["price_per"],
            total_inr=row["total_inr"],
        )


@dataclass
class PurchaseInvoice:
    """The last document in the pipeline: raised once a supplier's goods
    (against one of our purchase orders) actually arrive, carrying the
    supplier's own invoice/transport details. Unlike every other document
    type here, WE don't generate a PDF for this one - the supplier already
    sent their own invoice as a PDF (supplier_pdf_path); this record just
    saves its numbers alongside it. `invoice_number`/`invoice_date` are the
    SUPPLIER's own values as printed on that PDF; `purchase_invoice_number`
    is our own internal, auto-generated identifier, kept only for
    consistency with every other document type's numbering/version-history
    machinery. Discount/insurance/freight/tax/round-off are typed in
    directly (not derived) since they must match what the supplier actually
    charged, not what our own tax rules would compute."""
    id: Optional[int]
    company_id: int
    purchase_invoice_number: str
    invoice_number: str
    invoice_date: str
    seller_name: str
    created_by: int
    purchase_order_id: Optional[int] = None
    lead_id: Optional[int] = None
    seller_supplier_id: Optional[int] = None
    seller_address: Optional[str] = None
    seller_pan: Optional[str] = None
    seller_gstin: Optional[str] = None
    seller_ref_no: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    container_details: Optional[str] = None
    transporter_name: Optional[str] = None
    epcg_number: Optional[str] = None
    epcg_date: Optional[str] = None
    supplier_pdf_path: Optional[str] = None
    discount_amount: float = 0
    insurance_other: float = 0
    freight: float = 0
    igst_amount: float = 0
    cgst_amount: float = 0
    sgst_amount: float = 0
    round_off: float = 0
    remarks: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    purchase_order_number: Optional[str] = None  # populated by joined queries only
    # Currency the document is written in, picked from the Administration ->
    # Miscellaneous list and snapshotted so a later edit of that list can't
    # rewrite an issued sheet. Display information only - no conversion.
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    items: List[PurchaseInvoiceItem] = field(default_factory=list)
    vehicle_numbers: List[str] = field(default_factory=list)
    computed_subtotal_inr: Optional[float] = None  # precomputed by list queries that don't load items

    @staticmethod
    def from_row(row) -> "PurchaseInvoice":
        return PurchaseInvoice(
            id=row["id"],
            company_id=row["company_id"],
            purchase_invoice_number=row["purchase_invoice_number"],
            invoice_number=row["invoice_number"],
            invoice_date=row["invoice_date"],
            purchase_order_id=row["purchase_order_id"],
            lead_id=row["lead_id"],
            seller_supplier_id=row["seller_supplier_id"],
            seller_name=row["seller_name"],
            seller_address=row["seller_address"],
            seller_pan=row["seller_pan"],
            seller_gstin=row["seller_gstin"],
            seller_ref_no=row["seller_ref_no"],
            port_of_loading=row["port_of_loading"],
            port_of_discharge=row["port_of_discharge"],
            container_details=row["container_details"],
            transporter_name=row["transporter_name"],
            epcg_number=row["epcg_number"],
            epcg_date=row["epcg_date"],
            supplier_pdf_path=row["supplier_pdf_path"],
            discount_amount=row["discount_amount"],
            insurance_other=row["insurance_other"],
            freight=row["freight"],
            igst_amount=row["igst_amount"],
            cgst_amount=row["cgst_amount"],
            sgst_amount=row["sgst_amount"],
            round_off=row["round_off"],
            remarks=row["remarks"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
            purchase_order_number=row["purchase_order_number"] if "purchase_order_number" in row.keys() else None,
            computed_subtotal_inr=row["items_total"] if "items_total" in row.keys() else None,
            currency_code=row["currency_code"] if "currency_code" in row.keys() else None,
            currency_symbol=row["currency_symbol"] if "currency_symbol" in row.keys() else None,
        )

    @property
    def currency_name(self) -> str:
        """The currency's name, e.g. `USD` - what the money column headings
        and typed-amount labels read."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[0]

    @property
    def currency_prefix(self) -> str:
        """The symbol printed in front of an amount, e.g. `$`."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[1]

    @property
    def currency_label(self) -> str:
        """The Currency cell on a printed sheet, e.g. `USD [ $ ]`."""
        return currency_display(self.currency_code, self.currency_symbol, "INR", "₹")[2]

    @property
    def total_boxes(self) -> float:
        return sum(item.quantity_boxes or 0 for item in self.items)

    @property
    def total_quantity(self) -> float:
        return sum(item.quantity_value or 0 for item in self.items)

    @property
    def subtotal_inr(self) -> float:
        if self.computed_subtotal_inr is not None and not self.items:
            return self.computed_subtotal_inr
        return sum(item.total_inr for item in self.items)

    @property
    def invoice_value_inr(self) -> float:
        return round(
            self.subtotal_inr + self.freight + self.insurance_other
            + self.igst_amount + self.cgst_amount + self.sgst_amount
            - self.discount_amount + self.round_off,
            2,
        )


@dataclass
class PackingListItem:
    """One design of a product packed in a given quantity. product_name and
    design_name are stored snapshots - product_id/design_id are reference
    only, same as QuotationItem.product_id."""
    id: Optional[int]
    packing_list_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    design_id: Optional[int] = None
    design_name: Optional[str] = None
    hsn_code: Optional[str] = None
    box_per_pallet: Optional[float] = None
    pallets: Optional[float] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    pcs: Optional[float] = None
    quantity_value: float = 0
    unit: str = "SQM"
    net_weight_kg: Optional[float] = None
    gross_weight_kg: Optional[float] = None

    @staticmethod
    def from_row(row) -> "PackingListItem":
        return PackingListItem(
            id=row["id"],
            packing_list_id=row["packing_list_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            design_id=row["design_id"],
            design_name=row["design_name"],
            hsn_code=row["hsn_code"],
            box_per_pallet=row["box_per_pallet"],
            pallets=row["pallets"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            pcs=row["pcs"],
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            net_weight_kg=row["net_weight_kg"],
            gross_weight_kg=row["gross_weight_kg"],
        )


@dataclass
class PackingList:
    id: Optional[int]
    company_id: int
    packing_list_number: str
    packing_list_date: str
    consignee_name: str
    created_by: int
    lead_id: Optional[int] = None
    proforma_invoice_id: Optional[int] = None
    quotation_id: Optional[int] = None
    purchase_order_id: Optional[int] = None
    purchase_invoice_id: Optional[int] = None
    export_ref_no: Optional[str] = None
    buyer_order_no: Optional[str] = None
    other_reference: Optional[str] = None
    consignee_address: Optional[str] = None
    notify_name: Optional[str] = None
    notify_address: Optional[str] = None
    country_of_origin: Optional[str] = "INDIA"
    country_of_destination: Optional[str] = None
    vessel_flight: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    final_destination: Optional[str] = None
    container_details: Optional[str] = None
    terms_of_delivery: Optional[str] = None
    remarks: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    proforma_invoice_number: Optional[str] = None  # populated by joined queries only
    quotation_number: Optional[str] = None  # populated by joined queries only
    purchase_order_number: Optional[str] = None  # populated by joined queries only
    purchase_invoice_number: Optional[str] = None  # populated by joined queries only
    items: List[PackingListItem] = field(default_factory=list)

    @staticmethod
    def from_row(row) -> "PackingList":
        return PackingList(
            id=row["id"],
            company_id=row["company_id"],
            packing_list_number=row["packing_list_number"],
            packing_list_date=row["packing_list_date"],
            lead_id=row["lead_id"],
            proforma_invoice_id=row["proforma_invoice_id"],
            quotation_id=row["quotation_id"] if "quotation_id" in row.keys() else None,
            purchase_order_id=row["purchase_order_id"] if "purchase_order_id" in row.keys() else None,
            purchase_invoice_id=row["purchase_invoice_id"] if "purchase_invoice_id" in row.keys() else None,
            export_ref_no=row["export_ref_no"],
            buyer_order_no=row["buyer_order_no"],
            other_reference=row["other_reference"],
            consignee_name=row["consignee_name"],
            consignee_address=row["consignee_address"],
            notify_name=row["notify_name"],
            notify_address=row["notify_address"],
            country_of_origin=row["country_of_origin"],
            country_of_destination=row["country_of_destination"],
            vessel_flight=row["vessel_flight"],
            port_of_loading=row["port_of_loading"],
            port_of_discharge=row["port_of_discharge"],
            final_destination=row["final_destination"],
            container_details=row["container_details"],
            terms_of_delivery=row["terms_of_delivery"],
            remarks=row["remarks"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
            proforma_invoice_number=row["proforma_invoice_number"] if "proforma_invoice_number" in row.keys() else None,
            quotation_number=row["quotation_number"] if "quotation_number" in row.keys() else None,
            purchase_order_number=row["purchase_order_number"] if "purchase_order_number" in row.keys() else None,
            purchase_invoice_number=row["purchase_invoice_number"] if "purchase_invoice_number" in row.keys() else None,
        )

    @property
    def total_pallets(self) -> float:
        return sum(item.pallets or 0 for item in self.items)

    @property
    def total_boxes(self) -> float:
        return sum(item.quantity_boxes or 0 for item in self.items)

    @property
    def total_pcs(self) -> float:
        return sum(item.pcs or 0 for item in self.items)

    @property
    def total_quantity(self) -> float:
        return sum(item.quantity_value or 0 for item in self.items)

    @property
    def total_net_weight_kg(self) -> float:
        return sum(item.net_weight_kg or 0 for item in self.items)

    @property
    def total_gross_weight_kg(self) -> float:
        return sum(item.gross_weight_kg or 0 for item in self.items)


# A proforma invoice is a draft until it is explicitly confirmed. Confirming
# it freezes the document (only an admin can move it back to draft) and turns
# on the "purchase orders still to be placed" reminder, which stays up until
# every design on the PI's packing list has been placed, in full, on the
# packing list of some purchase order linked to that PI.
PROFORMA_STATUS_DRAFT = "draft"
PROFORMA_STATUS_CONFIRMED = "confirmed"
PROFORMA_STATUSES = [
    (PROFORMA_STATUS_DRAFT, "Draft"),
    (PROFORMA_STATUS_CONFIRMED, "Confirmed"),
]


@dataclass
class ProformaInvoiceItem:
    id: Optional[int]
    proforma_invoice_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    dimension_mm: Optional[str] = None
    hsn_code: Optional[str] = None
    surface: Optional[str] = None  # optional finish (GLOSSY / MATT / ...), drives the surface-grouped print view
    pallets: Optional[float] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    quantity_value: float = 0
    unit: str = "SQM"
    price_usd: float = 0
    total_usd: float = 0
    # Unused (kept so an old row still loads) - proforma invoices no longer
    # have an FOB-typed-price mode; price_usd is always the absolute price
    # the user typed. See ExportInvoiceItem.fob_price_usd, which has this now.
    fob_price_usd: Optional[float] = None

    @staticmethod
    def from_row(row) -> "ProformaInvoiceItem":
        return ProformaInvoiceItem(
            id=row["id"],
            proforma_invoice_id=row["proforma_invoice_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            dimension_mm=row["dimension_mm"],
            hsn_code=row["hsn_code"],
            surface=row["surface"] if "surface" in row.keys() else None,
            pallets=row["pallets"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            price_usd=row["price_usd"],
            total_usd=row["total_usd"],
            fob_price_usd=row["fob_price_usd"] if "fob_price_usd" in row.keys() else None,
        )


@dataclass
class ProformaInvoice(CifMoneyLadder):
    id: Optional[int]
    company_id: int
    invoice_number: str
    invoice_date: str
    consignee_name: str
    created_by: int
    lead_id: Optional[int] = None
    quotation_id: Optional[int] = None
    export_ref_no: Optional[str] = None
    buyer_order_no: Optional[str] = None
    other_reference: Optional[str] = None
    consignee_address: Optional[str] = None
    notify_name: Optional[str] = None
    notify_address: Optional[str] = None
    country_of_origin: Optional[str] = "INDIA"
    country_of_destination: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    final_destination: Optional[str] = None
    transhipment: Optional[str] = None
    partial_shipment: Optional[str] = None
    variation_in_qty: Optional[str] = None
    delivery_period: Optional[str] = None
    container_details: Optional[str] = None
    packing_details: Optional[str] = None  # e.g. "PALLATE" - same field as Quotation.packing_details
    terms_of_delivery: Optional[str] = None
    payment_terms: Optional[str] = None
    remarks: Optional[str] = None
    sea_freight: float = 0
    insurance: float = 0
    certification: float = 0
    other_charges: float = 0
    discount_amount: float = 0
    # Unused (kept so an old invoice's row still loads) - proforma invoices no
    # longer have an FOB-typed-price mode; see ExportInvoice.fob_pricing,
    # which has this now.
    fob_pricing: bool = False
    round_off: float = 0       # see Quotation.round_off
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc_code: Optional[str] = None
    bank_swift_code: Optional[str] = None
    bank_branch: Optional[str] = None
    bank_address: Optional[str] = None
    display_mode: str = "index"  # goods layout: 'index' (numbered) | 'surface' (grouped by category + surface)
    status: str = PROFORMA_STATUS_DRAFT  # 'draft' | 'confirmed' - see PROFORMA_STATUSES
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    # Currency the document is written in, picked from the Administration ->
    # Miscellaneous list and snapshotted so a later edit of that list can't
    # rewrite an issued sheet. Display information only - no conversion.
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    items: List[ProformaInvoiceItem] = field(default_factory=list)
    computed_subtotal_usd: Optional[float] = None  # precomputed by list queries that don't load items

    @staticmethod
    def from_row(row) -> "ProformaInvoice":
        return ProformaInvoice(
            id=row["id"],
            company_id=row["company_id"],
            invoice_number=row["invoice_number"],
            invoice_date=row["invoice_date"],
            lead_id=row["lead_id"],
            quotation_id=row["quotation_id"],
            export_ref_no=row["export_ref_no"],
            buyer_order_no=row["buyer_order_no"],
            other_reference=row["other_reference"],
            consignee_name=row["consignee_name"],
            consignee_address=row["consignee_address"],
            notify_name=row["notify_name"],
            notify_address=row["notify_address"],
            country_of_origin=row["country_of_origin"],
            country_of_destination=row["country_of_destination"],
            port_of_loading=row["port_of_loading"],
            port_of_discharge=row["port_of_discharge"],
            final_destination=row["final_destination"],
            transhipment=row["transhipment"],
            partial_shipment=row["partial_shipment"],
            variation_in_qty=row["variation_in_qty"],
            delivery_period=row["delivery_period"],
            container_details=row["container_details"],
            packing_details=row["packing_details"] if "packing_details" in row.keys() else None,
            terms_of_delivery=row["terms_of_delivery"],
            payment_terms=row["payment_terms"],
            remarks=row["remarks"],
            sea_freight=row["sea_freight"],
            insurance=row["insurance"],
            certification=row["certification"],
            other_charges=row["other_charges"],
            discount_amount=row["discount_amount"],
            fob_pricing=bool(row["fob_pricing"]) if "fob_pricing" in row.keys() else False,
            round_off=(row["round_off"] if "round_off" in row.keys() else 0) or 0,
            bank_name=row["bank_name"],
            bank_account_number=row["bank_account_number"],
            bank_ifsc_code=row["bank_ifsc_code"],
            bank_swift_code=row["bank_swift_code"],
            bank_branch=row["bank_branch"],
            bank_address=row["bank_address"],
            display_mode=(row["display_mode"] if "display_mode" in row.keys() else None) or "index",
            status=(row["status"] if "status" in row.keys() else None) or PROFORMA_STATUS_DRAFT,
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_name=row["created_by_name"] if "created_by_name" in row.keys() else None,
            computed_subtotal_usd=row["items_total"] if "items_total" in row.keys() else None,
            currency_code=row["currency_code"] if "currency_code" in row.keys() else None,
            currency_symbol=row["currency_symbol"] if "currency_symbol" in row.keys() else None,
        )

    @property
    def currency_name(self) -> str:
        """The currency's name, e.g. `USD` - what the money column headings
        and typed-amount labels read."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[0]

    @property
    def currency_prefix(self) -> str:
        """The symbol printed in front of an amount, e.g. `$`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[1]

    @property
    def currency_label(self) -> str:
        """The Currency cell on a printed sheet, e.g. `USD [ $ ]`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[2]

    @property
    def is_confirmed(self) -> bool:
        return self.status == PROFORMA_STATUS_CONFIRMED

    @property
    def status_label(self) -> str:
        return dict(PROFORMA_STATUSES).get(self.status, self.status)

    @property
    def subtotal_usd(self) -> float:
        if self.computed_subtotal_usd is not None:
            return self.computed_subtotal_usd
        return sum(item.total_usd for item in self.items)

    @property
    def cif_value_usd(self) -> float:
        """Overrides CifMoneyLadder.cif_value_usd - like Quotation, a proforma
        invoice's typed price is always the absolute FOB price (fob_pricing is
        hardcoded off - see _build_header - so apply_fob_uplift never touches
        it), so CIF is built UPWARDS from FOB by adding the charges rather
        than being the goods total on its own. This holds regardless of the
        shipping terms chosen - the terms only decide which charge fields are
        non-zero (see drops_sea_freight/drops_insurance), never the FOB total
        itself. Mirrors Quotation.cif_value_usd; a proforma invoice has no
        cif_adjust_usd equivalent (the printed/typed CIF figure is never
        manually overridden here)."""
        return self.subtotal_usd + self.charges_total

    @property
    def fob_value_usd(self) -> float:
        """Overrides CifMoneyLadder.fob_value_usd - the FOB value is simply
        the goods total (quantity x price, summed across every line), never
        reduced by the discount or rebuilt from the invoice value. Mirrors
        Quotation.fob_value_usd; see cif_value_usd above for why a proforma
        invoice's ladder runs upward like a quotation's rather than downward
        like the base CifMoneyLadder."""
        return self.subtotal_usd


EXPORT_TAX_MODE_IGST = "igst"
EXPORT_TAX_MODE_LUT = "lut"
EXPORT_TAX_MODES = [
    (EXPORT_TAX_MODE_IGST, "With Payment of IGST"),
    (EXPORT_TAX_MODE_LUT, "Without Payment of IGST under LUT"),
]
EXPORT_LOADING_BUFFER = "buffer"
EXPORT_LOADING_SELF_SEALING = "self_sealing"
EXPORT_LOADING_TYPES = [(EXPORT_LOADING_BUFFER, "Buffer loading"), (EXPORT_LOADING_SELF_SEALING, "Self-sealing")]


@dataclass
class ExportInvoiceItem:
    """One goods line on an Export Invoice - same shape as
    ProformaInvoiceItem, plus a per-line igst_percent snapshot so the summed
    tax is computed per-product (each HSN taxes differently) and stays stable
    against later catalog edits."""
    id: Optional[int]
    export_invoice_id: Optional[int]
    sr_no: int
    product_name: str
    product_id: Optional[int] = None
    dimension_mm: Optional[str] = None
    hsn_code: Optional[str] = None
    surface: Optional[str] = None
    pallets: Optional[float] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    quantity_value: float = 0
    unit: str = "SQM"
    price_usd: float = 0
    total_usd: float = 0
    igst_percent: float = 0
    # What was typed under FOB pricing (NULL otherwise) - see
    # ExportInvoice.fob_pricing / apply_fob_uplift in services.py.
    fob_price_usd: Optional[float] = None

    @property
    def tax_usd(self) -> float:
        return (self.total_usd or 0) * (self.igst_percent or 0) / 100.0

    @staticmethod
    def from_row(row) -> "ExportInvoiceItem":
        return ExportInvoiceItem(
            id=row["id"],
            export_invoice_id=row["export_invoice_id"],
            sr_no=row["sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            dimension_mm=row["dimension_mm"],
            hsn_code=row["hsn_code"],
            surface=row["surface"] if "surface" in row.keys() else None,
            pallets=row["pallets"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"] if "quantity_unit" in row.keys() else "PCS",
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            price_usd=row["price_usd"],
            total_usd=row["total_usd"],
            igst_percent=row["igst_percent"] if "igst_percent" in row.keys() else 0,
            fob_price_usd=row["fob_price_usd"] if "fob_price_usd" in row.keys() else None,
        )


@dataclass
class ExportInvoice(CifMoneyLadder):
    """The customer/customs-facing Export Invoice at the buyer end of the
    pipeline. References one or more Proforma Invoices (many-to-many via
    proforma_invoice_ids). Goods are prefilled from those PIs then edited.
    Tax is computed per-product and, per tax_mode ("Supply meant for"), either
    charged as IGST or zero-rated ("Without Payment of IGST under LUT");
    the exchange rate is manual and admin-locked once set. Buyer Order No &
    Date is a single field shared by every linked PI. The several child
    lists (containers / container_details / purchase_details) back the
    front-page and page-2 annexure blocks."""
    id: Optional[int]
    company_id: int
    export_invoice_number: str
    invoice_date: str
    consignee_name: str
    created_by: int
    lead_id: Optional[int] = None
    consignee_address: Optional[str] = None
    notify_name: Optional[str] = None
    notify_address: Optional[str] = None
    country_of_origin: Optional[str] = "INDIA"
    country_of_destination: Optional[str] = None
    place_of_receipt: Optional[str] = None
    pre_carriage_by: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    final_destination: Optional[str] = None
    nature_of_contract: Optional[str] = None
    payment_terms: Optional[str] = None
    buyer_order_no: Optional[str] = None
    buyer_order_date: Optional[str] = None
    # Only the government-scheme LINE of the printed "Export Under" block -
    # blank means "use OurCompany.government_schemes as it stands today". The
    # block's other lines (the SUPPLY MEANT FOR EXPORT heading from tax_mode,
    # the EPCG licence below, the company's LUT number) are composed by the
    # sheets at print time, so they can never go stale.
    export_under: Optional[str] = None
    epcg_number: Optional[str] = None
    epcg_date: Optional[str] = None
    loading_type: str = EXPORT_LOADING_SELF_SEALING
    tax_mode: str = EXPORT_TAX_MODE_IGST
    exchange_rate: float = 0
    sea_freight: float = 0
    insurance: float = 0
    certification: float = 0
    other_charges: float = 0
    discount_amount: float = 0
    # "Prices typed above are FOB" checkbox on the form - see
    # is_fob_pricing / apply_fob_uplift in services.py.
    fob_pricing: bool = False
    round_off: float = 0       # see Quotation.round_off
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc_code: Optional[str] = None
    bank_swift_code: Optional[str] = None
    bank_branch: Optional[str] = None
    bank_address: Optional[str] = None
    authorised_person_name: Optional[str] = None
    authorised_person_designation: Optional[str] = None
    self_sealing_declaration: Optional[str] = None
    shipping_bill_pdf_path: Optional[str] = None
    examination_date: Optional[str] = None
    location_code_08b: Optional[str] = None
    booking_no: Optional[str] = None
    vessel_voyage_no: Optional[str] = None  # printed in the "Vessel / Flight Name & No" cell of both sheets
    eway_bill_no: Optional[str] = None  # printed on the Tax Invoice attachment only
    eway_bill_date: Optional[str] = None
    # The Tax Invoice attachment's own number/date. Blank means "the export
    # invoice's own" - see tax_invoice_number_printed / _date_printed below.
    tax_invoice_number: Optional[str] = None
    tax_invoice_date: Optional[str] = None
    # The VGM declaration's manual-entry cells. Blank means "use the default"
    # - see the vgm_*_printed properties below.
    vgm_signatory: Optional[str] = None
    vgm_contact_24x7: Optional[str] = None
    vgm_weighing_method: Optional[str] = None
    vgm_cargo_type: Optional[str] = None
    vgm_hazardous_details: Optional[str] = None
    # The commercial invoice packing list's only two typed cells.
    bill_of_lading_no: Optional[str] = None
    bill_of_lading_date: Optional[str] = None
    issuing_authority: Optional[str] = None
    issuing_authority_address: Optional[str] = None
    permission_no: Optional[str] = None
    permission_date: Optional[str] = None
    permission_expiry: Optional[str] = None
    permission_is_one_time: bool = False  # printed as "One Time" instead of the (blank) expiry date
    manufacturer_name: Optional[str] = None
    manufacturer_address: Optional[str] = None
    stuffing_location: Optional[str] = None  # "Stuff At" address, printed on the export packing list
    remarks: Optional[str] = None
    total_net_weight_kg: Optional[float] = None  # front-page weight totals, typed not summed from containers
    total_gross_weight_kg: Optional[float] = None
    shipping_bill_no: Optional[str] = None
    shipping_bill_date: Optional[str] = None  # Annexure-C header: Shipping Bill Date
    # Currency printed on the invoice + packing list, picked from the
    # Administration -> Miscellaneous currency list and snapshotted here so a
    # later edit of that list can't rewrite an already-printed sheet.
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    status: str = "active"  # no draft/confirmed lock; kept for interface symmetry with other documents
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    items: List[ExportInvoiceItem] = field(default_factory=list)
    proforma_invoice_ids: List[int] = field(default_factory=list)
    containers: List[dict] = field(default_factory=list)  # [{container_type, container_count}]
    container_details: List[dict] = field(default_factory=list)  # [{container_no, line_seal_no, rfid_seal_no, vehicle_no, lr_no, transporter_name, max_permitted_weight, tare_weight_kg, gross_weight, net_weight}]
    purchase_details: List[dict] = field(default_factory=list)  # [{supplier_gstin, supplier_invoice_no}]
    linked_proformas: List[dict] = field(default_factory=list)  # [{id, invoice_number, invoice_date}] joined for display
    computed_subtotal_usd: Optional[float] = None  # precomputed by list queries that don't load items

    @staticmethod
    def from_row(row) -> "ExportInvoice":
        keys = row.keys()

        def g(name, default=None):
            return row[name] if name in keys else default

        return ExportInvoice(
            id=row["id"],
            company_id=row["company_id"],
            export_invoice_number=row["export_invoice_number"],
            invoice_date=row["invoice_date"],
            lead_id=g("lead_id"),
            consignee_name=row["consignee_name"],
            consignee_address=g("consignee_address"),
            notify_name=g("notify_name"),
            notify_address=g("notify_address"),
            country_of_origin=g("country_of_origin"),
            country_of_destination=g("country_of_destination"),
            place_of_receipt=g("place_of_receipt"),
            pre_carriage_by=g("pre_carriage_by"),
            port_of_loading=g("port_of_loading"),
            port_of_discharge=g("port_of_discharge"),
            final_destination=g("final_destination"),
            nature_of_contract=g("nature_of_contract"),
            payment_terms=g("payment_terms"),
            buyer_order_no=g("buyer_order_no"),
            buyer_order_date=g("buyer_order_date"),
            export_under=g("export_under"),
            epcg_number=g("epcg_number"),
            epcg_date=g("epcg_date"),
            loading_type=g("loading_type") or EXPORT_LOADING_SELF_SEALING,
            tax_mode=g("tax_mode") or EXPORT_TAX_MODE_IGST,
            exchange_rate=g("exchange_rate", 0) or 0,
            sea_freight=g("sea_freight", 0) or 0,
            insurance=g("insurance", 0) or 0,
            certification=g("certification", 0) or 0,
            other_charges=g("other_charges", 0) or 0,
            discount_amount=g("discount_amount", 0) or 0,
            fob_pricing=bool(g("fob_pricing", 0)),
            round_off=g("round_off", 0) or 0,
            bank_name=g("bank_name"),
            bank_account_number=g("bank_account_number"),
            bank_ifsc_code=g("bank_ifsc_code"),
            bank_swift_code=g("bank_swift_code"),
            bank_branch=g("bank_branch"),
            bank_address=g("bank_address"),
            authorised_person_name=g("authorised_person_name"),
            authorised_person_designation=g("authorised_person_designation"),
            self_sealing_declaration=g("self_sealing_declaration"),
            shipping_bill_pdf_path=g("shipping_bill_pdf_path"),
            examination_date=g("examination_date"),
            location_code_08b=g("location_code_08b"),
            booking_no=g("booking_no"),
            vessel_voyage_no=g("vessel_voyage_no"),
            eway_bill_no=g("eway_bill_no"),
            eway_bill_date=g("eway_bill_date"),
            tax_invoice_number=g("tax_invoice_number"),
            tax_invoice_date=g("tax_invoice_date"),
            vgm_signatory=g("vgm_signatory"),
            vgm_contact_24x7=g("vgm_contact_24x7"),
            vgm_weighing_method=g("vgm_weighing_method"),
            vgm_cargo_type=g("vgm_cargo_type"),
            vgm_hazardous_details=g("vgm_hazardous_details"),
            bill_of_lading_no=g("bill_of_lading_no"),
            bill_of_lading_date=g("bill_of_lading_date"),
            issuing_authority=g("issuing_authority"),
            issuing_authority_address=g("issuing_authority_address"),
            permission_no=g("permission_no"),
            permission_date=g("permission_date"),
            permission_expiry=g("permission_expiry"),
            permission_is_one_time=bool(g("permission_is_one_time", 0)),
            manufacturer_name=g("manufacturer_name"),
            manufacturer_address=g("manufacturer_address"),
            stuffing_location=g("stuffing_location"),
            remarks=g("remarks"),
            total_net_weight_kg=g("total_net_weight_kg"),
            total_gross_weight_kg=g("total_gross_weight_kg"),
            shipping_bill_no=g("shipping_bill_no"),
            shipping_bill_date=g("shipping_bill_date"),
            currency_code=g("currency_code"),
            currency_symbol=g("currency_symbol"),
            created_by=row["created_by"],
            created_at=g("created_at"),
            updated_at=g("updated_at"),
            created_by_name=g("created_by_name"),
            computed_subtotal_usd=row["items_total"] if "items_total" in keys else None,
        )

    @property
    def currency_name(self) -> str:
        """The currency's name, e.g. `USD` - what the money column headings
        and typed-amount labels read."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[0]

    @property
    def currency_prefix(self) -> str:
        """The symbol printed in front of an amount, e.g. `$`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[1]

    @property
    def currency_label(self) -> str:
        """The Currency cell on a printed sheet, e.g. `USD [ $ ]`."""
        return currency_display(self.currency_code, self.currency_symbol, "USD", "$")[2]

    @property
    def subtotal_usd(self) -> float:
        if self.computed_subtotal_usd is not None:
            return self.computed_subtotal_usd
        return sum(item.total_usd for item in self.items)

    @property
    def _fob_price_is_absolute(self) -> bool:
        """True when price_usd (or, if the uplift ran, fob_price_usd) is
        already the true FOB figure on its own - either because the uplift
        actually folded the charges into the CIF price and stashed the
        original typed price (fob_pricing on), or because there's no CIF
        concept to begin with (FOB terms - see is_fob_terms in app/utils.py).
        False means the opposite: CIF/CFR terms where the price was typed
        directly as the CIF price and never run through "Calculate CIF
        pricing" - the app's default assumption (see CifMoneyLadder's
        docstring: "every price in this app is a CIF price") - so FOB has to
        be worked out the base ladder's way, by stripping the charges back
        out of the CIF total instead of trusting price_usd as-is."""
        return bool(self.fob_pricing) or is_fob_terms(self.nature_of_contract)

    @property
    def fob_value_usd(self) -> float:
        """Overrides CifMoneyLadder.fob_value_usd.

        When _fob_price_is_absolute, the FOB value is quantity x the ORIGINAL
        typed price per line - fob_price_usd when the uplift ran (it mutated
        price_usd into the CIF price and stashed what was actually typed
        there), falling back to price_usd itself when it never did but the
        terms are FOB (price_usd IS still the typed FOB price in that case,
        nothing ever adjusts it). Never reduced by the discount or the four
        charges then - it only moves when a line's typed price or quantity
        changes, same as Quotation.fob_value_usd/ProformaInvoice.fob_value_usd.

        Otherwise (genuine CIF/CFR terms, uplift never run) falls back to
        exactly the base CifMoneyLadder formula, computed directly against
        cif_value_usd rather than via invoice_value_usd/super() - both of
        those are ALSO overridden on this class and would recurse back into
        _fob_price_is_absolute's other branch otherwise."""
        if self._fob_price_is_absolute:
            return sum(
                (item.quantity_value or 0)
                * (item.fob_price_usd if item.fob_price_usd is not None else (item.price_usd or 0))
                for item in self.items
            )
        return self.cif_value_usd - self.discount_amount - self.charges_total

    @property
    def invoice_value_usd(self) -> float:
        """Overrides CifMoneyLadder.invoice_value_usd to match fob_value_usd
        above - same branch, so the two stay a consistent ladder either way.

        When _fob_price_is_absolute, invoice value is built UP from FOB
        rather than down from CIF, since fob_value_usd no longer moves with
        the charges there: FOB value + every charge - discount. Whenever the
        uplift actually ran, subtotal_usd IS fob_value_usd + charges_total by
        construction (apply_fob_uplift spreads exactly charges_total across
        the lines), so this lands on the exact same figure as the base
        cif_value_usd - discount_amount would - no behaviour change for that
        case. The fix only bites under FOB terms (sea_freight and insurance
        are held at zero there, but certification/other_charges are not -
        see drops_sea_freight/drops_insurance in app/utils.py): previously
        those still-typed charges vanished from invoice_value_usd entirely
        because subtotal_usd never carried them and fob_value_usd (rightly)
        doesn't either; now they always reach the buyer's payable figure.

        Otherwise (genuine CIF/CFR terms, uplift never run) falls back to
        exactly the base formula - cif_value_usd - discount_amount - since
        the price is assumed CIF-inclusive already and nothing needs adding
        back on."""
        if self._fob_price_is_absolute:
            return self.fob_value_usd + self.charges_total - self.discount_amount
        return self.cif_value_usd - self.discount_amount

    # The two persisted columns export_invoices.cnf_value / .fob_value are
    # written from these on every save (nothing reads them back - they exist so
    # the figures are queryable outside the app). Both are just the ladder in
    # CifMoneyLadder under this document's own names: CNF and CIF are the same
    # value here, and FOB is the ladder's bottom line.
    @property
    def cnf_value(self) -> float:
        """Goods total, before the charges are stripped out to reach FOB."""
        return self.cif_value_usd

    @property
    def fob_value(self) -> float:
        """Quantity x the typed FOB price, summed - see fob_value_usd."""
        return self.fob_value_usd

    # ---- FOB-priced view of the goods lines -----------------------------
    # The customer's copy of the commercial invoice quotes an FOB rate rather
    # than the CIF rate every other document shows. This is the exact inverse
    # of the quotation's FOB uplift (see v39): the gap between CIF and FOB -
    # the four charges plus the discount - is spread uniformly over the total
    # alternate quantity and taken off each line's rate.
    def fob_priced_lines(self) -> List[dict]:
        """[{item, rate, total}] - each goods line at its FOB rate.

        The rate is rounded to the two decimals the sheet prints and the line
        total follows FROM the rounded rate, so a customer multiplying the
        printed rate by the printed quantity gets the printed total. That
        makes the FOB and CIF figures on this sheet differ very slightly from
        the exact ladder elsewhere - the price of a sheet that foots.

        The reduction is worked out directly from charges_total and
        discount_amount rather than as `cif_value_usd - fob_value_usd`: since
        the fob_value_usd fix, that property is the pure goods total
        (quantity x the originally typed price), ignoring the discount AND
        the four charges, so the old subtraction no longer recovers "the four
        charges plus the discount" this view intentionally spreads. This is
        numerically identical to what that subtraction used to yield (back
        when fob_value_usd was cif_value_usd - discount_amount -
        charges_total), so this view's own figures stay byte-for-byte
        unchanged by that fix."""
        total_qty = sum(i.quantity_value or 0 for i in self.items)
        reduction = ((self.charges_total + self.discount_amount) / total_qty) if total_qty else 0
        lines = []
        for item in self.items:
            rate = round((item.price_usd or 0) - reduction, 2)
            lines.append({
                "item": item,
                "rate": rate,
                "total": round(rate * (item.quantity_value or 0), 2),
            })
        return lines

    @property
    def fob_priced_total(self) -> float:
        """What the FOB-priced goods column adds up to."""
        return round(sum(line["total"] for line in self.fob_priced_lines()), 2)

    @property
    def fob_priced_cif_total(self) -> float:
        """The CIF total built back UP from the FOB-priced column: the charges
        and the discount that were spread out are added back on. Equals
        cif_value_usd but for the rate rounding above - the gap between the two
        is `fob_priced_round_off`, which the sheet prints as its own row."""
        return round(self.fob_priced_total + self.charges_total + self.discount_amount, 2)

    @property
    def fob_priced_round_off(self) -> float:
        """The cents the FOB-priced column can't carry.

        `fob_priced_lines` rounds each rate to the two decimals the sheet
        prints and takes the line total FROM that rounded rate, so the column
        adds up to a hair either side of the exact ladder. This is that hair.
        Printed as its own ROUND-OFF row, it lets the customer's copy close on
        the SAME cif_value_usd every other document quotes while every column
        on it still multiplies and adds out - the same bargain
        Quotation.round_off strikes for FOB-typed prices."""
        return round(self.cif_value_usd - self.fob_priced_cif_total, 2)

    @property
    def tax_invoice_number_printed(self) -> str:
        """What the Tax Invoice attachment prints in its Invoice No cell. Its
        own number once given one, otherwise this export invoice's - a tax
        invoice starts out numbered exactly as its parent."""
        return self.tax_invoice_number or self.export_invoice_number

    @property
    def tax_invoice_date_printed(self) -> str:
        """As above, for the Invoice Date cell."""
        return self.tax_invoice_date or self.invoice_date

    # ---- VGM declaration defaults ---------------------------------------
    # The manual-entry cells are optional; each falls back to what the app
    # already knows (or to the value the reference form carries), so the sheet
    # is complete before anyone opens its edit form.
    VGM_DEFAULT_WEIGHING_METHOD = "METHOD-1"
    VGM_DEFAULT_CARGO_TYPE = "NORMAL"
    VGM_DEFAULT_HAZARDOUS_DETAILS = "N/A"

    def vgm_signatory_printed(self, company=None) -> str:
        """Falls back to this invoice's own authorised signatory, worded the
        way the sheets already word it ('<name> <designation> of <company>')."""
        if self.vgm_signatory:
            return self.vgm_signatory
        if not self.authorised_person_name:
            return "-"
        parts = [self.authorised_person_name]
        if self.authorised_person_designation:
            parts.append(self.authorised_person_designation)
        if company and company.company_name:
            parts.append(f"of {company.company_name.upper()}")
        return " ".join(parts)

    @property
    def vgm_weighing_method_printed(self) -> str:
        return self.vgm_weighing_method or self.VGM_DEFAULT_WEIGHING_METHOD

    @property
    def vgm_cargo_type_printed(self) -> str:
        return self.vgm_cargo_type or self.VGM_DEFAULT_CARGO_TYPE

    @property
    def vgm_hazardous_details_printed(self) -> str:
        return self.vgm_hazardous_details or self.VGM_DEFAULT_HAZARDOUS_DETAILS

    def in_inr(self, value: float) -> float:
        """Any of this invoice's own-currency figures at its own exchange
        rate. The Tax Invoice attachment prints the WHOLE money ladder in INR,
        so the conversion lives here once rather than being re-multiplied per
        row in a template."""
        return (value or 0) * (self.exchange_rate or 0)

    @property
    def invoice_value_inr(self) -> float:
        return self.in_inr(self.invoice_value_usd)

    @property
    def tax_total_inr(self) -> float:
        """Per-product tax, summed. Each line's USD total is converted to INR
        at the invoice's exchange rate then taxed at that product's own IGST
        percentage - so a mixed-HSN invoice totals each line separately."""
        rate = self.exchange_rate or 0
        return sum((item.total_usd or 0) * rate * (item.igst_percent or 0) / 100.0 for item in self.items)

    @property
    def igst_amount_inr(self) -> float:
        """Zero-rated (LUT) supplies carry no IGST - only 'With Payment of
        IGST' actually charges the summed per-product tax."""
        return self.tax_total_inr if self.tax_mode == EXPORT_TAX_MODE_IGST else 0

    @property
    def tax_mode_label(self) -> str:
        return dict(EXPORT_TAX_MODES).get(self.tax_mode, self.tax_mode)

    @property
    def loading_type_label(self) -> str:
        return dict(EXPORT_LOADING_TYPES).get(self.loading_type, self.loading_type)

    @property
    def total_containers(self) -> int:
        return sum(int(c.get("container_count") or 0) for c in self.containers)

    @property
    def container_types_expanded(self) -> List[str]:
        """The booked Container Details list flattened to one TYPE per
        physical container - `2 x 20FT FCL` becomes ['20FT FCL', '20FT FCL'].

        That total is what drives how many section-11B rows the form asks for,
        so this list lines up with `container_details` by position, which is
        how the VGM attachment labels each container's size."""
        types: List[str] = []
        for c in self.containers:
            types += [c.get("container_type") or ""] * int(c.get("container_count") or 0)
        return types

    @property
    def top_costliest_items(self) -> List[ExportInvoiceItem]:
        """Section 09 lists the four costliest product lines (by line total)."""
        return sorted(self.items, key=lambda i: i.total_usd or 0, reverse=True)[:4]


@dataclass
class ExportPackingListItem:
    """One (container x goods line) allocation on an Export Packing List:
    `quantity_boxes` boxes of the export invoice's goods line
    `invoice_item_sr_no` loaded into the physical container
    `container_sr_no`. Every other quantity column is derived from the boxes
    (see ExportPackingListService), and the container identity is snapshotted
    off the invoice's section-11B row so a later 11B re-order can't silently
    rewrite an already-printed sheet."""
    id: Optional[int]
    export_packing_list_id: Optional[int]
    sr_no: int
    product_name: str
    container_sr_no: int = 1
    container_no: Optional[str] = None
    seal_no: Optional[str] = None
    rfid_seal_no: Optional[str] = None
    invoice_item_sr_no: Optional[int] = None
    product_id: Optional[int] = None
    group_label: Optional[str] = None
    hsn_code: Optional[str] = None
    pallets: Optional[float] = None
    quantity_boxes: Optional[float] = None
    quantity_unit: str = "PCS"
    quantity_value: float = 0
    unit: str = "SQM"
    net_weight_kg: Optional[float] = None
    gross_weight_kg: Optional[float] = None

    @staticmethod
    def from_row(row) -> "ExportPackingListItem":
        return ExportPackingListItem(
            id=row["id"],
            export_packing_list_id=row["export_packing_list_id"],
            sr_no=row["sr_no"],
            container_sr_no=row["container_sr_no"],
            container_no=row["container_no"],
            seal_no=row["seal_no"],
            rfid_seal_no=row["rfid_seal_no"],
            invoice_item_sr_no=row["invoice_item_sr_no"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            group_label=row["group_label"],
            hsn_code=row["hsn_code"],
            pallets=row["pallets"],
            quantity_boxes=row["quantity_boxes"],
            quantity_unit=row["quantity_unit"],
            quantity_value=row["quantity_value"],
            unit=row["unit"],
            net_weight_kg=row["net_weight_kg"],
            gross_weight_kg=row["gross_weight_kg"],
        )


@dataclass
class ExportPackingList:
    """The EXPORT PACKING LIST that accompanies an Export Invoice - exactly
    one per invoice, generated automatically whenever that invoice is saved
    and never created or edited on its own. It owns nothing but the
    container allocation: the whole printed header (consigner, consignee,
    ports, bank, declarations, EPCG, self-sealing block) comes from
    `invoice`, which the repository joins in."""
    id: Optional[int]
    company_id: int
    export_invoice_id: int
    packing_list_number: str
    packing_list_date: str
    created_by: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by_name: Optional[str] = None  # populated by joined queries only
    items: List[ExportPackingListItem] = field(default_factory=list)
    invoice: Optional[ExportInvoice] = None  # the parent, loaded by get_by_id
    export_invoice_number: Optional[str] = None  # carried by list queries, which don't load the parent

    @staticmethod
    def from_row(row) -> "ExportPackingList":
        keys = row.keys()
        return ExportPackingList(
            id=row["id"],
            company_id=row["company_id"],
            export_invoice_id=row["export_invoice_id"],
            packing_list_number=row["packing_list_number"],
            packing_list_date=row["packing_list_date"],
            created_by=row["created_by"],
            created_at=row["created_at"] if "created_at" in keys else None,
            updated_at=row["updated_at"] if "updated_at" in keys else None,
            created_by_name=row["created_by_name"] if "created_by_name" in keys else None,
            export_invoice_number=row["export_invoice_number"] if "export_invoice_number" in keys else None,
        )

    # ---- totals (the sheet's bottom row) --------------------------------
    @property
    def total_pallets(self) -> float:
        return sum(i.pallets or 0 for i in self.items)

    @property
    def total_boxes(self) -> float:
        return sum(i.quantity_boxes or 0 for i in self.items)

    @property
    def total_quantity(self) -> float:
        return sum(i.quantity_value or 0 for i in self.items)

    @property
    def total_net_weight(self) -> float:
        return sum(i.net_weight_kg or 0 for i in self.items)

    @property
    def total_gross_weight(self) -> float:
        return sum(i.gross_weight_kg or 0 for i in self.items)

    @property
    def container_count(self) -> int:
        return len({i.container_sr_no for i in self.items})

    @property
    def container_totals(self) -> dict:
        """container_sr_no -> {net_weight_kg, gross_weight_kg, pallets,
        quantity_boxes, quantity_value} summed across every goods line loaded
        into that physical container. Lets the Export Invoice's own 11B table
        show each container's actual weight/pallets/boxes (from what was typed
        into the packing list's container split) instead of separately
        hand-typed figures, and gives the BL draft its per-container line."""
        totals: dict = {}
        for i in self.items:
            t = totals.setdefault(i.container_sr_no, {
                "net_weight_kg": 0.0, "gross_weight_kg": 0.0, "pallets": 0.0,
                "quantity_boxes": 0.0, "quantity_value": 0.0,
            })
            t["net_weight_kg"] += i.net_weight_kg or 0
            t["gross_weight_kg"] += i.gross_weight_kg or 0
            t["pallets"] += i.pallets or 0
            t["quantity_boxes"] += i.quantity_boxes or 0
            t["quantity_value"] += i.quantity_value or 0
        return totals

    @property
    def printed_containers(self) -> List[dict]:
        """The sheet's body, ready to render: one entry per physical
        container, each holding the flat run of item rows printed beside its
        (rowspan-ed) Container No / Seal No / RFID cells. Each row carries its
        own product name and HSN code, so designs are listed individually
        rather than banded under a shared group heading."""
        containers: List[dict] = []
        current = None
        for item in self.items:
            key = (item.container_sr_no, item.container_no or "", item.seal_no or "", item.rfid_seal_no or "")
            if current is None or current["key"] != key:
                current = {
                    "key": key, "container_sr_no": item.container_sr_no, "container_no": item.container_no,
                    "seal_no": item.seal_no, "rfid_seal_no": item.rfid_seal_no, "rows": [],
                }
                containers.append(current)
            current["rows"].append({"kind": "item", "item": item})
        for c in containers:
            c["rowspan"] = len(c["rows"])
        return containers

    @property
    def container_rows(self) -> List[dict]:
        """One row per physical container - container_no/seal_no/rfid_seal_no
        (snapshotted identity, from that container's first item) alongside
        every quantity summed across the goods lines loaded into it (via
        `container_totals`). Feeds the Export Invoice's standalone Annexure-C
        container table (section 11), which reads six of these keys, and the
        BL draft's container table, which reads seven."""
        totals = self.container_totals
        rows: List[dict] = []
        seen = set()
        for item in self.items:
            if item.container_sr_no in seen:
                continue
            seen.add(item.container_sr_no)
            t = totals.get(item.container_sr_no, {})
            rows.append({
                "sr_no": item.container_sr_no,
                "container_no": item.container_no,
                "seal_no": item.seal_no,
                "rfid_seal_no": item.rfid_seal_no,
                "pallets": t.get("pallets", 0.0),
                "quantity_boxes": t.get("quantity_boxes", 0.0),
                "quantity_value": t.get("quantity_value", 0.0),
                "gross_weight_kg": t.get("gross_weight_kg", 0.0),
                "net_weight_kg": t.get("net_weight_kg", 0.0),
            })
        return rows


@dataclass
class DocumentVersion:
    """One past-or-current snapshot of a Quotation/ProformaInvoice/PackingList,
    taken on every create/update. `snapshot` is that document's full
    dataclass state (header fields + items) serialized as JSON - admin-only,
    read-only history, never itself editable."""
    id: Optional[int]
    company_id: int
    document_type: str
    document_id: int
    version_number: int
    document_number: str
    snapshot: dict
    changed_by: int
    created_at: Optional[str] = None
    changed_by_name: Optional[str] = None  # populated by joined queries only

    @staticmethod
    def from_row(row) -> "DocumentVersion":
        return DocumentVersion(
            id=row["id"],
            company_id=row["company_id"],
            document_type=row["document_type"],
            document_id=row["document_id"],
            version_number=row["version_number"],
            document_number=row["document_number"],
            snapshot=json.loads(row["snapshot"]),
            changed_by=row["changed_by"],
            created_at=row["created_at"],
            changed_by_name=row["changed_by_name"] if "changed_by_name" in row.keys() else None,
        )

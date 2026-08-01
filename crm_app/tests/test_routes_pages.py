"""
Broader route coverage: every major list/detail/form page renders, key POST
flows work end-to-end through HTTP, and admin-only pages stay admin-only.

These are the tests that catch a broken template, a renamed url_for endpoint,
or a route/service signature drift - things the service-level tests can't see.
"""

import pytest


# --------------------------------------------------------------------------
# Helpers that build data through the app's own container
# --------------------------------------------------------------------------
@pytest.fixture
def admin_ctx(app, client):
    """Logged-in admin plus a handle on the container, for seeding data."""
    container = app.container
    tenant = container.tenant_repo.create("Page Co", "page-co")
    admin = container.auth_service.create_user(
        tenant.id, "pageadmin", "page-pass-1", "Page Admin", "admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin.id
    return client, container, admin, tenant.id


@pytest.fixture
def employee_ctx(app, client):
    container = app.container
    tenant = container.tenant_repo.create("Emp Co", "emp-co2")
    emp = container.auth_service.create_user(
        tenant.id, "pageemp", "emp-pass-1", "Page Emp", "employee")
    with client.session_transaction() as sess:
        sess["user_id"] = emp.id
    return client, container, emp, tenant.id


def new_lead(container, user):
    return container.lead_service.create_lead(
        user, "Acme Buyer", "123", "a@x.com", None, None, None,
        [{"name": "Bob", "is_primary": True}])


# ==========================================================================
# List pages render
# ==========================================================================
class TestListPages:
    @pytest.mark.parametrize("path", [
        "/",
        "/leads/",
        "/buyers/",
        "/products/",
        "/quotations/",
        "/proforma-invoices/",
        "/purchase-orders/",
        "/packing-lists/",
        "/reports/",
        "/account",   # profile_bp is mounted at /account
    ])
    def test_page_renders_for_admin(self, admin_ctx, path):
        client, *_ = admin_ctx
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


# ==========================================================================
# Admin-only pages
# ==========================================================================
class TestAdminOnlyPages:
    @pytest.mark.parametrize("path", ["/admin/employees", "/company/", "/backup/"])
    def test_admin_can_open(self, admin_ctx, path):
        client, *_ = admin_ctx
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/admin/employees", "/company/", "/backup/"])
    def test_employee_gets_403(self, employee_ctx, path):
        client, *_ = employee_ctx
        assert client.get(path).status_code == 403


# ==========================================================================
# Lead flows through HTTP
# ==========================================================================
class TestLeadRoutes:
    def test_new_lead_form_renders(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/leads/new").status_code == 200

    def test_create_lead_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        # The form submits parallel arrays with [] suffixes, plus the index
        # of the row marked primary - see _extract_contacts_from_form.
        resp = client.post("/leads/new", data={
            "company_name": "Posted Co", "phone": "999", "email": "p@x.com",
            "contact_name[]": ["Carol"], "contact_phone[]": ["1"],
            "contact_email[]": ["c@x.com"], "primary_contact_index": "0",
        }, follow_redirects=True)
        assert resp.status_code == 200
        names = [l.company_name for l in container.lead_repo.list_all(company_id)]
        assert "Posted Co" in names

    def test_lead_detail_page(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        lead = new_lead(container, admin)
        assert client.get(f"/leads/{lead.id}").status_code == 200

    def test_unknown_lead_is_404(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/leads/99999").status_code == 404

    def test_update_status_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        lead = new_lead(container, admin)
        client.post(f"/leads/{lead.id}/status", data={"status": "in_follow_up"},
                    follow_redirects=True)
        assert container.lead_repo.get_by_id(lead.id).status == "in_follow_up"

    def test_add_communication_via_post(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        lead = new_lead(container, admin)
        client.post(f"/leads/{lead.id}/communications", data={
            "comm_date": "2026-01-01 10:00", "mode": "Call",
            "description": "Discussed pricing",
        }, follow_redirects=True)
        assert len(container.comm_repo.list_for("lead", lead.id)) == 1

    def test_convert_lead_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        lead = new_lead(container, admin)
        client.post(f"/leads/{lead.id}/convert", data={"client_type": "Buyer"},
                    follow_redirects=True)
        assert len(container.buyer_repo.list_all(company_id)) == 1


# ==========================================================================
# Client pages
# ==========================================================================
class TestClientRoutes:
    def test_client_detail_page(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        lead = new_lead(container, admin)
        c = container.buyer_service.convert_lead(lead.id, admin)
        assert client.get(f"/buyers/{c.id}").status_code == 200

    def test_unknown_client_is_404(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/buyers/99999").status_code == 404


# ==========================================================================
# Product catalog pages
# ==========================================================================
class TestProductRoutes:
    def _product(self, container, admin):
        return container.product_service.create_product(
            admin, product_name="Tiles", description="", hsn_code="6907",
            igst_percent="18", quantity="10", alternate_quantity="1.44")

    def test_new_product_form(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/products/new").status_code == 200

    def test_product_detail_page(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        p = self._product(container, admin)
        assert client.get(f"/products/{p.id}").status_code == 200

    def test_product_json_api(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        self._product(container, admin)
        resp = client.get("/products/api/list")
        assert resp.status_code == 200
        assert resp.is_json
        # Shape: {"products": [{id, name, hsn_code, ...}]}. The document forms
        # read these exact key names, so pin them.
        products = resp.get_json()["products"]
        product = next(p for p in products if p["name"] == "Tiles")
        assert product["hsn_code"] == "6907"
        assert product["igst_percent"] == 18
        assert product["alternate_quantity"] == "1.44"
        assert product["pallet_types"] == []

    def test_product_json_includes_derived_pallet_quantities(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        p = container.product_service.create_product(
            admin, product_name="Palletised", description="", hsn_code="",
            igst_percent="", quantity="", alternate_quantity="1.5",
            pallet_types=[{"name": "oak", "boxes_per_pallet": "10"}])
        products = client.get("/products/api/list").get_json()["products"]
        entry = next(x for x in products if x["id"] == p.id)
        pallet = entry["pallet_types"][0]
        assert pallet["name"] == "oak"
        assert pallet["boxes_per_pallet"] == 10
        assert pallet["alt_qty_per_pallet"] == 15.0  # 10 boxes x 1.5, derived

    def test_designs_json_api(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        p = self._product(container, admin)
        container.product_service.create_design(
            admin, p.id, None, "White", "", "10", "", None, None)
        resp = client.get(f"/products/api/{p.id}/designs")
        assert resp.status_code == 200
        assert resp.is_json

    def test_employee_cannot_open_new_product_form(self, employee_ctx):
        client, *_ = employee_ctx
        assert client.get("/products/new").status_code == 403


# ==========================================================================
# Document pages (quotation as the representative case)
# ==========================================================================
class TestDocumentRoutes:
    def _quotation(self, container, admin):
        return container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])

    def test_new_quotation_form(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/quotations/new").status_code == 200

    def test_quotation_detail_page(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        q = self._quotation(container, admin)
        assert client.get(f"/quotations/{q.id}").status_code == 200

    def test_quotation_edit_form(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        q = self._quotation(container, admin)
        assert client.get(f"/quotations/{q.id}/edit").status_code == 200

    def test_quotation_versions_page_admin_only(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        q = self._quotation(container, admin)
        assert client.get(f"/quotations/{q.id}/versions").status_code == 200

    def test_unknown_quotation_is_404(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/quotations/99999").status_code == 404

    def test_delete_quotation_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        q = self._quotation(container, admin)
        client.post(f"/quotations/{q.id}/delete", data={"delete_password": "page-pass-1"}, follow_redirects=True)
        assert container.quotation_repo.get_by_id(q.id) is None

    def test_delete_quotation_via_post_rejects_wrong_password(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        q = self._quotation(container, admin)
        client.post(f"/quotations/{q.id}/delete", data={"delete_password": "wrong"}, follow_redirects=True)
        assert container.quotation_repo.get_by_id(q.id) is not None

    def test_purchase_order_form_has_no_nested_form(self, admin_ctx):
        """The admin-only "Add new supplier" panel once shipped as a <form>
        nested inside the purchase order's own form. Browsers drop the inner
        tag, which handed its `required` company_name to the PO form - and a
        required control inside a `hidden` panel can't be focused, so Chrome
        refused to submit the PO at all, silently. Nothing server-side broke,
        so only a markup assertion catches it."""
        import re

        client, *_ = admin_ctx
        html = client.get("/purchase-orders/new").get_data(as_text=True)
        body = html[html.find('<form method="POST" id="po-form"'):]
        body = body[:body.find("</form>")]
        markup = re.sub(r"<!--.*?-->", "", body, flags=re.S)  # comments mention <form> in prose
        assert "<form" not in markup[1:]
        # The panel's controls must not become the PO form's controls.
        assert 'name="company_name"' not in markup
        panel = markup[markup.find("seller-add-new-panel"):markup.find("seller_select")]
        assert not re.search(r"\srequired[\s>]", panel)  # the attribute, not the required-mark class
        # ...and the submit button still lives inside the PO form.
        assert "Create purchase order" in markup

    def test_purchase_order_form_derives_taxes_instead_of_asking_for_them(self, admin_ctx):
        """The three GST percentages follow from "Purchase under" + the GSTIN
        state-code comparison, so the form shows them rather than collecting
        them - a posted percentage would only be a stale copy."""
        client, *_ = admin_ctx
        html = client.get("/purchase-orders/new").get_data(as_text=True)
        assert "Purchase under" in html
        assert 'value="full_tax"' in html and 'value="exemption"' in html
        for field in ("igst_percent", "cgst_percent", "sgst_percent"):
            assert f'name="{field}"' not in html


# ==========================================================================
# Reports
# ==========================================================================
class TestReportRoutes:
    def test_report_with_date_range(self, admin_ctx):
        client, *_ = admin_ctx
        resp = client.get("/reports/?start_date=2026-01-01&end_date=2026-12-31")
        assert resp.status_code == 200


# ==========================================================================
# Backup page + download
# ==========================================================================
class TestBackupRoutes:
    def test_backup_page_renders(self, admin_ctx):
        client, *_ = admin_ctx
        assert client.get("/backup/").status_code == 200

    def test_download_returns_a_zip(self, admin_ctx):
        client, *_ = admin_ctx
        resp = client.get("/backup/download")
        assert resp.status_code == 200
        assert resp.data[:2] == b"PK"  # zip magic bytes


# ==========================================================================
# Multi-tenancy isolation at the HTTP layer
# ==========================================================================
class TestTenantIsolationOverHttp:
    def test_cannot_open_another_companys_lead(self, app, client, admin_ctx):
        _, container, admin, _ = admin_ctx
        # A lead belonging to a different tenant.
        other = container.tenant_repo.create("Rival", "rival")
        rival_admin = container.auth_service.create_user(
            other.id, "rival", "pw123456", "Rival", "admin")
        rival_lead = new_lead(container, rival_admin)
        # The logged-in admin from admin_ctx must not see it.
        assert client.get(f"/leads/{rival_lead.id}").status_code == 404


# ==========================================================================
# Export invoice + its generated export packing list, end to end over HTTP
# ==========================================================================
class TestExportPackingListRoutes:
    def _create_export_invoice(self, client, container, admin, company_id, split=True):
        """Post the export invoice form the way the browser does, including
        the container split that generates the packing list."""
        product = container.product_service.create_product(
            current_user=admin, product_name="GVT 600X1200", description="", hsn_code="69072100",
            igst_percent="18", quantity="4", alternate_quantity="1.44",
            net_weight_kg=26.5, gross_weight_kg=27.0)
        data = {
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "stuffing_location": "ALIVE GRANITO LLP, MORBI",
            "item_product_id[]": str(product.id), "item_product_name[]": "GVT 600X1200",
            "item_hsn_code[]": "69072100", "item_quantity_boxes[]": "100",
            "item_quantity_value[]": "144", "item_unit[]": "SQM", "item_price_usd[]": "5.92",
            "cd_container_no[]": ["BLJU2253726", "SEGU3227471"],
            "cd_line_seal_no[]": ["UFL331090", "UFL331095"],
            "cd_rfid_seal_no[]": ["WIND02432727", "WIND02531142"],
            "cd_vehicle_no[]": ["", ""],
            "cd_tare_weight[]": ["2250.5", "2260"],
        }
        if split:
            data.update({
                "alloc_container_index[]": ["0", "1"],
                "alloc_invoice_item_index[]": ["0", "0"],
                "alloc_boxes[]": ["60", "40"],
                "alloc_group_label[]": ["GLAZED VITRIFIED TILES", "GLAZED VITRIFIED TILES"],
                "alloc_pallets[]": ["", ""],
                "alloc_net_weight[]": ["", ""],
                "alloc_gross_weight[]": ["", ""],
            })
        resp = client.post("/export-invoices/new", data=data, follow_redirects=True)
        assert resp.status_code == 200
        return container.export_invoice_service.list_all(company_id)[0]

    def test_new_and_edit_forms_render(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        assert client.get("/export-invoices/new").status_code == 200
        invoice = self._create_export_invoice(client, container, admin, company_id)
        assert client.get(f"/export-invoices/{invoice.id}/edit").status_code == 200

    def test_export_invoice_print_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.get(f"/export-invoices/{invoice.id}")
        assert resp.status_code == 200
        assert b"EXPORT INVOICE" in resp.data

    def test_saving_an_export_invoice_generates_its_packing_list(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)
        assert packing_list is not None
        assert [i.quantity_boxes for i in packing_list.items] == [60, 40]

    def test_packing_list_pages_render(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)

        assert client.get("/export-packing-lists/").status_code == 200
        resp = client.get(f"/export-packing-lists/{packing_list.id}")
        assert resp.status_code == 200
        assert b"EXPORT PACKING LIST" in resp.data
        assert b"BLJU2253726" in resp.data
        assert b"GLAZED VITRIFIED TILES - HSNC 69072100" in resp.data
        assert b"ALIVE GRANITO LLP, MORBI" in resp.data

    def test_tare_weight_prints_in_the_annexures_11b_table(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.get(f"/export-invoices/{invoice.id}")
        assert resp.status_code == 200
        assert b"2,250.50" in resp.data

    def test_11b_gross_net_weight_and_total_row_come_from_the_packing_list(self, admin_ctx):
        # Container 1 gets 60 boxes, container 2 gets 40 - both from a
        # product whose catalog net/gross weight per box is 26.5/27.0 KG
        # (see _create_export_invoice), so the packing list derives:
        #   container 1: net 60*26.5=1590.00, gross 60*27.0=1620.00
        #   container 2: net 40*26.5=1060.00, gross 40*27.0=1080.00
        #   TOTAL: net 2650.00, gross 2700.00
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.get(f"/export-invoices/{invoice.id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "1,620.00" in body  # container 1 gross
        assert "1,590.00" in body  # container 1 net
        assert "1,080.00" in body  # container 2 gross
        assert "1,060.00" in body  # container 2 net
        assert "2,700.00" in body  # TOTAL gross
        assert "2,650.00" in body  # TOTAL net
        assert "TOTAL" in body

    def test_for_invoice_shortcut_redirects_to_the_generated_list(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)
        resp = client.get(f"/export-packing-lists/for-invoice/{invoice.id}")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/export-packing-lists/{packing_list.id}")

    def test_an_unbalanced_split_is_rejected_and_nothing_is_saved(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        product = container.product_service.create_product(
            current_user=admin, product_name="GVT 600X1200", description="", hsn_code="69072100",
            igst_percent="18", quantity="4", alternate_quantity="1.44")
        resp = client.post("/export-invoices/new", data={
            "export_invoice_number": "1000000043", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_id[]": str(product.id), "item_product_name[]": "GVT 600X1200",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92",
            "cd_container_no[]": ["BLJU2253726"], "cd_line_seal_no[]": ["UFL331090"],
            "cd_rfid_seal_no[]": ["WIND02432727"], "cd_vehicle_no[]": [""],
            "alloc_container_index[]": ["0"], "alloc_invoice_item_index[]": ["0"],
            "alloc_boxes[]": ["70"], "alloc_group_label[]": [""],
            "alloc_pallets[]": [""], "alloc_net_weight[]": [""], "alloc_gross_weight[]": [""],
        })
        assert resp.status_code == 400
        assert b"still unassigned" in resp.data
        assert container.export_invoice_service.list_all(company_id) == []

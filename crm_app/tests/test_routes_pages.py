"""
Broader route coverage: every major list/detail/form page renders, key POST
flows work end-to-end through HTTP, and admin-only pages stay admin-only.

These are the tests that catch a broken template, a renamed url_for endpoint,
or a route/service signature drift - things the service-level tests can't see.
"""

import re

import pytest

from tests.test_services_company_stats_reports import save_company


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
    @pytest.mark.parametrize("path", ["/admin/employees", "/company/", "/backup/", "/misc/"])
    def test_admin_can_open(self, admin_ctx, path):
        client, *_ = admin_ctx
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/admin/employees", "/company/", "/backup/", "/misc/"])
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

    def test_admin_sees_the_delete_button(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        c = container.buyer_service.convert_lead(new_lead(container, admin).id, admin)
        assert f"/buyers/{c.id}/delete" in client.get(f"/buyers/{c.id}").get_data(as_text=True)

    def test_employee_does_not_see_the_delete_button(self, employee_ctx):
        client, container, emp, company_id = employee_ctx
        # An employee can't convert a lead, so seed the buyer with an admin
        # of the SAME tenant and view it as the employee.
        admin = container.auth_service.create_user(
            company_id, "empco-admin", "pass-1", "Emp Co Admin", "admin")
        c = container.buyer_service.convert_lead(new_lead(container, emp).id, admin)
        assert f"/buyers/{c.id}/delete" not in client.get(f"/buyers/{c.id}").get_data(as_text=True)

    def test_delete_buyer_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        c = container.buyer_service.convert_lead(new_lead(container, admin).id, admin)
        client.post(f"/buyers/{c.id}/delete", data={"delete_password": "page-pass-1"},
                    follow_redirects=True)
        assert container.buyer_repo.get_by_id(c.id) is None

    def test_delete_buyer_rejects_wrong_password(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        c = container.buyer_service.convert_lead(new_lead(container, admin).id, admin)
        client.post(f"/buyers/{c.id}/delete", data={"delete_password": "wrong"},
                    follow_redirects=True)
        assert container.buyer_repo.get_by_id(c.id) is not None

    def test_employee_cannot_post_a_delete(self, employee_ctx):
        client, container, emp, company_id = employee_ctx
        admin = container.auth_service.create_user(
            company_id, "empco-admin2", "pass-1", "Emp Co Admin", "admin")
        c = container.buyer_service.convert_lead(new_lead(container, emp).id, admin)
        resp = client.post(f"/buyers/{c.id}/delete", data={"delete_password": "emp-pass-1"})
        assert resp.status_code == 403
        assert container.buyer_repo.get_by_id(c.id) is not None


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

    def test_duplicate_quotation_via_post(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        q = self._quotation(container, admin)
        client.post(f"/quotations/{q.id}/duplicate", data={"duplicate_password": "page-pass-1"},
                    follow_redirects=True)
        assert len(container.quotation_repo.list_all(company_id)) == 2

    def test_duplicate_quotation_rejects_wrong_password(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        q = self._quotation(container, admin)
        client.post(f"/quotations/{q.id}/duplicate", data={"duplicate_password": "wrong"},
                    follow_redirects=True)
        assert len(container.quotation_repo.list_all(company_id)) == 1

    def test_employee_cannot_duplicate_a_quotation(self, employee_ctx):
        client, container, emp, company_id = employee_ctx
        q = self._quotation(container, emp)
        resp = client.post(f"/quotations/{q.id}/duplicate", data={"duplicate_password": "emp-pass-1"})
        assert resp.status_code == 403
        assert len(container.quotation_repo.list_all(company_id)) == 1

    def test_employee_does_not_see_the_duplicate_button(self, employee_ctx):
        client, container, emp, _ = employee_ctx
        q = self._quotation(container, emp)
        assert f"/quotations/{q.id}/duplicate" not in client.get(f"/quotations/{q.id}").get_data(as_text=True)

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
    def _create_export_invoice(self, client, container, admin, company_id, split=True, extra=None):
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
        data.update(extra or {})
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

    def test_pi_prefill_api_returns_only_the_pi_derived_fields(self, admin_ctx):
        """The form's "Load from selected PIs" button applies this JSON in
        place instead of reloading, so whatever the user typed in fields the
        PIs have no say over survives a (re-)load."""
        client, container, admin, company_id = admin_ctx
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "ROBUST INTERNATIONAL", "invoice_date": "2026-01-01",
                    "buyer_order_no": "EXP/001", "sea_freight": "100"},
            [{"product_name": "GVT 600X1200", "quantity_value": "144", "price_usd": "5.92"}])
        resp = client.get(f"/export-invoices/api/prefill?proforma_invoice_ids={proforma.id}")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["fields"]["consignee_name"] == "ROBUST INTERNATIONAL"
        assert payload["fields"]["buyer_order_no"] == "EXP/001"
        assert payload["fields"]["sea_freight"] == 100
        assert [i["product_name"] for i in payload["items"]] == ["GVT 600X1200"]
        # Fields no PI decides are simply absent - the form leaves them alone.
        for key in ("export_invoice_number", "permission_no", "stuffing_location", "booking_no", "vessel_voyage_no"):
            assert key not in payload["fields"]

    def test_export_invoice_print_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.get(f"/export-invoices/{invoice.id}")
        assert resp.status_code == 200
        assert b"EXPORT INVOICE" in resp.data

    def _set_government_schemes(self, container, admin, text):
        save_company(container, admin, government_schemes=text)

    def _export_under_cell(self, html):
        """The Export Under cell's printed lines, label row dropped. Anchored
        on the label span, not on the bare words - both sheets mention
        "Export Under" in a CSS comment further up."""
        anchor = re.search(r'-lbl">Export Under</span>', html)
        assert anchor, "no Export Under cell on this sheet"
        seg = html[anchor.end():]
        seg = seg[:seg.find("</td>")]
        seg = re.sub(r"<br\s*/?>", "\n", seg)
        seg = re.sub(r"<[^>]+>", "", seg)
        return [line.strip() for line in seg.split("\n") if line.strip()]

    def test_export_under_block_is_composed_of_scheme_heading_and_epcg(self, admin_ctx):
        """All three parts are derived - only the scheme line is ever typed."""
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "WE INTEND TO CLAIM REWARDS UNDER RoDTEP & DBK")
        invoice = self._create_export_invoice(client, container, admin, company_id, extra={
            "epcg_number": "2431000888", "epcg_date": "2019-09-17",
        })

        lines = self._export_under_cell(client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True))
        assert lines[0] == "WE INTEND TO CLAIM REWARDS UNDER RODTEP & DBK"
        assert lines[1] == "SUPPLY MEANT FOR EXPORT WITH PAYMENT OF IGST"
        assert lines[2].startswith("EPCG LICENCE NO : 2431000888")

    def test_the_epcg_line_is_absent_when_there_is_no_licence(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "WE INTEND TO CLAIM REWARDS UNDER RoDTEP & DBK")
        invoice = self._create_export_invoice(client, container, admin, company_id)

        lines = self._export_under_cell(client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True))
        assert not any("EPCG" in line for line in lines)

    def test_a_typed_export_under_overrides_only_the_scheme_line(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "COMPANY DEFAULT SCHEME")
        invoice = self._create_export_invoice(client, container, admin, company_id, extra={
            "export_under": "CLAIMING UNDER ADVANCE AUTHORISATION",
        })

        lines = self._export_under_cell(client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True))
        assert lines[0] == "CLAIMING UNDER ADVANCE AUTHORISATION"
        assert "COMPANY DEFAULT SCHEME" not in " ".join(lines)
        # The heading still follows tax_mode, not what was typed.
        assert lines[1] == "SUPPLY MEANT FOR EXPORT WITH PAYMENT OF IGST"

    def test_a_blank_export_under_falls_back_to_the_live_company_scheme(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "FIRST SCHEME TEXT")
        invoice = self._create_export_invoice(client, container, admin, company_id, extra={"export_under": ""})
        self._set_government_schemes(container, admin, "SECOND SCHEME TEXT")

        lines = self._export_under_cell(client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True))
        assert lines[0] == "SECOND SCHEME TEXT"

    def test_the_packing_list_prints_the_same_scheme_line_as_its_invoice(self, admin_ctx):
        """The packing list used to hard-code RoDTEP wording, so it could
        disagree with the invoice it belongs to."""
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "WE INTEND TO CLAIM REWARDS UNDER RoDTEP & DBK")
        invoice = self._create_export_invoice(client, container, admin, company_id, extra={
            "export_under": "CLAIMING UNDER ADVANCE AUTHORISATION",
        })
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)

        lines = self._export_under_cell(
            client.get(f"/export-packing-lists/{packing_list.id}").get_data(as_text=True))
        assert lines[0] == "CLAIMING UNDER ADVANCE AUTHORISATION"
        assert lines[1] == "SUPPLY MEANT FOR EXPORT WITH PAYMENT OF IGST"

    def test_the_lut_number_stays_out_of_the_export_under_cell(self, admin_ctx):
        """Customs reads this cell, and the LUT number has no place in it -
        it belongs to the SUPPLY MEANT FOR EXPORT heading at the top of the
        sheet. Pinned on both sheets, which print the identical cell."""
        client, container, admin, company_id = admin_ctx
        self._set_government_schemes(container, admin, "WE INTEND TO CLAIM REWARDS UNDER RoDTEP & DBK")
        invoice = self._create_export_invoice(client, container, admin, company_id, extra={
            "epcg_number": "2431000888", "epcg_date": "2019-09-17",
        })
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)

        for path in (f"/export-invoices/{invoice.id}", f"/export-packing-lists/{packing_list.id}"):
            page = client.get(path).get_data(as_text=True)
            lines = self._export_under_cell(page)
            assert not any("LUT" in line for line in lines), path
            assert len(lines) == 3      # scheme / heading / EPCG, nothing else
        # ...and the packing list still carries the company's LUT up in its
        # SUPPLY MEANT FOR EXPORT heading, which is where it belongs.
        assert "LUT123" in client.get(
            f"/export-packing-lists/{packing_list.id}").get_data(as_text=True)

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
        assert b"PACKING LIST" in resp.data
        assert b"BLJU2253726" in resp.data
        assert b"69072100" in resp.data
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


# ==========================================================================
# Administration -> Miscellaneous (the hand-maintained drop lists)
# ==========================================================================
class TestMiscRoutes:
    def test_currency_list_starts_on_the_builtin_fallback(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        assert container.misc_list_service.list_currencies(company_id) == []
        # ...but a dropdown is never empty.
        names = [c.name for c in container.misc_list_service.currency_options(company_id)]
        assert "USD" in names

    def test_add_edit_and_delete_a_currency(self, admin_ctx):
        client, container, admin, company_id = admin_ctx

        client.post("/misc/currencies", data={"name": "JPY", "symbol": "¥"}, follow_redirects=True)
        stored = container.misc_list_service.list_currencies(company_id)
        assert [(c.name, c.symbol) for c in stored] == [("JPY", "¥")]

        client.post(f"/misc/currencies/{stored[0].id}/edit",
                    data={"name": "JPY", "symbol": "JP¥"}, follow_redirects=True)
        assert container.misc_list_service.list_currencies(company_id)[0].symbol == "JP¥"

        client.post(f"/misc/currencies/{stored[0].id}/delete",
                    data={"delete_password": "page-pass-1"}, follow_redirects=True)
        assert container.misc_list_service.list_currencies(company_id) == []

    def test_a_currency_name_cannot_repeat(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/currencies", data={"name": "JPY", "symbol": "¥"}, follow_redirects=True)
        client.post("/misc/currencies", data={"name": "jpy", "symbol": "J"}, follow_redirects=True)
        assert len(container.misc_list_service.list_currencies(company_id)) == 1

    def test_add_edit_and_delete_a_nature_of_contract(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        assert container.misc_list_service.list_nature_of_contracts(company_id) == []

        client.post("/misc/nature-of-contracts", data={"name": "CIF - BEIRA"}, follow_redirects=True)
        stored = container.misc_list_service.list_nature_of_contracts(company_id)
        assert [e.name for e in stored] == ["CIF - BEIRA"]

        client.post(f"/misc/nature-of-contracts/{stored[0].id}/edit",
                    data={"name": "CIF - MAPUTO"}, follow_redirects=True)
        assert container.misc_list_service.list_nature_of_contracts(company_id)[0].name == "CIF - MAPUTO"

        # ...and a name can't repeat.
        client.post("/misc/nature-of-contracts", data={"name": "cif - maputo"}, follow_redirects=True)
        assert len(container.misc_list_service.list_nature_of_contracts(company_id)) == 1

        client.post(f"/misc/nature-of-contracts/{stored[0].id}/delete",
                    data={"delete_password": "page-pass-1"}, follow_redirects=True)
        assert container.misc_list_service.list_nature_of_contracts(company_id) == []

    def test_add_edit_and_delete_a_port_of_loading(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        assert container.misc_list_service.list_ports_of_loading(company_id) == []

        client.post("/misc/ports-of-loading", data={"name": "MUNDRA", "pin_code": "370421"},
                    follow_redirects=True)
        stored = container.misc_list_service.list_ports_of_loading(company_id)
        assert [(p.name, p.pin_code) for p in stored] == [("MUNDRA", "370421")]

        client.post(f"/misc/ports-of-loading/{stored[0].id}/edit",
                    data={"name": "MUNDRA", "pin_code": "370201"}, follow_redirects=True)
        assert container.misc_list_service.list_ports_of_loading(company_id)[0].pin_code == "370201"

        # ...and a port name can't repeat.
        client.post("/misc/ports-of-loading", data={"name": "mundra", "pin_code": "370421"},
                    follow_redirects=True)
        assert len(container.misc_list_service.list_ports_of_loading(company_id)) == 1

        client.post(f"/misc/ports-of-loading/{stored[0].id}/delete",
                    data={"delete_password": "page-pass-1"}, follow_redirects=True)
        assert container.misc_list_service.list_ports_of_loading(company_id) == []

    def test_a_port_of_loading_needs_both_halves(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/ports-of-loading", data={"name": "MUNDRA", "pin_code": ""},
                    follow_redirects=True)
        client.post("/misc/ports-of-loading", data={"name": "", "pin_code": "370421"},
                    follow_redirects=True)
        assert container.misc_list_service.list_ports_of_loading(company_id) == []

    @pytest.mark.parametrize("path,field", [
        ("/export-invoices/new", "nature_of_contract"),
        ("/proforma-invoices/new", "terms_of_delivery"),
        ("/quotations/new", "shipping_terms"),
    ])
    def test_nature_of_contract_fills_every_documents_delivery_terms_dropdown(self, admin_ctx, path, field):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/nature-of-contracts", data={"name": "CIF - BEIRA"}, follow_redirects=True)
        page = client.get(path).get_data(as_text=True)
        assert f'<select id="{field}" name="{field}">' in page
        assert "CIF - BEIRA" in page

    @pytest.mark.parametrize("path", [
        "/export-invoices/new",
        "/proforma-invoices/new",
        "/quotations/new",
    ])
    def test_port_of_loading_fills_every_documents_port_dropdown(self, admin_ctx, path):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/ports-of-loading", data={"name": "MUNDRA", "pin_code": "370421"},
                    follow_redirects=True)
        page = client.get(path).get_data(as_text=True)
        assert '<select id="port_of_loading" name="port_of_loading">' in page
        assert "MUNDRA" in page

    @pytest.mark.parametrize("path", [
        "/export-invoices/new",
        "/proforma-invoices/new",
        "/quotations/new",
    ])
    def test_an_admin_can_add_a_port_straight_from_the_dropdown(self, admin_ctx, path):
        client, container, admin, company_id = admin_ctx
        page = client.get(path).get_data(as_text=True)
        assert "+ Add a new port" in page

        resp = client.post("/misc/api/ports-of-loading",
                           data={"name": "MUNDRA", "pin_code": "370421"})
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "MUNDRA"
        stored = container.misc_list_service.list_ports_of_loading(company_id)
        assert [(p.name, p.pin_code) for p in stored] == [("MUNDRA", "370421")]
        # ...and it is on the dropdown from then on, like any other entry.
        assert "MUNDRA" in client.get(path).get_data(as_text=True)

    def test_quick_adding_a_port_reports_its_errors_as_json(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        # A port needs both halves, and a name can't repeat - the same rules
        # the Miscellaneous page enforces, reported back to the dropdown
        # rather than flashed onto a page the user never leaves.
        resp = client.post("/misc/api/ports-of-loading", data={"name": "MUNDRA", "pin_code": ""})
        assert resp.status_code == 400 and "Pincode" in resp.get_json()["error"]
        client.post("/misc/api/ports-of-loading", data={"name": "MUNDRA", "pin_code": "370421"})
        resp = client.post("/misc/api/ports-of-loading", data={"name": "mundra", "pin_code": "370421"})
        assert resp.status_code == 400 and "already on the port of loading list" in resp.get_json()["error"]
        assert len(container.misc_list_service.list_ports_of_loading(company_id)) == 1

    def test_a_non_admin_is_not_offered_the_add_option(self, app, client):
        """The list itself is admin-only, so an employee gets the plain
        dropdown rather than an entry that would only fail on them."""
        container = app.container
        tenant = container.tenant_repo.create("Emp Co", "emp-co")
        employee = container.auth_service.create_user(
            tenant.id, "empuser", "emp-pass-1", "Emp User", "employee")
        with client.session_transaction() as sess:
            sess["user_id"] = employee.id
        page = client.get("/quotations/new").get_data(as_text=True)
        assert '<select id="port_of_loading" name="port_of_loading">' in page
        assert "+ Add a new port" not in page

    def test_picked_currency_prints_on_the_invoice_and_packing_list(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/currencies", data={"name": "JPY", "symbol": "¥"}, follow_redirects=True)
        invoice = TestExportPackingListRoutes()._create_export_invoice(
            client, container, admin, company_id)
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": invoice.export_invoice_number,
            "invoice_date": invoice.invoice_date, "consignee_name": invoice.consignee_name,
            "tax_mode": "igst", "exchange_rate": "86.70", "currency_code": "JPY",
            "item_product_name[]": "GVT 600X1200", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92",
        }, follow_redirects=True)

        sheet = client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True)
        assert "JPY [ ¥ ]" in sheet and "USD [ $ ]" not in sheet
        # ...and every amount on the sheet carries that currency, not a $.
        assert "$" not in sheet
        assert "JPY" in sheet.split("Amount Chargeable")[-1][:400] or "¥" in sheet
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)
        epl = client.get(f"/export-packing-lists/{packing_list.id}").get_data(as_text=True)
        assert "JPY [ ¥ ]" in epl

    def test_added_currency_shows_in_a_payment_dropdown(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        client.post("/misc/currencies", data={"name": "JPY", "symbol": "¥"}, follow_redirects=True)
        buyer = container.buyer_service.create(
            admin, {"company_name": "Yen Buyer", "phone": "1", "email": "y@x.com"},
            [{"name": "Yui", "is_primary": True}])
        page = client.get(f"/buyers/{buyer.id}").get_data(as_text=True)
        assert "JPY" in page
        # The old hard-coded options are gone once a list of your own exists.
        assert 'value="SAR"' not in page


# ==========================================================================
# Currency on every document (picked from Administration -> Miscellaneous)
# ==========================================================================
class TestDocumentCurrency:
    def _currency(self, client):
        client.post("/misc/currencies", data={"name": "JPY", "symbol": "¥"}, follow_redirects=True)

    def test_quotation_stores_and_prints_its_currency(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._currency(client)
        client.post("/quotations/new", data={
            "buyer_name": "Buyer", "quotation_date": "2026-01-01", "currency_code": "JPY",
            "item_product_name[]": "P", "item_quantity_value[]": "10", "item_price_usd[]": "2",
        }, follow_redirects=True)
        quotation = container.quotation_service.list_all(company_id)[0]
        assert (quotation.currency_code, quotation.currency_symbol) == ("JPY", "¥")

        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "JPY [ ¥ ]" in sheet          # the Currency row
        assert "PRICE (JPY)" in sheet        # the money column headings
        assert "(USD)" not in sheet

    def test_a_document_saved_before_the_field_existed_still_prints_usd(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert quotation.currency_code is None
        assert quotation.currency_label == "USD [ $ ]"
        assert "PRICE (USD)" in client.get(f"/quotations/{quotation.id}").get_data(as_text=True)

    def test_purchase_order_defaults_to_inr_and_follows_its_pick(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._currency(client)
        order = container.purchase_order_service.create(
            admin, {"seller_name": "Supplier", "po_date": "2026-01-01"},
            [{"product_name": "P", "quantity_boxes": "5", "quantity_value": "10", "price_inr": "2"}])
        assert order.currency_label == "INR [ ₹ ]"

        updated = container.purchase_order_service.update(
            admin, order.id, {"seller_name": "Supplier", "po_date": "2026-01-01", "currency_code": "JPY"},
            [{"product_name": "P", "quantity_boxes": "5", "quantity_value": "10", "price_inr": "2"}])
        assert (updated.currency_code, updated.currency_symbol) == ("JPY", "¥")
        sheet = client.get(f"/purchase-orders/{order.id}").get_data(as_text=True)
        assert "TOTAL (JPY)" in sheet and "TOTAL (INR)" not in sheet

    def test_proforma_invoice_inherits_the_quotations_currency(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._currency(client)
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01", "currency_code": "JPY"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        prefill = container.proforma_invoice_service.build_prefill_from_quotation(quotation)
        assert prefill["fields"]["currency_code"] == "JPY"

    def test_every_document_form_offers_the_currency_dropdown(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._currency(client)
        for path in ("/quotations/new", "/proforma-invoices/new", "/purchase-orders/new",
                     "/purchase-invoices/new", "/export-invoices/new"):
            page = client.get(path).get_data(as_text=True)
            assert 'name="currency_code"' in page, path
            assert "JPY [ ¥ ]" in page, path

    def test_proforma_and_purchase_invoice_pages_render_in_their_currency(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._currency(client)
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01", "currency_code": "JPY"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True)
        assert "Rate<br>In JPY" in sheet and "¥" in sheet

        order = container.purchase_order_service.create(
            admin, {"seller_name": "Supplier", "po_date": "2026-01-01", "currency_code": "JPY"},
            [{"product_name": "P", "quantity_boxes": "5", "quantity_value": "10", "price_inr": "2"}])
        invoice = container.purchase_invoice_service.create(
            admin, {"seller_name": "Supplier", "invoice_number": "S-1", "invoice_date": "2026-01-02",
                    "purchase_order_id": str(order.id), "currency_code": "JPY"},
            [{"product_name": "P", "quantity_boxes": "5", "quantity_value": "10", "price_inr": "2"}], [])
        view = client.get(f"/purchase-invoices/{invoice.id}").get_data(as_text=True)
        assert "Price (JPY)" in view and "(INR)" not in view


# ==========================================================================
# The delivery terms drop charge rows from every printed sheet
# ==========================================================================
def _table_is_rectangular(html, marker):
    """Every row of the table containing `marker` accounts for the same number
    of columns, counting colspans and the rows a rowspan reaches into.

    This is what catches a dropped charge row taking the surrounding structure
    with it: a stale rowspan on the amount-in-words cell shows up here as a
    row that is one column short or one column over."""
    table = re.search(r"<table[^>]*>(?:(?!</table>).)*?"
                      + re.escape(marker) + r"(?:(?!</table>).)*?</table>", html, re.S)
    assert table, f"no table containing {marker!r}"
    rows = re.findall(r"<tr[^>]*>((?:(?!</tr>).)*)</tr>", table.group(0), re.S)
    widths, carried = [], {}          # carried: row offset -> columns still spanned
    for i, row in enumerate(rows):
        width = carried.pop(i, 0)
        for cell in re.finditer(r"<t[dh]([^>]*)>", row):
            attrs = cell.group(1)
            colspan = int((re.search(r'colspan="(\d+)"', attrs) or [0, 1])[1])
            rowspan = int((re.search(r'rowspan="(\d+)"', attrs) or [0, 1])[1])
            width += colspan
            for extra in range(1, rowspan):
                carried[i + extra] = carried.get(i + extra, 0) + colspan
        widths.append(width)
    assert not carried, f"a rowspan runs past the last row of the table: {carried}"
    assert len(set(widths)) == 1, f"ragged table around {marker!r}: row widths {widths}"


class TestDeliveryTermChargeRows:
    """FOB hands the whole ocean leg to the buyer, so the freight, the
    insurance and the certification all leave the sheet; CFR keeps the freight
    and the certification with the seller and drops the insurance alone.
    Each sheet has to lose exactly the right row(s) AND stay rectangular - the
    amount-in-words cell spans the whole ladder, so its rowspan has to be
    counted from the rows that actually print."""

    def test_quotation_sheet_drops_the_rows(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": "FOB", "sea_freight": "100", "insurance": "50",
                    "certification": "25"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "SEA FREIGHT" not in sheet and "INSURANCE" not in sheet
        assert "CERTIFICATION" not in sheet
        assert "OTHER" in sheet                  # the rest of the ladder stays
        assert "CIF VALUE" in sheet and "FOB VALUE" in sheet
        assert 'rowspan="5"' in sheet            # 8 ladder rows - the 3 dropped
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_proforma_sheet_drops_the_rows(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01",
                    "terms_of_delivery": "FOB MUNDRA", "sea_freight": "100", "insurance": "50",
                    "certification": "25"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True)
        assert "SEA FREIGHT" not in sheet and "INSURANCE" not in sheet
        assert "CERTIFICATION" not in sheet
        assert "CIF VALUE" in sheet and "INVOICE VALUE" in sheet and "FOB VALUE" in sheet
        assert 'rowspan="5"' in sheet            # 8 ladder rows - the 3 dropped
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_export_invoice_sheet_drops_the_rows(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = container.export_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-02-20",
                    "export_invoice_number": "1000000001", "tax_mode": "igst",
                    "exchange_rate": "86.70", "nature_of_contract": "FOB",
                    "sea_freight": "100", "insurance": "50", "certification": "25"},
            [{"product_name": "P", "quantity_value": "10", "unit": "SQM", "price_usd": "2"}])
        sheet = client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True)
        assert "Sea Freight" not in sheet and "Insurance" not in sheet
        assert "Certification" not in sheet
        assert "CIF Value" in sheet and "Invoice Value" in sheet
        assert "FOB Value" in sheet and "Other Charges" in sheet
        assert 'rowspan="5"' in sheet            # the shortened money ladder
        _table_is_rectangular(sheet, "Sr No")

    # ---- CFR: the insurance row alone comes out ------------------------------
    def test_quotation_sheet_drops_only_the_insurance_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": "CFR - BEIRA", "sea_freight": "100", "insurance": "50"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "INSURANCE" not in sheet
        assert "SEA FREIGHT" in sheet            # CFR = cost AND freight
        assert "CERTIFICATION" in sheet
        assert 'rowspan="7"' in sheet            # 8 ladder rows - the 1 dropped
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_proforma_sheet_drops_only_the_insurance_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01",
                    "terms_of_delivery": "CFR BEIRA", "sea_freight": "100", "insurance": "50"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True)
        assert "INSURANCE" not in sheet and "SEA FREIGHT" in sheet
        assert "CERTIFICATION" in sheet
        assert 'rowspan="7"' in sheet
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_export_invoice_sheet_drops_only_the_insurance_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = container.export_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-02-20",
                    "export_invoice_number": "1000000002", "tax_mode": "igst",
                    "exchange_rate": "86.70", "nature_of_contract": "CFR - BEIRA",
                    "sea_freight": "100", "insurance": "50"},
            [{"product_name": "P", "quantity_value": "10", "unit": "SQM", "price_usd": "2"}])
        sheet = client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True)
        assert "Insurance" not in sheet and "Sea Freight" in sheet
        assert "Certification" in sheet
        # 7 money rows: the Export Under cell spans them all, and the Net/Gross
        # weight cells split the remaining 5 between them (2 + 3).
        assert 'rowspan="7"' in sheet
        _table_is_rectangular(sheet, "Sr No")

    # ---- FOB-typed prices no longer add a ROUND-OFF row -----------------------
    # apply_fob_uplift keeps the uplift at full precision and only rounds each
    # line's Total, so any residual cent is absorbed there instead of being
    # tracked and printed as its own row.
    def test_fob_priced_quotation_sheet_has_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": "CIF", "sea_freight": "100", "fob_pricing": "1"},
            [{"product_name": "P", "quantity_value": "3", "price_usd": "7"}])
        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "ROUND-OFF" not in sheet
        assert 'rowspan="8"' in sheet
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_a_proforma_sheet_prints_cif_rates_with_no_round_off_row(self, admin_ctx):
        """The typed 7.00 is an FOB rate; 100 of sea freight over 3 SQM is
        33.33 a unit once rounded to the cent the Rate column prints, so the
        sheet quotes 40.33. 3 x 40.33 is a cent under the exact 121, and that
        cent is absorbed into the line's printed Total - customs has no
        round-off line to accept, and the FOB value the ladder is built up
        from is what the buyer and seller agreed."""
        client, container, admin, company_id = admin_ctx
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01",
                    "terms_of_delivery": "CIF", "sea_freight": "100"},
            [{"product_name": "P", "quantity_value": "3", "price_usd": "7"}])
        sheet = client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True)
        assert "40.33" in sheet          # the printed CIF rate
        assert "121.00" in sheet         # ...and the Total the column foots to
        assert "120.99" not in sheet
        assert "ROUND-OFF" not in sheet
        assert 'rowspan="8"' in sheet
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_a_proforma_sheet_that_divides_evenly_needs_no_absorbing(self, admin_ctx):
        # 90 over 3 SQM is exactly 30.00 a unit, so the printed Total is the
        # plain rate x quantity with nothing absorbed into it.
        client, container, admin, company_id = admin_ctx
        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01",
                    "terms_of_delivery": "CIF", "sea_freight": "90"},
            [{"product_name": "P", "quantity_value": "3", "price_usd": "7"}])
        sheet = client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True)
        assert "37.00" in sheet and "111.00" in sheet
        assert "ROUND-OFF" not in sheet
        assert 'rowspan="8"' in sheet
        _table_is_rectangular(sheet, "CIF VALUE")

    def test_fob_priced_export_invoice_sheet_has_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = container.export_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-02-20",
                    "export_invoice_number": "1000000003", "tax_mode": "igst",
                    "exchange_rate": "86.70", "nature_of_contract": "CIF",
                    "sea_freight": "100", "fob_pricing": "1"},
            [{"product_name": "P", "quantity_value": "3", "unit": "SQM", "price_usd": "7"}])
        sheet = client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in sheet
        # 8 money rows: the full ladder, with no Round-off row added.
        assert 'rowspan="8"' in sheet
        _table_is_rectangular(sheet, "Sr No")

    def test_plain_quotation_sheet_has_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": "CIF", "sea_freight": "100"},
            [{"product_name": "P", "quantity_value": "3", "price_usd": "7"}])
        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "ROUND-OFF" not in sheet
        assert 'rowspan="8"' in sheet
        _table_is_rectangular(sheet, "CIF VALUE")

    # ---- the structure survives the row coming out ---------------------------
    @pytest.mark.parametrize("terms", ["CIF", "CFR - BEIRA", "FOB"])
    def test_every_sheet_stays_rectangular_whatever_the_terms(self, admin_ctx, terms):
        client, container, admin, company_id = admin_ctx
        charges = {"sea_freight": "100", "insurance": "50", "certification": "20",
                   "other_charges": "10", "discount_amount": "30"}
        item = [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}]

        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": terms, **charges}, item)
        _table_is_rectangular(
            client.get(f"/quotations/{quotation.id}").get_data(as_text=True), "CIF VALUE")

        proforma = container.proforma_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-01-01",
                    "terms_of_delivery": terms, **charges}, item)
        _table_is_rectangular(
            client.get(f"/proforma-invoices/{proforma.id}").get_data(as_text=True), "CIF VALUE")

        invoice = container.export_invoice_service.create(
            admin, {"consignee_name": "Buyer", "invoice_date": "2026-02-20",
                    "export_invoice_number": f"EXP/{terms[:3]}", "tax_mode": "igst",
                    "exchange_rate": "86.70", "nature_of_contract": terms, **charges},
            [{"product_name": "P", "quantity_value": "10", "unit": "SQM", "price_usd": "2"}])
        _table_is_rectangular(
            client.get(f"/export-invoices/{invoice.id}").get_data(as_text=True), "Sr No")

    def test_non_fob_sheet_keeps_the_rows(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        quotation = container.quotation_service.create(
            admin, {"buyer_name": "Buyer", "quotation_date": "2026-01-01",
                    "shipping_terms": "CIF", "sea_freight": "100", "insurance": "50"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        sheet = client.get(f"/quotations/{quotation.id}").get_data(as_text=True)
        assert "SEA FREIGHT" in sheet and "INSURANCE" in sheet
        assert 'rowspan="8"' in sheet            # the full ladder


# ==========================================================================
# Tax Invoice - the read-only INR restatement of an export invoice
# ==========================================================================
class TestTaxInvoiceRoutes:
    """A tax invoice has no form of its own, so every one of these starts from
    a saved export invoice - hence borrowing (not inheriting) the helper that
    posts one, which would otherwise re-run that class's whole suite here."""

    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/tax-invoices/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_sheet_carries_the_export_invoices_own_number_and_date(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "TAX INVOICE" in body
        assert "1000000042" in body
        assert "20-02-2026" in body               # the invoice's own date, dd-mm-yyyy

    def test_money_is_converted_to_inr_at_the_invoices_exchange_rate(self, admin_ctx):
        # 144 SQM @ 5.92 = 852.48, at the helper's 86.70 rate -> 73,910.02,
        # printed with Indian grouping. The per-unit rate converts too:
        # 5.92 * 86.70 = 513.26.
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "73,910.02" in body
        assert "513.26" in body
        assert "86.70" in body                    # the rate it was converted at
        assert "RUPEES" in body                   # spelled out in INR wording
        assert "Total Invoice Value" in body

    def test_igst_is_the_per_product_tax_on_the_converted_total(self, admin_ctx):
        # The catalog product carries 18%, so the tax is charged on the
        # converted line total: 73,910.016 * 18% = 13,303.80.
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "IGST" in body
        assert "13,303.80" in body
        assert "IGST Value In Word" in body

    def test_igst_is_nil_under_lut(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id, extra={"tax_mode": "lut"})
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "NIL" in body
        assert "13,303.80" not in body

    def test_ship_to_is_the_consignee_and_bill_to_the_notify_party(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"notify_name": "ROBUST INTERNATIONAL PTE LTD"})
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "SHIP TO" in body and "BILL TO" in body
        assert "ROBUST INTERNATIONAL PTE LTD" in body

    def test_the_road_leg_comes_off_the_11b_rows_with_the_transporters_gstin(self, admin_ctx):
        """Transporter name / vehicle / LR are typed per container on the
        export invoice's 11B table; the registration number is looked up from
        the Transporters list by the name snapshotted on the row."""
        client, container, admin, company_id = admin_ctx
        container.transporter_service.create(
            admin, {"name": "FORTUNE SHIPPPING PVT LTD",
                    "gstin_transporter_no": "24AADCF9974G1ZB"}, [])
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"cd_transporter_name[]": ["FORTUNE SHIPPPING PVT LTD", ""],
                   "cd_vehicle_no[]": ["GJ12BX4611", "GJ99ZZ0000"],
                   "cd_lr_no[]": ["LR 0001", "LR 0002"]})
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "FORTUNE SHIPPPING PVT LTD" in body
        assert "24AADCF9974G1ZB" in body           # looked up, not stored on the row
        # Vehicle No and LR No name a single one: the first 11B row's, so the
        # second container's are deliberately not printed.
        assert "GJ12BX4611" in body and "GJ99ZZ0000" not in body
        assert "LR 0001" in body and "LR 0002" not in body

    def test_loading_port_pin_comes_from_the_misc_port_list(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        container.misc_list_service.create_port_of_loading(
            admin, {"name": "MUNDRA - INDIA", "pin_code": "370421"})
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"port_of_loading": "MUNDRA - INDIA"})
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "Loading Port PIN Code" in body
        assert "370421" in body

    def test_eway_bill_is_typed_on_the_tax_invoice_and_prints_there(self, admin_ctx):
        """The e-way bill appears on this sheet and nowhere else, so it is
        asked for here rather than on the export invoice form."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        # It is not on the export invoice's form at all.
        ei_form = client.get(f"/export-invoices/{invoice.id}/edit").get_data(as_text=True)
        assert 'name="eway_bill_no"' not in ei_form
        assert 'name="eway_bill_no"' in client.get(
            f"/tax-invoices/{invoice.id}/edit").get_data(as_text=True)

        resp = client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "tax_invoice_number": "", "tax_invoice_date": "",
            "eway_bill_no": "622115137765", "eway_bill_date": "2026-04-22"})
        assert resp.status_code == 302
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.eway_bill_no == "622115137765"
        assert got.eway_bill_date == "2026-04-22"
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "622115137765" in body
        assert "22-04-2026" in body

    def test_edit_form_asks_only_for_the_tax_invoices_own_number_and_date(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}/edit").get_data(as_text=True)
        assert "Tax invoice number" in body and "Tax invoice date" in body
        # The export invoice's own number/date are shown, but read-only.
        assert "Export invoice number" in body and "Export invoice date" in body
        assert 'name="tax_invoice_number"' in body and 'name="tax_invoice_date"' in body
        assert 'name="eway_bill_no"' in body and 'name="eway_bill_date"' in body
        # Nothing else is asked for - no consignee, bank, goods or charges, and
        # the export invoice's own number is shown but not submittable.
        for absent in ('name="consignee_name"', 'name="bank_name"', 'name="exchange_rate"',
                       'name="item_price_usd[]"', 'name="export_invoice_number"'):
            assert absent not in body

    def test_saving_a_number_and_date_changes_what_the_sheet_prints(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "tax_invoice_number": "TI/001/26-27", "tax_invoice_date": "2026-04-22"})
        assert resp.status_code == 302
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.tax_invoice_number == "TI/001/26-27"
        assert got.tax_invoice_date == "2026-04-22"
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "TI/001/26-27" in body
        assert "22-04-2026" in body
        # The export invoice itself is untouched.
        assert got.export_invoice_number == "1000000042"
        assert got.invoice_date == "2026-02-20"

    def test_blank_falls_back_to_the_export_invoices_own_number_and_date(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "tax_invoice_number": "TI/001/26-27", "tax_invoice_date": "2026-04-22"})
        client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "tax_invoice_number": "", "tax_invoice_date": ""})
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.tax_invoice_number is None and got.tax_invoice_date is None
        assert got.tax_invoice_number_printed == "1000000042"
        assert got.tax_invoice_date_printed == "2026-02-20"
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "1000000042" in body and "20-02-2026" in body

    def test_an_over_long_tax_invoice_number_is_rejected(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/tax-invoices/{invoice.id}/edit",
                           data={"tax_invoice_number": "X" * 17, "tax_invoice_date": ""})
        assert resp.status_code == 400
        assert container.export_invoice_service.get(invoice.id, company_id).tax_invoice_number is None

    def test_editing_the_export_invoice_does_not_wipe_the_tax_invoices_fields(self, admin_ctx):
        """The export invoice form never posts any of the four, so they must
        not ride along on its header tuple."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "tax_invoice_number": "TI/001/26-27", "tax_invoice_date": "2026-04-22",
            "eway_bill_no": "622115137765", "eway_bill_date": "2026-04-22"})
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_name[]": "GVT 600X1200", "item_hsn_code[]": "69072100",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92"}, follow_redirects=True)
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.tax_invoice_number == "TI/001/26-27"
        assert got.tax_invoice_date == "2026-04-22"
        assert got.eway_bill_no == "622115137765"
        assert got.eway_bill_date == "2026-04-22"

    def test_editing_another_companys_tax_invoice_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-ti-edit")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-ti-edit", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000002", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/tax-invoices/{rival.id}/edit").status_code == 404
        assert client.post(f"/tax-invoices/{rival.id}/edit",
                           data={"tax_invoice_number": "HACK"}).status_code == 404

    def test_tax_invoice_another_companys_invoice_is_a_404(self, app, client, admin_ctx):
        _, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-ti")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-ti", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000001", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/tax-invoices/{rival.id}").status_code == 404


# ==========================================================================
# BL Draft - the draft bill of lading, printed as PDF and downloaded as .docx
# ==========================================================================
class TestBlDraftRoutes:
    """Header from the export invoice, container table from its export packing
    list. Borrows (not inherits) the helper that posts an export invoice, for
    the same reason TestTaxInvoiceRoutes does."""

    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/bl-drafts/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_sheet_carries_the_invoice_header_and_the_packing_lists_containers(self, admin_ctx):
        # The helper splits 100 boxes across two containers, 60/40, of a
        # product weighing 26.5/27.0 KG net/gross per box:
        #   BLJU2253726 -> gross 1,620.00  net 1,590.00
        #   SEGU3227471 -> gross 1,080.00  net 1,060.00
        #   TOTAL          gross 2,700.00  net 2,650.00
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"booking_no": "EBKG16522374", "vessel_voyage_no": "KOTKA - IV618A",
                   "place_of_receipt": "MUNDRA - INDIA", "port_of_discharge": "BEIRA - MOZAMBIQUE",
                   "final_destination": "BEIRA - MOZAMBIQUE"})
        body = client.get(f"/bl-drafts/{invoice.id}").get_data(as_text=True)
        assert "BL DRAFT" in body
        assert "EBKG16522374" in body                 # booking no
        assert "KOTKA - IV618A" in body               # vessel and voyage
        assert "BEIRA - MOZAMBIQUE" in body           # discharge + delivery
        # Container rows come off the packing list split.
        assert "BLJU2253726" in body and "SEGU3227471" in body
        assert "UFL331090" in body                    # line seal no
        assert "1,620.00" in body and "1,080.00" in body
        assert "2,700.00" in body and "2,650.00" in body
        # The description block restates the invoice reference and weights.
        assert "INVOICE NO : 1000000042" in body
        assert "TOTAL GROSS WEIGHT : 2,700.00 KGS" in body
        assert "TOTAL NET WEIGHT : 2,650.00 KGS" in body

    def test_docx_download_is_a_real_word_package(self, admin_ctx):
        import zipfile, io as _io
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id, extra={"booking_no": "EBKG16522374"})
        resp = client.get(f"/bl-drafts/{invoice.id}/docx")
        assert resp.status_code == 200
        assert resp.mimetype == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert "BL-DRAFT-1000000042.docx" in resp.headers["Content-Disposition"]

        archive = zipfile.ZipFile(_io.BytesIO(resp.data))
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names
        document = archive.read("word/document.xml").decode("utf-8")
        # Well-formed XML, not just bytes that happen to zip.
        from xml.dom import minidom
        minidom.parseString(document)
        # ...and it carries the same data the sheet shows.
        for expected in ("BL DRAFT", "EBKG16522374", "BLJU2253726", "SEGU3227471",
                         "2,700.00", "2,650.00"):
            assert expected in document

    def test_docx_rowspan_emits_matching_merge_cells(self, admin_ctx):
        """The booking-no box spans the shipper and consignee rows: one
        opener plus one continuation, both with the same gridSpan, or Word
        drops the merge."""
        import zipfile, io as _io
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.get(f"/bl-drafts/{invoice.id}/docx")
        document = zipfile.ZipFile(_io.BytesIO(resp.data)).read("word/document.xml").decode("utf-8")
        assert document.count('<w:vMerge w:val="restart"/>') == 1
        assert document.count("<w:vMerge/>") == 1

    def test_an_invoice_with_no_11b_rows_still_renders_both_formats(self, admin_ctx):
        """Saving an export invoice always generates a packing list, so there
        is always a split - but an invoice with no 11B container rows has no
        container NUMBER to show, and no catalog product to weigh. Both
        outputs must still come out rather than erroring on the blanks."""
        client, container, admin, company_id = admin_ctx
        invoice = container.export_invoice_service.create(
            admin, {"export_invoice_number": "1000000099", "invoice_date": "2026-02-20",
                    "consignee_name": "ROBUST INTERNATIONAL", "exchange_rate": "86.70"},
            [{"product_name": "GVT 600X1200", "quantity_value": "144", "price_usd": "5.92"}])
        packing_list = container.export_packing_list_service.get_for_invoice(invoice.id, company_id)
        assert [r["container_no"] for r in packing_list.container_rows] == [None]
        body = client.get(f"/bl-drafts/{invoice.id}").get_data(as_text=True)
        assert "BL DRAFT" in body
        assert "0.00" in body                      # the un-weighed totals row
        assert client.get(f"/bl-drafts/{invoice.id}/docx").status_code == 200

    def test_another_companys_bl_draft_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-bl")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-bl", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000003", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/bl-drafts/{rival.id}").status_code == 404
        assert client.get(f"/bl-drafts/{rival.id}/docx").status_code == 404


# ==========================================================================
# VGM Attachment - derived per-container figures, with two typed cells
# ==========================================================================
class TestVgmAttachmentRoutes:
    """Borrows (not inherits) the export-invoice-posting helper, as the other
    attachment suites do."""

    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/vgm-attachments/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_rows_derive_size_weights_and_the_vgm_total(self, admin_ctx):
        # Two containers booked as 20FT FCL, split 60/40 boxes of a product
        # weighing 27.0 KG gross per box -> cargo 1,620.00 and 1,080.00.
        # Tare comes off the 11B rows (2,250.5 / 2,260), so:
        #   VGM = 2,250.50 + 1,620.00 = 3,870.50
        #   VGM = 2,260.00 + 1,080.00 = 3,340.00
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"booking_no": "EBKG16522374",
                   "container_type[]": ["20FT FCL"], "container_count[]": ["2"],
                   "cd_max_permitted_weight[]": ["30480", "30480"]})
        body = client.get(f"/vgm-attachments/{invoice.id}").get_data(as_text=True)
        assert "VGM ATTACHMENT" in body
        assert "EBKG16522374" in body
        assert "20FT FCL" in body                    # size, expanded from the booking
        assert "30,480.00" in body                   # max permitted weight
        assert "2,250.50" in body and "2,260.00" in body       # tare
        assert "1,620.00" in body and "1,080.00" in body       # cargo, from the packing list
        assert "3,870.50" in body and "3,340.00" in body       # tare + cargo

    def test_the_two_typed_cells_save_per_container(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/vgm-attachments/{invoice.id}", data={
            "vgm_sr_no[]": ["1", "2"],
            "vgm_weighbridge_name[]": ["Morbi", "Rajkot"],
            "vgm_weighing_slip_no[]": ["123", "124"],
        })
        assert resp.status_code == 302
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["weighbridge_name"] for d in details] == ["Morbi", "Rajkot"]
        assert [d["weighing_slip_no"] for d in details] == ["123", "124"]
        body = client.get(f"/vgm-attachments/{invoice.id}").get_data(as_text=True)
        assert 'value="Morbi"' in body and 'value="124"' in body

    def test_saving_the_export_invoice_does_not_wipe_the_vgm_cells(self, admin_ctx):
        """Saving the export invoice rewrites its 11B rows wholesale, so the
        VGM cells must be carried forward by row position."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/vgm-attachments/{invoice.id}", data={
            "vgm_sr_no[]": ["1", "2"],
            "vgm_weighbridge_name[]": ["Morbi", "Rajkot"],
            "vgm_weighing_slip_no[]": ["123", "124"],
        })
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_name[]": "GVT 600X1200", "item_hsn_code[]": "69072100",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92",
            "cd_container_no[]": ["BLJU2253726", "SEGU3227471"],
            "cd_tare_weight[]": ["2250.5", "2260"]}, follow_redirects=True)
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["weighbridge_name"] for d in details] == ["Morbi", "Rajkot"]
        assert [d["weighing_slip_no"] for d in details] == ["123", "124"]

    def test_a_submitted_row_for_an_unknown_container_changes_nothing(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/vgm-attachments/{invoice.id}", data={
            "vgm_sr_no[]": ["99", "not-a-number"],
            "vgm_weighbridge_name[]": ["Ghost", "Ghost"],
            "vgm_weighing_slip_no[]": ["666", "666"],
        })
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert all(d["weighbridge_name"] is None for d in details)
        assert len(details) == 2                      # nothing was created either

    def test_an_untyped_tare_leaves_the_vgm_total_blank(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id, extra={"cd_tare_weight[]": ["", ""]})
        body = client.get(f"/vgm-attachments/{invoice.id}").get_data(as_text=True)
        # Cargo is still known, but tare + cargo cannot be computed.
        assert "1,620.00" in body
        assert "3,870.50" not in body

    def test_another_companys_vgm_attachment_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-vgm")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-vgm", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000004", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/vgm-attachments/{rival.id}").status_code == 404
        assert client.post(f"/vgm-attachments/{rival.id}",
                           data={"vgm_sr_no[]": ["1"]}).status_code == 404


# ==========================================================================
# VGM declaration - the shipper-level sheet that quotes the attachment
# ==========================================================================
class TestVgmDeclarationRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def _with_containers(self, client, container, admin, company_id, count):
        """An export invoice carrying `count` physical containers, each with a
        container no., tare weight and saved weighbridge details."""
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"booking_no": "EBKG16522374",
                   "container_type[]": ["20FT FCL"], "container_count[]": [str(count)],
                   "cd_container_no[]": [f"DFSU288910{i}" for i in range(count)],
                   "cd_line_seal_no[]": [f"LG0054243{i}" for i in range(count)],
                   "cd_rfid_seal_no[]": ["" for _ in range(count)],
                   "cd_max_permitted_weight[]": ["30480" for _ in range(count)],
                   "cd_tare_weight[]": ["2100" for _ in range(count)],
                   "alloc_container_index[]": [str(i) for i in range(count)],
                   "alloc_invoice_item_index[]": ["0" for _ in range(count)],
                   "alloc_boxes[]": [str(100 // count)] * (count - 1) + [
                       str(100 - (100 // count) * (count - 1))],
                   "alloc_group_label[]": ["GLAZED VITRIFIED TILES"] * count,
                   "alloc_pallets[]": [""] * count,
                   "alloc_net_weight[]": [""] * count,
                   "alloc_gross_weight[]": [""] * count})
        client.post(f"/vgm-attachments/{invoice.id}", data={
            "vgm_sr_no[]": [str(i + 1) for i in range(count)],
            "vgm_weighbridge_name[]": ["Morbi"] * count,
            "vgm_weighing_slip_no[]": [str(123 + i) for i in range(count)],
        })
        return invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/vgm-declarations/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_shipper_particulars_come_from_the_invoice_and_our_company(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id, extra={"booking_no": "EBKG16522374"})
        body = client.get(f"/vgm-declarations/{invoice.id}").get_data(as_text=True)
        assert "INFORMATION ABOUT VERIFIED GROSS MASS OF CONTAINER" in body
        assert "EBKG16522374" in body
        assert "Unit Of Measure" in body and "KGS" in body
        # Defaults print without anyone having opened the edit form.
        assert "METHOD-1" in body and "NORMAL" in body and "N/A" in body

    def test_a_single_container_is_listed_in_full(self, admin_ctx):
        """One cell per field means only a one-container shipment fits inline -
        and then there is nothing to attach underneath."""
        client, container, admin, company_id = admin_ctx
        invoice = self._with_containers(client, container, admin, company_id, 1)
        body = client.get(f"/vgm-declarations/{invoice.id}").get_data(as_text=True)
        assert "DFSU2889100" in body
        assert "30,480.00" in body                  # max permissible weight
        assert "Morbi" in body                      # weighbridge
        assert "123" in body                        # slip number
        assert "VGM ATTACHMENT" not in body
        assert "Total VGM Weight" not in body       # no attachment table appended

    def test_several_containers_point_at_the_attachment_and_print_it_below(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._with_containers(client, container, admin, company_id, 4)
        body = client.get(f"/vgm-declarations/{invoice.id}").get_data(as_text=True)
        assert "VGM ATTACHMENT" in body
        # ... and the attachment itself rides along, read-only: every container
        # is enumerated, but the typed cells stay editable only on its own page.
        assert "Total VGM Weight" in body
        assert "DFSU2889100" in body and "DFSU2889103" in body
        assert "vgm_weighbridge_name[]" not in body
        # Shipper-level particulars still print normally.
        assert "EBKG16522374" in body and "KGS" in body

    def test_more_than_ten_containers_leave_the_attachment_on_its_own(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._with_containers(client, container, admin, company_id, 11)
        body = client.get(f"/vgm-declarations/{invoice.id}").get_data(as_text=True)
        assert "VGM ATTACHMENT" in body
        assert "Total VGM Weight" not in body       # too long to ride along
        assert "DFSU2889100" not in body

    def test_the_edit_form_asks_only_for_the_manual_particulars(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/vgm-declarations/{invoice.id}/edit").get_data(as_text=True)
        for name in ("vgm_signatory", "vgm_contact_24x7", "vgm_weighing_method",
                     "vgm_cargo_type", "vgm_hazardous_details"):
            assert f'name="{name}"' in body
        for absent in ('name="booking_no"', 'name="consignee_name"', 'name="cd_container_no[]"'):
            assert absent not in body

    def test_manual_particulars_save_and_override_the_defaults(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/vgm-declarations/{invoice.id}/edit", data={
            "vgm_signatory": "Mr JITENDRA MAKASANA Partner",
            "vgm_contact_24x7": "+91-98795-84347",
            "vgm_weighing_method": "METHOD-2",
            "vgm_cargo_type": "HAZARDOUS",
            "vgm_hazardous_details": "UN1234 CLASS 3",
        })
        assert resp.status_code == 302
        body = client.get(f"/vgm-declarations/{invoice.id}").get_data(as_text=True)
        assert "Mr JITENDRA MAKASANA Partner" in body
        assert "METHOD-2" in body and "METHOD-1" not in body
        assert "HAZARDOUS" in body and "UN1234 CLASS 3" in body

    def test_saving_the_export_invoice_does_not_wipe_the_manual_particulars(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/vgm-declarations/{invoice.id}/edit",
                    data={"vgm_weighing_method": "METHOD-2", "vgm_cargo_type": "REEFER"})
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_name[]": "GVT 600X1200", "item_hsn_code[]": "69072100",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92"}, follow_redirects=True)
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.vgm_weighing_method == "METHOD-2"
        assert got.vgm_cargo_type == "REEFER"

    def test_another_companys_vgm_declaration_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-vgmd")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-vgmd", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000005", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/vgm-declarations/{rival.id}").status_code == 404
        assert client.get(f"/vgm-declarations/{rival.id}/edit").status_code == 404
        assert client.post(f"/vgm-declarations/{rival.id}/edit",
                           data={"vgm_cargo_type": "HACK"}).status_code == 404


# ==========================================================================
# E-Seal sheet - derived per-container cells, with sealing time/date typed
# ==========================================================================
class TestEsealRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/e-seals/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_rows_derive_from_the_invoice_and_its_11b_rows(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"shipping_bill_no": "2620324", "shipping_bill_date": "2026-04-22",
                   "cd_vehicle_no[]": ["GJ12BX4611", "GJ12BX4612"]})
        # The e-way bill lives on the tax invoice form.
        client.post(f"/tax-invoices/{invoice.id}/edit",
                    data={"eway_bill_no": "622115137765"})
        body = client.get(f"/e-seals/{invoice.id}").get_data(as_text=True)
        assert "2620324" in body
        assert "22/04/2026" in body                 # dd/mm/yyyy, this sheet's format
        assert "GJ12BX4611" in body and "GJ12BX4612" in body
        assert "BLJU2253726" in body                # container no
        assert "WIND02432727" in body               # e-seal = the 11B RFID seal
        assert "622115137765" in body

    def test_sealing_time_and_date_save_per_container(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["1", "2"],
            "eseal_sealing_time[]": ["12:20", "13:45"],
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-23"],
        })
        assert resp.status_code == 302
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["sealing_time"] for d in details] == ["12:20", "13:45"]
        assert [d["sealing_date"] for d in details] == ["2026-04-22", "2026-04-23"]
        body = client.get(f"/e-seals/{invoice.id}").get_data(as_text=True)
        assert 'value="12:20"' in body
        assert "23/04/2026" in body                 # the printed twin, dd/mm/yyyy

    def test_labels_carry_format_hints(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/e-seals/{invoice.id}").get_data(as_text=True)
        assert "SHIPPING BILL DATE" in body and "SEALING TIME" in body
        assert "(dd/mm/yyyy)" in body
        assert "(HH:mm)" in body

    def test_sealing_time_is_stored_as_24_hour_hhmm(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["1", "2"],
            "eseal_sealing_time[]": ["9:5", "1845"],       # padded / bare digits
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-22"]})
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["sealing_time"] for d in details] == ["09:05", "18:45"]

    def test_a_time_that_is_not_a_24_hour_time_is_rejected(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        for bad in ("25:00", "12:75", "noon", "12:20 PM"):
            client.post(f"/e-seals/{invoice.id}", data={
                "eseal_sr_no[]": ["1"], "eseal_sealing_time[]": [bad],
                "eseal_sealing_date[]": ["2026-04-22"]})
            details = container.export_invoice_service.get(invoice.id, company_id).container_details
            assert details[0]["sealing_time"] is None, f"{bad!r} should not have been stored"

    def test_saving_the_export_invoice_does_not_wipe_the_sealing_details(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["1", "2"],
            "eseal_sealing_time[]": ["12:20", "13:45"],
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-23"],
        })
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_name[]": "GVT 600X1200", "item_hsn_code[]": "69072100",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92",
            "cd_container_no[]": ["BLJU2253726", "SEGU3227471"],
            "cd_tare_weight[]": ["2250.5", "2260"]}, follow_redirects=True)
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["sealing_time"] for d in details] == ["12:20", "13:45"]
        assert [d["sealing_date"] for d in details] == ["2026-04-22", "2026-04-23"]

    def test_the_vgm_and_eseal_cells_do_not_overwrite_each_other(self, admin_ctx):
        """Both documents write onto the same 11B rows, each owning two
        columns - saving one must leave the other's alone."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/vgm-attachments/{invoice.id}", data={
            "vgm_sr_no[]": ["1", "2"],
            "vgm_weighbridge_name[]": ["Morbi", "Rajkot"],
            "vgm_weighing_slip_no[]": ["123", "124"]})
        client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["1", "2"],
            "eseal_sealing_time[]": ["12:20", "13:45"],
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-23"]})
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert [d["weighbridge_name"] for d in details] == ["Morbi", "Rajkot"]
        assert [d["sealing_time"] for d in details] == ["12:20", "13:45"]

    def test_a_submitted_row_for_an_unknown_container_changes_nothing(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["99", "not-a-number"],
            "eseal_sealing_time[]": ["12:20", "12:20"],
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-22"]})
        details = container.export_invoice_service.get(invoice.id, company_id).container_details
        assert all(d["sealing_time"] is None for d in details)
        assert len(details) == 2

    def test_xlsx_download_is_a_real_workbook(self, admin_ctx):
        import zipfile, io as _io
        from xml.dom import minidom
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"shipping_bill_no": "2620324", "shipping_bill_date": "2026-04-22",
                   "cd_vehicle_no[]": ["GJ12BX4611", "GJ12BX4612"]})
        client.post(f"/e-seals/{invoice.id}", data={
            "eseal_sr_no[]": ["1", "2"],
            "eseal_sealing_time[]": ["12:20", "12:20"],
            "eseal_sealing_date[]": ["2026-04-22", "2026-04-22"]})

        resp = client.get(f"/e-seals/{invoice.id}/xlsx")
        assert resp.status_code == 200
        assert resp.mimetype == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert "E-SEAL-1000000042.xlsx" in resp.headers["Content-Disposition"]

        archive = zipfile.ZipFile(_io.BytesIO(resp.data))
        assert archive.testzip() is None
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                "xl/worksheets/sheet1.xml"} <= set(archive.namelist())
        for part in archive.namelist():
            minidom.parseString(archive.read(part))      # every part is well-formed XML

        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for expected in ("SHIPPING_BILL_NO", "E-Way Bill No", "2620324", "22/04/2026",
                         "GJ12BX4611", "BLJU2253726", "WIND02432727", "12:20"):
            assert expected in sheet
        # Identifiers must be text, or Excel eats leading zeros and coerces
        # anything date-shaped - see the note in app/xlsx.py.
        assert 't="inlineStr"' in sheet
        assert "<v>" not in sheet

    def test_xlsx_headers_match_the_printed_sheet(self, admin_ctx):
        import zipfile, io as _io
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/e-seals/{invoice.id}").get_data(as_text=True)
        sheet = zipfile.ZipFile(_io.BytesIO(
            client.get(f"/e-seals/{invoice.id}/xlsx").data)).read(
                "xl/worksheets/sheet1.xml").decode("utf-8")
        for label in ("SHIPPING_BILL_NO", "SHIPPING BILL DATE", "VEHICLE NUMBER",
                      "CONTAINER NUMBER", "ESEAL NUMBER", "SEALING TIME",
                      "SEALING DATE", "E-Way Bill No"):
            assert label in body and label in sheet

    def test_another_companys_eseal_sheet_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-es")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-es", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000006", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/e-seals/{rival.id}").status_code == 404
        assert client.get(f"/e-seals/{rival.id}/xlsx").status_code == 404
        assert client.post(f"/e-seals/{rival.id}",
                           data={"eseal_sr_no[]": ["1"]}).status_code == 404


# ==========================================================================
# E-Way Bill (multi vehicle) - fully derived, nothing typed
# ==========================================================================
class TestEwayBillRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/eway-bills/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_a_row_per_vehicle_with_totals(self, admin_ctx):
        # The helper splits 100 boxes 60/40 across two containers of a line
        # priced in SQM: 144 SQM total, so 86.40 / 57.60 alt qty. The sheet
        # is container-wise, not per goods line, so no description/HSN print.
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"cd_vehicle_no[]": ["GJ12BX4611", "GJ12BX4612"],
                   "cd_lr_no[]": ["LR 0001", "LR 0002"]})
        client.post(f"/tax-invoices/{invoice.id}/edit", data={
            "eway_bill_no": "622115137765", "eway_bill_date": "2026-04-22"})
        body = client.get(f"/eway-bills/{invoice.id}").get_data(as_text=True)
        assert "622115137765" in body
        assert "22-04-2026" in body
        assert "GJ12BX4611" in body and "GJ12BX4612" in body
        assert "LR 0001" in body and "LR 0002" in body
        assert "Description Of Goods" not in body and "HSNC" not in body
        assert "TOTAL" in body
        assert "144.00" in body                      # alt qty total
        assert "100" in body                         # boxes total

    def test_nothing_on_the_sheet_is_an_input(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/eway-bills/{invoice.id}").get_data(as_text=True)
        # The toolbar has buttons, but the sheet itself takes nothing.
        sheet = body[body.index('class="ewb-sheet'):]
        assert "<input" not in sheet and "<form" not in sheet

    def test_another_companys_eway_bill_sheet_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-ewb")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-ewb", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000007", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/eway-bills/{rival.id}").status_code == 404


# ==========================================================================
# Commercial Invoice (BRC copy) - fully derived, no form at all
# ==========================================================================
class TestCommercialInvoiceRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/commercial-invoices/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_it_carries_the_export_invoices_own_number_and_date(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "COMMERCIAL INVOICE" in body
        assert "1000000042" in body
        assert "20-02-2026" in body

    def test_money_is_the_invoices_own_currency_with_indian_grouping(self, admin_ctx):
        # 144 SQM @ 5.92 = 852.48 CIF, less a 100.00 discount and a 50.00
        # insurance -> FOB 702.48. Grouping only shows past 3 digits, so this
        # also checks the ladder itself reconciles.
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(
            client, container, admin, company_id,
            extra={"discount_amount": "100", "insurance": "50",
                   "nature_of_contract": "CIF"})
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "CIF Invoice Value" in body
        assert "852.48" in body
        assert "100.00" in body and "50.00" in body
        assert "702.48" in body                    # FOB = CIF - discount - charges
        assert "FOB Value" in body
        # No intermediate Invoice Value line on this sheet.
        assert ">Invoice Value<" not in body

    def test_the_amount_in_words_is_the_cif_invoice_value(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "Invoice Value In Word" in body
        assert "EIGHT HUNDRED FIFTY-TWO" in body   # 852.48, the CIF value
        assert "CENTS FORTY-EIGHT" in body

    def test_weights_come_from_the_packing_list(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "2,650.00" in body                  # net
        assert "2,700.00" in body                  # gross

    def test_there_is_no_form_and_no_edit_route(self, app, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "<input" not in body and "<form" not in body
        # The blueprint exposes a list and a view, nothing else.
        rules = {str(r) for r in app.url_map.iter_rules() if "commercial-invoices" in str(r)}
        assert rules == {"/commercial-invoices/", "/commercial-invoices/<int:export_invoice_id>"}
        assert client.post(f"/commercial-invoices/{invoice.id}").status_code == 405

    def test_another_companys_commercial_invoice_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-ci")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-ci", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000008", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/commercial-invoices/{rival.id}").status_code == 404


# ==========================================================================
# Commercial Invoice Packing List - derived, with only the BL pair typed
# ==========================================================================
class TestCommercialPackingListRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/commercial-packing-lists/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_rows_come_from_the_packing_list_split_with_totals(self, admin_ctx):
        # 100 boxes split 60/40 across two containers, 26.5/27.0 KG per box:
        #   net 1,590.00 / 1,060.00  gross 1,620.00 / 1,080.00
        #   TOTAL net 2,650.00, gross 2,700.00, alt qty 144.00
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-packing-lists/{invoice.id}").get_data(as_text=True)
        assert "COMMERCIAL INVOICE PACKING LIST" in body
        assert "BLJU2253726" in body and "SEGU3227471" in body      # container nos
        assert "UFL331090" in body                                  # line seal
        assert "WIND02432727" in body                               # RFID seal
        assert "1,590.00" in body and "1,620.00" in body
        assert "2,650.00" in body and "2,700.00" in body
        assert "144.00" in body

    def test_it_shares_the_commercial_invoices_header(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        pl = client.get(f"/commercial-packing-lists/{invoice.id}").get_data(as_text=True)
        ci = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        for shared in ("Buyer If Other Then Consignee[Notify]", "Nature Of Contract",
                       "SWIFT Code", "1000000042", "ROBUST INTERNATIONAL"):
            assert shared in pl and shared in ci

    def test_the_edit_form_asks_only_for_the_bill_of_lading_pair(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-packing-lists/{invoice.id}/edit").get_data(as_text=True)
        assert 'name="bill_of_lading_no"' in body and 'name="bill_of_lading_date"' in body
        # The export invoice's own number and date are shown, read-only.
        assert "Invoice number" in body and "Invoice date" in body
        assert "1000000042" in body
        for absent in ('name="export_invoice_number"', 'name="consignee_name"',
                       'name="item_price_usd[]"', 'name="cd_container_no[]"'):
            assert absent not in body

    def test_the_bill_of_lading_pair_saves_and_prints(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        resp = client.post(f"/commercial-packing-lists/{invoice.id}/edit", data={
            "bill_of_lading_no": "MEDUJ1234567", "bill_of_lading_date": "2026-04-22"})
        assert resp.status_code == 302
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.bill_of_lading_no == "MEDUJ1234567"
        assert got.bill_of_lading_date == "2026-04-22"
        body = client.get(f"/commercial-packing-lists/{invoice.id}").get_data(as_text=True)
        assert "MEDUJ1234567" in body
        assert "22-04-2026" in body

    def test_saving_the_export_invoice_does_not_wipe_the_bill_of_lading(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        client.post(f"/commercial-packing-lists/{invoice.id}/edit", data={
            "bill_of_lading_no": "MEDUJ1234567", "bill_of_lading_date": "2026-04-22"})
        client.post(f"/export-invoices/{invoice.id}/edit", data={
            "export_invoice_number": "1000000042", "invoice_date": "2026-02-20",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "item_product_name[]": "GVT 600X1200", "item_hsn_code[]": "69072100",
            "item_quantity_boxes[]": "100", "item_quantity_value[]": "144",
            "item_unit[]": "SQM", "item_price_usd[]": "5.92"}, follow_redirects=True)
        got = container.export_invoice_service.get(invoice.id, company_id)
        assert got.bill_of_lading_no == "MEDUJ1234567"
        assert got.bill_of_lading_date == "2026-04-22"

    def test_another_companys_packing_list_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-cpl")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-cpl", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000009", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/commercial-packing-lists/{rival.id}").status_code == 404
        assert client.get(f"/commercial-packing-lists/{rival.id}/edit").status_code == 404
        assert client.post(f"/commercial-packing-lists/{rival.id}/edit",
                           data={"bill_of_lading_no": "HACK"}).status_code == 404


# ==========================================================================
# Commercial Invoice (customer copy) - the goods column priced at FOB
# ==========================================================================
class TestCustomerInvoiceRoutes:
    _create_export_invoice = TestExportPackingListRoutes._create_export_invoice

    def _priced(self, client, container, admin, company_id):
        """144 SQM @ 5.92 = 852.48 CIF. Charges + discount = 100 + 50 + 20 =
        170, spread over 144 SQM = 1.18055../unit, so the FOB rate is
        5.92 - 1.1806 = 4.7394 -> 4.74 rounded, and the line total follows:
        4.74 * 144 = 682.56. Adding the 170 back gives 852.56 - eight cents
        away from the exact 852.48, which is the rate rounding.

        That eight cents is `fob_priced_round_off`: it prints as its own
        ROUND-OFF row so the sheet closes on 852.48, the SAME CIF value the
        export invoice and every other document quotes, while its own column
        still multiplies and adds out. This sheet used to close on 852.56
        instead - footing internally but disagreeing with the ladder
        elsewhere - which is the trade the round-off row removes."""
        return self._create_export_invoice(
            client, container, admin, company_id,
            extra={"nature_of_contract": "CIF", "sea_freight": "100",
                   "insurance": "50", "discount_amount": "20"})

    def test_list_page_renders(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        self._create_export_invoice(client, container, admin, company_id)
        resp = client.get("/customer-invoices/")
        assert resp.status_code == 200
        assert b"1000000042" in resp.data

    def test_rate_is_the_fob_rate_and_the_total_follows_from_it(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._priced(client, container, admin, company_id)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "4.74" in body                      # FOB rate, not the 5.92 CIF rate
        assert "682.56" in body                    # 4.74 x 144, the line total
        assert "5.92" not in body

    def test_the_ladder_runs_upwards_from_fob_to_cif(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._priced(client, container, admin, company_id)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "FOB Value" in body
        assert "Total CIF Invoice Value" in body
        # FOB 682.56 + sea freight 100 + insurance 50 + discount 20 = 852.56,
        # then the -0.08 round-off brings it back onto the exact CIF value.
        assert "852.48" in body
        assert "Round-off" in body
        assert "-0.08" in body
        for charge in ("Sea Freight", "Insurance", "Discount", "Other Charges"):
            assert charge in body

    def test_the_goods_column_adds_up_to_the_printed_fob_value(self, admin_ctx):
        """The point of rounding the rate first: a customer multiplying the
        printed rate by the printed quantity must get the printed total, and
        the totals must foot."""
        client, container, admin, company_id = admin_ctx
        invoice = self._priced(client, container, admin, company_id)
        got = container.export_invoice_service.get(invoice.id, company_id)
        lines = got.fob_priced_lines()
        assert round(sum(line["rate"] * line["item"].quantity_value for line in lines), 2) \
            == got.fob_priced_total
        assert round(got.fob_priced_total + got.charges_total + got.discount_amount, 2) \
            == got.fob_priced_cif_total

    def test_the_amount_in_words_is_the_cif_total(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._priced(client, container, admin, company_id)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "Invoice Value In Word" in body
        assert "EIGHT HUNDRED FIFTY-TWO" in body
        assert "CENTS FORTY-EIGHT" in body         # 852.48, the shared CIF total

    def test_it_is_read_only(self, app, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_export_invoice(client, container, admin, company_id)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "<input" not in body and "<form" not in body
        rules = {str(r) for r in app.url_map.iter_rules() if "customer-invoices" in str(r)}
        assert rules == {"/customer-invoices/", "/customer-invoices/<int:export_invoice_id>"}

    def test_another_companys_customer_invoice_is_a_404(self, admin_ctx):
        client, container, admin, _ = admin_ctx
        other = container.tenant_repo.create("Rival", "rival-cust")
        rival_admin = container.auth_service.create_user(
            other.id, "rival-cust", "pw123456", "Rival", "admin")
        rival = container.export_invoice_service.create(
            rival_admin, {"export_invoice_number": "9000000010", "invoice_date": "2026-03-01",
                          "consignee_name": "RIVAL BUYER", "exchange_rate": "80"},
            [{"product_name": "P", "quantity_value": "10", "price_usd": "2"}])
        assert client.get(f"/customer-invoices/{rival.id}").status_code == 404

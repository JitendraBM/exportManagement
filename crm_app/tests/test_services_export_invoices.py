"""
Tests for ExportInvoiceService (app/services.py) - the customer/customs-
facing document at the buyer end of the pipeline. Mirrors
test_services_proforma_po.py / test_services_purchase_invoices.py, focusing
on what is unique to this document type:

  - references MANY proforma invoices at once (many-to-many),
  - prefill that walks each PI -> its purchase orders -> their purchase
    invoices to import EPCG / export-under / supplier-exemption rows,
  - per-product tax computed and summed into IGST vs CGST/SGST,
  - a manual exchange rate that only an admin can change once set,
  - the container-count -> section-11B rows, and the optional shipping
    bill PDF upload.
"""

import io

import pytest
from werkzeug.datastructures import FileStorage

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError


def upload(filename="shipping-bill.pdf", data=b"fake-pdf-bytes"):
    return FileStorage(stream=io.BytesIO(data), filename=filename)


def make_company(container, seed, gstin="24AABFO8212B1ZV", declaration="DECL", lut="AD240225016083O",
                 government_schemes=""):
    container.company_repo.upsert(seed.company_id, "AAYU EXIM", "MORBI", gstin, "AABFO8212B", "IEC1", declaration,
                                  government_schemes=government_schemes or None)
    if lut:
        oc = container.company_repo.get(seed.company_id)
        container.company_repo.replace_lut_details(oc.id, [{"lut_number": lut, "financial_year": "2024-25", "is_primary": True}])
    container.company_repo.replace_contact_persons(
        container.company_repo.get(seed.company_id).id,
        [{"name": "Mr. Jignesh", "designation": "Partner Of Aayu Exim", "is_primary": True}],
    )


def make_product(container, seed, name="Tiles", igst="18"):
    return container.product_service.create_product(
        current_user=seed.admin, product_name=name, description="", hsn_code="69072100",
        igst_percent=igst, quantity="", alternate_quantity="")


def make_proforma(container, seed, product=None, buyer_order_no="EXP/003", **over):
    fields = {"consignee_name": "ROBUST INTERNATIONAL", "invoice_date": "2026-01-06",
              "consignee_address": "BEIRA", "buyer_order_no": buyer_order_no,
              "port_of_loading": "MUNDRA", "country_of_destination": "MOZAMBIQUE"}
    fields.update(over)
    item = {"product_name": product.product_name if product else "Tiles", "quantity_value": "100",
            "price_usd": "5.92", "hsn_code": "69072100", "unit": "SQM"}
    if product:
        item["product_id"] = str(product.id)
    return container.proforma_invoice_service.create(seed.admin, fields, [item])


def make_export(container, seed, proforma_ids=None, items=None, export_invoice_number="1000000001", **over):
    fields = {"consignee_name": "ROBUST INTERNATIONAL", "invoice_date": "2026-02-20",
              "tax_mode": "igst", "exchange_rate": "86.70", "export_invoice_number": export_invoice_number}
    if proforma_ids:
        fields["proforma_invoice_ids"] = [str(p) for p in proforma_ids]
    fields.update(over)
    raw_items = items or [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}]
    return container.export_invoice_service.create(seed.admin, fields, raw_items)


# ==========================================================================
# Basic create / read / update / delete
# ==========================================================================
class TestExportCrud:
    def test_create_persists_the_typed_number(self, container, seed):
        inv = make_export(container, seed, export_invoice_number="1234567890")
        assert inv.export_invoice_number == "1234567890"
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.consignee_name == "ROBUST INTERNATIONAL"
        assert len(got.items) == 1

    def test_fob_pricing_spreads_the_charges_and_books_a_round_off(self, container, seed):
        # The shared uplift (services.apply_fob_uplift): 100 of charges over
        # 3 units is 33.333..., so the printed price rounds to 40.33 and the
        # missing cent becomes the ROUND-OFF row. Tax follows the CIF price.
        inv = make_export(container, seed, nature_of_contract="CIF", sea_freight="100",
                          fob_pricing="1",
                          items=[{"product_name": "Tiles", "quantity_value": "3", "unit": "SQM",
                                  "price_usd": "7", "igst_percent": "18"}])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        item = got.items[0]
        assert (item.fob_price_usd, item.price_usd, item.total_usd) == (7.0, 40.33, 120.99)
        assert got.round_off == 0.01
        assert got.cif_value_usd == pytest.approx(121.0)
        assert got.fob_value_usd == pytest.approx(21.0)   # exactly 3 x the typed 7

    def test_without_fob_pricing_the_export_prices_are_untouched(self, container, seed):
        got = container.export_invoice_service.get(
            make_export(container, seed, nature_of_contract="CIF", sea_freight="100").id, seed.company_id)
        assert (got.fob_pricing, got.round_off) == (False, 0)
        assert got.items[0].price_usd == 5.92
        assert got.items[0].fob_price_usd is None

    def test_fob_nature_of_contract_drops_sea_freight_and_insurance(self, container, seed):
        # FOB puts the ocean leg on the buyer - neither charge is stored, and
        # the printed sheet drops both rows with them.
        inv = make_export(container, seed, nature_of_contract="FOB",
                          sea_freight="100", insurance="20")
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.sea_freight == 0
        assert got.insurance == 0

    def test_cfr_nature_of_contract_drops_only_the_insurance(self, container, seed):
        # CFR keeps the freight with the seller and moves only the cargo
        # insurance to the buyer, so only that row leaves the printed sheet.
        inv = make_export(container, seed, nature_of_contract="CFR - BEIRA",
                          sea_freight="100", insurance="20")
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.sea_freight == 100
        assert got.insurance == 0

    def test_number_is_required(self, container, seed):
        with pytest.raises(ValidationError):
            make_export(container, seed, export_invoice_number="")

    def test_number_allows_free_text(self, container, seed):
        inv = make_export(container, seed, export_invoice_number="EXP/AB-001")
        assert inv.export_invoice_number == "EXP/AB-001"

    def test_number_must_be_at_most_16_chars(self, container, seed):
        with pytest.raises(ValidationError):
            make_export(container, seed, export_invoice_number="1" * 17)

    def test_number_must_be_unique_per_company(self, container, seed):
        make_export(container, seed, export_invoice_number="5555555555")
        with pytest.raises(ValidationError):
            make_export(container, seed, export_invoice_number="5555555555")

    def test_get_is_tenant_scoped(self, container, seed):
        inv = make_export(container, seed)
        other = container.tenant_repo.create("Other Co", "other")
        with pytest.raises(NotFoundError):
            container.export_invoice_service.get(inv.id, other.id)

    def test_requires_a_consignee(self, container, seed):
        with pytest.raises(ValidationError):
            make_export(container, seed, consignee_name="")

    def test_requires_at_least_one_item(self, container, seed):
        with pytest.raises(ValidationError):
            container.export_invoice_service.create(
                seed.admin, {"consignee_name": "X", "invoice_date": "2026-02-20"}, [])

    def test_delete_removes_it(self, container, seed):
        inv = make_export(container, seed)
        container.export_invoice_service.delete(seed.admin, inv.id)
        with pytest.raises(NotFoundError):
            container.export_invoice_service.get(inv.id, seed.company_id)

    def test_examination_date_defaults_to_creation_date_not_edit(self, container, seed):
        inv = make_export(container, seed)  # no examination_date given
        assert inv.examination_date == inv.invoice_date
        # editing later (with a new invoice_date) does not move examination_date
        updated = container.export_invoice_service.update(
            seed.admin, inv.id,
            {"consignee_name": "ROBUST", "invoice_date": "2026-03-01", "exchange_rate": "86.70", "export_invoice_number": "1000000001"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])
        assert updated.examination_date == "2026-02-20"


# ==========================================================================
# Many-to-many links + prefill from proforma invoices
# ==========================================================================
class TestExportProformaLinks:
    def test_links_multiple_proformas(self, container, seed):
        p1 = make_proforma(container, seed)
        p2 = make_proforma(container, seed)
        inv = make_export(container, seed, proforma_ids=[p1.id, p2.id])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert set(got.proforma_invoice_ids) == {p1.id, p2.id}
        assert len(got.linked_proformas) == 2

    def test_rejects_proformas_from_different_buyers(self, container, seed):
        p1 = make_proforma(container, seed, consignee_name="ROBUST INTERNATIONAL")
        p2 = make_proforma(container, seed, consignee_name="OTHER BUYER LTD")
        with pytest.raises(ValidationError):
            make_export(container, seed, proforma_ids=[p1.id, p2.id])

    def test_prefill_merges_goods_from_all_selected_pis(self, container, seed):
        p1 = make_proforma(container, seed)
        p2 = make_proforma(container, seed)
        built = container.export_invoice_service.build_prefill_from_proformas([p1.id, p2.id], seed.company_id)
        assert len(built["items"]) == 2
        assert built["fields"]["consignee_name"] == "ROBUST INTERNATIONAL"

    def test_prefill_takes_buyer_order_from_first_pi_that_has_one(self, container, seed):
        # All PIs under one export invoice share the same buyer order, so the
        # prefill is a single field taken from the first PI that has one.
        p1 = make_proforma(container, seed, buyer_order_no="")
        p2 = make_proforma(container, seed, buyer_order_no="EXP/002")
        built = container.export_invoice_service.build_prefill_from_proformas([p1.id, p2.id], seed.company_id)
        assert built["fields"]["buyer_order_no"] == "EXP/002"

    def test_prefill_sums_charges_from_all_selected_pis(self, container, seed):
        p1 = make_proforma(container, seed, sea_freight="100", insurance="20", certification="5",
                            other_charges="10", discount_amount="2")
        p2 = make_proforma(container, seed, sea_freight="50", insurance="30", certification="0",
                            other_charges="0", discount_amount="8")
        built = container.export_invoice_service.build_prefill_from_proformas([p1.id, p2.id], seed.company_id)
        fields = built["fields"]
        assert fields["sea_freight"] == 150
        assert fields["insurance"] == 50
        assert fields["certification"] == 5
        assert fields["other_charges"] == 10
        assert fields["discount_amount"] == 10

    def test_prefill_nature_of_contract_from_first_pi_terms_of_delivery(self, container, seed):
        p1 = make_proforma(container, seed, terms_of_delivery="CNF- (Beira)")
        p2 = make_proforma(container, seed, terms_of_delivery="FOB- (Mundra)")
        built = container.export_invoice_service.build_prefill_from_proformas([p1.id, p2.id], seed.company_id)
        assert built["fields"]["nature_of_contract"] == "CNF- (Beira)"

    def test_prefill_export_under_from_company_government_schemes(self, container, seed):
        make_company(container, seed, government_schemes="WE INTEND TO CLAIM RoDTEP & DBK")
        p1 = make_proforma(container, seed)
        built = container.export_invoice_service.build_prefill_from_proformas([p1.id], seed.company_id)
        assert built["fields"]["export_under"] == "WE INTEND TO CLAIM RoDTEP & DBK"

    def test_prefill_ignores_other_companys_pi(self, container, seed):
        other = container.tenant_repo.create("Other Co", "other")
        other_admin = container.auth_service.create_user(
            company_id=other.id, username="o", password="pass-123456", full_name="O", role="admin")
        p_other = make_proforma(container, type("S", (), {"admin": other_admin, "company_id": other.id}))
        built = container.export_invoice_service.build_prefill_from_proformas([p_other.id], seed.company_id)
        assert built["items"] == []


# ==========================================================================
# Import EPCG / supplier-exemption details through the PI -> PO -> PInv chain
# ==========================================================================
class TestExportChainImport:
    def _chain(self, container, seed, purchase_type="exemption"):
        make_company(container, seed)
        product = make_product(container, seed)
        pi = make_proforma(container, seed, product=product)
        po = container.purchase_order_service.create(
            seed.admin,
            {"seller_name": "Alive Granito", "po_date": "2026-01-10", "seller_gstin": "24ABVFA1170D1ZO",
             "proforma_invoice_id": str(pi.id), "purchase_type": purchase_type},
            [{"product_name": "Tiles", "product_id": str(product.id), "quantity_boxes": "10",
              "quantity_value": "100", "price_inr": "500", "price_per": "BOX"}])
        pinv = container.purchase_invoice_service.create(
            seed.admin,
            {"seller_name": "Alive Granito", "invoice_number": "GSTT/4987", "invoice_date": "2026-01-15",
             "seller_gstin": "24ABVFA1170D1ZO", "purchase_order_id": str(po.id),
             "epcg_number": "2431000888", "epcg_date": "2021-09-17"},
            [{"product_name": "Tiles", "quantity_value": "100", "price_inr": "500", "price_per": "BOX",
              "quantity_boxes": "10"}], [])
        return pi, po, pinv

    def test_imports_epcg_from_purchase_invoice(self, container, seed):
        pi, po, pinv = self._chain(container, seed)
        built = container.export_invoice_service.build_prefill_from_proformas([pi.id], seed.company_id)
        assert built["fields"]["epcg_number"] == "2431000888"
        assert built["fields"]["epcg_date"] == "2021-09-17"

    def test_imports_supplier_exemption_purchase_details(self, container, seed):
        pi, po, pinv = self._chain(container, seed, purchase_type="exemption")
        built = container.export_invoice_service.build_prefill_from_proformas([pi.id], seed.company_id)
        pd = built["purchase_details"]
        assert len(pd) == 1
        assert pd[0]["supplier_gstin"] == "24ABVFA1170D1ZO"
        assert pd[0]["supplier_invoice_no"] == "GSTT/4987"

    def test_full_tax_purchase_contributes_no_purchase_detail_row(self, container, seed):
        pi, po, pinv = self._chain(container, seed, purchase_type="full_tax")
        built = container.export_invoice_service.build_prefill_from_proformas([pi.id], seed.company_id)
        assert built["purchase_details"] == []


# ==========================================================================
# Per-product tax
# ==========================================================================
class TestExportTax:
    def test_tax_is_per_product_summed_into_igst(self, container, seed):
        make_company(container, seed)
        p18 = make_product(container, seed, name="WallTile", igst="18")
        p5 = make_product(container, seed, name="Adhesive", igst="5")
        inv = make_export(container, seed, exchange_rate="100", tax_mode="igst", items=[
            {"product_name": "WallTile", "product_id": str(p18.id), "quantity_value": "10", "unit": "SQM", "price_usd": "10"},
            {"product_name": "Adhesive", "product_id": str(p5.id), "quantity_value": "10", "unit": "SQM", "price_usd": "10"},
        ])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        # line1: 100usd*100rate*18% = 1800 ; line2: 100usd*100rate*5% = 500
        assert round(got.tax_total_inr, 2) == 2300.0
        assert round(got.igst_amount_inr, 2) == 2300.0

    def test_lut_mode_is_zero_rated(self, container, seed):
        make_company(container, seed)
        p18 = make_product(container, seed, name="WallTile", igst="18")
        inv = make_export(container, seed, exchange_rate="100", tax_mode="lut", items=[
            {"product_name": "WallTile", "product_id": str(p18.id), "quantity_value": "10", "unit": "SQM", "price_usd": "10"},
        ])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert round(got.tax_total_inr, 2) == 1800.0
        assert got.igst_amount_inr == 0


# ==========================================================================
# Manual, admin-locked exchange rate
# ==========================================================================
class TestExportExchangeRate:
    def test_admin_can_change_rate(self, container, seed):
        inv = make_export(container, seed, exchange_rate="86.70")
        updated = container.export_invoice_service.update(
            seed.admin, inv.id, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "90", "export_invoice_number": "1000000002"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])
        assert updated.exchange_rate == 90

    def test_non_admin_owner_cannot_change_rate(self, container, seed):
        inv = container.export_invoice_service.create(
            seed.employee, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "86.70", "export_invoice_number": "1000000002"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])
        with pytest.raises(PermissionDeniedError):
            container.export_invoice_service.update(
                seed.employee, inv.id, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "99", "export_invoice_number": "1000000003"},
                [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])

    def test_non_admin_blank_rate_keeps_stored_value(self, container, seed):
        inv = container.export_invoice_service.create(
            seed.employee, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "86.70", "export_invoice_number": "1000000002"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])
        updated = container.export_invoice_service.update(
            seed.employee, inv.id, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "", "export_invoice_number": "1000000004"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}])
        assert updated.exchange_rate == 86.70


# ==========================================================================
# Child lists
# ==========================================================================
class TestExportChildLists:
    def test_containers_and_11b_rows_round_trip(self, container, seed):
        inv = make_export(
            container, seed,
            containers=[{"container_type": "20FT FCL", "container_count": "2"}],
            container_details_list=[
                {"container_type": "20FT FCL", "container_no": "ABCD1234", "line_seal_no": "LS1",
                 "rfid_seal_no": "RF1", "vehicle_no": "GJ01", "tare_weight": "2200"}],
        )
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.total_containers == 2
        assert got.container_details[0]["container_no"] == "ABCD1234"
        assert got.container_details[0]["tare_weight"] == "2200"

    def test_gross_and_net_weight_have_no_form_field_but_survive_edits(self, container, seed):
        # No form field sets these - they start out blank.
        inv = make_export(
            container, seed,
            containers=[{"container_type": "20FT FCL", "container_count": "1"}],
            container_details_list=[{"container_no": "ABCD1234", "tare_weight": "2200"}],
        )
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.container_details[0]["gross_weight"] is None
        assert got.container_details[0]["net_weight"] is None

        # Simulate them being set some other way (outside this form).
        container.export_invoice_repo.db.execute(
            "UPDATE export_invoice_container_details SET gross_weight = ?, net_weight = ? "
            "WHERE export_invoice_id = ?", ("5000", "2800", inv.id))

        # An unrelated edit through the service - the form always resubmits
        # every current 11B row's editable fields (container_no/tare_weight
        # etc.), but never gross/net weight, since they aren't form fields.
        updated = container.export_invoice_service.update(
            seed.admin, inv.id,
            {"consignee_name": "NEW NAME", "invoice_date": "2026-02-20",
             "export_invoice_number": inv.export_invoice_number,
             "containers": [{"container_type": "20FT FCL", "container_count": "1"}],
             "container_details_list": [{"container_no": "ABCD1234", "tare_weight": "2200"}]},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}],
        )
        assert updated.container_details[0]["gross_weight"] == "5000"
        assert updated.container_details[0]["net_weight"] == "2800"
        assert updated.container_details[0]["tare_weight"] == "2200"

    def test_11b_tare_weight_round_trips(self, container, seed):
        inv = make_export(
            container, seed,
            container_details_list=[
                {"container_no": "ABCD1234", "line_seal_no": "LS1", "rfid_seal_no": "RF1",
                 "vehicle_no": "GJ01", "tare_weight_kg": "2250.5"}],
        )
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.container_details[0]["tare_weight_kg"] == 2250.5

    def test_11b_tare_weight_must_be_a_number(self, container, seed):
        with pytest.raises(ValidationError):
            make_export(
                container, seed,
                container_details_list=[{"container_no": "ABCD1234", "tare_weight_kg": "heavy"}],
            )

    def test_a_row_carrying_only_a_tare_weight_is_still_kept(self, container, seed):
        """Blank 11B rows are dropped, and the export packing list's split
        indexes into what survives - so a row is 'filled in' if ANY column
        is, tare weight included, or the container numbering would shift."""
        inv = make_export(
            container, seed,
            container_details_list=[
                {"container_no": "", "line_seal_no": "", "rfid_seal_no": "", "vehicle_no": "",
                 "tare_weight_kg": "2100"},
                {"container_no": "", "line_seal_no": "", "rfid_seal_no": "", "vehicle_no": "",
                 "tare_weight_kg": ""}],
        )
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert len(got.container_details) == 1
        assert got.container_details[0]["tare_weight_kg"] == 2100

    def test_buyer_order_no_and_date_round_trip(self, container, seed):
        inv = make_export(container, seed, buyer_order_no="EXP/1", buyer_order_date="2026-02-01")
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.buyer_order_no == "EXP/1"
        assert got.buyer_order_date == "2026-02-01"

    def test_purchase_details_round_trip(self, container, seed):
        inv = make_export(container, seed, purchase_details=[
            {"supplier_gstin": "24ABVFA1170D1ZO", "supplier_invoice_no": "GSTT/4987"}])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.purchase_details[0]["supplier_invoice_no"] == "GSTT/4987"

    def test_11b_row_round_trip_without_dropped_fields(self, container, seed):
        # excise_seal_no/plts/boxes were dropped in v37 - anything still
        # sending them is ignored rather than stored.
        inv = make_export(container, seed, container_details_list=[
            {"container_no": "BLJU2253726", "tare_weight": "3800",
             "excise_seal_no": "WIND02432727", "plts": "24", "boxes": "1919"}])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        cd = got.container_details[0]
        assert cd["container_no"] == "BLJU2253726"
        assert cd["tare_weight"] == "3800"
        assert not {"excise_seal_no", "plts", "boxes"} & set(cd)

    def test_booking_no_round_trip(self, container, seed):
        inv = make_export(container, seed, booking_no="BKG/12345")
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.booking_no == "BKG/12345"

    def test_11b_lr_transporter_and_max_weight_round_trip(self, container, seed):
        inv = make_export(container, seed, container_details_list=[
            {"container_no": "BLJU2253726", "vehicle_no": "GJ01AB1234", "lr_no": "LR/2026/88",
             "transporter_name": "SHREE ROAD LINES", "max_permitted_weight": "36000"}])
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        cd = got.container_details[0]
        assert cd["lr_no"] == "LR/2026/88"
        assert cd["transporter_name"] == "SHREE ROAD LINES"
        assert cd["max_permitted_weight"] == "36000"

    def test_vessel_voyage_no_round_trip(self, container, seed):
        inv = make_export(container, seed, vessel_voyage_no="MSC ANNA / VOY 214W")
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.vessel_voyage_no == "MSC ANNA / VOY 214W"

    def test_weight_totals_and_shipping_bill_round_trip(self, container, seed):
        inv = make_export(
            container, seed, total_net_weight_kg="244019.00", total_gross_weight_kg="248099.00",
            shipping_bill_no="SB-9001",
        )
        got = container.export_invoice_service.get(inv.id, seed.company_id)
        assert got.total_net_weight_kg == 244019.00
        assert got.total_gross_weight_kg == 248099.00
        assert got.shipping_bill_no == "SB-9001"


# ==========================================================================
# Shipping bill PDF + version history
# ==========================================================================
class TestExportPdfAndVersions:
    def test_shipping_bill_pdf_upload_and_remove(self, container, seed):
        inv = container.export_invoice_service.create(
            seed.admin, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "86.70", "export_invoice_number": "1000000002"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}],
            pdf_file=upload())
        assert inv.shipping_bill_pdf_path
        removed = container.export_invoice_service.update(
            seed.admin, inv.id, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "86.70", "export_invoice_number": "1000000002"},
            [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}],
            remove_pdf=True)
        assert removed.shipping_bill_pdf_path is None

    def test_rejects_non_pdf_shipping_bill(self, container, seed):
        with pytest.raises(ValidationError):
            container.export_invoice_service.create(
                seed.admin, {"consignee_name": "R", "invoice_date": "2026-02-20", "exchange_rate": "86.70", "export_invoice_number": "1000000002"},
                [{"product_name": "Tiles", "quantity_value": "100", "unit": "SQM", "price_usd": "5.92"}],
                pdf_file=upload("evil.exe"))

    def test_version_recorded_and_rehydrates(self, container, seed):
        inv = make_export(container, seed)
        versions = container.document_version_service.list_for_document("export_invoice", inv.id)
        assert len(versions) == 1
        doc, ver = container.document_version_service.get_version("export_invoice", inv.id, versions[0].version_number)
        assert doc.export_invoice_number == inv.export_invoice_number
        assert len(doc.items) == 1


# ==========================================================================
# Currency (picked from Administration -> Miscellaneous, snapshotted here)
# ==========================================================================
class TestExportInvoiceCurrency:
    def test_defaults_to_usd_when_nothing_is_picked(self, container, seed):
        invoice = make_export(container, seed)
        assert invoice.currency_code is None
        assert invoice.currency_label == "USD [ $ ]"

    def test_picked_currency_snapshots_its_symbol(self, container, seed):
        container.misc_list_service.create_currency(seed.admin, {"name": "JPY", "symbol": "¥"})
        invoice = make_export(container, seed, currency_code="JPY")
        assert (invoice.currency_code, invoice.currency_symbol) == ("JPY", "¥")
        assert invoice.currency_label == "JPY [ ¥ ]"

    def test_editing_the_list_later_does_not_rewrite_an_issued_invoice(self, container, seed):
        currency = container.misc_list_service.create_currency(seed.admin, {"name": "JPY", "symbol": "¥"})
        invoice = make_export(container, seed, currency_code="JPY")
        container.misc_list_service.update_currency(seed.admin, currency.id, {"name": "JPY", "symbol": "YEN"})
        assert container.export_invoice_service.get(invoice.id, seed.company_id).currency_symbol == "¥"

    def test_a_currency_that_is_not_on_the_list_keeps_its_name_only(self, container, seed):
        invoice = make_export(container, seed, currency_code="XYZ")
        assert (invoice.currency_code, invoice.currency_symbol) == ("XYZ", None)
        assert invoice.currency_label == "XYZ"

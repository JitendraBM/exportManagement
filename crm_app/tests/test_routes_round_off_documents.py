"""
test_routes_round_off_documents.py
----------------------------------
The ROUND-OFF row that used to sit above CIF VALUE on FOB-priced documents
has been removed: apply_fob_uplift() keeps the per-unit uplift at full
precision and only rounds each line's Total column, so any residual cent
lands silently in that column instead of being tracked and printed as its
own row. These tests guard that the row is gone everywhere it used to print,
and that the CIF value still foots to the goods column.

The Customer Invoice's copy no longer re-derives its own FOB rate either
(it now prints item.price_usd straight through, same as every other
document - see app/routes/customer_invoices.py), so it never has a
round-off figure of its own to carry, tested alongside the rest here.
"""

import pytest


@pytest.fixture
def admin_ctx(app, logged_in_admin):
    """(client, container, admin, company_id) - the same shape the export
    invoice route tests use, over conftest's shared logged-in admin."""
    client, admin, company_id = logged_in_admin
    return client, app.container, admin, company_id


class TestRoundOffRemovedFromExportInvoiceAttachments:
    def _create_fob_priced_invoice(self, client, container, admin, company_id):
        """An export invoice that ticks the fob_pricing checkbox with charges
        chosen so the per-unit uplift does NOT divide evenly into the cent -
        the case that used to leave a round-off remainder back when the
        uplift rounded that gap into its own tracked figure."""
        product = container.product_service.create_product(
            current_user=admin, product_name="GVT 600X1200", description="", hsn_code="69072100",
            igst_percent="18", quantity="4", alternate_quantity="1.44",
            net_weight_kg=26.5, gross_weight_kg=27.0)
        data = {
            "export_invoice_number": "1000000099", "invoice_date": "2026-03-01",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "stuffing_location": "ALIVE GRANITO LLP, MORBI",
            "fob_pricing": "on",
            "sea_freight": "1234.57", "insurance": "321.09",
            "certification": "150", "other_charges": "99.99",
            "discount_amount": "50",
            "item_product_id[]": [str(product.id), str(product.id)],
            "item_product_name[]": ["GVT 600X1200", "GVT 800X800"],
            "item_hsn_code[]": ["69072100", "69072100"],
            "item_quantity_boxes[]": ["100", "70"],
            "item_quantity_value[]": ["144", "101.5"],
            "item_unit[]": ["SQM", "SQM"],
            "item_price_usd[]": ["5.92", "6.37"],
            "cd_container_no[]": ["BLJU2253726"],
            "cd_line_seal_no[]": ["UFL331090"],
            "cd_rfid_seal_no[]": ["WIND02432727"],
            "cd_vehicle_no[]": [""],
            "cd_tare_weight[]": ["2250.5"],
        }
        resp = client.post("/export-invoices/new", data=data, follow_redirects=True)
        assert resp.status_code == 200
        # Re-read through get(), not list_all(): the list query leaves `items`
        # empty and precomputes the subtotal, which would let the goods-column
        # assertions below pass without ever seeing a goods line.
        listed = container.export_invoice_service.list_all(company_id)[0]
        return container.export_invoice_service.get(listed.id, company_id)

    def test_fob_pricing_uplifts_the_price_and_never_carries_a_round_off(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        assert invoice.fob_pricing is True
        # Both lines share a catalog product, so quantity_value is recomputed
        # server-side as boxes x the product's alternate quantity (1.44) -
        # derive the total from the reloaded items rather than the posted
        # figures, which don't survive that recompute unchanged (see
        # test_boxes_times_alt_qty_overrides_client_quantity).
        total_qty = sum(item.quantity_value for item in invoice.items)
        uplift = (1234.57 + 321.09 + 150 + 99.99) / total_qty  # charges / total qty
        assert invoice.items[0].fob_price_usd == 5.92          # what was typed
        assert invoice.items[0].price_usd == pytest.approx(5.92 + uplift)  # the worked-out CIF price
        assert invoice.round_off == 0

    def test_cif_value_is_just_the_goods_column(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        assert invoice.items, "no goods lines - the assertion below would be vacuous"
        goods = sum(item.total_usd for item in invoice.items)
        assert round(goods, 2) == round(invoice.cif_value_usd, 2)

    def test_tax_invoice_prints_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in body

    def test_commercial_invoice_prints_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in body

    def test_customer_invoice_prints_no_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in body
        assert "CIF Invoice Value" in body

    def test_no_round_off_row_when_prices_are_typed_cif(self, admin_ctx):
        """The plain pricing path is untouched: nothing to carry, no row - so
        an invoice that never opted into FOB pricing prints exactly as before."""
        client, container, admin, company_id = admin_ctx
        product = container.product_service.create_product(
            current_user=admin, product_name="GVT 600X1200", description="", hsn_code="69072100",
            igst_percent="18", quantity="4", alternate_quantity="1.44",
            net_weight_kg=26.5, gross_weight_kg=27.0)
        resp = client.post("/export-invoices/new", data={
            "export_invoice_number": "1000000098", "invoice_date": "2026-03-02",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "sea_freight": "1234.57", "insurance": "321.09",
            "item_product_id[]": str(product.id), "item_product_name[]": "GVT 600X1200",
            "item_hsn_code[]": "69072100", "item_quantity_boxes[]": "100",
            "item_quantity_value[]": "144", "item_unit[]": "SQM", "item_price_usd[]": "5.92",
        }, follow_redirects=True)
        assert resp.status_code == 200
        invoice = container.export_invoice_service.list_all(company_id)[0]
        assert invoice.fob_pricing is False
        assert invoice.round_off == 0
        assert "Round-off" not in client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" not in client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)

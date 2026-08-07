"""
test_routes_round_off_documents.py
----------------------------------
The ROUND-OFF row on the documents that hang off an export invoice.

An FOB-priced invoice works its goods lines out from per-unit prices rounded to
the cent (see services.apply_fob_uplift), so the goods column lands a few cents
either side of the CIF value. Every sheet that prints both a goods column and a
CIF total therefore has to show that remainder, or it visibly doesn't foot.

The export invoice's own sheet is covered upstream; these are the attachments
built on top of it - the Tax Invoice, the Commercial Invoice and the customer's
copy - each of which reaches the total by a different route:

  * Tax Invoice        goods column + ROUND-OFF = Total Invoice Value (in INR)
  * Commercial Invoice ladder runs DOWN from CIF, so round-off sits above it
  * Customer Invoice   ladder runs UP from an independently re-rounded FOB
                       column, so it needs a round-off of its own
"""

import pytest


@pytest.fixture
def admin_ctx(app, logged_in_admin):
    """(client, container, admin, company_id) - the same shape the export
    invoice route tests use, over conftest's shared logged-in admin."""
    client, admin, company_id = logged_in_admin
    return client, app.container, admin, company_id


class TestRoundOffOnExportInvoiceAttachments:
    def _create_fob_priced_invoice(self, client, container, admin, company_id):
        """An export invoice priced FOB, with charges chosen so the per-unit
        uplift does NOT divide evenly into the cent - which is the whole point:
        a clean division would leave nothing to round off and prove nothing."""
        product = container.product_service.create_product(
            current_user=admin, product_name="GVT 600X1200", description="", hsn_code="69072100",
            igst_percent="18", quantity="4", alternate_quantity="1.44",
            net_weight_kg=26.5, gross_weight_kg=27.0)
        data = {
            "export_invoice_number": "1000000099", "invoice_date": "2026-03-01",
            "consignee_name": "ROBUST INTERNATIONAL", "tax_mode": "igst", "exchange_rate": "86.70",
            "stuffing_location": "ALIVE GRANITO LLP, MORBI",
            # Prices typed are FOB; the four charges get spread over the lines.
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

    def test_the_setup_actually_produces_a_round_off(self, admin_ctx):
        """Guard on the fixture itself: if the numbers above ever stop leaving a
        remainder, every assertion below would pass vacuously."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        assert invoice.fob_pricing is True
        assert invoice.round_off != 0

    def test_cif_value_is_the_goods_column_plus_the_round_off(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        assert invoice.items, "no goods lines - the assertion below would be vacuous"
        goods = sum(item.total_usd for item in invoice.items)
        assert round(goods + invoice.round_off, 2) == round(invoice.cif_value_usd, 2)

    def test_tax_invoice_prints_the_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        body = client.get(f"/tax-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" in body

    def test_commercial_invoice_prints_the_round_off_row(self, admin_ctx):
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        body = client.get(f"/commercial-invoices/{invoice.id}").get_data(as_text=True)
        assert "Round-off" in body

    def test_customer_invoice_closes_on_the_shared_cif_value(self, admin_ctx):
        """The customer's copy re-rounds its own FOB column, so its round-off is
        its own figure - but the total it closes on must be the SAME CIF value
        every other document quotes, not a near-miss."""
        client, container, admin, company_id = admin_ctx
        invoice = self._create_fob_priced_invoice(client, container, admin, company_id)
        assert invoice.fob_priced_total, "no FOB column - the assertion below would be vacuous"
        rebuilt = round(
            invoice.fob_priced_total + invoice.charges_total
            + invoice.discount_amount + invoice.fob_priced_round_off, 2)
        assert rebuilt == round(invoice.cif_value_usd, 2)
        body = client.get(f"/customer-invoices/{invoice.id}").get_data(as_text=True)
        assert "Total CIF Invoice Value" in body

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

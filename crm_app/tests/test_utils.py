"""
Tests for app/utils.py

Covers the number-to-words spelling helpers, the amount/INR word formatters,
and every Jinja template filter registered by register_template_helpers.
These are pure functions, so they're the cheapest regression net in the suite.
"""

import pytest
from flask import Flask

from app import utils
from app.utils import (
    number_to_words, amount_in_words, number_to_words_indian, inr_in_words,
    _three_digit_words, register_template_helpers,
    is_fob_terms, is_cfr_terms, drops_sea_freight, drops_insurance, drops_certification,
)


# --------------------------------------------------------------------------
# Delivery terms -> which charges apply
# --------------------------------------------------------------------------
class TestDeliveryTerms:
    """The options are hand-maintained free text, so the incoterm is matched on
    the leading word: 'CFR', 'cfr', 'CFR - BEIRA', 'CFR-BEIRA' are all CFR."""

    @pytest.mark.parametrize("terms", ["FOB", "fob", "FOB MUNDRA", "FOB-MUNDRA", "FOB - MUNDRA - INDIA"])
    def test_fob_is_recognised(self, terms):
        assert is_fob_terms(terms) and not is_cfr_terms(terms)

    @pytest.mark.parametrize("terms", ["CFR", "cfr", "CFR BEIRA", "CFR-BEIRA", "CFR - BEIRA"])
    def test_cfr_is_recognised(self, terms):
        assert is_cfr_terms(terms) and not is_fob_terms(terms)

    @pytest.mark.parametrize("terms", ["CIF", "CIF BEIRA", "", None, "FOBBY", "CFRX", "EX WORKS"])
    def test_other_terms_are_neither(self, terms):
        assert not is_fob_terms(terms) and not is_cfr_terms(terms)

    def test_fob_drops_the_freight_and_the_insurance(self):
        # The certification is NOT one of them - it's a seller-side cost that
        # stays payable under every term, like other_charges.
        assert drops_sea_freight("FOB MUNDRA") and drops_insurance("FOB MUNDRA")
        assert not drops_certification("FOB MUNDRA")

    def test_cfr_drops_the_insurance_only(self):
        # The whole point of CFR: the seller still pays the freight, and the
        # certification stays with it.
        assert drops_insurance("CFR BEIRA")
        assert not drops_sea_freight("CFR BEIRA")
        assert not drops_certification("CFR BEIRA")

    def test_cif_drops_nothing(self):
        assert not drops_sea_freight("CIF BEIRA") and not drops_insurance("CIF BEIRA")
        assert not drops_certification("CIF BEIRA")


# --------------------------------------------------------------------------
# _three_digit_words
# --------------------------------------------------------------------------
class TestThreeDigitWords:
    @pytest.mark.parametrize("n,expected", [
        (0, ""),
        (5, "FIVE"),
        (13, "THIRTEEN"),
        (20, "TWENTY"),
        (21, "TWENTY-ONE"),
        (99, "NINETY-NINE"),
        (100, "ONE HUNDRED"),
        (115, "ONE HUNDRED FIFTEEN"),
        (640, "SIX HUNDRED FORTY"),
        (999, "NINE HUNDRED NINETY-NINE"),
    ])
    def test_examples(self, n, expected):
        assert _three_digit_words(n) == expected


# --------------------------------------------------------------------------
# number_to_words (Western grouping)
# --------------------------------------------------------------------------
class TestNumberToWords:
    def test_zero(self):
        assert number_to_words(0) == "ZERO"

    @pytest.mark.parametrize("n,expected", [
        (7, "SEVEN"),
        (1000, "ONE THOUSAND"),
        (15640, "FIFTEEN THOUSAND SIX HUNDRED FORTY"),
        (1_000_000, "ONE MILLION"),
        (1_000_000_000, "ONE BILLION"),
        (1234, "ONE THOUSAND TWO HUNDRED THIRTY-FOUR"),
    ])
    def test_examples(self, n, expected):
        assert number_to_words(n) == expected

    def test_docstring_example(self):
        # The example promised in the function's own docstring.
        assert number_to_words(15640) == "FIFTEEN THOUSAND SIX HUNDRED FORTY"


# --------------------------------------------------------------------------
# amount_in_words
# --------------------------------------------------------------------------
class TestAmountInWords:
    def test_docstring_example(self):
        assert amount_in_words(15640.50) == (
            "US DOLLARS FIFTEEN THOUSAND SIX HUNDRED FORTY AND CENTS FIFTY ONLY"
        )

    def test_whole_amount_has_no_cents_clause(self):
        assert amount_in_words(100) == "US DOLLARS ONE HUNDRED ONLY"

    def test_zero(self):
        assert amount_in_words(0) == "US DOLLARS ZERO ONLY"

    def test_none_is_treated_as_zero(self):
        assert amount_in_words(None) == "US DOLLARS ZERO ONLY"

    def test_custom_currency_label(self):
        assert amount_in_words(5, "EUROS") == "EUROS FIVE ONLY"

    def test_rounds_to_two_decimals(self):
        # 1.005 rounds to 1.00 (banker's/standard rounding), 1.006 -> 1.01
        assert amount_in_words(1.006) == "US DOLLARS ONE AND CENTS ONE ONLY"

    def test_string_input_is_coerced(self):
        assert amount_in_words("3.25") == "US DOLLARS THREE AND CENTS TWENTY-FIVE ONLY"


# --------------------------------------------------------------------------
# number_to_words_indian (crore/lakh grouping)
# --------------------------------------------------------------------------
class TestNumberToWordsIndian:
    def test_zero(self):
        assert number_to_words_indian(0) == "ZERO"

    @pytest.mark.parametrize("n,expected", [
        (1000, "ONE THOUSAND"),
        (100000, "ONE LAKH"),
        (10000000, "ONE CRORE"),
        (383833, "THREE LAKH EIGHTY-THREE THOUSAND EIGHT HUNDRED THIRTY-THREE"),
    ])
    def test_examples(self, n, expected):
        assert number_to_words_indian(n) == expected


# --------------------------------------------------------------------------
# inr_in_words
# --------------------------------------------------------------------------
class TestInrInWords:
    def test_docstring_example(self):
        assert inr_in_words(383833) == (
            "THREE LAKH EIGHTY-THREE THOUSAND EIGHT HUNDRED THIRTY-THREE INR ONLY"
        )

    def test_whole_has_no_paise_clause(self):
        assert inr_in_words(500) == "FIVE HUNDRED INR ONLY"

    def test_with_paise(self):
        assert inr_in_words(10.50) == "TEN AND PAISE FIFTY INR ONLY"

    def test_none_is_zero(self):
        assert inr_in_words(None) == "ZERO INR ONLY"


# --------------------------------------------------------------------------
# Template filters (registered onto a throwaway Flask app)
# --------------------------------------------------------------------------
@pytest.fixture
def filters():
    app = Flask(__name__)
    register_template_helpers(app)
    return app.jinja_env.filters


class TestTemplateFilters:
    def test_amount_in_words_filter(self, filters):
        assert filters["amount_in_words"](100) == "US DOLLARS ONE HUNDRED ONLY"

    def test_inr_in_words_filter(self, filters):
        assert filters["inr_in_words"](500) == "FIVE HUNDRED INR ONLY"

    def test_long_date_formats(self, filters):
        assert filters["long_date"]("2025-01-23") == "23 January 2025"

    def test_long_date_handles_datetime_string(self, filters):
        assert filters["long_date"]("2025-01-23 14:30:00") == "23 January 2025"

    def test_long_date_empty_returns_dash(self, filters):
        assert filters["long_date"]("") == "—"
        assert filters["long_date"](None) == "—"

    def test_long_date_unparseable_returns_input(self, filters):
        assert filters["long_date"]("not-a-date") == "not-a-date"

    def test_friendly_date_truncates(self, filters):
        assert filters["friendly_date"]("2025-01-23 14:30:59") == "2025-01-23 14:30"

    def test_friendly_date_empty(self, filters):
        assert filters["friendly_date"](None) == "—"

    def test_status_css_known(self, filters):
        assert filters["status_css"]("new") == "slate"
        assert filters["status_css"]("in_follow_up") == "amber"
        assert filters["status_css"]("export_invoice_submission_pending") == "teal"

    def test_status_css_unknown_defaults_slate(self, filters):
        assert filters["status_css"]("something_else") == "slate"

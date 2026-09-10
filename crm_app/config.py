"""
config.py
---------
Centralised, single-responsibility configuration object.

Why this exists (SOLID):
  - Single Responsibility: this module's only job is to know *where settings
    come from*. Nothing else in the app reads environment variables directly.
  - Open/Closed: to add a new setting, add an attribute here. Nothing that
    consumes `Config` needs to change.

All secrets/paths are overridable via a `.env` file (see .env.example) so the
same code can run in development, testing, or production without edits.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

# Load variables from a .env file into the process environment, if present.
load_dotenv()

# Base directory of the project (folder that contains this file).
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Holds every tunable setting the app needs. Import this, don't
    scatter os.environ.get() calls throughout the codebase."""

    # Flask needs this to sign session cookies. CHANGE THIS IN PRODUCTION.
    # The literal below is also the sentinel `create_app` refuses to boot on
    # when DEBUG is off (see app/__init__.py) - a shipped default key means
    # anyone can forge a session cookie.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # --- session cookie hardening --------------------------------------------------
    # Secure defaults to ON; a developer running the app over plain HTTP on
    # localhost sets SESSION_COOKIE_SECURE=false in their .env so the browser
    # still returns the cookie. Production (behind TLS) leaves it unset.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Sessions become non-permanent-by-default cookies; login() opts each one
    # in (session.permanent = True) so this lifetime actually applies.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    # url_for(_external=True) built outside a request (there are none today,
    # but keep it correct) should assume HTTPS.
    PREFERRED_URL_SCHEME = "https"

    # --- login brute-force limits (see AuthService.authenticate + routes/auth) ----
    # After this many consecutive failures for one user, the account is locked
    # for LOGIN_LOCKOUT_MINUTES. A separate per-IP cap (LOGIN_IP_* below)
    # blunts spraying across many usernames from one host.
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))
    LOGIN_IP_MAX_ATTEMPTS = int(os.environ.get("LOGIN_IP_MAX_ATTEMPTS", "20"))
    LOGIN_IP_WINDOW_SECONDS = int(os.environ.get("LOGIN_IP_WINDOW_SECONDS", "300"))

    # Minimum length for any password the app sets (new user or self-service
    # change). Kept in one place so both call sites agree.
    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", "10"))

    # Content-Security-Policy. Shipped in report-only mode first (logs
    # violations in the browser console, breaks nothing) because templates
    # still carry inline <script>/<style>. Once those are cleaned up this
    # moves to an enforcing `Content-Security-Policy` header - a one-line
    # change in app/__init__.py's after_request.
    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    # Path to the SQLite database file.
    DATABASE_PATH = os.environ.get(
        "DATABASE_PATH", os.path.join(BASE_DIR, "instance", "crm.db")
    )

    # Path to the .sql file used to (re)create the schema on first run.
    SCHEMA_PATH = os.path.join(BASE_DIR, "app", "schema.sql")

    # Base currency all monetary values are converted into for reporting.
    # The brief says "amount in currency other than INR and its conversion",
    # so INR is our fixed base currency.
    BASE_CURRENCY = "INR"

    # Free, no-API-key exchange rate service used by CurrencyConversionService.
    # Swappable: change this one line to point at a different provider.
    EXCHANGE_RATE_API_URL = "https://api.frankfurter.app/latest"

    # If the exchange-rate API can't be reached (offline demo, no internet),
    # we fall back to these approximate static rates (units of foreign
    # currency per 1 INR is NOT how these are stored -- these are
    # "1 unit of FOREIGN currency = X INR", updated occasionally by an admin
    # editing this file). This keeps the app usable even without internet.
    FALLBACK_RATES_TO_INR = {
        "USD": 86.0,
        "EUR": 93.0,
        "GBP": 109.0,
        "AED": 23.4,
        "CNY": 12.0,
        "SAR": 22.9,
    }

    # How many days ahead counts as an "upcoming" follow-up on the employee
    # dashboard (used by StatsService).
    FOLLOWUP_LOOKAHEAD_DAYS = 3

    # Pagination default for list pages.
    PAGE_SIZE = 20

    # Where product photos / dimension photos get saved. Lives under
    # static/ so Flask can serve the files directly via url_for('static', ...).
    PRODUCT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads", "products")
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    # Where a supplier's own Purchase Invoice PDF gets saved - each supplier
    # sends their own document, so it's stored as an upload rather than
    # generated by the app (unlike every other document type here).
    PURCHASE_INVOICE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads", "purchase_invoices")
    ALLOWED_DOCUMENT_EXTENSIONS = {"pdf"}

    # Where an Export Invoice's optional Shipping Bill PDF gets saved - unlike
    # the invoice itself (which the app generates), the shipping bill is an
    # external document attached to the record. Same upload pattern as above.
    EXPORT_INVOICE_UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads", "export_invoices")

    # Where an uploaded Permit ("Permission", managed under Our Company) PDF
    # gets saved - each permit optionally carries the company's own permit
    # document. Lives under static/ so Flask serves it via url_for('static', ...).
    PERMIT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads", "permits")

    # Global cap on any single upload. Product photos are validated separately
    # by extension/type, so the only thing needing a large cap is a Database
    # Backup restore, whose ZIP holds the whole DB + every product image -
    # 10 MB is far too small for that. Override with MAX_UPLOAD_MB if needed.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024

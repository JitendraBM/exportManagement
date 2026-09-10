"""
tests/test_security_hardening.py
--------------------------------
Attack-regression tests for the HTTP/session hardening: security headers,
session-cookie flags, CSRF enforcement (forms + AJAX), login lockout and
per-IP throttle, the POST-only logout, the backup zip-slip guard, and the
default-SECRET_KEY boot refusal.

Most of the app's tests run with WTF_CSRF_ENABLED=False (conftest's
TestConfig) so they can POST freely. The CSRF tests here build their own
app with protection turned back ON.
"""

import io
import zipfile

import pytest

from app import create_app
from app.routes import auth as auth_routes
from app.exceptions import ValidationError
from config import Config


# ==========================================================================
# Security response headers
# ==========================================================================
class TestSecurityHeaders:
    def test_headers_present_on_login_page(self, client):
        r = client.get("/login")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        # CSP ships report-only for now; the enforcing header must NOT be set.
        assert "Content-Security-Policy-Report-Only" in r.headers
        assert "Content-Security-Policy" not in r.headers

    def test_headers_present_on_authenticated_page(self, logged_in_admin):
        client, _admin, _cid = logged_in_admin
        r = client.get("/")
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert "Content-Security-Policy-Report-Only" in r.headers


# ==========================================================================
# Session cookie flags
# ==========================================================================
class TestSessionCookie:
    def test_cookie_is_httponly_and_samesite(self, app, client, seed):
        r = client.post(
            "/login",
            data={"company_id": seed.company_id, "username": "admin", "password": "admin-pass-123"},
        )
        set_cookie = r.headers.get("Set-Cookie", "")
        assert "session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Lax" in set_cookie
        # TestConfig forces SESSION_COOKIE_SECURE off (the test client is
        # plain HTTP); production keeps the default, which is on.
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True


# ==========================================================================
# CSRF - built with protection ENABLED
# ==========================================================================
@pytest.fixture
def csrf_app(tmp_config):
    class CsrfConfig(tmp_config):
        TESTING = True
        WTF_CSRF_ENABLED = True
        # The app sets PREFERRED_URL_SCHEME=https, which makes the Werkzeug
        # test client speak https, which turns on Flask-WTF's strict-referrer
        # check. That check is a real production defence but needs a live
        # Referer header the test client doesn't send - out of scope here.
        WTF_CSRF_SSL_STRICT = False

    application = create_app(CsrfConfig)
    yield application


@pytest.fixture
def csrf_client(csrf_app):
    return csrf_app.test_client()


@pytest.fixture(autouse=True)
def _reset_ip_throttle():
    """Every test starts with an empty per-IP login-attempt record."""
    auth_routes._ip_hits.clear()
    yield
    auth_routes._ip_hits.clear()


def _seed_admin(app):
    c = app.container
    tenant = c.tenant_repo.create("CSRF Co", "csrf-co")
    admin = c.auth_service.create_user(
        company_id=tenant.id, username="csrfadmin", password="csrf-pass-123",
        full_name="CSRF Admin", role="admin",
    )
    return tenant.id, admin


class TestCsrfEnforced:
    def test_post_without_token_is_rejected(self, csrf_app, csrf_client):
        cid, admin = _seed_admin(csrf_app)
        with csrf_client.session_transaction() as s:
            s["user_id"] = admin.id
            s["company_id"] = cid
        r = csrf_client.post("/misc/currencies", data={"name": "Rupee", "symbol": "R"})
        assert r.status_code == 400

    def test_post_with_token_succeeds(self, csrf_app, csrf_client):
        cid, admin = _seed_admin(csrf_app)
        with csrf_client.session_transaction() as s:
            s["user_id"] = admin.id
            s["company_id"] = cid
        page = csrf_client.get("/misc/").get_data(as_text=True)
        token = _scrape_csrf(page)
        assert token, "no csrf token found on /misc/"
        r = csrf_client.post(
            "/misc/currencies",
            data={"name": "Rupee", "symbol": "R", "csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code in (200, 302)

    def test_ajax_without_header_is_rejected(self, csrf_app, csrf_client):
        cid, admin = _seed_admin(csrf_app)
        with csrf_client.session_transaction() as s:
            s["user_id"] = admin.id
            s["company_id"] = cid
        r = csrf_client.post("/misc/api/currencies", data={"name": "Yen", "symbol": "Y"})
        assert r.status_code == 400

    def test_ajax_with_header_is_accepted(self, csrf_app, csrf_client):
        cid, admin = _seed_admin(csrf_app)
        with csrf_client.session_transaction() as s:
            s["user_id"] = admin.id
            s["company_id"] = cid
        page = csrf_client.get("/misc/").get_data(as_text=True)
        token = _scrape_csrf(page)
        r = csrf_client.post(
            "/misc/api/currencies",
            data={"name": "Yen", "symbol": "Y"},
            headers={"X-CSRFToken": token},
        )
        assert r.status_code != 400


def _scrape_csrf(html: str):
    """Pull the token out of the <meta> tag or a hidden input."""
    import re
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


# ==========================================================================
# Login brute-force: per-account lockout + per-IP throttle
# ==========================================================================
class TestLoginLockout:
    def test_account_locks_after_max_attempts(self, app, client, seed):
        data = {"company_id": seed.company_id, "username": "admin", "password": "wrong"}
        last = None
        for _ in range(Config.LOGIN_MAX_ATTEMPTS):
            last = client.post("/login", data=data)
        # The attempt that hit the cap is answered with a lockout (429).
        assert last.status_code == 429

        # Even the RIGHT password is refused while locked.
        good = client.post(
            "/login",
            data={"company_id": seed.company_id, "username": "admin", "password": "admin-pass-123"},
        )
        assert good.status_code == 429
        with client.session_transaction() as s:
            assert s.get("user_id") is None

    def test_unlock_after_window_and_counter_resets(self, app, client, seed):
        # Drive it into a lock.
        for _ in range(Config.LOGIN_MAX_ATTEMPTS):
            client.post(
                "/login",
                data={"company_id": seed.company_id, "username": "admin", "password": "wrong"},
            )
        # Move the lock into the past directly via the repo.
        u = app.container.user_repo.get_by_username(seed.company_id, "admin")
        app.container.user_repo.lock_account(u.id, "2000-01-01 00:00:00")
        # Now the correct password works again...
        good = client.post(
            "/login",
            data={"company_id": seed.company_id, "username": "admin", "password": "admin-pass-123"},
        )
        assert good.status_code == 302
        # ...and the failure counter was cleared on success.
        u2 = app.container.user_repo.get_by_username(seed.company_id, "admin")
        assert u2.failed_attempts == 0
        assert u2.locked_until is None

    def test_per_ip_throttle_trips_regardless_of_username(self, app, client, seed):
        # Vary the username each time so no single account locks; the IP cap
        # is what must stop it.
        last = None
        for i in range(Config.LOGIN_IP_MAX_ATTEMPTS + 2):
            last = client.post(
                "/login",
                data={"company_id": seed.company_id, "username": f"nobody{i}", "password": "x"},
            )
        assert last.status_code == 429


# ==========================================================================
# Logout is POST-only
# ==========================================================================
class TestLogoutMethod:
    def test_get_logout_not_allowed(self, client):
        assert client.get("/logout").status_code == 405

    def test_post_logout_clears_session(self, app, client, seed):
        client.post(
            "/login",
            data={"company_id": seed.company_id, "username": "admin", "password": "admin-pass-123"},
        )
        client.post("/logout")
        with client.session_transaction() as s:
            assert s.get("user_id") is None


# ==========================================================================
# Backup restore: zip-slip guard
# ==========================================================================
class TestBackupZipSlip:
    def test_traversal_entry_is_refused(self, container):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
            zf.writestr("backup_manifest.json", "{}")
        buf.seek(0)

        class _FS:
            filename = "backup.zip"
            def save(self, path):
                with open(path, "wb") as fh:
                    fh.write(buf.getvalue())

        with pytest.raises(ValidationError):
            container.backup_service.restore_from_zip(_FS())


# ==========================================================================
# Refuse to boot on the shipped default SECRET_KEY
# ==========================================================================
class TestSecretKeyGuard:
    def test_default_secret_without_debug_raises(self, tmp_config):
        class BadConfig(tmp_config):
            TESTING = False
            SECRET_KEY = "dev-secret-key-change-me"

        with pytest.raises(RuntimeError):
            create_app(BadConfig)

    def test_default_secret_with_testing_is_allowed(self, tmp_config):
        class OkConfig(tmp_config):
            SECRET_KEY = "dev-secret-key-change-me"  # TESTING stays True
        # Should not raise.
        create_app(OkConfig)

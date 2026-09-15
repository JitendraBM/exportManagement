"""
app/routes/auth.py
-------------------
Login / logout. This is the only place that touches Flask's `session`
object for authentication - everywhere else just reads `g.user`.
"""

import time
import threading

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, g

from config import Config
from app.exceptions import AccountLockedError

auth_bp = Blueprint("auth", __name__)


# --- per-IP login throttle --------------------------------------------------
# A single process's in-memory record of recent POST /login attempts per
# client IP. Blunts spraying one host across many usernames (the per-account
# lockout in AuthService only slows an attacker down one username at a time).
# Per-worker under gunicorn - fine at the 2-3 workers this app runs; a shared
# store (Redis / a DB table) is the move only if it scales past that.
_ip_hits: dict[str, list[float]] = {}
_ip_hits_lock = threading.Lock()


def _ip_throttled(ip: str) -> bool:
    """Record this attempt and report whether the IP is now over the cap."""
    now = time.monotonic()
    window = Config.LOGIN_IP_WINDOW_SECONDS
    with _ip_hits_lock:
        hits = [t for t in _ip_hits.get(ip, ()) if now - t < window]
        hits.append(now)
        _ip_hits[ip] = hits
        # Opportunistic cleanup so the dict can't grow without bound.
        if len(_ip_hits) > 2048:
            for k in [k for k, v in _ip_hits.items() if not v or now - v[-1] > window]:
                _ip_hits.pop(k, None)
        return len(hits) > Config.LOGIN_IP_MAX_ATTEMPTS


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("dashboard.home"))

    companies = current_app.container.tenant_repo.list_active()

    if request.method == "POST":
        if _ip_throttled(request.remote_addr or "unknown"):
            flash("Too many login attempts from this address. Wait a few minutes and try again.", "error")
            return render_template("login.html", companies=companies), 429

        company_id = request.form.get("company_id", type=int)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            user = (
                current_app.container.auth_service.authenticate(company_id, username, password)
                if company_id else None
            )
        except AccountLockedError as e:
            flash(str(e), "error")
            return render_template("login.html", companies=companies), 429

        if user:
            session.clear()
            session.permanent = True  # apply PERMANENT_SESSION_LIFETIME (12h)
            session["user_id"] = user.id
            session["company_id"] = user.company_id
            flash(f"Welcome back, {user.full_name}.", "success")
            return redirect(url_for("dashboard.home"))
        flash("Incorrect company, username or password.", "error")

    return render_template("login.html", companies=companies)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # POST-only + CSRF-protected so a third-party page can't force a logout.
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))

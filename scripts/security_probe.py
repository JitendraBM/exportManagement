#!/usr/bin/env python3
"""
scripts/security_probe.py
-------------------------
Non-destructive security smoke check for a deployed Ledger CRM.

It never submits real credentials, never mutates data, rate-limits itself,
and has a hard cap on total requests. Run it right after every deploy and
from the nightly job against staging.

    python scripts/security_probe.py --url https://staging.example.com
    LIVE_WEBSITE=https://aayutiles.in/login python scripts/security_probe.py

Exit code is 0 only if every check passes (warnings do not fail the run),
so it can gate a deploy.

The login-throttle check is OFF by default and must never be pointed at
production - it deliberately sends a burst of bad logins for a throwaway
username:

    python scripts/security_probe.py --url https://staging... --test-throttle
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs `requests` (it's already in requirements.txt).")


PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class Probe:
    def __init__(self, base: str, max_requests: int = 60, delay: float = 0.3):
        # Normalise: strip a trailing /login etc. so we can hit the origin root.
        p = urllib.parse.urlparse(base)
        self.origin = f"{p.scheme}://{p.netloc}"
        self.login_url = base if p.path and p.path != "/" else f"{self.origin}/login"
        self.session = requests.Session()
        self.session.max_redirects = 5
        self._budget = max_requests
        self._delay = delay
        self.results: list[tuple[str, str, str]] = []

    # -- request helper with a hard budget --------------------------------
    def _get(self, url, allow_redirects=True, **kw):
        if self._budget <= 0:
            raise RuntimeError("request budget exhausted - aborting probe")
        self._budget -= 1
        time.sleep(self._delay)
        return self.session.get(url, allow_redirects=allow_redirects, timeout=15, **kw)

    def _post(self, url, **kw):
        if self._budget <= 0:
            raise RuntimeError("request budget exhausted - aborting probe")
        self._budget -= 1
        time.sleep(self._delay)
        return self.session.post(url, allow_redirects=False, timeout=15, **kw)

    def record(self, name, status, detail=""):
        self.results.append((name, status, detail))
        print(f"  [{status:4}] {name}" + (f" - {detail}" if detail else ""))

    # -- individual checks ----------------------------------------------------
    def check_https_redirect(self):
        if not self.origin.startswith("https://"):
            self.record("HTTPS", FAIL, "target URL is not https")
            return
        http_url = "http://" + self.origin[len("https://"):]
        try:
            r = self._get(http_url, allow_redirects=False)
        except requests.RequestException as e:
            self.record("HTTP->HTTPS redirect", WARN, f"no plain-HTTP listener ({e.__class__.__name__})")
            return
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 308) and loc.startswith("https://"):
            self.record("HTTP->HTTPS redirect", PASS, f"{r.status_code} -> {loc}")
        else:
            self.record("HTTP->HTTPS redirect", FAIL, f"{r.status_code} Location={loc!r}")

    def check_headers(self):
        r = self._get(self.login_url)
        h = {k.lower(): v for k, v in r.headers.items()}

        def want(name, predicate, detail_ok="", hard=True):
            val = h.get(name.lower())
            if val and predicate(val):
                self.record(f"header {name}", PASS, detail_ok or val)
            else:
                self.record(f"header {name}", FAIL if hard else WARN, f"got {val!r}")

        want("Strict-Transport-Security",
             lambda v: _hsts_max_age(v) >= 15552000,
             hard=True)
        want("X-Content-Type-Options", lambda v: v.strip().lower() == "nosniff")
        want("X-Frame-Options", lambda v: v.strip().upper() in ("DENY", "SAMEORIGIN"))
        want("Referrer-Policy", lambda v: bool(v.strip()))
        # CSP: report-only is acceptable for now, but SOME CSP must be present.
        if "content-security-policy" in h or "content-security-policy-report-only" in h:
            ro = "content-security-policy-report-only" in h and "content-security-policy" not in h
            self.record("header Content-Security-Policy", PASS,
                        "report-only" if ro else "enforcing")
        else:
            self.record("header Content-Security-Policy", FAIL, "absent")

        server = h.get("server", "")
        if re.search(r"\d", server):
            self.record("server banner", WARN, f"version disclosed: {server!r}")
        else:
            self.record("server banner", PASS, server or "(none)")

    def check_cookie_flags(self):
        r = self._get(self.origin + "/", allow_redirects=False)
        raw = r.headers.get("Set-Cookie", "")
        if "session=" not in raw:
            # Try the login page instead.
            r = self._get(self.login_url, allow_redirects=False)
            raw = r.headers.get("Set-Cookie", "")
        if "session=" not in raw:
            self.record("session cookie flags", WARN, "no session cookie issued to an anonymous visitor")
            return
        low = raw.lower()
        missing = [f for f in ("secure", "httponly", "samesite") if f not in low]
        if missing:
            self.record("session cookie flags", FAIL, f"missing: {', '.join(missing)}")
        else:
            self.record("session cookie flags", PASS, "Secure, HttpOnly, SameSite all set")

    def check_csrf_wired(self):
        r = self._get(self.login_url)
        body = r.text
        if 'name="csrf-token"' in body or 'name="csrf_token"' in body:
            self.record("CSRF wired", PASS, "token present on login page")
        else:
            self.record("CSRF wired", FAIL, "no csrf token/meta on the login page")

    def check_no_debug_surface(self):
        # A deliberately bad type on a form field must yield a normal error
        # page, never a Werkzeug traceback / interactive console.
        try:
            r = self._get(self.origin + "/definitely-not-a-real-route-xyz", allow_redirects=False)
        except requests.RequestException as e:
            self.record("no debug surface", WARN, str(e))
            return
        body = (r.text or "")[:20000]
        markers = ("Werkzeug Debugger", "Traceback (most recent call last)",
                   "werkzeug.debug", "console-cmd", "?__debugger__")
        hit = [m for m in markers if m in body]
        if hit:
            self.record("no debug surface", FAIL, f"debug markers in response: {hit}")
        elif r.status_code not in (404, 400, 302, 301):
            self.record("no debug surface", WARN, f"unexpected status {r.status_code} for a bogus path")
        else:
            self.record("no debug surface", PASS, f"{r.status_code} on bogus path")

    def check_exposed_paths(self):
        bad = 0
        for path in ("/.env", "/.git/config", "/console", "/backup/", "/config.py"):
            try:
                r = self._get(self.origin + path, allow_redirects=False)
            except requests.RequestException:
                continue
            ok = r.status_code in (301, 302, 401, 403, 404)
            if not ok:
                bad += 1
                self.record(f"exposed {path}", FAIL, f"status {r.status_code}")
        if not bad:
            self.record("exposed paths", PASS, ".env/.git/console/backup all blocked or 404")

    def _login_csrf_token(self):
        """Scrape a fresh CSRF token from the login page (the form is
        CSRF-protected, so a token-less POST just 400s before auth runs)."""
        body = self._get(self.login_url).text
        m = (re.search(r'name="csrf-token"\s+content="([^"]+)"', body)
             or re.search(r'name="csrf_token"\s+value="([^"]+)"', body))
        return m.group(1) if m else None

    def check_login_throttle(self, count: int):
        count = max(3, min(count, 25))
        user = "zzprobe-" + str(int(time.time()))
        token = self._login_csrf_token()
        statuses = []
        for _ in range(count):
            data = {"company_id": "999999", "username": user, "password": "definitely-wrong"}
            if token:
                data["csrf_token"] = token
            try:
                r = self._post(self.login_url, data=data,
                               headers={"Referer": self.login_url})
            except requests.RequestException as e:
                self.record("login throttle", WARN, str(e))
                return
            statuses.append(r.status_code)
            if r.status_code == 429:
                self.record("login throttle", PASS, f"429 after {len(statuses)} attempts")
                return
        hint = " (all 400 - CSRF token not accepted?)" if set(statuses) == {400} else ""
        self.record("login throttle", FAIL,
                    f"no 429 in {count} attempts (saw {sorted(set(statuses))}){hint}")

    # -- driver -------------------------------------------------------------
    def run(self, test_throttle: bool, throttle_count: int) -> int:
        print(f"Probing {self.origin}  (login: {self.login_url})\n")
        for fn in (self.check_https_redirect, self.check_headers, self.check_cookie_flags,
                   self.check_csrf_wired, self.check_no_debug_surface, self.check_exposed_paths):
            try:
                fn()
            except Exception as e:  # keep going; one failed check shouldn't stop the rest
                self.record(fn.__name__, FAIL, f"probe error: {e}")
        if test_throttle:
            self.check_login_throttle(throttle_count)
        else:
            self.record("login throttle", WARN, "skipped (pass --test-throttle against STAGING only)")

        fails = [r for r in self.results if r[1] == FAIL]
        warns = [r for r in self.results if r[1] == WARN]
        print(f"\n{len(self.results)} checks: "
              f"{len(self.results) - len(fails) - len(warns)} pass, {len(warns)} warn, {len(fails)} fail")
        return 1 if fails else 0


def _hsts_max_age(value: str) -> int:
    m = re.search(r"max-age\s*=\s*(\d+)", value, re.I)
    return int(m.group(1)) if m else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("LIVE_WEBSITE"),
                    help="Target base URL (or set LIVE_WEBSITE).")
    ap.add_argument("--max-requests", type=int, default=60, help="Hard cap on total requests.")
    ap.add_argument("--test-throttle", action="store_true",
                    help="STAGING ONLY: send a burst of bad logins to check the throttle fires.")
    ap.add_argument("--throttle-probe-count", type=int, default=12,
                    help="Bad-login attempts for --test-throttle (capped at 25).")
    args = ap.parse_args(argv)

    if not args.url:
        ap.error("no target: pass --url or set LIVE_WEBSITE")

    probe = Probe(args.url, max_requests=args.max_requests)
    return probe.run(args.test_throttle, args.throttle_probe_count)


if __name__ == "__main__":
    sys.exit(main())

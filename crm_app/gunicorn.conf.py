"""
gunicorn.conf.py
----------------
Run with:  gunicorn -c gunicorn.conf.py wsgi:app

Kept small on purpose. Notable choices:

  * bind is loopback only - nginx (see deploy/nginx.conf.example) is the
    public listener and reverse-proxies to this.
  * workers is deliberately low. The per-IP login throttle in
    app/routes/auth.py keeps its counters in each worker's memory, so a
    request landing on a different worker doesn't see the others' counts.
    At 2-3 workers that's an acceptable weakening; past that, move the
    throttle to a shared store.
  * forwarded_allow_ips trusts X-Forwarded-* only from localhost, i.e. only
    from nginx on the same host. ProxyFix in the app then reads them.
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", str(min(3, multiprocessing.cpu_count() * 2 + 1))))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))  # DB backup zips can be large
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")

accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")   # stdout -> journald/systemd
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

"""
wsgi.py
-------
Production entrypoint. A WSGI server (gunicorn) imports `app` from here:

    gunicorn -c gunicorn.conf.py wsgi:app

This module deliberately does NOT call app.run() - `run.py` is the local
development server and must never be used to serve production traffic
(it is single-threaded and, with DEBUG on, exposes an interactive
code-execution console).
"""

from app import create_app

app = create_app()

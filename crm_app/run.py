"""
run.py
------
Entry point for local development.

    python run.py

Reads HOST/PORT/DEBUG from environment variables (see .env.example) so
production deployment (behind gunicorn/waitress, etc.) doesn't need this
file at all - it can import `create_app` from `app` directly instead.
"""

import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    # Default OFF: this file is the dev server, but an accidental prod run of
    # it should not also turn on the interactive debugger.
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    if debug and host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"!!! run.py started with DEBUG=true and HOST={host} - the interactive\n"
            f"!!! debugger executes arbitrary code for anyone who can reach this port.\n"
            f"!!! Use gunicorn + wsgi:app for anything internet-facing (see README).",
            flush=True,
        )
    app.run(host=host, port=port, debug=debug)

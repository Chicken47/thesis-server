"""
Flask application factory.
"""

import os
import time
from flask import Flask, render_template, request, g
from flask_cors import CORS
from dotenv import load_dotenv
from api.logger import get_logger

load_dotenv()

log = get_logger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "*").split(","))

    # ── Request logging ────────────────────────────────────────────────────────
    @app.before_request
    def _before():
        g.start = time.perf_counter()

    @app.after_request
    def _after(response):
        if request.path in ("/health", "/docs") or request.path.startswith("/static"):
            return response
        elapsed = (time.perf_counter() - g.start) * 1000
        log.info(
            f"{request.method} {request.path} → {response.status_code}",
            extra={"ms": f"{elapsed:.0f}"},
        )
        return response

    # ── Blueprints ─────────────────────────────────────────────────────────────
    from api.routes.jobs_routes import jobs_bp
    from api.routes.admin import admin_bp

    app.register_blueprint(jobs_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")

    # Mark any jobs left as "running" from a previous process as failed.
    # Background threads die when Gunicorn restarts; without this they stay
    # stuck in "running" forever and the frontend polls indefinitely.
    try:
        from api.db import _conn
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs SET status = 'failed',
                        error_message = 'Server restarted — job was lost',
                        finished_at = NOW()
                    WHERE status IN ('running', 'queued')
                    """
                )
                count = cur.rowcount
        if count:
            log.warning(f"Marked {count} orphaned job(s) as failed on startup")
    except Exception as e:
        log.warning(f"Could not clean up orphaned jobs on startup: {e}")

    log.info("Flask app created", extra={"blueprints": "jobs,admin"})

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "thesis-stock-api"}

    @app.get("/docs")
    def docs():
        return render_template("swagger.html")

    return app

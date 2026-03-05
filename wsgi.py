"""
WSGI entry point for production deployment.

Local dev:
  python wsgi.py

Production (gunicorn):
  gunicorn wsgi:app --workers 2 --timeout 300 --bind 0.0.0.0:$PORT

Note: --timeout 300 is needed because the /analyze endpoint spawns a background
thread and returns 202 immediately, but gunicorn's default 30s timeout can kill
in-flight scraping on slow networks. The actual Claude API call runs in a
daemon thread and is not blocked by gunicorn's worker timeout.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from api.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )

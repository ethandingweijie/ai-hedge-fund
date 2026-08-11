"""
app/backend/health_responder.py
===============================
Minimal HTTP health responder for non-HTTP services (worker, scheduler).

One railway.toml applies to every service in this repo, and code config
overrides the dashboard, so EVERY service inherits healthcheckPath "/" on
$PORT — even processes that serve no HTTP. This module provides the tiny
200-OK responder those processes run on a daemon thread so the inherited
healthcheck passes.

Usage:
    from app.backend.health_responder import start_health_responder
    start_health_responder()   # binds $PORT, returns the server (or None)
"""
from __future__ import annotations

import http.server
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request with 200 — exists only for Railway's
    inherited healthcheckPath = "/" probe."""

    def _respond(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    do_GET = _respond
    do_HEAD = _respond

    def log_message(self, fmt, *args):  # silence per-request access logs
        pass


def start_health_responder() -> Optional[http.server.ThreadingHTTPServer]:
    """Bind the responder on $PORT in a daemon thread. Returns the server
    object, or None if the port is taken (logged; caller may continue)."""
    port = int(os.environ.get("PORT", "8080"))
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
        threading.Thread(
            target=server.serve_forever, name="health-responder", daemon=True
        ).start()
        logger.info("health responder listening on :%d", port)
        return server
    except OSError as exc:
        logger.warning("health responder failed to bind :%d (%s) — continuing", port, exc)
        return None

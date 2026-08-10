"""
app/backend/worker_main.py
==========================
Worker-service entry point for Railway.

Background: this project's single railway.toml applies to EVERY service
built from the repo, and code config always overrides the dashboard. The
worker therefore inherits the web service's build/deploy settings:

  - dockerfilePath = docker/Dockerfile.web  (fine: same image — it has
    arq and the full app; only the CMD differs)
  - predeployCommand = python scripts/sync_schema.py  (harmless:
    idempotent schema sync)
  - healthcheckPath = "/" on $PORT  (problem: arq serves no HTTP)

This module solves the last one: it starts a minimal health responder on
$PORT (so the inherited healthcheck passes), then runs the arq worker in
the foreground via arq's run_worker().

Start command — set on the worker service ONLY:
    Dashboard -> worker -> Settings -> Deploy -> Start Command:
        python -m app.backend.worker_main
(Leave the web service start command empty so Dockerfile.web's CMD runs.)

Do NOT use this entry point for the web service.
"""
from __future__ import annotations

import http.server
import logging
import os
import threading

logger = logging.getLogger(__name__)


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request with 200 — exists only for Railway's
    inherited healthcheckPath = "/" probe."""

    def _respond(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"worker ok")

    do_GET = _respond
    do_HEAD = _respond

    def log_message(self, fmt, *args):  # silence per-request access logs
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("PORT", "8080"))
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info("worker health responder listening on :%d", port)
    except OSError as exc:
        # No health responder, but the worker itself can still run.
        logger.warning("health responder failed to bind :%d (%s) — continuing", port, exc)

    # run_worker blocks until the process is signalled (Railway sends
    # SIGTERM on redeploy; arq finishes in-flight jobs within the drain
    # window before exiting).
    from arq.worker import run_worker

    from app.backend.worker import WorkerSettings

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()

"""
app/backend/worker_main.py
==========================
Worker-service entry point for Railway.

Background: this project's single railway.toml applies to EVERY service
built from the repo, and code config always overrides the dashboard. The
worker therefore inherits the web service's build/deploy settings:

  - dockerfilePath = docker/Dockerfile.web  (fine: same image — it has
    arq and the full app; the CMD branches on the START_CMD env var)
  - predeployCommand = python scripts/sync_schema.py  (harmless:
    idempotent schema sync)
  - healthcheckPath = "/" on $PORT  (problem: arq serves no HTTP)

This module solves the last one: it starts the shared health responder on
$PORT (so the inherited healthcheck passes), then runs the arq worker in
the foreground via arq's run_worker().

Start command — the worker service sets in its Variables:
    START_CMD=python -m app.backend.worker_main
(Dockerfile.web's CMD expands $START_CMD; the web service leaves it unset
and runs uvicorn.)

Do NOT use this entry point for the web service.
"""
from __future__ import annotations

import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    from app.backend.health_responder import start_health_responder
    start_health_responder()

    # run_worker blocks until the process is signalled (Railway sends
    # SIGTERM on redeploy; arq finishes in-flight jobs within the drain
    # window before exiting).
    from arq.worker import run_worker

    from app.backend.worker import WorkerSettings

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()

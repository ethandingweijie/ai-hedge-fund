"""Shared pytest fixtures — session-level environment hygiene.

Import-time ``load_dotenv`` in production modules (``app/backend/routes/analysis.py``
loads ``.env.local`` when imported, which happens during collection of the
queue-mode test modules) injects REAL API keys into ``os.environ`` for the
whole pytest process. Without this guard, later tests that build LLM/HTTP
clients from env (card-QA judge, digest agent, FMP augmentation) silently
make live paid calls — each hanging up to a 60 s timeout and burning tokens.

Tests that need these vars set fake values themselves via
``monkeypatch.setenv`` (e.g. the dual-mode suites point RUN_ARCHIVE_PATH at a
tmp file), so stripping the real ones at session start is safe and keeps the
suite hermetic and offline.
"""
from __future__ import annotations

import os

import pytest

# Keys whose presence routes code paths to live external services.
_SENSITIVE_ENV_KEYS = (
    "FMP_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEP_RESEARCH_API_KEY",
    "DEEPSEEK_API_KEY",
    "TAVILY_API_KEY",
    "SLACK_WEBHOOK_URL",
    "SMTP_USER",
    "SMTP_PASS",
    "DATABASE_URL",  # tests must use tmp sqlite, never the production Postgres
)


@pytest.fixture(autouse=True, scope="session")
def _strip_live_api_keys():
    """Remove real keys leaked into the process env by import-time dotenv
    loads (collection imports production modules before any test runs)."""
    removed = {k: os.environ.pop(k) for k in _SENSITIVE_ENV_KEYS if k in os.environ}
    if removed:
        print(
            "\n[conftest] stripped live keys leaked by import-time load_dotenv: "
            + ", ".join(sorted(removed))
        )
    yield

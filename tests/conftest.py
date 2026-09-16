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
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEP_RESEARCH_API_KEY",
    "DEEPSEEK_API_KEY",
    "TAVILY_API_KEY",
    "SLACK_WEBHOOK_URL",
    "SMTP_USER",
    "SMTP_PASS",
    "DATABASE_URL",  # tests must use tmp sqlite, never the production Postgres
)


def pytest_addoption(parser):
    parser.addoption(
        "--update-snapshots", action="store_true", default=False,
        help="Rewrite tests/golden/snapshots.json from current golden replay "
             "output. Requires a non-empty GOLDEN_UPDATE_REASON env var.",
    )


def pytest_configure(config):
    """Gate the golden-baseline rewrite on a written reason.

    ``snapshots.json`` is the only artefact that records "the valuation
    engine's answer changed". Letting it move without a reason is how a
    regression becomes the new baseline, so the flag refuses to work unless
    the caller states why in ``GOLDEN_UPDATE_REASON``.
    """
    if not config.getoption("--update-snapshots"):
        return
    reason = os.environ.get("GOLDEN_UPDATE_REASON", "").strip()
    if not reason:
        raise pytest.UsageError(
            "--update-snapshots refuses to run without GOLDEN_UPDATE_REASON.\n"
            "The golden baseline pins end-to-end valuations; moving it is the\n"
            "one change that needs a written reason. Set it, e.g.:\n\n"
            "  GOLDEN_UPDATE_REASON=\"Phase 1.1: convergence fade on "
            "cyclical/conglomerate profiles\" \\\n"
            "  pytest tests/test_golden_valuations.py --update-snapshots\n"
        )
    config._golden_update_reason = reason


@pytest.fixture(scope="session")
def golden_update_reason(request) -> str | None:
    """The ``GOLDEN_UPDATE_REASON`` when ``--update-snapshots`` was passed."""
    return getattr(request.config, "_golden_update_reason", None)


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


#: Process-lifetime memo caches. Each would otherwise carry one test's stubbed
#: upstream responses into the next test that asks for the same key.
_PROCESS_CACHES = (
    ("src.tools.api", "_STATEMENT_CACHE"),
    ("src.agents.routing.macro_regime", "_REGIME_CACHE"),
    ("src.memory.calibration", "_cache"),
    ("src.tools.earnings_calendar", "_CALENDAR_CACHE"),
)


@pytest.fixture(autouse=True)
def _clear_process_caches():
    import sys
    for module_name, attr in _PROCESS_CACHES:
        module = sys.modules.get(module_name)   # never import just to clear
        cache = getattr(module, attr, None) if module is not None else None
        if cache is not None:
            cache.clear()
    yield

"""The daily learning sweep: one call from the outcomes job, every loop in its own guard.

Runs after the outcome labels are written and before the canary, inside
worker.run_valuation_outcomes_task, so it shares that job's lock and same-day retry.
A loop that fails or is switched off reports so and never blocks the others.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


def _guard(name: str, fn: Callable, report: dict, **kw) -> None:
    try:
        report[name] = fn(**kw)
    except Exception as exc:                               # noqa: BLE001
        logger.warning("[learning] %s raised %s: %s", name, type(exc).__name__, exc)
        report[name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def run_daily(*, write: bool = True) -> dict:
    """run_features backfill, then the estimate scorer (and, as they ship, the gate and
    override scorers). Idempotent end to end."""
    from src.memory import estimate_outcomes as eo
    from src.memory import run_features as rf
    report: dict = {}
    _guard("run_features", rf.backfill, report, write=write)
    _guard("estimate_outcomes", eo.score_matured, report, write=write)
    try:                                                   # Phase D
        from src.memory import gate_outcomes as go
        _guard("gate_outcomes", go.score_matured, report, write=write)
    except ImportError:
        pass
    try:                                                   # Phase E
        from src.memory import override_outcomes as oo
        _guard("override_outcomes", oo.score_matured, report, write=write)
    except ImportError:
        pass
    return report

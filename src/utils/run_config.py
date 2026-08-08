"""
src/utils/run_config.py
========================
Per-run configuration overlay.

Background
----------
Web runs used to apply the caller's API keys by writing them straight into
``os.environ`` (analysis_service.py).  ``os.environ`` is process-global, so two
concurrent runs raced: the second run's keys clobbered the first's mid-flight,
and User A's pipeline could execute against User B's Anthropic/FMP key and
model.  With one user that never showed up; with two it is silent and constant.

This module replaces that with a ``ContextVar`` overlay.  Reads go through
``getenv()``, which consults the current run's overlay first and falls back to
``os.environ``.  Because it is a ContextVar and not a thread-local, the overlay
follows the run into worker threads as long as those threads are started with a
copied context — see ``spawn`` / ``submit`` below.

Usage
-----
Producer (once per run, on the request-handling side)::

    from src.utils import run_config
    run_config.set_run_settings({"ANTHROPIC_API_KEY": ..., "FMP_API_KEY": ...})

Consumer (anywhere a process-wide env read used to live)::

    from src.utils.run_config import getenv
    api_key = getenv("ANTHROPIC_API_KEY")

Crossing a thread boundary::

    from src.utils.run_config import spawn, submit
    spawn(target=_run_pipeline, daemon=True).start()     # threading.Thread
    submit(pool, fn, arg)                                # ThreadPoolExecutor
"""

from __future__ import annotations

import contextvars
import os
import threading
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Optional

# The overlay itself. Empty dict => every read falls through to os.environ,
# which is exactly the CLI / scheduler behaviour.
_run_settings: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "run_settings", default={}
)


# ── Reads ─────────────────────────────────────────────────────────────────────


def getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a setting: current run's overlay first, then ``os.environ``.

    Drop-in replacement for ``os.getenv`` / ``os.environ.get`` at any site that
    might execute inside a per-run context.
    """
    value = _run_settings.get().get(name)
    if value:
        return value
    return os.environ.get(name, default)


def current_settings() -> Mapping[str, str]:
    """Return the active overlay (empty outside a run). Read-only by convention."""
    return _run_settings.get()


# ── Writes ────────────────────────────────────────────────────────────────────


def set_run_settings(settings: Mapping[str, str]) -> contextvars.Token:
    """Install an overlay for the current context.

    Only non-empty values are kept, so passing ``{"FMP_API_KEY": ""}`` leaves
    the process-level key visible instead of blanking it.
    """
    cleaned = {k: v for k, v in settings.items() if k and v}
    return _run_settings.set(cleaned)


def update_run_settings(settings: Mapping[str, str]) -> contextvars.Token:
    """Merge additional values into the current overlay."""
    merged = dict(_run_settings.get())
    merged.update({k: v for k, v in settings.items() if k and v})
    return _run_settings.set(merged)


def reset_run_settings(token: contextvars.Token) -> None:
    """Undo a ``set_run_settings`` / ``update_run_settings``."""
    _run_settings.reset(token)


@contextmanager
def run_settings(settings: Mapping[str, str]) -> Iterator[None]:
    """Scope an overlay to a ``with`` block."""
    token = set_run_settings(settings)
    try:
        yield
    finally:
        reset_run_settings(token)


# ── Thread boundaries ─────────────────────────────────────────────────────────
#
# ContextVars are per-context, and a bare `threading.Thread` / `Executor.submit`
# starts with a *fresh* context — the overlay (and progress's run_id) would be
# lost. Both helpers below snapshot the caller's context and run the callable
# inside it, so anything spawned from a run stays tagged with that run.


def spawn(target: Callable[..., Any], *, args: tuple = (), kwargs: Optional[dict] = None,
          daemon: bool = False, name: Optional[str] = None) -> threading.Thread:
    """``threading.Thread`` that inherits the caller's context."""
    ctx = contextvars.copy_context()
    return threading.Thread(
        target=lambda: ctx.run(target, *args, **(kwargs or {})),
        daemon=daemon,
        name=name,
    )


def submit(executor: Executor, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """``Executor.submit`` that inherits the caller's context."""
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, lambda: fn(*args, **kwargs))

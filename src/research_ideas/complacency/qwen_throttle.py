"""
src/research_ideas/complacency/qwen_throttle.py
================================================
Process-wide token-bucket rate limiter for Qwen API calls.

PROBLEM: DashScope rate-limits aggressively under burst load (3-4
concurrent calls trips the "limit_burst_rate" 429). Per-client
max_retries handles single events but workers retry INDEPENDENTLY,
so 3 workers each retrying = 9 calls in the recovery window, which
re-trips the limit. This caused the NVDA force-rescore hang where
indicators 7-10 cascaded into 429 failures.

SOLUTION: A SINGLE token bucket shared across all Qwen worker threads
in the process. Workers must acquire() before any Qwen call. When a
429 is observed, report_429() drains the bucket so ALL other workers
back off cooperatively.

Used by:
  • src/llm/models.py (ChatOpenAI for qualitative first-pass scoring)
  • src/research_ideas/complacency/web_research.py (qwen_web_search,
    deep_research_indicator, fetch_earnings_qa)
  • src/research_ideas/contrarian/chat_agent.py (IoTD chat)

Tunable via env vars:
  QWEN_MAX_RPS               — refill rate (default 0.5 = 1 call / 2s)
  QWEN_BURST_SIZE            — bucket capacity (default 3)
  QWEN_BACKOFF_ON_429        — sec to drain on 429 with no Retry-After
                                (default 30s)
  QWEN_THROTTLE_DISABLED     — set to "true" to disable entirely
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Knobs ────────────────────────────────────────────────────────────────


_DISABLED = os.environ.get("QWEN_THROTTLE_DISABLED", "false").lower() == "true"
_MAX_RPS = float(os.environ.get("QWEN_MAX_RPS", "0.5"))
_BURST_SIZE = int(os.environ.get("QWEN_BURST_SIZE", "3"))
_DEFAULT_BACKOFF_S = float(os.environ.get("QWEN_BACKOFF_ON_429", "30"))


# ─── Core token-bucket implementation ─────────────────────────────────────


class _QwenThrottle:
    """Token bucket: refills at _MAX_RPS, capped at _BURST_SIZE.

    Workers call acquire() to spend a token, blocking if none available.
    On 429, report_429() can EXPLICITLY drain the bucket for N seconds,
    causing all other workers to wait cooperatively.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = float(_BURST_SIZE)
        self._last_refill = time.monotonic()
        self._cooldown_until = 0.0  # monotonic timestamp
        # Telemetry
        self._429_events: list[float] = []      # timestamps for sliding 5-min window
        self._acquire_count = 0
        self._wait_time_total = 0.0

    def _refill(self) -> None:
        """Add tokens based on elapsed time. Holds self._lock."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * _MAX_RPS
        self._tokens = min(_BURST_SIZE, self._tokens + added)
        self._last_refill = now

    def acquire(self, weight: float = 1.0, max_wait_s: float = 60.0) -> float:
        """Block until at least `weight` tokens available. Returns wait time.

        If _cooldown_until is in the future (recent 429), workers wait
        until cooldown expires before competing for tokens. max_wait_s
        is a safety cap — beyond which we return anyway (rather than
        deadlock if the throttle gets stuck).
        """
        if _DISABLED:
            return 0.0
        start = time.monotonic()
        while True:
            with self._lock:
                # Honor cooldown first — overrides token state
                remaining_cooldown = self._cooldown_until - time.monotonic()
                if remaining_cooldown <= 0:
                    self._refill()
                    if self._tokens >= weight:
                        self._tokens -= weight
                        waited = time.monotonic() - start
                        self._acquire_count += 1
                        self._wait_time_total += waited
                        return waited
                    # Not enough tokens — compute wait time to next available
                    deficit = weight - self._tokens
                    next_token_in = deficit / _MAX_RPS
                else:
                    next_token_in = remaining_cooldown

            # Out of lock — sleep then retry
            if (time.monotonic() - start) + next_token_in > max_wait_s:
                logger.warning(
                    "qwen_throttle.acquire: max_wait_s=%s reached without "
                    "acquiring %s tokens — proceeding anyway.", max_wait_s, weight,
                )
                with self._lock:
                    self._tokens = max(0.0, self._tokens - weight)
                    self._acquire_count += 1
                return time.monotonic() - start
            time.sleep(min(next_token_in, 1.0))

    def report_429(self, retry_after_s: Optional[float] = None) -> None:
        """Worker calls this immediately on observing HTTP 429.

        Sets cooldown_until = now + (retry_after_s OR _DEFAULT_BACKOFF_S).
        All concurrent workers calling acquire() will then wait until
        the cooldown expires before getting any token.
        """
        backoff = float(retry_after_s) if retry_after_s is not None else _DEFAULT_BACKOFF_S
        with self._lock:
            new_until = time.monotonic() + backoff
            if new_until > self._cooldown_until:
                self._cooldown_until = new_until
            self._tokens = 0.0  # drain bucket
            self._429_events.append(time.time())
            # Trim sliding window (keep last 5 minutes)
            cutoff = time.time() - 300
            self._429_events = [t for t in self._429_events if t > cutoff]
        logger.warning(
            "qwen_throttle: 429 reported — draining bucket for %.0fs "
            "(%d 429s in last 5 min)", backoff, len(self._429_events),
        )

    def stats(self) -> dict:
        with self._lock:
            self._refill()
            cooldown_remaining = max(0.0, self._cooldown_until - time.monotonic())
            return {
                "enabled": not _DISABLED,
                "max_rps": _MAX_RPS,
                "burst_size": _BURST_SIZE,
                "current_tokens": round(self._tokens, 2),
                "cooldown_remaining_s": round(cooldown_remaining, 1),
                "acquire_count_total": self._acquire_count,
                "avg_wait_s": round(
                    self._wait_time_total / max(1, self._acquire_count), 3,
                ),
                "429_events_last_5_min": len(self._429_events),
            }


# Singleton instance
_throttle = _QwenThrottle()


# ─── Public API ───────────────────────────────────────────────────────────


def acquire(weight: float = 1.0, max_wait_s: float = 60.0) -> float:
    """Block until token available. Returns wait time in seconds.

    Usage:
        wait = qwen_throttle.acquire()
        response = client.chat.completions.create(...)
    """
    return _throttle.acquire(weight=weight, max_wait_s=max_wait_s)


def report_429(retry_after_s: Optional[float] = None) -> None:
    """Notify throttle of an observed HTTP 429."""
    _throttle.report_429(retry_after_s=retry_after_s)


def stats() -> dict:
    """Return current throttle state for diagnostics."""
    return _throttle.stats()


@contextmanager
def qwen_call(weight: float = 1.0):
    """Convenience context manager.

    Usage:
        with qwen_throttle.qwen_call():
            try:
                response = client.chat.completions.create(...)
            except openai.RateLimitError as exc:
                ra = exc.response.headers.get('retry-after')
                qwen_throttle.report_429(float(ra) if ra else None)
                raise
    """
    wait = acquire(weight=weight)
    yield wait


def report_429_from_exception(exc) -> None:
    """Inspect an openai.RateLimitError and call report_429 with the
    Retry-After header if present. Safe to call on any exception —
    no-ops if not a 429.
    """
    # Duck-type: don't import openai at module level (avoid import
    # ordering issues; we only care about the .response.headers attr).
    try:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status != 429:
            return
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        report_429(retry_after_s=float(retry_after) if retry_after else None)
    except Exception:
        # Best-effort — never let a bad header break the flow
        report_429(retry_after_s=None)

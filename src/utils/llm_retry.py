"""
src/utils/llm_retry.py
======================
Shared classification + backoff logic for LLM call errors.

Ported from the proven logic in ``deep_research._call_llm_with_rate_retry``
(src/agents/industry/deep_research.py, battle-tested through the 2026-04-24
DashScope 403-as-rate-limit incident) so every LLM call path shares ONE
definition of "transient vs permanent":

  * ``call_llm`` (src/utils/llm.py) — pipeline agents, previously retried
    3x with no classification and no backoff.
  * ``deep_research._call_llm_with_rate_retry`` — delegates here instead of
    keeping its own inline copy.

Classification rules (retryable → kind):
  rate-limit family   → retryable, "rate_limit"
      Covers DashScope's quirk of returning HTTP 403 with a
      "Rate limit exceeded" message instead of HTTP 429, plus quota /
      throttle / access-denied variants seen from both the anthropic and
      openai SDKs.
  timeout/connection  → retryable, "timeout"
      APITimeoutError / connection drops — Qwen hanging past the client
      timeout is transient and deserves a fresh connection.
  everything else     → NOT retryable, "other"
      Auth errors, bad requests, missing keys — retrying these just burns
      attempts and re-hammers the same failure.

Parse errors are a separate concern (helper ``is_parse_error``, kind
``"parse"``): the LLM responded but its output failed to parse/validate.
That is transient by nature — the same prompt often parses on retry — so
``call_llm`` retries them like rate limits. ``classify_llm_error`` itself
stays infrastructure-only so ``deep_research`` keeps its exact semantics
(parse failures there propagate to the higher-level nudge/repair loops).

Backoff: ``compute_backoff(attempt, base, cap)`` = base * 2**attempt capped
at ``cap`` seconds. ``retry_after_from(exc)`` extracts an upstream
Retry-After header when the exception carries a response object; callers
take max(retry_after, computed) so they never hammer sooner than the
server asked but also never wait less than the backoff floor.
"""
from __future__ import annotations

# Error kinds returned by classify_llm_error
KIND_RATE_LIMIT = "rate_limit"
KIND_TIMEOUT = "timeout"
KIND_OTHER = "other"
KIND_PARSE = "parse"  # used by call_llm for malformed-LLM-output retries

# Class names that mean "the LLM answered but we couldn't parse/validate it".
# Matched by name so we don't need langchain/pydantic imports here.
_PARSE_EXC_NAMES = {"outputparserexception", "validationerror"}
_PARSE_MSG_HINTS = ("failed to parse", "could not parse", "invalid json")


def is_parse_error(exc: BaseException) -> bool:
    """True when the exception means "LLM output arrived but didn't parse".

    Covers langchain's OutputParserException (raised by
    ``with_structured_output``) and pydantic ValidationErrors, matched by
    class name plus common message phrases. These are transient: retrying
    the same prompt frequently yields parseable output.
    """
    if type(exc).__name__.lower() in _PARSE_EXC_NAMES:
        return True
    msg = str(exc).lower()
    return any(h in msg for h in _PARSE_MSG_HINTS)


def classify_llm_error(exc: BaseException) -> tuple[bool, str]:
    """Classify an LLM-call exception.

    Returns:
        (retryable, kind) where kind is one of KIND_RATE_LIMIT,
        KIND_TIMEOUT, KIND_OTHER. Non-retryable errors always return
        KIND_OTHER.
    """
    msg = str(exc).lower()
    exc_name = type(exc).__name__.lower()

    is_rate_limit = (
        "rate limit" in msg
        or "ratelimit" in msg
        or "rate_limit" in msg
        or "quota" in msg
        or "throttl" in msg
        or "accessdenied" in msg
        or "access_denied" in msg
    )
    if is_rate_limit:
        return True, KIND_RATE_LIMIT

    # APITimeoutError / connection drops from Qwen — treat as retryable
    # transient failures (same operational category as rate limits).
    is_timeout = (
        "timeout" in msg
        or "timed out" in msg
        or "connection" in msg
        or "apitimeouterror" in exc_name
        or "apiconnectionerror" in exc_name
    )
    if is_timeout:
        return True, KIND_TIMEOUT

    return False, KIND_OTHER


def compute_backoff(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    """Exponential backoff: base * 2**attempt, capped at `cap` seconds.

    attempt is 0-based: compute_backoff(0, 2.0, 30.0) == 2.0,
    compute_backoff(1) == 4.0, ... compute_backoff(5) == 30.0 (capped).
    """
    return min(base * (2 ** max(0, attempt)), cap)


def retry_after_from(exc: BaseException) -> float | None:
    """Extract Retry-After header seconds from an SDK exception, if present.

    Both the openai SDK (APIStatusError.response.headers) and the anthropic
    SDK expose response headers on their error objects, but naming varies —
    return None instead of raising on any shape mismatch.
    """
    try:
        response = getattr(exc, "response", None)
        if response is not None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                ra = headers.get("Retry-After") or headers.get("retry-after")
                if ra:
                    return float(ra)
    except (ValueError, TypeError, AttributeError):
        return None
    return None

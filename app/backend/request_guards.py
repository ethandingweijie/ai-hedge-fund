"""
app/backend/request_guards.py
==============================
Small input/output guards shared by route modules.

* ``validate_ticker_input`` -- server-side ticker shape check. A chaos audit
  found ``AAPL;DROP``, ``../../ETC``, ``\\x00AAPL``, 50-char strings and
  full-width characters all reached the cache lookup and the pipeline, and a
  non-string ticker raised a 500. Every accepted form (AAPL, BRK.B, BRK-B,
  0700.HK, 00700.HK, 2888, D05.SI) is plain ASCII letters/digits with at most
  one ``.``/``-`` suffix, so anything else is rejected with a 422 before any
  work starts. Canonicalisation (HK padding etc.) still happens afterwards in
  ``canonical_ticker`` -- this only decides what is allowed in.

* ``internal_error`` -- a 500 whose body carries a generic message and a
  request id instead of the exception text and traceback. The traceback is
  logged server-side under the same id so an operator can find it.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException

# Base symbol 1-10 ASCII alphanumerics, optional single suffix of 1-5
# (".B", "-B", ".HK", ".SI"). Explicit ASCII classes: \w would admit
# full-width and other Unicode letters.
# An optional venue prefix of 2-6 letters ("KRX:005930", "KOSDAQ:247540", "TYO:7203", "TSE:7203") is admitted and
# resolved by canonical_ticker; it is still plain ASCII and cannot carry a path or a second suffix.
# Exchange prefixes are an allowlist (Tokyo TYO/TSE/JPX/JP, Korea KRX/KOSPI/KOSDAQ/KS/KQ) -- "ABC:XYZ" is rejected.
TICKER_RE = re.compile(r"(?:(?i:TYO|TSE|JPX|JP|KRX|KOSPI|KOSDAQ|KS|KQ):)?[A-Za-z0-9]{1,10}(?:[.\-][A-Za-z0-9]{1,5})?")
TICKER_MAX_LEN = 16

# Model ids are things like "claude-sonnet-4-6", "qwen3.8-flash".
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]\-]{0,99}")


def is_valid_ticker_input(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) > TICKER_MAX_LEN:
        return False
    return TICKER_RE.fullmatch(s) is not None


def validate_ticker_input(value: Any, field: str = "ticker") -> str:
    """Return the stripped ticker, or raise HTTP 422."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HTTPException(status_code=422, detail=f"{field} is required")
    if not is_valid_ticker_input(value):
        raise HTTPException(
            status_code=422,
            detail=(f"invalid {field}: expected 1-10 letters/digits with an "
                    f"optional .XX or -X suffix (e.g. AAPL, BRK.B, 0700.HK, D05.SI, 600519.SS, 7203.T, 005930.KS)"),
        )
    return value.strip()


def validate_model_input(value: Any, default: str) -> str:
    """Return the requested model id (or the default), or raise HTTP 422."""
    if value is None or value == "":
        return default
    if not isinstance(value, str) or MODEL_RE.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="invalid model")
    return value


def internal_error(logger: logging.Logger, context: str) -> HTTPException:
    """Log the active exception with a fresh request id; return a generic 500.

    Call from inside an ``except`` block: ``raise internal_error(logger, "x")``.
    """
    request_id = uuid.uuid4().hex[:12]
    logger.exception("%s failed [request_id=%s]", context, request_id)
    return HTTPException(
        status_code=500,
        detail=f"Internal server error (request id {request_id})",
        headers={"X-Request-ID": request_id},
    )

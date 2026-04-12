"""
Shared helpers for the hk/ data layer.
"""
from __future__ import annotations


def _parse_float(val) -> float | None:
    """Safely coerce a value to float, handling AKShare sentinel strings."""
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "--", "-", "N/A", "nan", "None", "NaN", "inf"):
        return None
    try:
        # Strip common suffixes / formatting
        cleaned = s.replace(",", "").replace("%", "").replace("亿", "").replace("万", "")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _safe_div(a, b) -> float | None:
    """Return a / b, or None if either is None or b is zero."""
    a = _parse_float(a)
    b = _parse_float(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _safe_add(*args) -> float | None:
    """Return sum of args; return None if ALL args are None."""
    parsed = [_parse_float(v) for v in args]
    valid = [v for v in parsed if v is not None]
    if not valid:
        return None
    return sum(valid)


def _safe_sub(a, b) -> float | None:
    """Return a - b, or None if either is None."""
    a = _parse_float(a)
    b = _parse_float(b)
    if a is None or b is None:
        return None
    return a - b


def _ak_date(date_str: str) -> str:
    """Convert YYYY-MM-DD → YYYYMMDD for AKShare date parameters."""
    return date_str.replace("-", "")

"""
Shared helpers for SGX data layer.
Reuses patterns from src/tools/hk/_utils.py but simplified for SGD-denominated data.
"""

import math
from typing import Optional


def _parse_float(val) -> Optional[float]:
    """Safely coerce a value to float, handling common sentinel values."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    s = str(val).strip().replace(",", "").replace("%", "")
    if not s or s in ("--", "N/A", "nan", "inf", "-inf", "None", "null"):
        return None
    try:
        f = float(s)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """a / b with None and zero guards."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _safe_sub(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def _safe_add(*args: Optional[float]) -> Optional[float]:
    vals = [v for v in args if v is not None]
    return sum(vals) if vals else None


def _cap_growth(val: Optional[float], limit: float = 2.0) -> Optional[float]:
    """Cap growth rates to ±limit (default ±200%) to prevent parse errors."""
    if val is None:
        return None
    return max(-limit, min(limit, val))

"""
HK ticker detection and normalisation utilities.

Supported input formats:
  "700"        →  purely numeric (1-5 digits)
  "0700"       →  with leading zeros
  "00700"      →  full 5-digit AKShare format
  "0700.HK"    →  yfinance 4-digit + .HK
  "00700.HK"   →  5-digit + .HK

Canonical form (used as cache keys / DB keys): "NNNNN.HK"
AKShare form:  "NNNNN"    (5-digit zero-padded)
yfinance form: "NNNN.HK"  (4-digit zero-padded + .HK)
"""

import re

# HK tickers are purely numeric (1–5 digits), with an optional .HK suffix.
# US tickers always contain at least one letter — the regex is therefore safe.
_HK_PATTERN = re.compile(r"^\d{1,5}(\.HK)?$", re.IGNORECASE)


def is_hk_ticker(ticker: str) -> bool:
    """Return True if the ticker looks like an HKEX stock code.

    Examples
    --------
    >>> is_hk_ticker("00700")   # Tencent
    True
    >>> is_hk_ticker("0700.HK")
    True
    >>> is_hk_ticker("AAPL")
    False
    >>> is_hk_ticker("MSFT")
    False
    """
    if not ticker:
        return False
    return bool(_HK_PATTERN.match(ticker.strip()))


def to_akshare_code(ticker: str) -> str:
    """Normalise to AKShare 5-digit zero-padded format.

    Examples
    --------
    >>> to_akshare_code("700")
    '00700'
    >>> to_akshare_code("0700.HK")
    '00700'
    >>> to_akshare_code("00700.HK")
    '00700'
    """
    raw = ticker.strip().upper().replace(".HK", "")
    return raw.zfill(5)


def to_yfinance_code(ticker: str) -> str:
    """Normalise to yfinance 4-digit + .HK format.

    Examples
    --------
    >>> to_yfinance_code("00700")
    '0700.HK'
    >>> to_yfinance_code("700")
    '0700.HK'
    """
    raw = ticker.strip().upper().replace(".HK", "").lstrip("0") or "0"
    return raw.zfill(4) + ".HK"


def to_canonical(ticker: str) -> str:
    """Return the canonical "NNNNN.HK" form used as cache keys and DB keys.

    Examples
    --------
    >>> to_canonical("700")
    '00700.HK'
    >>> to_canonical("0700.HK")
    '00700.HK'
    """
    return to_akshare_code(ticker) + ".HK"

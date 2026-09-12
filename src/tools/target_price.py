"""Analyst consensus target prices for HK and SG listings.

FMP carries no price-target consensus for HKEX or SGX -- it says so itself
("no price-target consensus data for this venue ... by design, not a
failure"). That left every HK and SG valuation with no external reference to
be checked against, which is the difference between a number and a number
someone can disagree with.

stockanalysis.com renders the consensus into the page HTML, sourced from
S&P Global Market Intelligence, and covers both venues:

    HK   https://stockanalysis.com/quote/hkg/0700/forecast/
    SG   https://stockanalysis.com/quote/sgx/D05/forecast/

The figure is stated in a sentence rather than a data attribute -- "According
to 16 analysts polled by S&P Global, DBS Group Holdings stock has a consensus
rating of Buy and an average price target of $77.11" -- so it is parsed from
that sentence, and the analyst count and low/high are taken with it. A target
without a count is not much of a consensus.

Returned prices are in the LISTING currency: HKD for a .HK line, SGD for .SI.
The page writes every currency as "$", so the caller must not assume USD.

Responses are cached on disk: a consensus moves slowly, and a benchmark that
changes between two runs of the same comparison is not a benchmark.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path(os.environ.get(
    "TARGET_PRICE_CACHE_DIR",
    Path(__file__).resolve().parents[2] / ".cache" / "target_price"))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_SENTENCE = re.compile(
    r"According to\s+(?P<n>\d+)\s+analysts?[^.]*?"
    r"average price target of\s+\$?(?P<target>[\d,]+\.?\d*)", re.I | re.S)
_LOW = re.compile(r"lowest is\s+\$?([\d,]+\.?\d*)", re.I)
_HIGH = re.compile(r"highest is\s+\$?([\d,]+\.?\d*)", re.I)
_RATING = re.compile(r'consensus rating of\s+"?([A-Za-z ]+?)"?\s+and', re.I)


def _num(raw: str | None) -> Optional[float]:
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def quote_path(ticker: str) -> Optional[tuple[str, str]]:
    """(url path, listing currency) for a ticker, or None if unsupported."""
    t = (ticker or "").strip().upper()
    if t.endswith(".HK"):
        code = t[:-3].lstrip("0").zfill(4)      # site uses 4 digits: 0700
        return f"hkg/{code}", "HKD"
    if t.endswith(".SI"):
        return f"sgx/{t[:-3]}", "SGD"
    return None


def _cache_file(ticker: str) -> Path:
    key = hashlib.sha1(ticker.encode()).hexdigest()[:12]
    safe = "".join(c if c.isalnum() else "_" for c in ticker)
    return _CACHE_DIR / f"{safe}_{key}.json"


def get_consensus_target(ticker: str, *, refresh: bool = False
                         ) -> Optional[dict]:
    """{target, low, high, analysts, rating, currency, source} or None.

    Never raises. Returns None when the venue is unsupported, the page is
    unavailable, or the sentence cannot be parsed -- a missing benchmark must
    read as missing, not as zero.
    """
    route = quote_path(ticker)
    if not route:
        return None
    path, ccy = route

    cf = _cache_file(ticker)
    if not refresh and cf.exists():
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            pass

    url = f"https://stockanalysis.com/quote/{path}/forecast/"
    try:
        import requests
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=25)
        if resp.status_code != 200:
            return None
        body = resp.text
    except Exception:                                      # noqa: BLE001
        return None

    m = _SENTENCE.search(body)
    if not m:
        return None
    target = _num(m.group("target"))
    if not target:
        return None
    low_m, high_m, rating_m = _LOW.search(body), _HIGH.search(body), _RATING.search(body)
    out = {
        "ticker": ticker,
        "target": target,
        "low": _num(low_m.group(1)) if low_m else None,
        "high": _num(high_m.group(1)) if high_m else None,
        "analysts": int(m.group("n")),
        "rating": (rating_m.group(1).strip() if rating_m else None),
        "currency": ccy,
        "source": "stockanalysis.com / S&P Global Market Intelligence",
        "url": url,
    }
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(out, indent=1), encoding="utf-8")
    except Exception:                                      # noqa: BLE001
        pass
    return out

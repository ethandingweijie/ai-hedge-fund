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
#: The page states the implied move alongside the target. It is the only
#: internal cross-check available, and it catches a stale or mis-scaled
#: target that is otherwise indistinguishable from a good one.
_MOVE = re.compile(
    r"forecast is\s+([\d.]+)%\s+(higher|lower)\s+than the current stock price",
    re.I)
#: The page states the venue currency, and separately the quote currency when
#: the line trades in something else: "Currency is SGD - Price in USD". SGX
#: lists Jardine Matheson, Hongkong Land, DFI Retail and HPH Trust in USD, so
#: taking SGD from the .SI suffix compared a USD target against an SGD
#: valuation and called the ~27% gap a modelling error.
_CCY_VENUE = re.compile(r"Currency is\s+([A-Z]{3})\b")
_CCY_QUOTE = re.compile(r"Price in\s+([A-Z]{3})\b")

_BULLISH = ("buy", "strong buy", "outperform", "overweight")
_BEARISH = ("sell", "strong sell", "underperform", "underweight")


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


#: Bump when the payload shape changes. Entries written before currency
#: detection carry a currency that is simply wrong for USD-quoted SGX lines,
#: and a cached wrong answer is worse than no answer -- it never gets retried.
_SCHEMA = 2


def _cache_file(ticker: str) -> Path:
    key = hashlib.sha1(ticker.encode()).hexdigest()[:12]
    safe = "".join(c if c.isalnum() else "_" for c in ticker)
    return _CACHE_DIR / f"{safe}_{key}_v{_SCHEMA}.json"


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

    # Quote currency, straight from the page. "Price in X" wins over
    # "Currency is Y" -- the target is quoted in whatever the line trades in.
    q, v = _CCY_QUOTE.search(body), _CCY_VENUE.search(body)
    page_ccy = (q.group(1) if q else (v.group(1) if v else None))

    m = _SENTENCE.search(body)
    if not m:
        return None
    target = _num(m.group("target"))
    if not target:
        return None
    low_m, high_m, rating_m = _LOW.search(body), _HIGH.search(body), _RATING.search(body)
    rating = rating_m.group(1).strip() if rating_m else None

    # Cross-check the target against the implied move the page states itself.
    # MMG carried a "Strong Buy" with a target 83.57% BELOW the traded price --
    # internally contradictory, and it made MMG the single worst deviation in
    # a 144-name benchmark (IV 21.5 against a "target" of 1.49) purely because
    # the reference was broken. A benchmark that silently carries a bad value
    # corrupts every comparison drawn from it, so say so rather than return it
    # as though it were sound.
    move_m = _MOVE.search(body)
    implied_move = None
    if move_m:
        implied_move = float(move_m.group(1)) / 100.0
        if move_m.group(2).lower() == "lower":
            implied_move = -implied_move
    suspect_reason = None
    if implied_move is not None and rating:
        r = rating.lower()
        if r in _BULLISH and implied_move < -0.30:
            suspect_reason = (f"rating {rating!r} with a target {implied_move:.0%} "
                              f"vs price -- internally contradictory")
        elif r in _BEARISH and implied_move > 0.30:
            suspect_reason = (f"rating {rating!r} with a target {implied_move:+.0%} "
                              f"vs price -- internally contradictory")

    out = {
        "ticker": ticker,
        "target": target,
        "low": _num(low_m.group(1)) if low_m else None,
        "high": _num(high_m.group(1)) if high_m else None,
        "analysts": int(m.group("n")),
        "rating": rating,
        "implied_move": implied_move,
        "suspect": bool(suspect_reason),
        "suspect_reason": suspect_reason,
        "currency": page_ccy or ccy,
        "venue_currency": ccy,
        "source": "stockanalysis.com / S&P Global Market Intelligence",
        "url": url,
        "schema": _SCHEMA,
    }
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(out, indent=1), encoding="utf-8")
    except Exception:                                      # noqa: BLE001
        pass
    return out

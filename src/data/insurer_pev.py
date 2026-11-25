"""Life insurers' Embedded Value leg on a measured peer P/EV (owner, 2026-11-25, HK/SG audit).

The leg priced every life insurer at exactly 1.0x its embedded value, which rated Ping An a BUY at twice spot
(the market pays ~0.5x for China domestic life) and AIA a SELL at 0.6x (~1.2x for the pan-Asian franchises).
A new-business multiple derived from the same peers was negative for the China names, so the owner chose a
relative read: the subject's EV per share x the median price / EV of its own cohort, the subject excluded.

Each member's EV comes from its OWNER-ACCEPTED embedded_value input (review-gated, src/data/industry_inputs.py);
its price is the live quote in the listing currency, the EV restated into that currency. A cohort with fewer
than `min_members` measurable peers prices nothing and the leg keeps 1.0x, saying so.
"""
from __future__ import annotations

import statistics
from typing import Callable, Optional

#: Owner, 2026-11-25. The cohort is the market the subject competes in, not the exchange it lists on.
COHORTS: dict[str, tuple[str, ...]] = {
    "pan_asian": ("01299.HK", "02378.HK", "MFC", "01828.HK", "G07.SI"),
    "china_domestic": ("02318.HK", "02628.HK", "02601.HK", "01339.HK", "01336.HK"),
}
MIN_MEMBERS = 2


def cohort_of(ticker: str) -> Optional[str]:
    from src.tools.ticker_canonical import canonical_ticker
    t = canonical_ticker(ticker)
    return next((name for name, members in COHORTS.items() if t in members), None)


def _quote(symbol: str) -> Optional[float]:
    from src.agents.analysis.dcf_agent import _fmp_quote_symbol
    from src.tools.api import _STABLE, _fmp_get
    rows = _fmp_get(f"{_STABLE}/quote", {"symbol": _fmp_quote_symbol(symbol)}, api_key=None)
    p = (rows[0] or {}).get("price") if isinstance(rows, list) and rows else None
    return float(p) if isinstance(p, (int, float)) and p > 0 else None


def _ev_per_share(symbol: str, ccy: str) -> Optional[float]:
    from src.agents.industry import gemini_params as gp
    from src.data import industry_inputs as ii
    e = ii.accepted_entry(symbol, "embedded_value")
    if not e:
        return None
    return gp.embedded_value_to_engine(ii.canonical_data(e), ii._fx(ccy)).get("ev_per_share")


def cohort_pev(ticker: str, *, quote: Callable[[str], Optional[float]] = _quote,
               ev_ps: Callable[[str, str], Optional[float]] = _ev_per_share,
               listing_ccy: Optional[Callable[[str], str]] = None) -> Optional[dict]:
    """{'value', 'cohort', 'members_used', 'members_missing'} for the subject's cohort, or None when it has none.
    'value' is None when fewer than MIN_MEMBERS peers measure."""
    from src.tools.ticker_canonical import canonical_ticker
    name = cohort_of(ticker)
    if not name:
        return None
    if listing_ccy is None:
        from src.tools.api import get_listing_currency as listing_ccy
    subject = canonical_ticker(ticker)
    used, missing = [], []
    for m in COHORTS[name]:
        if m == subject:
            continue
        try:
            ccy = listing_ccy(m)
            px, ev = quote(m), ev_ps(m, ccy)
        except Exception:                                  # noqa: BLE001
            px, ev, ccy = None, None, None
        if px and ev and ev > 0:
            used.append({"symbol": m, "price": round(px, 4), "ev_per_share": round(ev, 4), "currency": ccy,
                         "p_ev": round(px / ev, 4)})
        else:
            missing.append(m)
    value = statistics.median(u["p_ev"] for u in used) if len(used) >= MIN_MEMBERS else None
    return {"value": round(value, 4) if value is not None else None, "cohort": name,
            "members_used": used, "members_missing": missing}


def _pb(symbol: str) -> Optional[float]:
    from src.agents.analysis.dcf_agent import _fmp_quote_symbol
    from src.tools.api import _STABLE, _fmp_get
    rows = _fmp_get(f"{_STABLE}/ratios-ttm", {"symbol": _fmp_quote_symbol(symbol)}, api_key=None)
    v = (rows[0] or {}).get("priceToBookRatioTTM") if isinstance(rows, list) and rows else None
    if not (isinstance(v, (int, float)) and 0.05 < v < 20):
        return None
    # FMP's ratio divides by TOTAL equity (incl. minorities); the subject's book is the parent's, so the peer's is too.
    bs = _fmp_get(f"{_STABLE}/balance-sheet-statement", {"symbol": _fmp_quote_symbol(symbol), "limit": 1}, api_key=None)
    b = bs[0] if isinstance(bs, list) and bs else {}
    se, mi = b.get("totalStockholdersEquity"), b.get("minorityInterest")
    if isinstance(se, (int, float)) and se > 0 and isinstance(mi, (int, float)) and mi > 0:
        v = float(v) * (float(se) + float(mi)) / float(se)
    return float(v)


def cohort_pb(ticker: str, *, pb: Callable[[str], Optional[float]] = _pb) -> Optional[dict]:
    """The same cohort's median P/B for the P/BV leg (owner, 2026-11-25): a China domestic life insurer is not priced
    on a Hong Kong life median that holds AIA at 2.4x. None when the subject has no cohort."""
    from src.tools.ticker_canonical import canonical_ticker
    name = cohort_of(ticker)
    if not name:
        return None
    subject = canonical_ticker(ticker)
    used, missing = [], []
    for m in COHORTS[name]:
        if m == subject:
            continue
        try:
            v = pb(m)
        except Exception:                                  # noqa: BLE001
            v = None
        (used.append({"symbol": m, "pb": round(v, 4)}) if v else missing.append(m))
    value = statistics.median(u["pb"] for u in used) if len(used) >= MIN_MEMBERS else None
    return {"value": round(value, 4) if value is not None else None, "cohort": name,
            "members_used": used, "members_missing": missing}

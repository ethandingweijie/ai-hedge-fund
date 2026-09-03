"""One entry point for segment maps, whichever market the ticker lists on.

The SOTP bridge should not know or care where a segment map came from. It asks
here; this routes to the market's parser and applies the shared rules in
`segment_normalize` to whatever comes back.

    US / ADR      -> src.tools.sec_segments      (SEC rendered XBRL)
    HKEX          -> src.tools.hkex_segments     (HKEXnews annual-report PDF)
    SGX           -> src.tools.sgx_segments      (not yet implemented)

Adding a market means writing ONE parser that returns the provider contract in
`segment_normalize`, and registering it below. The judgement about what makes a
valid segment map -- business line vs geography, hierarchy collapse,
reconciliation to group revenue -- is shared, so SGX cannot quietly acquire
different rules from HKEX.

Cross-listings are tried in order. A Hong Kong line with a US ADR has TWO
routes: 9988.HK is covered by Alibaba's 20-F as well as by its own HKEXnews
filing. SEC is preferred where it exists because rendered XBRL parses more
reliably than a PDF, and the HK filing is the fallback.
"""
from __future__ import annotations

from typing import Callable, Optional

# Why a provider declined, for diagnostics. Never raised -- a missing segment
# map is an ordinary outcome, not an error.
_LAST_REASON: dict[str, str] = {}


def _sec_provider(ticker: str, end_date: str) -> Optional[dict]:
    from src.tools.sec_segments import get_segment_footnote
    return get_segment_footnote(ticker, end_date)


def _hkex_provider(ticker: str, end_date: str) -> Optional[dict]:
    from src.tools.hkex_segments import get_segment_footnote
    return get_segment_footnote(ticker, end_date)


def _sgx_provider(ticker: str, end_date: str) -> Optional[dict]:
    try:
        from src.tools.sgx_segments import get_segment_footnote
    except ImportError:
        return None
    return get_segment_footnote(ticker, end_date)


def _routes(ticker: str) -> list[tuple[str, Callable]]:
    """Providers to try, best first."""
    t = (ticker or "").strip().upper()
    try:
        from src.tools.hk.ticker import is_hk_ticker
        from src.tools.sg.ticker import is_sg_ticker
    except Exception:                                  # noqa: BLE001
        return [("sec", _sec_provider)]

    if is_hk_ticker(t):
        # An HK line with a US ADR has both routes. Prefer SEC: rendered XBRL
        # carries explicit dimensions and parses far more reliably than a
        # 3-14MB PDF whose columns have to be recovered from geometry.
        from src.tools.sec_segments import _ADR_FILER_ALIAS
        from src.tools.ticker_canonical import canonical_ticker
        # The alias map records KNOWN-ABSENT filers as None -- Tencent, Xiaomi
        # and Meituan are all listed with an unsponsored ADR that files
        # nothing. Testing key presence rather than the value sent all three
        # on a pointless SEC round-trip before falling back.
        filer = None
        for key in (canonical_ticker(t), t):
            if key in _ADR_FILER_ALIAS:
                filer = _ADR_FILER_ALIAS[key]
                break
        return ([("sec", _sec_provider), ("hkex", _hkex_provider)] if filer
                else [("hkex", _hkex_provider)])
    if is_sg_ticker(t):
        return [("sgx", _sgx_provider)]
    return [("sec", _sec_provider)]


# Canonical key -> the aliases a provider may already use. The SEC parser
# grew its own names before there was a second market, and 09988.HK routing
# through it raised a bare KeyError on `reported_currency`. Conforming here
# rather than renaming inside the parsers means neither market's own callers
# break, and a THIRD market only has to satisfy this table.
_ALIASES = {
    "reported_currency": ("reporting_currency", "currency"),
    "profit_label": ("profit_metric",),
    "profit_is_operating_income": ("profit_is_gaap_operating_income",),
}
_REQUIRED = (
    "segments", "consolidated_revenue", "segment_sum_revenue",
    "sum_vs_consolidated", "n_segments", "profit_label",
    "profit_is_operating_income", "profit_disclosed", "assets_disclosed",
    "reported_currency", "period_end", "segment_axis", "source_url", "form",
)


def _conform(out: dict, provider: str) -> dict:
    """Make one provider's output satisfy the shared contract.

    Aliases are COPIED, not renamed, so a provider's existing consumers keep
    working while the bridge sees one shape whatever market it asked about.
    """
    for canon, aliases in _ALIASES.items():
        if out.get(canon) is None:
            for a in aliases:
                if out.get(a) is not None:
                    out[canon] = out[a]
                    break
        # Back-fill the other way too. The bridge still reads the SEC parser's
        # original names (`profit_metric`), so a market that only sets the
        # canonical name would silently lose its profit label -- Tencent's
        # "Gross profit" would reach the report as None, which is exactly the
        # disclosure that must not go missing.
        if out.get(canon) is not None:
            for a in aliases:
                if out.get(a) is None:
                    out[a] = out[canon]
    for key in _REQUIRED:
        out.setdefault(key, None)
    out.setdefault("warnings", [])
    out["provider"] = provider
    if out.get("segments") is not None:
        out["n_segments"] = len(out["segments"])
        out["profit_disclosed"] = any(
            s.get("profit") is not None for s in out["segments"])
        out["assets_disclosed"] = any(
            s.get("assets") is not None for s in out["segments"])
    return out


def get_segment_footnote(ticker: str, end_date: str) -> Optional[dict]:
    """Segment map for `ticker`, from whichever market source has one."""
    reasons = []
    for name, provider in _routes(ticker):
        try:
            out = provider(ticker, end_date)
        except Exception as exc:                       # noqa: BLE001
            reasons.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if out:
            return _conform(out, name)
        reasons.append(f"{name}: no segment map")
    _LAST_REASON[ticker] = "; ".join(reasons) or "no route"
    return None


def last_reason(ticker: str) -> str:
    return _LAST_REASON.get(ticker, "")


def available_markets() -> dict[str, bool]:
    """Which markets actually have a working parser right now."""
    out = {"sec": True, "hkex": False, "sgx": False}
    for key, mod in (("hkex", "src.tools.hkex_segments"),
                     ("sgx", "src.tools.sgx_segments")):
        try:
            __import__(mod)
            out[key] = True
        except Exception:                              # noqa: BLE001
            pass
    return out

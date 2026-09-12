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

from src.tools.segment_normalize import normalize_segment_profit

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


def _group_operating_profit(ticker: str, end_date: str) -> Optional[float]:
    """Group operating profit in the REPORTING currency, from FMP.

    FMP carries this for all three markets -- verified live for 0700.HK,
    9988.HK and D05.SI -- which is what lets one rule serve every provider
    instead of each market parsing its own income statement.
    """
    try:
        import os

        from src.tools.api import search_line_items
        from src.tools.fmp_transcripts import to_fmp_symbol
        rows = search_line_items(
            to_fmp_symbol(ticker), ["operating_income"], end_date,
            period="annual", limit=1,
            api_key=os.getenv("FINANCIAL_DATASETS_API_KEY"))
    except Exception:                                  # noqa: BLE001
        return None
    if not rows:
        return None
    val = getattr(rows[0], "operating_income", None)
    return float(val) if isinstance(val, (int, float)) else None


def _normalize_profit(out: dict, ticker: str, end_date: str) -> dict:
    """Put every market's segment profit on an operating-profit basis.

    Runs here rather than inside a provider so the markets cannot drift: the
    HK path was converting gross profit to EBIT while the SEC path passed
    Alibaba's ADJUSTED EBITA straight through, which is the same overstatement
    the HK conversion exists to prevent.
    """
    if out.get("profit_is_operating_income"):
        return out
    segs = out.get("segments") or []
    if not any(s.get("profit") is not None for s in segs):
        return out
    # FMP first -- it covers all three markets. A provider that read the figure
    # out of the filing itself is the fallback, which matters for years or
    # listings FMP does not carry.
    group_op = (_group_operating_profit(ticker, end_date)
                or out.get("group_operating_profit"))
    detail = normalize_segment_profit(
        segs, group_op, reported_label=out.get("profit_label"))
    if detail is None:
        out.setdefault("warnings", []).append(
            f"segment measure is {out.get('profit_label') or 'unknown'} and no "
            f"group operating profit was available -- profit left as reported, "
            f"NOT operating income")
        out["profit_basis"] = "reported_not_operating_income"
        return out
    out["reported_profit_label"] = out.get("profit_label")
    out["profit_label"] = (f"Operating profit (derived from "
                           f"{out.get('profit_label')})")
    out["profit_metric"] = out["profit_label"]
    out["profit_is_operating_income"] = True
    out["profit_is_gaap_operating_income"] = True
    out["profit_basis"] = "derived_from_reported_less_central"
    out["profit_normalisation"] = detail
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
            return _normalize_profit(_conform(out, name),
                                     ticker, end_date)
        # A provider may know WHY, and "this filer has one operating
        # segment" is a different answer from "the parser failed".
        detail = ""
        try:
            mod = provider.__globals__.get("__name__")
            import importlib
            m = importlib.import_module(
                {"hkex": "src.tools.hkex_segments",
                 "sec": "src.tools.sec_segments",
                 "sgx": "src.tools.sgx_segments"}[name])
            fn = getattr(m, "last_reason", None)
            detail = fn(ticker) if fn else ""
        except Exception:                          # noqa: BLE001
            detail = ""
        reasons.append(f"{name}: {detail or 'no segment map'}")
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

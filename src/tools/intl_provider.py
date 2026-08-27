"""
src/tools/intl_provider.py
==========================
Provider selection for the non-US markets (HK, SG).

Background
----------
HK fundamentals came from AKShare (Chinese-column scraping of Eastmoney) and
SG from a single yfinance `.info` snapshot with no statement history. The FMP
subscription now carries global coverage, verified 2026-08-27:

    0700.HK / D05.SI  full annual + quarterly statements, TTM key-metrics and
                      ratios, split-adjusted EOD back to 2000, analyst
                      estimates, transcripts.
    price-target-consensus is the one gap — empty for both, so that route
    keeps returning None.

Policy is FMP-primary with automatic fallback to the legacy provider, behind
a per-market env kill-switch:

    HK_DATA_PROVIDER = fmp (default) | legacy
    SG_DATA_PROVIDER = fmp (default) | legacy

Nothing that works today can regress: any empty/failed FMP response falls
through to the path that served the request before.

How the dispatch works
----------------------
Every public function in src/tools/api.py has the same shape — an HK branch,
an SG branch, then the US/FMP body that uses the ticker directly as the API
symbol. Rather than duplicating eleven FMP bodies, `try_fmp` re-enters the
same function with the FMP symbol under a thread-scoped "force" flag; the
HK/SG branches skip themselves while that flag is set, so the request lands
on the existing US/FMP body. The flag is scoped to one call and restored on
the way out, including on exceptions.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

Market = str  # "hk" | "sg"

PROVIDER_ENV: dict[Market, str] = {"hk": "HK_DATA_PROVIDER", "sg": "SG_DATA_PROVIDER"}
_DEFAULT_PROVIDER = "fmp"

_local = threading.local()


# ── Thread-scoped force flag ────────────────────────────────────────────────

def fmp_forced() -> bool:
    """True while a re-entrant FMP call is in flight on this thread.

    The HK/SG branches in api.py consult this so they step aside and let the
    US/FMP body handle the already-normalised symbol.
    """
    return getattr(_local, "force_fmp", False)


@contextmanager
def _forced():
    prev = getattr(_local, "force_fmp", False)
    _local.force_fmp = True
    try:
        yield
    finally:
        _local.force_fmp = prev


# ── Configuration ───────────────────────────────────────────────────────────

def provider_for(market: Market) -> str:
    """Configured provider for a market: "fmp" (default) or "legacy".

    Read through run_config.getenv so a per-run overlay can flip it, matching
    how the FMP key itself is resolved in api.py.
    """
    env_name = PROVIDER_ENV.get(market)
    if not env_name:
        return _DEFAULT_PROVIDER
    raw = None
    try:
        from src.utils.run_config import getenv as _getenv
        raw = _getenv(env_name)
    except Exception:
        raw = os.getenv(env_name)
    return (raw or _DEFAULT_PROVIDER).strip().lower()


def use_fmp(market: Market) -> bool:
    return provider_for(market) == "fmp"


# ── Symbol normalisation ────────────────────────────────────────────────────

def fmp_symbol(ticker: str, market: Market) -> Optional[str]:
    """Ticker in the symbol form FMP's global endpoints accept.

    HK needs the 4-digit form — FMP rejects the repo's 5-digit canonical
    (00700.HK -> empty, 0700.HK -> Tencent). SG already matches.
    """
    try:
        if market == "hk":
            from src.tools.hk.ticker import to_fmp_code
            return to_fmp_code(ticker)
        if market == "sg":
            from src.tools.sg.ticker import to_fmp_code
            return to_fmp_code(ticker)
    except Exception:
        return None
    return None


def canonical_symbol(ticker: str, market: Market) -> Optional[str]:
    """The repo's canonical key form (HK: NNNNN.HK, SG: XXX.SI).

    FMP results are relabelled to this so downstream consumers keyed on the
    canonical ticker keep matching regardless of which provider served them.
    """
    try:
        if market == "hk":
            from src.tools.hk.ticker import to_canonical
            return to_canonical(ticker)
        if market == "sg":
            from src.tools.sg.ticker import to_canonical
            return to_canonical(ticker)
    except Exception:
        return None
    return None


def detect_market(ticker: str) -> Optional[Market]:
    """"hk", "sg", or None for US.

    Order matters: HK codes are purely numeric, so HK must be tested first —
    the same ordering documented in src/tools/sg/ticker.py.
    """
    if not ticker:
        return None
    try:
        from src.tools.hk.ticker import is_hk_ticker
        if is_hk_ticker(ticker):
            return "hk"
    except Exception:
        pass
    try:
        from src.tools.sg.ticker import is_sg_ticker
        if is_sg_ticker(ticker):
            return "sg"
    except Exception:
        pass
    return None


# ── Result relabelling ──────────────────────────────────────────────────────

def relabel_ticker(result: Any, canonical: Optional[str]) -> Any:
    """Rewrite the `ticker` attribute/key on FMP rows to the canonical form.

    The FMP body stamps whatever symbol it was called with (0700.HK) onto
    FinancialMetrics.ticker / LineItem.ticker. Downstream code keys on the
    canonical (00700.HK), so normalise before returning. Rows without a
    ticker field (Price) pass through untouched.
    """
    if not canonical or result is None:
        return result
    rows = result if isinstance(result, list) else [result]
    for row in rows:
        try:
            if isinstance(row, dict):
                if "ticker" in row:
                    row["ticker"] = canonical
            elif hasattr(row, "ticker"):
                object.__setattr__(row, "ticker", canonical)
        except Exception:
            continue
    return result


# ── Dispatch ────────────────────────────────────────────────────────────────

def _non_empty(result: Any) -> bool:
    """Default validity check: a populated list, or any truthy scalar."""
    if result is None:
        return False
    if isinstance(result, (list, tuple, dict, str)):
        return len(result) > 0
    return True


def try_fmp(
    market: Market,
    ticker: str,
    call: Callable[[str], Any],
    *,
    validate: Callable[[Any], bool] = _non_empty,
    relabel: bool = True,
    what: str = "",
) -> Optional[Any]:
    """Serve a HK/SG request from FMP, or return None to fall back.

    `call` receives the FMP symbol and should re-enter the api.py function it
    was called from; the force flag makes that landing on the US/FMP body.

    Returns None — never raises — when the kill-switch selects legacy, the
    symbol cannot be mapped, FMP errors, or the response fails `validate`.
    The caller then runs its existing legacy body unchanged.
    """
    if not use_fmp(market):
        return None
    symbol = fmp_symbol(ticker, market)
    if not symbol:
        return None

    label = what or "data"
    try:
        with _forced():
            result = call(symbol)
    except Exception as exc:
        logger.warning(
            "[intl] %s %s (%s) provider=fmp failed (%s) — falling back to legacy",
            market.upper(), ticker, label, exc,
        )
        return None

    if not validate(result):
        logger.info(
            "[intl] %s %s (%s) provider=fmp returned nothing — falling back to legacy",
            market.upper(), ticker, label,
        )
        return None

    if relabel:
        relabel_ticker(result, canonical_symbol(ticker, market))
    logger.debug("[intl] %s %s (%s) provider=fmp symbol=%s", market.upper(),
                 ticker, label, symbol)
    return result

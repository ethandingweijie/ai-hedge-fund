"""HK lines that are the same company as a US ADR -- one table for the repo.

A dual-listed company has one set of accounts, one segment memory, one set of
SOTP inputs and one forward consensus. What differs by listing is only the
share count and currency, which the valuation engine applies last (per-share =
company value x USD->listing FX / that listing's shares). So everything that
describes the COMPANY resolves through `company_key`, and both listings get
the same answer expressed in their own units.

Keys are canonical 5-digit HK codes ("09988.HK"); callers may pass 4-digit.
`ads_ratio` is ordinary shares per ADS, for display and cross-checks only --
the engine never needs it because it divides by each listing's own shares.
"""
from __future__ import annotations

from typing import Optional

#: HK line -> (US ADR ticker, ordinary shares per ADS). Every ADR here files a
#: 20-F with the SEC.
DUAL_LISTINGS: dict[str, tuple[str, int]] = {
    "09988.HK": ("BABA", 8),    # Alibaba Group
    "09618.HK": ("JD", 2),      # JD.com
    "09888.HK": ("BIDU", 8),    # Baidu
    "09999.HK": ("NTES", 5),    # NetEase
    "09961.HK": ("TCOM", 1),    # Trip.com Group
    "02015.HK": ("LI", 2),      # Li Auto
    "09866.HK": ("NIO", 1),     # NIO
    "09868.HK": ("XPEV", 2),    # XPeng
}

#: HK names with an unsponsored ADR that files nothing with the SEC. Listed so
#: segment providers skip a pointless SEC round-trip (see segment_providers).
NO_SEC_FILER: frozenset[str] = frozenset({"00700.HK", "01810.HK", "03690.HK"})


def _canonical(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_canonical
        if is_hk_ticker(t):
            return to_canonical(t)
    except Exception:  # noqa: BLE001
        pass
    return t


def adr_for(ticker: str) -> Optional[str]:
    """The US ADR for an HK line, or None."""
    hit = DUAL_LISTINGS.get(_canonical(ticker))
    return hit[0] if hit else None


def hk_for(ticker: str) -> Optional[str]:
    """The HK line for a US ADR, or None."""
    t = (ticker or "").strip().upper()
    return next((hk for hk, (adr, _) in DUAL_LISTINGS.items() if adr == t), None)


def ads_ratio(ticker: str) -> Optional[int]:
    t = (ticker or "").strip().upper()
    hit = DUAL_LISTINGS.get(_canonical(t)) or next(
        (v for v in DUAL_LISTINGS.values() if v[0] == t), None)
    return hit[1] if hit else None


def company_key(ticker: str) -> str:
    """One key per company: the ADR for a dual listing, else the canonical ticker."""
    return adr_for(ticker) or _canonical(ticker)


def listings_for(ticker: str) -> list[str]:
    """Every listing of the company, ADR first."""
    key = company_key(ticker)
    hk = hk_for(key)
    return [key, hk] if hk else [key]


def sec_filer_alias() -> dict[str, Optional[str]]:
    """HK line -> SEC filer (None = known to have none), for sec_segments."""
    alias: dict[str, Optional[str]] = {hk: adr for hk, (adr, _) in DUAL_LISTINGS.items()}
    alias.update({hk: None for hk in NO_SEC_FILER})
    return alias

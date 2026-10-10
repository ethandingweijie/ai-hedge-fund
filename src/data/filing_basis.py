"""Which accounting basis a company files on -- the one question the lease treatment turns on.

A US GAAP filer's operating-lease cost sits inside EBITDA and operating cash flow, so its lease
liabilities are not debt to an EV built on those figures; an IFRS 16 filer's lease cost is below
EBITDA and its principal repayments are financing flows, so they are (owner, 2026-10-04, plan 1C.2).

Owner, 2026-10-10 (cross-sector audit E2): the basis comes from this explicit registry, never from
the reporting currency alone. Reporting in USD used to stand for US GAAP, which stripped the leases
of every IFRS filer that reports in dollars (BHP, Rio, Shell, BP, AstraZeneca, Novartis, HSBC, AIA,
Jardine, Hongkong Land, Wilmar) while their EBITDA excludes rent.

The same answer serves the subject's net debt (dcf_agent._valuation_net_debt) and the peers' EV in
the comps store (regional_comps, audit E1), so both sides of every EV multiple sit on one basis.
"""
from __future__ import annotations

from typing import Optional

#: Non-USD reporters that file under US GAAP: the Chinese issuers report in RMB under US GAAP (both
#: listings named).
US_GAAP_NON_USD_REPORTERS = frozenset({
    "BABA", "09988.HK", "9988.HK", "JD", "09618.HK", "9618.HK", "PDD", "BIDU", "09888.HK", "9888.HK",
    "NTES", "09999.HK", "9999.HK", "TCOM", "09961.HK", "9961.HK", "BILI", "09626.HK", "9626.HK",
    "ZTO", "02057.HK", "2057.HK", "YUMC", "09987.HK", "9987.HK", "LI", "02015.HK", "2015.HK",
    "NIO", "09866.HK", "9866.HK", "XPEV", "09868.HK", "9868.HK", "BEKE", "02423.HK", "2423.HK",
    "TME", "01698.HK", "1698.HK", "WB", "09898.HK", "9898.HK", "VIPS", "HTHT", "01179.HK", "1179.HK",
    "BZ", "02076.HK", "2076.HK", "TAL", "EDU", "09901.HK", "9901.HK", "QFIN", "03660.HK", "3660.HK",
})

#: USD reporters that file under IFRS (or an IFRS-identical framework: SFRS(I), HKFRS). US-listed ADRs
#: and the HK / SG dollar reporters. Extend as names are met; anything not here that reports in USD is
#: treated as US GAAP.
IFRS_USD_REPORTERS = frozenset({
    # Resources and energy
    "BHP", "RIO", "SHEL", "BP", "TTE", "EQNR", "VALE", "PBR", "PBR-A", "GOLD", "B", "AU", "GFI", "SBSW",
    "TECK", "WPM", "FNV", "SCCO",
    # Pharma, consumer, industrial ADRs reporting in USD
    "AZN", "NVS", "CCEP", "ICLR", "STLA",
    # Hong Kong dollar reporters
    "00005.HK", "0005.HK", "02888.HK", "2888.HK", "01299.HK", "1299.HK", "02378.HK", "2378.HK",
    "01910.HK", "1910.HK",
    # Singapore dollar reporters
    "J36.SI", "H78.SI", "D01.SI", "F34.SI", "E5H.SI", "C07.SI",
})


def _canon(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def reports_us_gaap(ticker: str, reported_currency: Optional[str]) -> bool:
    """True when the company files under US GAAP: a named non-USD US GAAP filer, or a USD reporter
    that is not a named IFRS filer."""
    t = _canon(ticker)
    if t in US_GAAP_NON_USD_REPORTERS:
        return True
    return str(reported_currency or "").upper() == "USD" and t not in IFRS_USD_REPORTERS


def basis(ticker: str, reported_currency: Optional[str]) -> str:
    return "US GAAP" if reports_us_gaap(ticker, reported_currency) else "IFRS"

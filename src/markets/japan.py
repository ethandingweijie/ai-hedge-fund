"""Japan (Tokyo Stock Exchange, ".T") -- the rules that only make sense for this market.

Shared engine files carry a one-line branch into this module; everything Japan-specific lives here so the China and
Korea branches can merge beside it. See docs/markets/JAPAN.md for what was verified and what is still PROPOSED.

Tickers
-------
Canonical stored form is the FMP / Yahoo form: the 4-character TSE securities code plus ".T" (``7203.T``). Since
January 2024 new codes may carry letters (``130A.T``, ``285A.T``); positions 2 and 4 can be letters, positions 1
and 3 stay digits. Accepted input forms: ``7203.T``, ``7203.JP``, ``7203.TYO``, ``TYO:7203``, ``TSE:7203``,
``JP:7203``. A BARE number (``7203``) is NOT Japanese here: bare numerics have always meant Hong Kong in this product
(``2888`` -> ``02888.HK``), and that contract is kept. TDnet and EDINET write the code with a trailing check digit
``0`` (``72030``); ``from_local_code`` reads that form, user input never does (five digits are an HK code).

Fiscal year
-----------
Most Japanese companies close on 31 March. The engine labels a fiscal year by the calendar year it ENDS in
(FMP's ``fiscalYear``: the year ended 2026-03-31 is FY2026), which is Toyota's own convention but NOT MUFG's or
Sony's: they call the same year "FY2025" (the year it STARTS). ``research_context`` tells the research model to
convert, and reports carry ``fy_disclosure`` so a reader can map "FY2026" to "2026年3月期".
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

MARKET = "JP"                 # the short market code used by capm / valuation_constants.risk_free_by_market
EXCHANGE = "JPX"              # regional_comps / MARKET_REGISTRY key (FMP's exchange code)
SUFFIX = ".T"
CURRENCY = "JPY"
#: Display label. "¥" alone is ambiguous with the renminbi (both are U+00A5), so Japan is always "JP¥".
CURRENCY_LABEL = "JP¥"

#: Effective statutory corporate tax rate for a large company (national corporate tax 23.2% plus local corporate,
#: inhabitants' and enterprise taxes, net of the enterprise-tax deduction): 29.74% as published by the Ministry of
#: Finance from FY2018 (Tokyo-based companies quote ~30.6% because of the metropolitan surcharge). Used ONLY as the
#: fallback when a company's own filed effective rate cannot be measured -- the role the US 21% / SG 17% play.
STATUTORY_TAX = 0.2974
STATUTORY_TAX_SOURCE = "Japan effective statutory corporate tax rate 29.74% (MOF, large companies, FY2018 onward)"

_PREFIXES = ("TYO", "TSE", "JPX", "JP", "XTKS", "TKS")
_SUFFIXES = ("T", "JP", "TYO", "TSE", "JPX", "XTKS")
#: TSE securities code: digit, digit-or-letter, digit, digit-or-letter (130A, 285A, 7203, 9984).
_CODE = r"[1-9][0-9A-Z][0-9][0-9A-Z]"
_CODE_RE = re.compile(rf"^{_CODE}$")
_SUFFIX_RE = re.compile(rf"^({_CODE})\.(?:{'|'.join(_SUFFIXES)})$")
_PREFIX_RE = re.compile(rf"^(?:{'|'.join(_PREFIXES)}):\s*({_CODE})$")


def _code(ticker: Optional[str]) -> Optional[str]:
    t = (ticker or "").strip().upper()
    if not t:
        return None
    m = _SUFFIX_RE.match(t) or _PREFIX_RE.match(t)
    return m.group(1) if m else None


def is_jp_ticker(ticker: Optional[str]) -> bool:
    """True for a Tokyo listing in any accepted input form (never for a bare number -- that is Hong Kong)."""
    return _code(ticker) is not None


def to_canonical(ticker: Optional[str]) -> str:
    """``TYO:7203`` / ``7203.jp`` / ``7203.T`` -> ``7203.T``; anything else is returned stripped and uppercased."""
    c = _code(ticker)
    return f"{c}{SUFFIX}" if c else (ticker or "").strip().upper()


def to_fmp_code(ticker: Optional[str]) -> str:
    """FMP's symbol is the canonical form (verified 2026-11-28: 7203.T, 8306.T, 9984.T, 130A.T)."""
    return to_canonical(ticker)


def local_code(ticker: Optional[str]) -> Optional[str]:
    """The 4-character TSE code (``7203``), or None."""
    return _code(ticker)


def local_code5(ticker: Optional[str]) -> Optional[str]:
    """The 5-character code TDnet and EDINET print (``72030``: the TSE code plus a trailing 0)."""
    c = _code(ticker)
    return f"{c}0" if c else None


def from_local_code(code5: Optional[str]) -> Optional[str]:
    """``72030`` (TDnet / EDINET) -> ``7203.T``; None when it is not that shape."""
    s = (code5 or "").strip().upper()
    if len(s) == 5 and s.endswith("0") and _CODE_RE.match(s[:4]):
        return f"{s[:4]}{SUFFIX}"
    return None


# ── Fiscal year ────────────────────────────────────────────────────────────

def _as_date(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def fiscal_year_end_month(period_ends: list) -> Optional[int]:
    """The month the company's fiscal year ends in, from its annual period-end dates (mode); None if none parse."""
    months = [d.month for d in (_as_date(p) for p in period_ends or []) if d]
    if not months:
        return None
    return max(set(months), key=months.count)


def fy_label(period_end) -> Optional[str]:
    """Japanese-style label for an annual period: 2026-03-31 -> ``FY3/26`` (2026年3月期)."""
    d = _as_date(period_end)
    return f"FY{d.month}/{d.year % 100:02d}" if d else None


#: Printed in every Tokyo report's data limitations (report_disclosures.data_limitations).
FY_NAMING_NOTE = ("Fiscal years are named by the calendar year in which they end: for a company with a March "
                  "year-end, FY2026 is April 2025 to March 2026 (2026年3月期). Some Japanese issuers call that same "
                  "year FY2025, so compare labels with care.")


def fy_disclosure(period_end) -> Optional[str]:
    """One sentence a report prints so "FY2026" is unambiguous for a March year-end company."""
    d = _as_date(period_end)
    if not d:
        return None
    if d.month == 12:
        return f"FY{d.year} is the calendar year ended 31 December {d.year}."
    start = date(d.year - 1, d.month % 12 + 1, 1)
    return (f"FY{d.year} is the fiscal year {start:%B %Y} to {d:%B %Y} ({d.year}年{d.month}月期, "
            f"{fy_label(d)}); some Japanese issuers call this year FY{d.year - 1}.")


# ── Research ───────────────────────────────────────────────────────────────

#: Japanese-language primary sources, in the order a Tokyo desk reads them.
PRIMARY_SOURCES = (
    ("TDnet", "https://www.release.tdnet.info/inbs/I_main_00.html",
     "timely disclosure: 決算短信 (kessan tanshin, quarterly results + full-year company forecast), "
     "業績予想の修正 (forecast revisions), 中期経営計画 (medium-term plans)"),
    ("EDINET", "https://disclosure2.edinet-fsa.go.jp/",
     "statutory filings: 有価証券報告書 (annual securities report), 半期報告書 (half-year report)"),
    ("JPX listed company search", "https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do",
     "corporate governance reports, listing data"),
)


def research_context(ticker: str, company_name: str = "") -> str:
    """Block appended to the deep-research prompt for a Tokyo listing. Empty for any other ticker."""
    if not is_jp_ticker(ticker):
        return ""
    code = local_code(ticker)
    who = f"{company_name} ({code}.T)" if company_name else f"{code}.T"
    srcs = "\n".join(f"  - {n} ({u}): {d}" for n, u, d in PRIMARY_SOURCES)
    return (
        f"\nJAPAN MARKET CONTEXT -- {who}, Tokyo Stock Exchange, securities code {code}:\n"
        f"  * Search in Japanese as well as English: the company name in Japanese, the code {code}, and the terms "
        f"決算短信, 業績予想, 有価証券報告書, 中期経営計画, 統合報告書. English pages often lag the Japanese release by "
        f"days and omit the company forecast table.\n"
        f"  * Primary sources:\n{srcs}\n"
        f"  * Company guidance is the 通期業績予想 (full-year forecast) in the latest 決算短信; quote it with the "
        f"date of that release. It is a company forecast, not a broker consensus.\n"
        f"  * FISCAL YEAR: most Japanese companies close on 31 March. State every fiscal year by the calendar year "
        f"in which it ENDS (the year ending March 2027 is FY2027 = 2027年3月期 = FY3/27). Many issuers (MUFG, Sony, "
        f"SoftBank Group) name that same year 'FY2026' after the year it starts -- convert their labels before "
        f"reporting a figure, and say which convention the source used.\n"
        f"  * Accounting basis differs by company: J-GAAP (日本基準), IFRS (国際会計基準) or US GAAP (米国基準). The "
        f"basis is printed in the 決算短信 title (e.g. 〔IFRS〕). Never mix bases between years or against peers "
        f"without saying so; for banks, J-GAAP domestic figures differ materially from the US-GAAP 20-F.\n"
        f"  * Amounts: Japanese releases state 百万円 (millions of yen) or 億円 (100 million yen). Convert to yen "
        f"exactly; 1 兆円 = 1 trillion yen = 10,000 億円. Write yen amounts as JP¥ (e.g. JP¥53.5tn), never a bare "
        f"'¥', which also denotes the renminbi.\n"
        f"  * Balance sheets: many Japanese companies hold large cash balances and 政策保有株式 (cross-shareholdings, "
        f"'investment securities'). Report the amounts and whether management plans to sell them.\n"
    )


_BARE_YEN = re.compile(r"(?<![A-Za-z])¥(?=\s?[\d.])")


def normalize_yen(text):
    """``¥3,384`` -> ``JP¥3,384`` in text known to be about a Tokyo listing (a bare ¥ is also the renminbi sign).
    ``JP¥`` / ``CN¥`` and a ¥ not followed by a number are left alone. Non-strings pass through."""
    if not isinstance(text, str) or "¥" not in text:
        return text
    return _BARE_YEN.sub("JP¥", text)


def search_hint(ticker: str) -> str:
    """A short suffix for web-search queries (freshness, news) on a Tokyo listing; '' otherwise."""
    if not is_jp_ticker(ticker):
        return ""
    return (f" Tokyo Stock Exchange code {local_code(ticker)}; include Japanese-language sources "
            f"(TDnet 決算短信, 業績予想の修正, Nikkei).")


# ── Primary filings available in English ───────────────────────────────────

#: Tokyo listings that file a Form 20-F with the SEC under their US ADR symbol. The 20-F is an English primary
#: source (business, risk factors, segments) -- but its accounting basis can differ from the Japanese filing that
#: FMP's statements follow, so a figure is cited from it only when the bases match. "basis_20f" was taken from the
#: issuers' 20-F cover statements as known at 2026-11; verify before treating a figure as reconciled.
ADR_20F: dict[str, dict] = {
    "7203.T": {"adr": "TM", "basis_20f": "IFRS", "basis_jp": "IFRS"},
    "6758.T": {"adr": "SONY", "basis_20f": "IFRS", "basis_jp": "IFRS"},
    "7267.T": {"adr": "HMC", "basis_20f": "IFRS", "basis_jp": "IFRS"},
    "4502.T": {"adr": "TAK", "basis_20f": "IFRS", "basis_jp": "IFRS"},
    "8306.T": {"adr": "MUFG", "basis_20f": "US GAAP", "basis_jp": "J-GAAP"},
    "8316.T": {"adr": "SMFG", "basis_20f": "IFRS", "basis_jp": "J-GAAP"},
    "8411.T": {"adr": "MFG", "basis_20f": "US GAAP", "basis_jp": "J-GAAP"},
    "8604.T": {"adr": "NMR", "basis_20f": "US GAAP", "basis_jp": "US GAAP"},
    "8591.T": {"adr": "IX", "basis_20f": "US GAAP", "basis_jp": "US GAAP"},
}


def adr_filer(ticker: str) -> Optional[dict]:
    """The SEC 20-F filer behind a Tokyo listing, or None."""
    return ADR_20F.get(to_canonical(ticker))

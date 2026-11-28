"""Korea (KRX: KOSPI ``.KS`` and KOSDAQ ``.KQ``) -- ticker forms, listing rules and PROPOSED constants.

Ticker forms
------------
The canonical stored form is the 6-character KRX short code plus the venue suffix: ``005930.KS`` (Samsung
Electronics, KOSPI), ``247540.KQ`` (EcoPro BM, KOSDAQ). FMP quotes the same symbols, so the canonical form IS the
FMP symbol (no 4-digit style rewrite as for HK).

Accepted on input and canonicalised:

* ``005930.KS`` / ``005930.KQ``  -- as is (upper-cased); a short code is zero-padded to 6 (``5930.KS``)
* ``KRX:005930``, ``KOSPI:005930``, ``KS:005930`` -> ``.KS``; ``KOSDAQ:247540``, ``KQ:247540`` -> ``.KQ``.
  ``KRX:`` covers both boards; a code this module knows to be on KOSDAQ takes ``.KQ``, anything else ``.KS``.
* a BARE 6-digit code (``005930``) is NOT accepted: China A-shares are also 6 digits (000001.SZ, 600519.SS) and
  ``000660`` is SK hynix on KRX and a Shenzhen code too. A bare code is left untouched by this module; the landing
  search returns both listings and the user picks one. ``KNOWN_LISTINGS`` only decides the BOARD of a ``KRX:`` code.

Preferred shares are separate listings with their own price: ``005935.KS`` is Samsung Electronics' preferred line,
``005385.KS`` Hyundai Motor's. On KRX a common share's code ends in ``0``; a preferred line ends in ``5`` (first
preferred), ``7``/``9`` (second/third) or ``K`` (new-type preferred). The issuer's statements are the same for both
lines (FMP returns identical statements and an issuer-level market cap for each).

Constants
---------
Nothing in this module is applied silently. The risk-free rate, minimum spread and rating hurdle are PROPOSED in
``src/data/valuation_constants.json`` (``risk_free_by_market.proposed.KR``) and are ignored by the engine until the
owner moves them to ``markets``. The sector pins below are PROPOSED; ``coverage.is_tuned`` does not count them.
See docs/markets/KOREA.md for every derivation.
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = [
    "KR_SUFFIXES", "KNOWN_LISTINGS", "PROPOSED_PROFILE_PINS", "PROPOSED_STATUTORY_TAX",
    "is_korea_ticker", "to_canonical", "is_preferred", "common_of", "board_of",
    "capm_market", "listing_currency", "research_context", "preferred_note",
]

KR_SUFFIXES = (".KS", ".KQ")

#: Prefix forms users paste from Google Finance / Bloomberg-style quotes.
_PREFIX_SUFFIX = {"KRX": None, "KOSPI": ".KS", "KS": ".KS", "KSE": ".KS", "KOSDAQ": ".KQ", "KQ": ".KQ"}

#: A KRX short code: up to six characters, digits with an optional trailing K (new-type preferred) and the newer
#: alphanumeric codes KRX has issued since 2024 (letters in positions 4-6); zero-padded to six.
_SUFFIXED = re.compile(r"^([0-9][0-9A-Z]{0,5})\.(KS|KQ)$")
_PREFIXED = re.compile(r"^(KRX|KOSPI|KSE|KS|KOSDAQ|KQ):([0-9][0-9A-Z]{0,5})$")

#: code -> (board suffix, English name): the large caps and the preferred lines that trade at size. Used to put a
#: ``KRX:`` code (either board) or a mis-boarded suffix on its board. It never makes a BARE code Korean.
KNOWN_LISTINGS: dict[str, tuple[str, str]] = {
    "005930": (".KS", "Samsung Electronics"),
    "005935": (".KS", "Samsung Electronics (preferred)"),
    "000660": (".KS", "SK hynix"),
    "373220": (".KS", "LG Energy Solution"),
    "207940": (".KS", "Samsung Biologics"),
    "005380": (".KS", "Hyundai Motor"),
    "005385": (".KS", "Hyundai Motor (preferred)"),
    "000270": (".KS", "Kia"),
    "068270": (".KS", "Celltrion"),
    "035420": (".KS", "NAVER"),
    "035720": (".KS", "Kakao"),
    "105560": (".KS", "KB Financial Group"),
    "055550": (".KS", "Shinhan Financial Group"),
    "086790": (".KS", "Hana Financial Group"),
    "316140": (".KS", "Woori Financial Group"),
    "138040": (".KS", "Meritz Financial Group"),
    "323410": (".KS", "KakaoBank"),
    "032830": (".KS", "Samsung Life Insurance"),
    "000810": (".KS", "Samsung Fire & Marine Insurance"),
    "005490": (".KS", "POSCO Holdings"),
    "012330": (".KS", "Hyundai Mobis"),
    "028260": (".KS", "Samsung C&T"),
    "006400": (".KS", "Samsung SDI"),
    "051910": (".KS", "LG Chem"),
    "051915": (".KS", "LG Chem (preferred)"),
    "066570": (".KS", "LG Electronics"),
    "003550": (".KS", "LG Corp"),
    "034730": (".KS", "SK Inc"),
    "402340": (".KS", "SK Square"),
    "096770": (".KS", "SK Innovation"),
    "017670": (".KS", "SK Telecom"),
    "030200": (".KS", "KT"),
    "033780": (".KS", "KT&G"),
    "015760": (".KS", "Korea Electric Power (KEPCO)"),
    "012450": (".KS", "Hanwha Aerospace"),
    "329180": (".KS", "HD Hyundai Heavy Industries"),
    "009540": (".KS", "HD Korea Shipbuilding & Offshore Engineering"),
    "042660": (".KS", "Hanwha Ocean"),
    "010140": (".KS", "Samsung Heavy Industries"),
    "034020": (".KS", "Doosan Enerbility"),
    "010130": (".KS", "Korea Zinc"),
    "011200": (".KS", "HMM"),
    "259960": (".KS", "Krafton"),
    "018260": (".KS", "Samsung SDS"),
    "009150": (".KS", "Samsung Electro-Mechanics"),
    "047810": (".KS", "Korea Aerospace Industries"),
    "247540": (".KQ", "EcoPro BM"),
    "086520": (".KQ", "EcoPro"),
    "196170": (".KQ", "Alteogen"),
    "028300": (".KQ", "HLB"),
}

#: PROPOSED sector pins (owner decision; NOT applied). Accepting a pin = moving it into
#: industry_profile_map.json ``ticker_overrides`` (which is also what makes the name TUNED in coverage.py).
#: Each is (sector, profile, why) and every profile must exist in INDUSTRY_VALUATION_PROFILES (tested).
#: Only names whose FMP industry label routes them to the WRONG archetype, or to nothing, are listed.
PROPOSED_PROFILE_PINS: dict[str, tuple[str, str, str]] = {
    "005930.KS": ("Semiconductor", "Memory / DRAM-NAND",
                  "FMP labels it 'Consumer Electronics'; memory (DS division) is the swing earnings driver through "
                  "the cycle. Alternative for the owner: a segment SOTP (DS / MX / VD-DA / SDC / Harman)."),
    "005935.KS": ("Semiconductor", "Memory / DRAM-NAND", "preferred line of 005930.KS: same issuer, same pin"),
    "373220.KS": ("Industrials", "Battery & Energy Storage", "battery cell maker; FMP 'Electrical Equipment & Parts'"),
    "006400.KS": ("Industrials", "Battery & Energy Storage", "battery cell maker"),
    "247540.KQ": ("Industrials", "Battery & Energy Storage", "cathode materials for EV batteries"),
    "207940.KS": ("Biopharma", "CDMO / Life Science Tools",
                  "a profitable CDMO labelled 'Biotechnology' (the 02269.HK WuXi Biologics case)"),
    "068270.KS": ("Biopharma", "Specialty & Generic Pharma", "biosimilars maker; commercial, cash-generative"),
    "196170.KQ": ("Biopharma", "Commercial Biotech", "royalty/licence revenue on ALT-B4 (Keytruda SC); not pre-revenue"),
    "035420.KS": ("Tech", "Mature Platform", "search/commerce/fintech platform; FMP 'Internet Content & Information'"),
    "035720.KS": ("Tech", "Mature Platform", "messaging platform with listed subsidiaries (KakaoBank, KakaoPay)"),
    "259960.KS": ("Tech", "Media & Streaming", "game publisher (PUBG)"),
    "105560.KS": ("Financials", "Money Center Bank", "largest Korean bank group"),
    "055550.KS": ("Financials", "Money Center Bank", "national bank group"),
    "086790.KS": ("Financials", "Money Center Bank", "national bank group"),
    "316140.KS": ("Financials", "Money Center Bank", "national bank group"),
    "323410.KS": ("Financials", "Bank / Lending Institution", "digital-only bank"),
    "032830.KS": ("Financials", "Insurance", "life insurer: embedded value, also holds 8.5% of Samsung Electronics"),
    "000810.KS": ("Financials", "Insurance (P&C)", "general insurer"),
    "028260.KS": ("Financials", "Asian Holding Company (Look-Through)",
                  "de facto Samsung holdco: SEC, Samsung Biologics, Samsung Life stakes. Holdco discount must be set by "
                  "the owner (see docs/markets/KOREA.md: market-implied discount derivation)"),
    "003550.KS": ("Financials", "Asian Holding Company (Look-Through)", "LG group holding company"),
    "034730.KS": ("Financials", "Asian Holding Company (Look-Through)", "SK group holding company"),
    "402340.KS": ("Financials", "Asian Holding Company (Look-Through)", "intermediate holdco; ~20% of SK hynix"),
    "005490.KS": ("Materials", "Steel / Metals", "steel holdco (also lithium/battery materials)"),
    "010130.KS": ("Resources", "Base Metals", "zinc/lead smelter"),
    "011200.KS": ("Transportation", "Container & Bulk Shipping", "container line"),
    "329180.KS": ("Industrials", "Long-Cycle E&C", "shipbuilder: multi-year backlog, percentage-of-completion revenue"),
    "009540.KS": ("Industrials", "Long-Cycle E&C", "shipbuilding intermediate holdco"),
    "042660.KS": ("Industrials", "Long-Cycle E&C", "shipbuilder"),
    "010140.KS": ("Industrials", "Long-Cycle E&C", "shipbuilder"),
    "012450.KS": ("Industrials", "Defense Primes", "land systems, engines and space"),
    "047810.KS": ("Industrials", "Defense Primes", "military aircraft"),
    "015760.KS": ("Energy", "Regulated Utility", "state-controlled tariff utility"),
    "017670.KS": ("Telco", "Telecom Carrier", "mobile carrier"),
    "030200.KS": ("Telco", "Telecom Carrier", "integrated carrier"),
    "033780.KS": ("Consumer", "Tobacco", "tobacco and ginseng"),
    "018260.KS": ("ProfessionalServices", "IT Services", "Samsung group IT services and logistics"),
}

#: Korean statutory corporate tax, PROPOSED (not applied: _company_tax_rate uses the FILED effective rate first and
#: falls back to the US 21% for a market it has no row for). National top bracket 24% plus local income tax at 10% of
#: the national tax = 26.4%. The 2025 tax-law amendment restored a 25% top bracket from FY2026 (27.5% with local tax):
#: the owner should confirm which year applies before this is wired.
PROPOSED_STATUTORY_TAX = {"value": 0.264, "alternative_fy2026": 0.275,
                          "basis": "national 24% top bracket + 10% local income tax surcharge",
                          "status": "PROPOSED"}


def _norm(ticker: Optional[str]) -> str:
    return (ticker or "").strip().upper().replace(" ", "")


def to_canonical(ticker: Optional[str]) -> Optional[str]:
    """``NNNNNN.KS`` / ``NNNNNN.KQ`` for any Korean form, else None (not Korean, or a plain code we cannot place)."""
    t = _norm(ticker)
    if not t:
        return None
    m = _SUFFIXED.match(t)
    if m:
        code = m.group(1).zfill(6)
        # A known code is on the board the registry says (005930.KQ -> 005930.KS): FMP answers a wrong board with
        # nothing, which reads as "no data" rather than as a typo.
        known = KNOWN_LISTINGS.get(code)
        return code + (known[0] if known else "." + m.group(2))
    m = _PREFIXED.match(t)
    if m:
        code = m.group(2).zfill(6)
        suf = _PREFIX_SUFFIX[m.group(1)]
        if suf is None:                       # KRX: covers both boards
            suf = (KNOWN_LISTINGS.get(code) or (".KS", ""))[0]
        return code + suf
    # A BARE 6-digit code is never claimed (lead, 2026-11-28): the China branch maps bare 0xxxxx/3xxxxx/6xxxxx to
    # .SZ/.SS, and 000660 is both SK hynix and a Shenzhen code. The landing search lists both listings and the user
    # picks one; an explicit marker (.KS/.KQ, KRX:, KS:, KQ:, KOSPI:, KOSDAQ:) is required here.
    return None


def is_korea_ticker(ticker: Optional[str]) -> bool:
    """True for any form ``to_canonical`` resolves (suffixed, prefixed, or a known plain code)."""
    return to_canonical(ticker) is not None


def board_of(ticker: Optional[str]) -> Optional[str]:
    """'KOSPI' | 'KOSDAQ' | None."""
    c = to_canonical(ticker)
    if not c:
        return None
    return "KOSDAQ" if c.endswith(".KQ") else "KOSPI"


def is_preferred(ticker: Optional[str]) -> bool:
    """A preferred-share line: a KRX code whose last character is not 0 (5/7/9 = 1st/2nd/3rd, K = new-type)."""
    c = to_canonical(ticker)
    return bool(c) and c[5] != "0"


def common_of(ticker: Optional[str]) -> Optional[str]:
    """The common line of a preferred listing (005935.KS -> 005930.KS); a common line returns itself."""
    c = to_canonical(ticker)
    if not c:
        return None
    return c if c[5] == "0" else c[:5] + "0" + c[6:]


def capm_market(ticker: Optional[str]) -> Optional[str]:
    """The risk-free / CAPM market code for a Korean listing ('KR'), or None for any other ticker."""
    return "KR" if is_korea_ticker(ticker) else None


def listing_currency(ticker: Optional[str]) -> Optional[str]:
    """KRW for every KRX line (no USD-quoted counters on KRX, unlike SGX), else None."""
    return "KRW" if is_korea_ticker(ticker) else None


def issuer_price_for_shares(ticker: Optional[str], spot_price: Optional[float],
                            api_key: Optional[str] = None) -> Optional[float]:
    """For a PREFERRED line, the common line's price -- the price FMP's issuer-level marketCap is built on; None for
    any other ticker (the caller keeps its own price).

    FMP quotes the same marketCap on both Samsung lines (KRW 1,166tn = common price x common+preferred shares,
    5,709.6m + 802.4m = 6,512m). Divided by the PREFERRED price (145,000) it gives 8,043m shares, 24% too many, and
    every per-share value on the preferred line came out 8% low against the common (372k vs 404k, 2026-11-28)."""
    if not is_preferred(ticker) or not spot_price:
        return None
    try:
        from src.tools.api import _STABLE, _fmp_get
        import os
        rows = _fmp_get(f"{_STABLE}/quote", {"symbol": common_of(ticker)}, api_key or os.environ.get("FMP_API_KEY"))
        px = (rows[0] or {}).get("price") if isinstance(rows, list) and rows else None
        return float(px) if isinstance(px, (int, float)) and px > 0 else None
    except Exception:                                      # noqa: BLE001
        return None


def preferred_note(ticker: Optional[str], price: Optional[float] = None,
                   common_price: Optional[float] = None) -> Optional[str]:
    """Disclosure for a preferred line, or None for a common one."""
    if not is_preferred(ticker):
        return None
    base = (f"{to_canonical(ticker)} is a PREFERRED share of the issuer whose common line is {common_of(ticker)}: "
            "the statements and intrinsic value per share are the issuer's (preferred shares carry the same claim on "
            "earnings, a small dividend premium and no vote)")
    if price and common_price and common_price > 0:
        disc = 1.0 - float(price) / float(common_price)
        base += f"; it trades at a {disc:.0%} discount to the common line"
    return base + "."


#: Research guidance for Korean listings. Korean-language sources carry most primary disclosure (DART) and same-day
#: coverage; English wires lag and summarise. K-IFRS publishes CONSOLIDATED (연결) and SEPARATE (별도) statements --
#: the separate parent-only figures are routinely quoted in Korean press and must not be mixed with consolidated.
_RESEARCH_HINT = (
    "\nKOREA LISTING ({board}) -- research guidance:\n"
    "  - Search Korean-language sources as well as English: DART electronic disclosure (dart.fss.or.kr, 전자공시), "
    "KRX KIND (kind.krx.co.kr), and Korean financial press (한국경제 hankyung.com, 매일경제 mk.co.kr, "
    "연합인포맥스 news.einfomax.co.kr, 서울경제 sedaily.com). Translate quoted figures; cite the original URL.\n"
    "  - Use K-IFRS CONSOLIDATED (연결) figures. Korean press often quotes SEPARATE / parent-only (별도) results; "
    "never mix the two bases, and say which one a figure is on.\n"
    "  - Amounts are in Korean won (KRW, ₩; no minor unit). 조 = trillion (1e12), 억 = 100 million (1e8). "
    "Fiscal year ends in December unless the filing says otherwise.\n"
    "  - Preliminary quarterly results (잠정실적, e.g. Samsung's early-month guidance) precede the full filing; "
    "label them preliminary.\n"
    "{pref}"
)


def research_context(ticker: Optional[str], filing_ref: Optional[dict] = None) -> str:
    """Prompt block for a Korean listing ('' for any other ticker): sources, statement basis, units, and the DART
    annual report when the resolver found one."""
    c = to_canonical(ticker)
    if not c:
        return ""
    pref = ""
    if is_preferred(c):
        pref = (f"  - {c} is a PREFERRED line; the issuer's common line is {common_of(c)}. Research the issuer; "
                "note the preferred discount and that preferred holders have no vote.\n")
    out = _RESEARCH_HINT.format(board=board_of(c), pref=pref)
    ref = filing_ref or {}
    if ref.get("exchange") == "DART" and ref.get("filing_url"):
        out += (f"  - PRIMARY SOURCE: {ref.get('company_name') or c} {ref.get('filing_type') or 'Business Report'} "
                f"FY{ref.get('fiscal_year') or ''} (DART, filed {ref.get('filing_date') or '?'}, receipt no. "
                f"{ref.get('accession_number') or '?'}) -- {ref['filing_url']}. Cite statement figures as "
                f"({ref.get('company_name') or c} {ref.get('filing_type') or 'Business Report'}, DART).\n")
    return out + "\n"

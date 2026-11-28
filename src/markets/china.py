"""China A-shares: Shanghai (SSE, ".SS", FMP exchange SHH) and Shenzhen (SZSE, ".SZ", FMP exchange SHZ).

Everything China-specific lives here; shared files carry one-line hooks into this module so the Korea and Japan
branches merge cleanly. See docs/markets/CHINA.md for what was verified, the accepted constants and the owner
decisions still open.

Ticker rules
------------
Canonical form is the 6-digit code plus the venue suffix FMP and Yahoo use: "600519.SS", "300750.SZ". Accepted
inputs (case-insensitive) always name the venue:

    600519.SS  600519.SH  600519.SHH  SH600519  SH.600519      -> 600519.SS
    000858.SZ  000858.SHZ  SZ000858   SZ.000858                -> 000858.SZ

The code must sit in that venue's range (the exchanges' own allocation):

    SSE   600 601 603 605 (main board)   688 689 (STAR Market)
    SZSE  000 001 002 003 (main board)   300 301 (ChiNext)

A BARE six-digit code ("600519", "000660") is NOT canonicalised (owner, 2026-11-28, D-CN-1): Korean codes are six
digits too ("000660" is SK Hynix), so a bare code stays exactly as typed and the landing search offers the
listings to pick from (search_local returns the A-share candidate implied by the prefix).

NOT supported (normalize returns None, so the ticker is left exactly as typed): B-shares (900xxx.SS quote in USD,
200xxx.SZ in HKD), the Beijing Stock Exchange (4xxxxx / 8xxxxx / 920xxx), and index codes (000001.SS is the SSE
Composite, 000300.SS the CSI 300 -- a ".SS" suffix on a 0-prefixed code is an index, never a stock).

Merge note for the Korea branch: neither market claims bare digits; both offer them as search candidates.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

__all__ = [
    "normalize", "bare_code_candidate", "is_cn_ticker", "to_canonical", "to_fmp_code", "exchange_code", "board", "price_limit",
    "LISTING_CURRENCY", "CURRENCY_LABEL", "CAPM_MARKET", "STATUTORY_TAX", "TOP_A_SHARES", "h_share_for",
    "a_share_for", "ah_premium", "is_ah_dual_listed", "AH_SHARES_SOURCE", "search_local", "CN_PROFILE_PINS", "CN_TICKER_SECTOR_LOOKUP",
    "ACCEPTED_CONSTANTS", "a_share_crp", "ev_input_withhold_reason", "ev_withhold_verdict", "reused_input_disclosure", "statutory_deadlines", "next_statutory_deadline", "research_context", "company_name_zh",
]

# ── tickers ─────────────────────────────────────────────────────────────────

SSE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_PREFIXES = ("000", "001", "002", "003", "300", "301")
_BOARDS = {
    "600": "SSE Main Board", "601": "SSE Main Board", "603": "SSE Main Board", "605": "SSE Main Board",
    "688": "STAR Market", "689": "STAR Market",
    "000": "SZSE Main Board", "001": "SZSE Main Board", "002": "SZSE Main Board", "003": "SZSE Main Board",
    "300": "ChiNext", "301": "ChiNext",
}
#: Daily price limit by board (exchange rules; ST names are 5% on the main boards, not modelled here).
_PRICE_LIMIT = {"SSE Main Board": 0.10, "SZSE Main Board": 0.10, "STAR Market": 0.20, "ChiNext": 0.20}

_SUFFIX_FORM = re.compile(r"^(\d{6})\.(SS|SH|SHH|SZ|SHZ)$")
_PREFIX_FORM = re.compile(r"^(SH|SZ)\.?(\d{6})$")
_PLAIN_FORM = re.compile(r"^\d{6}$")
_SH_SUFFIXES = ("SS", "SH", "SHH")


def _venue_for_code(code: str) -> Optional[str]:
    if code[:3] in SSE_PREFIXES:
        return "SS"
    if code[:3] in SZSE_PREFIXES:
        return "SZ"
    return None


def normalize(ticker: Optional[str]) -> Optional[str]:
    """The canonical "NNNNNN.SS" / "NNNNNN.SZ" form, or None when this is not a supported A-share stock code.

    An explicit venue must agree with the code's prefix: "000001.SS" is the SSE Composite index, not Ping An Bank
    (000001.SZ), and "600519.SZ" does not exist -- both return None rather than a plausible wrong listing."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    m = _SUFFIX_FORM.match(t)
    if m:
        code, suf = m.group(1), m.group(2)
        want = "SS" if suf in _SH_SUFFIXES else "SZ"
        return f"{code}.{want}" if _venue_for_code(code) == want else None
    m = _PREFIX_FORM.match(t)
    if m:
        want = "SS" if m.group(1) == "SH" else "SZ"
        code = m.group(2)
        return f"{code}.{want}" if _venue_for_code(code) == want else None
    return None                      # bare digits stay ambiguous (D-CN-1)


def bare_code_candidate(ticker: Optional[str]) -> Optional[str]:
    """The A-share listing a BARE six-digit code could be ("600519" -> "600519.SS"), for the search pick list only --
    never a canonicalisation."""
    t = (ticker or "").strip().upper()
    if not _PLAIN_FORM.match(t):
        return None
    venue = _venue_for_code(t)
    return f"{t}.{venue}" if venue else None


def is_cn_ticker(ticker: Optional[str]) -> bool:
    return normalize(ticker) is not None


def to_canonical(ticker: str) -> str:
    out = normalize(ticker)
    if out is None:
        raise ValueError(f"not a supported China A-share code: {ticker!r}")
    return out


def to_fmp_code(ticker: str) -> str:
    """FMP takes the canonical form as-is (verified 2026-11-28: /stable/profile for 600519.SS, 300750.SZ)."""
    return to_canonical(ticker)


def exchange_code(ticker: str) -> Optional[str]:
    """The FMP / regional_comps exchange key ("SHH" / "SHZ"), or None."""
    t = normalize(ticker)
    if not t:
        return None
    return "SHH" if t.endswith(".SS") else "SHZ"


def board(ticker: str) -> Optional[str]:
    t = normalize(ticker)
    return _BOARDS.get(t[:3]) if t else None


def price_limit(ticker: str) -> Optional[float]:
    b = board(ticker)
    return _PRICE_LIMIT.get(b) if b else None


# ── currency / tax / CAPM market ────────────────────────────────────────────

#: A-shares quote in renminbi. FMP /stable/profile returns "CNY" for all three smoke names; get_listing_currency
#: asks FMP first and falls back to this.
LISTING_CURRENCY = "CNY"
#: One label everywhere (web, PDF, Excel): "RMB", never a bare "¥", which is also the yen sign.
CURRENCY_LABEL = "RMB"
#: The key the CAPM layer (src/data/capm.py) and valuation_constants.risk_free_by_market use for China.
CAPM_MARKET = "CN"
#: PRC enterprise income tax, statutory 25% (EIT Law art. 4). High and New Technology Enterprises pay 15%; the
#: engine prefers the filed effective rate (dcf_agent._company_tax_rate) whenever two years of filings exist.
STATUTORY_TAX = 0.25


# ── the large caps: names, A/H pairs, ownership ─────────────────────────────
#
# H counterpart = the same issuer's Hong Kong line (verified 2026-11-28 by matching FMP company names; ISINs differ
# by line, as they should). An A-share and its H-share are DIFFERENT listings in DIFFERENT currencies with a
# persistent premium or discount between them: a valuation is always compared with the price of the listing it is
# for, never with the other line's price. ownership: "central_soe" (SASAC / MOF / Huijin controlled), "local_soe"
# (provincial or municipal SASAC), "private", or None where not checked -- from public knowledge, NOT verified
# against the 2025 annual reports; it is context for research, never an input to the valuation.

TOP_A_SHARES: dict[str, dict] = {
    "600519.SS": {"name": "Kweichow Moutai", "zh": "贵州茅台", "h": None, "ownership": "local_soe"},
    "300750.SZ": {"name": "CATL", "zh": "宁德时代", "h": "03750.HK", "ownership": "private"},
    "601318.SS": {"name": "Ping An Insurance", "zh": "中国平安", "h": "02318.HK", "ownership": None},
    "600036.SS": {"name": "China Merchants Bank", "zh": "招商银行", "h": "03968.HK", "ownership": "central_soe"},
    "601398.SS": {"name": "ICBC", "zh": "工商银行", "h": "01398.HK", "ownership": "central_soe"},
    "601288.SS": {"name": "Agricultural Bank of China", "zh": "农业银行", "h": "01288.HK", "ownership": "central_soe"},
    "601939.SS": {"name": "China Construction Bank", "zh": "建设银行", "h": "00939.HK", "ownership": "central_soe"},
    "601988.SS": {"name": "Bank of China", "zh": "中国银行", "h": "03988.HK", "ownership": "central_soe"},
    "601328.SS": {"name": "Bank of Communications", "zh": "交通银行", "h": "03328.HK", "ownership": "central_soe"},
    "601166.SS": {"name": "Industrial Bank", "zh": "兴业银行", "h": None, "ownership": "local_soe"},
    "601998.SS": {"name": "China CITIC Bank", "zh": "中信银行", "h": "00998.HK", "ownership": "central_soe"},
    "600016.SS": {"name": "China Minsheng Banking", "zh": "民生银行", "h": "01988.HK", "ownership": "private"},
    "000001.SZ": {"name": "Ping An Bank", "zh": "平安银行", "h": None, "ownership": None},
    "600900.SS": {"name": "China Yangtze Power", "zh": "长江电力", "h": None, "ownership": "central_soe"},
    "601857.SS": {"name": "PetroChina", "zh": "中国石油", "h": "00857.HK", "ownership": "central_soe"},
    "600028.SS": {"name": "Sinopec", "zh": "中国石化", "h": "00386.HK", "ownership": "central_soe"},
    "601088.SS": {"name": "China Shenhua Energy", "zh": "中国神华", "h": "01088.HK", "ownership": "central_soe"},
    "000858.SZ": {"name": "Wuliangye Yibin", "zh": "五粮液", "h": None, "ownership": "local_soe"},
    "000333.SZ": {"name": "Midea Group", "zh": "美的集团", "h": "00300.HK", "ownership": "private"},
    "000651.SZ": {"name": "Gree Electric Appliances", "zh": "格力电器", "h": None, "ownership": None},
    "002594.SZ": {"name": "BYD", "zh": "比亚迪", "h": "01211.HK", "ownership": "private"},
    "601633.SS": {"name": "Great Wall Motor", "zh": "长城汽车", "h": "02333.HK", "ownership": "private"},
    "601012.SS": {"name": "LONGi Green Energy", "zh": "隆基绿能", "h": None, "ownership": "private"},
    "600438.SS": {"name": "Tongwei", "zh": "通威股份", "h": None, "ownership": "private"},
    "600276.SS": {"name": "Jiangsu Hengrui Pharmaceuticals", "zh": "恒瑞医药", "h": "01276.HK", "ownership": "private"},
    "300760.SZ": {"name": "Mindray Bio-Medical", "zh": "迈瑞医疗", "h": None, "ownership": "private"},
    "601899.SS": {"name": "Zijin Mining", "zh": "紫金矿业", "h": "02899.HK", "ownership": "local_soe"},
    "688981.SS": {"name": "SMIC", "zh": "中芯国际", "h": "00981.HK", "ownership": None},
    "600309.SS": {"name": "Wanhua Chemical", "zh": "万华化学", "h": None, "ownership": "local_soe"},
    "600030.SS": {"name": "CITIC Securities", "zh": "中信证券", "h": "06030.HK", "ownership": "central_soe"},
    "300059.SZ": {"name": "East Money Information", "zh": "东方财富", "h": None, "ownership": "private"},
    "601628.SS": {"name": "China Life Insurance", "zh": "中国人寿", "h": "02628.HK", "ownership": "central_soe"},
    "601601.SS": {"name": "China Pacific Insurance", "zh": "中国太保", "h": "02601.HK", "ownership": None},
    "601319.SS": {"name": "PICC Group", "zh": "中国人保", "h": "01339.HK", "ownership": "central_soe"},
    "601336.SS": {"name": "New China Life Insurance", "zh": "新华保险", "h": "01336.HK", "ownership": None},
    "600941.SS": {"name": "China Mobile", "zh": "中国移动", "h": "00941.HK", "ownership": "central_soe"},
    "601728.SS": {"name": "China Telecom", "zh": "中国电信", "h": "00728.HK", "ownership": "central_soe"},
    "600050.SS": {"name": "China Unicom (A)", "zh": "中国联通", "h": None, "ownership": "central_soe"},
    "002415.SZ": {"name": "Hikvision", "zh": "海康威视", "h": None, "ownership": "central_soe"},
    "600887.SS": {"name": "Yili Group", "zh": "伊利股份", "h": None, "ownership": None},
    "603288.SS": {"name": "Foshan Haitian Flavouring", "zh": "海天味业", "h": "03288.HK", "ownership": "private"},
    "601138.SS": {"name": "Foxconn Industrial Internet", "zh": "工业富联", "h": None, "ownership": "private"},
    "002475.SZ": {"name": "Luxshare Precision", "zh": "立讯精密", "h": None, "ownership": "private"},
    "300308.SZ": {"name": "Zhongji Innolight", "zh": "中际旭创", "h": None, "ownership": "private"},
    "601766.SS": {"name": "CRRC", "zh": "中国中车", "h": "01766.HK", "ownership": "central_soe"},
    "601800.SS": {"name": "China Communications Construction", "zh": "中国交建", "h": "01800.HK", "ownership": "central_soe"},
    "601390.SS": {"name": "China Railway Group", "zh": "中国中铁", "h": "00390.HK", "ownership": "central_soe"},
    "601186.SS": {"name": "China Railway Construction", "zh": "中国铁建", "h": "01186.HK", "ownership": "central_soe"},
    "601668.SS": {"name": "China State Construction", "zh": "中国建筑", "h": None, "ownership": "central_soe"},
    # Added 2026-11-28 from the input-data audit (A+H share-count mismatches); FMP names verified on both lines.
    "600938.SS": {"name": "CNOOC", "zh": "中国海油", "h": "00883.HK", "ownership": "central_soe"},
    "601658.SS": {"name": "Postal Savings Bank of China", "zh": "邮储银行", "h": "01658.HK", "ownership": "central_soe"},
    "688347.SS": {"name": "Hua Hong Semiconductor", "zh": "华虹公司", "h": "01347.HK", "ownership": None},
    "688235.SS": {"name": "BeOne Medicines", "zh": "百济神州", "h": "06160.HK", "ownership": None},  # also ONC (US)
}


def company_name_zh(ticker: str) -> Optional[str]:
    t = normalize(ticker)
    return (TOP_A_SHARES.get(t) or {}).get("zh") if t else None


def h_share_for(ticker: str) -> Optional[str]:
    """The same issuer's Hong Kong line (canonical 5-digit), or None."""
    t = normalize(ticker)
    return (TOP_A_SHARES.get(t) or {}).get("h") if t else None


def a_share_for(hk_ticker: str) -> Optional[str]:
    """The A-share line of an HK-listed issuer, or None."""
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_canonical as _hk
        h = _hk(hk_ticker) if is_hk_ticker(hk_ticker) else (hk_ticker or "").strip().upper()
    except Exception:                                       # noqa: BLE001
        h = (hk_ticker or "").strip().upper()
    return next((a for a, v in TOP_A_SHARES.items() if v.get("h") == h), None)


def is_ah_dual_listed(ticker: str) -> bool:
    """True for EITHER line of an A+H issuer in TOP_A_SHARES (the A-share, or its HK line in any HK code form)."""
    if h_share_for(ticker):
        return True
    t = (ticker or "").strip().upper()
    try:
        from src.tools.hk.ticker import is_hk_ticker
        return is_hk_ticker(t) and a_share_for(t) is not None
    except Exception:                                       # noqa: BLE001
        return False


#: Owner/lead audit 2026-11-28: for an A+H issuer FMP's quote marketCap is COMPANY-level (A shares x A price + H
#: shares x H price), so marketCap / one line's price is not that line's share count -- it is too small when the line
#: trades at a premium (600941.SS: 13.94bn implied vs 21.75bn total, per-share values +56%) and too large at a
#: discount. Both lines therefore keep the company's TOTAL share count from the statements.
AH_SHARES_SOURCE = "ah_total_shares"


def ah_premium(a_price_cny: float, h_price_hkd: float, cny_per_hkd: float) -> Optional[float]:
    """A-share premium over the H-share, both in renminbi: A / (H x CNY-per-HKD) - 1. Display/disclosure only --
    it is never used to move either line's valuation onto the other's price."""
    try:
        h_cny = float(h_price_hkd) * float(cny_per_hkd)
        if h_cny <= 0 or float(a_price_cny) <= 0:
            return None
        return float(a_price_cny) / h_cny - 1.0
    except (TypeError, ValueError):
        return None


def search_local(query: str, limit: int = 8) -> list[dict]:
    """Company-search rows for a Chinese name, English name or A-share code -- FMP's search returns nothing for
    a CJK query ("贵州茅台" -> [], verified 2026-11-28) and Yahoo's is unreliable for it. Same row shape as
    /analysis/search: {ticker, name, exchange, type}."""
    q = (query or "").strip()
    if not q:
        return []
    out: list[dict] = []
    canon = normalize(q) or bare_code_candidate(q)     # a bare code is OFFERED, never assumed
    ql = q.lower()
    for t, v in TOP_A_SHARES.items():
        hit = (canon == t) or (v["zh"] and v["zh"] in q) or (q in v["zh"]) or (len(ql) >= 3 and ql in v["name"].lower())
        if hit:
            out.append({"ticker": t, "name": f"{v['name']} ({v['zh']})",
                        "exchange": "SHH" if t.endswith(".SS") else "SHZ", "type": "equity"})
    if canon and not any(r["ticker"] == canon for r in out):
        out.insert(0, {"ticker": canon, "name": canon, "exchange": exchange_code(canon), "type": "equity"})
    return out[:limit]


# ── sector pins: ACCEPTED by the owner 2026-11-28 (D-CN-5) ──────────────────
#
# CN_PROFILE_PINS -> CN_TICKER_SECTOR_LOOKUP (same 4-tuple shape as TICKER_SECTOR_LOOKUP), read by
# get_wacc_profile_for_ticker and coverage.is_tuned: these names are "tuned" (billing "new", 2 credits).
#
# Basis column: "H pin" = the owner's existing pin for the same issuer's HK line in TICKER_SECTOR_LOOKUP (same
# company, same accounts -- the strongest evidence available); "map" = the industry_profile_map row for the FMP
# label; "judgement" = the FMP label routes somewhere wrong and the row says why.
CN_PROFILE_PINS: dict[str, tuple[str, str, str]] = {
    "600519.SS": ("Consumer", "Food & Beverage", "map (Beverages - Alcoholic); owner: or Luxury Goods?"),
    "000858.SZ": ("Consumer", "Food & Beverage", "map (Beverages - Wineries & Distilleries)"),
    "600887.SS": ("Consumer", "Food & Beverage", "map (Packaged Foods)"),
    "603288.SS": ("Consumer", "Food & Beverage", "map (Packaged Foods)"),
    "300750.SZ": ("Industrials", "Battery & Energy Storage", "H pin 03750.HK"),
    "601318.SS": ("Financials", "Insurance", "H pin 02318.HK"),
    "601628.SS": ("Financials", "Insurance", "H pin 02628.HK"),
    "601601.SS": ("Financials", "Insurance", "H pin 02601.HK"),
    "601336.SS": ("Financials", "Insurance", "H pin 01336.HK"),
    "601319.SS": ("Financials", "Insurance (P&C)", "map (Insurance - Property & Casualty); no H pin on 01339.HK"),
    "600036.SS": ("Financials", "EM Bank", "H pin 03968.HK"),
    "601398.SS": ("Financials", "EM Bank", "H pin 01398.HK"),
    "601288.SS": ("Financials", "EM Bank", "H pin 01288.HK"),
    "601939.SS": ("Financials", "EM Bank", "H pin 00939.HK"),
    "601988.SS": ("Financials", "EM Bank", "H pin 03988.HK"),
    "601328.SS": ("Financials", "EM Bank", "H pin 03328.HK"),
    "601998.SS": ("Financials", "EM Bank", "H pin 00998.HK"),
    "601166.SS": ("Financials", "EM Bank", "judgement: same archetype as the H-pinned PRC banks (FMP: Banks - Regional -> Super-Regional Bank, a US row)"),
    "600016.SS": ("Financials", "EM Bank", "judgement: as 601166.SS"),
    "000001.SZ": ("Financials", "EM Bank", "judgement: as 601166.SS"),
    "600030.SS": ("Financials", "Investment Bank", "H pin 06030.HK"),
    "300059.SZ": ("Financials", "Brokerage", "map (Financial - Capital Markets)"),
    "600900.SS": ("Energy", "Regulated Utility", "judgement: tariff-regulated hydro (FMP: Renewable Utilities, unmapped); its P/Rate Base leg needs a SHH cost-of-equity row (D-CN-5)"),
    "601857.SS": ("Resources", "Integrated Oil & Gas", "map (Oil & Gas Integrated); H pin 00857.HK has no profile"),
    "600028.SS": ("Resources", "Integrated Oil & Gas", "map (Oil & Gas Integrated); H pin 00386.HK has no profile"),
    "601088.SS": ("Resources", "Coal", "judgement: FMP 'Coal' (unmapped); 01088.HK unpinned"),
    "601899.SS": ("Resources", "Precious Metals", "H pin 02899.HK"),
    "600309.SS": ("Materials", "Specialty Chemicals", "map (Chemicals - Specialty); owner: MDI is closer to Commodity Chemicals?"),
    "000333.SZ": ("Consumer", "Consumer Durables", "map (Furnishings, Fixtures & Appliances)"),
    "000651.SZ": ("Consumer", "Consumer Durables", "map (Furnishings, Fixtures & Appliances)"),
    "002594.SZ": ("Consumer", "Automotive & EV", "H pin 01211.HK"),
    "601633.SS": ("Industrials", "Automotive (OEM)", "map (Auto - Manufacturers); 02333.HK has no profile"),
    "601012.SS": ("Energy", "Clean Tech / Power Equipment OEM", "judgement: FMP labels LONGi 'Semiconductors' (-> IDM / Foundry); it makes solar wafers/modules, the Solar row"),
    "600438.SS": ("Energy", "Clean Tech / Power Equipment OEM", "map (Solar)"),
    "600276.SS": ("Biopharma", "Large Cap Pharma", "H pin 01276.HK"),
    "300760.SZ": ("Biopharma", "MedTech / Devices", "map (Medical - Devices)"),
    "688981.SS": ("Semiconductor", "IDM / Foundry", "H pin 00981.HK"),
    "300308.SZ": ("Tech", "Networking & Communication Equipment", "map (Communication Equipment)"),
    "600941.SS": ("Telco", "Telecom Carrier", "H pin 00941.HK"),
    "601728.SS": ("Telco", "Telecom Carrier", "H pin 00728.HK"),
    "600050.SS": ("Telco", "Telecom Carrier", "map (Telecommunications Services)"),
    "601766.SS": ("Industrials", "Capital Goods", "H pin 01766.HK"),
    "601800.SS": ("Industrials", "Long-Cycle E&C", "map (Engineering & Construction); 01800.HK unpinned"),
    "601390.SS": ("Industrials", "Long-Cycle E&C", "H pin 00390.HK"),
    "601186.SS": ("Industrials", "Long-Cycle E&C", "H pin 01186.HK"),
    "601668.SS": ("Industrials", "Long-Cycle E&C", "map (Engineering & Construction)"),
}
CN_TICKER_SECTOR_LOOKUP: dict[str, tuple[str, str, str, str]] = {
    t: (s, p, "", f"owner-accepted 2026-11-28; basis: {b}") for t, (s, p, b) in CN_PROFILE_PINS.items()}

#: Large caps with NO pin: the only rows that fit are archetypes built for something else (limited coverage).
NO_PIN_PROPOSED: dict[str, str] = {
    "002415.SZ": "FMP 'Computer Hardware' -> Mature Platform; Hikvision is security/video equipment -- no archetype fits",
    "601138.SS": "Contract manufacturing (EMS); the only EMS profile is SG-calibrated ('Tech Manufacturing / EMS (SG)')",
    "002475.SZ": "As 601138.SS",
}


# ── constants: ACCEPTED by the owner 2026-11-28. The engine reads valuation_constants.json; this records the
#    derivation next to the values. ─────────────────────────────────────────

#: Owner, 2026-11-28 (D-CN-3/4): A-shares carry the market registry's country risk premium (SHH/SHZ 1.5%) in the
#: WACC, the market cost of equity and the rating benchmark alike -- not the CNY-currency 0% that HK-listed CNY
#: reporters keep. Read through sector_profiles.market_crp so the two cannot drift.
def a_share_crp(ticker: str) -> Optional[float]:
    """The country risk premium for an A-share listing, or None when the ticker is not one."""
    t = normalize(ticker)
    if not t:
        return None
    from src.data.sector_profiles import market_crp
    return market_crp(exchange_code(t))


#: Owner, 2026-11-28 (D-CN-6): a life insurer is valued on Embedded Value x the cohort P/EV. Until an EV input is
#: accepted for the A-share line itself the valuation is WITHHELD (unrated) rather than published off a fallback leg
#: (Ping An: P&C GGM 117 / P/BV fallback 45 vs a 55 spot, neither a valuation).
EV_REQUIRED_PROFILES = frozenset({"Insurance"})


def ev_input_withhold_reason(ticker: str, profile: Optional[str], embedded_value_computed: bool) -> Optional[str]:
    """The unrated reason for an A-share life insurer whose Embedded Value leg could not be computed, else None."""
    t = normalize(ticker)
    if not t or profile not in EV_REQUIRED_PROFILES or embedded_value_computed:
        return None
    h = h_share_for(t)
    return ("valuation withheld: a life insurer is valued on embedded value and no embedded-value input has been "
            f"accepted for {t}" + (f" (the accepted input for its H-share {h} is not reused across listings)" if h
                                    else "") + ".")


def ev_withhold_verdict(ticker: str, profile: Optional[str], base_result: Optional[dict]) -> Optional[dict]:
    """The unrated verdict ({code, label, reason}) dcf_agent applies when an A-share life insurer's Embedded Value leg
    did not compute -- it is withheld, not published off its P/BV / P&C fallback (Ping An: 45 / 117 vs 55)."""
    ew = (base_result or {}).get("effective_weights") or []
    ev_ok = any(isinstance(e, dict) and e.get("method") == "Embedded Value" for e in ew)
    why = ev_input_withhold_reason(ticker, profile, ev_ok)
    if not why:
        return None
    from src.data.valuation_constants import UNRATED_REASONS
    return {"code": "input_required", "label": UNRATED_REASONS["input_required"], "reason": why}


def reused_input_disclosure(entry: dict, field: str, target_ccy: str, fx) -> Optional[dict]:
    """The disclosure a leg carries when its company input was REUSED from the H line (owner, 2026-11-28):
    {source_listing, original_value, original_currency, fx_rate, target_currency, converted_value, period,
    reviewed_at, text}. None when the entry is the listing's own. `fx` maps a currency to target units."""
    src = (entry or {}).get("_reused_from")
    if not src:
        return None
    node = ((entry.get("data") or {}).get(field) or {})
    val, ccy = node.get("value"), str(node.get("currency") or "").upper() or None
    rate = None
    try:
        rate = fx(ccy) if ccy else None
    except Exception:                                       # noqa: BLE001
        rate = None
    conv = (float(val) * float(rate)) if isinstance(val, (int, float)) and rate else None
    out = {"source_listing": src.get("listing"), "original_value": val, "original_currency": ccy,
           "fx_rate": rate, "target_currency": target_ccy, "converted_value": conv,
           "period": node.get("period") or (entry.get("data") or {}).get("fiscal_year"),
           "reviewed_at": src.get("reviewed_at")}
    out["text"] = (f"{field} reused from the same issuer's {src.get('listing')} accepted input: {val} {ccy}"
                   f" ({out['period']}), x FX {ccy}->{target_ccy} {rate if rate is not None else 'n/a'}"
                   + (f" = {conv:,.2f} {target_ccy}" if conv is not None else "")
                   + f"; accepted {str(src.get('reviewed_at') or 'n/a')[:10]}. A company fact on the shared share "
                   "base -- not the H-share price.")
    return out


ACCEPTED_CONSTANTS: dict = {
    "risk_free_by_market.markets.CN": {   # accepted 2026-11-28
        "value": 0.0159,
        "basis": "10Y China Government Bond (CGB)",
        "source": "ADB AsianBondsOnline market watch, close 2026-11-26 (1.591%); 1.594% at the 2026-11-18 close",
        "min_spread_over_rf": 0.03,
        "derivation": ("rf = CGB 10y, the currency of A-share cash flows (owner rule 2026-11-25: rf matches the cash "
                       "flows). min spread: the SG precedent (3.0pp over SGS 2.29%); on 1.59% a 0.6-beta name gets "
                       "Ke 1.59% + 0.6 x 4.41% = 4.2% without it. Market cross-check: the SHH sector NTM P/E median "
                       "is 13.5x (9 sectors, prod regional_comps 2026-11-28) = 7.4% earnings yield, 5.8pp over CGB, "
                       "so an implied China ERP sits above the US 4.41% the engine uses (D-CN-3)."),
    },
    "rating_benchmark.CN": {
        "index": "CSI 300 (already in ratings._BENCHMARKS for SHH/SHZ)",
        "expected_return": "rf + ERP + CRP on the valuation's own CAPM inputs (D1): 1.59% + 4.41% + 1.50% = 7.50%",
        "derivation": ("No new constant: the benchmark follows the CAPM inputs (CGB rf, US ERP, registry CRP 1.5%). "
                       "Tech names: no STAR 50 / ChiNext tech benchmark -- FMP carries neither index."),
    },
}


# ── disclosure calendar (CSRC periodic-report deadlines) ────────────────────
#
# Securities Law / CSRC Measures for the Administration of Information Disclosure: annual report within four months
# of year end (Apr 30), half-year within two months (Aug 31), Q1 and Q3 within one month (Apr 30, Oct 31). Every
# A-share has a December fiscal year, so these are the latest dates results can land; the exchanges publish the
# booked dates (预约披露时间) closer to the season. FMP's per-symbol /earnings rows carry the booked estimates.

def statutory_deadlines(year: int) -> list[tuple[str, date]]:
    return [("FY%d annual report" % (year - 1), date(year, 4, 30)),
            ("Q1 %d report" % year, date(year, 4, 30)),
            ("H1 %d interim report" % year, date(year, 8, 31)),
            ("Q3 %d report" % year, date(year, 10, 31))]


def next_statutory_deadline(today: Optional[date] = None) -> tuple[str, date]:
    d = today or date.today()
    for label, when in statutory_deadlines(d.year) + statutory_deadlines(d.year + 1):
        if when >= d:
            return label, when
    raise AssertionError("unreachable")                     # pragma: no cover


# ── research context ────────────────────────────────────────────────────────

def research_context(ticker: str, company_name: str = "", filing: Optional[dict] = None) -> str:
    """The block deep research adds for an A-share: Chinese-language primary sources, the A/H rule, the CAS
    basis. Plain text, prepended to the research base context."""
    t = normalize(ticker) or ticker
    meta = TOP_A_SHARES.get(t) or {}
    zh = meta.get("zh")
    venue = "Shanghai Stock Exchange (sse.com.cn)" if t.endswith(".SS") else "Shenzhen Stock Exchange (szse.cn)"
    lines = [f"CHINA A-SHARE {t} -- {company_name or meta.get('name') or t}" + (f" ({zh})" if zh else ""),
             f"  Listing: {venue}, {board(t) or 'A-share'}; quoted in RMB (CNY). Fiscal year = calendar year.",
             "  Accounting: China Accounting Standards (CAS / ASBE), substantially converged with IFRS. Statements "
             "are consolidated (合并报表); use the parent-shareholder figures (归属于母公司) for EPS and book value.",
             "  PRIMARY SOURCES ARE IN SIMPLIFIED CHINESE. Search in Chinese as well as English"
             + (f", using the company's Chinese name {zh}" if zh else "") + ": cninfo.com.cn (巨潮资讯, the CSRC-"
             "designated disclosure site), the exchange site above, 年度报告 / 半年度报告 / 季度报告 / 业绩预告 "
             "(results preview) / 业绩快报 (flash results).",
             "  Treat forum and retail-platform posts (东方财富股吧, 雪球) as unverified; financial press (财新 Caixin, "
             "证券时报, 上海证券报, 中国证券报) as secondary."]
    h = meta.get("h")
    if h:
        lines.append(f"  A/H DUAL LISTING: the same issuer also trades in Hong Kong as {h} in HKD. That is a "
                     "different line with its own price; never quote or value this A-share off the H-share price, "
                     "and never mix HKD and RMB per-share figures.")
    if meta.get("ownership") in ("central_soe", "local_soe"):
        lines.append("  State-owned enterprise ("
                     + ("central SOE" if meta["ownership"] == "central_soe" else "local SOE")
                     + "): note policy objectives, SASAC dividend guidance and capital-allocation constraints "
                     "where the sources discuss them.")
    if filing and filing.get("filing_url"):
        lines.append(f"  Latest annual report (cninfo, filed {filing.get('filing_date')}): {filing['filing_url']}")
    return "\n".join(lines) + "\n\n"

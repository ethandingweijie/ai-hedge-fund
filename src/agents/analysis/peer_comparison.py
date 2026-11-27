"""
Phase 4.6 — Peer Comparison Engine  (deterministic, no LLM)

Fetches live TTM financial metrics for the subject ticker(s) and a
sector-defined peer group, then stores the result in state so downstream
consumers (PDF, portfolio manager) can access it without re-fetching.

Return structure in state["data"]["peer_comparison"]:
{
  "NVDA": {                       # keyed by subject ticker
    "NVDA": { "ticker": "NVDA", "is_subject": True,  "pe_ratio": ..., ... },
    "AMD":  { "ticker": "AMD",  "is_subject": False, "pe_ratio": ..., ... },
    ...
  },
  ...
}

Each row dict contains:
  ticker, is_subject, pe_ratio, ev_ebitda, ev_revenue, pb_ratio,
  fcf_yield, roic, revenue_growth, gross_margin, net_margin, market_cap
"""

from __future__ import annotations

import os
from datetime import datetime

from src.graph.state import AgentState
from src.tools.api import get_financial_metrics

# ── Sector peer groups ──────────────────────────────────────────────────────────
# 5-6 liquid, representative peers per sector.
# Subject ticker is excluded at runtime if it appears in the list.
SECTOR_PEERS: dict[str, list[str]] = {
    # Tech: semiconductor + hyperscaler mix; AMD/AVGO/INTC closest GPU/chip comps
    "Tech":                ["AMD",  "AVGO", "MSFT", "GOOGL", "META", "INTC"],
    # Consumer: split into apparel and broad retail; router should select the right block
    # Broad consumer/retail (default)
    "Consumer":            ["AMZN", "WMT",  "COST", "TGT",  "HD",   "NKE"],
    # Apparel/athletic: used when subject is apparel-focused (LULU, NKE, UA, ONON, etc.)
    "ConsumerApparel":     ["NKE",  "ONON", "UA",   "VFC",  "PVH",  "RL"],
    "Biopharma":           ["JNJ",  "PFE",  "MRK",  "ABBV", "AMGN", "GILD"],
    "Telco":               ["VZ",   "T",    "TMUS", "AMT",  "CCI"],
    "Crypto":              ["MSTR", "COIN", "MARA", "RIOT", "HUT"],
    "Energy":              ["XOM",  "CVX",  "NEE",  "DUK",  "SO",   "SLB"],
    "Financials":          ["JPM",  "BAC",  "GS",   "MS",   "WFC",  "C"],
    "Industrials":         ["GE",   "HON",  "LMT",  "RTX",  "BA",   "CAT"],
    "RealEstate":          ["PLD",  "AMT",  "EQIX", "SPG",  "O"],
    "Transportation":      ["UPS",  "FDX",  "DAL",  "UAL",  "CSX"],
    "Materials":           ["LIN",  "APD",  "NEM",  "FCX",  "BHP"],
    "Resources":           ["XOM",  "CVX",  "COP",  "EOG",  "MPC"],
    "ProfessionalServices":["ACN",  "IBM",  "INFY", "WIT",  "EPAM"],
    "HealthcareServices":  ["UNH",  "CVS",  "HCA",  "CI",   "HUM"],
}

# Tickers that should use a sub-sector peer group instead of the broad sector default.
# Maps ticker → sector key in SECTOR_PEERS.
_TICKER_PEER_OVERRIDE: dict[str, str] = {
    # Athletic/premium apparel — use ConsumerApparel not generic Consumer
    "LULU": "ConsumerApparel",
    "NKE":  "ConsumerApparel",
    "UA":   "ConsumerApparel",
    "UAA":  "ConsumerApparel",
    "ONON": "ConsumerApparel",
    "VFC":  "ConsumerApparel",
    "PVH":  "ConsumerApparel",
    "RL":   "ConsumerApparel",
    "SKX":  "ConsumerApparel",
    "CROX": "ConsumerApparel",
}

_MAX_PEERS = 4   # peers shown alongside the subject (so table = 5 cols max)


# ── Metric extraction ───────────────────────────────────────────────────────────

def _extract_metrics(fm_list: list, ticker: str, is_subject: bool) -> dict:
    """Pull the most-recent TTM metrics from a FinancialMetrics list."""
    base: dict = {"ticker": ticker, "is_subject": is_subject}
    if not fm_list:
        return base
    m = fm_list[0]
    base.update({
        "pe_ratio":       getattr(m, "price_to_earnings_ratio",          None),
        # Correct attribute names from FinancialMetrics model:
        "ev_ebitda":      getattr(m, "enterprise_value_to_ebitda_ratio", None),
        "ev_revenue":     getattr(m, "enterprise_value_to_revenue_ratio",None),
        "pb_ratio":       getattr(m, "price_to_book_ratio",              None),
        "fcf_yield":      getattr(m, "free_cash_flow_yield",             None),
        "roic":           getattr(m, "return_on_invested_capital",       None),
        "revenue_growth": getattr(m, "revenue_growth",                   None),
        "gross_margin":   getattr(m, "gross_margin",                     None),
        "net_margin":     getattr(m, "net_margin",                       None),  # was net_profit_margin
        "market_cap":     getattr(m, "market_cap",                       None),
    })
    return base


# ── Main entry point ────────────────────────────────────────────────────────────

def run_peer_comparison(state: AgentState) -> AgentState:
    """
    Phase 4.6 — Peer Comparison Engine.

    Fetches live TTM financial metrics for each subject ticker and its
    sector peers. Writes results to state["data"]["peer_comparison"].
    Degrades gracefully if the API key is absent or calls fail.
    """
    tickers  = state["data"].get("tickers", [])
    sector   = state["data"].get("sector", "")
    end_date = state["data"].get("end_date", datetime.now().strftime("%Y-%m-%d"))
    api_key  = (
        state["data"].get("api_key")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    peer_comparison: dict[str, dict] = {}

    for subject in tickers:
        # Ticker-level override takes priority (e.g. LULU → ConsumerApparel)
        # then sector default, then empty list
        peer_sector = _TICKER_PEER_OVERRIDE.get(subject.upper(), sector)
        raw_peers   = SECTOR_PEERS.get(peer_sector, SECTOR_PEERS.get(sector, []))
        peers       = [p for p in raw_peers if p.upper() != subject.upper()][:_MAX_PEERS]
        all_tickers = [subject] + peers

        comparison: dict[str, dict] = {}
        for t in all_tickers:
            is_subj = (t.upper() == subject.upper())
            try:
                fm   = get_financial_metrics(t, end_date, period="ttm", limit=1, api_key=api_key)
                row  = _extract_metrics(fm, t, is_subj)
            except Exception:
                row  = {"ticker": t, "is_subject": is_subj}
            comparison[t] = row

        peer_comparison[subject] = comparison

    state["data"]["peer_comparison"] = peer_comparison
    return state


# ── Local + global peers from the comps store (owner, 2026-11-27) ───────────────
# The table above was one hard-coded US list per sector: UOB (a Singapore bank)
# was compared with JPM/BAC/GS/MS and Alibaba with AMD/AVGO/MSFT/GOOGL, in the
# PDF and in the portfolio manager's digest. The valuation's own multiples were
# never drawn from it (they price on the regional comps baskets), but the table
# a reader -- and the PM -- compares against must be the same market's peers.
#
# LOCAL  = the subject's own market: its routed profile's curated basket when
#          it has one (China Internet Platform on HKSE), else the industry
#          group on its exchange (banks pooled: DBS "Banks" with OCBC "Banks -
#          Regional").
# GLOBAL = the same industry group on the other markets, largest by USD cap,
#          never the same company twice (an ADR of a local peer is dropped).
# Multiples are the comps store's own band-checked values (the figures the
# medians are built from); an out-of-band value prints as "—".

_PEER_TABLE_GROUPS: tuple[frozenset, ...] = (
    frozenset({"Banks", "Banks - Regional", "Banks - Diversified"}),
    frozenset({"Insurance - Life", "Insurance - Diversified", "Insurance - Property & Casualty",
               "Insurance - Reinsurance", "Insurance - Specialty"}),
    frozenset({"Internet Content & Information", "Internet Retail", "Specialty Retail"}),
)

_MARKET_CCY = {"US": "USD", "HKSE": "HKD", "SES": "SGD", "JPX": "JPY", "KSC": "KRW", "SHH": "CNY", "SHZ": "CNY"}
_MARKET_LABEL = {"US": "US", "HKSE": "HK", "SES": "SG", "JPX": "JP", "KSC": "KR", "SHH": "CN", "SHZ": "CN"}
_N_LOCAL = 3
_N_GLOBAL = 3


_SUFFIX_LABEL = ((".HK", "HK"), (".SI", "SG"), (".T", "JP"), (".KS", "KR"), (".KQ", "KR"), (".SS", "CN"), (".SZ", "CN"),
                 (".PA", "FR"), (".L", "UK"), (".DE", "DE"), (".AS", "NL"), (".SW", "CH"), (".TW", "TW"))


def _market_label(symbol: str, market: str) -> str:
    """Where the peer is listed, read from its symbol (a cross-market basket mixes listings)."""
    sym = (symbol or "").upper()
    for suf, lab in _SUFFIX_LABEL:
        if sym.endswith(suf):
            return lab
    return "US" if "." not in sym else _MARKET_LABEL.get(market, market)


def _same_company_key(name) -> str:
    """normalize_name plus "&"/"and" folded: ICBC's A share ("... & Commercial ...") and H share
    ("... and Commercial ...") are one company and must not fill two global columns."""
    from src.data.regional_comps import normalize_name
    return " ".join(w for w in normalize_name(name).split() if w != "and")


def _related_industries(industry: str) -> set[str]:
    from src.data.regional_comps import INDUSTRY_FAMILIES
    ind = (industry or "").strip()
    for grp in _PEER_TABLE_GROUPS:
        if ind in grp:
            return set(grp)
    for members in INDUSTRY_FAMILIES.values():
        if ind in members:
            return set(members)
    return {ind} if ind else set()


def _band_value(metrics: dict, field: str):
    cell = (metrics or {}).get(field)
    if isinstance(cell, dict) and cell.get("in_band") and isinstance(cell.get("value"), (int, float)):
        return float(cell["value"])
    return None


def _usd(mcap, market: str):
    if not isinstance(mcap, (int, float)) or mcap <= 0:
        return None
    ccy = _MARKET_CCY.get(market, "USD")
    if ccy == "USD":
        return float(mcap)
    try:
        from src.tools.api import get_fx_rate
        r = get_fx_rate(ccy, "USD")
    except Exception:  # noqa: BLE001
        r = None
    return float(mcap) * float(r) if isinstance(r, (int, float)) and r > 0 else None


def _peer_row(member: dict, market: str, group: str) -> dict:
    m = member.get("metrics") or {}
    return {
        "ticker": member["symbol"], "is_subject": False, "group": group,
        "market": _market_label(member.get("symbol"), market), "name": member.get("name"),
        "pe_ratio": _band_value(m, "pe"), "ev_ebitda": _band_value(m, "ev_ebitda"),
        "ev_revenue": _band_value(m, "ev_revenue"), "pb_ratio": _band_value(m, "pb"),
        "fcf_yield": _band_value(m, "fcf_yield"), "roic": _band_value(m, "roic"),
        "revenue_growth": _band_value(m, "growth_avg"), "gross_margin": None, "net_margin": None,
        "market_cap": None, "market_cap_usd": _usd(member.get("market_cap"), market),
    }


def _members_for(market: str, industries: set[str]) -> list[dict]:
    from src.data.regional_comps import load_members
    seen: dict[str, dict] = {}
    for ind in industries:
        for mb in load_members(market, "industry", ind, "all"):
            seen.setdefault(mb["symbol"], mb)
    return sorted(seen.values(), key=lambda x: -(x.get("market_cap") or 0))


def _profile_members(market: str, profile: str) -> list[dict]:
    """The curated basket a profile names for this market, with each member's stored metrics."""
    from src.data import db as _db
    from src.data.regional_comps import CROSS_MARKET_BASKETS, CROSS_MARKET_EXCHANGE, PROFILE_PEER_BASKETS
    xm = CROSS_MARKET_BASKETS.get(profile or "")
    if xm and market in (xm.get("markets") or ()):
        exch, syms = CROSS_MARKET_EXCHANGE, tuple(xm.get("symbols") or ())
    else:
        exch, syms = market, tuple((PROFILE_PEER_BASKETS.get(profile or "") or {}).get(market) or ())
    out = []
    for sym in syms:
        try:
            r = _db.query_one(
                "SELECT symbol, name, market_cap, metrics_json FROM regional_comps_members "
                "WHERE exchange = ? AND symbol = ? ORDER BY computed_at DESC LIMIT 1", [exch, sym])
        except Exception:  # noqa: BLE001
            r = None
        if r:
            import json as _json
            try:
                metrics = _json.loads(r["metrics_json"] or "{}")
            except (TypeError, ValueError):
                metrics = {}
            out.append({"symbol": r["symbol"], "name": r["name"], "market_cap": r["market_cap"], "metrics": metrics})
    return sorted(out, key=lambda x: -(x.get("market_cap") or 0))


def localized_peer_table(subject: str, subject_row: dict, profile: str | None = None) -> dict | None:
    """{ticker: row} with the subject, up to three local and three global peers; None when the comps
    store has no peer for the subject's market (the caller keeps or drops its fallback)."""
    from src.data.regional_comps import get_fmp_classification, market_for_exchange, subject_symbol
    cls = get_fmp_classification(subject) or {}
    market = market_for_exchange(cls.get("exchange")) or cls.get("exchange")
    industry = cls.get("industry") or ""
    if not market or not industry:
        return None
    subj = subject_symbol(subject)
    names_taken = {_same_company_key(cls.get("name"))}

    def take(members, mkt, group, n):
        rows = []
        for mb in members:
            nm = _same_company_key(mb.get("name"))
            if mb["symbol"] in (subj, subject.upper()) or nm in names_taken:
                continue
            names_taken.add(nm)
            rows.append(_peer_row(mb, mkt, group))
            if len(rows) >= n:
                break
        return rows

    related = _related_industries(industry)
    local_src = _profile_members(market, profile) if profile else []
    local = take(local_src, market, "local", _N_LOCAL)
    if len(local) < _N_LOCAL:
        local += take(_members_for(market, related), market, "local", _N_LOCAL - len(local))

    pool = []
    for mkt in _MARKET_CCY:
        if mkt == market:
            continue
        for mb in _members_for(mkt, related)[:_N_GLOBAL * 2]:
            usd = _usd(mb.get("market_cap"), mkt)
            if usd:
                pool.append((usd, mkt, mb))
    pool.sort(key=lambda x: -x[0])
    # One global peer per region (mainland A shares and HK count as one): by size alone the
    # Chinese state banks filled every global column for UOB.
    region = {"SHH": "CN/HK", "SHZ": "CN/HK", "HKSE": "CN/HK"}
    own_region = region.get(market, market)
    glob, regions_used = [], {own_region}
    for _usd_cap, mkt, mb in pool:
        reg = region.get(mkt, mkt)
        if reg in regions_used:
            continue
        got = take([mb], mkt, "global", 1)
        if got:
            glob += got
            regions_used.add(reg)
        if len(glob) >= _N_GLOBAL:
            break

    if not local and not glob:
        return None
    subj_row = {**(subject_row or {"ticker": subject}), "is_subject": True, "group": "subject",
                "market": _MARKET_LABEL.get(market, market), "industry": industry}
    # The peers' caps are in US$; the subject's (listing currency) is restated so the header compares.
    if subj_row.get("market_cap_usd") is None:
        subj_row["market_cap_usd"] = _usd(subj_row.get("market_cap"), market)
    table = {subject: subj_row}
    for r in local + glob:
        table[r["ticker"]] = r
    return table


def localize_peer_tables(data: dict) -> dict:
    """Replace each subject's sector-list table with local + global comps peers.

    A subject outside the US whose comps store has no peers keeps only its own row (the PDF
    hides a one-column table) rather than a wrong-market list; a US subject without comps
    keeps the sector list. Never raises."""
    pc = data.get("peer_comparison") or {}
    dr = data.get("dcf_range") or {}
    out = dict(pc)
    for subject, table in pc.items():
        try:
            subj_row = (table or {}).get(subject) or {"ticker": subject, "is_subject": True}
            profile = ((dr.get(subject) or {}).get("profile")) if isinstance(dr.get(subject), dict) else None
            loc = localized_peer_table(subject, subj_row, profile)
            if loc:
                out[subject] = loc
            elif not _is_us_listing(subject):
                out[subject] = {subject: subj_row}
        except Exception:  # noqa: BLE001
            continue
    data["peer_comparison"] = out
    return out


def _is_us_listing(ticker: str) -> bool:
    t = (ticker or "").upper()
    return "." not in t and not t.isdigit()

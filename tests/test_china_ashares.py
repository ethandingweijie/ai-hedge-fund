"""China A-share support (SSE .SS / SZSE .SZ): every rule added on the China branch, offline.

Live checks behind these rules (FMP coverage, cninfo, stockanalysis) are recorded in docs/markets/CHINA.md.
"""
from __future__ import annotations

import io
import json
from datetime import date

import pytest

from src.markets import china


# ── tickers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("600519.SS", "600519.SS"), ("600519.ss", "600519.SS"), ("600519.SH", "600519.SS"), ("600519.SHH", "600519.SS"),
    ("SH600519", "600519.SS"), ("sh.600519", "600519.SS"), ("600519", "600519.SS"),
    ("688981", "688981.SS"), ("605499", "605499.SS"),
    ("000858", "000858.SZ"), ("SZ000858", "000858.SZ"), ("000858.SHZ", "000858.SZ"), ("300750.SZ", "300750.SZ"),
    ("301269", "301269.SZ"), ("002594", "002594.SZ"), ("003816", "003816.SZ"),
])
def test_normalize_accepted_forms(raw, want):
    assert china.normalize(raw) == want
    assert china.is_cn_ticker(raw)


@pytest.mark.parametrize("raw", [
    "000001.SS",    # SSE Composite index, not Ping An Bank
    "000300.SS",    # CSI 300 index
    "600519.SZ",    # venue disagrees with the code
    "SZ600519",
    "900901", "900901.SS",   # Shanghai B-share (USD)
    "200002", "200002.SZ",   # Shenzhen B-share (HKD)
    "830799", "430047", "920001",   # Beijing Stock Exchange
    "005930",       # Samsung (Korea): not an SZSE prefix
    "60051", "6005190", "", None, "AAPL", "0700.HK", "D05.SI",
])
def test_normalize_rejects(raw):
    assert china.normalize(raw) is None


def test_canonical_ticker_routes_a_shares_and_leaves_hk_sg_alone():
    from src.tools.ticker_canonical import canonical_ticker, ticker_match_forms
    assert canonical_ticker("600519") == "600519.SS"
    assert canonical_ticker("SZ000858") == "000858.SZ"
    assert canonical_ticker("600519.sh") == "600519.SS"
    assert canonical_ticker("2888") == "02888.HK"
    assert canonical_ticker("80700") == "80700.HK"
    assert canonical_ticker("d05") == "D05.SI"
    assert canonical_ticker("aapl") == "AAPL"
    assert canonical_ticker("000001.SS") == "000001.SS"      # an index stays as typed
    assert ticker_match_forms("600519.SH") == ["600519.SH", "600519.SS"]


def test_fmp_symbol_is_canonical_form():
    from src.tools.fmp_transcripts import to_fmp_symbol
    assert to_fmp_symbol("600519.SS") == "600519.SS"
    assert to_fmp_symbol("300750.SZ") == "300750.SZ"
    assert china.to_fmp_code("SH600519") == "600519.SS"


def test_exchange_board_and_price_limit():
    assert china.exchange_code("600519") == "SHH"
    assert china.exchange_code("300750") == "SHZ"
    assert china.board("688981.SS") == "STAR Market"
    assert china.board("300750.SZ") == "ChiNext"
    assert china.board("000858.SZ") == "SZSE Main Board"
    assert china.price_limit("600519.SS") == 0.10
    assert china.price_limit("688981.SS") == 0.20


def test_request_guard_accepts_a_share_forms():
    from app.backend.request_guards import is_valid_ticker_input
    for t in ("600519.SS", "300750.SZ", "SH600519", "600519"):
        assert is_valid_ticker_input(t)


def test_market_ask_supported_symbol():
    from app.backend.services.market_ask import _supported_symbol
    assert _supported_symbol("600519.SS") == "600519.SS"
    assert _supported_symbol("600519.SH") == "600519.SS"
    assert _supported_symbol("000001.SS") is None            # index, not a company
    assert _supported_symbol("900901.SS") is None            # B-share
    assert _supported_symbol("9988.HK") == "09988.HK"


def test_market_ask_tier1_has_china_disclosure_sites():
    from app.backend.services.market_ask import TIER1_DOMAINS
    assert {"cninfo.com.cn", "sse.com.cn", "szse.cn", "csrc.gov.cn"} <= TIER1_DOMAINS


# ── market keys: each market owns its constants ─────────────────────────────

def test_resolve_market_and_benchmark():
    from src.data.sector_profiles import resolve_market
    from src.decisions.ratings import resolve_benchmark
    assert resolve_market("600519.SS") == "SHH" and resolve_market("300750.SZ") == "SHZ"
    capm = {"rf": 0.0159, "erp": 0.0441, "crp": 0.0}
    b = resolve_benchmark("300750.SZ", "Industrials", capm=capm)
    assert b["code"] == "CSI300" and b["expected_return"] == pytest.approx(0.06)


def test_valuation_constants_market_key_never_falls_to_us():
    from src.data import valuation_constants as vc
    assert vc.market_key("600900.SS") == "SHH"
    assert vc.market_key("000858.SZ") == "SHZ"
    # No SHH row exists: the P/Rate Base leg declines rather than taking the US Treasury-based 7.25%.
    assert vc.cost_of_equity("Regulated Utility", vc.market_key("600900.SS"), cash_flow_ccy="CNY") is None
    assert vc.cost_of_equity("Regulated Utility", "US") == pytest.approx(0.0725)


def test_industry_map_market_of_cn_has_no_borrowed_table():
    from src.data.industry_profile_map import market_map, market_of
    assert market_of("600519.SS") == "CN" and market_of("000858.SZ") == "CN"
    assert market_map("CN") == {}


def test_capm_tax_and_rf_proposal_is_inert(monkeypatch):
    from src.data import capm, valuation_constants as vc
    assert capm._TAX["CN"] == 0.25
    doc = vc.load()
    rb = doc["risk_free_by_market"]
    if "CN" not in rb["markets"]:
        # PROPOSED only: the engine must not read it.
        assert rb["proposed"]["CN"]["status"] == "PROPOSED"
        monkeypatch.setenv("CAPM_LIVE_RF", "off")
        rf, src = capm.risk_free("CN")
        assert rf == capm.RF_FALLBACK and "owner-set" not in src
    # Once accepted (moved into markets), the CGB rate and its floor apply.
    accepted = json.loads(json.dumps(doc))
    accepted["risk_free_by_market"]["markets"]["CN"] = rb.get("proposed", {}).get("CN") or rb["markets"]["CN"]
    monkeypatch.setattr(vc, "load", lambda *a, **k: accepted)
    rf, src = capm.risk_free("CN")
    assert rf == pytest.approx(0.0159) and "owner-set" in src
    assert capm.local_min_spread("CN") == pytest.approx(0.03)
    w = capm.firm_wacc(profile="Food & Beverage", net_debt=0.0, market_cap=1e12, cost_of_debt=None, market="CN",
                       own_beta=0.4)
    assert w["rf"] == pytest.approx(0.0159) and w["tax_rate"] == 0.25
    assert w["cost_of_equity"] >= 0.0159 + 0.03 - 1e-9          # floored on the local curve


def test_listing_currency_default_is_cny(monkeypatch):
    from src.tools import api
    monkeypatch.setattr(api, "_fmp_get", lambda *a, **k: [])
    api._LISTING_CCY_CACHE.pop("601318.SS", None)
    assert api.get_listing_currency("601318.SS") == "CNY"
    api._LISTING_CCY_CACHE.pop("601318.SS", None)


# ── coverage / billing / pins ───────────────────────────────────────────────

def test_a_shares_are_limited_until_a_pin_is_accepted(monkeypatch):
    from src.data import coverage
    from src.data.sector_profiles import get_wacc_profile_for_ticker
    from app.backend.services import billing
    assert china.CN_TICKER_SECTOR_LOOKUP == {}              # proposals are inert
    assert coverage.coverage("600519")["level"] == "limited"
    assert billing.run_type("600519.SS", covered=False) == "limited"
    assert get_wacc_profile_for_ticker("600519.SS") == ("Tech", "")
    monkeypatch.setitem(china.CN_TICKER_SECTOR_LOOKUP, "600519.SS", ("Consumer", "Food & Beverage", "", "test"))
    assert coverage.is_tuned("600519") is True
    assert billing.run_type("600519.SS", covered=False) == "new"
    assert get_wacc_profile_for_ticker("600519.SS") == ("Consumer", "Food & Beverage")
    assert billing.run_type("600519.SS", covered=True) == "covered"


def test_proposed_pins_resolve_and_match_the_h_share_pins():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES, TICKER_SECTOR_LOOKUP
    assert len(china.PROPOSED_PROFILE_PINS) >= 30
    for t, (sector, profile, basis) in china.PROPOSED_PROFILE_PINS.items():
        assert china.normalize(t) == t
        assert INDUSTRY_VALUATION_PROFILES.get(sector, {}).get(profile), (t, sector, profile)
        if basis.startswith("H pin"):
            h = basis.split()[2]
            assert china.h_share_for(t) == h
            assert TICKER_SECTOR_LOOKUP[h][:2] == (sector, profile), (t, h)
    assert not set(china.PROPOSED_PROFILE_PINS) & set(china.NO_PIN_PROPOSED)


# ── A/H dual listings ───────────────────────────────────────────────────────

def test_ah_pairs_roundtrip_and_premium():
    for a, meta in china.TOP_A_SHARES.items():
        assert china.normalize(a) == a
        if meta["h"]:
            assert meta["h"].endswith(".HK") and len(meta["h"]) == 8
            assert china.a_share_for(meta["h"]) == a
    assert china.h_share_for("601318") == "02318.HK"
    assert china.a_share_for("2318") == "601318.SS"
    assert china.h_share_for("600519.SS") is None
    # China Life 2026-11-27: A RMB 35.90, H HK$23.26, CNYHKD 1.17855 -> +82% A premium.
    assert china.ah_premium(35.90, 23.26, 1 / 1.17855) == pytest.approx(0.819, abs=0.002)
    assert china.ah_premium(0, 23.26, 0.85) is None


def test_research_context_names_sources_and_the_ah_rule():
    ctx = china.research_context("601318.SS", "Ping An Insurance")
    assert "cninfo.com.cn" in ctx and "年度报告" in ctx and "中国平安" in ctx
    assert "02318.HK" in ctx and "never quote or value this A-share off the H-share price" in ctx
    assert "A/H" not in china.research_context("600519.SS")
    assert "local SOE" in china.research_context("600519.SS")


def test_search_local_finds_chinese_names_and_codes():
    assert china.search_local("贵州茅台")[0]["ticker"] == "600519.SS"
    assert china.search_local("宁德")[0]["ticker"] == "300750.SZ"
    assert china.search_local("moutai")[0]["ticker"] == "600519.SS"
    assert china.search_local("SZ300750")[0]["ticker"] == "300750.SZ"
    assert china.search_local("603999")[0] == {"ticker": "603999.SS", "name": "603999.SS", "exchange": "SHH",
                                                 "type": "equity"}
    assert china.search_local("ab") == []


def test_statutory_deadlines():
    assert china.next_statutory_deadline(date(2026, 11, 28)) == ("FY2026 annual report", date(2027, 4, 30))
    assert china.next_statutory_deadline(date(2026, 5, 2)) == ("H1 2026 interim report", date(2026, 8, 31))


# ── consensus target (stockanalysis.com) ────────────────────────────────────

def test_target_price_routes_and_parses_yuan(monkeypatch, tmp_path):
    from src.tools import target_price as tp
    assert tp.quote_path("600519.SS") == ("sha/600519", "CNY")
    assert tp.quote_path("300750.SZ") == ("she/300750", "CNY")
    monkeypatch.setattr(tp, "_CACHE_DIR", tmp_path)
    body = ('According to 23 analysts polled by S&amp;P Global, Kweichow Moutai stock has a consensus rating of '
            '"Strong Buy" and an average price target of ¥1,705. The lowest is ¥1,320 and the highest is ¥2,100. '
            'The forecast is 15.80% higher than the current stock price. Currency is CNY')

    class _R:
        status_code = 200
        content = body.encode("utf-8")       # served without a charset: must be decoded as UTF-8
        text = body.encode("utf-8").decode("latin-1")
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    out = tp.get_consensus_target("600519.SS", refresh=True)
    assert out["target"] == 1705.0 and out["low"] == 1320.0 and out["high"] == 2100.0
    assert out["analysts"] == 23 and out["currency"] == "CNY" and not out["suspect"]


def test_consensus_at_run_defers_for_a_shares():
    from src.agents.analysis.dcf_agent import _consensus_at_run
    assert _consensus_at_run("600519.SS", None)["status"] == "deferred"


# ── cninfo ──────────────────────────────────────────────────────────────────

def _ms(day: str) -> int:
    from datetime import datetime, timedelta, timezone
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


@pytest.fixture
def fake_cninfo(monkeypatch):
    from src.tools import cninfo_api as c
    c._ORG_CACHE.clear()
    c._ANN_CACHE.clear()
    anns = [
        {"announcementTitle": "中国平安2026年第三季度报告", "announcementTime": _ms("2026-10-29"),
         "adjunctUrl": "finalpage/2026-10-29/1.PDF"},
        {"announcementTitle": "中国平安：平安银行股份有限公司2026年第三季度报告", "announcementTime": _ms("2026-10-24"),
         "adjunctUrl": "finalpage/2026-10-24/2.PDF"},
        {"announcementTitle": "中国平安2026年中期报告", "announcementTime": _ms("2026-08-21"),
         "adjunctUrl": "finalpage/2026-08-21/3.PDF"},
        {"announcementTitle": "中国平安2026年中期报告摘要", "announcementTime": _ms("2026-08-21"),
         "adjunctUrl": "finalpage/2026-08-21/4.PDF"},
        {"announcementTitle": "中国平安2025年年度报告", "announcementTime": _ms("2026-03-27"),
         "adjunctUrl": "finalpage/2026-03-27/5.PDF"},
        {"announcementTitle": "中国平安2025年年度报告（英文版）", "announcementTime": _ms("2026-03-27"),
         "adjunctUrl": "finalpage/2026-03-27/6.PDF"},
    ]
    calls = []

    def _post(path, data):
        calls.append((path, data))
        if "topSearch" in path:
            return [{"code": data["keyWord"], "orgId": "9900002221", "zwjc": "中国平安", "category": "A股"}]
        return {"announcements": anns}
    monkeypatch.setattr(c, "_post", _post)
    return calls


def test_cninfo_periodic_reports_filter_and_beijing_dates(fake_cninfo):
    from src.tools import cninfo_api as c
    rows = c.periodic_reports("601318.SS", today=date(2026, 11, 28))
    assert [(r["kind"], r["date"]) for r in rows] == [
        ("q3", "2026-10-29"), ("interim", "2026-08-21"), ("annual", "2026-03-27")]
    assert rows[0]["url"] == "https://static.cninfo.com.cn/finalpage/2026-10-29/1.PDF"
    q = [d for p, d in fake_cninfo if "hisAnnouncement" in p][0]
    assert q["stock"] == "601318,9900002221" and q["column"] == "sse" and q["plate"] == "sh"
    assert c.latest_report_date("601318.SS", today=date(2026, 11, 28)) == "2026-10-29"


def test_cninfo_filing_refs_shape(fake_cninfo):
    from src.tools import cninfo_api as c
    ref = c.get_cninfo_filing_refs("601318")
    assert ref["exchange"] == "CNINFO" and ref["fiscal_year"] == "2025" and ref["filing_date"] == "2026-03-27"
    assert ref["filing_url"].endswith("/5.PDF") and ref["period_of_report"] == "2025-12-31"
    assert ref["is_stub"] is False and ref["cik"] is None
    assert c.get_cninfo_filing_refs("AAPL") == {}


def test_cninfo_transport_failure_is_soft(monkeypatch):
    from src.tools import cninfo_api as c
    c._ORG_CACHE.clear()
    c._ANN_CACHE.clear()
    monkeypatch.setattr(c, "_post", lambda *a, **k: None)
    assert c.get_cninfo_filing_refs("600519.SS") == {}
    assert c.latest_report_date("600519.SS") is None
    assert "600519" not in c._ORG_CACHE            # a failure is never cached


def test_resolver_routes_a_shares_to_cninfo(monkeypatch):
    from src.agents.industry import edgar_hkex_resolver as r
    monkeypatch.setattr(r, "get_cninfo_filing_refs", lambda t: {"exchange": "CNINFO", "filing_type": "AR",
                                                                "filing_date": "2026-04-17", "fiscal_year": "2025",
                                                                "filing_url": "u"})
    monkeypatch.setattr(r, "get_edgar_filing_refs", lambda t: pytest.fail("EDGAR must not be queried for an A-share"))
    st = r.run_edgar_hkex_resolver({"data": {"tickers": ["600519.SS"]}})
    assert st["data"]["edgar_filing_refs"]["600519.SS"]["exchange"] == "CNINFO"


def test_latest_earnings_event_reads_cninfo(monkeypatch):
    from src.memory import assumption_extract as ae
    from src.tools import api, cninfo_api, fmp_transcripts
    monkeypatch.setattr(api, "get_earnings_surprises", lambda *a, **k: [{"date": "2026-04-24"}])
    monkeypatch.setattr(fmp_transcripts, "get_transcript_dates", lambda t: [])
    monkeypatch.setattr(cninfo_api, "latest_report_date", lambda t, today=None: "2026-10-31")
    assert ae.latest_earnings_event("600519.SS") == "2026-10-31"


def test_earnings_calendar_falls_back_to_per_symbol_rows(monkeypatch):
    from src.tools import earnings_calendar as ec
    monkeypatch.setenv("FMP_API_KEY", "test")
    monkeypatch.setattr(ec, "_window", lambda start, days: {"AAPL": "2026-12-01"})

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return [{"date": "2027-04-01", "epsActual": None}, {"date": "2026-12-05", "epsActual": None},
                    {"date": "2026-10-30", "epsActual": 15.96}]
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    assert ec.next_earnings_date("600519.SS", days=21, today=date(2026, 11, 28)) == "2026-12-05"
    assert ec.next_earnings_date("600519.SS", days=3, today=date(2026, 11, 28)) is None
    assert ec.next_earnings_date("AAPL", days=21, today=date(2026, 11, 28)) == "2026-12-01"


# ── PDF: Chinese names render ───────────────────────────────────────────────

def test_cjk_wrap_and_pdf_render():
    from src.utils import cjk_font
    assert cjk_font.has_cjk("贵州茅台") and not cjk_font.has_cjk("Kweichow Moutai")
    assert cjk_font.wrap_cjk("Kweichow Moutai") == "Kweichow Moutai"
    name = cjk_font.font_name()
    assert name
    w = cjk_font.wrap_cjk("Kweichow Moutai 贵州茅台 (600519.SS)")
    assert w == f'Kweichow Moutai <font name="{name}">贵州茅台</font> (600519.SS)'

    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate
    from src.utils import pdf_report
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf)
    doc.build([pdf_report.Paragraph("<b>Kweichow Moutai 贵州茅台</b> (600519.SS) &amp; RMB 1,472.36",
                                    getSampleStyleSheet()["Normal"])])
    pdf = buf.getvalue()
    assert pdf.startswith(b"%PDF") and len(pdf) > 500
    font_tag = b"STSong" if name == "STSong-Light" else b"+"       # an embedded TTF subset is named ABCDEF+Font
    assert font_tag in pdf


def test_pdf_price_currency_symbol_is_rmb():
    from src.utils import pdf_report
    pdf_report._set_price_currency("CNY")
    try:
        assert pdf_report._cs() == "RMB "
    finally:
        pdf_report._set_price_currency("USD")


def test_cjk_cid_fallback_when_no_ttf_on_host(monkeypatch):
    """Production (Linux image without a CJK font) takes ReportLab's built-in STSong-Light."""
    from src.utils import cjk_font
    saved = dict(cjk_font._STATE)
    monkeypatch.setattr(cjk_font, "_CANDIDATES", ())
    monkeypatch.delenv("CJK_FONT_PATH", raising=False)
    cjk_font._STATE.update(name=None, source=None)
    try:
        assert cjk_font.font_name() == "STSong-Light"
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate
        from src.utils import pdf_report
        buf = io.BytesIO()
        SimpleDocTemplate(buf).build([pdf_report.Paragraph("中国平安 Ping An", getSampleStyleSheet()["Normal"])])
        assert b"STSong-Light" in buf.getvalue()
    finally:
        cjk_font._STATE.clear()
        cjk_font._STATE.update(saved)

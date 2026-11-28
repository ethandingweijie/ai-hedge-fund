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
    ("SH600519", "600519.SS"), ("sh.600519", "600519.SS"),
    ("688981.SS", "688981.SS"), ("SH605499", "605499.SS"),
    ("SZ000858", "000858.SZ"), ("000858.SHZ", "000858.SZ"), ("300750.SZ", "300750.SZ"),
    ("SZ301269", "301269.SZ"), ("002594.SZ", "002594.SZ"), ("003816.sz", "003816.SZ"),
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
    # Owner, 2026-11-28 (D-CN-1): bare six digits are ambiguous with Korea and are never canonicalised.
    "600519", "000858", "300750", "000660",
    "60051", "6005190", "", None, "AAPL", "0700.HK", "D05.SI",
])
def test_normalize_rejects(raw):
    assert china.normalize(raw) is None


def test_canonical_ticker_routes_a_shares_and_leaves_hk_sg_alone():
    from src.tools.ticker_canonical import canonical_ticker, ticker_match_forms
    assert canonical_ticker("600519") == "600519"            # bare digits stay as typed (D-CN-1)
    assert canonical_ticker("000660") == "000660"            # SK Hynix's KRX code, not Shenzhen
    assert canonical_ticker("SH600519") == "600519.SS"
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
    assert china.exchange_code("600519.SS") == "SHH"
    assert china.exchange_code("SZ300750") == "SHZ"
    assert china.exchange_code("600519") is None
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
    capm = {"rf": 0.0159, "erp": 0.0441, "crp": 0.015}
    b = resolve_benchmark("300750.SZ", "Industrials", capm=capm)
    assert b["code"] == "CSI300" and b["expected_return"] == pytest.approx(0.075)
    # The fallback (no CAPM on the run) carries the same registry 1.5% (owner, 2026-11-28, D-CN-3/4).
    assert resolve_benchmark("600519.SS", "Consumer")["country_risk_premium"] == pytest.approx(0.015)


def test_a_share_crp_is_the_registry_premium():
    from src.data.sector_profiles import market_crp
    assert china.a_share_crp("600519.SS") == market_crp("SHH") == pytest.approx(0.015)
    assert china.a_share_crp("SZ300750") == market_crp("SHZ") == pytest.approx(0.015)
    assert china.a_share_crp("00700.HK") is None and china.a_share_crp("600519") is None


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


def test_capm_tax_and_accepted_cgb_rate():
    from src.data import capm, valuation_constants as vc
    assert capm._TAX["CN"] == 0.25
    cn = vc.load()["risk_free_by_market"]["markets"]["CN"]       # owner-accepted 2026-11-28 (D-CN-2)
    assert cn["value"] == 0.0159 and cn["min_spread_over_rf"] == 0.03 and cn["reviewer"] == "owner"
    rf, src = capm.risk_free("CN")
    assert rf == pytest.approx(0.0159) and "owner-set" in src
    assert capm.local_min_spread("CN") == pytest.approx(0.03)
    w = capm.firm_wacc(profile="Food & Beverage", net_debt=0.0, market_cap=1e12, cost_of_debt=None, market="CN",
                       own_beta=0.4, crp=0.015)
    assert w["rf"] == pytest.approx(0.0159) and w["tax_rate"] == 0.25 and w["crp"] == pytest.approx(0.015)
    assert w["cost_of_equity"] >= 0.0159 + 0.03 - 1e-9          # floored on the local curve


def test_listing_currency_default_is_cny(monkeypatch):
    from src.tools import api
    monkeypatch.setattr(api, "_fmp_get", lambda *a, **k: [])
    api._LISTING_CCY_CACHE.pop("601318.SS", None)
    assert api.get_listing_currency("601318.SS") == "CNY"
    api._LISTING_CCY_CACHE.pop("601318.SS", None)


# ── coverage / billing / pins ───────────────────────────────────────────────

def test_accepted_pins_make_a_shares_tuned():
    from src.data import coverage
    from src.data.sector_profiles import get_wacc_profile_for_ticker
    from app.backend.services import billing
    assert len(china.CN_TICKER_SECTOR_LOOKUP) == len(china.CN_PROFILE_PINS) == 46   # owner-accepted 2026-11-28
    assert coverage.coverage("600519.SS")["level"] == "tuned"
    assert coverage.is_tuned("SH600519") is True
    assert billing.run_type("600519.SS", covered=False) == "new"
    assert billing.run_type("600519.SS", covered=True) == "covered"
    assert get_wacc_profile_for_ticker("600519.SS") == ("Consumer", "Food & Beverage")
    assert get_wacc_profile_for_ticker("601318.SS") == ("Financials", "Insurance")      # life insurer (H pin)
    # An unpinned A-share is limited coverage.
    assert coverage.coverage("002415.SZ")["level"] == "limited"
    assert billing.run_type("002415.SZ", covered=False) == "limited"


def test_pins_resolve_and_match_the_h_share_pins():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES, TICKER_SECTOR_LOOKUP
    for t, (sector, profile, basis) in china.CN_PROFILE_PINS.items():
        assert china.normalize(t) == t
        assert INDUSTRY_VALUATION_PROFILES.get(sector, {}).get(profile), (t, sector, profile)
        if basis.startswith("H pin"):
            h = basis.split()[2]
            assert china.h_share_for(t) == h
            assert TICKER_SECTOR_LOOKUP[h][:2] == (sector, profile), (t, h)
    assert not set(china.CN_PROFILE_PINS) & set(china.NO_PIN_PROPOSED)


def test_life_insurer_without_ev_input_is_withheld():
    why = china.ev_input_withhold_reason("601318.SS", "Insurance", embedded_value_computed=False)
    assert why and "embedded value" in why and "02318.HK" in why
    assert china.ev_input_withhold_reason("601318.SS", "Insurance", embedded_value_computed=True) is None
    assert china.ev_input_withhold_reason("601318.SS", "Insurance (P&C)", embedded_value_computed=False) is None
    assert china.ev_input_withhold_reason("02318.HK", "Insurance", embedded_value_computed=False) is None
    from src.data import valuation_constants as vc
    assert vc.UNRATED_REASONS["input_required"].startswith("Unrated")
    base = {"effective_weights": [{"method": "P/BV", "weight": 0.53}, {"method": "DDM", "weight": 0.06}]}
    v = china.ev_withhold_verdict("601318.SS", "Insurance", base)
    assert v["code"] == "input_required" and "601318.SS" in v["reason"]
    base["effective_weights"].append({"method": "Embedded Value", "weight": 0.4})
    assert china.ev_withhold_verdict("601318.SS", "Insurance", base) is None
    assert china.ev_withhold_verdict("600519.SS", "Food & Beverage", {}) is None


# ── A/H dual listings ───────────────────────────────────────────────────────

def test_ah_pairs_roundtrip_and_premium():
    for a, meta in china.TOP_A_SHARES.items():
        assert china.normalize(a) == a
        if meta["h"]:
            assert meta["h"].endswith(".HK") and len(meta["h"]) == 8
            assert china.a_share_for(meta["h"]) == a
    assert china.h_share_for("601318.SS") == "02318.HK"
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
    # A bare code is OFFERED as a pick-list candidate, never canonicalised (D-CN-1).
    assert china.search_local("603999")[0] == {"ticker": "603999.SS", "name": "603999.SS", "exchange": "SHH",
                                                 "type": "equity"}
    assert china.search_local("600519")[0]["ticker"] == "600519.SS"
    assert china.bare_code_candidate("600519") == "600519.SS" and china.bare_code_candidate("005930") is None
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
    ref = c.get_cninfo_filing_refs("SH601318")
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


# ── China ERP (owner, 2026-11-28) ───────────────────────────────────────────

def test_china_erp_is_the_implied_premium_net_of_crp():
    from src.data import capm, valuation_constants as vc
    cn = vc.load()["equity_risk_premium_by_market"]["markets"]["CN"]
    assert cn["reviewer"] == "owner" and cn["accepted"] == "2026-11-28"
    inp = cn["inputs"]
    import statistics
    med = statistics.median(inp["sector_pe_ntm"].values())
    assert med == pytest.approx(inp["median_pe_ntm"], abs=0.01)
    gross = 1.0 / med - inp["rf"]
    assert gross == pytest.approx(cn["gross_implied_premium"], abs=5e-4)
    assert cn["value"] == pytest.approx(cn["gross_implied_premium"] - cn["crp_netted"], abs=1e-9)   # no double count
    from src.data.sector_profiles import market_crp
    assert cn["crp_netted"] == market_crp("SHH") == market_crp("SHZ")
    assert capm.equity_risk_premium("CN")[0] == pytest.approx(0.0432)
    assert capm.equity_risk_premium("US")[0] == capm.ERP == capm.equity_risk_premium("SG")[0]


def test_china_erp_in_firm_capm_and_benchmark():
    from src.data import capm
    from src.decisions.ratings import resolve_benchmark
    w = capm.firm_wacc(profile="Food & Beverage", net_debt=0.0, market_cap=1e12, cost_of_debt=None, market="CN",
                       own_beta=1.5, crp=0.015)
    assert w["erp"] == pytest.approx(0.0432) and "CN ERP" in w["erp_source"]
    assert w["cost_of_equity"] == pytest.approx(0.0159 + w["beta_levered"] * 0.0432 + 0.015, abs=1e-5)
    us = capm.firm_wacc(profile="Food & Beverage", net_debt=0.0, market_cap=1e12, cost_of_debt=None, market="US",
                        own_beta=1.5)
    assert us["erp"] == capm.ERP
    # Fallback benchmark (no CAPM on the run) uses the CGB rf, China ERP and registry CRP: 1.59 + 4.32 + 1.50.
    assert resolve_benchmark("600519.SS", "Consumer")["expected_return"] == pytest.approx(0.0741)
    # With the run's CAPM inputs the benchmark follows them.
    b = resolve_benchmark("300750.SZ", "Industrials", capm={"rf": 0.0159, "erp": 0.0432, "crp": 0.015})
    assert b["expected_return"] == pytest.approx(0.0741)


# ── H-share company inputs reused by the A line (owner, 2026-11-28) ─────────

@pytest.fixture
def input_store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from src.data import db as _db
    from src.data import industry_inputs as ii
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(ii, "_reviews_ready_key", None)
    yield ii
    _db.close_all_connections()


def _ev_doc(ccy="CNY", evps=86.58):
    return {"version": 1, "tickers": {"02318.HK": {"embedded_value": {
        "data": {"ev_per_share": {"value": evps, "currency": ccy, "scale": "units", "period": "1H2026",
                                  "source_url": "https://www.pingan.cn/x", "quote": "EV per share RMB 86.58"},
                 "ev_total": {"value": 1567800.0, "currency": ccy, "scale": "mn", "period": "1H2026"},
                 "fiscal_year": "1H2026", "basis": "Traditional EV"},
        "checks": [], "ok": True}}}}


def test_a_share_reuses_the_h_share_accepted_input_with_disclosure(input_store):
    ii = input_store
    doc = _ev_doc()
    assert ii.accepted_entry("601318.SS", "embedded_value", doc) is None          # H pending -> nothing to reuse
    ii.set_review("02318.HK", "embedded_value", "accepted", "owner", doc=doc)
    e = ii.accepted_entry("601318.SS", "embedded_value", doc)
    assert e["_reused_from"]["listing"] == "02318.HK"
    assert ii.accepted_entry("02318.HK", "embedded_value", doc).get("_reused_from") is None
    cny = lambda c: {"CNY": 1.0, "HKD": 0.8485}.get(c)                             # noqa: E731
    d = china.reused_input_disclosure(e, "ev_per_share", "CNY", cny)
    assert d["source_listing"] == "02318.HK" and d["original_value"] == 86.58 and d["original_currency"] == "CNY"
    assert d["fx_rate"] == 1.0 and d["converted_value"] == pytest.approx(86.58) and d["period"] == "1H2026"
    assert "not the H-share price" in d["text"]
    det = ii.accepted_detail("601318.SS", "embedded_value", "CNY", doc=doc, fx=cny)
    assert det is None or det.get("reused_from") == "02318.HK"


def test_an_hkd_input_is_converted_at_the_run_fx(input_store):
    ii = input_store
    doc = _ev_doc(ccy="HKD", evps=100.0)
    ii.set_review("02318.HK", "embedded_value", "accepted", "owner", doc=doc)
    e = ii.accepted_entry("601318.SS", "embedded_value", doc)
    from src.agents.industry import gemini_params as gp
    cny = lambda c: {"CNY": 1.0, "HKD": 0.8485}.get(c)                             # noqa: E731
    assert gp.embedded_value_to_engine(ii.canonical_data(e), cny)["ev_per_share"] == pytest.approx(84.85)
    d = china.reused_input_disclosure(e, "ev_per_share", "CNY", cny)
    assert d["original_currency"] == "HKD" and d["fx_rate"] == 0.8485 and d["converted_value"] == pytest.approx(84.85)


def test_no_reuse_outside_a_shares_or_without_an_h_line(input_store):
    ii = input_store
    doc = _ev_doc()
    ii.set_review("02318.HK", "embedded_value", "accepted", "owner", doc=doc)
    assert ii.accepted_entry("600519.SS", "embedded_value", doc) is None   # no H line
    assert ii.accepted_entry("601318", "embedded_value", doc) is None      # bare code is not an A-share


def test_a_share_insurer_cohort_is_measured_on_a_share_lines():
    from src.data import insurer_pev as ip
    assert ip.cohort_of("601318.SS") == "china_domestic"
    assert ip.members_for("601318.SS", "china_domestic") == ("601628.SS", "601601.SS", "601319.SS", "601336.SS")
    assert "601628.SS" not in ip.members_for("601628.SS", "china_domestic")      # subject excluded
    assert "601318.SS" in ip.members_for("601628.SS", "china_domestic")          # Ping An A is China Life A's peer
    assert ip.members_for("02318.HK", "china_domestic") == ("02628.HK", "02601.HK", "01339.HK", "01336.HK")
    out = ip.cohort_pev("601318.SS", quote=lambda s: {"601628.SS": 35.9, "601601.SS": 32.42, "601336.SS": 52.91}.get(s),
                        ev_ps=lambda s, c: {"601628.SS": 51.93, "601601.SS": 66.21, "601336.SS": 92.27}.get(s),
                        listing_ccy=lambda s: "CNY")
    assert [m["symbol"] for m in out["members_used"]] == ["601628.SS", "601601.SS", "601336.SS"]
    assert out["value"] == pytest.approx(statistics_median([35.9 / 51.93, 32.42 / 66.21, 52.91 / 92.27]), abs=1e-4)


def statistics_median(xs):
    import statistics
    return statistics.median(xs)


# ── A+H share count (lead's input-data audit, 2026-11-28) ───────────────────

@pytest.mark.parametrize("line", [
    "600941.SS", "00941.HK", "0941.HK", "600938.SS", "00883.HK", "688981.SS", "00981.HK", "601939.SS", "00939.HK",
    "601288.SS", "01288.HK", "601318.SS", "02318.HK", "601658.SS", "01658.HK", "688347.SS", "01347.HK",
    "688235.SS", "06160.HK", "601319.SS", "01339.HK",
])
def test_both_lines_of_an_ah_issuer_are_dual_listed(line):
    assert china.is_ah_dual_listed(line)


@pytest.mark.parametrize("line", ["600519.SS", "000858.SZ", "00700.HK", "09988.HK", "AAPL", "600519"])
def test_single_listings_are_not_ah(line):
    assert not china.is_ah_dual_listed(line)


@pytest.mark.parametrize("mcap_over_price,total,bias", [
    # audit figures: company-level marketCap / the A price understates the count when A trades at a premium
    (13_942_208_433, 21_751_931_000, 0.56),     # 600941.SS China Mobile
    (34_566_182_777, 47_529_954_000, 0.37),     # 600938.SS CNOOC
    (5_761_147_859, 8_009_003_000, 0.39),       # 688981.SS SMIC
    (214_960_191_410, 256_107_000_000, 0.19),   # 601939.SS CCB
    (319_244_000_000, 373_129_487_000, 0.17),   # 601288.SS ABC
])
def test_quote_implied_count_is_biased_for_ah_issuers(mcap_over_price, total, bias):
    assert total / mcap_over_price - 1.0 == pytest.approx(bias, abs=0.01)


def test_dcf_keeps_the_total_count_for_ah_lines():
    import inspect
    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    i, j = src.index("_ah_dual(ticker)"), src.index("_shares_implied = _quote_mcap / _spot_price")
    assert i < j                                         # the A+H rule runs before the quote cross-check
    assert china.AH_SHARES_SOURCE == "ah_total_shares"

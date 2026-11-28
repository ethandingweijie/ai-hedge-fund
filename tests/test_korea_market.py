"""Korea (KOSPI .KS / KOSDAQ .KQ) end-to-end rules -- src/markets/korea.py and every shared hook it touches."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from src.markets import korea

ROOT = Path(__file__).resolve().parents[1]


# ── ticker forms ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,canon", [
    ("005930.KS", "005930.KS"), ("005930.ks", "005930.KS"), ("5930.KS", "005930.KS"),
    ("KRX:005930", "005930.KS"), ("kospi:005930", "005930.KS"), ("KS:000660", "000660.KS"),
    ("KOSDAQ:247540", "247540.KQ"), ("KQ:196170", "196170.KQ"), ("KRX:247540", "247540.KQ"),
    ("247540.KQ", "247540.KQ"), ("005930.KQ", "005930.KS"),        # a known code is on its own board
    ("005930", "005930.KS"), ("000660", "000660.KS"), ("005935", "005935.KS"),
    ("123456.KQ", "123456.KQ"), ("KRX:123456", "123456.KS"),
])
def test_to_canonical(raw, canon):
    assert korea.to_canonical(raw) == canon


@pytest.mark.parametrize("raw", ["600519", "000001", "123456", "AAPL", "00700", "0700.HK", "D05.SI", "", None,
                                 "005930.HK", "KRX:", "ABCDEF"])
def test_not_korean(raw):
    """A bare 6-digit code that the registry does not know may be a China A-share: it is left alone."""
    assert korea.to_canonical(raw) is None
    assert not korea.is_korea_ticker(raw)


def test_preferred_lines():
    assert korea.is_preferred("005935.KS") and not korea.is_preferred("005930.KS")
    assert korea.common_of("005935.KS") == "005930.KS"
    assert korea.common_of("005385.KS") == "005380.KS"
    assert korea.common_of("00088K.KS") == "000880.KS"           # new-type preferred (K)
    assert korea.common_of("005930.KS") == "005930.KS"
    note = korea.preferred_note("005935.KS", 145000, 179100)
    assert "005930.KS" in note and "19% discount" in note
    assert korea.preferred_note("005930.KS") is None


def test_board_currency_market():
    assert korea.board_of("247540.KQ") == "KOSDAQ" and korea.board_of("005930.KS") == "KOSPI"
    assert korea.listing_currency("005930.KS") == "KRW" and korea.listing_currency("AAPL") is None
    assert korea.capm_market("000660.KS") == "KR" and korea.capm_market("0700.HK") is None


def test_canonical_ticker_integration():
    from src.tools.ticker_canonical import canonical_ticker, ticker_match_forms
    assert canonical_ticker("KRX:005930") == "005930.KS"
    assert canonical_ticker("005930") == "005930.KS"
    assert canonical_ticker("600519") == "600519"                 # unknown 6-digit: untouched
    assert canonical_ticker("2888") == "02888.HK"                 # HK unchanged
    assert canonical_ticker("d05") == "D05.SI"                    # SG unchanged
    assert ticker_match_forms("005930") == ["005930", "005930.KS"]


@pytest.mark.parametrize("t", ["005930.KS", "247540.KQ", "KRX:005930", "KOSDAQ:247540", "00088K.KS"])
def test_request_guard_accepts_korean_forms(t):
    from app.backend.request_guards import is_valid_ticker_input
    assert is_valid_ticker_input(t)


@pytest.mark.parametrize("t", ["KRX:005930:X", "KRX::005930", "K:005930", "../KRX:1", "KRX:005930.KS.X"])
def test_request_guard_still_rejects(t):
    from app.backend.request_guards import is_valid_ticker_input
    assert not is_valid_ticker_input(t)


def test_market_ask_supported_symbol():
    from app.backend.services.market_ask import _supported_symbol
    assert _supported_symbol("005930.KS") == "005930.KS"
    assert _supported_symbol("247540.kq") == "247540.KQ"


# ── market registry / comps ─────────────────────────────────────────────────

def test_resolve_market_kosdaq():
    from src.data.sector_profiles import resolve_market
    assert resolve_market("005930.KS") == "KSC"
    assert resolve_market("247540.KQ") == "KSC"                 # was US before 2026-11-28
    assert resolve_market(exchange="KOE") == "KSC"


def test_kosdaq_reads_korean_comps_but_is_not_swept():
    from src.data import regional_comps as rc
    assert rc.market_for_exchange("KOE") == "KSC"
    assert rc.market_for_exchange("KSC") == "KSC"
    assert rc.MARKETS["KSC"] == ("KSC",)                         # pooling KOSDAQ into the refresh = owner decision
    assert "KOE" not in rc.EXCHANGES


def test_rating_benchmark_is_kospi_for_both_boards():
    from src.decisions.ratings import resolve_benchmark
    for t in ("005930.KS", "247540.KQ"):
        b = resolve_benchmark(t, "Industrials")
        assert b["code"] == "KOSPI" and b["market"] == "KSC"
    capm = {"rf": 0.0378, "erp": 0.0441, "crp": 0.007}
    assert resolve_benchmark("005930.KS", "Tech", capm)["expected_return"] == pytest.approx(0.0889, abs=1e-4)


def test_valuation_constants_market_key():
    from src.data import valuation_constants as vc
    assert vc.market_key("015760.KS") == "KSC" and vc.market_key("247540.KQ") == "KSC"
    assert vc.market_key("D05.SI") == "SES" and vc.market_key("AAPL") == "US"


def test_holdco_currency_kosdaq():
    from src.agents.analysis.holdco_sotp import currency_of
    assert currency_of("247540.KQ") == "KRW" and currency_of("005930.KS") == "KRW"


# ── risk-free: PROPOSED, never applied silently ─────────────────────────────

def test_kr_rf_is_proposed_not_applied(monkeypatch):
    from src.data import capm, valuation_constants as vc
    doc = vc.load()
    prop = doc["risk_free_by_market"]["proposed"]["KR"]
    assert prop["status"] == "PROPOSED" and 0.02 < prop["value"] < 0.06
    assert "KR" not in doc["risk_free_by_market"]["markets"]
    monkeypatch.setenv("CAPM_LIVE_RF", "off")
    assert capm.risk_free("KR") == capm.risk_free("US")         # unaccepted: priced exactly as today
    assert capm.local_min_spread("KR") is None


def test_rf_status_guard(monkeypatch):
    """A row under `markets` marked PROPOSED is ignored too (belt and braces for a hand edit)."""
    from src.data import capm, valuation_constants as vc
    monkeypatch.setenv("CAPM_LIVE_RF", "off")
    fake = {"risk_free_by_market": {"markets": {
        "KR": {"value": 0.0378, "basis": "KTB 10y", "source": "t", "status": "PROPOSED", "min_spread_over_rf": 0.03},
        "XX": {"value": 0.03, "basis": "b", "source": "s", "min_spread_over_rf": 0.02}}}}
    monkeypatch.setattr(vc, "load", lambda *a, **k: fake)
    assert capm.risk_free("KR")[0] == capm.RF_FALLBACK
    assert capm.local_min_spread("KR") is None
    assert capm.risk_free("XX")[0] == 0.03 and capm.local_min_spread("XX") == 0.02


def test_capm_market_code():
    from src.data.capm import market_code
    assert market_code("005930.KS") == "KR" and market_code("247540.KQ", "US") == "KR"
    assert market_code("AAPL") == "US" and market_code("0700.HK", "HK") == "HK" and market_code("D05.SI", "SG") == "SG"


# ── consensus target (stockanalysis.com) ────────────────────────────────────

def test_target_price_routes_korea():
    from src.tools.target_price import quote_path
    assert quote_path("005930.KS") == ("krx/005930", "KRW")
    assert quote_path("247540.KQ") == ("kosdaq/247540", "KRW")


def test_target_price_parses_won(monkeypatch, tmp_path):
    from src.tools import target_price as tp
    monkeypatch.setattr(tp, "_CACHE_DIR", tmp_path)
    body = ("<p>According to 29 analysts polled by S&amp;P Global, NAVER stock has a consensus rating of \"Buy\" and an "
            "average price target of ₩273,552. The lowest is ₩190,000 and the highest is ₩390,000. The "
            "forecast is 67.41% higher than the current stock price.</p> Currency is KRW")

    class R:
        status_code, encoding = 200, "ISO-8859-1"

        @property
        def text(self):                       # served as UTF-8 bytes; decoded with whatever encoding is set
            return body.encode("utf-8").decode(self.encoding)

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    out = tp.get_consensus_target("035420.KS", refresh=True)
    assert out["target"] == 273552 and out["low"] == 190000 and out["high"] == 390000
    assert out["analysts"] == 29 and out["currency"] == "KRW" and not out["suspect"]


def test_outcome_labels_consult_korean_consensus():
    src = (ROOT / "src/memory/valuation_outcomes.py").read_text(encoding="utf-8")
    assert '".HK", ".SI", ".KS", ".KQ"' in src
    from src.memory.valuation_outcomes import market_of
    assert market_of("005930.KS") == "KR" and market_of("247540.KQ") == "KR"


# ── earnings calendar: truncated bulk window ────────────────────────────────

def test_earnings_truncated_window_falls_back_to_symbol(monkeypatch):
    from src.tools import earnings_calendar as ec
    full = {f"S{i}.KS": "2026-11-13" for i in range(3990)}
    monkeypatch.setattr(ec, "_window", lambda start, days: full)
    monkeypatch.setattr(ec, "_symbol_dates", lambda sym, today: ["2026-07-29", "2026-10-29", "2027-02-04"])
    assert ec.next_earnings_date("005930.KS", days=21, today=date(2026, 10, 15)) == "2026-10-29"
    assert ec.next_earnings_date("005930.KS", days=5, today=date(2026, 10, 15)) is None


def test_earnings_untruncated_window_is_trusted(monkeypatch):
    from src.tools import earnings_calendar as ec
    monkeypatch.setattr(ec, "_window", lambda start, days: {"AAPL": "2026-10-30"})
    monkeypatch.setattr(ec, "_symbol_dates", lambda *a: pytest.fail("no per-symbol call on a complete window"))
    assert ec.next_earnings_date("005930.KS", today=date(2026, 10, 15)) is None
    assert ec.next_earnings_date("AAPL", today=date(2026, 10, 15)) == "2026-10-30"


# ── DART resolver ───────────────────────────────────────────────────────────

_CORP_XML = ("<?xml version='1.0' encoding='UTF-8'?><result>"
             "<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>"
             "<list><corp_code>99999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>"
             "</result>").encode("utf-8")
_LIST = {"status": "000", "list": [
    {"corp_name": "삼성전자", "report_nm": "사업보고서 (2024.12)", "rcept_no": "20250311001085", "rcept_dt": "20250311"},
    {"corp_name": "삼성전자", "report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_no": "20260320000123",
     "rcept_dt": "20260320"},
    {"corp_name": "삼성전자", "report_nm": "분기보고서 (2026.09)", "rcept_no": "20261114000001", "rcept_dt": "20261114"},
]}


def test_dart_parsers():
    from src.markets import korea_dart as kd
    codes = kd.parse_corp_codes(_CORP_XML)
    assert codes == {"005930": {"corp_code": "00126380", "corp_name": "삼성전자"}}
    row = kd.pick_annual_report(_LIST["list"])
    assert row["rcept_no"] == "20260320000123"                  # newest business report, quarterly ignored
    ref = kd.to_filing_ref(row, "005930.KS", codes["005930"])
    assert ref["exchange"] == "DART" and ref["fiscal_year"] == "2025" and ref["period_of_report"] == "2025-12"
    assert ref["filing_date"] == "2026-03-20" and ref["filing_url"].endswith("rcpNo=20260320000123")


def test_dart_without_key_is_a_clean_skip(monkeypatch):
    from src.markets import korea_dart as kd
    monkeypatch.delenv("DART_API_KEY", raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no network call without a key"))
    assert kd.get_dart_filing_refs("005930.KS") == {}
    assert kd.get_dart_filing_refs("AAPL") == {}


def test_dart_with_key_preferred_resolves_through_common(monkeypatch, tmp_path):
    from src.markets import korea_dart as kd
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(kd, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(kd, "_MEM", {"corp": None, "at": 0.0})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", _CORP_XML)
    seen = []

    class R:
        def __init__(self, content=b"", js=None):
            self.status_code, self.content, self._js = 200, content, js

        def json(self):
            return self._js

    def fake_get(url, params=None, timeout=None):
        seen.append((url.rsplit("/", 1)[-1], dict(params or {})))
        return R(content=buf.getvalue()) if url.endswith("corpCode.xml") else R(js=_LIST)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    ref = kd.get_dart_filing_refs("005935.KS", today=date(2026, 11, 28))
    assert ref["company_name"] == "삼성전자" and ref["corp_code"] == "00126380"
    assert seen[1][1]["corp_code"] == "00126380" and seen[1][1]["pblntf_detail_ty"] == "A001"


def test_resolver_routes_korea_to_dart(monkeypatch):
    from src.agents.industry import edgar_hkex_resolver as r
    monkeypatch.setattr(r, "get_dart_filing_refs", lambda t: {"exchange": "DART", "filing_type": "Business Report",
                                                              "filing_date": "2026-03-20", "fiscal_year": "2025",
                                                              "filing_url": "u"})
    monkeypatch.setattr(r, "get_edgar_filing_refs", lambda t: pytest.fail("Korean ticker sent to SEC EDGAR"))
    st = r.run_edgar_hkex_resolver({"data": {"tickers": ["005930.KS"]}})
    assert st["data"]["edgar_filing_refs"]["005930.KS"]["exchange"] == "DART"


def test_research_context():
    assert korea.research_context("AAPL") == ""
    ctx = korea.research_context("005935.KS", {"exchange": "DART", "filing_url": "https://dart/x", "fiscal_year": "2025",
                                               "company_name": "Samsung", "filing_type": "Business Report"})
    assert "dart.fss.or.kr" in ctx and "연결" in ctx and "PREFERRED" in ctx and "https://dart/x" in ctx


# ── sector pins: PROPOSED only ──────────────────────────────────────────────

def test_proposed_pins_resolve_and_are_not_tuned():
    from src.data.coverage import is_tuned
    from src.data.industry_profile_map import ticker_overrides
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    for t, (sector, profile, why) in korea.PROPOSED_PROFILE_PINS.items():
        assert korea.to_canonical(t) == t, t
        assert profile in P.get(sector, {}), (t, sector, profile)
        assert why
        assert t not in ticker_overrides()                       # nothing applied without the owner
    assert not is_tuned("005930.KS") and not is_tuned("KRX:005930")
    assert is_tuned("000660.KS")                                 # SK hynix: pinned before this work


def test_billing_run_type_for_korean_suffixes():
    from app.backend.services.billing import run_type
    assert run_type("000660.KS", covered=False) == "new"         # tuned (TICKER_SECTOR_LOOKUP pin)
    assert run_type("005930.KS", covered=False) == "limited"
    assert run_type("247540.KQ", covered=False) == "limited"
    assert run_type("247540.KQ", covered=True) == "covered"


# ── reports ─────────────────────────────────────────────────────────────────

def test_cjk_scripts_and_markup():
    from src.utils import cjk_font as cf
    assert cf.script_of("Samsung") is None and cf.script_of("삼성전자") == "hangul"
    assert cf.script_of("トヨタ自動車") == "kana" and cf.script_of("贵州茅台") == "han"
    assert cf.cjk_markup("Samsung <b>Electronics</b> &amp; co") == "Samsung <b>Electronics</b> &amp; co"
    out = cf.cjk_markup("<b>삼성전자 2025 사업보고서</b> (Samsung)")
    assert out.count("<font name=") == 1 and out.startswith("<b><font name=") and out.endswith("</b> (Samsung)")
    assert cf.font_for_text("AAPL") == "Helvetica"
    assert cf.font_for_text("삼성전자") != "Helvetica"


def test_pdf_paragraph_draws_hangul():
    """A Hangul paragraph builds into a PDF whose font resource is a CJK font, not Helvetica."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate
    from src.utils import pdf_report
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build([pdf_report.Paragraph("삼성전자 Samsung Electronics",
                                                                    getSampleStyleSheet()["Normal"])])
    pdf = buf.getvalue()
    assert pdf.startswith(b"%PDF") and (b"HYGothic" in pdf or b"CJK-hangul" in pdf or b"Malgun" in pdf
                                        or b"Nanum" in pdf)


def test_pdf_price_has_no_minor_unit_for_krw():
    from src.utils import pdf_report as pr
    try:
        pr._set_price_currency("KRW")
        assert pr._money(1110000) == "KRW 1,110,000"
        pr._set_price_currency("USD")
        assert pr._money(229.5) == "$229.50"
    finally:
        pr._set_price_currency("USD")


def test_web_currency_symbol_and_landing_gate():
    utils = (ROOT / "app/frontend/src/lib/utils.ts").read_text(encoding="utf-8")
    assert "upper.endsWith('.KS') || upper.endsWith('.KQ')) return '₩'" in utils
    page = (ROOT / "app/frontend/src/pages/ReportPage.tsx").read_text(encoding="utf-8")
    assert "if (t.endsWith('.KS') || t.endsWith('.KQ')) return 'KR';" in page


# ── regression: P/E-normalisation disclosure guard (crashed 247540.KQ) ──────

def test_pe_norm_disclosure_is_guarded_by_its_swaps():
    src = (ROOT / "src/agents/analysis/dcf_agent.py").read_text(encoding="utf-8")
    i = src.index('            _legs_txt = ", ".join(')
    head = src[max(0, i - 600):i]
    assert "        if _pe_norm_swaps:\n" in head
    j = src.index("P/E normalization: trailing net income deviates", i)
    assert "(_pe_norm_flag + \" \" if _pe_norm_flag else \"\")" in src[i:j]

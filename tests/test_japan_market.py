"""Japan (Tokyo Stock Exchange, .T) support, 2026-11-28 -- every rule added for the market, pinned.

Tickers and canonical forms; FMP symbol; request guard; listing currency and the JP¥ label; the JPY risk-free row
(PROPOSED, so not applied unless RF_APPLY_PROPOSED=JP); statutory tax fallback; the market keys (JPX constants,
TOPIX benchmark, industry-map market); stockanalysis.com consensus for Tokyo; market-question tickers; TDnet /
EDINET / ADR filings resolver; the CJK PDF font helper; the fiscal-year disclosure; the proposed profile pins.
No test touches the network.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.markets import japan as jp
from src.markets import japan_filings as jf


# ── tickers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,canon", [
    ("7203.T", "7203.T"), ("7203.t", "7203.T"), ("7203.JP", "7203.T"), ("TYO:7203", "7203.T"),
    ("tse:6758", "6758.T"), ("JP:8306", "8306.T"), ("130a.t", "130A.T"), ("285A.JP", "285A.T"),
    (" 9984.T ", "9984.T"), ("7203.TYO", "7203.T"),
])
def test_tokyo_forms_canonicalise(raw, canon):
    from src.tools.ticker_canonical import canonical_ticker
    assert jp.is_jp_ticker(raw)
    assert jp.to_canonical(raw) == canon
    assert canonical_ticker(raw) == canon


@pytest.mark.parametrize("raw", ["7203", "02888.HK", "D05.SI", "AAPL", "BRK.B", "TM", "12345.T", "A203.T", "7203.HK",
                                 "", None, "ABC:7203"])
def test_non_tokyo_forms_are_not_japanese(raw):
    assert not jp.is_jp_ticker(raw)


def test_a_bare_number_stays_hong_kong():
    from src.tools.ticker_canonical import canonical_ticker
    assert canonical_ticker("7203") == "07203.HK"       # the long-standing HK contract is kept
    assert canonical_ticker("D05") == "D05.SI"


def test_local_codes_round_trip():
    assert jp.local_code("TYO:7203") == "7203"
    assert jp.local_code5("7203.T") == "72030"
    assert jp.from_local_code("72030") == "7203.T"
    assert jp.from_local_code("130A0") == "130A.T"
    assert jp.from_local_code("7203") is None and jp.from_local_code("72031") is None


def test_fmp_symbol_is_the_canonical_form():
    from src.tools.fmp_transcripts import to_fmp_symbol
    assert to_fmp_symbol("TYO:7203") == "7203.T"
    assert to_fmp_symbol("7203.T") == "7203.T"
    assert to_fmp_symbol("BRK.B") == "BRK-B"              # US class shares unchanged


def test_request_guard_admits_tokyo_prefix_only():
    from app.backend.request_guards import is_valid_ticker_input
    for ok in ("7203.T", "TYO:7203", "tse:6758", "130A.T", "AAPL", "0700.HK"):
        assert is_valid_ticker_input(ok), ok
    for bad in ("ABC:XYZ", "AAPL;DROP", "TYO:", "TYO:7203:X"):
        assert not is_valid_ticker_input(bad), bad


def test_market_question_keeps_tokyo_tickers():
    from app.backend.services.market_ask import _supported_symbol
    assert _supported_symbol("7203.T") == "7203.T"
    assert _supported_symbol("TYO:6758") == "6758.T"
    assert _supported_symbol("7203.HK") == "07203.HK"


# ── currency ────────────────────────────────────────────────────────────────

def test_listing_currency_venue_default_is_jpy(monkeypatch):
    import src.tools.api as api
    monkeypatch.setattr(api, "_fmp_get", lambda *a, **k: [])
    api._LISTING_CCY_CACHE.pop("7203.T", None)
    assert api.get_listing_currency("7203.T") == "JPY"
    api._LISTING_CCY_CACHE.pop("7203.T", None)


def test_currency_labels_never_print_a_bare_yen_for_japan():
    from src.agents.analysis import dcf_agent as d
    from src.agents.analysis import sotp_report_extras as sx
    from src.utils import pdf_report as pr
    from src.utils import report_disclosures as rd
    assert jp.CURRENCY_LABEL == "JP¥"
    assert pr._CCY_SYMBOLS["JPY"] == "JP¥"
    assert rd._SYM["JPY"] == "JP¥"
    assert d._CCY_SYMBOLS["JPY"] == "JP¥"
    assert sx._CCY_SYMBOLS["JPY"] == "JP¥"
    pr._set_price_currency("JPY")
    try:
        assert pr._money(3384, 0) == "JP¥3,384"
        assert pr._CJK_HINT == "ja"
    finally:
        pr._set_price_currency("USD")
    assert pr._CJK_HINT is None


def test_bare_yen_in_a_tokyo_narrative_becomes_jp_yen():
    assert jp.normalize_yen("revenue ¥53.5tn, target ¥ 3,835; JP¥10 and CN¥5 kept") == \
        "revenue JP¥53.5tn, target JP¥ 3,835; JP¥10 and CN¥5 kept"
    assert jp.normalize_yen("the ¥ weakened") == "the ¥ weakened"
    assert jp.normalize_yen(None) is None
    assert "JP¥" in jp.research_context("7203.T")


def test_pdf_paragraphs_on_a_tokyo_report_print_jp_yen(monkeypatch):
    from src.utils import pdf_fonts as pf
    from src.utils import pdf_report as pr
    monkeypatch.setenv("PDF_CJK_SYSTEM_FONTS", "off")
    pf._RESOLVED.clear()
    pr._set_price_currency("JPY")
    try:
        p = pr.Paragraph("Target ¥3,835", pr.ParagraphStyle("x"))
        assert "JP¥3,835" in p.text
    finally:
        pr._set_price_currency("USD")
        pf._RESOLVED.clear()
    assert "¥3,835" in pr.Paragraph("Target ¥3,835", pr.ParagraphStyle("x")).text      # other reports untouched


def test_frontend_tables_label_jpy_as_jp_yen():
    root = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src"
    utils = (root / "lib" / "utils.ts").read_text(encoding="utf-8")
    assert "JPY: 'JP¥'" in utils and "return 'JP¥'" in utils
    page = (root / "pages" / "ReportPage.tsx").read_text(encoding="utf-8")
    assert "'US' | 'HK' | 'SG' | 'JP' | null" in page and "return 'JP';" in page


# ── risk-free rate, tax, market keys ────────────────────────────────────────

def test_jgb_row_is_proposed_and_derived():
    from src.data import valuation_constants as vc
    row = vc.load()["risk_free_by_market"]["markets"]["JP"]
    assert row["status"] == "PROPOSED"
    assert 0.0 < row["value"] < 0.06 and "JGB" in row["basis"] and "Ministry of Finance" in row["source"]
    assert row["min_spread_over_rf"] == 0.03 and row.get("derivation")


def test_a_proposed_rf_is_not_applied_by_default(monkeypatch):
    from src.data import capm
    monkeypatch.setenv("CAPM_LIVE_RF", "off")
    monkeypatch.delenv("RF_APPLY_PROPOSED", raising=False)
    rf, src = capm.risk_free("JP")
    assert rf == capm.RF_FALLBACK and "owner-set" not in src          # stays on the US curve until accepted
    sg, sg_src = capm.risk_free("SG")
    assert sg == pytest.approx(0.0229) and "owner-set" in sg_src      # accepted rows unaffected
    monkeypatch.setenv("RF_APPLY_PROPOSED", "JP")
    rf2, src2 = capm.risk_free("JP")
    assert rf2 == pytest.approx(0.027) and "PROPOSED" in src2 and "owner-set pending" in src2


def test_firm_capm_on_the_jgb_curve_floors_at_the_proposed_spread(monkeypatch):
    from src.data import capm
    monkeypatch.setenv("CAPM_LIVE_RF", "off")
    monkeypatch.setenv("RF_APPLY_PROPOSED", "JP")
    w = capm.firm_wacc(profile="Telecom Carrier", net_debt=0.0, market_cap=1e12, cost_of_debt=None,
                       market="JP", own_beta=0.1)
    assert w["rf"] == pytest.approx(0.027) and w["tax_rate"] == pytest.approx(0.2974)
    assert w["cost_of_equity"] >= 0.027 + 0.03 - 1e-9
    monkeypatch.delenv("RF_APPLY_PROPOSED")
    w2 = capm.firm_wacc(profile="Telecom Carrier", net_debt=0.0, market_cap=1e12, cost_of_debt=None,
                        market="JP", own_beta=0.1)
    assert w2["min_spread_over_rf"] is None and not w2["ke_floored"]   # no floor on the US curve


def test_statutory_tax_fallback_is_japans():
    from src.agents.analysis import dcf_agent as d
    rate, src = d._company_tax_rate([], "7203.T", "JPY")
    assert rate == pytest.approx(jp.STATUTORY_TAX) and src == "JP statutory rate"
    assert d._company_tax_rate([], "AAPL", "USD")[0] == pytest.approx(0.21)


def test_constants_key_japan_as_jpx_and_never_borrow_us():
    from src.data import valuation_constants as vc
    assert vc.market_key("7203.T") == "JPX"
    assert vc.market_key("0700.HK") == "HKSE" and vc.market_key("AAPL") == "US"
    assert vc.cost_of_equity("Regulated Utility", "JPX") is None


def test_benchmark_is_topix_on_the_runs_own_capm():
    from src.decisions.ratings import resolve_benchmark
    b = resolve_benchmark("7203.T", "Industrials", capm={"rf": 0.027, "erp": 0.0441, "crp": 0.0})
    assert b["code"] == "TOPIX" and b["market"] == "JPX"
    assert b["expected_return"] == pytest.approx(0.0711, abs=1e-4)


def test_industry_map_market_and_comps_market():
    from src.data.industry_profile_map import market_of
    from src.data.sector_profiles import market_crp, resolve_market
    assert market_of("7203.T") == "JP"
    assert resolve_market("7203.T") == "JPX" and market_crp("JPX") == pytest.approx(0.006)


# ── consensus target (stockanalysis.com) ────────────────────────────────────

_SA_PAGE = ('<div>Currency is JPY</div><p>According to 19 analysts polled by S&amp;P Global, Toyota Motor stock has a '
            'consensus rating of "Buy" and an average price target of ¥3,835. The average 1-year stock price forecast '
            'is 13.33% higher than the current stock price, while the lowest is ¥2,930 (-13.42%) and the highest is '
            '¥4,800 (+41.84%).</p>')


def test_tokyo_consensus_target_parses_the_yen_sign(monkeypatch, tmp_path):
    import requests
    from src.tools import target_price as tp
    assert tp.quote_path("7203.T") == ("tyo/7203", "JPY")
    assert tp.quote_path("TYO:130A") == ("tyo/130A", "JPY")
    monkeypatch.setattr(tp, "_CACHE_DIR", tmp_path)

    class _R:
        status_code, text = 200, _SA_PAGE
    seen = {}
    monkeypatch.setattr(requests, "get", lambda url, **k: (seen.setdefault("url", url), _R())[1])
    out = tp.get_consensus_target("7203.T", refresh=True)
    assert seen["url"] == "https://stockanalysis.com/quote/tyo/7203/forecast/"
    assert out["target"] == 3835.0 and out["low"] == 2930.0 and out["high"] == 4800.0
    assert out["analysts"] == 19 and out["currency"] == "JPY" and not out["suspect"]


def test_consensus_at_run_defers_tokyo_like_hk_and_sg():
    from src.agents.analysis import dcf_agent as d
    assert d._consensus_at_run("7203.T", None)["status"] == "deferred"


# ── filings: TDnet, EDINET, ADR 20-F ────────────────────────────────────────

_TDNET = """
<div class="pager-M" onClick="pagerLink('I_list_002_20261105.html')">2</div>
<div class="pager-M" onClick="pagerLink('I_list_003_20261105.html')">3</div>
<table id="main-list-table">
<tr>
<td class="evennew-L kjTime" noWrap>13:55</td>
<td class="evennew-M kjCode" noWrap>72030</td>
<td class="evennew-M kjName" noWrap>トヨタ自                  </td>
<td class="evennew-M kjTitle" align="left"><a href="140120261030561115.pdf" target="_blank">2027年3月期 第2四半期（中間期）決算短信〔IFRS〕（連結)</a></td>
<td class="evennew-M kjXbrl" noWrap align="center"><div><a class="style002" href="081220261030561115.zip">XBRL</a></div></td>
<td class="evennew-M kjPlace" noWrap align="left">東名</td>
<td class="evennew-R kjHistroy" align="left"> </td>
</tr>
<tr>
<td class="oddnew-L kjTime" noWrap>13:50</td>
<td class="oddnew-M kjCode" noWrap>72030</td>
<td class="oddnew-M kjName" noWrap>トヨタ自</td>
<td class="oddnew-M kjTitle" align="left"><a href="140120261030561100.pdf" target="_blank">（訂正）「2027年３月期 第１四半期決算短信〔IFRS〕（連結）」の一部訂正について</a></td>
<td class="oddnew-M kjXbrl" noWrap align="center"> </td>
<td class="oddnew-M kjPlace" noWrap align="left">東名</td>
<td class="oddnew-R kjHistroy" align="left"> </td>
</tr>
</table>
"""


def test_tdnet_page_parses_rows_and_pager():
    rows, pages = jf.parse_tdnet_page(_TDNET, date(2026, 11, 5))
    assert pages == 3 and len(rows) == 2
    r = rows[0]
    assert r["ticker"] == "7203.T" and r["time"] == "13:55"
    assert r["pdf_url"] == "https://www.release.tdnet.info/inbs/140120261030561115.pdf"
    assert r["xbrl_url"].endswith("081220261030561115.zip")


def test_tanshin_titles_classify():
    c = jf.classify_title("2027年3月期 第2四半期（中間期）決算短信〔IFRS〕（連結)")
    assert c == {"is_tanshin": True, "is_correction": False, "is_forecast_revision": False,
                 "fy_end": "2027-03", "quarter": 2, "basis": "IFRS"}
    full = jf.classify_title("2026年3月期 決算短信〔日本基準〕(連結)")
    assert full["quarter"] == 4 and full["basis"] == "J-GAAP" and full["fy_end"] == "2026-03"
    assert jf.classify_title("（訂正）「2027年３月期 第１四半期決算短信〔米国基準〕」")["is_correction"]
    assert jf.classify_title("業績予想の修正に関するお知らせ")["is_forecast_revision"]


def test_latest_tanshin_scans_only_the_earnings_days(monkeypatch):
    monkeypatch.setenv("JP_TDNET", "on")
    jf._DAY_CACHE.clear()
    fetched = []

    def fetch(url):
        fetched.append(url)
        return _TDNET if url.endswith("I_list_001_20261105.html") else "<table></table>"
    row = jf.latest_tanshin("7203.T", earnings_dates=["2026-11-05"], fetch=fetch, today=date(2026, 11, 28))
    assert row["quarter"] == 2 and not row["is_correction"]
    assert all("20261105" in u or "20261106" in u for u in fetched)          # no month-long scan
    assert jf.latest_tanshin("7203.T", earnings_dates=["2026-09-01"], fetch=fetch,
                             today=date(2026, 11, 28)) is None                # outside TDnet's window
    monkeypatch.setenv("JP_TDNET", "off")
    assert jf.latest_tanshin("7203.T", earnings_dates=["2026-11-05"], fetch=fetch, today=date(2026, 11, 28)) is None
    jf._DAY_CACHE.clear()


def test_adr_20f_is_used_only_on_the_same_accounting_basis():
    edgar = {"accession_number": "0001193125-26-264811", "filing_type": "20-F", "filing_date": "2026-06-10"}
    toyota = jf.resolve_filing_ref("7203.T", edgar_lookup=lambda adr: edgar, tanshin_lookup=lambda t: None)
    assert toyota["adr"] == "TM" and toyota["basis"] == "IFRS" and toyota["accession_number"]
    assert "exchange" not in toyota                       # cited as an EDGAR filing, not HKEX
    calls = []
    mufg = jf.resolve_filing_ref("8306.T", edgar_lookup=lambda adr: calls.append(adr) or edgar,
                                 tanshin_lookup=lambda t: {"date": "2026-11-13", "pdf_url": "https://x/y.pdf",
                                                           "fy_end": "2027-03", "quarter": 2, "basis": "J-GAAP",
                                                           "title": "決算短信", "name": "三菱ＵＦＪ"})
    assert calls == []                                    # US-GAAP 20-F never labels J-GAAP figures
    assert mufg["source"] == "TDnet" and mufg["exchange"] == "TSE" and mufg["fiscal_year"] == "2027"
    assert "TDnet" in jf.filing_context(mufg) and jf.filing_context(toyota) == ""
    assert jf.resolve_filing_ref("9984.T", edgar_lookup=lambda a: {}, tanshin_lookup=lambda t: None) == {}


def test_edinet_needs_a_key_and_finds_the_annual_report(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    assert jf.edinet_latest_annual("7203.T", "2025-03-31") is None

    def fake(url, params):
        if params["date"] == "2025-06-18":
            return {"results": [{"secCode": "72030", "docTypeCode": "120", "docID": "S100W0AB",
                                 "periodEnd": "2025-03-31", "docDescription": "有価証券報告書"}]}
        return {"results": [{"secCode": "99990", "docTypeCode": "120", "docID": "X"}]}
    r = jf.edinet_latest_annual("7203.T", "2025-03-31", api_key="k", fetch_json=fake)
    assert r["doc_id"] == "S100W0AB" and r["filing_date"] == "2025-06-18" and "S100W0AB" in r["viewer_url"]


def test_resolver_phase_routes_tokyo_to_the_japan_resolver(monkeypatch):
    import src.markets.japan_filings as jfm
    from src.agents.industry import edgar_hkex_resolver as er
    monkeypatch.setattr(jfm, "resolve_filing_ref", lambda t: {"source": "TDnet", "filing_date": "2026-11-05"})
    monkeypatch.setattr(er, "get_edgar_filing_refs", lambda t: pytest.fail("EDGAR must not be queried"))
    statuses = []
    monkeypatch.setattr(er.progress, "update_status", lambda a, t, s, *x, **k: statuses.append(s))
    out = er.run_edgar_hkex_resolver({"data": {"tickers": ["7203.T"]}})
    assert out["data"]["edgar_filing_refs"]["7203.T"]["source"] == "TDnet"
    assert statuses[-1].startswith("TSE OK") and not any(w in statuses[-1] for w in ("SEC", "EDGAR", "20-F"))


# ── research prompts ────────────────────────────────────────────────────────

def test_research_context_is_japan_only_and_names_the_fiscal_year_rule():
    ctx = jp.research_context("7203.T", "Toyota Motor Corp.")
    assert "決算短信" in ctx and "ENDS" in ctx and "J-GAAP" in ctx and "7203" in ctx
    assert jp.research_context("AAPL") == "" and jp.research_context("0700.HK") == ""
    assert "7203" in jp.search_hint("7203.T") and jp.search_hint("D05.SI") == ""


def test_fiscal_year_helpers():
    assert jp.fy_label("2026-03-31") == "FY3/26"
    assert jp.fiscal_year_end_month(["2026-03-31", "2025-03-31", "2024-12-31"]) == 3
    assert "2026年3月期" in jp.fy_disclosure("2026-03-31") and "April 2025" in jp.fy_disclosure("2026-03-31")
    assert jp.fy_disclosure("2025-12-31").startswith("FY2025 is the calendar year")
    assert jp.fy_label(None) is None


def test_tokyo_reports_disclose_the_fiscal_year_naming():
    from src.utils.report_disclosures import data_limitations
    assert jp.FY_NAMING_NOTE in data_limitations({}, "7203.T")
    assert jp.FY_NAMING_NOTE not in data_limitations({}, "AAPL")


# ── PDF: CJK glyphs ─────────────────────────────────────────────────────────

def test_cjk_markup_wraps_only_cjk_runs(monkeypatch):
    from src.utils import pdf_fonts as pf
    monkeypatch.setenv("PDF_CJK_SYSTEM_FONTS", "off")
    pf._RESOLVED.clear()
    try:
        out = pf.cjk_markup("Toyota (トヨタ自動車) &amp; <b>年報</b>", hint="ja")
        assert '<font name="HeiseiKakuGo-W5">トヨタ自動車</font>' in out
        assert '<font name="HeiseiKakuGo-W5">年報</font>' in out              # Han-only run takes the hint
        assert "&amp;" in out and "<b>" in out
        assert pf.cjk_markup("plain text") == "plain text" and pf.cjk_markup(None) is None
        assert pf.script_of("삼성전자") == "ko" and pf.script_of("年報") == "zh" and pf.script_of("トヨタ") == "ja"
        assert not pf.is_embedded("HeiseiKakuGo-W5")
    finally:
        pf._RESOLVED.clear()


def test_a_pdf_with_japanese_text_builds_and_carries_a_cjk_font(tmp_path, monkeypatch):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate
    from src.utils import pdf_fonts as pf
    from src.utils import pdf_report as pr
    monkeypatch.setenv("PDF_CJK_SYSTEM_FONTS", "off")
    pf._RESOLVED.clear()
    try:
        path = tmp_path / "jp.pdf"
        SimpleDocTemplate(str(path)).build([pr.Paragraph("トヨタ自動車 2027年3月期 決算短信 (Toyota)",
                                                         getSampleStyleSheet()["Normal"])])
        body = path.read_bytes()
        assert b"HeiseiKakuGo-W5" in body and len(body) > 500
    finally:
        pf._RESOLVED.clear()


# ── portfolio ───────────────────────────────────────────────────────────────

def test_portfolio_rows_are_jpy_with_a_usd_rate(monkeypatch):
    import src.tools.api as api
    from app.backend.services.portfolio_service import currency_info
    from src.tools.ticker_canonical import canonical_ticker
    monkeypatch.setattr(api, "get_listing_currency", lambda t, api_key=None: "JPY" if t.endswith(".T") else "USD")
    monkeypatch.setattr(api, "get_fx_rate", lambda a, b="USD", api_key=None: 0.00627 if a == "JPY" else 1.0)
    info = currency_info([canonical_ticker("TYO:7203"), "AAPL"])
    assert info["7203.T"] == {"currency": "JPY", "fx_to_usd": 0.00627}
    assert info["AAPL"]["currency"] == "USD"


def test_lease_basis_for_tokyo_us_gaap_filers():
    from src.data.filing_basis import basis
    assert basis("8604.T", "JPY") == "US GAAP"        # Nomura files US GAAP in Japan
    assert basis("7203.T", "JPY") == "IFRS"           # Toyota IFRS; J-GAAP names carry no lease liability


# ── coverage, billing, proposed pins ────────────────────────────────────────

def test_tokyo_names_are_limited_coverage_until_pinned():
    from app.backend.services.billing import run_type
    from src.data.coverage import is_tuned
    assert not is_tuned("7203.T") and not is_tuned("TYO:6861")
    assert run_type("7203.T", None, covered=False) == "limited"
    assert run_type("7203.T", None, covered=True) == "covered"


def test_proposed_pins_are_inactive_and_name_real_profiles():
    from src.data.industry_profile_map import ticker_overrides
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    doc = json.loads((Path(jp.__file__).parent / "japan_profile_pins.json").read_text(encoding="utf-8"))
    assert doc["status"] == "PROPOSED" and len(doc["pins"]) >= 30
    live = ticker_overrides()
    for t, row in doc["pins"].items():
        assert jp.is_jp_ticker(t) and jp.to_canonical(t) == t
        sector, profile = row["proposed"]
        assert profile in P.get(sector, {}), (t, sector, profile)
        assert t not in live                               # nothing applied without the owner
    assert doc["bank_calibration_proposal"]["status"] == "PROPOSED"

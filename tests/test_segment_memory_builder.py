"""The memory builder's source routing: HK/SG -> Gemini, US/ADR -> SEC filing, then FMP.

No network: the SEC parser and FMP are replaced with the shapes they return
(SEC shape as parsed from BABA's 20-F filed 2026-05-20).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_segment_revenue_memory as b  # noqa: E402

from src.agents.industry import gemini_params as gp  # noqa: E402
from src.data import segment_memory as sm  # noqa: E402

SEC_BABA = {
    "form": "20-F", "filed": "2026-05-20", "reporting_currency": "CNY",
    "report_short_name": "Segment information (Details)",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1577552/000119312526231755/R123.htm",
    "profit_metric": "baba_BABAAdjustedEBITAAdjustedEarningsBeforeInterestTaxesAndAmortization",
    "segments": [
        {"name": "Alibaba China E-commerce Group",
         "revenue_by_period": {"2026-03-31": 554217e6, "2025-03-31": 508380e6},
         "profit_by_period": {"2026-03-31": 107509e6, "2025-03-31": 193223e6}},
        {"name": "Cloud intelligence group",
         "revenue_by_period": {"2026-03-31": 158132e6, "2025-03-31": 118028e6},
         "profit_by_period": {"2026-03-31": 14265e6}},
    ],
}


@pytest.mark.parametrize("ticker,expected", [
    ("00700.HK", True), ("D05.SI", True), ("c38u.si", True), ("BABA", False), ("MSFT", False),
])
def test_only_hk_and_sg_names_go_to_gemini(ticker, expected):
    assert b.is_hk_sg(ticker) is expected


def test_a_sec_footnote_becomes_a_cited_memory_entry(monkeypatch):
    import src.tools.sec_segments as ss
    monkeypatch.setattr(ss, "get_segment_footnote", lambda t, d: SEC_BABA)
    entry = b.sec_entry("BABA", "Alibaba Group", "sotp_analyst")
    assert entry["source"] == "sec_segment_footnote" and entry["form"] == "20-F"
    commerce = entry["history"]["segments"][0]
    fy26 = next(y for y in commerce["years"] if y["period_end"] == "2026-03-31")
    assert fy26["revenue"]["value"] == 554217e6 and fy26["revenue"]["scale"] == "units"
    assert fy26["revenue"]["currency"] == "CNY"
    assert fy26["revenue"]["source_url"].startswith("https://www.sec.gov/")
    assert gp._cited_ok(fy26["revenue"]) and gp._cited_ok(fy26["profit"])
    cloud_fy25 = next(y for y in entry["history"]["segments"][1]["years"] if y["period_end"] == "2025-03-31")
    assert cloud_fy25["profit"] is None                         # not disclosed that period

    # and the memory reader values it exactly like a Gemini entry
    memory = {"_meta": {}, "tickers": {"BABA": {
        "company": "Alibaba Group", "sotp_basis": "sotp_analyst",
        "fmp_reporting_currency": "CNY", "history": entry["history"]}}}
    mix = sm.latest_mix("09988.HK", memory=memory, fx_to=lambda a, c: 1.0 if a == c else None)
    seg = {s["name"]: s for s in mix["segments"]}
    assert mix["year"] == "2026"
    assert seg["Alibaba China E-commerce Group"]["margin"] == pytest.approx(107509 / 554217)


def test_only_template_divisions_that_need_ebitda_are_requested():
    assert b.ebitda_divisions("00001.HK") == [
        "Ports & Related Services", "Retail (A.S. Watson)", "Telecommunications (3 Group Europe)"]
    assert b.ebitda_divisions("P15.SI") == []                        # listed stakes only
    assert b.ebitda_divisions("MSFT") == []                          # no template


def test_division_ebitda_keeps_exact_template_names_and_drops_uncited(monkeypatch):
    def fake_generate(prompt, schema=None, timeout=None, **kw):
        assert "Ports & Related Services" in prompt and schema is gp.DivisionEbitdaSet
        cited = {"currency": "HKD", "scale": "mn", "period": "FY2025",
                 "source_url": "https://www.ckh.com.hk/ar", "quote": "x"}
        return {"model": "gemini-3.8-flash", "latency_s": 1.0, "json": {"notes": "", "divisions": [
            {"division": "Ports and Related Services", "ebitda": {**cited, "value": 15000},
             "measure": "EBITDA", "includes_share_of_associates": True, "fiscal_year": "FY2025"},
            {"division": "Retail (A.S. Watson)", "ebitda": {**cited, "value": 20000, "source_url": ""},
             "measure": "EBITDA", "includes_share_of_associates": True, "fiscal_year": "FY2025"},
        ]}}
    monkeypatch.setattr(gp, "generate", fake_generate)
    memory = {"_meta": {}, "tickers": {"00001.HK": {"company": "CK Hutchison Holdings", "error": "503"}}}
    b.build_division_ebitda(memory, "00001.HK", timeout=10)
    entry = memory["tickers"]["00001.HK"]
    assert [i["division"] for i in entry["division_ebitda"]["items"]] == ["Ports & Related Services"]
    assert entry["division_ebitda"]["dropped"] == ["Retail (A.S. Watson)"]
    assert "Telecommunications (3 Group Europe)" in entry["division_ebitda"]["missing"]
    assert "error" not in entry and entry["history_error"] == "503"


def test_a_single_segment_filer_has_no_sec_entry(monkeypatch):
    import src.tools.sec_segments as ss
    monkeypatch.setattr(ss, "get_segment_footnote", lambda t, d: None)
    assert b.sec_entry("PDD", "PDD Holdings", "sotp_analyst") is None

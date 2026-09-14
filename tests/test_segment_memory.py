"""Segment revenue/profit memory: latest reported mix, UI summary, and the
memory -> engine bridge where only multiples come from Gemini."""
import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.industry import gemini_params as gp
from src.data import segment_memory as sm


def _c(v, ccy="CNY", scale="mn", url="https://www.alibabagroup.com/en-US/ir"):
    return {"value": v, "currency": ccy, "scale": scale, "period": "FY", "source_url": url, "quote": str(v)}


def _year(fy, end, rev, profit=None, ccy="CNY"):
    return {"fiscal_year": fy, "period_end": end, "revenue": _c(rev, ccy),
            "profit": _c(profit, ccy) if profit is not None else None,
            "profit_measure": "Adjusted EBITA" if profit is not None else None}


MEMORY = {"_meta": {"status": "pending_review", "model": "gemini-3.8-flash", "updated": "2026-09-15"},
          "tickers": {
              "BABA": {"company": "Alibaba Group", "sotp_basis": "sotp_analyst", "fmp_reporting_currency": "CNY",
                       "retrieved": "2026-09-15", "model": "gemini-3.8-flash", "citation_coverage": 1.0,
                       "reconciliation": {"2025": {"segment_sum": 1e12, "fmp_revenue": 996e9,
                                                   "segment_gap": 0.004, "total_gap": 0.0}},
                       "history": {"reporting_currency": "CNY", "segment_definition_changes": "",
                                   "total_revenue": [],
                                   "segments": [
                                       {"name": "China E-commerce", "years": [
                                           _year("FY2024", "2024-03-31", 420000, 180000),
                                           _year("FY2025", "2025-03-31", 450000, 190000)]},
                                       {"name": "Cloud Intelligence", "years": [
                                           _year("FY2025", "2025-03-31", 120000, 10000)]},
                                       {"name": "All others", "years": [
                                           _year("FY2025", "2025-03-31", 430000)]},
                                   ]}},
              "JD": {"company": "JD.com", "sotp_basis": "sotp_analyst", "error": "RuntimeError: 503"},
          }}

fx_to = lambda a, b: 1.0 if a == b else None                     # noqa: E731


def test_latest_mix_uses_the_most_recent_reported_year():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    assert mix["year"] == "2025" and mix["currency"] == "CNY"
    seg = {s["name"]: s for s in mix["segments"]}
    assert seg["China E-commerce"]["share"] == pytest.approx(450 / 1000)
    assert seg["China E-commerce"]["margin"] == pytest.approx(190 / 450)
    assert seg["All others"]["margin"] is None
    assert [s["name"] for s in mix["segments"]][0] == "China E-commerce"   # largest first


def test_the_hk_line_reads_the_adr_entry_and_errors_are_not_used():
    assert sm.latest_mix("09988.HK", memory=MEMORY, fx_to=fx_to)["year"] == "2025"
    assert sm.latest_mix("JD", memory=MEMORY, fx_to=fx_to) is None
    assert sm.latest_mix("MSFT", memory=MEMORY, fx_to=fx_to) is None


def test_ui_summary_carries_years_margins_citations_and_errors():
    ui = sm.ui_summary(memory=MEMORY, fx_to=fx_to)
    assert ui["status"] == "pending_review" and ui["model"] == "gemini-3.8-flash"
    baba = next(t for t in ui["tickers"] if t["ticker"] == "BABA")
    assert baba["years"] == ["2024", "2025"]
    commerce = baba["segments"][0]
    assert commerce["name"] == "China E-commerce"
    cell = commerce["years"][1]
    assert cell["revenue"] == 450000e6 and cell["margin"] == pytest.approx(190 / 450)
    assert cell["revenue_url"].startswith("https://")
    cloud_2024 = next(s for s in baba["segments"] if s["name"] == "Cloud Intelligence")["years"][0]
    assert cloud_2024 == {"year": "2024"}                               # not reported that year
    assert baba["profit_coverage"] == pytest.approx(3 / 4)
    assert next(t for t in ui["tickers"] if t["ticker"] == "JD")["error"].startswith("RuntimeError")


def _ranges(**over):
    doc = {"multiples": [
        {"segment": "China E-commerce", "metric": "pe", "low": 9.0, "high": 11.0, "basis": "brokers",
         "source_url": "https://a.com"},
        {"segment": "Cloud Intelligence Group", "metric": "ev_rev", "low": 4.0, "high": 6.0, "basis": "peers",
         "source_url": "https://b.com"},
    ], "associates_investments": _c(150, "CNY", "bn"), "net_cash": _c(300, "CNY", "bn"),
        "holdco_discount_low": 0.15, "holdco_discount_high": 0.25, "holdco_basis": "conglomerate"}
    doc.update(over)
    return doc


def test_memory_bridge_scales_consensus_by_reported_mix_and_uses_reported_margins():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    fx = {"CNY": 0.14, "USD": 1.0}.get
    assumptions, checks = gp.memory_to_engine(mix, _ranges(), fmp_revenue_fwd_usd=170e9, fx_to_usd=fx)
    seg = {s["name"]: s for s in assumptions["segments"]}
    assert seg["China E-commerce"]["revenue_fwd"] == pytest.approx(170e9 * 0.45)
    assert seg["China E-commerce"]["ebit_margin"] == pytest.approx(0.4222, abs=1e-3)
    assert seg["China E-commerce"]["pe_multiple"] == 10.0
    assert seg["Cloud Intelligence"]["ev_rev_multiple"] == 5.0             # name matched by containment
    assert checks["dropped_segments"] == ["All others"]                      # no multiple -> not guessed
    assert assumptions["holdco_discount_pct"] == pytest.approx(0.20)
    assert assumptions["net_cash"] == pytest.approx(300e9 * 0.14)
    assert _sotp_analyst_style(assumptions, shares=2.4e9)["per_share"] > 0


def test_bridge_reads_percent_holdco_and_drops_uncited_multiples():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    r = _ranges(holdco_discount_low=15, holdco_discount_high=25)
    r["multiples"][1]["source_url"] = ""
    assumptions, checks = gp.memory_to_engine(mix, r, fmp_revenue_fwd_usd=170e9, fx_to_usd={"CNY": 0.14}.get)
    assert assumptions["holdco_discount_pct"] == pytest.approx(0.20)
    assert "Cloud Intelligence" in checks["dropped_segments"]

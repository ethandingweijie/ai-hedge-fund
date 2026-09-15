"""Accepted segment memory in the live DCF engine (dcf_agent hook)."""
import pytest

import src.agents.analysis.dcf_agent as da
from src.data import segment_memory as sm
from src.data.models import AnalystEstimates


def _c(v):
    return {"value": v, "currency": "CNY", "scale": "mn", "period": "FY",
            "source_url": "https://www.sec.gov/x", "quote": str(v)}


ENTRY = {"company": "Alibaba Group", "fmp_reporting_currency": "CNY", "history": {
    "reporting_currency": "CNY", "total_revenue": [], "segment_definition_changes": "",
    "segments": [
        {"name": "Alibaba China E-commerce Group", "years": [
            {"fiscal_year": "FY2026", "period_end": "2026-03-31", "revenue": _c(600), "profit": _c(180)}]},
        {"name": "Cloud intelligence group", "years": [
            {"fiscal_year": "FY2026", "period_end": "2026-03-31", "revenue": _c(400), "profit": _c(40)}]},
    ]}}

SNAP = {"segments": [
    {"name": "Taobao and Tmall Group (China Commerce)", "revenue_fwd": 67e9, "pe_multiple": 10.4},
    {"name": "Cloud Intelligence Group", "revenue_fwd": 20e9, "ev_rev_multiple": 5.3},
], "holdco_discount_pct": 0.15, "net_cash": 1e9, "_origin": "snapshot:BABA"}


@pytest.fixture
def wired(monkeypatch):
    seen = {}

    def estimates(ticker, end_date, period="annual", limit=10, api_key=None):
        seen["consensus_ticker"] = ticker
        return [AnalystEstimates(ticker=ticker, period_end="2027-03-31", revenue_avg=1_200e9)]

    monkeypatch.setattr(sm, "accepted_entry", lambda t, memory=None: ("BABA", ENTRY))
    monkeypatch.setattr(da, "get_analyst_estimates", estimates)
    monkeypatch.setattr(da, "get_fx_rate", lambda a, b, key=None: 0.14)
    return seen


def test_the_hk_line_uses_the_adr_consensus_and_the_accepted_mix(wired):
    new, flag = da._apply_accepted_segment_memory("09988.HK", SNAP, "2026-09-15", "CNY")
    assert wired["consensus_ticker"] == "BABA"                         # once per company
    seg = {s["name"]: s for s in new["segments"]}
    fwd_usd = 1_200e9 * 0.14
    assert seg["Alibaba China E-commerce Group"]["revenue_fwd"] == pytest.approx(fwd_usd * 0.6)
    assert seg["Alibaba China E-commerce Group"]["ebit_margin"] == pytest.approx(0.30)
    assert seg["Alibaba China E-commerce Group"]["pe_multiple"] == 10.4
    assert seg["Cloud intelligence group"]["ev_rev_multiple"] == 5.3
    assert new["_segment_memory"]["consensus_ticker"] == "BABA"
    assert new["_origin"] == "snapshot:BABA" and new["net_cash"] == 1e9
    assert "accepted segment memory (BABA" in flag


def test_both_listings_get_identical_company_inputs(wired):
    hk, _ = da._apply_accepted_segment_memory("09988.HK", SNAP, "2026-09-15", "CNY")
    us, _ = da._apply_accepted_segment_memory("BABA", SNAP, "2026-09-15", "CNY")
    assert hk["segments"] == us["segments"]


def test_nothing_changes_without_acceptance(monkeypatch):
    monkeypatch.setattr(sm, "accepted_entry", lambda t, memory=None: (None, None))
    monkeypatch.setattr(da, "get_analyst_estimates", lambda *a, **k: pytest.fail("fetched without acceptance"))
    new, flag = da._apply_accepted_segment_memory("BABA", SNAP, "2026-09-15", "CNY")
    assert new is SNAP and flag is None


def test_no_consensus_keeps_current_inputs_and_says_so(wired, monkeypatch):
    monkeypatch.setattr(da, "get_analyst_estimates", lambda *a, **k: [])
    new, flag = da._apply_accepted_segment_memory("BABA", SNAP, "2026-09-15", "CNY")
    assert new is SNAP and "no forward consensus" in flag


def test_an_unmappable_memory_keeps_current_inputs_and_says_why(wired):
    snap = {"segments": [{"name": "PDD Domestic Core", "revenue_fwd": 48e9, "pe_multiple": 12}]}
    new, flag = da._apply_accepted_segment_memory("BABA", snap, "2026-09-15", "CNY")
    assert new is snap and "not applied" in flag and "no counterpart" in flag

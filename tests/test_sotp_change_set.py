"""Owner, 2026-09-26: the SOTP change set.

Methods are pre-determined by profile. The SOTP (analyst) leg reads the
owner-accepted Gemini inputs and nothing else; the extractor's output is an
unweighted cross-check. The 3.0 promotion, the snapshot attach and the
segment-note promotion are retired. The pre-fill is driven by the registry.
The China Internet Platform profile carries normalised earnings legs. The
review gate runs the engine's leg on every SOTP pre-fill and names the
segments that would be Degraded.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import industry_inputs as ii
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES


# ── the bridge ──────────────────────────────────────────────────────────────

def test_the_accepted_input_is_the_only_source_and_the_extractor_is_a_cross_check():
    src = inspect.getsource(d.run_dcf_agent)
    at = src.index("_ticker_sotp_extractor = sotp_assumptions_all.get(ticker) or None")
    block = src[at: at + 1400]
    assert 'most_recent["sotp_assumptions_extractor"] = _ticker_sotp_extractor' in block
    assert "_ticker_sotp = None" in block
    assert "_to_engine(_ii_s.canonical_data(_sotp_e))" in block
    assert "held as a cross-check only" in block
    assert '"gate_id": "GATE_SOTP_EXTRACTOR_CROSSCHECK"' in src
    assert '"applied": False' in src[src.index('"gate_id": "GATE_SOTP_EXTRACTOR_CROSSCHECK"'):][:600]


def test_the_holdco_pin_leaves_an_accepted_figure_alone():
    a = {"holdco_discount_pct": 0.30, "_origin": "gemini_accepted", "segments": []}
    out, flag = d._pin_sotp_holdco_discount("BABA", a)
    assert out is a and flag is None


def test_the_live_gate_flags_and_never_substitutes():
    src = inspect.getsource(d._gate_live_sotp)
    assert "lookup_snapshot" not in src and "snapshot:" not in src
    assert "review the accepted figures" in src
    a = {"segments": [{"name": "Core", "revenue_fwd": 1e9, "pe_multiple": 10.0}],
         "_origin": "gemini_accepted", "fx_usd_to_reporting": 1.0}
    out, flag = d._gate_live_sotp("BABA", a, shares=1e6, net_debt=0.0)
    assert out is a and flag and flag.startswith("SOTP (analyst): inputs degraded")


# ── retirements ─────────────────────────────────────────────────────────────

def test_the_promotions_and_the_snapshot_attach_are_gone():
    for name in ("_promote_sotp_analyst_profile", "_promote_segment_sotp",
                 "_SOTP_ANALYST_BLEND_WEIGHT", "_SEGMENT_SOTP_WEIGHT", "_SEGMENT_SOTP_PROFILES"):
        assert not hasattr(d, name), name
    assert "_SOTP_ANALYST_BLEND_WEIGHT" not in d._LEARNABLE_PARAM_NAMES
    assert "_SEGMENT_SOTP_WEIGHT" not in d._LEARNABLE_PARAM_NAMES
    from src.agents.analysis import sotp_snapshot as ss
    assert not hasattr(ss, "attach_snapshot") and hasattr(ss, "curated_holdco_discount")


def test_the_look_through_promotion_remains_the_one_data_driven_promotion():
    assert hasattr(d, "_promote_lookthrough_sotp")
    assert "_promote_lookthrough_sotp(" in inspect.getsource(d.run_dcf_agent)


# ── profiles ────────────────────────────────────────────────────────────────

def test_the_china_internet_platform_profile_and_its_pins():
    p = P["Tech"]["China Internet Platform"]
    w = {m["name"]: m["weight"] for m in p["methods"]}
    assert w == {"DCF": 0.30, "SOTP (analyst)": 0.35, "EV/EBITDA (norm)": 0.20, "P/E (norm)": 0.15}
    assert sum(w.values()) == pytest.approx(1.0)
    assert next(m for m in p["methods"] if m["anchor"])["name"] == "DCF"
    assert "Forward P/E" in p["excluded"]              # HK$3.24 on Meituan's 0.19 NTM EPS
    for t in ("BABA", "PDD", "JD", "03690.HK", "00700.HK", "09988.HK", "09618.HK"):
        assert sp.get_wacc_profile_for_ticker(t) == ("Tech", "China Internet Platform"), t


def test_every_profile_that_declares_the_analyst_sotp_is_implementable_and_sums_to_one():
    found = []
    for sec, profs in P.items():
        for name, pd in profs.items():
            ms = pd.get("methods") or []
            if any(m["name"] == "SOTP (analyst)" for m in ms):
                found.append(name)
                assert next(m for m in ms if m["name"] == "SOTP (analyst)")["implementable"] is True
                assert sum(m["weight"] for m in ms) == pytest.approx(1.0), name
    assert set(found) >= {"China Internet Platform", "Aerospace Holdco (HK)",
                          "Commercial Aerospace & Engines", "Aerospace & Engineering (SG)"}


def test_declared_shadow_methods_are_read_by_the_engine():
    src = inspect.getsource(d.run_dcf_agent)
    assert 'for _shadow in (profile_data.get("shadow_methods") or []):' in src
    assert '_shadow_names = (profile_data or {}).get("shadow_methods") or []' in src


# ── pre-fill by profile ─────────────────────────────────────────────────────

def test_the_sotp_prefill_list_is_derived_from_the_registry():
    from scripts.build_industry_inputs import sotp_profile_tickers
    got = sotp_profile_tickers()
    assert {"BABA", "PDD", "JD", "03690.HK", "00700.HK", "02357.HK", "BA", "S63.SI"} <= set(got)
    assert "LMT" not in got and "NEE" not in got


# ── the engine-preview check ────────────────────────────────────────────────

def test_the_preview_names_the_segments_that_would_be_degraded():
    conv = {"segments": [
        {"name": "Online Games", "revenue_fwd": 266.6e9 * 0.14, "pe_multiple": 23.0},
        {"name": "FinTech", "revenue_fwd": 250.4e9 * 0.14, "ev_rev_multiple": 4.25},
    ], "default_tax_rate": 0.15}
    c = ii.engine_preview_check(conv)
    assert c["check"] == "segments price in the engine" and c["ok"] is False
    assert "1 of 2 segment(s) Degraded" in c["detail"]
    assert "Online Games: P/E 23x cited but EBIT is not stated" in c["detail"]
    ok = ii.engine_preview_check({"segments": [
        {"name": "FinTech", "revenue_fwd": 1e9, "ev_rev_multiple": 4.25}], "default_tax_rate": 0.15})
    assert ok["ok"] is True and "all 1 segment(s) price (EV/Rev)" in ok["detail"]


def test_the_gate_appends_the_preview_to_every_sotp_row():
    doc = {"fiscal_year": "FY2026E", "holdco_discount_pct": 0.1, "holdco_basis": "x",
           "segments": [{"name": "Games", "multiple_metric": "pe", "multiple_low": 22.0, "multiple_high": 24.0,
                         "multiple_basis": "b", "multiple_source_url": "https://e.com/m",
                         "revenue_fwd": {"value": 266.6, "currency": "USD", "scale": "bn", "period": "FY2026E",
                                         "source_url": "https://e.com/r", "quote": "q"}}]}
    e = {"data": doc, "checks": [{"check": "segments price in the engine", "ok": True, "detail": "stale build check"}],
         "ok": True, "company": "T", "basis": "estimate"}
    rows = ii.ui_summary(doc={"version": 1, "tickers": {"00700.HK": {"sotp": e}}},
                         reviews=lambda *a, **k: {"status": "pending", "reviewer": None,
                                                  "reviewed_at": None, "stale": False})["rows"]
    previews = [c for c in rows[0]["checks"] if c["check"] == "segments price in the engine"]
    assert len(previews) == 1 and previews[0]["ok"] is False        # live, not the stale stored one
    assert "Games: P/E 23x cited but EBIT is not stated" in previews[0]["detail"]


# ── golden replay owns its review store ─────────────────────────────────────

def test_golden_replay_never_reads_the_developers_review_database(tmp_path, monkeypatch):
    import os
    from src.memory import golden_capture as gc
    monkeypatch.setattr(ii, "review_for", lambda *a, **k: {"status": "accepted", "reviewer": "dev",
                                                            "reviewed_at": "x", "stale": False})
    with gc.pinned_env():
        # an accepted entry in the developer's store is invisible inside the replay
        assert ii.review_for("BABA", "sotp", {"data": {"segments": []}})["status"] == "pending"
        assert ii.accepted_entry("BABA", "sotp", doc={"version": 1, "tickers": {"BABA": {"sotp": {
            "data": {"segments": []}}}}}) is None
    assert ii.review_for("BABA", "sotp", {})["status"] == "accepted"      # restored on exit

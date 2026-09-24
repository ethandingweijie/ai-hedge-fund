"""Owner, 2026-09-24: pre-deployment sequencing for the SOTP leg and pre-fill.

1. No classifier default in the leg: an earnings segment with non-positive EBIT
   prices on its CITED EV/Sales range or is Degraded with a reason string.
2. Net-cash sourcing precedence: a URL-cited figure is kept; FMP net debt is an
   audit variance flagged above 20%, never a silent overwrite.
3. Forward-period anchoring: the prompt demands FY+1/FY+2 and every figure on
   the reviewer gate carries its period label.
4. Segment reconciliation: segments summing above group revenue fail a hard
   check that blocks acceptance.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.industry import gemini_params as gp
from src.data import industry_inputs as ii
from src.data import valuation_constants as vc


# ── 1. the leg ──────────────────────────────────────────────────────────────

def test_a_pe_segment_with_a_loss_prices_on_its_cited_ev_sales_range():
    a = {"segments": [{"name": "Core Local Commerce", "revenue_fwd": 38.9e9, "ebit_margin": -0.026,
                       "pe_multiple": 15.0, "ev_rev_fallback_multiple": 1.6}],
         "default_tax_rate": 0.15}
    t = d._sotp_analyst_style(a, shares=6.1e9)
    row = t["rows"][0]
    assert row["method"] == "EV/Rev (cited, earnings non-positive)" and row["multiple"] == 1.6
    assert row["value"] == pytest.approx(38.9e9 * 1.6)
    assert t["degraded"] is False and t["per_share_reporting"] > 0


def test_a_pe_segment_with_a_loss_and_no_ev_sales_range_is_degraded_with_the_reason():
    a = {"segments": [{"name": "Core Local Commerce", "revenue_fwd": 38.9e9, "ebit_margin": -0.026,
                       "pe_multiple": 15.0}],
         "default_tax_rate": 0.15}
    t = d._sotp_analyst_style(a, shares=6.1e9)
    row = t["rows"][0]
    assert row["method"] == "Degraded" and row["value"] is None and row["multiple"] is None
    assert row["degraded_reason"] == ("P/E 15x cited but EBIT is -2.6% of revenue (non-positive); "
                                      "no cited EV/Sales range to fall back on")
    assert t["degraded"] is True and t["degraded_segments"][0]["name"] == "Core Local Commerce"
    assert t["per_share"] is None and t["per_share_reporting"] is None


def test_a_degraded_segment_beside_priced_ones_is_a_partial_nav_that_does_not_publish():
    a = {"segments": [{"name": "Hotel", "revenue_fwd": 9.9e9, "ebit_margin": 0.25, "pe_multiple": 10.0},
                      {"name": "Food Delivery", "revenue_fwd": 23.3e9, "pe_multiple": 12.0}],
         "default_tax_rate": 0.15, "net_cash": 0.8e9}
    t = d._sotp_analyst_style(a, shares=6.1e9)
    assert [r["method"] for r in t["rows"]] == ["P/E", "Degraded"]
    assert t["segment_value"] == pytest.approx(9.9e9 * 0.25 * 0.85 * 10.0)
    assert t["degraded"] is True and t["per_share_reporting"] is None
    assert "1 of 2 segment(s) Degraded" in t["degraded_reason"]
    assert "Food Delivery: P/E 12x cited but EBIT is not stated" in t["degraded_reason"]


def test_the_positive_earnings_path_ignores_the_fallback_range():
    a = {"segments": [{"name": "Core", "revenue_fwd": 100.0, "ebit": 30.0, "pe_multiple": 10.0,
                       "ev_rev_fallback_multiple": 9.0}]}
    t = d._sotp_analyst_style(a, shares=10.0)
    assert t["rows"][0]["method"] == "P/E" and t["rows"][0]["value"] == pytest.approx(30.0 * 0.85 * 10.0)


def test_the_classifier_constant_is_gone_from_the_leg_and_consumers_stop_on_degraded():
    src = inspect.getsource(d._sotp_analyst_style)
    assert "_classify_segment(" not in src and "EV/Rev (fallback)" not in src
    run = inspect.getsource(d._compute_method_value)
    assert 'if table.get("degraded_no_segments") or table.get("degraded"):' in run
    gate = inspect.getsource(d._gate_live_sotp)
    assert '(_tbl or {}).get("degraded_no_segments") or (_tbl or {}).get("degraded")' in gate


# ── 2. net cash ─────────────────────────────────────────────────────────────

CITE = {"source_url": "https://www1.hkexnews.hk/x.pdf", "period": "2025-12-31", "quote": "Net cash 56.1 billion yuan"}


def test_a_cited_net_cash_is_kept_and_fmp_is_the_audit_check():
    a = {"net_cash": 68e9, "_net_cash_citation": CITE, "fx_usd_to_reporting": 1.0}
    out, flag = d._refresh_sotp_net_cash(a, net_debt=12.8e9)          # FMP says -$12.8bn
    assert out["net_cash"] == 68e9 and out["_net_cash_source"] == "cited"
    assert out["_net_cash_fmp_check"] == pytest.approx(-12.8e9)
    assert flag and "kept at the cited $68.0bn" in flag and "above the 20% audit threshold" in flag


def test_a_cited_net_cash_inside_the_threshold_raises_no_flag():
    a = {"net_cash": 10e9, "_net_cash_citation": CITE, "fx_usd_to_reporting": 1.0}
    out, flag = d._refresh_sotp_net_cash(a, net_debt=-9e9)             # FMP: $9bn net cash, -10%
    assert out["net_cash"] == 10e9 and flag is None and out["_net_cash_variance"] == pytest.approx(-0.1)


def test_an_uncited_net_cash_is_still_restated_from_fmp():
    a = {"net_cash": 68e9, "fx_usd_to_reporting": 1.0}                  # the snapshot shape
    out, flag = d._refresh_sotp_net_cash(a, net_debt=12.8e9)
    assert out["net_cash"] == pytest.approx(-12.8e9) and out["_net_cash_source"] == "engine_net_debt"
    assert flag and "restated" in flag


def test_the_thresholds_are_owner_constants():
    thr = vc.sotp_input_thresholds()
    assert thr == {"net_cash_variance_flag": 0.20, "segment_sum_excess_tolerance": 0.05}
    assert vc.load()["sotp_inputs"]["reviewer"].startswith("owner, 2026-09-24")


# ── 3. forward periods on the prompt and the gate ───────────────────────────

def _cited(v, period="FY2026E", ccy="CNY", scale="bn"):
    return {"value": v, "currency": ccy, "scale": scale, "period": period,
            "source_url": "https://example.com/r", "quote": f"{v}"}


def _sotp_doc(period="FY2026E"):
    return {"fiscal_year": "FY2026E",
            "segments": [{"name": "Retail", "revenue_fwd": _cited(1200.0, period),
                          "ebit_margin": {"value": -0.03, "period": period, "source_url": "https://example.com/r", "quote": "-3%"},
                          "multiple_metric": "pe", "multiple_low": 8.0, "multiple_high": 13.0,
                          "multiple_basis": "broker", "multiple_source_url": "https://example.com/m",
                          "ev_sales_low": 0.4, "ev_sales_high": 0.6, "ev_sales_source_url": "https://example.com/m"},
                         {"name": "Logistics", "revenue_fwd": _cited(230.0, period), "ebit_margin": None,
                          "multiple_metric": "ev_rev", "multiple_low": 0.3, "multiple_high": 0.5,
                          "multiple_basis": "peers", "multiple_source_url": "https://example.com/m"}],
            "associates_investments": _cited(50.0, "2025-12-31"),
            "net_cash": _cited(56.1, "2025-12-31"),
            "holdco_discount_pct": 0.1, "holdco_basis": "standard"}


def test_the_prompt_demands_forward_figures_with_period_labels_and_external_revenue():
    p = gp.sotp_prompt("JD.com", "JD", {"revenue_next_fy_period": "2026-12-31"})
    assert "FORWARD figure for the NEXT fiscal year (FY+1, ending 2026-12-31)" in p
    assert "'FY2026E'" in p and "EXTERNAL of inter-segment sales" in p
    assert "must not exceed consolidated group revenue" in p
    assert "ALSO give the EV/Sales range" in p
    assert gp.SegmentEstimate.model_fields["ev_sales_low"].default is None
    assert "FORWARD year" in gp.SotpInputs.model_fields["fiscal_year"].description


def test_the_bridge_carries_the_loss_the_fallback_range_the_period_and_the_citation():
    fx = lambda ccy: 0.14 if ccy == "CNY" else None      # noqa: E731
    a, checks = gp.to_engine_assumptions(_sotp_doc(), fx_to_usd=fx)
    retail = a["segments"][0]
    assert retail["ebit_margin"] == pytest.approx(-0.03)            # a loss stays a loss
    assert retail["ev_rev_fallback_multiple"] == 0.5 and retail["pe_multiple"] == 10.5
    assert retail["period_label"] == "FY2026E"
    assert "ev_rev_fallback_multiple" not in a["segments"][1]
    assert a["_net_cash_citation"]["source_url"] == "https://example.com/r"
    assert a["_net_cash_citation"]["period"] == "2025-12-31"
    assert checks["clamped"] == []
    assert gp.MARGIN_BOUNDS == (-1.0, 0.60)


def test_the_gate_shows_every_sotp_figure_with_its_period_label():
    doc = {"version": 1, "tickers": {"JD": {"sotp": {"data": _sotp_doc(), "checks": [], "ok": True,
                                                    "company": "JD.com", "basis": "estimate"}}}}
    rows = ii.ui_summary(doc=doc, reviews=lambda *a, **k: {"status": "pending", "reviewer": None,
                                                           "reviewed_at": None, "stale": False})["rows"]
    r = rows[0]
    assert r["kind"] == "sotp" and r["period"] == "FY2026E"
    det = r["detail"]
    assert det["fiscal_year"] == "FY2026E"
    assert [s["revenue"]["period_label"] for s in det["segments"]] == ["FY2026E", "FY2026E"]
    assert det["segments"][0]["margin"] == {"value": -0.03, "period_label": "FY2026E"}
    assert det["segments"][0]["ev_sales_fallback"] == {"low": 0.4, "high": 0.6}
    assert det["segments"][1]["ev_sales_fallback"] is None
    assert det["net_cash"]["period_label"] == "2025-12-31" and det["associates"]["value"] == 50.0
    assert det["holdco_discount_pct"] == 0.1


# ── 4. segment reconciliation blocks acceptance ─────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(ii, "_reviews_ready_key", None)
    yield
    _db.close_all_connections()


def _entry(check_ok):
    return {"version": 1, "tickers": {"JD": {"sotp": {
        "data": _sotp_doc(), "company": "JD.com", "basis": "estimate", "ok": check_ok,
        "checks": [{"check": "segment revenue vs group revenue", "ok": check_ok,
                    "detail": "segments sum to 54.4bn USD vs FMP FY+1 consensus revenue 38.9bn (+39.8%; may not exceed by more than 5%)"}]}}}}


def test_a_failed_reconciliation_cannot_be_accepted(store):
    assert ii.HARD_CHECKS == ("segment revenue vs group revenue",)
    with pytest.raises(ValueError, match="cannot accept: segment revenue vs group revenue failed"):
        ii.set_review("JD", "sotp", "accepted", "owner", doc=_entry(False))
    assert ii.accepted_entry("JD", "sotp", doc=_entry(False)) is None


def test_a_passing_reconciliation_accepts_and_a_revoke_is_always_allowed(store):
    out = ii.set_review("JD", "sotp", "accepted", "owner", doc=_entry(True))
    assert out["status"] == "accepted"
    out = ii.set_review("JD", "sotp", "revoked", "owner", doc=_entry(False))
    assert out["status"] == "revoked"


def test_the_build_checks_are_one_sided_and_period_aware():
    src = open("scripts/build_industry_inputs.py", encoding="utf-8").read()
    assert '"check": "segment revenue vs group revenue", "ok": _excess <= _tol' in src
    assert 'sotp_input_thresholds()["segment_sum_excess_tolerance"]' in src
    assert "(ii._year(p) or 0) <= _latest" in src


# ── 5. grounding wrappers resolve to canonical sources (owner, 2026-09-24) ──

WRAP = gp.GROUNDING_REDIRECT_PREFIX + "AUZIYQabc"


def test_a_grounding_wrapper_is_not_a_canonical_source():
    assert gp.is_grounding_redirect(WRAP) and not gp.is_canonical_source(WRAP)
    assert gp.is_canonical_source("https://www1.hkexnews.hk/x.pdf")
    assert gp.resolve_source_url("https://www1.hkexnews.hk/x.pdf") == "https://www1.hkexnews.hk/x.pdf"


def test_the_walk_replaces_wrappers_and_keeps_them_as_provenance():
    doc = _sotp_doc()
    doc["net_cash"]["source_url"] = WRAP
    doc["segments"][0]["multiple_source_url"] = WRAP + "2"
    out, mapping = gp.canonicalize_citations(
        doc, resolver=lambda u: "https://www1.hkexnews.hk/r.pdf" if u == WRAP else None)
    assert out["net_cash"]["source_url"] == "https://www1.hkexnews.hk/r.pdf"
    assert out["net_cash"]["grounding_url"] == WRAP
    assert out["segments"][0]["multiple_source_url"] == WRAP + "2"     # unresolved stays, reported
    assert mapping == {WRAP: "https://www1.hkexnews.hk/r.pdf", WRAP + "2": None}
    assert doc["net_cash"]["source_url"] == WRAP                        # input untouched


def test_precedence_needs_a_canonical_url_not_a_wrapper():
    fx = lambda ccy: 0.14 if ccy == "CNY" else None      # noqa: E731
    doc = _sotp_doc(); doc["net_cash"]["source_url"] = WRAP
    a, checks = gp.to_engine_assumptions(doc, fx_to_usd=fx)
    assert "net_cash" in a and "_net_cash_citation" not in a
    assert a["_net_cash_citation_unresolved"] == WRAP and checks["unresolved_citations"] == ["net_cash"]
    out, flag = d._refresh_sotp_net_cash({**a, "fx_usd_to_reporting": 1.0}, net_debt=1e9)
    assert out["_net_cash_source"] == "engine_net_debt"                # restated, as before


def test_existing_entries_keep_their_hash_and_gain_a_canonical_map():
    doc = _sotp_doc(); doc["net_cash"]["source_url"] = WRAP
    e = {"data": doc, "canonical_urls": {WRAP: "https://www1.hkexnews.hk/r.pdf"}, "checks": [], "ok": True,
         "company": "JD.com", "basis": "estimate"}
    cd = ii.canonical_data(e)
    assert cd["net_cash"]["source_url"] == "https://www1.hkexnews.hk/r.pdf"
    assert cd["net_cash"]["grounding_url"] == WRAP and e["data"]["net_cash"]["source_url"] == WRAP
    assert ii.content_hash(e) == ii.content_hash({"data": doc})          # reviewed data unchanged
    rows = ii.ui_summary(doc={"version": 1, "tickers": {"JD": {"sotp": e}}},
                         reviews=lambda *a, **k: {"status": "pending", "reviewer": None,
                                                  "reviewed_at": None, "stale": False})["rows"]
    assert rows[0]["detail"]["net_cash"]["source_url"] == "https://www1.hkexnews.hk/r.pdf"
    src = inspect.getsource(d.run_dcf_agent)
    assert "_to_engine(_ii_s.canonical_data(_sotp_e))" in src

"""Workstream R3 — assumption_steward offline tests.

Runs against a temp sqlite DB (RUN_ARCHIVE_PATH; conftest strips
DATABASE_URL so dual-mode resolves to sqlite). No network, no LLM —
every FMP-touching branch is monkeypatched at the src.tools.api boundary
(the steward imports those inside functions, so attribute patches are
picked up at call time), and the LLM challenge-reading path is either
switched off (ASSUMPTION_STEWARD_LLM=false) or replaced with a stub.

Covers: parse helpers (incl. the FY27E 2-digit label case), monitor spec
/ focus fields / sector templates, all five deterministic detectors, the
cross-doc theme detector (direction keywords AND amount inference),
challenge dedupe, scorecard scoring + fact-based resolution, variant
driver ranking, the Watch block builder, the annotate-reading path, and
the ASSUMPTION_STEWARD=false kill-switch no-ops.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def st(tmp_path_factory):
    """Temp sqlite + fresh store DDL; yields (store, steward) modules."""
    db_file = tmp_path_factory.mktemp("assumption_steward") / "steward.db"
    saved = {k: os.environ.get(k) for k in
             ("RUN_ARCHIVE_PATH", "DATABASE_URL", "ASSUMPTION_STEWARD",
              "ASSUMPTION_STEWARD_LLM")}
    os.environ["RUN_ARCHIVE_PATH"] = str(db_file)
    os.environ.pop("DATABASE_URL", None)  # conftest already strips; be sure
    os.environ.pop("ASSUMPTION_STEWARD", None)      # default-on
    os.environ.pop("ASSUMPTION_STEWARD_LLM", None)
    import src.memory.assumption_store as store
    import src.memory.assumption_steward as steward
    store._ensured = False                          # fresh per-test-DB DDL
    try:
        yield SimpleNamespace(store=store, steward=steward)
    finally:
        store._ensured = False                      # don't leak state
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ══════════════════════════════════════════════════════════════════════════
# Parse helpers
# ══════════════════════════════════════════════════════════════════════════

def test_parse_year_label_forms(st):
    p = st.steward._parse_year_label
    assert p("FY2027") == 2027
    assert p("2027") == 2027
    assert p("FY27E") == 2027          # GS 2-digit estimate labels
    assert p("FY28E") == 2028
    assert p("FY+1", base_fy=2026) == 2027
    assert p("FY+1") is None           # needs base_fy
    assert p("n/a") is None
    assert p("") is None


def test_parse_pct(st):
    p = st.steward._parse_pct
    assert p("+45%") == 45.0
    assert p("45") == 45.0
    assert p("0.45") == 45.0           # bare decimal fraction -> percent
    assert p("-33%") == -33.0
    assert p(None) is None
    assert p("triple-digit growth") is None


def test_norm_direction(st):
    n = st.steward._norm_direction
    assert n("raised") == "up"
    assert n("lifted") == "up"
    assert n("cut") == "down"
    assert n("narrowing") == "down"
    assert n(None) == ""
    assert n("maintained") == ""
    # 6-K margin language emitted by the live extractor (BABA corpus)
    assert n("Increased to 12%") == "up"
    assert n("Decreased to 6%") == "down"


# ══════════════════════════════════════════════════════════════════════════
# Monitor spec / templates / focus fields (3-layer blueprint)
# ══════════════════════════════════════════════════════════════════════════

def test_focus_fields_monitor_spec_content(st):
    msft = st.steward.focus_fields("MSFT")
    joined = " | ".join(msft)
    assert "Azure" in joined                 # Intelligent Cloud segment
    assert "RPO" in joined                   # backlog metric
    assert "Copilot" in joined               # PBP segment attach rate
    assert msft == list(dict.fromkeys(msft))  # deduped, order stable

    amzn = st.steward.focus_fields("amzn")   # case-insensitive
    amzn_joined = " | ".join(amzn)
    assert "AWS revenue growth" in amzn_joined
    assert "GMV" in amzn_joined              # e-commerce template focus
    assert "RPO / backlog" in amzn_joined    # cloud template focus

    assert st.steward.focus_fields("NO-SPEC") == []


def test_templates_for_multi_membership(st):
    amzn_names = {t["template"] for t in st.steward.templates_for("AMZN")}
    assert amzn_names == {"cloud_saas", "ecommerce_marketplace"}
    meta_names = {t["template"] for t in st.steward.templates_for("META")}
    assert meta_names == {"digital_advertising"}
    assert st.steward.templates_for("UNKNOWN") == []


# ══════════════════════════════════════════════════════════════════════════
# Deterministic detectors — earnings quality (AMZN one-off case)
# ══════════════════════════════════════════════════════════════════════════

def test_detect_earnings_quality(st):
    st.store.upsert_earnings_assumptions(
        "EQTEST", 2026, 2, as_of="2026-08-01",
        one_offs=[
            # Boosting one-off WITH parseable amount -> flagged
            {"item": "Other income (one-off)", "amount": "$53.4B",
             "impact": "one-time gain helped boost EPS"},
            # Negative control: one-off that hurts earnings -> not flagged
            {"item": "Restructuring (one-off)", "amount": "$2.1B",
             "impact": "charge reduced net income"},
            # Boost wording but no parseable amount -> not flagged
            {"item": "Tax benefit", "amount": None,
             "impact": "boosted EPS"},
        ],
        model_used="test",
    )
    hits = st.steward.detect_earnings_quality("EQTEST")
    assert len(hits) == 1
    assert hits[0]["anomaly_type"] == "earnings_quality"
    assert hits[0]["field_key"].startswith("one_off.")
    assert "run-rate" in hits[0]["evidence"]


# ══════════════════════════════════════════════════════════════════════════
# Deterministic detectors — margin compression (BABA rev-up / EBITA-down)
# ══════════════════════════════════════════════════════════════════════════

def test_detect_margin_compression(st):
    st.store.upsert_earnings_assumptions(
        "MCTEST", 2027, 1, as_of="2026-08-20",
        segments=[{"name": "Cloud Intelligence", "growth_rate_pct": "45"}],
        kpis=[{"name": "CMR revenue", "growth_pct": "9"}],
        margins=[{"metric": "adjusted EBITA margin", "direction": "down",
                  "driver": "UX reinvestment and low-price subsidies"}],
        model_used="test",
    )
    hits = st.steward.detect_margin_compression("MCTEST")
    assert len(hits) == 1
    assert hits[0]["anomaly_type"] == "margin_compression"
    assert hits[0]["field_key"].startswith("margin.")
    ev = hits[0]["evidence"]
    assert "Cloud Intelligence +45%" in ev
    assert "CMR revenue +9%" in ev

    # Negative control: growth up but no margin guided down -> no challenge
    st.store.upsert_earnings_assumptions(
        "MCTEST2", 2027, 1, as_of="2026-08-20",
        segments=[{"name": "Cloud", "growth_rate_pct": "45"}],
        model_used="test",
    )
    assert st.steward.detect_margin_compression("MCTEST2") == []


# ══════════════════════════════════════════════════════════════════════════
# Deterministic detectors — direction reversal (same source's own history)
# ══════════════════════════════════════════════════════════════════════════

def test_detect_direction_reversal(st):
    src = "analyst:Goldman Sachs"
    st.store.append_assumption_versions([
        {"ticker": "REVTEST", "source": src, "field_key": "capex_guidance",
         "new_value": "$200B", "prior_value_stated": "$190B",
         "direction": "up"},
    ])
    st.store.append_assumption_versions([
        {"ticker": "REVTEST", "source": src, "field_key": "capex_guidance",
         "new_value": "$180B", "prior_value_stated": "$200B",
         "direction": "down"},
    ])
    hits = st.steward.detect_direction_reversal("REVTEST")
    assert len(hits) == 1
    assert hits[0]["anomaly_type"] == "direction_reversal"
    assert hits[0]["field_key"] == "capex_guidance"
    assert "reversed" in hits[0]["evidence"]

    # Same-direction continuation -> no reversal
    st.store.append_assumption_versions([
        {"ticker": "REVTEST2", "source": src, "field_key": "capex",
         "new_value": "$10B", "direction": "up"},
    ])
    st.store.append_assumption_versions([
        {"ticker": "REVTEST2", "source": src, "field_key": "capex",
         "new_value": "$12B", "prior_value_stated": "$10B",
         "direction": "raised further"},
    ])
    assert st.steward.detect_direction_reversal("REVTEST2") == []


# ══════════════════════════════════════════════════════════════════════════
# Deterministic detectors — cross-doc theme divergence
# ══════════════════════════════════════════════════════════════════════════

def test_detect_cross_doc_theme_up_vs_down(st):
    st.store.append_assumption_versions([
        {"ticker": "META", "source": "analyst:GS", "field_key": "capex.fy2026",
         "new_value": "$137.5B", "prior_value_stated": "$140B",
         "direction": "cut"},
    ])
    st.store.append_assumption_versions([
        {"ticker": "AMZN", "source": "analyst:GS", "field_key": "capex.fy2026",
         "new_value": "$220B", "prior_value_stated": "$200B",
         "direction": "raised"},
    ])
    hits = st.steward.detect_cross_doc_theme(["META", "AMZN"])
    assert len(hits) == 1
    assert hits[0]["field_key"] == "theme.capex_guidance"
    assert hits[0]["anomaly_type"] == "theme_divergence"
    assert "META" in hits[0]["evidence"] and "AMZN" in hits[0]["evidence"]


def test_detect_cross_doc_theme_same_direction_quiet(st):
    st.store.append_assumption_versions([
        {"ticker": "THQ1", "source": "s", "field_key": "capex",
         "new_value": "$10B", "prior_value_stated": "$9B", "direction": "up"},
    ])
    st.store.append_assumption_versions([
        {"ticker": "THQ2", "source": "s", "field_key": "capex",
         "new_value": "$20B", "prior_value_stated": "$18B", "direction": "up"},
    ])
    assert st.steward.detect_cross_doc_theme(["THQ1", "THQ2"]) == []


def test_detect_cross_doc_theme_direction_inferred_from_amounts(st):
    # Company version rows carry direction=None; the steward must infer
    # direction from new vs prior amounts.
    st.store.append_assumption_versions([
        {"ticker": "AMTINFA", "source": "earnings",
         "field_key": "guidance.capex.FY2027",
         "new_value": "$220B", "prior_value_stated": "$200B",
         "direction": None},
        {"ticker": "AMTINFB", "source": "earnings",
         "field_key": "guidance.capex.FY2027",
         "new_value": "$130B", "prior_value_stated": "$145B",
         "direction": None},
    ])
    hits = st.steward.detect_cross_doc_theme(["AMTINFA", "AMTINFB"])
    assert len(hits) == 1
    assert hits[0]["anomaly_type"] == "theme_divergence"


# ══════════════════════════════════════════════════════════════════════════
# Divergence bands (mgmt guidance vs FMP consensus; house vs guidance)
# ══════════════════════════════════════════════════════════════════════════

def test_detect_divergence(st, monkeypatch):
    # FMP boundary stubs: trailing revenue $100B, street FY+1 rev $108B
    monkeypatch.setattr(
        "src.tools.api.get_financial_metrics",
        lambda ticker, period=None, limit=1: [
            SimpleNamespace(revenue=100e9)])
    monkeypatch.setattr(
        "src.tools.api.get_analyst_estimates",
        lambda ticker, limit=1: [SimpleNamespace(revenue_avg=108e9)])

    st.store.upsert_earnings_assumptions(
        "DIVERG", 2026, 2, as_of="2026-08-01",
        guidance=[
            # mgmt rev $120B = +20% vs street +8% -> 12pp > 5pp band
            {"metric": "revenue", "period": "FY2027", "mid": "$120B"},
            # mgmt EBITDA $50B vs house $40B -> 20% > 10% band
            {"metric": "EBITDA", "period": "FY2027", "mid": "$50B"},
        ],
        model_used="test",
    )
    # House estimate uses the 2-digit FY27E label -> _parse_year_label path
    st.store.upsert_analyst_report(
        "DIVERG", "divhash1", house="Goldman Sachs",
        estimates=[{"fiscal_year_label": "FY27E", "ebitda": "$40B"}],
        ai_input_allowed=True, model_used="test",
    )

    hits = st.steward.detect_divergence("DIVERG")
    keys = {h["field_key"] for h in hits}
    assert keys == {"guidance.revenue.FY2027", "ebitda.FY2027"}
    assert all(h["anomaly_type"] == "divergence" for h in hits)
    rev = next(h for h in hits if "revenue" in h["field_key"])
    assert "pp apart" in rev["evidence"]

    # Within-band house estimate -> no EBITDA divergence
    st.store.upsert_analyst_report(
        "DIVERG2", "divhash2", house="GS",
        estimates=[{"fiscal_year_label": "FY2027", "ebitda": "$49B"}],
        ai_input_allowed=True, model_used="test",
    )
    st.store.upsert_earnings_assumptions(
        "DIVERG2", 2026, 2, as_of="2026-08-01",
        guidance=[{"metric": "EBITDA", "period": "FY2027", "mid": "$50B"}],
        model_used="test",
    )
    hits2 = st.steward.detect_divergence("DIVERG2")
    assert all(h["field_key"] != "ebitda.FY2027" for h in hits2)


# ══════════════════════════════════════════════════════════════════════════
# Kill switch — ASSUMPTION_STEWARD=false makes detectors/builders no-ops
# ══════════════════════════════════════════════════════════════════════════

def test_kill_switch_no_ops(st, monkeypatch):
    monkeypatch.setenv("ASSUMPTION_STEWARD", "false")
    assert st.steward.steward_enabled() is False
    # Rows exist for EQTEST/MCTEST from earlier tests, but the wrapper
    # short-circuits before any detector runs.
    assert st.steward.detect_challenges("EQTEST") == []
    assert st.steward.detect_challenges("MCTEST") == []
    assert st.steward.build_assumption_watch("MCTEST") == ""
    assert st.steward.score_actuals("MCTEST")["scored"] == 0
    assert st.steward.run_steward_inline(["MCTEST"])["status"] == "disabled"
    assert st.steward.run_steward_sweep()["status"] == "disabled"


def test_detect_challenges_aggregates(st):
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr("src.tools.api.get_financial_metrics",
                       lambda *a, **k: [])
        monkey.setattr("src.tools.api.get_analyst_estimates",
                       lambda *a, **k: [])
        hits = st.steward.detect_challenges("MCTEST")
        assert any(h["anomaly_type"] == "margin_compression" for h in hits)
    finally:
        monkey.undo()


# ══════════════════════════════════════════════════════════════════════════
# Challenge persistence
# ══════════════════════════════════════════════════════════════════════════

def test_raise_detected_dedupes(st):
    detected = [{"field_key": "guidance.capex.FY2026",
                 "anomaly_type": "divergence", "evidence": "seed"}]
    ids1 = st.steward.raise_detected("RAISEDUP", detected)
    ids2 = st.steward.raise_detected("RAISEDUP", detected)
    assert len(ids1) == 1 and ids1 == ids2
    rows = st.store.get_open_challenges("RAISEDUP")
    assert len(rows) == 1
    assert rows[0]["status"] == "open"


# ══════════════════════════════════════════════════════════════════════════
# Recursive scoring — guidance vs reported actuals
# ══════════════════════════════════════════════════════════════════════════

def test_score_actuals_quarterly_guidance(st, monkeypatch):
    st.store.upsert_earnings_assumptions(
        "SCORET", 2026, 2, as_of="2026-07-01",
        guidance=[
            {"metric": "EPS", "period": "Q2 2026", "mid": "1.00"},
            # FY-scoped -> must NOT be scored against a quarterly print
            {"metric": "revenue", "period": "FY2026", "mid": "$400B"},
        ],
        model_used="test",
    )
    st.store.raise_challenge("SCORET", "guidance.eps.Q2 2026", "divergence",
                             "seeded for fact-resolution")
    monkeypatch.setattr(
        "src.tools.api.get_earnings_surprises",
        lambda *a, **k: [{"date": "2026-07-25", "eps_actual": 1.05,
                          "revenueActual": 410e9}])

    result = st.steward.score_actuals("SCORET")
    assert result["scored"] == 1          # EPS only; revenue is FY-scoped
    assert result["resolved"] == 1        # open guidance challenge resolved

    summ = st.store.get_scorecard_summary("SCORET")
    assert summ["earnings"]["hits"] == 1  # 1.00 vs 1.05 = 5% <= 10% band
    assert summ["earnings"]["misses"] == 0

    # Challenge resolved BY FACTS as dismissed (actual landed in band)
    assert st.store.get_open_challenges("SCORET") == []


def test_score_actuals_guidance_post_dates_print(st, monkeypatch):
    st.store.upsert_earnings_assumptions(
        "SCORET2", 2026, 2, as_of="2026-08-01",   # AFTER the print below
        guidance=[{"metric": "EPS", "period": "Q2 2026", "mid": "1.00"}],
        model_used="test",
    )
    monkeypatch.setattr(
        "src.tools.api.get_earnings_surprises",
        lambda *a, **k: [{"date": "2026-07-25", "eps_actual": 1.05}])
    assert st.steward.score_actuals("SCORET2")["scored"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Source track record (scorecard-weighted confidence)
# ══════════════════════════════════════════════════════════════════════════

def test_source_track_record_low_track(st):
    for fq in (1, 2, 3):
        st.store.record_scorecard("TRACKT", "analyst:HouseX", "eps",
                                  2026, fq, predicted="2.00", actual="1.50",
                                  in_range=False, magnitude=0.25)
    rec = st.steward.source_track_record("TRACKT")
    assert rec["analyst:HouseX"]["n"] == 3
    assert rec["analyst:HouseX"]["hit_rate"] == 0.0
    assert rec["analyst:HouseX"]["low_track_record"] is True

    # Under the minimum row count -> never flagged low-track
    st.store.record_scorecard("TRACKT2", "analyst:HouseY", "eps", 2026, 1,
                              predicted="2.00", actual="1.00",
                              in_range=False, magnitude=0.5)
    rec2 = st.steward.source_track_record("TRACKT2")
    assert rec2["analyst:HouseY"]["low_track_record"] is False


# ══════════════════════════════════════════════════════════════════════════
# Variant drivers — ranked by gap x valuation sensitivity, PT never enters
# ══════════════════════════════════════════════════════════════════════════

def test_variant_drivers_ranking(st):
    st.store.upsert_analyst_report(
        "VARDRIV", "vdhash", house="Goldman Sachs",
        house_vs_consensus=[
            {"metric": "AWS op margin", "house_view": "39%",
             "street_view": "~34%", "comment": "above street"},
            {"metric": "capex", "house_view": "$220bn",
             "street_view": "$210bn", "comment": "5% above street"},
        ],
        ai_input_allowed=True, model_used="test",
    )
    st.store.raise_challenge(
        "VARDRIV", "margin.adjusted_ebita", "margin_compression",
        "EBITA guided down 12% while revenue grows")

    drivers = st.steward.variant_drivers("VARDRIV", top_n=2)
    assert len(drivers) == 2
    keys = [d["field_key"] for d in drivers]
    # margin sensitivity (1.2) ranks the 39-gap margin call first and the
    # 12-gap compression challenge second; the 5-gap capex call (0.8) drops
    assert keys == ["house_vs_consensus.AWS op margin",
                    "margin.adjusted_ebita"]
    assert drivers[0]["gap_pct"] == 39.0
    assert drivers[1]["gap_pct"] == 12.0
    # Doctrine: no price target ever appears as a variant driver
    assert all("price_target" not in k for k in keys)


# ══════════════════════════════════════════════════════════════════════════
# Assumption Watch block (qualitative feed into research + scenarios)
# ══════════════════════════════════════════════════════════════════════════

def test_build_assumption_watch(st):
    block = st.steward.build_assumption_watch("VARDRIV")
    assert block.startswith("ASSUMPTION WATCH (recursive steward)")
    assert "[margin_compression]" in block
    assert "Variant drivers" in block
    assert "AWS op margin" in block
    assert "Source track record" not in block  # no scorecard rows yet

    # Nothing tracked for this ticker -> empty string (block disappears)
    assert st.steward.build_assumption_watch("WATCHLESS") == ""


def test_build_assumption_watch_includes_track_record(st):
    block = st.steward.build_assumption_watch("TRACKT")
    assert "LOW-TRACK-RECORD" in block
    assert "hit-rate 0%" in block


# ══════════════════════════════════════════════════════════════════════════
# LLM challenge readings (stubbed — Q1 bundle path is covered separately)
# ══════════════════════════════════════════════════════════════════════════

def test_annotate_readings_writes_note_and_stays_open(st, monkeypatch):
    cid = st.store.raise_challenge("ANNOT", "guidance.capex.FY2026",
                                   "divergence", "seed")
    open_ch = st.store.get_open_challenges("ANNOT")

    def fake_reading(ticker, challenges):
        assert ticker == "ANNOT"
        return {"readings": [{
            "field_key": "guidance.capex.FY2026",
            "what_changed": "capex raised $20B",
            "why_anomalous": "against the peer capacity-cycle read",
            "affected_inputs": ["growth rate", "capex"],
            "confidence_pct": 80,
        }], "verdict": "Watch the capex line."}

    monkeypatch.setattr(st.steward, "challenge_reading", fake_reading)
    n = st.steward._annotate_readings("ANNOT", open_ch)
    assert n == 1

    rows = st.store.get_open_challenges("ANNOT")
    assert len(rows) == 1 and rows[0]["id"] == cid   # STILL open
    note = rows[0]["outcome_note"] or ""
    assert "[reading]" in note
    assert "capex raised $20B" in note
    assert "VERDICT: Watch the capex line." in note

    # Second pass: already-annotated challenges are not re-read
    def boom(*a, **k):
        raise AssertionError("challenge_reading must not be called again")
    monkeypatch.setattr(st.steward, "challenge_reading", boom)
    assert st.steward._annotate_readings("ANNOT", rows) == 0


def test_annotate_readings_llm_env_off(st, monkeypatch):
    st.store.raise_challenge("ANNOT2", "guidance.capex.FY2026",
                             "divergence", "seed")
    open_ch = st.store.get_open_challenges("ANNOT2")
    monkeypatch.setenv("ASSUMPTION_STEWARD_LLM", "false")

    def boom(*a, **k):
        raise AssertionError("LLM path must be skipped when env-off")
    monkeypatch.setattr(st.steward, "challenge_reading", boom)
    assert st.steward._annotate_readings("ANNOT2", open_ch) == 0


# ══════════════════════════════════════════════════════════════════════════
# Orchestration — inline pass
# ══════════════════════════════════════════════════════════════════════════

def test_run_steward_inline_smoke(st, monkeypatch):
    # MCTEST carries the seeded margin-compression anomaly
    monkeypatch.setenv("ASSUMPTION_STEWARD_LLM", "false")
    monkeypatch.setattr("src.tools.api.get_financial_metrics",
                        lambda *a, **k: [])
    monkeypatch.setattr("src.tools.api.get_analyst_estimates",
                        lambda *a, **k: [])
    monkeypatch.setattr("src.tools.api.get_earnings_surprises",
                        lambda *a, **k: [])
    summary = st.steward.run_steward_inline(["mctest"], trigger="test")
    assert summary["status"] == "ok"
    entry = summary["tickers"]["MCTEST"]
    assert entry["detected"] >= 1
    assert entry["challenges_open"] >= 1
    assert "margin.adjusted EBITA margin"[:40] in entry["variant_drivers"] or \
        any("margin." in fk for fk in entry["variant_drivers"])
    # Lower-case input normalized; unknown tickers don't break the pass
    assert "unknownticker" not in summary["tickers"]

"""Workstream R1 — assumption_store offline round-trip tests.

Runs against a temp sqlite DB (RUN_ARCHIVE_PATH; conftest strips
DATABASE_URL so dual-mode resolves to sqlite). No network, no LLM.
Covers: table DDL idempotency, earnings-assumption upsert/dedupe,
analyst-report content-hash PK + allowance gate, append-only version
trajectory (same-day same-value dedupe), challenge dedupe/resolve, and
the scorecard hit-rate summary.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("assumption_store") / "store.db"
    saved = {k: os.environ.get(k) for k in
             ("RUN_ARCHIVE_PATH", "DATABASE_URL")}
    os.environ["RUN_ARCHIVE_PATH"] = str(db_file)
    os.environ.pop("DATABASE_URL", None)  # conftest already strips; be sure
    import src.memory.assumption_store as s
    s._ensured = False                   # fresh per-test-DB DDL pass
    try:
        yield s
    finally:
        s._ensured = False               # don't leak into later modules
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── Earnings assumptions ──────────────────────────────────────────────────────

def test_upsert_and_latest_roundtrip(store):
    store.upsert_earnings_assumptions(
        "baba", 2027, 1,
        as_of="2026-08-20",
        source="edgar_6k_ex99+fmp_transcript",
        source_ref="0001104659-26-099220",
        period_label="June Quarter 2026",
        guidance=[{"metric": "capex", "period": "FY2027",
                   "low": "Rmb200bn", "high": "Rmb220bn", "unit": "RMB"}],
        segments=[{"name": "Cloud", "growth_rate_pct": "45",
                   "outlook": "accelerating"}],
        kpis=[{"name": "AI-related revenue", "value": "triple-digit growth"}],
        quotes=["We will continue to invest in AI infrastructure."],
        model_used="qwen3.6-plus",
    )
    latest = store.get_latest_earnings_assumptions("BABA")
    assert latest is not None
    assert latest["fiscal_year"] == 2027
    assert latest["fiscal_quarter"] == 1
    assert latest["as_of"] == "2026-08-20"
    assert latest["period_label"] == "June Quarter 2026"
    assert latest["guidance"][0]["metric"] == "capex"
    assert latest["segments"][0]["name"] == "Cloud"
    # Ticker lookup is case-insensitive
    assert store.get_latest_earnings_assumptions("baba") == latest


def test_upsert_same_quarter_overwrites_not_duplicates(store):
    store.upsert_earnings_assumptions(
        "BABA", 2027, 1, as_of="2026-08-21",
        guidance=[{"metric": "capex", "period": "FY2027", "mid": "Rmb210bn"}],
        model_used="qwen3.6-plus",
    )
    rows = store.get_earnings_assumptions("BABA", limit=20)
    q_rows = [r for r in rows
              if r["fiscal_year"] == 2027 and r["fiscal_quarter"] == 1]
    assert len(q_rows) == 1
    assert q_rows[0]["as_of"] == "2026-08-21"
    assert q_rows[0]["guidance"][0]["mid"] == "Rmb210bn"


def test_stored_quarter_and_newer_quarter_coexist(store):
    assert store.get_stored_quarter("BABA") == (2027, 1)
    store.upsert_earnings_assumptions("BABA", 2027, 2, as_of="2026-11-20")
    assert store.get_stored_quarter("BABA") == (2027, 2)
    latest = store.get_latest_earnings_assumptions("BABA")
    assert latest["fiscal_quarter"] == 2
    assert store.get_stored_quarter("NEVER-FILED") is None


# ── Analyst reports ───────────────────────────────────────────────────────────

def test_analyst_report_hash_pk_and_allowance(store):
    store.upsert_analyst_report(
        "BABA", "abc123hash",
        house="Goldman Sachs", rating="Buy", price_target=186.0,
        price_target_currency="USD",
        estimates=[{"fiscal_year_label": "FY2027", "revenue": "Rmb1.05tn"}],
        revisions=[{"field": "capex FY2027", "prior_value": "Rmb190bn",
                    "new_value": "Rmb210bn", "direction": "up"}],
        ai_input_allowed=False,
        model_used="qwen3.6-plus",
    )
    rows = store.get_analyst_reports("BABA")
    assert len(rows) == 1
    assert rows[0]["house"] == "Goldman Sachs"
    assert rows[0]["ai_input_allowed"] is False
    assert rows[0]["revisions"][0]["direction"] == "up"

    # Same hash re-upserted → still one row (content-hash PK dedupe)
    store.upsert_analyst_report("BABA", "abc123hash", house="Goldman Sachs",
                                rating="Buy", ai_input_allowed=False)
    assert len(store.get_analyst_reports("BABA")) == 1

    # Allowance gate flips
    store.set_analyst_report_allowance("BABA", "abc123hash", True)
    assert store.get_analyst_reports("BABA")[0]["ai_input_allowed"] is True

    by_hash = store.get_analyst_report_by_hash("BABA", "abc123hash")
    assert by_hash and by_hash["rating"] == "Buy"
    assert store.get_analyst_report_by_hash("BABA", "missing") is None


# ── Version trajectory (append-only, idempotent per day) ─────────────────────

def test_version_rows_idempotent_same_day(store):
    rows = [{
        "ticker": "BABA", "source": "analyst:Goldman Sachs",
        "fiscal_year": 2027, "fiscal_quarter": 1,
        "field_key": "capex", "new_value": "Rmb210bn",
        "prior_value_stated": "Rmb190bn", "direction": "up",
        "doc_ref": "abc123hash",
    }]
    assert store.append_assumption_versions(rows) == 1
    # Identical row the same day → deduped
    assert store.append_assumption_versions(rows) == 0
    vers = store.get_assumption_versions("BABA", field_key="capex")
    assert len(vers) == 1
    assert vers[0]["new_value"] == "Rmb210bn"
    assert vers[0]["prior_value_stated"] == "Rmb190bn"


def test_version_new_value_appends(store):
    rows = [{
        "ticker": "BABA", "source": "analyst:Goldman Sachs",
        "field_key": "capex", "new_value": "Rmb240bn",
        "prior_value_stated": "Rmb210bn", "direction": "up",
    }]
    assert store.append_assumption_versions(rows) == 1
    vers = store.get_assumption_versions("BABA", field_key="capex")
    assert len(vers) == 2
    # newest first
    assert vers[0]["new_value"] == "Rmb240bn"


# ── Challenges ────────────────────────────────────────────────────────────────

def test_challenge_dedupe_and_resolve(store):
    cid = store.raise_challenge(
        "META", "rating_vs_pt", "internal_contradiction",
        "Buy maintained while PT cut $815→$725")
    assert cid
    # identical OPEN challenge → deduped (same id returned, one row)
    cid2 = store.raise_challenge(
        "META", "rating_vs_pt", "internal_contradiction",
        "Buy maintained while PT cut $815→$725")
    open_rows = store.get_open_challenges("META")
    assert len(open_rows) == 1
    assert {cid, cid2} == {open_rows[0]["id"]}

    store.resolve_challenge(cid, "resolved", resolution="one-off factor")
    assert store.get_open_challenges("META") == []


# ── Scorecard ─────────────────────────────────────────────────────────────────

def test_scorecard_summary(store):
    store.record_scorecard("BABA", "management", "revenue", 2026, 4,
                           predicted="Rmb243B", actual="Rmb247B",
                           in_range=True, magnitude=0.016)
    store.record_scorecard("BABA", "management", "capex", 2026, 4,
                           predicted="Rmb190B", actual="Rmb210B",
                           in_range=False, magnitude=0.105)
    summ = store.get_scorecard_summary("BABA")
    # shape: {source: {hits, misses, hit_rate}}
    assert summ["management"]["hits"] == 1
    assert summ["management"]["misses"] == 1
    assert summ["management"]["hit_rate"] == 0.5
    # per-source filter; unknown source → empty tally
    mgmt = store.get_scorecard_summary("BABA", source="management")
    assert set(mgmt.keys()) == {"management"}
    assert store.get_scorecard_summary("BABA", source="analyst:GS") == {}

"""
tests/test_report_recap.py
==========================
M1 — report recap layer (src/memory/report_recap.py): structured
extraction from a saved run payload, the LLM compression pass (mocked) and
its structured-only fallback, save/roundtrip, get_recent_recap TTL +
ticker filtering, has_recap, kill switches, and the PG-compatible SQL
shape (ON CONFLICT DO UPDATE — never INSERT OR REPLACE).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "recap_test.db"))
    monkeypatch.delenv("REPORT_RECAPS", raising=False)
    monkeypatch.delenv("RECAP_MAX_AGE_DAYS", raising=False)
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def recap(tmp_db):
    from src.memory import report_recap
    report_recap._tables_ready_key = None  # re-create tables on the tmp DB
    return report_recap


def _payload(ticker: str = "CRWD", action: str = "BUY") -> dict:
    """web_runs full_result_json shape: flat data.* keyed by ticker."""
    t = ticker.upper()
    return {
        "run_id": "run-abc",
        "ticker": t,
        "model_name": "claude-sonnet-4-6",
        "run_at": "2026-08-10T09:30:00+00:00",
        "data": {
            "dcf_range": {t: {
                "wacc": 0.104,
                "base": {"intrinsic_value": 420.5},
                "bear": {"intrinsic_value": 310.0},
                "bull": {"intrinsic_value": 560.0},
            }},
            "scenario_analysis": {t: {"current_price": 385.2, "upside_pct": 9.2}},
            "power_law_analysis": {t: {"total_score": 7.5}},
            "value_trap_analysis": {t: {"overall_verdict": "LOW"}},
        },
        "decisions": {t: {
            "action": action,
            "price_target": 445.0,
            "stop_loss": 330.0,
            "entry_range": [370.0, 395.0],
            "position_size_pct": 3.0,
            "time_horizon": "medium",
            "rationale": "Recurring revenue compounding; valuation reset.",
            "signal_score": 7.4,
        }},
    }


# ── Structured extraction (pure) ─────────────────────────────────────────────

def test_extract_structured_fields(recap):
    s = recap.extract_structured(_payload(), "CRWD")
    assert s["final_action"] == "BUY"
    assert s["price_target"] == 445.0
    assert s["stop_loss"] == 330.0
    assert s["entry_range"] == [370.0, 395.0]
    assert s["position_size_pct"] == 3.0
    assert s["time_horizon"] == "medium"
    assert s["price_at_run"] == 385.2
    assert s["dcf_base_iv"] == 420.5
    assert s["dcf_bear_iv"] == 310.0
    assert s["dcf_bull_iv"] == 560.0
    assert s["dcf_wacc"] == 0.104
    assert s["power_law_score"] == 7.5
    assert s["value_trap_verdict"] == "LOW"
    assert s["ev_upside_pct"] == 9.2
    assert "Recurring revenue" in s["rationale"]


def test_extract_structured_missing_sections(recap):
    """A sparse payload (failed phases) must still yield a usable dict."""
    s = recap.extract_structured({"data": {}, "decisions": {}}, "ZZZZ")
    assert s["final_action"] is None
    assert s["price_target"] is None
    assert s["dcf_base_iv"] is None
    assert s["entry_range"] == []


def test_extract_structured_nan_rejected(recap):
    p = _payload()
    p["decisions"]["CRWD"]["price_target"] = float("nan")
    s = recap.extract_structured(p, "CRWD")
    assert s["price_target"] is None


# ── build_recap: LLM path + fallback ─────────────────────────────────────────

def test_build_recap_llm_path(recap, monkeypatch):
    monkeypatch.setattr(recap, "_call_recap_llm", lambda structured, ticker: {
        "recap_text": "BUY on valuation reset; ARR compounding 28%.",
        "assumptions": ["ARR growth >= 25%"],
        "catalysts": ["Q2 earnings"],
        "risks": ["multiple compression"],
    })
    r = recap.build_recap(_payload(), "CRWD")
    assert r["ticker"] == "CRWD"
    assert r["run_id"] == "run-abc"
    assert r["final_action"] == "BUY"
    assert r["recap_text"].startswith("BUY on valuation reset")
    assert r["recap_json"]["llm_used"] is True
    assert r["recap_json"]["assumptions"] == ["ARR growth >= 25%"]
    assert r["recap_json"]["catalysts"] == ["Q2 earnings"]
    assert r["recap_json"]["risks"] == ["multiple compression"]
    assert r["recap_json"]["dcf_base_iv"] == 420.5


def test_build_recap_llm_failure_fallback(recap, monkeypatch):
    """LLM failure degrades to a structured-only recap — never raises."""
    monkeypatch.setattr(recap, "_call_recap_llm", lambda structured, ticker: None)
    r = recap.build_recap(_payload(), "crwd")  # lowercase in, uppercase out
    assert r["ticker"] == "CRWD"
    assert r["recap_json"]["llm_used"] is False
    assert r["recap_json"]["assumptions"] == []
    # Deterministic fallback headlines the decision + targets.
    assert "BUY" in r["recap_text"]
    assert "445" in r["recap_text"]


# ── save / roundtrip / TTL ────────────────────────────────────────────────────

def _mk_recap(recap, ticker="CRWD", run_id="run-1", days_ago=2.0, action="BUY"):
    run_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "ticker": ticker.upper(),
        "run_id": run_id,
        "run_at": run_at.isoformat(),
        "price_at_run": 385.2,
        "final_action": action,
        "signal_score": 7.4,
        "recap_json": {"price_target": 445.0, "assumptions": ["a1"]},
        "recap_text": "thesis text",
    }


def test_save_and_get_recent_recap_roundtrip(recap):
    assert recap.save_recap(_mk_recap(recap)) is True
    got = recap.get_recent_recap("CRWD")
    assert got is not None
    assert got["run_id"] == "run-1"
    assert got["final_action"] == "BUY"
    assert got["recap_text"] == "thesis text"
    assert got["recap_json"]["price_target"] == 445.0
    assert 1.0 < got["age_days"] < 3.0
    # case-insensitive ticker lookup
    assert recap.get_recent_recap("crwd")["run_id"] == "run-1"


def test_get_recent_recap_ttl(recap, monkeypatch):
    """Recaps older than RECAP_MAX_AGE_DAYS are invisible."""
    monkeypatch.setenv("RECAP_MAX_AGE_DAYS", "7")
    recap.save_recap(_mk_recap(recap, days_ago=10.0))
    assert recap.get_recent_recap("CRWD") is None
    # explicit override still honoured
    assert recap.get_recent_recap("CRWD", max_age_days=30)["run_id"] == "run-1"


def test_get_recent_recap_freshest_wins(recap):
    recap.save_recap(_mk_recap(recap, run_id="old", days_ago=9.0, action="HOLD"))
    recap.save_recap(_mk_recap(recap, run_id="new", days_ago=1.0, action="BUY"))
    got = recap.get_recent_recap("CRWD")
    assert got["run_id"] == "new"
    assert got["final_action"] == "BUY"


def test_get_recent_recap_other_ticker_isolated(recap):
    recap.save_recap(_mk_recap(recap, ticker="CRWD"))
    assert recap.get_recent_recap("PANW") is None
    assert recap.get_recent_recap("") is None


def test_save_recap_upsert_same_key(recap):
    recap.save_recap(_mk_recap(recap, action="HOLD"))
    recap.save_recap(_mk_recap(recap, action="BUY"))  # same (ticker, run_id)
    got = recap.get_recent_recap("CRWD")
    assert got["final_action"] == "BUY"
    # still exactly one row
    rows = _db.query("SELECT run_id FROM report_recaps WHERE ticker = ?", ["CRWD"])
    assert len(rows) == 1


def test_has_recap(recap):
    assert recap.has_recap("CRWD", "run-1") is False
    recap.save_recap(_mk_recap(recap))
    assert recap.has_recap("CRWD", "run-1") is True
    assert recap.has_recap("crwd", "run-1") is True
    assert recap.has_recap("CRWD", "run-other") is False
    assert recap.has_recap("", "") is False


# ── Kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_build_and_save(recap, monkeypatch):
    monkeypatch.setenv("REPORT_RECAPS", "false")
    assert recap.recaps_enabled() is False
    assert recap.build_and_save_recap(_payload(), "CRWD", run_id="x") is None
    monkeypatch.setenv("REPORT_RECAPS", "true")
    assert recap.recaps_enabled() is True


def test_build_and_save_recap_overrides(recap, monkeypatch):
    """Backfill passes the web_runs row's own run_id/run_at."""
    monkeypatch.setattr(recap, "_call_recap_llm", lambda s, t: None)
    r = recap.build_and_save_recap(
        _payload(), "CRWD",
        run_id="web-run-id", run_at="2026-08-12T00:00:00+00:00")
    assert r is not None
    assert r["run_id"] == "web-run-id"
    assert r["run_at"] == "2026-08-12T00:00:00+00:00"
    assert recap.get_recent_recap("CRWD")["run_id"] == "web-run-id"


# ── PG-compatible SQL shape ──────────────────────────────────────────────────

def test_save_sql_pg_compatible(recap):
    """The upsert must work on BOTH backends: ON CONFLICT DO UPDATE with ?
    placeholders — never SQLite-only INSERT OR REPLACE."""
    sql = recap._SAVE_SQL
    assert "ON CONFLICT(ticker, run_id) DO UPDATE" in " ".join(sql.split())
    assert "INSERT OR REPLACE" not in sql.upper()
    assert "?" in sql

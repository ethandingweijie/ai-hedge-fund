"""Tests for src/data/backfill_vgpm.py — historical VGPM recomputation.

Covers:
  * Dry-run reports candidate updates without writing
  * Apply mode actually persists the new VGPM JSON
  * Idempotent: re-running on already-backfilled DB is a no-op
  * Pre-cutoff runs are NOT touched
  * Missing dcf_range / scenario_analysis → ticker skipped (not crashed)
  * Both top-level vgpm and data.vgpm keys updated in lockstep
  * Sector pulled from data.sectors[ticker]; missing sector → Tech default
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.data.backfill_vgpm import (
    DEFAULT_SINCE_ISO,
    _build_vgpm_inputs,
    _vgpm_changed,
    _vgpm_grade_summary,
    backfill_vgpm_for_runs,
)


# ── Fixture builder ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_archive_db():
    """Create a temp web_runs SQLite seeded with synthetic rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE web_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                ticker TEXT,
                run_at TEXT,
                full_result_json TEXT
            )
        """)

    def _add(
        ticker: str,
        run_at: str,
        sector: str = "Technology",
        rev_growth: float = 0.05,    # 5% growth — pre-fix → B-band; sector-aware → varies
        fcf_margin: float = 0.10,    # 10% margin
        existing_vgpm_grade: str = "B",  # the pre-fix collapsed grade
    ):
        payload = {
            "ticker": ticker,
            "run_at": run_at,
            "data": {
                "sectors":             {ticker: sector},
                "dcf_range": {
                    ticker: {
                        "base":              {"intrinsic_value": 110, "growth_rate": rev_growth,
                                              "margin_direction": "stable", "risk_flag": "MEDIUM"},
                        "wacc":              0.08,
                        "shares_outstanding": 100,
                        "revenue_base":      1000,
                        "fcf_margin_base":   fcf_margin,
                        "data_source":       "analyst",
                    },
                },
                "scenario_analysis": {
                    ticker: {
                        "current_price": 100, "upside_pct": 10,
                        "bull": {"fair_value": 130}, "bear": {"fair_value": 80},
                    },
                },
                "raw_financials": {"2024": {"net_income": 150, "revenue": 1000}},
                "analyst_signals": {
                    "insider_activity_agent": {ticker: {"summary": "Insider neutral"}},
                },
                # Pre-fix collapsed VGPM
                "vgpm": {
                    ticker: {
                        "valuation":     {"score": 52, "grade": existing_vgpm_grade,
                                          "subs": ["DCF MoS: +10%"]},
                        "growth":        {"score": 58, "grade": existing_vgpm_grade,
                                          "subs": ["Rev CAGR: +5.0%"]},
                        "profitability": {"score": 60, "grade": existing_vgpm_grade,
                                          "subs": ["FCF margin: 10.0%"]},
                        "momentum":      {"score": 55, "grade": existing_vgpm_grade,
                                          "subs": ["EV upside: +10.0%"]},
                    },
                },
            },
            "vgpm": {
                ticker: {
                    "valuation":     {"score": 52, "grade": existing_vgpm_grade, "subs": []},
                    "growth":        {"score": 58, "grade": existing_vgpm_grade, "subs": []},
                    "profitability": {"score": 60, "grade": existing_vgpm_grade, "subs": []},
                    "momentum":      {"score": 55, "grade": existing_vgpm_grade, "subs": []},
                },
            },
        }
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO web_runs (run_id, ticker, run_at, full_result_json) "
                "VALUES (?, ?, ?, ?)",
                (f"{ticker}_{run_at}", ticker, run_at, json.dumps(payload)),
            )

    yield path, _add
    try:
        os.unlink(path)
    except (OSError, PermissionError):
        pass


# ── _vgpm_changed ─────────────────────────────────────────────────────────


def test_vgpm_changed_detects_grade_difference():
    old = {"growth": {"score": 58, "grade": "B-"}}
    new = {"growth": {"score": 85, "grade": "A"}}
    assert _vgpm_changed(old, new) is True


def test_vgpm_changed_returns_false_when_identical():
    same = {"growth": {"score": 58, "grade": "B-"}}
    assert _vgpm_changed(same, same) is False


def test_vgpm_changed_handles_missing_dim():
    """One side missing the dim → considered changed."""
    assert _vgpm_changed({}, {"growth": {"score": 58, "grade": "B-"}}) is True


def test_vgpm_changed_handles_none():
    """None inputs — both None → not changed; one None → changed."""
    assert _vgpm_changed(None, None) is False
    assert _vgpm_changed(None, {"growth": {"score": 1, "grade": "D-"}}) is True


# ── _build_vgpm_inputs ─────────────────────────────────────────────────────


def test_build_inputs_extracts_sector_from_data():
    data = {
        "sectors":            {"AAPL": "Tech"},
        "dcf_range":          {"AAPL": {"base": {"intrinsic_value": 100, "growth_rate": 0.1}}},
        "scenario_analysis":  {"AAPL": {"current_price": 90}},
    }
    result = _build_vgpm_inputs(data, "AAPL")
    assert result is not None
    dcf_t, scen_t, raw_fin, dcf_cal, ins_sum, sector = result
    assert sector == "Tech"


def test_build_inputs_returns_none_when_no_dcf_or_scenario():
    """If both inputs are missing, can't recompute."""
    assert _build_vgpm_inputs({}, "AAPL") is None
    assert _build_vgpm_inputs({"sectors": {"AAPL": "Tech"}}, "AAPL") is None


def test_build_inputs_handles_insider_dict_and_string():
    """Insider summary may be dict (with summary sub-key) or string."""
    data = {
        "dcf_range":         {"X": {"base": {}}},
        "scenario_analysis": {"X": {"current_price": 100}},
        "analyst_signals": {
            "insider_activity_agent": {"X": {"summary": "Net buying"}},
        },
    }
    _, _, _, _, ins, _ = _build_vgpm_inputs(data, "X")
    assert ins == "Net buying"


# ── backfill_vgpm_for_runs — dry run ─────────────────────────────────────


def test_dry_run_reports_updates_without_writing(tmp_archive_db):
    db_path, add = tmp_archive_db
    # 3 post-cutoff runs across 3 sectors
    add("AAPL", "2026-05-15T10:00:00+00:00", sector="Technology", rev_growth=0.05)
    add("VZ",   "2026-05-16T10:00:00+00:00", sector="Telco",      rev_growth=0.04)
    add("JPM",  "2026-05-17T10:00:00+00:00", sector="Financials", rev_growth=0.06)

    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["runs_examined"] == 3
    assert result["runs_with_vgpm"] == 3
    # All 3 should have grade changes (sector-aware bands differ from pre-fix)
    assert result["runs_updated"] >= 1
    assert result["tickers_updated"] >= 1

    # Verify DB rows are STILL the pre-fix values (no write occurred)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT full_result_json FROM web_runs").fetchall()
    for (j,) in rows:
        d = json.loads(j)
        # Pre-fix grade was "B" in our fixture
        for tk_vgpm in d.get("vgpm", {}).values():
            assert tk_vgpm["growth"]["grade"] == "B"


def test_apply_mode_writes_updated_vgpm(tmp_archive_db):
    db_path, add = tmp_archive_db
    add("VZ", "2026-05-16T10:00:00+00:00", sector="Telco", rev_growth=0.04,
        existing_vgpm_grade="B")

    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=False)
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["runs_updated"] == 1

    # Verify the row was actually updated
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT full_result_json FROM web_runs WHERE ticker='VZ'").fetchone()
    d = json.loads(row[0])
    # Telco 4% growth should grade ABOVE B (sector-aware bands push it to A-band)
    new_growth_grade = d["vgpm"]["VZ"]["growth"]["grade"]
    assert new_growth_grade != "B", (
        f"Telco 4% growth should NOT stay at B (pre-fix collapse); "
        f"got {new_growth_grade}"
    )
    # Same change reflected in data.vgpm
    assert d["data"]["vgpm"]["VZ"]["growth"]["grade"] == new_growth_grade


def test_idempotent_after_apply(tmp_archive_db):
    """Second apply on already-backfilled DB is a no-op (runs_updated=0)."""
    db_path, add = tmp_archive_db
    add("VZ", "2026-05-16T10:00:00+00:00", sector="Telco", rev_growth=0.04)

    # First apply
    first = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                   dry_run=False)
    assert first["runs_updated"] >= 1

    # Second apply — should be no-op
    second = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=False)
    assert second["runs_updated"] == 0
    assert second["tickers_updated"] == 0


def test_pre_cutoff_runs_untouched(tmp_archive_db):
    """Anything before 'since' is skipped — even in apply mode."""
    db_path, add = tmp_archive_db
    add("OLD",   "2026-03-15T10:00:00+00:00", sector="Telco", rev_growth=0.04)  # pre-cutoff
    add("NEW",   "2026-05-15T10:00:00+00:00", sector="Telco", rev_growth=0.04)  # post-cutoff

    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-04-25T00:00:00+00:00",
                                    dry_run=False)
    # Only the NEW run is in the window
    assert result["runs_examined"] == 1

    # OLD row's JSON still has pre-fix B-band grade
    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT full_result_json FROM web_runs WHERE ticker='OLD'").fetchone()
    assert json.loads(old_row[0])["vgpm"]["OLD"]["growth"]["grade"] == "B"


def test_grade_changes_payload_captures_pre_post(tmp_archive_db):
    db_path, add = tmp_archive_db
    add("VZ", "2026-05-16T10:00:00+00:00", sector="Telco", rev_growth=0.04)

    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=True)
    assert len(result["grade_changes"]) >= 1
    chg = result["grade_changes"][0]
    assert chg["ticker"] == "VZ"
    assert "before" in chg
    assert "after" in chg
    # Before should be the pre-fix grade
    assert chg["before"]["growth"] == "B"
    # After should differ
    assert chg["after"]["growth"] != "B"


def test_missing_inputs_ticker_skipped(tmp_archive_db):
    """A row with no dcf_range / scenario_analysis for the ticker is skipped
    gracefully (not an error, just no recomputation)."""
    db_path, add = tmp_archive_db
    # Inject a row with empty data dict
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, ticker, run_at, full_result_json) "
            "VALUES (?, ?, ?, ?)",
            ("BAD_1", "BAD", "2026-05-16T10:00:00+00:00",
             json.dumps({"vgpm": {"BAD": {"valuation": {"score": 50, "grade": "B"}}},
                          "data": {"vgpm": {"BAD": {"valuation": {"score": 50, "grade": "B"}}}}})),
        )
    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=True)
    assert result["ok"] is True
    # Should not crash; ticker counts as skipped
    assert result["tickers_skipped"] >= 1


def test_unknown_db_path_fails_cleanly():
    """Missing DB path returns a clean error dict, not an exception."""
    result = backfill_vgpm_for_runs(db_path="/nonexistent/path/foo.db",
                                    dry_run=True)
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_malformed_json_logs_error_continues(tmp_archive_db):
    """A row with broken JSON should be logged but not stop the whole backfill."""
    db_path, add = tmp_archive_db
    # Good row
    add("GOOD", "2026-05-16T10:00:00+00:00", sector="Telco", rev_growth=0.04)
    # Bad row
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, ticker, run_at, full_result_json) "
            "VALUES (?, ?, ?, ?)",
            ("BAD_1", "BAD", "2026-05-17T10:00:00+00:00", "{ not json {{{"),
        )
    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=True)
    assert result["ok"] is True
    assert len(result["errors"]) >= 1
    # Good row still processed
    assert result["runs_with_vgpm"] >= 1


def test_sector_aware_grade_changes_for_telco(tmp_archive_db):
    """Headline integration check: Telco 4% growth was 'B' (pre-fix); after
    backfill the growth-dim letter MUST improve because the sector-aware
    Telecom band recognizes 4% growth as A-band-territory for telcos.

    NB: the OVERALL growth dim score is a weighted average of g1 + g2 + g3
    where only g1 (revenue growth band) is sector-aware. g2 (bull/bear
    asymmetry) and g3 (data source) are universal. With 4% rev_growth,
    g1 jumps from 44 (pre-fix Tech bands) to 70 (Telco bands), driving
    the dim from B-band to B+ — confirming the fix lands."""
    db_path, add = tmp_archive_db
    add("VZ", "2026-05-16T10:00:00+00:00", sector="Telco", rev_growth=0.04,
        existing_vgpm_grade="B")
    result = backfill_vgpm_for_runs(db_path=db_path, since_iso="2026-05-01T00:00:00+00:00",
                                    dry_run=False)
    assert result["runs_updated"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT full_result_json FROM web_runs").fetchone()
    new_growth = json.loads(row[0])["vgpm"]["VZ"]["growth"]
    # Grade letter MUST improve from B to B+ (or higher)
    assert new_growth["grade"] in ("B+", "A-", "A", "A+"), (
        f"Telco 4% growth grade should improve from B (pre-fix collapse) to "
        f">= B+. Got grade={new_growth['grade']} score={new_growth['score']}"
    )

"""Phase 8 tests — Layer B pattern aggregation.

Verifies:
  * SQL extraction of card_qa_audit from web_runs.full_result_json
  * Aggregation correctness (count, distinct tickers, evidence samples)
  * Schema-version segregation (v1 audits don't mix with v2)
  * Threshold filtering (9 fails → no alert, 10 fails → alert)
  * Top-N ranking by failure_count descending
  * Slack payload structure matches Auto Due-D conventions

Phase 8 Gate per plan:
  Synthetic 30-day audit dataset → top 10 ranked correctly
  Threshold tuning: 9 failures → no alert; 10 failures → alert
  Slack message format matches Auto Due-D conventions
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.agents.audit.pattern_analysis import (
    DEFAULT_THRESHOLD,
    MAX_SAMPLE_QUOTES,
    PatternRow,
    _aggregate_audits,
    analyze_card_failure_patterns,
)
from src.agents.audit.pattern_alerts import (
    PATTERN_PALETTE,
    _format_pattern_block,
    post_pattern_digest,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db_with_audits():
    """Create a temp web_runs SQLite seeded with N synthetic audit rows.

    Returns (db_path, builder) where builder is a callable to add rows.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)

    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE web_runs (
                run_id TEXT PRIMARY KEY,
                ticker TEXT,
                run_at TEXT,
                full_result_json TEXT
            )
        """)

    _counter = {"n": 0}

    def _add(
        ticker: str,
        run_at: str,
        card: str,
        field: str,
        evidence_quote: str = "",
        schema_version: int = 1,
        run_id: str | None = None,
    ):
        # Counter ensures uniqueness even when (ticker, run_at, card, field)
        # collides — common in threshold-boundary tests where many fails
        # share the same timestamp.
        _counter["n"] += 1
        rid = run_id or f"{ticker}_{run_at}_{card}_{field}_{_counter['n']}"
        payload = {
            "card_qa_audit": {
                ticker: {
                    "qa_version": "v1",
                    "qa_schema_versions": {card: schema_version},
                    "human_review_flags": [{
                        "card": card,
                        "field": field,
                        "reason": "extractor_dropped_per_judge",
                        "context": "synthetic",
                        "evidence_quote": evidence_quote,
                    }],
                }
            }
        }
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO web_runs (run_id, ticker, run_at, full_result_json) "
                "VALUES (?, ?, ?, ?)",
                (rid, ticker, run_at, json.dumps(payload)),
            )

    yield path, _add
    try:
        os.unlink(path)
    except OSError:
        pass


# ── _aggregate_audits ──────────────────────────────────────────────────────


def test_aggregate_groups_by_card_field_version():
    audits = [
        ("AAPL", "2026-05-01T10:00:00", {
            "qa_schema_versions": {"biopharma_pipeline_table": 1},
            "human_review_flags": [{
                "card":  "biopharma_pipeline_table",
                "field": "peak_sales_usd",
                "evidence_quote": "Peak: $1.5B by 2028",
            }],
        }),
        ("MSFT", "2026-05-02T10:00:00", {
            "qa_schema_versions": {"biopharma_pipeline_table": 1},
            "human_review_flags": [{
                "card":  "biopharma_pipeline_table",
                "field": "peak_sales_usd",
                "evidence_quote": "Peak: $2.0B by 2030",
            }],
        }),
        # Same card but different field → separate row
        ("AAPL", "2026-05-02T11:00:00", {
            "qa_schema_versions": {"biopharma_pipeline_table": 1},
            "human_review_flags": [{
                "card":  "biopharma_pipeline_table",
                "field": "phase_of_trial",
                "evidence_quote": "Phase III data Q2",
            }],
        }),
    ]
    rows = _aggregate_audits(audits)
    assert len(rows) == 2

    # Sort to make order deterministic for the assertion
    rows_by_field = {r.field: r for r in rows}
    assert rows_by_field["peak_sales_usd"].failure_count == 2
    assert rows_by_field["peak_sales_usd"].affected_tickers == ["AAPL", "MSFT"]
    assert rows_by_field["phase_of_trial"].failure_count == 1


def test_aggregate_segregates_by_schema_version():
    """Phase 8 invariant: v1 and v2 audits of the same card MUST be
    separate rows. Mixing them would corrupt the baseline when a
    schema entry is bumped."""
    audits = [
        ("A", "2026-05-01T10:00:00", {
            "qa_schema_versions": {"my_card": 1},
            "human_review_flags": [{"card": "my_card", "field": "f1"}],
        }),
        ("B", "2026-05-02T10:00:00", {
            "qa_schema_versions": {"my_card": 2},   # bumped
            "human_review_flags": [{"card": "my_card", "field": "f1"}],
        }),
    ]
    rows = _aggregate_audits(audits)
    assert len(rows) == 2
    versions = sorted(r.schema_version for r in rows)
    assert versions == [1, 2]


def test_aggregate_collects_sample_evidence_with_cap():
    """sample_evidence is capped at MAX_SAMPLE_QUOTES."""
    audits = []
    for i in range(MAX_SAMPLE_QUOTES + 3):
        audits.append(("T", f"2026-05-{i+1:02d}T10:00:00", {
            "qa_schema_versions": {"c": 1},
            "human_review_flags": [{
                "card": "c", "field": "f",
                "evidence_quote": f"quote_{i}",
            }],
        }))
    rows = _aggregate_audits(audits)
    assert len(rows) == 1
    assert len(rows[0].sample_evidence) == MAX_SAMPLE_QUOTES


def test_aggregate_dedupes_identical_evidence_quotes():
    """Don't waste space on duplicate quotes (same text from different runs)."""
    audits = []
    for i in range(5):
        audits.append(("T", f"2026-05-{i+1:02d}T10:00:00", {
            "qa_schema_versions": {"c": 1},
            "human_review_flags": [{
                "card": "c", "field": "f",
                "evidence_quote": "EXACTLY THE SAME QUOTE",
            }],
        }))
    rows = _aggregate_audits(audits)
    assert len(rows[0].sample_evidence) == 1  # deduped


def test_aggregate_handles_meta_check_flags():
    """Flags with card=None (Meta-Check failures) bucket under 'meta_check'."""
    audits = [
        ("ZTS", "2026-05-01T10:00:00", {
            "qa_schema_versions": {},
            "human_review_flags": [{
                "card": None, "field": None,
                "reason": "classification_likely_wrong",
                "context": "ZTS: HealthcareServices vs Biopharma",
            }],
        }),
    ]
    rows = _aggregate_audits(audits)
    assert len(rows) == 1
    assert rows[0].card == "meta_check"
    assert rows[0].field == "_classification"


def test_aggregate_ranks_by_failure_count_desc():
    """Most-failing patterns surface first → engineer triages highest-impact."""
    audits = [
        # 5x card_a.field_a
        *[("T", f"2026-05-{i+1:02d}", {
            "qa_schema_versions": {"card_a": 1},
            "human_review_flags": [{"card": "card_a", "field": "field_a"}],
        }) for i in range(5)],
        # 12x card_b.field_b
        *[("T", f"2026-05-{i+1:02d}", {
            "qa_schema_versions": {"card_b": 1},
            "human_review_flags": [{"card": "card_b", "field": "field_b"}],
        }) for i in range(12)],
        # 3x card_c.field_c
        *[("T", f"2026-05-{i+1:02d}", {
            "qa_schema_versions": {"card_c": 1},
            "human_review_flags": [{"card": "card_c", "field": "field_c"}],
        }) for i in range(3)],
    ]
    rows = _aggregate_audits(audits)
    # Order: card_b (12) → card_a (5) → card_c (3)
    assert [r.field for r in rows] == ["field_b", "field_a", "field_c"]
    assert [r.failure_count for r in rows] == [12, 5, 3]


# ── analyze_card_failure_patterns (end-to-end with DB) ─────────────────────


def test_analyze_with_real_db(tmp_db_with_audits):
    """Full path: DB → audit extraction → aggregation → ranked dict."""
    db_path, add = tmp_db_with_audits
    now = datetime.now(timezone.utc)

    # 15 failures of field_X (above threshold)
    for i in range(15):
        ts = (now - timedelta(days=i)).isoformat()
        add("AAPL", ts, "biopharma_pipeline_table", "field_X", f"quote_{i}")

    # 5 failures of field_Y (below threshold)
    for i in range(5):
        ts = (now - timedelta(days=i)).isoformat()
        add("MSFT", ts, "biopharma_pipeline_table", "field_Y", f"quote_y_{i}")

    result = analyze_card_failure_patterns(
        window_days=30, db_path=db_path, threshold=10, top_n=10,
    )

    assert result["total_audits_examined"] == 20  # 15 + 5
    assert result["total_patterns_identified"] == 2

    # Significant filter: only field_X passes threshold of 10
    assert len(result["significant_patterns"]) == 1
    assert result["significant_patterns"][0]["field"] == "field_X"
    assert result["significant_patterns"][0]["failure_count"] == 15

    # Top-N includes both regardless of threshold, ordered by count
    assert len(result["all_patterns_top_n"]) == 2
    assert result["all_patterns_top_n"][0]["field"] == "field_X"
    assert result["all_patterns_top_n"][1]["field"] == "field_Y"


def test_analyze_threshold_boundary_9_vs_10(tmp_db_with_audits):
    """Phase 8 GATE: 9 failures of field X → NO alert; 10 → alert.

    Plan-mandated test of the threshold semantics."""
    db_path, add = tmp_db_with_audits
    now = datetime.now(timezone.utc)
    for i in range(9):
        ts = (now - timedelta(days=i)).isoformat()
        add("T", ts, "card_x", "field_x", f"q_{i}")

    res_9 = analyze_card_failure_patterns(
        window_days=30, db_path=db_path, threshold=10,
    )
    assert len(res_9["significant_patterns"]) == 0  # 9 below threshold

    # Add the 10th
    add("T", now.isoformat(), "card_x", "field_x", "q_10")
    res_10 = analyze_card_failure_patterns(
        window_days=30, db_path=db_path, threshold=10,
    )
    assert len(res_10["significant_patterns"]) == 1
    assert res_10["significant_patterns"][0]["failure_count"] == 10


def test_analyze_window_filtering_excludes_old_runs(tmp_db_with_audits):
    """Failures outside the window should NOT be counted."""
    db_path, add = tmp_db_with_audits
    now = datetime.now(timezone.utc)

    # Inside window: 5 failures in last 30 days
    for i in range(5):
        ts = (now - timedelta(days=i)).isoformat()
        add("T", ts, "c", "f_inside", f"q_{i}")

    # Outside window: 100 ancient failures (60+ days ago)
    for i in range(100):
        ts = (now - timedelta(days=60 + i)).isoformat()
        add("T", ts, "c", "f_outside", f"q_old_{i}")

    res = analyze_card_failure_patterns(
        window_days=30, db_path=db_path, threshold=1,
    )

    # Should only see f_inside (window filter on run_at)
    fields = {p["field"] for p in res["all_patterns_top_n"]}
    assert "f_inside" in fields
    assert "f_outside" not in fields


def test_analyze_handles_empty_db_gracefully(tmp_db_with_audits):
    db_path, _ = tmp_db_with_audits   # no rows added
    res = analyze_card_failure_patterns(window_days=30, db_path=db_path)
    assert res["total_audits_examined"] == 0
    assert res["significant_patterns"] == []
    assert res["all_patterns_top_n"] == []


def test_analyze_handles_malformed_json_in_web_runs():
    """A row with broken JSON should be skipped, not crash the aggregation."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE web_runs (
                    run_id TEXT PRIMARY KEY, ticker TEXT, run_at TEXT,
                    full_result_json TEXT
                )
            """)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO web_runs VALUES (?, ?, ?, ?)",
                ("r1", "T", now, "{ not json {{{"),  # malformed
            )
        res = analyze_card_failure_patterns(window_days=30, db_path=db_path)
        assert res["total_audits_examined"] == 0  # malformed row skipped
    finally:
        # Windows can hold a SQLite file handle briefly after the
        # connection closes; tolerate transient PermissionError.
        try:
            os.unlink(db_path)
        except (OSError, PermissionError):
            pass


# ── Slack pattern_alerts ───────────────────────────────────────────────────


def test_pattern_alert_uses_distinct_palette():
    """Pattern alerts MUST use a different color/emoji from per-ticker DD
    alerts so the user can distinguish at-a-glance which kind they're
    seeing. Plan: 'reuses palette pattern' but adds new entry."""
    assert PATTERN_PALETTE["color"] != "#cc0000"  # not new_drop red
    assert PATTERN_PALETTE["color"] != "#1aaa55"  # not new_pump green
    assert PATTERN_PALETTE["color"] != "#3aa3e3"  # not reversal blue


def test_format_pattern_block_includes_all_key_fields():
    pattern = {
        "card": "biopharma_pipeline_table",
        "field": "peak_sales_usd",
        "schema_version": 1,
        "failure_count": 12,
        "affected_count": 4,
        "affected_tickers": ["MRNA", "BNTX", "INCY", "REGN"],
        "sample_evidence": ["Peak sales: $1.5B by 2028"],
    }
    block = _format_pattern_block(pattern)
    text = block["text"]["text"]
    assert "biopharma_pipeline_table" in text
    assert "peak_sales_usd" in text
    assert "12 failures" in text
    assert "4 tickers" in text
    assert "MRNA" in text
    assert "Peak sales" in text


def test_post_digest_returns_none_when_no_patterns():
    """No patterns → no Slack message wasted."""
    result = post_pattern_digest([], {"total_audits_examined": 0}, dry_run=True)
    assert result is None


def test_post_digest_dry_run_builds_payload():
    """Dry-run path lets tests verify payload without hitting Slack."""
    patterns = [{
        "card": "card_a", "field": "field_a",
        "schema_version": 1, "failure_count": 15,
        "affected_count": 3, "affected_tickers": ["A", "B", "C"],
        "sample_evidence": ["q1"],
    }]
    window = {
        "window_start_iso": "2026-04-20T00:00:00+00:00",
        "window_end_iso":   "2026-05-20T00:00:00+00:00",
        "total_audits_examined": 50,
    }
    resp = post_pattern_digest(patterns, window, dry_run=True)
    assert resp is not None
    payload = resp._payload   # type: ignore[attr-defined]
    assert payload["attachments"][0]["color"] == PATTERN_PALETTE["color"]
    body_text = json.dumps(payload)
    assert "card_a" in body_text
    assert "Engineering Backlog" in body_text

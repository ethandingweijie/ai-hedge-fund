"""Layer B — pattern aggregation over historical card_qa_audit data.

Daily cron analysis surfaces RECURRING failures so engineering can fix
the underlying extractor/schema/lookup bugs rather than patching one
run at a time. After N occurrences of the same (card, field) across
runs, a Slack alert fires (pattern_alerts.py) so a human can ship a
proper fix and the per-run QA cost drops on subsequent runs.

This is the "self-learning loop" half of the system: Layer A handles
individual runs in real-time; Layer B accumulates the failure history
and prioritizes the engineering backlog.

Public surface:
  analyze_card_failure_patterns(window_days=30, db_path=None)
    → list[PatternRow] sorted by failure_count DESC

Each PatternRow carries enough context to triage:
  * card + field (what to fix)
  * failure_count + affected_tickers (scope)
  * sample_evidence (verbatim quotes from the judge across runs)
  * schema_version (filter to consistent baseline; cross-version mixing
    would skew aggregations when a schema changed)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Default failure-count threshold for ranking. Used by the cron to decide
# whether a pattern is "significant enough" to surface in Slack.
DEFAULT_THRESHOLD: int = 10

# Days of audit history to aggregate over. 30 days is enough to spot
# weekly-recurring patterns; 7 days would miss biweekly issues.
DEFAULT_WINDOW_DAYS: int = 30

# Max sample evidence quotes to keep per pattern (storage + readability).
MAX_SAMPLE_QUOTES: int = 5

# How many top patterns to surface in Slack. 10 keeps the message brief
# while still covering the long tail of compound failures.
DEFAULT_TOP_N: int = 10


@dataclass
class PatternRow:
    """One row in the failure-pattern aggregation.

    Phase 8 Pattern Analysis filters audits by `schema_version` per card —
    a v2 audit that flags a newly-required field would skew v1 aggregates,
    so we segregate by version.
    """
    card:              str
    field:             str
    schema_version:    int
    failure_count:     int
    affected_tickers:  list[str] = field(default_factory=list)
    sample_evidence:   list[str] = field(default_factory=list)
    first_seen_at:     str | None = None
    last_seen_at:      str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "card":             self.card,
            "field":            self.field,
            "schema_version":   self.schema_version,
            "failure_count":    self.failure_count,
            "affected_tickers": sorted(set(self.affected_tickers))[:20],  # cap UI noise
            "affected_count":   len(set(self.affected_tickers)),
            "sample_evidence":  self.sample_evidence[:MAX_SAMPLE_QUOTES],
            "first_seen_at":    self.first_seen_at,
            "last_seen_at":     self.last_seen_at,
        }


# ── DB access ──────────────────────────────────────────────────────────────


def _resolve_db_path(db_path: str | None) -> str:
    """Default to the analysis_service DB path (where web_runs lives)."""
    if db_path:
        return db_path
    try:
        from app.backend.services.analysis_service import _get_db_path  # type: ignore
        return _get_db_path()
    except Exception:
        # Last-resort fallback — same location the screener / watchlist use.
        return str(Path("src") / "data" / "run_archive.db")


def _iter_recent_audits(
    db_path: str,
    cutoff_iso: str,
) -> list[tuple[str, str, dict]]:
    """Yield (ticker, run_at, card_qa_audit_dict) for every run since cutoff.

    Heavy-handed but simple — for a daily cron this scales to ~thousands
    of runs/day without trouble. If web_runs gets huge (millions of rows)
    we'd push the JSON-extraction down to SQL with sqlite's json_extract().
    """
    out: list[tuple[str, str, dict]] = []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT ticker, run_at, full_result_json
                FROM web_runs
                WHERE full_result_json IS NOT NULL
                  AND run_at >= ?
                ORDER BY run_at ASC
                """,
                (cutoff_iso,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("pattern_analysis: DB read failed: %s", exc)
        return []

    for r in rows:
        try:
            payload = json.loads(r["full_result_json"])
        except (TypeError, ValueError):
            continue
        # The pipeline persists card_qa_audit at two possible levels:
        # the return dict surfaces it at top-level (pipeline.py:892), and
        # state["data"]["card_qa_audit"] also exists. Check both.
        audits = payload.get("card_qa_audit")
        if not audits and isinstance(payload.get("data"), dict):
            audits = payload["data"].get("card_qa_audit")
        if not isinstance(audits, dict) or not audits:
            continue

        # audits is { ticker: audit_dict }. Sometimes the analysis is
        # single-ticker (ticker key matches row.ticker); sometimes
        # multi-ticker. Iterate ALL entries.
        for audit_ticker, audit in audits.items():
            if isinstance(audit, dict):
                out.append((audit_ticker, r["run_at"], audit))

    return out


# ── Aggregation ────────────────────────────────────────────────────────────


def _aggregate_audits(
    audits: list[tuple[str, str, dict]],
) -> list[PatternRow]:
    """Group flagged (card, field, schema_version) tuples across audits.

    A flag with `reason='budget_exhausted_mid_run'` is INCLUDED in counts —
    persistent budget exhaustion is itself a signal worth flagging (likely
    cause: too many failing cards per ticker → re-extract storms).

    Flags with `reason='classification_likely_wrong'` (Meta-Check failures)
    are also counted but the `card` slot is None — they roll up under a
    synthetic 'meta_check' card name for visibility.
    """
    # key: (card, field, schema_version) → accumulator
    grouped: dict[tuple[str, str, int], PatternRow] = {}

    for ticker, run_at, audit in audits:
        schema_versions = audit.get("qa_schema_versions") or {}

        for flag in audit.get("human_review_flags", []):
            if not isinstance(flag, dict):
                continue
            card_name = flag.get("card") or "meta_check"
            field_name = flag.get("field") or "_classification"
            version = schema_versions.get(card_name, 1)

            key = (card_name, field_name, version)
            if key not in grouped:
                grouped[key] = PatternRow(
                    card=card_name,
                    field=field_name,
                    schema_version=version,
                    failure_count=0,
                )

            row = grouped[key]
            row.failure_count += 1
            row.affected_tickers.append(ticker)

            quote = (flag.get("evidence_quote") or "").strip()
            if quote and quote not in row.sample_evidence:
                # Cap at MAX_SAMPLE_QUOTES at insertion time so the list
                # doesn't grow unbounded when one field fires 1000 times.
                if len(row.sample_evidence) < MAX_SAMPLE_QUOTES:
                    row.sample_evidence.append(quote[:500])

            if row.first_seen_at is None or run_at < row.first_seen_at:
                row.first_seen_at = run_at
            if row.last_seen_at is None or run_at > row.last_seen_at:
                row.last_seen_at = run_at

    # Sort descending by failure_count, then by affected ticker count
    rows = list(grouped.values())
    rows.sort(
        key=lambda r: (r.failure_count, len(set(r.affected_tickers))),
        reverse=True,
    )
    return rows


# ── Public entry point ─────────────────────────────────────────────────────


def analyze_card_failure_patterns(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    db_path: str | None = None,
    threshold: int = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Run the Layer B aggregation.

    Args:
      window_days: how many days of audit history to include
      db_path:     SQLite path (defaults to analysis_service DB)
      threshold:   minimum failure_count to include in "significant" set
      top_n:       cap on top patterns returned

    Returns:
      {
        "window_start_iso": "2026-04-20T00:00:00+00:00",
        "window_end_iso":   "2026-05-20T00:00:00+00:00",
        "total_audits_examined": int,
        "total_patterns_identified": int,
        "significant_patterns":     [PatternRow.as_dict(), ...],  # >= threshold
        "all_patterns_top_n":       [PatternRow.as_dict(), ...],  # top N regardless
      }
    """
    resolved_db = _resolve_db_path(db_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    cutoff_iso = cutoff.isoformat()

    audits = _iter_recent_audits(resolved_db, cutoff_iso)
    rows = _aggregate_audits(audits)

    significant = [r.as_dict() for r in rows if r.failure_count >= threshold]
    top_n_all   = [r.as_dict() for r in rows[:top_n]]

    return {
        "window_start_iso":         cutoff_iso,
        "window_end_iso":           now.isoformat(),
        "total_audits_examined":    len(audits),
        "total_patterns_identified":len(rows),
        "threshold":                threshold,
        "significant_patterns":     significant,
        "all_patterns_top_n":       top_n_all,
    }

"""Tests for src/agents/dd/alert_dedup.py — directional cooldown +
neutral-zone flip + high-water mark.

Each test gets an isolated SQLite file via the `tmp_db` fixture, which
overrides RUN_ARCHIVE_PATH to point at a tmp_path. No shared state across
tests; no pollution of production run_archive.db."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point alert_dedup at an isolated temp SQLite file.

    `_get_db_path()` reads RUN_ARCHIVE_PATH on every call (no caching), so
    setting the env var is sufficient — no module reload needed."""
    db_path = tmp_path / "test_dd_alerts.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    from src.agents.dd import alert_dedup
    # Truncate just in case (defensive — fresh tmp_path should already be empty)
    alert_dedup._clear_all_alerts_for_test()
    yield alert_dedup


def _utc(year: int, mon: int, day: int, hr: int = 0, mn: int = 0, sec: int = 0) -> datetime:
    return datetime(year, mon, day, hr, mn, sec, tzinfo=timezone.utc)


# ── First breach ────────────────────────────────────────────────────────────

def test_first_breach_drop_alerts(tmp_db):
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.11, current_price=89.0,
    )
    assert ok
    assert reason == "first_breach"


def test_first_breach_pump_alerts(tmp_db):
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=+0.12, current_price=112.0,
    )
    assert ok
    assert reason == "first_breach"


# ── Same direction within cooldown (no HWM) ─────────────────────────────────

def test_same_direction_within_cooldown_blocks_no_hwm(tmp_db):
    """Alert -11% at T0 trigger_price=$89, then T0+1h with -12% drop @ $88
    (additional drop only ~1%, well below HWM threshold). Should block."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.12, current_price=88.0,
        now=t0 + timedelta(hours=1),
    )
    assert not ok
    assert reason.startswith("in_cooldown")


# ── Direction flip — neutral-zone refinement ────────────────────────────────

def test_real_flip_drop_to_pump_crosses_extreme(tmp_db):
    """Alert -11% at T0 (DROP), then +11% at T0+2h (crosses +10% threshold)
    → True, direction_flip_DROP_to_PUMP."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=+0.11, current_price=111.0,
        now=t0 + timedelta(hours=2),
    )
    assert ok
    assert reason == "direction_flip_DROP_to_PUMP"


def test_real_flip_pump_to_drop_crosses_extreme(tmp_db):
    """Alert +11% at T0 (PUMP), then -11% at T0+2h (crosses -10% threshold)
    → True, direction_flip_PUMP_to_DROP."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="NVDA", direction="PUMP",
        pct=+0.11, price=111.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "NVDA", current_pct=-0.11, current_price=89.0,
        now=t0 + timedelta(hours=2),
    )
    assert ok
    assert reason == "direction_flip_PUMP_to_DROP"


def test_neutral_zone_no_flip_small_positive_bounce(tmp_db):
    """Alert -11% at T0 (DROP record). Then a +5% bounce — opposite SIGN
    but does NOT cross +10% extreme threshold. Per the user's neutral-zone
    rule, this is NOT a flip; should fall through to in_cooldown.

    (In production the upstream trigger gate would never call us with a
    sub-extreme value, but we test the predicate explicitly to enforce
    the spec.)"""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=+0.05, current_price=105.0,
        now=t0 + timedelta(hours=1),
    )
    assert not ok
    assert "direction_flip" not in reason
    assert reason.startswith("in_cooldown")


def test_neutral_zone_no_flip_at_threshold_boundary(tmp_db):
    """Alert -11% at T0. Then +9.9% — JUST under the +10% extreme. Must NOT
    flip (boundary test)."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=+0.099, current_price=109.9,
        now=t0 + timedelta(hours=1),
    )
    assert not ok
    assert "direction_flip" not in reason


def test_neutral_zone_flip_at_exact_threshold(tmp_db):
    """Alert -11% at T0. Then exactly +10.0% — AT the extreme threshold.
    Should flip (>= comparison)."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=+0.10, current_price=110.0,
        now=t0 + timedelta(hours=1),
    )
    assert ok
    assert reason == "direction_flip_DROP_to_PUMP"


# ── Cooldown expired ────────────────────────────────────────────────────────

def test_cooldown_expired_alerts(tmp_db):
    """Alert -11% at T0, then -11% at T0+25h (1h past cooldown) → True."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.11, current_price=89.0,
        now=t0 + timedelta(hours=25),
    )
    assert ok
    assert reason == "cooldown_expired"


def test_cooldown_just_under_24h_blocks(tmp_db):
    """T0+23h59m → still in cooldown."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.11, current_price=89.0,
        now=t0 + timedelta(hours=23, minutes=59),
    )
    assert not ok
    assert reason.startswith("in_cooldown")


# ── High-water mark ─────────────────────────────────────────────────────────

def test_drop_high_water_mark_triggers(tmp_db):
    """Alert -11% trigger_price=$89 at T0, then T0+3h price=$70.
    Additional drop = (89-70)/89 = 21.3% > 15% HWM threshold → True."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.30, current_price=70.0,
        now=t0 + timedelta(hours=3),
    )
    assert ok
    assert reason.startswith("high_water_mark")


def test_drop_high_water_mark_below_threshold_blocks(tmp_db):
    """Alert -11% trigger_price=$89, then T0+3h price=$80.
    Additional drop = (89-80)/89 = 10.1% < 15% HWM threshold → False."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "PEGA", current_pct=-0.20, current_price=80.0,
        now=t0 + timedelta(hours=3),
    )
    assert not ok
    assert reason.startswith("in_cooldown")


def test_pump_high_water_mark_triggers(tmp_db):
    """Alert +11% trigger_price=$111, then T0+3h price=$140.
    Additional pump = (140-111)/111 = 26.1% > 15% HWM threshold → True."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="NVDA", direction="PUMP",
        pct=+0.11, price=111.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "NVDA", current_pct=+0.40, current_price=140.0,
        now=t0 + timedelta(hours=3),
    )
    assert ok
    assert reason.startswith("high_water_mark")


def test_pump_high_water_mark_below_threshold_blocks(tmp_db):
    """Alert +11% trigger_price=$111, then T0+3h price=$120.
    Additional pump = (120-111)/111 = 8.1% < 15% HWM threshold → False."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="NVDA", direction="PUMP",
        pct=+0.11, price=111.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    ok, reason = tmp_db.check_alert_eligibility(
        "NVDA", current_pct=+0.20, current_price=120.0,
        now=t0 + timedelta(hours=3),
    )
    assert not ok
    assert reason.startswith("in_cooldown")


# ── Cooldown remaining helper ───────────────────────────────────────────────

def test_cooldown_remaining_during_lock(tmp_db):
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    rem = tmp_db.get_cooldown_remaining("PEGA", now=t0 + timedelta(hours=4))
    assert rem == timedelta(hours=20)


def test_cooldown_remaining_after_expiry(tmp_db):
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    assert tmp_db.get_cooldown_remaining("PEGA", now=t0 + timedelta(hours=25)) is None


def test_cooldown_remaining_for_unknown_ticker(tmp_db):
    assert tmp_db.get_cooldown_remaining("UNKNOWN") is None


# ── mark_alerted validation + persistence ───────────────────────────────────

def test_mark_alerted_rejects_invalid_direction(tmp_db):
    with pytest.raises(ValueError, match="DROP or PUMP"):
        tmp_db.mark_alerted(
            ticker="PEGA", direction="SIDEWAYS",
            pct=-0.11, price=89.0,
            tier="tier2_active", reason="first_breach",
        )


def test_mark_alerted_persists_full_record(tmp_db):
    """Round-trip: mark, then fetch latest, then assert all fields preserved."""
    t0 = _utc(2026, 5, 7, 14, 30, 15)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        quote={"changesPercentage": -11.0, "price": 89.0},
        cluster_id=None,
        dd_run_id="abc-123",
        sent_status="sent",
        now=t0,
    )
    rec = tmp_db.get_latest_alert("PEGA")
    assert rec is not None
    assert rec.ticker == "PEGA"
    assert rec.last_direction == "DROP"
    assert rec.trigger_price == 89.0
    assert rec.trigger_pct == -0.11
    assert rec.last_triggered_at == t0
    assert rec.tier == "tier2_active"
    assert rec.alert_reason == "first_breach"
    assert rec.cluster_id is None
    assert rec.dd_run_id == "abc-123"
    assert rec.sent_status == "sent"


def test_get_latest_alert_returns_most_recent_across_directions(tmp_db):
    """When a ticker has both DROP and PUMP records, latest wins."""
    t0 = _utc(2026, 5, 7, 14, 0)
    tmp_db.mark_alerted(
        ticker="NVDA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=t0,
    )
    tmp_db.mark_alerted(
        ticker="NVDA", direction="PUMP",
        pct=+0.11, price=111.0,
        tier="tier2_active", reason="direction_flip_DROP_to_PUMP",
        now=t0 + timedelta(hours=3),
    )
    rec = tmp_db.get_latest_alert("NVDA")
    assert rec is not None
    assert rec.last_direction == "PUMP"
    assert rec.alert_reason == "direction_flip_DROP_to_PUMP"


# ── cleanup_old_alerts (Phase 2B retention) ─────────────────────────────────


def test_cleanup_old_alerts_deletes_only_expired_rows(tmp_db):
    """Rows older than retention_days are deleted; fresher rows preserved."""
    now = datetime.now(timezone.utc)

    # 1 fresh, 1 stale (30 days old)
    tmp_db.mark_alerted(
        ticker="FRESH", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=now - timedelta(days=2),    # 2 days ago — within 7-day default
    )
    tmp_db.mark_alerted(
        ticker="STALE", direction="DROP",
        pct=-0.12, price=88.0,
        tier="tier2_active", reason="first_breach",
        now=now - timedelta(days=30),   # 30 days ago — well past 7
    )

    result = tmp_db.cleanup_old_alerts(retention_days=7)
    assert result["alerts_deleted"] == 1
    assert result["retention_days"] == 7

    # FRESH still around, STALE gone
    assert tmp_db.get_latest_alert("FRESH") is not None
    assert tmp_db.get_latest_alert("STALE") is None


def test_cleanup_old_alerts_zero_when_nothing_expired(tmp_db):
    now = datetime.now(timezone.utc)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        now=now - timedelta(days=1),
    )
    result = tmp_db.cleanup_old_alerts(retention_days=7)
    assert result["alerts_deleted"] == 0
    assert result["reports_deleted"] == 0


def test_cleanup_old_alerts_deletes_paired_dd_reports(tmp_db):
    """When a dd_alerts row is paired with a dd_reports row of the same age,
    both get deleted together."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(days=30)

    DD_RUN_ID = "dd-run-delete-me"
    tmp_db.upsert_dd_report(
        run_id=DD_RUN_ID,
        ticker="PEGA",
        model_name="dd_agent_qwen",
        full_result_json='{"report":{"cause_summary":"x"}}',
        run_at=stale,
    )
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        dd_run_id=DD_RUN_ID,
        now=stale,
    )

    result = tmp_db.cleanup_old_alerts(retention_days=7)
    assert result["alerts_deleted"] == 1
    assert result["reports_deleted"] == 1


def test_cleanup_never_touches_web_runs_in_new_architecture(tmp_db):
    """Phase 2B: cleanup_old_alerts deletes from dd_alerts + dd_reports
    only. web_runs rows (user ticker research) MUST never be touched by
    DD cleanup, regardless of what's in there."""
    from app.backend.services.analysis_service import _ensure_web_runs_table, _connect
    _ensure_web_runs_table()
    now = datetime.now(timezone.utc)

    # Pre-populate web_runs with a stale user research row
    stale_iso = (now - timedelta(days=400)).isoformat()
    SHARED_RUN_ID = "user-research-keep-me"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            (SHARED_RUN_ID, stale_iso, "PEGA", "warren_buffett_agent", "{}"),
        )
        conn.commit()

    # Also add a stale dd_alerts/dd_reports pair that COINCIDENTALLY shares
    # the same run_id (defensive — should never happen in practice)
    tmp_db.upsert_dd_report(
        run_id=SHARED_RUN_ID, ticker="PEGA", model_name="dd_agent_qwen",
        full_result_json="{}",
        run_at=now - timedelta(days=400),
    )
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP",
        pct=-0.11, price=89.0,
        tier="tier2_active", reason="first_breach",
        dd_run_id=SHARED_RUN_ID,
        now=now - timedelta(days=400),
    )

    tmp_db.cleanup_old_alerts(retention_days=7)

    # web_runs row survived
    with _connect() as conn:
        survived = conn.execute(
            "SELECT model_name FROM web_runs WHERE run_id = ?", (SHARED_RUN_ID,),
        ).fetchone()
    assert survived is not None
    assert survived[0] == "warren_buffett_agent"


# ── Phase 2B refactor: dd_reports table + upsert_dd_report ─────────────────


def test_upsert_dd_report_inserts_then_replaces(tmp_db):
    """upsert is INSERT-OR-REPLACE keyed by run_id."""
    tmp_db.upsert_dd_report(
        run_id="abc",
        ticker="PEGA",
        model_name="dd_agent_pending",
        full_result_json='{"report":{"cause_summary":"placeholder"}}',
    )
    tmp_db.upsert_dd_report(
        run_id="abc",
        ticker="PEGA",
        model_name="dd_agent_qwen",
        full_result_json='{"report":{"cause_summary":"real"}}',
    )
    # Single row with the latest content
    with tmp_db._conn() as conn:
        rows = conn.execute(
            "SELECT model_name, full_result_json FROM dd_reports WHERE run_id = ?",
            ("abc",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "dd_agent_qwen"
    assert "real" in rows[0][1]


# ── Phase 2B: purge_legacy_dd_rows_from_web_runs ────────────────────────────


def test_purge_legacy_only_deletes_dd_prefixed_web_runs(tmp_db):
    """Safety: only model_name LIKE 'dd_%' or = 'synthetic-dd-trigger'
    rows are removed from web_runs. Real ticker research is preserved."""
    from app.backend.services.analysis_service import _ensure_web_runs_table, _connect
    _ensure_web_runs_table()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        # 3 DD-prefixed legacy rows + 2 real research rows
        for run_id, model in [
            ("legacy-1", "dd_agent_qwen"),
            ("legacy-2", "dd_agent_pending"),
            ("legacy-3", "synthetic-dd-trigger"),
            ("research-1", "warren_buffett_agent"),
            ("research-2", "fundamentals_analyst_agent"),
        ]:
            conn.execute(
                "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
                "VALUES (?,?,?,?,?,0)",
                (run_id, now, "AAPL", model, "{}"),
            )
        conn.commit()

    result = tmp_db.purge_legacy_dd_rows_from_web_runs()
    assert result["web_runs_purged"] == 3

    # Research rows survived
    with _connect() as conn:
        survivors = {r[0] for r in conn.execute(
            "SELECT run_id FROM web_runs WHERE run_id IN ('research-1','research-2','legacy-1','legacy-2','legacy-3')"
        ).fetchall()}
    assert survivors == {"research-1", "research-2"}


def test_purge_legacy_is_idempotent(tmp_db):
    """Second call returns 0 (already purged)."""
    from app.backend.services.analysis_service import _ensure_web_runs_table, _connect
    _ensure_web_runs_table()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("x", datetime.now(timezone.utc).isoformat(), "AAPL", "dd_agent_qwen", "{}"),
        )
        conn.commit()
    r1 = tmp_db.purge_legacy_dd_rows_from_web_runs()
    r2 = tmp_db.purge_legacy_dd_rows_from_web_runs()
    assert r1["web_runs_purged"] == 1
    assert r2["web_runs_purged"] == 0

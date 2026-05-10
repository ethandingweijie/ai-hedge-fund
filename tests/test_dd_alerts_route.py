"""Tests for app/backend/routes/dd_alerts.py — admin trigger + read endpoints.

Phase C of the vertical slice. Uses FastAPI TestClient + tmp SQLite + mocked
Slack delivery. Verifies end-to-end flow:
  POST /admin/dd-trigger → alert_dedup → mark_alerted → web_runs row → Slack(mock)
  GET  /api/dd-alerts → list with hydrated report
  GET  /api/dd-alerts/digest/today → aggregate
  GET  /api/dd-alerts/{run_id} → detail

NOTE on agent_mode (Phase 2A wiring):
  Most tests pass ?agent_mode=synthetic to keep the route SYNCHRONOUS — the
  legacy Phase C behavior with an instant Slack post and the placeholder
  report inline. The real (default) async path is exercised in
  test_admin_trigger_real_mode_dispatches_background_thread which mocks the
  worker thread to verify the placeholder + mark_alerted side effects without
  actually calling Qwen.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


ADMIN_SECRET = "test-secret-xyz"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a minimal FastAPI app with ONLY the dd_alerts router so tests
    don't load the full backend (Ollama check, DB models, etc.). Isolated
    SQLite via tmp_path."""
    db_path = tmp_path / "test_dd_routes.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    monkeypatch.setenv("DB_UPLOAD_SECRET", ADMIN_SECRET)
    # Remove any inherited Slack URL so trigger uses 'skipped' path by default
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    # Force-import so the module-level ADMIN_SECRET reads our env var
    import importlib
    import app.backend.routes.dd_alerts as dd_route
    importlib.reload(dd_route)
    # Also reload alert_dedup so it picks up RUN_ARCHIVE_PATH (defensive)
    import src.agents.dd.alert_dedup as ad
    importlib.reload(ad)

    app = FastAPI()
    app.include_router(dd_route.router)
    return TestClient(app)


# ── Auth ────────────────────────────────────────────────────────────────────

def test_admin_trigger_rejects_missing_secret(client):
    r = client.post("/admin/dd-trigger?ticker=PEGA&pct=-0.11")
    assert r.status_code == 403


def test_admin_trigger_rejects_wrong_secret(client):
    r = client.post(f"/admin/dd-trigger?secret=wrong&ticker=PEGA&pct=-0.11")
    assert r.status_code == 403


# ── Direction inference + price defaulting ──────────────────────────────────

def test_admin_trigger_infers_drop_from_negative_pct(client):
    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    assert r.status_code == 200
    body = r.json()
    assert body["fired"] is True
    assert body["direction"] == "DROP"
    assert body["pct"] == -0.11
    # Default price = 100*(1+pct) = 89
    assert body["price"] == 89.0
    assert body["dd_run_id"]
    assert body["eligibility_reason"] == "first_breach"


def test_admin_trigger_infers_pump_from_positive_pct(client):
    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=NVDA&pct=0.12")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] == "PUMP"
    assert body["price"] == 112.0


def test_admin_trigger_explicit_direction_overrides(client):
    """Explicit direction param overrides sign inference. (Edge case — useful
    when testing flips with the same |pct| but flipped sign.)"""
    r = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11&direction=DROP"
    )
    assert r.status_code == 200
    assert r.json()["direction"] == "DROP"


def test_admin_trigger_invalid_direction_rejected(client):
    r = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11&direction=NEUTRAL"
    )
    assert r.status_code == 400


def test_admin_trigger_explicit_price_used(client):
    r = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11&price=85.50"
    )
    assert r.status_code == 200
    assert r.json()["price"] == 85.50


# ── Cooldown gate (cross-tier) ──────────────────────────────────────────────

def test_admin_trigger_blocks_when_in_cooldown(client):
    """Fire once → fires. Fire again → blocked by cooldown."""
    r1 = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    assert r1.json()["fired"] is True
    r2 = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.12")
    assert r2.json()["fired"] is False
    assert r2.json()["eligibility_reason"].startswith("in_cooldown")


def test_admin_trigger_force_bypasses_cooldown(client):
    """force=true overrides cooldown gate (useful for repeated testing
    without cooldown management)."""
    r1 = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    assert r1.json()["fired"] is True
    r2 = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11&force=true"
    )
    assert r2.json()["fired"] is True
    assert r2.json()["eligibility_reason"] == "admin_force_override"


# ── Slack integration (best-effort) ─────────────────────────────────────────

def test_admin_trigger_slack_skipped_when_webhook_unset(client):
    """Slack delivery is best-effort — when SLACK_WEBHOOK_URL is unset, the
    trigger still creates the alert + web_runs row and returns success."""
    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    body = r.json()
    assert body["fired"] is True
    assert body["slack"]["posted"] is False
    assert "not set" in body["slack"]["reason"].lower()


def test_admin_trigger_posts_to_slack_when_webhook_set(client, monkeypatch):
    """When SLACK_WEBHOOK_URL is set, the trigger calls slack_delivery
    (mocked to avoid real HTTP)."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test/FAKE/FAKE")
    fake_resp = MagicMock(status_code=200)
    with patch("src.agents.dd.slack_delivery.requests.post",
               return_value=fake_resp) as mock_post:
        r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    body = r.json()
    assert body["fired"] is True
    assert body["slack"]["posted"] is True
    assert body["slack"]["status_code"] == 200
    mock_post.assert_called_once()


def test_admin_trigger_continues_when_slack_post_raises(client, monkeypatch):
    """Slack failures (network, 5xx, etc.) must NOT bubble up — the alert
    row + web_runs row should still be created so the dashboard reflects
    the event regardless of push channel health."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test/FAKE/FAKE")
    with patch("src.agents.dd.slack_delivery.requests.post",
               side_effect=ConnectionError("network down")):
        r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    body = r.json()
    assert body["fired"] is True
    # Alert was still recorded (cooldown reflects it)
    r2 = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    assert r2.json()["fired"] is False
    assert r2.json()["eligibility_reason"].startswith("in_cooldown")


# ── List endpoint ───────────────────────────────────────────────────────────

def test_list_alerts_returns_empty_when_no_alerts(client):
    r = client.get("/api/dd-alerts")
    assert r.status_code == 200
    assert r.json() == []


def test_list_alerts_returns_recent_alerts_with_report(client):
    """Trigger an alert, then list — should return one row with hydrated
    report payload (from the joined web_runs row)."""
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    r = client.get("/api/dd-alerts")
    body = r.json()
    assert len(body) == 1
    item = body[0]
    assert item["ticker"] == "PEGA"
    assert item["last_direction"] == "DROP"
    assert item["trigger_pct"] == -0.11
    assert item["alert_reason"] == "first_breach"
    # Report hydrated from web_runs
    assert item["report"] is not None
    assert "SYNTHETIC" in item["report"]["cause_summary"]


def test_list_alerts_filter_by_direction(client):
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=NVDA&pct=+0.12")
    drops = client.get("/api/dd-alerts?direction=DROP").json()
    pumps = client.get("/api/dd-alerts?direction=PUMP").json()
    assert len(drops) == 1 and drops[0]["ticker"] == "PEGA"
    assert len(pumps) == 1 and pumps[0]["ticker"] == "NVDA"


def test_list_alerts_filter_by_ticker(client):
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=NVDA&pct=+0.12")
    only_pega = client.get("/api/dd-alerts?ticker=PEGA").json()
    assert len(only_pega) == 1
    assert only_pega[0]["ticker"] == "PEGA"


def test_list_alerts_limit_caps_result_count(client):
    for tk in ("AAA", "BBB", "CCC", "DDD"):
        client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker={tk}&pct=-0.11")
    body = client.get("/api/dd-alerts?limit=2").json()
    assert len(body) == 2


# ── Digest endpoint ─────────────────────────────────────────────────────────

def test_digest_today_aggregates_drops_and_pumps(client):
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=CRWD&pct=-0.15")
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=NVDA&pct=+0.12")
    r = client.get("/api/dd-alerts/digest/today")
    body = r.json()
    assert "date" in body
    assert len(body["drops"]) == 2
    assert len(body["pumps"]) == 1
    # Drops sorted by trigger_pct ASC (worst first) → CRWD before PEGA
    assert body["drops"][0]["ticker"] == "CRWD"
    assert body["drops"][1]["ticker"] == "PEGA"


def test_digest_today_returns_empty_clusters_by_default(client):
    """No cluster_id is set in admin_trigger paths, so clusters list is empty."""
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11")
    r = client.get("/api/dd-alerts/digest/today")
    assert r.json()["clusters"] == []


# ── Detail endpoint ─────────────────────────────────────────────────────────

def test_get_detail_returns_404_for_unknown_run_id(client):
    r = client.get("/api/dd-alerts/nonexistent-run-id")
    assert r.status_code == 404


def test_get_detail_returns_full_record(client):
    trigger = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11"
    ).json()
    run_id = trigger["dd_run_id"]
    r = client.get(f"/api/dd-alerts/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["dd_run_id"] == run_id
    assert body["ticker"] == "PEGA"
    assert body["report"] is not None


# ── Phase 2A: agent_mode branching ─────────────────────────────────────────


def test_admin_trigger_real_mode_dispatches_background_thread(client):
    """agent_mode=real (the default) should:
      - Insert a placeholder web_runs row immediately
      - Start a daemon thread named dd_agent_*
      - Return agent_status='running' WITHOUT waiting for the LLM
      - NOT return a 'slack' key (Slack fires later, from the thread)

    The actual thread runs but immediately bails out because
    DEEP_RESEARCH_API_KEY isn't set in the test env — that's fine; we're
    only verifying dispatch, not LLM behavior.
    """
    import threading
    pre_thread_count = threading.active_count()

    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=-0.11&agent_mode=real")
    body = r.json()

    assert r.status_code == 200
    assert body["fired"] is True
    assert body["agent_mode"] == "real"
    assert body["agent_status"] == "running"
    assert "dd_run_id" in body
    assert "slack" not in body, "Real mode must not Slack synchronously"
    assert "agent dispatched" in body["note"].lower()

    # Placeholder web_runs row should already be visible to the list endpoint
    listing = client.get("/api/dd-alerts").json()
    assert len(listing) == 1
    assert "agent generating" in listing[0]["report"]["cause_summary"].lower()

    # A daemon thread named dd_agent_PEGA_<short> got spawned
    found = any(
        t.name.startswith("dd_agent_PEGA_") and t.daemon
        for t in threading.enumerate()
    )
    assert found, f"No dd_agent thread spawned; active threads: {[t.name for t in threading.enumerate()]}"


def test_admin_trigger_off_mode_skips_agent_and_slack(client):
    """agent_mode=off records the alert + cooldown but skips both LLM agent
    and Slack post — useful for backfill / cooldown testing."""
    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=-0.11&agent_mode=off")
    body = r.json()

    assert body["fired"] is True
    assert body["agent_mode"] == "off"
    assert body["slack"]["posted"] is False
    assert body["slack"]["reason"] == "agent_mode=off"

    # Cooldown still recorded — second fire should be blocked
    r2 = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=-0.11&agent_mode=off")
    assert r2.json()["fired"] is False
    assert r2.json()["eligibility_reason"].startswith("in_cooldown")


def test_admin_trigger_invalid_agent_mode_rejected(client):
    r = client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=-0.11&agent_mode=invalid")
    assert r.status_code == 400


def test_admin_dd_cleanup_requires_secret(client):
    r = client.post("/admin/dd-cleanup")
    assert r.status_code == 403


# ── Phase 2C: sector cluster route ─────────────────────────────────────────


def test_admin_dd_trigger_cluster_requires_secret(client):
    r = client.post(
        "/admin/dd-trigger-cluster?sector=Tech&direction=DROP&members=A,B,C&pcts=-0.1,-0.1,-0.1&prices=100,100,100"
    )
    assert r.status_code == 403


def test_admin_dd_trigger_cluster_validates_array_lengths(client):
    """Mismatched member/pct/price arrays return 400."""
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=DROP&members=A,B,C&pcts=-0.1,-0.1&prices=100,100,100"
    )
    assert r.status_code == 400
    assert "same length" in r.json()["detail"].lower()


def test_admin_dd_trigger_cluster_validates_min_members(client):
    """A cluster of 1 isn't really a cluster — 400."""
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=DROP&members=A&pcts=-0.1&prices=100"
    )
    assert r.status_code == 400


def test_admin_dd_trigger_cluster_validates_direction(client):
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=NEUTRAL&members=A,B,C&pcts=-0.1,-0.1,-0.1&prices=100,100,100"
    )
    assert r.status_code == 400


def test_admin_dd_trigger_cluster_synthetic_path_writes_to_dd_reports(client):
    """agent_mode=synthetic writes one dd_reports row + member dd_alerts rows."""
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=DROP&members=CRM,NOW,NET"
        f"&pcts=-0.11,-0.12,-0.13&prices=200,800,60"
        f"&agent_mode=synthetic"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fired"] is True
    assert body["agent_mode"] == "synthetic"
    assert body["sector"] == "Tech"
    assert body["direction"] == "DROP"
    assert set(body["members"]) == {"CRM", "NOW", "NET"}
    cid = body["cluster_id"]

    # Verify the cluster report sits in dd_reports keyed by cluster_id
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        rep = conn.execute(
            "SELECT model_name FROM dd_reports WHERE run_id = ?", (cid,)
        ).fetchone()
    assert rep is not None
    assert "synthetic" in rep[0].lower() or "sector" in rep[0].lower()

    # Verify each member has a dd_alerts row tagged sent_status='cluster_member'
    # (or already-tagged if cleanup happened); cluster_id should match
    from src.agents.dd import alert_dedup
    with alert_dedup._conn() as conn:
        member_rows = conn.execute(
            "SELECT ticker, cluster_id, sent_status FROM dd_alerts WHERE cluster_id = ?",
            (cid,),
        ).fetchall()
    member_tickers = {row[0] for row in member_rows}
    assert member_tickers == {"CRM", "NOW", "NET"}
    for row in member_rows:
        assert row[1] == cid
        assert "cluster" in row[2]   # 'cluster_member' or 'sent' if cleanup done


def test_admin_dd_trigger_cluster_off_mode_no_slack(client):
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=DROP&members=A,B,C&pcts=-0.11,-0.12,-0.10&prices=100,100,100"
        f"&agent_mode=off"
    )
    body = r.json()
    assert body["fired"] is True
    assert body["agent_mode"] == "off"
    assert body["slack"]["posted"] is False


def test_admin_dd_trigger_cluster_real_mode_dispatches_thread(client):
    """Real mode returns immediately + spawns background thread."""
    import threading
    r = client.post(
        f"/admin/dd-trigger-cluster?secret={ADMIN_SECRET}"
        f"&sector=Tech&direction=DROP&members=A,B,C&pcts=-0.11,-0.12,-0.10&prices=100,100,100"
        f"&agent_mode=real"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fired"] is True
    assert body["agent_mode"] == "real"
    assert body["agent_status"] == "running"
    assert body["cluster_id"]
    # A daemon thread should be alive
    assert any(
        t.name.startswith("dd_sector_agent_") and t.daemon
        for t in threading.enumerate()
    )


# ── Phase 2E: EOD digest route ─────────────────────────────────────────────


def test_admin_dd_digest_requires_secret(client):
    r = client.post("/admin/dd-digest")
    assert r.status_code == 403


def test_admin_dd_digest_off_mode_returns_aggregates_only(client):
    """agent_mode=off aggregates today's data without LLM call or DB write."""
    # Fire a synthetic alert so today's aggregates are non-zero
    client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=PEGA&pct=-0.11"
    )
    r = client.post(f"/admin/dd-digest?secret={ADMIN_SECRET}&agent_mode=off")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_mode"] == "off"
    assert body["aggregates"]["n_drops"] == 1
    # No dd_reports row written for the digest (off mode)
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM dd_reports WHERE run_id LIKE 'digest_%'"
        ).fetchone()
    assert row[0] == 0


def test_admin_dd_digest_synthetic_writes_dd_reports_row(client):
    r = client.post(f"/admin/dd-digest?secret={ADMIN_SECRET}&agent_mode=synthetic")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_mode"] == "synthetic"
    assert body["run_id"].startswith("digest_")

    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        row = conn.execute(
            "SELECT model_name FROM dd_reports WHERE run_id = ?",
            (body["run_id"],),
        ).fetchone()
    assert row is not None
    assert "FALLBACK" in row[0] or "synthetic" in row[0].lower() or "digest" in row[0].lower()


def test_admin_dd_digest_invalid_agent_mode_rejected(client):
    r = client.post(f"/admin/dd-digest?secret={ADMIN_SECRET}&agent_mode=bogus")
    assert r.status_code == 400


def test_get_digest_today_includes_narrative_when_present(client):
    """After /admin/dd-digest synthetic runs, /digest/today surfaces the
    narrative payload."""
    # First call: no digest yet → narrative is None
    r1 = client.get("/api/dd-alerts/digest/today")
    assert r1.json()["narrative"] is None

    # Run synthetic digest
    client.post(f"/admin/dd-digest?secret={ADMIN_SECRET}&agent_mode=synthetic")

    r2 = client.get("/api/dd-alerts/digest/today")
    body = r2.json()
    assert body["narrative"] is not None
    assert "narrative" in body["narrative"]    # the inner key
    assert body["narrative"]["_model_name"]   # tagged with model name


def test_get_dd_universe_tier1_returns_watchlist(client):
    """The dd-dispatcher cron service hits this to fetch its monitoring
    universe. Verify it reflects what's in the watchlist DB."""
    # Seed the watchlist directly (no auth flow needed for tests)
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                added_at TEXT,
                user_id INTEGER
            )
        """)
        for t in ("CRM", "NOW", "PYPL"):
            conn.execute("INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
                         (t, "2026-05-10T00:00:00Z"))
        conn.commit()

    r = client.get("/api/dd-universe/tier1")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "tier1_watchlist"
    assert set(body["tickers"]) == {"CRM", "NOW", "PYPL"}
    assert body["count"] == 3


def test_get_dd_universe_tier1_empty_returns_empty_list(client):
    """No watchlist → returns empty list (cron service logs + skips tick)."""
    r = client.get("/api/dd-universe/tier1")
    assert r.status_code == 200
    assert r.json() == {"tier": "tier1_watchlist", "tickers": [], "count": 0}


def test_admin_dd_purge_legacy_requires_secret(client):
    r = client.post("/admin/dd-purge-legacy-web-runs")
    assert r.status_code == 403


def test_admin_dd_purge_legacy_returns_count(client):
    """Endpoint returns the count of rows removed from web_runs."""
    # Insert 2 fake DD-prefixed rows + 1 real research row directly
    from app.backend.services.analysis_service import _connect, _ensure_web_runs_table
    from datetime import datetime, timezone
    _ensure_web_runs_table()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("legacy-a", now, "PEGA", "dd_agent_qwen", "{}"),
        )
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("legacy-b", now, "PEGA", "synthetic-dd-trigger", "{}"),
        )
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("real-x", now, "PEGA", "warren_buffett_agent", "{}"),
        )
        conn.commit()

    r = client.post(f"/admin/dd-purge-legacy-web-runs?secret={ADMIN_SECRET}")
    assert r.status_code == 200
    assert r.json()["web_runs_purged"] == 2


def test_admin_dd_cleanup_returns_counts(client):
    """End-to-end: fire 2 alerts (one fresh, one synthetic-dated stale),
    cleanup with retention_days=0 deletes the stale one."""
    # Fire one fresh alert via the admin trigger (synthetic mode for speed)
    client.post(f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic&ticker=FRESH&pct=-0.11")
    # Cleanup with a long retention should delete nothing (alert is fresh)
    r = client.post(f"/admin/dd-cleanup?secret={ADMIN_SECRET}&retention_days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["alerts_deleted"] == 0
    # And with retention_days=0 (delete everything older than NOW), the
    # alert *just* fired is "older than now" by milliseconds → deleted.
    r2 = client.post(f"/admin/dd-cleanup?secret={ADMIN_SECRET}&retention_days=0")
    body2 = r2.json()
    assert body2["alerts_deleted"] >= 1


# ── Phase 2B architecture invariants — explicit user-requested checks ──────


def test_invariant_dd_runs_stored_in_dd_reports_not_web_runs(client):
    """End-to-end proof of the Phase 2B refactor:

    User requirement: 'DD runs are stored separately from web runs.
    DD runs are refreshed daily but web runs are permanent.'

    Verifies that firing /admin/dd-trigger writes to the dd_reports table,
    NOT to web_runs.
    """
    from app.backend.services.analysis_service import _connect

    # Fire one DD alert (synthetic to keep it sync)
    r = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic"
        f"&ticker=PEGA&pct=-0.11"
    )
    assert r.json()["fired"] is True
    run_id = r.json()["dd_run_id"]

    # ── Invariant 1: a row appeared in dd_reports ─────────────────────────
    with _connect() as conn:
        dd_row = conn.execute(
            "SELECT run_id, ticker, model_name FROM dd_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert dd_row is not None, "Expected a row in dd_reports for the new alert"
    assert dd_row[1] == "PEGA"
    assert dd_row[2].startswith("dd_")

    # ── Invariant 2: web_runs is UNTOUCHED ────────────────────────────────
    # First ensure the table exists (the dd-trigger flow no longer needs it,
    # which is itself a proof of separation — but we still need to query it
    # to verify zero DD rows leaked through). Touching this table is
    # intentional: it's the table the History tab reads from.
    from app.backend.services.analysis_service import _ensure_web_runs_table
    _ensure_web_runs_table()
    with _connect() as conn:
        web_row = conn.execute(
            "SELECT COUNT(*) FROM web_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
    assert web_row[0] == 0, "DD report MUST NOT appear in web_runs"


def test_invariant_auto_due_d_endpoint_reads_from_dd_reports(client):
    """User requirement: 'Auto Due-D side tab reads from dd_reports
    database and prints.'

    Verifies GET /api/dd-alerts hydrates the report payload via the
    dd_alerts ⨝ dd_reports JOIN — and that the payload is the real one
    written by the trigger (not a stale web_runs leak)."""
    client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&agent_mode=synthetic"
        f"&ticker=NVDA&pct=0.12"
    )
    r = client.get("/api/dd-alerts")
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["ticker"] == "NVDA"
    assert item["report"] is not None
    # The report must come through the dd_reports JOIN (not web_runs).
    # Direct verification: drop the dd_reports row, the GET should now
    # show report=None even though dd_alerts row still exists.
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM dd_reports WHERE run_id = ?", (item["dd_run_id"],))
        conn.commit()
    r2 = client.get("/api/dd-alerts")
    items2 = r2.json()
    assert len(items2) == 1
    assert items2[0]["report"] is None, (
        "After dropping dd_reports row, report must vanish — proving the JOIN "
        "target is dd_reports, not web_runs"
    )


def test_invariant_web_runs_clean_after_purge(client):
    """User requirement: 'Web runs no longer have DD runs data.'

    Tests the full cleanup story: /admin/dd-purge-legacy-web-runs removes
    pre-Phase-2B DD-prefixed rows from web_runs."""
    from app.backend.services.analysis_service import _connect, _ensure_web_runs_table
    from datetime import datetime, timezone
    _ensure_web_runs_table()

    # Simulate pre-Phase-2B state: a few legacy DD rows hanging out in web_runs
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for run_id, model in [
            ("legacy-dd-1", "dd_agent_qwen"),
            ("legacy-dd-2", "synthetic-dd-trigger"),
            ("legacy-dd-3", "dd_agent_pending"),
            ("user-research", "warren_buffett_agent"),  # MUST be preserved
        ]:
            conn.execute(
                "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
                "VALUES (?,?,?,?,?,0)",
                (run_id, now, "AAPL", model, "{}"),
            )
        conn.commit()

    # Run the purge
    r = client.post(f"/admin/dd-purge-legacy-web-runs?secret={ADMIN_SECRET}")
    assert r.json()["web_runs_purged"] == 3

    # All DD rows gone, user research preserved
    with _connect() as conn:
        rows = conn.execute("SELECT run_id, model_name FROM web_runs").fetchall()
    survivors = {r[0]: r[1] for r in rows}
    assert "legacy-dd-1" not in survivors
    assert "legacy-dd-2" not in survivors
    assert "legacy-dd-3" not in survivors
    assert survivors.get("user-research") == "warren_buffett_agent"


def test_admin_trigger_real_mode_includes_prior_direction(client):
    """The real-mode response should expose prior_direction so the caller
    knows which prompt the agent will run (DROP-after-PUMP → Reversal, etc.)."""
    # Fire DROP first
    r1 = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=-0.11&agent_mode=off"
    )
    assert r1.json()["prior_direction"] is None  # First-ever

    # Now fire PUMP that crosses the +10% threshold (direction flip)
    r2 = client.post(
        f"/admin/dd-trigger?secret={ADMIN_SECRET}&ticker=PEGA&pct=0.11&agent_mode=real"
    )
    body = r2.json()
    assert body["fired"] is True
    assert body["prior_direction"] == "DROP"   # Last alert was a DROP
    assert body["eligibility_reason"].startswith("direction_flip")

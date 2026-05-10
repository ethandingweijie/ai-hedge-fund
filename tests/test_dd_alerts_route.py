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

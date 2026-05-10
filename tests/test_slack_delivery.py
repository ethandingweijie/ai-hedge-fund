"""Tests for src/agents/dd/slack_delivery.py — palette mapping + payload
structure + webhook env-var fail-fast.

All HTTP calls are mocked. Real Slack delivery is verified manually at
Phase E end-to-end smoke (per user's choice in plan review)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_webhook(monkeypatch):
    """Set a placeholder SLACK_WEBHOOK_URL so post_dd_report's lazy lookup
    succeeds. Tests mock requests.post so no actual HTTP fires."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test/FAKE/FAKE")


@pytest.fixture
def sample_report():
    """Synthetic report dict matching the DD agent's structured output schema."""
    return {
        "cause_summary":       "Q4 earnings missed by $0.10; raised FY guidance offset by FX headwind.",
        "thesis_impact":       "thesis_intact — durability of recurring rev unchanged",
        "recommended_action":  "HOLD — wait for Q1 results to confirm normalization",
        "news_drivers": [
            {"title": "PEGA misses Q4 EPS by $0.10",
             "url":   "https://example.com/news/1",
             "publishedDate": "2026-05-07T14:00:00Z"},
            {"title": "Pegasystems raises FY guidance",
             "url":   "https://example.com/news/2",
             "publishedDate": "2026-05-07T14:30:00Z"},
        ],
        "filings": [
            {"form": "8-K", "filing_date": "2026-05-07",
             "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000000",
             "summary": "Item 2.02 Results of Operations"},
        ],
        "insider_signal": "neutral — no insider trades in last 14 days",
    }


# ── Palette mapping ─────────────────────────────────────────────────────────

def test_palette_drop_first_breach_is_new_drop():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    assert _palette_for("DROP", "first_breach") == PALETTE["new_drop"]


def test_palette_pump_first_breach_is_new_pump():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    assert _palette_for("PUMP", "first_breach") == PALETTE["new_pump"]


def test_palette_drop_cooldown_expired_treated_as_new_drop():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    assert _palette_for("DROP", "cooldown_expired") == PALETTE["new_drop"]


def test_palette_pump_cooldown_expired_treated_as_new_pump():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    assert _palette_for("PUMP", "cooldown_expired") == PALETTE["new_pump"]


def test_palette_drop_to_pump_flip_is_reversal():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    # Reversal palette regardless of post-flip direction
    assert _palette_for("PUMP", "direction_flip_DROP_to_PUMP") == PALETTE["reversal"]


def test_palette_pump_to_drop_flip_is_reversal():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    assert _palette_for("DROP", "direction_flip_PUMP_to_DROP") == PALETTE["reversal"]


def test_palette_drop_hwm_is_hwm_extension():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    reason = "high_water_mark(+21.3% from trigger)"
    assert _palette_for("DROP", reason) == PALETTE["hwm_extension"]


def test_palette_pump_hwm_is_hwm_extension():
    from src.agents.dd.slack_delivery import _palette_for, PALETTE
    reason = "high_water_mark(+26.1% from trigger)"
    assert _palette_for("PUMP", reason) == PALETTE["hwm_extension"]


def test_palette_colors_match_spec():
    """Lock the exact hex colors from the user's plan (table in plan
    section 4). Future drift would be caught here."""
    from src.agents.dd.slack_delivery import PALETTE
    assert PALETTE["new_drop"]["color"]      == "#cc0000"
    assert PALETTE["new_pump"]["color"]      == "#1aaa55"
    assert PALETTE["reversal"]["color"]      == "#3aa3e3"
    assert PALETTE["hwm_extension"]["color"] == "#800080"


def test_palette_tones_match_spec():
    from src.agents.dd.slack_delivery import PALETTE
    assert PALETTE["new_drop"]["tone"]      == "Crisis Management"
    assert PALETTE["new_pump"]["tone"]      == "Opportunity Assessment"
    assert PALETTE["reversal"]["tone"]      == "Narrative Shift"
    assert PALETTE["hwm_extension"]["tone"] == "Compounding Risk"


# ── No EOD digest function (digest is web-only per spec) ────────────────────

def test_no_post_eod_digest_function_exported():
    """EOD digest is web-only per plan section 6. The Slack module must
    NOT export a post_eod_digest function — guards against accidental
    addition."""
    from src.agents.dd import slack_delivery
    assert not hasattr(slack_delivery, "post_eod_digest"), (
        "EOD digest must be web-only; do not add post_eod_digest to "
        "slack_delivery — see plan section 6."
    )


# ── Payload structure ──────────────────────────────────────────────────────

def test_build_payload_includes_text_fallback(sample_report):
    """The top-level `text` field is the mobile-push preview / fallback
    when blocks aren't supported. Must include ticker + pct + tone."""
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    assert "PEGA" in p["text"]
    assert "-0.1" in p["text"] or "-11" in p["text"]   # signed pct rendering
    assert "Crisis Management" in p["text"]


def test_build_payload_attachment_color_matches_palette(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    assert p["attachments"][0]["color"] == "#cc0000"


def test_build_payload_blocks_have_required_sections(sample_report):
    """Every alert payload must have: header + context (reason) + cause/thesis
    section + recommended action + news + filings + run_id context."""
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    blocks = p["attachments"][0]["blocks"]

    # Header
    assert blocks[0]["type"] == "header"
    assert "PEGA" in blocks[0]["text"]["text"]

    # Reason context
    assert blocks[1]["type"] == "context"
    assert "first_breach" in blocks[1]["elements"][0]["text"]

    # Cause + thesis fields
    fields_block = next(b for b in blocks if b.get("type") == "section" and "fields" in b)
    field_texts = [f["text"] for f in fields_block["fields"]]
    assert any("Cause" in t for t in field_texts)
    assert any("Thesis impact" in t for t in field_texts)

    # Recommended action section
    assert any("Recommended action" in str(b) for b in blocks)

    # News + Filings
    assert any("News drivers" in str(b) for b in blocks)
    assert any("SEC filings" in str(b) for b in blocks)

    # Run ID footer
    assert any("abc-123" in str(b) for b in blocks)


def test_build_payload_includes_app_link_when_base_url_provided(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
        app_base_url="https://equitable.example.com",
    )
    blocks = p["attachments"][0]["blocks"]
    link_blocks = [b for b in blocks if "Open full report" in str(b)]
    assert len(link_blocks) == 1
    assert "equitable.example.com/dd-alerts/abc-123" in str(link_blocks[0])


def test_build_payload_omits_app_link_when_no_base_url(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    blocks = p["attachments"][0]["blocks"]
    assert not any("Open full report" in str(b) for b in blocks)


def test_build_payload_pump_uses_green_color(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="NVDA", pct_change=+0.11, direction="PUMP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    assert p["attachments"][0]["color"] == "#1aaa55"
    # Sign rendered as +
    assert "+0.1" in p["text"] or "+11" in p["text"]


def test_build_payload_reversal_uses_blue_color(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=+0.11, direction="PUMP",
        reason="direction_flip_DROP_to_PUMP", report=sample_report,
        run_id="abc-123",
    )
    assert p["attachments"][0]["color"] == "#3aa3e3"
    # Tone in text fallback
    assert "Narrative Shift" in p["text"]


def test_build_payload_hwm_uses_purple_color(sample_report):
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.30, direction="DROP",
        reason="high_water_mark(+21.3% from trigger)", report=sample_report,
        run_id="abc-123",
    )
    assert p["attachments"][0]["color"] == "#800080"
    assert "Compounding Risk" in p["text"]


# ── News + filings formatting ───────────────────────────────────────────────

def test_format_news_empty_returns_placeholder():
    from src.agents.dd.slack_delivery import _format_news
    assert "no recent news" in _format_news([]).lower()


def test_format_news_renders_link_when_url_present():
    from src.agents.dd.slack_delivery import _format_news
    out = _format_news([{"title": "Foo", "url": "https://x.com",
                         "publishedDate": "2026-05-07T14:00:00Z"}])
    assert "<https://x.com|Foo>" in out
    assert "2026-05-07" in out


def test_format_filings_empty_returns_placeholder():
    from src.agents.dd.slack_delivery import _format_filings
    assert "no SEC filings" in _format_filings([])


def test_format_filings_renders_form_and_summary():
    from src.agents.dd.slack_delivery import _format_filings
    out = _format_filings([{
        "form": "8-K", "filing_date": "2026-05-07",
        "url": "https://sec.gov/x", "summary": "Item 2.02 Results"
    }])
    assert "8-K" in out
    assert "Item 2.02" in out
    assert "<https://sec.gov/x|8-K>" in out


# ── Webhook env-var fail-fast ───────────────────────────────────────────────

def test_post_dd_report_raises_when_webhook_missing(monkeypatch, sample_report):
    """If SLACK_WEBHOOK_URL is not set, post_dd_report must fail fast at
    call time (not at import time — that would break unrelated tests)."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    from src.agents.dd.slack_delivery import post_dd_report
    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
        post_dd_report(
            ticker="PEGA", pct_change=-0.11, direction="DROP",
            reason="first_breach", report=sample_report, run_id="abc-123",
        )


def test_module_import_succeeds_without_webhook(monkeypatch):
    """Lazy lookup — the module must import cleanly even without env var,
    so unrelated test files (which don't touch Slack delivery) can import
    without setup."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    # Force a fresh import to verify
    import importlib
    import src.agents.dd.slack_delivery as mod
    importlib.reload(mod)
    # Module-level constants accessible without env var
    assert "new_drop" in mod.PALETTE


# ── Delivery happy path (mocked) ────────────────────────────────────────────

def test_post_dd_report_calls_requests_post_with_webhook_url(fake_webhook, sample_report):
    from src.agents.dd import slack_delivery
    fake_resp = MagicMock(status_code=200, text="ok")
    with patch.object(slack_delivery.requests, "post", return_value=fake_resp) as mock_post:
        resp = slack_delivery.post_dd_report(
            ticker="PEGA", pct_change=-0.11, direction="DROP",
            reason="first_breach", report=sample_report, run_id="abc-123",
        )
    assert resp is fake_resp
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["text"].startswith(":chart_with_downwards_trend:")
    # URL is the env var
    assert mock_post.call_args.args[0] == "https://hooks.slack.com/test/FAKE/FAKE"


def test_post_dd_report_passes_timeout(fake_webhook, sample_report):
    from src.agents.dd import slack_delivery
    fake_resp = MagicMock(status_code=200)
    with patch.object(slack_delivery.requests, "post", return_value=fake_resp) as mock_post:
        slack_delivery.post_dd_report(
            ticker="PEGA", pct_change=-0.11, direction="DROP",
            reason="first_breach", report=sample_report, run_id="abc-123",
            timeout_s=5.5,
        )
    assert mock_post.call_args.kwargs["timeout"] == 5.5


def test_post_dd_report_does_not_leak_webhook_url_into_payload(fake_webhook, sample_report):
    """Defensive: ensure the webhook URL never ends up serialized into the
    Slack payload itself (would be a credential leak in audit logs)."""
    from src.agents.dd.slack_delivery import build_payload
    p = build_payload(
        ticker="PEGA", pct_change=-0.11, direction="DROP",
        reason="first_breach", report=sample_report, run_id="abc-123",
    )
    import json
    serialized = json.dumps(p)
    assert "hooks.slack.com" not in serialized

"""Slack incoming-webhook delivery for the DD agent (Phase B vertical slice).

Real-time alerts only — NO end-of-day digest. EOD digest is web-only per
the plan (mighty-gliding-graham.md, section 6: "EOD digest: web-only").

Public API:
  post_dd_report(...) — single-ticker DD report
  _palette_for(direction, reason) — exposed for the web frontend so the
                                    dashboard can match Slack's color
                                    palette exactly
  PALETTE — dict of the 4 visual variants (also imported by frontend
            via the JSON-serialised report payload)

Visual taxonomy (per user's spec, plan section 4):
  new_drop      → red (#cc0000),    📉 :rotating_light:,   Crisis Management
  new_pump      → green (#1aaa55),  📈 :rocket:,           Opportunity Assessment
  reversal      → blue (#3aa3e3),   🔄 :left_right_arrow:, Narrative Shift
  hwm_extension → purple (#800080), ⚠️ :double_vertical_bar:, Compounding Risk

Webhook URL is read from SLACK_WEBHOOK_URL env var at call time (lazy —
imports succeed without the env var, but post_dd_report() raises
RuntimeError if missing). Webhook URL is a credential — must be set as
a Railway env var; never committed.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests


# ── Visual palette ──────────────────────────────────────────────────────────
# Frozen at module load — these constants ARE the spec.

PALETTE: dict[str, dict[str, str]] = {
    "new_drop": {
        "color": "#cc0000",
        "emoji": ":chart_with_downwards_trend: :rotating_light:",
        "tone":  "Crisis Management",
    },
    "new_pump": {
        "color": "#1aaa55",
        "emoji": ":chart_with_upwards_trend: :rocket:",
        "tone":  "Opportunity Assessment",
    },
    "reversal": {
        "color": "#3aa3e3",
        "emoji": ":arrows_counterclockwise: :left_right_arrow:",
        "tone":  "Narrative Shift",
    },
    "hwm_extension": {
        "color": "#800080",
        "emoji": ":warning: :heavy_minus_sign:",
        "tone":  "Compounding Risk",
    },
}


def _palette_for(direction: str, reason: str) -> dict[str, str]:
    """Map (direction, reason) → palette dict.

    Reason takes precedence over direction:
      - direction_flip_*  → reversal (blue), regardless of direction
      - high_water_mark*  → hwm_extension (purple), regardless of direction
      - first_breach OR cooldown_expired → new_drop or new_pump per direction

    `direction` should be 'DROP' or 'PUMP'. `reason` is the alert_reason
    string from alert_dedup.check_alert_eligibility."""
    if reason.startswith("direction_flip"):
        return PALETTE["reversal"]
    if reason.startswith("high_water_mark"):
        return PALETTE["hwm_extension"]
    return PALETTE["new_drop"] if direction == "DROP" else PALETTE["new_pump"]


# ── Webhook URL (lazy-read) ─────────────────────────────────────────────────

def _webhook_url() -> str:
    """Read SLACK_WEBHOOK_URL at call time. Imports succeed without it; only
    actual delivery fails fast. Lets unrelated tests import this module
    without setting the env var."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL env var is required for Slack delivery. "
            "Set it as a Railway env var (production) or via .env.local "
            "(local dev). Never commit the webhook URL to code."
        )
    return url


# ── Block builders ──────────────────────────────────────────────────────────

def _format_news(items: list[dict]) -> str:
    """News drivers list → markdown bullets. Truncates titles + cites date."""
    if not items:
        return "_no recent news drivers surfaced_"
    lines = []
    for item in items:
        title = (item.get("title") or "").strip()[:120]
        url   = item.get("url") or ""
        date  = item.get("date") or item.get("publishedDate") or ""
        date_str = date[:10] if date else ""
        if url:
            lines.append(f"• <{url}|{title}>{f' — {date_str}' if date_str else ''}")
        else:
            lines.append(f"• {title}{f' — {date_str}' if date_str else ''}")
    return "\n".join(lines)


def _format_filings(items: list[dict]) -> str:
    """SEC filings list → markdown bullets."""
    if not items:
        return "_no SEC filings in lookback window_"
    lines = []
    for item in items:
        form = item.get("form") or item.get("type") or "?"
        date = item.get("filing_date") or item.get("date") or ""
        url  = item.get("url") or ""
        summary = (item.get("summary") or item.get("title") or "").strip()[:140]
        date_str = date[:10] if date else ""
        if url:
            lines.append(f"• <{url}|{form}> {date_str} — {summary}")
        else:
            lines.append(f"• {form} {date_str} — {summary}")
    return "\n".join(lines)


def _build_blocks(*, ticker: str, pct_change: float, palette: dict[str, str],
                  reason: str, report: dict[str, Any], run_id: str,
                  app_base_url: str | None) -> list[dict]:
    """Build the Slack blocks array. Pure function — no I/O."""
    sign = "+" if pct_change > 0 else ""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{ticker}  {sign}{pct_change:.1f}%  ·  {palette['tone']}",
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{palette['emoji']}  *{reason}*"}],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Cause*\n{report.get('cause_summary', '_n/a_')}"},
                {"type": "mrkdwn", "text": f"*Thesis impact*\n{report.get('thesis_impact', '_n/a_')}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recommended action*\n{report.get('recommended_action', '_n/a_')}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*News drivers*\n" + _format_news(report.get("news_drivers", [])[:3]),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*SEC filings*\n" + _format_filings(report.get("filings", [])[:3]),
            },
        },
    ]

    # Optional "Open in Equitable" deep-link if app_base_url provided
    if app_base_url:
        link = f"{app_base_url.rstrip('/')}/dd-alerts/{run_id}"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{link}|Open full report in Equitable →>"},
        })

    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"insider: {report.get('insider_signal', 'n/a')}  ·  "
                f"run_id: `{run_id}`  ·  "
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
            ),
        }],
    })

    return blocks


def build_payload(*, ticker: str, pct_change: float, direction: str,
                  reason: str, report: dict[str, Any], run_id: str,
                  app_base_url: str | None = None) -> dict[str, Any]:
    """Build the full Slack payload dict (text + attachments). Pure function;
    exposed for tests + for callers that want to preview the payload before
    sending (e.g. dry-run mode)."""
    palette = _palette_for(direction, reason)
    sign    = "+" if pct_change > 0 else ""
    return {
        # `text` is the fallback / mobile-notification preview
        "text": f"{palette['emoji']} {ticker} {sign}{pct_change:.1f}% — {palette['tone']}",
        "attachments": [{
            "color":  palette["color"],
            "blocks": _build_blocks(
                ticker=ticker, pct_change=pct_change, palette=palette,
                reason=reason, report=report, run_id=run_id,
                app_base_url=app_base_url,
            ),
        }],
    }


# ── Delivery ────────────────────────────────────────────────────────────────

def post_dd_report(*, ticker: str, pct_change: float, direction: str,
                   reason: str, report: dict[str, Any], run_id: str,
                   app_base_url: str | None = None,
                   timeout_s: float = 10.0) -> requests.Response:
    """POST a single-ticker DD report to the configured Slack webhook.

    Real-time alerts only — there is intentionally NO post_eod_digest()
    function in this module. EOD digest is rendered on the web dashboard
    only, per the user's design (plan section 6).

    Args:
      ticker:       e.g. 'PEGA'
      pct_change:   signed decimal, e.g. -0.11 for -11%
      direction:    'DROP' or 'PUMP'
      reason:       string from alert_dedup.check_alert_eligibility
                    ('first_breach', 'direction_flip_DROP_to_PUMP', etc.)
      report:       structured report dict from the DD agent. Expected keys:
                    cause_summary, thesis_impact, recommended_action,
                    news_drivers (list), filings (list), insider_signal
      run_id:       link key to the full report in web_runs (used in deep-link)
      app_base_url: optional — if provided, payload includes an
                    "Open in Equitable →" link
      timeout_s:    HTTP timeout (default 10s)

    Returns the requests.Response object so callers can log status_code.
    Raises RuntimeError if SLACK_WEBHOOK_URL env is missing.
    """
    payload = build_payload(
        ticker=ticker, pct_change=pct_change, direction=direction,
        reason=reason, report=report, run_id=run_id,
        app_base_url=app_base_url,
    )
    return requests.post(_webhook_url(), json=payload, timeout=timeout_s)

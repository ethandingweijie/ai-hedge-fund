"""
src/research_ideas/contrarian/notifier.py
==========================================
Outbound notifications for newly-generated contrarian ideas.

Slack: posts a formatted Block Kit message to the webhook URL in
SLACK_WEBHOOK_URL. No-op if the env var is missing — the rest of the
generation pipeline always succeeds regardless of notification result.

Usage:
    from src.research_ideas.contrarian.notifier import notify_slack
    notify_slack(idea_dict, app_base_url="https://your-app.railway.app")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


_MODE_EMOJI = {
    "deep_value":           ":mag:",      # 🔍 bottom-up dig
    "thematic_geographic":  ":earth_asia:",  # 🌏 country/region rotation
    "thematic_sector":      ":chart_with_upwards_trend:",  # 📈 industry trend
    "special_situation":    ":dart:",     # 🎯 corporate-action event
}

_MODE_LABEL = {
    "deep_value":          "Deep Value",
    "thematic_geographic": "Geographic Theme",
    "thematic_sector":     "Sector Theme",
    "special_situation":   "Special Situation",
}


def _build_slack_payload(idea: dict, app_base_url: Optional[str] = None) -> dict:
    """Build the Slack Block Kit payload for an idea."""
    mode = idea.get("idea_mode") or "deep_value"
    emoji = _MODE_EMOJI.get(mode, ":bulb:")
    mode_label = _MODE_LABEL.get(mode, mode)
    ticker = idea.get("ticker", "?")
    company = idea.get("company_name") or ""

    region = idea.get("region")
    sector = idea.get("sector")
    mcap_b = (idea.get("market_cap_usd") or 0) / 1e9 if idea.get("market_cap_usd") else None

    # Header bits
    meta_bits = []
    if region:
        meta_bits.append(f"*Region:* {region}")
    if sector:
        meta_bits.append(f"*Sector:* {sector}")
    if mcap_b:
        meta_bits.append(f"*Mcap:* ${mcap_b:.1f}B")
    meta_bits.append(f"*Conviction:* {idea.get('conviction_score', '?')}/10")
    meta_line = "  ·  ".join(meta_bits)

    # Theme block (only for thematic modes)
    theme_text = idea.get("theme") or idea.get("industry_theme") or ""

    # Compose blocks
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} Research Idea of the Day: {ticker}",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{mode_label}*  ·  {company}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Hypothesis:* {idea.get('hypothesis', '(none)')}",
            },
        },
    ]

    if theme_text:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Theme:* {theme_text[:500]}",
            },
        })

    blocks.extend([
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Deep Value ({idea.get('deep_value_score', '?')}/10):* "
                    f"{(idea.get('deep_value_angle') or '')[:300]}"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Asymmetric ({idea.get('asymmetry_score', '?')}/10):* "
                    f"{(idea.get('asymmetric_angle') or '')[:300]}"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Contrarian ({idea.get('contrarian_score', '?')}/10):* "
                    f"{(idea.get('contrarian_angle') or '')[:300]}"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Catalyst:* {idea.get('primary_catalyst', '?')}"
                    + (f"  ·  _{idea.get('catalyst_timeline')}_" if idea.get("catalyst_timeline") else "")
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": meta_line},
            ],
        },
    ])

    # Action button — link to the idea in the app
    if app_base_url and idea.get("idea_id"):
        url = f"{app_base_url.rstrip('/')}/#/research-ideas/idea-of-the-day/{idea['idea_id']}"
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in app  ↗"},
                    "url": url,
                    "style": "primary",
                },
            ],
        })

    return {
        # Fallback text for notifications / mobile previews
        "text": f"{emoji} Research Idea of the Day: {ticker} — {idea.get('hypothesis', '')[:200]}",
        "blocks": blocks,
    }


def notify_slack(idea: dict, app_base_url: Optional[str] = None) -> bool:
    """
    Post the idea to Slack if SLACK_WEBHOOK_URL is configured.

    Returns True on success, False on any failure (including missing
    webhook URL). Never raises — the caller's generation flow shouldn't
    fail because Slack is down.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.info(
            "notify_slack: SLACK_WEBHOOK_URL not set — skipping Slack push "
            "for idea %s", idea.get("idea_id", "?"),
        )
        return False

    app_url = app_base_url or os.environ.get("APP_BASE_URL")
    payload = _build_slack_payload(idea, app_base_url=app_url)

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            logger.info(
                "notify_slack: posted idea %s (%s) to Slack OK",
                idea.get("idea_id", "?"), idea.get("ticker", "?"),
            )
            return True
        logger.warning(
            "notify_slack: Slack webhook returned %d: %s",
            resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.warning("notify_slack: post failed: %s", exc)
        return False

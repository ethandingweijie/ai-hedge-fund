"""
src/research_ideas/fundflow/notifier.py
========================================
Outbound Slack notification for the weekly geographic fund-flow brief.

Mirrors src/research_ideas/contrarian/notifier.py — Block Kit payload, posts
to SLACK_WEBHOOK_URL, no-op when that env var is missing, and never raises so
a Slack outage can never fail the run that produced the brief.

The message is a condensed version of the page's summary panel: the headline
and regime, a compact inflow/outflow league table, the key changes and the
implications. Slack truncates aggressively and nobody reads a wall of text in
a channel, so the bullet lists are capped and the per-region table is limited
to the strongest movers at each end rather than all nine rows.

Usage:
    from src.research_ideas.fundflow.notifier import notify_slack
    notify_slack(cohort_dict, app_base_url="https://your-app.railway.app")
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


# Slack hard-limits a section block's text to 3000 characters; these caps keep
# the message well inside that and, more importantly, readable in a channel.
_MAX_BULLETS = 5
_MAX_BULLET_CHARS = 300
_TABLE_ROWS_PER_SIDE = 4


def _usd(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    sign = "-" if x < 0 else "+"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}bn"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f}m"
    return f"{sign}${a / 1e3:.0f}k"


def _sig(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:+.2f}σ"


def _signed(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+.0f}"


def _bullets(items: list[str], limit: int = _MAX_BULLETS) -> str:
    if not items:
        return "_none_"
    out = []
    for it in items[:limit]:
        t = it.strip()
        if len(t) > _MAX_BULLET_CHARS:
            t = t[: _MAX_BULLET_CHARS - 1].rstrip() + "…"
        out.append(f"• {t}")
    return "\n".join(out)


def _region_line(r: dict) -> str:
    """One league-table row: flag, label, composite, pressure, issuer flow."""
    emoji = r.get("emoji") or ""
    label = r.get("label") or r.get("region") or "?"
    issuer = r.get("implied_flow_21d")
    issuer_txt = _usd(issuer) if issuer is not None else "n/a"
    return (
        f"{emoji} *{label}*  `{_signed(r.get('composite'))}`  "
        f"{_sig(r.get('cmf_z_21'))} 1M  ·  issuer {issuer_txt}"
    )


def _build_slack_payload(cohort: dict, app_base_url: Optional[str] = None) -> dict:
    summary: dict[str, Any] = cohort.get("summary") or {}
    regions: list[dict] = cohort.get("regions") or []

    headline = summary.get("headline") or "Weekly geographic fund-flow brief."
    regime = summary.get("regime") or ""
    inflow_n = cohort.get("inflow_count") or 0
    outflow_n = cohort.get("outflow_count") or 0

    ranked = sorted(regions, key=lambda r: -(r.get("composite") or 0))
    inflows = [r for r in ranked if (r.get("composite") or 0) > 0][:_TABLE_ROWS_PER_SIDE]
    outflows = [r for r in reversed(ranked) if (r.get("composite") or 0) < 0][:_TABLE_ROWS_PER_SIDE]

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":earth_asia: Weekly Fund Flow — Geographic",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"*{inflow_n} inflow*  ·  *{outflow_n} outflow*  across "
                    f"{cohort.get('region_count', 0)} geographies"
                    + (f"  ·  _{regime}_" if regime else "")
                ),
            }],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": headline[:2900]},
        },
        {"type": "divider"},
    ]

    if inflows:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":large_green_circle: *Money moving in*\n"
                        + "\n".join(_region_line(r) for r in inflows),
            },
        })
    if outflows:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":red_circle: *Money moving out*\n"
                        + "\n".join(_region_line(r) for r in outflows),
            },
        })

    if summary.get("key_changes"):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*What changed*\n" + _bullets(summary["key_changes"]),
            },
        })

    if summary.get("implications"):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Implications*\n" + _bullets(summary["implications"]),
            },
        })

    if summary.get("watch_items"):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Watch*\n" + _bullets(summary["watch_items"], 3),
            },
        })

    # Provenance — the reader should always know whether the prose was written
    # by the model or fell back to the computed draft.
    src = summary.get("summary_source") or "deterministic"
    model = summary.get("model_used")
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                (f"Brief written by {model}" if src == "deepseek" and model
                 else "Computed brief (AI narrator unavailable this run)")
                + "  ·  flow pressure is tape-derived (σ vs each region's own "
                  "baseline); issuer flow is measured creation/redemption"
            ),
        }],
    })

    if app_base_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Open in app  ↗"},
                "url": f"{app_base_url.rstrip('/')}/#/research-ideas/fundflow",
                "style": "primary",
            }],
        })

    return {
        "text": f":earth_asia: Weekly Fund Flow — {headline[:200]}",
        "blocks": blocks,
    }


def notify_slack(cohort: dict, app_base_url: Optional[str] = None) -> bool:
    """
    Post the weekly fund-flow brief to Slack if SLACK_WEBHOOK_URL is set.

    Returns True on success, False on any failure (including a missing webhook
    URL). Never raises — the scheduled run must not fail because Slack is down.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.info(
            "fundflow notify_slack: SLACK_WEBHOOK_URL not set — skipping push "
            "for run %s", cohort.get("run_id", "?"),
        )
        return False

    app_url = app_base_url or os.environ.get("APP_BASE_URL")
    payload = _build_slack_payload(cohort, app_base_url=app_url)

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            logger.info(
                "fundflow notify_slack: posted run %s to Slack OK",
                cohort.get("run_id", "?"),
            )
            return True
        logger.warning(
            "fundflow notify_slack: webhook returned %d: %s",
            resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.warning("fundflow notify_slack: post failed: %s", exc)
        return False

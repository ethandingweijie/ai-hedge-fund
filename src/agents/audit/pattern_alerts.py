"""Layer B Slack alerts — fires when failure patterns cross threshold.

Posts a single consolidated Slack message per daily cron run containing
the top N significant (≥threshold) failure patterns. Reuses the existing
SLACK_WEBHOOK_URL infrastructure from slack_delivery.py. A NEW palette
entry "pattern_alert" distinguishes these META alerts (engineering
backlog signal) from the existing per-ticker DD alerts (real-time
market events).

Why distinct palette: same Slack channel is fine, but the alert TYPE is
fundamentally different. A ticker alert is "look at this stock NOW". A
pattern alert is "fix this code by end of week so the pile of QA flags
stops accumulating". Conflating the two would mute one or the other.

Public surface:
  post_pattern_digest(patterns: list[dict], window_summary: dict)
    -> requests.Response
  PATTERN_PALETTE  -> the chosen colors/emoji (single source of truth)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


# Distinct palette so pattern digests don't visually collide with ticker
# alerts. Yellow/gold conveys "needs review" without crisis-red urgency.
PATTERN_PALETTE: dict[str, str] = {
    "color": "#f2c744",
    "emoji": ":mag: :memo:",
    "tone":  "Engineering Backlog",
}


def _webhook_url() -> str:
    """Lazy read of SLACK_WEBHOOK_URL — same pattern as slack_delivery.py."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL env var is required for pattern alerts. "
            "Set as Railway env var (production)."
        )
    return url


def _format_pattern_block(p: dict[str, Any]) -> dict[str, Any]:
    """Render one PatternRow.as_dict() into a Slack section block."""
    card  = p.get("card", "?")
    field = p.get("field", "?")
    count = p.get("failure_count", 0)
    ticker_count = p.get("affected_count", 0)
    tickers = p.get("affected_tickers", [])[:6]
    tickers_str = ", ".join(tickers)
    if ticker_count > len(tickers):
        tickers_str += f" +{ticker_count - len(tickers)} more"
    version = p.get("schema_version", 1)

    header = (
        f"*{card}.{field}* (v{version}) — {count} failures across {ticker_count} tickers"
    )
    body_lines = [header]
    if tickers:
        body_lines.append(f"  Affected: {tickers_str}")

    # Sample evidence — up to 3 quotes inline, each <=200 chars
    quotes = p.get("sample_evidence", [])[:3]
    for q in quotes:
        truncated = q.replace("\n", " ").strip()[:200]
        body_lines.append(f"  > {truncated}")

    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
    }


def post_pattern_digest(
    patterns: list[dict[str, Any]],
    window_summary: dict[str, Any],
    *,
    dry_run: bool = False,
) -> requests.Response | None:
    """Post a consolidated Slack message with the top patterns.

    Returns None when there's nothing to post (empty patterns list).
    Returns the requests.Response on success.

    Args:
      patterns:        list of PatternRow.as_dict() from analyze_card_failure_patterns
      window_summary:  dict with window_start_iso / window_end_iso / total_audits_examined
      dry_run:         if True, build the payload but don't POST. Used by tests.

    Raises RuntimeError if SLACK_WEBHOOK_URL is missing AND patterns is non-empty.
    """
    if not patterns:
        logger.info("post_pattern_digest: no significant patterns — skipping Slack post")
        return None

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Card QA Patterns — {PATTERN_PALETTE['tone']}",
            },
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"{PATTERN_PALETTE['emoji']}  "
                    f"*{len(patterns)} patterns* over "
                    f"{window_summary.get('window_start_iso', '?')[:10]} → "
                    f"{window_summary.get('window_end_iso', '?')[:10]}  "
                    f"({window_summary.get('total_audits_examined', 0)} audits scanned)"
                ),
            }],
        },
        {"type": "divider"},
    ]

    for p in patterns:
        blocks.append(_format_pattern_block(p))

    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                "_Action: review the top entries and ship a fix at the extractor / "
                "schema / lookup-table level. The QA layer auto-remediates per-run, "
                "but recurring patterns mean an upstream bug is worth fixing._"
            ),
        }],
    })

    payload = {
        "text": (
            f"Card QA Patterns ({len(patterns)}) — engineering backlog signal"
        ),
        "attachments": [{
            "color": PATTERN_PALETTE["color"],
            "blocks": blocks,
        }],
    }

    if dry_run:
        # Stash on a fake response-like for assertions in tests.
        logger.info("post_pattern_digest: dry_run=True, returning payload-only")

        class _FakeResp:
            status_code = 200
            text = "dry_run"
            _payload = payload
        return _FakeResp()   # type: ignore[return-value]

    return requests.post(_webhook_url(), json=payload, timeout=10)

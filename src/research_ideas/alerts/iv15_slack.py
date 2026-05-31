"""
src/research_ideas/alerts/iv15_slack.py
========================================
Slack delivery for the IV15 "price reached fair value" sweep.

One consolidated message per sweep: a header summarising how many names
crossed at/below their stored IV15, then one section per name showing the
fresh live price, the per-share IV15 anchor, the resulting P/IV15, plus AICT
tier (and HK50 conviction). Deep-links back into the SW46 / HK50 cohort views.

No-op if SLACK_WEBHOOK_URL is unset. Never raises — the sweep's persistence
must not be held hostage to Slack being reachable.

Usage:
    from src.research_ideas.alerts.iv15_slack import post_iv15_alerts
    post_iv15_alerts(fired, app_base_url="https://your-app.railway.app")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


_COHORT_LABEL = {
    "sw46": "SW46 — Software Cohort",
    "hk50": "Long China / HK",
}

_COHORT_PATH = {
    "sw46": "/#/research-ideas/sw46",
    "hk50": "/#/research-ideas/hk50",
}


def _fmt_price(v: Optional[float], currency: str = "") -> str:
    if v is None:
        return "—"
    sym = {"USD": "$", "HKD": "HK$", "CNY": "¥", "CNH": "¥"}.get((currency or "").upper(), "")
    if v >= 1000:
        return f"{sym}{v:,.0f}"
    if v >= 1:
        return f"{sym}{v:,.2f}"
    return f"{sym}{v:.4f}"


def build_iv15_alert_payload(fired: list, app_base_url: Optional[str] = None) -> dict:
    """Build the consolidated Slack Block Kit payload for the fired alerts.

    `fired` is a list of FiredAlert dataclasses (see iv15_monitor.py). They are
    grouped by cohort in the message so SW46 and HK50 names read separately.
    """
    n = len(fired)
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🎯 IV15 Value Alert — {n} name{'s' if n != 1 else ''} at/below fair value",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Live price has fallen to or below the stored per-share *IV15* (P/IV15 ≤ 1.00).",
                },
            ],
        },
    ]

    # Group by cohort, preserving SW46-then-HK50 ordering.
    for cohort in ("sw46", "hk50"):
        rows = [a for a in fired if a.cohort == cohort]
        if not rows:
            continue
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{_COHORT_LABEL.get(cohort, cohort.upper())}*"},
            ],
        })
        for a in rows:
            ccy = getattr(a, "currency", "") or ""
            meta_bits = []
            if getattr(a, "aict", ""):
                meta_bits.append(f"AICT: *{a.aict}*")
            if getattr(a, "conviction", ""):
                meta_bits.append(f"Conviction: *{a.conviction}*")
            meta_line = "   ·   ".join(meta_bits)

            text = (
                f"*{a.display_ticker}* — {a.name}\n"
                f"Live {_fmt_price(a.live_price, ccy)}  →  "
                f"IV15 {_fmt_price(a.iv15, ccy)}   "
                f"*P/IV15 {a.live_p_iv15:.2f}×*"
            )
            if meta_line:
                text += f"\n{meta_line}"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            })

    # Action buttons — deep-link to each cohort view that had a hit.
    if app_base_url:
        base = app_base_url.rstrip("/")
        elements = []
        for cohort in ("sw46", "hk50"):
            if any(a.cohort == cohort for a in fired):
                elements.append({
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Open {_COHORT_LABEL.get(cohort, cohort.upper())}  ↗",
                        "emoji": True,
                    },
                    "url": f"{base}{_COHORT_PATH.get(cohort, '/#/research-ideas')}",
                    "style": "primary" if cohort == "sw46" else None,
                })
        # Strip None styles (Slack rejects "style": null).
        for el in elements:
            if el.get("style") is None:
                el.pop("style", None)
        if elements:
            blocks.append({"type": "actions", "elements": elements})

    # Fallback text for notifications / mobile previews.
    names = ", ".join(a.display_ticker for a in fired[:8])
    if n > 8:
        names += f" +{n - 8} more"
    return {
        "text": f"🎯 IV15 Value Alert — {n} name(s) at/below fair value: {names}",
        "blocks": blocks,
    }


def post_iv15_alerts(fired: list, app_base_url: Optional[str] = None) -> bool:
    """Post one consolidated IV15 alert message to Slack.

    Returns True on success, False on any failure (including a missing webhook
    URL or an empty `fired` list). Never raises.
    """
    if not fired:
        return False

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.info(
            "post_iv15_alerts: SLACK_WEBHOOK_URL not set — skipping Slack push "
            "for %d fired alert(s)", len(fired),
        )
        return False

    app_url = app_base_url or os.environ.get("APP_BASE_URL")
    payload = build_iv15_alert_payload(fired, app_base_url=app_url)

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            logger.info("post_iv15_alerts: posted %d alert(s) to Slack OK", len(fired))
            return True
        logger.warning(
            "post_iv15_alerts: Slack webhook returned %d: %s",
            resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.warning("post_iv15_alerts: post failed: %s", exc)
        return False

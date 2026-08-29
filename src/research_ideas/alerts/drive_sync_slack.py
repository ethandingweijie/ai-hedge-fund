"""Slack digest for the analyst-document refresh.

The sync runs daily and, until now, told nobody anything. This reports what
the refresh *learnt* rather than that it ran: which documents arrived, what
valuation method and numeric assumptions were parsed out of them, and how
the multiple the street applied compares with what that industry's peers
actually trade at.

That last line is the point. `regional_comps` already learns industry and
sector medians per market; the deposited reports say what analysts apply.
Nothing had ever compared the two. Putting the spread in the digest turns
each new PDF into an observation about the peer median rather than a number
consumed once and forgotten.

**Posts only when something was learnt.** A no-op day is silent, so a
message in the channel always means something changed. An unmatched document
counts as news — it is a report the system holds and cannot attribute.

Follows iv15_slack.py's contract: no-ops without SLACK_WEBHOOK_URL, never
raises, returns True only on a successful post.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_MAX_LEARNT_LINES = 12
_MAX_UNMATCHED = 6

# How a parsed method key reads in the digest.
_METHOD_LABEL = {
    "pe": "P/E", "ev_ebitda": "EV/EBITDA", "ggm_pb": "GGM (P/B)",
    "ddm": "DDM", "dcf": "DCF", "sotp": "SOTP", "nav": "NAV",
}

# Which comps field a parsed multiple should be compared against.
_BASIS_TO_COMP_FIELD = {
    "pe": "pe", "ev_ebitda": "ev_ebitda", "p_s": "ev_revenue",
    "p_b": "pb", "p_nav": "pb",
}


def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _peer_median(ticker: str, field: str) -> Optional[dict]:
    """Industry (or sector) median for one comps field, or None.

    Never raises and never blocks the digest: a cold comps table, an
    unclassifiable ticker or a network hiccup all degrade to no comparison
    line rather than to a wrong one.
    """
    try:
        from src.data.regional_comps import (
            get_fmp_classification, get_regional_multiples, market_for_exchange,
        )
        info = get_fmp_classification(ticker) or {}
        market = market_for_exchange(info.get("exchange", ""))
        if not market:
            return None
        comps = get_regional_multiples(
            market, info.get("industry"), info.get("sector")
        ) or {}
        row = comps.get(field)
        return row if isinstance(row, dict) and row.get("value") else None
    except Exception:
        return None


def collect_learnings(result: dict) -> list[dict]:
    """What the sync actually learnt, one entry per newly-attributed ticker.

    Reads the parsed basis back out of the store rather than re-parsing, so
    the digest reports exactly what the rest of the system will use.
    """
    learnt: list[dict] = []
    seen: set[str] = set()
    for doc in (result or {}).get("matched") or []:
        for ticker in doc.get("tickers") or []:
            if ticker in seen:
                continue
            seen.add(ticker)
            try:
                from src.memory.analyst_basis import get_analyst_basis
                basis = get_analyst_basis(ticker) or {}
            except Exception:
                basis = {}
            if not basis:
                continue
            entry: dict[str, Any] = {
                "ticker": ticker,
                "house": basis.get("house") or "sell-side",
                "as_of": basis.get("as_of") or "",
                "method": basis.get("method"),
                "target_multiple": basis.get("target_multiple"),
                "multiple_basis": basis.get("multiple_basis"),
                "wacc": basis.get("wacc"),
                "cost_of_equity": basis.get("cost_of_equity"),
                "terminal_growth": basis.get("terminal_growth"),
            }
            field = _BASIS_TO_COMP_FIELD.get(basis.get("multiple_basis") or "")
            if entry["target_multiple"] and field:
                peer = _peer_median(ticker, field)
                if peer:
                    entry["peer_median"] = peer["value"]
                    entry["peer_count"] = peer.get("peer_count")
                    entry["peer_basis"] = peer.get("basis")
                    if peer["value"]:
                        entry["spread_pct"] = (
                            entry["target_multiple"] / peer["value"] - 1.0
                        )
            learnt.append(entry)
    return learnt


def _learning_line(e: dict) -> str:
    """One ticker's learning, as a Slack mrkdwn line."""
    bits: list[str] = []
    method = _METHOD_LABEL.get(e.get("method") or "", e.get("method") or "")
    if method:
        bits.append(method)
    if e.get("target_multiple"):
        bits.append(f"{e['target_multiple']:g}x")
    for key, label in (("wacc", "WACC"), ("cost_of_equity", "CoE"),
                       ("terminal_growth", "g")):
        if e.get(key) is not None:
            bits.append(f"{label} {_fmt_pct(e[key])}")
    head = f"*{e['ticker']}* ({e['house']}{' ' + e['as_of'] if e['as_of'] else ''}) — "
    head += ", ".join(bits) if bits else "no method parsed"

    if e.get("peer_median"):
        spread = e.get("spread_pct")
        head += (
            f"\n        vs {e['peer_median']:.1f}x {e.get('peer_basis') or 'peer'} "
            f"median (n={e.get('peer_count')})"
        )
        if spread is not None:
            head += f" · street {spread:+.0%}"
    return head


def build_drive_sync_digest(result: dict,
                            learnt: Optional[list[dict]] = None) -> Optional[dict]:
    """Slack payload for one refresh, or None when there is nothing to say.

    Returning None is the silent-day contract — the caller posts nothing, so
    a message in the channel always means something changed.
    """
    result = result or {}
    learnt = collect_learnings(result) if learnt is None else learnt
    unmatched = result.get("unmatched") or []
    errors = result.get("errors") or []
    extracted = int(result.get("extracted") or 0)
    gated = int(result.get("gated") or 0)

    if not (learnt or unmatched or errors or extracted or gated):
        return None

    listed = int(result.get("listed") or 0)
    header = (f"Analyst archive refresh — {extracted} extracted "
              f"of {listed} listed")

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": header[:150], "emoji": False}},
    ]

    if learnt:
        lines = [_learning_line(e) for e in learnt[:_MAX_LEARNT_LINES]]
        if len(learnt) > _MAX_LEARNT_LINES:
            lines.append(f"_…and {len(learnt) - _MAX_LEARNT_LINES} more_")
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "*Learnt*\n" + "\n".join(lines)}})

    if unmatched:
        names = [str(d.get("name", ""))[:60] for d in unmatched[:_MAX_UNMATCHED]]
        extra = ("" if len(unmatched) <= _MAX_UNMATCHED
                 else f" _(+{len(unmatched) - _MAX_UNMATCHED} more)_")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": ("*Matched no ticker* — held but unattributed\n• "
                              + "\n• ".join(names) + extra)},
        })

    if gated:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn",
             "text": f"{gated} document(s) gated (ai_input_allowed=false)"}]})

    if errors:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "*Errors*\n• "
                                        + "\n• ".join(str(e)[:160]
                                                      for e in errors[:4])}})

    return {"text": header, "blocks": blocks}


def post_drive_sync_digest(result: dict) -> bool:
    """Post the digest. True only on a successful post. Never raises."""
    try:
        payload = build_drive_sync_digest(result)
    except Exception as exc:
        logger.warning("drive_sync digest build failed: %s", exc)
        return False

    if payload is None:
        logger.info("drive_sync digest: nothing learnt — staying silent")
        return False

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.info("drive_sync digest: SLACK_WEBHOOK_URL not set — skipping")
        return False

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            logger.info("drive_sync digest: posted OK")
            return True
        logger.warning("drive_sync digest: webhook returned %d: %s",
                       resp.status_code, resp.text[:300])
        return False
    except Exception as exc:
        logger.warning("drive_sync digest: post failed: %s", exc)
        return False

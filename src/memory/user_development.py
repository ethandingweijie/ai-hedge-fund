"""A verified market development raised by a user question, folded into a run (owner, 2026-11-28).

The landing page answers questions about recent market developments (app/backend/services/market_ask.py). When the
user then updates an affected ticker's analysis, the development rides into the run as a MATERIAL freshness event:

* a covered ticker (prior research on file) takes the existing material-news amend path -- sections amended from
  the development's sources, extractors re-run, valuation repriced -- and the new run supersedes the shared one;
* a new ticker gets full research with the development as a question the research must answer.

Only a development the ask service VERIFIED (dated; two or more independent domains the search tool returned, one of
them tier-1) can reach here. The pipeline never sees what a browser typed: not the question, only the verified
headline, date, summary and sources (review 2026-11-28). This module is DB-free so src/ never imports the backend.

A run carrying a development whose 12-month target moves more than REVIEW_MOVE from the prior report's target is
placed Under Review (HOLD at zero weight) until a regular run confirms it (portfolio_manager._research_view_for);
USER_DEVELOPMENT_REVIEW_MOVE sets the threshold (default 20%).
"""
from __future__ import annotations

from typing import Optional

#: events kept on a freshness delta (the classifier's own cap).
_MAX_EVENTS = 5


def review_move() -> float:
    import os
    try:
        v = float(os.environ.get("USER_DEVELOPMENT_REVIEW_MOVE", "0.20"))
        return v if v > 0 else 0.20
    except (TypeError, ValueError):
        return 0.20


def review_needed(delta: Optional[dict], prior_target, new_target) -> Optional[dict]:
    """{prior, new, move} when a user-raised development moved the target past review_move(); else None."""
    if not (delta or {}).get("user_development"):
        return None
    try:
        p, n = float(prior_target), float(new_target)
    except (TypeError, ValueError):
        return None
    if p <= 0 or n <= 0:
        return None
    move = n / p - 1.0
    return {"prior": p, "new": n, "move": move} if abs(move) > review_move() else None


def source_block(dev: dict) -> str:
    """The development as search-result text: what the amend synthesis reads in place of a fresh search."""
    lines = [f"USER-RAISED DEVELOPMENT (verified against the sources below): {dev.get('headline') or ''}",
             f"Date: {dev.get('date') or 'unknown'}"]
    if dev.get("summary"):
        lines.append(f"Summary: {dev['summary']}")
    for s in dev.get("sources") or []:
        if not isinstance(s, dict):
            continue
        bits = [str(s.get("title") or "").strip(), str(s.get("date") or "").strip(), str(s.get("url") or "").strip()]
        fact = str(s.get("fact") or "").strip()
        lines.append("- " + " | ".join(b for b in bits if b) + (f" -- {fact}" if fact else ""))
    return "\n".join(lines)


def merge(delta: Optional[dict], dev: dict) -> dict:
    """One ticker's freshness delta with the development on top. Always material; the development is event #1."""
    d = dict(delta or {})
    d.setdefault("events", [])
    ev = {"headline": str(dev.get("headline") or "")[:200], "date": str(dev.get("date") or "")[:10],
          "relevance": str(dev.get("relevance") or dev.get("summary") or "")[:300], "source": "user_question"}
    d["events"] = [ev] + [e for e in (d.get("events") or []) if isinstance(e, dict)][:_MAX_EVENTS - 1]
    d["material"] = True
    prior_snips = str(d.get("snippets") or "").strip()
    d["snippets"] = source_block(dev) + (("\n\n" + prior_snips) if prior_snips else "")
    prior_verdict = str(d.get("verdict") or "").strip()
    d["verdict"] = f"User-raised development: {ev['headline']}" + (f". {prior_verdict}" if prior_verdict else "")
    d["user_development"] = {"ask_id": dev.get("ask_id"), "headline": ev["headline"], "date": ev["date"],
                             "sources": [s for s in (dev.get("sources") or []) if isinstance(s, dict)][:6]}
    return d


def merge_all(deltas: Optional[dict], developments: Optional[dict]) -> dict:
    """{ticker: delta} with each ticker's development merged in (tickers with no prior get a delta of their own)."""
    out = dict(deltas or {})
    for t, dev in (developments or {}).items():
        if isinstance(dev, dict) and dev.get("headline"):
            out[t] = merge(out.get(t), dev)
    return out


def research_brief(delta: Optional[dict]) -> str:
    """The instruction full research gets when the run carries a user-raised development."""
    ud = (delta or {}).get("user_development") or {}
    if not ud.get("headline"):
        return ""
    srcs = "; ".join(f"{s.get('title') or s.get('url')}" for s in ud.get("sources") or [] if isinstance(s, dict))[:600]
    return (f"\nUSER-RAISED DEVELOPMENT ({ud.get('date') or 'recent'}): {ud['headline']}. Sources: {srcs}. "
            f"This analysis was requested to reflect it: research its effect on revenue, margins, guidance and "
            f"risk, state the figures, and say plainly whether it changes the thesis.\n")

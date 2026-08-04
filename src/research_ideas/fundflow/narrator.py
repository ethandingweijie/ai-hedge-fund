"""
src/research_ideas/fundflow/narrator.py
========================================
DeepSeek narration layer for the fund-flow brief.

The division of labour is deliberate and load-bearing:

  summary.py  computes every FACT — which region leads, by how many sigma,
              what flipped, what the measured issuer flows were. Pure
              arithmetic over scored fields.
  narrator.py hands those facts to DeepSeek and asks it to write the brief.

The model is a writer, never a calculator. It receives a compact fact table
plus the deterministic draft and is instructed to reuse the supplied figures
verbatim; it is not asked to derive anything. That keeps the one failure mode
that would matter here — a confident wrong number in the headline — off the
table, while still buying genuinely better prose than string templates can
produce: connected reasoning across regions, and implications that read like
an analyst wrote them.

Every failure path returns the deterministic draft unchanged, with
`summary_source` left at "deterministic" so the UI can say which it is.

  narrate(summary, regions, benchmarks) -> FundFlowSummary
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import requests

from src.research_ideas.fundflow.schemas import FundFlowRegionResult, FundFlowSummary


logger = logging.getLogger(__name__)

_API_URL = "https://api.deepseek.com/v1/chat/completions"
_DEFAULT_MODEL = "deepseek-v4-flash"
_TIMEOUT = 180
# deepseek-v4-flash is a reasoning model: reasoning_tokens are drawn from the
# same completion budget as the answer, and on a twelve-region payload the
# trace alone ran past 8000 — returning either empty content or JSON cut off
# mid-string. The ceiling has to cover the reasoning AND the answer, and
# raising it costs nothing when the model finishes early.
_MAX_TOKENS = 32000

# The weekly job gets one shot, so transient failures are retried rather than
# silently costing a week's brief.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_S = 4

# Caps on what comes back, so a verbose response cannot blow out the summary
# panel's layout. The model is told these limits too; this enforces them.
_MAX_ITEMS = {"key_flows": 6, "key_changes": 6, "implications": 6, "watch_items": 5}


_SYSTEM_PROMPT = """\
You are a senior cross-asset flow strategist writing the summary panel that \
sits at the top of a geographic fund-flow dashboard. Your reader is a \
portfolio manager who will scroll past you to the table underneath, so \
anything you say must survive being checked against it.

ABSOLUTE RULES
1. Use ONLY the figures supplied in the FACTS payload. Never compute, adjust, \
round differently, or invent a number. If a figure is null, do not mention it.
2. Two different quantities are supplied and must never be conflated:
   - "flow pressure" / "rel_flow_z": tape-derived, measured in sigma (σ) off a \
region's own baseline. Never describe these as dollars or as a percentage of \
assets.
   - "implied_flow_21d": measured issuer creation/redemption in US dollars. \
This is the only figure you may describe as actual money in or out, and it is \
null for regions whose share-count feed is stale.
3. Name regions by their label. You may use the supplied emoji at most once \
per bullet, at the start.
3b. MULTI-HORIZON IS MANDATORY. Every region carries a `by_period` block with \
1M, 3M, 6M and 1Y readings. Never write the brief off the 1-month column \
alone — a month in isolation cannot distinguish a regime change from a wobble \
inside a longer trend, and that distinction is the main thing the reader wants \
from you. Specifically:
   - In `key_flows`, place each region's 1M reading against its 3M/6M/1Y \
     readings. Say whether the month CONTINUES the longer trend or BREAKS it.
   - In `key_changes`, prioritise regions where the short and long horizons \
     DISAGREE (e.g. 1M inflow against 6M and 1Y outflow) — those are the real \
     turns. A region positive on every horizon is a standing regime, not news.
   - In `implications`, state explicitly whether a move looks like a durable \
     multi-quarter reallocation (consistent across 3M/6M/1Y) or a one-month \
     positioning shift that could reverse. These call for different sizing and \
     you should say which applies.
   - Cite the horizon whenever you quote a figure: "+$4.5bn over 1Y" not \
     "+$4.5bn". An unlabelled figure is a defect.
4. No hedging filler, no "it is important to note", no restating the \
methodology. Every sentence must carry a fact or a consequence.
5. Write in plain British-English prose. Active voice.
6. FORMAT figures the way a note to a PM would, never as raw values:
   - dollars: $22.7bn, $955m, -$1.2bn  (never "22715428656 US dollars")
   - sigma:   +0.65σ, -1.39σ  (two decimals, always signed)
   - percent of assets: +0.69%, -6.80%
   - composites: +5, -4  (integers, always signed)
   - returns:   +7.5%, -11.3%  (one decimal)
7. Never print a payload field name. Write "the issuer flow feed is stale", \
not "implied_flow is null"; "flow pressure", not "cmf_z_21". The reader never \
sees this JSON.

OUTPUT
Return a single JSON object with exactly these keys. Every array element must \
be a plain finished sentence as a JSON string — never an object, never a \
key/value pair, never a bare region name.
  "headline"     : string, 1-2 sentences. The single most important thing that \
happened to flows this month, with the figures that establish it.
  "regime"       : string, one short clause characterising the tape overall \
(e.g. whether money is entering equities broadly or rotating between \
geographies).
  "key_flows"    : array of up to 6 strings. Where money is going and coming \
from, strongest first. One region per bullet.
  "key_changes"  : array of up to 6 strings. What is DIFFERENT from a month \
ago: direction flips, rotation in relative flow, fresh inflections.
  "implications" : array of up to 6 strings. What a portfolio manager should \
DO or WATCH FOR as a result. This is the most valuable section — prioritise \
flow-versus-price divergences, rotation pairs, and cases where a regional \
reading is merely the global tide. Each bullet: the observation, then the \
consequence.
  "watch_items"  : array of up to 5 strings. Unresolved situations and data \
caveats worth carrying into next week.
"""


def _f(x: Optional[float], digits: int = 2) -> Optional[float]:
    return None if x is None else round(float(x), digits)


def _region_facts(r: FundFlowRegionResult) -> dict[str, Any]:
    """
    The subset of scored fields the narrator is allowed to talk about.

    Kept tight on purpose. Every extra field is another thing the model
    reasons about before writing a word, and the reasoning trace is drawn from
    the same token budget as the answer — a fuller payload measurably pushed
    the response into truncation. These are the fields that carry the story.
    """
    return {
        "label": r.label,
        "emoji": r.emoji,
        "bloc": r.bloc,
        "verdict": r.verdict,
        "flow_composite_now": _f(r.composite, 0),
        "pillars_pressure_turn_accel": [
            _f(r.pressure_score, 0), _f(r.turn_score, 0), _f(r.accel_score, 0),
        ],
        # Every horizon, keyed identically so the model can compare across a
        # row without re-deriving anything. A one-month reading alone cannot
        # tell a regime change from a wobble inside a year-long trend, which
        # is exactly the judgement the brief needs to make.
        "by_period": {
            "1M": {
                "flow_pressure_sigma": _f(r.cmf_z_21),
                "flow_composite_then": _f(r.composite_1m, 0),
                "issuer_flow_usd": _f(r.implied_flow_21d, 0),
                "issuer_flow_pct_of_assets": _f(r.implied_flow_21d_pct_aum, 4),
                "return": _f(r.r_21d, 4),
            },
            "3M": {
                "flow_pressure_sigma": _f(r.cmf_z_63),
                "flow_composite_then": _f(r.composite_3m, 0),
                "issuer_flow_usd": _f(r.implied_flow_63d, 0),
                "issuer_flow_pct_of_assets": _f(r.implied_flow_63d_pct_aum, 4),
                "return": _f(r.r_63d, 4),
            },
            "6M": {
                "flow_pressure_sigma": _f(r.cmf_z_126),
                "flow_composite_then": _f(r.composite_6m, 0),
                "issuer_flow_usd": _f(r.implied_flow_126d, 0),
                "issuer_flow_pct_of_assets": _f(r.implied_flow_126d_pct_aum, 4),
                "return": _f(r.r_126d, 4),
            },
            "1Y": {
                "flow_pressure_sigma": _f(r.cmf_z_252),
                "flow_composite_then": _f(r.composite_12m, 0),
                "issuer_flow_usd": _f(r.implied_flow_252d, 0),
                "issuer_flow_pct_of_assets": _f(r.implied_flow_252d_pct_aum, 4),
                "return": _f(r.r_252d, 4),
            },
        },
        "rel_flow_vs_world_sigma": _f(r.rel_flow_z),
        "rel_flow_rotation_sigma": _f(r.rel_flow_z_delta),
        "turnover_vs_normal": _f(r.turnover_surge),
        "days_since_inflection": r.days_since_turn,
        "implied_feed_quality": r.implied_quality,
        "price_composite": _f(r.price_composite, 0),
        "fx_drag_1m": _f(r.fx_drag_21d, 4),
        "flow_vs_price": r.divergence,
    }


def _build_payload(summary: FundFlowSummary,
                   regions: list[FundFlowRegionResult],
                   benchmarks: list[FundFlowRegionResult]) -> dict[str, Any]:
    # Only the global benchmark goes in. EM and DM-ex-US are useful in the
    # table but they invite the model to write about blocs the reader did not
    # ask for, and they enlarge the payload for no narrative gain.
    world = next((b for b in benchmarks if b.region == "WORLD"), None)
    return {
        "as_of_note": "All flow windows are trailing 21 sessions (~1 month) unless stated.",
        "scoring_note": (
            "flow_composite_now is -6..+6, the sum of three signed pillars each -2..+2: "
            "pressure (where flow stands vs the region's own baseline), turn (fresh "
            "inflection), accel (whether it is strengthening). Positive means money "
            "arriving. It is always scored on the 1-MONTH window. "
            "by_period gives the same measures over four trailing windows: "
            "flow_pressure_sigma is flow over that window in standard deviations off "
            "the region's own baseline (NOT dollars, NOT a percentage); "
            "flow_composite_then is where the composite stood that long AGO, so "
            "comparing it with flow_composite_now gives the change over the period; "
            "issuer_flow_usd is measured creation/redemption in dollars over that "
            "window, null where the share-count feed is stale. "
            "rel_flow_vs_world_sigma and rel_flow_rotation_sigma are 1-month reads: "
            "positive rotation means the geography is winning share of the world's "
            "bid, negative means losing it."
        ),
        "universe_totals": {
            "regions_with_inflow_verdict": summary.inflow_count,
            "regions_with_outflow_verdict": summary.outflow_count,
            "net_measured_issuer_flow_21d_usd": _f(summary.net_implied_flow_21d, 0),
            "regions_with_live_share_feed": summary.implied_coverage,
            "total_regions": len(regions),
        },
        "regions": [_region_facts(r) for r in regions],
        "global_benchmark": _region_facts(world) if world else None,
        # The computed draft, as grounding and a tone reference. The model is
        # asked to improve on it, not to reproduce it.
        "deterministic_draft": {
            "regime": summary.regime,
            "key_changes": summary.key_changes[:8],
            "implications": summary.implications,
        },
    }


def _clean_list(value: Any, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            # Tolerate the model wrapping a bullet as an object despite the
            # instruction. Join every string-ish field rather than taking the
            # first, so {"region": "Japan", "note": "..."} does not collapse to
            # the bare word "Japan".
            bits = [
                str(v).strip() for v in item.values()
                if isinstance(v, (str, int, float)) and str(v).strip()
            ]
            if bits:
                out.append(" — ".join(bits))
        if len(out) >= cap:
            break
    return out


def narrate(summary: FundFlowSummary,
            regions: list[FundFlowRegionResult],
            benchmarks: list[FundFlowRegionResult],
            model: Optional[str] = None,
            api_key: Optional[str] = None) -> FundFlowSummary:
    """
    Rewrite `summary` with DeepSeek. Returns `summary` untouched on any
    failure — a missing key, a network error, a non-200, or a response that
    does not parse into the expected shape. A flow dashboard that renders a
    slightly plainer summary is fine; one that renders an error is not.
    """
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        logger.info("fundflow: DEEPSEEK_API_KEY not set — keeping deterministic summary")
        return summary

    model_name = model or os.getenv("FUNDFLOW_SUMMARY_MODEL") or _DEFAULT_MODEL
    payload = _build_payload(summary, regions, benchmarks)
    body = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": "FACTS:\n" + json.dumps(payload, ensure_ascii=False)},
        ],
    }

    # This call carries a long reasoning trace over a ~90s connection, so a
    # dropped socket or a 5xx is a live risk — one was observed in testing
    # ("Response ended prematurely"). The scheduled job runs ONCE A WEEK, so
    # a single transient failure would cost a whole week's brief. Retry the
    # recoverable failures; fall through to the deterministic draft only once
    # the attempts are spent.
    data: Optional[dict] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        last = attempt == _MAX_ATTEMPTS
        try:
            resp = requests.post(
                _API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning(
                "fundflow: DeepSeek request failed (%s) [attempt %d/%d]",
                exc, attempt, _MAX_ATTEMPTS,
            )
            if last:
                return summary
            time.sleep(_RETRY_BACKOFF_S * attempt)
            continue

        if resp.status_code != 200:
            # 4xx other than 429 is a request defect — retrying an identical
            # body cannot fix it, so fail fast rather than burn the budget.
            retryable = resp.status_code == 429 or resp.status_code >= 500
            logger.warning(
                "fundflow: DeepSeek returned %s (%s) [attempt %d/%d, retryable=%s]",
                resp.status_code, resp.text[:200], attempt, _MAX_ATTEMPTS, retryable,
            )
            if last or not retryable:
                return summary
            time.sleep(_RETRY_BACKOFF_S * attempt)
            continue

        try:
            choice = resp.json()["choices"][0]
            content = choice["message"].get("content") or ""
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("fundflow: DeepSeek envelope unreadable (%s)", exc)
            return summary

        if not content.strip():
            # Almost always finish_reason="length": the reasoning trace
            # consumed the whole completion budget before any answer.
            logger.warning(
                "fundflow: DeepSeek returned empty content (finish_reason=%s, "
                "max_tokens=%s) [attempt %d/%d]",
                finish, _MAX_TOKENS, attempt, _MAX_ATTEMPTS,
            )
            if last:
                return summary
            time.sleep(_RETRY_BACKOFF_S * attempt)
            continue

        try:
            data = json.loads(content)
            break
        except ValueError as exc:
            logger.warning(
                "fundflow: DeepSeek content is not valid JSON (%s, finish_reason=%s) "
                "[attempt %d/%d]", exc, finish, attempt, _MAX_ATTEMPTS,
            )
            if last:
                return summary
            time.sleep(_RETRY_BACKOFF_S * attempt)

    if data is None:
        return summary

    headline = data.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        logger.warning("fundflow: DeepSeek returned no headline — keeping deterministic summary")
        return summary

    regime = data.get("regime")
    narrated = summary.model_copy(update={
        "headline": headline.strip(),
        "regime": regime.strip() if isinstance(regime, str) and regime.strip() else summary.regime,
        "key_flows": _clean_list(data.get("key_flows"), _MAX_ITEMS["key_flows"]) or summary.key_flows,
        "key_changes": _clean_list(data.get("key_changes"), _MAX_ITEMS["key_changes"]) or summary.key_changes,
        "implications": _clean_list(data.get("implications"), _MAX_ITEMS["implications"]) or summary.implications,
        "watch_items": _clean_list(data.get("watch_items"), _MAX_ITEMS["watch_items"]) or summary.watch_items,
        "summary_source": "deepseek",
        "model_used": model_name,
    })
    logger.info("fundflow: summary narrated by %s", model_name)
    return narrated

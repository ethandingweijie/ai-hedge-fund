"""Interactive valuation agent, the conversational half (owner, 2026-10-03).

The user queries the agent's thinking on the page: "why 5% margin in 2028?", "what if growth is
4%?". The agent answers from the run's own trace -- the guidance block, the forecast steps, the
invariants, the DCF leg, the blend and the brief with its footnotes -- and, when the user asks for
a change, PROPOSES an override in the exact shape estimate_override_service accepts. It never
writes one: the page shows the proposal with a recompute, and only the user's acceptance saves it.

One deepseek-v4-flash call per question (the what-if simulator's pattern); the model is
overridable with ESTIMATE_AGENT_MODEL. Without a key the endpoint says so (503), the page still
works for direct edits.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.backend.services import estimate_override_service as eo

logger = logging.getLogger(__name__)

AGENT_MODEL_NAME = "deepseek-v4-flash"      # override: ESTIMATE_AGENT_MODEL env

_SYSTEM = """You are the AI analyst behind an equity research report. The person asking may be a
beginner investor or a professional; write so a beginner understands without dumbing down the
analyst view. Answer from the CONTEXT only: the published rating and why it was set (including any
rule that capped it), the previous report and what changed since, the news checked since then, the
thesis, the risks, the guidance block, the forecast trace, the DCF leg, the blend weights, the
target bridge and the industry brief.

Rules:
- Plain words first. Open with the direct answer in one or two everyday sentences, then the reason.
  At most 120 words (160 when explaining several terms), no headings, no bullet lists, no markdown.
- Explain any finance term you use in a few words the first time (e.g. "intrinsic value, our
  estimate of what a share is worth"). Use Buy / Hold / Sell for the rating, with the research word
  (Overweight / Neutral / Underweight) in brackets once, word first: "Hold (Neutral)", never "Neutral (Hold)".
- No formulas, equations or model-step names unless the user asks how something is calculated.
- "Should I buy?": you cannot give personal advice; say what the report's rating means, what would
  have to happen for the view to change, and that the decision depends on their own situation.
- "Why this rating?" / rating changes: use RATING, RATING CAP / RULES and PREVIOUS REPORT. If a rule
  capped or set the rating, say so plainly; do not credit news that the NEWS CHECK says changed nothing.
- Never write bracketed footnote markers such as [3]. When a claim comes from the brief, name the
  source in words ("Singtel's Q1 FY2025 investor presentation").
- Numbers you quote must appear in the context or follow from it by arithmetic you state.
- When the user proposes a change, asks "what if", or asks you to change an estimate, return a
  `proposal`: the override object in this exact shape, with only the fields that change:
    {"shared": {"fade_years": int, "tax_rate": x, "capex_alpha": x, "nwc_intensity": x, "wacc": x, "tgr": x},
     "scenarios": {"base": {"revenue_growth_fy1": x, "revenue_growth_fy2": x, "ebitda_margin_fy1": x,
                            "ebitda_margin_fy2": x, "eps_fy1": x, "eps_fy2": x}, "bear": {...}, "bull": {...}},
     "medium_term_target": {"metric": "eps|revenue|revenue_growth|ebitda_margin", "target_year": "FY2029",
                            "low": x, "mid": x, "high": x, "unit": "USD"},
     "rationale": "one sentence"}
  Rates and margins are decimals (5% = 0.05). Otherwise `proposal` is null.
- You do not apply anything. Say the user can recompute and save the proposal, or discard it.
- If the context cannot answer, say what is missing. Do not invent guidance, sources or numbers.
Respond in JSON format: {"answer": "...", "proposal": {...} | null}."""


class _AskOut(BaseModel):
    answer: str = Field(..., description="the reply, at most 120 words")
    proposal: Optional[dict] = Field(None, description="an override object or null")


def _pc(v) -> str:
    try:
        return f"{float(v):.1%}"
    except (TypeError, ValueError):
        return "n/a"


def _n(v, d=2) -> str:
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "n/a"


def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _report_context(payload: dict, ticker: str) -> list[str]:
    """What a reader asks about first (owner, 2026-11-27): the published rating and the rule that
    set or capped it, the decision text, the previous report, and the news checked since. The agent
    could not explain a rating change without these."""
    data = payload.get("data") or {}
    dec = ((payload.get("decisions") or {}).get(ticker)) or {}
    rv = dec.get("research_view") or {}
    out: list[str] = []
    if rv:
        bm = rv.get("benchmark") or {}
        out.append(
            f"RATING: {rv.get('rating_label')} (trade action {rv.get('trade_action')}; Overweight = Buy, Neutral = Hold, "
            f"Underweight = Sell); benchmark {bm.get('name')} expected return {_pc(bm.get('expected_return'))}; "
            f"price {_n(rv.get('price'))} on {rv.get('price_as_of')}; 12-month target {_n(rv.get('target_12m'))}; "
            f"intrinsic value {_n(rv.get('intrinsic_value'))}; expected 12-month total shareholder return {_pc(rv.get('tsr_12m'))} "
            f"(price {_pc(rv.get('capital_gain_12m'))} + dividend {_pc(rv.get('dividend_yield'))}); "
            f"long-run view {rv.get('structural_rating')}, 12-month view {rv.get('tactical_rating')}.")
        if rv.get("callout"):
            out.append(f"RATING BASIS: {_clip(rv.get('callout'), 400)}")
        notes = (rv.get("compliance") or {}).get("notes") or []
        if notes or rv.get("valuation_unreconciled") or rv.get("under_review"):
            out.append("RATING CAP / RULES: " + " ".join(_clip(n, 400) for n in notes[:4])
                       + (" The rating is Under Review." if rv.get("under_review") else ""))
        if rv.get("rating_definition"):
            out.append(f"RATING DEFINITION: {_clip(rv.get('rating_definition'), 400)}")
        mu = rv.get("material_update") or {}
        if mu.get("sentence"):
            out.append(f"NEWS SINCE LAST REPORT, EFFECT ON ESTIMATES: {_clip(mu.get('sentence'), 600)}")
    elif dec.get("action"):
        out.append(f"DECISION: {dec.get('action')}; price target {_n(dec.get('price_target'))}.")
    di = (dec.get("decision_inputs") or {}).get("quantitative") or {}
    if di.get("band_action"):
        out.append(f"VALUATION BAND (long-run value check, price vs intrinsic value only): {di.get('band_action')}; "
                   f"upside to intrinsic value {di.get('upside_to_iv_pct')}%. The rating can differ from the band.")
    if dec.get("headline"):
        out.append(f"HEADLINE: {_clip(dec.get('headline'), 200)}")
    if dec.get("rationale"):
        out.append(f"THESIS: {_clip(dec.get('rationale'), 1600)}")
    vt = ((data.get("value_trap_analysis") or {}).get(ticker)) or {}
    pl = ((data.get("power_law_analysis") or {}).get(ticker)) or {}
    if vt or pl:
        out.append(f"RISK CHECKS: value-trap verdict {vt.get('verdict') or vt.get('overall_verdict')}; "
                   f"power-law score {pl.get('score') or pl.get('total_score')}/10.")
    pr = ((data.get("prior_recap") or {}).get(ticker)) or {}
    if pr:
        pj = pr.get("recap_json") or {}
        out.append(f"PREVIOUS REPORT ({str(pr.get('run_at') or '')[:10]}): rating {pj.get('rating_label') or pr.get('final_action')} "
                   f"(trade action {pr.get('final_action')}); target {_n(pj.get('price_target'))} at price {_n(pr.get('price_at_run'))}; "
                   f"intrinsic value {_n(pj.get('blended_iv') or pj.get('dcf_base_iv'))}. Summary: {_clip(pr.get('recap_text'), 700)}")
    fd = ((data.get("freshness_delta") or {}).get(ticker)) or {}
    if fd:
        evs = "; ".join(f"{e.get('date') or ''} {e.get('headline') or ''}: {_clip(e.get('relevance'), 220)}".strip()
                        for e in (fd.get("events") or [])[:5])
        out.append(f"NEWS CHECK SINCE PREVIOUS REPORT: material {fd.get('material')}; events {evs or 'none'}; "
                   f"verdict {_clip(fd.get('verdict'), 400)}")
    chg = dec.get("change_attribution") or {}
    if chg.get("sentence"):
        out.append(f"WHAT CHANGED VS PREVIOUS REPORT: {_clip(chg.get('sentence'), 900)}")
    return out


_FOOTNOTE_RE = re.compile(r"\s?\[(\d{1,3})(?:\s*[,–-]\s*\d{1,3})*\]")


def strip_footnotes(answer: str, footnotes: list[dict]) -> tuple[str, list[dict]]:
    """Bare [n] markers are invisible to a reader (nothing on the page resolves them). Remove them
    from the answer and return the cited footnotes as named sources, with a link when one exists."""
    by_id = {str(e.get("ref_id") or e.get("n")): e for e in (footnotes or [])}
    cited: list[str] = []
    for m in _FOOTNOTE_RE.finditer(answer or ""):
        for k in re.findall(r"\d{1,3}", m.group(0)):
            if k not in cited:
                cited.append(k)
    text = _FOOTNOTE_RE.sub("", answer or "")
    text = re.sub(r"\s+([.,;:])", r"\1", text).strip()
    sources = []
    for k in cited:
        e = by_id.get(k)
        if not e:
            continue
        name = str(e.get("source_name") or e.get("source") or "").strip().rstrip(".")
        if name:
            sources.append({"n": e.get("ref_id") or e.get("n"), "name": name, "date": e.get("date") or None, "url": e.get("url") or None})
    return text, sources


def build_context(payload: dict, ticker: str, max_brief_chars: int = 3500) -> str:
    """The agent's working context, compact and numbered so answers can point at it."""
    data = payload.get("data") or {}
    dr = (data.get("dcf_range") or {}).get(ticker) or {}
    ge = dr.get("guidance_estimates") or {}
    fc = dr.get("guidance_forecast") or {}
    ov = dr.get("estimate_override")
    base = dr.get("base") or {}
    leg = (base.get("leg_inputs") or {}).get("DCF") or {}
    pb = dr.get("pt_bridge") or {}
    sa = (data.get("scenario_analysis") or {}).get(ticker) or {}
    lines = [f"TICKER {ticker}; profile {dr.get('profile')}; sector {data.get('sector')}; reporting currency {dr.get('reported_currency')}."]
    lines.extend(_report_context(payload, ticker))
    g = ge.get("guidance") or {}
    lines.append("GUIDANCE AS STATED (" + str(ge.get("fiscal_year_1") or "FY+1") + "): " + json.dumps(
        {k: g.get(k) for k in ("revenue_growth", "revenue", "ebitda_margin", "eps", "basis", "status", "source") if g.get(k) is not None}, default=str)
        + f"; confidence {ge.get('confidence')}; consensus {json.dumps(ge.get('consensus') or {}, default=str)}")
    if ge.get("quote"):
        lines.append(f"QUOTE: {g.get('quote')}")
    lines.append("MODEL ESTIMATES: " + json.dumps(ge.get("estimates") or {}, default=str))
    if ge.get("medium_term_target"):
        lines.append("MEDIUM-TERM TARGET: " + json.dumps(ge.get("medium_term_target"), default=str))
    if ge.get("rationale"):
        lines.append(f"RESEARCH RATIONALE: {ge.get('rationale')}")
    if fc:
        lines.append(f"FORECAST: archetype {fc.get('archetype')} {fc.get('archetype_name')} ({fc.get('archetype_reason')}); horizon {fc.get('horizon_years')} years, "
                     f"fade {fc.get('fade_years')}; EBIT margin {_pc(fc.get('margin_start'))} → {_pc(fc.get('margin_target'))} ({fc.get('margin_source')}).")
        for st in fc.get("steps") or []:
            lines.append(f"STEP {st.get('n')} {st.get('title')}: {st.get('detail')}")
        rows = fc.get("rows") or []
        if rows:
            lines.append("YEAR TABLE (year, growth, EBIT margin, EPS, UFCF, phase): " + "; ".join(
                f"Y{r.get('year')} {_pc(r.get('growth'))} {_pc(r.get('ebit_margin'))} {_n(r.get('eps'))} {_n((r.get('ufcf') or 0) / 1e9, 2)}bn {r.get('phase')}" for r in rows))
        for inv in fc.get("invariants") or []:
            lines.append(f"INVARIANT {inv.get('id')} {inv.get('name')}: {'PASS' if inv.get('ok') is True else 'FAIL' if inv.get('ok') is False else 'n/a'} — {inv.get('detail')}")
        for f in fc.get("flags") or []:
            lines.append(f"FLAG: {f}")
        h = fc.get("history") or {}
        lines.append("HISTORY RATIOS: " + json.dumps({k: h.get(k) for k in ("tax_rate", "tax_rate_source", "capex_alpha", "nwc_intensity", "da_pct_revenue", "roic_median", "years")}, default=str))
    else:
        ctx = dr.get("forecast_context") or {}
        if ctx:
            lines.append("NO GUIDANCE FORECAST BUILT (no guidance block with FY+1 growth). HISTORY RATIOS: " + json.dumps(ctx.get("history") or {}, default=str)[:600])
    lines.append(f"DCF LEG (base): value {_n(leg.get('value'))} per share; WACC {_pc(leg.get('wacc'))}; terminal growth {_pc(leg.get('tgr'))}; "
                 f"revenue base {_n((leg.get('revenue_base') or 0) / 1e9)}bn; FCF margin base {_pc(leg.get('fcf_margin_base'))}; "
                 f"growth schedule {', '.join(_pc(x) for x in (leg.get('growth_schedule') or []))}; net debt {_n((leg.get('net_debt') or 0) / 1e9)}bn; shares {_n((leg.get('shares') or 0) / 1e6, 0)}m.")
    lines.append("BLEND (base): " + ", ".join(f"{e.get('method')} {float(e.get('weight') or 0):.0%}" for e in (base.get("effective_weights") or []))
                 + f"; leg values {json.dumps(base.get('method_iv_table') or {}, default=str)}; intrinsic value {_n(base.get('intrinsic_value'))}.")
    lines.append(f"TARGET BRIDGE: {pb.get('rule')}; spot {_n(pb.get('spot'))}; capture {pb.get('capture')}; scenarios {json.dumps(pb.get('scenarios') or {}, default=str)}; "
                 f"probability-weighted 12m target {_n(sa.get('12m_price_target'))}; probabilities " + json.dumps({s: (sa.get(s) or {}).get('probability') for s in ('bear', 'base', 'bull')}))
    if ov:
        lines.append("ACTIVE USER OVERRIDE: " + json.dumps({k: ov.get(k) for k in ("fields", "note", "created_at", "before", "after")}, default=str))
    for f in (base.get("forward_flags") or [])[:12]:
        lines.append(f"ENGINE FLAG: {f}")
    brief = (data.get("industry_brief") or "")[:max_brief_chars]
    if brief:
        lines.append("INDUSTRY BRIEF: " + brief)
    fns = data.get("industry_footnotes") or []
    if fns:
        lines.append("FOOTNOTES: " + "; ".join(f"[{e.get('ref_id') or e.get('n')}] {e.get('source_name') or e.get('source')} {e.get('date') or ''}".strip() for e in fns[:30]))
    return "\n".join(lines)


def _default_llm(system: str, user: str) -> Optional[_AskOut]:
    from src.llm.models import ModelProvider, get_model
    model_name = os.environ.get("ESTIMATE_AGENT_MODEL", AGENT_MODEL_NAME)
    llm = get_model(model_name, ModelProvider.DEEPSEEK, None)
    if llm is None:
        return None
    messages = [("system", system), ("human", user)]
    try:
        return llm.with_structured_output(_AskOut, method="json_mode").invoke(messages)
    except Exception as exc:                                 # noqa: BLE001
        logger.warning("estimate agent: structured output failed (%s); retrying raw", exc)
        raw = llm.invoke(messages)
        text = raw.content if hasattr(raw, "content") else str(raw)
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return _AskOut(**json.loads(text[start:end + 1]))
        return _AskOut(answer=text.strip()[:1200], proposal=None)


def ask(payload: dict, ticker: str, question: str, history: Optional[list[dict]] = None, llm_caller=None) -> dict:
    """{answer, proposal, proposal_valid, proposal_error, model}. `llm_caller(system, user) -> _AskOut` for tests."""
    question = (question or "").strip()
    if not question:
        raise ValueError("question is empty")
    ctx = build_context(payload, ticker)
    convo = ""
    for m in (history or [])[-6:]:
        role = "User" if (m.get("role") == "user") else "Agent"
        convo += f"{role}: {str(m.get('content') or '')[:600]}\n"
    user = f"CONTEXT\n{ctx}\n\nCONVERSATION SO FAR\n{convo}\nUser: {question}\n\nRespond in JSON format."
    out = (llm_caller or _default_llm)(_SYSTEM, user)
    if out is None:
        raise RuntimeError("the estimate agent's model is not configured (DEEPSEEK_API_KEY)")
    proposal, valid, err = None, False, None
    if isinstance(out.proposal, dict) and out.proposal:
        raw = {k: v for k, v in out.proposal.items() if k in ("shared", "scenarios", "medium_term_target")}
        try:
            proposal = eo.normalize_overrides(raw)
            proposal["rationale"] = str(out.proposal.get("rationale") or "")[:300]
            valid = True
        except ValueError as exc:
            proposal, err = out.proposal, str(exc)
    answer, sources = strip_footnotes((out.answer or "").strip(), (payload.get("data") or {}).get("industry_footnotes") or [])
    return {"answer": answer, "sources": sources, "proposal": proposal, "proposal_valid": valid, "proposal_error": err,
            "model": os.environ.get("ESTIMATE_AGENT_MODEL", AGENT_MODEL_NAME)}


def regenerate_pm(payload: dict, ticker: str) -> dict:
    """Rewrite the PM's rationale and headline on the run AS OVERRIDDEN (the payload must already carry
    the override, i.e. come from get_run_result). One LLM call, on the user's explicit request only.
    Returns {rationale, headline, headline_flags, regenerated_at}."""
    from datetime import datetime, timezone
    from src.agents.portfolio_manager import run_advanced_portfolio_manager
    data = payload.get("data") or {}
    if ticker not in (data.get("dcf_range") or {}):
        raise KeyError(ticker)
    state = {"messages": [], "data": {**data, "tickers": [ticker]},
             "metadata": {"model_name": payload.get("model_name"), "model_provider": None, "show_reasoning": False}}
    out = run_advanced_portfolio_manager(state)
    dec = ((out.get("decisions") or {}).get(ticker)) or {}
    if not dec.get("rationale"):
        raise RuntimeError("the portfolio manager returned no rationale")
    ovr = ((data.get("dcf_range") or {}).get(ticker) or {}).get("estimate_override") or {}
    return {"rationale": dec.get("rationale"), "headline": dec.get("headline"), "headline_flags": dec.get("headline_flags"),
            "rationale_length": dec.get("rationale_length"), "regenerated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "override_id": ovr.get("id"), "fields": ovr.get("fields")}

"""
src/research_ideas/hundred_q/qualitative.py
==============================================
LLM-based qualitative scoring layer — Phase 1 of the approved plan.

Model: DeepSeek (deepseek-v4-flash by default) via the existing
src/llm/models.py::get_model(name, ModelProvider.DEEPSEEK, ...) path —
DEEPSEEK_API_KEY must be set (see .env.local). Override the model with
the HUNDRED_Q_QUAL_MODEL env var.

Reuses complacency's evidence-gathering primitives (SEC 10-K section
extraction, DEF 14A excerpts, Tavily search, FMP news) — those are
generic SEC/Tavily/FMP fetchers, not complacency-specific, so this module
imports them directly rather than re-implementing SEC scraping.

Unlike complacency's 0-5 severity rubric (built for a short-thesis
red-flag screen), the 100-Question screener's qualitative questions are
literally yes/no by design (e.g. "does the company have network
effects?") — so the LLM is asked for a direct boolean `answer`, not a
score to binarize afterward.

PHASE 1 SCOPE: a representative subset of qualitative questions (one per
distinct evidence-gathering pattern: 10-K narrative, DEF 14A proxy,
catalyst/calendar, litigation/risk-factors) to prove the pillar-scoped
LLM architecture end-to-end and validate cost/latency/evidence quality —
not the full ~30-question buildout yet. Extending INDICATORS with the
rest of Pillar 3/4/5/6's qual questions is mechanical repetition of the
patterns already here.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from src.llm.models import ModelProvider, get_model
from src.research_ideas.complacency.evidence_sources import (
    fetch_sec_10k_sections,
    fetch_sec_def14a_excerpt,
    fetch_sec_recent_8k,
    format_8k_filings_for_prompt,
    fetch_stock_news,
    tavily_search,
)
from src.research_ideas.hundred_q import storage
from src.research_ideas.hundred_q.schemas import QualEvidence, QuestionAnswer

logger = logging.getLogger(__name__)

# ─── Model config ────────────────────────────────────────────────────────

QUAL_MODEL_NAME = os.environ.get("HUNDRED_Q_QUAL_MODEL", "deepseek-v4-flash")
QUAL_MODEL_PROVIDER = ModelProvider.DEEPSEEK
QUAL_CACHE_TTL_DAYS = 7


class _LLMEvidence(BaseModel):
    source: str = Field(description="Source label, e.g. '10-K risk factors' or 'DEF 14A comp table'")
    quote: str = Field(description="Verbatim quote from the source (<= 300 chars)")
    date: Optional[str] = Field(default=None, description="ISO date yyyy-mm-dd if known")
    url: Optional[str] = Field(default=None)


class _LLMQualOutput(BaseModel):
    """Structured output for a single yes/no qualitative question."""
    answer: bool = Field(description="True if the evidence supports a YES answer to the question, else False.")
    confidence: float = Field(ge=0.0, le=1.0, description="0=no confidence, 1=certain. <0.4 if no usable evidence found.")
    summary: str = Field(description="One-line justification, <= 200 chars")
    evidence: list[_LLMEvidence] = Field(default_factory=list, description="Verbatim quotes backing the answer")


_SHARED_SYSTEM_INSTRUCTIONS = """
You are a fundamental research analyst answering ONE yes/no due-diligence question about a company.

STRICT RULES:
1. Use ONLY the evidence provided. Do NOT invent facts not present in the evidence.
2. If the evidence is insufficient to answer confidently, set answer=false and confidence < 0.4 — do not guess.
3. Every evidence quote must be VERBATIM (copied from the provided text), <= 300 chars.
4. Be conservative: if the evidence is mixed or weak, prefer answer=false with a summary explaining the ambiguity.
5. A single high-quality source (10-K, DEF 14A, official press release) is sufficient if it directly addresses the question.

OUTPUT FORMAT (JSON only):
{
  "answer": true|false,
  "confidence": <0-1 float>,
  "summary": "<<=200 char justification>",
  "evidence": [{"source": "<label>", "quote": "<verbatim <=300 chars>", "date": "yyyy-mm-dd"}]
}
"""


def _build_user_prompt(ticker: str, name: str, sector: Optional[str], question_label: str, rubric: str, packs: list[dict]) -> str:
    parts = [
        f"TICKER: {ticker} ({name})",
        f"SECTOR: {sector or 'unknown'}",
        "",
        f"QUESTION: {question_label}",
        "",
        "GUIDANCE:",
        rubric.strip(),
        "",
        "EVIDENCE PROVIDED:",
    ]
    if not packs:
        parts.append("(none — return answer=false, confidence < 0.4)")
    else:
        for i, pack in enumerate(packs, 1):
            parts.append(f"--- Source {i}: {pack.get('source', '?')} ({pack.get('date', '?')}) ---")
            parts.append((pack.get("text") or "").strip()[:4000])
            parts.append("")
    parts.append("Now return the JSON answer.")
    return "\n".join(parts)


def _call_llm_indicator(system_prompt: str, user_prompt: str) -> tuple[Optional[_LLMQualOutput], float]:
    """Call the configured DeepSeek model for one qualitative question.
    Returns (parsed_output, approx_cost_usd)."""
    llm = get_model(QUAL_MODEL_NAME, QUAL_MODEL_PROVIDER, None)
    if llm is None:
        logger.warning("hundred_q qualitative: LLM unavailable (DEEPSEEK_API_KEY missing?)")
        return None, 0.0

    structured = llm.with_structured_output(_LLMQualOutput, method="json_mode")
    messages = [("system", system_prompt), ("human", user_prompt)]

    try:
        result = structured.invoke(messages)
    except Exception as exc:
        logger.warning("DeepSeek structured-output failed (%s); retrying raw.", exc)
        try:
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                result = _LLMQualOutput(**json.loads(text[start:end + 1]))
            else:
                return None, 0.0
        except Exception:
            logger.exception("DeepSeek JSON extraction failed for indicator call")
            return None, 0.0

    # DeepSeek pricing is far cheaper than Qwen Max; rough estimate only.
    approx_input_tokens = len(system_prompt + user_prompt) // 3
    approx_output_tokens = 300
    cost = (approx_input_tokens * 0.27e-6) + (approx_output_tokens * 1.1e-6)
    return result, cost


# ─── Evidence gatherers ─────────────────────────────────────────────────

def _tavily_packs(query: str, days: int, max_results: int = 4) -> list[dict]:
    out = []
    for r in tavily_search(query, days=days, max_results=max_results):
        out.append({
            "source": f"Tavily - {r.get('title', '')[:80]}",
            "date": (r.get("published_date") or "")[:10],
            "text": f"{r.get('title', '')}\n\n{r.get('content', '')}",
        })
    return out


def _news_packs(ticker: str, days: int = 90, limit: int = 4) -> list[dict]:
    out = []
    for r in fetch_stock_news(ticker, days=days, limit=limit):
        out.append({"source": r["source"], "date": r["date"], "text": f"{r['title']}\n\n{r['text']}"})
    return out


def _gather_10k_moat(ticker: str) -> list[dict]:
    """Generic 10-K narrative evidence for moat-style questions."""
    packs = []
    sec = fetch_sec_10k_sections(ticker)
    if sec.get("mda"):
        packs.append({"source": f"10-K MD&A (filed {sec.get('_filed_date', '?')})", "date": sec.get("_filed_date", ""), "text": sec["mda"]})
    if sec.get("risk_factors"):
        packs.append({"source": f"10-K Risk Factors (filed {sec.get('_filed_date', '?')})", "date": sec.get("_filed_date", ""), "text": sec["risk_factors"][:4000]})
    return packs


def _gather_evidence_market_share(ticker: str) -> list[dict]:
    packs = _gather_10k_moat(ticker)
    packs.extend(_tavily_packs(f'"{ticker}" market share leader OR "number one" OR "market leader"', days=365, max_results=3))
    return packs


def _gather_evidence_network_effects(ticker: str) -> list[dict]:
    packs = _gather_10k_moat(ticker)
    packs.extend(_tavily_packs(f'"{ticker}" network effects OR platform OR ecosystem lock-in', days=365, max_results=3))
    return packs


def _gather_evidence_switching_costs(ticker: str) -> list[dict]:
    packs = _gather_10k_moat(ticker)
    packs.extend(_tavily_packs(f'"{ticker}" switching costs OR customer lock-in OR integration cost', days=365, max_results=3))
    return packs


def _gather_evidence_pricing_power(ticker: str) -> list[dict]:
    packs = _gather_10k_moat(ticker)
    packs.extend(_news_packs(ticker, days=180, limit=4))
    packs.extend(_tavily_packs(f'"{ticker}" price increase OR raised prices OR pricing power', days=365, max_results=3))
    return packs


# ─── Gatherer factories ─────────────────────────────────────────────────
# The remaining ~23 questions reuse the same handful of evidence shapes
# (10-K narrative, DEF 14A section, 8-K item-filtered, news+Tavily-only) —
# factories avoid ~23 near-duplicate hand-written gatherer bodies.

def _make_10k_and_tavily_gatherer(
    tavily_query: str, days: int = 365, max_results: int = 4, include_news: bool = False,
) -> Callable[[str], list[dict]]:
    def _gather(ticker: str) -> list[dict]:
        packs = _gather_10k_moat(ticker)
        if include_news:
            packs.extend(_news_packs(ticker, days=180, limit=4))
        packs.extend(_tavily_packs(tavily_query.format(ticker=ticker), days=days, max_results=max_results))
        return packs
    return _gather


def _make_def14a_gatherer(
    section_keys: list[str], tavily_query: str, days: int = 365, max_results: int = 4,
) -> Callable[[str], list[dict]]:
    def _gather(ticker: str) -> list[dict]:
        packs: list[dict] = []
        proxy = fetch_sec_def14a_excerpt(ticker)
        if proxy:
            sections = proxy.get("sections") or {}
            for key in section_keys:
                snip = sections.get(key)
                if snip:
                    packs.append({
                        "source": f"DEF 14A {key.replace('_', ' ')} (filed {proxy.get('_filed_date', '?')})",
                        "date": proxy.get("_filed_date", ""), "text": snip[:4000],
                    })
        packs.extend(_tavily_packs(tavily_query.format(ticker=ticker), days=days, max_results=max_results))
        return packs
    return _gather


def _make_8k_and_tavily_gatherer(
    item_prefixes: tuple[str, ...], tavily_query: str, news_days: int = 180, tavily_days: int = 180,
) -> Callable[[str], list[dict]]:
    def _gather(ticker: str) -> list[dict]:
        packs: list[dict] = []
        filings_8k = fetch_sec_recent_8k(ticker, days=news_days, limit=12)
        relevant = [f for f in filings_8k if any(
            (it.get("code", "") if isinstance(it, dict) else str(it)).startswith(item_prefixes)
            for it in (f.get("items") or [])
        )]
        if relevant:
            packs.append({
                "source": f"SEC 8-K filings (last {news_days} days, relevant items)",
                "date": relevant[0].get("filed_date", ""),
                "text": format_8k_filings_for_prompt(relevant),
            })
        packs.extend(_tavily_packs(tavily_query.format(ticker=ticker), days=tavily_days, max_results=4))
        return packs
    return _gather


def _make_news_and_tavily_gatherer(
    tavily_query: str, news_days: int = 180, tavily_days: int = 180, news_limit: int = 5,
) -> Callable[[str], list[dict]]:
    def _gather(ticker: str) -> list[dict]:
        packs = _news_packs(ticker, days=news_days, limit=news_limit)
        packs.extend(_tavily_packs(tavily_query.format(ticker=ticker), days=tavily_days, max_results=4))
        return packs
    return _gather


def _make_10k_risk_only_and_tavily_gatherer(
    tavily_query: str, days: int = 180, max_results: int = 4,
) -> Callable[[str], list[dict]]:
    def _gather(ticker: str) -> list[dict]:
        packs: list[dict] = []
        sec = fetch_sec_10k_sections(ticker)
        if sec.get("risk_factors"):
            packs.append({
                "source": f"10-K Risk Factors (filed {sec.get('_filed_date', '?')})",
                "date": sec.get("_filed_date", ""), "text": sec["risk_factors"][:4000],
            })
        packs.extend(_tavily_packs(tavily_query.format(ticker=ticker), days=days, max_results=max_results))
        return packs
    return _gather


# Gatherers built from the factories above (kept as module-level names,
# not inlined in the registry, so they read the same way as the earlier
# hand-written ones and stay independently testable).
_gather_evidence_brand_nps = _make_news_and_tavily_gatherer(
    '"{ticker}" brand loyalty OR customer satisfaction OR NPS OR net promoter score', news_days=180, tavily_days=365,
)
_gather_evidence_comp_roic = _make_def14a_gatherer(
    ["executive_compensation"], '"{ticker}" executive compensation ROIC OR free cash flow performance metric',
)
_gather_evidence_ma_discipline = _make_news_and_tavily_gatherer(
    '"{ticker}" acquisition OR goodwill impairment OR M&A track record', news_days=730, tavily_days=730, news_limit=6,
)
_gather_evidence_catalyst = _make_news_and_tavily_gatherer(
    '"{ticker}" catalyst OR product launch OR spin-off OR margin expansion', news_days=60, tavily_days=180,
)
_gather_evidence_litigation = _make_10k_risk_only_and_tavily_gatherer(
    '"{ticker}" lawsuit OR litigation OR regulatory investigation OR antitrust', days=180,
)


# ─── Question registry ───────────────────────────────────────────────────
# ~33 qualitative questions from the approved plan's reconciled list
# (§1), covering every distinct evidence-gathering shape. Entries with
# "alias_of" (P6.6) resolve to another question's cached answer instead
# of a second LLM call — the plan explicitly calls out P3.11 (TAM growth)
# and P6.6 (secular tailwinds) as the same underlying judgment, so
# scoring both would just double the LLM cost for no new signal.

_QUAL_INDICATORS: dict[str, dict] = {
    # ── Pillar 3 — Moat & Competitive Position ──────────────────────────
    "P3.1": {
        "pillar": "P3", "label": "Top-3 market share in primary industry",
        "gather": _gather_evidence_market_share,
        "rubric": "Answer true only if evidence explicitly supports the company being a top-3 player by market share in its primary industry.",
    },
    "P3.2": {
        "pillar": "P3", "label": "Clear network effects that strengthen with user base growth",
        "gather": _gather_evidence_network_effects,
        "rubric": "Answer true only if the business model shows genuine network effects (value to each user grows as more users join), not just scale economies.",
    },
    "P3.3": {
        "pillar": "P3", "label": "High customer switching costs",
        "gather": _gather_evidence_switching_costs,
        "rubric": "Answer true only if customers face meaningful switching costs (integration effort, data lock-in, retraining, contractual penalties) that would deter churn.",
    },
    "P3.4": {
        "pillar": "P3", "label": "Strong pricing power (can raise prices without losing demand)",
        "gather": _gather_evidence_pricing_power,
        "rubric": "Answer true only if there is evidence of successful recent price increases without a corresponding drop in volume/demand.",
    },
    "P3.5": {
        "pillar": "P3", "label": "Defensible intellectual property (patents, proprietary tech)",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" patents OR proprietary technology OR intellectual property portfolio'),
        "rubric": "Answer true only if the company holds patents or proprietary technology explicitly described as a competitive barrier, not just routine IP filings.",
    },
    "P3.6": {
        "pillar": "P3", "label": "Product/service differentiated vs low-cost substitutes",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" differentiated product OR unique features OR premium positioning vs competitors'),
        "rubric": "Answer true only if there is clear evidence the product/service is differentiated (features, quality, ecosystem) rather than competing mainly on price.",
    },
    "P3.9": {
        "pillar": "P3", "label": "Brand equity / NPS above industry average",
        "gather": _gather_evidence_brand_nps,
        "rubric": "Answer true only if there is credible evidence (survey data, press coverage, analyst commentary) that brand loyalty or customer satisfaction is above typical industry levels.",
    },
    "P3.8": {
        "pillar": "P3", "label": "Low supplier concentration (top-5 suppliers < 30% of COGS)",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" supplier concentration OR single supplier dependency OR sole source supplier'),
        "rubric": "Answer true only if evidence indicates the company is NOT heavily dependent on a small number of suppliers. Answer false if a single-supplier or concentrated-supplier risk is disclosed.",
    },
    "P3.10": {
        "pillar": "P3", "label": "Benefits from economies of scale vs competitors",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" economies of scale OR scale advantage OR cost advantage from size'),
        "rubric": "Answer true only if there is evidence the company's unit costs are lower than competitors specifically because of its scale (not just because it's profitable).",
    },
    "P3.11": {
        "pillar": "P3", "label": "Total addressable market (TAM) growing > 8%/yr",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" total addressable market TAM growth rate OR industry growth forecast', tavily_days=365),
        "rubric": "Answer true only if credible sources (analyst research, industry reports, company disclosures) indicate the company's core addressable market is growing faster than roughly 8% per year.",
    },
    "P3.12": {
        "pillar": "P3", "label": "Market structure consolidating rather than fragmented",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" industry consolidation OR market fragmentation OR M&A wave in industry', tavily_days=365),
        "rubric": "Answer true only if evidence shows the industry is consolidating (fewer, larger players gaining share) rather than remaining fragmented with many small competitors.",
    },
    "P3.13": {
        "pillar": "P3", "label": "Product lifecycle resilient to quick technological obsolescence",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" product lifecycle OR technology obsolescence risk OR platform longevity'),
        "rubric": "Answer true only if the product/platform has a track record of longevity and adaptation, without evidence of being at near-term risk of obsolescence.",
    },
    "P3.14": {
        "pillar": "P3", "label": "High regulatory barriers to entry for new competitors",
        "gather": _make_10k_risk_only_and_tavily_gatherer('"{ticker}" regulatory barriers to entry OR licensing requirements OR compliance costs deter new entrants'),
        "rubric": "Answer true only if regulatory, licensing, or compliance requirements are explicitly described as a meaningful barrier to new entrants in this industry.",
    },
    "P3.15": {
        "pillar": "P3", "label": "Controls a proprietary distribution network or critical supply channel",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" proprietary distribution network OR exclusive distribution channel OR direct sales advantage'),
        "rubric": "Answer true only if the company controls a distribution channel or supply relationship that competitors cannot easily replicate.",
    },
    "P3.16": {
        "pillar": "P3", "label": "LTV/CAC > 3:1, or NRR > 105%, or churn < 5%",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" net revenue retention NRR OR LTV CAC ratio OR customer churn rate', news_days=120, tavily_days=180),
        "rubric": "Answer true only if disclosed or credibly reported unit-economics metrics (NRR, LTV/CAC, or churn) meet the stated thresholds. Answer false if metrics are worse or undisclosed/unclear.",
    },
    "P3.18": {
        "pillar": "P3", "label": "Lack of direct, lower-cost technological alternatives",
        "gather": _make_10k_and_tavily_gatherer('"{ticker}" no viable alternative OR lack of substitutes OR customers switching to competitor product'),
        "rubric": "Answer true only if there is no credible evidence of an emerging lower-cost substitute actively taking share. Answer false if such a substitute is disclosed or reported.",
    },

    # ── Pillar 4 — Management & Governance ──────────────────────────────
    "P4.3": {
        "pillar": "P4", "label": "Management incentive comp explicitly tied to ROIC/FCF rather than just revenue",
        "gather": _gather_evidence_comp_roic,
        "rubric": "Answer true only if the DEF 14A / compensation disclosure explicitly ties incentive pay to ROIC, FCF, or similar capital-efficiency metrics (not just revenue growth or stock price alone).",
    },
    "P4.4": {
        "pillar": "P4", "label": "Executive management stable, no key turnover in last 3 years",
        "gather": _make_8k_and_tavily_gatherer(("5.0", "5.1"), '"{ticker}" CEO OR CFO departure OR executive turnover OR management change', news_days=1095),
        "rubric": "Answer true only if there is no evidence of a CEO, CFO, or other key-executive departure/replacement in roughly the last 3 years. Answer false if such a change is disclosed.",
    },
    "P4.5": {
        "pillar": "P4", "label": "Track record of disciplined M&A execution",
        "gather": _gather_evidence_ma_discipline,
        "rubric": "Answer true only if the company's acquisition history over the past several years shows disciplined execution (reasonable multiples paid, minimal large goodwill impairments) rather than value-destroying deals.",
    },
    "P4.10": {
        "pillar": "P4", "label": "Management clearly articulates long-term capital allocation priorities",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" capital allocation priorities OR capital allocation framework OR investor day'),
        "rubric": "Answer true only if management has publicly and specifically articulated capital-allocation priorities (e.g. buybacks vs. reinvestment vs. M&A vs. dividends), not just generic language.",
    },
    "P4.11": {
        "pillar": "P4", "label": "Related-party transactions absent or minimal (< 1% of revenue)",
        "gather": _make_def14a_gatherer(["related_party"], '"{ticker}" related party transactions disclosed'),
        "rubric": "Answer true only if the DEF 14A discloses no related-party transactions, or explicitly small/immaterial ones. Answer false if a material related-party arrangement is disclosed.",
    },
    "P4.12": {
        "pillar": "P4", "label": "Active, transparent succession plan for senior executives",
        "gather": _make_def14a_gatherer(["executive_compensation", "board_independence"], '"{ticker}" CEO succession plan OR management succession OR board succession planning'),
        "rubric": "Answer true only if there is credible evidence of an active, disclosed succession plan for key executives. Answer false if no such disclosure exists (silence is common — don't assume a plan exists without evidence).",
    },
    "P4.14": {
        "pillar": "P4", "label": "Insider cluster-buy narrative: who is buying and why (context on Form-4 activity)",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" insider buying OR executive purchased shares OR insider stock purchase open market'),
        "rubric": "Answer true only if there is evidence of a notable, discretionary open-market insider purchase (not routine 10b5-1 activity) with a credible bullish signal. This is a narrative overlay on top of the quantitative A/D ratio, not a replacement for it.",
    },
    "P4.15": {
        "pillar": "P4", "label": "Executive compensation reasonably benchmarked vs peers",
        "gather": _make_def14a_gatherer(["executive_compensation", "ceo_pay_ratio"], '"{ticker}" CEO compensation vs peers OR executive pay benchmarking OR pay ratio criticism'),
        "rubric": "Answer true only if compensation appears reasonably benchmarked against peers (no evidence of significant outlier pay or shareholder criticism). Answer false if evidence suggests compensation is a governance concern.",
    },
    "P4.16": {
        "pillar": "P4", "label": "No major management-credibility red flags (scandals, restatements, abrupt departures)",
        "gather": _make_8k_and_tavily_gatherer(("5.0", "4.0"), '"{ticker}" CEO OR CFO scandal OR investigation OR restatement OR abrupt resignation', news_days=730),
        "rubric": "Answer true only if there is no evidence of a management-credibility red flag (scandal, SEC inquiry, restatement, abrupt/unexplained departure). Answer false if any such red flag is disclosed.",
    },

    # ── Pillar 5 — Valuation & Margin of Safety ──────────────────────────
    "P5.7": {
        "pillar": "P5", "label": "Valuation multiple derated without a fundamental business shift",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" multiple compression OR valuation de-rating OR why has the stock multiple fallen'),
        "rubric": "Answer true only if evidence suggests the valuation multiple has compressed WITHOUT a corresponding deterioration in the underlying business (i.e. a sentiment-driven de-rating, not a fundamentals-driven one).",
    },
    "P5.9": {
        "pillar": "P5", "label": "Attractive upside/downside asymmetry (> 3:1)",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" upside downside risk reward OR bull case bear case price target range'),
        "rubric": "Answer true only if credible bull/bear analysis suggests the potential upside is roughly 3x or more the potential downside from current levels. Default to false if evidence is insufficient to judge asymmetry.",
    },
    "P5.10": {
        "pillar": "P5", "label": "Sell-side earnings expectations conservative/realistic vs recent trends",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" analyst estimates vs company guidance OR consensus estimates too high OR too low'),
        "rubric": "Answer true only if evidence suggests sell-side estimates are conservative or realistic relative to the company's own guidance and recent trends. Answer false if evidence suggests estimates are aggressive/unrealistic.",
    },

    # ── Pillar 6 — Catalysts, Risks & Market Dynamics ────────────────────
    "P6.1": {
        "pillar": "P6", "label": "Clear, identifiable 12-18 month catalyst",
        "gather": _gather_evidence_catalyst,
        "rubric": "Answer true only if a specific, dated or near-term catalyst (new product, margin-expansion program, spin-off, major contract) is identifiable within the next 12-18 months.",
    },
    "P6.4": {
        "pillar": "P6", "label": "Free from imminent adverse regulatory or litigation threats",
        "gather": _gather_evidence_litigation,
        "rubric": "Answer true only if there is NO evidence of a material, imminent lawsuit, antitrust action, or regulatory investigation. Answer false if such a threat is disclosed.",
    },
    "P6.5": {
        "pillar": "P6", "label": "Business model resilient to macroeconomic downturns/recessions",
        "gather": _make_news_and_tavily_gatherer('"{ticker}" recession resilient OR macro headwinds impact OR economic downturn demand'),
        "rubric": "Answer true only if there is credible evidence the business has historically held up well, or is structurally insulated, in economic downturns. Answer false if evidence points to high macro cyclicality.",
    },
    "P6.6": {
        "pillar": "P6", "label": "Benefits from long-term secular megatrend tailwinds",
        "alias_of": "P3.11",
    },
    "P6.8": {
        "pillar": "P6", "label": "Key operational inputs/raw materials free from supply-chain bottlenecks",
        "gather": _make_10k_risk_only_and_tavily_gatherer('"{ticker}" supply chain disruption OR raw material shortage OR input cost bottleneck'),
        "rubric": "Answer true only if there is no evidence of a material, ongoing supply-chain or input-availability bottleneck. Answer false if such a bottleneck is disclosed or reported.",
    },
}


def _answer_to_question_answer(question_id: str, cache_row: dict) -> QuestionAnswer:
    evidence_raw = json.loads(cache_row.get("evidence_json") or "[]")
    return QuestionAnswer(
        question_id=question_id,
        pillar=cache_row["pillar"],
        label=_QUAL_INDICATORS.get(question_id, {}).get("label", question_id),
        q_type="qual",
        answer=None if cache_row.get("answer") is None else bool(cache_row["answer"]),
        source=f"llm_{cache_row.get('model_used', 'unknown')}",
        evaluated_at=cache_row.get("last_evaluated_at"),
        confidence=cache_row.get("confidence"),
        evidence=[QualEvidence(**e) for e in evidence_raw],
        threshold_desc=cache_row.get("summary"),
    )


def get_stale_qual_questions(ticker: str, max_age_days: int = 365) -> list[str]:
    """Which registered qualitative questions are missing/stale for this
    ticker — used by the quarterly annual-backstop job (Phase 3), the one
    place a near-full qual sweep is legitimate rather than pillar-scoped."""
    all_ids = list(_QUAL_INDICATORS.keys())
    return storage.get_stale_qual_question_ids(ticker, all_ids, max_age_days=max_age_days)


def assess_qualitative_pillar(
    ticker: str,
    name: str,
    sector: Optional[str],
    question_ids: list[str],
    force_refresh: bool = False,
    triggered_by: str = "manual",
) -> dict[str, QuestionAnswer]:
    """
    Score exactly the requested question_ids — never more. Reads
    hq_qualitative_cache first (7-day TTL); only calls the LLM for
    cache-misses or when force_refresh=True. This is the mechanism behind
    "a Form-4 trigger only re-scores 1-2 questions, not all ~30" from the
    approved plan's cost-control design (Layer 2/3).
    """
    ticker = ticker.upper()
    results: dict[str, QuestionAnswer] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=QUAL_CACHE_TTL_DAYS)

    cached = {} if force_refresh else storage.get_qual_cache(ticker, question_ids)

    to_score: list[str] = []
    to_alias: list[str] = []
    for qid in question_ids:
        qdef = _QUAL_INDICATORS.get(qid)
        if qdef is None:
            continue
        row = cached.get(qid)
        if row:
            try:
                evaluated_at = datetime.fromisoformat(row["last_evaluated_at"])
                if evaluated_at.tzinfo is None:
                    evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
                if evaluated_at >= cutoff:
                    results[qid] = _answer_to_question_answer(qid, row)
                    continue
            except Exception:
                pass
        if "alias_of" in qdef:
            to_alias.append(qid)
        else:
            to_score.append(qid)

    for qid in to_score:
        qdef = _QUAL_INDICATORS[qid]
        packs = qdef["gather"](ticker)
        system_prompt = _SHARED_SYSTEM_INSTRUCTIONS
        user_prompt = _build_user_prompt(ticker, name, sector, qdef["label"], qdef["rubric"], packs)

        parsed, cost = _call_llm_indicator(system_prompt, user_prompt)
        now_iso = datetime.now(timezone.utc).isoformat()

        if parsed is None:
            answer, confidence, summary, evidence = None, None, "LLM call failed", []
        else:
            answer, confidence, summary = parsed.answer, parsed.confidence, parsed.summary
            evidence = [e.model_dump() for e in parsed.evidence]

        storage.set_qual_answer(
            ticker=ticker, question_id=qid, pillar=qdef["pillar"], answer=answer,
            confidence=confidence, summary=summary, evidence_json=json.dumps(evidence),
            model_used=QUAL_MODEL_NAME, cost_usd=cost, triggered_by=triggered_by,
        )

        results[qid] = QuestionAnswer(
            question_id=qid, pillar=qdef["pillar"], label=qdef["label"], q_type="qual",
            answer=answer, source=f"llm_{QUAL_MODEL_NAME}", evaluated_at=now_iso,
            confidence=confidence, evidence=[QualEvidence(**e) for e in evidence],
            threshold_desc=summary,
        )

    # ── Alias resolution — zero extra LLM cost ──────────────────────────
    # (e.g. P6.6 "secular tailwinds" reuses P3.11 "TAM growth" — the plan
    # flags these as the same underlying judgment; scoring both would
    # just double the LLM spend for no new signal.)
    for qid in to_alias:
        qdef = _QUAL_INDICATORS[qid]
        target = qdef["alias_of"]
        target_qa = results.get(target)
        if target_qa is None:
            target_qa = assess_qualitative_pillar(
                ticker, name, sector, [target], force_refresh=force_refresh, triggered_by=triggered_by,
            ).get(target)
        if target_qa is None:
            continue

        evidence_dicts = [e.model_dump() for e in target_qa.evidence]
        storage.set_qual_answer(
            ticker=ticker, question_id=qid, pillar=qdef["pillar"], answer=target_qa.answer,
            confidence=target_qa.confidence, summary=f"[alias of {target}] {target_qa.threshold_desc}",
            evidence_json=json.dumps(evidence_dicts), model_used=QUAL_MODEL_NAME, cost_usd=0.0,
            triggered_by=f"alias_of:{target}",
        )
        results[qid] = QuestionAnswer(
            question_id=qid, pillar=qdef["pillar"], label=qdef["label"], q_type="qual",
            answer=target_qa.answer, source=target_qa.source, evaluated_at=target_qa.evaluated_at,
            confidence=target_qa.confidence, evidence=target_qa.evidence,
            threshold_desc=f"[alias of {target}] {target_qa.threshold_desc}",
        )

    return results


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(override=True)
    load_dotenv(".env.local", override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    test_tickers = [
        ("AAPL", "Apple", "Technology"),
        ("MA", "Mastercard", "Financial Services"),
        ("TSLA", "Tesla", "Consumer Cyclical"),
    ]
    question_ids = list(_QUAL_INDICATORS.keys())

    total_cost = 0.0
    for ticker, name, sector in test_tickers:
        print(f"\n{'='*70}\n{ticker} ({name})\n{'='*70}")
        answers = assess_qualitative_pillar(ticker, name, sector, question_ids, force_refresh=True, triggered_by="phase1_test")
        for qid, qa in answers.items():
            print(f"  {qid:<6} answer={qa.answer!s:<5} conf={qa.confidence} :: {qa.threshold_desc}")
            for ev in qa.evidence[:1]:
                print(f"         evidence: [{ev.source}] \"{ev.quote[:120]}\"")
    print(f"\nDone.")

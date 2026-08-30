"""
src/memory/assumption_extract.py
================================
Workstream R1 — structured assumption extraction (one bounded LLM pass per
source, Q1 bundle-call discipline).

Two extractors:
  * extract_company_assumptions — channel 1+2 output (EDGAR press release
    + FMP transcript) → earnings_assumptions row fields.
  * extract_analyst_report      — channel 3 output (deposited PDF text,
    vision-capable) → analyst_reports row fields.

Model discipline (lessons from Workstream Q1 live gates):
  * qwen3.6-plus via ALIBABA (env ASSUMPTION_MODEL override), resolved by
    get_model so DEEP_RESEARCH_API_KEY flows from env;
  * dedicated client timeout=480/max_retries=0 — hidden in-call retry
    chains only add latency; raw-retry + salvage catches parse failures;
  * enable_thinking=False — reasoning tokens truncated the ARM bundle;
  * include_raw=True + lenient coercion — one bad field never rejects the
    whole extraction;
  * images (raster exhibit pages) only when ASSUMPTION_VISION_MODEL is
    set; text-only is the safe default (the SOTP pipeline path already
    vision-reads the same PDFs at run time).

Also builds assumption_versions rows (R1 writes them too — R3's
recursion data starts accumulating from day one).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

ASSUMPTION_MODEL_NAME = os.environ.get("ASSUMPTION_MODEL", "qwen3.6-plus")
_BUNDLE_TIMEOUT_S = 480
_BUNDLE_MAX_TOKENS = 12000
_BUNDLE_EXTRA_BODY = {"enable_thinking": False}

_PRESS_MAX_CHARS = 30_000
_PREPARED_MAX_CHARS = 24_000
_QA_MAX_CHARS = 16_000
_PDF_MAX_CHARS = 80_000


# ── Output schemas (lenient: everything optional, values as raw strings) ────
#
# Live Qwen output deviates from the array/object shape even under json_mode
# (seen 2026-08-24 on BABA: fiscal_quarter "Q1" as a string, guidance and
# segments returned as objects instead of arrays).  Every collection field
# therefore carries a lenient before-validator (Q1 lesson: one bad shape
# must never reject the whole extraction).

def _coerce_list(v):
    """Accept list, dict-of-items, {note, items}, or scalar → list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        if isinstance(v.get("items"), list):
            return v["items"]
        out = []
        for k, item in v.items():
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault("name", k)
                merged.setdefault("metric", k)
                merged.setdefault("field", k)
                merged.setdefault("item", k)
                out.append(merged)
            elif isinstance(item, str):
                out.append({"name": k, "value": item})
        return out
    return [v]


def _coerce_quotes(v):
    out = []
    for q in _coerce_list(v):
        if isinstance(q, str):
            out.append(q)
        elif isinstance(q, dict):
            out.append(str(q.get("quote") or q.get("text")
                           or q.get("value") or q))
        else:
            out.append(str(q))
    return out


def _coerce_year(v):
    if v is None or isinstance(v, int):
        return v
    m = re.search(r"((?:19|20)\d{2})", str(v))
    return int(m.group(1)) if m else None


def _coerce_quarter(v):
    if v is None:
        return None
    if isinstance(v, int):
        return v if 1 <= v <= 4 else None
    s = str(v).strip().lower()
    m = re.search(r"q\s*([1-4])", s) or re.fullmatch(r"([1-4])", s) \
        or re.search(r"([1-4])(?:st|nd|rd|th)?\s+quarter", s)
    return int(m.group(1)) if m else None


def _first_present(d: dict, keys: tuple):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _opt_str(v):
    """Optional[str] coercion — the model returns numerics (4 for '4%')
    for growth/price fields typed as Optional[str]; int/float must not
    fail validation."""
    return None if v is None else str(v)


def _alias_norm(data, aliases: dict):
    """Map common alternative key names onto canonical schema fields
    (model drifts in key naming even under json_mode). Only fills a
    canonical field when it is currently empty."""
    if not isinstance(data, dict):
        return data
    d = dict(data)
    for canon, srcs in aliases.items():
        if d.get(canon) in (None, ""):
            got = _first_present(d, srcs)
            if got is not None:
                d[canon] = got
    return d


_SEGMENT_ALIASES = {
    "growth_rate_pct": ("reported_yoy_growth", "yoy_growth", "growth_rate",
                        "growth", "revenue_growth", "yoy"),
    "outlook": ("commentary", "note", "guidance"),
}
_MARGIN_ALIASES = {
    "driver": ("drivers", "reason", "commentary"),
    "metric": ("name", "margin"),
    "quote": ("reported", "reported_value", "value"),
}
_KPI_ALIASES = {
    "value": ("description", "detail", "reported", "note"),
    "growth_pct": ("growth", "yoy", "yoy_growth", "growth_rate"),
}


class GuidanceItem(BaseModel):
    metric: str = Field(default="", description="revenue|ebitda|adj_ebita|eps|capex|other")
    period: str = Field(default="", description="fiscal period, e.g. FY2027, Q1 FY2027")
    low: Optional[str] = None
    mid: Optional[str] = None
    high: Optional[str] = None
    unit: str = ""
    quote: str = ""

    @field_validator("metric", "period", "unit", "quote", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("low", "mid", "high", mode="before")
    @classmethod
    def _opt(cls, v):
        return _opt_str(v)


class SegmentItem(BaseModel):
    name: str = ""
    growth_rate_pct: Optional[str] = None
    outlook: str = ""
    constant_currency_note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _norm(cls, data):
        return _alias_norm(data, _SEGMENT_ALIASES)

    @field_validator("name", "outlook", "constant_currency_note", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("growth_rate_pct", mode="before")
    @classmethod
    def _opt(cls, v):
        return _opt_str(v)


class MarginItem(BaseModel):
    metric: str = Field(default="", description="gross|operating|ebita|fcf_margin|other")
    direction: str = Field(default="", description="up|down|flat|guided_range")
    driver: str = ""
    quote: str = ""

    @model_validator(mode="before")
    @classmethod
    def _norm(cls, data):
        return _alias_norm(data, _MARGIN_ALIASES)

    @field_validator("metric", "direction", "driver", "quote", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class KpiItem(BaseModel):
    name: str = ""
    value: str = ""
    growth_pct: Optional[str] = None
    quote: str = ""

    @model_validator(mode="before")
    @classmethod
    def _norm(cls, data):
        return _alias_norm(data, _KPI_ALIASES)

    @field_validator("name", "value", "quote", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("growth_pct", mode="before")
    @classmethod
    def _opt(cls, v):
        return _opt_str(v)


class CapAllocItem(BaseModel):
    action: str = Field(default="", description="buyback|dividend|ma|debt_repay|other")
    detail: str = ""

    @field_validator("action", "detail", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class OneOffItem(BaseModel):
    item: str = ""
    amount: str = ""
    impact: str = ""

    @field_validator("item", "amount", "impact", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class CallSignalItem(BaseModel):
    """One qualitative reading of the call — never feeds a number."""
    topic: str = ""
    detail: str = ""
    quote: str = ""

    @field_validator("topic", "detail", "quote", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class EarningsAssumptionOutput(BaseModel):
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
    fiscal_period_label: str = ""
    guidance: list[GuidanceItem] = Field(default_factory=list)
    segments: list[SegmentItem] = Field(default_factory=list)
    margins: list[MarginItem] = Field(default_factory=list)
    kpis: list[KpiItem] = Field(default_factory=list)
    capital_allocation: list[CapAllocItem] = Field(default_factory=list)
    one_offs: list[OneOffItem] = Field(default_factory=list)
    verbatim_quotes: list[str] = Field(default_factory=list)
    # Qualitative call signals — these feed the LLM write-up and R3's
    # challenge loop, never an intrinsic value. Transcript-only: none of
    # this exists in a press release or a filing.
    tone_shift: str = ""
    qa_pressure: list[CallSignalItem] = Field(default_factory=list)
    new_risks: list[CallSignalItem] = Field(default_factory=list)
    strategic_pivots: list[CallSignalItem] = Field(default_factory=list)
    regulatory: list[CallSignalItem] = Field(default_factory=list)

    @field_validator("fiscal_period_label", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("fiscal_year", mode="before")
    @classmethod
    def _fy(cls, v):
        return _coerce_year(v)

    @field_validator("fiscal_quarter", mode="before")
    @classmethod
    def _fq(cls, v):
        return _coerce_quarter(v)

    @field_validator("tone_shift", mode="before")
    @classmethod
    def _tone(cls, v):
        return "" if v is None else str(v)

    @field_validator("guidance", "segments", "margins", "kpis",
                     "capital_allocation", "one_offs", "qa_pressure",
                     "new_risks", "strategic_pivots", "regulatory",
                     mode="before")
    @classmethod
    def _lists(cls, v):
        return _coerce_list(v)

    @field_validator("verbatim_quotes", mode="before")
    @classmethod
    def _qlist(cls, v):
        return _coerce_quotes(v)


class RevisionItem(BaseModel):
    field: str = Field(default="", description="what changed, e.g. FY27 capex estimate")
    new_value: str = ""
    prior_value: str = ""
    direction: str = Field(default="", description="up|down|flat")
    reason: str = ""

    @field_validator("field", "new_value", "prior_value", "direction", "reason",
                     mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class EstimateItem(BaseModel):
    fiscal_year_label: str = Field(default="", description="e.g. FY+1, FY2027")
    revenue: str = ""
    ebitda: str = ""
    eps: str = ""

    @field_validator("fiscal_year_label", "revenue", "ebitda", "eps", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class ValuationRatioItem(BaseModel):
    """One forward year from the note's ratios table.

    Nearly every broker note carries one — Goldman prints "Ratios &
    Valuation" (P/E, P/B, EV/EBITDA, EV/sales, FCF yield, ROE), Phillip
    prints "Valuation Ratios" (P/E, P/B, dividend yield). Only the
    methodology SENTENCE was being read, which states a multiple in roughly
    a quarter of notes; the table states several, per forward year, in
    nearly all of them. Those forward multiples are also the right thing to
    hold against a peer median, which is itself forward-looking.
    """
    fiscal_year_label: str = Field(default="", description="e.g. FY26e, 3/27E, 12/27E")
    pe: str = ""
    pb: str = ""
    ev_ebitda: str = ""
    ev_sales: str = ""
    fcf_yield: str = ""
    dividend_yield: str = ""
    roe: str = ""

    @field_validator("fiscal_year_label", "pe", "pb", "ev_ebitda", "ev_sales",
                     "fcf_yield", "dividend_yield", "roe", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class ScenarioItem(BaseModel):
    case: str = Field(default="", description="bull|base|bear")
    price_target: str = ""
    probability_pct: Optional[str] = None
    summary: str = ""

    @field_validator("case", "price_target", "summary", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("probability_pct", mode="before")
    @classmethod
    def _opt(cls, v):
        return _opt_str(v)


class HouseVsConsensusItem(BaseModel):
    metric: str = ""
    house_view: str = ""
    street_view: str = ""
    comment: str = ""

    @field_validator("metric", "house_view", "street_view", "comment", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class AnalystReportOutput(BaseModel):
    house: str = ""
    analyst: str = ""
    report_date: str = ""
    rating: str = ""
    price_target: str = Field(default="", description="as printed, e.g. US$186 or $725")
    price_target_currency: str = ""
    pt_methodology: str = Field(
        default="",
        description="DCF/SOTP/EV-EBITDA etc. incl. any disclosed WACC, "
                    "terminal growth, multiple")
    estimates: list[EstimateItem] = Field(default_factory=list)
    valuation_ratios: list[ValuationRatioItem] = Field(default_factory=list)
    house_vs_consensus: list[HouseVsConsensusItem] = Field(default_factory=list)
    scenarios: list[ScenarioItem] = Field(default_factory=list)
    revisions: list[RevisionItem] = Field(default_factory=list)
    thesis_points: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @field_validator("house", "analyst", "report_date", "rating",
                     "price_target", "price_target_currency",
                     "pt_methodology", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("estimates", "valuation_ratios", "house_vs_consensus",
                     "scenarios", "revisions", mode="before")
    @classmethod
    def _lists(cls, v):
        return _coerce_list(v)

    @field_validator("thesis_points", "catalysts", "risks", mode="before")
    @classmethod
    def _strlists(cls, v):
        return _coerce_quotes(v)


# ── Client plumbing (Q1 bundle pattern) ─────────────────────────────────────

def _get_bundle_client():
    """(client, model_name) or (None, None) when the model is unavailable."""
    from src.llm.models import ModelProvider, get_model
    provider = ModelProvider.ALIBABA
    llm = get_model(ASSUMPTION_MODEL_NAME, provider, None)
    if llm is None:
        logger.warning(
            "Assumption extraction: %s unavailable (DEEP_RESEARCH_API_KEY "
            "missing?)", ASSUMPTION_MODEL_NAME)
        return None, None
    try:
        from langchain_openai import ChatOpenAI as _ChatOpenAI
        client = _ChatOpenAI(
            model=llm.model_name,
            api_key=llm.openai_api_key,
            base_url=llm.openai_api_base,
            timeout=_BUNDLE_TIMEOUT_S,
            max_retries=0,
        )
    except Exception as exc:
        logger.warning("Bundle client rebuild failed (%s); shared client.", exc)
        client = llm
    return client, getattr(llm, "model_name", ASSUMPTION_MODEL_NAME)


def _structured_call(client, schema: type[BaseModel], system_prompt: str,
                     human_prompt: str, images: list | None = None) -> Optional[BaseModel]:
    """One structured json_mode call with raw-retry + salvage."""
    structured = client.with_structured_output(
        schema, method="json_mode", include_raw=True,
    ).bind(max_tokens=_BUNDLE_MAX_TOKENS, extra_body=_BUNDLE_EXTRA_BODY)

    if images:
        # Vision-capable override model: image_url blocks (OpenAI-compatible).
        content: list = [{"type": "text", "text": human_prompt}]
        for img in images:
            b64 = img.get("data_b64") or ""
            if not b64:
                continue
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.get('mime', 'image/png')};base64,{b64}"},
            })
        messages = [("system", system_prompt), ("human", content)]
    else:
        messages = [("system", system_prompt), ("human", human_prompt)]

    try:
        from src.research_ideas.complacency import qwen_throttle
        qwen_throttle.acquire(weight=1.0)
    except Exception:
        pass

    try:
        out = structured.invoke(messages)
        parsed = out.get("parsed") if isinstance(out, dict) else out
        if parsed is not None:
            return parsed
        raw = out.get("raw") if isinstance(out, dict) else None
        text = getattr(raw, "content", "") or ""
        logger.warning("Assumption extraction parse failed; salvaging raw text.")
        return _salvage(text, schema)
    except Exception as exc:
        try:
            from src.research_ideas.complacency import qwen_throttle
            qwen_throttle.report_429_from_exception(exc)
        except Exception:
            pass
        logger.warning("Assumption structured call failed (%s); raw retry.", exc)
        try:
            from src.research_ideas.complacency import qwen_throttle
            qwen_throttle.acquire(weight=1.0)
        except Exception:
            pass
        try:
            raw_llm = client.bind(
                max_tokens=_BUNDLE_MAX_TOKENS, extra_body=_BUNDLE_EXTRA_BODY)
            raw = raw_llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            return _salvage(text, schema)
        except Exception as exc2:
            logger.exception("Assumption raw retry failed: %s", exc2)
            return None


def _salvage(text: str, schema: type[BaseModel]) -> Optional[BaseModel]:
    """Best-effort JSON object salvage (Q1 pattern, lenient field drop)."""
    import json as _json
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = _json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return schema(**data)
    except Exception:
        # Drop unknown/invalid keys one layer at a time rather than losing all
        try:
            known = set(schema.model_fields.keys())
            cleaned = {k: v for k, v in data.items() if k in known}
            return schema(**cleaned)
        except Exception:
            return None


# ── Channel 1+2: company material → earnings assumptions ────────────────────

def _focus_block(ticker: str) -> str:
    """R3 monitor-spec focus fields as a prompt block (empty when the
    steward is disabled or the ticker has no spec/templates)."""
    try:
        from src.memory.assumption_steward import focus_fields, steward_enabled
        if not steward_enabled():
            return ""
        fields = focus_fields(ticker)
    except Exception:
        return ""
    if not fields:
        return ""
    return ("\n\nFOCUS FIELDS (monitor spec — prioritise these drivers "
            "when stated in the source):\n- "
            + "\n- ".join(fields[:18]))


def _industry_block(ticker: str) -> str:
    """Industry / sub-sector extraction targets as a prompt block.

    Resolves off the FMP industry string, so an HKEX property developer is
    asked about contracted sales and gearing while an SGX REIT is asked
    about rental reversion and aggregate leverage. Empty when the ticker
    cannot be classified — the base extraction still runs.
    """
    try:
        from src.memory.transcript_focus import industry_prompt_block
        return industry_prompt_block(ticker)
    except Exception:
        return ""


_COMPANY_SYSTEM = (
    "You extract management's forward-looking assumptions from a company's "
    "own earnings materials (press release + call transcript). Report ONLY "
    "what management said — no outside estimates, no inference beyond the "
    "text. Keep amounts as printed with units (e.g. 'RMB 240 billion', "
    "'$130-145 billion'). Respond in JSON format. Every collection field "
    "must be a JSON array, never an object."
)


def extract_company_assumptions(ticker: str,
                                press_release: dict | None,
                                transcript: dict | None) -> Optional[dict]:
    """One bundled pass over press release + transcript.

    Returns a dict shaped for assumption_store.upsert_earnings_assumptions
    (fiscal_year/fiscal_quarter may be None when not determinable), or None
    when nothing could be extracted.
    """
    press_text = (press_release or {}).get("text") or ""
    prepared = (transcript or {}).get("prepared_remarks") or ""
    qa = (transcript or {}).get("qa") or ""
    if not press_text and not prepared:
        return None

    parts = []
    if press_text:
        parts.append("=== EARNINGS PRESS RELEASE (SEC exhibit) ===\n"
                     + press_text[:_PRESS_MAX_CHARS])
    if prepared:
        parts.append("=== EARNINGS CALL — PREPARED REMARKS ===\n"
                     + prepared[:_PREPARED_MAX_CHARS])
    if qa:
        parts.append("=== EARNINGS CALL — Q&A ===\n" + qa[:_QA_MAX_CHARS])
    human = (
        f"Ticker: {ticker}\n"
        f"Sources: press release "
        f"({'yes' if press_text else 'no'}), transcript "
        f"({'yes' if prepared else 'no'}), Q&A ({'yes' if qa else 'no'}).\n\n"
        + "\n\n".join(parts) +
        "\n\nExtract into this exact JSON object shape (every collection "
        "MUST be a JSON array — empty [] when nothing applies — never an "
        "object):\n"
        "- fiscal_year: integer — of the REPORTED quarter (the quarter "
        "whose results are being announced; fiscal, not calendar, if "
        "stated)\n"
        "- fiscal_quarter: integer 1-4\n"
        "- fiscal_period_label: string, as printed\n"
        "- guidance: array of {metric, period, low, mid, high, unit, "
        "quote} — management's EXPLICIT forward guidance only "
        "(revenue/EBITDA/adj EBITA/EPS/capex, amounts as printed)\n"
        "- segments: array of {name, growth_rate_pct, outlook, "
        "constant_currency_note} — reported segment growth rates go in "
        "growth_rate_pct\n"
        "- margins: array of {metric, direction, driver, quote}\n"
        "- kpis: array of {name, value, growth_pct, quote} — sector "
        "metrics: cloud/AI revenue, ARR/NRR, GMV, DAU/ARPU, unit "
        "economics...\n"
        "- capital_allocation: array of {action, detail}\n"
        "- one_offs: array of {item, amount, impact} — impairments, "
        "restructuring, FX, notable non-recurring items\n"
        "- verbatim_quotes: array of up to 3 strings — the most "
        "decision-relevant management statements.\n"
        "- tone_shift: string — how management's confidence changed versus "
        "the prior call (e.g. firm full-year guidance last quarter, now "
        "hedged as subject to macro conditions). Empty string when the call "
        "gives no basis for a comparison. Do NOT infer it from the numbers.\n"
        "- qa_pressure: array of {topic, detail, quote} — topics analysts "
        "pressed on repeatedly or from several banks, and whether "
        "management answered directly or deflected. Q&A section only.\n"
        "- new_risks: array of {topic, detail, quote} — risks named here "
        "that were not raised before.\n"
        "- strategic_pivots: array of {topic, detail, quote} — changes in "
        "stated strategy, capital priorities or competitive positioning.\n"
        "- regulatory: array of {topic, detail, quote} — policy, regulatory "
        "or geopolitical commentary."
    ) + _focus_block(ticker) + _industry_block(ticker)

    client, model_name = _get_bundle_client()
    if client is None:
        return None
    out = _structured_call(client, EarningsAssumptionOutput,
                           _COMPANY_SYSTEM, human)
    if out is None:
        return None
    return {
        "fiscal_year": out.fiscal_year,
        "fiscal_quarter": out.fiscal_quarter,
        "fiscal_period_label": (out.fiscal_period_label or "")[:120],
        "guidance": [g.model_dump() for g in out.guidance[:12]],
        "segments": [s.model_dump() for s in out.segments[:10]],
        "margins": [m.model_dump() for m in out.margins[:10]],
        "kpis": [k.model_dump() for k in out.kpis[:15]],
        "capital_allocation": [c.model_dump() for c in out.capital_allocation[:8]],
        "one_offs": [o.model_dump() for o in out.one_offs[:8]],
        "quotes": [q[:400] for q in out.verbatim_quotes[:3]],
        "call_signals": {
            "tone_shift": (out.tone_shift or "")[:600],
            "qa_pressure": [c.model_dump() for c in out.qa_pressure[:8]],
            "new_risks": [c.model_dump() for c in out.new_risks[:8]],
            "strategic_pivots": [c.model_dump() for c in out.strategic_pivots[:6]],
            "regulatory": [c.model_dump() for c in out.regulatory[:6]],
        },
        "model_used": f"{model_name}(assumptions)",
        "sources": {
            "press_release": bool(press_text),
            "transcript": bool(prepared),
            "press_ref": (press_release or {}).get("exhibit_url"),
            "press_filed": (press_release or {}).get("filed"),
            "transcript_source": (transcript or {}).get("source"),
        },
    }


# ── Channel 3: analyst PDF → analyst_reports row ────────────────────────────

_ANALYST_SYSTEM = (
    "You extract the structured view from ONE sell-side research report. "
    "Report ONLY what the document says. Capture revisions WITH their "
    "stated prior values (new_value/prior_value/direction) — revision "
    "deltas are first-class. Keep all amounts as printed with units and "
    "currency. Respond in JSON format. Every collection field must be a "
    "JSON array, never an object."
)


def extract_analyst_report(ticker: str, extracted_pdf: dict,
                           doc_meta: dict | None = None) -> Optional[dict]:
    """One bundled pass over a deposited research PDF (text + optional
    raster-exhibit images when ASSUMPTION_VISION_MODEL is configured)."""
    text = (extracted_pdf or {}).get("text") or ""
    if len(text.strip()) < 200:
        return None
    images: list = []
    if os.environ.get("ASSUMPTION_VISION_MODEL"):
        images = (extracted_pdf or {}).get("table_images") or []

    human = (
        f"Ticker under coverage: {ticker}\n\n"
        f"=== RESEARCH REPORT TEXT ===\n{text[:_PDF_MAX_CHARS]}\n\n"
        f"IMPORTANT — this report may cover SEVERAL companies. Extract "
        f"ONLY what the document says about {ticker}: the rating, price "
        f"target, estimates, revisions and thesis must be the ones stated "
        f"for {ticker}, never another covered company's. If the document "
        f"states no {ticker}-specific price target / rating / estimate, "
        f"leave that field empty — do NOT copy another company's number.\n\n"
        "Extract into this exact JSON object shape (every collection MUST "
        "be a JSON array — empty [] when nothing applies — never an "
        "object):\n"
        "- house, analyst, report_date, rating: strings\n"
        "- price_target: string as printed (e.g. 'US$186 / HK$180'); "
        "price_target_currency: string\n"
        "- pt_methodology: string — SOTP/DCF/EV-EBITDA with any disclosed "
        "WACC / terminal growth / multiples\n"
        "- estimates: array of {fiscal_year_label, revenue, ebitda, eps} "
        "— FY+1 to FY+3 as printed\n"
        "- valuation_ratios: array of {fiscal_year_label, pe, pb, ev_ebitda, "
        "ev_sales, fcf_yield, dividend_yield, roe} — READ THE RATIOS TABLE. "
        "Almost every note has one and it is the densest source of stated "
        "multiples in the document. Goldman prints it as 'Ratios & "
        "Valuation'; Phillip and DBS print 'Valuation Ratios'. It is a grid "
        "with one COLUMN PER FISCAL YEAR (e.g. 3/26, 3/27E, 3/28E, 3/29E or "
        "FY22..FY26e) and one ROW PER METRIC ('P/E (X)', 'P/B (X)', "
        "'EV/EBITDA (X)', 'EV/sales (X)', 'FCF yield (%)', 'Dividend Yield', "
        "'ROE (%)'). Emit ONE OBJECT PER YEAR COLUMN, carrying that column's "
        "value for each metric row. Copy the numbers exactly as printed; do "
        "not convert, annualise or infer. Use the column header verbatim as "
        "fiscal_year_label. Leave a field empty when its row is absent, 'NM' "
        "or '—'. Bracketed numbers are negative: '(2.3)' is -2.3. Ignore "
        "rows that are not valuation metrics (inventory days, receivable "
        "days, interest cover, leverage, CROCI, turnover).\n"
        "- house_vs_consensus: array of {metric, house_view, street_view, "
        "comment} — every explicit 'above/below street/GSe' comparison\n"
        "- scenarios: array of {case, price_target, probability_pct, "
        "summary} — bull/base/bear\n"
        "- revisions: array of {field, new_value, prior_value, direction, "
        "reason} — every 'raised/cut/lifted from X to Y' WITH prior "
        f"values. ATTRIBUTE RULE: record a revision ONLY when the revised "
        f"line explicitly names {ticker} or its unmistakable sub-business; "
        f"if the revised figure belongs to another covered company, leave "
        f"revisions empty for it\n"
        "- thesis_points, catalysts, risks: arrays of strings."
    ) + _focus_block(ticker)
    client, model_name = _get_bundle_client()
    if client is None:
        return None
    out = _structured_call(client, AnalystReportOutput,
                           _ANALYST_SYSTEM, human, images=images)
    if out is None:
        return None
    meta = doc_meta or {}
    pt_num = parse_amount(out.price_target)
    return {
        "house": (out.house or "")[:80],
        "analyst": (out.analyst or "")[:120],
        "report_date": (out.report_date or "")[:16],
        "rating": (out.rating or "")[:40],
        "price_target": pt_num,
        "price_target_raw": (out.price_target or "")[:40],
        "price_target_currency": (out.price_target_currency or "")[:8],
        "pt_methodology": (out.pt_methodology or "")[:600],
        "estimates": [e.model_dump() for e in out.estimates[:6]],
        # Capped at 8: a ratios table runs 4-6 year columns, and a
        # longer list means the model has started emitting metric rows
        # as years.
        "valuation_ratios": [v.model_dump() for v in out.valuation_ratios[:8]],
        "house_vs_consensus": [h.model_dump() for h in out.house_vs_consensus[:10]],
        "scenarios": [s.model_dump() for s in out.scenarios[:5]],
        "revisions": [r.model_dump() for r in out.revisions[:15]],
        "thesis": {
            "points": [t[:300] for t in out.thesis_points[:8]],
            "catalysts": [c[:300] for c in out.catalysts[:8]],
            "risks": [r[:300] for r in out.risks[:8]],
        },
        "model_used": f"{model_name}(analyst)",
        "doc_path": meta.get("path"),
        "drive_file_id": meta.get("drive_file_id"),
        "source_url": meta.get("source_url"),
        "ai_input_allowed": bool(meta.get("ai_input_allowed", False)),
    }


# ── assumption_versions row builders (R1 writes them too) ───────────────────

def version_rows_from_company(ticker: str, extraction: dict) -> list[dict]:
    """Guidance + margin + KPI values become version rows."""
    rows: list[dict] = []
    fy = extraction.get("fiscal_year")
    fq = extraction.get("fiscal_quarter")
    for g in extraction.get("guidance") or []:
        metric = (g.get("metric") or "other").lower()
        period = g.get("period") or ""
        val = g.get("mid") or g.get("high") or g.get("low")
        if not val:
            continue
        rng = g.get("low") and g.get("high") and f"{g['low']}-{g['high']}"
        rows.append({
            "ticker": ticker, "source": "earnings",
            "fiscal_year": fy, "fiscal_quarter": fq,
            "field_key": f"guidance.{metric}.{period or 'unspecified'}".strip("."),
            "new_value": rng or str(val),
            "prior_value_stated": None,
            "direction": None,
            "doc_ref": (extraction.get("sources") or {}).get("press_ref"),
        })
    for k in extraction.get("kpis") or []:
        if not k.get("value"):
            continue
        rows.append({
            "ticker": ticker, "source": "earnings",
            "fiscal_year": fy, "fiscal_quarter": fq,
            "field_key": f"kpi.{(k.get('name') or 'unknown')[:60]}",
            "new_value": str(k["value"])[:120],
            "prior_value_stated": None,
            "direction": None,
            "doc_ref": (extraction.get("sources") or {}).get("press_ref"),
        })
    return rows


def version_rows_from_analyst(ticker: str, extraction: dict) -> list[dict]:
    """Rating/PT + every (new, prior) revision tuple becomes a version row."""
    rows: list[dict] = []
    house = extraction.get("house") or "unknown"
    doc_ref = extraction.get("doc_path") or extraction.get("drive_file_id")
    if extraction.get("rating"):
        rows.append({
            "ticker": ticker, "source": f"analyst:{house}",
            "field_key": "rating",
            "new_value": extraction["rating"], "prior_value_stated": None,
            "direction": None, "doc_ref": doc_ref,
        })
    if extraction.get("price_target_raw"):
        rows.append({
            "ticker": ticker, "source": f"analyst:{house}",
            "field_key": "price_target",
            "new_value": extraction["price_target_raw"],
            "prior_value_stated": None,
            "direction": None, "doc_ref": doc_ref,
        })
    for rev in extraction.get("revisions") or []:
        if not rev.get("field") or not (rev.get("new_value")
                                        or rev.get("prior_value")):
            continue
        rows.append({
            "ticker": ticker, "source": f"analyst:{house}",
            "field_key": f"revision.{rev['field'][:80]}",
            "new_value": (rev.get("new_value") or "")[:120],
            "prior_value_stated": (rev.get("prior_value") or "")[:120],
            "direction": rev.get("direction") or None,
            "doc_ref": doc_ref,
        })
    return rows


# ── Deterministic amount parser (consumers need floats) ─────────────────────

_AMOUNT_RE = re.compile(
    r"(?P<sign>-)?\s*(?:US\$|HK\$|RMB|¥|\$|€|£|S\$)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<mult>trillion|billion|million|bn|tn|mn|mm|b|m|k)?",
    re.IGNORECASE,
)
_MULT_FACTOR = {
    "trillion": 1e12, "tn": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
    "k": 1e3,
}


def parse_amount(text) -> Optional[float]:
    """'US$186' → 186.0; 'Rmb210bn' → 210e9; '$130-145bn' → midpoint 137.5e9.
    Returns None when nothing numeric is present."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    has_range = bool(re.search(r"\d\s*[-–—]\s*\d", s))
    # Range like "$130-145bn" → attach the trailing multiplier to the low
    # end AND switch the separator to a word so the amount regex does not
    # read the '-' as a negative sign on the high leg
    # ("$130bn to 145bn" → both legs positive, scaled identically).
    s = re.sub(
        r"(\d[\d,]*(?:\.\d+)?)\s*[-–—]\s*(\d[\d,]*(?:\.\d+)?)\s*"
        r"(trillion|billion|million|bn|tn|mn|mm|b|m|k)\b",
        r"\1\3 to \2\3", s, flags=re.IGNORECASE)
    matches = list(_AMOUNT_RE.finditer(s))
    if not matches:
        return None
    vals = []
    for m in matches:
        try:
            num = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        mult = (m.group("mult") or "").lower()
        num *= _MULT_FACTOR.get(mult, 1.0)
        if m.group("sign"):
            num = -num
        vals.append(num)
    if not vals:
        return None
    if len(vals) >= 2 and has_range:
        return round(sum(vals) / len(vals), 6)   # true range → midpoint
    return vals[0]                               # multiple amounts → first


# ── Deposit-time ingestion (CLI + Drive sync share this) ────────────────────

def extract_and_persist_analyst_pdf(path: str, tickers: list[str], *,
                                    ai_input_allowed: bool,
                                    drive_file_id: str | None = None,
                                    source_url: str | None = None) -> dict:
    """Full channel-3 ingestion for one deposited PDF.

    Extraction runs ONLY when ai_input_allowed — the compliance gate is
    checked again here even though callers gate too. Content-hash dedupe:
    a (ticker, content_hash) row already present → skipped.

    Returns {content_hash, tickers: {ticker: extracted|exists|failed}}.
    """
    import hashlib

    from src.memory import assumption_store
    from src.utils.research_pdf import extract_research_pdf

    summary: dict = {"content_hash": None, "tickers": {}}
    if not ai_input_allowed:
        summary["tickers"] = {t.upper(): "gated" for t in tickers}
        return summary
    with open(path, "rb") as fh:
        content_hash = hashlib.sha256(fh.read()).hexdigest()
    summary["content_hash"] = content_hash

    try:
        extracted_pdf = extract_research_pdf(path)
    except Exception as exc:
        logger.warning("analyst PDF unreadable (%s): %s", path, exc)
        summary["tickers"] = {t.upper(): "failed" for t in tickers}
        return summary

    # PDF parse is document-level (once); the LLM extraction is
    # TICKER-scoped ('Ticker under coverage: X') — one call per ticker,
    # never reused across tickers, or a multi-company doc writes the
    # first ticker's view onto every row (live 2026-08-24 bug: JD's PT
    # landed on the BABA/Meituan rows of the shared GS note).
    for ticker in tickers:
        tkr = ticker.upper()
        existing = assumption_store.get_analyst_report_by_hash(tkr, content_hash)
        if existing:
            summary["tickers"][tkr] = "exists"
            continue
        extraction = extract_analyst_report(tkr, extracted_pdf, {
            "path": path, "drive_file_id": drive_file_id,
            "source_url": source_url, "ai_input_allowed": True,
        })
        if extraction is None:
            summary["tickers"][tkr] = "failed"
            continue
        try:
            assumption_store.upsert_analyst_report(
                tkr, content_hash,
                house=extraction.get("house"),
                analyst=extraction.get("analyst"),
                report_date=extraction.get("report_date"),
                rating=extraction.get("rating"),
                price_target=extraction.get("price_target"),
                price_target_currency=extraction.get("price_target_currency"),
                pt_methodology=extraction.get("pt_methodology"),
                estimates=extraction.get("estimates"),
                valuation_ratios=extraction.get("valuation_ratios"),
                house_vs_consensus=extraction.get("house_vs_consensus"),
                scenarios=extraction.get("scenarios"),
                thesis=extraction.get("thesis"),
                revisions=extraction.get("revisions"),
                doc_path=path,
                drive_file_id=drive_file_id,
                source_url=source_url,
                ai_input_allowed=True,
                model_used=extraction.get("model_used"),
            )
            added = assumption_store.append_assumption_versions(
                version_rows_from_analyst(tkr, extraction))
            summary["tickers"][tkr] = f"extracted (+{added} versions)"
        except Exception as exc:
            logger.exception("analyst persistence failed for %s: %s", tkr, exc)
            summary["tickers"][tkr] = "failed"
    # R3 — steward inline pass over the tickers that actually got new
    # extractions this ingest (detectors -> challenges -> scorecard)
    extracted_tickers = [t for t, s in summary["tickers"].items()
                         if str(s).startswith("extracted")]
    if extracted_tickers:
        try:
            from src.memory.assumption_steward import run_steward_inline
            run_steward_inline(extracted_tickers, trigger="analyst_ingest")
        except Exception as exc:
            logger.warning("[steward] inline pass failed: %s", exc)
    return summary


# ── Channel 1+2 orchestrator (the pipeline-facing entry point) ──────────────

def _assumptions_enabled() -> bool:
    return os.environ.get("EARNINGS_ASSUMPTIONS", "true").strip().lower() \
        not in ("0", "false", "no", "off", "")


def latest_earnings_event(ticker: str) -> Optional[str]:
    """Date (YYYY-MM-DD) of the newest earnings event we can see, from
    whichever sources have coverage. None = no coverage on any channel."""
    from datetime import date, timedelta

    candidates: list[str] = []
    # FMP surprises — global coverage now includes HK and SG (verified
    # 2026-08-27), so this is no longer a US-only channel.
    try:
        from src.tools.api import get_earnings_surprises
        rows = get_earnings_surprises(
            ticker, (date.today() + timedelta(days=1)).isoformat(), limit=4)
        candidates.extend(r["date"] for r in rows if r.get("date"))
    except Exception:
        pass
    # FMP transcript dates (broadest coverage, incl. many HK names)
    try:
        from src.tools.fmp_transcripts import get_transcript_dates
        dates = get_transcript_dates(ticker)
        candidates.extend((d.get("date") or "")[:10] for d in dates[:4])
    except Exception:
        pass
    candidates = [c for c in candidates if c]
    return max(candidates) if candidates else None


def refresh_company_assumptions(ticker: str, force: bool = False) -> dict:
    """Channels 1+2 for one ticker: cache-first, then fetch + extract.

    Returns {ticker, status, quarter?, ...} — status is one of
    skipped_disabled | up_to_date | no_sources | extracted | failed.
    Never raises (soft-fail — pipeline runs call this inline).
    """
    from src.memory import assumption_store

    tkr = ticker.upper()
    if not _assumptions_enabled():
        return {"ticker": tkr, "status": "skipped_disabled"}
    try:
        stored = assumption_store.get_latest_earnings_assumptions(tkr)
        stored_as_of = (stored or {}).get("as_of") or ""

        latest_event = latest_earnings_event(tkr)
        if latest_event and stored_as_of and latest_event <= stored_as_of \
                and not force:
            return {"ticker": tkr, "status": "up_to_date",
                    "as_of": stored_as_of}

        # Channel 1 — EDGAR press release (FPI 6-K / domestic 8-K Item 2.02)
        press = None
        try:
            from src.tools.edgar_earnings import get_earnings_press_release
            press = get_earnings_press_release(tkr)
        except Exception as exc:
            logger.warning("press release fetch failed for %s: %s", tkr, exc)

        # Channel 2 — FMP transcript (full content, prepared/QA split)
        transcript = None
        try:
            from src.tools.fmp_transcripts import fetch_earnings_transcript
            transcript = fetch_earnings_transcript(tkr)
        except Exception as exc:
            logger.warning("transcript fetch failed for %s: %s", tkr, exc)

        if not press and not transcript:
            return {"ticker": tkr, "status": "no_sources"}

        extraction = extract_company_assumptions(tkr, press, transcript)
        if extraction is None:
            return {"ticker": tkr, "status": "failed",
                    "press": bool(press), "transcript": bool(transcript)}

        fy = extraction.get("fiscal_year")
        fq = extraction.get("fiscal_quarter")
        if fy is None or fq is None:
            # Deterministic fallback key: the transcript's calendar labels
            fy = fy or (transcript or {}).get("year") or 0
            fq = fq or (transcript or {}).get("quarter") or 0

        as_of = max([d for d in [
            (press or {}).get("filed"),
            (transcript or {}).get("date"),
        ] if d], default=None) or latest_event

        sources = extraction.get("sources") or {}
        source_bits = []
        if press:
            source_bits.append(press.get("source") or "edgar")
        if transcript:
            source_bits.append("fmp_transcript")
        assumption_store.upsert_earnings_assumptions(
            tkr, int(fy), int(fq),
            as_of=as_of,
            source="+".join(source_bits) or None,
            source_ref=sources.get("press_ref"),
            period_label=extraction.get("fiscal_period_label"),
            guidance=extraction.get("guidance"),
            segments=extraction.get("segments"),
            margins=extraction.get("margins"),
            kpis=extraction.get("kpis"),
            capital_allocation=extraction.get("capital_allocation"),
            one_offs=extraction.get("one_offs"),
            quotes=extraction.get("quotes"),
            model_used=extraction.get("model_used"),
        )
        # Qualitative call signals — separate table, narrative-only. Written
        # only when a transcript was actually available: a press release
        # carries no tone and no Q&A, so there is nothing to read from it.
        if transcript:
            try:
                signals = extraction.get("call_signals") or {}
                industry_label = ""
                try:
                    from src.memory.transcript_focus import targets_for_ticker
                    industry_label = targets_for_ticker(tkr)[0]
                except Exception:
                    pass
                assumption_store.upsert_transcript_signals(
                    tkr, int(fy), int(fq),
                    as_of=as_of,
                    tone_shift=signals.get("tone_shift"),
                    qa_pressure=signals.get("qa_pressure"),
                    new_risks=signals.get("new_risks"),
                    strategic_pivots=signals.get("strategic_pivots"),
                    regulatory=signals.get("regulatory"),
                    speakers=(transcript or {}).get("speakers"),
                    industry_label=industry_label,
                    model_used=extraction.get("model_used"),
                )
            except Exception as exc:
                logger.warning("[assumptions] transcript signals persist "
                               "failed for %s: %s", tkr, exc)

        added = assumption_store.append_assumption_versions(
            version_rows_from_company(tkr, extraction))
        logger.info("[assumptions] %s FY%sQ%s extracted (guidance %d, "
                    "kpis %d, +%d versions)", tkr, fy, fq,
                    len(extraction.get("guidance") or []),
                    len(extraction.get("kpis") or []), added)
        # R3 — steward inline pass (detectors -> challenges -> scorecard;
        # never raises into the pipeline)
        try:
            from src.memory.assumption_steward import run_steward_inline
            run_steward_inline([tkr], trigger="company_ingest")
        except Exception as exc:
            logger.warning("[steward] inline pass failed for %s: %s", tkr, exc)
        return {"ticker": tkr, "status": "extracted",
                "fiscal_year": fy, "fiscal_quarter": fq, "as_of": as_of,
                "versions_added": added}
    except Exception as exc:
        logger.exception("refresh_company_assumptions failed for %s: %s",
                         tkr, exc)
        return {"ticker": tkr, "status": "failed", "error": str(exc)}


# ── Consumption helper (deep-research / report grounding) ───────────────────

def build_assumption_context(ticker: str, max_chars: int = 4500) -> str:
    """Compact human-readable block of the CURRENT earnings assumptions +
    analyst views for one ticker — injected into deep-research context.
    Empty string when nothing is stored."""
    from src.memory import assumption_store

    parts: list[str] = []
    latest = None
    try:
        latest = assumption_store.get_latest_earnings_assumptions(ticker)
    except Exception:
        pass
    if latest:
        lines = [f"Company guidance (FY{latest['fiscal_year']} Q"
                 f"{latest['fiscal_quarter']}"
                 + (f", {latest['period_label']}" if latest.get("period_label") else "")
                 + f", as of {latest.get('as_of') or '?'}):"]
        for g in (latest.get("guidance") or [])[:8]:
            rng = g.get("low") and g.get("high") and f"{g['low']}–{g['high']}"
            val = rng or g.get("mid") or g.get("high") or g.get("low")
            if val:
                lines.append(f"  - {g.get('metric')} {g.get('period')}: {val} "
                             f"{g.get('unit') or ''}".rstrip())
        for s in (latest.get("segments") or [])[:6]:
            if s.get("growth_rate_pct") or s.get("outlook"):
                lines.append(f"  - segment {s.get('name')}: "
                             f"{s.get('growth_rate_pct') or ''} "
                             f"{s.get('outlook') or ''}".rstrip())
        for k in (latest.get("kpis") or [])[:8]:
            if k.get("value"):
                lines.append(f"  - {k.get('name')}: {k['value']}"
                             + (f" ({k['growth_pct']} yoy)" if k.get("growth_pct") else ""))
        for q in (latest.get("quotes") or [])[:2]:
            lines.append(f'  > "{q}"')
        if len(lines) > 1:
            parts.append("\n".join(lines))

    # Qualitative call signals. These are explicitly NOT valuation inputs —
    # they are what the write-up can say about how management sounded and
    # where analysts pushed, which no filing or press release records.
    try:
        sig = assumption_store.get_latest_transcript_signals(ticker)
    except Exception:
        sig = None
    if sig:
        sig_lines = [
            f"Earnings-call signals (FY{sig['fiscal_year']} Q"
            f"{sig['fiscal_quarter']}"
            + (f", {sig['industry_label']}" if sig.get("industry_label") else "")
            + ") — qualitative, not a valuation input:"
        ]
        if sig.get("tone_shift"):
            sig_lines.append(f"  - tone vs prior call: {sig['tone_shift']}")
        for label, key in (("analysts pressed on", "qa_pressure"),
                           ("new risk raised", "new_risks"),
                           ("strategic shift", "strategic_pivots"),
                           ("regulatory/policy", "regulatory")):
            for item in (sig.get(key) or [])[:3]:
                topic = item.get("topic") or ""
                detail = item.get("detail") or ""
                if topic or detail:
                    sig_lines.append(f"  - {label}: {topic}"
                                     + (f" — {detail}" if detail else ""))
        if len(sig_lines) > 1:
            parts.append("\n".join(sig_lines))

    reports: list[dict] = []
    try:
        reports = assumption_store.get_analyst_reports(ticker, limit=3)
    except Exception:
        pass
    rep_lines = []
    for r in reports:
        head = (f"{r.get('house') or 'Sell-side'} {r.get('report_date') or ''}: "
                f"{r.get('rating') or '?'}"
                + (f", PT {r.get('price_target') or ''}" if r.get("price_target") else ""))
        rep_lines.append(f"  - {head.strip()}")
        for rev in (r.get("revisions") or [])[:4]:
            rep_lines.append(f"      revision: {rev.get('field')}: "
                             f"{rev.get('prior_value') or '?'} → "
                             f"{rev.get('new_value') or '?'} "
                             f"({rev.get('direction') or '?'})")
    if rep_lines:
        parts.append("Licensed analyst reports on file:\n" + "\n".join(rep_lines))

    # R3 — Assumption Watch (open challenges, variant drivers, hit-rates).
    # Empty when the steward is disabled or nothing is flagged.
    try:
        from src.memory.assumption_steward import build_assumption_watch
        watch = build_assumption_watch(ticker, max_chars=1500)
        if watch:
            parts.append(watch)
    except Exception:
        pass

    text = "\n\n".join(parts)
    return text[:max_chars]

"""Gemini Flash valuation parameters -- cited inputs for the deterministic engine.

Qwen's deep research stays the qualitative assessment. The NUMBERS a valuation
consumes (segment revenue and margin, the multiple range and its basis,
associates, net cash, holdco discount) come from a separate Gemini call with
Google Search grounding, every figure carrying its source. The engine -- not
the model -- turns them into a value.

Amounts are taken exactly as the source prints them (value, currency, scale)
and converted to USD here. The first live gemini-3.8-flash run returned
Alibaba's RMB segment revenue labelled "USD bn" -- 7x too large -- and only
the reconciliation to FMP revenue caught it.

Transport is plain REST through `requests`. The google-genai SDK would force
anyio>=4.8 / httpx>=0.28, and the locked fastapi 0.104.1 pins anyio<4: adding
it broke Starlette's TestClient locally. The REST endpoint takes the same
tools / responseSchema / temperature.

Not wired into the pipeline: Part E evaluates it first
(scripts/eval_gemini_valuation.py). Every failure raises a typed error so the
caller can fall back to today's extractors.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
TIMEOUT_S = 90
#: Segments vs group forward revenue. Reportable segments exclude eliminations
#: and unallocated revenue: the validated BABA snapshot sums 12% under FMP's
#: FY2027 consensus, so a +/-3% match would reject correct inputs.
SEGMENT_SUM_TOLERANCE = 0.15
MARGIN_BOUNDS = (0.0, 0.60)
_SCALES = {"units": 1.0, "thousands": 1e3, "mn": 1e6, "bn": 1e9, "tn": 1e12}


class GeminiUnavailable(RuntimeError):
    """No key, or the service refused in a way retrying will not fix."""


class GeminiBillingError(GeminiUnavailable):
    """Prepaid credits exhausted / quota -- an account action, not a retry."""


def model_name() -> str:
    return os.getenv("GEMINI_PARAMS_MODEL") or DEFAULT_MODEL


# ── schemas ─────────────────────────────────────────────────────────────────

class Cited(BaseModel):
    """A monetary amount exactly as the source states it."""
    value: float = Field(description="The number exactly as printed in the source")
    currency: str = Field(description="ISO code of the source's currency, e.g. CNY, HKD, USD, SGD")
    scale: Literal["units", "thousands", "mn", "bn", "tn"] = Field(
        description="Scale the source prints the number in")
    period: str = Field(description="Fiscal period the number refers to, e.g. 'FY2027E'")
    source_url: str = Field(description="URL of the page the number was taken from")
    quote: str = Field(description="Short verbatim quote containing the number")


class CitedRatio(BaseModel):
    value: float = Field(description="Decimal ratio, e.g. 0.28 for 28%")
    period: str
    source_url: str
    quote: str


class SegmentEstimate(BaseModel):
    name: str
    revenue_fwd: Cited = Field(description="Next-fiscal-year segment revenue, in the source's currency and scale")
    ebit_margin: Optional[CitedRatio] = Field(default=None, description="Segment EBIT or EBITA margin")
    multiple_metric: Literal["pe", "ev_rev"]
    multiple_low: float
    multiple_high: float
    multiple_basis: str = Field(description="Why this range: peers, broker SOTP convention, growth; cite")
    multiple_source_url: str


class SotpInputs(BaseModel):
    fiscal_year: str
    segments: list[SegmentEstimate]
    associates_investments: Optional[Cited] = None
    net_cash: Optional[Cited] = Field(
        default=None, description="Cash + short-term investments - debt; negative if net debt")
    holdco_discount_pct: float = Field(description="Decimal, e.g. 0.15")
    holdco_basis: str


class DirectEstimate(BaseModel):
    """Evaluation arm G2 only -- never used by a live run."""
    sotp_value: float
    fair_value: float
    currency: str
    per: Literal["ADS", "share"]
    reasoning: str
    source_urls: list[str]


class SegmentYear(BaseModel):
    fiscal_year: str = Field(description="e.g. 'FY2025'")
    period_end: str = Field(description="ISO date the fiscal year ended, e.g. '2025-03-31'")
    revenue: Cited


class SegmentHistory(BaseModel):
    name: str
    years: list[SegmentYear]


class SegmentRevenueHistory(BaseModel):
    """Reported (not estimated) segment revenue, for the segment-revenue memory."""
    reporting_currency: str
    segments: list[SegmentHistory]
    total_revenue: list[SegmentYear] = Field(description="Group total revenue for the same years, for reconciliation")
    segment_definition_changes: str = Field(description="Any resegmentation during the period, else ''")


# ── REST ────────────────────────────────────────────────────────────────────

_SCHEMA_KEYS = {"type", "properties", "required", "items", "enum", "description",
                "nullable", "format"}


def to_gemini_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> the OpenAPI subset Gemini's responseSchema takes:
    $refs inlined, Optional[X] as nullable, titles and defaults dropped."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})

    def conv(node: dict) -> dict:
        if "$ref" in node:
            out = conv(defs[node["$ref"].split("/")[-1]])
            if node.get("description"):
                out["description"] = node["description"]
            return out
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            out = conv(options[0])
            if len(options) < len(node["anyOf"]):
                out["nullable"] = True
            if node.get("description"):
                out["description"] = node["description"]
            return out
        out = {k: v for k, v in node.items() if k in _SCHEMA_KEYS}
        if "const" in node:
            out["enum"] = [node["const"]]
        if isinstance(out.get("type"), str):
            out["type"] = out["type"].upper()
        if "properties" in out:
            out["properties"] = {k: conv(v) for k, v in out["properties"].items()}
        if "items" in out:
            out["items"] = conv(out["items"])
        if "enum" in out and "type" not in out:
            out["type"] = "STRING"
        return out

    return conv(raw)


#: Transient statuses retried with backoff. 503 "model is currently
#: experiencing high demand" hit 1 of 3 default-reasoning BABA calls on
#: 2026-09-15; a billing 429 is never retried (see _post).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RETRIES = 3
BACKOFF_S = (5.0, 15.0, 45.0)


def _post(model: str, body: dict, timeout: float, session=None) -> dict:
    key = os.getenv("GEMINI_API_KEY") or ""
    if not key:
        raise GeminiUnavailable("GEMINI_API_KEY is not set")
    http = session
    if http is None:
        import requests as http
    for attempt in range(RETRIES + 1):
        resp = http.post(_ENDPOINT.format(model=model), params={"key": key},
                         json=body, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code not in _RETRY_STATUS or attempt == RETRIES:
            break
        try:
            msg = str(((resp.json() or {}).get("error") or {}).get("message") or "")
        except ValueError:
            msg = ""
        if resp.status_code == 429 and ("credit" in msg.lower() or "billing" in msg.lower()):
            break
        time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        err = {}
    message = str(err.get("message") or resp.text or "")[:400]
    if resp.status_code == 429 and ("credit" in message.lower() or "billing" in message.lower()):
        raise GeminiBillingError(message)
    if resp.status_code in (401, 403):
        raise GeminiUnavailable(f"{resp.status_code}: {message}")
    raise RuntimeError(f"gemini {resp.status_code}: {message}")


def _unpack(payload: dict) -> dict:
    cands = payload.get("candidates") or []
    cand = cands[0] if cands else {}
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    gm = cand.get("groundingMetadata") or {}
    usage = payload.get("usageMetadata") or {}
    feedback = payload.get("promptFeedback") or {}
    return {
        "text": text,
        # A high-reasoning BABA call returned no text and no finish reason:
        # record whether there was a candidate at all and any block reason.
        "finish_reason": cand.get("finishReason") or (
            f"no_candidate:{feedback.get('blockReason')}" if not cands else None),
        "n_candidates": len(cands),
        "block_reason": feedback.get("blockReason"),
        "grounding_urls": [((c.get("web") or {}).get("uri")) for c in gm.get("groundingChunks") or []
                           if (c.get("web") or {}).get("uri")],
        "queries": list(gm.get("webSearchQueries") or []),
        # Our prompt is ~1.2k tokens; a grounded call bills ~27k input (search
        # results the tool injects) and ~32k it spends reasoning. Recording
        # them separately is what shows where the latency goes.
        "usage": {"in": usage.get("promptTokenCount"), "out": usage.get("candidatesTokenCount"),
                  "thoughts": usage.get("thoughtsTokenCount"),
                  "tool_prompt": usage.get("toolUsePromptTokenCount"),
                  "total": usage.get("totalTokenCount")},
    }


def _parse_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in response")
    return json.loads(t[start:t.rfind("}" if t[start] == "{" else "]") + 1])


class GeminiParseError(ValueError):
    """The call succeeded but returned no usable JSON; carries why."""

    def __init__(self, message: str, *, finish_reason=None, text_head: str = "", usage=None):
        super().__init__(f"{message} (finish_reason={finish_reason}, text={text_head!r})")
        self.finish_reason = finish_reason
        self.text_head = text_head
        self.usage = usage


def generate(prompt: str, *, schema: Optional[type[BaseModel]] = None, grounded: bool = True,
             model: Optional[str] = None, temperature: float = 0.1,
             thinking: Optional[dict] = None,
             timeout: float = TIMEOUT_S, session=None) -> dict:
    """One Gemini call. With both grounding and a schema it tries a single call;
    if the model refuses that combination it runs grounded text first, then a
    schema-only extraction over that text ("two_step").

    `thinking` is passed through as generationConfig.thinkingConfig, e.g.
    {"thinkingLevel": "low"} -- reasoning tokens dominate a grounded call."""
    model = model or model_name()
    started = time.monotonic()
    gen_cfg: dict = {"temperature": temperature}
    if thinking:
        gen_cfg["thinkingConfig"] = dict(thinking)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg}
    if grounded:
        body["tools"] = [{"google_search": {}}]
    if schema is not None:
        gen_cfg.update(responseMimeType="application/json",
                       responseSchema=to_gemini_schema(schema))
    mode = "single"
    try:
        out = _unpack(_post(model, body, timeout, session))
    except RuntimeError as exc:
        combined = grounded and schema is not None
        if not combined or isinstance(exc, GeminiUnavailable) or not str(exc).startswith("gemini 400"):
            raise
        mode = "two_step"
        text_body = {"contents": body["contents"], "tools": body["tools"],
                     "generationConfig": {k: v for k, v in gen_cfg.items()
                                          if k not in ("responseMimeType", "responseSchema")}}
        grounded_out = _unpack(_post(model, text_body, timeout, session))
        extract_body = {
            "contents": [{"role": "user", "parts": [{"text": (
                "Convert the research below into the JSON schema. Use only numbers and "
                "URLs that appear in it.\n\n" + grounded_out["text"])}]}],
            "generationConfig": {**gen_cfg},
        }
        out = _unpack(_post(model, extract_body, timeout, session))
        out["grounding_urls"] = grounded_out["grounding_urls"]
        out["queries"] = grounded_out["queries"]
    out["mode"] = mode
    out["model"] = model
    out["latency_s"] = round(time.monotonic() - started, 2)
    out["json"] = None
    if schema is not None:
        try:
            out["json"] = schema.model_validate(_parse_json(out["text"])).model_dump()
        except (ValueError, TypeError) as exc:
            raise GeminiParseError(f"{type(exc).__name__}: {str(exc)[:200]}",
                                   finish_reason=out.get("finish_reason"),
                                   text_head=(out.get("text") or "")[:300],
                                   usage=out.get("usage")) from exc
    return out


# ── prompts ─────────────────────────────────────────────────────────────────

_AMOUNT_RULE = (
    "Report every amount EXACTLY as the source prints it: the number, the source's "
    "currency (ISO code, e.g. CNY for RMB) and its scale (mn, bn...). Never convert "
    "currencies or rescale yourself. Every number needs the URL it came from and a "
    "short verbatim quote. Do not invent numbers; omit what you cannot source."
)


def sotp_prompt(company: str, ticker: str, anchors: dict) -> str:
    return (
        f"You are building sum-of-the-parts valuation INPUTS for {company} ({ticker}).\n"
        "1. Segment revenue and margin: take them from the COMPANY's own results "
        "announcements, annual reports or consensus estimates of REVENUE. Broker SOTP "
        "tables list segment VALUES (enterprise value, value per share/ADS) next to "
        "multiples -- never report a value from such a table as revenue. A segment's "
        "revenue is always smaller than the group's total revenue.\n"
        "2. Multiples: a RANGE per segment (P/E on segment NOPAT for profitable core "
        "businesses, EV/Sales otherwise) with the basis; broker SOTP notes are a good "
        "source for the multiple itself.\n"
        "3. Associates and strategic investments (total, not per share), net cash "
        "(cash + short-term investments - total debt; negative if net debt) and a "
        "holding-company discount.\n"
        f"Do NOT compute a per-share value.\n{_AMOUNT_RULE}\n"
        f"Fixed anchors from FMP (do not contradict): {json.dumps(anchors)}"
    )


def history_prompt(company: str, ticker: str, years: int = 5) -> str:
    return (
        f"From {company}'s ({ticker}) annual reports and results announcements, list the "
        f"REPORTED revenue of each reportable business segment for each of the last "
        f"{years} completed fiscal years, and group total revenue for the same years. "
        "Use the segment names the company used; if it resegmented, use the latest "
        "definition where the company restated prior years and describe the change. "
        f"Reported figures only, no estimates.\n{_AMOUNT_RULE}"
    )


def direct_prompt(company: str, ticker: str, per: str) -> str:
    return (
        f"Estimate a sum-of-the-parts equity value and a 12-month fair value per {per} for "
        f"{company} ({ticker}) from current public information. State the currency, give "
        "brief reasoning, and list the URLs you relied on."
    )


# ── guardrails and the engine bridge ────────────────────────────────────────

def _cited_ok(c: Optional[dict]) -> bool:
    return bool(c) and c.get("value") is not None \
        and str(c.get("source_url", "")).startswith("http") and bool(str(c.get("quote", "")).strip())


def _default_fx(currency: str) -> Optional[float]:
    ccy = (currency or "").upper()
    if ccy == "USD":
        return 1.0
    try:
        from src.tools.api import get_fx_rate
        rate = get_fx_rate(ccy, "USD")
        return float(rate) if rate and rate > 0 else None
    except Exception:  # noqa: BLE001
        return None


def amount(c: Optional[dict], to_ccy_rate: Callable[[str], Optional[float]]) -> Optional[float]:
    """Full-unit amount of a cited figure in the target currency, or None when
    uncited, of unknown scale, or its currency cannot be converted."""
    if not _cited_ok(c) or c.get("scale") not in _SCALES:
        return None
    rate = to_ccy_rate(c.get("currency") or "")
    if not rate:
        return None
    return float(c["value"]) * _SCALES[c["scale"]] * rate


def citation_coverage(sotp: dict) -> float:
    cited = [s.get("revenue_fwd") for s in sotp.get("segments") or []]
    cited += [s.get("ebit_margin") for s in sotp.get("segments") or [] if s.get("ebit_margin")]
    cited += [sotp.get(k) for k in ("associates_investments", "net_cash") if sotp.get(k)]
    return round(sum(_cited_ok(c) for c in cited) / len(cited), 4) if cited else 0.0


def to_engine_assumptions(sotp: dict, *, fmp_revenue_fwd_usd: Optional[float] = None,
                          fx_to_usd: Optional[Callable[[str], Optional[float]]] = None) -> tuple[dict, dict]:
    """Gemini SOTP inputs -> the USD assumptions dict `_sotp_analyst_style` consumes.

    Uncited or unconvertible revenue drops the segment; uncited margins,
    associates or net cash are left out; margins are clamped; the multiple is
    the midpoint of a sane range. When FMP's forward revenue is known, segments
    must sum to it within SEGMENT_SUM_TOLERANCE or the set is rejected."""
    fx = fx_to_usd or _default_fx
    checks: dict = {"dropped_segments": [], "dropped_fields": [], "clamped": [], "currencies": []}
    segments = []
    for s in sotp.get("segments") or []:
        rev_c = s.get("revenue_fwd")
        rev = amount(rev_c, fx)
        if not rev or rev <= 0:
            checks["dropped_segments"].append(s.get("name"))
            continue
        checks["currencies"].append(f"{(rev_c or {}).get('currency')} {(rev_c or {}).get('scale')}")
        lo, hi = s.get("multiple_low"), s.get("multiple_high")
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo <= hi):
            checks["dropped_segments"].append(s.get("name"))
            continue
        seg = {"name": s["name"], "revenue_fwd": rev,
               "pe_multiple": None, "ev_rev_multiple": None,
               "rationale": f"{s.get('multiple_metric')} {lo}-{hi}x: {s.get('multiple_basis', '')}"[:300],
               "source": "gemini_grounded"}
        seg["pe_multiple" if s.get("multiple_metric") == "pe" else "ev_rev_multiple"] = round((lo + hi) / 2, 3)
        margin = s.get("ebit_margin")
        if margin is not None:
            if _cited_ok(margin):
                m = float(margin["value"])
                if m > 1.0:                        # "28" meaning 28%
                    m = m / 100.0
                clamped = min(max(m, MARGIN_BOUNDS[0]), MARGIN_BOUNDS[1])
                if clamped != m:
                    checks["clamped"].append(f"{s['name']} margin {m}")
                seg["ebit_margin"] = clamped
            else:
                checks["dropped_fields"].append(f"{s['name']} margin")
        segments.append(seg)

    total = sum(seg["revenue_fwd"] for seg in segments)
    if fmp_revenue_fwd_usd and fmp_revenue_fwd_usd > 0 and segments:
        gap = (total - fmp_revenue_fwd_usd) / fmp_revenue_fwd_usd
        checks["segment_sum_gap"] = round(gap, 4)
        if abs(gap) > SEGMENT_SUM_TOLERANCE:
            checks["rejected"] = (f"segments sum to {total / 1e9:.1f}bn USD vs FMP forward revenue "
                                  f"{fmp_revenue_fwd_usd / 1e9:.1f}bn ({gap:+.1%})")
            segments = []

    assumptions: dict = {"segments": segments,
                         "holdco_discount_pct": min(max(float(sotp.get("holdco_discount_pct") or 0.0), 0.0), 0.5),
                         "default_tax_rate": 0.15,
                         "_origin": "gemini", "_sources": {"all": "gemini_grounded"}}
    for field in ("associates_investments", "net_cash"):
        c = sotp.get(field)
        if c is None:
            continue
        value = amount(c, fx)
        if value is None:
            checks["dropped_fields"].append(field)
        else:
            assumptions[field] = value
    checks["citation_coverage"] = citation_coverage(sotp)
    return assumptions, checks


def reconcile_history(hist: dict, fmp_revenue_by_year: dict[str, float], reporting_ccy: str,
                      fx_to: Callable[[str, str], Optional[float]]) -> dict:
    """Per fiscal year: cited segment sum and cited group total vs FMP reported
    revenue, all in the FMP reporting currency. Keys are period-end years."""
    def to_rep(c):
        return amount(c, lambda ccy: 1.0 if ccy.upper() == reporting_ccy.upper() else fx_to(ccy, reporting_ccy))

    by_year: dict[str, dict] = {}
    for seg in hist.get("segments") or []:
        for y in seg.get("years") or []:
            key = str(y.get("period_end", ""))[:4]
            v = to_rep(y.get("revenue"))
            slot = by_year.setdefault(key, {"segment_sum": 0.0, "segments": 0, "uncited": 0})
            if v is None:
                slot["uncited"] += 1
            else:
                slot["segment_sum"] += v
                slot["segments"] += 1
    for y in hist.get("total_revenue") or []:
        key = str(y.get("period_end", ""))[:4]
        by_year.setdefault(key, {"segment_sum": 0.0, "segments": 0, "uncited": 0})["cited_total"] = to_rep(y.get("revenue"))
    for key, slot in by_year.items():
        fmp = fmp_revenue_by_year.get(key)
        slot["fmp_revenue"] = fmp
        slot["segment_gap"] = round(slot["segment_sum"] / fmp - 1, 4) if fmp and slot["segment_sum"] else None
        ct = slot.get("cited_total")
        slot["total_gap"] = round(ct / fmp - 1, 4) if fmp and ct else None
    return dict(sorted(by_year.items()))

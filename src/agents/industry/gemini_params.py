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
MARGIN_BOUNDS = (-1.0, 0.60)   # a loss-making segment reaches the engine as a loss (owner, 2026-09-24)
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
    revenue_fwd: Cited = Field(description="NEXT fiscal year (FY+1) consensus or guided segment revenue, "
                                           "external of inter-segment sales, in the source's currency and scale; "
                                           "period must name the forward year, e.g. 'FY2026E'")
    ebit_margin: Optional[CitedRatio] = Field(default=None, description="FY+1 segment EBIT or EBITA margin "
                                                                        "(consensus or guided); negative if loss-making")
    multiple_metric: Literal["pe", "ev_rev"]
    multiple_low: float
    multiple_high: float
    multiple_basis: str = Field(description="Why this range: peers, broker SOTP convention, growth; cite")
    multiple_source_url: str
    ev_sales_low: Optional[float] = Field(default=None, description="For a P/E segment whose FY+1 earnings are "
                                                                     "negative or near zero: the cited EV/Sales range low")
    ev_sales_high: Optional[float] = Field(default=None, description="...and its high")
    ev_sales_source_url: Optional[str] = None


class SotpInputs(BaseModel):
    fiscal_year: str = Field(description="The FORWARD year every segment figure is stated for, e.g. 'FY2026E'")
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
    profit: Optional[Cited] = Field(
        default=None, description="The segment profit measure the company reports for this year "
                                  "(e.g. adjusted EBITA, operating profit); null if not disclosed")
    profit_measure: Optional[str] = Field(default=None, description="Name of that measure as the company labels it")


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
            payload = resp.json()
            # HTTP 200 with zero candidates and no block reason: JD and PDD in
            # the 2026-09-15 memory build, and one high-reasoning BABA call.
            # Nothing is wrong with the request, so it is retried like a 503;
            # after the budget the empty payload is returned and surfaces as
            # a GeminiParseError carrying its usage and model version.
            empty = not (payload.get("candidates") or [])
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            if empty and not blocked and attempt < RETRIES:
                time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                continue
            return payload
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
        "model_version": payload.get("modelVersion"),
        "response_id": payload.get("responseId"),
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
    combined = grounded and schema is not None
    try:
        out = _unpack(_post(model, body, timeout, session))
        # Swire Pacific: grounded + schema returned no candidate through every
        # retry (597s) while other names succeeded. Splitting search from
        # formatting is the one remaining lever, so an empty combined answer
        # takes the two-step path instead of failing.
        if combined and out.get("n_candidates") == 0 and not out.get("block_reason"):
            raise RuntimeError("gemini empty: no candidate after retries")
    except RuntimeError as exc:
        if not combined or isinstance(exc, GeminiUnavailable) or not (
                str(exc).startswith("gemini 400") or str(exc).startswith("gemini empty")):
            raise
        mode = "two_step" if str(exc).startswith("gemini 400") else "two_step_after_empty"
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
            raise GeminiParseError(f"{type(exc).__name__}: {str(exc)[:200]} "
                                   f"[candidates={out.get('n_candidates')}, usage={out.get('usage')}, "
                                   f"model_version={out.get('model_version')}]",
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
    # Owner, 2026-09-24 (pre-deployment item 3): FORWARD figures. The first
    # build cited FY2025 actuals on three of four names and a JD Retail SOTP on
    # trailing revenue and margin is a different valuation from the FY26E one
    # the brokers publish.
    fwd = anchors.get("revenue_next_fy_period") or "the next fiscal year"
    return (
        f"You are building sum-of-the-parts valuation INPUTS for {company} ({ticker}).\n"
        f"PERIOD RULE: every segment revenue and margin must be a FORWARD figure for the NEXT "
        f"fiscal year (FY+1, ending {fwd}) -- consensus estimates or management guidance -- and "
        "FY+2 where the source gives it. State the period on EVERY number as the forward year "
        "label (e.g. 'FY2026E'). Do not answer with the latest reported actuals; if no forward "
        "estimate exists for a segment, say so in the quote and give the actual with its own "
        "period label so the reviewer can see it is not forward.\n"
        "1. Segment revenue and margin: consensus segment estimates or the company's guidance, "
        "EXTERNAL of inter-segment sales (net of eliminations) where the company discloses "
        "that split. Broker SOTP tables list segment VALUES (enterprise value, value per "
        "share/ADS) next to multiples -- never report a value from such a table as revenue. "
        "Never cite the group's total revenue as a segment's revenue: the sum of segment "
        "revenues must not exceed consolidated group revenue.\n"
        "2. Multiples: a RANGE per segment (P/E on segment NOPAT for profitable core "
        "businesses, EV/Sales otherwise) with the basis; broker SOTP notes are a good "
        "source for the multiple itself. For a P/E segment whose FY+1 earnings are negative "
        "or near zero, ALSO give the EV/Sales range brokers use for it (ev_sales_low/high).\n"
        "3. Associates and strategic investments (total, not per share), net cash "
        "(cash + short-term investments - total debt; negative if net debt) at the latest "
        "balance-sheet date with that date as the period, and a holding-company discount.\n"
        f"Do NOT compute a per-share value.\n{_AMOUNT_RULE}\n"
        f"Fixed anchors from FMP (do not contradict): {json.dumps(anchors)}"
    )


def history_prompt(company: str, ticker: str, years: int = 5) -> str:
    return (
        f"From {company}'s ({ticker}) annual reports and results announcements, list for "
        f"each reportable business segment and each of the last {years} completed fiscal "
        "years: the REPORTED segment revenue, and the segment PROFIT measure the company "
        "itself reports for segments (e.g. adjusted EBITA, operating profit, segment "
        "result) with that measure's name. Leave profit null where the company does not "
        "disclose it. Also group total revenue for the same years. Use the segment names "
        "the company used; if it resegmented, use the latest definition where the "
        "company restated prior years and describe the change. List only reportable "
        "segments -- never a subtotal row that adds up other segments. In "
        "segment_definition_changes also state whether segment revenue includes the "
        "company's share of associates and joint ventures. Reported figures only, "
        f"no estimates, never figures from broker valuation tables.\n{_AMOUNT_RULE}"
    )


class DivisionEbitda(BaseModel):
    division: str = Field(description="Exactly one of the division names supplied")
    ebitda: Cited
    measure: str = Field(description="The company's own label, e.g. 'EBITDA' or "
                                     "'EBITDA including share of associates and joint ventures'")
    includes_share_of_associates: bool
    fiscal_year: str


class DivisionEbitdaSet(BaseModel):
    """Reported division EBITDA for a holdco's unlisted operating divisions --
    the input the look-through SOTP values on a peer EV/EBITDA range."""
    divisions: list[DivisionEbitda]
    notes: str


def division_ebitda_prompt(company: str, ticker: str, division_names: list[str]) -> str:
    return (
        f"From {company}'s ({ticker}) latest annual report or annual results announcement, "
        "give the EBITDA the company itself reports for each of these divisions for the latest "
        f"completed fiscal year: {'; '.join(division_names)}.\n"
        "Use the company's own division EBITDA figure. If it reports EBITDA including its share "
        "of associates and joint ventures, give that figure and set includes_share_of_associates. "
        "Use exactly the division names supplied. If a division's EBITDA is not disclosed, omit "
        f"that division -- never derive or estimate it.\n{_AMOUNT_RULE}"
    )


class SegmentMultiple(BaseModel):
    segment: str = Field(description="Exactly one of the segment names supplied")
    metric: Literal["pe", "ev_rev"]
    low: float
    high: float
    basis: str = Field(description="Peers or broker SOTP convention the range rests on")
    source_url: str


class MultipleRanges(BaseModel):
    """Gemini's part of the SOTP once revenue and margin come from the memory:
    the multiples, the balance sheet items and the holdco discount, cited."""
    multiples: list[SegmentMultiple]
    associates_investments: Optional[Cited] = Field(default=None, description="Total, not per share")
    net_cash: Optional[Cited] = Field(
        default=None, description="Cash + short-term investments - total debt; negative if net debt")
    holdco_discount_low: float
    holdco_discount_high: float
    holdco_basis: str


def multiples_prompt(company: str, ticker: str, segments: list[dict]) -> str:
    listing = "; ".join(f"{s['name']} (latest reported revenue share {s['share']:.0%}"
                        + (f", margin {s['margin']:.0%}" if s.get("margin") is not None else "") + ")"
                        for s in segments)
    return (
        f"Sum-of-the-parts for {company} ({ticker}). Segment revenue and margins are already "
        f"known from the company's filings: {listing}.\n"
        "For EACH of those segments give a valuation multiple RANGE with its basis and a "
        "source (P/E on segment NOPAT for profitable core businesses, EV/Sales otherwise); "
        "broker SOTP notes and listed peers are good sources. Also give associates and "
        "strategic investments (total), net cash (cash + short-term investments - total "
        "debt), and a holding-company discount range with its basis. Do NOT compute a "
        f"per-share value and do NOT restate segment revenue.\n{_AMOUNT_RULE}"
    )


def direct_prompt(company: str, ticker: str, per: str) -> str:
    return (
        f"Estimate a sum-of-the-parts equity value and a 12-month fair value per {per} for "
        f"{company} ({ticker}) from current public information. State the currency, give "
        "brief reasoning, and list the URLs you relied on."
    )


# ── guardrails and the engine bridge ────────────────────────────────────────

# ── industry inputs FMP does not carry (energy & A&D waves, review-gated) ────

class CitedQuantity(BaseModel):
    """A physical quantity exactly as the source states it."""
    value: float = Field(description="The number exactly as printed in the source")
    unit: str = Field(description="Unit as printed, e.g. MMboe, MMbbl, Bcf, Bcfe, Mt")
    period: str = Field(description="Date or fiscal year the quantity refers to")
    source_url: str
    quote: str


class ReserveValue(BaseModel):
    """Proved reserves and their discounted value (upstream oil & gas)."""
    measure: Literal["standardized_measure", "pv10"] = Field(
        description="standardized_measure = the after-tax standardized measure of discounted "
                    "future net cash flows; pv10 = the pre-tax PV-10 non-GAAP measure")
    value: Cited = Field(description="The discounted value of proved reserves, total for the company")
    proved_reserves: Optional[CitedQuantity] = Field(
        default=None, description="Total proved reserves (1P), oil-equivalent if the company reports it")
    price_basis: str = Field(description="The price deck the value uses, as the filing states it "
                                         "(e.g. 'SEC 12-month average: WTI $75.48/bbl')")


class BacklogValue(BaseModel):
    """Contracted backlog (oilfield services, drillers, EPC, defense)."""
    kind: Literal["total", "funded", "contract_drilling", "order_backlog", "rpo"] = Field(
        description="What the company calls it: total backlog, funded backlog, contract drilling "
                    "backlog, order backlog, or remaining performance obligations (RPO)")
    value: Cited = Field(description="Backlog at the latest reported period end, total for the company")
    book_to_bill: Optional[float] = Field(default=None, description="Latest reported book-to-bill ratio, if stated")
    orders: Optional[Cited] = Field(
        default=None, description="Total ORDERS booked in the latest COMPLETED fiscal year, as the company "
                                  "reports them, so a book-to-bill can be formed when none is stated")


class MaintenanceCapex(BaseModel):
    """Capital expenditure to sustain the existing asset base (midstream)."""
    value: Cited = Field(description="Maintenance (sustaining) capital expenditure for the latest fiscal year")
    definition: str = Field(description="How the company defines maintenance capex, as it states it")


class FcfGuidance(BaseModel):
    """Management's free-cash-flow guidance for the next fiscal year (an OVERLAY,
    never a baseline: owner rule 2026-09-20)."""
    value: Cited = Field(description="Guided free cash flow for the next fiscal year; the MIDPOINT when a range is given")
    revenue: Cited = Field(description="Guided revenue for the SAME fiscal year; the midpoint when a range is given")
    guidance_range: str = Field(description="The guidance exactly as management states it, both ranges, "
                                            "e.g. 'FCF $11.5-12.5bn on revenue of $45.5-46.5bn'")
    drivers: str = Field(description="What management says drives the cash flow, in its own words -- in "
                                     "particular customer advances, progress payments or reservation fees")


class RateBase(BaseModel):
    """Regulated rate base and the return the regulator allows on it (utilities)."""
    value: Cited = Field(description="Total regulated rate base at the latest reported period end, "
                                     "all jurisdictions combined, as the company states it")
    allowed_roe: Optional[CitedRatio] = Field(
        default=None, description="Authorised (allowed) return on equity, decimal; the rate-base-weighted "
                                  "average across jurisdictions if the company states one")
    equity_ratio: Optional[CitedRatio] = Field(
        default=None, description="Authorised equity share of the regulatory capital structure, decimal "
                                  "(e.g. 0.52 for a 52% equity layer)")
    jurisdiction: str = Field(description="The regulator(s) the figures are set by, as the filing names them "
                                          "(e.g. 'Florida PSC', 'Hong Kong Scheme of Control')")
    basis: str = Field(description="What the rate base covers and how the company measures it, as it states "
                                   "it (e.g. 'year-end regulatory capital employed', 'average net fixed assets "
                                   "under the Scheme of Control')")


INDUSTRY_INPUT_SCHEMAS: dict = {
    "pv10": ReserveValue,
    "backlog": BacklogValue,
    "maintenance_capex": MaintenanceCapex,
    "rate_base": RateBase,
    "fcf_guidance": FcfGuidance,
    "sotp": SotpInputs,
}

_INDUSTRY_ASK = {
    "pv10": (
        "the discounted value of its PROVED oil and gas reserves at the latest fiscal year end, "
        "taken from the SUPPLEMENTAL disclosures on oil and gas producing activities (unaudited) "
        "-- 'Standardized Measure of Discounted Future Net Cash Flows' under ASC 932 for US "
        "filers, or the equivalent supplementary disclosure a 20-F / annual report carries for a "
        "non-US filer. Report the TOTAL COMPANY figure exactly as the table prints it, with no "
        "adjustment, no escalation and no netting of anything the table does not net. Use the "
        "company's own PV-10 only when it publishes no standardized measure, and say which of the "
        "two the figure is. Include total proved reserves and the price basis the table states."),
    "backlog": (
        "its contracted BACKLOG at the latest reported period end, using the company's own term "
        "(total backlog, funded backlog, contract drilling backlog, order backlog, or remaining "
        "performance obligations) and its latest book-to-bill ratio if it states one. Also report "
        "the total ORDERS it booked in the latest completed fiscal year, as it reports them."),
    # `sotp` is asked through sotp_prompt(company, ticker, anchors), which needs
    # the FMP anchors; this entry keeps the kind tables complete.
    "sotp": "its business segments with a cited multiple range each (see sotp_prompt)",
    "fcf_guidance": (
        "management's most recent FREE CASH FLOW GUIDANCE for the NEXT fiscal year and its REVENUE "
        "guidance for the same year, exactly as stated (give the midpoint of each range and quote "
        "both ranges), and what management says drives that cash flow -- customer advances, progress "
        "payments, reservation fees. This is guidance and must be for a fiscal year that has NOT "
        "ended; if the company has issued no free-cash-flow guidance, omit the figure."),
    "maintenance_capex": (
        "its MAINTENANCE (sustaining) capital expenditure for the latest COMPLETED fiscal year, "
        "as the company reports it (often in its distributable cash flow reconciliation), and the "
        "company's definition of it. Growth or expansion capex must not be included, and guidance "
        "for a future year is not a reported figure -- report the actual spend of a year that has "
        "ended."),
    "rate_base": (
        "its total REGULATED RATE BASE at the latest reported period end (the asset base its "
        "regulators allow it to earn a return on -- 'rate base', 'regulatory capital employed', "
        "'regulated asset base', or under Hong Kong's Scheme of Control 'average net fixed "
        "assets'), all jurisdictions combined, using the company's own term and figure. Also "
        "report the AUTHORISED return on equity and the AUTHORISED equity share of the "
        "regulatory capital structure as the company or its rate orders state them, and name "
        "the regulator(s). A projected or targeted rate base for a future year is not a reported "
        "figure. Where the regime sets a permitted return on assets and no return on equity "
        "(the Scheme of Control), omit the return on equity rather than deriving one."),
}


_OVERLAY_ASK = {
    "sotp": "segment revenue",
    "fcf_guidance": "free cash flow",
    "rate_base": "regulated rate base",
    "maintenance_capex": "maintenance (sustaining) capital expenditure",
    "backlog": "contracted backlog",
    "pv10": "the discounted value of proved reserves",
}


def industry_overlay_prompt(kind: str, company: str, ticker: str, baseline: str) -> str:
    """Management's forward figure for a metric whose audited actual is known.

    Kept separate from the actual on purpose (owner, 2026-09-20): the baseline
    stays the audited historical, and this becomes a delta on it that applies
    only when the forward overlay is switched on.
    """
    return (f"{company} ({ticker}) reported {baseline} as its latest actual "
            f"{_OVERLAY_ASK[kind]}. Report the company's OWN GUIDANCE or plan for the NEXT "
            f"fiscal year for the same measure, as management states it (guidance range: give "
            f"the midpoint). State the fiscal year the guidance is for. If the company has "
            f"issued no such guidance, omit the figure rather than estimating one.\n" + _AMOUNT_RULE)


def industry_input_prompt(kind: str, company: str, ticker: str) -> str:
    """Prompt for one review-gated industry input, reported figures only."""
    return (f"From {company}'s ({ticker}) own filings (annual report, 10-K, 20-F, results "
            f"announcement or investor presentation), report {_INDUSTRY_ASK[kind]} Reported "
            "figures only -- no estimates, no broker figures.\n" + _AMOUNT_RULE)


# ── Grounding redirect wrappers (owner, 2026-09-24) ─────────────────────────
# Gemini grounding cites `vertexaisearch.cloud.google.com/grounding-api-redirect/…`,
# a wrapper that 302s to the page. A wrapper is not a source: a domain
# allowlist (EDGAR, HKEX, company IR, broker portals) would fail it, and the
# net-cash precedence rule must not key on one. Resolve to the terminal URL
# before recording; keep the wrapper beside it as provenance.
GROUNDING_REDIRECT_PREFIX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
_RESOLVED: dict[str, Optional[str]] = {}


def is_grounding_redirect(url: Optional[str]) -> bool:
    return isinstance(url, str) and url.startswith(GROUNDING_REDIRECT_PREFIX)


def is_canonical_source(url: Optional[str]) -> bool:
    """A direct http(s) URL that is not a grounding wrapper."""
    return isinstance(url, str) and url.startswith("http") and not is_grounding_redirect(url)


def resolve_source_url(url: str, timeout: float = 15.0) -> Optional[str]:
    """The terminal URL behind a grounding wrapper (one HEAD, redirects
    followed), or None when it does not resolve. Cached per process; a
    canonical URL resolves to itself without a request."""
    if not is_grounding_redirect(url):
        return url if is_canonical_source(url) else None
    if url in _RESOLVED:
        return _RESOLVED[url]
    final: Optional[str] = None
    try:
        import requests
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        cand = r.url if r.url and not is_grounding_redirect(r.url) else None
        if not cand and r.headers.get("location"):
            cand = r.headers["location"]
        if not cand:
            r = requests.get(url, allow_redirects=True, timeout=timeout, stream=True)
            cand = r.url if r.url and not is_grounding_redirect(r.url) else None
            r.close()
        final = cand if is_canonical_source(cand) else None
    except Exception:                                      # noqa: BLE001
        final = None
    _RESOLVED[url] = final
    return final


def canonicalize_citations(data, resolver=None) -> tuple:
    """Walk a Gemini answer and replace every grounding-wrapped `*source_url`
    with its terminal URL, keeping the wrapper under `grounding_url` beside
    it. Returns (copy, {wrapper: canonical_or_None}); the input is never
    mutated. A wrapper that does not resolve is left in place and reported."""
    resolver = resolver or resolve_source_url
    mapping: dict = {}

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and k.endswith("source_url") and is_grounding_redirect(v):
                    canon = mapping[v] if v in mapping else resolver(v)
                    mapping[v] = canon
                    if canon:
                        out[k] = canon
                        out[k.replace("source_url", "grounding_url")] = v
                    else:
                        out[k] = v
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(data), mapping


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
        # Owner, 2026-09-24 (item 1): the cited EV/Sales range a P/E segment
        # falls to when its earnings are non-positive. Never competes with the
        # P/E when earnings are positive -- the leg reads it only when no
        # earnings anchor can price.
        f_lo, f_hi = s.get("ev_sales_low"), s.get("ev_sales_high")
        if (s.get("multiple_metric") == "pe" and isinstance(f_lo, (int, float))
                and isinstance(f_hi, (int, float)) and 0 < f_lo <= f_hi):
            seg["ev_rev_fallback_multiple"] = round((f_lo + f_hi) / 2, 3)
        seg["period_label"] = (rev_c or {}).get("period")
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
            if field == "net_cash":
                # Item 2: the citation travels with the figure so the engine's
                # net-cash restatement can defer to it (see _refresh_sotp_net_cash).
                # Only a CANONICAL URL earns precedence; a grounding wrapper is
                # recorded as unresolved and the figure is restated as before.
                if is_canonical_source(c.get("source_url")):
                    assumptions["_net_cash_citation"] = {"source_url": c.get("source_url"),
                                                         "period": c.get("period"), "quote": c.get("quote")}
                else:
                    assumptions["_net_cash_citation_unresolved"] = c.get("source_url")
                    checks.setdefault("unresolved_citations", []).append("net_cash")
    checks["citation_coverage"] = citation_coverage(sotp)
    return assumptions, checks


def memory_to_engine(mix: dict, ranges: dict, *, fmp_revenue_fwd_usd: float,
                     fx_to_usd: Optional[Callable[[str], Optional[float]]] = None) -> tuple[dict, dict]:
    """SOTP assumptions where revenue and margin are ours and only multiples,
    balance-sheet items and the holdco discount come from Gemini.

    Forward segment revenue = FMP consensus group revenue x the latest REPORTED
    segment share (segment_memory.latest_mix); margin = latest reported segment
    profit / revenue. A segment Gemini gave no valid multiple for is dropped and
    listed, never valued on a guess."""
    from src.agents.analysis.sotp_multiple_basis import normalize_key

    fx = fx_to_usd or _default_fx
    checks: dict = {"dropped_segments": [], "dropped_fields": [], "clamped": [],
                    "mix_year": mix.get("year"), "revenue_basis": "fmp_consensus_x_reported_mix"}
    by_key = {normalize_key(m.get("segment", "")): m for m in ranges.get("multiples") or []}

    def match(name: str) -> Optional[dict]:
        k = normalize_key(name)
        if k in by_key:
            return by_key[k]
        return next((m for mk, m in by_key.items() if mk and (mk in k or k in mk)), None)

    segments = []
    for s in mix.get("segments") or []:
        m = match(s["name"])
        lo, hi = (m or {}).get("low"), (m or {}).get("high")
        if not m or not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo <= hi) \
                or not str(m.get("source_url", "")).startswith("http"):
            checks["dropped_segments"].append(s["name"])
            continue
        seg = {"name": s["name"], "revenue_fwd": fmp_revenue_fwd_usd * s["share"],
               "pe_multiple": None, "ev_rev_multiple": None,
               "rationale": f"{m['metric']} {lo}-{hi}x: {m.get('basis', '')}"[:300],
               "source": "memory_mix+gemini_multiple"}
        seg["pe_multiple" if m["metric"] == "pe" else "ev_rev_multiple"] = round((lo + hi) / 2, 3)
        if s.get("margin") is not None:
            clamped = min(max(float(s["margin"]), MARGIN_BOUNDS[0]), MARGIN_BOUNDS[1])
            if clamped != s["margin"]:
                checks["clamped"].append(f"{s['name']} margin {s['margin']:.3f}")
            seg["ebit_margin"] = clamped
        segments.append(seg)

    lo_h, hi_h = ranges.get("holdco_discount_low"), ranges.get("holdco_discount_high")
    holdco = ((float(lo_h) + float(hi_h)) / 2
              if isinstance(lo_h, (int, float)) and isinstance(hi_h, (int, float)) else 0.0)
    if holdco > 1.0:                                     # "15-25" meaning percent
        holdco /= 100.0
    assumptions: dict = {"segments": segments, "holdco_discount_pct": min(max(holdco, 0.0), 0.5),
                         "default_tax_rate": 0.15, "_origin": "memory+gemini",
                         "_sources": {"revenue": "fmp_consensus_x_reported_mix",
                                      "margin": "reported_segment_profit",
                                      "multiples": "gemini_grounded"}}
    for field in ("associates_investments", "net_cash"):
        c = ranges.get(field)
        if c is None:
            continue
        value = amount(c, fx)
        if value is None:
            checks["dropped_fields"].append(field)
        else:
            assumptions[field] = value
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

"""Per-segment forward growth, sourced by precedence.

The SOTP bridge needs a growth rate for every business segment. Four sources
exist and they are not equally good, so they are tried in order of how close
they sit to management's own forward view:

  1. EARNINGS TRANSCRIPT -- what management said about that segment on the most
     recent call. Freshest and most specific: META states "Family of Apps
     revenue was $60.4 billion, up 28% year-over-year" and "Reality Labs ...
     up 16%". Not every filer speaks in reportable-segment names -- NVDA
     discusses "hyperscale" and "networking" rather than Compute & Networking
     vs Graphics -- so this tier covers some names and not others.
  2. ANALYST BASIS -- a deposited sell-side report's segment growth, when one
     is attached for the ticker.
  3. FILING HISTORY -- reported year-over-year growth from the segment
     footnote. Always available where the filing parses, but backward-looking.
  4. GROUP CONSENSUS -- the market's forward group growth applied uniformly.
     The weakest tier: it is exactly the constant-mix assumption the
     per-segment work exists to avoid, and it is only a floor.

Determinism
-----------
Transcript extraction is an LLM call, and an earlier benchmark showed LLM
variance swinging the same ticker's target price by 40-75% run to run. A
transcript is immutable once published, so the extraction is CACHED ON DISK
keyed by (ticker, transcript date). Repeated runs reuse the same numbers and
the growth layer stops being a source of noise.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

_CACHE_DIR = Path(os.environ.get(
    "SEGMENT_GROWTH_CACHE_DIR",
    Path(__file__).resolve().parents[3] / ".cache" / "segment_growth"))

# Growth outside this band is a standing start, a disposal or a mis-parse
# rather than a rate to project. JD's New Businesses printed +157%.
_GROWTH_FLOOR, _GROWTH_CEIL = -0.30, 0.60

_SYSTEM = """You extract SEGMENT-LEVEL revenue growth from an earnings call.

You are given a company's REPORTABLE SEGMENT names and the prepared remarks
from its most recent earnings call. For each segment, report the forward or
most-recently-stated year-over-year revenue growth rate that management gave
FOR THAT SEGMENT.

Rules:
- Only report a segment if management actually spoke about it. Do NOT infer,
  average, or carry the group rate down to a segment.
- Prefer forward guidance over reported growth. If only reported growth is
  given, use it and say so.
- Management often uses business vernacular rather than the reportable-segment
  name ("hyperscale", "our networking business"). Map it ONLY when the
  reference is unambiguous; otherwise leave the segment out.
- growth is a decimal: 28% -> 0.28, a decline of 5% -> -0.05.
- evidence must quote the sentence you took it from, verbatim and short.
Return an empty list if the call says nothing segment-specific.

Respond in JSON format matching this schema exactly:
{"segments": [{"name": "<reportable segment name>", "growth": 0.28,
               "basis": "guidance" | "reported",
               "evidence": "<short verbatim quote>"}]}
"""


def _cache_path(ticker: str, date: str) -> Path:
    key = hashlib.sha1(f"{ticker}|{date}".encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{ticker.replace('/', '_')}_{key}.json"


def _load_cached(ticker: str, date: str) -> Optional[dict]:
    p = _cache_path(ticker, date)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return None


def _store_cached(ticker: str, date: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ticker, date).write_text(
            json.dumps(payload, indent=1), encoding="utf-8")
    except Exception:                              # noqa: BLE001
        pass


def _clamp(g: Optional[float]) -> Optional[float]:
    if g is None:
        return None
    return min(max(float(g), _GROWTH_FLOOR), _GROWTH_CEIL)


def transcript_segment_growth(ticker: str, segment_names: list[str],
                              state: Any = None) -> dict[str, dict]:
    """Segment growth as stated by management on the latest earnings call.

    Returns {segment_name: {growth, basis, evidence, source_date}}. Missing
    segments are simply absent -- the caller falls through to the next tier.
    """
    if not segment_names:
        return {}
    try:
        from src.tools.fmp_transcripts import (
            fetch_earnings_transcript, split_sections,
        )
        tr = fetch_earnings_transcript(ticker) or {}
    except Exception as exc:                       # noqa: BLE001
        print(f"  [segment-growth] {ticker}: transcript fetch failed "
              f"({type(exc).__name__})")
        return {}
    content = tr.get("content") or ""
    date = str(tr.get("date") or tr.get("fiscalYear") or "")
    if not content or not date:
        return {}

    cached = _load_cached(ticker, date)
    if cached is not None:
        return cached.get("segments") or {}

    prepared, _qa = split_sections(content)
    text = (prepared or content)[:24000]
    try:
        out = _extract_with_llm(ticker, segment_names, text, date, state)
    except Exception as exc:                       # noqa: BLE001
        print(f"  [segment-growth] {ticker}: extraction failed "
              f"({type(exc).__name__}: {exc})")
        return {}
    # Never cache an empty extraction. A failed call collapses to a synthetic
    # empty default, and caching that poisons every later run for the quarter
    # with a result the model never actually produced.
    if out:
        _store_cached(ticker, date, {"ticker": ticker, "transcript_date": date,
                                     "segments": out})
    return out


def _extract_with_llm(ticker: str, segment_names: list[str], text: str,
                      date: str, state: Any) -> dict[str, dict]:
    from pydantic import BaseModel, Field

    class _Seg(BaseModel):
        name: str = Field(description="the reportable segment name, verbatim")
        growth: float = Field(description="YoY revenue growth as a decimal")
        basis: str = Field(default="reported",
                           description="'guidance' or 'reported'")
        evidence: str = Field(default="", description="short verbatim quote")

    class _Out(BaseModel):
        segments: list[_Seg] = Field(default_factory=list)

    from src.utils.llm import call_llm_vision
    human = (f"Company: {ticker}\n"
             f"Reportable segments: {json.dumps(segment_names)}\n\n"
             f"=== Earnings call, prepared remarks ({date}) ===\n{text}")
    res = call_llm_vision(
        system_text=_SYSTEM, human_text=human, images=None,
        pydantic_model=_Out, agent_name="segment_growth",
        state=state, max_tokens=1500, temperature=0.0,
        default_factory=lambda: _Out(segments=[]),
    )
    out: dict[str, dict] = {}
    wanted = {re.sub(r"[^a-z0-9]", "", n.lower()): n for n in segment_names}
    for s in (getattr(res, "segments", None) or []):
        key = re.sub(r"[^a-z0-9]", "", (s.name or "").lower())
        match = wanted.get(key)
        if not match:
            # Accept a containment match, but never a fuzzy guess: a growth
            # rate pinned to the wrong segment is worse than none.
            cands = [orig for k, orig in wanted.items()
                     if key and (k in key or key in k)]
            if len(cands) != 1:
                continue
            match = cands[0]
        g = _clamp(s.growth)
        if g is None:
            continue
        out[match] = {"growth": g, "basis": (s.basis or "reported"),
                      "evidence": (s.evidence or "")[:240],
                      "source_date": date}
    return out


def resolve_segment_growth(ticker: str, segments: list[dict], *,
                           filing_growth: Optional[dict] = None,
                           group_growth: Optional[float] = None,
                           state: Any = None,
                           use_transcript: bool = True) -> dict[str, dict]:
    """Growth per segment, by precedence, with the winning source recorded.

    Every segment gets an entry so the bridge never has to guess, and each
    entry names its tier -- a run whose growth came from the group fallback is
    not the same evidence as one management gave on the call, and the report
    has to be able to say which it was.
    """
    names = [s.get("name") for s in segments if s.get("name")]
    resolved: dict[str, dict] = {}

    transcript = {}
    if use_transcript:
        transcript = transcript_segment_growth(ticker, names, state=state)

    analyst = _analyst_segment_growth(ticker, names)

    for name in names:
        if name in transcript:
            entry = dict(transcript[name])
            entry["source"] = "earnings_transcript"
            resolved[name] = entry
            continue
        if name in analyst:
            entry = dict(analyst[name])
            entry["source"] = "analyst_report"
            resolved[name] = entry
            continue
        fg = (filing_growth or {}).get(name)
        if fg is not None:
            resolved[name] = {"growth": _clamp(fg), "source": "filing_history",
                              "basis": "reported", "evidence": ""}
            continue
        if group_growth is not None:
            resolved[name] = {"growth": _clamp(group_growth),
                              "source": "group_consensus", "basis": "market",
                              "evidence": "group forward growth applied to a "
                                          "segment with no specific source"}
    return resolved


def _analyst_segment_growth(ticker: str, names: list[str]) -> dict[str, dict]:
    """Segment growth from a deposited analyst report, when one is attached."""
    try:
        from src.agents.analysis.sotp_extractor import _analyst_basis
        basis = _analyst_basis(ticker, {}) or {}
    except Exception:                              # noqa: BLE001
        return {}
    rows = basis.get("segment_growth") or {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict] = {}
    wanted = {re.sub(r"[^a-z0-9]", "", n.lower()): n for n in names}
    for raw, val in rows.items():
        key = re.sub(r"[^a-z0-9]", "", str(raw).lower())
        name = wanted.get(key)
        if name is None:
            continue
        g = _clamp(val)
        if g is not None:
            out[name] = {"growth": g, "basis": "analyst",
                         "evidence": f"{basis.get('house') or 'sell-side'} "
                                     f"{basis.get('as_of') or ''}".strip(),
                         "source_date": basis.get("as_of") or ""}
    return out

"""
src/research_ideas/complacency/web_research.py
===============================================
Dedicated web-research layer for the Complacency qualitative scorer.

Uses **Qwen's native web search** (DashScope `enable_search=True`) — no
Tavily dependency. Two public capabilities:

  • fetch_earnings_qa(ticker, n_quarters=2)
        Pulls the most recent earnings-call Q&A via Qwen web search and
        returns a structured digest: top analyst questions, management
        answers, topics flagged (competition, pricing, churn, AI threat,
        guidance pressure). Used by A1, A2, B2, D1 gatherers.

  • deep_research_indicator(ticker, name, sector, indicator_code, ...)
        Second-pass scorer. Fires when the first-pass `score_indicator()`
        returns confidence < 0.50. Asks Qwen to run targeted web searches
        for the specific gap, then re-scores with a higher-confidence
        finding. Doesn't replace the cached first-pass score — adds an
        ENRICHED layer the caller can use to override low-confidence
        zeros with substantive findings.

Both are cached in the dual-mode DB (`complacency_web_research` table via
src.data.db) keyed by (ticker, indicator/kind, scope) with configurable TTL.

Implementation notes (from src/agents/industry/deep_research.py):
  - DashScope's enable_search requires `stream=True` (non-streaming = 400).
  - Cannot combine with `enable_thinking` (errors).
  - search_options.search_strategy="agent" lets Qwen run multiple
    searches before synthesising.
  - Empty-content failures happen under concurrent load; retry once.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from src.data import db as _db

logger = logging.getLogger(__name__)


# ─── Qwen web-search client ───────────────────────────────────────────────


QWEN_RESEARCH_MODEL = os.environ.get(
    "COMPLACENCY_RESEARCH_MODEL",
    os.environ.get("DEEP_RESEARCH_MODEL", "qwen3.6-plus"),
)


# LIVE FINDING (DDOG, 2026-08-16): fetch_earnings_qa runs inside the
# shared-evidence gather. With the client-level timeout=180 ×
# max_retries=3, one stalled QA call can hide ~12 min inside the gather
# wall. Bound it: 150s per attempt, 1 retry → worst case ≈ 5 min, and
# the parallel gather (Q2.5) means that is the floor, not a stack.
_QA_REQUEST_TIMEOUT_S = 150.0


def _qwen_client():
    """OpenAI-compatible client pointed at DashScope's search endpoint.

    timeout=180s (down from 300s): web-search agent calls take 30-90s in
    practice; 180s gives 2x headroom but caps single-call hangs at 3 min
    instead of 5 min. Compounds with assess_qualitative's parallel pool
    where each hung call previously blocked one of 3 worker slots.
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — Qwen web search disabled.")
        return None
    api_key = os.environ.get("DEEP_RESEARCH_API_KEY")
    if not api_key:
        logger.warning("DEEP_RESEARCH_API_KEY missing — Qwen web search disabled.")
        return None
    base_url = os.environ.get(
        "DEEP_RESEARCH_SEARCH_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )
    # max_retries=3 (vs openai-python default 2): DashScope hits clients
    # with HTTP 429 "limit_burst_rate" when 3-4 Qwen calls fire in parallel
    # from the qualitative scorer's worker pool + concurrent deep-research
    # escalations. The openai-python retry layer uses exponential backoff
    # (~1s, 2s, 4s, ~7s total) which clears DashScope's burst window —
    # without this, force-rescore cascading-failed at ~7-9/10 indicators.
    return OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=3)


def qwen_web_search(
    user_prompt: str,
    system_prompt: str = "You are a forensic equity research analyst.",
    max_chars: int = 12000,
    retries: int = 1,
    request_timeout_s: float | None = None,
) -> Optional[str]:
    """
    Single-shot Qwen call with native web search enabled. Streams to get
    around the DashScope non-streaming 400, then returns the final text
    (after discarding the reasoning_content phase). None on failure.

    request_timeout_s: latency-sensitive callers (evidence gather) bound
    the per-call wall here — the shared client's timeout=180/max_retries=3
    can otherwise hide ~12 min inside one call (same hidden-retry
    pathology as the Q1 bundle client).
    """
    client = _qwen_client()
    if client is None:
        return None
    if request_timeout_s is not None:
        try:
            client = client.with_options(
                timeout=request_timeout_s, max_retries=1)
        except Exception:  # very old openai SDK — keep client defaults
            pass

    last_exc: Exception | None = None
    from src.research_ideas.complacency import qwen_throttle
    for attempt in range(retries + 1):
        # Process-wide throttle: blocks if 429-cooldown active or
        # bucket empty. weight=2 because web-search calls are heavier
        # than plain chat completions (longer + more API surface).
        qwen_throttle.acquire(weight=2.0)
        try:
            stream = client.chat.completions.create(
                model=QWEN_RESEARCH_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                extra_body={
                    "enable_search": True,
                    "search_options": {"search_strategy": "agent"},
                },
                stream=True,
            )

            text = ""
            reasoning = ""
            for chunk in stream:
                delta = getattr(chunk.choices[0] if chunk.choices else None, "delta", None)
                if not delta:
                    continue
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning += rc
                c = getattr(delta, "content", None)
                if c:
                    text += c

            text = text.strip()

            # Empty-content recovery (per deep_research.py comment): the
            # streaming layer occasionally drops the content phase but keeps
            # reasoning. Send the reasoning back as assistant turn + ask for
            # final answer without search.
            if not text and reasoning and attempt < retries:
                logger.warning(
                    "Qwen web-search returned empty content (reasoning %d chars) — "
                    "retrying with synthesis nudge.", len(reasoning)
                )
                retry = client.chat.completions.create(
                    model=QWEN_RESEARCH_MODEL,
                    messages=[
                        {"role": "system",    "content": system_prompt},
                        {"role": "user",      "content": user_prompt},
                        {"role": "assistant", "content": reasoning[-4000:]},
                        {"role": "user",      "content": (
                            "Your reasoning above is captured. Please now write "
                            "the final answer using ONLY that reasoning. Output "
                            "only the answer, no meta-commentary."
                        )},
                    ],
                    extra_body={"enable_search": False},
                    stream=False,
                    temperature=0.3,
                    max_tokens=4096,
                )
                text = (retry.choices[0].message.content or "").strip()

            if text:
                return text[:max_chars]
            # else fall through to next attempt

        except Exception as exc:
            last_exc = exc
            # If this was a 429, notify the throttle so siblings back off.
            qwen_throttle.report_429_from_exception(exc)
            logger.warning("Qwen web-search attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.5)

    if last_exc:
        logger.error("Qwen web-search permanently failed: %s", last_exc)
    return None


# ─── Cache (dual-mode via src.data.db, same database as the cohort) ──────
#
# S1 batch, 2026-08-16: was raw sqlite3 against RUN_ARCHIVE_PATH — a
# per-process private file on Railway, so a web-research result cached by
# the worker (where the complacency refresh runs in queue mode) was
# invisible to the web replicas and vice versa. complacency_web_research
# was already copied to PG by the 2026-08 migration.

_DDL = """
CREATE TABLE IF NOT EXISTS complacency_web_research (
    ticker     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    scope      TEXT NOT NULL,
    cached_at  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (ticker, kind, scope)
)
"""
# kind: 'earnings_qa' or 'deep_indicator'; scope: '' for earnings_qa,
# indicator_code for deep. payload is JSON.


# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_table() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(_DDL)
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("complacency web_research _ensure_table: %s", exc)


def _cache_get(ticker: str, kind: str, scope: str, max_age_days: int) -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        "SELECT cached_at, payload FROM complacency_web_research "
        "WHERE ticker = ? AND kind = ? AND scope = ?",
        [ticker.upper(), kind, scope],
    )
    if not row:
        return None
    try:
        cached_at = datetime.fromisoformat(row["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - cached_at).total_seconds() / 86400
        if age_days > max_age_days:
            return None
        payload = json.loads(row["payload"])
        payload["_cached_age_days"] = round(age_days, 2)
        return payload
    except Exception as exc:
        logger.warning("Cache read failed for %s/%s/%s: %s", ticker, kind, scope, exc)
        return None


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_CACHE_PUT_SQL = """
INSERT INTO complacency_web_research
    (ticker, kind, scope, cached_at, payload)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(ticker, kind, scope) DO UPDATE SET
    cached_at = excluded.cached_at,
    payload = excluded.payload
"""


def _cache_put(ticker: str, kind: str, scope: str, payload: dict) -> None:
    _ensure_table()
    _db.execute(
        _CACHE_PUT_SQL,
        [
            ticker.upper(), kind, scope,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(payload),
        ],
    )


# ─── Priority 2: Earnings call Q&A digest ─────────────────────────────────


EARNINGS_QA_CACHE_TTL_DAYS = 30   # earnings calls happen quarterly

# In-flight dedup: the four indicator gatherers that want the earnings Q&A
# (A1, A2, B2, D1) run on parallel threads and ALL call fetch_earnings_qa
# before any of them has written the SQLite cache — without a per-ticker
# lock they fire 4 identical 30-90 s Qwen web searches for the same
# transcript on a cold cache. The lock coalesces them: the first thread
# runs the search, the rest block briefly then read the cache it wrote.
# Negative results ("no transcript found") aren't persisted to SQLite
# (they retry in 3 days by design), so they're memoized in-process for
# 10 min to keep waiters from serially re-running the dead search.
_QA_TICKER_LOCKS: dict[str, threading.Lock] = {}
_QA_LOCKS_GUARD = threading.Lock()
_QA_NEG_MEMO: dict[str, tuple[float, dict]] = {}   # ticker -> (monotonic_ts, payload)
_QA_NEG_MEMO_TTL_S = 600.0


def _qa_ticker_lock(ticker: str) -> threading.Lock:
    key = ticker.upper()
    with _QA_LOCKS_GUARD:
        lk = _QA_TICKER_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _QA_TICKER_LOCKS[key] = lk
        return lk


def fetch_earnings_qa(
    ticker: str,
    n_quarters: int = 2,
    force_refresh: bool = False,
) -> Optional[dict]:
    """
    Fetches the most-recent earnings call Q&A via Qwen native web search.

    Qwen runs multiple searches (transcript aggregators like Motley Fool,
    Seeking Alpha free articles, company IR pages, Reuters/Bloomberg
    coverage) and synthesises:
      - top 5 analyst questions verbatim
      - management's answers verbatim where possible
      - explicit topic flags (competition pressure, pricing, churn, AI
        threat, guidance changes, restatements)

    Returns {
      summary: str,            # 200-300 char headline
      digest: str,             # 2-4k char structured Q&A digest
      topics_flagged: list[str],
      source_hint: str,        # which transcript/source the LLM cited
      fetched_at: str,
      _cached_age_days: float | absent
    } or None on failure.

    Cached 30 days per ticker. Concurrent callers for the same ticker
    coalesce onto a single web search (see _qa_ticker_lock).
    """
    if not force_refresh:
        cached = _cache_get(ticker, "earnings_qa", "", EARNINGS_QA_CACHE_TTL_DAYS)
        if cached:
            return cached
        neg = _QA_NEG_MEMO.get(ticker.upper())
        if neg and (time.monotonic() - neg[0]) <= _QA_NEG_MEMO_TTL_S:
            return neg[1]

    # Serialize same-ticker callers: the winner runs the search, waiters
    # re-check the cache (or negative memo) the winner just wrote.
    with _qa_ticker_lock(ticker):
        if not force_refresh:
            cached = _cache_get(ticker, "earnings_qa", "", EARNINGS_QA_CACHE_TTL_DAYS)
            if cached:
                return cached
            neg = _QA_NEG_MEMO.get(ticker.upper())
            if neg and (time.monotonic() - neg[0]) <= _QA_NEG_MEMO_TTL_S:
                return neg[1]

        system = (
            "You are a forensic short-seller research analyst. You have access "
            "to web search via a tool that returns Google-quality results. Use "
            "it to find earnings call transcripts and extract the most "
            "consequential analyst Q&A exchanges."
        )

        user = (
            f"TASK: For {ticker}, find the {n_quarters} most-recent earnings "
            f"conference call transcripts (try Motley Fool fool.com, Seeking "
            f"Alpha seekingalpha.com free articles, the company's investor "
            f"relations page, MarketBeat, Reuters, or Bloomberg coverage). "
            f"Search the web — DO NOT make up content.\n\n"

            f"FROM THE Q&A SECTION (not management prepared remarks), extract "
            f"the 5 most consequential analyst questions and management's "
            f"answers VERBATIM. Prioritise questions about: competitive "
            f"pressure, pricing power / NRR / discounting, customer churn, "
            f"AI threat (hyperscalers, open-source), guidance changes, "
            f"goodwill / restructuring, regulatory inquiries, or executive "
            f"departures.\n\n"

            f"OUTPUT (STRICT JSON, no other text):\n"
            f"{{\n"
            f'  "summary": "<200-300 char one-paragraph headline of the Q&A>",\n'
            f'  "digest": "<2000-4000 char structured Q&A: each entry as '
            f'\\"Q (analyst, firm): ...\\\\n A (exec): ...\\\\n\\\\n\\">",\n'
            f'  "topics_flagged": ["<topic1>", "<topic2>", ...],  '
            f'  // from: competition, pricing, churn, ai_threat, guidance, '
            f'restatement, regulatory, exec_departure\n'
            f'  "source_hint": "<which transcript URL or publisher you used>"\n'
            f"}}\n\n"
            f"If you cannot find a real transcript (don't fabricate), return:\n"
            f'  {{"summary": "no transcript found", "digest": "", '
            f'"topics_flagged": [], "source_hint": ""}}\n'
        )

        raw = qwen_web_search(user, system_prompt=system, max_chars=8000,
                              request_timeout_s=_QA_REQUEST_TIMEOUT_S)
        if not raw:
            return None

        # Extract JSON from the response
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                logger.warning("fetch_earnings_qa(%s): no JSON in response", ticker)
                return None
            parsed = json.loads(raw[start : end + 1])
        except Exception as exc:
            logger.warning("fetch_earnings_qa(%s) JSON parse failed: %s", ticker, exc)
            return None

        if not parsed.get("digest"):
            # LLM said no transcript found — cache the negative result briefly
            # (3 days) so we don't keep retrying, but not the full 30
            parsed_neg = {
                "summary": parsed.get("summary", "no transcript found"),
                "digest": "",
                "topics_flagged": [],
                "source_hint": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            # In-process memo so same-run waiters don't re-run the dead
            # search; SQLite persistence deliberately skipped (3-day retry).
            _QA_NEG_MEMO[ticker.upper()] = (time.monotonic(), parsed_neg)
            return parsed_neg

        parsed["fetched_at"] = datetime.now(timezone.utc).isoformat()
        _cache_put(ticker, "earnings_qa", "", parsed)
        return parsed


def format_earnings_qa_for_prompt(qa: dict) -> str:
    if not qa or not qa.get("digest"):
        return ""
    lines = [
        f"EARNINGS CALL Q&A DIGEST (via Qwen web search):",
        f"  source hint: {qa.get('source_hint','?')}",
        f"  topics flagged: {', '.join(qa.get('topics_flagged') or []) or '(none)'}",
        f"  summary: {qa.get('summary','')}",
        "",
        "  ───── Q&A excerpts ─────",
        qa.get("digest", "")[:4000],
    ]
    return "\n".join(lines)


# ─── Priority 3: Deep research for low-confidence indicators ──────────────


DEEP_INDICATOR_CACHE_TTL_DAYS = 7


def deep_research_indicator(
    ticker: str,
    name: str,
    sector: str | None,
    indicator_code: str,
    indicator_label: str,
    rubric: str,
    initial_score: int,
    initial_confidence: float,
    initial_summary: str,
    initial_evidence: list[dict],
    force_refresh: bool = False,
) -> Optional[dict]:
    """
    Second-pass researcher for low-confidence indicators (typically < 0.50
    from the first-pass scorer). Asks Qwen — with native web search
    enabled — to identify the gap, run targeted searches, and produce a
    HIGHER-CONFIDENCE finding.

    Critical safety: does NOT blindly override the first-pass score. The
    caller decides whether to adopt the deep finding. The deep result is
    structured so the caller can compare confidences and pick the better.

    Returns {
      score: int 0-5,
      confidence: float 0-1,
      summary: str,
      evidence: [{source, quote, url, date}],
      search_queries_used: list[str],
      reasoning: str (≤ 500 chars why this differs from initial),
      fetched_at: str,
      _cached_age_days: float | absent
    } or None on failure.

    Cached 7 days per (ticker, indicator).
    """
    if not force_refresh:
        cached = _cache_get(
            ticker, "deep_indicator", indicator_code, DEEP_INDICATOR_CACHE_TTL_DAYS,
        )
        if cached:
            return cached

    system = (
        "You are a forensic short-seller doing DEEP RESEARCH to improve a "
        "low-confidence first-pass score. You have web search. Use it "
        "aggressively — the first pass had thin evidence, your job is to "
        "find what it missed."
    )

    # Render initial evidence compactly
    initial_ev_text = "\n".join(
        f"  - [{e.get('source','?')[:50]}]: {e.get('quote','')[:200]}"
        for e in (initial_evidence or [])[:5]
    ) or "  (no evidence provided)"

    user = (
        f"TICKER: {ticker}  ({name})\n"
        f"SECTOR: {sector or 'unknown'}\n"
        f"INDICATOR: {indicator_code} — {indicator_label}\n\n"

        f"RUBRIC (0-5 scale):\n{rubric.strip()}\n\n"

        f"FIRST-PASS RESULT (low confidence — your job is to corroborate "
        f"OR correct it):\n"
        f"  score      : {initial_score}/5\n"
        f"  confidence : {initial_confidence:.0%}  ← thin evidence\n"
        f"  summary    : {initial_summary}\n"
        f"  evidence   :\n{initial_ev_text}\n\n"

        f"YOUR TASK:\n"
        f"  1. Identify what the first pass MISSED for {indicator_code}.\n"
        f"  2. Run 2-4 focused web searches (the tool runs them for you). "
        f"     Examples for D1 management: '{ticker} CEO departure 2025', "
        f"     '{ticker} executive scandal investigation', "
        f"     '{ticker} board changes'. "
        f"     Examples for B1 concentration: "
        f"     '{ticker} top customers revenue concentration', "
        f"     '{ticker} {sector} customer dependency'. "
        f"     Examples for B2 competition: "
        f"     '{ticker} vs competitors market share 2025', "
        f"     '{ticker} pricing pressure earnings call'.\n"
        f"  3. Cite Tier-1 sources (SEC filings, WSJ, Bloomberg, Reuters, "
        f"     FT) where possible; Tier-2 (CNBC, MarketWatch) acceptable; "
        f"     Tier-3 (random blogs) only if no better. NO fabrication.\n"
        f"  4. Score per the rubric. Adjust your confidence based on "
        f"     evidence quality.\n\n"

        f"OUTPUT (STRICT JSON, nothing else):\n"
        f"{{\n"
        f'  "score": <0-5 integer>,\n'
        f'  "confidence": <0-1 float>,\n'
        f'  "summary": "<≤ 200 char takeaway>",\n'
        f'  "evidence": [\n'
        f'    {{"source": "<publisher>", "quote": "<≤ 300 chars verbatim>", '
        f'"url": "<url if known>", "date": "<yyyy-mm-dd or null>"}}\n'
        f'  ],\n'
        f'  "search_queries_used": ["<query1>", "<query2>", ...],\n'
        f'  "reasoning": "<≤ 500 chars: why your score differs from / '
        f'corroborates first-pass>"\n'
        f"}}"
    )

    raw = qwen_web_search(user, system_prompt=system, max_chars=10000)
    if not raw:
        return None

    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            logger.warning(
                "deep_research_indicator(%s/%s): no JSON in response",
                ticker, indicator_code,
            )
            return None
        parsed = json.loads(raw[start : end + 1])
    except Exception as exc:
        logger.warning(
            "deep_research_indicator(%s/%s) JSON parse failed: %s",
            ticker, indicator_code, exc,
        )
        return None

    # Validate shape
    if "score" not in parsed or "confidence" not in parsed:
        return None
    try:
        parsed["score"] = max(0, min(5, int(parsed["score"])))
        parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    except Exception:
        return None
    parsed.setdefault("summary", "")
    parsed.setdefault("evidence", [])
    parsed.setdefault("search_queries_used", [])
    parsed.setdefault("reasoning", "")
    parsed["fetched_at"] = datetime.now(timezone.utc).isoformat()

    _cache_put(ticker, "deep_indicator", indicator_code, parsed)
    return parsed


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tkr = sys.argv[1] if len(sys.argv) > 1 else "ARM"
    print(f"\n=== fetch_earnings_qa({tkr}) ===")
    qa = fetch_earnings_qa(tkr, force_refresh=True)
    if qa:
        print(f"summary: {qa.get('summary','')}")
        print(f"topics : {qa.get('topics_flagged')}")
        print(f"source : {qa.get('source_hint','')}")
        print(f"digest first 1200 chars:")
        print(qa.get("digest", "")[:1200])
    else:
        print("NONE")

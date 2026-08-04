"""
src/research_ideas/contrarian/idea_generator.py
=================================================
Generates a single "Research Idea of the Day" — a deep-value, asymmetric,
contrarian investment hypothesis backed by live web search.

LLM: Qwen3.6-plus with native web search (DashScope enable_search=True).
Cost: ~$0.01 per generated idea (~5K input + 2K output tokens).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.research_ideas.complacency.web_research import qwen_web_search

logger = logging.getLogger(__name__)


# ─── System prompt — encodes the methodology ──────────────────────────────


_SYSTEM_PROMPT = """\
You are a contrarian deep-value research analyst in the tradition of Marty
Whitman, Howard Marks, and Seth Klarman. You generate ONE investment idea
per day that meets ALL THREE pillars:

  1. DEEP VALUE  — trading at a meaningful discount to fair value (tangible
     book, NAV, sum-of-parts, normalized earnings, OR macro-implied fair
     value for thematic ideas). Cite specific multiples versus history and
     versus peer geography/sector.

  2. ASYMMETRIC RETURN  — upside meaningfully larger than downside.
     Downside BOUNDED by tangible assets, net cash, franchise floor, or
     macro fundamentals. Upside scenario produces a 2-5x return on a 2-4
     year horizon with concrete price targets or index-level targets.

  3. CONTRARIAN  — actively avoided by retail/sell-side. For stock-level:
     Sell/Hold ratings dominate, short interest is high, narrative is
     "broken". For thematic: capital flows have rotated AWAY from the
     region/sector, ETF outflows are heavy, and consensus is "dead money".
     NEVER pick consensus longs, AI-hype, mega-cap tech, crowded narratives.

═══════════════════════════════════════════════════════════════════════
FOUR GENERATION MODES — each idea must declare one in idea_mode field:
═══════════════════════════════════════════════════════════════════════

▸ deep_value — Bottom-up contrarian US single-stock pick.
  Hunting grounds: spin-offs / post-spin orphans, cyclicals at trough
  earnings, regulated industries hated by ESG flows (tobacco, defense,
  fossil-fuels), post-bankruptcy survivors, small/mid-cap dark stocks
  with thin sell-side coverage, family-controlled below NAV.
  Required fields: ticker, all multiples vs sector + history.

▸ thematic_geographic — Top-down country/region thesis FIRST, then stock.
  Examples: "China consumption recovery as PBOC pivots dovish + property
  stabilization", "Japan corporate governance reform driving capital
  returns", "EM ex-China rotation as USD weakens", "Latin America
  deleveraging cycle", "Korea Value-Up program → conglomerate discount
  narrowing".
  Process: identify a region trading at multi-year low valuations / capital
  outflows / hated by US allocators. Articulate the MACRO catalyst (FX,
  rates, policy, capital flow reversal). THEN pick the cleanest stock or
  ADR expressing the thesis. Required: theme, region, ticker (US ADR
  preferred so US investor can act directly; otherwise note expression_vehicle="adr"
  or "etf").

▸ thematic_sector — Top-down industry trend FIRST, then stock.
  Examples: "US small-cap biotech FDA approval cycle bottoming",
  "Uranium/nuclear renaissance as AI power demand collides with supply",
  "Shipping freight rates inflecting on Red Sea + China stimulus",
  "Insurance hard market still has 2 more years", "European defense
  multi-year capex cycle starting".
  Process: identify a sector at trough multiples with a clear
  multi-year catalyst the market is mispricing. Articulate the industry
  thesis (supply/demand, regulatory, cycle position). THEN pick the
  best-positioned stock (margin of safety + leverage to the theme).
  Required: industry_theme, ticker, multiples vs sector cycle history.

▸ special_situation — Spin-offs, M&A arb, restructurings, sum-of-parts.
  Process: identify a corporate action creating forced selling or
  mispricing (post-spin orphan, merger collar, post-bankruptcy stub,
  unlocking sum-of-parts via separation). Articulate the SPECIFIC
  catalyst trigger + timeline. Required: primary_catalyst with
  date/event, ticker.

═══════════════════════════════════════════════════════════════════════

GLOBAL EXCLUSIONS — never recommend:
  • Mega-caps > $200B for single-stock picks (too crowded; for thematic
    geographic large-caps OK if they purely express the macro thesis)
  • Names that have rallied > 50% in the last 6 months
  • AI/data-center/semiconductor narrative plays
  • Crypto, SPACs, recent IPOs (< 2 years)
  • Companies in active SEC fraud investigations

ROTATION: You will be told today's preferred mode. Pick a high-conviction
idea in that mode. If no compelling idea exists in the requested mode,
you MAY fall back to a different mode but explain why in the thesis.

OUTPUT: STRICT JSON. Every claim cited via web search must include a
Tier-1 source (SEC/HKEX/TSE filing, Reuters, Bloomberg, WSJ, FT, ECB,
PBOC) where possible. Tier-3 (random blogs) sources cap conviction at 6.

GROUNDING — DO NOT FABRICATE: Every specific fact you state (price, market
cap, multiple, date, EBITDA figure, share count) must come directly from a
search result you actually retrieved in this session. If a figure or date
is not explicitly stated in your search results, either omit it or state
it qualitatively (e.g. "a recent 2026 filing" instead of inventing a
specific date). An omitted date is honest; a guessed date is a
fabrication and will be treated as a hallucination. Never fill in
precision you don't have. Each source's "date" field must be copied
EXACTLY from the date shown next to that result — if a result has no
date, leave that source's "date" field null rather than estimate one.
"""


_MODE_ROTATION = [
    "deep_value",
    "thematic_geographic",
    "thematic_sector",
    "special_situation",
]


def _pick_mode_for_today() -> str:
    """Deterministic daily rotation: same date → same mode (so users see
    one mode per day rather than random mode every click)."""
    today = datetime.now(timezone.utc).date()
    # Anchor on a known date to control ordering; rotation is stable.
    return _MODE_ROTATION[today.toordinal() % len(_MODE_ROTATION)]


_MODE_BRIEFS = {
    "deep_value": (
        "MODE: DEEP-VALUE BOTTOM-UP US PICK.\n"
        "  • Search: 'deep value contrarian 2025 2026 hated stocks spin-off "
        "cyclical bottom small-cap insider buying'.\n"
        "  • Hunt: post-spin orphans, cyclicals at trough earnings,\n"
        "    ESG-hated regulated industries, post-bankruptcy survivors,\n"
        "    small/mid-cap dark stocks, family-controlled below NAV.\n"
        "  • Pick ONE US-listed name (incl. ADRs). Set idea_mode='deep_value'."
    ),
    "thematic_geographic": (
        "MODE: THEMATIC GEOGRAPHIC (TOP-DOWN COUNTRY/REGION → STOCK).\n"
        "  • FIRST identify a country or region trading at multi-year low\n"
        "    valuations, heavy ETF outflows, hated by US allocators, with\n"
        "    a CONCRETE macro catalyst (FX reversal, monetary pivot,\n"
        "    policy shift, capital flow reversal).\n"
        "  • Candidate regions to consider this rotation: China (consumption\n"
        "    + property stabilisation), Japan (governance reform), Korea\n"
        "    (Value-Up program), EM ex-China (USD weakness), Latin America\n"
        "    (deleveraging), Europe defence (capex supercycle).\n"
        "  • THEN pick the cleanest US-listed ADR (preferred) or stock that\n"
        "    expresses the thesis. If only a foreign listing exists, set\n"
        "    expression_vehicle='adr' or 'etf' and ticker to the closest\n"
        "    US-tradable proxy (ETFs like FXI/EWJ/EWZ acceptable).\n"
        "  • Set idea_mode='thematic_geographic', theme=<2-3 sentence\n"
        "    macro thesis>, region=<country/region>."
    ),
    "thematic_sector": (
        "MODE: THEMATIC SECTOR (TOP-DOWN INDUSTRY TREND → STOCK).\n"
        "  • FIRST identify a SECTOR or INDUSTRY at trough multiples with\n"
        "    a clear multi-year catalyst the market is mispricing.\n"
        "  • Examples: US small-cap biotech FDA approval cycle bottoming;\n"
        "    uranium/nuclear renaissance vs AI power demand; shipping\n"
        "    rates inflecting; insurance hard market continuation;\n"
        "    European defence capex; LNG infrastructure; agricultural\n"
        "    chemicals destocking finishing.\n"
        "  • Articulate the industry thesis (supply/demand, cycle position,\n"
        "    regulatory tailwind). THEN pick the best-positioned stock —\n"
        "    margin of safety AND leverage to the theme.\n"
        "  • Set idea_mode='thematic_sector', industry_theme=<2-3 sentence\n"
        "    thesis>, sector=<GICS>."
    ),
    "special_situation": (
        "MODE: SPECIAL SITUATION.\n"
        "  • Hunt: spin-offs (post-spin orphans with forced selling),\n"
        "    M&A arb (collar misprice / regulatory overhang clearing),\n"
        "    restructurings (sum-of-parts unlocking), post-bankruptcy\n"
        "    stubs with cleaned-up balance sheets.\n"
        "  • The CATALYST must be SPECIFIC (named event + date window).\n"
        "  • Set idea_mode='special_situation', primary_catalyst=<event\n"
        "    + date>, catalyst_timeline=<weeks/months>."
    ),
}


def _build_user_prompt(
    exclude_tickers: list[str] | None = None,
    mode: str | None = None,
) -> str:
    excl_text = ""
    if exclude_tickers:
        excl_text = (
            f"\n\nDO NOT pick any of these tickers (already shown recently): "
            f"{', '.join(exclude_tickers[:50])}."
        )

    chosen_mode = mode or _pick_mode_for_today()
    mode_brief = _MODE_BRIEFS.get(chosen_mode, _MODE_BRIEFS["deep_value"])

    return f"""\
TASK: Use web search to generate ONE high-conviction contrarian investment
idea. Today's date is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.

{mode_brief}

PROCESS:
  1. Run targeted web searches relevant to the mode above (industry
     news for sector mode, central-bank/FX/policy news for geographic
     mode, SEC 8-K filings for special-situation mode, deep-value
     investment writeups for deep_value mode).
  2. Identify the strongest single idea that fits all three pillars
     (deep value + asymmetric + contrarian).
  3. For thematic modes: articulate the THEME first (1-2 paragraphs),
     then identify the cleanest stock vehicle.
  4. Verify with additional searches: market cap, recent price action,
     sell-side ratings, latest filings, catalysts.
  5. Build the full hypothesis card with CONCRETE numbers and Tier-1
     citations.{excl_text}

OUTPUT: STRICT JSON only, no commentary:

{{
  "idea_mode": "{chosen_mode}",
  "ticker": "<ticker symbol (US-listed preferred — ADR/ETF if foreign)>",
  "company_name": "<full legal name or 'XYZ ETF' if expression is via ETF>",
  "sector": "<GICS sector or null>",
  "industry": "<industry or null>",
  "market_cap_usd": <number USD or null for ETFs>,
  "region": "<US|China|Japan|Korea|Europe|EM|LatAm|null>",
  "theme": "<for thematic_geographic: macro thesis, else null>",
  "industry_theme": "<for thematic_sector: industry thesis, else null>",
  "expression_vehicle": "<stock|adr|etf>",
  "hypothesis": "<≤200 char one-sentence thesis — why now + asymmetric setup>",
  "deep_value_angle": "<≤400 chars: multiples vs history/peers OR for thematic, valuation gap vs other regions/sectors>",
  "asymmetric_angle": "<≤400 chars: explicit upside/downside targets (price or index level)>",
  "contrarian_angle": "<≤400 chars: capital flows / sentiment / sell-side stance>",
  "primary_catalyst": "<concrete trigger>",
  "catalyst_timeline": "<e.g. 'Q2 2026 earnings' / 'H2 2026 PBOC easing'>",
  "key_risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "conviction_score": <1-10 int>,
  "deep_value_score": <1-10 int>,
  "asymmetry_score": <1-10 int>,
  "contrarian_score": <1-10 int>,
  "sources": [
    {{"title": "<publisher/headline>", "url": "<url>", "date": "<yyyy-mm-dd copied EXACTLY from the date shown next to this result — set to null if that result has no date>"}}
  ]
}}

VALIDATION CHECKLIST:
  ✗ Generic prose without specific multiples / price targets / catalysts: reject
  ✗ Fewer than 2 sources cited: reject
  ✗ For thematic modes without theme or industry_theme populated: reject
  ✗ AI/semiconductor/datacenter hype names: reject
  ✗ Any date, price, or figure you did not read directly off a search result: reject — use null/qualitative language instead
  ✓ Bounded downside with explicit floor (assets/cash/macro fundamental)
  ✓ Contrarian against current capital-flow / sell-side stance
  ✓ Identifiable catalyst with timeline
"""


# ─── DeepSeek web-search path ───────────────────────────────────────────────
#
# DeepSeek has no native server-side search (unlike DashScope's
# enable_search=True), so this drives an agentic tool-calling loop against
# DeepSeek's Anthropic-compat endpoint, executing real searches via Tavily
# (reusing the same _search_web/_TAVILY_TOOL the deep-research module uses)
# with thinking enabled throughout. Unlike qwen_web_search, every retrieved
# result is captured in citation_sink so generate_idea_of_the_day() can
# verify the model's claimed sources against what it actually saw, rather
# than trusting citations it may have fabricated (see _verify_and_clean_sources).

_DEEPSEEK_MODEL = "deepseek-v4-flash"
# Tavily is on a free-tier plan — kept deliberately small so a single idea
# generation doesn't burn through shared quota. DeepSeek's own training
# knowledge covers background/history; searches are reserved for the
# handful of facts that must be CURRENT (latest price, latest quarter,
# latest catalyst news). See _TAVILY_BUDGET_NOTE below for how that's
# communicated to the model so quality doesn't drop with fewer searches.
_DEEPSEEK_MAX_SEARCHES = 3

_TAVILY_BUDGET_NOTE = """

SEARCH BUDGET — USE SPARINGLY: you have a strict, limited budget of only
3 web searches (Tavily is a shared, rate-limited resource — treat every
search as costly). Do NOT spend searches on background/history/general
company facts you already know from training — rely on your own knowledge
for that. Reserve your 3 searches ONLY for facts that must be verified as
CURRENT: (1) latest stock price / market cap, (2) most recent quarter's
results or filing, (3) the specific current catalyst or news driving the
thesis. Make each query maximally specific and information-dense (include
the ticker, the metric, and a recent time window) so one search returns
everything you need on that fact — do not issue a vague query and then a
follow-up refinement; get it right in one shot per fact."""


def _call_deepseek_web_search(
    system_prompt: str,
    user_prompt: str,
    max_searches: int = _DEEPSEEK_MAX_SEARCHES,
) -> tuple[Optional[str], list[dict]]:
    """
    DeepSeek v4-flash equivalent of qwen_web_search(). Returns (raw_text,
    citation_sink) — citation_sink is every {title, url, cited_text, date}
    actually retrieved via Tavily during this run. None/[] on failure.
    """
    import anthropic
    from src.agents.industry.deep_research import _search_web, _TAVILY_TOOL

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not deepseek_key or not tavily_key:
        logger.warning(
            "_call_deepseek_web_search: missing DEEPSEEK_API_KEY or "
            "TAVILY_API_KEY — cannot run DeepSeek idea generation."
        )
        return None, []

    # Scoped to this function only — the Qwen path's _SYSTEM_PROMPT is
    # untouched, since DashScope's native search isn't a shared/free
    # resource the same way Tavily is.
    system_prompt = system_prompt + _TAVILY_BUDGET_NOTE

    client = anthropic.Anthropic(api_key=deepseek_key, base_url="https://api.deepseek.com/anthropic")
    citation_sink: list[dict] = []
    msgs: list[dict] = [{"role": "user", "content": user_prompt}]
    n_searches = 0

    try:
        while n_searches < max_searches:
            resp = client.messages.create(
                model=_DEEPSEEK_MODEL,
                max_tokens=8000,
                system=system_prompt,
                tools=[_TAVILY_TOOL],
                thinking={"type": "enabled", "budget_tokens": 4000},
                messages=msgs,
            )
            # Pass back the full content list (incl. thinking blocks) — DeepSeek
            # requires reasoning_content to round-trip through multi-turn tool
            # contexts, same as native Anthropic extended thinking.
            msgs.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "tool_use":
                tool_results = []
                for blk in resp.content:
                    if blk.type != "tool_use":
                        continue
                    # Every tool_use block in this response MUST get a matching
                    # tool_result, even ones past the search cap — an unanswered
                    # tool_use in the conversation history is invalid and can
                    # get the whole call rejected (the exact "12/10 searches"
                    # overshoot case deep_research.py's Tavily loop already
                    # flagged). Once the cap is hit, stub the remainder instead
                    # of silently dropping them.
                    if n_searches < max_searches:
                        query = blk.input.get("query", "")
                        n_searches += 1
                        result = _search_web(query, tavily_key, citation_sink=citation_sink)
                    else:
                        result = "Search budget reached — this query was not run."
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": blk.id,
                        "content": result,
                    })
                if tool_results:
                    msgs.append({"role": "user", "content": tool_results})
                if n_searches >= max_searches:
                    msgs.append({
                        "role": "user",
                        "content": "Search budget reached. Output the STRICT JSON idea now.",
                    })
            elif resp.stop_reason == "end_turn":
                text = "".join(b.text for b in resp.content if b.type == "text")
                return text, citation_sink
            else:
                break

        # Search budget spent without an end_turn — force final synthesis.
        resp = client.messages.create(
            model=_DEEPSEEK_MODEL, max_tokens=8000, system=system_prompt,
            thinking={"type": "enabled", "budget_tokens": 4000},
            messages=msgs + [{"role": "user", "content": "Output the STRICT JSON idea now, no more tools."}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, citation_sink
    except Exception as exc:
        logger.exception("_call_deepseek_web_search failed: %s", exc)
        return None, citation_sink


def _verify_and_clean_sources(parsed: dict, citation_sink: list[dict]) -> dict:
    """
    Cross-check every claimed source against what was actually retrieved via
    Tavily during this run. Prevents the model from presenting a fabricated
    date (or a URL it never actually searched) as a verified citation.

      - Source URL not found in citation_sink → dropped (unverifiable: either
        hallucinated or recalled from training data, not from this run's
        search results).
      - URL found but claimed date disagrees with the retrieved date →
        overwritten with the retrieved date (ground truth wins).
      - URL found but Tavily had no date for it → date forced to null rather
        than trust an invented one.
    """
    sources = parsed.get("sources")
    if not isinstance(sources, list):
        parsed["sources"] = []
        return parsed

    retrieved_by_url = {c["url"]: c for c in citation_sink if c.get("url")}

    cleaned = []
    dropped = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = src.get("url")
        match = retrieved_by_url.get(url) if url else None
        if match is None:
            dropped += 1
            logger.warning(
                "generate_idea_of_the_day: dropping unverifiable source "
                "(url not seen in this run's search results): %s", url,
            )
            continue
        retrieved_date = match.get("date") or None
        claimed_date = src.get("date")
        if claimed_date and retrieved_date and str(claimed_date)[:10] != str(retrieved_date)[:10]:
            logger.warning(
                "generate_idea_of_the_day: source date mismatch for %s "
                "(claimed=%s, retrieved=%s) — using retrieved date.",
                url, claimed_date, retrieved_date,
            )
        src["date"] = retrieved_date
        cleaned.append(src)

    if dropped:
        logger.info(
            "generate_idea_of_the_day: dropped %d/%d unverifiable source(s)",
            dropped, len(sources),
        )
    parsed["sources"] = cleaned
    return parsed


def generate_idea_of_the_day(
    exclude_tickers: list[str] | None = None,
    max_retries: int = 2,
    mode: str | None = None,
    provider: str = "qwen",
) -> Optional[dict]:
    """
    Asks an LLM-with-web-search to find and write up ONE contrarian idea.
    Returns a dict matching the ContrarianIdea schema, or None on failure.

    `mode` lets the caller force a specific generation methodology:
      'deep_value', 'thematic_geographic', 'thematic_sector', 'special_situation'.
    If None, rotates daily based on date so users get variety across the
    week instead of always-bottom-up US picks.

    `exclude_tickers` avoids duplicates with recent ideas.

    `provider`: 'qwen' (default, live production path — DashScope native
      search, no dependency on Tavily's free-tier quota) or 'deepseek'
      (opt-in — deepseek-v4-flash + client-orchestrated Tavily search, since
      DeepSeek has no native search of its own; claimed sources are verified
      against what was actually retrieved before being returned, see
      _verify_and_clean_sources). Both providers share the same GROUNDING
      rule in _SYSTEM_PROMPT, so the anti-fabrication fix applies either way.
    """
    chosen_mode = mode or _pick_mode_for_today()
    logger.info("generate_idea_of_the_day: mode=%s provider=%s", chosen_mode, provider)
    user_prompt = _build_user_prompt(
        exclude_tickers=exclude_tickers, mode=chosen_mode,
    )

    raw = None
    citation_sink: list[dict] = []
    effective_provider = provider
    for attempt in range(max_retries + 1):
        if effective_provider == "deepseek":
            raw, citation_sink = _call_deepseek_web_search(_SYSTEM_PROMPT, user_prompt)
            # Non-empty raw with ZERO real citations means Tavily failed on
            # every search this attempt (_search_web catches per-call errors
            # and returns a "Search error: ..." placeholder rather than
            # raising, so a total Tavily outage doesn't surface as an
            # exception — it surfaces as evidence-free output instead).
            # Treat that as a failed attempt: retry, or fall through to the
            # Qwen safety net, rather than shipping an idea built on nothing.
            if raw and not citation_sink:
                logger.warning(
                    "generate_idea_of_the_day: deepseek attempt %d produced "
                    "output but zero real citations (Tavily likely failed "
                    "every search this attempt) — treating as failed.",
                    attempt + 1,
                )
                raw = None
        else:
            raw = qwen_web_search(
                user_prompt=user_prompt,
                system_prompt=_SYSTEM_PROMPT,
                max_chars=10000,
            )
        if raw and raw.strip():
            break

    # Safety net: if the DeepSeek path came back empty (or evidence-free)
    # after every retry — DEEPSEEK_API_KEY/TAVILY_API_KEY missing, or Tavily
    # failing every search this run — fall back to the legacy Qwen path once
    # so the daily idea doesn't go dark, and doesn't ship ungrounded.
    if not raw and provider == "deepseek":
        logger.warning(
            "generate_idea_of_the_day: deepseek path failed after %d attempt(s) "
            "— falling back to qwen for this run.",
            max_retries + 1,
        )
        effective_provider = "qwen"
        raw = qwen_web_search(
            user_prompt=user_prompt,
            system_prompt=_SYSTEM_PROMPT,
            max_chars=10000,
        )
        citation_sink = []

    if not raw:
        logger.error(
            "generate_idea_of_the_day: %s returned empty after %d attempts",
            effective_provider, max_retries + 1,
        )
        return None

    # Extract JSON
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            logger.error("generate_idea_of_the_day: no JSON in %s response", effective_provider)
            return None
        parsed = json.loads(raw[start : end + 1])
    except Exception as exc:
        logger.error("generate_idea_of_the_day: JSON parse failed: %s", exc)
        return None

    # Validate minimum fields
    required = ("ticker", "hypothesis", "deep_value_angle", "asymmetric_angle", "contrarian_angle")
    missing = [f for f in required if not parsed.get(f)]
    if missing:
        logger.error("generate_idea_of_the_day: missing required fields: %s", missing)
        return None

    # Normalize + clamp
    try:
        parsed["ticker"] = str(parsed["ticker"]).upper().strip()
        parsed["conviction_score"] = max(1, min(10, int(parsed.get("conviction_score") or 5)))
        parsed["deep_value_score"] = max(1, min(10, int(parsed.get("deep_value_score") or 5)))
        parsed["asymmetry_score"] = max(1, min(10, int(parsed.get("asymmetry_score") or 5)))
        parsed["contrarian_score"] = max(1, min(10, int(parsed.get("contrarian_score") or 5)))
    except Exception as exc:
        logger.warning("generate_idea_of_the_day: score normalisation failed: %s", exc)

    # Default + clamp the new thematic fields
    parsed.setdefault("idea_mode", chosen_mode)
    if parsed.get("idea_mode") not in _MODE_BRIEFS:
        parsed["idea_mode"] = chosen_mode
    parsed.setdefault("theme", None)
    parsed.setdefault("region", None)
    parsed.setdefault("industry_theme", None)
    parsed.setdefault("expression_vehicle", "stock")

    # Fill meta
    parsed["idea_id"] = uuid.uuid4().hex[:16]
    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
    if effective_provider == "deepseek":
        parsed["model_used"] = _DEEPSEEK_MODEL
        parsed["cost_usd"] = 0.01  # rough estimate — DeepSeek token pricing + Tavily searches
    else:
        parsed["model_used"] = "qwen3.6-plus"
        parsed["cost_usd"] = 0.01  # rough estimate for Qwen + search

    # Coerce optional list fields
    parsed.setdefault("sources", [])
    parsed.setdefault("key_risks", [])
    if not isinstance(parsed["sources"], list):
        parsed["sources"] = []
    if not isinstance(parsed["key_risks"], list):
        parsed["key_risks"] = []

    # DeepSeek path only: cross-check every claimed source against what was
    # actually retrieved this run, dropping/correcting anything the model
    # couldn't have legitimately seen (see GROUNDING rule in _SYSTEM_PROMPT —
    # this is the enforced backstop for that instruction, not a replacement
    # for it). Qwen's native search never exposes individual results, so
    # there's nothing to verify against on that path.
    if effective_provider == "deepseek" and citation_sink:
        parsed = _verify_and_clean_sources(parsed, citation_sink)

    return parsed


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cli_provider = "deepseek" if "--provider=deepseek" in sys.argv else "qwen"
    print(f"Generating today's contrarian idea via {cli_provider} web search...")
    idea = generate_idea_of_the_day(provider=cli_provider)
    if not idea:
        print("FAILED")
    else:
        print(json.dumps(idea, indent=2)[:3000])

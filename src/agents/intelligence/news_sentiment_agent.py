"""
src/agents/intelligence/news_sentiment_agent.py
================================================
Phase 2.5 — News Sentiment Agent (deterministic, no LLM)

Runs in parallel with the Insider Activity and Analyst Revision agents
immediately after the Strategic Router (Phase 2) and before the Industry
Specialist (Phase 3).

Data sources:
  FMP /stable/news/stock           → ticker-specific news (Starter plan, $22/mo)
  FMP /stable/news/press-releases  → official press releases (Starter plan)

Scoring methodology (fully deterministic, no LLM required):
  1. Each article title + text snippet is scanned against curated keyword
     lexicons (bullish / bearish / special-event).
  2. Raw score: +1.0 per bullish keyword hit, -1.0 per bearish keyword hit,
     clipped to [-1, +1].
  3. Recency weight: last 7 days = 2.0×, 8–30 days = 1.0×, older = 0.5×.
  4. Press-release weight: 1.5× (company-authored = higher signal).
  5. Composite score = weighted-mean of all article scores.
  6. Signal threshold: composite > +0.10 → BULLISH, < -0.10 → BEARISH, else NEUTRAL.

Special-event detection (override rules applied after scoring):
  - Earnings guidance raised + bullish → boost score by 0.20
  - SEC investigation / fraud keyword → clamp signal to BEARISH
  - Dividend initiation / buyback announcement → boost score by 0.10

Volume spike detection:
  - Fetches 30 days of articles; counts articles per day.
  - Spike flag = True if article count in last 7 days > 2× the prior 7-day count.

Output written to state["data"]["news_sentiment"][ticker] as a
NewsSentimentOutput dict, consumed by:
  - Investor prompts (intel_section) — all 12 agents
  - Value Trap auditor (negative news volume signal)
  - Portfolio Manager (conviction haircut when HIGH volume spike + BEARISH)

State compatibility:
  Reads:  state["data"]["tickers"], state["data"]["end_date"]
  Writes: state["data"]["news_sentiment"][ticker]
  Format: NewsSentimentOutput.model_dump() — safe to deserialise downstream
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Literal

from src.graph.state import AgentState
from src.data.models import NewsSentimentOutput, ScoredArticle
from src.tools.api import get_company_news, get_press_releases

# ── Sentiment lexicons ──────────────────────────────────────────────────────

_BULLISH_KEYWORDS: list[str] = [
    # earnings / guidance
    "beat", "beats", "exceeded", "exceeds", "above estimates", "above consensus",
    "raised guidance", "raises guidance", "raised outlook", "raises outlook",
    "record revenue", "record earnings", "record profit", "record quarter",
    "all-time high", "strong results", "strong quarter", "solid results",
    "double-digit growth", "accelerating growth", "growth acceleration",
    # capital returns
    "buyback", "share repurchase", "dividend increase", "dividend hike",
    "dividend initiation", "special dividend", "raised dividend",
    # deals & expansion
    "strategic acquisition", "wins contract", "new contract", "major contract",
    "partnership", "expands into", "new product launch", "product launch",
    "fda approval", "fda approved", "regulatory approval", "cleared",
    # analyst actions
    "upgrade", "upgraded", "outperform", "buy rating", "overweight",
    "price target raised", "price target increase", "bull case",
    # market / macro positive
    "market share gain", "margin expansion", "improving margins",
    "positive outlook", "optimistic", "confident", "strong demand",
    "exceeds expectations", "upside surprise",
]

_BEARISH_KEYWORDS: list[str] = [
    # earnings / guidance
    "miss", "missed", "below estimates", "below consensus", "below expectations",
    "disappoints", "disappointing", "cut guidance", "cuts guidance",
    "lowered guidance", "lowers guidance", "reduced outlook", "withdraws guidance",
    "earnings warning", "profit warning", "guidance cut",
    # financial distress
    "layoffs", "job cuts", "restructuring charges", "impairment", "write-down",
    "write-off", "asset write", "bankruptcy", "chapter 11", "debt default",
    "covenant breach", "credit downgrade", "junk rating",
    # legal / regulatory
    "sec investigation", "doj investigation", "securities fraud", "class action",
    "lawsuit", "regulatory fine", "penalty", "recall", "safety concern",
    "investigation launched", "under investigation", "charged with",
    # analyst actions
    "downgrade", "downgraded", "underperform", "sell rating", "underweight",
    "price target cut", "price target reduced", "bear case",
    # market / macro negative
    "market share loss", "margin compression", "rising costs",
    "slowing growth", "deceleration", "headwinds", "demand weakness",
    "disappointing outlook", "cautious", "uncertain", "challenging",
    "revenue shortfall", "cash burn", "going concern",
]

_GUIDANCE_RAISED_KEYWORDS: list[str] = [
    "raised guidance", "raises guidance", "raised outlook", "raises outlook",
    "increased guidance", "increases guidance", "above guidance",
]

_NEGATIVE_OVERRIDE_KEYWORDS: list[str] = [
    "sec investigation", "securities fraud", "class action lawsuit",
    "doj investigation", "going concern", "bankruptcy", "fraud alleged",
]

_CAPITAL_RETURN_BOOST_KEYWORDS: list[str] = [
    "buyback", "share repurchase", "dividend initiation", "special dividend",
    "dividend increase", "raised dividend",
]


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for keyword matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _count_keyword_hits(text_norm: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text_norm)


def _score_article(title: str, text: str) -> tuple[float, Literal["BULLISH", "BEARISH", "NEUTRAL"]]:
    """
    Score a single article on [-1.0, +1.0].

    Returns (score, label).  Score is clipped before any boost modifiers.
    """
    combined = _normalise(f"{title} {text}")

    bullish_hits = _count_keyword_hits(combined, _BULLISH_KEYWORDS)
    bearish_hits = _count_keyword_hits(combined, _BEARISH_KEYWORDS)

    # Net hit score, capped at ±1.0 before boosts
    raw = float(bullish_hits - bearish_hits)
    score = max(-1.0, min(1.0, raw * 0.3 if abs(raw) > 1 else raw))

    # Boost: earnings guidance raised → push further positive
    if any(kw in combined for kw in _GUIDANCE_RAISED_KEYWORDS) and score >= 0:
        score = min(1.0, score + 0.20)

    # Boost: capital return announced → push slightly positive
    if any(kw in combined for kw in _CAPITAL_RETURN_BOOST_KEYWORDS) and score >= 0:
        score = min(1.0, score + 0.10)

    # Override: negative legal / regulatory event → clamp to at least -0.5
    if any(kw in combined for kw in _NEGATIVE_OVERRIDE_KEYWORDS):
        score = min(score, -0.50)

    # Label
    if score > 0.05:
        label: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
    elif score < -0.05:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return round(score, 4), label


def _recency_weight(article_date: str, end_date: str) -> float:
    """Return a recency multiplier based on days before end_date."""
    try:
        delta = (
            datetime.strptime(end_date, "%Y-%m-%d")
            - datetime.strptime(article_date, "%Y-%m-%d")
        ).days
    except ValueError:
        return 1.0

    if delta <= 7:
        return 2.0
    if delta <= 30:
        return 1.0
    return 0.5


def _detect_volume_spike(articles_30d: list[ScoredArticle], end_date: str) -> bool:
    """
    Return True if the article volume in the last 7 days is more than 2×
    the article volume in the preceding 7 days (days 8–14 before end_date).
    Requires at least 5 total articles to fire.
    """
    if len(articles_30d) < 5:
        return False

    end_dt   = datetime.strptime(end_date, "%Y-%m-%d")
    last7_dt  = end_dt - timedelta(days=7)
    prior7_dt = end_dt - timedelta(days=14)

    last7_count  = sum(
        1 for a in articles_30d
        if last7_dt <= datetime.strptime(a.date, "%Y-%m-%d") <= end_dt
    )
    prior7_count = sum(
        1 for a in articles_30d
        if prior7_dt <= datetime.strptime(a.date, "%Y-%m-%d") < last7_dt
    )

    if prior7_count == 0:
        return False  # not enough baseline
    return last7_count > 2 * prior7_count


def _press_release_signal(
    prs: list[ScoredArticle],
) -> Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "NONE"]:
    if not prs:
        return "NONE"
    avg = sum(p.score for p in prs) / len(prs)
    if avg > 0.10:
        return "POSITIVE"
    if avg < -0.10:
        return "NEGATIVE"
    return "NEUTRAL"


def run_news_sentiment_agent(state: AgentState) -> AgentState:
    """
    Compute news sentiment metrics for each ticker in state.

    Reads:   state["data"]["tickers"], state["data"]["end_date"]
    Writes:  state["data"]["news_sentiment"][ticker]
    """
    tickers  = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    api_key  = (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    # Lookback window: 30 days of news for volume comparison; 60 for trending
    start_30d = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    start_60d = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")

    results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  [NewsSentimentAgent] {ticker} — fetching news & press releases")

        # ── Fetch stock news (last 60 days, up to 50 articles) ────────────
        raw_news = get_company_news(
            ticker=ticker,
            end_date=end_date,
            start_date=start_60d,
            limit=50,
            api_key=api_key,
        )

        # ── Fetch press releases (last 60 days, up to 20) ─────────────────
        raw_prs = get_press_releases(
            ticker=ticker,
            end_date=end_date,
            start_date=start_60d,
            limit=20,
            api_key=api_key,
        )

        if not raw_news and not raw_prs:
            results[ticker] = NewsSentimentOutput(
                ticker=ticker,
                signal="NEUTRAL",
                analysis_note="No news or press release data available (FMP Starter plan required).",
            ).model_dump()
            print(f"  [NewsSentimentAgent] {ticker} — no data (plan tier)")
            continue

        # ── Score all articles ─────────────────────────────────────────────
        scored: list[ScoredArticle] = []

        # Filter out articles whose FMP-reported symbol doesn't match the requested ticker.
        # FMP occasionally returns off-topic articles (e.g. AAPL news in NKE feed).
        raw_news = [a for a in raw_news if a.ticker.upper() == ticker.upper()]

        for article in raw_news:
            sc, lbl = _score_article(article.title, "")  # text not in CompanyNews
            scored.append(ScoredArticle(
                ticker=ticker,
                title=article.title,
                text="",
                date=article.date,
                source=article.source,
                url=article.url,
                is_press_release=False,
                score=sc,
                label=lbl,
            ))

        scored_prs: list[ScoredArticle] = []
        for pr in raw_prs:
            sc, lbl = _score_article(pr.title, "")
            scored_prs.append(ScoredArticle(
                ticker=ticker,
                title=pr.title,
                text="",
                date=pr.date,
                source=pr.source,
                url=pr.url,
                is_press_release=True,
                score=sc,
                label=lbl,
            ))

        all_scored = scored + scored_prs

        # ── Compute recency-weighted composite score ───────────────────────
        total_weight = 0.0
        weighted_sum = 0.0

        for a in all_scored:
            rw = _recency_weight(a.date, end_date)
            pw = 1.5 if a.is_press_release else 1.0
            w  = rw * pw
            weighted_sum += a.score * w
            total_weight += w

        composite = round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0

        # ── Classify composite signal ──────────────────────────────────────
        if composite > 0.10:
            signal: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
        elif composite < -0.10:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        # ── Count labels ───────────────────────────────────────────────────
        bullish_count = sum(1 for a in all_scored if a.label == "BULLISH")
        bearish_count = sum(1 for a in all_scored if a.label == "BEARISH")
        neutral_count = sum(1 for a in all_scored if a.label == "NEUTRAL")

        # ── Volume spike (use 30d articles only for comparison) ───────────
        articles_30d = [a for a in all_scored if a.date >= start_30d]
        volume_spike = _detect_volume_spike(articles_30d, end_date)

        # ── Press-release-only signal ──────────────────────────────────────
        pr_signal = _press_release_signal(scored_prs)

        # ── Top headlines: highest |score| articles ────────────────────────
        top = sorted(all_scored, key=lambda a: abs(a.score), reverse=True)[:5]
        top_headlines = [f"[{a.label}] {a.title[:90]}" for a in top]

        # ── Build analysis note ────────────────────────────────────────────
        note_parts: list[str] = [
            f"Scored {len(scored)} news articles + {len(scored_prs)} press releases. "
            f"Composite score: {composite:+.3f} → {signal}. "
        ]
        if bullish_count:
            note_parts.append(f"Bullish articles: {bullish_count}. ")
        if bearish_count:
            note_parts.append(f"Bearish articles: {bearish_count}. ")
        if volume_spike:
            note_parts.append("VOLUME SPIKE: unusual news volume in last 7 days. ")
        if pr_signal != "NONE":
            note_parts.append(f"Press release signal: {pr_signal}. ")

        output = NewsSentimentOutput(
            ticker=ticker,
            signal=signal,
            composite_score=composite,
            article_count=len(scored),
            press_release_count=len(scored_prs),
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            neutral_count=neutral_count,
            press_release_signal=pr_signal,
            volume_spike=volume_spike,
            top_headlines=top_headlines,
            scored_articles=all_scored[:20],   # cap stored articles for state size
            analysis_note="".join(note_parts),
        )

        results[ticker] = output.model_dump()

        print(
            f"  [NewsSentimentAgent] {ticker} — {signal} | "
            f"composite={composite:+.3f} | "
            f"B:{bullish_count} N:{neutral_count} Be:{bearish_count} | "
            f"PR={pr_signal} | spike={volume_spike}"
        )

    state["data"]["news_sentiment"] = results
    return state

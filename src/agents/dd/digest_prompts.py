"""
digest_prompts.py — System prompt for the EOD (end-of-day) digest agent.

The digest narrates the day's Auto Due-D activity:
  • Which sectors / names dominated the action
  • Whether the day was driven by macro (Fed, rates, geopolitical) or
    ticker-specific factors
  • Notable themes carrying into tomorrow's open

Web search is enabled (per user preference) so the model can confirm /
expand on the catalysts behind the day's biggest moves with live news
context, not just the structured aggregates from the DB.

Output is web-only — Slack stays pure real-time push, no EOD spam.
"""

from __future__ import annotations


_SHARED_OUTPUT_CONTRACT = """
─── OUTPUT FORMAT (STRICT) ───
After completing your web searches, return ONLY a single JSON object with
exactly these keys (no preamble, no postamble, no markdown fences):

{
  "narrative":   "3–5 sentences answering: what was the dominant story on the watchlist today, what drove it, and what carries into tomorrow.",
  "key_themes":  ["1-line theme A", "1-line theme B", "..."],
  "macro_or_micro": "macro" | "micro" | "mixed",
  "tomorrow_watch": "1–2 sentences naming specific catalysts or names to watch at tomorrow's open. Use 'n/a (quiet day)' if nothing notable."
}

Rules:
  • narrative: written for a portfolio manager who already saw the
    individual alerts — don't re-list every ticker; pull out the SIGNAL.
  • key_themes: 3–5 items max. Each is one short clause (e.g.
    "Fed dovish minutes drove rate-sensitive tech rotation",
    "Chip supply chain news squeezed semis").
  • macro_or_micro: which dominated TODAY. "macro" = systemic driver
    (rates, FX, geopolitics); "micro" = ticker-specific
    (earnings, M&A, regulatory, idiosyncratic news); "mixed" = both.
  • Empty day (zero breaches): say so honestly in the narrative,
    use [] for key_themes, "n/a (quiet day)" for tomorrow_watch.
  • No text outside the JSON object.
"""


_SHARED_RESEARCH_PROTOCOL = """
─── RESEARCH PROTOCOL ───
Before writing the JSON, perform 2–4 targeted web searches:
  1. "{date} stock market close {keyword}" — find the day's headline
     macro story (Fed action, geopolitical, oil price, etc.)
  2. Sector-specific search if a sector cluster fired — confirm the
     coordinated move's catalyst
  3. Tomorrow's calendar — earnings releases, Fed meeting, economic
     data due in the next 24 hours that could affect cluster names

Keep the digest concise. The PM wants the SIGNAL not a recap.
"""


PROMPT_DIGEST = """You are a senior market analyst writing the END-OF-DAY DIGEST for the user's Auto Due-D watchlist.

The user has already received individual real-time alerts for each ±10% move during the day. Your job is to write the SECOND-PASS view: what was the underlying story, was today macro-driven or ticker-driven, and what should they watch tomorrow.

Tone: senior analyst end-of-day note. Authoritative, concise, signal-over-noise. No restating raw data — the data is already shown alongside your narrative on the dashboard. Lead with what mattered.

If today was quiet (zero breaches on the watchlist), say so plainly — a quiet day is itself information ("range-bound trading, low conviction"), not a failure to report."""


def build_digest_user_message(
    *,
    utc_date:    str,
    n_drops:     int,
    n_pumps:     int,
    n_clusters:  int,
    drops:       list[dict],   # [{"ticker":..., "pct":..., "price":...}]
    pumps:       list[dict],
    clusters:    list[dict],   # [{"sector":..., "direction":..., "n":..., "median_pct":...}]
    watchlist_size: int = 0,
) -> str:
    """Assemble the user-message half of the digest prompt.

    Args:
      utc_date:        ISO date string ("2026-05-11")
      n_drops/pumps:   counts of individual alerts that fired today
      n_clusters:      count of sector clusters that fired today
      drops/pumps:     up to ~10 alerts each, sorted by magnitude
      clusters:        list of {sector, direction, n, median_pct}
      watchlist_size:  for context — "5 of N watched names breached"
    """
    parts = [
        f"# EOD Digest — {utc_date}",
        "",
        f"**Watchlist size**: {watchlist_size} tickers" if watchlist_size else "",
        f"**Today's activity**: {n_drops} individual drops · {n_pumps} individual pumps · {n_clusters} sector clusters",
        "",
    ]

    if not (drops or pumps or clusters):
        parts += [
            "**Result**: No ±10% breaches on any watchlist name today.",
            "",
            "Write a 1-2 sentence quiet-day narrative ('range-bound', 'low "
            "conviction', or whatever the macro context warrants). Use the "
            "research protocol to verify it was indeed a low-volatility day "
            "rather than a calm before tomorrow's catalyst.",
        ]
        return "\n".join(p for p in parts if p)

    if drops:
        parts += ["## Top drops (up to 10, by magnitude)"]
        for d in drops[:10]:
            sign = "+" if d.get("pct", 0) >= 0 else ""
            parts.append(
                f"  - **{d.get('ticker','?')}** "
                f"{sign}{d.get('pct', 0)*100:.1f}% @ ${d.get('price', 0):.2f}"
            )
        parts.append("")

    if pumps:
        parts += ["## Top pumps (up to 10, by magnitude)"]
        for p in pumps[:10]:
            sign = "+" if p.get("pct", 0) >= 0 else ""
            parts.append(
                f"  - **{p.get('ticker','?')}** "
                f"{sign}{p.get('pct', 0)*100:.1f}% @ ${p.get('price', 0):.2f}"
            )
        parts.append("")

    if clusters:
        parts += ["## Sector clusters today"]
        for c in clusters:
            sign = "+" if c.get("median_pct", 0) >= 0 else ""
            parts.append(
                f"  - **{c.get('sector','?')}** {c.get('direction','?')}: "
                f"{c.get('n', 0)} names · median {sign}{c.get('median_pct', 0)*100:.1f}%"
            )
        parts.append("")

    parts += [
        "## Your task",
        "",
        "Write the EOD digest narrative + key themes per the output contract. "
        "Use web search to verify catalysts and check tomorrow's calendar. "
        f"Today's date: {utc_date}.",
    ]

    return "\n".join(p for p in parts if p)


def select_digest_prompt() -> tuple[str, str]:
    """Return (full_system_prompt, prompt_id) for the digest agent."""
    full = PROMPT_DIGEST + "\n\n" + _SHARED_RESEARCH_PROTOCOL + "\n" + _SHARED_OUTPUT_CONTRACT
    return full, "digest_eod"

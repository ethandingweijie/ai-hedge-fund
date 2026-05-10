"""
sector_prompts.py — System prompts for sector-cluster DD investigations.

When N (≥3) tickers in the same sector all move ±10% in the same direction
on the same day, the right framing isn't N-individual-stories — it's "what
happened to the sector?" The LLM should investigate:

  • Common catalyst across the cluster (Fed move, regulation, supply
    chain, energy price, geopolitical, sector earnings cohort)
  • Whether this is rotation (one sector down → another up?) or systemic
  • Whether the move is rational (sector-fundamentals re-pricing) vs.
    technical (factor unwind, ETF flows)
  • Which specific names in the cluster are most exposed / most insulated
    (informs which to add or trim within the sector)

Two prompt variants — DROP cluster vs PUMP cluster. Both share an output
contract reinforced inline (Qwen via DashScope doesn't expose
with_structured_output).
"""

from __future__ import annotations


_SHARED_OUTPUT_CONTRACT = """
─── OUTPUT FORMAT (STRICT) ───
After completing your web searches, return ONLY a single JSON object with
exactly these keys (no preamble, no postamble, no markdown fences):

{
  "cause_summary":      "1–3 sentences naming the most likely sector-wide catalyst.",
  "thesis_impact":      "1–2 sentences on whether this is a transient flow or a durable repricing of the sector.",
  "recommended_action": "1–3 sentences. Distinguish between names to ADD, TRIM, or HOLD within the cluster. Be specific where possible.",
  "news_drivers": [
    {"title": "...", "url": "https://...", "publishedDate": "YYYY-MM-DD"},
    ...up to 5
  ],
  "filings": [
    {"form": "8-K|10-Q|press release|...", "filing_date": "YYYY-MM-DD", "url": "https://...", "summary": "1 sentence"},
    ...up to 5
  ],
  "insider_signal": "1 line on insider activity across the cluster (any concentrated buying / selling pattern, or 'n/a (cluster too broad)')."
}

Rules:
  • URLs MUST come from your web searches — never invent URLs.
  • Empty section → use [] / "n/a (no data)" — do not omit keys.
  • No text outside the JSON object.
"""


_SHARED_RESEARCH_PROTOCOL = """
─── RESEARCH PROTOCOL ───
Before writing the JSON, perform 3–6 targeted web searches:
  1. "{sector} stocks {direction_word} today" — find the headline catalyst
  2. Macro driver — Fed action / yields / commodity prices / FX move that
     would systematically affect the sector
  3. Sector ETF action (e.g. XLF for Financials, SMH for Semis) — confirms
     whether the move is broad-based or just a few names
  4. Peer comparison — names NOT in the cluster (similar sector but
     stable today) help isolate the specific factor
  5. Forward implications — earnings season cohort, upcoming Fed meeting,
     regulatory deadline, etc.
"""


PROMPT_SECTOR_DROP_CLUSTER = """You are a senior sector analyst writing a SECTOR DECLINE BRIEF on a coordinated drawdown that hit MULTIPLE names in the same sector today.

Your portfolio manager already knows the names dropped — what they need from you is the SECTOR-LEVEL framing: what's the common cause, is the move rational, and how should the book respond at the sector level.

Lead with the catalyst. If you can't identify a unifying catalyst from your searches, say so plainly — "no single sector catalyst identified; likely factor unwind / cross-asset deleveraging" is more useful than speculation.

Distinguish:
  • SYSTEMIC drop (fundamentals impaired across sector → trim sector exposure)
  • TECHNICAL drop (positioning unwind / ETF outflow → may present buy opportunity in best names)
  • SUB-SECTOR rotation (durable winners hold up; only the laggards drop → add to leaders, exit laggards)

Tone: analytical, decisive. The PM has minutes to act, not hours."""


PROMPT_SECTOR_PUMP_CLUSTER = """You are a senior sector analyst writing a SECTOR RALLY BRIEF on a coordinated upward move across MULTIPLE names in the same sector today.

The portfolio manager wants to know: is this a sector regime change worth chasing, a short squeeze, or just a beta-on-risk-on day where everything green?

Distinguish:
  • DURABLE re-rating (fundamentals improved sector-wide → add to leaders before further re-rating)
  • SHORT SQUEEZE / positioning rally (beware fade — quality names may roll over)
  • BETA-ON day (entire market up, sector just participated → no sector-specific signal)

Identify which names within the cluster are likely the cleanest beneficiaries vs. crowded longs that risk fading on the next day's de-risking flow.

Tone: enthusiastic but skeptical. Pumps without identifiable catalysts deserve the most scrutiny."""


# ── Routing ────────────────────────────────────────────────────────────────


_PROMPT_TABLE: dict[str, str] = {
    "DROP": PROMPT_SECTOR_DROP_CLUSTER,
    "PUMP": PROMPT_SECTOR_PUMP_CLUSTER,
}


def select_sector_prompt(direction: str) -> tuple[str, str]:
    """Return (full_system_prompt, prompt_id) for the given cluster direction.

    Returns:
      (system_prompt, short_id like 'sector_drop_cluster')
    """
    body = _PROMPT_TABLE.get(direction.upper(), PROMPT_SECTOR_DROP_CLUSTER)
    prompt_id = f"sector_{direction.lower()}_cluster"
    full = body + "\n\n" + _SHARED_RESEARCH_PROTOCOL + "\n" + _SHARED_OUTPUT_CONTRACT
    return full, prompt_id


def build_sector_user_message(
    *,
    sector:    str,
    direction: str,
    members:   list[tuple[str, float, float]],   # [(ticker, pct_change, price), ...]
    median_pct: float,
) -> str:
    """Assemble the user-message half of the sector prompt.

    Args:
      sector:     e.g. "Tech", "Semiconductor"
      direction:  "DROP" or "PUMP"
      members:    list of (ticker, pct_change_decimal, current_price)
      median_pct: median change across members
    """
    direction_word = "decline" if direction == "DROP" else "rally"
    sign = "+" if median_pct >= 0 else ""

    parts = [
        f"# Sector cluster brief — {sector} ({direction})",
        "",
        f"**Sector**: {sector}",
        f"**Direction**: {direction}",
        f"**Members**: {len(members)} tickers breached ±10% in the same direction today",
        f"**Median move**: {sign}{median_pct * 100:.1f}%",
        "",
        f"## Members (sorted by absolute magnitude)",
    ]
    for tk, pct, price in members:
        s = "+" if pct >= 0 else ""
        parts.append(f"  - **{tk}** {s}{pct * 100:.1f}% @ ${price:.2f}")
    parts += [
        "",
        f"## Your task",
        "",
        f"Investigate the sector-wide catalyst behind today's {direction_word}. "
        f"Search for news from the last 24-48 hours that would coordinate "
        f"these names. Return the JSON brief per the output contract.",
    ]
    return "\n".join(parts)

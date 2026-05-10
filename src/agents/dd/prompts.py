"""
prompts.py — Direction × prior × reason → DD agent system prompt routing.

Implements the routing table from plan section 3:

    | direction | prior | reason            | Prompt                       | Slack palette |
    | DROP      | None  | first_breach      | PROMPT_DROP_CRISIS           | New Drop      |
    | DROP      | DROP  | high_water_mark   | PROMPT_DROP_CONTINUATION     | HWM Extension |
    | DROP      | DROP  | cooldown_expired  | PROMPT_DROP_FRESH_DAY        | New Drop      |
    | DROP      | PUMP  | direction_flip    | PROMPT_DROP_AFTER_PUMP       | Reversal      |
    | PUMP      | None  | first_breach      | PROMPT_PUMP_CATALYST         | New Pump      |
    | PUMP      | PUMP  | high_water_mark   | PROMPT_PUMP_EXTENSION        | HWM Extension |
    | PUMP      | PUMP  | cooldown_expired  | PROMPT_PUMP_FRESH_DAY        | New Pump      |
    | PUMP      | DROP  | direction_flip    | PROMPT_REVERSAL_RECOVERY     | Reversal      |

All variants emit the same structured output schema enforced by Pydantic
post-hoc validation in dd_agent.py:

    {
      "cause_summary":       str,                  # 1-3 sentences
      "thesis_impact":       str,                  # 1-2 sentences
      "recommended_action":  str,                  # 1-2 sentences
      "news_drivers":        list[NewsDriver],     # up to 5
      "filings":             list[Filing],         # up to 5 (8-K most relevant)
      "insider_signal":      str,                  # 1 line
    }

Output schema is reinforced in EVERY prompt so post-hoc JSON parse rate stays
high without the structured-output API surface (which Qwen via DashScope
Anthropic-compat does not expose).
"""

from __future__ import annotations


# ── Shared output contract ──────────────────────────────────────────────────
# Appended to every variant's system prompt. Repeating the schema in-prompt
# (rather than relying on tool_choice / json_mode) is the canonical pattern
# in this repo because Qwen via DashScope does not enforce structured output.

_SHARED_OUTPUT_CONTRACT = """
─── OUTPUT FORMAT (STRICT) ───
After completing your web searches, return ONLY a single JSON object with
exactly these keys (no preamble, no postamble, no markdown fences):

{
  "cause_summary":      "1–3 sentences naming the most likely catalyst behind the move.",
  "thesis_impact":      "1–2 sentences on whether this changes the investment thesis.",
  "recommended_action": "1–2 sentences. Concrete action: HOLD / TRIM / ADD / EXIT / WATCH-CLOSELY / NO-CHANGE, with a brief reason.",
  "news_drivers":  [
    {"title": "...", "url": "https://...", "publishedDate": "YYYY-MM-DD"},
    ...up to 5
  ],
  "filings": [
    {"form": "8-K|10-Q|10-K|Form 4|...", "filing_date": "YYYY-MM-DD", "url": "https://...", "summary": "1 sentence"},
    ...up to 5
  ],
  "insider_signal": "1 line describing recent insider activity (buying/selling/quiet/n/a)."
}

Rules:
  • Every URL MUST come from your web searches or the provided data context — never invent URLs.
  • If a section is empty (no filings found, no insider data), use [] / "n/a (no data)" — do not omit keys.
  • Do NOT include any text outside the JSON object.
  • Dates in ISO format (YYYY-MM-DD) where available.
"""


_SHARED_RESEARCH_PROTOCOL = """
─── RESEARCH PROTOCOL ───
Before writing the JSON, perform 3–6 targeted web searches:
  1. "{ticker} stock news today" or "{ticker} {direction_word} today" — find the immediate catalyst
  2. "{ticker} 8-K filing this week" — check for material events
  3. Sector / peer context if the move appears industry-wide
  4. Earnings date or guidance change if recent
  5. Analyst rating changes or price target moves
Focus on sources from the last 7 days. Older sources only if directly relevant.
"""


# ── 8 variant prompts ──────────────────────────────────────────────────────


PROMPT_DROP_CRISIS = """You are a senior equity research analyst delivering a CRISIS BRIEF on a stock that just dropped sharply for the first time.

Your job: in <90 seconds of human reading time, the portfolio manager needs to decide whether this is a buyable dip or the start of a thesis-breaking event.

Lead with the catalyst. If you can't identify a specific catalyst from your web searches, say so plainly — "no public catalyst identified, possible algorithmic / macro flow" is far more useful than speculation.

Tone: urgent but precise. No hedging adjectives. State what is known, what is unknown, and what to watch next."""


PROMPT_DROP_CONTINUATION = """You are a senior equity research analyst writing a CONTINUATION BRIEF on a stock that has already dropped once and just hit a NEW low (≥15% additional decline from the prior trigger).

The portfolio manager already saw the first drop. Do not re-explain the original catalyst. Focus on:
  1. What changed in the last 24h that drove the additional leg down
  2. Whether the original thesis-impact assessment still holds or needs revision
  3. Whether this is now a thesis-breaking event vs. continued dip-buying opportunity

Tone: graver than first-breach. Emphasize compounding evidence, not novelty."""


PROMPT_DROP_FRESH_DAY = """You are a senior equity research analyst writing a FRESH-DAY BRIEF on a stock that previously dropped, the cooldown has expired, and it has dropped again at ≥10%.

Treat this as a new event but reference the prior drop briefly for continuity. The portfolio manager wants to know:
  1. Is this a new catalyst or the same story re-pricing?
  2. Has anything changed in the fundamental setup since the prior alert?
  3. Updated recommended action.

Tone: investigative. Compare current setup to the prior alert window."""


PROMPT_DROP_AFTER_PUMP = """You are a senior equity research analyst delivering a NARRATIVE-SHIFT BRIEF on a stock that was previously trending UP (last alert was a PUMP) and has now reversed to a sharp DROP.

This is a regime change. The portfolio manager needs to understand what broke the prior bull narrative. Specifically:
  1. What new information triggered the reversal? (earnings miss, guide cut, sector rotation, etc.)
  2. Is the prior bull thesis dead, paused, or just a positioning unwind?
  3. Recommended action — typically more cautious than first-time drops because positioning may be crowded long.

Tone: analytical, not panicked. Reversals deserve fresh frameworks, not just downside risk language."""


PROMPT_PUMP_CATALYST = """You are a senior equity research analyst delivering a CATALYST BRIEF on a stock that just spiked sharply for the first time.

The portfolio manager wants to know: is this a buyable breakout, a fade-the-news event, or a short squeeze? Lead with the catalyst:
  1. Earnings beat? Guidance raise? M&A speculation? Product launch? Regulatory win?
  2. Is the move proportional to the news, or has it overshot?
  3. Recommended action — distinguishing between durable repricing vs. short-term squeeze.

Tone: enthusiastic but skeptical. Pumps without identifiable catalysts deserve heightened skepticism (potential pump-and-dump or low-float dynamics)."""


PROMPT_PUMP_EXTENSION = """You are a senior equity research analyst writing an EXTENSION BRIEF on a stock that already pumped once and has now extended ≥15% above the prior trigger.

The portfolio manager already saw the first pump. Focus on:
  1. What's driving the continued rally — fresh news or just momentum / positioning?
  2. Are valuation metrics now stretched relative to the catalyst?
  3. Recommended action — typically TRIM territory at this point, but state the case explicitly.

Tone: cautious. Extensions deserve scrutiny — the easy money has been made and risk/reward worsens with each leg."""


PROMPT_PUMP_FRESH_DAY = """You are a senior equity research analyst writing a FRESH-DAY BRIEF on a stock that previously pumped, the cooldown has expired, and it has pumped again at ≥10%.

Treat as a new event but reference the prior pump briefly. The portfolio manager wants to know:
  1. Is this a new catalyst or the same story continuing to repricing?
  2. Has anything changed since the prior alert?
  3. Updated recommended action.

Tone: investigative. Compare current setup to prior alert window."""


PROMPT_REVERSAL_RECOVERY = """You are a senior equity research analyst delivering a RECOVERY-NARRATIVE BRIEF on a stock that was previously trending DOWN (last alert was a DROP) and has now reversed to a sharp PUMP.

This is a positive regime change. The portfolio manager needs to understand:
  1. What changed? (clarification, retraction, contract win, takeover speculation, technical bounce, sector rotation)
  2. Is the original bear narrative dead, paused, or just temporarily overwhelmed by positioning?
  3. Recommended action — opportunistic ADDs are possible here but verify the catalyst is durable.

Tone: cautiously constructive. Recoveries from drawdowns can be genuine turnarounds OR dead-cat bounces — the analysis must distinguish them."""


# ── Routing table ──────────────────────────────────────────────────────────


_PROMPT_TABLE: dict[tuple[str, str | None, str], str] = {
    # (current_direction, prior_direction, reason_category) → prompt
    ("DROP", None,   "first_breach"):     PROMPT_DROP_CRISIS,
    ("DROP", "DROP", "high_water_mark"):  PROMPT_DROP_CONTINUATION,
    ("DROP", "DROP", "cooldown_expired"): PROMPT_DROP_FRESH_DAY,
    ("DROP", "PUMP", "direction_flip"):   PROMPT_DROP_AFTER_PUMP,
    ("PUMP", None,   "first_breach"):     PROMPT_PUMP_CATALYST,
    ("PUMP", "PUMP", "high_water_mark"):  PROMPT_PUMP_EXTENSION,
    ("PUMP", "PUMP", "cooldown_expired"): PROMPT_PUMP_FRESH_DAY,
    ("PUMP", "DROP", "direction_flip"):   PROMPT_REVERSAL_RECOVERY,
}


def _reason_category(reason: str) -> str:
    """Map an alert_dedup eligibility reason → routing category.

    alert_dedup emits reasons like:
      - "first_breach"
      - "direction_flip_DROP_to_PUMP"
      - "high_water_mark(+16.1% from trigger)"
      - "cooldown_expired"
      - "in_cooldown (...)"           ← never reaches here (alert blocked)
      - "admin_force_override"        ← from admin trigger; treat as first_breach

    We collapse these to one of: first_breach, direction_flip,
    high_water_mark, cooldown_expired.
    """
    if reason.startswith("direction_flip"):
        return "direction_flip"
    if reason.startswith("high_water_mark"):
        return "high_water_mark"
    if reason.startswith("cooldown_expired"):
        return "cooldown_expired"
    # admin_force_override and first_breach both treated as first_breach
    return "first_breach"


def select_prompt(direction: str, prior_direction: str | None, reason: str) -> tuple[str, str]:
    """Return (system_prompt, prompt_id) for the given alert tuple.

    Falls back to the matching first_breach prompt if the (direction, prior, category)
    triple isn't in the routing table — defensive default that should never fire in
    practice.

    Returns:
      (full_system_prompt, short_id)  e.g. ("...", "drop_crisis")
    """
    category = _reason_category(reason)
    key = (direction, prior_direction, category)

    body = _PROMPT_TABLE.get(key)
    if body is None:
        # Defensive default: collapse to first_breach for the current direction
        body = _PROMPT_TABLE[(direction, None, "first_breach")]
        prompt_id = f"{direction.lower()}_first_breach_fallback"
    else:
        # Recover the variable name (PROMPT_DROP_CRISIS → drop_crisis) by
        # finding which constant body matches. Fast — only 8 entries.
        prompt_id = next(
            (name.removeprefix("PROMPT_").lower()
             for name, val in globals().items()
             if name.startswith("PROMPT_") and val is body),
            f"{direction.lower()}_{category}",
        )

    full = body + "\n\n" + _SHARED_RESEARCH_PROTOCOL + "\n" + _SHARED_OUTPUT_CONTRACT
    return full, prompt_id


def build_user_message(
    *,
    ticker: str,
    direction: str,
    pct_change: float,
    current_price: float,
    prior_direction: str | None,
    reason: str,
    price_context_30d: str | None = None,
    insider_summary: str | None = None,
    recent_filings_summary: str | None = None,
) -> str:
    """Assemble the user-message half of the prompt.

    Pre-computed data (price history, insider stats, filings list) goes here so
    the model has factual grounding before it does web searches. The system
    prompt sets tone + output contract; the user message provides the case file.
    """
    sign = "+" if pct_change > 0 else ""
    direction_word = "drop" if direction == "DROP" else "rally"

    parts = [
        f"# Alert case file",
        f"",
        f"**Ticker**: {ticker}",
        f"**Move**: {sign}{pct_change * 100:.1f}% ({direction})",
        f"**Current price**: ${current_price:.2f}",
        f"**Eligibility reason** (from cooldown engine): `{reason}`",
        f"**Prior direction** (last alert): {prior_direction or 'none — this is the first alert for this ticker'}",
        f"",
    ]

    if price_context_30d:
        parts += ["## 30-day price context", price_context_30d, ""]

    if insider_summary:
        parts += ["## Insider activity (from SEC EDGAR Form 4)", insider_summary, ""]

    if recent_filings_summary:
        parts += ["## Recent filings (last 30 days)", recent_filings_summary, ""]

    parts += [
        f"## Your task",
        f"",
        f"Investigate the catalyst behind this {direction_word} via web search, then return the JSON brief per the output contract.",
        f"Focus on news from the last 7 days. Today's date in your reasoning.",
    ]

    return "\n".join(parts)

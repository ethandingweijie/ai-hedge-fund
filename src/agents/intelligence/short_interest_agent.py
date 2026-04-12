"""
src/agents/intelligence/short_interest_agent.py
================================================
Phase 2.5 — Short Interest Agent (deterministic, no LLM)

Runs in parallel with Insider Activity, Analyst Revision, News Sentiment,
and Earnings Quality agents immediately after the Strategic Router (Phase 2)
and before the Industry Specialist (Phase 3).

Data source:
  yfinance — FINRA bi-monthly settlement data (free).
  Returns current + prior-month periods for trend calculation.
  Degrades to UNKNOWN on error or missing data — no pipeline failure.

Metrics computed:
  - Short float %  (short interest / float)       → HIGH/MEDIUM/LOW
  - Days to cover  (short interest / avg vol)     → HIGH/MEDIUM/LOW
  - Borrow rate    (annualised %, if available)   → HIGH/MEDIUM/LOW
  - Trend          (current vs prior period)       → INCREASING/STABLE/DECREASING
  - Squeeze risk   (short float > 20% AND DTC > 7 days)
  - Crowded trade  (short float > 15%)
  - Signal         → HEAVILY_SHORTED / MODERATELY_SHORTED / LOW_SHORT_INTEREST

Persona-specific notes injected into investor prompts:
  - burry_note       : forensic / variant-perception framing
  - druckenmiller_note : positioning / crowded-trade / squeeze-fuel framing

Output written to state["data"]["short_interest"][ticker] as a
ShortInterestOutput dict. Consumed by:
  - All 12 investor prompts via intel_section (Pathway 1)
  - Burry and Druckenmiller receive additional persona-specific notes
    surfacing the signal through their specific investment lenses

Backward compatibility:
  If the key is absent from state (agent failed or plan restriction),
  downstream consumers receive an empty dict and fall through to their
  pre-existing inference paths without any code changes required.
"""

from __future__ import annotations

from src.graph.state import AgentState
from src.data.models import ShortInterestOutput
from src.tools.api import get_short_interest

# ── Thresholds ───────────────────────────────────────────────────────────────

_SHORT_FLOAT_HIGH   = 20.0   # % — heavily shorted; Burry target zone / squeeze risk
_SHORT_FLOAT_MEDIUM = 10.0   # % — moderately shorted; watch for crowding
_SHORT_FLOAT_CROWDED = 15.0  # % — crowded trade threshold (Druckenmiller risk flag)

_DTC_HIGH   = 10.0   # days — extended short squeeze fuel
_DTC_MEDIUM =  5.0   # days

_BORROW_HIGH   = 50.0   # % p.a. — specialness; hard-to-borrow name
_BORROW_MEDIUM = 20.0   # % p.a.

_SQUEEZE_DTC_THRESHOLD  = 7.0   # days DTC to trigger squeeze_risk flag
_TREND_CHANGE_THRESHOLD = 0.05  # 5% relative change to register trend


# ── Helpers ──────────────────────────────────────────────────────────────────

def _classify_short_float(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct > _SHORT_FLOAT_HIGH:
        return "HIGH"
    if pct > _SHORT_FLOAT_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _classify_dtc(dtc: float | None) -> str:
    if dtc is None:
        return "UNKNOWN"
    if dtc > _DTC_HIGH:
        return "HIGH"
    if dtc > _DTC_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _classify_borrow(rate: float | None, is_bps: bool = False) -> str:
    if rate is None:
        return "UNKNOWN"
    pct = rate / 100.0 if is_bps else rate   # convert basis points → %
    if pct > _BORROW_HIGH:
        return "HIGH"
    if pct > _BORROW_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _short_trend(current: float | None, prior: float | None) -> str:
    if current is None or prior is None or prior == 0:
        return "UNKNOWN"
    change = (current - prior) / prior
    if change > _TREND_CHANGE_THRESHOLD:
        return "INCREASING"
    if change < -_TREND_CHANGE_THRESHOLD:
        return "DECREASING"
    return "STABLE"


def _build_burry_note(
    signal: str,
    short_float_pct: float | None,
    dtc: float | None,
    borrow_rate: float | None,
    trend: str,
    crowded: bool,
) -> str:
    """
    Frame the short interest data through Burry's forensic contrarian lens.
    Burry cares about: variant perception confirmation, crowded shorts,
    and squeeze risk that could invalidate a bearish thesis prematurely.
    """
    parts: list[str] = []

    if signal == "HEAVILY_SHORTED":
        parts.append(
            f"Short float {short_float_pct:.1f}% — market has discovered the bearish thesis. "
            "This confirms variant perception BUT signals crowded positioning."
        )
        if borrow_rate and borrow_rate > _BORROW_MEDIUM:
            parts.append(
                f"Borrow cost {borrow_rate:.1f}% p.a. — hard-to-borrow; "
                "short sellers are paying to hold this position. "
                "High borrow + high short float = squeeze fuel if thesis is wrong."
            )
    elif signal == "MODERATELY_SHORTED":
        parts.append(
            f"Short float {short_float_pct:.1f}% — moderate short interest. "
            "Market is sceptical but not unanimously bearish. "
            "Burry variant perception requires the crowd to be wrong here."
        )
    else:
        parts.append(
            f"Short float {short_float_pct:.1f}% — low short interest. "
            "Market is NOT positioned against this name — "
            "if Burry is bearish, he has first-mover advantage on the short."
        )

    if trend == "INCREASING":
        parts.append("Short interest is RISING — others are discovering the same thesis.")
    elif trend == "DECREASING":
        parts.append("Short interest is FALLING — shorts are covering; thesis may be playing out.")

    if dtc and dtc > _SQUEEZE_DTC_THRESHOLD:
        parts.append(
            f"Days-to-cover {dtc:.1f}d — high squeeze risk. "
            "A positive catalyst could force a violent covering rally. "
            "Size position accordingly and define the stop-loss precisely."
        )

    return " ".join(parts)


def _build_druckenmiller_note(
    signal: str,
    short_float_pct: float | None,
    dtc: float | None,
    borrow_rate: float | None,
    trend: str,
    squeeze_risk: bool,
    crowded: bool,
) -> str:
    """
    Frame the short interest data through Druckenmiller's macro/positioning lens.
    Druckenmiller cares about: crowded trades (dangerous for longs AND shorts),
    squeeze risk as a stop-loss trigger, and flow/positioning signals.
    """
    parts: list[str] = []

    if crowded:
        parts.append(
            f"CROWDED TRADE WARNING: short float {short_float_pct:.1f}% > 15%. "
            "Crowded shorts are one of the most dangerous situations for a long position — "
            "any positive surprise forces rapid covering and price spikes. "
            "If LONG: momentum of covering rally is a tailwind. "
            "If SHORT: premature squeeze risk — position sizing must be conservative."
        )
    elif signal == "HEAVILY_SHORTED":
        parts.append(
            f"Short float {short_float_pct:.1f}% — heavily shorted. "
            "Consensus is bearish; Druckenmiller needs a macro catalyst to time the reversal."
        )
    elif signal == "MODERATELY_SHORTED":
        parts.append(
            f"Short float {short_float_pct:.1f}% — moderate short positioning. "
            "Flow signal is cautiously bearish; monitor for direction change."
        )
    else:
        parts.append(
            f"Short float {short_float_pct:.1f}% — low short interest. "
            "No meaningful short-side positioning; market not pricing in downside."
        )

    if squeeze_risk:
        parts.append(
            f"SQUEEZE RISK: {dtc:.1f} days-to-cover. "
            "This is a stop-loss trigger consideration: if the macro thesis turns, "
            "a short squeeze would move faster than the exits. "
            "Define the stop before entering this position."
        )

    if borrow_rate and borrow_rate > _BORROW_MEDIUM:
        parts.append(
            f"Borrow rate {borrow_rate:.1f}% p.a. — elevated. "
            "Institutional short sellers are paying to hold; "
            "a positive catalyst generates forced covering flows."
        )

    if trend == "INCREASING":
        parts.append("Trend: short interest BUILDING — positioning momentum is bearish.")
    elif trend == "DECREASING":
        parts.append("Trend: short interest FALLING — bears are covering; reduces downside flow pressure.")

    return " ".join(parts)


# ── Agent entry point ─────────────────────────────────────────────────────────

def run_short_interest_agent(state: AgentState) -> AgentState:
    """
    Phase 2.5 — Short Interest Agent.

    Reads:   state["data"]["tickers"]
    Writes:  state["data"]["short_interest"][ticker]

    Fetches current + prior-month settlement data from yfinance (FINRA data,
    free). Computes short float %, days-to-cover, trend, squeeze risk,
    crowded-trade flag, and persona-specific notes for Burry and Druckenmiller.
    """
    tickers = state["data"]["tickers"]
    results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  [ShortInterestAgent] {ticker} — fetching short interest")

        try:
            rows = get_short_interest(ticker)

            if not rows:
                print(f"  [ShortInterestAgent] {ticker} — no data (API unavailable or no short data)")
                results[ticker] = ShortInterestOutput(
                    ticker=ticker,
                    signal="UNKNOWN",
                    data_source="NONE",
                    analysis_note="No short interest data available.",
                ).model_dump()
                continue

            # ── Latest period ─────────────────────────────────────────────────
            latest = rows[0]
            prior  = rows[1] if len(rows) > 1 else None

            short_float_pct = latest.get("short_percent")
            shares_short    = latest.get("short_interest")
            shares_float    = latest.get("shares_float")
            dtc             = latest.get("days_to_cover")
            borrow_raw      = latest.get("borrow_rate")
            is_bps          = latest.get("borrow_rate_is_bps", False)
            report_date     = latest.get("date", "")
            row_source      = latest.get("source", "FMP")   # "FMP" or "yfinance"

            # Normalise borrow rate: convert bps → % if needed
            borrow_rate_pct: float | None = None
            if borrow_raw is not None:
                borrow_rate_pct = borrow_raw / 100.0 if is_bps else borrow_raw

            # Derive short_float_pct from raw shares if not in response
            if short_float_pct is None and shares_short and shares_float and shares_float > 0:
                short_float_pct = shares_short / shares_float * 100.0

            # ── Prior period ──────────────────────────────────────────────────
            prior_short_pct: float | None = None
            if prior:
                prior_short_pct = prior.get("short_percent")
                if prior_short_pct is None:
                    ps = prior.get("short_interest")
                    pf = prior.get("shares_float")
                    if ps and pf and pf > 0:
                        prior_short_pct = ps / pf * 100.0

            # ── Classify ──────────────────────────────────────────────────────
            sf_flag     = _classify_short_float(short_float_pct)
            dtc_flag    = _classify_dtc(dtc)
            borrow_flag = _classify_borrow(borrow_rate_pct)
            trend       = _short_trend(short_float_pct, prior_short_pct)

            squeeze_risk = bool(
                short_float_pct is not None and short_float_pct > _SHORT_FLOAT_HIGH
                and dtc is not None and dtc > _SQUEEZE_DTC_THRESHOLD
            )
            crowded_trade = bool(
                short_float_pct is not None and short_float_pct > _SHORT_FLOAT_CROWDED
            )

            # ── Overall signal ────────────────────────────────────────────────
            if short_float_pct is not None:
                if short_float_pct > _SHORT_FLOAT_HIGH:
                    signal = "HEAVILY_SHORTED"
                elif short_float_pct > _SHORT_FLOAT_MEDIUM:
                    signal = "MODERATELY_SHORTED"
                else:
                    signal = "LOW_SHORT_INTEREST"
            else:
                signal = "UNKNOWN"

            # ── Persona notes ─────────────────────────────────────────────────
            burry_note = _build_burry_note(
                signal, short_float_pct, dtc, borrow_rate_pct, trend, crowded_trade
            )
            druck_note = _build_druckenmiller_note(
                signal, short_float_pct, dtc, borrow_rate_pct, trend, squeeze_risk, crowded_trade
            )

            # yfinance note when borrow rate is unavailable
            borrow_note = (
                f"Borrow: {borrow_rate_pct:.1f}% [{borrow_flag}]"
                if borrow_rate_pct is not None
                else "Borrow: n/a (yfinance)"
            )
            note = (
                f"Source: {row_source}. Date: {report_date}. "
                f"Short float: {short_float_pct:.1f}% [{sf_flag}] | "
                f"DTC: {dtc:.1f}d [{dtc_flag}] | "
                f"{borrow_note} | "
                f"Trend: {trend} | Squeeze: {squeeze_risk} | Crowded: {crowded_trade}."
                if short_float_pct is not None and dtc is not None
                else f"Source: {row_source}. Date: {report_date}. Partial data available."
            )

            data_source_val: str = "yfinance"

            results[ticker] = ShortInterestOutput(
                ticker=ticker,
                report_date=report_date or None,
                short_interest_shares=shares_short,
                short_float_pct=round(short_float_pct, 2) if short_float_pct is not None else None,
                shares_float=shares_float,
                days_to_cover=round(dtc, 2) if dtc is not None else None,
                borrow_rate_pct=round(borrow_rate_pct, 2) if borrow_rate_pct is not None else None,
                short_float_flag=sf_flag,
                days_to_cover_flag=dtc_flag,
                borrow_rate_flag=borrow_flag,
                short_interest_trend=trend,
                short_float_pct_prior=round(prior_short_pct, 2) if prior_short_pct is not None else None,
                squeeze_risk=squeeze_risk,
                crowded_trade=crowded_trade,
                signal=signal,
                burry_note=burry_note,
                druckenmiller_note=druck_note,
                data_source=data_source_val,
                analysis_note=note,
            ).model_dump()

            borrow_str = f"{borrow_rate_pct:.1f}%" if borrow_rate_pct is not None else "n/a"
            print(
                f"  [ShortInterestAgent] {ticker} ({row_source}) — {signal} | "
                f"short_float={short_float_pct:.1f}% [{sf_flag}] | "
                f"dtc={dtc:.1f}d | borrow={borrow_str} | "
                f"squeeze={squeeze_risk} | crowded={crowded_trade} | trend={trend}"
                if short_float_pct is not None and dtc is not None
                else f"  [ShortInterestAgent] {ticker} ({row_source}) — {signal} (partial data)"
            )

        except Exception as exc:
            print(f"  [ShortInterestAgent] {ticker} — ERROR: {exc}")
            results[ticker] = ShortInterestOutput(
                ticker=ticker,
                data_source="NONE",
                analysis_note=f"Agent error: {exc}",
            ).model_dump()

    state["data"]["short_interest"] = results
    return state

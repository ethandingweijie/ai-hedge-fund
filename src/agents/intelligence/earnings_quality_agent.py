"""
src/agents/intelligence/earnings_quality_agent.py
==================================================
Phase 2.5 — Earnings Quality Scorer (deterministic, no LLM)

Runs in parallel with the Insider Activity, Analyst Revision, and News Sentiment
agents immediately after the Strategic Router (Phase 2) and before the Industry
Specialist (Phase 3).

Metrics computed (all from FMP financial statements — no new endpoints):
  1. Accrual ratio (Sloan 1996) — (NI − OCF) / Total Assets
     HIGH accruals predict negative future returns; RED if 3-year avg > 0.10
  2. Cash conversion ratio — OCF / Net Income
     Measures how much reported profit converts to real cash; RED if < 0.75
  3. AR vs Revenue divergence — 3-year CAGR of Accounts Receivable vs Revenue
     AR growing faster than revenue = channel stuffing or aggressive recognition
  4. Days Sales Outstanding (DSO) trend — computed from AR / Revenue × 365
     Rising DSO signals collection deterioration even when revenue looks healthy
  5. Stock-based compensation drag — SBC / OCF
     Inflates reported EPS while consuming real cash; HIGH if > 25% of OCF
  6. FCF vs Net Income divergence — trend of FCF / NI over 3 years
     A widening gap between net income and FCF is the most reliable value trap
     precursor in empirical finance literature

Output written to state["data"]["earnings_quality"][ticker] as an
EarningsQualityOutput dict, consumed by:
  - Pathway 1 : all 12 investor agent prompts via intel_section injection
  - Pathway 2 : Value Trap agent Check 3 (earnings vs cash flow mismatch)
  - Pathway 3 : Risk Manager — appended as a non-blocking remark only

Forward compatibility: all field accesses use .get(); graceful UNKNOWN/PARTIAL
degradation when fewer than 3 years of data are available.

Backward compatibility: if this key is absent from state (agent failed or was
not run), every downstream consumer falls back to its pre-existing LLM inference
path without any code changes required.
"""

from __future__ import annotations

import os
import statistics
from typing import Any

from src.graph.state import AgentState
from src.data.models import EarningsQualityOutput
from src.tools.api import search_line_items, get_financial_metrics


# ── Thresholds ──────────────────────────────────────────────────────────────

_ACCRUAL_RED   = 0.10   # avg (NI-OCF)/TA above this → RED
_ACCRUAL_AMBER = 0.05   # 0.05–0.10 → AMBER

_CCR_RED   = 0.75   # OCF/NI below this → RED
_CCR_AMBER = 0.85   # 0.75–0.85 → AMBER

_AR_DIV_RED   = 1.50   # AR CAGR > Revenue CAGR × 1.5 → RED
_AR_DIV_AMBER = 1.20   # × 1.2–1.5 → AMBER

_SBC_HIGH   = 25.0   # SBC/OCF % above this → HIGH drag
_SBC_MEDIUM = 15.0   # 15–25% → MEDIUM drag

# Scoring deductions (start at 10.0)
_SCORE_DEDUCTIONS = {
    "accrual_red":    2.0,
    "accrual_amber":  1.0,
    "ccr_red":        2.0,
    "ccr_amber":      1.0,
    "ar_div_red":     1.5,
    "ar_div_amber":   0.5,
    "dso_rising":     1.0,
    "sbc_high":       0.5,
    "fcf_ni_red":     1.5,
    "fcf_ni_amber":   0.5,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cagr(start: float, end: float, years: int) -> float | None:
    """Compound annual growth rate from start to end over n years."""
    if years <= 0 or start is None or end is None or start <= 0:
        return None
    try:
        return (end / start) ** (1.0 / years) - 1.0
    except (ZeroDivisionError, ValueError):
        return None


def _trend_direction(values: list[float]) -> str:
    """
    Returns 'RISING', 'FALLING', or 'STABLE' for a list of values
    ordered newest-first.  Requires at least 2 values.
    At least 2 of the last 3 consecutive pairs must agree for a trend.
    """
    if len(values) < 2:
        return "UNKNOWN"
    # Pairs: (newer, older) — newest is values[0]
    # Positive diff = newer > older = rising when read chronologically
    pairs = [values[i] - values[i + 1] for i in range(min(len(values) - 1, 3))]
    up   = sum(1 for d in pairs if d > 0)
    down = sum(1 for d in pairs if d < 0)
    if up >= 2 and up > down:
        return "RISING"
    if down >= 2 and down > up:
        return "FALLING"
    return "STABLE"


def _accrual_trend(ratios: list[float]) -> str:
    """
    Accruals are stored newest-first.
    DETERIORATING = accruals getting larger (more manipulation risk)
    IMPROVING     = accruals shrinking
    """
    if len(ratios) < 2:
        return "UNKNOWN"
    direction = _trend_direction(ratios)
    if direction == "RISING":
        return "DETERIORATING"
    if direction == "FALLING":
        return "IMPROVING"
    return "STABLE"


def _fcf_ni_divergence(ratios: list[float]) -> str:
    """
    FCF/NI ratios newest-first.
    RED   = ratio declining in 2+ of last 3 year-on-year comparisons
    AMBER = declining in 1 of last 3
    GREEN = stable or improving
    """
    if len(ratios) < 2:
        return "UNKNOWN"
    direction = _trend_direction(ratios)  # reuses same logic on FCF/NI ratio
    if direction == "FALLING":
        return "RED"   # FCF/NI shrinking → earnings quality eroding
    if direction == "RISING":
        return "GREEN"
    return "AMBER"


# ── Core computation ─────────────────────────────────────────────────────────

def _compute_earnings_quality(
    ticker: str,
    series: list[dict],      # list of merged period rows, newest first
) -> EarningsQualityOutput:
    """
    Given a list of per-period financial rows (newest first), compute all
    earnings quality metrics and return an EarningsQualityOutput.

    Each row is expected to contain snake_case field names as produced by
    search_line_items() / get_financial_metrics() merge logic.
    """
    flags: list[str] = []
    score = 10.0
    metrics_computed = 0

    # ── Filter to rows with enough data for each metric ─────────────────────
    # We work from newest to oldest; need at least 1 row for current metrics,
    # 3+ rows for trend/CAGR metrics.

    # ── 1. Accrual ratio ────────────────────────────────────────────────────
    accrual_ratios: list[float] = []
    for row in series:
        ni  = _safe_float(row, "net_income")
        ocf = _safe_float(row, "operating_cash_flow")
        ta  = _safe_float(row, "total_assets")
        if ni is not None and ocf is not None and ta and ta > 0:
            accrual_ratios.append((ni - ocf) / ta)

    accrual_ratio_avg: float | None = None
    accrual_trend_val = "UNKNOWN"
    accrual_flag = "UNKNOWN"

    if accrual_ratios:
        metrics_computed += 1
        accrual_ratio_avg = statistics.mean(accrual_ratios[:3]) if len(accrual_ratios) >= 1 else accrual_ratios[0]
        accrual_trend_val = _accrual_trend(accrual_ratios)
        avg = accrual_ratio_avg
        if avg > _ACCRUAL_RED:
            accrual_flag = "RED"
            score -= _SCORE_DEDUCTIONS["accrual_red"]
            flags.append(
                f"Accrual ratio avg {avg:.3f} > {_ACCRUAL_RED} — earnings likely overstated vs cash flow"
            )
        elif avg > _ACCRUAL_AMBER:
            accrual_flag = "AMBER"
            score -= _SCORE_DEDUCTIONS["accrual_amber"]
            flags.append(f"Accrual ratio avg {avg:.3f} — moderate earnings quality concern")
        else:
            accrual_flag = "GREEN"

    # ── 2. Cash conversion ratio (OCF / NI) ─────────────────────────────────
    cash_conversion_ratio: float | None = None
    cash_conversion_flag = "UNKNOWN"

    # Use most recent row with both OCF and NI > 0
    for row in series:
        ni  = _safe_float(row, "net_income")
        ocf = _safe_float(row, "operating_cash_flow")
        if ni is not None and ocf is not None and ni > 0:
            cash_conversion_ratio = ocf / ni
            metrics_computed += 1
            if cash_conversion_ratio < _CCR_RED:
                cash_conversion_flag = "RED"
                score -= _SCORE_DEDUCTIONS["ccr_red"]
                flags.append(
                    f"Cash conversion ratio {cash_conversion_ratio:.2f} < {_CCR_RED} — "
                    f"only {cash_conversion_ratio * 100:.0f}% of net income converts to operating cash"
                )
            elif cash_conversion_ratio < _CCR_AMBER:
                cash_conversion_flag = "AMBER"
                score -= _SCORE_DEDUCTIONS["ccr_amber"]
                flags.append(
                    f"Cash conversion ratio {cash_conversion_ratio:.2f} — "
                    "net income not fully converting to cash (monitor trend)"
                )
            else:
                cash_conversion_flag = "GREEN"
            break

    # ── 3. AR vs Revenue divergence ──────────────────────────────────────────
    ar_cagr_3y: float | None = None
    revenue_cagr_3y: float | None = None
    ar_revenue_divergence = "UNKNOWN"

    # Need at least 4 rows for a 3-year CAGR (current + 3 years prior)
    ar_vals  = [_safe_float(r, "accounts_receivable") for r in series[:4]]
    rev_vals = [_safe_float(r, "revenue") for r in series[:4]]

    ar_valid  = [v for v in ar_vals  if v is not None and v > 0]
    rev_valid = [v for v in rev_vals if v is not None and v > 0]

    if len(ar_valid) >= 2 and len(rev_valid) >= 2:
        years = min(len(ar_valid), len(rev_valid)) - 1
        ar_cagr_3y      = _cagr(ar_valid[-1],  ar_valid[0],  years)
        revenue_cagr_3y = _cagr(rev_valid[-1], rev_valid[0], years)
        if ar_cagr_3y is not None and revenue_cagr_3y is not None:
            metrics_computed += 1
            # Only meaningful when revenue is growing (if revenue declining,
            # AR ratios behave differently — exclude to avoid false signals)
            if revenue_cagr_3y > 0:
                ratio = ar_cagr_3y / revenue_cagr_3y if revenue_cagr_3y != 0 else float("inf")
                if ratio > _AR_DIV_RED:
                    ar_revenue_divergence = "RED"
                    score -= _SCORE_DEDUCTIONS["ar_div_red"]
                    flags.append(
                        f"AR growing {ar_cagr_3y:.1%} CAGR vs revenue {revenue_cagr_3y:.1%} — "
                        "receivables expanding faster than sales (channel stuffing / recognition risk)"
                    )
                elif ratio > _AR_DIV_AMBER:
                    ar_revenue_divergence = "AMBER"
                    score -= _SCORE_DEDUCTIONS["ar_div_amber"]
                    flags.append(
                        f"AR CAGR {ar_cagr_3y:.1%} vs revenue CAGR {revenue_cagr_3y:.1%} — "
                        "mild divergence, monitor next 2 quarters"
                    )
                else:
                    ar_revenue_divergence = "GREEN"
            else:
                # Revenue declining: AR growing in this context is a larger red flag
                if ar_cagr_3y is not None and ar_cagr_3y > 0.05:
                    ar_revenue_divergence = "RED"
                    score -= _SCORE_DEDUCTIONS["ar_div_red"]
                    flags.append(
                        f"AR growing {ar_cagr_3y:.1%} while revenue declining {revenue_cagr_3y:.1%} — "
                        "receivables expanding into a shrinking business (high collection risk)"
                    )
                else:
                    ar_revenue_divergence = "AMBER"

    # ── 4. DSO trend ─────────────────────────────────────────────────────────
    dso_values: list[float] = []
    dso_trend_val = "UNKNOWN"

    # Prefer pre-computed DSO from key metrics; fall back to AR/Revenue × 365
    for row in series:
        dso = _safe_float(row, "days_sales_outstanding")
        if dso and dso > 0:
            dso_values.append(dso)
        else:
            ar  = _safe_float(row, "accounts_receivable")
            rev = _safe_float(row, "revenue")
            if ar is not None and rev and rev > 0:
                dso_values.append(ar / rev * 365.0)

    if len(dso_values) >= 2:
        metrics_computed += 1
        dso_trend_val = _trend_direction(dso_values)
        if dso_trend_val == "RISING":
            score -= _SCORE_DEDUCTIONS["dso_rising"]
            flags.append(
                f"DSO trending up — latest {dso_values[0]:.0f}d vs "
                f"{dso_values[-1]:.0f}d {len(dso_values) - 1}yr ago "
                "(collections deteriorating or revenue pull-forward)"
            )

    # ── 5. SBC drag ──────────────────────────────────────────────────────────
    sbc_drag_pct: float | None = None
    sbc_drag_flag = "UNKNOWN"

    for row in series:
        sbc = _safe_float(row, "stock_based_compensation")
        ocf = _safe_float(row, "operating_cash_flow")
        if sbc is not None and ocf and ocf > 0:
            sbc_drag_pct = abs(sbc) / ocf * 100.0
            metrics_computed += 1
            if sbc_drag_pct > _SBC_HIGH:
                sbc_drag_flag = "HIGH"
                score -= _SCORE_DEDUCTIONS["sbc_high"]
                flags.append(
                    f"SBC drag {sbc_drag_pct:.1f}% of OCF — "
                    "reported EPS meaningfully inflated relative to economic earnings"
                )
            elif sbc_drag_pct > _SBC_MEDIUM:
                sbc_drag_flag = "MEDIUM"
                flags.append(f"SBC drag {sbc_drag_pct:.1f}% of OCF — moderate dilution pressure")
            else:
                sbc_drag_flag = "LOW"
            break

    # ── 6. FCF vs NI divergence ──────────────────────────────────────────────
    fcf_ni_ratios: list[float] = []
    fcf_ni_div = "UNKNOWN"

    for row in series:
        fcf = _safe_float(row, "free_cash_flow")
        ni  = _safe_float(row, "net_income")
        if fcf is not None and ni is not None and ni > 0:
            fcf_ni_ratios.append(fcf / ni)

    if len(fcf_ni_ratios) >= 2:
        metrics_computed += 1
        fcf_ni_div = _fcf_ni_divergence(fcf_ni_ratios)
        if fcf_ni_div == "RED":
            score -= _SCORE_DEDUCTIONS["fcf_ni_red"]
            flags.append(
                f"FCF/NI ratio declining: {fcf_ni_ratios[0]:.2f} (latest) "
                f"vs {fcf_ni_ratios[-1]:.2f} ({len(fcf_ni_ratios) - 1}yr ago) — "
                "net income diverging from free cash flow (earnings quality eroding)"
            )
        elif fcf_ni_div == "AMBER":
            score -= _SCORE_DEDUCTIONS["fcf_ni_amber"]
            flags.append(
                f"FCF/NI mixed trend: {fcf_ni_ratios[0]:.2f} latest — "
                "one-year decline in cash conversion quality"
            )

    # ── Aggregate score / verdict ─────────────────────────────────────────────
    score = max(0.0, min(10.0, score))

    if score >= 7.0:
        quality_verdict = "HIGH"
    elif score >= 4.0:
        quality_verdict = "MEDIUM"
    else:
        quality_verdict = "LOW"

    # ── Pre-earnings risk ─────────────────────────────────────────────────────
    # Combines accruals (Sloan signal) with FCF/NI divergence and AR mismatch.
    # Accruals-based anomaly is strongest in the 12 months before reporting.
    high_risk = (
        (accrual_ratio_avg is not None and accrual_ratio_avg > _ACCRUAL_RED)
        or fcf_ni_div == "RED"
        or (ar_revenue_divergence == "RED" and dso_trend_val == "RISING")
    )
    medium_risk = (
        (accrual_ratio_avg is not None and accrual_ratio_avg > _ACCRUAL_AMBER)
        or fcf_ni_div == "AMBER"
        or ar_revenue_divergence == "AMBER"
        or cash_conversion_flag == "RED"
    )
    if high_risk:
        pre_earnings_risk = "HIGH"
    elif medium_risk:
        pre_earnings_risk = "MEDIUM"
    else:
        pre_earnings_risk = "LOW"

    # ── Data quality ──────────────────────────────────────────────────────────
    if metrics_computed >= 5:
        data_quality = "FULL"
    elif metrics_computed >= 3:
        data_quality = "PARTIAL"
    else:
        data_quality = "INSUFFICIENT"

    note = (
        f"Computed {metrics_computed}/6 metrics from {len(series)} periods. "
        f"Score={score:.1f}/10. "
        + (f"Flags: {'; '.join(flags[:3])}." if flags else "No red flags detected.")
    )

    return EarningsQualityOutput(
        ticker=ticker,
        accrual_ratio_avg=round(accrual_ratio_avg, 4) if accrual_ratio_avg is not None else None,
        accrual_ratios=[round(r, 4) for r in accrual_ratios[:5]],
        accrual_trend=accrual_trend_val,
        accrual_flag=accrual_flag,
        cash_conversion_ratio=round(cash_conversion_ratio, 3) if cash_conversion_ratio is not None else None,
        cash_conversion_flag=cash_conversion_flag,
        ar_cagr_3y=round(ar_cagr_3y, 4) if ar_cagr_3y is not None else None,
        revenue_cagr_3y=round(revenue_cagr_3y, 4) if revenue_cagr_3y is not None else None,
        ar_revenue_divergence=ar_revenue_divergence,
        dso_values=[round(v, 1) for v in dso_values[:5]],
        dso_trend=dso_trend_val,
        sbc_drag_pct=round(sbc_drag_pct, 2) if sbc_drag_pct is not None else None,
        sbc_drag_flag=sbc_drag_flag,
        fcf_ni_ratios=[round(r, 3) for r in fcf_ni_ratios[:5]],
        fcf_ni_divergence=fcf_ni_div,
        overall_quality_score=round(score, 2),
        quality_verdict=quality_verdict,
        pre_earnings_risk=pre_earnings_risk,
        flags=flags,
        data_quality=data_quality,
        analysis_note=note,
    )


# ── Agent entry point ─────────────────────────────────────────────────────────

def run_earnings_quality_agent(state: AgentState) -> AgentState:
    """
    Phase 2.5 — Earnings Quality Scorer.

    Reads:   state["data"]["tickers"], state["data"]["end_date"]
    Writes:  state["data"]["earnings_quality"][ticker]

    Fetches 5 years of income statement, cash flow, and balance sheet data
    via search_line_items() (same endpoint used by data_router — free tier).
    Falls back to INSUFFICIENT if fewer than 2 periods are available.
    """
    tickers  = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    api_key  = (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    # Fields requested per statement — only what we actually compute
    _INCOME_FIELDS   = ["revenue", "net_income"]
    _CASHFLOW_FIELDS = ["operating_cash_flow", "free_cash_flow", "stock_based_compensation"]
    _BALANCE_FIELDS  = ["total_assets", "accounts_receivable", "accounts_payable"]
    _METRICS_FIELDS  = ["days_sales_outstanding"]

    results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  [EarningsQualityAgent] {ticker} — fetching financials")

        try:
            # ── Fetch raw data ────────────────────────────────────────────────
            income_rows: list[Any] = search_line_items(
                ticker,
                _INCOME_FIELDS,
                end_date=end_date,
                period="annual",
                limit=5,
                api_key=api_key,
            ) or []

            cf_rows: list[Any] = search_line_items(
                ticker,
                _CASHFLOW_FIELDS,
                end_date=end_date,
                period="annual",
                limit=5,
                api_key=api_key,
            ) or []

            bs_rows: list[Any] = search_line_items(
                ticker,
                _BALANCE_FIELDS,
                end_date=end_date,
                period="annual",
                limit=5,
                api_key=api_key,
            ) or []

            km_rows: list[Any] = get_financial_metrics(
                ticker,
                end_date=end_date,
                period="annual",
                limit=5,
                api_key=api_key,
            ) or []

            # ── Merge by period into unified rows, newest first ───────────────
            # search_line_items returns LineItem objects with a .period attribute;
            # get_financial_metrics returns FinancialMetrics objects.
            # We convert each to a plain dict keyed by period string.

            def _to_dict(obj: Any) -> dict:
                if isinstance(obj, dict):
                    return obj
                return obj.model_dump() if hasattr(obj, "model_dump") else vars(obj)

            def _period_key(row: Any) -> str:
                d = _to_dict(row)
                return str(d.get("period") or d.get("date") or d.get("period_end") or "")

            # Build period → merged dict
            merged: dict[str, dict] = {}
            for source in (income_rows, cf_rows, bs_rows):
                for row in source:
                    d    = _to_dict(row)
                    pkey = _period_key(row)
                    if pkey:
                        merged.setdefault(pkey, {}).update(d)

            # Metrics use a different period key structure — merge on date match
            for row in km_rows:
                d    = _to_dict(row)
                pkey = _period_key(row)
                if pkey and pkey in merged:
                    merged[pkey].update(d)
                elif pkey:
                    merged.setdefault(pkey, {}).update(d)

            # Sort newest first, cap at 5 periods
            periods_sorted = sorted(merged.keys(), reverse=True)[:5]
            series = [merged[p] for p in periods_sorted]

            if len(series) < 2:
                print(f"  [EarningsQualityAgent] {ticker} — insufficient data ({len(series)} periods)")
                results[ticker] = EarningsQualityOutput(
                    ticker=ticker,
                    data_quality="INSUFFICIENT",
                    analysis_note=f"Only {len(series)} period(s) available — need ≥ 2 for trend analysis.",
                ).model_dump()
                continue

            # ── Compute all metrics ───────────────────────────────────────────
            output = _compute_earnings_quality(ticker, series)
            results[ticker] = output.model_dump()

            print(
                f"  [EarningsQualityAgent] {ticker} — "
                f"verdict={output.quality_verdict} | score={output.overall_quality_score:.1f}/10 | "
                f"pre_earnings_risk={output.pre_earnings_risk} | "
                f"data_quality={output.data_quality} | flags={len(output.flags)}"
            )

        except Exception as exc:
            print(f"  [EarningsQualityAgent] {ticker} — ERROR: {exc}")
            results[ticker] = EarningsQualityOutput(
                ticker=ticker,
                data_quality="INSUFFICIENT",
                analysis_note=f"Agent error: {exc}",
            ).model_dump()

    state["data"]["earnings_quality"] = results
    return state

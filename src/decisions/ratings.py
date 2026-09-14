"""Research rating vs trade action -- the translation layer.

Two different things were sharing one word. A research RATING is a view
relative to a benchmark over twelve months: Overweight / Neutral /
Underweight. A trade ACTION is an instruction a portfolio or broker can
execute: BUY / HOLD / SELL. Reports now lead with the rating; everything that
executes, logs or scores trades keeps consuming actions through the 1:1 map
below, so legacy code that asks `if action == "BUY"` keeps working.

How a rating is decided
-----------------------
  12-month total shareholder return (TSR)
      = (12m target - price + projected 12m dividend per share) / price

  benchmark expected return = the market's cost of equity
      = risk-free rate + equity risk premium + country risk premium
        (the same Damodaran anchors and per-market CRP the WACC uses)

  excess = TSR - benchmark return
      >= +500 bps  -> Overweight
      <= -500 bps  -> Underweight
      otherwise    -> Neutral

The HEADLINE rating is the tactical one: 12-month TSR vs the benchmark, which
is what the rating definition promises. The STRUCTURAL rating applies the same
test to intrinsic value -- the long-horizon view -- and is shown alongside.

Compliance
----------
A report never publishes an unexplained divergence. When structural and
tactical disagree the report must name the near-term reason; if it cannot
and an earnings date falls inside the review window, the rating is placed
Under Review instead. When the SOTP/NAV value sits far from the 12-month
target, both are disclosed.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

#: Damodaran January 2026 anchors -- the same basis the WACC registry uses.
RISK_FREE_RATE = 0.0395
EQUITY_RISK_PREMIUM = 0.0446
RATING_THRESHOLD = 0.05          # 500 bps
DIVERGENCE_REVIEW_DAYS = 21      # earnings this close -> Under Review
SOTP_DISCLOSURE_GAP = 0.25       # |SOTP - target| / target beyond this -> disclose both


class ResearchRating(str, Enum):
    OVERWEIGHT = "OVERWEIGHT"
    NEUTRAL = "NEUTRAL"
    UNDERWEIGHT = "UNDERWEIGHT"


class TradeAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


RATING_TO_ACTION_MAP: dict[ResearchRating, TradeAction] = {
    ResearchRating.OVERWEIGHT: TradeAction.BUY,
    ResearchRating.NEUTRAL: TradeAction.HOLD,
    ResearchRating.UNDERWEIGHT: TradeAction.SELL,
}

ACTION_TO_RATING_MAP: dict[TradeAction, ResearchRating] = {
    TradeAction.BUY: ResearchRating.OVERWEIGHT,
    TradeAction.HOLD: ResearchRating.NEUTRAL,
    TradeAction.SELL: ResearchRating.UNDERWEIGHT,
}

#: Legacy actions that are not in TradeAction, folded to their direction.
_LEGACY_ACTIONS = {"SHORT": TradeAction.SELL, "COVER": TradeAction.HOLD}

RATING_LABELS = {
    ResearchRating.OVERWEIGHT: "Overweight",
    ResearchRating.NEUTRAL: "Neutral",
    ResearchRating.UNDERWEIGHT: "Underweight",
}

RATING_DEFINITION = (
    "Overweight: expected 12-month total shareholder return exceeds the "
    "benchmark's expected return by at least 500 bps. Underweight: trails it "
    "by at least 500 bps. Neutral: within 500 bps either way. The benchmark's "
    "expected return is its market cost of equity."
)

REGULATORY_DISCLAIMER = (
    "Under regulatory reporting guidelines, Overweight corresponds to Buy, "
    "Neutral to Hold, and Underweight to Sell."
)


def to_action(rating: ResearchRating | str) -> TradeAction:
    return RATING_TO_ACTION_MAP[ResearchRating(rating)]


def to_rating(action: TradeAction | str) -> ResearchRating:
    a = str(getattr(action, "value", action) or "").upper()
    return ACTION_TO_RATING_MAP[_LEGACY_ACTIONS.get(a) or TradeAction(a)]


def normalize_legacy_rating(row: dict) -> dict:
    """A decision as the new schema reads it, without mutating the stored row.

    Runs written before the rating layer carry only `action`; the rating is
    derived from it on read and marked as such, so history stays immutable and
    old and new rows analyse together."""
    out = dict(row or {})
    if out.get("research_rating"):
        return out
    try:
        rating = to_rating(out.get("action"))
    except (KeyError, ValueError):
        return out
    out["research_rating"] = rating.value
    out["rating_label"] = RATING_LABELS[rating]
    out["rating_source"] = "legacy_action"
    return out


# ── benchmark ───────────────────────────────────────────────────────────────

#: (market, tech?) -> (code, name). Tech names are rated against the market's
#: tech index where one exists; everything else against the broad index.
_BENCHMARKS: dict[tuple[str, bool], tuple[str, str]] = {
    ("HKSE", True): ("HSTECH", "Hang Seng Tech Index"),
    ("HKSE", False): ("HSI", "Hang Seng Index"),
    ("SES", False): ("STI", "Straits Times Index"),
    ("US", False): ("SPX", "S&P 500"),
    ("JPX", False): ("N225", "Nikkei 225"),
    ("KSC", False): ("KOSPI", "KOSPI"),
    ("SHH", False): ("CSI300", "CSI 300"),
    ("SHZ", False): ("CSI300", "CSI 300"),
}


def resolve_benchmark(ticker: str, sector: str = "") -> dict:
    """The index this ticker is rated against, and its expected 12-month return."""
    from src.agents.industry.sector_prompts import is_tech_sector
    from src.data.sector_profiles import market_crp, resolve_market

    market = resolve_market(ticker)
    tech = is_tech_sector(sector or "")
    code, name = (_BENCHMARKS.get((market, tech))
                  or _BENCHMARKS.get((market, False))
                  or _BENCHMARKS[("US", False)])
    crp = market_crp(market)
    expected = RISK_FREE_RATE + EQUITY_RISK_PREMIUM + crp
    return {
        "code": code, "name": name, "market": market,
        "expected_return": round(expected, 4),
        "basis": (f"market cost of equity: risk-free {RISK_FREE_RATE:.2%} + "
                  f"equity premium {EQUITY_RISK_PREMIUM:.2%} + country risk {crp:.2%}"),
    }


# ── return and rating ───────────────────────────────────────────────────────

def total_return(price: float, target: Optional[float],
                 dps: Optional[float]) -> Optional[dict]:
    """12-month TSR decomposition, or None when price or target is missing."""
    if not price or price <= 0 or target is None or target <= 0:
        return None
    dividend = float(dps) if dps and dps > 0 else 0.0
    capital = (float(target) - price) / price
    yld = dividend / price
    return {"capital_gain": round(capital, 6), "dividend_yield": round(yld, 6),
            "tsr": round(capital + yld, 6), "dividend_known": dps is not None}


def rate(tsr: float, benchmark_return: float,
         threshold: float = RATING_THRESHOLD) -> ResearchRating:
    excess = round(tsr - benchmark_return, 9)
    if excess >= threshold:
        return ResearchRating.OVERWEIGHT
    if excess <= -threshold:
        return ResearchRating.UNDERWEIGHT
    return ResearchRating.NEUTRAL


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def build_research_view(
    *, ticker: str, sector: str, price: float, price_as_of: Optional[str],
    target_12m: Optional[float], intrinsic_value: Optional[float],
    dps: Optional[float], near_term_catalyst: Optional[str] = None,
    methodology_gap: Optional[str] = None,
    next_earnings: Optional[str] = None, sotp_per_share: Optional[float] = None,
    today: Optional[date] = None,
) -> Optional[dict]:
    """Everything the report header and the trade layer need, decided once.

    When structural and tactical disagree, the explanation is chosen in order:
      1. `near_term_catalyst` -- a real near-term event (material news);
      2. an earnings date inside DIVERGENCE_REVIEW_DAYS -> Under Review, since
         a pending binary event is exactly when a rating should not be pinned;
      3. `methodology_gap` -- the standing bridge between the 12-month and
         long-run methods, stated so the divergence is never silent.

    Returns None when there is no usable 12-month target (the caller keeps its
    no-valuation path)."""
    bench = resolve_benchmark(ticker, sector)
    tactical_tr = total_return(price, target_12m, dps)
    if tactical_tr is None:
        return None
    tactical = rate(tactical_tr["tsr"], bench["expected_return"])
    structural_tr = total_return(price, intrinsic_value, dps)
    structural = (rate(structural_tr["tsr"], bench["expected_return"])
                  if structural_tr else None)

    excess = tactical_tr["tsr"] - bench["expected_return"]
    callout = (
        f"Rating is based on 12-month total shareholder return: capital "
        f"{_pct(tactical_tr['capital_gain'])} + dividend yield "
        f"{_pct(tactical_tr['dividend_yield'])} = {_pct(tactical_tr['tsr'])} TSR, "
        f"against {bench['name']} expected {bench['expected_return']:.1%} "
        f"({excess * 10000:+.0f} bps)."
    )

    notes: list[str] = []
    status = "clean"
    under_review = False
    if structural is not None and structural != tactical:
        status = "explained_divergence"
        if near_term_catalyst:
            notes.append(
                f"Structural view {RATING_LABELS[structural]} vs tactical 12-month "
                f"{RATING_LABELS[tactical]}: {near_term_catalyst}")
        else:
            days = None
            if next_earnings:
                try:
                    days = (date.fromisoformat(next_earnings[:10])
                            - (today or date.today())).days
                except ValueError:
                    days = None
            if days is not None and 0 <= days <= DIVERGENCE_REVIEW_DAYS:
                status = "under_review"
                under_review = True
                notes.append(
                    f"Rating Under Review: structural {RATING_LABELS[structural]} and "
                    f"tactical {RATING_LABELS[tactical]} disagree ahead of earnings on "
                    f"{next_earnings[:10]}.")
            elif methodology_gap:
                notes.append(
                    f"Structural view {RATING_LABELS[structural]} vs tactical 12-month "
                    f"{RATING_LABELS[tactical]}: {methodology_gap}")
            else:
                notes.append(
                    f"Structural view {RATING_LABELS[structural]} vs tactical 12-month "
                    f"{RATING_LABELS[tactical]}: no near-term catalyst was identified; "
                    f"the report must state one.")
    if sotp_per_share and target_12m and target_12m > 0 \
            and abs(sotp_per_share - target_12m) / target_12m > SOTP_DISCLOSURE_GAP:
        notes.append(
            f"SOTP / NAV value {sotp_per_share:,.2f} disclosed alongside the 12-month "
            f"target {target_12m:,.2f}: they differ by "
            f"{abs(sotp_per_share - target_12m) / target_12m:.0%}.")
        if status == "clean":
            status = "holdco_disclosure"

    headline = ResearchRating.NEUTRAL if under_review else tactical
    return {
        "research_rating": headline.value,
        "rating_label": ("Under Review" if under_review else RATING_LABELS[headline]),
        "under_review": under_review,
        "trade_action": to_action(headline).value,
        "tactical_rating": tactical.value,
        "structural_rating": structural.value if structural else None,
        "benchmark": bench,
        "price": round(price, 4), "price_as_of": price_as_of,
        "target_12m": round(float(target_12m), 4),
        "intrinsic_value": (round(float(intrinsic_value), 4)
                            if intrinsic_value else None),
        "capital_gain_12m": tactical_tr["capital_gain"],
        "dividend_yield": tactical_tr["dividend_yield"],
        "dividend_known": tactical_tr["dividend_known"],
        "projected_dps": dps,
        "tsr_12m": tactical_tr["tsr"],
        "excess_return_bps": round(excess * 10000),
        "callout": callout,
        "rating_definition": RATING_DEFINITION,
        "disclaimer": REGULATORY_DISCLAIMER,
        "compliance": {"status": status, "notes": notes},
    }

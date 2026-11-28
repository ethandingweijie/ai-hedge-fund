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
        (owner, 2026-11-27, D1: the valuation's OWN CAPM inputs -- the run's
        firm-CAPM rf, ERP and CRP at beta 1, e.g. SG rf 2.29% on the SGS 10y,
        US 4.94%, ERP 4.41%; the fixed constants below only when a run
        carries no CAPM build)

  The 12-month target carries the full price path, [spot + capture x
  (IV - spot)] x (1 + Ke - dividend yield), so a fairly valued stock's TSR is
  its own cost of equity and is rated Neutral against the market's. The
  structural rating applies the same carry to intrinsic value.

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

import math
from datetime import date
from enum import Enum
from typing import Optional

#: Damodaran January 2026 anchors. Since 2026-11-27 (D1) the FALLBACK only: the benchmark is built from the run's own
#: CAPM inputs (`resolve_benchmark(capm=...)`); these apply when a caller passes none.
RISK_FREE_RATE = 0.0395
EQUITY_RISK_PREMIUM = 0.0446
RATING_THRESHOLD = 0.05          # 500 bps
DIVERGENCE_REVIEW_DAYS = 21      # earnings this close -> Under Review
SOTP_DISCLOSURE_GAP = 0.25       # |SOTP - target| / target beyond this -> disclose both


class ResearchRating(str, Enum):
    OVERWEIGHT = "OVERWEIGHT"
    NEUTRAL = "NEUTRAL"
    UNDERWEIGHT = "UNDERWEIGHT"
    #: No valuation was published (pre-revenue, or too little of the method set
    #: could be computed), so there is no target and no total return to rate.
    #: NOT a fourth opinion: it is the absence of one, and it is never derived
    #: from an action -- ACTION_TO_RATING_MAP deliberately has no route to it.
    UNRATED = "UNRATED"


class TradeAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


RATING_TO_ACTION_MAP: dict[ResearchRating, TradeAction] = {
    ResearchRating.OVERWEIGHT: TradeAction.BUY,
    ResearchRating.NEUTRAL: TradeAction.HOLD,
    ResearchRating.UNDERWEIGHT: TradeAction.SELL,
    # No recommendation: nothing is bought or sold on it. The decision carries
    # HOLD at a zero weight, the same instruction the no-valuation path gives.
    ResearchRating.UNRATED: TradeAction.HOLD,
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
    ResearchRating.UNRATED: "Unrated",
}

UNRATED_DEFINITION = (
    "Unrated: no intrinsic value or 12-month target is published for this company, so no "
    "rating applies. It is not a Neutral view and carries no recommendation."
)

RATING_DEFINITION = (
    "Overweight: expected 12-month total shareholder return exceeds the "
    "benchmark's expected return by at least 500 bps. Underweight: trails it "
    "by at least 500 bps. Neutral: within 500 bps either way. The benchmark's "
    "expected return is its market cost of equity: the risk-free rate, equity "
    "risk premium and country risk premium the valuation itself uses."
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


def build_unrated_view(rating_state: dict, *, price: Optional[float] = None,
                       price_as_of: Optional[str] = None) -> dict:
    """The research view of a name whose valuation was withheld.

    Same keys as `build_research_view`, so every reader that handles a view
    handles this one, with the figures that do not exist set to None. Returned
    instead of None on purpose: None means "a run from before the rating layer",
    and the two must not look alike.
    """
    rs = rating_state or {}
    label = rs.get("label") or RATING_LABELS[ResearchRating.UNRATED]
    reason = rs.get("reason") or "no valuation was published"
    return {
        "research_rating": ResearchRating.UNRATED.value,
        "rating_label": label,
        "under_review": False,
        "material_unreflected": False,
        "valuation_unreconciled": None,
        "trade_action": TradeAction.HOLD.value,
        "tactical_rating": None,
        "structural_rating": None,
        "benchmark": None,
        "price": round(float(price), 4) if price else None, "price_as_of": price_as_of,
        "target_12m": None,
        "intrinsic_value": None,
        "carry": None,
        "capital_gain_12m": None, "dividend_yield": None, "dividend_known": False,
        "projected_dps": None, "tsr_12m": None, "excess_return_bps": None,
        "callout": f"{label}: {reason}. No intrinsic value, 12-month target or rating is published.",
        "rating_definition": UNRATED_DEFINITION,
        "disclaimer": "An unrated company carries no Buy, Hold or Sell recommendation.",
        "compliance": {"status": "unrated", "notes": [reason]},
        "unrated": {k: rs.get(k) for k in ("code", "label", "reason", "weight_surviving")},
    }


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
    # Japan, owner 2026-11-28 (docs/markets/JAPAN.md): TOPIX, the cap-weighted all-Prime index the Japanese
    # buy side benchmarks against (the price-weighted Nikkei 225 is 40%+ in a handful of names). The NAME only: the
    # hurdle itself is rf + ERP + CRP from the run's CAPM build, as for every market.
    ("JPX", False): ("TOPIX", "TOPIX (Tokyo Stock Price Index)"),
    ("KSC", False): ("KOSPI", "KOSPI"),
    ("SHH", False): ("CSI300", "CSI 300"),
    ("SHZ", False): ("CSI300", "CSI 300"),
}


def _capm_inputs(capm: Optional[dict]) -> Optional[tuple[float, float, float]]:
    """(rf, erp, crp) from a run's CAPM build, or None when it is missing or implausible."""
    if not isinstance(capm, dict):
        return None
    try:
        rf, erp = float(capm.get("rf")), float(capm.get("erp"))
        crp = float(capm.get("crp") or 0.0)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in (rf, erp, crp)) or not (0.0 < rf < 0.15 and 0.0 < erp < 0.15
                                                                 and -0.05 < crp < 0.15):
        return None
    return rf, erp, crp


def resolve_benchmark(ticker: str, sector: str = "", capm: Optional[dict] = None) -> dict:
    """The index this ticker is rated against, and its expected 12-month return.

    Owner, 2026-11-27 (D1): expected = rf + ERP + CRP from the valuation's own CAPM inputs (`capm`: the payload's
    wacc_build.firm_capm, else its wacc_build.market_capm), so the hurdle and the cost of capital the valuation
    discounts at stand on one risk-free rate. The fixed Damodaran constants are the fallback only -- they put the SG
    hurdle at 8.91% against a valuation on the 2.29% SGS curve (6.70%), a 221bp Underweight bias, and the US hurdle
    94bp below its own CAPM."""
    from src.agents.industry.sector_prompts import is_tech_sector
    from src.data.sector_profiles import market_crp, resolve_market

    market = resolve_market(ticker)
    tech = is_tech_sector(sector or "")
    code, name = (_BENCHMARKS.get((market, tech))
                  or _BENCHMARKS.get((market, False))
                  or _BENCHMARKS[("US", False)])
    inputs = _capm_inputs(capm)
    if inputs:
        rf, erp, crp = inputs
        src = "the valuation's CAPM inputs"
    else:
        rf, erp, crp = RISK_FREE_RATE, EQUITY_RISK_PREMIUM, market_crp(market)
        if market in ("SHH", "SHZ"):                       # owner, 2026-11-28: the A-share ERP and CGB rf
            from src.data.capm import equity_risk_premium, risk_free
            rf, erp = risk_free("CN")[0], equity_risk_premium("CN")[0]
        src = "fallback constants (no CAPM build on the run)"
    expected = rf + erp + crp
    return {
        "code": code, "name": name, "market": market,
        "expected_return": round(expected, 4),
        "risk_free": round(rf, 5), "equity_risk_premium": round(erp, 5), "country_risk_premium": round(crp, 5),
        "source": src,
        "basis": (f"market cost of equity ({src}): risk-free {rf:.2%} + "
                  f"equity premium {erp:.2%} + country risk {crp:.2%}"),
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


#: Owner, 2026-11-25: weighted valuation legs further apart than this (highest / lowest) cap the rating at Neutral.
LEG_DISPERSION_CAP = 2.5
#: Owner, 2026-11-28 (pre-launch sweep, bridge until D4 b/c land): the report publishes a RANGE instead of a single
#: target only above this ratio. Between LEG_DISPERSION_CAP and this, the single target is shown but the rating stays
#: capped at Neutral (unchanged). RANGE_PUBLICATION_RATIO overrides.
RANGE_PUBLICATION_RATIO = 4.0


def range_publication_ratio() -> float:
    import os
    try:
        v = float(os.environ.get("RANGE_PUBLICATION_RATIO", RANGE_PUBLICATION_RATIO))
        return v if v >= LEG_DISPERSION_CAP else LEG_DISPERSION_CAP
    except (TypeError, ValueError):
        return RANGE_PUBLICATION_RATIO


def unreconciled_legs(dispersion: Optional[dict], ticker: str = "") -> Optional[dict]:
    """The dispersion record when it exceeds LEG_DISPERSION_CAP and the owner has not reviewed it, else None.
    A review (valuation_constants.dispersion_reviewed[ticker], with a reason) lifts the cap for that ticker."""
    if not isinstance(dispersion, dict) or not isinstance(dispersion.get("ratio"), (int, float)):
        return None
    if dispersion["ratio"] <= LEG_DISPERSION_CAP:
        return None
    try:
        from src.data import valuation_constants as _vc
        if ((_vc.load().get("dispersion_reviewed") or {}).get("tickers") or {}).get(ticker):
            return None
    except Exception:                                      # noqa: BLE001
        pass
    return dispersion


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
    today: Optional[date] = None, unreconciled: Optional[dict] = None,
    material_unreflected: Optional[dict] = None,
    capm: Optional[dict] = None, carry: Optional[float] = None,
    user_development: Optional[dict] = None, user_development_review: Optional[dict] = None,
) -> Optional[dict]:
    """Everything the report header and the trade layer need, decided once.

    When structural and tactical disagree, the explanation is chosen in order:
      1. `near_term_catalyst` -- a real near-term event (material news);
      2. an earnings date inside DIVERGENCE_REVIEW_DAYS -> Under Review, since
         a pending binary event is exactly when a rating should not be pinned;
      3. `methodology_gap` -- the standing bridge between the 12-month and
         long-run methods, stated so the divergence is never silent.

    `capm` (D1, owner 2026-11-27): the run's CAPM inputs, so the benchmark is the market cost of equity the
    valuation itself uses. `carry`: the target's 12-month price-path factor (pt_bridge.carry.factor, 1 + Ke -
    dividend yield); the structural test applies it to intrinsic value (IV x carry), so the long-run and
    12-month views are compared on the same horizon. None = 1.0 (a run from before the carry).

    Returns None when there is no usable 12-month target (the caller keeps its
    no-valuation path)."""
    bench = resolve_benchmark(ticker, sector, capm=capm)
    tactical_tr = total_return(price, target_12m, dps)
    if tactical_tr is None:
        return None
    tactical = rate(tactical_tr["tsr"], bench["expected_return"])
    _k = float(carry) if isinstance(carry, (int, float)) and math.isfinite(float(carry)) and 0.5 < float(carry) < 1.5 \
        else 1.0
    structural_tr = total_return(price, (float(intrinsic_value) * _k) if intrinsic_value else intrinsic_value, dps)
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

    # Owner, 2026-11-28: a run made at a reader's request to fold in a verified development says so; a target move
    # past the review threshold holds the rating Under Review until a regular run confirms it.
    if user_development:
        notes.insert(0, (f"This analysis was updated at a reader's request to reflect a verified development: "
                         f"{str(user_development.get('headline') or '')[:160]}"
                         + (f" ({user_development.get('date')})" if user_development.get("date") else "") + "."))
    if user_development_review:
        status = "under_review"
        under_review = True
        notes.insert(0, (f"Rating Under Review: a reader-raised development moved the 12-month target "
                         f"{user_development_review['move']:+.0%} (from {user_development_review['prior']:,.2f} to "
                         f"{user_development_review['new']:,.2f}); it is held until a regular analysis confirms it."))

    # Owner, 2026-11-27: material news the estimates do not yet reflect. The target
    # predates the news, so the rating is not pinned: Under Review, trade HOLD at zero.
    if material_unreflected:
        heads = "; ".join(
            (f"{e.get('date')}: " if e.get("date") else "") + str(e.get("headline") or "")[:100]
            for e in (material_unreflected.get("events") or [])[:3] if e.get("headline"))
        status = "under_review"
        under_review = True
        notes.insert(0, (
            f"Rating Under Review: material news since the prior report"
            + (f" ({heads})" if heads else "")
            + f" is not yet reflected in our estimates; the 12-month target "
            f"{float(target_12m):,.2f} predates it and is not current."))

    # Owner, 2026-11-25 (CFA review item 2): weighted legs that disagree by more than LEG_DISPERSION_CAP average values
    # on incompatible premises; the blended value is still published but carries no conviction, so the rating is
    # capped at Neutral until the outlier leg is fixed or the owner excludes it (valuation_constants
    # dispersion_reviewed).
    capped = False
    if unreconciled and tactical != ResearchRating.NEUTRAL and not under_review:
        capped = True
        status = "valuation_unreconciled"
        notes.append(
            f"Rating capped at Neutral (tactical {RATING_LABELS[tactical]}): valuation unreconciled -- "
            f"{unreconciled.get('hi_leg')} {unreconciled.get('hi'):,.2f} is {unreconciled.get('ratio'):.1f}x "
            f"{unreconciled.get('lo_leg')} {unreconciled.get('lo'):,.2f}; the blend averages values on different "
            f"premises, so it carries no conviction until the outlier leg is resolved.")
    headline = ResearchRating.NEUTRAL if (under_review or capped) else tactical
    return {
        "research_rating": headline.value,
        "rating_label": ("Under Review" if under_review else RATING_LABELS[headline]),
        "under_review": under_review,
        "material_unreflected": bool(material_unreflected),
        "valuation_unreconciled": (dict(unreconciled) if capped else None),
        "trade_action": to_action(headline).value,
        "tactical_rating": tactical.value,
        "structural_rating": structural.value if structural else None,
        "benchmark": bench,
        "price": round(price, 4), "price_as_of": price_as_of,
        "target_12m": round(float(target_12m), 4),
        "intrinsic_value": (round(float(intrinsic_value), 4)
                            if intrinsic_value else None),
        "carry": round(_k, 6),
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

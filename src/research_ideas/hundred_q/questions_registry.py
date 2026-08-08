"""
src/research_ideas/hundred_q/questions_registry.py
======================================================
Declarative registry of the 100-Question screener's questions — the single
source of truth read by both scoring.py (drives quant_fn) and, in a later
phase, the event-trigger mapper (TRIGGER_TO_QUESTIONS).

PHASE 0 SCOPE: only QUANT-tagged questions are registered here. The ~30
QUAL-LLM questions (moat narrative, governance nuance, catalysts — see the
approved plan §1) are intentionally NOT stubbed in yet; they're added in
Phase 1 alongside qualitative.py, per the phased rollout plan. Building
empty placeholders for them now would just be dead code until there's an
LLM layer to fill them in.

Each QUANT question's quant_fn returns (answer, raw_value_str):
  answer     — True/False, or None if the data needed isn't available
               (excluded from the composite's denominator, not scored 0).
  raw_value  — human-readable string of the number(s) behind the check,
               for the audit ledger.

`ctx` passed to every quant_fn:
  {
    "sector_medians": {sector: {field: median_value}},  # computed once per
                                                          # batch across the
                                                          # screening universe
    "risk_free_rate": float,                             # e.g. 0.04
  }

A number of questions from the reconciled plan are marked
`quant_fn=None` (data_unavailable) rather than force-computed from a shaky
proxy — this mirrors the user-approved decision to defer the 6 Pillar-2
debt-structure questions, extended here to a few other quant-tagged
questions across pillars where no clean free-tier FMP/EDGAR field exists
yet (board composition, DCF-gated valuation, SOTP, institutional
ownership, FX exposure, organic-growth decomposition, restatement scan,
cash-conversion-cycle). These are the concrete Phase 5 backlog items.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.data.sector_profiles import get_wacc
from src.research_ideas.hundred_q._calc import (
    bps_stdev,
    cagr,
    clean,
    last,
    latest_yoy_growth,
    safe_div,
    sum_clean,
    wacc_profile_for,
)
from src.research_ideas.hundred_q.data_fetch import HundredQBundle

QuantFn = Callable[[HundredQBundle, dict], tuple[Optional[bool], Optional[str]]]


@dataclass
class QuestionDef:
    question_id: str
    pillar: str
    label: str
    q_type: str = "quant"          # Phase 0: always "quant" (see module docstring)
    quant_fn: Optional[QuantFn] = None
    threshold_desc: str = ""
    deferred_reason: Optional[str] = None   # set when quant_fn is None


def _sector_median(ctx: dict, sector: Optional[str], field: str) -> Optional[float]:
    return (ctx.get("sector_medians", {}).get(sector or "", {}) or {}).get(field)


def _cv(series) -> Optional[float]:
    vals = clean(series)
    if len(vals) < 3:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return (variance ** 0.5) / abs(mean)


# ─────────────────────────────────────────────────────────────────────────
# Pillar 1 — Financial Performance & Quality of Earnings
# ─────────────────────────────────────────────────────────────────────────

def _p1_1(b: HundredQBundle, ctx: dict):
    c = cagr(b.revenue_series)
    if c is None:
        return None, None
    return c > 0.10, f"revenue_cagr={c:.1%}"


def _p1_2(b: HundredQBundle, ctx: dict):
    if b.return_on_equity is None:
        return None, None
    return b.return_on_equity > 0.15, f"roe={b.return_on_equity:.1%}"


def _p1_3(b: HundredQBundle, ctx: dict):
    if b.return_on_invested_capital is None:
        return None, None
    profile = wacc_profile_for(b.sector, b.industry)
    wacc = get_wacc(profile, leverage=b.debt_to_equity or 0.0, macro_regime="neutral")
    return b.return_on_invested_capital > wacc, f"roic={b.return_on_invested_capital:.1%} wacc={wacc:.1%}"


def _p1_4(b: HundredQBundle, ctx: dict):
    stdev_bps = bps_stdev(b.annual_gross_margin_series)
    if stdev_bps is None:
        return None, None
    return stdev_bps < 300, f"gross_margin_stdev_bps={stdev_bps:.0f}"


def _p1_5(b: HundredQBundle, ctx: dict):
    med = _sector_median(ctx, b.sector, "operating_margin")
    if b.operating_margin is None or med is None:
        return None, None
    return b.operating_margin >= med, f"op_margin={b.operating_margin:.1%} sector_median={med:.1%}"


def _p1_6(b: HundredQBundle, ctx: dict):
    fcf3 = sum_clean(b.fcf_series, 3)
    ni3 = sum_clean(b.net_income_series, 3)
    if fcf3 is None or ni3 is None or ni3 <= 0:
        return None, None
    return (fcf3 / ni3) > 0.80, f"fcf3={fcf3:,.0f} ni3={ni3:,.0f} ratio={fcf3/ni3:.1%}"


def _p1_7(b: HundredQBundle, ctx: dict):
    fcf = last(b.fcf_series)
    ni = last(b.net_income_series)
    if fcf is None or ni is None or ni <= 0:
        return None, None
    return (fcf / ni) > 1.0, f"fcf={fcf:,.0f} ni={ni:,.0f} ratio={fcf/ni:.2f}"


def _p1_8(b: HundredQBundle, ctx: dict):
    if not b.total_debt:
        return True, "no_total_debt_on_balance_sheet"
    ocf = last(b.ocf_series)
    if ocf is None:
        return None, None
    ratio = ocf / b.total_debt
    return ratio > 0.30, f"ocf={ocf:,.0f} total_debt={b.total_debt:,.0f} ratio={ratio:.1%}"


def _p1_9(b: HundredQBundle, ctx: dict):
    ar_g = latest_yoy_growth(b.ar_series)
    rev_g = latest_yoy_growth(b.revenue_series)
    if ar_g is None or rev_g is None:
        return None, None
    return ar_g <= rev_g + 0.02, f"ar_growth={ar_g:.1%} revenue_growth={rev_g:.1%}"


def _p1_10(b: HundredQBundle, ctx: dict):
    cv = _cv(b.annual_inventory_turnover_series)
    if cv is None:
        return None, None
    return cv < 0.15, f"inventory_turnover_cv={cv:.1%}"


def _p1_11(b: HundredQBundle, ctx: dict):
    capex = [abs(v) if v is not None else None for v in b.capex_series]
    intensity = [safe_div(c, r) for c, r in zip(capex, b.revenue_series)]
    vals = clean(intensity)
    if len(vals) < 2:
        return None, None
    return vals[-1] <= 1.5 * vals[0], f"capex_intensity_first={vals[0]:.1%} latest={vals[-1]:.1%}"


def _p1_12(b: HundredQBundle, ctx: dict):
    med = _sector_median(ctx, b.sector, "asset_turnover")
    if b.asset_turnover is None or med is None:
        return None, None
    return b.asset_turnover >= med, f"asset_turnover={b.asset_turnover:.2f} sector_median={med:.2f}"


def _p1_14(b: HundredQBundle, ctx: dict):
    sbc = last(b.sbc_series)
    rev = last(b.revenue_series)
    if sbc is None or rev is None or rev <= 0:
        return None, None
    ratio = abs(sbc) / rev
    return ratio < 0.05, f"sbc={abs(sbc):,.0f} revenue={rev:,.0f} ratio={ratio:.1%}"


def _p1_15(b: HundredQBundle, ctx: dict):
    if b.altman_z is None:
        return None, None
    return b.altman_z > 2.9, f"altman_z={b.altman_z:.2f}"


def _p1_17(b: HundredQBundle, ctx: dict):
    opinc_g = latest_yoy_growth(b.operating_income_series)
    rev_g = latest_yoy_growth(b.revenue_series)
    if opinc_g is None or rev_g is None:
        return None, None
    return opinc_g > rev_g, f"op_income_growth={opinc_g:.1%} revenue_growth={rev_g:.1%}"


def _p1_19(b: HundredQBundle, ctx: dict):
    rd = abs(last(b.rd_series) or 0)
    capex = abs(last(b.capex_series) or 0)
    ocf = last(b.ocf_series)
    if ocf is None or ocf <= 0:
        return None, None
    ratio = (rd + capex) / ocf
    return ratio >= 0.20, f"rd_plus_capex={rd+capex:,.0f} ocf={ocf:,.0f} ratio={ratio:.1%}"


PILLAR1: list[QuestionDef] = [
    QuestionDef("P1.1", "P1", "Revenue CAGR (3-5yr) > 10%", quant_fn=_p1_1,
                threshold_desc="5yr revenue CAGR > 10%"),
    QuestionDef("P1.2", "P1", "ROE > 15% (TTM)", quant_fn=_p1_2),
    QuestionDef("P1.3", "P1", "ROIC > WACC", quant_fn=_p1_3),
    QuestionDef("P1.4", "P1", "Gross margin stability (<300bps stdev, 5yr)", quant_fn=_p1_4),
    QuestionDef("P1.5", "P1", "Operating margin >= sector median", quant_fn=_p1_5),
    QuestionDef("P1.6", "P1", "FCF conversion > 80% of NI (3yr trailing)", quant_fn=_p1_6),
    QuestionDef("P1.7", "P1", "FCF/NI > 1.0 (latest FY)", quant_fn=_p1_7),
    QuestionDef("P1.8", "P1", "Operating CF / total debt > 30%", quant_fn=_p1_8),
    QuestionDef("P1.9", "P1", "AR growth <= revenue growth", quant_fn=_p1_9),
    QuestionDef("P1.10", "P1", "Inventory turnover stability (CV < 15%, 5yr)", quant_fn=_p1_10),
    QuestionDef("P1.11", "P1", "CapEx intensity not blown out vs 5yr-ago level", quant_fn=_p1_11),
    QuestionDef("P1.12", "P1", "Asset turnover >= sector median", quant_fn=_p1_12),
    QuestionDef("P1.13", "P1", "No earnings restatements in 5yr", quant_fn=None,
                deferred_reason="needs 8-K Item 4.02 scan (Phase 5)"),
    QuestionDef("P1.14", "P1", "SBC < 5% of revenue", quant_fn=_p1_14),
    QuestionDef("P1.15", "P1", "Altman Z-score > 2.9", quant_fn=_p1_15),
    QuestionDef("P1.16", "P1", "Organic growth >= 80% of revenue growth", quant_fn=None,
                deferred_reason="needs revenue-segmentation continuity check (Phase 5)"),
    QuestionDef("P1.17", "P1", "Operating leverage (opinc growth > revenue growth)", quant_fn=_p1_17),
    QuestionDef("P1.18", "P1", "Cash conversion cycle stability", quant_fn=None,
                deferred_reason="needs cost_of_revenue + inventory balance (Phase 5)"),
    QuestionDef("P1.19", "P1", "R&D + growth CapEx >= 20% of OCF reinvested", quant_fn=_p1_19),
]


# ─────────────────────────────────────────────────────────────────────────
# Pillar 2 — Balance Sheet Strength
# ─────────────────────────────────────────────────────────────────────────

def _p2_1(b: HundredQBundle, ctx: dict):
    if b.net_debt is None or not b.ebitda_latest or b.ebitda_latest <= 0:
        return None, None
    ratio = b.net_debt / b.ebitda_latest
    return ratio < 2.0, f"net_debt/ebitda={ratio:.2f}"


def _p2_2(b: HundredQBundle, ctx: dict):
    if b.interest_coverage is None:
        return None, None
    return b.interest_coverage > 5.0, f"interest_coverage={b.interest_coverage:.1f}x"


def _p2_3(b: HundredQBundle, ctx: dict):
    if b.current_ratio is None:
        return None, None
    return b.current_ratio > 1.5, f"current_ratio={b.current_ratio:.2f}"


def _p2_4(b: HundredQBundle, ctx: dict):
    if b.quick_ratio is None:
        return None, None
    return b.quick_ratio > 1.0, f"quick_ratio={b.quick_ratio:.2f}"


def _p2_7(b: HundredQBundle, ctx: dict):
    if b.net_debt is None:
        return None, None
    return b.net_debt < 0, f"net_debt={b.net_debt:,.0f}"


def _p2_8(b: HundredQBundle, ctx: dict):
    if b.total_assets is None or not b.total_assets:
        return None, None
    gw_intang = (b.goodwill or 0) + (b.intangible_assets or 0)
    ratio = gw_intang / b.total_assets
    return ratio < 0.30, f"goodwill+intangibles={gw_intang:,.0f} total_assets={b.total_assets:,.0f} ratio={ratio:.1%}"


def _p2_12(b: HundredQBundle, ctx: dict):
    divs = abs(last(b.dividends_series) or 0)
    fcf = last(b.fcf_series)
    if fcf is None or fcf <= 0:
        return None, None
    ratio = divs / fcf
    return ratio < 0.60, f"dividends={divs:,.0f} fcf={fcf:,.0f} ratio={ratio:.1%}"


def _p2_13(b: HundredQBundle, ctx: dict):
    rev = last(b.revenue_series)
    opinc = last(b.operating_income_series)
    if rev is None or opinc is None or b.cash_and_equivalents is None:
        return None, None
    annual_opex = rev - opinc
    if annual_opex <= 0:
        return None, None
    return b.cash_and_equivalents >= annual_opex, f"cash={b.cash_and_equivalents:,.0f} annual_opex={annual_opex:,.0f}"


def _p2_14(b: HundredQBundle, ctx: dict):
    nd = clean(b.net_debt_series)
    shares = clean(b.shares_outstanding_series)
    if len(nd) < 2 or len(shares) < 2:
        return None, None
    first_ratio = safe_div(nd[0], shares[0])
    last_ratio = safe_div(nd[-1], shares[-1])
    if first_ratio is None or last_ratio is None:
        return None, None
    return last_ratio <= first_ratio, f"net_debt_per_share first={first_ratio:.2f} latest={last_ratio:.2f}"


def _p2_16(b: HundredQBundle, ctx: dict):
    if not b.ebitda_latest or not b.interest_expense:
        return None, None
    stressed_coverage = (b.ebitda_latest * 0.7) / abs(b.interest_expense)
    return stressed_coverage >= 1.5, f"stressed_interest_coverage={stressed_coverage:.1f}x"


PILLAR2: list[QuestionDef] = [
    QuestionDef("P2.1", "P2", "Net Debt/EBITDA < 2.0x", quant_fn=_p2_1),
    QuestionDef("P2.2", "P2", "Interest coverage > 5.0x", quant_fn=_p2_2),
    QuestionDef("P2.3", "P2", "Current ratio > 1.5x", quant_fn=_p2_3),
    QuestionDef("P2.4", "P2", "Quick ratio > 1.0x", quant_fn=_p2_4),
    QuestionDef("P2.5", "P2", "Debt maturities well-laddered (<20% due in 12mo)", quant_fn=None,
                deferred_reason="needs 10-Q debt-schedule text extraction (Phase 5)"),
    QuestionDef("P2.6", "P2", ">70% of debt fixed-rate or hedged", quant_fn=None,
                deferred_reason="needs 10-Q debt-schedule text extraction (Phase 5)"),
    QuestionDef("P2.7", "P2", "Positive net cash position", quant_fn=_p2_7),
    QuestionDef("P2.8", "P2", "Goodwill + intangibles < 30% of assets", quant_fn=_p2_8),
    QuestionDef("P2.9", "P2", "Pension plan fully funded / negligible", quant_fn=None,
                deferred_reason="needs 10-K pension-note extraction (Phase 5)"),
    QuestionDef("P2.10", "P2", "Undrawn credit lines > 15% of revenue", quant_fn=None,
                deferred_reason="needs 10-K liquidity-note extraction (Phase 5)"),
    QuestionDef("P2.11", "P2", "Off-balance-sheet liabilities < 10% of assets", quant_fn=None,
                deferred_reason="needs 10-K lease/guarantee-note extraction (Phase 5)"),
    QuestionDef("P2.12", "P2", "Dividend payout < 60% of FCF", quant_fn=_p2_12),
    QuestionDef("P2.13", "P2", "Cash cushion >= 12mo of operating expenses", quant_fn=_p2_13),
    QuestionDef("P2.14", "P2", "Net debt per share flat or improving (5yr)", quant_fn=_p2_14),
    QuestionDef("P2.15", "P2", "Average debt maturity > 3yr", quant_fn=None,
                deferred_reason="needs 10-Q debt-schedule text extraction (Phase 5)"),
    QuestionDef("P2.16", "P2", "Interest still covered >=1.5x if EBITDA fell 30%", quant_fn=_p2_16),
]


# ─────────────────────────────────────────────────────────────────────────
# Pillar 3 — Moat & Competitive Position (quant subset only — see module docstring)
# ─────────────────────────────────────────────────────────────────────────

def _p3_17(b: HundredQBundle, ctx: dict):
    med = _sector_median(ctx, b.sector, "gross_margin")
    if b.gross_margin is None or med is None:
        return None, None
    return b.gross_margin >= med, f"gross_margin={b.gross_margin:.1%} sector_median={med:.1%}"


PILLAR3: list[QuestionDef] = [
    QuestionDef("P3.7", "P3", "Customer concentration < 25% (top-5)", quant_fn=None,
                deferred_reason="no reliable free-tier customer-concentration field (Phase 5 / qual overlay)"),
    QuestionDef("P3.17", "P3", "Cost leadership: gross margin >= sector median", quant_fn=_p3_17),
]


# ─────────────────────────────────────────────────────────────────────────
# Pillar 4 — Management & Governance (quant subset only)
# ─────────────────────────────────────────────────────────────────────────

def _p4_2(b: HundredQBundle, ctx: dict):
    if b.insider_ad_ratio_12mo is None:
        return None, None
    return b.insider_ad_ratio_12mo > 0.5, f"insider_ad_ratio_12mo={b.insider_ad_ratio_12mo:.1%}"


def _p4_9(b: HundredQBundle, ctx: dict):
    if not b.guidance_quarters_reported or b.guidance_quarters_reported < 4:
        return None, None
    beat_rate = (b.guidance_beats_last8 or 0) / b.guidance_quarters_reported
    return beat_rate >= 0.75, f"beats={b.guidance_beats_last8}/{b.guidance_quarters_reported}"


def _p4_13(b: HundredQBundle, ctx: dict):
    divs = abs(last(b.dividends_series) or 0)
    buyback = abs(last(b.buyback_series) or 0)
    ocf = last(b.ocf_series)
    if ocf is None:
        return None, None
    returned = divs + buyback
    if returned <= 0:
        return None, None
    return returned <= ocf, f"shareholder_returns={returned:,.0f} ocf={ocf:,.0f}"


PILLAR4: list[QuestionDef] = [
    QuestionDef("P4.1", "P4", "Insider ownership >= 5%", quant_fn=None,
                deferred_reason="no reliable free-tier ownership-% field (Phase 5)"),
    QuestionDef("P4.2", "P4", "Insider buying > selling, trailing 12mo", quant_fn=_p4_2),
    QuestionDef("P4.6", "P4", "Board independence > 50%", quant_fn=None,
                deferred_reason="needs DEF 14A board-table extraction (Phase 5)"),
    QuestionDef("P4.7", "P4", "Chairman/CEO roles separated", quant_fn=None,
                deferred_reason="needs DEF 14A / 10-K cover extraction (Phase 5)"),
    QuestionDef("P4.8", "P4", "Single-class share structure", quant_fn=None,
                deferred_reason="needs 10-K cover-page share-class extraction (Phase 5)"),
    QuestionDef("P4.9", "P4", "Guidance beat rate >= 75% of last 8 quarters", quant_fn=_p4_9,
                threshold_desc="proxy for 'beat streak' — consecutive-streak data isn't reliably available"),
    QuestionDef("P4.13", "P4", "Buybacks + dividends funded by OCF, not debt", quant_fn=_p4_13),
]


# ─────────────────────────────────────────────────────────────────────────
# Pillar 5 — Valuation & Margin of Safety (quant subset only)
# ─────────────────────────────────────────────────────────────────────────

def _p5_1(b: HundredQBundle, ctx: dict):
    avg5 = None
    vals = clean(b.annual_pe_series)
    if vals:
        avg5 = sum(vals) / len(vals)
    if b.price_to_earnings_ratio is None or avg5 is None or avg5 <= 0:
        return None, None
    return b.price_to_earnings_ratio < avg5, f"pe={b.price_to_earnings_ratio:.1f} 5yr_avg={avg5:.1f}"


def _p5_2(b: HundredQBundle, ctx: dict):
    med = _sector_median(ctx, b.sector, "enterprise_value_to_ebitda_ratio")
    if b.enterprise_value_to_ebitda_ratio is None or med is None:
        return None, None
    return b.enterprise_value_to_ebitda_ratio <= med, f"ev_ebitda={b.enterprise_value_to_ebitda_ratio:.1f} sector_median={med:.1f}"


def _p5_4(b: HundredQBundle, ctx: dict):
    if b.free_cash_flow_yield is None:
        return None, None
    threshold = ctx.get("risk_free_rate", 0.04) + 0.03
    return b.free_cash_flow_yield > threshold, f"fcf_yield={b.free_cash_flow_yield:.1%} threshold={threshold:.1%}"


def _p5_5a(b: HundredQBundle, ctx: dict):
    if b.peg_ratio is None or b.peg_ratio <= 0:
        return None, None
    return b.peg_ratio < 1.5, f"peg={b.peg_ratio:.2f}"


def _p5_5b(b: HundredQBundle, ctx: dict):
    if b.peg_ratio is None or b.peg_ratio <= 0:
        return None, None
    return b.peg_ratio < 1.0, f"peg={b.peg_ratio:.2f}"


def _p5_6(b: HundredQBundle, ctx: dict):
    med_pb = _sector_median(ctx, b.sector, "price_to_book_ratio")
    med_roe = _sector_median(ctx, b.sector, "return_on_equity")
    if None in (b.price_to_book_ratio, b.return_on_equity, med_pb, med_roe) or not med_pb or not med_roe:
        return None, None
    rel_pb = b.price_to_book_ratio / med_pb
    rel_roe = b.return_on_equity / med_roe
    return rel_pb <= rel_roe * 1.15, f"rel_pb={rel_pb:.2f} rel_roe={rel_roe:.2f}"


def _p5_8(b: HundredQBundle, ctx: dict):
    med_evs = _sector_median(ctx, b.sector, "enterprise_value_to_revenue_ratio")
    med_opm = _sector_median(ctx, b.sector, "operating_margin")
    if None in (b.enterprise_value_to_revenue_ratio, b.operating_margin, med_evs, med_opm) or not med_evs or not med_opm:
        return None, None
    rel_evs = b.enterprise_value_to_revenue_ratio / med_evs
    rel_opm = b.operating_margin / med_opm
    return rel_evs <= rel_opm * 1.15, f"rel_ev_sales={rel_evs:.2f} rel_op_margin={rel_opm:.2f}"


PILLAR5: list[QuestionDef] = [
    QuestionDef("P5.1", "P5", "Current P/E < 5yr average", quant_fn=_p5_1),
    QuestionDef("P5.2", "P5", "EV/EBITDA <= sector median", quant_fn=_p5_2),
    QuestionDef("P5.3", "P5", "DCF intrinsic value >= 20% above price", quant_fn=None,
                deferred_reason="needs tier-gated run_dcf_agent() call (Phase 5)"),
    QuestionDef("P5.4", "P5", "FCF yield > risk-free rate + 300bps", quant_fn=_p5_4),
    QuestionDef("P5.5a", "P5", "PEG < 1.5x", quant_fn=_p5_5a),
    QuestionDef("P5.5b", "P5", "PEG < 1.0x", quant_fn=_p5_5b),
    QuestionDef("P5.6", "P5", "P/B premium justified by ROE premium vs peers", quant_fn=_p5_6),
    QuestionDef("P5.8", "P5", "EV/Sales premium justified by margin premium vs peers", quant_fn=_p5_8),
    QuestionDef("P5.11", "P5", "SOTP discount (multi-segment names)", quant_fn=None,
                deferred_reason="needs DCF-agent SOTP method, multi-segment only (Phase 5)"),
]


# ─────────────────────────────────────────────────────────────────────────
# Pillar 6 — Catalysts, Risks & Market Dynamics (quant subset only)
# ─────────────────────────────────────────────────────────────────────────

def _p6_3(b: HundredQBundle, ctx: dict):
    if b.short_percent_float is None:
        return None, None
    return b.short_percent_float < 5.0, f"short_pct_float={b.short_percent_float:.1f}%"


def _p6_9(b: HundredQBundle, ctx: dict):
    if b.avg_dollar_volume is None:
        return None, None
    threshold = 5_000_000
    return b.avg_dollar_volume > threshold, f"avg_dollar_volume={b.avg_dollar_volume:,.0f}"


PILLAR6: list[QuestionDef] = [
    QuestionDef("P6.2", "P6", "Institutional ownership 30-70%", quant_fn=None,
                deferred_reason="no free-tier institutional-ownership field wired yet (Phase 5)"),
    QuestionDef("P6.3", "P6", "Short interest < 5% of float", quant_fn=_p6_3),
    QuestionDef("P6.7", "P6", "FX exposure managed / naturally hedged", quant_fn=None,
                deferred_reason="needs geographic-segmentation + FX exposure calc (Phase 5)"),
    QuestionDef("P6.9", "P6", "Trading liquidity sufficient (avg $ volume > $5M/day)", quant_fn=_p6_9),
]
# Note: P6.10 ("thesis fits fund mandate") is manual/static per the approved
# plan — deliberately excluded from this registry, not even as deferred.


REGISTRY: dict[str, QuestionDef] = {
    q.question_id: q
    for q in [*PILLAR1, *PILLAR2, *PILLAR3, *PILLAR4, *PILLAR5, *PILLAR6]
}

# Phase 2 — maps each event trigger_type (fired by src/triggers/detectors.py's
# new_edgar_filing/form4_net_buy/earnings_reported, or the existing
# price_shock) to the EXACT question_ids it should re-score. This is the
# anti-overrun mechanism from the approved plan §4/§5: a Form-4 net-buy
# only re-scores the 2 questions here, never all ~33 qualitative
# questions. IDs may be quant (cheap to just recompute) or qual (routed
# to qualitative.assess_qualitative_pillar) — runner.py's
# run_event_triggered_rescore dispatches each accordingly.
#
# "annual_backstop" is NOT listed here — it's inherently dynamic ("any
# qualitative question whose cache row is >365 days stale", which varies
# per ticker/time), so it's resolved at runtime via
# qualitative.get_stale_qual_questions(ticker) rather than a static list.
TRIGGER_TO_QUESTIONS: dict[str, list[str]] = {
    # Form-4 net-buy > $100k — the plan's own worked example: fires ONLY
    # the insider-narrative qual question, plus a cheap quant A/D recompute.
    "form4_net_buy": ["P4.2", "P4.14"],

    # New 10-K — annual-cadence moat narrative (Pillar 3) + the one
    # static-governance quant field that's only re-checked on a new 10-K.
    "new_10k": [
        "P3.1", "P3.2", "P3.3", "P3.4", "P3.5", "P3.6", "P3.8",
        "P3.10", "P3.13", "P3.14", "P3.15", "P3.18", "P4.8",
    ],

    # New 10-Q — only the questions with genuinely quarterly-disclosed
    # evidence (NRR/churn, switching-cost re-verification).
    "new_10q": ["P3.3", "P3.16"],

    # New DEF 14A — the proxy-sourced governance cluster.
    "new_def14a": ["P4.3", "P4.6", "P4.7", "P4.11", "P4.12", "P4.15"],

    # Price move >= 8% — re-validate valuation-qual + catalyst thesis.
    "price_shock_8pct": ["P5.7", "P5.9", "P6.1"],

    # Earnings just reported — quant guidance-beat recompute + qual
    # estimate-conservatism / catalyst re-check.
    "earnings_reported": ["P4.9", "P5.10", "P6.1"],

    # 8-K Item 5.02 (director/officer change) — exec-turnover + succession.
    "new_8k_5.02": ["P4.4", "P4.12"],

    # 8-K litigation-flagged item — regulatory/litigation qual question.
    "new_8k_litigation": ["P6.4"],
}

"""Industry-specific portfolio-manager write-up (owner, 2026-09-27).

The generic PM agent wrote every thesis in one growth-and-margin frame and, under a rule that
demanded two figures per theme, invented figures the valuation record did not carry (JPM: "a
consensus claim of +45%", "41.7 percentage-point divergence", "ROTCE of 17%"). This module gives the
one writer three things keyed by the profile's report family (`src/data/report_families.py`):

1. `family_addendum(family)`: the house rules and vocabulary of that industry's sell-side desk,
   appended to the system prompt (the bank addendum that already existed is the Banks entry).
2. `family_checklist(...)`: the talking points an industry analyst would open with, computed from
   the valuation record with their numbers, so the writer narrates measured figures instead of
   producing them. Deterministic; no model call.
3. `number_guard(...)`: every number in the draft must appear in the inputs the writer was given
   (anchors, checklist, rating block, research digest, prior report). Offending sentences are named,
   the writer gets one retry, and what still fails is removed before publication.

Focus order set by the owner: energy, aerospace and defence, consumer staples, health care, then
technology. The other families take a shorter addendum and the common checklist.
"""
from __future__ import annotations

import re
from typing import Optional

# ── 1. Family language ──────────────────────────────────────────────────────

_COMMON_FIDELITY = (
    "FIDELITY RULE (every family): every figure you write must appear in the anchors, the family "
    "checklist, the rating block, the research digest or the prior-report context supplied below. "
    "Never compute, extrapolate or recall a number. If a point has no supplied figure, make it "
    "without one. A flag quoted from the inputs is quoted as what it says, not turned into a "
    "finding of your own.\n"
)

FAMILY_ADDENDA: dict[str, str] = {
    "Energy and resources": (
        "ENERGY & RESOURCES DESK RULES (this name is an energy or resources producer):\n"
        "- Frame value on MID-CYCLE earnings: the normalised EV/EBITDA multiple and the window it is "
        "normalised over, and where the current strip sits against mid-cycle. Say whether the engine "
        "is pricing peak, trough or mid-cycle.\n"
        "- Speak in the desk's units: production and realised price, unit costs, EBITDA per barrel or "
        "tonne, capex intensity, free cash flow at strip, net debt to EBITDA, reserve life or PV-10 "
        "coverage where supplied, and the capital-returns framework (base dividend, variable, buyback).\n"
        "- For refiners: crack spreads and utilisation; for midstream: fee-based share of EBITDA and "
        "distribution coverage; for utilities: rate base growth, allowed ROE and the regulatory calendar; "
        "for miners: grade, cost curve position and the commodity price the DCF assumes.\n"
        "- Name the commodity or regime assumption the valuation rests on, and what breaks it.\n"
    ),
    "Industrials, materials and transport": (
        "AEROSPACE, DEFENCE & INDUSTRIALS DESK RULES (this name is an industrial):\n"
        "- Lead with BACKLOG VISIBILITY: backlog, book-to-bill, backlog coverage in years of revenue, "
        "and whether the anchor is the backlog-coverage DCF on an owner-accepted backlog or the "
        "unbounded projection. Say which.\n"
        "- Speak in the desk's units: programme and platform mix, aftermarket versus original equipment, "
        "segment margins and incremental margins, orders, deliveries and production rates, working "
        "capital and free cash conversion, budget and procurement cycle for defence names.\n"
        "- For long-cycle names separate contracted from optional revenue; for cyclicals name the "
        "point in the cycle the normalised EV/EBIT multiple assumes.\n"
        "- Treat a normalised multiple as a stance about the cycle and say what would falsify it.\n"
    ),
    "Consumer": (
        "CONSUMER & STAPLES DESK RULES (this name is a consumer company):\n"
        "- Decompose growth into VOLUME, PRICE and MIX and say which is carrying it; state gross margin "
        "and operating margin direction and the input-cost or promotional driver behind it.\n"
        "- Speak in the desk's units: organic growth, category share, same-store or membership metrics, "
        "private-label pressure, elasticity, payout ratio and dividend growth, and the forward P/E "
        "against the staples basket the engine used.\n"
        "- Where the engine applied a Forward P/E anchor or a dividend model at cost of equity, cite "
        "those inputs as supplied; a quality premium the market pays above the basket is named as a "
        "premium, not explained away.\n"
    ),
    "Health care": (
        "HEALTH CARE DESK RULES (this name is a pharma, biotech, device, services or managed-care company):\n"
        "- State the valuation anchor and its basis in the desk's terms: risk-adjusted NPV of the "
        "pipeline (assets, phase, PTRS, peak sales, loss of exclusivity) and whether that leg is on an "
        "owner-accepted input or quarantined; forward earnings for commercial names; EV/Revenue for "
        "devices; medical loss ratio and operating EPS for managed care; same-facility volumes and "
        "reimbursement for providers.\n"
        "- Speak in the desk's units: franchise concentration, patent cliffs, readout calendar, pricing "
        "and reimbursement exposure, gross-to-net, R&D intensity.\n"
        "- When the Forward P/E sanity gate or a structural flag (for example the managed-care MLR "
        "cycle) is in the inputs, cite it as the engine's own qualification of the number.\n"
    ),
    "Technology, telecom and media": (
        "TECHNOLOGY, TELECOM & MEDIA DESK RULES (this name is a technology, semiconductor, telecom or media company):\n"
        "- Frame value on the FORWARD multiple against the live basket the engine used and state the "
        "terminal convergence the DCF assumes. Growth durability is the thesis: net revenue retention, "
        "bookings or RPO, unit growth, pricing, and the capacity or capex cycle for semis and telcos.\n"
        "- Speak in the desk's units: gross and free-cash-flow margin, stock-based compensation as a "
        "share of revenue and how the engine treated it, net cash, capital return, and for cyclicals "
        "(memory, equipment) the point in the cycle the normalised earnings assume.\n"
        "- A Growth_Inflection_Speculative flag means the market prices a re-rated path; say so and "
        "keep the valuation stance separate from the momentum.\n"
    ),
    "Banks": (
        "BANK-SPECIFIC RULES (this name is a bank):\n"
        "- Use TOTAL INCOME (net interest income + non-interest income) whenever you refer to the "
        "bank's revenue. Never use gross interest income.\n"
        "- Value the bank on P/B against ROE - target P/B = (ROE - g) / (CoE - g) - and on P/E. "
        "NEVER cite EV/EBITDA, EV/Revenue or free cash flow for a bank: deposits are not debt, and "
        "free cash flow is not a meaningful unit of output for a balance-sheet business.\n"
        "- Cover, where the supplied inputs support it: NIM and its direction, cost-income ratio, "
        "credit cost in bps against the guided range, NPL ratio and coverage, CET1 against target, "
        "and the capital-return plan (dividend per share, yield, buyback).\n"
        "- Separate the two halves of the earnings engine: rate-driven net interest income versus "
        "fee-driven non-interest income (wealth, insurance, treasury). State which is carrying "
        "growth, and whether that is sustainable.\n"
        "- Where management guidance is supplied, cite it alongside our own estimate so the reader "
        "can see the gap.\n"
    ),
    "Insurance": (
        "INSURANCE DESK RULES: value P&C on book against the return on it and the combined ratio; "
        "value life on price to embedded value with the value of new business, and say whether the "
        "embedded-value leg is on an accepted input or quarantined. Never cite EV/EBITDA or free cash "
        "flow for an insurer; reserves are not debt.\n"
    ),
    "Fee financials": (
        "FEE-BUSINESS DESK RULES: value on the earnings the market prices (distributable earnings and "
        "fee-related earnings for alternative managers, forward earnings and EBITDA for exchanges, "
        "brokers, data and payments); flows, volumes and operating leverage are the thesis.\n"
    ),
    "Property, REITs and holdcos": (
        "PROPERTY & HOLDCO DESK RULES: value on net asset value and the discount to it, distributions "
        "and gearing; for a holding company name the parts and whether the look-through is on an "
        "accepted input.\n"
    ),
}


def family_skeleton(family: Optional[str]) -> list[str]:
    """The theme order the family's desk writes in (declared in the report-family registry)."""
    try:
        from src.data.report_families import REPORT_FAMILIES
        return list((REPORT_FAMILIES.get(family or "") or {}).get("skeleton") or [])
    except Exception:                                      # noqa: BLE001
        return []


def family_skeleton_text(family: Optional[str]) -> str:
    sk = family_skeleton(family)
    if not sk:
        return ""
    return ("THESIS SKELETON (write the themes in this order; drop a theme only when the inputs carry "
            "nothing for it): " + "; ".join(f"{i}. {t}" for i, t in enumerate(sk, 1)) + ".\n")


def family_addendum(family: Optional[str]) -> str:
    """The desk rules appended to the system prompt for this family (always with the fidelity rule
    and the family's thesis skeleton)."""
    return _COMMON_FIDELITY + (FAMILY_ADDENDA.get(family or "") or "") + family_skeleton_text(family)


_RATING_WORDS = ("overweight", "neutral", "underweight", "buy", "sell", "hold", "short", "unrated")


def skeleton_check(rationale: str, family: Optional[str]) -> dict:
    """Did the draft follow the family skeleton? Theme count against the skeleton and whether theme 1
    opens with the rating. Observation for the record; it does not fail the draft."""
    sk = family_skeleton(family)
    themes = [t.strip() for t in re.split(r"(?:^|\n)\s*\d+[.)]\s+", rationale or "") if t.strip()]
    first = (themes[0].lower() if themes else "")
    return {"family": family, "skeleton_len": len(sk), "themes": len(themes),
            "enough_themes": (len(themes) >= min(3, len(sk))) if sk else True,
            "opens_with_rating": any(w in first for w in _RATING_WORDS) if themes else False}


# ── 4. Signals versus rating ────────────────────────────────────────────────

_BULL = {"BULLISH", "ACCELERATING_UP", "ACCELERATING UP", "IMPROVING", "UP"}
_BEAR = {"BEARISH", "ACCELERATING_DOWN", "ACCELERATING DOWN", "DETERIORATING", "DOWN"}


def signals_reconciliation(action: Optional[str], rating_label: Optional[str],
                           news_signal: Optional[str] = None, revision_direction: Optional[str] = None,
                           insider_signal: Optional[str] = None) -> Optional[str]:
    """One computed sentence when the momentum signals run against the valuation-driven rating;
    None when they agree or nothing is known. No model involvement."""
    a = (action or "").upper(); r = (rating_label or "").lower()
    bearish_call = a in ("SELL", "SHORT") or "underweight" in r
    bullish_call = a in ("BUY", "COVER") or "overweight" in r
    if not (bearish_call or bullish_call):
        return None
    sig = {"news sentiment": (news_signal or "").upper().replace("_", " "),
           "analyst revisions": (revision_direction or "").upper().replace("_", " "),
           "insider activity": (insider_signal or "").upper().replace("_", " ")}
    against = [f"{k} {v.lower()}" for k, v in sig.items()
               if v and ((bearish_call and v in {x.replace("_", " ") for x in _BULL})
                         or (bullish_call and v in {x.replace("_", " ") for x in _BEAR}))]
    if not against:
        return None
    return (f"Momentum signals ({', '.join(against)}) run against this valuation-driven "
            f"{'Underweight' if bearish_call else 'Overweight'} rating; the rating is a 12-month "
            "total-return call on the valuation, not a momentum call.")


# ── 2. Family checklist from the valuation record ───────────────────────────

def _f(v, nd=2):
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(v, nd=1):
    try:
        return f"{float(v) * 100:+.{nd}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _leg(dr: dict, name: str) -> Optional[dict]:
    li = ((dr.get("base") or {}).get("leg_inputs") or {}).get(name)
    return li if isinstance(li, dict) else None


def _flags(dr: dict, *needles: str) -> list[str]:
    out = []
    for f in ((dr.get("base") or {}).get("forward_flags") or []):
        if any(n.lower() in str(f).lower() for n in needles):
            out.append(str(f))
    return out


def _gate(dr: dict, gate_id: str) -> Optional[dict]:
    for g in dr.get("gate_evaluations") or []:
        if g.get("gate_id") == gate_id:
            return g
    return None


def family_checklist(family: Optional[str], dr: Optional[dict], scenario: Optional[dict] = None,
                     sector_kpis: Optional[dict] = None, sym: str = "$") -> list[str]:
    """Talking points with their numbers, read from the valuation record. Every line is something
    an analyst on that desk would state first. Nothing is estimated here; a missing input gives no line."""
    dr = dr or {}
    b = dr.get("base") or {}
    table = b.get("method_iv_table") or {}
    pb = dr.get("pt_bridge") or {}
    spot = pb.get("spot") or (scenario or {}).get("current_price")
    lines: list[str] = []

    # ── common: the anchor, where it sits, and the engine's own qualifications ──
    anchor = dr.get("anchor_method")
    if anchor:
        av = table.get(anchor)
        used = anchor in (b.get("methods_used") or [])
        if isinstance(av, (int, float)):
            lines.append(f"Anchor {anchor} = {sym}{_f(av)} per share ({'in the blend' if used else 'NOT in the blend'})"
                         + (f"; blended IV {sym}{_f(b.get('intrinsic_value'))}" if b.get("intrinsic_value") else ""))
        else:
            lines.append(f"Anchor {anchor} did not compute this run; the blend renormalised over {b.get('methods_used')}")
    for x in (b.get("legs_dropped") or []):
        if isinstance(x, dict) and x.get("weight"):
            lines.append(f"Leg {x.get('method')} (w={x.get('weight')}) dropped: {x.get('reason')}")
    if isinstance(dr.get("consensus_spread"), (int, float)):
        lines.append(f"Consensus target sits {_pct(dr['consensus_spread'], 0)} from spot"
                     + (f"; regime flag {dr['regime_flag']}" if dr.get("regime_flag") else ""))
    for f in _flags(dr, "quarantined", "sanity gate", "Structural:", "implied CoE", "SOTP precedence", "leg_fallback",
                    "Degraded", "cross-check"):
        lines.append(f"Engine flag: {f[:220]}")

    # ── per family ──
    if family == "Energy and resources":
        for name in ("EV/EBITDA (norm)", "EV/EBIT (norm)", "FCF Yield", "EV/OCF", "DDM", "P/NAV", "SOTP (segments)"):
            li = _leg(dr, name)
            if li and isinstance(li.get("value"), (int, float)):
                mp = li.get("multiple_parts") or {}
                lines.append(f"{name} leg {sym}{_f(li['value'])}"
                             + (f" on {_f(li.get('multiple'))}x ({mp.get('peer_source') or 'engine'})" if li.get("multiple") else "")
                             + (f", target yield {_pct(li.get('target_yield'))}" if li.get("target_yield") else ""))
        for f in _flags(dr, "Normalized", "normalis", "mid-cycle", "peak trigger", "PV-10", "reserve"):
            lines.append(f"Cycle: {f[:200]}")
        dcf = _leg(dr, "DCF")
        if dcf and dcf.get("fcf_margin_base") is not None:
            lines.append(f"DCF base: FCF margin {_pct(dcf.get('fcf_margin_base'))}, growth {_pct(dcf.get('growth_base'))}, WACC {_pct(dcf.get('wacc'))}")
    elif family == "Industrials, materials and transport":
        g = _gate(dr, "GATE_BACKLOG_VISIBILITY")
        if g:
            cov = g.get("gated_output_path_b")
            lines.append("Backlog visibility: " + (f"accepted backlog = {_f(cov, 2)} years of revenue ({g.get('basis')})"
                                                   if isinstance(cov, (int, float)) else str(g.get("basis"))))
        g2 = _gate(dr, "GATE_LONG_CYCLE_ELIGIBILITY")
        if g2:
            lines.append(f"Long-cycle eligibility: {g2.get('basis')}")
        for name in ("Backlog-coverage DCF", "EV/EBIT (norm)", "EV/EBITDA (norm)", "PEG", "Forward P/E"):
            li = _leg(dr, name)
            if li and isinstance(li.get("value"), (int, float)):
                mp = li.get("multiple_parts") or {}
                lines.append(f"{name} leg {sym}{_f(li['value'])}" + (f" on {_f(li.get('multiple'))}x ({mp.get('peer_source') or 'engine'})" if li.get("multiple") else ""))
        for f in _flags(dr, "Product segments", "peak trigger", "mid-cycle", "Normalized"):
            lines.append(f"Mix and cycle: {f[:200]}")
    elif family == "Consumer":
        for name in ("Forward P/E", "P/E (norm)", "EV/EBITDA (norm)", "DDM", "FCF Yield", "EV/EBITDA"):
            li = _leg(dr, name)
            if li and isinstance(li.get("value"), (int, float)):
                mp = li.get("multiple_parts") or {}
                extra = ""
                if name == "DDM" and isinstance(li.get("assumptions"), dict):
                    a = li["assumptions"]; extra = f" (cost of equity {_pct(a.get('cost_of_equity'))}, {a.get('source') or ''})"
                lines.append(f"{name} leg {sym}{_f(li['value'])}"
                             + (f" on {_f(li.get('multiple'))}x ({mp.get('peer_source') or 'engine'})" if li.get("multiple") else "") + extra)
        dcf = _leg(dr, "DCF")
        if dcf and dcf.get("growth_base") is not None:
            lines.append(f"Growth base {_pct(dcf.get('growth_base'))}, FCF margin base {_pct(dcf.get('fcf_margin_base'))}")
        for f in _flags(dr, "Deep Value", "premium", "Normalized NI"):
            lines.append(f"Multiple context: {f[:200]}")
    elif family == "Health care":
        for f in _flags(dr, "rNPV", "pipeline", "Pipeline"):
            lines.append(f"Pipeline leg: {f[:220]}")
        g = _gate(dr, "GATE_FORWARD_PE_SANITY")
        if g:
            lines.append(f"Forward P/E sanity gate fired: forward P/E at spot {g.get('raw_input_path_a')}x; weight rolled {g.get('gated_output_path_b')}")
        for name in ("Forward P/E", "EV/Fwd Rev", "EV/Revenue", "P/E (Ops)", "P/E (ops)", "P/E", "EV/EBITDA", "rNPV (Pipeline)"):
            li = _leg(dr, name)
            if li and isinstance(li.get("value"), (int, float)):
                mp = li.get("multiple_parts") or {}
                lines.append(f"{name} leg {sym}{_f(li['value'])}" + (f" on {_f(li.get('multiple'))}x ({mp.get('peer_source') or 'engine'})" if li.get("multiple") else ""))
        for f in _flags(dr, "Product segments"):
            lines.append(f"Franchise mix: {f[:200]}")
    elif family == "Technology, telecom and media":
        for name in ("Forward P/E", "EV/NTM Revenue", "EV/Fwd Rev", "EV/EBITDA", "EV/Revenue", "P/E (norm)", "NRR-adj DCF", "DCF (FCF+)"):
            li = _leg(dr, name)
            if li and isinstance(li.get("value"), (int, float)):
                mp = li.get("multiple_parts") or {}
                lines.append(f"{name} leg {sym}{_f(li['value'])}" + (f" on {_f(li.get('multiple'))}x ({mp.get('peer_source') or 'engine'}"
                                                                       + (f", growth premium {_f(mp.get('growth_premium'), 2)}" if mp.get("growth_premium") else "") + ")" if li.get("multiple") else ""))
        for f in _flags(dr, "SBC drag", "SBC", "terminal", "convergence", "Growth_Inflection", "CAGR-divergence"):
            lines.append(f"Growth and SBC: {f[:200]}")
        dcf = _leg(dr, "DCF")
        if dcf and dcf.get("growth_base") is not None:
            lines.append(f"DCF base: growth {_pct(dcf.get('growth_base'))}, FCF margin {_pct(dcf.get('fcf_margin_base'))}, TGR {_pct(dcf.get('tgr'))}")
    elif family == "Banks":
        bb = dr.get("bank_breakdown") or {}
        ggm = _leg(dr, "GGM (P/B)")
        a = (ggm or {}).get("assumptions") or {}
        if a:
            lines.append(f"GGM: RoTE {_pct(a.get('roe'))}, CoE {_pct(a.get('coe'))}, g {_pct(a.get('g'))} -> target P/B {_f(ggm.get('target_pb'), 2)}x"
                         + (f"; TBV/share {sym}{_f(bb.get('tbv_per_share'))}" if bb.get("tbv_per_share") else ""))
        if isinstance(spot, (int, float)) and bb.get("tbv_per_share"):
            lines.append(f"Spot P/TBV {_f(float(spot) / float(bb['tbv_per_share']), 2)}x")
        for f in _flags(dr, "Bank metrics", "Bank growth base", "NIM", "CET1"):
            lines.append(f"Bank: {f[:220]}")
    if sector_kpis:
        kp = ", ".join(f"{k} {v}" for k, v in list(sector_kpis.items())[:8] if v is not None)
        if kp:
            lines.append(f"Sector KPIs (extracted): {kp}")
    return lines


def checklist_block(family: Optional[str], lines: list[str]) -> str:
    if not lines:
        return f"Family checklist ({family or 'Operating company'}): (no record lines)"
    return f"Family checklist ({family or 'Operating company'}) -- narrate these, in this order where they apply:\n" + "\n".join(f"  - {l}" for l in lines)


# ── 3. The number guard ─────────────────────────────────────────────────────

_NUM_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        s = m.group(0).replace(",", "").lstrip("+")
        try:
            out.append(abs(float(s)))
        except ValueError:
            continue
    return out


def _allowed(v: float, pool: list[float]) -> bool:
    if v <= 12 and float(v).is_integer():          # theme numbers, small counts, "3 themes"
        return True
    if 1990 <= v <= 2100 and float(v).is_integer():  # years
        return True
    tol = max(0.006 * v, 0.006)
    if any(abs(v - p) <= tol for p in pool):
        return True
    # the same figure at a different scale (bn vs mn, % vs decimal): 1e3 / 1e6 / 1e9 and x100
    for k in (100.0, 1e3, 1e6, 1e9):
        if any(abs(v * k - p) <= max(0.006 * p, 0.006) or abs(v / k - p) <= max(0.006 * p, 0.006) for p in pool):
            return True
    return False


def number_guard(rationale: str, inputs_text: str) -> dict:
    """Which numbers in the draft are not in the inputs, and the sentences that carry them."""
    pool = numbers_in(inputs_text)
    sentences = re.split(r"(?<=[.;!?])\s+|\n", rationale or "")
    offenders, bad_sentences = [], []
    for s in sentences:
        bad = [n for n in numbers_in(s) if not _allowed(n, pool)]
        if bad:
            offenders.extend(bad); bad_sentences.append(s.strip())
    return {"ok": not offenders, "offending_numbers": offenders, "offending_sentences": bad_sentences,
            "checked": len(numbers_in(rationale or ""))}


def strip_offending(rationale: str, verdict: dict) -> str:
    """Remove the sentences the guard named; keep the theme numbering readable."""
    if verdict.get("ok"):
        return rationale
    bad = set(verdict.get("offending_sentences") or [])
    kept = []
    for line in (rationale or "").split("\n"):
        parts = re.split(r"(?<=[.;!?])\s+", line)
        keep = [p for p in parts if p.strip() and p.strip() not in bad]
        if keep and not re.fullmatch(r"\s*\d+[.)]?\s*", " ".join(keep)):   # a theme number left on its own goes too
            kept.append(" ".join(keep))
    return "\n".join(kept)


def retry_instruction(verdict: dict) -> str:
    nums = ", ".join(f"{n:g}" for n in verdict.get("offending_numbers") or [])
    return ("\nREWRITE: the previous draft cited figures that are not in the supplied inputs: "
            f"{nums}. Remove them or replace them with figures that appear verbatim in the anchors, "
            "the family checklist, the rating block or the research digest. Do not add new figures.\n")

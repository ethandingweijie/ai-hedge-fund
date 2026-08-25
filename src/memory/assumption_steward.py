"""
src/memory/assumption_steward.py
================================
Workstream R3 — the Assumption Steward: recursive learning over the R1
ledger (assumption_store). Keeps assumptions updated, challenges them
when anomalous, and feeds valuation quantitatively (flags) and
qualitatively (context blocks).

Doctrine (user directives 2026-08-24):
  * Price targets are IGNORED by the recursive layer — the steward
    tracks/challenges/projects ONLY line-item drivers. PTs/ratings stay
    in R1 analyst_reports for display; a PT is the OUTPUT of the drivers
    we actually want to learn.
  * Stress testing isolates the VARIANT DRIVERS: the 1-2 variables where
    our research/extractors diverge materially from house + consensus.
  * LLM calls only where deterministic flags fire (bounded cost — one
    bundled challenge-reading call per flagged ticker, Q1 discipline).

Layers:
  1. Deterministic detectors (no LLM): divergence bands, direction
     reversals, cross-document theme divergence, earnings quality,
     margin compression, scorecard weighting.
  2. ONE structured LLM challenge-reading call per flagged ticker
     (qwen3.6-plus, bundle discipline from assumption_extract).
  3. Recursive scoring: after each earnings print, prior predictions vs
     reported actuals -> assumption_scorecard (the learning memory).

Kill switch: ASSUMPTION_STEWARD=false -> every entry point is a no-op;
R1 rows flow unchanged, no challenges, no flags, no Watch blocks.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Divergence bands (per-metric) ────────────────────────────────────────────
# Beyond-band disagreement between management guidance, house estimates and
# FMP consensus is a challenge-worthy divergence.
_BAND_GROWTH_PP = 5.0        # growth-rate divergence, percentage points
_BAND_EBITDA_PCT = 10.0      # EBITDA level divergence, percent of guidance
_BAND_EPS_PCT = 10.0         # EPS level divergence, percent
_LOW_TRACK_RATE = 0.4        # source hit-rate below this -> low_track_record
_MIN_SCORECARD_N = 3         # hit-rate weighting needs at least this many rows
_EPS_MATCH_BAND = 0.10       # scorecard: predicted vs actual within 10% = hit


def steward_enabled() -> bool:
    return os.environ.get("ASSUMPTION_STEWARD", "true").strip().lower() \
        not in ("0", "false", "no", "off", "")


# ══════════════════════════════════════════════════════════════════════════
# Monitor spec — the recursive-learning fields to extract / track / challenge
# per ticker (user directive 2026-08-24). Drives (a) extraction focus,
# (b) which version-tracked field_keys are projection candidates,
# (c) the valuation-method anchors to cross-check per company.
# ══════════════════════════════════════════════════════════════════════════

_MONITOR_SPECS: dict[str, dict] = {
    "AMZN": {
        "drivers": [
            "AWS revenue growth (YoY run-rate, backlog consumption, AI "
            "workload contribution)",
            "e-commerce GMV & 3P services (paid-unit growth, ad attach rates)",
        ],
        "margins": [
            "AWS op margin (chip amortization, server useful-life accounting)",
            "retail regionalization (fulfillment/shipping cost per unit leverage)",
        ],
        "methods": [
            "SOTP (high EV/EBITDA on AWS + low on retail)",
            "EV/EBITDA & DCF on long-term FCF margin targets",
        ],
    },
    "META": {
        "drivers": [
            "ad impressions vs price/ad (FoA YoY)",
            "AI engagement (time spent via Reels/recommendations)",
            "WhatsApp click-to-message monetization ramp",
        ],
        "margins": [
            "capex guidance & depreciation (AI data centers)",
            "Reality Labs burn run-rate vs FoA margin cushion",
        ],
        "methods": [
            "Forward P/E (NTM, peer-adjusted vs Alphabet)",
            "DCF (terminal growth + long-term op margin ~35-40%)",
        ],
    },
    "BABA": {
        "drivers": [
            "CMR (Taobao/Tmall GMV conversion + take rate)",
            "Cloud Intelligence (public cloud vs low-margin project/hybrid mix)",
            "AIDC (Choice/AliExpress order growth)",
        ],
        "margins": [
            "AIDC loss narrowing -> projected breakeven quarter",
            "Taobao/Tmall margin dilution (UX reinvestment, low-price subsidies)",
            "net buyback/dividend yield",
        ],
        "methods": [
            "SOTP (Taobao/Tmall single-digit P/E + net cash/investments)",
            "adjusted P/E & FCF yield with China equity risk premium",
        ],
    },
    "JD": {
        "drivers": [
            "1P electronics/home appliances (replacement cycles, national "
            "trade-in subsidies)",
            "3P general merchandise (active merchant count, commission growth)",
            "JD Logistics external-customer revenue share",
        ],
        "margins": [
            "gross-margin expansion from 3P/1P mix shift",
            "fulfillment cost per order",
            "subsidy impact on adj. op margin",
        ],
        "methods": [
            "Target P/E & EV/EBITDA vs historical bands (8-12x NTM P/E)",
            "SOTP (JD Retail + JD Logistics stake + net cash/share)",
        ],
    },
    "3690.HK": {
        "drivers": [
            "food-delivery order volume & AOV",
            "In-Store/Hotel/Travel GTV share vs Douyin",
            "Keeta international milestones",
        ],
        "margins": [
            "delivery op profit per order (subsidies/order, rider cost leverage)",
            "in-store op margin (take rate vs merchant retention spend)",
            "Select/new-initiative loss-reduction pace",
        ],
        "methods": [
            "SOTP / segment EV/EBITDA (core local commerce separate from "
            "new initiatives)",
            "forward P/E on core stripping loss units",
        ],
    },
    "MSFT": {
        # Segment-structured spec (user directive): each segment carries its
        # own metrics + why-analysts-track-it note.
        "segments": {
            "Intelligent Cloud": {
                "metrics": [
                    "Azure constant-currency YoY growth — split BASE cloud "
                    "consumption vs AI-services contribution (pp of growth)",
                    "Commercial RPO: total backlog + % recognized within "
                    "next 12 months",
                ],
                "why": ("enterprise cloud adoption, infrastructure capacity "
                        "limits, direct return on AI capex"),
            },
            "Productivity & Business Processes": {
                "metrics": [
                    "M365 Commercial Cloud seat growth & ARPU (paid-seat "
                    "expansion vs E3->E5 pricing-mix upgrades)",
                    "M365 Copilot attach rate (paid adoption, $30/user/mo "
                    "pricing, enterprise penetration)",
                    "LinkedIn & Dynamics 365 growth (ERP/CRM share retention)",
                ],
                "why": ("software pricing power, gen-AI monetization at the "
                        "application layer, recurring-subscription durability"),
            },
            "More Personal Computing": {
                "metrics": [
                    "Windows OEM growth (PC replacement cycles, AI-PC "
                    "shipment trends)",
                    "Gaming — Game Pass subscription growth + content/"
                    "hardware mix (Xbox + Activision Blizzard)",
                    "Search & News advertising (Bing share gains/losses)",
                ],
                "why": ("cyclical/consumer health; lower-multiple segment — "
                        "steward falls back to segment SOTP convention"),
            },
        },
        "methods": ["segment SOTP", "DCF on operating margins per segment"],
    },
}

# ── Sector-level base templates (3-layer blueprint, layer 2) ────────────────
# Shared extraction schema per business model; a ticker can map to SEVERAL
# templates (AMZN: cloud + e-commerce).
_SECTOR_TEMPLATES: dict[str, dict] = {
    "cloud_saas": {
        "tickers": ["MSFT", "AMZN"],
        "focus": [
            "constant-currency growth", "RPO / backlog",
            "commercial cloud gross margins", "capex/depreciation splits",
        ],
    },
    "ecommerce_marketplace": {
        "tickers": ["BABA", "JD", "AMZN"],
        "focus": [
            "GMV", "take rates", "1P vs 3P mix", "fulfillment cost per order",
        ],
    },
    "digital_advertising": {
        "tickers": ["META"],
        "focus": ["ad impressions", "price per ad", "regional ARPU"],
    },
    "local_services_superapp": {
        "tickers": ["3690.HK"],
        "focus": [
            "GTV", "order volumes", "AOV", "new-initiative loss run-rates",
        ],
    },
}


def monitor_spec(ticker: str) -> Optional[dict]:
    return _MONITOR_SPECS.get((ticker or "").upper())


def templates_for(ticker: str) -> list[dict]:
    tkr = (ticker or "").upper()
    return [dict(t, template=name) for name, t in _SECTOR_TEMPLATES.items()
            if tkr in t.get("tickers", [])]


def focus_fields(ticker: str) -> list[str]:
    """Layer-3 focus list injected into extraction prompts: the monitor
    spec's drivers/margins (+ segment metrics) plus every template focus.
    Lightweight by design — tag lists, not per-company prose prompts."""
    tkr = (ticker or "").upper()
    out: list[str] = []
    spec = monitor_spec(tkr)
    if spec:
        out.extend(spec.get("drivers") or [])
        out.extend(spec.get("margins") or [])
        for seg in (spec.get("segments") or {}).values():
            out.extend(seg.get("metrics") or [])
    for tmpl in templates_for(tkr):
        out.extend(tmpl.get("focus") or [])
    seen: set[str] = set()
    uniq = []
    for f in out:
        if f and f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


# ══════════════════════════════════════════════════════════════════════════
# Parsing helpers
# ══════════════════════════════════════════════════════════════════════════

_FY4_RE = re.compile(r"((?:19|20)\d{2})")
# Trailing (?!\d) instead of \b: GS labels like "FY27E" have a word-char
# suffix (E = estimate) where \b would refuse to match.
_FY2_RE = re.compile(r"\bFY\s*['’]?(\d{2})(?!\d)", re.IGNORECASE)
_FYPLUS_RE = re.compile(r"FY\s*\+\s*(\d)", re.IGNORECASE)


def _parse_year_label(label, base_fy: int | None = None) -> Optional[int]:
    """'FY2027' | '2027' | 'FY27E' | 'FY+1' -> fiscal year int.
    FY+n arithmetic needs base_fy (the reported fiscal year)."""
    s = str(label or "")
    if not s:
        return None
    m = _FYPLUS_RE.search(s)
    if m and base_fy:
        return int(base_fy) + int(m.group(1))
    m = _FY4_RE.search(s)
    if m:
        return int(m.group(1))
    m = _FY2_RE.search(s)
    if m:
        return 2000 + int(m.group(1))
    return None


def _parse_pct(text) -> Optional[float]:
    """'+45%' | '45' | '0.45' -> 45.0 (percent). None when unparseable."""
    if text is None:
        return None
    s = str(text).strip().replace("+", "")
    if not s:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|pp|ppt)?", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) is None and abs(v) <= 1.5 and "." in s:
        v *= 100.0  # bare decimal fraction -> percent
    return v


def _parse_amount(text) -> Optional[float]:
    try:
        from src.memory.assumption_extract import parse_amount
        return parse_amount(text)
    except Exception:
        return None


def _norm_direction(d) -> str:
    """Loose LLM/free-text directions -> 'up' | 'down' | ''.
    Live extraction emits 'Increased to 12%' / 'Decreased to 6%' (6-K
    margin language) — the increas/decreas stems are load-bearing."""
    s = str(d or "").lower()
    if any(w in s for w in ("up", "raise", "lift", "hike", "increas",
                            "expand", "higher", "grew")):
        return "up"
    if any(w in s for w in ("down", "cut", "lower", "trim", "reduc",
                            "decreas", "compress", "narrow", "decline")):
        return "down"
    return ""


def _guidance_for_year(guidance: list[dict], fy: int,
                       metrics: tuple[str, ...]) -> dict | None:
    """First guidance item matching metric-class + fiscal year (fallback:
    first matching-metric item whose period label is empty)."""
    fallback = None
    for g in guidance or []:
        metric = str(g.get("metric") or "").lower()
        if not any(m in metric for m in metrics):
            continue
        gy = _parse_year_label(g.get("period") or "")
        if gy == fy:
            return g
        if fallback is None and not str(g.get("period") or "").strip():
            fallback = g
    return fallback


def _mid_value(g: dict) -> Optional[float]:
    for key in ("mid", "high", "low"):
        v = _parse_amount(g.get(key))
        if v is not None:
            return v
    return None


def _latest_company(ticker: str) -> dict | None:
    from src.memory import assumption_store
    try:
        return assumption_store.get_latest_earnings_assumptions(ticker)
    except Exception:
        return None


def _analyst_rows(ticker: str) -> list[dict]:
    from src.memory import assumption_store
    try:
        return assumption_store.get_analyst_reports(ticker, limit=10)
    except Exception:
        return []


def _fmp_consensus_revenue_growth(ticker: str) -> Optional[float]:
    """FMP street FY+1 revenue growth vs trailing revenue (None = no data,
    incl. HK/SG where FMP analyst endpoints return [])."""
    try:
        from src.tools.api import get_analyst_estimates, get_financial_metrics
        ests = get_analyst_estimates(ticker, limit=1)
        if not ests:
            return None
        rev_est = getattr(ests[0], "revenue_avg", None)
        if not rev_est:
            return None
        metrics = get_financial_metrics(ticker, period="annual", limit=1)
        if not metrics:
            return None
        rev_last = getattr(metrics[0], "revenue", None)
        if not rev_last:
            return None
        return (float(rev_est) / float(rev_last)) - 1.0
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════
# Deterministic challenge detectors (no LLM)
# ══════════════════════════════════════════════════════════════════════════

def detect_divergence(ticker: str) -> list[dict]:
    """Management FY+1 guidance vs house FY+1 estimates vs FMP consensus.
    Beyond-band -> challenge dicts (anomaly_type='divergence')."""
    out: list[dict] = []
    comp = _latest_company(ticker)
    if not comp:
        return out
    fy_reported = comp.get("fiscal_year")
    if not fy_reported:
        return out
    fy_next = int(fy_reported) + 1
    guidance = comp.get("guidance") or []

    # ── revenue growth divergence (management vs street) ──
    g_rev = _guidance_for_year(guidance, fy_next, ("revenue",))
    mgmt_growth = None
    if g_rev:
        gval = _mid_value(g_rev)
        try:
            from src.tools.api import get_financial_metrics
            m = get_financial_metrics(ticker, period="annual", limit=1)
            if m and getattr(m[0], "revenue", None) and gval:
                mgmt_growth = (gval / float(m[0].revenue)) - 1.0
        except Exception:
            pass
    street_growth = _fmp_consensus_revenue_growth(ticker)
    if mgmt_growth is not None and street_growth is not None:
        gap_pp = abs(mgmt_growth - street_growth) * 100.0
        if gap_pp > _BAND_GROWTH_PP:
            out.append({
                "field_key": f"guidance.revenue.FY{fy_next}",
                "anomaly_type": "divergence",
                "evidence": (
                    f"Management FY{fy_next} revenue guidance implies "
                    f"{mgmt_growth:+.1%} growth vs street consensus "
                    f"{street_growth:+.1%} — {gap_pp:.1f}pp apart "
                    f"(band {_BAND_GROWTH_PP:.0f}pp)."),
            })

    # ── EBITDA level divergence (house estimates vs management guidance) ──
    g_ebitda = _guidance_for_year(guidance, fy_next, ("ebitda", "adj_ebita"))
    g_val = _mid_value(g_ebitda) if g_ebitda else None
    if g_val:
        for rep in _analyst_rows(ticker):
            for est in rep.get("estimates") or []:
                ey = _parse_year_label(est.get("fiscal_year_label") or "",
                                       int(fy_reported))
                if ey != fy_next:
                    continue
                house_val = _parse_amount(est.get("ebitda"))
                if not house_val:
                    break
                gap_pct = abs(house_val - g_val) / max(abs(g_val), 1.0) * 100.0
                if gap_pct > _BAND_EBITDA_PCT:
                    out.append({
                        "field_key": f"ebitda.FY{fy_next}",
                        "anomaly_type": "divergence",
                        "evidence": (
                            f"{rep.get('house') or 'House'} FY{fy_next} EBITDA "
                            f"estimate diverges {gap_pct:.0f}% from management "
                            f"guidance (band {_BAND_EBITDA_PCT:.0f}%)."),
                    })
                break  # first matching-year estimate per report
    return out


def detect_direction_reversal(ticker: str, lookback: int = 6) -> list[dict]:
    """Time-series: a source's OWN newest version row reverses direction on
    the same field_key vs its prior row (capex/margin guidance etc.)."""
    from src.memory import assumption_store
    out: list[dict] = []
    try:
        versions = assumption_store.get_assumption_versions(ticker, limit=200)
    except Exception:
        return out
    seen_pairs: dict[tuple, list[dict]] = {}
    for v in versions:  # newest-first
        key = (v.get("source") or "", v.get("field_key") or "")
        seen_pairs.setdefault(key, []).append(v)
    for (source, field_key), rows in seen_pairs.items():
        if len(rows) < 2:
            continue
        newest, prior = rows[0], rows[1]
        dn = _norm_direction(newest.get("direction"))
        dp = _norm_direction(prior.get("direction"))
        if dn and dp and dn != dp:
            out.append({
                "field_key": field_key,
                "anomaly_type": "direction_reversal",
                "evidence": (
                    f"{source} reversed on '{field_key}': was '{dp}' "
                    f"({prior.get('new_value') or '?'}), now '{dn}' "
                    f"({newest.get('new_value') or '?'})."),
            })
    return out[:lookback]


def detect_cross_doc_theme(tickers: list[str]) -> list[dict]:
    """Cross-document same-theme divergence within the covered corpus (e.g.
    one name CUTS capex guidance while another RAISES in the same quarter).
    Surfaced as THEME challenges (ticker 'THEME'), never pinned on one
    company as an error."""
    from src.memory import assumption_store
    capex_moves: list[tuple[str, str, str]] = []  # (ticker, direction, value)
    for tkr in tickers:
        try:
            versions = assumption_store.get_assumption_versions(tkr, limit=200)
        except Exception:
            continue
        for v in versions:  # newest-first; first DIRECTIONAL capex row wins
            fk = (v.get("field_key") or "").lower()
            if "capex" not in fk:
                continue
            direction = _norm_direction(v.get("direction"))
            if not direction:
                new_amt = _parse_amount(v.get("new_value"))
                prior_amt = _parse_amount(v.get("prior_value_stated"))
                if new_amt and prior_amt and new_amt != prior_amt:
                    direction = "up" if new_amt > prior_amt else "down"
            if not direction:
                continue
            capex_moves.append((tkr.upper(), direction,
                                v.get("new_value") or "?"))
            break
    ups = [m for m in capex_moves if m[1] == "up"]
    downs = [m for m in capex_moves if m[1] == "down"]
    if ups and downs:
        up_txt = ", ".join(f"{t} raised ({v})" for t, _, v in ups)
        dn_txt = ", ".join(f"{t} cut ({v})" for t, _, v in downs)
        return [{
            "field_key": "theme.capex_guidance",
            "anomaly_type": "theme_divergence",
            "evidence": (
                f"Same-quarter capex-guidance divergence across covered "
                f"names: {up_txt} — while {dn_txt}. Cross-check the "
                f"capacity-cycle read before leaning on either guidance."),
        }]
    return []


def detect_earnings_quality(ticker: str) -> list[dict]:
    """Large one-off items flagged as boosting earnings/EPS (the AMZN
    $53.4bn other-income case) -> earnings_quality challenge."""
    comp = _latest_company(ticker)
    if not comp:
        return []
    out: list[dict] = []
    _BOOST_RE = re.compile(
        r"boost|help|benefit|inflat|driv[ea]n? by|support|upside",
        re.IGNORECASE)
    _EPS_RE = re.compile(
        r"eps|earnings per share|net income|other income|one[- ]?off|"
        r"one[- ]?time|non[- ]?recurring|gain", re.IGNORECASE)
    for off in comp.get("one_offs") or []:
        amt = _parse_amount(off.get("amount"))
        if not amt:
            continue
        blob = f"{off.get('item') or ''} {off.get('impact') or ''}"
        if _EPS_RE.search(blob) and _BOOST_RE.search(blob):
            out.append({
                "field_key": f"one_off.{str(off.get('item') or 'item')[:40]}",
                "anomaly_type": "earnings_quality",
                "evidence": (
                    f"One-off item '{off.get('item')}' ({off.get('amount')}) "
                    f"is flagged as boosting earnings: "
                    f"'{(off.get('impact') or '')[:180]}'. Strip it before "
                    f"treating EPS growth as run-rate."),
            })
    return out[:3]


def detect_margin_compression(ticker: str) -> list[dict]:
    """Revenue/segment growth UP while an EBITA/operating margin is guided
    or reported DOWN (the BABA rev +9% / adj EBITA -33% case)."""
    comp = _latest_company(ticker)
    if not comp:
        return []
    growth_bits: list[str] = []
    for s in comp.get("segments") or []:
        g = _parse_pct(s.get("growth_rate_pct"))
        if g is not None and g > 0 and s.get("name"):
            growth_bits.append(f"{s['name']} +{g:.0f}%")
    for k in comp.get("kpis") or []:
        name = str(k.get("name") or "")
        g = _parse_pct(k.get("growth_pct"))
        if g is not None and g > 0 and any(
                w in name.lower() for w in ("revenue", "rev ", "cmr",
                                            "cloud", "gmv")):
            growth_bits.append(f"{name} +{g:.0f}%")
    down_margin = None
    for m in comp.get("margins") or []:
        metric = str(m.get("metric") or "").lower()
        if _norm_direction(m.get("direction")) == "down" and any(
                w in metric for w in ("ebita", "operating", "op ", "margin")):
            down_margin = m
            break
    if growth_bits and down_margin:
        return [{
            "field_key": f"margin.{str(down_margin.get('metric') or 'ebita')[:40]}",
            "anomaly_type": "margin_compression",
            "evidence": (
                f"Growth up ({'; '.join(growth_bits[:3])}) while "
                f"'{down_margin.get('metric')}' is guided/reported DOWN "
                f"(driver: {(down_margin.get('driver') or '?')[:160]}). "
                f"Margin compression — value on margin trajectory, not "
                f"top-line growth."),
        }]
    return []


def detect_challenges(ticker: str) -> list[dict]:
    """All deterministic per-ticker detectors (never raises)."""
    if not steward_enabled():
        return []
    out: list[dict] = []
    for fn in (detect_divergence, detect_direction_reversal,
               detect_earnings_quality, detect_margin_compression):
        try:
            out.extend(fn(ticker) or [])
        except Exception as exc:
            logger.warning("steward detector %s failed for %s: %s",
                           fn.__name__, ticker, exc)
    return out


# ══════════════════════════════════════════════════════════════════════════
# Challenge persistence + the ONE bundled LLM challenge-reading call
# ══════════════════════════════════════════════════════════════════════════

def raise_detected(ticker: str, detected: list[dict]) -> list[str]:
    """Persist detected anomalies as open challenges (idempotent — an
    identical open challenge is never duplicated). Returns challenge ids."""
    from src.memory import assumption_store
    ids: list[str] = []
    for d in detected or []:
        try:
            cid = assumption_store.raise_challenge(
                ticker, d["field_key"], d["anomaly_type"], d["evidence"])
            ids.append(cid)
        except Exception as exc:
            logger.warning("raise_challenge failed (%s): %s", ticker, exc)
    return ids


class ChallengeReadingItem(BaseModel):
    field_key: str = ""
    what_changed: str = ""
    why_anomalous: str = ""
    affected_inputs: list[str] = Field(default_factory=list)
    confidence_pct: Optional[int] = None


class ChallengeReadingOutput(BaseModel):
    readings: list[ChallengeReadingItem] = Field(default_factory=list)
    verdict: str = ""


_CHALLENGE_SYSTEM = (
    "You are the Assumption Steward. You receive anomalies ALREADY flagged "
    "deterministically on one company's tracked assumption ledger "
    "(management guidance + licensed sell-side estimates). For each flag, "
    "explain what changed, why it is anomalous, and which VALUATION INPUTS "
    "it affects (growth rate, margin, capex, segment multiple). Discuss "
    "line-item DRIVERS ONLY — never price targets. Respond in JSON format. "
    "Every collection field must be a JSON array, never an object."
)


def challenge_reading(ticker: str, challenges: list[dict]) -> Optional[dict]:
    """ONE bundled structured call reading every open challenge for the
    ticker (Q1 bundle discipline). Returns {readings, verdict} or None —
    a failed call NEVER breaks the run."""
    if not challenges:
        return None
    try:
        from src.memory.assumption_extract import (
            _get_bundle_client, _structured_call)
    except Exception:
        return None
    client, _model = _get_bundle_client()
    if client is None:
        return None

    lines = []
    for i, c in enumerate(challenges[:8], 1):
        lines.append(
            f"{i}. field_key={c.get('field_key')} | type="
            f"{c.get('anomaly_type')}\n   evidence: "
            f"{str(c.get('evidence') or '')[:500]}")
    comp = _latest_company(ticker)
    snap = []
    if comp:
        for g in (comp.get("guidance") or [])[:6]:
            val = g.get("mid") or g.get("high") or g.get("low")
            if val:
                snap.append(f"  guidance {g.get('metric')} "
                            f"{g.get('period')}: {val}")
    human = (
        f"Ticker: {ticker}\n\n"
        f"=== FLAGGED ANOMALIES ===\n" + "\n".join(lines) + "\n\n"
        + ("=== CURRENT COMPANY GUIDANCE SNAPSHOT ===\n"
           + "\n".join(snap) + "\n\n" if snap else "")
        + "Return this exact JSON object shape:\n"
        "- readings: array of {field_key, what_changed, why_anomalous, "
        "affected_inputs (array of short strings), confidence_pct "
        "(integer 0-100)} — one entry per flagged anomaly\n"
        "- verdict: one sentence — the single most decision-relevant "
        "reading."
    )
    try:
        out = _structured_call(client, ChallengeReadingOutput,
                               _CHALLENGE_SYSTEM, human)
    except Exception as exc:
        logger.warning("steward challenge reading failed for %s: %s",
                       ticker, exc)
        return None
    if out is None:
        return None
    return {
        "readings": [r.model_dump() for r in out.readings[:8]],
        "verdict": (out.verdict or "")[:400],
    }


# ══════════════════════════════════════════════════════════════════════════
# Recursive scoring — predictions vs reported actuals (the learning memory)
# ══════════════════════════════════════════════════════════════════════════

def score_actuals(ticker: str) -> dict:
    """Best-effort: score stored quarterly guidance against the newest
    reported actuals (get_earnings_surprises). Writes assumption_scorecard
    rows and resolves open guidance challenges BY FACTS. Conservative on
    purpose — only quarterly-scoped guidance is scored, and a guidance row
    that post-dates the print scores nothing."""
    from src.memory import assumption_store
    result = {"ticker": ticker.upper(), "scored": 0, "resolved": 0}
    if not steward_enabled():
        return result
    comp = _latest_company(ticker)
    if not comp:
        return result
    try:
        from datetime import date, timedelta
        from src.tools.api import get_earnings_surprises
        surprises = get_earnings_surprises(
            ticker, (date.today() + timedelta(days=1)).isoformat(), limit=4)
    except Exception:
        return result
    if not surprises:
        return result
    newest = surprises[0]
    eps_actual = newest.get("eps_actual")
    rev_actual = newest.get("revenueActual")
    as_of = comp.get("as_of") or ""
    if as_of and newest.get("date") and newest["date"] <= as_of:
        return result  # guidance row post-dates the print — nothing predicted

    scored: list[tuple[str, float, float, float]] = []
    for g in comp.get("guidance") or []:
        metric = str(g.get("metric") or "").lower()
        predicted = _mid_value(g)
        if predicted is None:
            continue
        if "eps" in metric and eps_actual:
            actual = float(eps_actual)
        elif "revenue" in metric and rev_actual:
            actual = float(rev_actual)
        else:
            continue
        # Only quarterly-scoped guidance is scored against a quarterly print
        if not re.search(r"\bQ[1-4]\b|quarter", str(g.get("period") or ""),
                         re.IGNORECASE):
            continue
        mag = abs(actual - predicted) / max(abs(predicted), 1e-9)
        assumption_store.record_scorecard(
            ticker, "earnings", f"guidance.{metric}",
            int(comp.get("fiscal_year") or 0),
            int(comp.get("fiscal_quarter") or 0),
            predicted=str(g.get("mid") or g.get("high") or g.get("low")),
            actual=str(actual),
            in_range=mag <= _EPS_MATCH_BAND,
            magnitude=round(mag, 4),
        )
        scored.append((metric, predicted, actual, mag))
        result["scored"] += 1

    # Resolve open guidance challenges by the facts we just scored
    for metric, predicted, actual, mag in scored:
        for ch in assumption_store.get_open_challenges(ticker):
            fk = str(ch.get("field_key") or "")
            if metric.split(".")[0] in fk and "guidance" in fk:
                assumption_store.resolve_challenge(
                    ch["id"], "resolved",
                    resolution="confirmed_by_actuals" if mag > _EPS_MATCH_BAND
                    else "dismissed_by_actuals",
                    outcome_note=(f"Reported {metric} {actual} vs guided "
                                  f"{predicted} ({mag:.1%} off)."))
                result["resolved"] += 1
    return result


def source_track_record(ticker: str) -> dict:
    """{source: {hit_rate, n, low_track_record}} — sources under
    _LOW_TRACK_RATE with >= _MIN_SCORECARD_N scored rows get their fresh
    estimates weighted down automatically in the Watch block."""
    from src.memory import assumption_store
    try:
        summary = assumption_store.get_scorecard_summary(ticker)
    except Exception:
        return {}
    out = {}
    for src, t in (summary or {}).items():
        n = t.get("hits", 0) + t.get("misses", 0)
        out[src] = {
            "hit_rate": t.get("hit_rate"),
            "n": n,
            "low_track_record": bool(
                t.get("hit_rate") is not None
                and n >= _MIN_SCORECARD_N
                and t["hit_rate"] < _LOW_TRACK_RATE),
        }
    return out


# ══════════════════════════════════════════════════════════════════════════
# Variant drivers — the 1-2 variables where the view diverges from street
# ══════════════════════════════════════════════════════════════════════════

# Lookarounds instead of \b: "%" is itself a non-word char, so "\b" after
# it only matched when a WORD char followed ("5% above" never matched).
_GAP_PCT_RE = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:%|pp|ppt)(?!\d)")

# Valuation-sensitivity weights for ranking (heuristic, documented):
_DRIVER_WEIGHTS = (
    ("ebitda", 1.2), ("margin", 1.2), ("ebita", 1.2),
    ("eps", 1.1), ("revenue", 1.0), ("growth", 1.0),
    ("capex", 0.8),
)


def _driver_weight(text: str) -> float:
    s = str(text).lower()
    for key, w in _DRIVER_WEIGHTS:
        if key in s:
            return w
    return 0.7


def variant_drivers(ticker: str, top_n: int = 2) -> list[dict]:
    """Rank divergence candidates by (gap magnitude x driver sensitivity).
    Sources: (a) explicit house-vs-consensus call-outs extracted from the
    licensed reports — the documented variant perceptions; (b) open
    divergence/margin_compression challenges. Price targets never enter."""
    candidates: list[dict] = []
    for rep in _analyst_rows(ticker):
        house = rep.get("house") or "sell-side"
        for hv in rep.get("house_vs_consensus") or []:
            blob = " ".join(str(hv.get(k) or "") for k in
                            ("metric", "house_view", "street_view", "comment"))
            gaps = [float(g) for g in _GAP_PCT_RE.findall(blob)]
            gap = max(gaps) if gaps else _BAND_GROWTH_PP  # band-equivalent
            candidates.append({
                "field_key": f"house_vs_consensus.{str(hv.get('metric') or 'metric')[:50]}",
                "source": f"analyst:{house}",
                "house_view": str(hv.get("house_view") or "")[:120],
                "street_view": str(hv.get("street_view") or "")[:120],
                "gap_pct": gap,
                "score": gap * _driver_weight(blob),
            })
    try:
        from src.memory import assumption_store
        for ch in assumption_store.get_open_challenges(ticker):
            if ch.get("anomaly_type") in ("divergence", "margin_compression"):
                blob = str(ch.get("evidence") or "")
                gaps = [float(g) for g in _GAP_PCT_RE.findall(blob)]
                gap = max(gaps) if gaps else _BAND_GROWTH_PP
                candidates.append({
                    "field_key": ch.get("field_key") or "?",
                    "source": "steward",
                    "house_view": "", "street_view": "",
                    "gap_pct": gap,
                    "score": gap * _driver_weight(ch.get("field_key") or ""),
                })
    except Exception:
        pass
    candidates.sort(key=lambda c: c["score"], reverse=True)
    seen: set[str] = set()
    out = []
    for c in candidates:
        if c["field_key"] in seen:
            continue
        seen.add(c["field_key"])
        out.append(c)
        if len(out) >= top_n:
            break
    return out


# ══════════════════════════════════════════════════════════════════════════
# Qualitative feed — the Assumption Watch block
# ══════════════════════════════════════════════════════════════════════════

def build_assumption_watch(ticker: str, max_chars: int = 2200) -> str:
    """Compact Assumption Watch text block: open challenges, variant
    drivers, source hit-rates. Injected into deep-research _base_context
    (via build_assumption_context) and scenario prompts. Empty string when
    nothing is tracked or the steward is disabled."""
    if not steward_enabled():
        return ""
    from src.memory import assumption_store
    try:
        open_ch = assumption_store.get_open_challenges(ticker)
    except Exception:
        open_ch = []
    variants = variant_drivers(ticker)
    track = source_track_record(ticker)
    if not open_ch and not variants and not track:
        return ""

    lines = ["ASSUMPTION WATCH (recursive steward)"]
    if open_ch:
        lines.append(f"Open challenges ({len(open_ch)}):")
        for ch in open_ch[:5]:
            lines.append(
                f"  - [{ch.get('anomaly_type')}] {ch.get('field_key')}: "
                f"{str(ch.get('evidence') or '')[:200]}")
            note = str(ch.get("outcome_note") or "")
            if "[reading]" in note:
                lines.append(
                    f"      reading: {note.split('[reading]', 1)[1][:220]}")
    if variants:
        lines.append("Variant drivers (largest divergence vs street):")
        for v in variants:
            hv = v.get("house_view") or ""
            sv = v.get("street_view") or ""
            views = f" — house '{hv}' vs street '{sv}'" if hv or sv else ""
            lines.append(
                f"  - {v['field_key']} ({v['source']}), gap ~"
                f"{v['gap_pct']:.0f}%{views}")
    if track:
        bits = []
        for src, t in track.items():
            tag = " LOW-TRACK-RECORD" if t.get("low_track_record") else ""
            hr = t.get("hit_rate")
            bits.append(f"{src} hit-rate "
                        f"{f'{hr:.0%}' if hr is not None else '?'} "
                        f"(n={t.get('n')}){tag}")
        lines.append("Source track record: " + "; ".join(bits))
    return "\n".join(lines)[:max_chars]


# ══════════════════════════════════════════════════════════════════════════
# Orchestration — inline pass (after every R1 ingest) + weekly sweep
# ══════════════════════════════════════════════════════════════════════════

def _annotate_readings(ticker: str, open_ch: list[dict]) -> int:
    """One bundled LLM reading over the open challenges that have no
    reading yet; annotate them (status stays open). Returns count."""
    from src.memory import assumption_store
    pending = [c for c in open_ch
               if "[reading]" not in str(c.get("outcome_note") or "")]
    if not pending:
        return 0
    if os.environ.get("ASSUMPTION_STEWARD_LLM", "true").strip().lower() \
            in ("0", "false", "no", "off"):
        return 0
    reading = challenge_reading(ticker, pending)
    if not reading or not reading.get("readings"):
        return 0
    by_fk = {r.get("field_key") or "": r for r in reading["readings"]}
    n = 0
    for ch in pending:
        r = by_fk.get(ch.get("field_key") or "")
        if not r:
            continue
        aff = ", ".join((r.get("affected_inputs") or [])[:4])
        note = (f"[reading] {r.get('what_changed') or ''} | anomalous: "
                f"{r.get('why_anomalous') or ''} | affects: {aff} | conf "
                f"{r.get('confidence_pct') or '?'}%"
                + (f" | VERDICT: {reading.get('verdict')}"
                   if reading.get("verdict") else ""))
        try:
            assumption_store.annotate_challenge(ch["id"], note[:1800])
            n += 1
        except Exception as exc:
            logger.warning("annotate_challenge failed: %s", exc)
    return n


def run_steward_inline(tickers: list[str], trigger: str = "ingest") -> dict:
    """The steward pass that runs after every R1 ingest (company or
    analyst). Deterministic detectors -> open challenges -> scorecard ->
    one LLM reading per flagged ticker. NEVER raises into the caller."""
    summary: dict = {"trigger": trigger, "status": "ok", "tickers": {}}
    if not steward_enabled():
        summary["status"] = "disabled"
        return summary
    from src.memory import assumption_store
    tkr_list = [t.upper() for t in (tickers or []) if t]
    if not tkr_list:
        return summary
    # Cross-doc theme scan needs the covered universe, not just this batch
    universe = sorted(set(tkr_list) | set(_MONITOR_SPECS))
    try:
        theme_hits = detect_cross_doc_theme(universe)
        theme_ids = raise_detected("THEME", theme_hits)
        if theme_ids:
            summary["theme_challenges"] = len(theme_ids)
    except Exception as exc:
        logger.warning("steward theme scan failed: %s", exc)

    for tkr in tkr_list:
        entry: dict = {}
        try:
            detected = detect_challenges(tkr)
            raise_detected(tkr, detected)
            entry["detected"] = len(detected)
            entry["challenges_open"] = len(
                assumption_store.get_open_challenges(tkr))
            entry["scored"] = score_actuals(tkr).get("scored", 0)
            open_ch = assumption_store.get_open_challenges(tkr)
            entry["readings"] = _annotate_readings(tkr, open_ch)
            entry["variant_drivers"] = [
                v["field_key"] for v in variant_drivers(tkr)]
        except Exception as exc:
            logger.warning("steward inline failed for %s: %s", tkr, exc)
            entry["error"] = str(exc)
        summary["tickers"][tkr] = entry
    logger.info("[steward] inline pass (%s): %s", trigger,
                {t: e.get("detected", 0)
                 for t, e in summary["tickers"].items()})
    return summary


def run_steward_sweep() -> dict:
    """Weekly sweep over the monitored universe (worker cron). Same pass
    as inline, plus every ticker with open challenges so nothing ages
    out unreviewed."""
    if not steward_enabled():
        return {"status": "disabled"}
    from src.memory import assumption_store
    universe = set(_MONITOR_SPECS)
    try:
        for ch in assumption_store.get_open_challenges():
            if ch.get("ticker") and ch["ticker"] != "THEME":
                universe.add(ch["ticker"])
    except Exception:
        pass
    return run_steward_inline(sorted(universe), trigger="weekly_sweep")

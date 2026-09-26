"""Owner-set valuation constants, with a calibration rule and a review clock.

A profile constant that never moves goes stale; one that tracks the market
daily stops being a fundamental anchor and starts being the price. This module
holds the middle: a stored constant the engine reads, a formula that proposes
what it should be, and an inertia rule that refuses to move it for noise.

The first constant is the midstream target distributable-cash-flow yield.
`Distributable CF Yield` used to capitalise (OCF - maintenance capex) at the
peer *free* cash flow yield, which is net of total capex -- two different
bases. While maintenance capex fell back to D&A the numerator was roughly FCF
and the mismatch stayed hidden; Energy Transfer's audited $1.32bn maintenance
capex broke the accident and the leg priced ET at $49 against a $21 quote.

The constant is grounded in a published benchmark rather than chosen:

    target_dcf_yield = index distribution yield x sector coverage factor

with the Alerian MLP index (AMLP as the tradable proxy) supplying the yield and
midstream coverage of ~1.4-1.7x supplying the factor.

Nothing here writes a new constant into effect on its own. `calibrate` returns
a proposal; `apply_proposal` records it only when an owner accepts, the same
review gate `industry_inputs` uses for cited figures.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_PATH = Path(__file__).resolve().parent / "valuation_constants.json"

#: A quarter, for the scheduled review. The cadence is the reporting cycle --
#: mid-February, May, August and November -- so the clock is set from the last
#: review rather than to fixed calendar dates.
_REVIEW_DAYS = 91


def load(path: Optional[Path] = None) -> dict:
    p = Path(path or _PATH)
    if not p.exists():
        return {"version": 1, "profiles": {}}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save(doc: dict, path: Optional[Path] = None) -> None:
    p = Path(path or _PATH)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def entry(profile: Optional[str], doc: Optional[dict] = None) -> Optional[dict]:
    if not profile:
        return None
    return ((doc or load()).get("profiles") or {}).get(profile)


def target_dcf_yield(profile: Optional[str], doc: Optional[dict] = None) -> Optional[float]:
    """The stored yield for this profile, or None when none is authored.

    None is a real answer: a caller with no constant must say what it fell back
    to rather than substitute a number from a different basis.
    """
    e = entry(profile, doc)
    v = (e or {}).get("target_dcf_yield")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def market_key(ticker: Optional[str]) -> str:
    """The market a constant is keyed by: HKSE, SES or US (the comps store's keys)."""
    t = (ticker or "").upper()
    return "HKSE" if t.endswith(".HK") else ("SES" if t.endswith(".SI") else "US")


def cost_of_equity(profile: Optional[str], market: Optional[str] = "US",
                   doc: Optional[dict] = None) -> Optional[float]:
    """The owner-set cost of equity for this profile IN THIS MARKET, or None.

    Read by the P/Rate Base leg, whose justified multiple is
    (allowed ROE - g) / (CoE - g). The rate must match the currency of the cash
    flows (owner, 2026-09-21): a USD utility discounts at a Treasury-based rate,
    an HKD/RMB one at a CGB-based rate, and neither borrows the other's. None
    means the leg declines and its declared proxy prices the weight: a cost of
    equity is never inferred from a sector WACC, a blended rate on another basis.
    """
    e = ((((doc or load()).get("cost_of_equity") or {}).get("profiles") or {})
         .get(profile or "") or {}).get(market or "US") or {}
    v = e.get("value")
    return float(v) if isinstance(v, (int, float)) and 0.03 <= v <= 0.20 else None


def long_cycle_eligibility(*, sector: Optional[str], backlog_coverage: Optional[float],
                           book_to_bill: Optional[float], contract_liability_share: Optional[float],
                           doc: Optional[dict] = None) -> dict:
    """{eligible, profile, checks} for the Backlog-Gated Long Cycle profile.

    All three rules must hold, on figures that exist. A rule whose input is
    MISSING fails: eligibility is a claim about the company, and a claim that
    cannot be checked is not made. `checks` names each rule, its reading and its
    threshold, so a valuation can say exactly why a name did or did not qualify.
    """
    cfg = (doc or load()).get("backlog_gated_long_cycle") or {}
    rules = (
        ("backlog coverage of forward sales", backlog_coverage, cfg.get("min_backlog_coverage_forward_sales")),
        ("book-to-bill", book_to_bill, cfg.get("min_book_to_bill")),
        ("contract liabilities / (receivables + inventory)", contract_liability_share,
         cfg.get("min_contract_liability_share")),
    )
    checks = []
    for name, value, floor in rules:
        ok = (isinstance(value, (int, float)) and isinstance(floor, (int, float)) and value > floor)
        checks.append({"rule": name, "value": (round(float(value), 4) if isinstance(value, (int, float)) else None),
                       "minimum": floor, "ok": bool(ok)})
    in_sector = (sector in (cfg.get("sectors") or [])) if sector else False
    return {"eligible": bool(cfg) and in_sector and all(c["ok"] for c in checks),
            "profile": cfg.get("profile"), "sector_in_scope": in_sector, "checks": checks}


def fcf_guidance_margin_schedule(guided_margin: Optional[float], years: int = 10,
                                 doc: Optional[dict] = None) -> Optional[dict]:
    """{schedule, explicit_margin, floor, ...} for an accepted FCF guidance, or None.

        years 1..E          the guided margin, capped at the owner's ceiling
        years E+1..E+F      minus `fade_bps_per_year` each year, never below the floor
        years after, and the terminal (which takes the last year's margin): the floor

    None when the guidance is not above the floor: there is nothing to fade, and
    a guided margin BELOW the steady-state floor must not be lifted onto it.
    """
    cfg = (doc or load()).get("fcf_guidance_fade") or {}
    floor, ceil = cfg.get("floor_margin"), cfg.get("explicit_margin_ceiling")
    e_years, f_years, bps = cfg.get("explicit_years"), cfg.get("fade_years"), cfg.get("fade_bps_per_year")
    if not all(isinstance(x, (int, float)) for x in (guided_margin, floor, ceil, e_years, f_years, bps)):
        return None
    if guided_margin <= floor:
        return None
    explicit = min(float(guided_margin), float(ceil))
    step = float(bps) / 10000.0
    sched = []
    for t in range(1, years + 1):
        if t <= e_years:
            m = explicit
        elif t <= e_years + f_years:
            m = max(explicit - step * (t - e_years), float(floor))
        else:
            m = float(floor)
        sched.append(round(m, 6))
    return {"schedule": sched, "guided_margin": float(guided_margin), "explicit_margin": explicit,
            "ceiling_applied": guided_margin > ceil, "floor": float(floor),
            "explicit_years": int(e_years), "fade_years": int(f_years), "fade_bps_per_year": float(bps)}


#: Reason codes, in the order they are tested. The label is what a reader sees.
UNRATED_REASONS = {
    "pre_revenue": "Unrated — pre-revenue",
    "no_valuation": "Unrated — no method could value it",
    "insufficient_methods": "Unrated — too little of the method set could be computed",
}


def unrated_verdict(*, revenue_base: Optional[float], base_iv: Optional[float],
                    weight_surviving: Optional[float], doc: Optional[dict] = None) -> Optional[dict]:
    """{code, label, reason} when a valuation must be withheld, else None.

    Three tests, any one sufficient, thresholds owner-set in `unrated`:

      pre_revenue           revenue below the floor: there is no operating
                            business for an earnings or cash-flow method to value
      no_valuation          every leg dropped, or what survived is not positive
                            (CGN Power once published -3.24 this way: with the
                            blend empty the raw DCF was published in its place)
      insufficient_methods  less than `min_weight_surviving` of the profile's
                            intended weight could be computed

    A missing `weight_surviving` is NOT a reason: older profiles do not record
    it, and absence of a measurement must not withhold a valuation.
    """
    cfg = (doc or load()).get("unrated") or {}
    floor = cfg.get("pre_revenue_below")
    min_w = cfg.get("min_weight_surviving")
    code = reason = None
    if isinstance(floor, (int, float)) and isinstance(revenue_base, (int, float)) and revenue_base < floor:
        code = "pre_revenue"
        reason = f"revenue of {revenue_base / 1e6:,.1f}m is below the {floor / 1e6:,.0f}m pre-revenue floor"
    elif base_iv is None or not isinstance(base_iv, (int, float)) or base_iv <= 0:
        code = "no_valuation"
        reason = ("every valuation leg dropped" if base_iv is None
                  else f"the only figure the method set produced is not positive ({base_iv:,.2f})")
    elif (isinstance(min_w, (int, float)) and isinstance(weight_surviving, (int, float))
          and weight_surviving < min_w):
        code = "insufficient_methods"
        reason = (f"only {weight_surviving:.0%} of the profile's intended method weight could be computed "
                  f"(minimum {min_w:.0%})")
    if not code:
        return None
    return {"code": code, "label": UNRATED_REASONS[code], "reason": reason}


def regime_deviation(ticker: Optional[str], doc: Optional[dict] = None) -> Optional[dict]:
    """The owner-recorded structural regime deviation this ticker belongs to, or None.

    A deviation is a named, dated statement that the market is pricing a group
    of names on a regime the engine's through-cycle methods do not, and should
    not, chase. It changes NO number. It says on the valuation, and in the
    wave scorecard, that the gap to consensus is known, why it exists, and that
    it was recorded rather than tuned away -- so a reader does not take a
    deliberate through-cycle stance for a broken model, and a scorecard does not
    count one decision eight times.
    """
    t = (ticker or "").upper()
    for key, e in (((doc or load()).get("regime_deviations") or {}).get("regimes") or {}).items():
        if t in {str(x).upper() for x in (e.get("tickers") or [])}:
            return {"key": key, **e}
    return None


def ticker_multiple_discount(ticker: Optional[str], doc: Optional[dict] = None) -> float:
    """The owner-set factor on PEER multiples for one ticker; 1.0 for everyone else.

    A discount is a statement about one company against its basket (CGN Power:
    a utility whose tariffs are 40-55% marketised), so it lives with the ticker,
    is recorded as its own part of the multiple, and never edits the basket.
    """
    e = (((doc or load()).get("ticker_multiple_discounts") or {}).get("tickers") or {}).get((ticker or "").upper())
    v = (e or {}).get("factor")
    return float(v) if isinstance(v, (int, float)) and 0.5 <= v <= 1.0 else 1.0


def peg_detail(profile: Optional[str], doc: Optional[dict] = None) -> Optional[dict]:
    """{ratio, status, interval} for the PEG leg, or None when no ratio is recorded.

    Owner rule 2 (2026-09-23): a ratio tagged OWNER_OVERRIDE_PENDING still
    runs as the active baseline -- the engine never halts on it -- and the
    leg carries a sensitivity over the interval until it is signed off.
    """
    e = entry(profile, doc) or {}
    r = peg_ratio(profile, doc)
    if r is None:
        return None
    iv = e.get("sensitivity_interval")
    return {"ratio": r, "status": e.get("status") or "owner-set",
            "interval": [float(iv[0]), float(iv[1])] if isinstance(iv, list) and len(iv) == 2 else None}


def ddm_equity_spread(doc: Optional[dict] = None) -> float:
    """Spread over WACC the DDM leg uses when no owner cost-of-equity row exists
    (2026-09-26, PROPOSED 150bps)."""
    v = (((doc or load()).get("cost_of_equity") or {}).get("ddm_equity_spread") or {}).get("value")
    return float(v) if isinstance(v, (int, float)) and 0.0 <= v <= 0.10 else 0.015


def country_risk_premium(currency: Optional[str], doc: Optional[dict] = None) -> float:
    """Owner-set premium added to the discount rate for cash flows domiciled in
    `currency` (2026-09-26: China +350bps, the midpoint of the owner's range)."""
    tbl = ((doc or load()).get("country_risk_premium") or {}).get("by_currency") or {}
    v = tbl.get((currency or "").upper())
    return float(v) if isinstance(v, (int, float)) and 0.0 <= v < 0.20 else 0.0


def growth_inflection_flag(consensus_spread: Optional[float], doc: Optional[dict] = None) -> Optional[str]:
    """The regime label when the consensus target sits more than the owner's
    threshold above spot, else None. `consensus_spread` = consensus/spot - 1."""
    cfg = ((doc or load()).get("regime_flags") or {}).get("growth_inflection") or {}
    thr = cfg.get("consensus_spread_min")
    if not isinstance(consensus_spread, (int, float)) or not isinstance(thr, (int, float)):
        return None
    return str(cfg.get("label") or "Growth_Inflection_Speculative") if consensus_spread > thr else None


def sotp_input_thresholds(doc: Optional[dict] = None) -> dict:
    """Owner thresholds for SOTP inputs (2026-09-24): the net-cash variance
    above which the FMP check flags a cited figure, and the excess of segment
    revenue over consolidated revenue a pre-fill may carry."""
    cfg = (doc or load()).get("sotp_inputs") or {}
    def _f(k, default):
        v = cfg.get(k)
        return float(v) if isinstance(v, (int, float)) and 0.0 < v < 1.0 else default
    return {"net_cash_variance_flag": _f("net_cash_variance_flag", 0.20),
            "segment_sum_excess_tolerance": _f("segment_sum_excess_tolerance", 0.05)}


def target_margin(ticker: Optional[str], profile: Optional[str],
                  doc: Optional[dict] = None) -> Optional[dict]:
    """The terminal EBIT margin for `Rev DCF (Target Margin)`: the ticker's own
    figure over the profile's, or None when neither is recorded."""
    cfg = (doc or load()).get("target_margins") or {}
    base = dict((cfg.get("profiles") or {}).get(profile or "") or {})
    own = (cfg.get("tickers") or {}).get((ticker or "").upper()) or {}
    merged = {**base, **own}
    m = merged.get("ebit_margin")
    if not isinstance(m, (int, float)) or not 0.0 < m < 0.6:
        return None
    return {"ebit_margin": float(m), "band": merged.get("band"),
            "ramp_years": int(merged.get("ramp_years") or 5),
            "tax_rate": float(merged.get("tax_rate") if isinstance(merged.get("tax_rate"), (int, float)) else 0.21),
            "basis": merged.get("basis"), "source": "ticker" if own.get("ebit_margin") else "profile"}


def peg_ratio(profile: Optional[str], doc: Optional[dict] = None) -> Optional[float]:
    """The profile's PEG ratio for the PEG leg, or None when none is recorded."""
    v = (entry(profile, doc) or {}).get("peg_ratio")
    return float(v) if isinstance(v, (int, float)) and 0.3 <= v <= 6.0 else None


def detail(profile: Optional[str], doc: Optional[dict] = None,
           today: Optional[date] = None) -> Optional[dict]:
    """The constant plus the audit trail behind it: benchmark, band, review
    clock, and whether that clock has run out."""
    e = entry(profile, doc)
    if not e:
        return None
    due, reasons = review_due(profile, doc=doc, today=today)
    return {
        "value": target_dcf_yield(profile, doc),
        "benchmark": e.get("benchmark"),
        "benchmark_detail": e.get("benchmark_detail"),
        "tolerance_band": e.get("tolerance_band"),
        "last_reviewed": e.get("last_reviewed"),
        "effective_until": e.get("effective_until"),
        "review_due": due,
        "review_reasons": reasons,
        "note": e.get("note"),
    }


def _as_date(v) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except (TypeError, ValueError):
        return None


def review_due(profile: Optional[str], doc: Optional[dict] = None,
               today: Optional[date] = None,
               ten_year: Optional[float] = None,
               index_yield: Optional[float] = None) -> tuple[bool, list[str]]:
    """Is this constant due for review, and why.

    Three clocks, any of which is sufficient: the scheduled quarter has ended,
    the 10-year Treasury has moved past its trigger (pipeline distributions
    compete with fixed income), or the benchmark index has re-rated past its.
    The macro arguments are optional so the scheduled check works offline.
    """
    e = entry(profile, doc)
    if not e:
        return False, []
    today = today or datetime.now(timezone.utc).date()
    reasons: list[str] = []

    until = _as_date(e.get("effective_until"))
    last = _as_date(e.get("last_reviewed"))
    if until and today > until:
        reasons.append(f"scheduled review passed ({e.get('effective_until')})")
    elif not until and last and today - last > timedelta(days=_REVIEW_DAYS):
        reasons.append(f"more than a quarter since review ({e.get('last_reviewed')})")

    obs = e.get("observed_at_last_review") or {}
    trig = e.get("macro_triggers") or {}
    for key, val, label, bps_key in (
            ("ten_year", ten_year, "10-year Treasury", "ten_year_move_bps"),
            ("index_yield", index_yield, "benchmark index yield", "index_yield_move_bps")):
        prior, limit = obs.get(key), trig.get(bps_key)
        if val is None or not isinstance(prior, (int, float)) or not limit:
            continue
        move_bps = abs(val - prior) * 1e4
        if move_bps >= limit:
            reasons.append(f"{label} moved {move_bps:.0f}bps since review "
                           f"({prior:.2%} -> {val:.2%}), trigger {limit}bps")
    return bool(reasons), reasons


def calibrate(profile: str, *, index_yield: float, coverage_factor: Optional[float] = None,
              ten_year: Optional[float] = None, doc: Optional[dict] = None,
              today: Optional[date] = None) -> dict:
    """Propose what the constant should be. Never writes.

    The raw candidate is the benchmark formula. Two rules stand between it and
    the stored value, and both are reported rather than applied silently:

      inertia -- a move smaller than the profile's threshold is noise, and a
                 cash-flow anchor that tracks weekly index moves is just price;
      band    -- a candidate outside the owner's tolerance band is not clamped
                 quietly. It is clamped AND flagged, because a benchmark that
                 leaves the band is telling you the band needs a decision, not
                 that the number needs rounding.
    """
    doc = doc or load()
    e = entry(profile, doc)
    if not e:
        raise KeyError(profile)
    bd = e.get("benchmark_detail") or {}
    cov = coverage_factor if coverage_factor is not None else bd.get("coverage_factor")
    if not cov or index_yield <= 0:
        raise ValueError("index yield and coverage factor are both required")

    current = target_dcf_yield(profile, doc)
    raw = float(index_yield) * float(cov)
    lo, hi = (e.get("tolerance_band") or [None, None])
    clamped = raw
    outside = False
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        clamped = min(max(raw, lo), hi)
        outside = raw < lo or raw > hi

    inertia = float(e.get("inertia_bps") or 0) / 1e4
    move = abs(clamped - current) if current else None
    held = bool(current and move is not None and move < inertia)
    proposed = current if held else clamped

    due, reasons = review_due(profile, doc=doc, today=today,
                              ten_year=ten_year, index_yield=index_yield)
    return {
        "profile": profile,
        "current": current,
        "raw_candidate": raw,
        "proposed": proposed,
        "changed": bool(current is None or abs(proposed - current) > 1e-12),
        "held_by_inertia": held,
        "move_bps": None if move is None else round(move * 1e4, 1),
        "inertia_bps": e.get("inertia_bps"),
        "outside_band": outside,
        "tolerance_band": e.get("tolerance_band"),
        "inputs": {"index_yield": index_yield, "coverage_factor": cov,
                   "ten_year": ten_year},
        "review_due": due,
        "review_reasons": reasons,
        "as_of": (today or datetime.now(timezone.utc).date()).isoformat(),
    }


def apply_proposal(proposal: dict, *, reviewer: str, doc: Optional[dict] = None,
                   effective_until: Optional[str] = None,
                   path: Optional[Path] = None) -> dict:
    """Record an accepted proposal. Called only behind an explicit owner
    acceptance -- calibration proposes, the owner decides."""
    doc = doc or load(path)
    profile = proposal["profile"]
    e = entry(profile, doc)
    if not e:
        raise KeyError(profile)
    today = _as_date(proposal.get("as_of")) or datetime.now(timezone.utc).date()
    e["target_dcf_yield"] = round(float(proposal["proposed"]), 6)
    bd = e.setdefault("benchmark_detail", {})
    bd["index_yield"] = proposal["inputs"]["index_yield"]
    bd["coverage_factor"] = proposal["inputs"]["coverage_factor"]
    obs = e.setdefault("observed_at_last_review", {})
    obs["index_yield"] = proposal["inputs"]["index_yield"]
    if proposal["inputs"].get("ten_year") is not None:
        obs["ten_year"] = proposal["inputs"]["ten_year"]
    e["last_reviewed"] = today.isoformat()
    e["effective_until"] = effective_until or (today + timedelta(days=_REVIEW_DAYS)).isoformat()
    e["reviewer"] = reviewer
    e.setdefault("history", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "value": e["target_dcf_yield"], "raw_candidate": proposal["raw_candidate"],
        "held_by_inertia": proposal["held_by_inertia"],
        "outside_band": proposal["outside_band"], "reviewer": reviewer,
        "inputs": proposal["inputs"],
    })
    save(doc, path)
    return e


# ── Benchmark feed ──────────────────────────────────────────────────────────

def fetch_index_yield(symbol: str = "AMLP") -> Optional[dict]:
    """Trailing distribution yield of the benchmark proxy, from FMP.

    Trailing four declared distributions over the current price, rather than
    FMP's own `yield` field, so the number is reproducible from the rows and a
    changed distribution shows up the quarter it is declared.
    """
    from src.data.regional_comps import _fmp_get
    _S = "https://financialmodelingprep.com/stable"
    divs = _fmp_get(f"{_S}/dividends", {"symbol": symbol, "limit": 8}, api_key=None)
    quote = _fmp_get(f"{_S}/quote", {"symbol": symbol}, api_key=None)
    if not isinstance(divs, list) or not divs or not isinstance(quote, list) or not quote:
        return None
    price = quote[0].get("price")
    paid = [float(d.get("dividend") or 0) for d in divs[:4]]
    if not price or price <= 0 or not any(paid):
        return None
    return {"symbol": symbol, "price": float(price), "trailing_4": paid,
            "annual": sum(paid), "yield": sum(paid) / float(price),
            "latest_date": divs[0].get("date")}


def fetch_ten_year() -> Optional[float]:
    """The 10-year Treasury, for the out-of-cycle macro trigger."""
    from src.data.regional_comps import _fmp_get
    rows = _fmp_get("https://financialmodelingprep.com/stable/treasury-rates",
                    {}, api_key=None)
    if not isinstance(rows, list) or not rows:
        return None
    v = rows[0].get("year10")
    return float(v) / 100.0 if isinstance(v, (int, float)) else None


def env_override(profile: str) -> Optional[float]:
    """An escape hatch for a run that must price on a different yield, e.g.
    a what-if. Named per profile so one override cannot silently move another.
    """
    key = "DCF_YIELD_" + "".join(
        c if c.isalnum() else "_" for c in profile.upper()).strip("_")
    raw = os.environ.get(key)
    try:
        v = float(raw) if raw else 0.0
    except ValueError:
        return None
    return v if v > 0 else None


# ── Owner plausibility bands on multiple LEVELS ──────────────────────────────
#
# A fourth kind of band, and the three it must not be confused with:
#
#   regional_comps._BANDS        drops one peer's reading before the median;
#   dynamic_multiples.MAX_DEVIATION  caps how far the quarterly engine may move
#                                a basket's multiple from its own baseline --
#                                it CLAMPS, by the owner's choice;
#   SEGMENT_BASELINES[..]["band"]  accept_value refuses a segment multiple
#                                outside it.
#
# This one is a sanity range on the RESULTING LEVEL, per (market, profile,
# field). It only ever reports. `check_multiples` has no return path that
# carries a value, so a caller cannot consume a clamped number: there is none
# to consume. A multiple outside its band is telling you the band or the cohort
# needs a decision -- the same argument `calibrate` makes about tolerance_band,
# minus the clamp that argument then contradicts.
#
# Resolution is most-specific-first: (market, profile), (market, "*"),
# ("*", profile), ("*", "*"). No match is no opinion, not a pass.

_BAND_WILDCARD = "*"


def _bands_doc(doc: Optional[dict] = None) -> dict:
    return ((doc or load()).get("multiple_bands") or {}).get("bands") or {}


def multiple_band(market: Optional[str], profile: Optional[str], field: str,
                  doc: Optional[dict] = None) -> Optional[tuple[float, float, str]]:
    """(lo, hi, the key that supplied it), or None when no band is authored."""
    bands = _bands_doc(doc)
    mk, pf = (market or _BAND_WILDCARD), (profile or _BAND_WILDCARD)
    for m, p in ((mk, pf), (mk, _BAND_WILDCARD), (_BAND_WILDCARD, pf),
                 (_BAND_WILDCARD, _BAND_WILDCARD)):
        pair = ((bands.get(m) or {}).get(p) or {}).get(field)
        if (isinstance(pair, (list, tuple)) and len(pair) == 2
                and all(isinstance(v, (int, float)) for v in pair)):
            lo, hi = float(pair[0]), float(pair[1])
            if lo < hi:
                return lo, hi, f"{m}/{p}"
    return None


def check_multiples(market: Optional[str], profile: Optional[str],
                    fields: dict, doc: Optional[dict] = None) -> list[dict]:
    """One record per field whose value sits outside its authored band.

    Returns records only. Nothing here rounds, clips or substitutes a multiple:
    the caller keeps the value it had and says so.
    """
    out: list[dict] = []
    for field, value in sorted((fields or {}).items()):
        if not isinstance(value, (int, float)) or value != value:
            continue
        band = multiple_band(market, profile, field, doc)
        if band is None:
            continue
        lo, hi, source_key = band
        if lo <= value <= hi:
            continue
        side = "below" if value < lo else "above"
        edge = lo if value < lo else hi
        out.append({
            "field": field, "value": float(value), "band": [lo, hi],
            "source_key": source_key, "side": side,
            "market": market or _BAND_WILDCARD,
            "profile": profile or _BAND_WILDCARD,
            "distance_pct": (float(value) / edge - 1.0) if edge else None,
        })
    return out


def bands_reviewed(doc: Optional[dict] = None) -> dict:
    """Who last reviewed the band table, and when."""
    return ((doc or load()).get("multiple_bands") or {}).get("reviewed") or {}

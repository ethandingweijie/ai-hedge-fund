"""Guidance-to-forecast engine (owner's five principles, 2026-10-03).

Takes the research's guidance block (FY+1, FY+2 and a medium-term target), the audited
history and the valuation profile, and builds the intermediate years a flat rate would
miss:

  1. Target deconstruction -- the guided endpoints are back-solved into implied EBIT, net
     income, tax and non-operating lines, and checked against each other; the guided
     top-line CAGR is compared with the market's and a spread over 500bp is flagged as
     share capture, capacity or M&A that must be named.
  2. Archetype routing -- the profile selects one of four trajectory curves for the margin
     path (regulatory lag S-curve, operating-leverage J-curve, retail rollout with pre-opening
     drag, platform bifurcation) instead of a straight line.
  3. Three-statement engine -- working-capital absorption from the history's intensity,
     capex as maintenance (D&A) plus growth (capital intensity x new revenue), D&A rolled
     forward, tax and interest from the history, net income, EPS and unlevered FCF per year.
  4. Horizon -- after the target year growth decays linearly to the engine's terminal rate
     over a fade window; terminal ROIC consistency (reinvestment = g / ROIC) and the implied
     exit multiple are reported beside the engine's own terminal.
  5. Invariants -- operating jaws, cash-conversion bounds, share-count integrity, terminal
     multiple against the mid-cycle peer median; the fifth (no engine residue) is the PM
     prompt's rule and is recorded as such.

The output feeds the DCF leg as its growth and FCF-margin schedules (one projection, no
display-only value) and is published for the report, the PDF and the workbook. Every
constant is PROPOSED in valuation_constants.json["guidance_forecast"]; the code defaults
below are the same numbers so a missing block changes nothing.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

PROJECTION_YEARS = 10

DEFAULTS: dict = {
    "fade_years": 4,
    "max_horizon_years": 6,
    "organic_spread_flag": 0.05,
    "cash_conversion_bounds": [0.60, 1.20],
    "capex_alpha_bounds": [0.0, 1.5],
    "nwc_intensity_bounds": [-0.10, 0.50],
    "da_useful_life_years": 10,
    "tax_rate_default": 0.21,
    "tax_rate_bounds": [0.10, 0.35],
    "jaws_margin_step": 0.005,
    "terminal_roic_floor_over_wacc": 0.02,
    "curves": {
        "I":   {"shape": "s_curve", "steepness": 1.6, "year1_lag": 0.15},
        "II":  {"shape": "convex", "power": 2.0},
        "III": {"shape": "linear_drag", "preopen_drag": 0.25},
        "IV":  {"shape": "linear"},
        "G":   {"shape": "linear"},
    },
}

ARCHETYPES = {
    "I":   "Regulatory & contract lag",
    "II":  "Fixed-asset operating leverage",
    "III": "Retail & unit rollout",
    "IV":  "Platform & network scale",
    "G":   "Generic (linear path)",
}

# Explicit profile routing first; then the report family; then the sector.
_PROFILE_ARCHETYPE: dict[str, str] = {
    "Managed Care": "I", "Regulated Utility": "I", "IPP": "I", "Merchant Power": "I", "Defense Primes": "I",
    "Insurance": "I", "Insurance (P&C)": "I", "Regulated Gas": "I", "City Gas Distribution (HK / China)": "I",
    "Capital Goods": "II", "Steel / Metals": "II", "Semi IDM/Foundry": "II", "Semi IDM / Foundry": "II",
    "Automotive (OEM)": "II", "Automotive & EV": "II", "Airlines": "II", "Offshore Marine & Resources (SG)": "II",
    "Engineering & Construction": "II", "EPC Contractor": "II", "Rail / Logistics": "II", "Mining (Major)": "II",
    "Refining & Marketing": "II", "Oilfield Services & Drilling": "II", "Cruise Lines": "II", "Casinos & Integrated Resorts": "II",
    "Traditional Retail": "III", "Specialty Retail": "III", "Restaurants": "III", "Luxury Goods": "III",
    "Apparel & Footwear": "III", "Lodging (Asset-Light)": "III", "Household / Personal": "III",
    "Hyperscaler / Tech Conglomerate": "IV", "China Internet Platform": "IV", "Hyper-Growth Platform": "IV",
    "Mature Platform": "IV", "Mature SaaS": "IV", "Growth SaaS": "IV", "Cybersecurity": "IV", "Online Travel": "IV",
    "Payment Network": "IV", "Payment Networks": "IV",
}
_FAMILY_ARCHETYPE: dict[str, str] = {
    "Banks": "I", "Insurance": "I", "Fee financials": "IV", "Property, REITs and holdcos": "I",
    "Energy and resources": "II", "Consumer": "III", "Health care": "G",
    "Industrials, materials and transport": "II", "Technology, telecom and media": "IV", "Operating company": "G",
}
_SECTOR_ARCHETYPE: dict[str, str] = {"Tech": "IV", "Consumer": "III", "Industrials": "II", "Energy": "I",
                                     "Financials": "I", "RealEstate": "I", "Resources": "II"}


def load_cfg() -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v)) for k, v in DEFAULTS.items()}
    cfg["curves"] = {k: dict(v) for k, v in DEFAULTS["curves"].items()}
    try:
        from src.data import valuation_constants as _vc
        blk = (_vc.load().get("guidance_forecast") or {})
        for k, v in blk.items():
            if k in cfg and k != "curves" and isinstance(v, type(cfg[k])):
                cfg[k] = v
        for code, cv in (blk.get("curves") or {}).items():
            if code in cfg["curves"] and isinstance(cv, dict):
                cfg["curves"][code].update(cv)
    except Exception:  # noqa: BLE001
        pass
    return cfg


def archetype_route(profile_name: Optional[str], sector: Optional[str] = None) -> tuple[str, str, str]:
    """(code, name, reason): the archetype and the routing step that chose it, for the trace."""
    p = (profile_name or "").strip()
    if p in _PROFILE_ARCHETYPE:
        code = _PROFILE_ARCHETYPE[p]
        reason = f"the {p} profile is routed to archetype {code} explicitly"
    else:
        code = None
        reason = ""
        try:
            from src.data.report_families import report_family_for
            fam = report_family_for(p)
            if fam != "Operating company" or not sector:
                code = _FAMILY_ARCHETYPE.get(fam)
                reason = f"no explicit route for the {p or 'unnamed'} profile; its report family ({fam}) routes to archetype {code}"
        except Exception:  # noqa: BLE001
            code = None
        if code in (None, "G") and sector:
            code = _SECTOR_ARCHETYPE.get(sector, code or "G")
            reason = f"no explicit route for the {p or 'unnamed'} profile; the {sector} sector routes to archetype {code}"
        code = code or "G"
        reason = reason or "no profile, family or sector route: the generic linear path"
    return code, ARCHETYPES[code], reason


def archetype_for(profile_name: Optional[str], sector: Optional[str] = None) -> tuple[str, str]:
    code, name, _ = archetype_route(profile_name, sector)
    return code, name


# ── curves: the fraction of the margin gap closed by year t of T ──────────────

def margin_curve(code: str, T: int, cfg: Optional[dict] = None) -> list[float]:
    """w(1..T) in [0, 1] with w(T) = 1. The shape is the archetype's."""
    cfg = cfg or load_cfg()
    cv = (cfg.get("curves") or {}).get(code) or {"shape": "linear"}
    T = max(int(T), 1)
    if T == 1:
        return [1.0]
    shape = cv.get("shape", "linear")
    out: list[float] = []
    if shape == "s_curve":
        k = float(cv.get("steepness", 1.6))
        lo, hi = 1.0 / (1.0 + math.exp(k * (T / 2.0))), 1.0 / (1.0 + math.exp(-k * (T / 2.0)))
        for t in range(1, T + 1):
            w = (1.0 / (1.0 + math.exp(-k * (t - T / 2.0))) - lo) / (hi - lo)
            out.append(w)
        lag = float(cv.get("year1_lag", 0.0))
        out[0] = min(out[0], lag)                         # year 1 absorbs the lag: cost trend outruns rates
    elif shape == "convex":
        p = float(cv.get("power", 2.0))
        out = [(t / T) ** p for t in range(1, T + 1)]
    elif shape == "linear_drag":
        drag = float(cv.get("preopen_drag", 0.25))
        out = [t / T for t in range(1, T + 1)]
        out[0] = out[0] - drag / T                        # pre-opening expense depresses year 1
    else:
        out = [t / T for t in range(1, T + 1)]
    out[-1] = 1.0
    return [round(w, 6) for w in out]


# ── history ───────────────────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _median(vals: list[float], lo: float, hi: float, default: float) -> float:
    vals = [v for v in vals if v is not None and v == v]
    if not vals:
        return default
    return max(lo, min(hi, median(vals)))


def history_ratios(series: list[dict], cfg: dict) -> dict:
    """Activity and intensity ratios from the audited years (medians), with their sources."""
    rows = [r for r in (series or []) if isinstance(r, dict) and _f(r.get("revenue"))]
    last = rows[-1] if rows else {}
    rev0 = _f(last.get("revenue")) or 0.0
    ebit0 = _f(last.get("ebit")) or _f(last.get("operating_income"))
    da0 = _f(last.get("depreciation_and_amortization")) or 0.0
    capex0 = abs(_f(last.get("capital_expenditure")) or 0.0)
    ni0 = _f(last.get("net_income"))
    int0 = abs(_f(last.get("interest_expense")) or 0.0)
    shares0 = _f(last.get("shares_outstanding"))
    taxes, alphas, nwcs, buybacks, roics = [], [], [], [], []
    for i, r in enumerate(rows):
        e, n, it = _f(r.get("ebit")) or _f(r.get("operating_income")), _f(r.get("net_income")), abs(_f(r.get("interest_expense")) or 0.0)
        if e is not None and n is not None and (e - it) > 0:
            taxes.append(1.0 - n / (e - it))
        ic = _f(r.get("invested_capital"))
        if e is not None and ic and ic > 0:
            roics.append(e * (1.0 - cfg["tax_rate_default"]) / ic)
        bb = _f(r.get("share_buyback")) or _f(r.get("common_stock_repurchased"))
        if bb is not None:
            buybacks.append(abs(bb))
        if i > 0:
            drev = (_f(r.get("revenue")) or 0.0) - (_f(rows[i - 1].get("revenue")) or 0.0)
            if drev > 0:
                cx, da = abs(_f(r.get("capital_expenditure")) or 0.0), _f(r.get("depreciation_and_amortization")) or 0.0
                alphas.append((cx - da) / drev)
                cwc = _f(r.get("change_in_working_capital"))
                if cwc is not None:
                    nwcs.append(-cwc / drev)             # cash-flow sign: a negative change absorbs cash
    tb = cfg["tax_rate_bounds"]
    return {
        "revenue": rev0, "ebit": ebit0, "ebit_margin": (ebit0 / rev0) if (ebit0 is not None and rev0) else None,
        "da": da0, "da_pct_revenue": (da0 / rev0) if rev0 else 0.0, "capex": capex0, "net_income": ni0,
        "interest": int0, "shares": shares0,
        "tax_rate": _median(taxes, tb[0], tb[1], cfg["tax_rate_default"]), "tax_rate_source": "history" if taxes else "default",
        "capex_alpha": _median(alphas, cfg["capex_alpha_bounds"][0], cfg["capex_alpha_bounds"][1], 0.0), "capex_alpha_n": len(alphas),
        "nwc_intensity": _median(nwcs, cfg["nwc_intensity_bounds"][0], cfg["nwc_intensity_bounds"][1], 0.0), "nwc_n": len(nwcs),
        "buyback_median": _median(buybacks, 0.0, float("inf"), 0.0), "roic_median": (_median(roics, -1.0, 2.0, float("nan")) if roics else None),
        "years": len(rows),
    }


# ── the targets ───────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def targets_for(block: dict, scenario: str, hist: dict) -> dict:
    """The endpoints the guidance gives this scenario: FY+1, FY+2 and the medium-term target."""
    est = ((block or {}).get("estimates") or {}).get(scenario) or {}
    mt = (block or {}).get("medium_term_target") or {}
    g1, g2 = _num(est.get("revenue_growth_fy1")), _num(est.get("revenue_growth_fy2"))
    m1, m2 = _num(est.get("ebitda_margin_fy1")), _num(est.get("ebitda_margin_fy2"))
    e1, e2 = _num(est.get("eps_fy1")), _num(est.get("eps_fy2"))
    out = {"g1": g1, "g2": g2, "m1": m1, "m2": m2, "eps1": e1, "eps2": e2, "T": 2 if g2 is not None else (1 if g1 is not None else 0),
           "target": None}
    if mt and mt.get("metric") and mt.get("target_year"):
        pick = {"bear": "low", "base": "mid", "bull": "high"}[scenario]
        val = _num(mt.get(pick))
        if val is None:
            val = _num(mt.get("mid"))
        fy1 = str((block or {}).get("fiscal_year_1") or "")
        y1 = _year(fy1)
        yT = _year(str(mt.get("target_year")))
        if val is not None and y1 and yT and yT > y1:
            out["target"] = {"metric": str(mt["metric"]), "year_index": yT - y1 + 1, "target_year": str(mt["target_year"]),
                             "value": val, "unit": mt.get("unit"), "basis": mt.get("basis"), "source": mt.get("source")}
    return out


def _year(s: str) -> Optional[int]:
    import re
    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group(0)) if m else None


def deconstruct(targets: dict, hist: dict, shares: float, cfg: dict, market_growth: Optional[float]) -> dict:
    """Principle 1: back-solve the unstated lines of the target year and test their compatibility."""
    tax = hist["tax_rate"]
    interest = hist["interest"]
    rev0 = hist["revenue"] or 0.0
    out: dict = {"flags": []}
    tgt = targets.get("target")
    T = targets["T"]
    rev_path = [rev0]
    for g in (targets["g1"], targets["g2"]):
        if g is not None:
            rev_path.append(rev_path[-1] * (1.0 + g))
    revT = rev_path[-1]
    horizon = T
    out["bridge_growth"] = []
    if tgt and tgt["year_index"] > T:
        horizon = min(int(tgt["year_index"]), int(cfg["max_horizon_years"]))
        if tgt["metric"] in ("revenue",):
            revT = float(tgt["value"])
        elif tgt["metric"] == "revenue_growth":
            revT = rev_path[-1] * (1.0 + float(tgt["value"])) ** (horizon - T)
        else:
            # An EPS or margin target says nothing about revenue: the last guided growth rate
            # steps linearly toward the market's (else holds) over the bridge years, instead of
            # flat-lining at zero (the first build's quirk, seen on the Molina trace).
            g_last = targets["g2"] if targets["g2"] is not None else (targets["g1"] or 0.0)
            g_end = market_growth if market_growth is not None else g_last
            n = horizon - T
            for i in range(1, n + 1):
                g_i = g_last + (g_end - g_last) * i / float(n + 1)
                out["bridge_growth"].append(g_i)
                revT *= (1.0 + g_i)
    out["horizon_years"] = horizon
    out["revenue_T"] = revT
    cagr = (revT / rev0) ** (1.0 / horizon) - 1.0 if (rev0 > 0 and horizon > 0 and revT > 0) else None
    out["guided_cagr"] = cagr
    out["market_cagr"] = market_growth
    if cagr is not None and market_growth is not None and cagr - market_growth > float(cfg["organic_spread_flag"]):
        out["flags"].append(f"Guided revenue CAGR {cagr:.1%} runs {(cagr - market_growth) * 1e4:,.0f}bp above the market's {market_growth:.1%}: "
                            "assign the excess to share capture, capacity additions or M&A")
    # the target-year margin and earnings
    mT = targets["m2"] if targets["m2"] is not None else targets["m1"]
    epsT = targets["eps2"] if targets["eps2"] is not None else targets["eps1"]
    if tgt and tgt["year_index"] > T:
        if tgt["metric"] == "eps":
            epsT = float(tgt["value"])
        elif tgt["metric"] in ("ebitda_margin", "operating_margin", "ebit_margin"):
            mT = float(tgt["value"])
    out["margin_T_guided"] = mT
    out["eps_T_guided"] = epsT
    ebit_T = revT * mT if mT is not None else None
    ni_T_from_eps = epsT * shares if (epsT is not None and shares) else None
    ni_T_from_ebit = (ebit_T - interest) * (1.0 - tax) if ebit_T is not None else None
    out["ebit_T_implied"] = ebit_T
    out["net_income_T_from_eps"] = ni_T_from_eps
    out["net_income_T_from_margin"] = ni_T_from_ebit
    if ni_T_from_eps is not None and ebit_T is not None and ebit_T - interest > 0:
        implied_tax = 1.0 - ni_T_from_eps / (ebit_T - interest)
        out["implied_tax_rate"] = implied_tax
        if not (0.0 <= implied_tax <= 0.45):
            out["flags"].append(f"Guided EPS and guided margin are not compatible: they imply a {implied_tax:.0%} tax-and-non-operating take "
                                f"(history {tax:.0%}); one endpoint is wrong or the share count moves")
    if ni_T_from_eps is not None and ebit_T is None:
        # back-solve the EBIT the EPS needs at the history's tax and interest
        out["ebit_T_implied"] = ni_T_from_eps / (1.0 - tax) + interest
        out["margin_T_implied"] = out["ebit_T_implied"] / revT if revT else None
    return out


# ── the forecast ──────────────────────────────────────────────────────────────

def build_forecast(block: dict, *, scenario: str, series: list[dict], profile_name: str, sector: str,
                   wacc: float, tgr: float, shares: float, net_debt: float, spot: Optional[float] = None,
                   peer_ev_ebitda: Optional[float] = None, market_growth: Optional[float] = None,
                   engine_growth_path: Optional[list[float]] = None, fcf_margin_base: Optional[float] = None,
                   cfg: Optional[dict] = None, hist: Optional[dict] = None, overrides: Optional[dict] = None) -> Optional[dict]:
    """The intermediate years from today to the guided endpoints, by the owner's five principles.

    Returns the per-year table, the schedules the DCF leg runs on (growth and FCF margin for
    PROJECTION_YEARS years), the deconstruction, the terminal diagnostics and the invariants;
    None when the block carries no FY+1 revenue growth for this scenario.

    `hist` is a precomputed `history_ratios` result (the interactive recompute passes the one the
    run stored, so no statements are re-read); `overrides` are the user's: `fade_years` on the
    config, `tax_rate`, `capex_alpha`, `nwc_intensity` on the history. The estimate overrides
    (growth, margins, EPS, the medium-term target) are applied to `block` by the caller."""
    cfg = dict(cfg or load_cfg())
    ov = dict(overrides or {})
    if not block or not shares or shares <= 0:
        return None
    if _num(ov.get("fade_years")) is not None:
        cfg["fade_years"] = int(max(1, min(8, round(float(ov["fade_years"])))))
    hist = dict(hist) if hist else history_ratios(series, cfg)
    applied: dict = {}
    for k in ("tax_rate", "capex_alpha", "nwc_intensity"):
        if _num(ov.get(k)) is not None:
            hist[k] = float(ov[k])
            applied[k] = float(ov[k])
            if k == "tax_rate":
                hist["tax_rate_source"] = "user override"
    if _num(ov.get("fade_years")) is not None:
        applied["fade_years"] = cfg["fade_years"]
    roic_user = _num(ov.get("terminal_roic"))
    if roic_user is not None:
        applied["terminal_roic"] = float(roic_user)
    if not hist.get("revenue"):
        return None
    tg = targets_for(block, scenario, hist)
    if tg["g1"] is None:
        return None
    code, arche, arche_reason = archetype_route(profile_name, sector)
    dec = deconstruct(tg, hist, shares, cfg, market_growth)
    T = int(dec["horizon_years"])
    rev0, m0 = hist["revenue"], hist["ebit_margin"] if hist["ebit_margin"] is not None else 0.0
    tax, interest = hist["tax_rate"], hist["interest"]
    mT = dec.get("margin_T_guided") if dec.get("margin_T_guided") is not None else dec.get("margin_T_implied")
    if mT is None:
        mT = m0                                             # no margin endpoint: hold the margin, growth only
        margin_source = "held at history (no guided margin)"
    else:
        margin_source = "guided" if dec.get("margin_T_guided") is not None else "implied by guided EPS"
    curve = margin_curve(code, T, cfg)
    # revenue path: the guided years, then a constant CAGR to the target revenue
    revs = [rev0]
    for g in (tg["g1"], tg["g2"]):
        if g is not None and len(revs) <= T:
            revs.append(revs[-1] * (1.0 + g))
    if len(revs) - 1 < T:
        rem = T - (len(revs) - 1)
        bridge = dec.get("bridge_growth") or []
        if len(bridge) == rem:
            for g_i in bridge:
                revs.append(revs[-1] * (1.0 + g_i))
        else:
            step = (dec["revenue_T"] / revs[-1]) ** (1.0 / rem) if revs[-1] > 0 and dec["revenue_T"] > 0 else 1.0
            for _ in range(rem):
                revs.append(revs[-1] * step)
    # fade: growth decays linearly to tgr over fade_years, then tgr to year 10
    gT = revs[-1] / revs[-2] - 1.0 if len(revs) > 1 and revs[-2] > 0 else tg["g1"]
    F = int(cfg["fade_years"])
    growth_sched: list[float] = []
    for t in range(1, PROJECTION_YEARS + 1):
        if t <= T:
            growth_sched.append(revs[t] / revs[t - 1] - 1.0)
        elif t <= T + F:
            growth_sched.append(gT + (float(tgr) - gT) * (t - T) / float(F + 1))
        else:
            growth_sched.append(float(tgr))
    # extend revenue to year 10 on the faded growth
    rev_all = [rev0]
    for t in range(1, PROJECTION_YEARS + 1):
        rev_all.append(rev_all[-1] * (1.0 + growth_sched[t - 1]))
    # margin path: the archetype curve to the target margin, held after T (steady state)
    margins = []
    for t in range(1, PROJECTION_YEARS + 1):
        w = curve[t - 1] if t <= T else 1.0
        margins.append(m0 + (mT - m0) * w)
    # three statements
    da_prev, life = hist["da"], float(cfg["da_useful_life_years"])
    alpha, nwc_i = hist["capex_alpha"], hist["nwc_intensity"]
    shares_path = [float(shares)]
    epsT = dec.get("eps_T_guided")
    rows: list[dict] = []
    for t in range(1, PROJECTION_YEARS + 1):
        rev, rev_prev = rev_all[t], rev_all[t - 1]
        ebit = rev * margins[t - 1]
        nopat = ebit * (1.0 - tax)
        da = da_prev
        capex = da + alpha * max(rev - rev_prev, 0.0)
        dnwc = nwc_i * (rev - rev_prev)
        ufcf = nopat + da - capex - dnwc
        ni = (ebit - interest) * (1.0 - tax)
        sh = shares_path[-1]
        rows.append({"year": t, "revenue": rev, "growth": growth_sched[t - 1], "ebit_margin": margins[t - 1], "ebit": ebit,
                     "tax": (ebit - interest) * tax if ebit > interest else 0.0, "nopat": nopat, "da": da, "capex": capex,
                     "capex_maintenance": da, "capex_growth": capex - da, "delta_nwc": dnwc, "ufcf": ufcf,
                     "net_income": ni, "shares": sh, "eps": (ni / sh) if sh else None, "fcf_margin": (ufcf / rev) if rev else 0.0,
                     "phase": "guided" if t <= T else ("fade" if t <= T + F else "steady")})
        da_prev = da + (capex - da) / life
        shares_path.append(sh)
    # share-count integrity: the EPS endpoint implies a share count; the buyback that gets there
    inv: list[dict] = []
    if epsT is not None and rows[T - 1]["net_income"] and epsT > 0:
        implied_shares = rows[T - 1]["net_income"] / epsT
        d_sh = float(shares) - implied_shares
        price = spot if (spot and spot > 0) else None
        need = d_sh * price if (price and d_sh > 0) else None
        ok = None
        detail = (f"guided EPS implies {implied_shares / 1e6:,.0f}m shares in the target year vs {shares / 1e6:,.0f}m today"
                  + (f"; {d_sh / 1e6:,.0f}m to retire needs about {need / 1e9:,.1f}bn of buybacks over {T} years "
                     f"against a {hist['buyback_median'] / 1e9:,.1f}bn a year history" if need is not None else ""))
        if need is not None and hist["buyback_median"] >= 0:
            ok = need <= hist["buyback_median"] * T * 1.5 + 1e-9
        elif d_sh <= 0:
            ok = True
        inv.append({"id": 3, "name": "Share-count integrity", "ok": ok, "detail": detail})
        for r in rows:
            r["eps"] = (r["net_income"] / implied_shares) if (r["year"] >= T and implied_shares > 0) else r["eps"]
    # operating jaws
    jaws = []
    for t in range(2, T + 1):
        r, p = rows[t - 1], rows[t - 2]
        if r["growth"] < p["growth"] and r["ebit_margin"] > p["ebit_margin"] + float(cfg["jaws_margin_step"]):
            jaws.append(t)
    catalyst = (margin_source == "guided") or code in ("I", "II")
    inv.insert(0, {"id": 1, "name": "Operating jaws", "ok": (True if not jaws else (None if catalyst else False)),
                   "detail": ("no year expands margin into decelerating growth" if not jaws else
                              f"years {jaws} expand margin while growth slows; catalyst: "
                              + ("guided margin" if margin_source == "guided" else
                                 "the archetype's catch-up (rate resets / fixed-cost absorption)" if code in ("I", "II") else "NONE NAMED"))})
    # cash conversion in the steady state
    lo, hi = cfg["cash_conversion_bounds"]
    steady = [r for r in rows if r["phase"] != "guided" and r["net_income"] > 0]
    conv = [r["ufcf"] / r["net_income"] for r in steady] if steady else []
    conv_med = median(conv) if conv else None
    inv.insert(1, {"id": 2, "name": "Cash-conversion bounds", "ok": (lo <= conv_med <= hi) if conv_med is not None else None,
                   "detail": (f"steady-state UFCF / net income {conv_med:.2f}x (bounds {lo:.2f}-{hi:.2f}); capex alpha {alpha:.2f}, "
                              f"NWC intensity {nwc_i:.2f} of new revenue" if conv_med is not None else "no positive steady-state net income")})
    # terminal: ROIC consistency and the implied exit multiple against the mid-cycle peer median
    last = rows[-1]
    roic_T = hist["roic_median"] if (hist["roic_median"] is not None and hist["roic_median"] == hist["roic_median"]) else None
    roic_terminal = max(float(wacc) + float(cfg["terminal_roic_floor_over_wacc"]), roic_T or 0.0)
    if roic_user is not None:
        roic_terminal = float(roic_user)                  # the user's terminal ROIC, as entered
    reinvest = float(tgr) / roic_terminal if roic_terminal > 0 else None
    tv = last["ufcf"] * (1.0 + float(tgr)) / (float(wacc) - float(tgr)) if float(wacc) > float(tgr) else None
    ebitda_T = last["ebit"] + last["da"]
    exit_mult = (tv / ebitda_T) if (tv and ebitda_T > 0) else None
    inv.append({"id": 4, "name": "Terminal multiple bounds", "ok": (exit_mult <= peer_ev_ebitda) if (exit_mult is not None and peer_ev_ebitda) else None,
                "detail": (f"implied exit EV/EBITDA {exit_mult:.1f}x vs mid-cycle peer median {peer_ev_ebitda:.1f}x" if (exit_mult is not None and peer_ev_ebitda)
                           else "no peer median or no positive terminal EBITDA")})
    inv.append({"id": 5, "name": "No engine residue", "ok": None, "detail": "held by the PM rationale prompt's voice and rounding rules"})
    fcf_sched = [r["fcf_margin"] for r in rows]
    steps = _trace_steps(scenario=scenario, block=block, tg=tg, code=code, arche=arche, arche_reason=arche_reason, dec=dec, T=T, F=F,
                         tax=tax, rows=rows, rev0=rev0, rev_all=rev_all, growth_sched=growth_sched, m0=m0, mT=mT, margin_source=margin_source,
                         curve=curve, hist=hist, interest=interest, alpha=alpha, nwc_i=nwc_i, life=life, gT=gT, tgr=tgr, wacc=wacc,
                         roic_terminal=roic_terminal, reinvest=reinvest, exit_mult=exit_mult, peer_ev_ebitda=peer_ev_ebitda, inv=inv,
                         fcf_sched=fcf_sched, fcf_margin_base=fcf_margin_base, cfg=cfg, applied=applied)
    return {
        "scenario": scenario, "archetype": code, "archetype_name": arche, "archetype_reason": arche_reason, "horizon_years": T, "fade_years": F,
        "margin_source": margin_source, "margin_start": m0, "margin_target": mT, "curve": curve,
        "deconstruction": {k: v for k, v in dec.items() if k != "flags"}, "flags": dec["flags"],
        "history": {k: hist.get(k) for k in ("revenue", "ebit", "ebit_margin", "da", "da_pct_revenue", "capex", "net_income", "interest", "shares",
                                              "tax_rate", "tax_rate_source", "capex_alpha", "capex_alpha_n", "nwc_intensity", "nwc_n",
                                              "buyback_median", "roic_median", "years")},
        "inputs": {"wacc": float(wacc), "tgr": float(tgr), "shares": float(shares), "net_debt": float(net_debt or 0.0), "spot": spot,
                   "peer_ev_ebitda": peer_ev_ebitda, "market_growth": market_growth, "profile_name": profile_name, "sector": sector,
                   "fcf_margin_base": fcf_margin_base, "engine_growth_path": engine_growth_path},
        "overrides_applied": applied or None,
        "steps": steps,
        "rows": rows, "growth_schedule": [round(g, 6) for g in growth_sched], "fcf_margin_schedule": [round(m, 6) for m in fcf_sched],
        "terminal": {"tgr": float(tgr), "wacc": float(wacc), "roic_terminal": roic_terminal, "reinvestment_rate": reinvest,
                     "implied_exit_ev_ebitda": exit_mult, "peer_ev_ebitda_median": peer_ev_ebitda},
        "invariants": inv,
        "target": tg.get("target"),
    }


def _pc(v) -> str:
    return f"{float(v):.1%}" if _num(v) is not None else "n/a"


def _bn(v) -> str:
    return f"{float(v) / 1e9:,.2f}bn" if _num(v) is not None else "n/a"


def _trace_steps(**k) -> list[dict]:
    """The thinking shown on the page and in the exports: one step per principle, with the numbers
    the build actually used (owner, 2026-10-03: the user sees the agent work the valuation out)."""
    tg, dec, rows, T, F = k["tg"], k["dec"], k["rows"], k["T"], k["F"]
    tgt = tg.get("target")
    rT = rows[T - 1]
    gs, fs, curve, inv = k["growth_sched"], k["fcf_sched"], k["curve"], k["inv"]
    read = f"{k['scenario']} case: FY+1 revenue growth {_pc(tg['g1'])}"
    if tg["g2"] is not None:
        read += f", FY+2 {_pc(tg['g2'])}"
    if tg["m1"] is not None:
        read += f"; margin FY+1 {_pc(tg['m1'])}"
    if tg["m2"] is not None:
        read += f", FY+2 {_pc(tg['m2'])}"
    if tg["eps1"] is not None:
        read += f"; EPS FY+1 {tg['eps1']:,.2f}"
    if tg["eps2"] is not None:
        read += f", FY+2 {tg['eps2']:,.2f}"
    if tgt:
        read += f"; medium-term target: {tgt['metric']} {tgt['value']:,.2f} in {tgt['target_year']}" + (f" ({tgt['source']})" if tgt.get("source") else "")
    else:
        read += "; no medium-term target"
    read += f". Confidence {k['block'].get('confidence') or 'n/a'}."
    back = f"Horizon {T} years; target-year revenue {_bn(dec.get('revenue_T'))}"
    if dec.get("ebit_T_implied") is not None:
        back += f", implied EBIT {_bn(dec.get('ebit_T_implied'))}"
    if dec.get("implied_tax_rate") is not None:
        back += f", implied tax-and-non-operating take {_pc(dec.get('implied_tax_rate'))} (history {_pc(k['tax'])})"
    if dec.get("guided_cagr") is not None and dec.get("market_cagr") is not None:
        back += f"; guided CAGR {_pc(dec.get('guided_cagr'))} vs market {_pc(dec.get('market_cagr'))}"
    back += (". " + " ".join(dec["flags"])) if dec.get("flags") else ". Endpoints are compatible."
    three = (f"Tax {_pc(k['tax'])} ({k['hist'].get('tax_rate_source')}), interest {_bn(k['interest'])} a year, capex = D&A + {k['alpha']:.2f} x new revenue, "
             f"working capital absorbs {k['nwc_i']:.2f} of new revenue, D&A rolls forward over {k['life']:.0f} years. Year {T}: net income {_bn(rT['net_income'])}"
             + (f", EPS {rT['eps']:,.2f}" if _num(rT.get("eps")) is not None else "") + f", UFCF {_bn(rT['ufcf'])} ({_pc(rT['fcf_margin'])} of revenue).")
    term = (f"After year {T} growth fades linearly from {_pc(k['gT'])} to the terminal {_pc(k['tgr'])} over {F} years, margin held at {_pc(k['mT'])}. "
            f"Terminal ROIC {_pc(k['roic_terminal'])} (at least WACC {_pc(k['wacc'])} + {_pc(k['cfg']['terminal_roic_floor_over_wacc'])}), reinvestment g/ROIC {_pc(k['reinvest'])}")
    if k["exit_mult"] is not None and k["peer_ev_ebitda"]:
        term += f"; implied exit EV/EBITDA {k['exit_mult']:.1f}x vs peer median {k['peer_ev_ebitda']:.1f}x"
    term += "."
    hand = "Growth " + ", ".join(_pc(g) for g in gs) + "; FCF margin " + ", ".join(_pc(m) for m in fs)
    hand += f". The engine's own year-1 FCF margin was {_pc(k['fcf_margin_base'])}." if k["fcf_margin_base"] is not None else "."
    steps = [
        {"n": 1, "title": "Read the guidance", "detail": read},
        {"n": 2, "title": "Route the archetype", "detail": f"{k['arche']} (archetype {k['code']}): {k['arche_reason']}. The margin path takes this archetype's curve shape."},
        {"n": 3, "title": "Back-solve the target year", "detail": back},
        {"n": 4, "title": "Lay the revenue path",
         "detail": "Guided years: " + ", ".join(f"Y{i + 1} {_pc(gs[i])}" for i in range(T)) + f"; revenue {_bn(k['rev0'])} to {_bn(k['rev_all'][T])} by year {T}."},
        {"n": 5, "title": "Shape the margin path",
         "detail": f"EBIT margin {_pc(k['m0'])} to {_pc(k['mT'])} ({k['margin_source']}); the gap closes " + ", ".join(f"{w:.0%}" for w in curve) + f" of the way over years 1-{T}."},
        {"n": 6, "title": "Build the three statements", "detail": three},
        {"n": 7, "title": "Fade and terminal", "detail": term},
        {"n": 8, "title": "Run the invariants",
         "detail": "; ".join(f"{i['name']}: " + ("PASS" if i["ok"] is True else "FAIL" if i["ok"] is False else "n/a") for i in inv) + "."},
        {"n": 9, "title": "Hand the schedules to the DCF", "detail": hand},
    ]
    if k["applied"]:
        steps.insert(0, {"n": 0, "title": "User overrides in force", "detail": ", ".join(f"{a} = {b}" for a, b in k["applied"].items()) + "."})
    return steps


def summary(fc: Optional[dict]) -> Optional[dict]:
    """The report block: everything but the per-year rows' internals that only the workbook needs."""
    if not fc:
        return None
    keep = ("year", "revenue", "growth", "ebit_margin", "ebit", "net_income", "eps", "ufcf", "fcf_margin", "capex", "capex_growth", "delta_nwc", "phase")
    return {**{k: v for k, v in fc.items() if k not in ("rows", "curve")},
            "rows": [{k: r.get(k) for k in keep} for r in fc["rows"]]}

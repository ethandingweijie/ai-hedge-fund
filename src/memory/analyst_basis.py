"""Analyst valuation basis — the sell-side method and its parameters.

Every broker note states, in one line, which model produced its target and
what went into it:

    "Discounted Cash-Flow, WACC 6.3%, g 3.5%"                  (Apple)
    "Gordon Growth Model (COE: 8.6%, g: 3.3%)"                 (DBS)
    "DDM (Cost of Equity: 6.83%; Terminal g: 1.75%)"           (Keppel DC REIT)
    "SOTP valuation"                                           (Keppel)
    "EV/Adj. EBITDA@9x FY25e + DPN book value"                 (Sembcorp)

`assumption_store.analyst_reports.pt_methodology_json` already captures
that line verbatim, but as prose — so the numbers sit in the database
unusable. This module parses it into a structured basis and exposes it as
an INITIAL BENCHMARK for the valuation engine.

Two rules govern how it is used, and both matter:

  * Profile routing is unaffected. The analyst's choice of method never
    reroutes a ticker — a single mis-extraction would otherwise redirect a
    whole valuation, and the profile system is ours to own. Where the
    analyst's method disagrees with the profile's anchor, that is surfaced
    as a flag for a human, not acted on.

  * The basis outranks our defaults ONLY for discount-rate parameters —
    cost of equity, WACC, terminal growth, holdco discount. No issuer
    discloses those; they are analyst constructs, so a published table
    beats a profile default. Anything derived from the financials stays
    ours.
"""

from __future__ import annotations

import re
from typing import Any, Optional

__all__ = [
    "parse_pt_methodology",
    "get_analyst_basis",
    "get_analyst_thesis",
    "METHOD_ANCHORS",
]


# Canonical method → the engine method name that anchors that profile.
# Used only to detect disagreement; never to select a profile.
METHOD_ANCHORS: dict[str, tuple[str, ...]] = {
    "dcf":       ("DCF", "Owner Earnings DCF"),
    "ggm_pb":    ("GGM (P/B)",),
    "ddm":       ("DDM (S-REIT)", "DDM"),
    "sotp":      ("SOTP", "SOTP (analyst)"),
    "ev_ebitda": ("EV/EBITDA", "Forward EV/EBITDA"),
    "pe":        ("P/E (norm)", "Forward P/E"),
    "nav":       ("NAV (Cap Rates)",),
}

# Ordered: the first pattern that matches wins, so the more specific
# models are tested before the bare-multiple fallbacks. "Gordon Growth"
# must beat "growth"; DDM must beat the generic dividend wording.
_METHOD_PATTERNS: list[tuple[str, str]] = [
    ("ggm_pb",    r"gordon\s+growth|\bggm\b|excess\s+return"),
    ("ddm",       r"\bddm\b|dividend\s+discount|distribution\s+discount"),
    ("sotp",      r"\bsotp\b|sum[-\s]of[-\s]the[-\s]parts"),
    ("dcf",       r"\bdcf\b|discounted\s+cash[-\s]?flow"),
    ("nav",       r"\brnav\b|\bnav\b|net\s+asset\s+value|cap\s+rate"),
    ("ev_ebitda", r"ev\s*/\s*(adj\.?\s*)?ebitda|ev/ebitda"),
    # Slash optional: Asian notes write "28x PE" and "PER", not "P/E".
    # The basis detector below already used `/?`, so Sheng Siong's
    # "28x PE valuations rolled over to FY27e" resolved a multiple_basis
    # of "pe" while leaving `method` unset — the two patterns disagreed
    # about the same notation.
    ("pe",        r"\bp\s*/?\s*e\b|price[-\s]earnings|\bper\b"),
]

_PCT = r"([0-9]{1,2}(?:\.[0-9]{1,2})?)\s*%"


def _pct(pattern: str, text: str) -> Optional[float]:
    """First percentage captured by `pattern`, as a decimal."""
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    try:
        v = float(m.group(m.lastindex or 1)) / 100.0
    except (TypeError, ValueError):
        return None
    # A rate outside 0-40% is a mis-parse (a price, a year, a share count).
    return v if 0.0 <= v <= 0.40 else None


def parse_pt_methodology(text: str) -> dict[str, Any]:
    """Structure a broker's methodology line.

    Returns a dict with `method` (canonical key or None) plus whichever of
    `wacc`, `cost_of_equity`, `terminal_growth`, `target_multiple`,
    `multiple_basis` and `holdco_discount` the line discloses. Absent
    fields are omitted rather than guessed — a missing parameter must fall
    through to the profile default, not to zero.
    """
    out: dict[str, Any] = {"raw": (text or "").strip()}
    if not text:
        return out
    t = " ".join(str(text).split())

    for canonical, pat in _METHOD_PATTERNS:
        if re.search(pat, t, re.I):
            out["method"] = canonical
            break

    # Cost of equity — "COE: 8.6%", "Cost of Equity 6.83%", "CoE:7%"
    coe = _pct(r"(?:cost\s+of\s+equity|\bco\.?e\b)\s*[:=]?\s*" + _PCT, t)
    if coe is not None:
        out["cost_of_equity"] = coe

    # WACC — "WACC of 6.3%", "WACC 6.3%"
    wacc = _pct(r"\bwacc\b\s*(?:of)?\s*[:=]?\s*" + _PCT, t)
    if wacc is not None:
        out["wacc"] = wacc

    # Terminal growth — "terminal growth of 3.5%", "Terminal g: 1.75%",
    # "g 3.3%". The bare "g" form is tried last so it cannot swallow a
    # percentage belonging to another field.
    # Both orders occur in the wild: "Terminal g: 1.75%" and, from OCBC on
    # CapitaLand India Trust, "DCF with 2.75% terminal growth rate" — the
    # rate leads. Matching only the trailing form silently dropped a stated
    # assumption and fell back to the profile default.
    g = (_pct(r"terminal\s+(?:growth|g)\s*(?:rate)?\s*(?:of)?\s*[:=]?\s*" + _PCT, t)
         or _pct(_PCT + r"\s*(?:terminal|long[-\s]term|perpetual)\s+growth", t)
         or _pct(r"long[-\s]term\s+growth\s*[:=]?\s*" + _PCT, t)
         or _pct(r"(?<![a-z])g\s*[:=]?\s*" + _PCT, t))
    if g is not None:
        out["terminal_growth"] = g

    # Holdco / conglomerate discount — "applying a 10% holding discount",
    # "30% discount to book".
    hd = _pct(r"" + _PCT + r"\s*(?:holdco|holding(?:\s+company)?)\s+discount", t)
    if hd is None:
        hd = _pct(r"(?:holdco|holding(?:\s+company)?)\s+discount\s*(?:of)?\s*[:=]?\s*" + _PCT, t)
    if hd is not None:
        out["holdco_discount"] = hd

    # Target multiple — "9x EV/EBITDA", "EV/Adj. EBITDA@9x", "15x PE",
    # "2.51x FY26e P/BV".
    mult = None
    basis = None
    m = re.search(r"([0-9]{1,3}(?:\.[0-9]{1,2})?)\s*x", t, re.I)
    if m:
        try:
            mult = float(m.group(1))
        except ValueError:
            mult = None
    if mult is not None and 0.1 <= mult <= 200:
        if re.search(r"ev\s*/\s*(adj\.?\s*)?ebitda", t, re.I):
            basis = "ev_ebitda"
        elif re.search(r"p\s*/\s*bv?\b|price[-\s]to[-\s]book", t, re.I):
            basis = "p_b"
        elif re.search(r"p\s*/\s*nav", t, re.I):
            basis = "p_nav"
        elif re.search(r"p\s*/\s*s\b|price[-\s]sales", t, re.I):
            basis = "p_s"
        elif re.search(r"\bp\s*/?\s*e\b", t, re.I):
            basis = "pe"
        out["target_multiple"] = mult
        if basis:
            out["multiple_basis"] = basis

    return out


def get_analyst_basis(ticker: str) -> Optional[dict[str, Any]]:
    """Latest parsed valuation basis for `ticker`, or None.

    Soft-fails to None on any store error so the valuation engine is never
    blocked by a missing or malformed report.
    """
    try:
        from src.memory import assumption_store
        reports = assumption_store.get_analyst_reports(ticker, limit=1)
    except Exception:
        return None
    if not reports:
        return None

    rep = reports[0] or {}
    basis = parse_pt_methodology(rep.get("pt_methodology") or "")
    if not basis.get("method") and len(basis) <= 1:
        return None

    basis["house"] = rep.get("house") or ""
    basis["as_of"] = rep.get("report_date") or ""
    basis["rating"] = rep.get("rating") or ""
    basis["price_target"] = rep.get("price_target") or ""
    basis["price_target_currency"] = rep.get("price_target_currency") or ""
    return basis


def get_analyst_thesis(ticker: str) -> Optional[dict[str, Any]]:
    """Latest sell-side thesis, catalysts and risks for `ticker`, or None.

    Market-agnostic: the shape is identical for US, HKEX and SGX rows
    (`points` / `catalysts` / `risks`), so one accessor serves all three.

    This is a REFERENCE VIEW, not an instruction. It is another analyst's
    argument, published on a date, and the engine's own numbers are not
    downstream of it. Callers must present it as a view to weigh — see the
    prompt rule in portfolio_manager, which requires the thesis to be
    engaged with and any disagreement stated, rather than restated.

    Soft-fails to None so a missing or malformed store never blocks a
    decision.
    """
    try:
        from src.memory import assumption_store
        reports = assumption_store.get_analyst_reports(ticker, limit=1)
    except Exception:
        return None
    if not reports:
        return None

    rep = reports[0] or {}
    thesis = rep.get("thesis") or {}
    if isinstance(thesis, str):
        try:
            import json as _json
            thesis = _json.loads(thesis)
        except Exception:
            thesis = {}
    if not isinstance(thesis, dict):
        return None

    def _clean(seq) -> list[str]:
        out: list[str] = []
        for item in (seq or []):
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out

    points = _clean(thesis.get("points"))
    catalysts = _clean(thesis.get("catalysts"))
    risks = _clean(thesis.get("risks"))
    if not (points or catalysts or risks):
        return None

    return {
        "points":        points,
        "catalysts":     catalysts,
        "risks":         risks,
        "house":         rep.get("house") or "",
        "analyst":       rep.get("analyst") or "",
        "as_of":         rep.get("report_date") or "",
        "rating":        rep.get("rating") or "",
        "price_target":  rep.get("price_target") or "",
    }


def method_disagrees(basis: Optional[dict], anchor_method: str) -> bool:
    """True when the analyst's method is not the profile's anchor.

    Reported as a flag only. Profile routing is ours; a broker's framing
    does not get to redirect it.
    """
    if not basis or not basis.get("method"):
        return False
    expected = METHOD_ANCHORS.get(basis["method"], ())
    return bool(expected) and anchor_method not in expected

"""Report disclosures, target methodology, the Street comparison and data limitations.

Owner, 2026-11-25 (CFA-grade report audit). One deterministic builder, read by the PDF
and published as ``data.report_disclosures[ticker]`` so the web report can render the
same items. Nothing here computes a valuation: every figure is read from the run.

Shape (all keys always present; values may be None or empty):
  horizon               "12 months"
  rating_definition     the published rating definition (src/decisions/ratings.py)
  regulatory_mapping    Overweight = Buy, Neutral = Hold, Underweight = Sell
  benchmark             {name, expected_return, basis} or None
  ai_disclosure         plain-English statement that the text is AI-generated, and by which model
  model                 the model that wrote the text, when recorded
  data_sources          list of sources
  as_of                 the price / data date
  limitations           standing limitations of the method
  target_methodology    {text, rule, capture, capture_reason, method, scenarios: [...]}
  vs_consensus          {applies, text, our_target, street_low, street_high, street_mean, driver, facts}
  priced_in             {applies, text, spot, implied_cagr10, model_cagr10, own_cagr, own_years} -- the reverse DCF
  data_limitations      list of plain-English strings for this run
"""
from __future__ import annotations

import re
from typing import Optional

HORIZON_LABEL = "12 months"

STANDING_LIMITATIONS = [
    "Intrinsic value rests on forecasts of revenue, margins and cost of capital; small changes in these "
    "inputs move the value materially.",
    "The 12-month target closes only part of the gap between today's price and intrinsic value; it is a "
    "rule-based estimate, not a forecast of where the price will trade.",
    "Financial data comes from a third-party provider and company filings and may contain errors or lags.",
    "The report does not consider any reader's objectives, financial situation or needs.",
]

_SYM = {"USD": "US$", "SGD": "S$", "HKD": "HK$", "CNY": "RMB", "EUR": "€", "GBP": "£", "JPY": "¥",
        "AUD": "A$", "INR": "₹"}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _money(v, sym: str) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    return f"{sym}{f:,.2f}" if abs(f) < 20 else f"{sym}{f:,.0f}"


def _t(d: dict, key: str, ticker: str):
    v = (d.get(key) or {})
    return v.get(ticker) if isinstance(v, dict) else None


# ── target methodology ───────────────────────────────────────────────────────

def target_methodology(dcf_t: dict, scen: dict, sym: str) -> dict:
    pb = (dcf_t or {}).get("pt_bridge") or {}
    cap = _num(pb.get("capture"))
    reason = re.sub(r"\b(\d+) points\b", r"\1 percentage points", str(pb.get("capture_reason") or "").strip())
    method = (scen or {}).get("12m_pt_method") or (dcf_t or {}).get("12m_pt_method")
    rows = []
    for case in ("bear", "base", "bull"):
        sc = (pb.get("scenarios") or {}).get(case) or {}
        p = _num(((scen or {}).get(case) or {}).get("probability"))
        iv = _num(sc.get("intrinsic_value")) or _num(((scen or {}).get(case) or {}).get("fair_value"))
        rows.append({"case": case, "probability": p, "intrinsic_value": iv, "target": _num(sc.get("target"))})
    if (dcf_t.get("rating_state") or {}).get("state") == "unrated":
        return {"text": "No 12-month target is published for an unrated company.", "rule": None, "capture": None,
                "capture_reason": None, "method": None, "scenarios": rows}
    parts = []
    if cap is not None:
        parts.append(f"12-month target = price + {cap:.0%} × (intrinsic value − price)")
        if reason:
            _why = re.sub(r"^[0-9]+%:\s*", "", reason)
            parts[-1] += f"; the share is {cap:.0%} because {_why}"
    elif method:
        parts.append(f"12-month target on {method}")
    sc_bits = [f"{r['case']} {_money(r['intrinsic_value'], sym)}"
               + (f" ({r['probability']:.0%})" if r["probability"] is not None else "")
               for r in rows if r["intrinsic_value"] is not None]
    if sc_bits:
        parts.append("intrinsic value by scenario (probability): " + ", ".join(sc_bits))
    t_bits = [f"{r['case']} {_money(r['target'], sym)}" for r in rows if r["target"] is not None]
    if t_bits:
        parts.append("12-month target by scenario: " + ", ".join(t_bits))
    text = ". ".join(p[:1].upper() + p[1:] for p in parts) + ("." if parts else "")
    return {"text": text or None, "rule": pb.get("rule"), "capture": cap, "capture_reason": reason or None,
            "method": method, "scenarios": rows}


# ── what the price requires ──────────────────────────────────────────────────

def priced_in(dcf_t: dict, sym: str) -> dict:
    """Owner, 2026-11-25 (premium-franchise review): the reverse DCF beside the target -- the revenue growth
    today's price needs, against the model's and the company's own ten-year record. Read from the run."""
    rd = (dcf_t or {}).get("reverse_dcf") or {}
    cyc = (dcf_t or {}).get("cycle") or {}
    imp, mod, spot = _num(rd.get("implied_cagr10")), _num(rd.get("model_cagr10")), _num(rd.get("spot"))
    own = _num(cyc.get("trend_revenue_growth"))
    own_n = min(10, int(cyc.get("n_years") or 0)) if own is not None else 0
    out = {"applies": False, "text": None, "spot": spot, "implied_cagr10": imp, "model_cagr10": mod,
           "own_cagr": own if own_n >= 6 else None, "own_years": own_n if own_n >= 6 else None}
    if not (rd.get("solved") and imp is not None and mod is not None and spot is not None):
        return out
    text = (f"At {_money(spot, sym)} the shares need revenue growth of about {imp:.1%} a year for ten years, "
            f"with margins and the discount rate held at ours, against {mod:.1%} in our model")
    if out["own_cagr"] is not None:
        text += f" and {own:.1%} a year for the company over the last {own_n} years (log-linear trend)"
    out.update(applies=True, text=text + ".")
    return out


# ── vs consensus ─────────────────────────────────────────────────────────────

def vs_consensus(dcf_t: dict, scen: dict, target, sym: str) -> dict:
    cons = (dcf_t or {}).get("consensus_pt") or (dcf_t or {}).get("consensus_at_run") or {}
    lo, hi = _num(cons.get("low")), _num(cons.get("high"))
    mean = _num(cons.get("consensus")) or _num(cons.get("target"))
    tgt = _num(target)
    out = {"applies": False, "text": None, "our_target": tgt, "street_low": lo, "street_high": hi,
           "street_mean": mean, "driver": None, "facts": []}
    if tgt is None or lo is None or hi is None or lo <= tgt <= hi:
        return out
    below = tgt < lo
    out["applies"] = True
    facts: list[dict] = []
    # 1. convergence: our intrinsic value sits inside or beyond the Street range
    iv = _num(((scen or {}).get("reconciliation") or {}).get("blended_iv"))
    cap = _num(((dcf_t or {}).get("pt_bridge") or {}).get("capture"))
    spot = _num((scen or {}).get("current_price"))
    if iv is not None and ((below and iv >= lo) or (not below and iv <= hi)) and cap is not None:
        facts.append({"driver": "convergence",
                      "text": (f"our intrinsic value of about {_money(iv, sym)} is "
                               f"{'within' if lo <= iv <= hi else ('above' if iv > hi else 'below')} the Street range, "
                               f"but the 12-month target closes only {cap:.0%} of the gap from today's price "
                               f"of {_money(spot, sym)}")})
    # 2. cycle normalisation
    cyc = (dcf_t or {}).get("cycle") or {}
    pos = str(cyc.get("position") or "").lower()
    mid, last = _num(cyc.get("mid_cycle_margin")), _num(cyc.get("latest_margin"))
    if cyc.get("cyclical") and mid is not None and last is not None and \
            ((below and pos == "peak") or (not below and pos == "trough")):
        facts.append({"driver": "cycle_normalisation",
                      "text": (f"we value the company on a mid-cycle operating margin of {mid:.0%} against "
                               f"{last:.0%} today ({pos} of the cycle), where the Street extrapolates nearer-term "
                               "earnings")})
    # 3. our earnings vs consensus
    sc_eps = (((dcf_t or {}).get("street_consensus") or {}).get("eps") or {}).get("fy1")
    our_eps = ((((dcf_t or {}).get("guidance_estimates") or {}).get("estimates") or {}).get("base") or {}).get("eps_fy1")
    sc_eps, our_eps = _num(sc_eps), _num(our_eps)
    if sc_eps and our_eps and sc_eps > 0:
        gap = our_eps / sc_eps - 1
        if abs(gap) >= 0.10:
            facts.append({"driver": "earnings_estimate",
                          "text": (f"our next-year EPS estimate of {sym}{our_eps:,.2f} is {abs(gap):.0%} "
                                   f"{'below' if gap < 0 else 'above'} consensus of {sym}{sc_eps:,.2f}")})
    if not facts:
        facts.append({"driver": "multiple",
                      "text": ("our valuation multiples and cost of capital are "
                               f"{'more conservative' if below else 'less conservative'} than those implied by "
                               "Street targets")})
    out["facts"] = facts
    out["driver"] = facts[0]["driver"]
    rng = f"{_money(lo, sym)}–{_money(hi, sym)}"
    text = (f"Our 12-month target of {_money(tgt, sym)} is {'below' if below else 'above'} the Street range of "
            f"{rng}" + (f" (mean {_money(mean, sym)})" if mean is not None else "") + ". The main reason: "
            + facts[0]["text"] + ".")
    if len(facts) > 1:
        text += " Also: " + "; ".join(f["text"] for f in facts[1:]) + "."
    out["text"] = text
    return out


# ── data limitations ─────────────────────────────────────────────────────────

_EXTRACTOR_RELEVANCE = {
    "insurance_metrics": ("insur",), "bank_metrics": ("bank", "lending", "mortgage"),
    "reit_metrics": ("reit", "real estate", "property"), "saas_metrics": ("saas", "software"),
    "pipeline_assets": ("bio", "pharma", "drug"),
}
_SIZING_NOTE = re.compile(r"conviction|size|sizing|shifted|blocked|capped .* to|regulatory watch", re.I)


def _plain(s: str) -> str:
    from src.agents.pm.reader_text import lint_jargon
    s = re.sub(r"^[\s⚠\-–—•]+", "", str(s or "")).strip()
    s, _ = lint_jargon(s)
    return s[:1].upper() + s[1:] if s else s


def data_limitations(data: dict, ticker: str) -> list[str]:
    out: list[str] = []
    dcf_t = _t(data, "dcf_range", ticker) or {}
    profile = str(dcf_t.get("profile") or "").lower()
    # research quality
    for r in data.get("research_degraded_reasons") or []:
        if isinstance(r, dict) and r.get("text"):
            out.append(r["text"])
    if data.get("research_degraded") and not data.get("research_degraded_reasons"):
        out.append("Live research ran below the minimum number of confirmed web searches.")
    tier = str(data.get("research_tier") or "").lower()
    if tier in ("none", "knowledge_only"):
        out.append("No live web research was available; the narrative relies on the model's background knowledge.")
    # financial data gaps: a partial statement fetch from the data provider
    try:
        from src.tools.api import statement_gap_text
        out.extend(statement_gap_text(_t(data, "fmp_data_gaps", ticker) or []))
    except Exception:                                      # noqa: BLE001 -- a disclosure never sinks a run
        pass
    divs = _t(data, "research_financial_divergences", ticker) or {}
    if divs and not data.get("research_degraded_reasons"):
        out.append("Some figures in the research narrative differ from reported financials; reported financials "
                   "were used for every number in the valuation.")
    # valuation
    mu = dcf_t.get("methods_unavailable") or []
    if mu:
        names = ", ".join(_plain(str((m or {}).get("method") if isinstance(m, dict) else m)) for m in mu[:6])
        out.append(f"Valuation methods that could not be computed and carry no weight: {names}.")
    # Owner, 2026-11-25 (CFA review item 5): a key input that fails its accounting identity is a stated limitation.
    for c in ((dcf_t.get("input_checks") or {}).get("checks") or []):
        if isinstance(c, dict) and c.get("status") == "fail":
            out.append(f"Input check failed ({c.get('check')}): {c.get('detail')}.")
    rs = dcf_t.get("rating_state") or {}
    if rs.get("state") == "unrated" and rs.get("reason"):
        out.append(f"No rating is published: {rs['reason']}.")
    for f in (dcf_t.get("forward_flags") or [])[:5]:
        if f:
            out.append(_plain(f))
    # extractors relevant to this company
    for e in _t(data, "extractor_failures", ticker) or []:
        name = str((e or {}).get("extractor") or "") if isinstance(e, dict) else str(e)
        keys = _EXTRACTOR_RELEVANCE.get(name)
        if keys and not any(k in profile for k in keys):
            continue
        label = name.replace("_", " ")
        out.append(f"Sector data ({label}) could not be extracted from the research; it does not inform this report.")
    # value trap
    trap = _t(data, "value_trap_analysis", ticker) or {}
    if str(trap.get("overall_verdict") or "").endswith("NOT ASSESSED"):
        out.append("The value-trap checklist could not be run; its checks carry no verdict.")
    # consistency notes that are about data, not sizing
    cf = _t(data, "consistency_flags", ticker)
    if isinstance(cf, str):
        for bit in cf.split(" | "):
            if bit.strip() and not _SIZING_NOTE.search(bit):
                out.append(_plain(bit))
    # de-duplicate, keep order
    return list(dict.fromkeys(x for x in out if x))


# ── public entry ─────────────────────────────────────────────────────────────

def build_report_disclosures(data: dict, ticker: str, model: Optional[str] = None) -> dict:
    from src.decisions.ratings import RATING_DEFINITION, REGULATORY_DISCLAIMER, UNRATED_DEFINITION
    data = data or {}
    dec = _t(data, "decisions", ticker) or {}
    scen = _t(data, "scenario_analysis", ticker) or {}
    dcf_t = _t(data, "dcf_range", ticker) or {}
    rv = dec.get("research_view") or {}
    ccy = str(dcf_t.get("reported_currency") or data.get("reported_currency") or "USD").upper()
    sym = _SYM.get(ccy, ccy + " ")
    unrated = (dcf_t.get("rating_state") or {}).get("state") == "unrated" or rv.get("research_rating") == "UNRATED"
    model = model or dec.get("writer_model") or data.get("model_name")
    bench = rv.get("benchmark") if isinstance(rv.get("benchmark"), dict) else None
    as_of = (rv.get("price_as_of") or scen.get("price_as_of") or data.get("end_date")
             or str(((dcf_t.get("consensus_at_run") or {}).get("fetched_at") or ""))[:10] or None)
    tier = str(data.get("research_tier") or "").lower()
    sources = ["Financial Modeling Prep: financial statements, prices, Street estimates and price targets",
               "Company filings, earnings releases and news found by live web research"
               + ("" if tier not in ("none", "knowledge_only") else " (not available for this report)"),
               "Damodaran (January 2026): risk-free rate, equity risk premium and country risk premia"]
    target = None if unrated else (dec.get("price_target") or scen.get("12m_price_target"))
    return {
        "horizon": HORIZON_LABEL,
        "rating_definition": UNRATED_DEFINITION if unrated else RATING_DEFINITION,
        "regulatory_mapping": REGULATORY_DISCLAIMER,
        "benchmark": ({"name": bench.get("name"), "expected_return": bench.get("expected_return"),
                       "basis": bench.get("basis")} if bench else None),
        "ai_disclosure": ("This report's text was written by an AI language model"
                          + (f" ({model})" if model else "")
                          + " from the data listed below, and checked by fixed rules for figures, units and "
                            "wording. It has not been reviewed by a human analyst and is not investment advice."),
        "model": model,
        "data_sources": sources,
        "as_of": as_of,
        "limitations": list(STANDING_LIMITATIONS),
        "target_methodology": target_methodology(dcf_t, scen, sym),
        "vs_consensus": vs_consensus(dcf_t, scen, target, sym) if target is not None else
        {"applies": False, "text": None, "our_target": None, "street_low": None, "street_high": None,
         "street_mean": None, "driver": None, "facts": []},
        "priced_in": priced_in(dcf_t, sym),
        "data_limitations": data_limitations(data, ticker),
    }


def build_all(data: dict, model: Optional[str] = None) -> dict:
    out = {}
    for t in (data.get("decisions") or {}):
        try:
            out[t] = build_report_disclosures(data, t, model=model)
        except Exception as exc:                          # noqa: BLE001 -- a disclosure never sinks a run
            out[t] = {"horizon": HORIZON_LABEL, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return out

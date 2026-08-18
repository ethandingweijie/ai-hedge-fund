"""Stage 7 / Phase 7i — SOTP report enrichment helpers (Tier 1 package).

Pure functions that turn the extractor's ``sotp_assumptions`` dict into the
report payload material stored at ``dcf_range[ticker]["sotp_breakdown"]``:

  * ``build_sotp_sentence``   — GS-style one-line valuation bridge
    ("TP HK$123 = Σ segments … + associates + net cash − 15% holdco ÷ shares").
  * ``build_sotp_snapshot``   — assumption snapshot persisted with the run so
    the next run can produce a New-vs-Old estimate revision table.
  * ``diff_sotp_snapshots``   — revision rows between two snapshots.
  * ``sotp_elasticities``     — ±10% assumption perturbations re-run through
    the deterministic engine → "what moves the TP" ($/share impact).
  * ``sotp_scenario_tps``     — bear/bull per-segment multiple overrides
    (deep-research 2A.5 SCENARIO lines) re-run through the engine.
  * ``build_sotp_breakdown``  — orchestrator assembling the full payload.

Everything here is a pure function of the assumptions dict — deterministic
by construction (same as ``_sotp_analyst_style``). No I/O, no LLM calls.
"""
from __future__ import annotations

import copy
from typing import Optional

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.analysis.sotp_multiple_basis import normalize_key

_CCY_SYMBOLS = {
    "USD": "$", "HKD": "HK$", "CNY": "RMB", "RMB": "RMB",
    "SGD": "S$", "EUR": "€", "GBP": "£", "JPY": "¥",
}

# Relative tolerance for snapshot diffs — sub-0.5% drift is rounding noise
# (FMP share counts and consensus figures wiggle a few bps between runs).
_DIFF_TOL_REL = 0.005


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_usd(v: Optional[float]) -> str:
    if v is None:
        return "—"
    av = abs(v)
    sign = "−" if v < 0 else ""
    if av >= 1e9:
        return f"{sign}${av / 1e9:,.1f}B"
    if av >= 1e6:
        return f"{sign}${av / 1e6:,.1f}M"
    return f"{sign}${av:,.0f}"


# ── Tier 1.1: valuation sentence ─────────────────────────────────────────────

def build_sotp_sentence(table: dict, reporting_ccy: str = "USD") -> str:
    """One-line GS-style valuation bridge for the SOTP table.

    Shape: ``TP HK$122.77 = Σ segments $120.9B (Food Delivery 12x P/E + …)
    + associates $12.3B + net cash $15.5B − 15% holdco → $98.6B ÷ 6,250M
    shares``. Zero bridge items are skipped (single-segment MSFT shape reads
    cleanly); net debt renders as "− net debt".
    """
    if not table:
        return ""
    ccy = (reporting_ccy or "USD").upper()
    sym = _CCY_SYMBOLS.get(ccy, f"{ccy} ")
    seg_bits = " + ".join(
        f"{r['name']} {r['multiple']:g}x {r['method'].replace(' (fallback)', '')}"
        for r in table.get("rows") or []
    )
    frags: list[str] = []
    if table.get("associates"):
        frags.append(f"+ associates {_fmt_usd(table['associates'])}")
    nc = table.get("net_cash") or 0.0
    if nc > 0:
        frags.append(f"+ net cash {_fmt_usd(nc)}")
    elif nc < 0:
        frags.append(f"− net debt {_fmt_usd(-nc)}")
    hp = table.get("holdco_discount_pct") or 0.0
    if hp > 0:
        frags.append(f"− {hp:.0%} holdco")
    tail = (" " + " ".join(frags)) if frags else ""
    shares_mn = (table.get("shares") or 0) / 1e6
    return (
        f"TP {sym}{table['per_share_reporting']:,.2f} = Σ segments "
        f"{_fmt_usd(table['segment_value'])} ({seg_bits}){tail} → "
        f"{_fmt_usd(table['final'])} ÷ {shares_mn:,.0f}M shares"
    )


# ── Tier 1.2: assumption snapshots + revision diff ───────────────────────────

def build_sotp_snapshot(assumptions: dict, table: dict) -> dict:
    """Compact assumption snapshot persisted with the run.

    The next run diffs its snapshot against the previous one (retrieved from
    ``web_runs.full_result_json`` at save time) to produce the GS-style
    "New vs Old" estimate revision table.
    """
    fwd = assumptions.get("_fwd_estimates") or {}
    sources = assumptions.get("_sources") or {}
    return {
        "version": 1,
        "segments": [
            {"name": r["name"], "revenue_fwd": r["revenue_fwd"],
             "ebit": r["ebit"], "method": r["method"],
             "multiple": r["multiple"], "value": r["value"]}
            for r in table.get("rows") or []
        ],
        "net_cash": table.get("net_cash"),
        "associates": table.get("associates"),
        "holdco_discount_pct": table.get("holdco_discount_pct"),
        "tax_rate": assumptions.get("default_tax_rate"),
        "shares": table.get("shares"),
        "per_share_usd": table.get("per_share"),
        "per_share_reporting": table.get("per_share_reporting"),
        "fx_to_reporting": table.get("fx_to_reporting"),
        "consensus": {
            "period_end": fwd.get("period_end"),
            "revenue_avg": fwd.get("revenue_avg"),
            "net_income_avg": fwd.get("net_income_avg"),
        } if fwd else None,
        "sources": {
            k: sources.get(k)
            for k in ("net_cash", "associates", "segment_revenues",
                      "forward_estimates")
        },
    }


def diff_sotp_snapshots(prev: dict, curr: dict) -> list[dict]:
    """New-vs-Old revision rows between two snapshots.

    Returns ``[{item, section, old, new, delta_pct}]`` — numeric rows within
    ±0.5% relative tolerance are suppressed (FMP rounding noise); segment
    matching is by ``normalize_key`` (handles "&"/"and" flips and
    capitalization drift across runs).
    """
    if not prev or not curr:
        return []
    rows: list[dict] = []

    def _add(item: str, section: str, old, new, kind: str = "amount"):
        if old is None and new is None:
            return
        if old is not None and new is not None:
            if kind == "pct":
                if abs(new - old) < _DIFF_TOL_REL:
                    return
            else:
                if abs(new - old) / max(abs(old), 1e-9) < _DIFF_TOL_REL:
                    return
        delta_pct = None
        if old not in (None, 0) and new is not None:
            delta_pct = (new - old) / abs(old)
        rows.append({"item": item, "section": section, "old": old,
                     "new": new, "delta_pct": delta_pct})

    def _mn(v):
        return v / 1e6 if v else None

    # Global bridge / assumption fields.
    _add("Net cash / (debt)", "Balance sheet",
         prev.get("net_cash"), curr.get("net_cash"))
    _add("Associates & investments", "Balance sheet",
         prev.get("associates"), curr.get("associates"))
    _add("Holdco discount", "NAV bridge",
         prev.get("holdco_discount_pct"), curr.get("holdco_discount_pct"),
         kind="pct")
    _add("Group tax rate", "Assumptions",
         prev.get("tax_rate"), curr.get("tax_rate"), kind="pct")
    _add("Shares (mn)", "Assumptions",
         _mn(prev.get("shares")), _mn(curr.get("shares")))
    _add("SOTP per share (USD)", "Outcome",
         prev.get("per_share_usd"), curr.get("per_share_usd"))

    # Consensus forward estimates (when FMP coverage exists).
    pc = prev.get("consensus") or {}
    cc = curr.get("consensus") or {}
    if pc or cc:
        period = cc.get("period_end") or pc.get("period_end") or "Y+1"
        _add(f"Consensus revenue ({period})", "Estimates",
             pc.get("revenue_avg"), cc.get("revenue_avg"))
        _add(f"Consensus net income ({period})", "Estimates",
             pc.get("net_income_avg"), cc.get("net_income_avg"))

    # Per-segment: matched by normalized key; added/removed flagged outright.
    prev_segs = {normalize_key(str(s.get("name", ""))): s
                 for s in prev.get("segments") or []}
    curr_segs = {normalize_key(str(s.get("name", ""))): s
                 for s in curr.get("segments") or []}
    for key, cs in curr_segs.items():
        name = str(cs.get("name", ""))
        ps = prev_segs.get(key)
        if ps is None:
            rows.append({"item": f"{name} (added)", "section": "Segments",
                         "old": None, "new": cs.get("value"),
                         "delta_pct": None})
            continue
        _add(f"{name} multiple", "Segments",
             ps.get("multiple"), cs.get("multiple"))
        _add(f"{name} fwd revenue", "Segments",
             ps.get("revenue_fwd"), cs.get("revenue_fwd"))
        if ps.get("method") != cs.get("method"):
            rows.append({"item": f"{name} method", "section": "Segments",
                         "old": ps.get("method"), "new": cs.get("method"),
                         "delta_pct": None})
    for key, ps in prev_segs.items():
        if key not in curr_segs:
            rows.append({"item": f"{str(ps.get('name', ''))} (removed)",
                         "section": "Segments", "old": ps.get("value"),
                         "new": None, "delta_pct": None})
    return rows


# ── Tier 1.4: risk → TP elasticities ─────────────────────────────────────────

def _perturbed(assumptions: dict, seg_idx: Optional[int], param: str,
               factor: float) -> Optional[dict]:
    """Deep-copied assumptions with ``param`` scaled by ``factor``."""
    a = copy.deepcopy(assumptions)
    if seg_idx is None:
        base = a.get(param)
        if base is None:
            return None
        a[param] = base * factor
        return a
    segs = a.get("segments") or []
    if seg_idx >= len(segs) or not isinstance(segs[seg_idx], dict):
        return None
    seg = segs[seg_idx]
    if param == "unit_economics.profit_per_unit":
        ue = seg.get("unit_economics") or {}
        ppu = ue.get("profit_per_unit")
        if ppu is None:
            return None
        ue["profit_per_unit"] = ppu * factor
        return a
    base = seg.get(param)
    if base is None:
        return None
    seg[param] = base * factor
    return a


def sotp_elasticities(assumptions: dict, shares: Optional[float] = None,
                      fx: float = 1.0, perturb: float = 0.10,
                      top_n: int = 8) -> list[dict]:
    """Per-assumption TP elasticities via symmetric ±perturbation re-runs.

    Candidates follow the winning method of each segment in the base table
    (P/E segments: multiple / revenue / margin / EBIT / profit-per-unit;
    EV/Rev segments: multiple / revenue) plus the global bridge items
    (net cash, associates, holdco) when non-zero. Impact is the central
    difference in reporting-currency per-share value.
    """
    shares = shares or assumptions.get("_shares")
    if not assumptions or not shares or shares <= 0:
        return []
    base = _sotp_analyst_style(assumptions, shares=shares,
                               fx_to_reporting=fx)
    if base is None:
        return []
    base_ps = base["per_share_reporting"]
    row_by_key = {normalize_key(str(r["name"])): r for r in base["rows"]}
    segs = assumptions.get("segments") or []

    cands: list[tuple[str, Optional[int], str, float]] = []
    for i, seg in enumerate(segs):
        if not isinstance(seg, dict):
            continue
        name = str(seg.get("name", ""))
        row = row_by_key.get(normalize_key(name))
        if row is None:
            continue
        method = row["method"]
        if method == "P/E" and seg.get("pe_multiple") is not None:
            cands.append((f"{name} P/E multiple", i, "pe_multiple",
                          float(seg["pe_multiple"])))
        if method.startswith("EV/Rev") and seg.get("ev_rev_multiple") is not None:
            cands.append((f"{name} EV/Rev multiple", i, "ev_rev_multiple",
                          float(seg["ev_rev_multiple"])))
        if seg.get("revenue_fwd") is not None:
            cands.append((f"{name} revenue", i, "revenue_fwd",
                          float(seg["revenue_fwd"])))
        if seg.get("ebit_margin") is not None:
            cands.append((f"{name} EBIT margin", i, "ebit_margin",
                          float(seg["ebit_margin"])))
        if seg.get("ebit") is not None and method == "P/E":
            cands.append((f"{name} EBIT", i, "ebit", float(seg["ebit"])))
        ue = seg.get("unit_economics") or {}
        if ue.get("profit_per_unit") is not None and method == "P/E":
            cands.append((f"{name} profit/unit", i,
                          "unit_economics.profit_per_unit",
                          float(ue["profit_per_unit"])))
    for gparam, glabel in (("net_cash", "Net cash / (debt)"),
                           ("associates_investments",
                            "Associates & investments"),
                           ("holdco_discount_pct", "Holdco discount")):
        v = assumptions.get(gparam)
        if v is None or v == 0:
            continue
        cands.append((glabel, None, gparam, float(v)))

    out: list[dict] = []
    for label, seg_idx, param, base_value in cands:
        up_a = _perturbed(assumptions, seg_idx, param, 1.0 + perturb)
        down_a = _perturbed(assumptions, seg_idx, param, 1.0 - perturb)
        if up_a is None or down_a is None:
            continue
        tu = _sotp_analyst_style(up_a, shares=shares, fx_to_reporting=fx)
        td = _sotp_analyst_style(down_a, shares=shares, fx_to_reporting=fx)
        if tu is None or td is None:
            continue
        impact = (tu["per_share_reporting"] - td["per_share_reporting"]) / 2.0
        if abs(impact) < 1e-9:
            continue
        impact_pct = (impact / base_ps) if base_ps else None
        out.append({
            "label": label,
            "segment": (str(segs[seg_idx].get("name", ""))
                        if seg_idx is not None else None),
            "parameter": param,
            "base_value": base_value,
            "impact_per_share": impact,
            "impact_pct": impact_pct,
            "elasticity": (impact_pct / perturb) if impact_pct is not None
                          else None,
        })
    out.sort(key=lambda r: abs(r["impact_per_share"]), reverse=True)
    return out[:top_n]


# ── Tier 3.8: scenario multiples (bear/bull TPs) ─────────────────────────────

def sotp_scenario_tps(assumptions: dict, scenarios: dict,
                      shares: Optional[float] = None,
                      fx: float = 1.0) -> dict:
    """Bear/bull TPs from per-segment multiple overrides.

    ``scenarios`` shape (from deep-research 2A.5 SCENARIO lines, surfaced on
    the assumptions dict as ``_scenarios``)::

        {"bear": [{"name": ..., "pe_multiple"|"ev_rev_multiple": ...}],
         "bull": [...]}

    Each case deep-copies the assumptions, matches override segments via
    ``normalize_key`` (equality or substring, both directions), applies the
    one-metric rule (both multiples cleared, then the override's set), and
    re-runs the engine. Cases with no matched override are omitted.
    """
    shares = shares or assumptions.get("_shares")
    if not assumptions or not scenarios or not shares or shares <= 0:
        return {}
    out: dict = {}
    for case in ("bear", "bull"):
        overrides = scenarios.get(case) or []
        if not overrides:
            continue
        a = copy.deepcopy(assumptions)
        applied: list[str] = []
        for ov in overrides:
            if not isinstance(ov, dict):
                continue
            ov_key = normalize_key(str(ov.get("name", "")))
            if not ov_key:
                continue
            target = None
            for seg in a.get("segments") or []:
                if not isinstance(seg, dict):
                    continue
                seg_key = normalize_key(str(seg.get("name", "")))
                if not seg_key:
                    continue
                if (seg_key == ov_key or ov_key in seg_key
                        or seg_key in ov_key):
                    target = seg
                    break
            if target is None:
                continue
            target.pop("pe_multiple", None)
            target.pop("ev_rev_multiple", None)
            if ov.get("pe_multiple") is not None:
                target["pe_multiple"] = float(ov["pe_multiple"])
            if ov.get("ev_rev_multiple") is not None:
                target["ev_rev_multiple"] = float(ov["ev_rev_multiple"])
            applied.append(str(target.get("name", "")))
        if not applied:
            continue
        t = _sotp_analyst_style(a, shares=shares, fx_to_reporting=fx)
        if t is not None:
            out[case] = {
                "per_share": t["per_share"],
                "per_share_reporting": t["per_share_reporting"],
                "applied": applied,
            }
    return out


# ── Orchestrator ──────────────────────────────────────────────────────────────

def build_sotp_breakdown(assumptions: dict, reporting_ccy: str = "USD",
                         shares: Optional[float] = None,
                         table: Optional[dict] = None) -> Optional[dict]:
    """Assemble the full ``sotp_breakdown`` payload for the report.

    ``table`` may be passed pre-computed (the shadow-method table stored on
    ``most_recent["sotp_analyst_table"]``) so the breakdown is guaranteed
    consistent with the "SOTP (analyst)" row in the per-method table; when
    omitted it is recomputed from the assumptions. Returns None when the
    engine cannot run (missing shares/segments) — the caller stores None and
    the frontend feature-flags off ``dcfRange?.sotp_breakdown``.
    """
    if not assumptions:
        return None
    shares = shares or assumptions.get("_shares")
    if not shares or shares <= 0:
        return None
    fx = float(assumptions.get("fx_usd_to_reporting") or 1.0)
    if table is None:
        table = _sotp_analyst_style(assumptions, shares=shares,
                                    fx_to_reporting=fx)
    if table is None:
        return None

    fwd = assumptions.get("_fwd_estimates") or {}
    forward_estimates: list[dict] = []
    if fwd.get("period_end"):
        forward_estimates.append({
            "period_end": fwd.get("period_end"),
            "revenue": fwd.get("revenue_avg"),
            "ebit": fwd.get("ebit_avg"),
            "ebitda": fwd.get("ebitda_avg"),
            "net_income": fwd.get("net_income_avg"),
            "source": "FMP consensus",
        })

    basis = assumptions.get("_multiple_basis") or {}
    basis_segments = basis.get("segments")
    basis_compact = {
        "summary": basis.get("summary"),
        "jurisdiction": basis.get("jurisdiction"),
        "divergence_flags": basis.get("divergence_flags") or [],
        "segments": {
            name: {k: d.get(k) for k in ("status", "metric", "derived",
                                         "applied", "flag", "divergence_pct",
                                         "n_comps")}
            for name, d in basis_segments.items()
        } if isinstance(basis_segments, dict) else {},
    }

    return {
        "method": "SOTP (analyst)",
        "sentence": build_sotp_sentence(table, reporting_ccy),
        "reporting_currency": reporting_ccy or "USD",
        "rows": table["rows"],
        "segment_value": table["segment_value"],
        "associates": table["associates"],
        "net_cash": table["net_cash"],
        "nav": table["nav"],
        "holdco_discount_pct": table["holdco_discount_pct"],
        "holdco_discount": table["holdco_discount"],
        "final": table["final"],
        "per_share": table["per_share"],
        "per_share_reporting": table["per_share_reporting"],
        "shares": table["shares"],
        "fx_to_reporting": table["fx_to_reporting"],
        "forward_estimates": forward_estimates,
        "elasticities": sotp_elasticities(assumptions, shares=shares, fx=fx),
        "scenarios": sotp_scenario_tps(
            assumptions, assumptions.get("_scenarios") or {},
            shares=shares, fx=fx),
        "snapshot": build_sotp_snapshot(assumptions, table),
        "multiple_basis": basis_compact,
        "sources": dict(assumptions.get("_sources") or {}),
        "confidence": dict(assumptions.get("_confidence") or {}),
        "data_limitations": assumptions.get("_data_limitations") or "",
    }

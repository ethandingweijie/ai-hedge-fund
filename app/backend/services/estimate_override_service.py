"""Interactive valuation agent, the deterministic half (owner, 2026-10-03).

The user disagrees with an estimate the agent built from management guidance and changes it on
the page. This module recomputes exactly what the pipeline would have computed from that
number, nothing more and nothing less:

    guidance block (with the user's numbers)
      → guidance_forecast.build_forecast on the run's stored history ratios   (same engine)
      → the DCF leg re-projected on the forecast's growth and FCF-margin schedules (same projector)
      → the profile blend with the DCF leg's new value at its SAME weight       (same weights)
      → target = spot + capture × (IV − spot) per scenario                     (same bridge)
      → the probability-weighted 12-month target                               (same probabilities)

No LLM is involved. The agent (estimate_agent_service) explains and proposes; only the user's
acceptance writes an override, and the override is applied on read -- the stored run is never
rewritten, so "revert to the agent" is a delete. Web, PDF and Excel all read through
analysis_service.get_run_result, so one application covers all three surfaces.

Scope: only the DCF leg moves. Peer-multiple legs are priced on the cohort, not on the guidance,
and the record says so when the DCF carries no weight in the profile's blend (SBUX's Restaurants
profile): the override then moves the DCF cross-check, not the intrinsic value or the target.
"""
from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCENARIOS = ("bear", "base", "bull")
SCENARIO_FIELDS = ("revenue_growth_fy1", "revenue_growth_fy2", "ebitda_margin_fy1", "ebitda_margin_fy2", "eps_fy1", "eps_fy2")
SHARED_FIELDS = ("fade_years", "tax_rate", "capex_alpha", "nwc_intensity", "terminal_roic", "wacc", "tgr",
                 "gross_margin", "sbc_pct", "interest_rate", "payout_ratio", "buyback_annual",
                 "bank_loan_growth", "bank_nim", "bank_fee_growth", "bank_cost_to_income", "bank_credit_cost_bps", "bank_cet1_target",
                 "ins_premium_growth", "ins_combined_ratio", "ins_investment_yield")
STATEMENT_FIELDS = ("gross_margin", "sbc_pct", "interest_rate", "payout_ratio", "buyback_annual")
BANK_FIELD_MAP = {"bank_loan_growth": ("asset_growth_fy1", "asset_growth_fy2"), "bank_nim": ("nim_fy1", "nim_fy2"), "bank_fee_growth": ("fee_income_growth_fy1", "fee_income_growth_fy2"),
                  "bank_cost_to_income": ("cost_to_income_fy1", "cost_to_income_fy2"), "bank_credit_cost_bps": ("credit_cost_bps_fy1", "credit_cost_bps_fy2"),
                  "bank_cet1_target": ("cet1_target",), "payout_ratio": ("payout_ratio",), "buyback_annual": ("buyback_annual",),
                  "ins_premium_growth": ("premium_growth_fy1", "premium_growth_fy2"), "ins_combined_ratio": ("combined_ratio_fy1", "combined_ratio_fy2"),
                  "ins_investment_yield": ("investment_yield_fy1", "investment_yield_fy2")}
MEDIUM_TERM_METRICS = ("eps", "revenue", "revenue_growth", "ebitda_margin", "operating_margin", "ebit_margin")
_BOUNDS = {
    "revenue_growth_fy1": (-0.9, 3.0), "revenue_growth_fy2": (-0.9, 3.0),
    "ebitda_margin_fy1": (-1.0, 1.0), "ebitda_margin_fy2": (-1.0, 1.0),
    "eps_fy1": (-1e6, 1e6), "eps_fy2": (-1e6, 1e6),
    "fade_years": (1, 8), "tax_rate": (0.0, 0.6), "capex_alpha": (0.0, 3.0), "nwc_intensity": (-0.5, 1.0), "terminal_roic": (0.02, 1.0),
    "wacc": (0.02, 0.30), "tgr": (-0.02, 0.06),
    "gross_margin": (0.0, 0.98), "sbc_pct": (0.0, 0.25), "interest_rate": (0.0, 0.20), "payout_ratio": (0.0, 1.5), "buyback_annual": (0.0, 1e12),
    "bank_loan_growth": (-0.10, 0.25), "bank_nim": (0.003, 0.08), "bank_fee_growth": (-0.3, 0.4), "bank_cost_to_income": (0.20, 0.90), "bank_credit_cost_bps": (0.0, 400.0),
    "bank_cet1_target": (0.06, 0.25), "ins_premium_growth": (-0.2, 0.4), "ins_combined_ratio": (0.70, 1.20), "ins_investment_yield": (0.005, 0.10),
}


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def normalize_overrides(raw: Any) -> dict:
    """Validate and shape the user's overrides. Raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("overrides must be an object")
    out: dict = {"shared": {}, "scenarios": {}, "medium_term_target": None}
    for k, v in (raw.get("shared") or {}).items():
        if k not in SHARED_FIELDS:
            raise ValueError(f"unknown shared field {k!r}")
        f = _num(v)
        if f is None:
            continue
        lo, hi = _BOUNDS[k]
        if not (lo <= f <= hi):
            raise ValueError(f"{k} = {f} is outside {lo}..{hi}")
        out["shared"][k] = int(round(f)) if k == "fade_years" else f
    for sc, fields in (raw.get("scenarios") or {}).items():
        if sc not in SCENARIOS:
            raise ValueError(f"unknown scenario {sc!r}")
        row = {}
        for k, v in (fields or {}).items():
            if k not in SCENARIO_FIELDS:
                raise ValueError(f"unknown estimate field {k!r}")
            f = _num(v)
            if f is None:
                continue
            lo, hi = _BOUNDS[k]
            if not (lo <= f <= hi):
                raise ValueError(f"{sc}.{k} = {f} is outside {lo}..{hi}")
            row[k] = f
        if row:
            out["scenarios"][sc] = row
    mt = raw.get("medium_term_target")
    if mt == {} or mt == "none":
        out["medium_term_target"] = {}                      # explicit: drop the agent's target
    elif isinstance(mt, dict) and mt:
        metric = str(mt.get("metric") or "").strip().lower()
        if metric not in MEDIUM_TERM_METRICS:
            raise ValueError(f"medium_term_target.metric must be one of {MEDIUM_TERM_METRICS}")
        if not mt.get("target_year"):
            raise ValueError("medium_term_target.target_year is required")
        vals = {k: _num(mt.get(k)) for k in ("low", "mid", "high")}
        if all(v is None for v in vals.values()):
            raise ValueError("medium_term_target needs low, mid or high")
        out["medium_term_target"] = {"metric": metric, "target_year": str(mt["target_year"]), **vals,
                                     "unit": mt.get("unit"), "basis": mt.get("basis") or "user", "source": mt.get("source") or "user override"}
    if not out["shared"] and not out["scenarios"] and out["medium_term_target"] is None:
        raise ValueError("no overrides given")
    return out


def changed_fields(ov: dict) -> list[str]:
    out = [f"{k}" for k in (ov.get("shared") or {})]
    for sc, row in (ov.get("scenarios") or {}).items():
        out += [f"{sc}.{k}" for k in row]
    if ov.get("medium_term_target") is not None:
        out.append("medium_term_target")
    return out


# ── the recompute ─────────────────────────────────────────────────────────────

def _recompute_bank(payload: dict, ticker: str, ov: dict, dr: dict, ctx: dict, bank_ctx: dict, spot, capture, probs: dict, sa: dict) -> dict:
    """A bank or insurer: the earnings-and-capital model on the user's drivers; the GGM (P/B) leg
    re-priced on the model's RoTE and FY+1 book (the same formula and bounds as the engine), the
    forward P/E on the model's EPS; every other leg as priced; the blend at the run's own weights."""
    from src.agents.analysis import bank_model as bmod
    from src.agents.analysis.dcf_agent import _return_on_book_basis
    shared = ov["shared"]
    kind = bank_ctx.get("kind") or "bank"
    a = deepcopy(bank_ctx.get("assumptions") or {})
    applied = {}
    for field, targets in BANK_FIELD_MAP.items():
        if shared.get(field) is None:
            continue
        for tname in targets:
            if tname in a or field.startswith(("bank_", "ins_")) or tname in ("payout_ratio", "buyback_annual"):
                a[tname] = {"value": shared[field], "source": "user override", "needed_for": (a.get(tname) or {}).get("needed_for")}
                applied[tname] = shared[field]
    out_sc: dict = {}
    for sc in SCENARIOS:
        scen = dr.get(sc) or {}
        pb_leg = ((scen.get("leg_inputs") or {}).get("GGM (P/B)")) or {}
        rec: dict = {"skipped": None, "forecast": None, "dcf": None, "intrinsic_value": _num(scen.get("intrinsic_value")),
                     "target": _num(((dr.get("pt_bridge") or {}).get("scenarios") or {}).get(sc, {}).get("target")), "dcf_weight": 0.0, "legs": {},
                     "before": {"intrinsic_value": _num(scen.get("intrinsic_value")), "dcf_value": None,
                                "target": _num(((dr.get("pt_bridge") or {}).get("scenarios") or {}).get(sc, {}).get("target"))}}
        out_sc[sc] = rec
        m = (bmod.build_bank if kind == "bank" else bmod.build_insurer)(bank_ctx["opening"], a, scenario=sc)
        if not m or m.get("skipped"):
            rec["skipped"] = (m or {}).get("skipped") or "the model did not build"
            continue
        m["coverage"] = bmod.coverage(m)
        m["overrides_applied"] = applied or None
        rec["three_statements"] = m
        legs_changed: dict = {}
        if not applied:
            rec["note"] = "no driver changed; the model is as the agent built it"
            continue
        # GGM (P/B): target P/B = (ROE_book − g) / (CoE − g), bounded 0.3..4.0, on FY+1 book, times the scenario band
        pa = pb_leg.get("assumptions") or {}
        coe, g, band = _num(pa.get("coe")), _num(pa.get("g")), _num(pb_leg.get("scenario_band")) or 1.0
        rote, bvps, tbvps = _num(m.get("steady_rote") or m.get("steady_roe")), _num(m.get("bvps_fy1")), _num(m.get("tbvps_fy1"))
        if pb_leg and coe is not None and g is not None and (coe - g) > 0.005 and rote and bvps:
            roe_book = _return_on_book_basis(rote, bvps, tbvps) or rote
            if roe_book > g:
                tpb = max(0.3, min((roe_book - g) / (coe - g), 4.0))
                new_v = bvps * tpb * band
                legs_changed["GGM (P/B)"] = {"metric": "bvps", "metric_before": _num(pa.get("bvps")), "metric_after": bvps, "multiple": tpb,
                                             "value_before": _num(pb_leg.get("value")), "value_after": new_v,
                                             "basis": f"RoTE {rote:.1%} → book-basis ROE {roe_book:.1%}; target P/B {tpb:.2f}x on FY+1 BVPS {bvps:,.2f}"}
        # Forward P/E on the model's FY+1 EPS
        for name, tr in (scen.get("leg_inputs") or {}).items():
            if isinstance(tr, dict) and tr.get("kind") == "equity_multiple" and "EPS" in str(tr.get("metric") or "") and ("NTM" in str(tr.get("metric")) or "guidance" in str(tr.get("metric"))):
                eps = _num(m.get("eps_fy1"))
                if eps and _num(tr.get("multiple")) and eps > 0:
                    legs_changed[name] = {"metric": "eps", "metric_before": _num(tr.get("metric_value")), "metric_after": eps, "multiple": float(tr["multiple"]),
                                          "value_before": _num(tr.get("value")), "value_after": eps * float(tr["multiple"]), "basis": "the model's FY+1 EPS in place of the leg's metric"}
        rec["legs"] = legs_changed
        eff = scen.get("effective_weights") or []
        mit = scen.get("method_iv_table") or {}
        if legs_changed:
            num = den = 0.0
            for e in eff:
                k = e.get("value_key") or e.get("method")
                w = float(e.get("weight") or 0.0)
                v = legs_changed[k]["value_after"] if k in legs_changed else _num(mit.get(k))
                if w > 0 and v is not None:
                    num += w * v
                    den += w
            if den > 0:
                rec["intrinsic_value"] = num / den
        else:
            rec["note"] = "no leg in this profile's blend reads the model (no GGM or forward P/E leg traced)"
        if spot is not None and capture is not None and rec["intrinsic_value"] is not None:
            rec["target"] = spot + capture * (rec["intrinsic_value"] - spot)
    pt = sum(probs[sc] * out_sc[sc]["target"] for sc in SCENARIOS if out_sc[sc]["target"] is not None)
    ev = sum(probs[sc] * out_sc[sc]["intrinsic_value"] for sc in SCENARIOS if out_sc[sc]["intrinsic_value"] is not None)
    pb = dr.get("pt_bridge") or {}
    before = {"intrinsic_value": _num((dr.get("base") or {}).get("intrinsic_value")), "dcf_value": None,
              "target": _num(((pb.get("scenarios") or {}).get("base") or {}).get("target")), "12m_price_target": _num(sa.get("12m_price_target")), "expected_value": _num(sa.get("expected_value"))}
    after = {"intrinsic_value": out_sc["base"]["intrinsic_value"], "dcf_value": None, "target": out_sc["base"]["target"],
             "12m_price_target": pt if pt else None, "expected_value": ev if ev else None}
    return {"ticker": ticker, "overrides": ov, "fields": changed_fields(ov), "spot": spot, "capture": capture, "probabilities": probs, "scenarios": out_sc,
            "before": before, "after": after, "model_kind": kind, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _statements_for(fc: dict, ctx: dict, shared: dict, dr: dict) -> Optional[dict]:
    """The three statements on the user's forecast and assumption overrides (base scenario)."""
    try:
        from src.agents.analysis import three_statement as ts
        if ctx.get("statements_family_ok") is False:
            return {"skipped": f"the {dr.get('profile')} profile is a balance-sheet business; the statement model does not apply"}
        opening = ctx.get("opening_balance_sheet")
        if not opening:
            return {"skipped": "no audited balance sheet on this run"}
        a = deepcopy(ctx.get("statement_assumptions") or {})
        for k in STATEMENT_FIELDS:
            if shared.get(k) is not None:
                a[k] = {"value": shared[k], "source": "user override", "needed_for": (a.get(k) or {}).get("needed_for")}
        out = ts.build(fc, opening, a, fx=ctx.get("fx_to_valuation")
                       or ((dr.get("financials_used") or {}).get("fx_rate")) or 1.0)
        if out:
            out["coverage"] = ts.coverage(fc, opening, a)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"skipped": f"the statements did not build: {type(exc).__name__}: {str(exc)[:120]}"}


def _dcf_mod():
    from src.agents.analysis import dcf_agent
    return dcf_agent


def _block_for(dr: dict, ctx: dict, ov: dict) -> dict:
    ge = dr.get("guidance_estimates") or {}
    block = {"estimates": deepcopy(ge.get("estimates") or {}), "medium_term_target": deepcopy(ge.get("medium_term_target")),
             "fiscal_year_1": ge.get("fiscal_year_1") or ctx.get("fiscal_year_1"), "fiscal_year_2": ge.get("fiscal_year_2") or ctx.get("fiscal_year_2"),
             "confidence": ge.get("confidence") or "USER"}
    for sc in SCENARIOS:
        block["estimates"].setdefault(sc, {})
        block["estimates"][sc].update(ov.get("scenarios", {}).get(sc, {}))
    # a scenario the user did not touch and the research did not estimate inherits the base case
    base = block["estimates"]["base"]
    for sc in ("bear", "bull"):
        if block["estimates"][sc].get("revenue_growth_fy1") is None and base.get("revenue_growth_fy1") is not None:
            block["estimates"][sc] = {**base, **block["estimates"][sc]}
    if ov.get("medium_term_target") is not None:
        block["medium_term_target"] = ov["medium_term_target"] or None
    # Owner, 2026-10-08: the run's engine fields (street growth path, street FY+1 EBITDA margin, EPS basis).
    block.update(deepcopy(ctx.get("block_extras") or {}))
    return block


def _loe_for(ctx: dict, fc: dict, wacc: float, tgr: float) -> Optional[dict]:
    """The run's LOE overlay re-applied on the override's forecast and discount rate (None when the run had none)."""
    from src.agents.analysis.dcf_agent import _franchise_loe_overlay, _LOE_COVERED_YEARS
    lo = ctx.get("loe") or {}
    if not lo.get("entry") or not fc or not fc.get("growth_schedule"):
        return None
    return _franchise_loe_overlay(lo["entry"], int(lo["fy0_year"]), list(fc["growth_schedule"]), float(wacc), float(tgr),
                                  covered_years=_LOE_COVERED_YEARS + int(fc.get("street_years") or 0),
                                  profile_name=lo.get("profile_name"), pipeline_ra_peak=lo.get("pipeline_ra_peak"))


def recompute(payload: dict, ticker: str, overrides: dict) -> dict:
    """The deterministic chain on the stored run. Pure: `payload` is not modified."""
    from src.agents.analysis import guidance_forecast as gfm
    from src.agents.analysis.dcf_agent import _project_dcf, _loe_scale_forecast

    ov = normalize_overrides(overrides)
    data = payload.get("data") or {}
    dr = (data.get("dcf_range") or {}).get(ticker)
    if not dr:
        raise KeyError(ticker)
    ctx = dr.get("forecast_context") or {}
    gf0 = dr.get("guidance_forecast") or {}
    hist = ctx.get("history") or gf0.get("history")
    inputs = dict(gf0.get("inputs") or {})
    inputs.update({k: v for k, v in (ctx.get("inputs") or {}).items() if v is not None})
    if not hist or not hist.get("revenue"):
        raise ValueError("this run predates the forecast context; run the analysis again to edit its estimates")
    pb = dr.get("pt_bridge") or {}
    spot = _num(pb.get("spot")) or _num(inputs.get("spot"))
    capture = _num(pb.get("capture"))
    block = _block_for(dr, ctx, ov)
    shared = ov["shared"]
    engine_ov = {k: shared[k] for k in ("fade_years", "tax_rate", "capex_alpha", "nwc_intensity", "terminal_roic") if k in shared}
    block0 = _block_for(dr, ctx, {"shared": {}, "scenarios": {}, "medium_term_target": None})     # the agent's block, for the legs' baseline
    shares_dr = _num(dr.get("shares_outstanding"))
    sa = (data.get("scenario_analysis") or {}).get(ticker) or {}
    probs = {sc: (_num((sa.get(sc) or {}).get("probability")) or {"bear": 0.25, "base": 0.5, "bull": 0.25}[sc]) for sc in SCENARIOS}
    out_sc: dict = {}
    bank_ctx = ctx.get("bank_model") or {}
    if bank_ctx.get("opening"):
        return _recompute_bank(payload, ticker, ov, dr, ctx, bank_ctx, spot, capture, probs, sa)
    for sc in SCENARIOS:
        scen = dr.get(sc) or {}
        leg = ((scen.get("leg_inputs") or {}).get("DCF")) or {}
        rec: dict = {"skipped": None, "forecast": None, "dcf": None, "intrinsic_value": _num(scen.get("intrinsic_value")),
                     "target": _num(((pb.get("scenarios") or {}).get(sc) or {}).get("target")), "dcf_weight": 0.0,
                     "before": {"intrinsic_value": _num(scen.get("intrinsic_value")), "dcf_value": _num(leg.get("value")),
                                "target": _num(((pb.get("scenarios") or {}).get(sc) or {}).get("target"))}}
        out_sc[sc] = rec
        if not leg or _num(leg.get("revenue_base")) is None or not _num(leg.get("shares")):
            rec["skipped"] = "no DCF leg was traced for this scenario"
            continue
        wacc = shared.get("wacc", _num(leg.get("wacc")) or _num(inputs.get("wacc")))
        tgr = shared.get("tgr", _num(leg.get("tgr")) if _num(leg.get("tgr")) is not None else _num(inputs.get("tgr")))
        if wacc is None or tgr is None:
            rec["skipped"] = "the leg's WACC or terminal growth is not recorded"
            continue
        try:
            fc = gfm.build_forecast(block, scenario=sc, series=None, hist=hist, overrides=engine_ov,
                                    profile_name=inputs.get("profile_name") or dr.get("profile"), sector=inputs.get("sector") or data.get("sector"),
                                    wacc=wacc, tgr=tgr, shares=float(leg["shares"]), net_debt=float(leg.get("net_debt") or 0.0), spot=spot,
                                    peer_ev_ebitda=_num(inputs.get("peer_ev_ebitda")), market_growth=_num(inputs.get("market_growth")),
                                    engine_growth_path=inputs.get("engine_growth_path") or leg.get("growth_schedule"),
                                    fcf_margin_base=_num(leg.get("fcf_margin_base")),
                                    fx_to_valuation=_num(inputs.get("fx_to_valuation")) or _num((dr.get("financials_used") or {}).get("fx_rate")) or 1.0,
                                    valuation_currency=inputs.get("valuation_currency") or (dr.get("financials_used") or {}).get("currency"))
        except Exception as exc:                            # noqa: BLE001
            rec["skipped"] = f"the forecast did not build: {type(exc).__name__}: {exc}"
            continue
        if not fc:
            rec["skipped"] = "no FY+1 revenue growth for this scenario (enter one to price it)"
            continue
        _tv_mult = 1.0
        try:
            _loe = _loe_for(ctx, fc, float(wacc), float(tgr))
            if _loe:
                _loe_scale_forecast(fc, _loe)
                _tv_mult = float(_loe["terminal_multiplier"])
        except Exception:                                   # noqa: BLE001
            _tv_mult = 1.0
        iv_dcf, pv_fcf, pv_tv, rows = _project_dcf(
            revenue_base=float(leg["revenue_base"]), fcf_margin_base=float(leg.get("fcf_margin_base") or 0.0),
            growth_rate=float(leg.get("growth_base") or 0.0), margin_delta_per_year=0.0, wacc=float(wacc), tgr=float(tgr),
            fcf_floor=float(leg.get("fcf_floor") or 0.0), net_debt=float(leg.get("net_debt") or 0.0), shares=float(leg["shares"]),
            growth_schedule=fc["growth_schedule"], wacc_schedule=leg.get("wacc_schedule") if not shared.get("wacc") else None,
            margin_delta_absolute=_num(leg.get("margin_delta_absolute")), margin_schedule=fc["fcf_margin_schedule"],
            minority_interest=float(leg.get("minority_interest") or 0.0), preferred_equity=float(leg.get("preferred_equity") or 0.0),
            timing=leg.get("timing"),                       # D2: the run's dating, so an override re-prices like-for-like
            terminal_multiplier=_tv_mult)
        iv_dcf = _num(iv_dcf)
        rec["forecast"] = gfm.summary(fc)
        if sc == "base":
            rec["three_statements"] = _statements_for(fc, ctx, shared, dr)
        rec["dcf"] = {"value": iv_dcf, "pv_fcf_per_share": pv_fcf, "pv_tv_per_share": pv_tv, "projection_rows": rows,
                      "growth_schedule": fc["growth_schedule"], "margin_schedule": fc["fcf_margin_schedule"], "wacc": float(wacc), "tgr": float(tgr),
                      "timing": leg.get("timing")}
        # Owner, 2026-10-03 ("especially the management guidance to estimates"): the forward multiples
        # re-price on the user's FY+1 estimates by the pipeline's own rule. The agent's metric for the
        # same leg is rebuilt from the agent's block and forecast, and the leg moves by that ratio, so
        # the leg keeps its basis (consensus or guidance-derived) and moves only for the user's change.
        legs_changed: dict = {}
        try:
            fc0 = gfm.build_forecast(block0, scenario=sc, series=None, hist=hist, overrides=None,
                                     profile_name=inputs.get("profile_name") or dr.get("profile"), sector=inputs.get("sector") or data.get("sector"),
                                     wacc=_num(leg.get("wacc")) or wacc, tgr=_num(leg.get("tgr")) if _num(leg.get("tgr")) is not None else tgr,
                                     shares=float(leg["shares"]), net_debt=float(leg.get("net_debt") or 0.0), spot=spot,
                                     peer_ev_ebitda=_num(inputs.get("peer_ev_ebitda")), market_growth=_num(inputs.get("market_growth")),
                                     engine_growth_path=inputs.get("engine_growth_path") or leg.get("growth_schedule"),
                                    fcf_margin_base=_num(leg.get("fcf_margin_base")),
                                    fx_to_valuation=_num(inputs.get("fx_to_valuation")) or _num((dr.get("financials_used") or {}).get("fx_rate")) or 1.0,
                                    valuation_currency=inputs.get("valuation_currency") or (dr.get("financials_used") or {}).get("currency"))
        except Exception:                                   # noqa: BLE001
            fc0 = None
        user_m = _dcf_mod()._guidance_forward_overlay(block, None, sc, fc, _num(leg.get("revenue_base")), eps_check=False) or {}
        agent_m = _dcf_mod()._guidance_forward_overlay(block0, None, sc, fc0, _num(leg.get("revenue_base")), eps_check=False) or {}
        new_leg_values: dict = {}
        for name, tr in (scen.get("leg_inputs") or {}).items():
            if name == "DCF" or not isinstance(tr, dict) or tr.get("kind") not in ("equity_multiple", "ev_multiple"):
                continue
            metric_label = str(tr.get("metric") or "")
            if "NTM" not in metric_label and "guidance" not in metric_label:
                continue                                    # trailing legs do not move on a forward estimate
            m = ("eps" if "EPS" in metric_label else "ebitda" if "EBITDA" in metric_label else "ebit" if "EBIT" in metric_label
                 else "revenue" if "Revenue" in metric_label else None)
            if not m or not _num(tr.get("metric_value")) or not _num(tr.get("multiple")):
                continue
            u, a = _num((user_m.get(m) or {}).get(sc)), _num((agent_m.get(m) or {}).get(sc))
            if u is None or u <= 0:
                continue
            old_metric = float(tr["metric_value"])
            if a is not None and a > 0:
                new_metric = old_metric * (u / a)
                basis = "scaled by your estimate vs the agent's"
            elif "guidance" in metric_label:
                new_metric = u
                basis = "your estimate in place of the agent's"
            else:
                new_metric = u
                basis = "your estimate in place of consensus"
            if abs(new_metric - old_metric) < 1e-12:
                continue
            mult = float(tr["multiple"])
            if tr.get("kind") == "equity_multiple":
                new_value = new_metric * mult
            else:
                sh = _num(tr.get("shares")) or _num(leg.get("shares")) or shares_dr
                if not sh:
                    continue
                ev = new_metric * mult
                new_value = (ev - float(tr.get("net_debt") or leg.get("net_debt") or 0.0) - float(tr.get("minority_interest") or 0.0)
                             - float(tr.get("preferred_equity") or 0.0)) / sh
            legs_changed[name] = {"metric": m, "metric_before": old_metric, "metric_after": new_metric, "multiple": mult,
                                  "value_before": _num(tr.get("value")), "value_after": new_value, "basis": basis}
        rec["legs"] = legs_changed
        # the blend: every leg at its own weight -- the DCF re-projected, the forward legs re-priced
        eff = scen.get("effective_weights") or []
        mit = scen.get("method_iv_table") or {}
        w_dcf = sum(float(e.get("weight") or 0.0) for e in eff if (e.get("value_key") or e.get("method")) == "DCF")
        rec["dcf_weight"] = w_dcf
        weights = [(e.get("value_key") or e.get("method"), float(e.get("weight") or 0.0)) for e in eff]
        if w_dcf == 0 and iv_dcf is not None and iv_dcf > 0 and any(str(p.get("name")) == "DCF" for p in (scen.get("profile_weights") or [])):
            # the agent's run dropped the DCF (it did not price); the user's numbers make it price, so
            # the profile's INTENDED weights apply, renormalised over the legs that have a value
            weights = [(str(p.get("name")), float(p.get("weight") or 0.0)) for p in (scen.get("profile_weights") or [])]
            w_dcf = sum(w for k, w in weights if k == "DCF")
            rec["dcf_weight"] = w_dcf
            rec["note"] = "the agent's run dropped the DCF (it did not price); your estimates make it price, so the profile's intended weights apply, renormalised"
        # the blend is rebuilt only when something in it moved (the DCF at weight, or a forward leg);
        # otherwise the run's own intrinsic value stands, rounding and all
        if (w_dcf > 0 and iv_dcf is not None and iv_dcf > 0) or legs_changed:
            num = den = 0.0
            for k, w in weights:
                if w <= 0:
                    continue
                if k == "DCF":
                    v = iv_dcf if (iv_dcf is not None and iv_dcf > 0) else None
                elif k in legs_changed:
                    v = legs_changed[k]["value_after"]
                else:
                    v = _num(mit.get(k))
                if v is not None:
                    num += w * v
                    den += w
            if den > 0:
                rec["intrinsic_value"] = num / den
        if w_dcf > 0 and not (iv_dcf is not None and iv_dcf > 0):
            rec["note"] = "the re-projected DCF did not price (value ≤ 0); its weight is renormalised onto the other legs"
        elif w_dcf == 0 and not legs_changed:
            rec["note"] = f"the DCF carries no weight in the {dr.get('profile')} profile's blend and no forward leg carries an estimate: the override moves the DCF cross-check only"
        elif w_dcf == 0:
            rec["note"] = f"the DCF carries no weight in the {dr.get('profile')} profile's blend; the forward legs re-priced on your estimates: " + ", ".join(legs_changed)
        if spot is not None and capture is not None and rec["intrinsic_value"] is not None:
            rec["target"] = spot + capture * (rec["intrinsic_value"] - spot)
    pt = sum(probs[sc] * out_sc[sc]["target"] for sc in SCENARIOS if out_sc[sc]["target"] is not None)
    ev = sum(probs[sc] * out_sc[sc]["intrinsic_value"] for sc in SCENARIOS if out_sc[sc]["intrinsic_value"] is not None)
    base_leg = ((dr.get("base") or {}).get("leg_inputs") or {}).get("DCF") or {}
    before = {"intrinsic_value": _num((dr.get("base") or {}).get("intrinsic_value")), "dcf_value": _num(base_leg.get("value")),
              "target": _num(((pb.get("scenarios") or {}).get("base") or {}).get("target")),
              "12m_price_target": _num(sa.get("12m_price_target")), "expected_value": _num(sa.get("expected_value"))}
    after = {"intrinsic_value": out_sc["base"]["intrinsic_value"], "dcf_value": (out_sc["base"]["dcf"] or {}).get("value"),
             "target": out_sc["base"]["target"], "12m_price_target": pt if pt else None, "expected_value": ev if ev else None}
    return {"ticker": ticker, "overrides": ov, "fields": changed_fields(ov), "spot": spot, "capture": capture, "probabilities": probs,
            "scenarios": out_sc, "before": before, "after": after,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ── applying a saved override to a run payload (on read) ─────────────────────

def apply_to_payload(payload: dict, ticker: str, record: dict) -> dict:
    """Patch the stored run with the override's recomputed figures. Mutates and returns `payload`."""
    res = record.get("result") or {}
    data = payload.setdefault("data", {})
    dr = (data.get("dcf_range") or {}).get(ticker)
    if not dr or not res:
        return payload
    meta = {"note": record.get("note"), "created_at": record.get("created_at"), "overrides": res.get("overrides"), "fields": res.get("fields"),
            "before": res.get("before"), "after": res.get("after"), "id": record.get("id")}
    dr["estimate_override"] = meta
    pb = dr.setdefault("pt_bridge", {})
    pb_sc = pb.setdefault("scenarios", {})
    flag = (f"USER OVERRIDE ({str(record.get('created_at') or '')[:10]}): {', '.join(res.get('fields') or [])}"
            + (f" — {record.get('note')}" if record.get("note") else "")
            + (f"; the agent's base IV {res['before']['intrinsic_value']:,.2f} → {res['after']['intrinsic_value']:,.2f}"
               if _num((res.get("before") or {}).get("intrinsic_value")) is not None and _num((res.get("after") or {}).get("intrinsic_value")) is not None else ""))
    for sc, rec in (res.get("scenarios") or {}).items():
        scen = dr.get(sc)
        if not scen or rec.get("skipped") or not (rec.get("dcf") or rec.get("legs")):
            continue
        d = rec.get("dcf") or {}
        fc = rec.get("forecast") or {}
        if d:
            leg = (scen.setdefault("leg_inputs", {})).setdefault("DCF", {})
            leg.update({"value": d.get("value"), "pv_fcf_per_share": d.get("pv_fcf_per_share"), "pv_tv_per_share": d.get("pv_tv_per_share"),
                        "projection_rows": d.get("projection_rows"), "growth_schedule": d.get("growth_schedule"), "margin_schedule": d.get("margin_schedule"),
                        "wacc": d.get("wacc"), "tgr": d.get("tgr"), "user_override": True})
            leg["guidance_forecast"] = {k: v for k, v in fc.items() if k not in ("rows", "curve")} or None
        if d.get("value") is not None:
            scen["iv_dcf"] = d["value"]
            if "DCF" in (scen.get("method_iv_table") or {}) or rec.get("dcf_weight"):
                scen.setdefault("method_iv_table", {})["DCF"] = round(float(d["value"]), 2)
        for name, lg in (rec.get("legs") or {}).items():
            tr = (scen.get("leg_inputs") or {}).get(name)
            if isinstance(tr, dict) and lg.get("value_after") is not None:
                if tr.get("kind") == "ggm":
                    tr.update({"value": lg["value_after"], "value_before_band": lg["value_after"] / (float(tr.get("scenario_band") or 1.0) or 1.0), "target_pb": lg["multiple"],
                               "assumptions": {**(tr.get("assumptions") or {}), "bvps": lg["metric_after"], "bvps_basis": "user estimate: FY+1 book from the bank model", "user_basis": lg.get("basis")},
                               "user_override": True})
                else:
                    tr.update({"value": lg["value_after"], "metric_value": lg["metric_after"], "per_share_metric": (lg["metric_after"] if tr.get("kind") == "equity_multiple" else tr.get("per_share_metric")),
                               "metric_agent": lg["metric_before"], "metric": str(tr.get("metric") or "") + " · user estimate", "user_override": True})
                if name in (scen.get("method_iv_table") or {}):
                    scen["method_iv_table"][name] = round(float(lg["value_after"]), 2)
        if rec.get("intrinsic_value") is not None:
            scen["intrinsic_value"] = round(float(rec["intrinsic_value"]), 2)
            pb_sc.setdefault(sc, {})["intrinsic_value"] = round(float(rec["intrinsic_value"]), 2)
        if rec.get("target") is not None:
            pb_sc.setdefault(sc, {})["target"] = round(float(rec["target"]), 2)
            dr.setdefault("12m_targets", {})[sc] = round(float(rec["target"]), 2)
        flags = scen.setdefault("forward_flags", [])
        if flag not in flags:
            flags.insert(0, flag)
        if sc != "base":
            if rec.get("three_statements"):
                dr.setdefault("three_statements_scenarios", {})
                if isinstance(dr["three_statements_scenarios"], dict):
                    dr["three_statements_scenarios"][sc] = rec["three_statements"]
            continue
        if fc:
            dr["guidance_forecast"] = {**fc, "override": meta}
        if rec.get("three_statements"):
            dr["three_statements"] = {**rec["three_statements"], "override": meta}
        if True:
            dr["projection_rows"] = d.get("projection_rows") or dr.get("projection_rows")
            if d and d.get("pv_fcf_per_share") is not None:
                dr["pv_fcf_base"] = d["pv_fcf_per_share"]
                dr["pv_tv_base"] = d["pv_tv_per_share"]
            ge = dr.get("guidance_estimates")
            if isinstance(ge, dict):
                ge["applied"] = True
                ge["channel"] = {**(ge.get("channel") or {}), "schedule": d.get("growth_schedule"), "source": "user override → guidance forecast",
                                 "explicit_years": fc.get("horizon_years"), "fade_years": fc.get("fade_years")}
                ge["not_applied_reason"] = None
                ests = ge.setdefault("estimates", {})
                for sc2, row in ((res.get("overrides") or {}).get("scenarios") or {}).items():
                    ests.setdefault(sc2, {}).update(row)
                if (res.get("overrides") or {}).get("medium_term_target") is not None:
                    ge["medium_term_target"] = (res["overrides"]["medium_term_target"] or None)
    pm = record.get("pm") or (res.get("pm") if isinstance(res.get("pm"), dict) else None)
    if pm and pm.get("rationale"):
        for holder in ((data.get("decisions") or {}).get(ticker), (payload.get("decisions") or {}).get(ticker)):
            if isinstance(holder, dict):
                holder["rationale_agent"] = holder.get("rationale_agent", holder.get("rationale"))
                holder["headline_agent"] = holder.get("headline_agent", holder.get("headline"))
                holder["rationale"] = pm["rationale"]
                if pm.get("headline"):
                    holder["headline"] = pm["headline"]
                holder["pm_regenerated_at"] = pm.get("regenerated_at")
    after = res.get("after") or {}
    sa = (data.get("scenario_analysis") or {}).get(ticker)
    if isinstance(sa, dict) and after.get("12m_price_target") is not None:
        sa["12m_price_target"] = round(float(after["12m_price_target"]), 2)
        sa["12m_targets_by_scenario"] = {sc: (dr.get("12m_targets") or {}).get(sc) for sc in SCENARIOS}
        if after.get("expected_value") is not None:
            sa["expected_value"] = round(float(after["expected_value"]), 2)
        rec = sa.get("reconciliation")
        if isinstance(rec, dict):
            cp = _num(rec.get("current_price")) or _num(sa.get("current_price"))
            rec["12m_price_target"] = sa["12m_price_target"]
            if after.get("expected_value") is not None:
                rec["blended_iv"] = rec["expected_value"] = sa["expected_value"]
            if cp:
                rec["upside_to_pt_pct"] = round((sa["12m_price_target"] / cp - 1.0) * 100.0, 1)
                if after.get("expected_value") is not None:
                    rec["upside_to_iv_pct"] = round((sa["expected_value"] / cp - 1.0) * 100.0, 1)
            rec["estimate_override"] = True
        dec = (data.get("decisions") or {}).get(ticker)
        if isinstance(dec, dict):
            dec["price_target_agent"] = dec.get("price_target_agent", dec.get("price_target"))
            dec["price_target"] = sa["12m_price_target"]
            dec["price_target_override"] = True
        top = (payload.get("decisions") or {}).get(ticker)
        if isinstance(top, dict):
            top["price_target_agent"] = top.get("price_target_agent", top.get("price_target"))
            top["price_target"] = sa["12m_price_target"]
            top["price_target_override"] = True
    return payload


# ── persistence: src.data.estimate_override_store, shared with the pipeline's carry-forward ──

def ensure_table() -> None:
    from src.data import estimate_override_store as store
    store.ensure_table()


def get_active(run_id: str, ticker: str) -> Optional[dict]:
    from src.data import estimate_override_store as store
    return store.get_active(run_id, ticker)


def list_active(run_id: str) -> list[dict]:
    from src.data import estimate_override_store as store
    return store.list_active(run_id)


def save(run_id: str, ticker: str, user_id: Optional[int], overrides: dict, note: Optional[str], result: dict) -> dict:
    from src.data import estimate_override_store as store
    from app.backend.services import analysis_service as svc
    return store.save(run_id, ticker, user_id, overrides, note, svc._sanitize_floats(result))


def clear(run_id: str, ticker: str) -> int:
    from src.data import estimate_override_store as store
    return store.clear(run_id, ticker)


def attach_pm(record: dict, pm: dict) -> dict:
    """Store a regenerated PM rationale and headline on the active record (applied on read)."""
    from src.data import estimate_override_store as store
    res = dict(record.get("result") or {})
    res["pm"] = pm
    store.update_result(record["id"], res)
    record["result"] = res
    return record


def apply_saved(run_id: str, payload: dict) -> dict:
    """Called by analysis_service.get_run_result: every active override for the run, applied on read."""
    try:
        recs = list_active(run_id)
    except Exception as exc:                                 # noqa: BLE001
        logger.warning("estimate overrides unavailable for %s: %s", run_id, exc)
        return payload
    for rec in recs:
        try:
            apply_to_payload(payload, rec["ticker"], rec)
        except Exception:                                    # noqa: BLE001
            logger.exception("estimate override %s did not apply", rec.get("id"))
    return payload

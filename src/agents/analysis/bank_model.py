"""Earnings-and-capital model for balance-sheet businesses (owner, 2026-10-03, steps two and three).

A bank is not valued on free cash flow and cannot be rolled through a working-capital model: its
earnings come from a balance sheet (earning assets × margin, plus fees), its costs are a ratio of
income, its provisions a ratio of loans, and what it keeps after dividends and buybacks is capital
that must clear a CET1 target. The methods that price it -- GGM P/B, residual income, P/E -- read
return on equity, book value per share and EPS. So management guidance for a bank arrives as loan
growth, net interest margin, fee growth, cost-to-income, credit cost, payout and a capital target,
and this module turns those into a five-year earnings-and-capital forecast whose outputs the legs
consume (dcf_agent: `_bank_model_roe`, `_bank_model_bvps`, `_bank_model_eps`).

Insurers follow the same shape with premiums, a combined ratio and an investment yield on the float.

Inputs, in order of precedence per assumption: the research's family guidance (`family_metrics`),
the bank-metrics extraction (CET1, NIM, efficiency, credit cost, payout, loan growth), the latest
audited line items, then a PROPOSED default. Every value carries its source. Scenario bands are
PROPOSED constants (bear: NIM −10bp, credit cost +50%, cost-to-income +2pp; bull the mirror).
"""
from __future__ import annotations

from typing import Optional

YEARS = 5

DEFAULTS = {
    "nim_bounds": (0.003, 0.08), "cost_to_income_bounds": (0.20, 0.90), "credit_cost_bps_bounds": (0.0, 400.0),
    "payout_bounds": (0.0, 1.2), "asset_growth_bounds": (-0.10, 0.25), "tax_rate_default": 0.21, "tax_rate_bounds": (0.05, 0.40),
    "loan_share_of_assets": 0.55,          # loans ≈ this share of total assets when the filing gives no loan book (PROPOSED)
    "earning_asset_share": 0.90,           # earning assets ≈ this share of total assets (PROPOSED)
    "cet1_capital_share_of_equity": 0.90,  # CET1 capital ≈ tangible common equity (PROPOSED)
    "cet1_target_default": 0.13,
    "bands": {"bear": {"nim_bp": -10.0, "credit_cost_mult": 1.5, "cost_to_income_pp": 0.02, "growth_pp": -0.02},
              "base": {"nim_bp": 0.0, "credit_cost_mult": 1.0, "cost_to_income_pp": 0.0, "growth_pp": 0.0},
              "bull": {"nim_bp": 10.0, "credit_cost_mult": 0.7, "cost_to_income_pp": -0.02, "growth_pp": 0.02}},
    "insurer": {"combined_ratio_bounds": (0.70, 1.20), "investment_yield_bounds": (0.005, 0.10), "float_share_of_assets": 0.75,
                "bands": {"bear": {"combined_ratio_pp": 0.03, "yield_bp": -25.0, "growth_pp": -0.02},
                          "base": {"combined_ratio_pp": 0.0, "yield_bp": 0.0, "growth_pp": 0.0},
                          "bull": {"combined_ratio_pp": -0.02, "yield_bp": 25.0, "growth_pp": 0.02}}},
}

BANK_GUIDED_FIELDS = ("loan_growth_fy1", "loan_growth_fy2", "nim_fy1", "nim_fy2", "fee_income_growth_fy1", "fee_income_growth_fy2",
                      "cost_to_income_fy1", "cost_to_income_fy2", "credit_cost_bps_fy1", "credit_cost_bps_fy2", "payout_ratio", "cet1_target")
INSURER_GUIDED_FIELDS = ("premium_growth_fy1", "premium_growth_fy2", "combined_ratio_fy1", "combined_ratio_fy2", "investment_yield_fy1",
                         "investment_yield_fy2", "payout_ratio")


def _f(v) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _src(value, source, needed_for):
    return {"value": value, "source": source, "needed_for": needed_for}


# ── opening position ─────────────────────────────────────────────────────────

def opening_from_line_items(most_recent: dict, shares: Optional[float], total_income: Optional[float], fy_label: Optional[str] = None) -> Optional[dict]:
    """The latest audited year for a bank or insurer: equity, assets, income, earnings, shares, goodwill."""
    eq = _f(most_recent.get("total_equity"))
    ni = _f(most_recent.get("net_income"))
    ta = _f(most_recent.get("total_assets"))
    if not eq or not ta or ni is None:
        return None
    gw = (_f(most_recent.get("goodwill")) or 0.0) + (_f(most_recent.get("intangible_assets")) or 0.0)
    sh = _f(shares) or _f(most_recent.get("shares_outstanding"))
    return {"fy_label": fy_label or str(most_recent.get("report_period") or "")[:4], "fiscal_year": _year(fy_label or most_recent.get("report_period")),
            "equity": eq, "tangible_equity": max(eq - gw, 0.0), "goodwill_intangibles": gw, "total_assets": ta, "net_income": ni,
            "total_income": _f(total_income) or _f(most_recent.get("revenue")), "shares": sh, "bvps": (eq / sh) if sh else None,
            "tbvps": (max(eq - gw, 0.0) / sh) if sh else None, "roe": (ni / eq) if eq else None,
            "interest_income": _f(most_recent.get("interest_income")), "interest_expense": _f(most_recent.get("interest_expense")),
            "pretax": _f(most_recent.get("pretax_income")), "tax": _f(most_recent.get("income_tax_expense")),
            "dps": _f(most_recent.get("dividends_per_share")), "ticker": most_recent.get("ticker")}


def _accepted_bank_payout(ticker) -> Optional[float]:
    """Plan IN2 (2026-10-04): an owner-ACCEPTED total payout (ordinary + capital-return dividends) from
    valuation_constants.bank_payout; a PROPOSED entry prices nothing."""
    try:
        from src.data import valuation_constants as _vc
        e = ((_vc.load().get("bank_payout") or {}).get("entries") or {}).get(str(ticker or "").upper()) or {}
        if str(e.get("status") or "").upper() != "ACCEPTED":
            return None
        v = e.get("payout_ratio")
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:                                      # noqa: BLE001
        return None


def _year(s) -> Optional[int]:
    import re
    m = re.search(r"(19|20)\d{2}", str(s or ""))
    return int(m.group(0)) if m else None


# ── assumptions ──────────────────────────────────────────────────────────────

def bank_assumptions(opening: dict, bank_metrics: Optional[dict], block: Optional[dict], *, coe: Optional[float] = None,
                     target_roe: Optional[float] = None, cet1_target: Optional[float] = None) -> dict:
    """Each assumption from the first source that has it: family guidance → bank-metrics extraction → audited line items → default."""
    bm = bank_metrics or {}
    fam = (((block or {}).get("family_metrics") or {}).get("bank")) or {}
    est = ((block or {}).get("estimates") or {}).get("base") or {}
    cfg = DEFAULTS
    a: dict = {}

    def pick(name, needed, *cands, bounds=None, default=None):
        for val, label in cands:
            v = _f(val)
            if v is not None:
                if bounds:
                    v = _clamp(v, *bounds)
                a[name] = _src(v, label, needed)
                return
        a[name] = _src(default, "default (PROPOSED)" if default is not None else "missing", needed)

    pick("asset_growth_fy1", "earning assets and loans, year 1", (fam.get("loan_growth_fy1"), "guidance: loan growth FY+1"), (bm.get("loan_growth_yoy"), "bank metrics: loan growth"),
         (est.get("revenue_growth_fy1"), "estimate: total income growth FY+1"), bounds=cfg["asset_growth_bounds"], default=0.04)
    pick("asset_growth_fy2", "earning assets and loans, year 2", (fam.get("loan_growth_fy2"), "guidance: loan growth FY+2"), (fam.get("loan_growth_fy1"), "guidance: loan growth FY+1 carried"),
         (bm.get("loan_growth_yoy"), "bank metrics: loan growth"), (est.get("revenue_growth_fy2"), "estimate: total income growth FY+2"), bounds=cfg["asset_growth_bounds"], default=0.04)
    nim_hist = None
    ta, ii, ie = opening.get("total_assets"), opening.get("interest_income"), opening.get("interest_expense")
    if ta and ii is not None and ie is not None and ta > 0:
        nim_hist = (ii - abs(ie)) / (ta * cfg["earning_asset_share"])
    pick("nim_fy1", "net interest income = earning assets × NIM", (fam.get("nim_fy1"), "guidance: NIM FY+1"), (bm.get("nim_pct"), "bank metrics: NIM"), (nim_hist, "line items: (interest income − expense) / earning assets"),
         bounds=cfg["nim_bounds"], default=0.02)
    pick("nim_fy2", "NIM, year 2 onward", (fam.get("nim_fy2"), "guidance: NIM FY+2"), (a["nim_fy1"]["value"], a["nim_fy1"]["source"] + " (held)"), bounds=cfg["nim_bounds"])
    nii0 = (a["nim_fy1"]["value"] or 0.0) * (ta or 0.0) * cfg["earning_asset_share"]
    ti0 = opening.get("total_income") or 0.0
    if ti0 and ti0 > nii0:
        a["fee_income_opening"] = _src(ti0 - nii0, "total income − implied NII", "non-interest income base")
    else:
        a["fee_income_opening"] = _src(nii0 * 0.45, "default (PROPOSED): 45% of NII; the filing gives no usable total income", "non-interest income base")
    pick("fee_income_growth_fy1", "non-interest income growth", (fam.get("fee_income_growth_fy1"), "guidance: fee growth FY+1"), (est.get("revenue_growth_fy1"), "estimate: total income growth FY+1"), bounds=(-0.3, 0.4), default=0.03)
    pick("fee_income_growth_fy2", "non-interest income growth, year 2", (fam.get("fee_income_growth_fy2"), "guidance: fee growth FY+2"), (est.get("revenue_growth_fy2"), "estimate: total income growth FY+2"),
         (a["fee_income_growth_fy1"]["value"], a["fee_income_growth_fy1"]["source"] + " (held)"), bounds=(-0.3, 0.4), default=0.03)
    pick("cost_to_income_fy1", "operating expenses = cost-to-income × total income", (fam.get("cost_to_income_fy1"), "guidance: cost-to-income FY+1"), (bm.get("efficiency_ratio"), "bank metrics: efficiency ratio"),
         bounds=cfg["cost_to_income_bounds"], default=0.50)
    pick("cost_to_income_fy2", "cost-to-income, year 2 onward", (fam.get("cost_to_income_fy2"), "guidance: cost-to-income FY+2"), (a["cost_to_income_fy1"]["value"], a["cost_to_income_fy1"]["source"] + " (held)"),
         bounds=cfg["cost_to_income_bounds"])
    pick("credit_cost_bps_fy1", "provisions = credit cost × loans", (fam.get("credit_cost_bps_fy1"), "guidance: credit cost FY+1"), (bm.get("credit_cost_bps"), "bank metrics: credit cost"), bounds=cfg["credit_cost_bps_bounds"], default=25.0)
    pick("credit_cost_bps_fy2", "credit cost, year 2 onward", (fam.get("credit_cost_bps_fy2"), "guidance: credit cost FY+2"), (a["credit_cost_bps_fy1"]["value"], a["credit_cost_bps_fy1"]["source"] + " (held)"),
         bounds=cfg["credit_cost_bps_bounds"])
    tax_hist = None
    if opening.get("pretax") and opening.get("tax") is not None and opening["pretax"] > 0:
        tax_hist = abs(opening["tax"]) / opening["pretax"]
    pick("tax_rate", "income tax", (tax_hist, "line items: tax / pre-tax"), bounds=cfg["tax_rate_bounds"], default=cfg["tax_rate_default"])
    # Plan IN2 (2026-10-04): the bank's own payout from the filing (ordinary and special dividends per
    # share x shares over net income) before the 40% default -- DBS runs a capital-return programme.
    _dps, _sh, _ni = _f(opening.get("dps")), _f(opening.get("shares")), _f(opening.get("net_income"))
    payout_hist = (_dps * _sh / _ni) if (_dps and _sh and _ni and _ni > 0) else None
    _owner_payout = _accepted_bank_payout(opening.get("ticker"))
    pick("payout_ratio", "dividends and the equity roll", (_owner_payout, "owner-accepted bank payout (ordinary + capital return)"),
         (fam.get("payout_ratio"), "guidance: payout"), (bm.get("dividend_payout_ratio"), "bank metrics: payout"),
         (payout_hist, "line items: dividends per share x shares / net income"), bounds=cfg["payout_bounds"], default=0.40)
    pick("cet1_target", "the capital constraint on distributions", (fam.get("cet1_target"), "guidance: CET1 target"), (cet1_target, "profile calibration: target CET1"), bounds=(0.06, 0.25), default=cfg["cet1_target_default"])
    pick("cet1_ratio_opening", "opening CET1 ratio", (bm.get("cet1_ratio"), "bank metrics: CET1"), bounds=(0.04, 0.30), default=a["cet1_target"]["value"])
    a["loan_share_of_assets"] = _src(cfg["loan_share_of_assets"], "default (PROPOSED)", "loans for the credit-cost charge")
    a["earning_asset_share"] = _src(cfg["earning_asset_share"], "default (PROPOSED)", "earning assets for NII")
    a["cet1_capital_share_of_equity"] = _src(cfg["cet1_capital_share_of_equity"], "default (PROPOSED)", "CET1 capital from equity")
    a["buyback_annual"] = _src(0.0, "default: none unless guided", "share repurchases")
    a["coe"] = _src(_f(coe) or _f(bm.get("cost_of_equity")), "GGM leg" if coe else ("bank metrics" if bm.get("cost_of_equity") else "missing"), "ROE vs cost of equity check")
    a["management_target_roe"] = _src(_f(target_roe) or _f(bm.get("management_target_roe")), "research / profile", "comparison with the model's ROE")
    guided = [k for k in BANK_GUIDED_FIELDS if _f(fam.get(k)) is not None]
    a["_guided_fields"] = {"value": guided, "source": "research family_metrics.bank", "needed_for": "which assumptions management gave"}
    return a


# ── the bank model ───────────────────────────────────────────────────────────

def build_bank(opening: dict, a: dict, *, scenario: str = "base", years: int = YEARS, cfg: Optional[dict] = None) -> Optional[dict]:
    if not opening or not opening.get("equity") or not opening.get("total_assets"):
        return None
    cfg = cfg or DEFAULTS
    band = cfg["bands"].get(scenario) or cfg["bands"]["base"]
    v = lambda k, d=None: (a.get(k, {}).get("value") if isinstance(a.get(k), dict) else a.get(k, d))   # noqa: E731
    val = lambda k, d=0.0: (v(k) if v(k) is not None else d)                                            # noqa: E731
    fy0 = opening.get("fiscal_year")
    labels = [f"FY{fy0 + i + 1}E" if fy0 else f"FY+{i + 1}E" for i in range(years)]
    ta, eq, sh = float(opening["total_assets"]), float(opening["equity"]), float(opening.get("shares") or 0.0)
    gw = float(opening.get("goodwill_intangibles") or 0.0)
    fee = float(val("fee_income_opening"))
    cet1_cap = eq * val("cet1_capital_share_of_equity", 0.9)
    rwa = cet1_cap / val("cet1_ratio_opening", val("cet1_target", 0.13)) if val("cet1_ratio_opening", 0) > 0 else ta * 0.5
    rwa_density = rwa / ta if ta else 0.5
    tax_rate, payout, bb_target, cet1_target = val("tax_rate", 0.21), val("payout_ratio", 0.4), val("buyback_annual"), val("cet1_target", 0.13)
    coe = v("coe")
    rows: dict = {}
    L = lambda k, x: rows.setdefault(k, []).append(x)                                                   # noqa: E731
    recon, checks, notes = [], [], []
    for i in range(years):
        yr = i + 1
        g = (val("asset_growth_fy1") if yr == 1 else val("asset_growth_fy2")) + band["growth_pp"]
        nim = (val("nim_fy1") if yr == 1 else val("nim_fy2")) + band["nim_bp"] / 1e4
        cti = _clamp((val("cost_to_income_fy1") if yr == 1 else val("cost_to_income_fy2")) + band["cost_to_income_pp"], 0.1, 0.95)
        ccb = (val("credit_cost_bps_fy1") if yr == 1 else val("credit_cost_bps_fy2")) * band["credit_cost_mult"]
        fee_g = (val("fee_income_growth_fy1") if yr == 1 else val("fee_income_growth_fy2")) + band["growth_pp"]
        ta_open = ta
        ta = ta_open * (1.0 + g)
        ea_avg = (ta_open + ta) / 2.0 * val("earning_asset_share", 0.9)
        nii = ea_avg * nim
        fee = fee * (1.0 + fee_g)
        income = nii + fee
        opex = income * cti
        ppop = income - opex
        loans = (ta_open + ta) / 2.0 * val("loan_share_of_assets", 0.55)
        prov = loans * ccb / 1e4
        pretax = ppop - prov
        tax = max(pretax, 0.0) * tax_rate
        ni = pretax - tax
        div = max(ni, 0.0) * payout
        # capital: CET1 capital accrues retained earnings; distributions are cut to hold the target
        rwa = rwa_density * ta
        cet1_pre = cet1_cap + ni - div
        headroom = cet1_pre - cet1_target * rwa
        bb = min(bb_target, max(headroom, 0.0)) if bb_target else 0.0
        cut = 0.0
        if cet1_pre - bb < cet1_target * rwa:
            cut = min(div, cet1_target * rwa - (cet1_pre - bb))
            div -= cut
        eq_open = eq
        eq = eq_open + ni - div - bb
        cet1_cap = cet1_cap + ni - div - bb
        cet1 = cet1_cap / rwa if rwa else None
        if bb and opening.get("bvps"):
            sh = max(sh - bb / (opening["bvps"] * 1.5), 1.0)          # repurchased at 1.5x book when no price is given (PROPOSED)
        roe = ni / ((eq_open + eq) / 2.0) if (eq_open + eq) else None
        rote = ni / (((eq_open - gw) + (eq - gw)) / 2.0) if ((eq_open - gw) + (eq - gw)) > 0 else None
        for k, x in (("total_assets", ta), ("asset_growth", g), ("earning_assets_avg", ea_avg), ("nim", nim), ("net_interest_income", nii), ("fee_income", fee),
                     ("total_income", income), ("cost_to_income", cti), ("operating_expenses", -opex), ("pre_provision_profit", ppop), ("loans_avg", loans),
                     ("credit_cost_bps", ccb), ("provisions", -prov), ("pretax", pretax), ("tax", -tax), ("net_income", ni), ("shares", sh), ("eps", ni / sh if sh else None),
                     ("dividends", -div), ("dividend_per_share", div / sh if sh else None), ("buybacks", -bb), ("payout_ratio", (div / ni) if ni > 0 else None),
                     ("equity", eq), ("tangible_equity", eq - gw), ("bvps", eq / sh if sh else None), ("tbvps", (eq - gw) / sh if sh else None),
                     ("roe", roe), ("rote", rote), ("roa", ni / ((ta_open + ta) / 2.0)), ("rwa", rwa), ("cet1_capital", cet1_cap), ("cet1_ratio", cet1),
                     ("distribution_cut", cut)):
            L(k, x)
        for aid, name, lhs, rhs in ((1, "Equity ending = prior + net income − dividends − buybacks", eq, eq_open + ni - div - bb),
                                    (2, "Book value per share = equity / shares", eq / sh if sh else 0.0, (eq / sh) if sh else 0.0),
                                    (3, "CET1 ratio = CET1 capital / risk-weighted assets", cet1 or 0.0, (cet1_cap / rwa) if rwa else 0.0),
                                    (4, "Net income = pre-provision profit − provisions − tax", ni, ppop - prov - tax),
                                    (5, "No distributions while CET1 is below target (dividends and buybacks cut first)", (div + bb) if (cet1 is not None and cet1 < cet1_target - 1e-9) else 0.0, 0.0)):
            ok = (lhs <= rhs + 1e-6) if aid == 5 else (abs(lhs - rhs) < 0.01)
            recon.append({"id": aid, "name": name, "year": labels[i], "lhs": lhs, "rhs": rhs, "diff": lhs - rhs, "ok": ok})
        checks.append({"year": labels[i], "roe": roe, "coe": coe, "roe_over_coe": (roe - coe) if (roe is not None and coe) else None,
                       "cet1_ratio": cet1, "cet1_target": cet1_target, "distribution_cut": cut})
    if any(c["distribution_cut"] > 0 for c in checks):
        notes.append("Dividends were cut in " + ", ".join(c["year"] for c in checks if c["distribution_cut"] > 0) + " to hold CET1 at the target.")
    _short = [c for c in checks if c.get("cet1_ratio") is not None and c["cet1_ratio"] < c["cet1_target"] - 1e-9]
    if _short:
        notes.append("CET1 stays below the target in " + ", ".join(c["year"] for c in _short) + " even with nothing distributed: a capital shortfall on these drivers.")
    if coe and rows.get("roe"):
        _r3 = [r for r in rows["roe"][2:] if r is not None]
        if _r3 and sum(_r3) / len(_r3) < coe:
            notes.append(f"Steady-state ROE {sum(_r3) / len(_r3):.1%} is below the cost of equity {coe:.1%}: the franchise destroys value on these assumptions.")
    failures = [r for r in recon if not r["ok"]]
    steady = [r for r in rows["roe"][2:] if r is not None] or [r for r in rows["roe"] if r is not None]
    steady_rote = [r for r in rows["rote"][2:] if r is not None] or [r for r in rows["rote"] if r is not None]
    # Calibration: does the model, on these drivers, reproduce the latest audited year? FY+1 net
    # income against the opening year's grown by the asset growth; outside ±20% the drivers (a
    # line-item NIM, a default tax rate) do not describe this bank, and the legs keep the research.
    ni0 = _f(opening.get("net_income"))
    g1 = val("asset_growth_fy1") + band["growth_pp"]
    ratio = (rows["net_income"][0] / (ni0 * (1.0 + g1))) if (ni0 and ni0 > 0) else None
    calibration = {"opening_net_income": ni0, "fy1_net_income": rows["net_income"][0], "ratio_to_grown_opening": ratio,
                   "ok": (0.80 <= ratio <= 1.25) if ratio is not None else False,
                   "detail": (f"FY+1 net income {rows['net_income'][0] / 1e9:,.2f}bn vs the latest year {ni0 / 1e9:,.2f}bn grown {g1:+.1%} ({ratio:.2f}x)" if ratio is not None else "no latest-year net income to calibrate against")}
    if not calibration["ok"]:
        notes.append("Not calibrated to the latest year: " + calibration["detail"] + ". The legs keep the research's ROE until management's drivers are in the guidance block.")
    out = {"kind": "bank", "calibration": calibration, "scenario": scenario, "fy_labels": labels, "opening": opening, "assumptions": a, "rows": rows, "checks": checks, "notes": notes,
           "reconciliation": {"ok": not failures, "assertions": recon, "failures": failures,
                              "suite": ["Equity_Ending == Equity_Prior + Net_Income - Dividends - Buybacks", "BVPS == Equity / Shares",
                                        "CET1_Ratio == CET1_Capital / RWA", "Net_Income == PPOP - Provisions - Tax", "No_Distributions_While_CET1_Below_Target"]},
           "flow": ["Balance sheet drivers (asset growth, NIM, loans)", "Income statement (NII + fees − costs − provisions − tax)",
                    "Capital (retained earnings, dividends, buybacks, CET1 vs target)", "Per share (EPS, DPS, BVPS, ROE, ROTE)"],
           "steady_roe": (sum(steady) / len(steady)) if steady else None, "steady_rote": (sum(steady_rote) / len(steady_rote)) if steady_rote else None,
           "bvps_fy1": rows["bvps"][0] if rows.get("bvps") else None, "tbvps_fy1": rows["tbvps"][0] if rows.get("tbvps") else None,
           "eps_fy1": rows["eps"][0] if rows.get("eps") else None, "eps_fy2": rows["eps"][1] if rows.get("eps") and len(rows["eps"]) > 1 else None,
           "guided_fields": (a.get("_guided_fields") or {}).get("value") or []}
    if failures:
        out["skipped"] = ("RECONCILIATION FAILED: " + "; ".join(f"{f['year']} #{f['id']} {f['name']} (diff {f['diff']:,.4f})" for f in failures[:6])
                          + ". Trace the discrepancy to the equity roll, the capital identity or the distribution cut.")
    return out


# ── the insurer model ────────────────────────────────────────────────────────

def insurer_assumptions(opening: dict, metrics: Optional[dict], block: Optional[dict], *, coe: Optional[float] = None) -> dict:
    m = metrics or {}
    fam = (((block or {}).get("family_metrics") or {}).get("insurer")) or {}
    est = ((block or {}).get("estimates") or {}).get("base") or {}
    icfg = DEFAULTS["insurer"]
    a: dict = {}

    def pick(name, needed, *cands, bounds=None, default=None):
        for val_, label in cands:
            x = _f(val_)
            if x is not None:
                a[name] = _src(_clamp(x, *bounds) if bounds else x, label, needed)
                return
        a[name] = _src(default, "default (PROPOSED)" if default is not None else "missing", needed)

    pick("premium_growth_fy1", "net earned premiums, year 1", (fam.get("premium_growth_fy1"), "guidance: premium growth FY+1"), (m.get("premium_growth"), "insurance metrics: premium growth"),
         (est.get("revenue_growth_fy1"), "estimate: revenue growth FY+1"), bounds=(-0.2, 0.4), default=0.04)
    pick("premium_growth_fy2", "net earned premiums, year 2 onward", (fam.get("premium_growth_fy2"), "guidance: premium growth FY+2"), (est.get("revenue_growth_fy2"), "estimate: revenue growth FY+2"),
         (a["premium_growth_fy1"]["value"], a["premium_growth_fy1"]["source"] + " (held)"), bounds=(-0.2, 0.4))
    pick("combined_ratio_fy1", "underwriting result = premiums × (1 − combined ratio)", (fam.get("combined_ratio_fy1"), "guidance: combined ratio FY+1"), (m.get("combined_ratio"), "insurance metrics: combined ratio"),
         bounds=icfg["combined_ratio_bounds"], default=0.96)
    pick("combined_ratio_fy2", "combined ratio, year 2 onward", (fam.get("combined_ratio_fy2"), "guidance: combined ratio FY+2"), (a["combined_ratio_fy1"]["value"], a["combined_ratio_fy1"]["source"] + " (held)"),
         bounds=icfg["combined_ratio_bounds"])
    pick("investment_yield_fy1", "investment income = float × yield", (fam.get("investment_yield_fy1"), "guidance: investment yield FY+1"), (m.get("investment_yield"), "insurance metrics: investment yield"),
         bounds=icfg["investment_yield_bounds"], default=0.035)
    pick("investment_yield_fy2", "investment yield, year 2 onward", (fam.get("investment_yield_fy2"), "guidance: investment yield FY+2"), (a["investment_yield_fy1"]["value"], a["investment_yield_fy1"]["source"] + " (held)"),
         bounds=icfg["investment_yield_bounds"])
    tax_hist = (abs(opening["tax"]) / opening["pretax"]) if (opening.get("pretax") and opening.get("tax") is not None and opening["pretax"] > 0) else None
    pick("tax_rate", "income tax", (tax_hist, "line items: tax / pre-tax"), bounds=DEFAULTS["tax_rate_bounds"], default=DEFAULTS["tax_rate_default"])
    pick("payout_ratio", "dividends and the equity roll", (fam.get("payout_ratio"), "guidance: payout"), (m.get("dividend_payout_ratio"), "insurance metrics: payout"), bounds=DEFAULTS["payout_bounds"], default=0.40)
    a["float_share_of_assets"] = _src(icfg["float_share_of_assets"], "default (PROPOSED)", "investable float from total assets")
    a["premiums_opening"] = _src(_f(opening.get("total_income")), "line items: revenue (premiums + investment income)" if opening.get("total_income") else "missing", "premium base")
    a["coe"] = _src(_f(coe) or _f(m.get("cost_of_equity")), "leg" if coe else "missing", "ROE vs cost of equity check")
    a["_guided_fields"] = {"value": [k for k in INSURER_GUIDED_FIELDS if _f(fam.get(k)) is not None], "source": "research family_metrics.insurer", "needed_for": "which assumptions management gave"}
    return a


def build_insurer(opening: dict, a: dict, *, scenario: str = "base", years: int = YEARS) -> Optional[dict]:
    if not opening or not opening.get("equity") or not opening.get("total_assets"):
        return None
    icfg = DEFAULTS["insurer"]
    band = icfg["bands"].get(scenario) or icfg["bands"]["base"]
    v = lambda k: (a.get(k, {}).get("value") if isinstance(a.get(k), dict) else None)                     # noqa: E731
    val = lambda k, d=0.0: (v(k) if v(k) is not None else d)                                             # noqa: E731
    fy0 = opening.get("fiscal_year")
    labels = [f"FY{fy0 + i + 1}E" if fy0 else f"FY+{i + 1}E" for i in range(years)]
    ta, eq, sh = float(opening["total_assets"]), float(opening["equity"]), float(opening.get("shares") or 0.0)
    gw = float(opening.get("goodwill_intangibles") or 0.0)
    ti0 = float(val("premiums_opening") or 0.0)
    inv0 = ta * val("float_share_of_assets", 0.75) * val("investment_yield_fy1", 0.035)
    prem = max(ti0 - inv0, 0.0) if ti0 else ta * 0.3
    tax_rate, payout, coe = val("tax_rate", 0.21), val("payout_ratio", 0.4), v("coe")
    rows: dict = {}
    L = lambda k, x: rows.setdefault(k, []).append(x)                                                    # noqa: E731
    recon, checks, notes = [], [], []
    for i in range(years):
        yr = i + 1
        g = (val("premium_growth_fy1") if yr == 1 else val("premium_growth_fy2")) + band["growth_pp"]
        cr = _clamp((val("combined_ratio_fy1") if yr == 1 else val("combined_ratio_fy2")) + band["combined_ratio_pp"], 0.5, 1.3)
        y = (val("investment_yield_fy1") if yr == 1 else val("investment_yield_fy2")) + band["yield_bp"] / 1e4
        prem = prem * (1.0 + g)
        ta_open = ta
        ta = ta_open * (1.0 + g)
        float_avg = (ta_open + ta) / 2.0 * val("float_share_of_assets", 0.75)
        uw = prem * (1.0 - cr)
        inv = float_avg * y
        pretax = uw + inv
        tax = max(pretax, 0.0) * tax_rate
        ni = pretax - tax
        div = max(ni, 0.0) * payout
        eq_open = eq
        eq = eq_open + ni - div
        roe = ni / ((eq_open + eq) / 2.0) if (eq_open + eq) else None
        for k, x in (("net_earned_premiums", prem), ("premium_growth", g), ("combined_ratio", cr), ("underwriting_result", uw), ("float_avg", float_avg), ("investment_yield", y),
                     ("investment_income", inv), ("pretax", pretax), ("tax", -tax), ("net_income", ni), ("shares", sh), ("eps", ni / sh if sh else None), ("dividends", -div),
                     ("dividend_per_share", div / sh if sh else None), ("equity", eq), ("bvps", eq / sh if sh else None), ("tbvps", (eq - gw) / sh if sh else None), ("roe", roe), ("total_assets", ta)):
            L(k, x)
        for aid, name, lhs, rhs in ((1, "Equity ending = prior + net income − dividends", eq, eq_open + ni - div), (2, "Book value per share = equity / shares", (eq / sh) if sh else 0.0, (eq / sh) if sh else 0.0),
                                    (3, "Net income = underwriting result + investment income − tax", ni, uw + inv - tax), (4, "Dividends ≤ net income", div, max(ni, 0.0)),
                                    (5, "Investment income = float × yield", inv, float_avg * y)):
            ok = (lhs <= rhs + 1e-6) if aid == 4 else (abs(lhs - rhs) < 0.01)
            recon.append({"id": aid, "name": name, "year": labels[i], "lhs": lhs, "rhs": rhs, "diff": lhs - rhs, "ok": ok})
        checks.append({"year": labels[i], "roe": roe, "coe": coe, "roe_over_coe": (roe - coe) if (roe is not None and coe) else None, "combined_ratio": cr})
    failures = [r for r in recon if not r["ok"]]
    steady = [r for r in rows["roe"][2:] if r is not None] or [r for r in rows["roe"] if r is not None]
    out = {"kind": "insurer", "scenario": scenario, "fy_labels": labels, "opening": opening, "assumptions": a, "rows": rows, "checks": checks, "notes": notes,
           "reconciliation": {"ok": not failures, "assertions": recon, "failures": failures,
                              "suite": ["Equity_Ending == Equity_Prior + Net_Income - Dividends", "BVPS == Equity / Shares", "Net_Income == Underwriting + Investment - Tax",
                                        "Dividends <= Net_Income", "Investment_Income == Float x Yield"]},
           "flow": ["Premiums (growth)", "Underwriting (combined ratio) and investment income (float × yield)", "Earnings and dividends", "Per share (EPS, DPS, BVPS, ROE)"],
           "steady_roe": (sum(steady) / len(steady)) if steady else None, "steady_rote": None, "bvps_fy1": rows["bvps"][0], "tbvps_fy1": rows["tbvps"][0],
           "eps_fy1": rows["eps"][0], "eps_fy2": rows["eps"][1] if len(rows["eps"]) > 1 else None, "guided_fields": (a.get("_guided_fields") or {}).get("value") or []}
    if failures:
        out["skipped"] = "RECONCILIATION FAILED: " + "; ".join(f"{f['year']} #{f['id']} {f['name']} (diff {f['diff']:,.4f})" for f in failures[:6])
    return out


def coverage(model: Optional[dict]) -> list[dict]:
    """What the model needed and where each piece came from (the owner's assumption check)."""
    if not model:
        return [{"assumption": "opening position", "status": "MISSING: no equity, assets or earnings in the line items", "source": None}]
    rows = []
    for name, rec in (model.get("assumptions") or {}).items():
        if name.startswith("_") or not isinstance(rec, dict):
            continue
        src = str(rec.get("source") or "")
        status = ("from management guidance" if src.startswith("guidance") else "missing" if rec.get("value") is None or src.startswith("missing")
                  else "default (PROPOSED)" if src.startswith("default") else "from the extraction / filings")
        rows.append({"assumption": name, "status": status, "source": src, "value": rec.get("value"), "needed_for": rec.get("needed_for")})
    rows.append({"assumption": "opening position", "status": "from the latest audited year", "source": (model.get("opening") or {}).get("fy_label")})
    return rows

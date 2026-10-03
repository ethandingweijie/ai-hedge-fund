"""Three-statement forecast FY+1E .. FY+5E from the valuation agent's estimates (owner, 2026-10-03).

A professional analyst's model carries an income statement, a balance sheet and a cash-flow
statement that tie: net income flows to equity, D&A and capex roll PP&E, working capital moves with
revenue, cash closes the balance sheet. The guidance-to-forecast engine already decides the
operating lines (revenue, EBIT margin, D&A, capex, working-capital absorption, tax, interest). This
module adds the assumptions a full model needs beyond those, takes each from the audited history
with its source named, and builds the three statements so they balance by construction:

    opening balance sheet (latest audited FY, from the run's raw financials)
      + the forecast's operating lines per year
      + the statement assumptions (gross margin, SBC, interest rates, payout, buybacks, debt policy)
      → IS → CF → BS, with a balance check and a minimum-cash revolver

`coverage()` is the check the owner asked for: which assumptions the agent has, which come from
history, which are defaults, and which are missing. Every value is overridable in the estimate
workbench (estimate_override_service) and the same builder re-runs on the user's numbers.
"""
from __future__ import annotations

import re
from statistics import median
from typing import Optional

YEARS = 5

DEFAULTS = {
    "interest_rate_bounds": (0.005, 0.15),
    "interest_income_rate_bounds": (0.0, 0.06),
    "payout_bounds": (0.0, 1.5),
    "sbc_pct_bounds": (0.0, 0.25),
    "gross_margin_bounds": (0.0, 0.98),
    "days_bounds": (0.0, 365.0),
    "revolver_rate_spread": 0.0,           # a draw costs the debt rate; the Excel Model tab reproduces this exactly
}

_OPENING_FIELDS = {
    "cash": "cash_and_equivalents", "sti": "short_term_investments", "receivables": "accounts_receivable", "inventory": "inventory",
    "current_assets": "current_assets", "ppe": "property_plant_equipment", "goodwill": "goodwill", "intangibles": "intangible_assets",
    "total_assets": "total_assets", "payables": "accounts_payable", "short_term_debt": "short_term_debt", "current_liabilities": "current_liabilities",
    "long_term_debt": "long_term_debt", "total_debt": "total_debt", "total_liabilities": "total_liabilities", "equity": "shareholders_equity",
    "minority_interest": "minority_interest", "retained_earnings": "retained_earnings", "shares": "shares_outstanding",
    "revenue": "revenue", "cost_of_revenue": "cost_of_revenue", "gross_profit": "gross_profit", "net_income": "net_income",
    "interest_expense": "interest_expense", "interest_income": "interest_income", "dividends": "dividends_and_distributions",
    "buybacks": "share_buyback", "sbc": "stock_based_compensation", "da": "depreciation_and_amortization", "capex": "capital_expenditure",
    "ebit": "operating_income", "income_tax": "income_tax_expense", "pretax": "pretax_income",
}


def _f(v) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _fy(label: str) -> Optional[int]:
    m = re.search(r"(19|20)\d{2}", str(label or ""))
    return int(m.group(0)) if m else None


def _bounded_median(vals, lo, hi, default):
    vals = [v for v in vals if v is not None and v == v]
    if not vals:
        return default, "default"
    return max(lo, min(hi, median(vals))), "history"


def raw_rows(raw_financials: Optional[dict]) -> list[tuple[str, dict]]:
    """[(FY label, row)] oldest first, from the run's raw_financials (keyed FY2021, FY2022, ...)."""
    if not isinstance(raw_financials, dict):
        return []
    rows = [(k, v) for k, v in raw_financials.items() if isinstance(v, dict) and _fy(k) and _f(v.get("revenue"))]
    rows.sort(key=lambda kv: _fy(kv[0]))
    return rows


def opening_from_raw(raw_financials: Optional[dict]) -> Optional[dict]:
    """The latest audited year's balances and flows, normalised to the names the builder uses."""
    rows = raw_rows(raw_financials)
    if not rows:
        return None
    label, row = rows[-1]
    out = {"fy_label": label, "fiscal_year": _fy(label)}
    for k, src in _OPENING_FIELDS.items():
        out[k] = _f(row.get(src))
    # residual "other" lines make the opening sheet balance exactly as filed
    ca = out.get("current_assets")
    out["other_current_assets"] = (ca - sum(out.get(k) or 0.0 for k in ("cash", "sti", "receivables", "inventory"))) if ca is not None else 0.0
    gi = (out.get("goodwill") or 0.0) + (out.get("intangibles") or 0.0)
    out["goodwill_intangibles"] = gi
    ta = out.get("total_assets")
    out["other_noncurrent_assets"] = (ta - (ca or 0.0) - (out.get("ppe") or 0.0) - gi) if ta is not None else 0.0
    cl = out.get("current_liabilities")
    out["other_current_liabilities"] = (cl - (out.get("payables") or 0.0) - (out.get("short_term_debt") or 0.0)) if cl is not None else 0.0
    tl = out.get("total_liabilities")
    out["other_noncurrent_liabilities"] = (tl - (cl or 0.0) - (out.get("long_term_debt") or 0.0)) if tl is not None else 0.0
    out["balance_gap_as_filed"] = ((ta or 0.0) - (tl or 0.0) - (out.get("equity") or 0.0) - (out.get("minority_interest") or 0.0)) if ta is not None else None
    return out


def assumptions_from_history(raw_financials: Optional[dict], hist: Optional[dict] = None, spot: Optional[float] = None) -> dict:
    """The statement assumptions beyond the forecast's operating lines, each with its source."""
    rows = [r for _, r in raw_rows(raw_financials)][-4:]
    cfg = DEFAULTS
    a: dict = {}

    def put(name, value, source, needed_for):
        a[name] = {"value": value, "source": source, "needed_for": needed_for}

    gm, src = _bounded_median([(_f(r.get("gross_profit")) or 0.0) / _f(r["revenue"]) for r in rows if _f(r.get("gross_profit")) is not None], *cfg["gross_margin_bounds"], None)
    put("gross_margin", gm, src if gm is not None else "missing (no gross profit in the filings)", "gross profit and the COGS line")
    sbc, src = _bounded_median([abs(_f(r.get("stock_based_compensation")) or 0.0) / _f(r["revenue"]) for r in rows if _f(r.get("stock_based_compensation")) is not None], *cfg["sbc_pct_bounds"], 0.0)
    put("sbc_pct", sbc, src, "SBC add-back in operating cash flow and the equity roll")
    def _debt(r):
        td = _f(r.get("total_debt"))
        if td is None and (_f(r.get("short_term_debt")) is not None or _f(r.get("long_term_debt")) is not None):
            td = (_f(r.get("short_term_debt")) or 0.0) + (_f(r.get("long_term_debt")) or 0.0)
        return td
    ir, src = _bounded_median([abs(_f(r.get("interest_expense")) or 0.0) / _debt(r) for r in rows if _debt(r) and _debt(r) > 0 and _f(r.get("interest_expense")) is not None],
                              *cfg["interest_rate_bounds"], 0.05)
    put("interest_rate", ir, src, "interest expense on opening debt")
    iir, src = _bounded_median([(_f(r.get("interest_income")) or 0.0) / ((_f(r.get("cash_and_equivalents")) or 0.0) + (_f(r.get("short_term_investments")) or 0.0))
                                for r in rows if ((_f(r.get("cash_and_equivalents")) or 0.0) + (_f(r.get("short_term_investments")) or 0.0)) > 0 and _f(r.get("interest_income")) is not None],
                               *cfg["interest_income_rate_bounds"], 0.0)
    put("interest_income_rate", iir, src, "interest income on opening cash and investments")
    po, src = _bounded_median([abs(_f(r.get("dividends_and_distributions")) or 0.0) / _f(r["net_income"]) for r in rows if _f(r.get("net_income")) and _f(r["net_income"]) > 0 and _f(r.get("dividends_and_distributions")) is not None],
                              *cfg["payout_bounds"], 0.0)
    put("payout_ratio", po, src, "dividends in financing cash flow and the equity roll")
    bb_vals = [abs(_f(r.get("share_buyback")) or 0.0) for r in rows if _f(r.get("share_buyback")) is not None]
    bb = median(bb_vals) if bb_vals else ((hist or {}).get("buyback_median") or 0.0)
    put("buyback_annual", bb, "history" if bb_vals else ("engine history" if (hist or {}).get("buyback_median") else "default"), "share repurchases and the share count")
    last = rows[-1] if rows else {}
    rev0, cogs0 = _f(last.get("revenue")), abs(_f(last.get("cost_of_revenue")) or 0.0) if last else None
    for name, key, base, label in (("receivable_days", "accounts_receivable", rev0, "receivables"), ("inventory_days", "inventory", cogs0, "inventory"),
                                   ("payable_days", "accounts_payable", cogs0, "payables")):
        v = _f(last.get(key)) if last else None
        days = (v / base * 365.0) if (v is not None and base) else None
        put(name, (max(0.0, min(365.0, days)) if days is not None else None), "history (latest year)" if days is not None else "missing (line not filed); the engine's working-capital intensity carries the change", f"{label} on the balance sheet")
    put("debt_policy", "hold", "assumption", "gross debt held flat; a revolver draw only when cash would fall below the minimum")
    put("min_cash", _f(last.get("cash_and_equivalents")) if last else None, "history (latest year)" if last else "missing", "the revolver trigger")
    put("minority_share_of_ni", 0.0, "default (the filings give no minority P&L line)", "net income to common")
    put("acquisitions", 0.0, "assumption", "investing cash flow: no M&A modelled")
    put("buyback_price", spot, "spot price (held)" if spot else "missing (no spot): shares held flat", "shares retired by the buyback")
    return a


def coverage(fc: Optional[dict], opening: Optional[dict], a: dict) -> list[dict]:
    """The check: what a three-statement model needs, and where each piece comes from."""
    rows = []
    has_fc = bool(fc and fc.get("rows"))
    for name, needed in (("revenue path", "forecast (guided years, bridge, fade)"), ("EBIT margin path", "forecast (archetype curve to the guided margin)"),
                         ("D&A", "forecast (rolled forward over the useful life)"), ("capex", "forecast (D&A + alpha × new revenue)"),
                         ("working-capital change", "forecast (history intensity × new revenue)"), ("tax rate", "forecast (history median)"),
                         ("interest expense (base year)", "forecast (history)")):
        rows.append({"assumption": name, "status": "from the agent's forecast" if has_fc else "MISSING: no guidance-derived forecast on this run", "source": needed if has_fc else "enter estimates in the workbench"})
    for name, rec in a.items():
        v = rec.get("value")
        status = ("missing" if v is None else ("default" if str(rec.get("source", "")).startswith("default") else "from history/assumption"))
        rows.append({"assumption": name, "status": status, "source": rec.get("source"), "value": v, "needed_for": rec.get("needed_for")})
    rows.append({"assumption": "opening balance sheet", "status": "from the latest audited year" if opening else "MISSING: no raw financials on this run",
                 "source": (opening or {}).get("fy_label")})
    return rows


def build(fc: dict, opening: dict, a: dict, *, years: int = YEARS, fy1: Optional[int] = None, interest_base: Optional[float] = None) -> Optional[dict]:
    """IS, CF and BS for FY+1 .. FY+years from the forecast rows, the opening sheet and the assumptions."""
    rows = (fc or {}).get("rows") or []
    if not rows or not opening:
        return None
    v = lambda k, d=0.0: (a.get(k, {}).get("value") if isinstance(a.get(k), dict) else a.get(k))  # noqa: E731
    val = lambda k, d=0.0: (v(k) if v(k) is not None else d)                                           # noqa: E731
    fy0 = opening.get("fiscal_year") or ((fy1 - 1) if fy1 else None)
    labels = [f"FY{(fy0 or 0) + i + 1}E" if fy0 else f"FY+{i + 1}E" for i in range(years)]
    gm, sbc_pct, ir, iir, po = v("gross_margin"), val("sbc_pct"), val("interest_rate", 0.05), val("interest_income_rate"), val("payout_ratio")
    bb_target, price = val("buyback_annual"), v("buyback_price")
    ar_d, inv_d, ap_d = v("receivable_days"), v("inventory_days"), v("payable_days")
    min_cash = val("min_cash", opening.get("cash") or 0.0)
    o = {k: (opening.get(k) or 0.0) for k in ("cash", "sti", "receivables", "inventory", "other_current_assets", "ppe", "goodwill_intangibles", "other_noncurrent_assets",
                                               "payables", "short_term_debt", "other_current_liabilities", "long_term_debt", "other_noncurrent_liabilities", "equity", "minority_interest")}
    o["retained_earnings"] = float(opening.get("retained_earnings") or 0.0) if opening.get("retained_earnings") is not None else o["equity"]
    o["other_equity"] = o["equity"] - o["retained_earnings"]                 # APIC, treasury, reserves: takes buybacks and SBC
    recon: list[dict] = []
    shares = float(opening.get("shares") or (fc.get("inputs") or {}).get("shares") or 0.0)
    rev_prev = opening.get("revenue") or (rows[0]["revenue"] / (1.0 + rows[0]["growth"]) if rows[0].get("growth") not in (None, -1.0) else rows[0]["revenue"])
    IS, CF, BS, SCH, checks, notes = {}, {}, {}, {}, [], []
    L = lambda d, k, x: d.setdefault(k, []).append(x)                                                   # noqa: E731
    revolver = 0.0
    for i in range(min(years, len(rows))):
        r = rows[i]
        rev, ebit, da, capex, dnwc = float(r["revenue"]), float(r["ebit"]), float(r.get("da") or 0.0), float(r.get("capex") or 0.0), float(r.get("delta_nwc") or 0.0)
        cogs = rev * (1.0 - gm) if gm is not None else None
        gp = rev - cogs if cogs is not None else None
        opex = (gp - ebit - da) if gp is not None else (rev - ebit - da)
        sbc = rev * sbc_pct
        debt_open = o["short_term_debt"] + o["long_term_debt"]
        int_exp = debt_open * ir + revolver * (ir + DEFAULTS["revolver_rate_spread"])
        int_inc = (o["cash"] + o["sti"]) * iir
        pretax = ebit - int_exp + int_inc
        tax_rate = float((fc.get("history") or {}).get("tax_rate") or 0.21)
        tax = max(pretax, 0.0) * tax_rate
        ni_total = pretax - tax
        mi_share = ni_total * val("minority_share_of_ni")
        ni = ni_total - mi_share
        # cash flow
        cfo = ni + da + sbc - dnwc + mi_share
        cfi = -capex - val("acquisitions")
        div = max(ni, 0.0) * po
        cash_pre = o["cash"] + cfo + cfi - div
        bb = max(0.0, min(bb_target, cash_pre - min_cash)) if bb_target else 0.0
        cash_post = cash_pre - bb
        draw = max(0.0, min_cash - cash_post)                     # revolver keeps cash at the minimum
        revolver += draw
        cff = -div - bb + draw
        cash = o["cash"] + cfo + cfi + cff
        # balance sheet
        ar = (rev * ar_d / 365.0) if ar_d is not None else o["receivables"]
        inv = ((cogs if cogs is not None else rev) * inv_d / 365.0) if inv_d is not None else o["inventory"]
        ap = ((cogs if cogs is not None else rev) * ap_d / 365.0) if ap_d is not None else o["payables"]
        named_dnwc = (ar - o["receivables"]) + (inv - o["inventory"]) - (ap - o["payables"])
        oca = o["other_current_assets"] + (dnwc - named_dnwc)   # the forecast's working-capital change beyond the named lines
        ppe = o["ppe"] + capex - da
        gi, onca = o["goodwill_intangibles"], o["other_noncurrent_assets"]
        std, ltd, ocl, oncl, mi = o["short_term_debt"] + draw, o["long_term_debt"], o["other_current_liabilities"], o["other_noncurrent_liabilities"], o["minority_interest"] + mi_share
        re_ = o["retained_earnings"] + ni - div                   # assertion 3: RE_end = RE_prior + NI - common dividends
        oeq = o["other_equity"] - bb + sbc
        eq = re_ + oeq
        ta = cash + o["sti"] + ar + inv + oca + ppe + gi + onca
        tl = ap + std + ocl + ltd + oncl
        tle = tl + eq + mi
        if bb and price:
            shares = max(shares - bb / price, 1.0)
        for k, x in (("revenue", rev), ("growth", rev / rev_prev - 1.0 if rev_prev else None), ("cogs", -cogs if cogs is not None else None), ("gross_profit", gp),
                     ("gross_margin", (gp / rev) if gp is not None and rev else None), ("opex_ex_da", -opex), ("ebitda", ebit + da), ("da", -da), ("ebit", ebit),
                     ("ebit_margin", ebit / rev if rev else None), ("interest_expense", -int_exp), ("interest_income", int_inc), ("pretax", pretax), ("tax", -tax),
                     ("tax_rate", tax_rate), ("minority", -mi_share), ("net_income", ni), ("net_margin", ni / rev if rev else None), ("shares", shares), ("eps", ni / shares if shares else None),
                     ("dividends_per_share", div / shares if shares else None), ("sbc_memo", sbc)):
            L(IS, k, x)
        for k, x in (("net_income", ni), ("da", da), ("sbc", sbc), ("change_nwc", -dnwc), ("minority", mi_share), ("cfo", cfo), ("capex", -capex), ("acquisitions", -val("acquisitions")), ("cfi", cfi),
                     ("dividends", -div), ("buybacks", -bb), ("debt_change", draw), ("cff", cff), ("net_change_cash", cfo + cfi + cff), ("opening_cash", o["cash"]), ("closing_cash", cash),
                     ("fcf", cfo - capex), ("fcf_after_sbc", cfo - capex - sbc)):
            L(CF, k, x)
        for k, x in (("cash", cash), ("sti", o["sti"]), ("receivables", ar), ("inventory", inv), ("other_current_assets", oca), ("current_assets", cash + o["sti"] + ar + inv + oca),
                     ("ppe", ppe), ("goodwill_intangibles", gi), ("other_noncurrent_assets", onca), ("total_assets", ta),
                     ("payables", ap), ("short_term_debt", std), ("other_current_liabilities", ocl), ("current_liabilities", ap + std + ocl), ("long_term_debt", ltd),
                     ("other_noncurrent_liabilities", oncl), ("total_liabilities", tl), ("retained_earnings", re_), ("other_equity", oeq), ("equity", eq), ("minority_interest", mi), ("total_liabilities_equity", tle),
                     ("net_debt", std + ltd - cash - o["sti"]), ("balance_check", ta - tle)):
            L(BS, k, x)
        for k, x in (("wc_receivables", ar), ("wc_inventory", inv), ("wc_payables", ap), ("wc_other", oca), ("wc_net", ar + inv + oca - ap),
                     ("wc_change", dnwc), ("wc_named_change", named_dnwc),
                     ("ppe_open", o["ppe"]), ("ppe_capex", capex), ("ppe_da", -da), ("ppe_close", ppe),
                     ("debt_open", debt_open), ("debt_draw", draw), ("debt_close", debt_open + draw), ("debt_interest", int_exp), ("debt_rate", ir),
                     ("cash_open", o["cash"]), ("cash_interest_income", int_inc)):
            L(SCH, k, x)
        # ── the reconciliation suite (owner's execution mandate) ──
        d_ca_ex_cash = (o["sti"] + ar + inv + oca) - (o["sti"] + o["receivables"] + o["inventory"] + o["other_current_assets"])
        d_cl_ex_std = (ap + ocl) - (o["payables"] + o["other_current_liabilities"])
        for aid, name, lhs, rhs in (
                (1, "Total assets = total liabilities + equity", ta, tle),
                (2, "Cash ending = cash beginning + net change in cash (CFS)", cash, o["cash"] + (cfo + cfi + cff)),
                (3, "Retained earnings ending = prior + net income - common dividends", re_, o["retained_earnings"] + ni - div),
                (4, "Net PP&E = prior + capex (CFS) - depreciation (IS)", ppe, o["ppe"] + capex - da),
                (5, "NWC change (CFS) = -(delta current assets ex cash - delta current liabilities ex short debt)", -dnwc, -(d_ca_ex_cash - d_cl_ex_std))):
            recon.append({"id": aid, "name": name, "year": labels[i], "lhs": lhs, "rhs": rhs, "diff": lhs - rhs, "ok": abs(lhs - rhs) < 0.01})
        checks.append({"year": labels[i], "balance_gap": ta - tle, "cash_at_minimum": draw > 0, "revolver_draw": draw,
                       "net_debt_to_ebitda": ((std + ltd - cash - o["sti"]) / (ebit + da)) if (ebit + da) else None,
                       "interest_cover": (ebit / int_exp) if int_exp else None})
        o.update({"cash": cash, "receivables": ar, "inventory": inv, "other_current_assets": oca, "ppe": ppe, "payables": ap, "short_term_debt": std,
                  "equity": eq, "retained_earnings": re_, "other_equity": oeq, "minority_interest": mi})
        rev_prev = rev
    if gm is None:
        notes.append("No gross profit in the filings: the income statement runs from revenue to EBIT without a COGS line.")
    if any(c["revolver_draw"] > 0 for c in checks):
        notes.append("Cash would fall below the opening level in " + ", ".join(c["year"] for c in checks if c["revolver_draw"] > 0) + "; a revolver draw keeps it there (shown in short-term debt).")
    if price is None and bb_target:
        notes.append("No spot price for the buyback: cash is spent, the share count is held.")
    notes.append("Minority interest and 'other' balance-sheet lines are held at the opening year; goodwill and intangibles are not amortised separately (all D&A rolls PP&E).")
    failures = [r for r in recon if not r["ok"]]
    reconciliation = {"ok": not failures, "assertions": recon, "failures": failures,
                      "suite": ["ABS(Total_Assets - Total_Liabilities_And_Equity) < 0.01", "Cash_Ending_BS == Cash_Beginning_BS + Net_Change_In_Cash_CFS",
                                "Retained_Earnings_Ending == Retained_Earnings_Prior + Net_Income - Common_Dividends",
                                "Net_PPE_BS == Net_PPE_Prior + Capex_CFS - Depreciation_IS",
                                "Net_Working_Capital_Delta_CFS == -1 * (Delta_Current_Assets_ex_Cash - Delta_Current_Liabilities_ex_ShortDebt)"]}
    if failures:
        # The mandate: a failed assertion withholds the statements; the trace is what prints.
        return {"skipped": "RECONCILIATION FAILED: " + "; ".join(f"{f['year']} #{f['id']} {f['name']} (diff {f['diff']:,.2f})" for f in failures[:6])
                           + ". Trace the discrepancy to sign conventions, unlinked cash balances or missing equity deductions.",
                "reconciliation": reconciliation, "fy_labels": None, "opening": opening, "assumptions": a}
    return {"fy_labels": labels[:len(IS.get("revenue", []))], "opening": opening, "assumptions": a, "income": IS, "schedules": SCH, "cashflow": CF, "balance": BS,
            "checks": checks, "notes": notes, "currency": None, "reconciliation": reconciliation,
            "flow": ["Income statement (revenue & EBIT)", "Supporting schedules (working capital, capex & D&A, debt)",
                     "Cash flow statement (net change in cash & free cash flow)", "Balance sheet (cash, PP&E, debt, retained earnings: A = L + E)"]}

"""Product-level revenue and a line-by-line cost build for drug developers (owner, 2026-10-07).

The forecast the DCF runs on is a company total (guidance / consensus for the near years, the archetype fade
after, each named drug eroded after its loss of exclusivity). This module decomposes that SAME total:

* revenue by product -- each drug in the owner-accepted franchise input (franchise_loe) is its base-year share of
  the un-eroded company path times its own survival on its LOE curve; partnership revenue (collaboration profit
  shares, royalties, alliance revenue) is its own tagged line; "other products and new launches" is the remainder,
  so the lines sum to the forecast exactly;
* costs line by line -- cost of revenue, R&D and SG&A / other operating expense at their three-year historical
  ratios to revenue, down to a built EBIT; the forecast's EBIT differs by the margin path it assumes, shown as its
  own named line ("operating leverage / margin path"), so the build ties to the EBIT the DCF uses.

Nothing here changes the valuation: it is the decomposition and the audit of the forecast, every figure tying to it.
"""
from __future__ import annotations

import re
from typing import Optional

_PARTNERSHIP_RE = re.compile(r"collaboration|profit share|share of (?:collaboration )?profits|royalt|alliance|partner|"
                             r"bayer|sanofi|novartis|roche|astellas|bms|bristol", re.I)
HISTORY_YEARS = 3


def revenue_type(drug: dict) -> str:
    txt = " ".join(str(drug.get(k) or "") for k in ("name", "loe_basis", "source"))
    return "partnership" if _PARTNERSHIP_RE.search(txt) else "product"


def _ratio(series: list[dict], num_key, years: int = HISTORY_YEARS) -> Optional[float]:
    vals = []
    for row in (series or [])[-years:]:
        rev = row.get("revenue")
        num = num_key(row) if callable(num_key) else row.get(num_key)
        if isinstance(rev, (int, float)) and rev > 0 and isinstance(num, (int, float)):
            vals.append(float(num) / float(rev))
    return (sum(vals) / len(vals)) if vals else None


def cost_ratios(series: list[dict]) -> dict:
    """Three-year average ratios to revenue: cost of revenue, R&D, SG&A and other operating expense."""
    def _sga(row):
        opx, rd = row.get("operating_expense"), row.get("research_and_development")
        if isinstance(opx, (int, float)) and isinstance(rd, (int, float)):
            return float(opx) - float(rd)
        return None
    out = {"cost_of_revenue": _ratio(series, "cost_of_revenue"),
           "research_and_development": _ratio(series, "research_and_development"),
           "sga_and_other": _ratio(series, _sga)}
    out["years"] = [str(r.get("period") or "")[:4] for r in (series or [])[-HISTORY_YEARS:]]
    return out


def build(rows: list[dict], series: list[dict], fy0: Optional[int], loe: Optional[dict] = None,
          entry: Optional[dict] = None) -> Optional[dict]:
    """The decomposition for the base forecast `rows` (each with year, revenue, ebit). `loe` is the overlay result
    (with drug_paths) and `entry` the franchise input; without them only the cost build is made."""
    if not rows:
        return None
    n = len(rows)
    years = [f"FY{fy0 + int(r.get('year') or i + 1)}E" if fy0 else f"Year {int(r.get('year') or i + 1)}" for i, r in enumerate(rows)]
    total = [float(r.get("revenue") or 0.0) for r in rows]
    out: dict = {"years": years, "revenue_total": total, "products": [], "other": None,
                 "basis": None, "costs": None}
    if loe and loe.get("drug_paths"):
        idx = list(loe.get("index") or [1.0] * n)[:n] + [1.0] * max(0, n - len(loe.get("index") or []))
        uneroded = [total[t] / (idx[t] or 1.0) for t in range(n)]
        lines = []
        for d in loe["drug_paths"]:
            path = list(d.get("path") or [])
            path = (path + [path[-1] if path else 0.0] * n)[:n]
            lines.append({"name": d.get("name"), "type": d.get("type") or "product", "loe_year": d.get("loe_year"),
                          "share_fy0": d.get("share"), "values": [uneroded[t] * float(path[t]) for t in range(n)]})
        # drugs on the input without a dated LOE (no practical generic route): their share of the un-eroded path
        named = {str(d.get("name")) for d in loe["drug_paths"]}
        tot_fy = (entry or {}).get("total_revenue")
        for d in (entry or {}).get("drugs") or []:
            if (isinstance(d, dict) and str(d.get("name")) not in named and isinstance(d.get("revenue_fy"), (int, float))
                    and isinstance(tot_fy, (int, float)) and tot_fy > 0):
                sh = float(d["revenue_fy"]) / float(tot_fy)
                lines.append({"name": d.get("name"), "type": revenue_type(d), "loe_year": None, "share_fy0": sh,
                              "values": [uneroded[t] * sh for t in range(n)], "note": "no dated LOE: held at its FY0 share"})
        other = [total[t] - sum(l["values"][t] for l in lines) for t in range(n)]
        out.update(products=lines, other=other,
                   basis=("each drug = its FY0 share of the un-eroded company path x its survival on its LOE curve; "
                          "'other products and new launches' = the forecast less the named drugs"))
    cr = cost_ratios(series)
    if all(isinstance(cr.get(k), (int, float)) for k in ("cost_of_revenue", "research_and_development", "sga_and_other")):
        cogs = [cr["cost_of_revenue"] * v for v in total]
        rd = [cr["research_and_development"] * v for v in total]
        sga = [cr["sga_and_other"] * v for v in total]
        ebit_build = [total[t] - cogs[t] - rd[t] - sga[t] for t in range(n)]
        ebit_fc = [float(r.get("ebit") or 0.0) for r in rows]
        out["costs"] = {"ratios": cr, "cost_of_revenue": cogs, "research_and_development": rd, "sga_and_other": sga,
                        "ebit_build": ebit_build, "margin_path": [ebit_fc[t] - ebit_build[t] for t in range(n)],
                        "ebit_forecast": ebit_fc}
    return out

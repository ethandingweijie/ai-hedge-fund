"""Three-statement financials, built deterministically from FMP line items.

Every analyst note in the archive presents income statement, balance sheet
and cash flow as three separate statements with a growth column beside each
level. The report payload carried none of that: `raw_financials` is a flat
mix of ~25 fields with revenue, total assets and free cash flow sitting
side by side.

Two design decisions worth stating, because both were forced by evidence:

**Growth is derived here, not fetched.** FMP serves
`income-statement-growth` and friends for the US, Singapore and Korea, but
returns *nothing* for Hong Kong — 00700, 01398, 00005, 00388 and 09988 were
all checked and all five came back empty. Deriving YoY from the five-year
series we already fetch is the only way HK gets a growth column at all, and
it keeps one definition of YoY across every market.

**Layout is profile-aware.** A bank's income statement has no cost of
revenue and no gross profit: the DBS and OCBC notes lead with net interest
income, non-interest income and total income. An S-REIT leads with gross
revenue, net property income and distributable income. Rendering
`revenue / COGS / gross profit` for either is the same category error as
pricing an S-REIT on P/FFO.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# A row is (key, label, emphasis, indent). `key` is either a line-item name
# or a derived pseudo-key resolved in _derive().
Row = tuple[str, str, bool, int]

_STANDARD_INCOME: list[Row] = [
    ("revenue",                    "Revenue",                       True,  0),
    ("cost_of_revenue",            "Cost of revenue",               False, 1),
    ("gross_profit",               "Gross profit",                  True,  0),
    ("research_and_development",   "Research & development",        False, 1),
    ("selling_general_admin",      "Selling, general & admin",      False, 1),
    ("operating_expense",          "Total operating expenses",      False, 1),
    ("operating_income",           "Operating income",              True,  0),
    ("interest_expense",           "Interest expense",              False, 1),
    ("other_income_expense",       "Other income / (expense)",      False, 1),
    ("pretax_income",              "Pre-tax income",                True,  0),
    ("income_tax_expense",         "Income tax expense",            False, 1),
    ("net_income",                 "Net income",                    True,  0),
    ("earnings_per_share",         "Diluted EPS",                   False, 0),
    ("ebitda",                     "EBITDA",                        False, 0),
]

# Banks: NII + non-II = total income. Gross `revenue` is the provider's
# convention (gross interest income) and is NOT what the street quotes, so
# it is deliberately absent from this layout.
_BANK_INCOME: list[Row] = [
    ("interest_income",            "Interest income",               False, 1),
    ("interest_expense",           "Interest expense",              False, 1),
    ("net_interest_income",        "Net interest income",           True,  0),
    ("_non_interest_income",       "Non-interest income",           False, 1),
    ("_total_income",              "Total income",                  True,  0),
    ("operating_expense",          "Operating expenses",            False, 1),
    ("_pre_provision_profit",      "Pre-provision operating profit", True, 0),
    ("provision_for_loan_losses",  "Allowances for credit losses",  False, 1),
    ("pretax_income",              "Pre-tax profit",                True,  0),
    ("income_tax_expense",         "Tax",                           False, 1),
    ("net_income",                 "Net profit",                    True,  0),
    ("earnings_per_share",         "Diluted EPS",                   False, 0),
]

# S-REITs: gross revenue → net property income → distributable income.
# NPI and DPU are not FMP line items; they arrive from the KPI extractor
# and are merged in when present rather than fabricated.
_REIT_INCOME: list[Row] = [
    ("revenue",                    "Gross revenue",                 True,  0),
    ("operating_expense",          "Property operating expenses",   False, 1),
    ("net_property_income",        "Net property income",           True,  0),
    ("interest_expense",           "Finance costs",                 False, 1),
    ("operating_income",           "Operating income",              False, 0),
    ("net_income",                 "Total return / net income",     True,  0),
    ("distributable_income",       "Distributable income",          True,  0),
    ("dpu",                        "DPU (cents)",                   False, 0),
]

_STANDARD_BALANCE: list[Row] = [
    ("cash_and_equivalents",       "Cash & equivalents",            False, 1),
    ("short_term_investments",     "Short-term investments",        False, 1),
    ("accounts_receivable",        "Accounts receivable",           False, 1),
    ("inventory",                  "Inventory",                     False, 1),
    ("current_assets",             "Total current assets",          True,  0),
    ("property_plant_equipment",   "Property, plant & equipment",   False, 1),
    ("goodwill",                   "Goodwill",                      False, 1),
    ("intangible_assets",          "Intangible assets",             False, 1),
    ("non_current_assets",         "Total non-current assets",      False, 0),
    ("total_assets",               "Total assets",                  True,  0),
    ("accounts_payable",           "Accounts payable",              False, 1),
    ("short_term_debt",            "Short-term debt",               False, 1),
    ("current_liabilities",        "Total current liabilities",     True,  0),
    ("long_term_debt",             "Long-term debt",                False, 1),
    ("non_current_liabilities",    "Total non-current liabilities", False, 0),
    ("total_liabilities",          "Total liabilities",             True,  0),
    ("retained_earnings",          "Retained earnings",             False, 1),
    ("minority_interest",          "Minority interest",             False, 1),
    ("shareholders_equity",        "Shareholders' equity",          True,  0),
    ("total_debt",                 "Total debt",                    False, 0),
    ("net_debt",                   "Net debt",                      False, 0),
    ("book_value_per_share",       "Book value per share",          False, 0),
]

# A bank's balance sheet is a loan book funded by deposits; inventory and
# PP&E are noise on it.
_BANK_BALANCE: list[Row] = [
    ("cash_and_equivalents",       "Cash & balances with banks",    False, 1),
    ("loans_receivable",           "Net loans & advances",          True,  0),
    ("loans_held_for_investment",  "Loans held for investment",     False, 1),
    ("total_assets",               "Total assets",                  True,  0),
    ("total_deposits",             "Customer deposits",             True,  0),
    ("short_term_deposits",        "Short-term deposits",           False, 1),
    ("total_debt",                 "Total borrowings",              False, 1),
    ("total_liabilities",          "Total liabilities",             True,  0),
    ("retained_earnings",          "Retained earnings",             False, 1),
    ("minority_interest",          "Minority interest",             False, 1),
    ("shareholders_equity",        "Shareholders' equity",          True,  0),
    ("book_value_per_share",       "Book value per share",          False, 0),
]

_REIT_BALANCE: list[Row] = [
    ("cash_and_equivalents",       "Cash & equivalents",            False, 1),
    ("property_plant_equipment",   "Investment properties",         True,  0),
    ("total_assets",               "Total assets",                  True,  0),
    ("short_term_debt",            "Short-term borrowings",         False, 1),
    ("long_term_debt",             "Long-term borrowings",          False, 1),
    ("total_debt",                 "Total borrowings",              True,  0),
    ("total_liabilities",          "Total liabilities",             True,  0),
    ("minority_interest",          "Minority interest",             False, 1),
    ("shareholders_equity",        "Net assets attributable",       True,  0),
    ("book_value_per_share",       "NAV per unit",                  False, 0),
]

_STANDARD_CASHFLOW: list[Row] = [
    ("net_income",                    "Net income",                 False, 1),
    ("depreciation_and_amortization", "Depreciation & amortisation", False, 1),
    ("stock_based_compensation",      "Share-based compensation",   False, 1),
    ("change_in_working_capital",     "Change in working capital",  False, 1),
    ("operating_cash_flow",           "Cash flow from operations",  True,  0),
    ("capital_expenditure",           "Capital expenditure",        False, 1),
    ("acquisitions_net",              "Acquisitions, net",          False, 1),
    ("investing_cash_flow",           "Cash flow from investing",   True,  0),
    ("net_debt_issuance",             "Net debt issued / (repaid)", False, 1),
    ("share_buyback",                 "Share buybacks",             False, 1),
    ("dividends_and_distributions",   "Dividends paid",             False, 1),
    ("financing_cash_flow",           "Cash flow from financing",   True,  0),
    ("net_change_in_cash",            "Net change in cash",         True,  0),
    ("cash_at_end_of_period",         "Cash at end of period",      False, 0),
    ("free_cash_flow",                "Free cash flow",             True,  0),
]

_LAYOUTS: dict[str, dict[str, list[Row]]] = {
    "standard": {
        "income":   _STANDARD_INCOME,
        "balance":  _STANDARD_BALANCE,
        "cashflow": _STANDARD_CASHFLOW,
    },
    "bank": {
        "income":   _BANK_INCOME,
        "balance":  _BANK_BALANCE,
        "cashflow": _STANDARD_CASHFLOW,
    },
    "reit": {
        "income":   _REIT_INCOME,
        "balance":  _REIT_BALANCE,
        "cashflow": _STANDARD_CASHFLOW,
    },
}

_TITLES = {
    "income":   "Income Statement",
    "balance":  "Balance Sheet",
    "cashflow": "Cash Flow Statement",
}

# Every real line item any layout can render. data_router adds these to the
# single search_line_items call it already makes, so presenting three
# statements costs no extra round trip. Derived pseudo-keys (leading "_")
# and extractor-sourced REIT lines are excluded — they are not FMP fields.
_EXTRACTOR_SOURCED = {"net_property_income", "distributable_income", "dpu"}

STATEMENT_LINE_ITEMS: list[str] = sorted({
    key
    for layout in _LAYOUTS.values()
    for rows in layout.values()
    for key, _label, _emph, _indent in rows
    if not key.startswith("_") and key not in _EXTRACTOR_SOURCED
})

# Rows that are ratios or per-share figures, where a percentage change of a
# percentage is meaningless to show as "growth".
_NO_GROWTH = {"book_value_per_share", "gross_margin"}


def resolve_layout(sector: str = "", profile: str = "") -> str:
    """Pick the statement layout family for a sector / valuation profile.

    Reuses the same loose-matching helpers that route the KPI framework, so
    a sector stored as "Banking" or "Financials" lands identically.
    """
    blob = f"{sector} {profile}".strip()
    if not blob:
        return "standard"
    try:
        from src.agents.industry.sector_prompts import (
            is_bank_sector, is_reit_sector,
        )
        if is_bank_sector(blob):
            return "bank"
        if is_reit_sector(blob):
            return "reit"
    except Exception:
        pass
    low = blob.lower()
    if "bank" in low:
        return "bank"
    if "reit" in low:
        return "reit"
    return "standard"


def _derive(key: str, row: dict) -> Optional[float]:
    """Resolve a derived pseudo-key (leading underscore) from a flat row."""
    if key == "_total_income":
        try:
            from src.agents.analysis.dcf_agent import _bank_total_income
            return _bank_total_income(row)
        except Exception:
            return None
    if key == "_non_interest_income":
        total = _derive("_total_income", row)
        nii = row.get("net_interest_income")
        if total is None or nii is None:
            return None
        return total - nii
    if key == "_pre_provision_profit":
        total = _derive("_total_income", row)
        opex = row.get("operating_expense")
        if total is None or opex is None:
            return None
        return total - abs(opex)
    return None


def _growth(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """YoY change, or None where the number would mislead.

    A sign flip (loss to profit) has no meaningful percentage, and a
    near-zero base makes the ratio explode; both return None rather than a
    figure a reader would take at face value.
    """
    if curr is None or prev is None:
        return None
    try:
        curr, prev = float(curr), float(prev)
    except (TypeError, ValueError):
        return None
    if prev == 0 or (curr < 0) != (prev < 0):
        return None
    if abs(prev) < 1e-9:
        return None
    return (curr - prev) / abs(prev)


def _blank_unreported_zeros(
    values: dict[str, Optional[float]], periods: list[str]
) -> None:
    """Treat an isolated hard zero on a balance-sheet line as unreported.

    A stock item that was material last year and is exactly 0.0 this year is
    a reporting gap, not a real balance. FMP returns
    `retainedEarnings = 0` for DBS FY2025 while equity is S$68,867mn — which
    rendered as "S$0.00  −100%" and read as though the bank had wiped out its
    reserves. Mutates `values` in place; the row is dropped upstream if that
    leaves nothing.
    """
    material = [
        abs(v) for v in values.values()
        if isinstance(v, (int, float)) and v != 0
    ]
    if not material:
        return
    # "Material" relative to the line's own scale, so a genuinely small
    # balance (minority interest at S$47mn) is never blanked.
    threshold = max(material) * 0.01
    for period in periods:
        v = values.get(period)
        if isinstance(v, (int, float)) and v == 0 and threshold > 0:
            values[period] = None


def _fy_key(period: str) -> Optional[str]:
    m = re.search(r"(\d{4})", str(period or ""))
    return f"FY{m.group(1)}" if m else None


def build_financial_statements(
    rows_by_fy: dict[str, dict],
    sector: str = "",
    profile: str = "",
    currency: str = "",
    extra: Optional[dict[str, dict]] = None,
) -> dict[str, Any]:
    """Group flat per-FY rows into three statements with derived growth.

    `rows_by_fy` is the existing `raw_financials` shape: {"FY2024": {...}}.
    `extra` optionally supplies non-FMP values per FY (REIT net property
    income, distributable income, DPU) sourced from the KPI extractor —
    merged when present, never fabricated.

    The flat input is not mutated and not replaced; this is an additive view.
    """
    periods = sorted(k for k in (rows_by_fy or {}) if str(k).startswith("FY"))
    layout_name = resolve_layout(sector, profile)
    layout = _LAYOUTS[layout_name]

    merged: dict[str, dict] = {}
    for fy in periods:
        row = dict(rows_by_fy.get(fy) or {})
        if extra and fy in extra:
            for k, v in (extra[fy] or {}).items():
                row.setdefault(k, v)
        merged[fy] = row

    statements: dict[str, Any] = {}
    for stmt, rows in layout.items():
        out_rows = []
        for key, label, emphasis, indent in rows:
            values: dict[str, Optional[float]] = {}
            for fy in periods:
                row = merged[fy]
                val = _derive(key, row) if key.startswith("_") else row.get(key)
                values[fy] = val if isinstance(val, (int, float)) else None
            if stmt == "balance":
                _blank_unreported_zeros(values, periods)
            if all(v is None for v in values.values()):
                continue  # never render a row the data cannot fill
            growth: dict[str, Optional[float]] = {}
            if key not in _NO_GROWTH:
                for i, fy in enumerate(periods):
                    growth[fy] = (
                        None if i == 0
                        else _growth(values[fy], values[periods[i - 1]])
                    )
            out_rows.append({
                "key":      key.lstrip("_"),
                "label":    label,
                "values":   values,
                "growth":   growth,
                "emphasis": emphasis,
                "indent":   indent,
            })
        if out_rows:
            statements[stmt] = {"title": _TITLES[stmt], "rows": out_rows}

    return {
        "layout":     layout_name,
        "currency":   currency or "",
        "periods":    periods,
        "statements": statements,
    }


# ── Forecast columns (owner, 2026-10-04) ─────────────────────────────────────
# The valuation agent's three-statement forecast (FY+1E..FY+5E) printed on the SAME rows as
# the reported years, for the web and mobile Financials tab. One mapping, shared with the
# PDF's statements page, so every surface shows the same figure on the same line.

def forecast_values(th: dict) -> dict:
    """The three-statement model's series keyed like the reported rows, in the reported sign
    conventions (expenses positive on the income statement; outflows negative on the cash
    flow), per forecast year."""
    IS, BS, CF = th.get("income") or {}, th.get("balance") or {}, th.get("cashflow") or {}
    op = th.get("opening") or {}
    n = len(th.get("fy_labels") or [])

    def ser(d, k, sign=1.0):
        v = d.get(k)
        return [((sign * x) if isinstance(x, (int, float)) else None) for x in v][:n] if v else None

    def add(a, b):
        return [((x or 0.0) + (y or 0.0)) if (x is not None or y is not None) else None
                for x, y in zip(a or [None] * n, b or [None] * n)]

    def sub(a, b):
        return [((x or 0.0) - (y or 0.0)) if (x is not None or y is not None) else None
                for x, y in zip(a or [None] * n, b or [None] * n)]

    def div(a, b):
        return [((x / y) if (x is not None and y) else None) for x, y in zip(a or [None] * n, b or [None] * n)]

    held = lambda key: [op.get(key)] * n if isinstance(op.get(key), (int, float)) else None     # noqa: E731
    return {
        "income": {
            "revenue": ser(IS, "revenue"), "cost_of_revenue": ser(IS, "cogs", -1.0), "gross_profit": ser(IS, "gross_profit"),
            "operating_expense": sub([0.0] * n, add(ser(IS, "opex_ex_da"), ser(IS, "da"))), "operating_income": ser(IS, "ebit"),
            "interest_expense": ser(IS, "interest_expense", -1.0), "other_income_expense": ser(IS, "interest_income"),
            "pretax_income": ser(IS, "pretax"), "income_tax_expense": ser(IS, "tax", -1.0), "net_income": ser(IS, "net_income"),
            "earnings_per_share": ser(IS, "eps"), "ebitda": ser(IS, "ebitda"),
        },
        "balance": {
            "cash_and_equivalents": ser(BS, "cash"), "short_term_investments": ser(BS, "sti"),
            "accounts_receivable": ser(BS, "receivables"), "inventory": ser(BS, "inventory"),
            "current_assets": ser(BS, "current_assets"), "property_plant_equipment": ser(BS, "ppe"),
            "goodwill": held("goodwill"), "intangible_assets": held("intangibles"),
            "non_current_assets": sub(ser(BS, "total_assets"), ser(BS, "current_assets")), "total_assets": ser(BS, "total_assets"),
            "accounts_payable": ser(BS, "payables"), "short_term_debt": ser(BS, "short_term_debt"),
            "current_liabilities": ser(BS, "current_liabilities"), "long_term_debt": ser(BS, "long_term_debt"),
            "non_current_liabilities": sub(ser(BS, "total_liabilities"), ser(BS, "current_liabilities")),
            "total_liabilities": ser(BS, "total_liabilities"), "retained_earnings": ser(BS, "retained_earnings"),
            "minority_interest": ser(BS, "minority_interest"), "shareholders_equity": ser(BS, "equity"),
            "total_debt": add(ser(BS, "short_term_debt"), ser(BS, "long_term_debt")), "net_debt": ser(BS, "net_debt"),
            "book_value_per_share": div(ser(BS, "equity"), ser(IS, "shares")),
        },
        "cashflow": {
            "net_income": ser(CF, "net_income"), "depreciation_and_amortization": ser(CF, "da"),
            "stock_based_compensation": ser(CF, "sbc"), "change_in_working_capital": ser(CF, "change_nwc"),
            "operating_cash_flow": ser(CF, "cfo"), "capital_expenditure": ser(CF, "capex"),
            "acquisitions_net": ser(CF, "acquisitions"), "investing_cash_flow": ser(CF, "cfi"),
            "net_debt_issuance": ser(CF, "debt_change"), "share_buyback": ser(CF, "buybacks"),
            "dividends_and_distributions": ser(CF, "dividends"), "financing_cash_flow": ser(CF, "cff"),
            "net_change_in_cash": ser(CF, "net_change_cash"), "cash_at_end_of_period": ser(CF, "closing_cash"),
            "free_cash_flow": ser(CF, "fcf"),
        },
    }


def forecast_labels(th: Optional[dict]) -> list[str]:
    """The forecast years an operating company's statements can carry; [] for a withheld
    model, a bank or insurer (their earnings-and-capital model has its own lines) or none."""
    th = th or {}
    if th.get("fy_labels") and not th.get("skipped") and th.get("kind") in (None, "operating") and th.get("income"):
        return [str(x) for x in th["fy_labels"]]
    return []


def attach_forecast(fs: Optional[dict], th: Optional[dict], override: Optional[dict] = None) -> Optional[dict]:
    """A copy of the statements payload with the forecast years on the reported rows.

    `periods` stays the reported years (the PDF and the workbook read it as such);
    `forecast_periods` lists the appended labels, each row's `values` and `growth` gain those
    keys, and `forecast` says what the columns are, whether the reconciliation suite passed,
    or why they are absent. Idempotent: an earlier forecast on the payload is stripped first,
    so a recompute (a user override applied on read) replaces rather than stacks."""
    if not isinstance(fs, dict) or not fs.get("statements"):
        return fs
    import copy
    out = copy.deepcopy(fs)
    stale = [str(x) for x in (out.pop("forecast_periods", None) or [])]
    out.pop("forecast", None)
    for st in (out.get("statements") or {}).values():
        for r in (st or {}).get("rows") or []:
            for lab in stale:
                (r.get("values") or {}).pop(lab, None)
                (r.get("growth") or {}).pop(lab, None)

    th = th if isinstance(th, dict) else {}
    labels = forecast_labels(th)
    periods = [str(x) for x in (out.get("periods") or [])]
    if not labels:
        if th.get("skipped"):
            meta = {"status": "withheld", "reason": str(th["skipped"])[:300]}
        elif th.get("kind") in ("bank", "insurer"):
            meta = {"status": "not_applicable",
                    "reason": "The earnings-and-capital model carries this family's forecast; the reported layout does not."}
        else:
            meta = {"status": "none"}
        out["forecast_periods"] = []
        out["forecast"] = meta
        return out

    fv = forecast_values(th)
    filled = 0
    for sec, keyed in fv.items():
        for r in ((out.get("statements") or {}).get(sec) or {}).get("rows") or []:
            series = keyed.get(r.get("key"))
            if not series:
                continue
            vals = r.setdefault("values", {})
            growth = r.setdefault("growth", {})
            prev = periods[-1] if periods else None
            for lab, x in zip(labels, series):
                vals[lab] = x
                if r.get("key") not in _NO_GROWTH:
                    growth[lab] = _growth(x, vals.get(prev)) if prev else None
                prev = lab
            filled += 1

    rc = th.get("reconciliation") or {}
    by_id: dict = {}
    for a in rc.get("assertions") or []:
        if isinstance(a, dict) and a.get("id") is not None:
            slot = by_id.setdefault(a["id"], {"id": a["id"], "name": a.get("name"), "ok": True})
            slot["ok"] = bool(slot["ok"] and a.get("ok"))
    meta = {
        "status": "ok",
        "source": "the valuation agent's three-statement forecast on its guidance-derived estimates",
        "rows_filled": filled,
        "reconciliation": {"ok": rc.get("ok"), "assertions": [by_id[k] for k in sorted(by_id)]},
    }
    ovr = th.get("override") or override
    if isinstance(ovr, dict) and ovr:
        meta["override"] = {"created_at": str(ovr.get("created_at") or "")[:10], "fields": list(ovr.get("fields") or [])}
    out["forecast_periods"] = labels
    out["forecast"] = meta
    return out

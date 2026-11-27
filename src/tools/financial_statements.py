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


def normalise_period_keys(rows_by_fy: Optional[dict]) -> dict[str, dict]:
    """Collapse period-key variants of one fiscal year into a single "FYyyyy" row.

    raw_financials can carry the same year twice -- "FY2022" from the FMP
    overlay and "FY2022 (Ending 2022-06-30)" from the research echo -- and
    the statements rendered both as separate columns (SGX, S68.SI). The bare
    "FYyyyy" key wins field by field; a variant only fills what it lacks.
    Keys with no four-digit year are dropped (they are not periods).
    """
    out: dict[str, dict] = {}
    if not isinstance(rows_by_fy, dict):
        return out
    # canonical keys first, so their values take precedence over the variants
    keys = sorted(rows_by_fy, key=lambda k: (not re.fullmatch(r"FY\d{4}", str(k)), str(k)))
    for k in keys:
        row = rows_by_fy.get(k)
        fy = _fy_key(k)
        if not fy or not isinstance(row, dict):
            continue
        dst = out.setdefault(fy, {})
        for f, v in row.items():
            if dst.get(f) is None and v is not None:
                dst[f] = v
    return out


# -- Comparability (owner request 2026-11-27, Singtel FY2025) -----------------
# FMP swapped Singtel's FY2025 cost of sales and operating expenses: COGS read
# S$4,188m against ~S$11,000m in every other year, so gross profit equalled
# revenue (+317% then -80%) and three surfaces printed three different
# operating incomes. A year whose cost CLASSIFICATION breaks from its
# neighbours while revenue does not is not comparable: its cost lines are
# withheld, no growth is computed into or out of it, and operating income is
# the reported line, never rebuilt from the swapped costs.

#: Lines whose value depends on how costs were classified in the year.
CLASSIFICATION_ROWS = ("cost_of_revenue", "gross_profit", "operating_expense", "gross_margin")
#: Rows whose YoY growth is meaningless across a non-comparable year.
_CLASSIFICATION_GROWTH = {"cost_of_revenue", "gross_profit", "operating_expense", "operating_income",
                          "research_and_development", "selling_general_admin", "ebitda"}


def _f(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def noncomparable_flags(rows: list[dict]) -> list[Optional[str]]:
    """Per row (oldest first), the reason the year's cost classification is not
    comparable with its neighbours, or None.

    A year is flagged when, with revenue within 25% of an adjacent year's:
      * gross margin is ~100% (>= 99.5%) while an adjacent year's is below 90%, or
      * COGS / revenue moves more than 50% (relative) from the median of the other years.
    A company that never reports COGS (gross margin 100% every year) is not
    flagged: the test is a break from its own history, not a level.
    """
    n = len(rows)
    rev = [_f((r or {}).get("revenue")) for r in rows]
    cogs = [_f((r or {}).get("cost_of_revenue")) for r in rows]
    gp = [_f((r or {}).get("gross_profit")) for r in rows]

    def gm(i):
        if not rev[i] or rev[i] <= 0:
            return None
        if gp[i] is not None:
            return gp[i] / rev[i]
        if cogs[i] is not None:
            return 1.0 - abs(cogs[i]) / rev[i]
        return None

    def cr(i):
        return (abs(cogs[i]) / rev[i]) if (cogs[i] is not None and rev[i] and rev[i] > 0) else None

    out: list[Optional[str]] = [None] * n
    for i in range(n):
        if not rev[i] or rev[i] <= 0:
            continue
        nbrs = [j for j in (i - 1, i + 1)
                if 0 <= j < n and rev[j] and rev[j] > 0 and abs(rev[i] / rev[j] - 1.0) <= 0.25]
        if not nbrs:
            continue
        g = gm(i)
        low = [gm(j) for j in nbrs if gm(j) is not None and gm(j) < 0.90]
        if g is not None and g >= 0.995 and low:
            out[i] = (f"gross margin {g:.0%} against {min(low):.0%} in the adjacent year: cost of "
                      f"revenue and operating expenses are not classified comparably (provider data)")
            continue
        c = cr(i)
        # Baseline: the median COGS ratio of every OTHER year, so one broken year
        # does not drag its clean neighbour in with it (FY2026 beside Singtel's FY2025).
        others = sorted(x for j in range(n) if j != i for x in [cr(j)] if x)
        if c is None or len(others) < 2:
            continue
        mid = len(others) // 2
        base = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2.0
        if base > 0 and abs(c / base - 1.0) > 0.5:
            out[i] = (f"cost of revenue {c:.0%} of revenue against a {base:.0%} median in the other "
                      f"years while revenue moved <25%: cost lines are not comparable (provider data)")
    return out


def noncomparable_years(rows_by_fy: dict[str, dict]) -> dict[str, str]:
    """{fiscal-year key: reason} for the years noncomparable_flags() rejects."""
    periods = sorted(k for k in (rows_by_fy or {}) if str(k).startswith("FY"))
    flags = noncomparable_flags([rows_by_fy[k] or {} for k in periods])
    return {k: why for k, why in zip(periods, flags) if why}


def derive_per_share(key: str, row: dict) -> Optional[float]:
    """EPS / BVPS from the row's own totals, for a per-share line that is missing or a hard zero.

    FMP zero-fills per-share lines for some SGX filers (UOB: EPS and BVPS 0.0 every year while
    net income and equity were S$4-6bn and S$43-49bn); the export derived them, the web printed
    S$0.00. One derivation for every surface.
    """
    sh = _f(row.get("shares_outstanding"))
    if not sh or sh <= 0:
        return None
    if key == "earnings_per_share":
        ni = _f(row.get("net_income"))
        return (ni / sh) if ni is not None else None
    if key == "book_value_per_share":
        eq = _f(row.get("shareholders_equity"))
        if eq is None:
            eq = _f(row.get("total_equity"))
        return (eq / sh) if (eq is not None and eq > 0) else None
    return None


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
    rows_by_fy = normalise_period_keys(rows_by_fy)
    extra = normalise_period_keys(extra) if extra else extra
    periods = sorted(k for k in rows_by_fy if str(k).startswith("FY"))
    layout_name = resolve_layout(sector, profile)
    layout = _LAYOUTS[layout_name]

    merged: dict[str, dict] = {}
    for fy in periods:
        row = dict(rows_by_fy.get(fy) or {})
        if extra and fy in extra:
            for k, v in (extra[fy] or {}).items():
                row.setdefault(k, v)
        for ps_key in ("earnings_per_share", "book_value_per_share"):
            if not _f(row.get(ps_key)):
                d = derive_per_share(ps_key, row)
                if d is not None:
                    row[ps_key] = d
        merged[fy] = row

    # A bank's or REIT's income statement has no COGS / gross profit split to test.
    not_comparable = noncomparable_years(merged) if layout_name == "standard" else {}
    for fy in not_comparable:
        for k in CLASSIFICATION_ROWS:
            merged[fy][k] = None

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
                _across = key.lstrip("_") in _CLASSIFICATION_GROWTH
                for i, fy in enumerate(periods):
                    growth[fy] = (
                        None if i == 0
                        or (_across and (fy in not_comparable or periods[i - 1] in not_comparable))
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
        # {FY: reason}: years whose cost lines are withheld and across which no
        # cost-line growth is computed. Operating income stays the reported line.
        "not_comparable": not_comparable,
    }


def repair_statements(fs: Optional[dict]) -> Optional[dict]:
    """A stored statements payload with the 2026-11-27 normalisation applied on read.

    Runs archived before the fix replay forever, so the read path repairs them the same way
    build_financial_statements now builds: one column per fiscal year (S68.SI's "FY2022" and
    "FY2022 (Ending 2022-06-30)" merged, the bare key winning), and on a standard layout a year
    whose cost classification breaks from its history has its cost lines withheld and no
    cost-line growth across it. Values are never invented. Returns a copy; idempotent.
    """
    if not isinstance(fs, dict) or not fs.get("statements"):
        return fs
    import copy
    out = copy.deepcopy(fs)
    forecast = [str(x) for x in (out.get("forecast_periods") or [])]
    old_periods = [str(p) for p in (out.get("periods") or [])]
    periods = sorted({_fy_key(p) or p for p in old_periods})
    # canonical first so its value wins
    order = sorted(old_periods, key=lambda k: (not re.fullmatch(r"FY\d{4}", k), k))
    all_rows = [r for st in out["statements"].values() for r in (st or {}).get("rows") or []]
    for r in all_rows:
        vals = r.get("values") or {}
        merged: dict = {}
        for p in order:
            fy = _fy_key(p) or p
            v = vals.get(p)
            if merged.get(fy) is None and isinstance(v, (int, float)):
                merged[fy] = v
            merged.setdefault(fy, None)
        for lab in forecast:
            if lab in vals:
                merged[lab] = vals[lab]
        r["values"] = merged
    nc: dict = dict(out.get("not_comparable") or {})
    if out.get("layout", "standard") == "standard":
        by_fy = {fy: {r.get("key"): (r.get("values") or {}).get(fy) for r in all_rows} for fy in periods}
        nc.update(noncomparable_years(by_fy))
        for r in all_rows:
            if r.get("key") in CLASSIFICATION_ROWS:
                for fy in nc:
                    if fy in r["values"]:
                        r["values"][fy] = None
    for r in all_rows:
        key = r.get("key")
        if key in _NO_GROWTH:
            continue
        across = key in _CLASSIFICATION_GROWTH
        cols = periods + [lab for lab in forecast if lab in r["values"]]
        g = {}
        for i, p in enumerate(cols):
            prev = cols[i - 1] if i else None
            g[p] = (None if prev is None or (across and (p in nc or prev in nc))
                    else _growth(r["values"].get(p), r["values"].get(prev)))
        r["growth"] = g
    for st in out["statements"].values():
        if isinstance(st, dict):
            st["rows"] = [r for r in st.get("rows") or [] if any(v is not None for v in (r.get("values") or {}).values())]
    out["periods"] = periods
    out["not_comparable"] = nc
    return out


# ── Forecast columns (owner, 2026-10-04) ─────────────────────────────────────
# The valuation agent's three-statement forecast (FY+1E..FY+5E) printed on the SAME rows as
# the reported years, for the web and mobile Financials tab. One mapping, shared with the
# PDF's statements page, so every surface shows the same figure on the same line.

def forecast_values(th: dict) -> dict:
    """The three-statement model's series keyed like the reported rows, in the reported sign
    conventions (expenses positive on the income statement; outflows negative on the cash
    flow), per forecast year."""
    if th.get("kind") in _CAPITAL_MODEL_KINDS:
        return _capital_model_values(th)
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


_CAPITAL_MODEL_KINDS = ("bank", "insurer")


def _capital_model_values(th: dict) -> dict:
    """A bank or insurer's earnings-and-capital model on the reported rows it shares with them (owner, 2026-11-24:
    the page printed no forward years for these families). Only lines the model computes are filled; the rest of
    the reported layout (deposits, reserves, working capital) stays reported-only."""
    R = th.get("rows") or {}
    n = len(th.get("fy_labels") or [])

    def ser(k, sign=1.0):
        v = R.get(k)
        return [((sign * x) if isinstance(x, (int, float)) else None) for x in v][:n] if v else None

    if th.get("kind") == "insurer" and not R.get("total_income"):          # a model stored before total_income was a row
        prem, inv = ser("net_earned_premiums"), ser("investment_income")
        revenue = [((p or 0.0) + (i or 0.0)) if (p is not None or i is not None) else None
                   for p, i in zip(prem or [None] * n, inv or [None] * n)] if (prem or inv) else None
    else:
        revenue = ser("total_income")
    op = th.get("opening") or {}
    gw = op.get("goodwill_intangibles")
    return {
        "income": {"revenue": revenue, "total_income": revenue, "pretax_income": ser("pretax"), "income_tax_expense": ser("tax", -1.0),
                   "operating_expense": ser("operating_expenses", -1.0), "pre_provision_profit": ser("pre_provision_profit"),
                   "net_income": ser("net_income"), "earnings_per_share": ser("eps")},
        "balance": {"total_assets": ser("total_assets"), "shareholders_equity": ser("equity"), "book_value_per_share": ser("bvps"),
                    "total_liabilities": ([(a - e) if (a is not None and e is not None) else None
                                           for a, e in zip(ser("total_assets") or [None] * n, ser("equity") or [None] * n)]
                                          if (R.get("total_assets") and R.get("equity")) else None)},
        "cashflow": {"net_income": ser("net_income"), "dividends_and_distributions": ser("dividends"), "share_buyback": ser("buybacks")},
    } if n else {"income": {}, "balance": {}, "cashflow": {}}


def forecast_labels(th: Optional[dict]) -> list[str]:
    """The forecast years the reported statements can carry: an operating company's three-statement model, or a
    bank or insurer's earnings-and-capital model on the rows it shares (owner, 2026-11-24); [] when withheld or none."""
    th = th or {}
    if th.get("fy_labels") and not th.get("skipped") and th.get("kind") in (None, "operating") and th.get("income"):
        return [str(x) for x in th["fy_labels"]]
    if th.get("fy_labels") and not th.get("skipped") and th.get("kind") in _CAPITAL_MODEL_KINDS and th.get("rows"):
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
        "source": (f"the valuation agent's {th.get('kind')} earnings-and-capital model" if th.get("kind") in _CAPITAL_MODEL_KINDS
                   else "the valuation agent's three-statement forecast on its guidance-derived estimates"),
        "rows_filled": filled,
        "reconciliation": {"ok": rc.get("ok"), "assertions": [by_id[k] for k in sorted(by_id)]},
    }
    ovr = th.get("override") or override
    if isinstance(ovr, dict) and ovr:
        meta["override"] = {"created_at": str(ovr.get("created_at") or "")[:10], "fields": list(ovr.get("fields") or [])}
    out["forecast_periods"] = labels
    out["forecast"] = meta
    return out

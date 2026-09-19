"""Report exports for a saved run: the Excel valuation workbook and the PDF report.

Wires the pure workbook builder (src/utils/valuation_workbook.py) to its two
live data sources: the reported statements (FMP, via the same
`search_line_items` path the engine uses) and the named comps basket members
(`regional_comps.load_members`). Access control is the caller's: the route
only builds a workbook for a run `get_run_result` already returned to this
user.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

#: Statement lines for the IS / BS / CFS tabs (search_line_items names).
STATEMENT_FIELDS = [
    # income statement
    "revenue", "cost_of_revenue", "gross_profit", "selling_general_admin",
    "research_and_development", "operating_expense", "operating_income",
    "depreciation_and_amortization", "ebitda", "interest_expense",
    "other_income_expense", "pretax_income", "income_tax_expense", "net_income",
    "shares_outstanding", "earnings_per_share",
    # balance sheet
    "cash_and_equivalents", "short_term_investments", "accounts_receivable",
    "inventory", "current_assets", "property_plant_equipment",
    "goodwill_plus_intangibles", "total_assets", "accounts_payable",
    "short_term_debt", "current_liabilities", "long_term_debt",
    "total_liabilities", "shareholders_equity", "minority_interest",
    # cash flow
    "operating_cash_flow", "stock_based_compensation", "change_in_working_capital",
    "capital_expenditure", "acquisitions_net", "investing_cash_flow",
    "dividends_and_distributions", "share_buyback", "net_debt_issuance",
    "financing_cash_flow", "free_cash_flow",
]


def load_statements(ticker: str, end_date: Optional[str]) -> dict:
    """Up to five annual periods of reported statements, oldest first."""
    from src.tools.api import search_line_items
    items = search_line_items(ticker, STATEMENT_FIELDS,
                              end_date or date.today().isoformat(),
                              period="annual", limit=5) or []
    rows = []
    for li in items:
        d = li.model_dump() if hasattr(li, "model_dump") else dict(li)
        row = {"period": d.get("report_period")}
        for f in STATEMENT_FIELDS:
            row[f] = d.get(f)
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("period") or ""))
    currency = next((getattr(li, "currency", None) for li in items
                     if getattr(li, "currency", None)), None)
    return {"rows": rows, "currency": currency,
            "estimates": _consensus(ticker, end_date), "beta": _beta(ticker)}


def _consensus(ticker: str, end_date: Optional[str]) -> list[dict]:
    """FMP forward consensus (the same call the engine's forward legs use)."""
    try:
        from src.tools.api import get_analyst_estimates
        est = get_analyst_estimates(ticker, end_date or date.today().isoformat(),
                                    period="annual", limit=5) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in est:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        out.append({k: d.get(k) for k in ("period_end", "revenue_avg", "ebitda_avg", "ebit_avg",
                                          "net_income_avg", "eps_avg", "revenue_low",
                                          "revenue_high", "analyst_count_revenue",
                                          "analyst_count_eps")})
    return sorted(out, key=lambda r: str(r.get("period_end") or ""))


def _beta(ticker: str) -> Optional[float]:
    """Company beta from the FMP profile (CAPM cross-check only)."""
    try:
        import os
        from src.tools.api import _fmp_get
        from src.tools.fmp_transcripts import to_fmp_symbol
        rows = _fmp_get("https://financialmodelingprep.com/stable/profile",
                        {"symbol": to_fmp_symbol(ticker)}, os.environ.get("FMP_API_KEY"))
        if isinstance(rows, list) and rows:
            b = rows[0].get("beta")
            return float(b) if b is not None else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _members(exchange, level, key, cohort="all"):
    from src.data.regional_comps import load_members
    return load_members(exchange, level, key, cohort)


def safe_filename(ticker: str, run_at: Optional[str]) -> str:
    day = (run_at or "")[:10] or date.today().isoformat()
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{ticker}_valuation_{day}.xlsx")


def build_run_pdf(run: dict, ticker: Optional[str] = None) -> tuple[bytes, str]:
    """(pdf bytes, filename) for a run payload: the same report the CLI writes.

    Raises KeyError when the run has no result for `ticker`.
    """
    import os
    import tempfile
    from src.utils.pdf_report import generate_pdf_report
    data = run.get("data") or {}
    decisions = data.get("decisions") or {}
    t = ticker or data.get("primary_ticker") or next(iter(decisions), None)         or next(iter(data.get("dcf_range") or {}), None)
    if not t or (decisions and t not in decisions):
        raise KeyError(t or "")
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        generate_pdf_report(data, path, open_after=False)
        with open(path, "rb") as fh:
            blob = fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    day = (run.get("run_at") or data.get("end_date") or "")[:10] or date.today().isoformat()
    return blob, re.sub(r"[^A-Za-z0-9._-]", "_", f"{t}_report_{day}.pdf")


def build_run_workbook(run: dict, ticker: Optional[str] = None) -> tuple[bytes, str]:
    """(xlsx bytes, filename) for one ticker of a run payload.

    `run` is what `analysis_service.get_run_result` returns. Raises KeyError
    when the ticker has no valuation in the run.
    """
    from src.utils.valuation_workbook import build_workbook
    data = run.get("data") or {}
    dcf = data.get("dcf_range") or {}
    t = ticker or data.get("primary_ticker") or next(iter(dcf), None)
    if not t or t not in dcf:
        raise KeyError(t or "")
    blob = build_workbook(run, t, load_members=_members, load_statements=load_statements,
                          meta={"run_id": run.get("run_id")})
    return blob, safe_filename(t, run.get("run_at") or data.get("end_date"))

"""
Phase 2.7 — EDGAR_HKEX Resolver  (lightweight, no LLM, ~0.5 s per ticker)

Resolves annual filing references for all tickers:
  • US / ADR tickers  → SEC EDGAR submissions API (10-K / 20-F)
  • HK-listed tickers → HKEXnews titleSearchServlet (Annual Report / 年報)

For US tickers, looks up the most recent SEC annual filing using the free SEC
EDGAR submissions API.  No API key required; respects SEC rate-limit guideline.

Output stored in state:
  state["data"]["edgar_filing_refs"][ticker] — dict with keys:
      cik, company_name, filing_type, is_foreign,
      accession_number, filing_date, period_of_report,
      fiscal_year, filing_url, viewer_url

Why this matters:
  The deep research agent pre-loads FMP financial data and instructs the LLM
  to cite every figure as "(Financial Data API)".  That vague attribution
  causes the Citation Auditor to score all financial metrics as UNVERIFIED,
  even though the underlying data originates from the company's SEC filing.

  By resolving the actual EDGAR accession number BEFORE deep research runs,
  we can rewrite the citation label to:
      "CHA Form 20-F (FY2024, SEC EDGAR acc: 0001234567-25-012345)"
  so that every quantitative claim in the report carries a traceable,
  primary-source citation from the start.
"""

from __future__ import annotations

from src.graph.state import AgentState
from src.tools.api import get_edgar_filing_refs
from src.tools.hk.ticker import is_hk_ticker
from src.tools.hkex_api import get_hkex_filing_refs
from src.utils.progress import progress


def run_edgar_hkex_resolver(state: AgentState) -> AgentState:
    """
    Phase 2.7 — EDGAR_HKEX Resolver: resolve annual filing references for all tickers.

    For US/ADR tickers : looks up the SEC EDGAR submissions API (10-K / 20-F).
    For HK tickers     : looks up HKEXnews titleSearchServlet (Annual Report / 年報).

    Reads:
        state["data"]["tickers"]   — list of ticker symbols

    Writes:
        state["data"]["edgar_filing_refs"]  — {ticker: filing_ref_dict}
    """
    agent_id = "edgar_hkex_resolver"
    tickers  = state["data"]["tickers"]
    edgar_refs: dict[str, dict] = {}

    for ticker in tickers:
        # ── HK-listed stocks: resolve via HKEXnews, not SEC EDGAR ─────────
        if is_hk_ticker(ticker):
            progress.update_status(agent_id, ticker, "Resolving HKEXnews Annual Report (年報)...")
            ref = get_hkex_filing_refs(ticker)
            if ref:
                edgar_refs[ticker] = ref
                progress.update_status(
                    agent_id, ticker,
                    f"HKEX OK: {ref['filing_type']} | "
                    f"filed={ref['filing_date']} | "
                    f"FY={ref['fiscal_year']} | "
                    f"url={ref['filing_url']}"
                )
            else:
                edgar_refs[ticker] = {}
                progress.update_status(
                    agent_id, ticker,
                    "HKEXnews: no Annual Report found — AKShare attribution will be used"
                )
            continue

        progress.update_status(agent_id, ticker, "Resolving SEC EDGAR annual filing...")
        ref = get_edgar_filing_refs(ticker)

        if ref:
            edgar_refs[ticker] = ref
            # Tailor the status message to the resolution tier
            if ref.get("is_stub"):
                progress.update_status(
                    agent_id, ticker,
                    f"EDGAR CIK stub: company={ref['company_name']} | "
                    f"CIK={ref['cik']} | no annual filing yet — CIK used for attribution"
                )
            elif ref.get("is_ipo_prospectus"):
                progress.update_status(
                    agent_id, ticker,
                    f"EDGAR IPO prospectus: {ref['filing_type']} | "
                    f"acc={ref['accession_number']} | "
                    f"filed={ref['filing_date']} | "
                    f"recent IPO — no 20-F/10-K yet"
                )
            else:
                progress.update_status(
                    agent_id, ticker,
                    f"EDGAR OK: {ref['filing_type']} | "
                    f"acc={ref['accession_number']} | "
                    f"filed={ref['filing_date']} | "
                    f"period={ref['period_of_report']} | "
                    f"foreign={ref['is_foreign']}"
                )
        else:
            edgar_refs[ticker] = {}
            progress.update_status(
                agent_id, ticker,
                "EDGAR CIK not found — FMP attribution will be used as fallback"
            )

    state["data"]["edgar_filing_refs"] = edgar_refs
    return state

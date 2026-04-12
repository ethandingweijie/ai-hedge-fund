"""
Phase 7f — Governance & Citation Auditor: Data Provenance & Source of Truth

  Phase 0 — EDGAR pre-resolution: upgrade unverified financial-metric entries
             to PARTIAL using the SEC filing ref from Phase 2.7
  Phase 1 — Classify citations from the deep_research citation_registry
  Phase 2 — LLM audit: hallucination flags + source gap list + audit score

Inputs consumed from state:
  citation_registry       — structured list from deep_research.py
  deep_research_sections  — 2a–2f section text (fallback if registry empty)
  decisions, scenario, dcf_range

Output stored in state:
  state["data"]["citation_audit"][ticker]  — CitationAuditOutput dict
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import CitationAuditOutput
from src.graph.state import AgentState
from src.utils.llm import call_llm
from src.utils.progress import progress


# ── Audit system prompt ───────────────────────────────────────────────────────

_AUDIT_SYSTEM = """
You are a Supervisory Research Auditor. Do not search. Work only from the provided registry and context.

STEP 1 — FLAG HALLUCINATIONS if any registry entry shows:
- Metric the company stopped disclosing cited as current
- Financial figures in different currency units than current price
- Forward P/E, price targets cited without underlying EPS/EBITDA anchor
- WACC lacks country risk premium for non-US company
- Claims conflict with provided DCF/scenario data; arithmetic errors in ratios or growth rates
- Third-party research date attributed to company's SEC filing period
- Restructuring headcount without 8-K, earnings transcript, or dated press release
- Government contract values without SAM.gov, press release, or SEC filing
- Undisclosed segment margin cited as reported fact
- Private-company revenue cited as fact without "estimated" qualifier
- Source URL points to a different publisher than the named source

STEP 2 — GAP LIST: "GAP [n] — [description]: [suggested fix]"
Prioritise: contract values/headcount > market share > competitive estimates > background
Only list gaps where a primary source is realistically obtainable.

AUDIT SCORE (1-10):
- 7-10: financials sourced to correct filing; no material hallucination
- 5-6: minor attribution gaps; no hallucinations
- 3-4: significant unverified estimates present
- 1-2: material hallucination or pipeline failure
Floor is 4 for structurally plausible, internally consistent data.

Output JSON only — example structure:
{{"hallucination_flags": ["..."], "primary_source_gaps": ["GAP 1 — ..."], "audit_score": 7}}
""".strip()


def run_citation_auditor(state: AgentState) -> AgentState:
    """Phase 7f: Governance & Citation Auditor — hallucination flags + source gaps."""
    agent_id = "citation_auditor"
    tickers  = state["data"]["tickers"]
    audit_results: dict = {}

    # Pull citation registry built by deep_research.py
    citation_registry: list[dict] = state["data"].get("citation_registry", []) or []

    # Deep research sections as fallback context
    deep_research_sections = state["data"].get("deep_research_sections", {}) or {}
    deep_research_text     = state["data"].get("deep_research", "") or ""

    # EDGAR filing refs resolved by Phase 2.7 (used for pre-resolution)
    edgar_refs_map: dict = state["data"].get("edgar_filing_refs") or {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Phase 0 — EDGAR pre-resolution")

        # ── Pipeline data for audit context ───────────────────────────────────
        decision      = (state["data"].get("decisions") or {}).get(ticker, {})
        scenario      = (state["data"].get("scenario_analysis") or {}).get(ticker, {})
        dcf_ticker    = (state["data"].get("dcf_range") or {}).get(ticker, {})
        action      = str(decision.get("action", "HOLD")).upper()
        rationale   = str(decision.get("rationale", decision.get("reasoning", "")))[:600]
        ev          = scenario.get("expected_value", 0)
        curr_price  = scenario.get("current_price", 0)
        base_iv     = (dcf_ticker.get("base") or {}).get("intrinsic_value", 0)
        data_source = dcf_ticker.get("data_source", "unknown")
        cal_note    = dcf_ticker.get("calibration_note", "")
        fx_note     = dcf_ticker.get("fx_note", "")
        reported_currency = dcf_ticker.get("reported_currency", "USD")

        # ── Phase 0: EDGAR pre-resolution ─────────────────────────────────────
        # Use the filing ref from Phase 2.7 to upgrade unverified financial-metric
        # entries to PARTIAL before Phase 2 LLM resolver runs.
        # This avoids wasting Phase 2 search slots on facts we already know the
        # primary source for (the company's 20-F/10-K on SEC EDGAR).
        edgar_ref = edgar_refs_map.get(ticker) or {}
        edgar_preresolved: list[dict] = []

        if edgar_ref and edgar_ref.get("accession_number"):
            _ft  = edgar_ref["filing_type"]
            _acc = edgar_ref["accession_number"]
            _fd  = edgar_ref.get("filing_date", "")
            _pr  = edgar_ref.get("period_of_report", "")
            _fy  = edgar_ref.get("fiscal_year", "") or _pr[:4]
            _cn  = edgar_ref.get("company_name", ticker)
            _url = edgar_ref.get("filing_url", "")

            _FIN_KW = {
                "revenue", "net income", "profit", "loss", "cash flow", "capex",
                "capital expenditure", "net debt", "ebitda", "margin", "sales",
                "earnings", "fcf", "ocf", "free cash", "fy20", "fy19", "fy18",
                "store count", "gmv", "active user", "active member", "membership",
                "franchis", "royalt", "gross profit", "operating income",
            }
            _IPO_KW = {
                "ipo", "initial public offering", "listed", "nasdaq", "nyse",
                "f-1", "424b4", "prospectus", "ads price", "ipo proceeds",
            }
            # Claims that must NOT be bound to the company's 10-K/20-F:
            # market share, CAGR, and industry size figures come from third-party
            # research publishers (IDC, Gartner, IoT Analytics, etc.) whose publication
            # date differs from the company's SEC filing period.
            # Analyst inferences about undisclosed segments (e.g. AFS margin) are
            # not in the filing and should remain unverified for the LLM auditor to flag.
            _THIRD_PARTY_KW = {
                "market share", "cagr", "industry size", "market size", "tam",
                "addressable market", "global market", "market growth", "forecast",
                "gartner", "idc", "forrester", "cb insights", "pitchbook",
                "iot analytics", "cognitive market", "frost & sullivan",
                "wood mackenzie", "ihs markit", "analyst estimate",
                "undisclosed segment", "not formally disclosed", "inferred",
            }

            for entry in citation_registry:
                if entry.get("verified"):
                    continue  # Already verified — skip
                claim_lc  = (entry.get("claim") or "").lower()
                src_type  = (entry.get("source_type") or "").lower()
                src_name  = (entry.get("source_name") or "").lower()

                is_already_sourced = src_type in ("10-k", "20-f", "10-q", "press_release",
                                                   "earnings_transcript", "regulatory_filing")
                if is_already_sourced:
                    if src_type == "earnings_transcript":
                        # Transcripts live on the IR page or as 8-K exhibits under a
                        # *different* accession from the annual filing — do NOT inject
                        # the 10-K EDGAR URL.  Preserve source_name / source_type as-is.
                        # The entry remains unverified so the auditor LLM can flag gaps.
                        pass
                    elif not entry.get("url") and _url:
                        # For actual filing types (10-K, 20-F, 10-Q etc.) fill in the
                        # EDGAR URL if none was captured during web search.
                        entry["url"] = _url
                    continue

                is_third_party = any(kw in claim_lc for kw in _THIRD_PARTY_KW)
                if is_third_party:
                    continue  # Do not bind third-party research claims to the company's SEC filing

                # Guard: entry already carries a named non-EDGAR publisher — do not
                # overwrite its source_name / url with the company's SEC filing.
                # Checks src_name because source_type alone (e.g. "web_search") does
                # not distinguish between an EDGAR page and an external publisher URL.
                _NAMED_PUBLISHERS = {
                    "yahoo", "quartr", "bloomberg", "reuters", "ginnie", "fhfa",
                    "hud", "treasury", "federal reserve", "fed reserve", "wsj",
                    "financial times", "cnbc", "seeking alpha", "s&p global",
                    "moody", "fitch", "investor relations", "earnings call",
                    "press release", "conference call", "mortgage bankers",
                    "urban institute", "cfpb", "hmda", "sifma", "finra",
                    # FMP / data-API sourced entries must not be re-attributed to the
                    # company's SEC filing — the API is the actual source of record.
                    "financial data api", "fmp", "pre-loaded", "financial modelling prep",
                    "financial modeling prep", "api data", "api (pre-loaded",
                }
                has_named_source = any(kw in src_name for kw in _NAMED_PUBLISHERS)
                if has_named_source:
                    continue  # Preserve the existing named-publisher attribution

                is_ipo = any(kw in claim_lc for kw in _IPO_KW)
                is_fin = any(kw in claim_lc for kw in _FIN_KW)

                if is_ipo:
                    entry["source_type"] = "press_release"
                    entry["source_name"] = f"{ticker} Form F-1/424B4 Final Prospectus (SEC EDGAR)"
                    entry["url"]  = edgar_ref.get("viewer_url", "").replace(_ft, "F-1")
                    entry["date"] = entry.get("date") or _fd
                    entry["verified"] = True   # IPO metadata is definitively in the F-1
                    edgar_preresolved.append(entry)
                elif is_fin:
                    entry["source_type"] = _ft
                    entry["source_name"] = f"{_cn} Form {_ft} FY{_fy} (SEC EDGAR, acc: {_acc})"
                    entry["url"]  = _url
                    entry["date"] = entry.get("date") or _pr
                    # Mark as verified=True — FMP financial data originates from this filing
                    entry["verified"] = True
                    edgar_preresolved.append(entry)

            if edgar_preresolved:
                progress.update_status(
                    agent_id, ticker,
                    f"Phase 0 EDGAR pre-resolution: {len(edgar_preresolved)} entries upgraded "
                    f"to {_ft} (acc: {_acc})"
                )

        n_total   = len(citation_registry)
        n_sourced = sum(1 for e in citation_registry if e.get("verified"))
        progress.update_status(
            agent_id, ticker,
            f"Phase 0 complete: {n_sourced}/{n_total} EDGAR pre-resolved | running audit"
        )

        # ── Audit LLM call ────────────────────────────────────────────────────
        progress.update_status(agent_id, ticker, "Audit — hallucination flags + source gaps")

        # Format registry for the audit prompt (increased cap: 40)
        registry_block = ""
        if citation_registry:
            lines = []
            for e in citation_registry[:40]:
                status = "VERIFIED" if e.get("verified") else "UNVERIFIED"
                src    = e.get("source_name", "") or "?"
                date   = e.get("date", "")
                url    = e.get("url", "")
                lines.append(
                    f"[{status}] REF-{e.get('ref_id',0):02d} | "
                    f"{e.get('section','?').upper()} | "
                    f"{e.get('claim','')[:80]} | "
                    f"Source: {src}" + (f" ({date})" if date else "")
                    + (f" | URL: {url[:60]}" if url else "")
                )
            registry_block = "\n".join(lines)
        else:
            # Fallback: use raw deep research text if no registry
            dr_parts = [
                deep_research_sections.get("2c", ""),
                deep_research_sections.get("2a", ""),
                deep_research_sections.get("2d", ""),
            ]
            registry_block = "\n\n".join(p for p in dr_parts if p)[:4000] or (
                deep_research_text[:3000] if deep_research_text else "No citation data available."
            )

        # EDGAR pre-resolution summary for audit prompt
        edgar_preresolve_block = ""
        if edgar_preresolved:
            _er = edgar_ref
            edgar_preresolve_block = (
                f"EDGAR PRE-RESOLUTION — {len(edgar_preresolved)} claims resolved to "
                f"{_er.get('company_name', ticker)} Form {_er.get('filing_type')} "
                f"(acc: {_er.get('accession_number')}, period: {_er.get('period_of_report')})"
            )

        template = ChatPromptTemplate.from_messages([
            ("system", _AUDIT_SYSTEM),
            ("human", (
                "TICKER: {ticker}\n\n"
                "=== EDGAR PRE-RESOLUTION ===\n{edgar_preresolve}\n\n"
                "=== CITATION REGISTRY ===\n{registry}\n\n"
                "=== KEY PIPELINE CLAIMS ===\n"
                "Recommendation: {action}\n"
                "Rationale: {rationale}\n"
                "Expected Value (prob-wtd): ${ev:.2f} | Current Price: ${curr:.2f} | Base IV: ${base_iv:.2f}\n"
                "Reported currency: {currency} | FX note: {fx_note}\n"
                "Growth data source: {data_source} | Calibration: {cal_note}"
            )),
        ])

        prompt = template.invoke({
            "ticker":           ticker,
            "edgar_preresolve": edgar_preresolve_block or "No EDGAR filing ref available.",
            "registry":         registry_block or "No citation registry available.",
            "action":           action,
            "rationale":        rationale,
            "ev":               ev or 0,
            "curr":             curr_price or 0,
            "base_iv":          base_iv or 0,
            "currency":         reported_currency or "USD",
            "fx_note":          fx_note or "N/A",
            "data_source":      data_source,
            "cal_note":         cal_note or "N/A",
        })

        result: CitationAuditOutput = call_llm(
            prompt=prompt,
            pydantic_model=CitationAuditOutput,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: CitationAuditOutput(
                hallucination_flags=["Citation auditor LLM call failed."],
                primary_source_gaps=["Full audit not available."],
                audit_score=5,
            ),
        )

        audit_results[ticker] = result.model_dump()

        progress.update_status(
            agent_id, ticker,
            f"Audit score: {result.audit_score}/10 | "
            f"{len(result.hallucination_flags)} hallucination flag(s) | "
            f"{len(result.primary_source_gaps)} source gap(s)"
        )

    state["data"]["citation_audit"] = audit_results
    return state

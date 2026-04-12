"""
Phase 7d — BU-Level Analyst: Operational Deep Dive

Role: "Act as an Equity Research Analyst. Using earnings transcripts and
segment financials, refine Business Unit projections."

Four analytical tasks:
  1. KPI Extraction: unit economics, backlog (RPO), segment NRR/DBNRR
  2. Margin Attribution: management commentary on segment margin drivers
  3. Capex Breakdown: Growth vs Maintenance capex split
  4. Product Resilience: new product adoption, competitive win-rates

Output:
  - Segment-level 3-year revenue and margin forecast table (bottom-up DCF)
  - BU analysis dict stored in state["data"]["bu_analysis"][ticker]

FMP API check:
  - Earnings call transcripts not available on free tier (requires Ultimate $149/mo)
  - Uses /stable/press-releases and deep_research_sections as proxy sources
  - Degrades gracefully if transcript data unavailable
"""

from langchain_core.prompts import ChatPromptTemplate
from src.graph.state import AgentState
from src.tools.api import get_press_releases, search_line_items, get_analyst_estimates
from src.utils.llm import call_llm
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state
from src.data.models import BUAnalysisOutput


SYSTEM_PROMPT = """
You are a Buy-Side Equity Research Analyst specialising in operational deep dives.

Your job is to construct a bottom-up Business Unit (BU) model from all available data.

Step 1 — KPI Extraction:
Identify and quantify:
- Unit economics: revenue per user/customer, cost per acquisition, payback period
- Backlog / RPO: current backlog level, YoY growth, coverage ratio (backlog / annual revenue)
- Segment retention: Net Revenue Retention (NRR), Dollar-Based NRR per business unit

Step 2 — Margin Attribution (per segment where data exists):
- What is driving margin expansion or compression?
- Separate: pricing power, opex leverage, mix shift, input cost changes
- Quote management guidance where available

Step 3 — Capex Breakdown:
- Growth capex (new capacity, new markets, R&D): estimated %
- Maintenance capex (sustaining existing base): estimated %
- Total capex as % of revenue trend

Step 4 — Product Resilience:
- New product adoption rates (attach rate, penetration %)
- Competitive win-rate evidence (management commentary or third-party data)
- Any quantitative evidence of switching costs or lock-in

Step 5 — 3-Year Segment Revenue & Margin Forecast:
Build a bottom-up table with:
- Bear / Base / Bull revenue growth per year (Year 1, 2, 3)
- Corresponding EBITDA margin trajectory
- Key assumption for each scenario
- Output these as structured JSON

Output JSON only. Be specific; cite evidence for every key claim.
""".strip()


def run_bu_analyst(state: AgentState) -> AgentState:
    """Phase 7d: Operational deep dive — BU-level KPIs, margin attribution, capex split."""
    agent_id = "bu_analyst"
    tickers = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")

    dr_sections = state["data"].get("deep_research_sections", {})
    deep_research_text = state["data"].get("deep_research", "") or ""
    industry_brief = state["data"].get("industry_brief", "")
    sector = state["data"].get("sector", "")

    bu_results: dict = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Fetching operational data")

        # ── Press releases as transcript proxy (FMP free tier available) ──────
        press_releases_text = ""
        try:
            releases = get_press_releases(ticker, end_date, limit=8, api_key=api_key)
            if releases:
                lines = []
                for r in releases[:6]:
                    title = getattr(r, "title", "") or r.get("title", "")
                    body  = getattr(r, "text",  "") or r.get("text",  "")
                    if title or body:
                        lines.append(f"[{title}]\n{str(body)[:800]}")
                press_releases_text = "\n\n".join(lines)
        except Exception:
            press_releases_text = ""

        # ── Latest financial line items for capex computation ─────────────────
        capex_text = ""
        try:
            li = search_line_items(
                ticker,
                ["revenue", "capital_expenditure", "free_cash_flow", "ebitda",
                 "research_and_development", "operating_expenses", "net_income",
                 "shares_outstanding"],
                end_date, period="annual", limit=4, api_key=api_key,
            )
            if li:
                rows = []
                for item in li[:4]:
                    rev   = getattr(item, "revenue", None)
                    capex = getattr(item, "capital_expenditure", None)
                    fcf   = getattr(item, "free_cash_flow", None)
                    ebitda = getattr(item, "ebitda", None)
                    period = getattr(item, "report_period", "")
                    if rev and capex:
                        capex_pct = abs(capex) / rev * 100
                        fcf_pct   = (fcf / rev * 100) if fcf else None
                        rows.append(
                            f"{period}: Rev ${rev/1e9:.1f}B | Capex ${abs(capex)/1e9:.1f}B "
                            f"({capex_pct:.1f}% rev) | FCF {f'{fcf_pct:.1f}%' if fcf_pct else 'N/A'} "
                            f"| EBITDA ${ebitda/1e9:.1f}B" if ebitda else ""
                        )
                capex_text = "\n".join(rows)
        except Exception:
            capex_text = ""

        # ── Analyst estimates for forward projections ─────────────────────────
        analyst_fwd_text = ""
        try:
            estimates = get_analyst_estimates(
                ticker, end_date, period="annual", limit=3, api_key=api_key
            )
            if estimates:
                est_lines = []
                for e in estimates[:3]:
                    rev_avg = getattr(e, "revenue_avg", None)
                    eps_avg = getattr(e, "eps_avg",     None)
                    period  = getattr(e, "period",      "")
                    if rev_avg:
                        est_lines.append(
                            f"{period}: Rev est ${rev_avg/1e9:.1f}B"
                            + (f" | EPS est ${eps_avg:.2f}" if eps_avg else "")
                        )
                analyst_fwd_text = "\n".join(est_lines)
        except Exception:
            analyst_fwd_text = ""

        # ── Deep research sections most relevant to BU analysis ───────────────
        # 2A = Competitive Position, 2B = Competitive Dynamics, 2D = Management Quality
        bu_context = "\n\n".join(filter(None, [
            dr_sections.get("2a", ""),
            dr_sections.get("2b", ""),
            dr_sections.get("2d", ""),
            deep_research_text[:3000] if not any(dr_sections.values()) else "",
        ]))[:4000]

        progress.update_status(agent_id, ticker, "Running BU analysis LLM")

        template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", (
                "Ticker: {ticker}\n"
                "Sector: {sector}\n\n"
                "=== Capex & Revenue Financials (last 4 years) ===\n{capex}\n\n"
                "=== Analyst Forward Estimates ===\n{estimates}\n\n"
                "=== Press Releases / Earnings Commentary ===\n{press}\n\n"
                "=== Deep Research — Competitive & Management Context ===\n{context}\n\n"
                "=== Industry Brief ===\n{brief}\n\n"
                "Output format:\n"
                "{{\n"
                '  "kpi_extraction": {{\n'
                '    "unit_economics": "...",\n'
                '    "backlog_rpo": "...",\n'
                '    "segment_nrr": "..."\n'
                "  }},\n"
                '  "margin_attribution": "...",\n'
                '  "capex_breakdown": {{\n'
                '    "growth_capex_pct": float,\n'
                '    "maintenance_capex_pct": float,\n'
                '    "capex_as_pct_revenue": float,\n'
                '    "commentary": "..."\n'
                "  }},\n"
                '  "product_resilience": "...",\n'
                '  "segment_forecast": {{\n'
                '    "bear": {{"yr1_rev_growth": float, "yr2_rev_growth": float, "yr3_rev_growth": float, "ebitda_margin_yr3": float, "assumption": "..."}},\n'
                '    "base": {{"yr1_rev_growth": float, "yr2_rev_growth": float, "yr3_rev_growth": float, "ebitda_margin_yr3": float, "assumption": "..."}},\n'
                '    "bull": {{"yr1_rev_growth": float, "yr2_rev_growth": float, "yr3_rev_growth": float, "ebitda_margin_yr3": float, "assumption": "..."}}\n'
                "  }},\n"
                '  "data_limitations": "list any missing data that would improve this analysis"\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "sector": sector,
            "capex": capex_text or "Not available",
            "estimates": analyst_fwd_text or "Not available",
            "press": press_releases_text[:2000] if press_releases_text else "Not available (requires FMP press-release plan)",
            "context": bu_context or "Not available",
            "brief": industry_brief[:20000] if industry_brief else "Not available",
        })

        result: BUAnalysisOutput = call_llm(
            prompt=prompt,
            pydantic_model=BUAnalysisOutput,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: BUAnalysisOutput(
                kpi_extraction={"unit_economics": "N/A", "backlog_rpo": "N/A", "segment_nrr": "N/A"},
                margin_attribution="Analysis unavailable.",
                capex_breakdown={"growth_capex_pct": 0.0, "maintenance_capex_pct": 0.0,
                                 "capex_as_pct_revenue": 0.0, "commentary": "N/A"},
                product_resilience="Analysis unavailable.",
                segment_forecast={
                    "bear": {"yr1_rev_growth": 0.0, "yr2_rev_growth": 0.0, "yr3_rev_growth": 0.0,
                             "ebitda_margin_yr3": 0.0, "assumption": "N/A"},
                    "base": {"yr1_rev_growth": 0.0, "yr2_rev_growth": 0.0, "yr3_rev_growth": 0.0,
                             "ebitda_margin_yr3": 0.0, "assumption": "N/A"},
                    "bull": {"yr1_rev_growth": 0.0, "yr2_rev_growth": 0.0, "yr3_rev_growth": 0.0,
                             "ebitda_margin_yr3": 0.0, "assumption": "N/A"},
                },
                data_limitations="LLM call failed.",
            ),
        )

        bu_results[ticker] = result.model_dump()
        progress.update_status(agent_id, ticker, "BU analysis complete")

    state["data"]["bu_analysis"] = bu_results
    return state

"""
Phase 4 — Data Router

What it does:
- Pure Python, NO LLM call
- Defines which financial line items each investor philosophy actually needs
- Pre-fetches those items once from the API and stores in state["data"]["routed_data"]
- Prevents Phase 5's 12 parallel investor threads from making redundant API calls
  for the same data (e.g. all of them fetching revenue independently)

Design choice: a single dict lookup (AGENT_DATA_FEEDS) rather than a dynamic LLM call.
The routing logic is stable, auditable, and cheap — no token cost.
"""

from src.graph.state import AgentState
from src.tools.api import search_line_items, get_prices, get_financial_metrics, get_market_cap, get_web_intelligence
from src.agents.industry.deep_research import run_deep_research_agent
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state

# ---------------------------------------------------------------------------
# Sector-specific line items merged into ALL agent bundles when sector matches.
# Fields absent from the FMP response are silently skipped — the
# getattr(..., None) + "if val is not None" chain in run_data_router handles this.
# ---------------------------------------------------------------------------
SECTOR_LINE_ITEM_OVERLAYS: dict[str, list[str]] = {
    "Tech": [
        "research_and_development",   # R&D intensity; AI arms race proxy
        "deferred_revenue",           # SaaS ARR proxy; growth = bookings health
        "intangible_assets",          # Platform/data moat; IP capitalisation
        "stock_based_compensation",   # Dilution risk in high-growth tech
        "gross_profit",               # Powers Gross Margin tile (Mature SaaS view)
        "operating_income",           # Powers Operating Margin tile (Hyperscaler + Mature SaaS views)
    ],
    "Biopharma": [
        "research_and_development",
        "intangible_assets",          # Capitalised drug development costs
        "goodwill",                   # Acquisition-heavy pipeline assessment
    ],
    "Financials": [
        "interest_expense",
        "interest_income",            # NIM reconstruction
        "provision_for_loan_losses",  # Credit cycle signal
    ],
    "Energy": [
        "capital_expenditure",
        "depreciation_and_amortization",
        "operating_cash_flow",
    ],
    "Industrials": [
        "capital_expenditure",
        "revenue",
        "operating_income",
    ],
    "RealEstate": [
        "dividends_and_distributions",    # FFO/AFFO proxy — key REIT income metric
        "depreciation_and_amortization",  # Add-back to derive FFO from net income
        "capital_expenditure",            # Maintenance vs. growth capex split
        "long_term_debt",                 # LTV and refinancing risk
    ],
    "Transportation": [
        "capital_expenditure",            # Fleet renewal / infrastructure spend
        "depreciation_and_amortization",  # Aircraft/fleet depreciation schedule
        "operating_cash_flow",            # Cash generation after high D&A
        "interest_expense",               # High leverage in airlines
    ],
    "Materials": [
        "capital_expenditure",            # Sustaining vs. growth capex in commodity cycles
        "depreciation_and_amortization",  # Asset-heavy; D&A is significant
        "operating_income",               # Mid-cycle normalised earnings proxy
        "long_term_debt",                 # Balance sheet leverage at cycle trough
    ],
    "Resources": [
        "capital_expenditure",            # Drilling / mine development spend
        "depreciation_and_amortization",  # Depletion charge — proxy for reserve consumption
        "operating_cash_flow",            # Cash flow from operations (DACF proxy)
        "long_term_debt",                 # Net debt / EBITDA — key credit metric
    ],
    "ProfessionalServices": [
        "operating_income",               # Pre-bonus EBIT for ad agencies
        "stock_based_compensation",       # Key cost for payment tech companies
        "research_and_development",       # Technology investment for payment processors
        "interest_income",                # Float income — material for payment processors
    ],
    "Consumer": [
        "cost_of_revenue",                # contribution margin = (Revenue - COGS) / Revenue
        "selling_general_administrative", # brand investment intensity; SG&A / Revenue trend
        "capital_expenditure",            # store rollout / capex-light vs. capex-heavy model
    ],
    "Telco": [
        "capital_expenditure",            # maintenance vs. growth capex split
        "depreciation_and_amortization",  # tower / spectrum asset D&A; capex + D&A = cash burn
        "interest_expense",               # high leverage in Telco; debt service coverage
        "operating_cash_flow",            # FCF = OCF - capex; FCF yield computation
    ],
    "Crypto": [
        "capital_expenditure",            # MW pipeline expansion spend
        "operating_expenses",             # energy cost + opex = AISC denominator
        "depreciation_and_amortization",  # ASIC rig depreciation schedule
    ],
    "HealthcareServices": [
        "revenue",                        # premium revenue base for MLR and PMPM
        "operating_income",               # pre-interest EBIT; adjusted for one-time items
        "selling_general_administrative", # SG&A as % of premiums (target <15%)
        "long_term_debt",                 # debt-to-equity; leverage risk for managed care
    ],
}

# ---------------------------------------------------------------------------
# What line items each investor cares most about
# ---------------------------------------------------------------------------
AGENT_LINE_ITEMS: dict[str, list[str]] = {
    "buffett": [
        "free_cash_flow", "return_on_equity", "capital_expenditure",
        "net_income", "revenue", "gross_margin", "long_term_debt",
    ],
    "graham": [
        "earnings_per_share", "book_value_per_share", "current_assets",
        "current_liabilities", "long_term_debt", "total_assets",
        "net_income", "dividends_and_distributions",
    ],
    "munger": [
        "return_on_equity", "gross_margin", "free_cash_flow",
        "revenue", "operating_income", "capital_expenditure",
    ],
    "ackman": [
        "free_cash_flow", "revenue", "operating_income", "net_income",
        "shares_outstanding", "long_term_debt", "capital_expenditure",
    ],
    "cathie_wood": [
        "revenue", "research_and_development", "gross_margin",
        "operating_cash_flow", "capital_expenditure",
    ],
    "burry": [
        "revenue", "free_cash_flow", "net_income", "accounts_receivable",
        "total_assets", "total_liabilities", "operating_cash_flow",
        "capital_expenditure", "long_term_debt",
    ],
    "damodaran": [
        "revenue", "operating_income", "net_income", "capital_expenditure",
        "depreciation_and_amortization", "free_cash_flow", "total_debt",
        "book_value_per_share", "earnings_per_share",
    ],
    "pabrai": [
        "free_cash_flow", "net_income", "total_assets", "long_term_debt",
        "revenue", "book_value_per_share",
    ],
    "lynch": [
        "earnings_per_share", "revenue", "net_income", "long_term_debt",
        "free_cash_flow", "dividends_and_distributions",
    ],
    "fisher": [
        "revenue", "research_and_development", "gross_margin",
        "operating_income", "net_income", "capital_expenditure",
    ],
    "jhunjhunwala": [
        "revenue", "net_income", "return_on_equity", "free_cash_flow",
        "capital_expenditure", "long_term_debt",
    ],
    "druckenmiller": [
        # Druckenmiller is macro-first; prices matter more than line items
        "revenue", "free_cash_flow", "net_income",
    ],
}


def run_data_router(state: AgentState) -> AgentState:
    """Phase 4: pre-fetch per-agent data to eliminate redundant API calls in Phase 5."""
    agent_id = "data_router"
    ticker = state["data"].get("primary_ticker", state["data"]["tickers"][0])
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]
    start_date = state["data"]["start_date"]

    progress.update_status(agent_id, ticker, "Pre-fetching shared financial metrics")

    # Shared data every investor uses
    metrics = get_financial_metrics(ticker, end_date, period="ttm", limit=5, api_key=api_key)
    market_cap = get_market_cap(ticker, end_date, api_key=api_key)
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)

    # Sector-aware overlay: add sector-specific fields to every agent's fetch.
    # The LLM classifier may emit the sector as the canonical key ("Tech",
    # "Biopharma", "Financials", "RealEstate") OR a loose variant
    # ("Technology", "Biotechnology", "Banking", "Real Estate", …). Strict
    # equality silently dropped the overlay whenever a variant was stored —
    # observed on CRM where sector was "Technology" and stock_based_compensation
    # / R&D / gross_margin / operating_income were missing from raw_financials
    # despite being wired into SECTOR_LINE_ITEM_OVERLAYS["Tech"]. Use the
    # shared is_*_sector() helpers to route variants to the canonical key.
    from src.agents.industry.sector_prompts import (
        is_biopharma_sector, is_tech_sector, is_bank_sector, is_reit_sector,
    )
    sector = state["data"].get("sector", "")
    _overlay_key = None
    if is_biopharma_sector(sector):
        _overlay_key = "Biopharma"
    elif is_tech_sector(sector):
        _overlay_key = "Tech"
    elif is_bank_sector(sector):
        _overlay_key = "Financials"
    elif is_reit_sector(sector):
        _overlay_key = "RealEstate"
    else:
        # Exact-match fallback for sectors that don't have loose-match helpers
        # (Energy, Industrials, Consumer, Telco, Crypto, HealthcareServices, …)
        _overlay_key = sector if sector in SECTOR_LINE_ITEM_OVERLAYS else None
    sector_extras: set[str] = (
        set(SECTOR_LINE_ITEM_OVERLAYS.get(_overlay_key, [])) if _overlay_key else set()
    )

    # Deduplicate line item requests across all agents (+ sector overlays)
    all_fields: set[str] = set()
    for fields in AGENT_LINE_ITEMS.values():
        all_fields.update(fields)
    all_fields.update(sector_extras)

    progress.update_status(agent_id, ticker, f"Fetching {len(all_fields)} unique line items")

    line_items = search_line_items(
        ticker=ticker,
        line_items=sorted(all_fields),
        end_date=end_date,
        period="annual",
        limit=5,
        api_key=api_key,
    )

    # Index line items by field name for fast per-agent slicing
    line_item_by_field: dict[str, list] = {}
    for item in (line_items or []):
        for field in all_fields:
            val = getattr(item, field, None)
            if val is not None:
                line_item_by_field.setdefault(field, []).append({
                    "period": item.report_period,
                    "value": val,
                })

    # Merge sector-overlay line items into raw_financials so downstream agents
    # and the LLM context injection see the complete per-FY row. Without this,
    # Tech tickers are missing stock_based_compensation / R&D / gross_margin /
    # operating_income despite the overlay being fetched. Same applies for
    # Biopharma (R&D / intangibles), Bank (non-interest income), REIT (FFO/NOI).
    _raw_fin = state["data"].get("raw_financials")
    if isinstance(_raw_fin, dict):
        # Index line items by FY key (same format raw_financials uses: "FY2024" etc.)
        # line_item_by_field is keyed by field → [{period: "2024-01-31", value: ...}, ...]
        # Build FY → field → value map by extracting FY from the period string
        import re as _re
        _period_to_fy: dict[str, dict[str, object]] = {}
        for _field, _entries in line_item_by_field.items():
            for _entry in _entries:
                _period = _entry.get("period", "")
                _year_match = _re.search(r"(\d{4})", _period)
                if not _year_match:
                    continue
                _fy = f"FY{_year_match.group(1)}"
                _period_to_fy.setdefault(_fy, {})[_field] = _entry.get("value")
        # Augment each FY row with any overlay fields it lacks
        for _fy, _fy_dict in _raw_fin.items():
            if not isinstance(_fy_dict, dict):
                continue
            _overlay_row = _period_to_fy.get(_fy, {})
            for _field, _val in _overlay_row.items():
                if _field not in _fy_dict and _val is not None:
                    _fy_dict[_field] = _val

    # Build per-agent data bundles (agent-specific fields + sector overlays)
    routed_data: dict[str, dict] = {}
    for agent_key, fields in AGENT_LINE_ITEMS.items():
        bundle: dict[str, object] = {
            "ticker": ticker,
            "market_cap": market_cap,
            "metrics": [m.model_dump() for m in (metrics or [])],
        }
        combined_fields = set(fields) | sector_extras
        for field in combined_fields:
            if field in line_item_by_field:
                bundle[field] = line_item_by_field[field]

        # Druckenmiller and macro traders get price history
        if agent_key == "druckenmiller":
            bundle["prices"] = [
                {"date": p.time, "close": p.close} for p in (prices or [])
            ]

        routed_data[agent_key] = bundle

    # ── Deep research (Anthropic native web search — no Tavily required) ─────
    # Phase 3.5: Claude uses Anthropic's built-in web_search_20250305 tool,
    # which executes searches server-side within the API call.
    # Only ANTHROPIC_API_KEY is required; run_deep_research_agent handles its
    # own absence check and degrades gracefully to "".
    progress.update_status(agent_id, ticker, "Starting deep research (Phase 3.5)")
    state = run_deep_research_agent(state)
    # Ensure keys always exist even if research was skipped
    state["data"].setdefault("deep_research", "")
    state["data"].setdefault("deep_research_sections", {})
    state["data"].setdefault("web_intelligence", {})
    state["data"].setdefault("citation_registry", [])   # populated by _extract_citation_registry

    progress.update_status(agent_id, ticker, "Data routing complete")

    state["data"]["routed_data"] = routed_data

    return state

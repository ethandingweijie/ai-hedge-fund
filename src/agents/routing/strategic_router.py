"""
Phase 2 — Strategic Routing Agent

What it does:
- Pulls 5 years of raw annual financials for each ticker (revenue, net income, OCF, net debt, capex)
- Pulls insider trading activity (last 12 months)
- Asks the LLM to: (a) classify the sector, (b) produce a structured raw scratchpad,
  (c) decide which industry specialist block and data feeds to activate
- Deliberately DOES NOT compute ratios — that's Phase 3's job
  (keeping raw numbers here and derived metrics in the specialist avoids double-work
   and lets each investor agent see the same authoritative raw table)
"""

from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate

import logging

from src.data.models import StrategicRouterOutput
from src.data.sector_profiles import validate_sector
from src.graph.state import AgentState
from src.memory.run_archive import get_routing_cache, save_routing_cache
from src.tools.api import search_line_items, get_insider_trades
from src.utils.llm import call_llm
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state
from src.utils.company_name import fetch_company_name as _fetch_company_name

_log = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are a world-class financial analyst executing Phase 1: Strategic Routing.

Step 1 — Sector Classification:
Classify the ticker into exactly one of:
Consumer | Tech | Biopharma | Telco | Crypto | Energy | Financials | Industrials | RealEstate | Transportation | Materials | Resources | ProfessionalServices | HealthcareServices

Step 2 — Raw Financial Scratchpad:
List every raw figure provided in a structured way. Do NOT compute ratios.
Label clearly by fiscal year (FY2020–FY2024 or latest 5 years available).

Step 3 — Insider Summary:
Summarise net insider buying/selling trend over the last 12 months.

Step 4 — Routing Decision:
Identify:
- which industry specialist block to activate (must match sector)
- which data feeds are most relevant for investor agents
  (e.g. "NRR, CAC payback" for Tech; "NCAV, EPV" for Consumer value plays)

Output JSON with keys: sector, raw_financials, insider_summary, routing_decision.
""".strip()


def run_strategic_router(state: AgentState) -> AgentState:
    """Phase 2: sector classification + raw data scratchpad."""
    agent_id = "strategic_router"
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]
    start_date_12m = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365)
    ).strftime("%Y-%m-%d")

    # Use first ticker as primary (the pipeline is designed for focused single-stock analysis;
    # multi-ticker runs just use the first for the industry brief and routing)
    ticker = state["data"]["tickers"][0]

    # ── Routing cache check ────────────────────────────────────────────────────
    # If we have a fresh cached sector+routing for this ticker (≤30 days old),
    # skip the LLM call and both FMP API fetches entirely.
    _cached = get_routing_cache(ticker, max_age_days=30)
    if _cached:
        progress.update_status(
            agent_id, ticker,
            f"Routing cache hit ({_cached['age_days']:.1f}d old) — "
            f"sector={_cached['sector']} ({_cached['sector_confidence']}) — skipping LLM"
        )
        final_sector       = _cached["sector"]
        sector_confidence  = _cached["sector_confidence"] or "HIGH"
        sector_warning     = _cached["sector_warning"]
        company_name       = _cached["company_name"] or ticker
        raw_financials_out = _cached["raw_financials"] or {}
        routing_decision   = _cached["routing_decision"] or {}
        sector_llm_raw     = _cached["sector_llm_raw"] or final_sector

        state["data"]["sector"]            = final_sector
        state["data"]["sector_llm_raw"]    = sector_llm_raw
        state["data"]["sector_confidence"] = sector_confidence
        state["data"]["sector_warning"]    = sector_warning
        state["data"]["raw_financials"]    = raw_financials_out
        state["data"]["insider_summary"]   = "(cached — not re-fetched)"
        state["data"]["routing_decision"]  = routing_decision
        state["data"]["primary_ticker"]    = ticker
        state["data"]["company_name"]      = company_name

        all_tickers = state["data"]["tickers"]
        sectors: dict[str, str] = {ticker: final_sector}
        for t in all_tickers:
            if t == ticker:
                continue
            t_sector, _, _ = validate_sector(t, final_sector)
            sectors[t] = t_sector
            progress.update_status(agent_id, t, f"Sector (lookup): {t_sector}")
        state["data"]["sectors"] = sectors

        # Profile pre-classification on cache path (mirrors the no-cache path below)
        try:
            from src.data.sector_profiles import (
                get_wacc_profile_for_ticker,
                INDUSTRY_VALUATION_PROFILES,
            )
            profile_names: dict[str, str] = {}
            for t in all_tickers:
                _, _lookup_profile = get_wacc_profile_for_ticker(t)
                if not _lookup_profile:
                    continue
                _sector_key = "RealEstate" if sectors.get(t) == "REIT" else sectors.get(t, "")
                _profile_data = INDUSTRY_VALUATION_PROFILES.get(_sector_key, {}).get(_lookup_profile)
                if _profile_data:
                    profile_names[t] = _lookup_profile
                    progress.update_status(agent_id, t, f"Profile (lookup): {_lookup_profile}")
            if profile_names:
                state["data"]["profile_names"] = profile_names
                if ticker in profile_names:
                    state["data"]["profile_name"] = profile_names[ticker]
        except Exception as _exc:
            _log.warning("[strategic_router cache-path] Profile pre-classification skipped: %s", _exc)

        return state
    # ── Cache miss — run full classification ──────────────────────────────────

    progress.update_status(agent_id, ticker, "Fetching 5-year raw financials")

    line_items = search_line_items(
        ticker=ticker,
        line_items=[
            "revenue",
            "net_income",
            "operating_cash_flow",
            "net_debt",
            "capital_expenditure",
            "free_cash_flow",
            "total_assets",
            "total_liabilities",
        ],
        end_date=end_date,
        period="annual",
        limit=5,
        api_key=api_key,
    )

    progress.update_status(agent_id, ticker, "Fetching insider trades")

    insider_trades = get_insider_trades(
        ticker=ticker,
        end_date=end_date,
        start_date=start_date_12m,
        limit=50,
        api_key=api_key,
    )

    # Build compact financial table for the prompt
    financial_rows = []
    for item in (line_items or []):
        row = {"period": item.report_period}
        for field in [
            "revenue", "net_income", "operating_cash_flow",
            "net_debt", "capital_expenditure", "free_cash_flow",
            "total_assets", "total_liabilities",
        ]:
            val = getattr(item, field, None)
            if val is not None:
                row[field] = val
        financial_rows.append(row)

    # Compact insider summary
    net_buy_value = sum(
        (t.transaction_value or 0)
        for t in (insider_trades or [])
        if (t.transaction_shares or 0) > 0
    )
    net_sell_value = sum(
        abs(t.transaction_value or 0)
        for t in (insider_trades or [])
        if (t.transaction_shares or 0) < 0
    )
    insider_text = (
        f"Net insider buying: ${net_buy_value:,.0f}  |  Net insider selling: ${net_sell_value:,.0f}  "
        f"over last 12 months ({len(insider_trades or [])} transactions)."
    )

    # Resolve the full legal company name to prevent misclassification of
    # ambiguous ticker symbols (e.g. "CHA" = CHAGEE *or* China Telecom ADR).
    company_name    = _fetch_company_name(ticker)
    company_display = f"{company_name} (ticker: {ticker})" if company_name != ticker else ticker

    progress.update_status(agent_id, ticker, f"Running sector classification for {company_display}")

    template = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", (
            "Company: {company}\n\n"
            "Raw annual financials (last 5 years):\n{financials}\n\n"
            "Insider activity:\n{insider}"
        )),
    ])
    prompt = template.invoke({
        "company":   company_display,
        "financials": str(financial_rows),
        "insider":    insider_text,
    })

    result: StrategicRouterOutput = call_llm(
        prompt=prompt,
        pydantic_model=StrategicRouterOutput,
        agent_name=agent_id,
        state=state,
        default_factory=lambda: StrategicRouterOutput(
            sector="Tech",
            raw_financials={},
            insider_summary=insider_text,
            routing_decision={"specialist_block": "Tech", "data_feeds": []},
        ),
    )

    progress.update_status(agent_id, ticker, f"Sector classified: {result.sector}")

    # ── Guardrail: cross-validate LLM sector against hard-coded ticker lookup ──
    # validate_sector() checks TICKER_SECTOR_LOOKUP in sector_profiles.py.
    # If the ticker is known and the LLM disagrees, the lookup wins (allow_override=True).
    # If the ticker is unknown, the LLM classification is used but flagged when
    # it falls into a high-misclassification-risk category.
    final_sector, confidence, sector_warning = validate_sector(
        ticker, result.sector, allow_override=True
    )

    if sector_warning:
        _log.warning(sector_warning)
        progress.update_status(agent_id, ticker, f"⚠ {sector_warning}")

    if final_sector != result.sector:
        progress.update_status(
            agent_id, ticker,
            f"Sector overridden: '{result.sector}' → '{final_sector}' "
            f"(confidence: {confidence})"
        )

    state["data"]["sector"]             = final_sector
    state["data"]["sector_llm_raw"]     = result.sector          # preserve original for audit
    state["data"]["sector_confidence"]  = confidence             # HIGH | MEDIUM | LOW
    state["data"]["sector_warning"]     = sector_warning         # None or warning string
    state["data"]["raw_financials"]     = result.raw_financials
    state["data"]["insider_summary"]    = result.insider_summary
    state["data"]["routing_decision"]   = result.routing_decision
    state["data"]["primary_ticker"]     = ticker
    # Store resolved company name so downstream agents (specialist, deep_research,
    # power_law, pdf_report) can display the correct name without re-fetching.
    state["data"]["company_name"] = company_name

    # ── Persist to routing cache for future runs ──────────────────────────────
    save_routing_cache(
        ticker            = ticker,
        sector            = final_sector,
        sector_llm_raw    = result.sector,
        sector_confidence = confidence,
        sector_warning    = sector_warning,
        company_name      = company_name,
        routing_decision  = result.routing_decision if isinstance(result.routing_decision, dict)
                            else (result.routing_decision.model_dump()
                                  if hasattr(result.routing_decision, "model_dump") else {}),
        raw_financials    = result.raw_financials if isinstance(result.raw_financials, dict)
                            else {},
    )

    # Build per-ticker sector map for multi-ticker runs.
    # Primary ticker is already classified above via LLM + validate_sector.
    # Remaining tickers use validate_sector with the lookup table (no LLM cost).
    all_tickers = state["data"]["tickers"]
    sectors: dict[str, str] = {ticker: final_sector}
    for t in all_tickers:
        if t == ticker:
            continue
        t_sector, _, _ = validate_sector(t, final_sector)  # fallback guess = primary sector
        sectors[t] = t_sector
        progress.update_status(agent_id, t, f"Sector (lookup): {t_sector}")
    state["data"]["sectors"] = sectors

    # ── Profile pre-classification (Tier 2 architecture refactor) ────────────
    # Pre-populate profile_name from TICKER_SECTOR_LOOKUP when an explicit
    # profile override is configured. Eliminates the downstream UnboundLocalError
    # class of bugs where DCF code references profile_name before it's assigned
    # and preserves a single source of truth for sub-profile classification.
    #
    # For tickers WITHOUT a lookup override, state["data"]["profile_names"][t]
    # remains absent and run_dcf_agent falls back to classify_valuation_profile
    # using computed financial metrics — preserving the existing routing for
    # uncovered tickers.
    try:
        from src.data.sector_profiles import (
            get_wacc_profile_for_ticker,
            INDUSTRY_VALUATION_PROFILES,
        )
        profile_names: dict[str, str] = {}
        for t in all_tickers:
            _, _lookup_profile = get_wacc_profile_for_ticker(t)
            if not _lookup_profile:
                # Diagnostic: when a ticker's lookup returns no profile, the DCF
                # agent will classify in-situ. Log this so user can spot
                # unresolved tickers that should be added to TICKER_SECTOR_LOOKUP.
                print(f"  Profile ({t}): (no lookup override) — will classify in DCF")
                continue
            # Verify the profile is actually defined in INDUSTRY_VALUATION_PROFILES
            _sector_key = "RealEstate" if sectors.get(t) == "REIT" else sectors.get(t, "")
            _profile_data = INDUSTRY_VALUATION_PROFILES.get(_sector_key, {}).get(_lookup_profile)
            if _profile_data:
                profile_names[t] = _lookup_profile
                progress.update_status(
                    agent_id, t,
                    f"Profile (lookup): {_lookup_profile}"
                )
                # Visible in Railway stdout (progress.update_status only goes
                # to SSE for the frontend). Prefix with 2 spaces to match the
                # rest of the router's print output style.
                print(f"  Profile ({t}): {_lookup_profile} [lookup, verified]")
            else:
                # Profile key found in lookup but missing from INDUSTRY_VALUATION_PROFILES —
                # mismatch between TICKER_SECTOR_LOOKUP and the valuation-profile table.
                print(
                    f"  Profile ({t}): ⚠ lookup returned '{_lookup_profile}' but not in "
                    f"INDUSTRY_VALUATION_PROFILES[{_sector_key!r}] — SKIPPED"
                )
        if profile_names:
            state["data"]["profile_names"] = profile_names
            # Convenience: primary ticker's profile under singular key
            if ticker in profile_names:
                state["data"]["profile_name"] = profile_names[ticker]
                print(f"  → state.profile_name = '{profile_names[ticker]}' (primary={ticker})")
            else:
                print(f"  → state.profile_names populated for {len(profile_names)} ticker(s), "
                      f"but primary {ticker!r} has no entry")
        else:
            print(f"  → No profile_names written to state "
                  f"(none of the {len(all_tickers)} tickers had lookup+valuation match)")
    except Exception as _exc:
        # Never block strategic_router on profile pre-classification — DCF
        # will classify in-situ as before if this fails.
        # Use logger.exception so the full traceback is recorded — a silent
        # failure here strips profile_name / profile_names from state["data"],
        # which cascades into (a) empty sector panels on the frontend,
        # (b) saas_metrics extractor being gated off (see sector_prompts.py),
        # and (c) DCF valuation fallback paths. Previously this used
        # _log.warning which hid the stacktrace — making it impossible to
        # diagnose why CRM / Tech runs had no profile classification.
        _log.exception(
            "[strategic_router] Profile pre-classification skipped: %s", _exc
        )

    return state

"""CARD_SCHEMAS — catalog of frontend cards the QA agent inspects.

Each schema entry describes ONE frontend card's data contract:
  * applies_when            — predicate gating whether to audit this card
                              for a given (state, ticker)
  * mandatory_state_paths   — dot-notation paths that MUST resolve to a
                              non-empty value. Phase 2 LLM judge fires
                              only when a mandatory path is empty.
  * opportunistic_state_paths — best-effort paths; absence flagged but
                              never escalated to human review
  * qa_prompt_hint          — card-specific guidance the LLM judge uses
                              when classifying the failure
  * schema_version          — bump when ANY field above changes; Phase 8
                              Pattern Analysis filters audits by version
                              so schema drift doesn't poison aggregations

Phase 1 ships ONE card (biopharma_pipeline_rnpv) so the orchestration
plumbing is proven before scaling to the full ~12-card catalog in Phase 7.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# Type alias for `applies_when` predicates. Takes the full pipeline
# `state` dict (NOT state["data"]) and a ticker symbol.
AppliesWhen = Callable[[dict, str], bool]


@dataclass(frozen=True)
class CardSchema:
    """Audit contract for one frontend card."""

    schema_version: int
    applies_when: AppliesWhen
    mandatory_state_paths: tuple[str, ...]
    opportunistic_state_paths: tuple[str, ...]
    qa_prompt_hint: str


# ── Predicate helpers ───────────────────────────────────────────────────────

def _data(state: dict) -> dict:
    """Safe accessor for state['data']."""
    d = state.get("data")
    return d if isinstance(d, dict) else {}


def _sector(state: dict, ticker: str) -> str:
    """Persisted sector for ticker, '' if absent. Older pipeline runs sometimes
    don't populate state.sectors[ticker] even when the analysis succeeded —
    profile is the more reliable signal in those cases."""
    return ((_data(state).get("sectors") or {}).get(ticker) or "")


def _profile(state: dict, ticker: str) -> str:
    """Persisted profile_name for ticker, '' if absent."""
    return ((_data(state).get("profile_names") or {}).get(ticker) or "")


def _matches_any(haystack: str, needles: tuple[str, ...]) -> bool:
    """Case-insensitive substring match of any needle in haystack."""
    h = (haystack or "").lower()
    return any(n.lower() in h for n in needles)


# ── Profile predicates ──────────────────────────────────────────────────────
#
# Each predicate is PERMISSIVE: matches when EITHER sector OR profile_name
# signals the card's domain. This handles older fixture runs where
# state.sectors[ticker] is absent but profile_names[ticker] is populated.
# Meta-Check (Phase 3) separately catches sector/profile inconsistencies.


def _is_biopharma_pre_approval(state: dict, ticker: str) -> bool:
    """Pre-approval / clinical-stage biotech — Pipeline rNPV cohort.
    Large-cap pharma (e.g. NVO, PFE) uses different cards."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    is_biopharma_sector = sector == "Biopharma"
    is_preapproval_profile = _matches_any(profile, ("Pre-approval Biotech", "Clinical-Stage"))
    return is_biopharma_sector and is_preapproval_profile


def _is_biopharma_any(state: dict, ticker: str) -> bool:
    """Any biopharma profile (pre-approval + large-cap). Used for the
    pipeline-table card which can show data for both subtypes."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    if sector == "Biopharma":
        return True
    return _matches_any(profile, (
        "Pre-approval Biotech", "Clinical-Stage Biotech",
        "Large Cap Pharma", "Generics",
    ))


def _is_large_cap_pharma(state: dict, ticker: str) -> bool:
    profile = _profile(state, ticker)
    sector  = _sector(state, ticker)
    return sector == "Biopharma" and "Large Cap Pharma" in profile


def _is_managed_care(state: dict, ticker: str) -> bool:
    """HealthcareServices + Managed Care profile (UNH, MOH, ELV, HUM).
    NOT animal pharma (ZTS misclassification — Meta-Check catches that)."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    sector_match = sector == "HealthcareServices"
    profile_match = "Managed Care" in profile
    return sector_match or profile_match


def _is_tech_saas(state: dict, ticker: str) -> bool:
    """Mature SaaS / Growth SaaS / Cybersecurity / Mission-Critical SaaS.
    NOT hardware-heavy Tech (AAPL hyperscaler) — those use a different card."""
    profile = _profile(state, ticker)
    sector  = _sector(state, ticker)
    is_tech = sector in ("Tech", "Technology")
    saas_profile = _matches_any(profile, ("SaaS", "Software"))
    return is_tech and saas_profile


def _is_bank(state: dict, ticker: str) -> bool:
    """Bank-family profiles ONLY (lenders with CET1 / TBV / NIM).

    EXPLICITLY EXCLUDES other Financials-sector profiles (Asset Manager,
    Insurance, Brokerage, Payment Networks, FinTech) — those share
    sector='Financials' but have completely different KPI panels and
    would generate false-positive flags if grouped under bank_card.

    Profile substring is the primary signal — older fixtures sometimes
    have sectors[ticker] absent but profile_names[ticker] populated.
    """
    profile = _profile(state, ticker)
    # Require explicit bank profile. Pure sector="Financials" without
    # profile is insufficient — we can't safely apply CET1/TBV/NIM
    # mandatory-field checks without knowing it's a bank.
    bank_profiles = (
        "Money Center Bank",
        "Regional Bank",
        "Super-Regional Bank",
        "Investment Bank",
        "EM Bank",          # also matches "EM Bank (Premium)"
        "Bank / Lending Institution",
        "Neo/Challenger",   # neobanks
    )
    if not _matches_any(profile, bank_profiles):
        return False
    # Defensive exclusions to prevent false positives on profiles that
    # happen to contain "Bank" as a substring of something else.
    if "Asset" in profile or "Insurance" in profile or "Investment Banking" in profile:
        # "Brokerage & Investment Banking" is a separate profile from
        # "Investment Bank"; exclude the former.
        return False if "& Investment Banking" in profile else True
    return True


def _is_asset_manager(state: dict, ticker: str) -> bool:
    """Asset Manager / Alt Asset Manager (BLK, BX, APO, KKR, ARES).

    Distinct from banks — fee-on-AUM model, no deposits, no NIM. Cards
    surface AUM, fee rate compression, performance fee mix, dry powder."""
    profile = _profile(state, ticker)
    return "Asset Manager" in profile  # matches both "Asset Manager" and "Alt Asset Manager"


def _is_hyperscaler(state: dict, ticker: str) -> bool:
    """Hyperscaler / Tech Conglomerate (AAPL, MSFT, GOOGL, AMZN, META).

    Distinct from SaaS — capex-heavy, ad/cloud diversified, different
    valuation lens (cloud capacity utilization, AI capex ROI, services
    attach rates). Hardware + cloud + ads, not pure SaaS unit economics."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    if sector not in ("Tech", "Technology"):
        return False
    return "Hyperscaler" in profile or "Tech Conglomerate" in profile


def _is_mature_saas(state: dict, ticker: str) -> bool:
    """Mature SaaS only (INTU, ADBE, CRM-mature). NRR + Rule of 40 + FCF
    margin lens. Excludes Growth SaaS (which emphasizes unit economics +
    runway) and Cybersecurity SaaS (which has a separate platform-attach
    framing)."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    if sector not in ("Tech", "Technology"):
        return False
    return "Mature SaaS" in profile


def _is_growth_saas(state: dict, ticker: str) -> bool:
    """Growth SaaS only (SNOW, DDOG, MDB, CRWD-growth). Unit economics +
    Magic Number + CAC payback lens. Excludes Mature SaaS."""
    sector  = _sector(state, ticker)
    profile = _profile(state, ticker)
    if sector not in ("Tech", "Technology"):
        return False
    return "Growth SaaS" in profile or "Hyper-Growth Platform" in profile


def _is_reit(state: dict, ticker: str) -> bool:
    """REIT (any sub-type: Data Center / Industrial / Residential / Office)."""
    profile = _profile(state, ticker)
    sector  = _sector(state, ticker)
    sector_match = sector in ("RealEstate", "Real Estate", "REITs")
    profile_match = "REIT" in profile
    return sector_match or profile_match


def _applies_always(state: dict, ticker: str) -> bool:
    """Universal cards that fire on every ticker analysis (DCF, scenario,
    decisions, industry brief). Used to catch upstream agent crashes that
    blank these out across all profiles."""
    return True


# ── Card catalog ────────────────────────────────────────────────────────────

CARD_SCHEMAS: dict[str, CardSchema] = {
    # ── Biopharma family ────────────────────────────────────────────────
    "biopharma_pipeline_rnpv": CardSchema(
        schema_version=1,
        applies_when=_is_biopharma_pre_approval,
        mandatory_state_paths=(
            "data.dcf_range.{ticker}",
        ),
        opportunistic_state_paths=(
            "data.dcf_range.{ticker}.base.intrinsic_value",
            "data.dcf_range.{ticker}.bear.intrinsic_value",
            "data.dcf_range.{ticker}.bull.intrinsic_value",
        ),
        qa_prompt_hint=(
            "Pipeline rNPV / Share card for a pre-approval Biopharma. "
            "Needs dcf_range[ticker] with bear/base/bull intrinsic_value. "
            "If dcf_range[ticker]={{}}, the DCF agent likely crashed — "
            "check state.data.dcf_engine_error first."
        ),
    ),

    "biopharma_pipeline_table": CardSchema(
        schema_version=1,
        applies_when=_is_biopharma_any,
        # The MRNA bug: 9 pipeline assets extracted but each one missing
        # peak_sales_usd. The card needs the LIST to be non-empty AND every
        # row's peak_sales_usd populated. We can only check the LIST here;
        # per-row peak_sales is judged by the LLM (path indexing into a
        # list is Phase 8+ territory).
        mandatory_state_paths=(
            "data.pipeline_assets.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Pipeline Assets table for Biopharma. Each pipeline asset row "
            "should include name, indication, phase, AND peak_sales_usd. "
            "MRNA bug pattern: rows present but peak_sales_usd missing from "
            "every row → renders '—' for every Peak column. If the deep "
            "research mentions peak sales projections (e.g. '$1.5B by 2028') "
            "for assets present in the list, classify as EXTRACTOR_DROPPED."
        ),
    ),

    "large_cap_pharma_card": CardSchema(
        schema_version=1,
        applies_when=_is_large_cap_pharma,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(
            "data.dcf_range.{ticker}",
        ),
        qa_prompt_hint=(
            "Large-cap pharma KPI card (e.g. NVO, PFE, MRK). Needs the "
            "framework_metrics_all[ticker] dict populated with margin / "
            "R&D-intensity / mature-pharma KPIs. ZTS (animal pharma) "
            "routes here via TICKER_SECTOR_LOOKUP override — but that's "
            "a Meta-Check concern, not this card's."
        ),
    ),

    # ── Sector-specific cards ──────────────────────────────────────────
    "managed_care_sector_card": CardSchema(
        schema_version=1,
        applies_when=_is_managed_care,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Managed Care card — Medicare Advantage Mix, Medical Loss Ratio "
            "(MLR), member growth, cohort margins. Applies to legitimate "
            "Managed Care insurers (UNH, MOH, ELV, HUM). ZTS misclassification "
            "would route here — Meta-Check should catch that upstream, but "
            "if it doesn't, the judge should return WRONG_PROFILE because "
            "ZTS deep_research won't contain MLR or member data."
        ),
    ),

    "tech_saas_card": CardSchema(
        schema_version=1,
        applies_when=_is_tech_saas,
        mandatory_state_paths=(
            "data.saas_metrics.{ticker}",
        ),
        opportunistic_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        qa_prompt_hint=(
            "Tech SaaS card — NRR, Rule of 40, LTV/CAC, Magic Number, "
            "CAC Payback. Applies to Mature SaaS / Growth SaaS / "
            "Cybersecurity profiles. NOT applicable to hyperscaler "
            "conglomerates (MSFT/AAPL) — they get a different card."
        ),
    ),

    "bank_card": CardSchema(
        schema_version=1,
        applies_when=_is_bank,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Bank card — CET1 ratio, Tangible Book Value (TBV) per share, "
            "Net Interest Margin (NIM), Efficiency Ratio. Applies to Money "
            "Center / Regional / Super-Regional / Investment / EM / Neo "
            "Banks (JPM, BAC, GS, PNC, USB). NOT insurance, NOT asset "
            "managers — those have their own cards with different KPI "
            "families (AUM-based vs deposit-based)."
        ),
    ),

    "asset_manager_card": CardSchema(
        schema_version=1,
        applies_when=_is_asset_manager,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Asset Manager card — AUM (assets under management), management "
            "fee rate, performance fee mix, fee compression trend, dry powder "
            "(for alts), net flows. Applies to BLK / TROW / IVZ (traditional) "
            "and BX / APO / KKR / ARES (alternatives). Distinct from banks: "
            "fee-on-AUM business model, NO deposits, NO NIM."
        ),
    ),

    "hyperscaler_card": CardSchema(
        schema_version=1,
        applies_when=_is_hyperscaler,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(
            "data.saas_metrics.{ticker}",
        ),
        qa_prompt_hint=(
            "Hyperscaler / Tech Conglomerate card — cloud capex intensity, "
            "AI compute spend, capacity utilization, services attach rate, "
            "ads + cloud + hardware revenue mix. Applies to MSFT / AAPL / "
            "GOOGL / AMZN / META. Distinct from SaaS: capex-heavy, "
            "non-pure-software unit economics. Hardware + cloud + ads."
        ),
    ),

    "reit_card": CardSchema(
        schema_version=1,
        applies_when=_is_reit,
        mandatory_state_paths=(
            "data.framework_metrics_all.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "REIT card — NAV per share, P/FFO, P/AFFO, occupancy rate, "
            "same-store NOI growth. Applies to any REIT sub-type (Data "
            "Center / Industrial / Residential / Office)."
        ),
    ),

    # ── Universal cards (apply to every ticker) ────────────────────────
    "dcf_range_summary": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.dcf_range.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "DCF Range Summary — needs dcf_range[ticker] with bear/base/bull "
            "intrinsic_value. This card universally applies (all profiles). "
            "Empty dcf_range[ticker] is the most common breakage pattern — "
            "DCF agent crashed. If state.data.dcf_engine_error is also "
            "present, the verdict should be GENUINELY_ABSENT (system "
            "error, not extraction issue). Most non-extraction failures "
            "in our pipeline trip this card."
        ),
    ),

    "scenario_analysis_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.scenario_analysis.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Scenario Analysis — bear/base/bull fair values. Computed by "
            "the scenario engine downstream of DCF. Missing usually means "
            "the upstream agent didn't run or crashed silently."
        ),
    ),

    "decisions_panel": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.decisions.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Decisions panel — recommended_action + reasoning. The portfolio "
            "manager agent produces this. Missing means PM agent crashed or "
            "the ticker was filtered out before PM ran."
        ),
    ),

    # ── Universal sidecar cards (Phase 7+ expansion) ─────────────────────
    "power_law_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.power_law_analysis.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Power Law card — scaling/winner-take-all radar with axes like "
            "scale economies, network effects, switching costs. Missing "
            "means the power_law_analyst agent crashed for this ticker."
        ),
    ),

    "value_trap_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.value_trap_analysis.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Value Trap Audit card — 6-point checklist (capital allocation, "
            "FCF quality, etc.). Missing means value_trap_agent didn't run."
        ),
    ),

    "agent_signals_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.analyst_signals",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Agent Signals card — multi-agent fan-out (Buffett, Munger, "
            "Druckenmiller, etc.). Missing means the entire investor "
            "agents phase crashed. NB: analyst_signals is keyed by agent "
            "first, then ticker — we just check the top-level dict has "
            "at least one agent's output."
        ),
    ),

    "industry_intelligence_brief_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.industry_brief",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Industry Intelligence Brief — 20-25KB sector-level narrative "
            "from the industry_specialist agent. Missing means the "
            "specialist crashed or the run never reached that phase. The "
            "ticker key is NOT in this path (industry_brief is a global "
            "string, not per-ticker)."
        ),
    ),

    "citation_registry_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.citation_registry",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Citation Registry — structured source list (URLs, dates, "
            "quotes) extracted from deep_research. Missing means the "
            "citation extractor crashed; the report will lack inline "
            "footnote links."
        ),
    ),

    "financial_statements_card": CardSchema(
        schema_version=1,
        applies_when=_applies_always,
        mandatory_state_paths=(
            "data.raw_financials.{ticker}",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Financial Statements card — 5-year income statement / "
            "balance sheet / cash flow from FMP. Missing means FMP "
            "returned no historicals (private firm, brand-new IPO, or "
            "FMP plan restriction)."
        ),
    ),

    # ── Sector-specific narrative commentary cards (Phase 7+) ──────────
    # All commentary cards share the same mandatory state path
    # (`data.deep_research_sections.{ticker}` or the global 2c/2d/2e/2f
    # sections). Predicate gates per profile; the qa_prompt_hint tells the
    # judge which subsection to look for in the narrative.

    "bank_loan_book_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_bank,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Bank Loan Book commentary — qualitative analysis of loan "
            "composition, NPL trends, credit quality, sector concentration. "
            "Lives in deep_research_sections section 2C or 2D as a "
            "'Loan Book' subsection. If missing, the deep_research narrative "
            "either lacks this subsection or used a different heading."
        ),
    ),

    "bank_nim_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_bank,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Bank NIM commentary — net interest margin trajectory, "
            "deposit beta, rate sensitivity. Lives in deep_research_sections "
            "2C/2D under 'Net Interest Margin' or similar heading."
        ),
    ),

    "bank_pre_provision_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_bank,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Bank Pre-Provision Operating Profit commentary — operating "
            "leverage, efficiency ratio trend, fee income mix."
        ),
    ),

    "biopharma_patent_cliff_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_biopharma_any,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Biopharma Patent Cliff / Legacy Decline commentary — LOE "
            "(loss of exclusivity) schedule, biosimilar threats, key "
            "product revenue at risk. Especially material for large-cap "
            "pharma (PFE, MRK) where the cliff dominates valuation."
        ),
    ),

    "biopharma_rd_productivity_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_biopharma_any,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Biopharma R&D Productivity commentary — pipeline depth, "
            "expected ROI per program, comparison to industry benchmarks, "
            "trial success-rate assumptions."
        ),
    ),

    "reit_dpu_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_reit,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "REIT Distribution per Share (DPU/DPS) commentary — payout "
            "ratio sustainability, AFFO coverage, historical growth, "
            "tax-shelter mechanics."
        ),
    ),

    "reit_npi_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_reit,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "REIT Net Property Income (NPI) commentary — same-store NOI "
            "growth, occupancy trends, lease rollover risk, property-type "
            "concentration."
        ),
    ),

    "tech_hyperscaler_ai_capex_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_hyperscaler,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Hyperscaler AI Capex ROI commentary — cloud + AI capex "
            "intensity, ROI on data center build-out, capacity "
            "utilization, monetization timing. Applies to MSFT / GOOGL / "
            "AMZN / META; AAPL has it in a slightly different form "
            "(services + AI services attach)."
        ),
    ),

    "tech_hyperscaler_regulatory_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_hyperscaler,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Hyperscaler Regulatory Overhang commentary — antitrust risk, "
            "DOJ / EU actions, App Store policy changes, ad-tech "
            "structural risks. Material for all major hyperscalers."
        ),
    ),

    "tech_mature_saas_nrr_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_mature_saas,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Mature SaaS NRR Trajectory commentary — Net Revenue "
            "Retention historical + forward, expansion vs. contraction "
            "drivers, cohort behavior over time. Applies to INTU, ADBE, "
            "WDAY, etc."
        ),
    ),

    "tech_mature_saas_ai_monetization_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_mature_saas,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Mature SaaS AI Monetization Strategy commentary — pricing "
            "uplift from AI features, attach rates of premium AI SKUs, "
            "competitive moat vs. AI-native challengers."
        ),
    ),

    "tech_growth_saas_unit_economics_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_growth_saas,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Growth SaaS Unit Economics Sustainability commentary — "
            "Magic Number, CAC payback, LTV/CAC, gross retention. "
            "Critical for the collapse-risk lens on pre-FCF SaaS firms."
        ),
    ),

    "tech_growth_saas_path_to_profitability_commentary": CardSchema(
        schema_version=1,
        applies_when=_is_growth_saas,
        mandatory_state_paths=(
            "data.deep_research_sections",
        ),
        opportunistic_state_paths=(),
        qa_prompt_hint=(
            "Growth SaaS Path to Profitability commentary — runway, "
            "expected operating-margin inflection year, SBC dilution "
            "trajectory, free cash flow milestone targets."
        ),
    ),

    # Total cards: 27 (matches frontend's visible card landscape, up from 12)
    #   - 1 from Phase 1 (biopharma_pipeline_rnpv)
    #   - 9 from Phase 7 (table, large pharma, managed care, saas, bank,
    #     reit, dcf_range_summary, scenario_analysis_card, decisions_panel)
    #   - 2 from Phase 7+ predicate patches (asset_manager_card,
    #     hyperscaler_card) after the false-positive audit
    #   - 6 universal sidecar cards (power_law, value_trap, agent_signals,
    #     industry_brief, citation_registry, financial_statements)
    #   - 9 sector-specific narrative commentary cards (bank x3,
    #     biopharma x2, reit x2, tech hyperscaler x2, tech mature_saas x2,
    #     tech growth_saas x2 — counted 27 total when split-out)
    # Future expansions (insurance, payment_networks, MedTech, CDMO,
    # valuation_anchors, per-asset peak tracking, sub-sector cards for
    # Energy/Mining/Industrials) deferred until production patterns
    # indicate which are highest-priority via Layer B aggregation.
}

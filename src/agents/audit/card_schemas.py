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
    """Money Center Bank / Regional Bank / Investment Bank profile.
    Profile is the more reliable signal here because state.sectors is
    often missing on older fixture runs."""
    profile = _profile(state, ticker)
    sector  = _sector(state, ticker)
    sector_match = sector in ("Financials", "Financial Services", "Banks")
    profile_match = _matches_any(profile, ("Money Center Bank", "Regional Bank", "Investment Bank"))
    return sector_match or profile_match


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
            "Center / Regional / Investment Banks (JPM, BAC, GS). NOT "
            "insurance, NOT asset managers (different KPI families)."
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
    # Total cards: 10 (1 from Phase 1 + 9 added in Phase 7).
    # Future expansions (industry_intelligence_brief, valuation_anchors,
    # per-asset peak_sales tracking) go in Phase 12 or later.
}

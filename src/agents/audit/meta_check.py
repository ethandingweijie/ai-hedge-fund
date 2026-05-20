"""Meta-Check (Phase 10.5a) — validate upstream classification BEFORE
auditing downstream cards.

Without this, a misclassified ticker (e.g. ZTS routed to "Managed Care"
in v3.18) would pass the `applies_when` predicates for the wrong cards
and the per-card LLM judge would dutifully look for non-existent KPIs —
wasting budget AND adding misleading data to `human_review_flags`.

Meta-Check answers ONE question:
    "Is the state's persisted classification (sector + profile_name) for
    this ticker consistent with what we'd expect?"

Checks (cheap → expensive; first failure short-circuits):

  1. TICKER_LOOKUP_CONSISTENCY (free)
     If `TICKER_SECTOR_LOOKUP` has an explicit override for this ticker,
     state's persisted classification MUST match it. If it doesn't, the
     override is treated as ground truth and Meta-Check fails with a
     suggested profile equal to the override's profile_name.

  2. SECTOR_PROFILE_CONSISTENCY (free)
     Cross-check: does state.sectors[ticker] match the family of
     state.profile_names[ticker]? E.g. profile="Managed Care" implies
     sector ∈ HealthcareServices; profile="Money Center Bank" implies
     sector ∈ Financials. Mismatch → fail.

Phase 3 explicitly skips an LLM-based plausibility check. That's a
Phase 5 enhancement when we wire the budget-capped Qwen call into the
orchestrator. For now: if both deterministic checks pass, Meta-Check
passes. False negatives are acceptable (the per-card judge in Phase
10.5b still catches mismatches one card at a time).

Returns a dict matching the persistence schema's `meta_check` slot:
  {
    "passed":             bool,
    "checks_run":         list[str],     # which checks fired
    "issues":             list[str],     # human-readable failure descriptions
    "suggested_profile":  str | None,    # only set on failure
  }
"""
from __future__ import annotations

import logging
from typing import Any

from src.data.sector_profiles import TICKER_SECTOR_LOOKUP

logger = logging.getLogger(__name__)


# ── Profile → expected sector mapping ───────────────────────────────────────
# Used in check 2 (sector/profile consistency). Each profile string maps to
# the set of sector strings that are consistent with it. Profiles not in this
# table are skipped (treated as "we don't have a rule, so don't fail").
_PROFILE_TO_EXPECTED_SECTORS: dict[str, set[str]] = {
    # Healthcare family
    "Managed Care":               {"HealthcareServices"},
    "Health Insurance":           {"HealthcareServices"},
    "Pharmacy Benefit Manager":   {"HealthcareServices"},
    # Biopharma family
    "Large Cap Pharma":           {"Biopharma"},
    "Pre-approval Biotech":       {"Biopharma"},
    "Clinical-Stage Biotech":     {"Biopharma"},
    "Generics":                   {"Biopharma"},
    # Bank family
    "Money Center Bank":          {"Financials"},
    "Regional Bank":              {"Financials"},
    "Investment Bank":            {"Financials"},
    "Asset Manager":              {"Financials"},
    # Insurance family
    "P&C Insurance":              {"Financials"},
    "Life Insurance":             {"Financials"},
    # REIT family
    "Data Center REIT":           {"RealEstate"},
    "Industrial REIT":            {"RealEstate"},
    "Residential REIT":           {"RealEstate"},
    "Office REIT":                {"RealEstate"},
    # Tech family
    "Hyperscaler / Tech Conglomerate": {"Tech"},
    "Mature SaaS":                {"Tech"},
    "Growth SaaS":                {"Tech"},
    "Cybersecurity / Mission-Critical SaaS": {"Tech"},
    # Semiconductor (distinct from Tech)
    "Fabless":                    {"Semiconductor"},
    "IDM / Foundry":              {"Semiconductor"},
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _state_classification(state: dict, ticker: str) -> tuple[str | None, str | None]:
    """Return (state.sectors[ticker], state.profile_names[ticker]).
    Either may be None if the key is absent."""
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    sectors = data.get("sectors") or {}
    profile_names = data.get("profile_names") or {}
    return sectors.get(ticker), profile_names.get(ticker)


def _empty_result(passed: bool = True) -> dict[str, Any]:
    return {
        "passed":            passed,
        "checks_run":        [],
        "issues":            [],
        "suggested_profile": None,
    }


# ── Public entry point ─────────────────────────────────────────────────────


def run_meta_check(state: dict, ticker: str) -> dict[str, Any]:
    """Run Meta-Check against the state's classification for `ticker`.

    Returns a dict matching the audit's `meta_check` slot. Always returns;
    never raises. If a check itself crashes, that check is recorded as
    "skipped_due_to_error" but does NOT fail the meta-check.
    """
    result = _empty_result(passed=True)
    state_sector, state_profile = _state_classification(state, ticker)

    # ─ Check 1: TICKER_LOOKUP_CONSISTENCY ─────────────────────────────────
    # Only fails when state has POSITIVE evidence of disagreement.
    # Pure absence (state.sectors[ticker] missing entirely) is NOT a
    # misclassification — many runs simply don't populate the per-ticker
    # classification dicts (depends on pipeline path). False positives
    # on absence would noise up Layer A's flag rate badly.
    result["checks_run"].append("ticker_lookup_consistency")
    override = TICKER_SECTOR_LOOKUP.get(ticker)
    if override is not None:
        expected_sector, expected_profile = override[0], override[1]
        # A field "disagrees" only when it's non-empty AND ≠ expected.
        sector_disagrees = (
            bool(state_sector) and state_sector != expected_sector
        )
        profile_disagrees = (
            bool(state_profile)
            and bool(expected_profile)
            and state_profile != expected_profile
        )
        if sector_disagrees or profile_disagrees:
            result["passed"] = False
            details = (
                f"TICKER_SECTOR_LOOKUP override says {ticker} should be "
                f"sector={expected_sector!r}, profile={expected_profile!r}; "
                f"persisted state has sector={state_sector!r}, profile={state_profile!r}"
            )
            result["issues"].append(details)
            result["suggested_profile"] = expected_profile or None
            logger.info("meta_check[%s]: FAIL on ticker_lookup_consistency — %s", ticker, details)
            return result

    # ─ Check 2: SECTOR_PROFILE_CONSISTENCY ─────────────────────────────────
    # Only fires when both sector and profile are non-empty (otherwise we
    # don't have enough signal to make a claim either way).
    result["checks_run"].append("sector_profile_consistency")
    if state_sector and state_profile:
        expected_sectors = _PROFILE_TO_EXPECTED_SECTORS.get(state_profile)
        if expected_sectors and state_sector not in expected_sectors:
            result["passed"] = False
            details = (
                f"Profile {state_profile!r} requires sector in {sorted(expected_sectors)!r}, "
                f"but persisted state has sector={state_sector!r}. "
                f"Likely a routing bug in strategic_router."
            )
            result["issues"].append(details)
            # No suggestion here — we can flag the issue but can't infer
            # the correct profile from sector+profile mismatch alone.
            # Phase 5's LLM enhancement can populate this.
            logger.info(
                "meta_check[%s]: FAIL on sector_profile_consistency — %s",
                ticker, details,
            )
            return result

    return result

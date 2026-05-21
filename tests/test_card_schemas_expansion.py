"""Phase 7 — Card schema expansion (9 new cards) validation against the
Phase 0 fixture set.

Gate per plan:
  "All 12 cards' applies_when predicates fire correctly on the 10-fixture
   eval set. No false applies (e.g. bank_card matching a tech ticker)."

This file documents the EXPECTED applies_when truth table per (card × fixture)
pair AND asserts mandatory-path behavior. Mandatory-path PASS/FAIL outcomes
depend on which fixture vintage was pulled — we capture observed reality.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.audit.card_qa_agent import run_card_qa_agent
from src.agents.audit.card_schemas import CARD_SCHEMAS

FIXTURE_DIR = Path(__file__).resolve().parent / "audit" / "fixtures"


def _load_fixture(filename: str) -> dict:
    with (FIXTURE_DIR / filename).open(encoding="utf-8") as f:
        return json.load(f)


# ── Expected applies_when truth table ──────────────────────────────────────
#
# Maps (fixture_file, ticker) → set of card names that should apply.
# Built from the cross-fixture state-shape probe + each card's predicate logic.
#
# IMPORTANT: this is the *primary* Phase 7 gate. False positives or
# false negatives here mean the predicates are wrong and the LLM judge
# would waste budget on cards that shouldn't apply.

# Universal cards fire on every ticker (regardless of classification).
# These get unioned into each fixture's expected set below.
_UNIVERSAL = {
    "dcf_range_summary",
    "scenario_analysis_card",
    "decisions_panel",
    "power_law_card",
    "value_trap_card",
    "agent_signals_card",
    "industry_intelligence_brief_card",
    "citation_registry_card",
    "financial_statements_card",
}

EXPECTED_APPLIES: dict[str, set[str]] = {
    # MRNA — Biopharma + Pre-approval Biotech
    "MRNA__0182e126.json": _UNIVERSAL | {
        "biopharma_pipeline_rnpv",
        "biopharma_pipeline_table",
        "biopharma_patent_cliff_commentary",
        "biopharma_rd_productivity_commentary",
    },
    "MRNA__70b7d8b1.json": _UNIVERSAL | {
        "biopharma_pipeline_rnpv",
        "biopharma_pipeline_table",
        "biopharma_patent_cliff_commentary",
        "biopharma_rd_productivity_commentary",
    },

    # ZTS — persisted MISCLASSIFICATION as HealthcareServices/Managed Care.
    # Meta-Check fails → cards_inspected becomes []. The truth-table here
    # describes what WOULD apply absent the short-circuit (predicates only),
    # but the orchestrator's actual cards_inspected stays empty by design.
    "ZTS__b91aa9b4.json": _UNIVERSAL | {
        "managed_care_sector_card",
    },

    # MSFT — Tech + Hyperscaler/Tech Conglomerate
    "MSFT__f616514d.json": _UNIVERSAL | {
        "hyperscaler_card",
        "tech_hyperscaler_ai_capex_commentary",
        "tech_hyperscaler_regulatory_commentary",
    },

    # JPM — profile="Money Center Bank" (sectors[JPM] absent in fixture)
    "JPM__f58865fb.json": _UNIVERSAL | {
        "bank_card",
        "bank_loan_book_commentary",
        "bank_nim_commentary",
        "bank_pre_provision_commentary",
    },

    # MOH — profile="Managed Care" (sectors absent)
    "MOH__cebfa77e.json": _UNIVERSAL | {
        "managed_care_sector_card",
    },

    # DLR — sectors + profile both missing → universals only
    "DLR__e4ecbe13.json": _UNIVERSAL,

    # NVO — sectors + profile both missing → universals only
    "NVO__3a5d11f5.json": _UNIVERSAL,

    # AAPL — sectors + profile both missing → universals only
    "AAPL__8a81be97.json": _UNIVERSAL,

    # INTU — Tech + Mature SaaS → tech_saas_card + mature_saas commentaries
    "INTU__869c6dfe.json": _UNIVERSAL | {
        "tech_saas_card",
        "tech_mature_saas_nrr_commentary",
        "tech_mature_saas_ai_monetization_commentary",
    },
}


@pytest.mark.parametrize("fixture_file,ticker", [
    ("MRNA__0182e126.json", "MRNA"),
    ("MRNA__70b7d8b1.json", "MRNA"),
    ("ZTS__b91aa9b4.json",  "ZTS"),
    ("MSFT__f616514d.json", "MSFT"),
    ("JPM__f58865fb.json",  "JPM"),
    ("MOH__cebfa77e.json",  "MOH"),
    ("DLR__e4ecbe13.json",  "DLR"),
    ("NVO__3a5d11f5.json",  "NVO"),
    ("AAPL__8a81be97.json", "AAPL"),
    ("INTU__869c6dfe.json", "INTU"),
])
def test_applies_when_matches_expected_truth_table(fixture_file, ticker):
    """Phase 7 PRIMARY GATE: each fixture triggers exactly the expected set of cards.

    Two failure modes this catches:
      1. False positive: a card applies when it shouldn't (e.g. bank_card on a
         pharma ticker) → would waste LLM budget on irrelevant audits
      2. False negative: a card doesn't apply when it should (e.g. tech_saas_card
         on INTU) → misses a real card-quality check
    """
    fixture = _load_fixture(fixture_file)
    audit = run_card_qa_agent(fixture, ticker)
    actual_applies = {c["card"] for c in audit["cards_inspected"]}

    # ZTS's meta_check fails → cards_inspected is empty by design (short-circuit).
    # The expected set is what WOULD apply if we ran in audit-only mode without
    # meta-check short-circuit. So we test this case via the schema directly.
    if ticker == "ZTS" and audit.get("meta_check", {}).get("passed") is False:
        # Verify the predicates would apply if meta-check hadn't tripped
        from src.agents.audit.card_schemas import CARD_SCHEMAS as _S
        would_apply = {n for n, s in _S.items() if s.applies_when(fixture, ticker)}
        expected = EXPECTED_APPLIES[fixture_file]
        assert would_apply == expected, (
            f"[{ticker}] ZTS predicate-only check: expected {expected}, got {would_apply}"
        )
        # And the orchestrator correctly short-circuits to no cards
        assert actual_applies == set(), (
            f"[{ticker}] meta-check fail must short-circuit cards_inspected"
        )
        return

    expected = EXPECTED_APPLIES[fixture_file]
    assert actual_applies == expected, (
        f"[{ticker}] applies mismatch:\n"
        f"  expected:  {sorted(expected)}\n"
        f"  actual:    {sorted(actual_applies)}\n"
        f"  extra:     {sorted(actual_applies - expected)}\n"
        f"  missing:   {sorted(expected - actual_applies)}"
    )


# ── Card-level: no false-positive sanity checks ───────────────────────────


def test_bank_card_never_applies_to_pharma_tickers():
    """Cross-card false-positive guard: bank_card MUST NOT fire on a biopharma
    fixture, even with a synthetic state that has biopharma classification."""
    state = {
        "data": {
            "sectors":       {"FAKE": "Biopharma"},
            "profile_names": {"FAKE": "Large Cap Pharma"},
        }
    }
    audit = run_card_qa_agent(state, "FAKE")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "bank_card" not in cards


def test_tech_saas_card_never_applies_to_bank():
    state = {
        "data": {
            "sectors":       {"FAKE": "Financials"},
            "profile_names": {"FAKE": "Money Center Bank"},
        }
    }
    audit = run_card_qa_agent(state, "FAKE")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "tech_saas_card" not in cards
    assert "bank_card" in cards  # but the correct card DOES apply


def test_reit_card_never_applies_to_managed_care():
    state = {
        "data": {
            "sectors":       {"FAKE": "HealthcareServices"},
            "profile_names": {"FAKE": "Managed Care"},
        }
    }
    audit = run_card_qa_agent(state, "FAKE")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "reit_card" not in cards
    assert "managed_care_sector_card" in cards


def test_bank_card_does_not_apply_to_asset_managers():
    """Phase 7+ false-positive guard. Asset Managers and Banks BOTH share
    sector='Financials' — the OLD _is_bank predicate would have wrongly
    flagged Asset Managers under bank_card. The fix: bank requires a
    bank-specific profile substring."""
    state = {
        "data": {
            "sectors":       {"BLK": "Financials"},
            "profile_names": {"BLK": "Asset Manager"},
        }
    }
    audit = run_card_qa_agent(state, "BLK")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "bank_card" not in cards, (
        "Asset Manager wrongly matched bank_card — predicate is too loose"
    )
    # The correct card DOES apply
    assert "asset_manager_card" in cards


def test_bank_card_does_not_apply_to_insurance():
    """Insurance is also Financials sector; must not get bank_card."""
    state = {
        "data": {
            "sectors":       {"AIG": "Financials"},
            "profile_names": {"AIG": "Insurance"},
        }
    }
    audit = run_card_qa_agent(state, "AIG")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "bank_card" not in cards
    # No insurance_card yet (deferred), so audit just gets universals
    assert "asset_manager_card" not in cards


def test_asset_manager_card_applies_to_alt_asset_manager_too():
    """'Alt Asset Manager' (BX, APO, KKR) shares the asset_manager card
    because the audit scope is the same (AUM, fees, dry powder)."""
    for profile in ("Asset Manager", "Alt Asset Manager"):
        state = {
            "data": {
                "sectors":       {"X": "Financials"},
                "profile_names": {"X": profile},
            }
        }
        audit = run_card_qa_agent(state, "X")
        cards = {c["card"] for c in audit["cards_inspected"]}
        assert "asset_manager_card" in cards, (
            f"asset_manager_card did not match profile={profile!r}"
        )


def test_hyperscaler_card_applies_to_msft_aapl_profile():
    state = {
        "data": {
            "sectors":       {"MSFT": "Tech"},
            "profile_names": {"MSFT": "Hyperscaler / Tech Conglomerate"},
        }
    }
    audit = run_card_qa_agent(state, "MSFT")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "hyperscaler_card" in cards
    # Hyperscaler is NOT SaaS — saas card should NOT fire
    assert "tech_saas_card" not in cards


def test_tech_saas_card_matches_all_three_saas_variants():
    """Growth SaaS, Mature SaaS, Cybersecurity SaaS all share the QA card
    (NRR / Rule of 40 / LTV-CAC). Predicate must catch all three."""
    for profile in (
        "Growth SaaS",
        "Mature SaaS",
        "Cybersecurity / Mission-Critical SaaS",
    ):
        state = {
            "data": {
                "sectors":       {"X": "Tech"},
                "profile_names": {"X": profile},
            }
        }
        audit = run_card_qa_agent(state, "X")
        cards = {c["card"] for c in audit["cards_inspected"]}
        assert "tech_saas_card" in cards, (
            f"tech_saas_card missed profile={profile!r} despite SaaS substring"
        )


def test_super_regional_bank_now_matches_bank_card():
    """Phase 7 patch: 'Super-Regional Bank' was previously missed by the
    bank predicate. Verify the fix."""
    state = {
        "data": {
            "sectors":       {"PNC": "Financials"},
            "profile_names": {"PNC": "Super-Regional Bank"},
        }
    }
    audit = run_card_qa_agent(state, "PNC")
    cards = {c["card"] for c in audit["cards_inspected"]}
    assert "bank_card" in cards


def test_em_bank_matches_bank_card():
    """EM Bank and EM Bank (Premium) both route to bank_card."""
    for profile in ("EM Bank", "EM Bank (Premium)"):
        state = {
            "data": {
                "sectors":       {"X": "Financials"},
                "profile_names": {"X": profile},
            }
        }
        audit = run_card_qa_agent(state, "X")
        cards = {c["card"] for c in audit["cards_inspected"]}
        assert "bank_card" in cards, f"bank_card missed profile={profile!r}"


# ── Schema-shape sanity ────────────────────────────────────────────────────


def test_all_schemas_have_version_one():
    """Phase 7 introduces new schemas. All should ship at version 1."""
    for name, schema in CARD_SCHEMAS.items():
        assert schema.schema_version == 1, (
            f"Card {name} has schema_version={schema.schema_version}, expected 1"
        )


def test_all_schemas_have_nonempty_prompt_hint():
    """A blank qa_prompt_hint would tell the LLM nothing about the card —
    judge accuracy would tank. Guard against empty hints landing accidentally."""
    for name, schema in CARD_SCHEMAS.items():
        assert len(schema.qa_prompt_hint) >= 50, (
            f"Card {name} has too-short qa_prompt_hint ({len(schema.qa_prompt_hint)} chars)"
        )


def test_all_schemas_have_mandatory_paths():
    """Every card must declare at least one mandatory path or the audit
    becomes a no-op."""
    for name, schema in CARD_SCHEMAS.items():
        assert len(schema.mandatory_state_paths) >= 1, (
            f"Card {name} has no mandatory_state_paths"
        )


def test_expected_card_count_after_phase_7_expansion():
    """Phase 7 final scope post-expansion to user-requested 20+ coverage:
       1 (phase 1)
     + 9 (phase 7 base)
     + 2 (asset_manager + hyperscaler patches)
     + 6 universal sidecars (power_law, value_trap, agent_signals,
        industry_brief, citation_registry, financial_statements)
     + 13 sector commentary cards (bank x3, biopharma x2, reit x2,
        hyperscaler x2, mature_saas x2, growth_saas x2)
     = 31 total.

    If this drifts unexpectedly downward, a card was removed (regression).
    If it drifts upward, a card was added without updating this count."""
    assert len(CARD_SCHEMAS) == 31

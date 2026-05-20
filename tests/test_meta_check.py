"""Tests for src/agents/audit/meta_check.py (Phase 10.5a).

Phase 3 gate: ZTS fixture → Meta-Check fails with suggested 'Large Cap Pharma'.
              MOH fixture → Meta-Check passes (genuine Managed Care).
              MRNA fixture → Meta-Check passes (genuine Pre-approval Biotech).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.audit.meta_check import (
    _PROFILE_TO_EXPECTED_SECTORS,
    _state_classification,
    run_meta_check,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "audit" / "fixtures"


def _load_fixture(filename: str) -> dict:
    with (FIXTURE_DIR / filename).open(encoding="utf-8") as f:
        return json.load(f)


# ── Unit tests ──────────────────────────────────────────────────────────────


def test_state_classification_extracts_both_fields():
    state = {"data": {
        "sectors":       {"AAPL": "Tech"},
        "profile_names": {"AAPL": "Hyperscaler / Tech Conglomerate"},
    }}
    sector, profile = _state_classification(state, "AAPL")
    assert sector == "Tech"
    assert profile == "Hyperscaler / Tech Conglomerate"


def test_state_classification_returns_none_on_missing_keys():
    sector, profile = _state_classification({"data": {}}, "AAPL")
    assert sector is None
    assert profile is None


def test_meta_check_passes_with_no_data_and_no_override():
    """A ticker not in TICKER_SECTOR_LOOKUP, with no sector/profile assigned,
    should NOT fail meta-check. Phase 3 only fires when it has positive
    evidence of a mismatch; absence of data is silently accepted."""
    result = run_meta_check({"data": {}}, "UNKNOWN_TICKER_XYZ")
    assert result["passed"] is True
    assert result["suggested_profile"] is None
    assert "ticker_lookup_consistency" in result["checks_run"]


def test_meta_check_passes_when_classification_matches_lookup():
    """If state matches TICKER_SECTOR_LOOKUP exactly, check passes."""
    state = {"data": {
        "sectors":       {"MSFT": "Tech"},
        "profile_names": {"MSFT": "Hyperscaler / Tech Conglomerate"},
    }}
    result = run_meta_check(state, "MSFT")
    assert result["passed"] is True


def test_meta_check_fails_on_zts_misclassification():
    """The canonical Phase 3 case: ZTS persisted state has the OLD wrong
    classification (Managed Care). TICKER_SECTOR_LOOKUP now overrides to
    Large Cap Pharma. Meta-Check should fail and suggest the override."""
    state = {"data": {
        "sectors":       {"ZTS": "HealthcareServices"},
        "profile_names": {"ZTS": "Managed Care"},
    }}
    result = run_meta_check(state, "ZTS")
    assert result["passed"] is False
    assert result["suggested_profile"] == "Large Cap Pharma"
    assert any("ZTS" in i and "Large Cap Pharma" in i for i in result["issues"])


def test_meta_check_fails_on_profile_sector_mismatch_without_override():
    """A ticker NOT in TICKER_SECTOR_LOOKUP but with internally-inconsistent
    sector + profile should still fail check 2 (sector_profile_consistency)."""
    state = {"data": {
        "sectors":       {"FAKE": "Biopharma"},
        "profile_names": {"FAKE": "Money Center Bank"},   # makes no sense
    }}
    result = run_meta_check(state, "FAKE")
    assert result["passed"] is False
    assert "Money Center Bank" in result["issues"][0]
    # No suggestion because there's no override and we can't infer correct profile
    assert result["suggested_profile"] is None


def test_meta_check_skips_consistency_check_when_profile_has_no_rule():
    """A profile we don't have a rule for (e.g. a new sector profile not yet
    in _PROFILE_TO_EXPECTED_SECTORS) shouldn't FAIL meta-check just because
    we lack data on it. Silent pass is the safe default."""
    state = {"data": {
        "sectors":       {"FAKE": "RareSectorXYZ"},
        "profile_names": {"FAKE": "Some Brand New Profile"},
    }}
    result = run_meta_check(state, "FAKE")
    assert result["passed"] is True


# ── Fixture-driven Phase 3 gate tests ──────────────────────────────────────


def test_zts_fixture_fails_meta_check_with_correct_suggestion():
    """Phase 3 Gate: the actual production ZTS fixture must trigger
    Meta-Check failure and suggest Large Cap Pharma."""
    fixture = _load_fixture("ZTS__b91aa9b4.json")
    result = run_meta_check(fixture, "ZTS")
    assert result["passed"] is False
    assert result["suggested_profile"] == "Large Cap Pharma"


def test_mrna_fixture_passes_meta_check():
    """Phase 3 Gate: MRNA's persisted classification is correct
    (Biopharma + Pre-approval Biotech). Should pass."""
    fixture = _load_fixture("MRNA__0182e126.json")
    result = run_meta_check(fixture, "MRNA")
    assert result["passed"] is True, (
        f"MRNA Meta-Check unexpectedly failed: issues={result['issues']}"
    )


def test_moh_fixture_passes_meta_check():
    """Phase 3 Gate: MOH is GENUINELY Managed Care (Molina Healthcare).
    Critical contrast to ZTS: same profile string, but legit. Meta-Check
    MUST distinguish."""
    fixture = _load_fixture("MOH__cebfa77e.json")
    result = run_meta_check(fixture, "MOH")
    assert result["passed"] is True, (
        f"MOH Meta-Check unexpectedly failed: issues={result['issues']}"
    )


@pytest.mark.parametrize("fixture_file,ticker", [
    ("AAPL__8a81be97.json", "AAPL"),
    ("MSFT__f616514d.json", "MSFT"),
    ("JPM__f58865fb.json",  "JPM"),
    ("DLR__e4ecbe13.json",  "DLR"),
    ("NVO__3a5d11f5.json",  "NVO"),
    ("INTU__869c6dfe.json", "INTU"),
    ("MRNA__70b7d8b1.json", "MRNA"),
])
def test_healthy_fixtures_all_pass_meta_check(fixture_file, ticker):
    """All non-ZTS fixtures should pass Meta-Check. Confirms zero false
    positives across the eval set."""
    fixture = _load_fixture(fixture_file)
    result = run_meta_check(fixture, ticker)
    assert result["passed"] is True, (
        f"{ticker} Meta-Check failed unexpectedly: issues={result['issues']}"
    )


# ── Coverage sanity ─────────────────────────────────────────────────────────


def test_profile_mapping_table_covers_known_profiles():
    """If we add a new profile to TICKER_SECTOR_LOOKUP without adding it
    to _PROFILE_TO_EXPECTED_SECTORS, this test won't catch it (silently
    skipped). But document the existing coverage so removals are spotted."""
    expected_profiles = {
        "Managed Care", "Large Cap Pharma", "Pre-approval Biotech",
        "Money Center Bank", "Hyperscaler / Tech Conglomerate",
        "Mature SaaS", "Fabless",
    }
    for p in expected_profiles:
        assert p in _PROFILE_TO_EXPECTED_SECTORS, (
            f"profile {p!r} dropped from _PROFILE_TO_EXPECTED_SECTORS — "
            f"Meta-Check coverage regression"
        )

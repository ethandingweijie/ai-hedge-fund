"""
Tests for src/data/sector_kpi_framework.py — the unified sub-profile
KPI specification used by the deep research overlay, the LLM extractor
schema, and the dcf_agent attachment loop.

These tests enforce the consistency rule:
  Every sub-profile in SECTOR_KPI_FRAMEWORK must have:
    - At least one mandatory KPI (no empty sub-profile)
    - All mandatory KPIs have a population path (extractor OR FMP)
    - The 5 renderers (overlay, schema, validator, extractor, attach)
      produce equivalent output to the legacy hand-written extractors

PR #1 only populates Insurance. Subsequent PRs add Money Center Bank,
REIT, Growth SaaS, etc.
"""
from __future__ import annotations

import json
import pytest

from src.data.sector_kpi_framework import (
    SECTOR_KPI_FRAMEWORK,
    render_search_overlay,
    render_specialist_addendum,
    build_extractor_schema,
    validate_extractor_output,
    attach_overrides,
    render_card_payload,
    render_card_payloads_for_run,
    is_legacy_profile,
)


# ── Guardrail 1: schema completeness per sub-profile ─────────────────────────

@pytest.mark.parametrize("profile_name", list(SECTOR_KPI_FRAMEWORK.keys()))
def test_each_sub_profile_has_anchor_methods_and_kpis(profile_name):
    """Every entry must declare at least one anchor method and at least one KPI."""
    spec = SECTOR_KPI_FRAMEWORK[profile_name]
    assert spec.get("sector"), f"{profile_name}: missing 'sector'"
    assert spec.get("anchor_methods"), f"{profile_name}: missing 'anchor_methods'"
    assert spec.get("kpis"), f"{profile_name}: empty 'kpis' list"


@pytest.mark.parametrize("profile_name", list(SECTOR_KPI_FRAMEWORK.keys()))
def test_each_sub_profile_has_at_least_one_mandatory_kpi(profile_name):
    """A sub-profile with no mandatory KPI is mis-classified — either drop it
    or make at least one KPI mandatory so _completeness_score is meaningful."""
    spec = SECTOR_KPI_FRAMEWORK[profile_name]
    mandatory = [k for k in spec["kpis"] if k.get("mandatory")]
    assert mandatory, (
        f"{profile_name}: zero mandatory KPIs — either drop the sub-profile "
        f"or mark at least one KPI mandatory so _completeness_score is meaningful"
    )


@pytest.mark.parametrize("profile_name", list(SECTOR_KPI_FRAMEWORK.keys()))
def test_mandatory_kpis_have_population_path(profile_name):
    """Every MANDATORY KPI must have a path to populate it — at least one of:
       - search_phrases (LLM extractor can find it in research narrative), OR
       - fmp_field (deterministic FMP-direct read), OR
       - source: F or H (FMP-derivable via compute helper)
    A mandatory KPI with none of these can never be filled."""
    spec = SECTOR_KPI_FRAMEWORK[profile_name]
    for kpi in spec["kpis"]:
        if not kpi.get("mandatory"):
            continue
        has_extractor_path = bool(kpi.get("search_phrases"))
        has_fmp_path = bool(kpi.get("fmp_field"))
        has_fmp_compute_path = kpi.get("source") in ("F", "H")
        assert has_extractor_path or has_fmp_path or has_fmp_compute_path, (
            f"{profile_name}.{kpi['key']} is mandatory but has NO population "
            f"path. Add search_phrases (extractor), fmp_field (direct), "
            f"or source: F/H (compute helper)."
        )


@pytest.mark.parametrize("profile_name", list(SECTOR_KPI_FRAMEWORK.keys()))
def test_clamp_ranges_are_sane(profile_name):
    """Every clamp tuple must be (lo, hi) with lo < hi."""
    spec = SECTOR_KPI_FRAMEWORK[profile_name]
    for kpi in spec["kpis"]:
        if "clamp" not in kpi:
            continue
        lo, hi = kpi["clamp"]
        assert lo < hi, (
            f"{profile_name}.{kpi['key']}: clamp lo ({lo}) >= hi ({hi})"
        )


# ── Renderer 1: Section 2F overlay ───────────────────────────────────────────

def test_render_search_overlay_unmapped_returns_empty():
    """Unknown sub-profile/sector → empty string → zero behavior change for
    legacy sub-profiles still on the hand-written extractor path."""
    assert render_search_overlay("BogusProfile", "BogusSector") == ""


def test_render_search_overlay_insurance_pc_includes_mandatory():
    """Insurance + sub_sub='P&C' overlay must include combined_ratio
    (mandatory P&C) and SCR ratio, must EXCLUDE Life-only KPIs."""
    overlay = render_search_overlay("Insurance", "Financials", sub_sub="P&C")
    assert "combined_ratio" in overlay
    assert "SCR ratio" in overlay or "solvency_ratio_scr" in overlay
    assert "vnb_margin" not in overlay, "Life-only KPI leaked into P&C overlay"
    assert "embedded_value_per_share" not in overlay


def test_render_search_overlay_insurance_life_includes_mandatory():
    """Insurance + sub_sub='Life' overlay must include vnb_margin and
    embedded_value_per_share, must EXCLUDE P&C-only KPIs like combined_ratio."""
    overlay = render_search_overlay("Insurance", "Financials", sub_sub="Life")
    assert "vnb_margin" in overlay
    assert "embedded_value_per_share" in overlay
    assert "combined_ratio" not in overlay, "P&C-only KPI leaked into Life overlay"


def test_render_search_overlay_falls_back_to_sector():
    """profile_name unknown but sector matches → uses sector overlay."""
    # Insurance is sub-profile-keyed, so passing only sector="Insurance" — wait,
    # Insurance is the profile_name itself. Test: profile_name="Unknown" + sector="Insurance"
    overlay = render_search_overlay("UnknownProfile", "Insurance")
    assert "combined_ratio" in overlay or "vnb_margin" in overlay


# ── Renderer 2: extractor schema ─────────────────────────────────────────────

def test_build_extractor_schema_insurance():
    spec = build_extractor_schema("Insurance")
    assert spec["system_prompt"], "Insurance schema produced no system prompt"
    # All clamps must be in the prompt schema (sanity)
    for key in spec["clamps"]:
        assert key in spec["system_prompt"], (
            f"Schema clamp '{key}' missing from system_prompt"
        )
    # Mandatory list aligns with framework
    expected_mandatory = {
        "combined_ratio", "vnb_margin", "embedded_value_per_share",
        "solvency_ratio_scr",
    }
    assert set(spec["mandatory"]) == expected_mandatory


def test_build_extractor_schema_unmapped_returns_empty():
    spec = build_extractor_schema("BogusProfile")
    assert spec["system_prompt"] == ""
    assert spec["clamps"] == {}
    assert spec["kpi_keys"] == []


# ── Renderer 3: validator (soft-mandatory) ───────────────────────────────────

def test_validate_completeness_full_match():
    output = {
        "combined_ratio":           0.884,
        "vnb_margin":               0.22,        # Life — won't apply to P&C but counts
        "embedded_value_per_share": 65.40,
        "solvency_ratio_scr":       2.18,
    }
    validated = validate_extractor_output("Insurance", output)
    assert validated["_completeness_score"] == 1.0
    assert validated["_mandatory_missing"] == []


def test_validate_completeness_partial():
    """Only 1 of 4 mandatory present → _completeness_score = 0.25"""
    output = {"combined_ratio": 0.884}
    validated = validate_extractor_output("Insurance", output)
    assert validated["_completeness_score"] == 0.25
    assert set(validated["_mandatory_missing"]) == {
        "vnb_margin", "embedded_value_per_share", "solvency_ratio_scr",
    }


def test_validate_completeness_zero():
    """Empty extractor output → _completeness_score = 0.0"""
    validated = validate_extractor_output("Insurance", {})
    assert validated["_completeness_score"] == 0.0


def test_validate_unmapped_profile_passes_through():
    """Unknown profile → full score (no contract to enforce)."""
    validated = validate_extractor_output("BogusProfile", {})
    assert validated["_completeness_score"] == 1.0


# ── Renderer 5: attach_overrides (dcf_agent attachment loop) ─────────────────

def test_attach_overrides_writes_keys_to_most_recent():
    most_recent = {"revenue": 1.0e9}
    extractor_output = {
        "combined_ratio":      0.884,
        "solvency_ratio_scr":  2.18,
        "_completeness_score": 1.0,
        "_mandatory_missing":  [],
    }
    audit = attach_overrides("Insurance", extractor_output, most_recent)

    assert most_recent["combined_ratio"] == 0.884
    assert most_recent["solvency_ratio_scr"] == 2.18
    assert most_recent["_Insurance_completeness"] == 1.0
    assert most_recent["_Insurance_missing"] == []
    assert any("combined_ratio" in line for line in audit)


def test_attach_overrides_empty_output_is_noop():
    most_recent = {"revenue": 1.0e9}
    audit = attach_overrides("Insurance", {}, most_recent)
    assert audit == []
    assert most_recent == {"revenue": 1.0e9}


# ── End-to-end: build prompt + clamps + validate against legacy clamps ───────

LEGACY_INSURANCE_CLAMPS = {
    # Source: src/agents/industry/deep_research.py:_extract_insurance_metrics
    # _clamps dict (legacy hand-written body — kept as fallback). The
    # framework spec must produce IDENTICAL clamps for backward compatibility.
    "combined_ratio":           (0.70, 1.20),
    "loss_ratio":               (0.40, 0.85),
    "expense_ratio":            (0.15, 0.40),
    "vnb_margin":               (0.05, 0.40),
    "embedded_value_per_share": (0.0, 1_000_000.0),
    "solvency_ratio_scr":       (1.0, 3.0),
    "reserve_release_pct":      (-0.05, 0.15),
    "catastrophe_losses_pct":   (0.0, 0.20),
    "new_money_yield":          (0.02, 0.10),
}


def test_framework_clamps_match_legacy_insurance_extractor():
    """Framework auto-generated clamps for Insurance must match the legacy
    hand-written _extract_insurance_metrics _clamps dict — guarantees
    behavioural equivalence after migration."""
    framework_clamps = build_extractor_schema("Insurance")["clamps"]
    assert framework_clamps == LEGACY_INSURANCE_CLAMPS, (
        f"Framework clamps drifted from legacy:\n"
        f"  framework: {framework_clamps}\n"
        f"  legacy:    {LEGACY_INSURANCE_CLAMPS}"
    )


# ── Stage A backward-compat clamps tests (PR #2-#5) ──────────────────────────
# Each sub-profile that has a legacy hand-written extractor must have framework
# clamps that are a SUPERSET of the legacy clamps (framework can add new fields
# but cannot drop or change existing ones — guarantees no IV regression when
# Stage B swaps the extractor code path).

LEGACY_BANK_CLAMPS = {
    # Source: src/agents/industry/deep_research.py:_extract_bank_metrics line 805
    "cet1_ratio":            (0.05, 0.25),
    "nim_pct":               (0.005, 0.08),
    "efficiency_ratio":      (0.30, 0.80),
    "npl_ratio":             (0.0, 0.15),
    "net_charge_offs_pct":   (0.0, 0.05),
    "management_target_roe": (0.05, 0.25),
    "loan_to_deposit_ratio": (0.40, 1.20),
    "dividend_payout_ratio": (0.0, 1.0),
    "loan_growth_yoy":       (-0.30, 0.40),
    "deposit_growth_yoy":    (-0.30, 0.40),
}

LEGACY_SAAS_CLAMPS = {
    # Source: src/agents/industry/deep_research.py:_extract_saas_metrics line 703
    "nrr_pct":              (0.80, 1.50),
    "gross_retention_pct":  (0.80, 1.00),
    "cac_payback_months":   (3, 60),
    "ltv_cac_ratio":        (1, 15),
    "rule_of_40_score":     (-30, 120),
    "magic_number":         (0.1, 3.0),
    "rpo_growth_yoy":       (-0.20, 0.80),
    "billings_growth_yoy":  (-0.20, 0.80),
}

LEGACY_REIT_CLAMPS = {
    # Source: src/agents/industry/deep_research.py:_extract_reit_metrics
    # (validated via individual if-statements with these ranges, lines 916-980)
    "cap_rate_market":     (0.02, 0.20),
    "occupancy_rate":      (0.3, 1.0),
    "wale_years":          (0.5, 30),
    "leverage_ratio":      (0, 0.80),
    "dpu_cents":           (0, 500),
    "affo_per_unit_cents": (0, 500),
    # v3.2 — US-REIT vocabulary additions; legacy extractor accepts these in
    # the same validate-and-clamp block, so framework + legacy must agree.
    "same_store_noi_growth_pct": (-0.20, 0.40),
    "dps_usd":                   (0.01, 100.0),
    "core_ffo_per_share":        (0.10, 50.0),
}


def test_framework_clamps_match_legacy_bank_extractor():
    """Money Center Bank framework clamps must equal the legacy bank extractor's
    clamps — guarantees IV equivalence when Stage B swaps the code path."""
    framework_clamps = build_extractor_schema("Money Center Bank")["clamps"]
    assert framework_clamps == LEGACY_BANK_CLAMPS, (
        f"Bank clamps drifted:\n"
        f"  framework: {framework_clamps}\n"
        f"  legacy:    {LEGACY_BANK_CLAMPS}"
    )


@pytest.mark.parametrize("saas_profile", [
    "Cybersecurity / Mission-Critical SaaS",
])
def test_framework_clamps_match_legacy_saas_extractor(saas_profile):
    """All SaaS-family sub-profiles must COVER the legacy SaaS clamps — every
    legacy KPI must be present with identical (lo, hi). Framework may add new
    KPIs beyond the legacy snapshot (e.g. cash_runway_years for Cybersecurity's
    R-driver added v3.x); IV equivalence only requires the legacy KPI subset
    to remain identical."""
    framework_clamps = build_extractor_schema(saas_profile)["clamps"]
    missing = set(LEGACY_SAAS_CLAMPS) - set(framework_clamps)
    drifted = {
        k: (framework_clamps[k], LEGACY_SAAS_CLAMPS[k])
        for k in LEGACY_SAAS_CLAMPS
        if k in framework_clamps and framework_clamps[k] != LEGACY_SAAS_CLAMPS[k]
    }
    assert not missing and not drifted, (
        f"{saas_profile} legacy-clamp coverage broken:\n"
        f"  missing legacy KPIs: {missing}\n"
        f"  drifted (framework, legacy): {drifted}\n"
        f"  framework: {framework_clamps}\n"
        f"  legacy:    {LEGACY_SAAS_CLAMPS}"
    )


def test_framework_clamps_match_legacy_reit_extractor():
    """REIT framework clamps must match the legacy REIT extractor's per-field
    range checks. Note: subtype_mix and geographic_mix are dict-shaped and
    excluded from the numeric clamps comparison."""
    framework_clamps = build_extractor_schema("REIT")["clamps"]
    assert framework_clamps == LEGACY_REIT_CLAMPS, (
        f"REIT clamps drifted:\n"
        f"  framework: {framework_clamps}\n"
        f"  legacy:    {LEGACY_REIT_CLAMPS}"
    )


# ── Sub-sub-profile gating sanity (per Stage A entry) ────────────────────────

def test_money_center_bank_overlay_has_mandatory_kpis():
    """Bank overlay must include CET1, NIM, efficiency_ratio, target ROE."""
    overlay = render_search_overlay("Money Center Bank", "Financials")
    for kpi in ("cet1_ratio", "nim_pct", "efficiency_ratio", "management_target_roe"):
        assert kpi in overlay, f"Bank overlay missing mandatory KPI: {kpi}"
    assert "MANDATORY" in overlay


def test_reit_overlay_has_cap_rate_occupancy_dpu():
    """REIT overlay must include the 4 mandatory metrics that drive NAV/P/FFO/P/AFFO."""
    overlay = render_search_overlay("REIT", "REIT")
    for kpi in ("cap_rate_market", "occupancy_rate", "leverage_ratio", "dpu_cents"):
        assert kpi in overlay, f"REIT overlay missing mandatory KPI: {kpi}"


def test_saas_overlays_differ_by_subprofile_mandatory():
    """Growth SaaS marks NRR + Rule of 40 mandatory; Mature SaaS marks only NRR
    mandatory; Cybersecurity marks NRR + RPO growth mandatory."""
    growth_spec = build_extractor_schema("Growth SaaS")
    mature_spec = build_extractor_schema("Mature SaaS")
    cyber_spec  = build_extractor_schema("Cybersecurity / Mission-Critical SaaS")

    assert "rule_of_40_score" in growth_spec["mandatory"]
    assert "rule_of_40_score" not in mature_spec["mandatory"]
    assert "rpo_growth_yoy" in cyber_spec["mandatory"]
    assert "rpo_growth_yoy" not in growth_spec["mandatory"]


def test_biopharma_subprofiles_have_distinct_mandatory():
    """Pre-approval Biotech mandates cash_runway_qtrs + next_catalyst_date.
    Large Cap Pharma mandates loe_year_top_drug. They must NOT overlap."""
    biotech = build_extractor_schema("Pre-approval Biotech")
    pharma  = build_extractor_schema("Large Cap Pharma")
    assert "cash_runway_qtrs" in biotech["mandatory"]
    assert "next_catalyst_date" in biotech["mandatory"]
    assert "loe_year_top_drug" in pharma["mandatory"]
    # Distinct mandatory sets (cash runway irrelevant for Big Pharma; LOE less critical for pre-revenue)
    assert "loe_year_top_drug" not in biotech["mandatory"]
    assert "cash_runway_qtrs" not in pharma["mandatory"]


# ── PR #6 sanity tests for new sub-profiles ──────────────────────────────────

PR6_NEW_PROFILES = [
    "Regulated Utility",
    "Upstream Oil & Gas",
    "Mining (Major)",
    "Fabless",
    "IDM / Foundry",
    "Stable Growth",         # Telco
    "Automotive & EV",
    "Managed Care",
]


@pytest.mark.parametrize("profile", PR6_NEW_PROFILES)
def test_pr6_profile_in_framework(profile):
    """All PR #6 sub-profiles must be registered."""
    assert profile in SECTOR_KPI_FRAMEWORK


@pytest.mark.parametrize("profile", PR6_NEW_PROFILES)
def test_pr6_profile_has_overlay(profile):
    """Each PR #6 sub-profile must produce a non-empty Section 2F overlay
    when the prompt builder looks it up."""
    spec = SECTOR_KPI_FRAMEWORK[profile]
    overlay = render_search_overlay(profile, spec["sector"])
    assert overlay, f"{profile} produced empty overlay"
    assert "MANDATORY" in overlay, f"{profile} overlay missing MANDATORY section"


@pytest.mark.parametrize("profile,probe_kpi", [
    ("Regulated Utility",   "allowed_roe"),
    ("Upstream Oil & Gas",  "pv10_value_usd"),
    ("Mining (Major)",      "aisc_per_oz"),
    ("Fabless",             "data_center_revenue_pct"),
    ("IDM / Foundry",       "wafer_capacity_kwspm"),
    ("Stable Growth",       "arpu_usd"),
    ("Automotive & EV",     "auto_gross_margin_ex_credits"),
    ("Managed Care",        "medical_loss_ratio"),
])
def test_pr6_profile_has_distinctive_mandatory_kpi(profile, probe_kpi):
    """Each PR #6 sub-profile must have a distinctive mandatory KPI that
    proves the framework spec captures the sub-profile-specific value driver."""
    spec_built = build_extractor_schema(profile)
    assert probe_kpi in spec_built["mandatory"], (
        f"{profile} should mark {probe_kpi} mandatory (it's the distinctive "
        f"sub-profile KPI that drives the valuation card)"
    )


@pytest.mark.parametrize("profile", PR6_NEW_PROFILES)
def test_pr6_profiles_framework_dispatch_fires(profile, monkeypatch):
    """PR #6 new sub-profiles must NOT be excluded from the generic
    framework_metrics dispatch — otherwise the framework task wouldn't
    fire for them and the SectorValuationCard renders empty.

    History: this used to grep deep_research.py for the
    `_LEGACY_COVERED_PROFILES` exclusion set. That set was dead code
    (v3.14 removed the gate) and was deleted by the Workstream C1
    fan-out refactor (2026-08), so the guarantee is now tested
    behaviorally: running the shared fan-out for a PR6 profile must
    call extract_via_framework exactly once.
    """
    import src.agents.industry.deep_research as dr
    import src.agents.industry.sector_prompts as sp
    import src.data.sector_kpi_framework as skf

    assert profile in skf.SECTOR_KPI_FRAMEWORK, "precondition: registered"

    calls = []

    def fake_extract(client, model, sections, report, ticker,
                     profile_name=None, retry_directive=""):
        calls.append(profile_name)
        return {"probe": "kpi"}

    monkeypatch.setattr(
        sp, "needs_extractor",
        lambda name, sector, pname, ticker=None: name == "framework_metrics",
    )
    monkeypatch.setattr(skf, "extract_via_framework", fake_extract)

    results, failures = dr._run_extractor_fanout(
        sdk_client=None, synthesis_model="m",
        sections={"2a": "x"}, final_report="r",
        ticker="TEST", sector="Tech", profile_name=profile,
        raw_financials={},
    )

    assert calls == [profile], (
        f"{profile}: framework dispatch did not fire exactly once "
        f"(calls={calls}) — profile may have been re-added to an "
        f"exclusion gate."
    )
    assert results["framework_metrics"] == {"probe": "kpi"}


# ── render_specialist_addendum tests ─────────────────────────────────────────

def test_specialist_addendum_unmapped_returns_empty():
    """Unknown sub-profile/sector → empty addendum → specialist prompt unchanged
    (graceful no-op for sub-profiles not in framework)."""
    assert render_specialist_addendum("BogusProfile", "BogusSector") == ""


@pytest.mark.parametrize("profile_name", list(SECTOR_KPI_FRAMEWORK.keys()))
def test_specialist_addendum_well_formed_markdown(profile_name):
    """Every framework profile produces a well-formed markdown table addendum
    with required structural elements."""
    addendum = render_specialist_addendum(profile_name)
    assert addendum, f"{profile_name}: addendum should be non-empty"
    assert "## Key Sector Metrics" in addendum, \
        f"{profile_name}: missing required '## Key Sector Metrics' h2 heading"
    assert "| Metric | Value | Source |" in addendum, \
        f"{profile_name}: missing markdown table header row"
    assert "|---|---|---|" in addendum, \
        f"{profile_name}: missing markdown table separator row"
    assert f"SECTOR KPI ADDENDUM — {profile_name}" in addendum, \
        f"{profile_name}: missing addendum banner"


def test_specialist_addendum_includes_all_kpis():
    """The Insurance addendum should list every KPI from the framework spec
    (mandatory + nice-to-have)."""
    spec = SECTOR_KPI_FRAMEWORK["Insurance"]
    addendum = render_specialist_addendum("Insurance")
    for kpi in spec["kpis"]:
        # The label is built from compute_hint or the key — check for the key
        # (which always appears either in the label or compute_hint substring).
        # Looser test: at least the human-readable form should be findable.
        key_words = kpi["key"].replace("_", " ").lower()
        # Match either the snake_case key, the compute_hint, or the title-cased label
        present = (
            key_words in addendum.lower()
            or (kpi.get("compute_hint", "") and kpi["compute_hint"] in addendum)
        )
        assert present, (
            f"Insurance addendum missing KPI: {kpi['key']} "
            f"(searched for '{key_words}' and compute_hint)"
        )


def test_specialist_addendum_marks_mandatory_kpis():
    """Mandatory KPIs must be flagged with **(M)** marker; optional KPIs are not."""
    addendum = render_specialist_addendum("Insurance")
    spec = SECTOR_KPI_FRAMEWORK["Insurance"]
    n_mandatory_in_addendum = addendum.count("**(M)**")
    n_mandatory_in_spec = sum(1 for k in spec["kpis"] if k.get("mandatory"))
    assert n_mandatory_in_addendum == n_mandatory_in_spec, (
        f"Insurance: {n_mandatory_in_addendum} (M) markers in addendum vs "
        f"{n_mandatory_in_spec} mandatory KPIs in spec"
    )


def test_specialist_addendum_pc_sub_sub_excludes_life_kpis():
    """sub_sub='P&C' should exclude Life-only KPIs (vnb_margin, EV/share)."""
    pc_addendum = render_specialist_addendum("Insurance", sub_sub="P&C")
    assert "combined_ratio" in pc_addendum.lower() \
        or "Combined ratio" in pc_addendum.lower() \
        or "P&C" in pc_addendum
    # Life-only KPIs should NOT appear in the P&C-gated addendum
    assert "vnb_margin" not in pc_addendum.lower()
    assert "embedded_value_per_share" not in pc_addendum.lower()


def test_specialist_addendum_includes_source_priority():
    """Addendum should include the framework's source_priority list."""
    spec = SECTOR_KPI_FRAMEWORK["Insurance"]
    addendum = render_specialist_addendum("Insurance")
    if spec.get("source_priority"):
        for source in spec["source_priority"]:
            assert source in addendum, \
                f"Insurance addendum missing source priority: {source}"


def test_specialist_addendum_falls_back_to_sector():
    """profile_name unknown but sector matches → uses sector overlay."""
    # Insurance is keyed under "Insurance" profile_name; passing only sector
    # "Insurance" with a bogus profile_name should still find it via fallback.
    addendum = render_specialist_addendum("UnknownProfile", "Insurance")
    assert "## Key Sector Metrics" in addendum


# ════════════════════════════════════════════════════════════════════════════
# render_card_payload — frontend Option B card render
# ════════════════════════════════════════════════════════════════════════════

# Legacy sub-profiles with bespoke frontend cards (must be excluded from the
# generic sector_card render — they keep their existing UI).
_LEGACY_PROFILES_FOR_TESTS = {
    "Growth SaaS", "Mature SaaS", "Hyperscaler",
    "REIT", "Pipeline (Pre-revenue Biotech)",
    "Pre-approval Biotech", "Pre-Revenue Biotech",
}


def test_is_legacy_profile_recognises_known_legacy():
    # v3.4: Growth SaaS + Mature SaaS migrated off legacy. Remaining legacy
    # set: Hyperscaler / REIT / Pre-approval Biotech variants.
    for p in ("Hyperscaler", "REIT", "Pre-approval Biotech"):
        assert is_legacy_profile(p), f"{p} should be legacy"


def test_is_legacy_profile_returns_false_for_non_legacy():
    for p in (
        "Insurance", "Money Center Bank", "Mining (Major)", "Fabless",
        "Growth SaaS", "Mature SaaS",  # v3.4 — migrated off legacy
    ):
        assert not is_legacy_profile(p), f"{p} should NOT be legacy"
    assert not is_legacy_profile("")
    assert not is_legacy_profile(None)


def test_render_card_payload_returns_none_for_legacy_profile():
    """Legacy profiles must return None — frontend renders bespoke card."""
    assert render_card_payload("REIT", {}, "DLR") is None
    assert render_card_payload("Pre-approval Biotech", {}, "MRNA") is None


def test_render_card_payload_returns_none_for_unknown_profile():
    assert render_card_payload("", {}, "X") is None
    assert render_card_payload("NotARealProfile", {}, "X") is None


def test_render_card_payload_insurance_pgr_full_shape():
    """Insurance with all P&C metrics populated produces all expected fields."""
    state = {
        "data": {
            "tickers": ["PGR"],
            "profile_names": {"PGR": "Insurance"},
            "insurance_metrics_all": {
                "PGR": {
                    "combined_ratio": 0.882,
                    "loss_ratio": 0.658,
                    "expense_ratio": 0.224,
                    "solvency_ratio_scr": 2.15,
                    "reserve_release_pct": 0.012,
                    "catastrophe_losses_pct": 0.041,
                    "new_money_yield": 0.052,
                    "_completeness_score": 0.75,  # must be filtered out
                },
            },
        },
    }
    payload = render_card_payload("Insurance", state, "PGR")
    assert payload is not None
    assert payload["ticker"] == "PGR"
    assert payload["sector"] == "Financials"
    assert payload["profile_name"] == "Insurance"
    assert payload["anchor_methods"] == [
        "Embedded Value", "P/BV", "Combined Ratio Gate",
    ]
    # Heuristic grouping bins KPIs into themed sections
    titles = [g["title"] for g in payload["groups"]]
    assert "Profitability" in titles  # combined_ratio, loss_ratio, etc.
    assert "Risk & Reserves" in titles  # PYD, cat losses
    assert "Capital" in titles          # SCR/RBC
    # KPI value, mandatory, format, clamp must round-trip correctly
    all_kpis = [k for g in payload["groups"] for k in g["kpis"]]
    cr = next(k for k in all_kpis if k["key"] == "combined_ratio")
    assert cr["value"] == 0.882
    assert cr["mandatory"] is True
    assert cr["format"] == "pct"
    assert cr["clamp_low"] == 0.70
    assert cr["clamp_high"] == 1.20
    # Framework metadata MUST NOT leak into the rendered card
    assert "_completeness_score" not in {k["key"] for k in all_kpis}


def test_render_card_payload_filters_non_finite_floats():
    """NaN/Inf values must coerce to None — frontend tabular-nums chokes."""
    state = {
        "data": {
            "insurance_metrics_all": {
                "X": {"combined_ratio": float("nan"), "loss_ratio": float("inf")},
            },
        },
    }
    payload = render_card_payload("Insurance", state, "X")
    assert payload is not None
    all_kpis = [k for g in payload["groups"] for k in g["kpis"]]
    cr = next(k for k in all_kpis if k["key"] == "combined_ratio")
    lr = next(k for k in all_kpis if k["key"] == "loss_ratio")
    assert cr["value"] is None
    assert lr["value"] is None


def test_render_card_payload_missing_metric_state_renders_card_with_none_values():
    """No metric state → card still renders, all values None (graceful fallback)."""
    payload = render_card_payload(
        "Insurance",
        {"data": {"profile_names": {"PGR": "Insurance"}}},
        "PGR",
    )
    assert payload is not None
    assert payload["groups"], "Card should still render even with no values"
    all_kpis = [k for g in payload["groups"] for k in g["kpis"]]
    assert all(k["value"] is None for k in all_kpis)


def test_render_card_payloads_for_run_excludes_legacy_tickers():
    """The multi-ticker convenience must skip remaining-legacy profile
    tickers (Hyperscaler / REIT / Pre-approval Biotech). v3.4: Growth SaaS
    + Mature SaaS no longer legacy — they DO get included.
    """
    state = {
        "data": {
            "tickers": ["PGR", "NEM", "DLR"],
            "profile_names": {
                "PGR": "Insurance",
                "NEM": "Mining (Major)",
                "DLR": "REIT",  # legacy — bespoke REIT panel renders
            },
            "insurance_metrics_all": {"PGR": {"combined_ratio": 0.88}},
            "framework_metrics_all": {"NEM": {"aisc_per_oz": 1428}},
        },
    }
    out = render_card_payloads_for_run(state)
    assert "DLR" not in out, "Legacy REIT must be excluded"
    assert "PGR" in out and "NEM" in out


def test_render_card_payloads_for_run_handles_empty_state():
    assert render_card_payloads_for_run({}) == {}
    assert render_card_payloads_for_run({"data": {}}) == {}
    # No tickers + no profile_names → empty (no work to do)
    assert render_card_payloads_for_run({"data": {"tickers": []}}) == {}


@pytest.mark.parametrize(
    "profile_name",
    [p for p in SECTOR_KPI_FRAMEWORK.keys() if p not in _LEGACY_PROFILES_FOR_TESTS],
)
def test_render_card_payload_smoke_all_non_legacy_profiles(profile_name):
    """Every non-legacy profile must produce a well-formed payload (smoke test).

    The contract for the frontend is:
      - ticker, sector, profile_name, anchor_methods, groups all present
      - groups contain at least one KPI
      - every KPI has key, label, format, clamp_low/high (None ok), mandatory bool
    """
    payload = render_card_payload(
        profile_name,
        {"data": {"profile_names": {"XYZ": profile_name}}},
        "XYZ",
    )
    assert payload is not None, f"{profile_name}: payload was None"
    assert payload["ticker"] == "XYZ"
    assert payload["profile_name"] == profile_name
    assert payload["anchor_methods"], f"{profile_name}: no anchor methods"
    assert payload["groups"], f"{profile_name}: no groups"
    for g in payload["groups"]:
        assert g["title"], f"{profile_name}: group missing title"
        assert g["accent"] in ("blue", "green", "amber", "rose", "violet"), \
            f"{profile_name}: invalid accent {g['accent']!r}"
        assert g["kpis"], f"{profile_name}: group {g['title']!r} has no KPIs"
        for k in g["kpis"]:
            assert k["key"], f"{profile_name}: KPI missing key"
            assert k["label"], f"{profile_name}: KPI missing label"
            assert k["format"] in (
                "pct", "pct100", "bps", "usd", "usd_b", "x", "int", "string"
            ), f"{profile_name}/{k['key']}: invalid format {k['format']!r}"
            assert isinstance(k["mandatory"], bool)


def test_render_card_payload_payload_is_json_serializable():
    """Persistence requires the full payload to round-trip through json.dumps."""
    state = {
        "data": {
            "insurance_metrics_all": {
                "PGR": {"combined_ratio": 0.88, "solvency_ratio_scr": 2.15},
            },
        },
    }
    payload = render_card_payload("Insurance", state, "PGR")
    s = json.dumps(payload)  # raises TypeError if any value isn't JSON-safe
    reloaded = json.loads(s)
    assert reloaded["ticker"] == "PGR"
    # Key fields survive the roundtrip with same shape
    assert reloaded["groups"] == payload["groups"]


# ── Guardrail 8: KPI display-format / unit contract (Phase 1 QA) ─────────────
# Regression net for the silent unit bugs found in live runs:
#   PYPL  take_rate_bps 166  → rendered "16600%"  (bps mis-tagged pct)
#   FRSH  rule_of_40 45      → rendered "4500%"   ("% in hint" → pct)
#   ZTS   net_debt_to_ebitda → raw float "-0.4326732673" (numeric `string`)
#   *     ltv_cac 8          → "800%"             (ratio mis-tagged pct)
# All of these are *present-but-wrong* values that self-healing can't see, so
# the contract is enforced statically here instead.

from src.data.sector_kpi_framework import (  # noqa: E402
    _infer_kpi_format,
    _FORMAT_META,
    _KPI_FORMAT_OVERRIDES,
)

_VALID_FORMATS = {"pct", "pct100", "bps", "usd", "usd_b", "x", "int", "string"}

# KPI keys that are legitimately opaque text (a year, a free-form date) and are
# therefore allowed to resolve to the `string` format.
_INTENTIONAL_STRING_KEYS = {"loe_year_top_drug", "next_catalyst_date"}


def _all_kpis():
    """Yield (profile_name, kpi_dict) for every KPI instance in the framework."""
    for profile, spec in SECTOR_KPI_FRAMEWORK.items():
        if not isinstance(spec, dict):
            continue
        for kpi in spec.get("kpis", []):
            if isinstance(kpi, dict) and kpi.get("key"):
                yield profile, kpi


def test_every_kpi_resolves_to_a_known_format():
    """No KPI may resolve to an unknown format token, and _FORMAT_META must
    carry an entry for each format so render_card_payload can emit decimals/unit."""
    for profile, kpi in _all_kpis():
        fmt = _infer_kpi_format(kpi)
        assert fmt in _VALID_FORMATS, f"{profile}/{kpi['key']}: unknown format {fmt!r}"
        assert fmt in _FORMAT_META, f"format {fmt!r} missing from _FORMAT_META"


def test_no_bps_key_renders_as_percent():
    """A `*_bps` KPI sent through the pct path multiplies by 100 → 16600%."""
    for profile, kpi in _all_kpis():
        key = kpi["key"].lower()
        if key.endswith("_bps"):
            assert _infer_kpi_format(kpi) == "bps", (
                f"{profile}/{key}: bps KPI resolved to "
                f"{_infer_kpi_format(kpi)!r} (would mis-scale ×100)"
            )


def test_known_multiples_render_as_x_not_pct_or_string():
    """Leverage / coverage / unit-economics multiples and unitless scores must
    render as '×', never as a percentage or a raw float."""
    multiples = {k for k, v in _KPI_FORMAT_OVERRIDES.items() if v == "x"}
    # sanity: the headline offenders are covered
    assert {"net_debt_to_ebitda", "ltv_cac_ratio", "rule_of_40_score"} <= multiples
    for profile, kpi in _all_kpis():
        if kpi["key"] in multiples:
            assert _infer_kpi_format(kpi) == "x", (
                f"{profile}/{kpi['key']}: multiple resolved to "
                f"{_infer_kpi_format(kpi)!r}, expected 'x'"
            )


def test_no_unintended_numeric_string_format():
    """The `string` format dumps the value verbatim. Only the curated textual
    keys are allowed to use it; everything else must carry a numeric unit."""
    for profile, kpi in _all_kpis():
        fmt = _infer_kpi_format(kpi)
        if fmt == "string":
            assert kpi["key"] in _INTENTIONAL_STRING_KEYS, (
                f"{profile}/{kpi['key']}: numeric KPI fell through to 'string' "
                f"(would render an unrounded raw float)"
            )


def test_headline_offenders_resolve_correctly():
    """Point checks mirroring the exact live-report defects."""
    expected = {
        "take_rate_bps": "bps",
        "take_rate_stability_bps": "bps",
        "g_fee_rate_bps": "bps",
        "cost_of_risk_bps": "bps",
        "rule_of_40_score": "x",
        "net_debt_to_ebitda": "x",
        "ltv_cac_ratio": "x",
        "magic_number": "x",
        "ai_revenue_run_rate_usd_b": "usd_b",
    }
    seen = {}
    for _profile, kpi in _all_kpis():
        if kpi["key"] in expected:
            seen[kpi["key"]] = _infer_kpi_format(kpi)
    for key, want in expected.items():
        assert seen.get(key) == want, f"{key}: got {seen.get(key)!r}, want {want!r}"


def test_explicit_fmt_key_overrides_heuristic():
    """An explicit `fmt` on a KPI dict always wins over the heuristic."""
    assert _infer_kpi_format({"key": "take_rate_bps", "fmt": "pct100"}) == "pct100"
    assert _infer_kpi_format({"key": "anything_at_all", "fmt": "usd_b"}) == "usd_b"


# ── Guardrail 9: HealthcareServices sub-sector mapping (Phase 3) ──────────────
# Regression guard for the ZTS "BIOPHARMA · MANAGED CARE + insurer KPIs" defect.
# Root cause: HealthcareServices had NO key in INDUSTRY_VALUATION_PROFILES and
# no SECTOR_KPI_FRAMEWORK profile carried sector="HealthcareServices", so every
# health-services name fell to the sector default ("Managed Care" insurer KPIs).

_HEALTHCARE_SERVICES_PROFILES = {
    "Managed Care",
    "Healthcare Providers / Services",
    "Medical Devices",
    "Animal Health",
    "Pharma Distribution",
}


def test_healthcareservices_profiles_registered_in_framework():
    """Every HealthcareServices sub-profile must be in SECTOR_KPI_FRAMEWORK with
    sector='HealthcareServices' (so the router lists them as candidates)."""
    hs = {p for p, s in SECTOR_KPI_FRAMEWORK.items()
          if s.get("sector") == "HealthcareServices"}
    assert hs == _HEALTHCARE_SERVICES_PROFILES, (
        f"SECTOR_KPI_FRAMEWORK HealthcareServices set {hs} != "
        f"{_HEALTHCARE_SERVICES_PROFILES}"
    )


def test_healthcareservices_profiles_match_valuation_profiles():
    """The SECTOR_KPI_FRAMEWORK HealthcareServices keys must mirror
    INDUSTRY_VALUATION_PROFILES['HealthcareServices'] EXACTLY — Tier-1 profile
    verification (strategic_router) does an exact-string lookup across both."""
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as _IVP
    ivp_keys = set(_IVP.get("HealthcareServices", {}).keys())
    assert ivp_keys == _HEALTHCARE_SERVICES_PROFILES, (
        f"INDUSTRY_VALUATION_PROFILES['HealthcareServices'] keys {ivp_keys} != "
        f"SECTOR_KPI_FRAMEWORK HealthcareServices set {_HEALTHCARE_SERVICES_PROFILES} "
        f"— the two tables MUST agree or Tier-1 verification silently fails"
    )


def test_healthcareservices_default_resolves_to_a_real_profile():
    """The per-sector default must be a registered profile (not 'Managed Care',
    which gave un-overridden health names insurer KPIs)."""
    from src.agents.routing.strategic_router import _SECTOR_PROFILE_DEFAULT
    default = _SECTOR_PROFILE_DEFAULT["HealthcareServices"]
    assert default != "Managed Care", (
        "HealthcareServices default must not be the insurer profile"
    )
    assert default in SECTOR_KPI_FRAMEWORK
    assert SECTOR_KPI_FRAMEWORK[default]["sector"] == "HealthcareServices"


def test_resolve_profile_spec_normalises_whitespace_and_case():
    """The card lookup must tolerate whitespace / case drift in profile_name,
    and return (None, None) for genuinely unknown profiles."""
    from src.data.sector_kpi_framework import _resolve_profile_spec
    assert _resolve_profile_spec("Animal Health")[0] == "Animal Health"
    assert _resolve_profile_spec("  Animal   Health ")[0] == "Animal Health"
    assert _resolve_profile_spec("animal health")[0] == "Animal Health"
    assert _resolve_profile_spec("Totally Bogus Profile") == (None, None)
    assert _resolve_profile_spec("") == (None, None)


def test_render_card_payload_healthcareservices_reports_correct_sector():
    """An Animal Health card must render sector='HealthcareServices' (not the
    old 'Biopharma'), proving the Managed Care sector move + sibling profiles."""
    state = {"data": {"framework_metrics_all": {"ZTS": {
        "organic_revenue_growth_pct": 0.08,
        "operating_margin_pct": 0.36,
        "net_debt_to_ebitda": 2.1,
    }}}}
    payload = render_card_payload("Animal Health", state, "ZTS")
    assert payload is not None
    assert payload["sector"] == "HealthcareServices"
    assert payload["profile_name"] == "Animal Health"
    # No insurer KPIs leaked in
    all_keys = {kp["key"] for g in payload["groups"] for kp in g["kpis"]}
    assert "medical_loss_ratio" not in all_keys
    assert "medicare_advantage_mix_pct" not in all_keys

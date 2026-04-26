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
    "Growth SaaS",
    "Mature SaaS",
    "Cybersecurity / Mission-Critical SaaS",
])
def test_framework_clamps_match_legacy_saas_extractor(saas_profile):
    """All SaaS-family sub-profiles must use the legacy SaaS clamps verbatim
    (legacy _extract_saas_metrics is sector-gated, not sub-profile-gated)."""
    framework_clamps = build_extractor_schema(saas_profile)["clamps"]
    assert framework_clamps == LEGACY_SAAS_CLAMPS, (
        f"{saas_profile} clamps drifted:\n"
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


def test_pr6_profiles_not_double_extracted():
    """PR #6 new sub-profiles must NOT appear in the legacy-covered exclusion
    list in deep_research.py — otherwise the generic framework task wouldn't
    fire for them."""
    # Re-derive the exclusion list from deep_research.py source
    import re as _re
    from pathlib import Path
    dr_path = (Path(__file__).resolve().parent.parent
               / "src" / "agents" / "industry" / "deep_research.py")
    src = dr_path.read_text(encoding="utf-8")
    # Find the _LEGACY_COVERED_PROFILES set literal
    m = _re.search(r"_LEGACY_COVERED_PROFILES = \{(.*?)\}", src, _re.DOTALL)
    assert m, "Could not find _LEGACY_COVERED_PROFILES in deep_research.py"
    legacy_text = m.group(1)
    for profile in PR6_NEW_PROFILES:
        assert f'"{profile}"' not in legacy_text, (
            f"{profile} is in _LEGACY_COVERED_PROFILES but should be "
            f"framework-dispatched (PR #6). Remove from exclusion list."
        )


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
    for p in ("Growth SaaS", "Mature SaaS", "Hyperscaler", "REIT"):
        assert is_legacy_profile(p), f"{p} should be legacy"


def test_is_legacy_profile_returns_false_for_non_legacy():
    for p in ("Insurance", "Money Center Bank", "Mining (Major)", "Fabless"):
        assert not is_legacy_profile(p), f"{p} should NOT be legacy"
    assert not is_legacy_profile("")
    assert not is_legacy_profile(None)


def test_render_card_payload_returns_none_for_legacy_profile():
    """Legacy profiles must return None — frontend renders bespoke card."""
    assert render_card_payload("Growth SaaS", {}, "CRWD") is None
    assert render_card_payload("REIT", {}, "DLR") is None


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
    """The multi-ticker convenience must skip legacy profile tickers."""
    state = {
        "data": {
            "tickers": ["PGR", "NEM", "CRWD"],
            "profile_names": {
                "PGR": "Insurance",
                "NEM": "Mining (Major)",
                "CRWD": "Growth SaaS",
            },
            "insurance_metrics_all": {"PGR": {"combined_ratio": 0.88}},
            "framework_metrics_all": {"NEM": {"aisc_per_oz": 1428}},
        },
    }
    out = render_card_payloads_for_run(state)
    assert "CRWD" not in out, "Legacy SaaS must be excluded"
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
            assert k["format"] in ("pct", "usd", "x", "int", "string"), \
                f"{profile_name}/{k['key']}: invalid format {k['format']!r}"
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

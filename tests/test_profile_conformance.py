"""Stage 5 / D2 — taxonomy conformance guard.

Regression net against the class of bug where a profile string assigned
upstream (TICKER_SECTOR_LOOKUP, SECTOR_KPI_FRAMEWORK, classify fallback)
fails to resolve downstream — previously discarded SILENTLY by DCF
guardrail 4, which sent profitable crypto exchanges down the "Pre-Revenue
Tech" method set or the pure-DCF fallback.

Asserts (all strict — no exception sets):
  1. Every non-empty profile in TICKER_SECTOR_LOOKUP resolves in
     INDUSTRY_VALUATION_PROFILES for its lookup sector.
  2. Every SECTOR_KPI_FRAMEWORK key is a real valuation profile (so card
     rendering and FMP augmentation can resolve it).
  3. Every _SECTOR_PROFILE_DEFAULT entry resolves, and every sector with
     profiles has an explicit default (no order-dependent fallback).
  4. Every valuation profile's method weights sum to 1.0 and each method
     carries the keys _compute_method_value dispatches on.
  5. D4 alias regression: bare "Hyperscaler" resolves via the alias table
     and is no longer gated as a legacy profile.
  6. D3 fallback regression: a sector with no ladder branch
     (HealthcareServices) classifies to its explicit default, never to an
     order-dependent first key.
"""
from __future__ import annotations

import pytest

from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    TICKER_SECTOR_LOOKUP,
    _SECTOR_PROFILE_DEFAULT,
    classify_valuation_profile,
)
from src.data.sector_kpi_framework import (
    SECTOR_KPI_FRAMEWORK,
    _LEGACY_PROFILES,
    _resolve_profile_spec,
    is_legacy_profile,
)


def _all_profile_names() -> set[str]:
    return {p for profs in INDUSTRY_VALUATION_PROFILES.values() for p in profs}


# ── 1. lookup → valuation profiles ───────────────────────────────────────────

def test_every_lookup_profile_resolves():
    """Every profile override assigned by TICKER_SECTOR_LOOKUP must exist in
    INDUSTRY_VALUATION_PROFILES — an unresolved override used to be dropped
    silently by DCF guardrail 4."""
    unresolved = []
    for ticker, (sector, profile, _gics, _desc) in TICKER_SECTOR_LOOKUP.items():
        if not profile:
            continue  # empty = deliberate, sector default classifies in-situ
        if profile not in INDUSTRY_VALUATION_PROFILES.get(sector, {}):
            unresolved.append((ticker, sector, profile))
    assert not unresolved, f"unresolved lookup profiles: {unresolved}"


def test_lookup_sectors_have_profiles():
    """A lookup sector with zero profiles can never resolve an override."""
    bad = sorted({sector for sector, _p, _g, _d in TICKER_SECTOR_LOOKUP.values()
                  if sector not in INDUSTRY_VALUATION_PROFILES})
    assert not bad, f"lookup sectors missing from INDUSTRY_VALUATION_PROFILES: {bad}"


# ── 2. framework keys → valuation profiles ───────────────────────────────────

def test_every_framework_key_is_a_valuation_profile():
    """Card rendering + FMP augmentation resolve via the framework key; a key
    that is not a valuation profile renders nothing and skips augmentation."""
    all_profiles = _all_profile_names()
    orphans = sorted(set(SECTOR_KPI_FRAMEWORK) - all_profiles)
    assert not orphans, f"framework keys with no valuation profile: {orphans}"


# ── 3. explicit sector defaults ──────────────────────────────────────────────

def test_sector_default_table_complete_and_resolvable():
    """_SECTOR_PROFILE_DEFAULT covers every sector and every default exists."""
    missing = sorted(set(INDUSTRY_VALUATION_PROFILES) - set(_SECTOR_PROFILE_DEFAULT))
    assert not missing, f"sectors without explicit default: {missing}"
    bad = [(s, p) for s, p in _SECTOR_PROFILE_DEFAULT.items()
           if p not in INDUSTRY_VALUATION_PROFILES.get(s, {})]
    assert not bad, f"default profiles that do not resolve: {bad}"


# ── 4. method weights contract ───────────────────────────────────────────────

def test_profile_method_weights_sum_to_one():
    for sector, profiles in INDUSTRY_VALUATION_PROFILES.items():
        for name, spec in profiles.items():
            methods = spec.get("methods", [])
            assert methods, f"{sector}/{name}: no methods"
            total = sum(m.get("weight", 0.0) for m in methods)
            assert abs(total - 1.0) < 1e-6, f"{sector}/{name}: weights sum {total}"
            for m in methods:
                assert m.get("name"), f"{sector}/{name}: method without name"
                assert "weight" in m, f"{sector}/{name}/{m.get('name')}: no weight"
                assert "implementable" in m, (
                    f"{sector}/{name}/{m.get('name')}: no implementable flag"
                )


# ── 5. D4 — Hyperscaler alias regression ─────────────────────────────────────

def test_hyperscaler_alias_resolves():
    canon, spec = _resolve_profile_spec("Hyperscaler")
    assert canon == "Hyperscaler / Tech Conglomerate"
    assert spec is not None


def test_hyperscaler_no_longer_legacy():
    """D4 migrated Hyperscaler off the legacy gate (same pattern as the v3.4
    SaaS migration): the generic card renders and FMP augmentation runs."""
    assert "Hyperscaler" not in _LEGACY_PROFILES
    assert not is_legacy_profile("Hyperscaler")
    assert not is_legacy_profile("Hyperscaler / Tech Conglomerate")


# ── 6. D3 — classify fallback is explicit, not order-dependent ───────────────

def test_classify_fallthrough_uses_explicit_default():
    """HealthcareServices has no ladder branch — classification must land on
    the explicit default, not whichever profile is defined first."""
    result = classify_valuation_profile(
        "HealthcareServices",
        revenue_cagr=0.08, fcf_margin=0.05, debt_to_equity=1.2,
    )
    assert result == _SECTOR_PROFILE_DEFAULT["HealthcareServices"]
    assert result != next(iter(INDUSTRY_VALUATION_PROFILES["HealthcareServices"]))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── 7. duplicate literal keys silently shadow each other ─────────────────────
#
# A dict literal with the same key twice keeps the LAST one, with no error and
# no warning. Found live on GOOG: the curated row
#     "GOOG": ("Tech", "Hyperscaler / Tech Conglomerate", ..., "same business as GOOGL")
# was overwritten thirty lines later by
#     "GOOG": ("Tech", "", ...)
# so Alphabet's two share classes routed to different profiles -- GOOGL to the
# Hyperscaler table, GOOG to the classifier -- while the shadowed line's own
# comment stated the intent that had been lost. XOM, CVX, COP and 01810.HK
# carried the same shape, there harmlessly (the specific row happened to come
# second). Line numbers move; this does not.

import ast
import pathlib


def _duplicate_keys_in_dict_literals(path: pathlib.Path) -> dict[str, list[str]]:
    """{assigned name: [keys appearing more than once]} for every module-level
    dict literal in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dups: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value, targets = node.value, (
            node.targets if isinstance(node, ast.Assign) else [node.target])
        if not isinstance(value, ast.Dict):
            continue
        name = next((t.id for t in targets if isinstance(t, ast.Name)), None)
        if not name:
            continue
        seen, repeated = set(), []
        for k in value.keys:
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                continue
            if k.value in seen:
                repeated.append(k.value)
            seen.add(k.value)
        if repeated:
            dups[name] = sorted(set(repeated))
    return dups


@pytest.mark.parametrize("relpath", [
    "src/data/sector_profiles.py",
    "src/data/regional_comps.py",
    "src/agents/analysis/dcf_agent.py",
])
def test_no_dict_literal_shadows_one_of_its_own_keys(relpath):
    path = pathlib.Path(__file__).resolve().parents[1] / relpath
    dups = _duplicate_keys_in_dict_literals(path)
    assert not dups, f"{relpath}: duplicate keys silently shadowed: {dups}"


def test_alphabet_share_classes_route_identically():
    """GOOG and GOOGL are one company. A regression here means the duplicate
    key came back, or one class was edited without the other."""
    assert TICKER_SECTOR_LOOKUP["GOOG"][:2] == TICKER_SECTOR_LOOKUP["GOOGL"][:2]

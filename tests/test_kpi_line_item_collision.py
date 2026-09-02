"""
Guard: an LLM-extracted KPI must never silently replace a filed value.

`attach_overrides` writes each framework KPI onto `most_recent` under its own
key. 36 KPI keys across the framework are also real fields on the financial
models, so those writes land on the same slot the filings populate — and the
consumer reading it back with `.get(key)` cannot tell a statement apart from a
sentence in a research note.

The live instances when this guard was added:

  * `net_debt_to_ebitda` — 34 profiles
  * `nav_per_unit`       — 2 REIT profiles, and it feeds the NAV Discount
                           valuation method directly

The historical instance is DBS: an archived run carried a bank_metrics KPI
`book_value_per_share = 32.5` against a filed 24.28, which propagated into the
P/TBV card and the GGM P/B target. That particular key is no longer in any
spec, so the run below asserts the *mechanism* rather than that one key.

Gap-filling stays supported: REIT NAV per unit is disclosed in filings the
data provider often omits, so a research value is applied when nothing filed
occupies the slot — and the audit line says so.
"""
from __future__ import annotations

import pytest

from src.data.sector_kpi_framework import (
    PROTECTED_LINE_ITEM_KEYS,
    SECTOR_KPI_FRAMEWORK,
    attach_overrides,
)


def test_protected_set_covers_the_models():
    from src.data.models import FinancialMetrics

    assert set(FinancialMetrics.model_fields) <= PROTECTED_LINE_ITEM_KEYS
    for name in ("book_value_per_share", "tangible_book_value_per_share",
                 "net_debt_to_ebitda", "nav_per_unit", "total_equity",
                 "net_income", "shares_outstanding"):
        assert name in PROTECTED_LINE_ITEM_KEYS, name


def test_filed_value_survives_a_colliding_kpi():
    """The core guarantee."""
    most_recent = {"nav_per_unit": 4.10}
    audit = attach_overrides("S-REIT", {"nav_per_unit": 5.75}, most_recent)

    assert most_recent["nav_per_unit"] == 4.10
    assert most_recent["_research_nav_per_unit"] == 5.75
    assert any("NOT applied" in line for line in audit)


def test_gap_is_still_filled_when_nothing_was_filed():
    most_recent: dict = {}
    audit = attach_overrides("S-REIT", {"nav_per_unit": 5.75}, most_recent)

    assert most_recent["nav_per_unit"] == 5.75
    assert any("research-sourced" in line for line in audit)


def test_explicit_none_counts_as_no_filed_value():
    """A provider that returned the field but with no value is still a gap."""
    most_recent = {"nav_per_unit": None}
    attach_overrides("S-REIT", {"nav_per_unit": 5.75}, most_recent)
    assert most_recent["nav_per_unit"] == 5.75


def test_non_colliding_kpis_are_unaffected():
    """Research is authoritative for genuine KPIs — that must not change."""
    most_recent = {"cet1_ratio": 0.99}
    attach_overrides("Money Center Bank (SG)", {"cet1_ratio": 0.146}, most_recent)
    assert most_recent["cet1_ratio"] == 0.146


def test_collision_is_reported_loudly():
    """A silent divert would just relocate the invisibility."""
    most_recent = {"nav_per_unit": 4.10}
    audit = attach_overrides("S-REIT", {"nav_per_unit": 5.75}, most_recent)
    line = next(l for l in audit if l.startswith("nav_per_unit"))
    assert "4.1" in line and "5.75" in line and "_research_nav_per_unit" in line


def _colliding_keys() -> list[tuple[str, str]]:
    return [
        (prof, k["key"])
        for prof, spec in SECTOR_KPI_FRAMEWORK.items()
        for k in spec.get("kpis", [])
        if k["key"] in PROTECTED_LINE_ITEM_KEYS
    ]


def test_every_current_collision_is_handled():
    """Every colliding key in the shipped framework must divert, not clobber."""
    collisions = _colliding_keys()
    assert collisions, "expected the known collisions to still be present"
    for profile, key in collisions:
        most_recent = {key: 1.0}
        attach_overrides(profile, {key: 999.0}, most_recent)
        assert most_recent[key] == 1.0, f"{profile}/{key} was clobbered"
        assert most_recent[f"_research_{key}"] == 999.0


def test_known_collision_inventory():
    """Fails when a new colliding key is introduced, so the choice is deliberate.

    A new entry here is not automatically a bug — the guard handles it — but it
    should be a decision someone made, not a name that drifted in.
    """
    keys = {k for _, k in _colliding_keys()}
    assert keys == {"net_debt_to_ebitda", "nav_per_unit"}, (
        f"KPI/line-item collisions changed: {sorted(keys)}. The guard protects "
        f"the filed value, but prefer renaming the KPI (e.g. "
        f"'<name>_research') so the two never share a slot."
    )

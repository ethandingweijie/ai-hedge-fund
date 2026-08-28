"""Lululemon / Birkenstock -> Apparel; Flutter / DraftKings -> gaming.

Methods quoted from each note:

    LULU  "our target now based on 4.50x Q5-Q8 EV/EBITDA"
    BIRK  "based on a DCF methodology with a 9.5% WACC and 2.5% terminal
          growth rate", cross-checked against footwear peers on P/E
    FLUT  "lowering our US target multiple to 11.75x NTM+4 EBITDA"
    DKNG  "an equal blend of (1) EV/Sales applied to our NTM+1 estimates
          and (2) a modified DCF using an EV/GAAP EBITDA multiple"

LULU and BIRK needed no new profile: Apparel / Athletic Wear already
anchors EV/EBITDA with DCF and P/E (norm) behind it. They were simply
never routed — LULU resolved to ("Consumer", "") and BIRK, FLUT and DKNG
were absent from the lookup entirely, falling through to a Tech default.
"""

from __future__ import annotations

import pytest

from app.backend.services.drive_sync import build_gazetteer, match_tickers
from src.data.sector_kpi_framework import (
    SECTOR_KPI_FRAMEWORK, build_extractor_schema, render_card_payload,
)
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES, get_wacc_profile_for_ticker,
)

GAMING = "Online Gaming / Sports Betting"
APPAREL = "Apparel / Athletic Wear"


# ── Routing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker, profile", [
    ("LULU", APPAREL),
    ("BIRK", APPAREL),
    ("FLUT", GAMING),
    ("DKNG", GAMING),
])
def test_routing(ticker, profile):
    assert get_wacc_profile_for_ticker(ticker) == ("Consumer", profile)


@pytest.mark.parametrize("ticker", ["LULU", "BIRK", "FLUT", "DKNG"])
def test_none_fall_through_to_the_tech_default(ticker):
    """An unrouted ticker resolves to ("Tech", "") and gets SaaS-shaped
    treatment — wrong for an apparel brand and for a sportsbook alike."""
    sector, profile = get_wacc_profile_for_ticker(ticker)
    assert (sector, profile) != ("Tech", "")
    assert profile, f"{ticker} has a sector but no sub-profile"


# ── Methods ──────────────────────────────────────────────────────────────

def test_gaming_anchors_ev_ebitda():
    methods = INDUSTRY_VALUATION_PROFILES["Consumer"][GAMING]["methods"]
    assert [m["name"] for m in methods if m.get("anchor")] == ["EV/EBITDA"]


def test_gaming_keeps_real_weight_on_ev_revenue():
    """DraftKings is valued on an equal blend that leans on EV/Sales, so a
    token weight would misrepresent the method."""
    methods = {m["name"]: m["weight"]
               for m in INDUSTRY_VALUATION_PROFILES["Consumer"][GAMING]["methods"]}
    assert methods["EV/Revenue"] >= 0.25
    assert sum(methods.values()) == pytest.approx(1.0)


def test_gaming_excludes_book_value_methods():
    """The asset base is licences and brand, not book."""
    excluded = INDUSTRY_VALUATION_PROFILES["Consumer"][GAMING]["excluded"]
    assert "P/BV" in excluded


def test_apparel_carries_both_methods_the_notes_use():
    """LULU is EV/EBITDA, BIRK is DCF — the profile must support both.

    `Brand Val` is deliberately not implementable and carries an
    `EV/Revenue` proxy instead, so the requirement is that every method is
    either computable or proxied — not that all are computable.
    """
    methods = {m["name"]: m for m in
               INDUSTRY_VALUATION_PROFILES["Consumer"][APPAREL]["methods"]}
    assert methods["EV/EBITDA"].get("anchor") is True
    assert any(n.startswith("DCF") for n in methods)
    for name, m in methods.items():
        assert m.get("implementable") or m.get("proxy"), (
            f"{name} is neither implementable nor proxied"
        )


# ── Extraction + card ────────────────────────────────────────────────────

def test_gaming_extractor_schema():
    schema = build_extractor_schema(GAMING)
    assert schema
    assert set(schema["kpi_keys"]) == {
        k["key"] for k in SECTOR_KPI_FRAMEWORK[GAMING]["kpis"]}
    assert set(schema["mandatory"]) == {
        "monthly_paying_players", "net_revenue_margin_pct", "gaming_ebitda_margin"}


def test_gaming_card_renders():
    payload = render_card_payload(GAMING, {"data": {}}, "DKNG")
    assert payload and payload.get("groups")


# ── The collision guard, now across every profile ────────────────────────

def test_no_kpi_key_anywhere_shadows_a_statement_line_item():
    """attach_overrides does `most_recent[key] = value` unconditionally, so
    a KPI named after a line item silently replaces real FMP data.

    This found two live collisions beyond the SGX profiles the original
    guard covered:

      * `inventory_turnover` on Apparel / Athletic Wear and Traditional
        Retail — the profile LULU and BIRK now route to.
      * `tangible_book_value_per_share` on Super-Regional Bank, which
        dcf_agent reads as "Primary — FMP direct" and which the comment
        there records as the fix for the JPM over-strip bug. The extractor
        was undoing that fix.

    Both are now namespaced. This guard covers ALL profiles so the next one
    is caught before it ships.
    """
    from src.tools.api import (
        _BALANCE_MAP, _CASHFLOW_MAP, _INCOME_MAP, _RATIOS_MAP,
    )
    line_items = (
        set(_INCOME_MAP.values()) | set(_BALANCE_MAP.values())
        | set(_CASHFLOW_MAP.values()) | set(_RATIOS_MAP.values())
    )
    collisions = [
        f"{profile}.{kpi['key']}"
        for profile, spec in SECTOR_KPI_FRAMEWORK.items()
        for kpi in spec.get("kpis", [])
        if kpi["key"] in line_items
    ]
    assert not collisions, (
        "KPI keys shadowing statement line items: " + ", ".join(collisions)
    )


def test_band_references_resolve_to_real_kpis():
    """Renaming a KPI without updating the band that points at it leaves the
    quality/risk multiplier reading a key that never populates."""
    bad = []
    for profile, spec in SECTOR_KPI_FRAMEWORK.items():
        keys = {k["key"] for k in spec.get("kpis", [])}
        refs = [b.get("kpi")
                for b in spec.get("quality_tiers", {}).get("kpi_bands", [])]
        risk = spec.get("risk_adjustment", {}).get("kpi")
        if risk:
            refs.append(risk)
        for ref in refs:
            if ref and ref not in keys:
                bad.append(f"{profile} -> {ref}")
    assert not bad, "band references with no matching KPI: " + ", ".join(bad)


# ── The archive feeds them ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def gazetteer():
    return build_gazetteer()


@pytest.mark.parametrize("filename, expected", [
    ("Lululemon Athletica Inc. (LULU)_ Refresh.pdf", {"LULU"}),
    ("Birkenstock (BIRK)_ Post 3Q26 Results.pdf",    {"BIRK"}),
    ("Flutter Entertainment (FLUT)_ 2Q26 Review.pdf", {"FLUT"}),
    ("DraftKings Inc. (DKNG)_ Q2 26 Review.pdf",     {"DKNG"}),
])
def test_reports_match_their_ticker(filename, expected, gazetteer):
    assert set(match_tickers(filename, gazetteer)) == expected

"""Stage 7 / Phase 7g — SOTP extractor deterministic-guard tests.

Pure-function guards in ``sotp_extractor`` that need no API/LLM access:

  * ``_drop_aggregate_segments`` — parent+child double-count detection.
  * ``_units_scale_factor`` — $-millions/$-billions emission detection.
  * ``_parse_research_sotp_block`` — deep-research 2A.5 SOTP block parser.
"""
from __future__ import annotations

from src.agents.analysis.sotp_extractor import (
    _drop_aggregate_segments,
    _parse_research_sotp_block,
    _units_scale_factor,
)

# ── _drop_aggregate_segments ──────────────────────────────────────────────────

_MEITUAN_MAP = [
    {"name": "Core local commerce", "revenue_fwd": 37.4e9},
    {"name": "Food Delivery", "revenue_fwd": 23.3e9},
    {"name": "Instashopping", "revenue_fwd": 5.7e9},
    {"name": "IHT", "revenue_fwd": 9.9e9},
    {"name": "New initiatives", "revenue_fwd": 11.8e9},
]


def test_parent_dropped_children_kept():
    kept = [s["name"] for s in _drop_aggregate_segments(_MEITUAN_MAP)]
    assert kept == ["Food Delivery", "Instashopping", "IHT", "New initiatives"]


def test_clean_map_untouched():
    clean = [s for s in _MEITUAN_MAP if s["name"] != "Core local commerce"]
    assert [s["name"] for s in _drop_aggregate_segments(clean)] == [
        s["name"] for s in clean]


def test_two_segments_never_dropped():
    segs = [{"name": "a", "revenue_fwd": 10e9},
            {"name": "b", "revenue_fwd": 10e9}]
    assert len(_drop_aggregate_segments(segs)) == 2


def test_all_matching_never_drops_everything():
    # Pathological all-equal map: guard must not return empty.
    segs = [{"name": f"s{i}", "revenue_fwd": 10e9} for i in range(4)]
    assert len(_drop_aggregate_segments(segs)) >= 1


# ── _units_scale_factor ───────────────────────────────────────────────────────

def test_units_millions_detected():
    assert _units_scale_factor([23251, 5711], 50.2e9) == 1e6


def test_units_billions_detected():
    assert _units_scale_factor([23.251, 5.711], 50.2e9) == 1e9


def test_units_raw_left_alone():
    assert _units_scale_factor([23.251e9, 5.711e9], 50.2e9) == 1.0


def test_units_partial_map_left_alone():
    # Segment map covering only part of the group: no rescale.
    assert _units_scale_factor([23.251e9], 50.2e9) == 1.0


def test_units_no_anchor():
    assert _units_scale_factor([12345], None) == 1.0


# ── _parse_research_sotp_block ────────────────────────────────────────────────

_BLOCK = """
SOTP_BLOCK_START
SEGMENT | name=Food Delivery | rev_fwd_usd_bn=23.251 | ebit_margin_pct=UNKNOWN | unit_economics=67mn daily orders x Rmb1.1 (Source: GS, Aug 2026) | multiple_ref=12x P/E referencing single-digit growth (Source: GS)
SEGMENT | name=New initiatives | rev_fwd_usd_bn=11.836 | ebit_margin_pct=UNKNOWN | unit_economics=NA | multiple_ref=1.3x EV/Rev (Source: GS)
ASSOCIATES | fair_value_usd_bn=12.339 | basis=per broker SOTP (Source: GS)
NET_CASH | usd_bn=15.455 | as_of=2Q26
HOLDCO_DISCOUNT | pct=15 | convention=China internet
TAX_RATE | pct=15
SOTP_BLOCK_END
"""


def test_block_parses_segments():
    b = _parse_research_sotp_block(_BLOCK)
    assert b is not None
    fd = b["segments"][0]
    assert fd["name"] == "Food Delivery"
    assert fd["revenue_fwd"] == 23.251e9
    assert fd["ebit_margin"] is None          # UNKNOWN
    assert fd["pe_multiple"] == 12.0
    assert fd["ev_rev_multiple"] is None


def test_block_parses_evrev_multiple():
    b = _parse_research_sotp_block(_BLOCK)
    ni = b["segments"][1]
    assert ni["ev_rev_multiple"] == 1.3
    assert ni["pe_multiple"] is None


def test_block_parses_balance_sheet_lines():
    b = _parse_research_sotp_block(_BLOCK)
    assert b["associates_fair"] == 12.339e9
    assert b["net_cash"] == 15.455e9
    assert b["holdco_pct"] == 0.15
    assert b["tax_rate"] == 0.15


def test_no_block_returns_none():
    assert _parse_research_sotp_block("plain research text, no markers") is None
    assert _parse_research_sotp_block("") is None


def test_empty_block_returns_none():
    assert _parse_research_sotp_block(
        "SOTP_BLOCK_START\nSOTP_BLOCK_END") is None


# ── _reconcile_research_segments ─────────────────────────────────────────────

from src.agents.analysis.sotp_extractor import (  # noqa: E402
    _reconcile_research_segments,
)

_RS_BLOCK = {"segments": [
    {"name": "Food Delivery", "revenue_fwd": 23.3e9, "pe_multiple": 12.0},
    {"name": "Instashopping", "revenue_fwd": 5.7e9, "pe_multiple": 25.0},
    {"name": "In-store, Hotel and Travel", "revenue_fwd": 9.9e9,
     "pe_multiple": 10.0},
    {"name": "New initiatives", "revenue_fwd": 11.8e9,
     "ev_rev_multiple": 1.3},
]}


def test_dropped_research_segment_readded():
    merged = [dict(s) for s in _RS_BLOCK["segments"][:3]]  # LLM dropped the 4th
    out = _reconcile_research_segments(merged, _RS_BLOCK)
    assert [s["name"] for s in out] == [
        "Food Delivery", "Instashopping",
        "In-store, Hotel and Travel", "New initiatives"]
    assert out[-1]["ev_rev_multiple"] == 1.3


def test_renamed_segment_not_duplicated():
    merged = [dict(s) for s in _RS_BLOCK["segments"]]
    merged[2]["name"] = "In-store, Hotel & Travel"  # "&" variant of the note
    out = _reconcile_research_segments(merged, _RS_BLOCK)
    assert len(out) == 4  # matched via normalized-key fold, not re-added
    assert sum("In-store" in s["name"] for s in out) == 1


def test_substring_variant_not_duplicated():
    merged = [{"name": "New Initiatives (Keeta, community group buying)",
               "revenue_fwd": 11.8e9}]
    out = _reconcile_research_segments(merged, _RS_BLOCK)
    # "newinitiatives" is a substring of the LLM's longer name -> no re-add;
    # the other three rs segments are genuinely missing and get re-added.
    assert len(out) == 4
    assert sum(s["name"].startswith("New Initiatives") for s in out) == 1


def test_no_block_is_noop():
    merged = [{"name": "Food Delivery", "revenue_fwd": 23.3e9}]
    assert _reconcile_research_segments(merged, None) is merged
    assert _reconcile_research_segments(merged, {"segments": []}) is merged


# ── _reconcile_research_segments: canonical-map mode ─────────────────────────
# When the researched block covers >=70% of reported group revenue it IS the
# segment map: non-matching anchor-derived segments (different taxonomy) are
# double-count candidates and get dropped, not stacked alongside.

def test_canonical_drops_unmatched_anchor_segments():
    # GS geographic buckets vs FMP product lines (AMZN shape).
    merged = [
        {"name": "Online Stores", "revenue_fwd": 300e9},
        {"name": "Amazon Web Services", "revenue_fwd": 180e9},
        {"name": "Advertising Services", "revenue_fwd": 90e9},
    ]
    rs = {"segments": [
        {"name": "North America", "revenue_fwd": 518.6e9},
        {"name": "International", "revenue_fwd": 209.7e9},
        {"name": "AWS", "revenue_fwd": 248.9e9},
    ]}
    out = _reconcile_research_segments(merged, rs, group_revenue=716.9e9)
    assert [s["name"] for s in out] == [
        "North America", "International", "AWS"]


def test_canonical_replaces_conflicting_merge():
    # Abbreviation-vs-full-name ("AWS" vs "Amazon Web Services") cannot be
    # resolved by key matching — the complete researched map wins wholesale.
    merged = [
        {"name": "Amazon Web Services", "revenue_fwd": 180e9},
        {"name": "Online Stores", "revenue_fwd": 300e9},
    ]
    rs = {"segments": [
        {"name": "AWS", "revenue_fwd": 248.9e9},
        {"name": "North America", "revenue_fwd": 518.6e9},
    ]}
    out = _reconcile_research_segments(merged, rs, group_revenue=716.9e9)
    assert [s["name"] for s in out] == ["AWS", "North America"]


def test_canonical_single_group_segment_wins():
    # MSFT shape: one researched group segment vs product-line anchors.
    merged = [{"name": f"Product {i}", "revenue_fwd": 30e9} for i in range(10)]
    rs = {"segments": [{"name": "Microsoft Group", "revenue_fwd": 470.5e9}]}
    out = _reconcile_research_segments(merged, rs, group_revenue=331.8e9)
    assert [s["name"] for s in out] == ["Microsoft Group"]


def test_partial_block_stays_additive():
    # Research block covering <70% of group revenue: additive behavior.
    merged = [{"name": "Anchor A", "revenue_fwd": 100e9}]
    rs = {"segments": [{"name": "Researched B", "revenue_fwd": 20e9}]}
    out = _reconcile_research_segments(merged, rs, group_revenue=200e9)
    assert [s["name"] for s in out] == ["Anchor A", "Researched B"]


def test_no_group_revenue_stays_additive():
    merged = [{"name": "Anchor A", "revenue_fwd": 100e9}]
    rs = {"segments": [{"name": "Researched B", "revenue_fwd": 400e9}]}
    out = _reconcile_research_segments(merged, rs, group_revenue=None)
    assert [s["name"] for s in out] == ["Anchor A", "Researched B"]


# ── _parse_research_sotp_block: negative net cash / zero tax ────────────────

_NEG_BLOCK = """
SOTP_BLOCK_START
SEGMENT | name=North America | rev_fwd_usd_bn=518.601 | ebit_margin_pct=10.075 | multiple_ref=16x P/E on NTM+1 GAAP EBIT (Source: GS)
SEGMENT | name=International | rev_fwd_usd_bn=209.723 | multiple_ref=1.25x EV/Sales on NTM+1 revenue (Source: GS)
NET_CASH | usd_bn=-59.22 | as_of=2Q26
HOLDCO_DISCOUNT | pct=0 | convention=none
TAX_RATE | pct=0 | convention=EV/EBIT on pre-tax EBIT
SOTP_BLOCK_END
"""


def test_block_parses_negative_net_cash_and_zero_rates():
    b = _parse_research_sotp_block(_NEG_BLOCK)
    assert b is not None
    assert b["net_cash"] == -59.22e9
    assert b["holdco_pct"] == 0.0
    assert b["tax_rate"] == 0.0
    na, intl = b["segments"]
    assert na["pe_multiple"] == 16.0 and na["ev_rev_multiple"] is None
    assert intl["ev_rev_multiple"] == 1.25 and intl["pe_multiple"] is None
    assert intl["ebit_margin"] is None


# ── _parse_research_sotp_block: SCENARIO lines (Tier 3.8) ───────────────────

_SCEN_BLOCK = """
SOTP_BLOCK_START
SEGMENT | name=Food Delivery | rev_fwd_usd_bn=23.251 | multiple_ref=12x P/E (Source: GS)
SEGMENT | name=New initiatives | rev_fwd_usd_bn=11.836 | multiple_ref=1.3x EV/Rev (Source: GS)
SCENARIO | case=bear | multiples=Food Delivery:8x P/E (Source: GS); New initiatives:0.8x EV/Sales (Source: GS)
SCENARIO | case=bull | segment=Food Delivery | multiple_ref=16x P/E referencing ad recovery (Source: GS)
SOTP_BLOCK_END
"""


def test_scenario_compact_form():
    b = _parse_research_sotp_block(_SCEN_BLOCK)
    assert b is not None
    bear = b["scenarios"]["bear"]
    assert len(bear) == 2
    assert bear[0]["name"] == "Food Delivery"
    assert bear[0]["pe_multiple"] == 8.0
    assert bear[0]["ev_rev_multiple"] is None
    assert bear[1]["name"] == "New initiatives"
    assert bear[1]["ev_rev_multiple"] == 0.8
    assert bear[1]["pe_multiple"] is None


def test_scenario_per_segment_form():
    b = _parse_research_sotp_block(_SCEN_BLOCK)
    bull = b["scenarios"]["bull"]
    assert len(bull) == 1
    assert bull[0]["name"] == "Food Delivery"
    assert bull[0]["pe_multiple"] == 16.0
    assert bull[0]["ev_rev_multiple"] is None
    assert "ad recovery" in bull[0]["rationale"]


def test_scenario_invalid_case_ignored():
    blk = ("SOTP_BLOCK_START\n"
           "SEGMENT | name=A | rev_fwd_usd_bn=1 | multiple_ref=2x P/E\n"
           "SCENARIO | case=base | multiples=A:5x P/E\n"
           "SOTP_BLOCK_END")
    b = _parse_research_sotp_block(blk)
    assert b is not None
    assert not b.get("scenarios")


def test_scenario_malformed_pairs_skipped():
    blk = ("SOTP_BLOCK_START\n"
           "SCENARIO | case=bear | multiples=no-colon-here; B:notaMultiple; C:3x P/E\n"
           "SOTP_BLOCK_END")
    b = _parse_research_sotp_block(blk)
    assert b is not None
    bear = b["scenarios"]["bear"]
    assert len(bear) == 1
    assert bear[0]["name"] == "C"
    assert bear[0]["pe_multiple"] == 3.0


def test_block_without_scenarios_has_none():
    b = _parse_research_sotp_block(_BLOCK)
    assert b is not None
    assert b.get("scenarios") is None


def test_scenario_only_block_not_empty():
    # A block carrying ONLY scenario lines still parses (segments may be
    # supplied by the anchor path) — the emptiness check includes scenarios.
    blk = ("SOTP_BLOCK_START\n"
           "SCENARIO | case=bull | multiples=A:5x P/E\n"
           "SOTP_BLOCK_END")
    b = _parse_research_sotp_block(blk)
    assert b is not None
    assert b["scenarios"]["bull"][0]["pe_multiple"] == 5.0

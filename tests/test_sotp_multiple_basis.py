"""Stage 7 / Phase 7h — independent SOTP multiple basis tests (pure logic).

Guards ``sotp_multiple_basis`` without touching FMP: archetype classifier,
median/IQR, the haircut x growth-adjustment math, tier detection, and the
apply-precedence rules (research-note override > comp basis > LLM/policy),
including the one-metric rule and the >25% divergence cross-check flag.
"""
from __future__ import annotations

import pytest

from src.agents.analysis.sotp_multiple_basis import (
    _load_calibration,
    adjusted_multiple,
    apply_margin_basis,
    classify_archetype,
    derive_segment_basis,
    med_iqr,
)
from src.agents.analysis.sotp_multiple_basis import (
    apply_multiple_basis as _apply_multiple_basis,
)


def apply_multiple_basis(*args, **kwargs):
    # Legacy-tier tests pin the learned-model artifact OFF: the on-disk
    # artifact would otherwise activate the model tier and change results.
    # Model-tier behavior is covered separately below with mock artifacts.
    kwargs.setdefault("_artifact", None)
    return _apply_multiple_basis(*args, **kwargs)

_HAIRCUT_CN = {"value": 0.6, "basis": "test"}
_HAIRCUT_US = {"value": 1.0, "basis": "test"}

# Four comps with fwd P/E and EV/Rev + consensus growth (17%).
_ROWS_4 = [
    {"ticker": "A", "pe": 10.0, "ev_rev": 1.0, "g_pct": 17.0},
    {"ticker": "B", "pe": 15.0, "ev_rev": 1.4, "g_pct": 17.0},
    {"ticker": "C", "pe": 20.0, "ev_rev": 2.0, "g_pct": 17.0},
    {"ticker": "D", "pe": 30.0, "ev_rev": 3.0, "g_pct": 17.0},
]


def _fetch(rows):
    return lambda peers, end_date: rows


# ── Archetype classifier ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Food Delivery", "food_delivery"),
    ("Core Local Commerce", "food_delivery"),
    ("Instashopping", "growth_commerce"),
    ("In-store, Hotel & Travel", "ota_travel"),
    ("New Initiatives", "low_margin_logistics"),
    ("Taobao and Tmall Group", "ecommerce_core"),
    ("JD Retail", "ecommerce_core"),
    ("Cloud Intelligence Group", "cloud"),
    ("Intelligent Cloud", "cloud"),
    ("AWS", "cloud"),
    ("Amazon Web Services", "cloud"),
    ("Azure", "cloud"),
    ("North America", "ecommerce_core"),
    ("Online Stores", "ecommerce_core"),
    ("JD Logistics", "logistics"),
    ("Cainiao Smart Logistics", "logistics"),
    ("Alibaba International Digital Commerce (AIDC)", "international_commerce"),
    ("Marketing Services", "ads"),
    ("Advertising", "ads"),
    ("FinTech and Business Services", "fintech_payments"),
    ("Value Added Services", "games_media"),
])
def test_classify_archetype_known(name, expected):
    assert classify_archetype(name) == expected


@pytest.mark.parametrize("name", [
    "New Businesses (food delivery, Joybuy international, Dada)",
    "All Others",
    "Unallocated items",
    "",
])
def test_classify_archetype_none(name):
    # Parenthetical gloss must not drive classification; unmatched -> None.
    assert classify_archetype(name) is None


# ── Median / IQR ──────────────────────────────────────────────────────────────

def test_med_iqr_quartiles_when_four_plus():
    med, lo, hi = med_iqr([30.0, 10.0, 20.0, 40.0])
    assert med == 25.0
    assert lo < med < hi
    assert lo == pytest.approx(12.5)
    assert hi == pytest.approx(37.5)


def test_med_iqr_range_when_few():
    med, lo, hi = med_iqr([8.0, 12.0])
    assert (med, lo, hi) == (10.0, 8.0, 12.0)


# ── Haircut x growth adjustment ───────────────────────────────────────────────

def test_pe_haircut_applied():
    # 20x median x 0.60 jurisdiction haircut, no growth differential.
    v = adjusted_multiple(20.0, 30.0, haircut=0.6, metric="pe",
                          seg_growth_pct=10.0, comp_growth_pct=10.0)
    assert v == pytest.approx(12.0)


def test_capped_at_comp_p75():
    v = adjusted_multiple(50.0, 30.0, haircut=1.0, metric="pe",
                          seg_growth_pct=10.0, comp_growth_pct=10.0)
    assert v == pytest.approx(30.0)


def test_evrev_haircut_floor():
    # EV/Rev is less jurisdiction-sensitive: haircut floored at 0.8.
    v = adjusted_multiple(1.6, 3.4, haircut=0.6, metric="ev_rev",
                          seg_growth_pct=15.0, comp_growth_pct=15.0)
    assert v == pytest.approx(1.6 * 0.8)


def test_growth_ratio_clamped_high():
    # g_seg/g_comp = 10 clamps to 2.0; elasticity 0.5 -> sqrt(2).
    v = adjusted_multiple(20.0, 30.0, haircut=0.6, metric="pe",
                          seg_growth_pct=100.0, comp_growth_pct=10.0)
    assert v == pytest.approx(20.0 * 0.6 * 2.0 ** 0.5)


def test_growth_ratio_clamped_low():
    v = adjusted_multiple(20.0, 30.0, haircut=0.6, metric="pe",
                          seg_growth_pct=1.0, comp_growth_pct=10.0)
    assert v == pytest.approx(20.0 * 0.6 * 0.5 ** 0.5)


def test_missing_growth_falls_back_to_neutral():
    v = adjusted_multiple(20.0, 30.0, haircut=0.6, metric="pe",
                          seg_growth_pct=None, comp_growth_pct=None)
    assert v == pytest.approx(12.0)


# ── Tier detection (derive_segment_basis) ────────────────────────────────────

def test_derive_ok_profitable_pe():
    b = derive_segment_basis("Food Delivery", profitable=True, loss=False,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4))
    assert b["status"] == "ok"
    assert b["metric"] == "pe"
    assert b["n_comps"] == 4
    # median 17.5 x 0.6 haircut (equal growth), below p75 27.5.
    assert b["derived"] == pytest.approx(10.5)


def test_derive_loss_making_takes_evrev():
    b = derive_segment_basis("Food Delivery", profitable=False, loss=True,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4))
    assert b["status"] == "ok"
    assert b["metric"] == "ev_rev"


def test_derive_forced_evrev_archetype_even_if_profitable():
    b = derive_segment_basis("New Initiatives", profitable=True, loss=False,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4))
    assert b["metric"] == "ev_rev"


def test_derive_thin_comps():
    b = derive_segment_basis("Food Delivery", profitable=True, loss=False,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4[:2]))
    assert b["status"] == "thin_comps"
    assert b["n_comps"] == 2


def test_derive_unknown_profitability():
    b = derive_segment_basis("Food Delivery", profitable=False, loss=False,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4))
    assert b["status"] == "unknown_profitability"


def test_derive_no_archetype():
    b = derive_segment_basis("All Others", profitable=True, loss=False,
                             end_date="2026-08-16", haircut=0.6,
                             fetch=_fetch(_ROWS_4))
    assert b["status"] == "no_archetype"


# ── Apply precedence (apply_multiple_basis) ──────────────────────────────────

def _seg(name="Food Delivery", ebit=1e9, pe=None, ev_rev=None):
    return {"name": name, "revenue_fwd": 20e9, "ebit": ebit,
            "pe_multiple": pe, "ev_rev_multiple": ev_rev,
            "rationale": "llm rationale", "source": "research_llm"}


def test_research_note_overrides_llm_and_basis():
    segs = [_seg(pe=15.0)]
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0,
           "rationale": "12x P/E (Source: note)"}]
    out, detail = apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == 12.0
    assert out[0]["source"] == "deep_research_2a5"
    assert out[0]["rationale"] == "12x P/E (Source: note)"
    d = detail["segments"]["Food Delivery"]
    assert d["status"] == "research_override"
    assert d["llm_original"] == 15.0


def test_research_block_one_metric_rule():
    segs = [_seg()]  # profitable
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0,
           "ev_rev_multiple": 2.0, "rationale": "r"}]
    out, _ = apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == 12.0
    assert out[0]["ev_rev_multiple"] is None  # both given -> P/E wins (profit)


def test_comp_basis_default_when_no_research():
    segs = [_seg(pe=40.0)]
    out, detail = apply_multiple_basis(
        segs, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == pytest.approx(10.5)
    assert out[0]["ev_rev_multiple"] is None
    assert out[0]["source"] == "comp_basis"
    assert "comp basis" in out[0]["rationale"]
    d = detail["segments"]["Food Delivery"]
    assert d["status"] == "comp_basis_applied"
    assert d["llm_original"] == 40.0
    assert d["flag"] is False  # applied == derived -> no divergence


def test_comp_basis_clears_opposite_metric():
    segs = [_seg(ev_rev=2.0)]  # LLM gave EV/Rev; basis says profitable -> P/E
    out, _ = apply_multiple_basis(
        segs, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == pytest.approx(10.5)
    assert out[0]["ev_rev_multiple"] is None


def test_thin_comps_keeps_llm_multiple():
    segs = [_seg(pe=15.0)]
    out, detail = apply_multiple_basis(
        segs, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4[:2]), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == 15.0
    assert out[0]["source"] == "research_llm"
    assert detail["segments"]["Food Delivery"]["status"] == "thin_comps"
    assert "thin_comps:1" in detail["summary"]


def test_divergence_flag_when_research_far_from_basis():
    segs = [_seg()]
    rs = [{"name": "Food Delivery", "pe_multiple": 30.0, "rationale": "r"}]
    flat = [dict(r, pe=20.0) for r in _ROWS_4]  # derived = 20x at hc 1.0
    _, detail = apply_multiple_basis(
        segs, rs, ticker="MPNGY", end_date="2026-08-16", china=False,
        _fetch=_fetch(flat), _haircut=_HAIRCUT_US)
    d = detail["segments"]["Food Delivery"]
    assert d["divergence_pct"] == pytest.approx(50.0)
    assert d["flag"] is True
    assert len(detail["divergence_flags"]) == 1


def test_metric_mismatch_no_numeric_divergence():
    segs = [_seg()]
    rs = [{"name": "Food Delivery", "ev_rev_multiple": 1.5, "rationale": "r"}]
    _, detail = apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    d = detail["segments"]["Food Delivery"]
    assert d["divergence_pct"] is None  # EV/Rev vs P/E basis: not comparable
    assert d["flag"] is False


def test_non_china_haircut_is_one():
    _, detail = apply_multiple_basis(
        [_seg()], [], ticker="MSFT", end_date="2026-08-16", china=False,
        _fetch=_fetch(_ROWS_4), _haircut=None)
    assert detail["jurisdiction"]["value"] == 1.0


def test_segment_name_substring_match_for_research_block():
    segs = [_seg(name="Food Delivery and Local Commerce")]
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0, "rationale": "r"}]
    out, detail = apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == 12.0
    assert detail["segments"]["Food Delivery and Local Commerce"]["status"] == \
        "research_override"


def test_ampersand_and_variant_spellings_match():
    # LLM flips between "&" and "and" across runs; both must match the note.
    segs = [_seg(name="In-store, Hotel & Travel")]
    rs = [{"name": "In-store, Hotel and Travel", "pe_multiple": 10.0,
           "rationale": "r"}]
    out, detail = apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    assert out[0]["pe_multiple"] == 10.0
    assert out[0]["source"] == "deep_research_2a5"


def test_unit_economics_profit_per_unit_marks_profitable():
    # UE segments with no ebit/margin still get a P/E cross-check basis.
    segs = [{"name": "Instashopping", "revenue_fwd": 5e9, "ebit": None,
             "ebit_margin": None,
             "unit_economics": {"volume_annual": 5.5e9,
                                "profit_per_unit": 0.1, "fx_to_usd": 0.14},
             "pe_multiple": 25.0, "ev_rev_multiple": None,
             "rationale": "", "source": "research_llm"}]
    _, detail = apply_multiple_basis(
        segs, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    d = detail["segments"]["Instashopping"]
    assert d["status"] == "comp_basis_applied"  # was unknown_profitability
    assert d["metric"] == "pe"


# ── Learned-model tier (multiple_model_v1) ────────────────────────────────────

import math  # noqa: E402


def _mock_artifact(point_pe=18.0, sigma=0.30, arch="food_delivery",
                   n_arch=12, ev_rev_point=1.5, margin_point=0.12):
    """Minimal valid artifact: flat intercept-only fits per target."""
    names = ["intercept", "g_fwd_pct", "log_revenue_scale", "china"]
    return {
        "version": 1, "fit_end_date": "2026-08-18", "n_obs": 40,
        "archetypes": [arch], "archetype_n": {arch: n_arch},
        "targets": {
            "pe": {"feature_names": names,
                   "coeffs": [math.log(point_pe), 0.0, 0.0, 0.0],
                   "n_obs": n_arch, "residual_std": sigma, "r2": 0.8},
            "ev_rev": {"feature_names": names,
                       "coeffs": [math.log(ev_rev_point), 0.0, 0.0, 0.0],
                       "n_obs": n_arch, "residual_std": sigma, "r2": 0.7},
            "margin": {"feature_names": names,
                       "coeffs": [margin_point, 0.0, 0.0, 0.0],
                       "n_obs": n_arch, "residual_std": 0.05, "r2": 0.5},
        },
        "observations": [], "outcome_corrections": {},
    }


def test_model_tier_beats_comp_basis_without_note():
    segs = [_seg(pe=40.0)]
    out, detail = _apply_multiple_basis(
        segs, [], ticker="MPNGY", end_date="2026-08-16", china=False,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_US,
        _artifact=_mock_artifact(point_pe=18.0))
    assert out[0]["pe_multiple"] == pytest.approx(18.0)
    assert out[0]["ev_rev_multiple"] is None
    assert out[0]["source"] == "multiple_model_v1"
    assert "model basis" in out[0]["rationale"]
    d = detail["segments"]["Food Delivery"]
    assert d["status"] == "model_applied"
    assert d["model"]["point"] == pytest.approx(18.0)
    assert d["llm_original"] == 40.0


def test_note_still_overrides_model():
    segs = [_seg(pe=15.0)]
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0, "rationale": "note"}]
    out, detail = _apply_multiple_basis(
        segs, rs, ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN,
        _artifact=_mock_artifact(point_pe=18.0))
    assert out[0]["pe_multiple"] == 12.0
    assert out[0]["source"] == "deep_research_2a5"
    assert detail["segments"]["Food Delivery"]["status"] == "research_override"


def test_thin_archetype_in_artifact_falls_back_to_comp_basis():
    # Containment gate: archetype with < MIN_ARCH_OBS fit observations is
    # refused by predict -> legacy comp basis applies.
    segs = [_seg(pe=40.0)]
    out, detail = _apply_multiple_basis(
        segs, [], ticker="MPNGY", end_date="2026-08-16", china=False,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_US,
        _artifact=_mock_artifact(n_arch=2))
    assert out[0]["source"] == "comp_basis"
    assert detail["segments"]["Food Delivery"]["status"] == "comp_basis_applied"
    assert "model" not in detail["segments"]["Food Delivery"]


def test_zscore_flag_when_note_far_from_model():
    segs = [_seg()]
    # note 30x vs model 18x, sigma 0.30 -> z = ln(30/18)/0.3 = +1.70 > 1.5
    rs = [{"name": "Food Delivery", "pe_multiple": 30.0, "rationale": "r"}]
    _, detail = _apply_multiple_basis(
        segs, rs, ticker="MPNGY", end_date="2026-08-16", china=False,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_US,
        _artifact=_mock_artifact(point_pe=18.0, sigma=0.30))
    d = detail["segments"]["Food Delivery"]
    assert d["zscore"] == pytest.approx(1.70, abs=0.01)
    assert d["flag"] is True
    assert len(detail["divergence_flags"]) == 1


def test_no_zscore_flag_within_band():
    segs = [_seg()]
    rs = [{"name": "Food Delivery", "pe_multiple": 20.0, "rationale": "r"}]
    _, detail = _apply_multiple_basis(
        segs, rs, ticker="MPNGY", end_date="2026-08-16", china=False,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_US,
        _artifact=_mock_artifact(point_pe=18.0, sigma=0.30))
    d = detail["segments"]["Food Delivery"]
    assert abs(d["zscore"]) < 1.5
    assert d["flag"] is False


def test_no_artifact_keeps_legacy_behavior_bit_identical():
    segs_a, segs_b = [_seg(pe=40.0)], [_seg(pe=40.0)]
    out_a, det_a = apply_multiple_basis(  # wrapper pins _artifact=None
        segs_a, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN)
    out_b, det_b = _apply_multiple_basis(
        segs_b, [], ticker="3690.HK", end_date="2026-08-16", china=True,
        _fetch=_fetch(_ROWS_4), _haircut=_HAIRCUT_CN, _artifact=None)
    assert out_a == out_b
    assert det_a == det_b
    assert "model" not in det_a["segments"]["Food Delivery"]


def test_load_calibration_missing_or_corrupt_returns_none(tmp_path):
    assert _load_calibration(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{corrupt", encoding="utf-8")
    assert _load_calibration(str(bad)) is None


# ── Margin basis (margin_model_v1) ────────────────────────────────────────────

def _bare_seg(name="Food Delivery", rev=20e9, margin=None, ebit=None, ue=None):
    return {"name": name, "revenue_fwd": rev, "ebit": ebit,
            "ebit_margin": margin, "unit_economics": ue,
            "pe_multiple": None, "ev_rev_multiple": None,
            "rationale": "", "source": "research_llm"}


def test_margin_fill_when_no_researched_economics():
    segs = [_bare_seg()]
    out, detail = apply_margin_basis(
        segs, [], china=False, _artifact=_mock_artifact(margin_point=0.12))
    assert out[0]["ebit_margin"] == pytest.approx(0.12)
    assert out[0]["margin_source"] == "margin_model_v1"
    d = detail["segments"]["Food Delivery"]
    assert d["status"] == "margin_model_applied"
    assert detail["summary"] == "filled:1"


def test_margin_never_overrides_researched():
    segs = [
        _bare_seg(margin=0.20),                       # researched margin
        dict(_bare_seg(name="Instashopping"), ebit=1e9),  # researched EBIT
        _bare_seg(name="Core Local Commerce", ue={"profit_per_unit": 0.1}),
    ]
    out, detail = apply_margin_basis(
        segs, [], china=False, _artifact=_mock_artifact(margin_point=0.12))
    assert out[0]["ebit_margin"] == 0.20
    assert out[1].get("ebit_margin") is None
    assert out[2].get("ebit_margin") is None
    assert all(detail["segments"][s["name"]]["status"] == "researched_kept"
               for s in out)
    assert detail["summary"] == "filled:0"


def test_margin_skips_forced_evrev_archetypes():
    segs = [_bare_seg(name="New Initiatives")]
    out, detail = apply_margin_basis(
        segs, [], china=False,
        _artifact=_mock_artifact(arch="low_margin_logistics"))
    assert out[0].get("ebit_margin") is None
    assert detail["segments"]["New Initiatives"]["status"] == "skipped"
    assert detail["segments"]["New Initiatives"]["reason"] == "ev_rev_archetype"


def test_margin_no_artifact_is_noop():
    segs = [_bare_seg()]
    out, detail = apply_margin_basis(segs, [], china=False, _artifact=None)
    assert out[0].get("ebit_margin") is None
    assert detail["summary"] == "no_artifact"


def test_margin_unclassified_segment_skipped():
    segs = [_bare_seg(name="All Others")]
    out, detail = apply_margin_basis(
        segs, [], china=False, _artifact=_mock_artifact())
    assert out[0].get("ebit_margin") is None
    assert detail["segments"]["All Others"]["reason"] == "no_archetype"


def test_margin_skips_segments_matched_to_research_note():
    # Note multiples are calibrated to the note's own EBIT estimates; a
    # model margin under a note multiple mixes bases (2026-08-18 3690.HK
    # regression: NOTE-mode TP 159.2 -> 83.7 vs GS 123). Note-matched
    # segments must stay untouched while unmatched ones still fill.
    segs = [_bare_seg(), _bare_seg(name="Core Local Commerce")]
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0, "rationale": "note"}]
    out, detail = apply_margin_basis(
        segs, rs, china=True, _artifact=_mock_artifact(margin_point=0.12))
    assert out[0].get("ebit_margin") is None
    assert detail["segments"]["Food Delivery"]["status"] == "research_note_kept"
    assert out[1]["ebit_margin"] == pytest.approx(0.12)
    assert detail["segments"]["Core Local Commerce"]["status"] == \
        "margin_model_applied"
    assert detail["summary"] == "filled:1"


def test_margin_note_match_substring_variant():
    segs = [_bare_seg(name="Food Delivery and Local Commerce")]
    rs = [{"name": "Food Delivery", "pe_multiple": 12.0}]
    out, detail = apply_margin_basis(
        segs, rs, china=True, _artifact=_mock_artifact())
    assert out[0].get("ebit_margin") is None
    assert detail["segments"]["Food Delivery and Local Commerce"]["status"] == \
        "research_note_kept"

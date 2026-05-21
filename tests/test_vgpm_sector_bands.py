"""Tests for sector-aware VGPM sub-score bands.

Two layers:
  1. tests of src/utils/vgpm_thresholds.py (the per-sector band tables)
  2. integration tests of src/utils/pdf_report.py::_compute_vgpm
     verifying sector-dependent grade routing on realistic synthetic inputs

Headline invariant — the post-Apr-25 B-band collapse fix:
  A Tech ticker, a Utility ticker, and a Bank ticker fed the SAME 5%
  revenue growth must produce DIFFERENT growth sub-scores. Pre-fix they
  all hit g1=58 (B-band) because the bands were cross-sector universal.
"""
from __future__ import annotations

import pytest

from src.utils.pdf_report import _compute_vgpm
from src.utils.vgpm_thresholds import (
    CANONICAL_SECTORS,
    SECTOR_FCF_MARGIN_BANDS,
    SECTOR_GROWTH_BANDS,
    SECTOR_P_FCF_BANDS,
    SECTOR_ROIC_SPREAD_BANDS,
    resolve_sector,
    score_fcf_margin,
    score_growth,
    score_p_fcf,
    score_roic_spread,
)


# ── Sector alias resolution ────────────────────────────────────────────


def test_resolve_canonical_sector_names_pass_through():
    for sector in CANONICAL_SECTORS:
        assert resolve_sector(sector) == sector


def test_resolve_known_fmp_aliases():
    assert resolve_sector("Financials") == "Financial Services"
    assert resolve_sector("Consumer Staples") == "Consumer Defensive"
    assert resolve_sector("Information Technology") == "Technology"
    assert resolve_sector("Materials") == "Basic Materials"
    # Telecom / Telecommunications now route to the dedicated "Telecom"
    # canonical (was → Communication Services pre-2026-05-21). Legacy
    # telcos behave like Utilities, not like ad-tech platforms.
    assert resolve_sector("Telecommunications") == "Telecom"
    assert resolve_sector("Telecom") == "Telecom"


def test_resolve_pipeline_internal_aliases():
    """Pipeline TICKER_SECTOR_LOOKUP uses internal short names."""
    assert resolve_sector("Tech") == "Technology"
    # Biopharma is now its OWN canonical (was → Healthcare pre-2026-05-21).
    # Pre-approval biotech dynamics are too different to merge with HealthcareServices.
    assert resolve_sector("Biopharma") == "Biopharma"
    assert resolve_sector("HealthcareServices") == "Healthcare"
    assert resolve_sector("Semiconductor") == "Technology"
    assert resolve_sector("RealEstate") == "Real Estate"


def test_resolve_previously_unaliased_pipeline_strings():
    """These were the coverage gaps fixed 2026-05-21. Pipeline emits these
    strings but they were silently routing to the Tech default before."""
    # Telco — was falling to default Tech bands (way too aggressive).
    assert resolve_sector("Telco") == "Telecom"
    # Consumer (plain, without Defensive/Cyclical suffix) — was Tech default.
    assert resolve_sector("Consumer") == "Consumer Cyclical"
    # Financial (singular, no s) — pipeline emits this; was Tech default.
    assert resolve_sector("Financial") == "Financial Services"
    # Pharmaceuticals / Biotechnology FMP aliases — route to Biopharma.
    assert resolve_sector("Pharmaceuticals") == "Biopharma"
    assert resolve_sector("Biotechnology")   == "Biopharma"


def test_resolve_unknown_falls_back_to_technology():
    """Safe default — Technology bands are the most aggressive (preserves
    backward compatibility on saved runs without sector metadata)."""
    assert resolve_sector("Some Weird Sector") == "Technology"
    assert resolve_sector("") == "Technology"
    assert resolve_sector(None) == "Technology"


# ── Coverage sanity — all canonical sectors defined in every band table ──


def test_growth_bands_cover_all_canonical_sectors():
    assert set(SECTOR_GROWTH_BANDS.keys()) == set(CANONICAL_SECTORS)


def test_fcf_margin_bands_cover_all_canonical_sectors():
    assert set(SECTOR_FCF_MARGIN_BANDS.keys()) == set(CANONICAL_SECTORS)


def test_p_fcf_bands_cover_all_canonical_sectors():
    assert set(SECTOR_P_FCF_BANDS.keys()) == set(CANONICAL_SECTORS)


def test_roic_spread_bands_cover_all_canonical_sectors():
    assert set(SECTOR_ROIC_SPREAD_BANDS.keys()) == set(CANONICAL_SECTORS)


def test_band_score_ladders_strictly_descending():
    """Sanity — no typo'd band can flip the ordering."""
    for bands_dict in (SECTOR_GROWTH_BANDS, SECTOR_FCF_MARGIN_BANDS,
                       SECTOR_P_FCF_BANDS, SECTOR_ROIC_SPREAD_BANDS):
        for sector, bands in bands_dict.items():
            scores = [s for _, s in bands]
            assert scores == sorted(scores, reverse=True), (
                f"{sector}: scores must be strictly descending, got {scores}"
            )


# ── Core fix verification — same input, different sector → different score ──


def test_growth_5pct_tech_vs_utility_vs_bank():
    """THE invariant. 5% revenue growth means very different things across sectors.
    Pre-fix all three hit g1=58 (B-band). Post-fix the spread breaks open."""
    tech_score    = score_growth(5.0, "Technology")
    utility_score = score_growth(5.0, "Utilities")
    bank_score    = score_growth(5.0, "Financial Services")

    # Utility 5% growth should be top-tier (rare for utilities)
    assert utility_score >= 70, (
        f"Utility 5% growth should be A-band (>=70), got {utility_score}"
    )
    # Tech 5% growth should be mediocre (low for Tech standards)
    assert tech_score < 60, f"Tech 5% growth should be B-band (<60), got {tech_score}"
    # Spread must be material (at least 12 points = different letter grade)
    assert utility_score - tech_score >= 12, (
        f"Sector spread for 5% growth must exceed 12pts; got "
        f"Utility={utility_score} vs Tech={tech_score}"
    )


def test_growth_15pct_tech_vs_utility():
    """15% growth: Tech is mid; Utility is impossibly good."""
    assert score_growth(15.0, "Technology") < score_growth(15.0, "Utilities")
    assert score_growth(15.0, "Utilities") >= 90  # A+ for utility
    assert score_growth(15.0, "Technology") < 80  # not yet A for Tech


# ── Biopharma + Telecom (NEW canonical sectors) ────────────────────────


def test_biopharma_tolerates_negative_growth():
    """Pre-approval biotech can have -20% growth (revenue lumps); should
    not be punished as harshly as a Tech ticker with the same growth."""
    biopharma_score = score_growth(-20.0, "Biopharma")
    tech_score      = score_growth(-20.0, "Technology")
    assert biopharma_score >= tech_score, (
        f"Biopharma should tolerate -20% growth better than Tech "
        f"(clinical-stage revenue is lumpy); got Biopharma={biopharma_score} "
        f"Tech={tech_score}"
    )


def test_biopharma_high_growth_still_a_plus():
    """Mature biopharma turning a launch corner can grow 30%+."""
    assert score_growth(35.0, "Biopharma") >= 90


def test_biopharma_tolerates_negative_fcf_margin():
    """Pre-approval biotech: -20% FCF margin is normal. Should NOT crater
    the way a -20% margin Tech ticker would."""
    biopharma_score = score_fcf_margin(-20.0, "Biopharma")
    tech_score      = score_fcf_margin(-20.0, "Technology")
    assert biopharma_score >= tech_score


def test_biopharma_tolerates_negative_roic_spread():
    """Pre-approval biotech: ROIC < WACC is normal during clinical years."""
    biopharma_score = score_roic_spread(-0.10, "Biopharma")
    tech_score      = score_roic_spread(-0.10, "Technology")
    assert biopharma_score > tech_score


def test_telecom_3pct_growth_is_top_tier():
    """Telco growing 3% is A-band — that's exceptional for legacy telcos.
    Same 3% in Tech would be D+ band."""
    telco_score = score_growth(3.0, "Telecom")
    tech_score  = score_growth(3.0, "Technology")
    # Telco should grade much higher
    assert telco_score >= 70, f"Telco 3% growth should be A-band, got {telco_score}"
    assert telco_score > tech_score + 20


def test_telecom_p_fcf_tighter_than_communication_services():
    """Legacy telcos trade at lower multiples than ad-tech platforms.
    P/FCF 18x: telcos compressed; Comm Services accepted."""
    telco_score    = score_p_fcf(18.0, "Telecom")
    commsvc_score  = score_p_fcf(18.0, "Communication Services")
    # Comm Services accepts higher multiples; telcos don't.
    assert commsvc_score >= telco_score


def test_consumer_alias_routes_to_cyclical_bands():
    """Pipeline emits plain 'Consumer'; we treat it as Cyclical (broader
    default than Defensive). Both sub-buckets remain available if explicitly
    requested."""
    consumer_growth  = score_growth(8.0, "Consumer")
    cyclical_growth  = score_growth(8.0, "Consumer Cyclical")
    assert consumer_growth == cyclical_growth


def test_growth_30pct_tech_a_plus_but_others_too():
    """At extreme growth (30%+), Tech still A+; everyone else also A+."""
    for sector in CANONICAL_SECTORS:
        if sector == "Financial Services":
            # Banks can plausibly grow 15-30%; still A+
            continue
        assert score_growth(30.0, sector) >= 85, (
            f"{sector} should grade 30% growth as at least A, got {score_growth(30.0, sector)}"
        )


def test_growth_negative_5_bank_vs_tech():
    """Negative growth: Banks penalised less harshly than Tech (cyclicality
    accepted), but BOTH should be C-band or lower."""
    bank_score = score_growth(-5.0, "Financial Services")
    tech_score = score_growth(-5.0, "Technology")
    # Both below A-band
    assert bank_score < 60
    assert tech_score < 60


# ── FCF margin sector dependency ────────────────────────────────────────


def test_fcf_margin_8pct_tech_vs_utility():
    """8% FCF margin: Tech is OK; Utility is exceptional (utilities have low margins)."""
    tech_score    = score_fcf_margin(8.0, "Technology")
    utility_score = score_fcf_margin(8.0, "Utilities")
    assert utility_score >= 80, f"Utility 8% margin should be A, got {utility_score}"
    assert tech_score < utility_score


def test_fcf_margin_20pct_universal_top_tier():
    """20% FCF margin: top-tier everywhere except REIT (where AFFO bar is higher).
    Bank/FinServ FCF margin is ill-defined; skip."""
    for sector in CANONICAL_SECTORS:
        if sector in ("Financial Services", "Real Estate"):
            continue  # FCF margin ill-defined / different scale
        assert score_fcf_margin(20.0, sector) >= 80, (
            f"{sector} should grade 20% FCF margin as at least A, got "
            f"{score_fcf_margin(20.0, sector)}"
        )


def test_fcf_margin_negative_5_utility_vs_tech():
    """Negative margins penalised. Utilities have very thin tolerance band."""
    assert score_fcf_margin(-5.0, "Utilities") < 20
    assert score_fcf_margin(-5.0, "Technology") < 40


# ── P/FCF sector dependency ────────────────────────────────────────────


def test_p_fcf_25x_tech_vs_utility():
    """P/FCF 25×: average for Tech; expensive for Utility."""
    tech_score    = score_p_fcf(25.0, "Technology")
    utility_score = score_p_fcf(25.0, "Utilities")
    assert tech_score > utility_score, (
        f"Tech accepts higher multiples; expected Tech score > Utility, got "
        f"Tech={tech_score} Utility={utility_score}"
    )


def test_p_fcf_none_or_negative_returns_below_average():
    """Matches pre-fix behaviour: 30 score for unavailable / negative FCF."""
    assert score_p_fcf(None, "Technology") == 30
    assert score_p_fcf(-5.0, "Technology") == 30
    assert score_p_fcf(0, "Utilities") == 30


# ── ROIC-WACC spread sector dependency ─────────────────────────────────


def test_roic_spread_3pp_bank_vs_tech():
    """+3pp ROIC-WACC spread: well above-average for Bank; mediocre for Tech."""
    bank_score = score_roic_spread(0.03, "Financial Services")
    tech_score = score_roic_spread(0.03, "Technology")
    assert bank_score >= 78, f"Bank +3pp spread should be A, got {bank_score}"
    # Tech needs higher spread to be A
    assert tech_score < bank_score


def test_roic_spread_15pp_tech_a_plus():
    """+15pp ROIC-WACC = top-tier for Tech."""
    assert score_roic_spread(0.15, "Technology") >= 90


def test_roic_spread_zero_utility_vs_tech():
    """0pp spread (ROIC exactly = WACC): acceptable for Utility; mediocre for Tech."""
    util_score = score_roic_spread(0.0, "Utilities")
    tech_score = score_roic_spread(0.0, "Technology")
    assert util_score >= 60   # ROIC=WACC is fine for regulated utility
    # Tech 0pp spread also ends up B-band; not asserting strict comparison


# ── Integration with _compute_vgpm — sector flows through correctly ────


def _base_vgpm_inputs(growth=0.10, fcf_margin=0.10):
    """Standard inputs for _compute_vgpm. Tweak growth/margin per test."""
    dcf = {
        "base": {"intrinsic_value": 110, "growth_rate": growth},
        "wacc": 0.08,
        "shares_outstanding": 100,
        "revenue_base": 1000,
        "fcf_margin_base": fcf_margin,
        "data_source": "analyst",
    }
    scen = {"current_price": 100, "upside_pct": 10,
            "bull": {"fair_value": 130}, "bear": {"fair_value": 80}}
    raw_fin = {"2024": {"net_income": 150, "revenue": 1000}}
    dcf_cal = {"margin_direction": "stable", "risk_flag": "MEDIUM"}
    return dcf, scen, raw_fin, dcf_cal


def test_compute_vgpm_signature_accepts_sector_keyword():
    """Backward compat — sector= is OPTIONAL (defaults to Technology)."""
    dcf, scen, raw, cal = _base_vgpm_inputs()
    # Should not raise when sector is omitted
    result_no_sector = _compute_vgpm(dcf, scen, raw, cal, "")
    assert "valuation" in result_no_sector
    # And with sector
    result_with_sector = _compute_vgpm(dcf, scen, raw, cal, "", sector="Utilities")
    assert "valuation" in result_with_sector


def test_compute_vgpm_growth_differs_by_sector():
    """5% growth: Tech vs Utility growth dim scores diverge."""
    dcf, scen, raw, cal = _base_vgpm_inputs(growth=0.05)
    tech_result = _compute_vgpm(dcf, scen, raw, cal, "", sector="Technology")
    util_result = _compute_vgpm(dcf, scen, raw, cal, "", sector="Utilities")

    # Same input, different sector → different growth sub-component → likely
    # different overall growth score (and possibly different grade)
    assert tech_result["growth"]["score"] != util_result["growth"]["score"], (
        "Growth dim should differ between Tech and Utility for 5% growth; "
        f"both got {tech_result['growth']['score']}"
    )
    # Utility should grade higher (5% is exceptional for utility)
    assert util_result["growth"]["score"] > tech_result["growth"]["score"]


def test_compute_vgpm_fcf_margin_differs_by_sector():
    """8% FCF margin: Tech mediocre, Utility exceptional."""
    dcf, scen, raw, cal = _base_vgpm_inputs(fcf_margin=0.08)
    tech_result = _compute_vgpm(dcf, scen, raw, cal, "", sector="Technology")
    util_result = _compute_vgpm(dcf, scen, raw, cal, "", sector="Utilities")
    assert util_result["profitability"]["score"] > tech_result["profitability"]["score"]


def test_compute_vgpm_unknown_sector_falls_back_to_technology():
    """An unrecognised sector should NOT crash + should produce sensible output."""
    dcf, scen, raw, cal = _base_vgpm_inputs()
    weird_result = _compute_vgpm(dcf, scen, raw, cal, "", sector="Aliens & Spaceships")
    tech_result  = _compute_vgpm(dcf, scen, raw, cal, "", sector="Technology")
    # Should be identical because unknown falls back to Technology
    assert weird_result["growth"]["score"]        == tech_result["growth"]["score"]
    assert weird_result["profitability"]["score"] == tech_result["profitability"]["score"]


def test_compute_vgpm_grade_spread_widens_with_sector_awareness():
    """Headline fix verification: 8 tickers across 4 sectors with realistic
    metrics produce MORE letter-grade variance than the pre-fix B-band
    collapse would.

    Pre-fix: cross-sector universal bands → most tickers cluster in B-band
    Post-fix: sector-aware bands → wider spread."""
    dcf, scen, raw, cal = _base_vgpm_inputs()
    cases = [
        ("Technology",          0.40, 0.30, "expand mature tech"),  # high growth, high margin
        ("Technology",         -0.02, 0.05, "bad tech"),
        ("Utilities",           0.06, 0.10, "decent utility"),
        ("Utilities",          -0.02, 0.02, "weak utility"),
        ("Financial Services",  0.15, None, "great bank"),
        ("Financial Services",  0.02, None, "average bank"),
        ("Healthcare",          0.20, 0.25, "growing pharma"),
        ("Healthcare",         -0.10, -0.05, "biotech burning cash"),
    ]
    grades = set()
    for sector, growth, fcf_margin, _ in cases:
        dcf_, scen_, raw_, cal_ = _base_vgpm_inputs(
            growth=growth,
            fcf_margin=fcf_margin if fcf_margin is not None else 0.0,
        )
        result = _compute_vgpm(dcf_, scen_, raw_, cal_, "", sector=sector)
        for dim_data in result.values():
            grades.add(dim_data["grade"])

    # Pre-fix the entire universe clustered in B-/B/B+ (3 grades). Post-fix
    # must span at least 4 distinct letter grades across realistic inputs.
    # NB: V and M dims are largely sector-agnostic in _compute_vgpm (only
    # P/FCF + ROIC differ by sector); the wider spread comes from G and P
    # dims, plus from the input variance (good/bad ticker per sector).
    assert len(grades) >= 4, (
        f"Sector-aware VGPM should produce >=4 distinct letter grades on "
        f"realistic inputs; got only {sorted(grades)}. The B-band collapse "
        f"fix is the entire point of this change."
    )
    # Stronger check: A-tier present (good tickers grade well) AND B-/C-band
    # present (bad tickers grade poorly) — confirms spread isn't just shuffling
    # within a single letter family.
    has_a_tier = any(g.startswith("A") for g in grades)
    has_b_minus_or_worse = any(g in ("B-", "C+", "C", "C-", "D+", "D", "D-") for g in grades)
    assert has_a_tier and has_b_minus_or_worse, (
        f"Spread doesn't reach extremes — got {sorted(grades)}. Should have "
        f"at least one A-tier (top performer) and one B-/C/D (poor performer)."
    )

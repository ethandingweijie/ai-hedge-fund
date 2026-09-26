"""Wave 5, health care (owner spec and universe decisions, 2026-09-26).

Stage 0 missed on routing: none of the health labels was in scope, so 12 of
16 names fell to the ratio ladder. This file pins the Stage 4 fix: the labels
in scope with the two rows they lacked, the Commercial Biotech profile the
registry lacked, the pins, and the review-gated pipeline pre-fill that is the
rNPV leg's only source on a revenue name (quarantined until accepted, with
the blend re-weighting to 1.0 when absent).
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.industry import gemini_params as gp
from src.data import industry_inputs as ii
from src.data import industry_profile_map as ipm
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES
LABELS = {
    "Drug Manufacturers - General": ("Biopharma", "Large Cap Pharma"),
    "Biotechnology": ("Biopharma", "Pre-approval Biotech"),          # default row; commercial names are pinned
    "Medical - Devices": ("Biopharma", "MedTech / Devices"),
    "Medical - Instruments & Supplies": ("Biopharma", "MedTech / Devices"),
    "Medical - Healthcare Plans": ("HealthcareServices", "Managed Care"),
    "Medical - Care Facilities": ("HealthcareServices", "Healthcare Providers / Services"),
    "Medical - Diagnostics & Research": ("Biopharma", "CDMO / Life Science Tools"),
}


def test_every_health_label_is_in_scope_and_routes():
    scope = ipm.routing_scope()
    for label, target in LABELS.items():
        assert label in scope, label
        assert ipm.profile_for_industry(label) == target, label
    assert "Software - Application" not in scope                     # VEEV stays on Tech; out of the wave
    assert ipm.market_map("SG")["Medical - Care Facilities"] == ("Healthcare", "Healthcare Provider (SG)")


def test_the_commercial_biotech_profile_is_the_owner_spec():
    p = P["Biopharma"]["Commercial Biotech"]
    w = {m["name"]: m["weight"] for m in p["methods"]}
    assert w == {"Forward P/E": 0.35, "EV/Fwd Rev": 0.25, "DCF": 0.25, "rNPV (Pipeline)": 0.15}
    assert sum(w.values()) == pytest.approx(1.0)
    assert [m["name"] for m in p["methods"] if m.get("anchor")] == ["Forward P/E"]
    assert all(m["implementable"] for m in p["methods"])
    assert {"EV/R&D", "Cash Runway"} <= set(p["excluded"])
    assert sp.RNPV_COMMERCIAL_DEFAULTS["Commercial Biotech"]["peak_op_margin"] == 0.42
    assert sp.SECTOR_PEER_MULTIPLES["Commercial Biotech"]["pe_ntm"] == 17.2


@pytest.mark.parametrize("ticker,profile", [
    ("VRTX", "Commercial Biotech"), ("01801.HK", "Commercial Biotech"),
    ("MDT", "MedTech / Devices"), ("SYK", "MedTech / Devices"), ("BAX", "MedTech / Devices"),
    ("00853.HK", "MedTech / Devices"),
    ("01093.HK", "Large Cap Pharma"), ("LLY", "Large Cap Pharma"),
    ("02269.HK", "CDMO / Life Science Tools"), ("TMO", "CDMO / Life Science Tools"),
    ("MOH", "Managed Care"), ("UNH", "Managed Care"), ("CVS", "Managed Care"),
    ("HCA", "Healthcare Providers / Services"),
])
def test_the_pins_the_owner_decided(ticker, profile):
    assert sp.get_wacc_profile_for_ticker(ticker)[1] == profile


# Owner taxonomy, 2026-09-26: the full seven-profile health-care universe across
# US, HK and SG. Two corrections were flagged and taken: 00992.HK is Lenovo (not
# a MicroPort spin-off; the spin-offs 02160.HK and 02252.HK are pinned) and
# 02162.HK is Kangji Medical, a device maker, so Clover 02197.HK is the Chapter
# 18A name. CTLS is delisted and skipped. 01099.HK and 03320.HK are distributors
# the owner placed on the provider profile.
OWNER_TAXONOMY = {
    "Large Cap Pharma": ["JNJ", "ABBV", "MRK", "BMY", "LLY", "PFE", "AMGN", "01177.HK", "03692.HK", "02196.HK", "01093.HK"],
    "Commercial Biotech": ["VRTX", "REGN", "BIIB", "ALNY", "BGNE", "01801.HK", "09926.HK", "06160.HK", "06990.HK"],
    "Pre-approval Biotech": ["CRSP", "BEAM", "KYMR", "02197.HK", "09688.HK"],
    "MedTech / Devices": ["MDT", "SYK", "BAX", "ABT", "BSX", "ISRG", "EW", "BDX",
                          "00853.HK", "01666.HK", "02190.HK", "02160.HK", "02252.HK"],
    "CDMO / Life Science Tools": ["TMO", "DHR", "ILMN", "A", "CRL", "IQV", "02269.HK", "02268.HK", "01548.HK", "03759.HK", "02359.HK"],
    "Managed Care": ["UNH", "CVS", "MOH", "ELV", "CI", "CNC", "HUM"],
    "Healthcare Providers / Services": ["HCA", "THC", "UHS", "ENSG", "01099.HK", "03320.HK", "01515.HK", "06078.HK"],
    "Healthcare Provider (SG)": ["Q0F.SI", "A50.SI", "QC7.SI"],
}


@pytest.mark.parametrize("profile,tickers", list(OWNER_TAXONOMY.items()))
def test_the_owner_taxonomy_resolves_every_name_to_its_profile(profile, tickers):
    assert [sp.get_wacc_profile_for_ticker(t)[1] for t in tickers] == [profile] * len(tickers)


def test_the_two_flagged_tickers_are_not_pinned_to_health_profiles():
    assert sp.get_wacc_profile_for_ticker("00992.HK")[1] != "MedTech / Devices"     # Lenovo
    assert sp.get_wacc_profile_for_ticker("02162.HK")[1] != "Pre-approval Biotech"  # Kangji Medical
    assert sp.get_wacc_profile_for_ticker("HCA")[0] == "HealthcareServices"
    assert sp.get_wacc_profile_for_ticker("01515.HK")[0] == "HealthcareServices"


# ── the pipeline pre-fill and the quarantined leg ────────────────────────────

def _pipe_doc():
    cite = {"currency": "USD", "scale": "bn", "period": "peak (2031E)", "source_url": "https://example.com/r", "quote": "q"}
    return {"as_of": "2026-09-26", "assets": [
        {"name": "orforglipron", "indication": "obesity", "phase": "phase_3", "launch_year": 2027, "patent_expiry": 2039,
         "peak_sales": {**cite, "value": 15.0},
         "ptrs": {"value": 0.70, "period": "benchmark", "source_url": "https://example.com/p", "quote": "metabolic Ph3 65-75%"},
         "ptrs_basis": "metabolic Phase 3 benchmark"},
        {"name": "retatrutide", "indication": "obesity", "phase": "phase_3", "launch_year": 2028,
         "peak_sales": {**cite, "value": 20.0},
         "ptrs": {"value": 95.0, "period": "n/a", "source_url": "not a url", "quote": ""}},      # uncited: table prices it
        {"name": "uncited asset", "indication": "oncology", "phase": "phase_2",
         "peak_sales": {**cite, "value": 3.0, "source_url": "no"}},                                # dropped
    ]}


def test_the_bridge_builds_the_assets_the_rnpv_leg_consumes():
    assets, checks = gp.pipeline_to_engine_assets(_pipe_doc(), fx_to_usd=lambda c: 1.0)
    assert [a["name"] for a in assets] == ["orforglipron", "retatrutide"]
    a = assets[0]
    assert a["peak_sales_usd"] == 15e9 and a["phase"] == "phase_3" and a["launch_year"] == 2027
    assert a["ptrs_override"] == 0.70 and a["source"] == "gemini_accepted"
    assert "ptrs_override" not in assets[1]                          # uncited PTRS ignored, reported
    assert checks["ptrs_ignored"] == ["retatrutide"] and checks["dropped_assets"] == ["uncited asset"]
    assert gp.INDUSTRY_INPUT_SCHEMAS["pipeline"] is gp.PipelineInputs
    assert "pipeline" in ii.KINDS and "pipeline" in ii.NO_OVERLAY and ii.BOUNDS["pipeline"][0] == "revenue"


def test_the_prompt_asks_for_late_stage_assets_peak_sales_and_ptrs_benchmarks():
    p = gp.pipeline_prompt("Eli Lilly", "LLY", {"revenue_latest_usd_bn": 60.0})
    assert "LATE-STAGE assets only" in p and "CONSENSUS PEAK annual sales" in p
    assert "probability of technical and regulatory success" in p and "oncology Phase 3 about 50-60%" in p
    assert "Do NOT value anything" in p


def test_an_accepted_ptrs_replaces_the_phase_table_in_rnpv():
    src = inspect.getsource(d._compute_rnpv)
    assert '_pos_o = asset.get("ptrs_override")' in src and "pos = float(_pos_o)" in src


def test_the_rnpv_leg_reads_accepted_inputs_first_and_quarantines_revenue_names():
    src = inspect.getsource(d._compute_method_value)
    at = src.index('if method_name in {"rNPV", "rNPV (Pipeline)"}:')
    block = src[at: at + 1200]
    assert 'assets = most_recent.get("pipeline_assets_accepted") or []' in block
    assert 'if (profile_name or "") == "Pre-approval Biotech":' in block
    assert '"_pipeline_quarantine"' in block and "the blend re-weights without the leg" in block
    run = inspect.getsource(d.run_dcf_agent)
    assert '_pipe_e = _ii_p.accepted_entry(ticker, "pipeline")' in run
    assert 'most_recent["pipeline_assets_extractor"] = _ticker_pipeline' in run
    assert 'if most_recent.get("_pipeline_quarantine"):' in run


def test_a_revenue_name_with_no_accepted_pipeline_prices_without_the_leg():
    """The owner's fallback: no synthetic markdown. An uncomputable rNPV returns
    None and the blend renormalises the surviving legs to 1.0."""
    methods = P["Biopharma"]["Commercial Biotech"]["methods"]
    values = {"Forward P/E": 100.0, "EV/Fwd Rev": 90.0, "DCF": 110.0, "rNPV (Pipeline)": None}
    iv, bd = d._blend_methods(methods, values, c_macro=0.0, forward_flags=[], dcf_tv_fraction=0.0)
    assert iv == pytest.approx((0.35 * 100 + 0.25 * 90 + 0.25 * 110) / 0.85)
    assert bd.get("weight_surviving") == pytest.approx(0.85)


def test_the_gate_shows_every_pipeline_asset_with_its_peak_sales_and_ptrs():
    e = {"data": _pipe_doc(), "checks": [], "ok": True, "company": "Eli Lilly", "basis": "estimate"}
    rows = ii.ui_summary(doc={"version": 1, "tickers": {"LLY": {"pipeline": e}}},
                         reviews=lambda *a, **k: {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False})["rows"]
    r = rows[0]
    assert r["kind"] == "pipeline" and r["period"] == "2026-09-26"
    det = r["detail"]
    assert [a["name"] for a in det["assets"]] == ["orforglipron", "retatrutide", "uncited asset"]
    assert det["assets"][0]["peak_sales"]["period_label"] == "peak (2031E)"
    assert det["assets"][0]["ptrs"] == 0.70 and det["assets"][0]["ptrs_basis"] == "metabolic Phase 3 benchmark"

"""Pipeline rules (owner, 2026-10-05): each error found in VRTX's Gemini pre-fill is caught by a rule.

Offline: the ClinicalTrials.gov lookups are injected.
"""
from datetime import date

import pytest

from src.agents.industry.gemini_params import pipeline_to_engine_assets
from src.data import industry_inputs as ii
from src.data import pipeline_rules as pr


def _asset(name, phase="phase_3", peak=2.5, quote=None, url="https://example.com/news", period="Peak", **kw):
    a = {"name": name, "indication": kw.pop("indication", "rare disease"), "phase": phase,
         "launch_year": kw.pop("launch_year", 2029),
         "peak_sales": {"value": peak, "currency": "USD", "scale": "bn", "period": period, "source_url": url,
                        "quote": quote if quote is not None else f"{name} peak sales ${peak} billion"}}
    a.update(kw)
    return a


def _by(checks, name):
    return next(c for c in checks if c["check"] == name)


def test_r1_a_combined_deal_figure_fails():
    # VRTX atumelnant carried "Palsonify and atumelnant will add more than $5 billion" from the deal release.
    assets = [_asset("Atumelnant (CRN04894)", quote="Palsonify and atumelnant will add more than $5 billion combined",
                     url="https://crinetics.com/press-releases/vertex-to-acquire-crinetics-pharmaceuticals/"),
              _asset("Palsonify (paltusotine)", phase="approved")]
    c = _by(pr.check({"assets": assets}), "single-asset peak sales")
    assert c["ok"] is False and "combined" in c["detail"] and "deal announcement" in c["detail"]


def test_r1_an_unnamed_snippet_is_reported_not_failed():
    c = _by(pr.check({"assets": [_asset("Povetacicept", quote="peak global sales of $4.3 billion by 2039",
                                        pdufa_date="2026-11-30", phase="filed", launch_year=2026)]}),
            "single-asset peak sales")
    assert c["ok"] is True and "does not name" in c["detail"]


def test_r2_a_launch_before_the_trial_allows_fails_and_an_interim_filing_is_honoured():
    late = _asset("Inaxaplin (VX-147)", launch_year=2028, primary_completion="2028-06-02")
    assert _by(pr.check({"assets": [late]}), "launch year vs trial dates")["ok"] is False   # floor 2030
    interim = dict(late, interim_readout="2027-02", interim_source_url="https://news.vrtx.com/x")
    assert _by(pr.check({"assets": [interim]}), "launch year vs trial dates")["ok"] is True  # 2027-02 + 22m = 2028
    undated = _asset("Zimislecel", launch_year=2028)
    assert _by(pr.check({"assets": [undated]}), "launch year vs trial dates")["ok"] is False


def test_r2_filed_reads_the_pdufa_date():
    a = _asset("Povetacicept", phase="filed", launch_year=2026, pdufa_date="2026-11-30")
    assert _by(pr.check({"assets": [a]}), "launch year vs trial dates")["ok"] is True
    assert _by(pr.check({"assets": [dict(a, launch_year=2025)]}), "launch year vs trial dates")["ok"] is False


def test_r3_a_peak_after_exclusivity_fails_and_the_biologic_floor_counts():
    # VRTX povetacicept: patent 2035 stored, peak 2039.
    a = _asset("Povetacicept", phase="filed", launch_year=2026, pdufa_date="2026-11-30", patent_expiry=2035, period="2039E")
    assert _by(pr.check({"assets": [a]}), "exclusivity covers the peak")["ok"] is False
    a2 = dict(a, modality="biologic", patent_expiry=2040)          # 2026 + 12 = 2038; patent 2040 covers 2039
    assert _by(pr.check({"assets": [a2]}), "exclusivity covers the peak")["ok"] is True
    assert pr.exclusivity_end(dict(a, modality="small_molecule", orphan=True, patent_expiry=None)) == 2033


def test_r4_ptrs_is_held_within_the_band_and_stale_sources_give_way():
    base = pr.table_ptrs("phase_3", "oncology")
    v, why = pr.band_ptrs(0.95, "phase_3", "oncology", "2026", "2026-10-05")
    assert v == pytest.approx(min(1.0, base + pr.PTRS_BAND)) and "held" in why
    v2, why2 = pr.band_ptrs(0.60, "phase_3", "oncology", "2020", "2026-10-05")
    assert v2 == pytest.approx(base) and "older" in why2
    # The engine conversion applies the band.
    data = {"as_of": "2026-10-05", "assets": [dict(_asset("X", indication="oncology"), ptrs={"value": 0.95, "period": "2026",
                                                                        "source_url": "https://a.b/c", "quote": "95%"})]}
    assets, checks = pipeline_to_engine_assets(data)
    assert assets[0]["ptrs_override"] == pytest.approx(min(1.0, base + pr.PTRS_BAND)) and checks.get("ptrs_banded")


def test_r5_a_missing_live_phase3_fails_and_a_shared_ingredient_does_not_cover_it():
    registry = [{"nct_id": "NCT04786262", "interventions": ["VX-880", "Zimislecel"]},
                {"nct_id": "NCT05331183", "interventions": ["ELX/TEZ/IVA", "VX-445/VX-661/VX-770"]},
                {"nct_id": "NCT07204275", "interventions": ["Povetacicept", "Tacrolimus"]}]
    assets = [_asset("Povetacicept"), _asset("Alyftrek (vanzacaftor/tezacaftor/deutivacaftor)", phase="approved")]
    c = _by(pr.check({"assets": assets}, registry=registry), "pipeline completeness")
    assert c["ok"] is False and "NCT04786262" in c["detail"] and "NCT05331183" in c["detail"]
    data = {"assets": assets + [_asset("Zimislecel (VX-880)")], "approved_portfolio": ["Trikafta (VX-445/VX-661/VX-770)"]}
    assert _by(pr.check(data, registry=registry), "pipeline completeness")["ok"] is True


def test_the_pipeline_rules_are_hard_checks():
    assert set(pr.HARD) <= set(ii.HARD_CHECKS)


def test_r6_freshness_flags_age_passed_events_and_failed_rules():
    e = {"built_at": "2026-06-01T00:00:00+00:00",
         "data": {"assets": [{"name": "Povetacicept", "pdufa_date": "2026-09-30"}]},
         "checks": [{"check": "pipeline completeness", "ok": False}]}
    f = pr.freshness(e, "2026-06-02", today=date(2026, 10, 5))
    assert "days ago" in f and "PDUFA 2026-09-30 has passed" in f and "pipeline completeness" in f
    assert pr.freshness({"built_at": "2026-10-01", "data": {"assets": []}, "checks": []}, "2026-10-02",
                        today=date(2026, 10, 5)) is None


def test_registry_dates_overwrite_a_model_written_date():
    data = {"assets": [_asset("Inaxaplin", nct_ids=["NCT05312879"], primary_completion="2027-01")]}
    out, notes = pr.enrich_dates(data, fetch=lambda n: {"primary_completion": "2028-06-02"})
    assert out["assets"][0]["primary_completion"] == "2028-06-02" and notes


def test_a_partnered_asset_is_valued_at_the_company_share():
    data = {"as_of": "2026-10-05", "assets": [dict(_asset("Casgevy", phase="approved", peak=3.6), economic_share=0.4)]}
    assets, checks = pipeline_to_engine_assets(data)
    assert assets[0]["peak_sales_usd"] == pytest.approx(1.44e9) and checks["economic_share"]


def test_an_accepted_empty_input_does_not_fall_back_to_the_extractor():
    import inspect
    from src.agents.analysis import dcf_agent as d
    src = inspect.getsource(d)
    assert 'if not assets and not most_recent.get("pipeline_input_accepted"):' in src


def test_a_risk_adjusted_peak_is_not_discounted_twice():
    data = {"as_of": "2026-10-05", "assets": [dict(_asset("IBI363", indication="oncology"), peak_risk_adjusted=True,
                                                   ptrs={"value": 0.5, "period": "2026", "source_url": "https://a.b/c", "quote": "50%"})]}
    assets, checks = pipeline_to_engine_assets(data)
    assert assets[0]["ptrs_override"] == 1.0 and checks["risk_adjusted_peaks"] == ["IBI363"]


def test_combination_strings_are_split_into_their_parts():
    reg = [{"nct_id": "NCT07796100", "interventions": ["ZL-1310 in combination with Atezolizumab or Durvalumab"]},
           {"nct_id": "NCT04316364", "interventions": ["SHR-1316、Paclitaxel"]}]
    data = {"assets": [_asset("Zocilurtatug pelitecan (ZL-1310)"), _asset("Adebrelimab (SHR-1316)", phase="approved")]}
    assert _by(pr.check(data, registry=reg), "pipeline completeness")["ok"] is True


def test_the_pm_summary_print_survives_an_unrated_decision():
    # CRSP 2026-10-05: price_target None crashed the pipeline after the valuation had finished.
    import inspect
    from src import pipeline
    src = inspect.getsource(pipeline)
    assert "(d.get('price_target') or 0):.2f" in src and "d.get('price_target', 0):.2f" not in src

"""CFA-grade report audit, owner 2026-11-25. Seven defects seen in the 2026-11-21 production
cycle (DOW, F, FCX, GM, MU, NEM, OXY, UAL, VLO):

1. the risk theme was the one the 300-word cap cut; risks to the rating get their own field;
2. the value-trap checklist read FY2021 (oldest-first, truncated) and a failed call was GREEN;
3. the write-up invented reasons for target / rating changes;
4. basis points printed as "points", "-0.0%", negative "upside";
5. operator jargon (engine, leg, clamp, norm, rolled, gate, cascade) in reader text;
6. the PDF printed "Medium" for a 12-month call and had no disclosures, methodology,
   Street comparison or data limitations;
7. research that contradicts the books was not disclosed as degraded.
"""
import inspect

import pytest

from src.agents import portfolio_manager as pm
from src.agents.pm import reader_text as rt


# ── 1. the risk theme survives the length cap ────────────────────────────────

def _words(n, tag="w"):
    return " ".join([tag] * n)


def test_the_risk_theme_is_kept_and_middle_themes_are_cut_first():
    themes = ["1. Overweight thesis " + _words(100) + ".",
              "2. Valuation basis " + _words(100) + ".",
              "3. Guidance " + _words(100) + ".",
              "4. The primary risk is a margin reset " + _words(60) + "."]
    d = pm._enforce_pm_length({"rationale": "\n".join(themes), "headline": "PT $262."}, "PT $262")
    out = d["rationale"].split("\n")
    assert out[0].startswith("1. Overweight thesis") and "primary risk" in out[-1]
    assert d["rationale_length"]["words_after"] <= pm.PM_RATIONALE_MAX_WORDS
    assert d["rationale_length"]["risk_theme_kept"] is True
    assert [t[:2] for t in out] == [f"{i}." for i in range(1, len(out) + 1)]      # renumbered, no gap


def test_a_rationale_without_a_risk_theme_keeps_the_old_cut():
    rationale = "\n".join(["1. " + _words(120) + ".", "2. " + _words(120) + ".", "3. " + _words(120) + "."])
    d = pm._enforce_pm_length({"rationale": rationale, "headline": "PT $262."}, "PT $262")
    assert d["rationale"].count("\n") == 1 and "risk_theme_kept" not in d["rationale_length"]


def test_risks_to_rating_are_computed_on_the_target_rule():
    view = {"rating_label": "Overweight", "dividend_yield": 0.0,
            "benchmark": {"name": "S&P 500", "expected_return": 0.084}}
    bridge = {"scenarios": {"bear": {"intrinsic_value": 810.76, "target": 702.63, "total_return": 0.1828}}}
    scen = {"bear": {"assumptions": "AI memory demand peaks mid-FY27. More text."}}
    trap = {"insider_behaviour": {"status": "AMBER", "evidence": "Net insider selling of $240M. Second."}}
    r = rt.build_risks_to_rating(rating_label="Overweight", spot=594.51, target=846.13, view=view,
                                 pt_bridge=bridge, scenario=scen, trap=trap, sym="US$")
    assert 2 <= len(r) <= 4
    assert r[0].startswith("Downside risk: in our bear case (AI memory demand peaks mid-FY27)")
    assert "US$703" in r[0]
    assert "falls to Neutral if the 12-month target drops below about US$674" in r[1]   # 594.51 x 1.134
    assert r[2].startswith("Insider selling (AMBER): Net insider selling of $240M")


def test_the_pm_publishes_risks_to_rating_and_the_pdf_renders_them():
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    assert 'd["risks_to_rating"] = _brr(' in src
    from src.utils import pdf_report as pr
    assert "RISKS TO RATING" in inspect.getsource(pr._risks_to_rating_block)


# ── 2. value-trap checklist ──────────────────────────────────────────────────

def test_the_trap_agent_reads_the_newest_years_first_and_complete():
    from src.agents.analysis import value_trap_agent as vt
    raw = {f"FY{y}": {"revenue": 100e9 + y, "free_cash_flow": -1e9 if y == 2021 else 5e9,
                      "dividends_and_distributions": -0.5e9, "net_debt": 10e9, "ebitda": 20e9,
                      "period_end": f"{y}-12-31", "noise": "x" * 500} for y in range(2018, 2026)}
    txt = vt.financials_for_trap(raw)
    lines = txt.split("\n")
    assert lines[0].startswith("Fiscal years FY2025 back to FY2021, newest first")
    assert [ln.split(":")[0].split(" ")[0] for ln in lines[1:]] == ["FY2025", "FY2024", "FY2023", "FY2022", "FY2021"]
    assert "FCF payout 10%" in lines[1] and "net debt/EBITDA 0.50x" in lines[1]
    assert "FCF payout n/m (FCF negative)" in lines[-1] and "noise" not in txt
    assert 'str(raw_financials)[:2000]' not in inspect.getsource(vt.run_value_trap_agent)


def test_a_failed_trap_call_is_not_assessed_never_green():
    from src.agents.analysis import value_trap_agent as vt
    from src.data.models import ValueTrapCheck, ValueTrapOutput
    out = vt.not_assessed_output().model_dump()
    assert out["overall_verdict"] == "TRAP RISK NOT ASSESSED"
    assert {out[k]["status"] for k in out if k != "overall_verdict"} == {"NOT ASSESSED"}
    assert "default_factory=not_assessed_output" in inspect.getsource(vt.run_value_trap_agent)
    assert ValueTrapCheck(status="N/A", evidence="").status == "NOT ASSESSED"
    assert ValueTrapCheck(status="clear", evidence="").status == "GREEN"
    assert ValueTrapOutput.model_validate({**out, "overall_verdict": "not assessed"}).overall_verdict \
        == "TRAP RISK NOT ASSESSED"


# ── 3. change attribution ────────────────────────────────────────────────────

_MU_PRIOR = {"run_at": "2026-11-12T07:23:58+00:00", "price_at_run": 811.3, "final_action": "BUY",
             "recap_json": {"price_target": 1434.78, "price_at_run": 811.3,
                            "rationale": "Our 12-month price target of US$1,435 is set at 50% convergence toward..."}}


def _mu_attr():
    return rt.build_change_attribution(_MU_PRIOR, spot=594.51, blended_iv=1097.75, capture=0.5, target=846.13,
                                       rating_label="Overweight", capture_reason="50%: all scenarios above spot",
                                       sym="US$")


def test_the_target_change_decomposes_exactly_on_the_target_rule():
    a = _mu_attr()
    assert a["rating_change"] == "unchanged" and a["capture_changed"] is False
    assert a["prior"]["capture"] == 0.5 and a["prior"]["capture_source"] == "prior report text"
    dr = a["drivers"]
    assert dr["spot"] == pytest.approx(-108.4, abs=0.1) and dr["intrinsic_value"] == pytest.approx(-480.3, abs=0.1)
    assert dr["capture"] == 0 and abs(dr["residual"]) < 0.01
    assert a["dominant_driver"] == "intrinsic_value"
    assert "We maintain our Overweight rating" in a["sentence"]
    assert "a lower intrinsic value (about US$2,058 to US$1,098)" in a["sentence"]
    assert "never 'upgrade'" in a["prompt_block"] and "do not call it 'mechanical'" in a["prompt_block"]


def test_false_change_narratives_are_replaced_by_the_computed_one():
    text = ("Micron warrants an Overweight on HBM.\n"
            "The price target has been revised to US$846, reflecting a spot correction and the application of a "
            "50% convergence rule that now points to a lower anchor. The magnitude of the downward revision is "
            "mechanical rather than fundamental. The thesis is intact.")
    out, hits = rt.check_change_claims(text, _mu_attr())
    assert "mechanical" not in out and "50% convergence rule" not in out
    assert out.count("We maintain our Overweight rating") == 1 and "The thesis is intact." in out
    assert len(hits) == 2
    # NEM: "Upgraded from SELL" when the rating did not change
    nem = rt.build_change_attribution({"final_action": "SELL", "recap_json": {"price_target": 107.22,
                                                                               "price_at_run": 118.47}},
                                      spot=94.9, blended_iv=67.0, capture=0.5, target=81.0,
                                      rating_label="Underweight", sym="US$")
    out2, hits2 = rt.check_change_claims("1. Underweight vs S&P 500. Upgraded from SELL at US$118, so the thesis "
                                         "migrated.", nem)
    assert "Upgraded" not in out2 and "We maintain our Underweight rating" in out2 and hits2


def test_a_capture_attributed_to_the_macro_regime_is_removed_with_its_follow_on():
    gm = rt.build_change_attribution({"final_action": "BUY", "recap_json": {"price_target": 165.2,
                                                                              "price_at_run": 88.9}},
                                     spot=78.6, blended_iv=168.7, capture=0.5, target=123.67,
                                     rating_label="Overweight", sym="US$")
    text = ("GM is at trough margins. The reduction reflects a tighter 50%-of-gap convergence assumption in a "
            "risk-off regime. Those conditions cap conviction and prevent anchoring. Warranty savings of US$1.0B.")
    out, hits = rt.check_change_claims(text, gm)
    assert "tighter" not in out and "Those conditions" not in out and "Warranty savings" in out
    assert len(hits) == 2


def test_a_plain_statement_of_the_capture_is_not_a_change_claim():
    oxy = rt.build_change_attribution({"final_action": "SELL", "recap_json": {"price_target": 55.0,
                                                                                "price_at_run": 58.0,
                                                                                "capture": 0.35}},
                                      spot=57.2, blended_iv=49.0, capture=0.35, target=52.26,
                                      rating_label="Underweight", sym="US$")
    s = "The derivation reflects 35% convergence toward the US$49 intrinsic value, with cross-checks consistent."
    assert rt.check_change_claims(s, oxy) == (s, [])


def test_an_initiation_says_so_and_never_upgrades():
    a = rt.build_change_attribution(None, spot=10, blended_iv=12, capture=0.35, target=10.7,
                                    rating_label="Overweight")
    assert a["rating_change"] == "initiation" and "initiation" in a["prompt_block"]
    out, hits = rt.check_change_claims("Upgraded to Overweight on the margin story.", a)
    assert hits and "Upgraded" not in out


def test_the_pm_prompt_carries_the_change_block_and_the_post_check_runs_after_the_stylist():
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    assert '"{change_block}' in src and '"change_block": _change_block' in src
    assert src.index("restyle_rationale(") < src.index("_finalise_reader_text(d, _change_attr, _inputs_text)")
    P = pm._PM_RATIONALE_SYSTEM_PROMPT
    assert "ONLY permitted explanation of a change in rating or target" in P and "\"mechanical\"" in P


def test_the_recap_stores_what_the_next_attribution_needs():
    from src.memory.report_recap import extract_structured
    s = extract_structured({"decisions": {"MU": {"action": "BUY", "rating_label": "Overweight"}},
                            "data": {"scenario_analysis": {"MU": {"reconciliation": {"blended_iv": 1097.75}}},
                                     "dcf_range": {"MU": {"pt_bridge": {"capture": 0.5, "capture_reason": "r"}}}}},
                           "MU")
    assert (s["rating_label"], s["blended_iv"], s["capture"], s["capture_reason"]) == ("Overweight", 1097.75, 0.5, "r")


# ── 4. units ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("trails the index by about 2,180 points.", "trails the index by about 22 percentage points."),
    ("implies roughly 3,400 basis points of outperformance", "implies about 34 percentage points of outperformance"),
    ("a spread of 651 bps", "a spread of about 7 percentage points"),
    ("margin change of -0.0% this year", "margin change of 0% this year"),
    ("a -8.7% upside to target", "a 8.7% downside to target"),
    ("Upside to target -14% from here", "Downside to target 14% from here"),
])
def test_unit_errors_are_rewritten(src, want):
    out, hits = rt.lint_units(src)
    assert out == want and hits


def test_the_writer_gets_a_percentage_point_gap_not_basis_points():
    view = {"rating_label": "Underweight", "benchmark": {"name": "S&P 500", "expected_return": 0.084},
            "trade_action": "SELL", "tsr_12m": -0.134, "compliance": {"notes": []},
            "callout": "Rating is based on 12-month TSR: -13.4% TSR, against S&P 500 expected 8.4% (-2180 bps)."}
    blk = pm._rating_block_text(view)
    assert "bps" not in blk and "about 22 percentage points behind it" in blk
    P = pm._PM_RATIONALE_SYSTEM_PROMPT
    assert "about 29 percentage points" in P and "never bare" in P and "never basis points" in P


# ── 5. jargon ────────────────────────────────────────────────────────────────

def test_operator_jargon_is_rewritten_and_a_real_engine_is_only_flagged():
    s = ("RNAV was unavailable; weight rolled into EV/EBITDA (norm). The EV/OCF leg lands at US$62. The terminal "
         "value was clamped to 7.6x. The engine is pricing peak. A gate cascade applied. GE's jet engine franchise.")
    out, hits = rt.lint_jargon(s)
    for bad in ("rolled", "(norm)", " leg ", "clamped", "The engine", " gate ", "cascade"):
        assert bad not in out, bad
    assert "weight moved to EV/EBITDA (normalised)" in out and "Our model is pricing peak" in out
    assert "jet engine franchise" in out and any("flag only" in h for h in hits)
    assert "Banned-words rule" in pm._PM_RATIONALE_SYSTEM_PROMPT


def test_finalise_applies_every_check_and_holds_the_cap():
    d = {"rationale": "1. Overweight. The EV/EBITDA leg gives about 2,463 points of gap.", "headline": "PT $50."}
    out = pm._finalise_reader_text(d, None, "PT $50")
    assert "method" in out["rationale"] and "about 25 percentage points" in out["rationale"]
    assert out["reader_text_lint"]["units"] and out["reader_text_lint"]["jargon"]


# ── 6. PDF + disclosures payload ─────────────────────────────────────────────

def _run(target=846.13):
    t = "MU"
    return {
        "decisions": {t: {"action": "BUY", "price_target": target, "time_horizon": "medium",
                          "rating_label": "Overweight", "writer_model": "claude-test",
                          "risks_to_rating": ["Downside risk: bear case.", "The rating falls to Neutral below X."],
                          "research_view": {"rating_label": "Overweight", "price_as_of": "2026-11-21",
                                            "benchmark": {"name": "S&P 500", "expected_return": 0.084,
                                                          "basis": "market cost of equity"}}}},
        "scenario_analysis": {t: {"current_price": 594.51, "12m_price_target": target,
                                  "reconciliation": {"blended_iv": 1097.75},
                                  "bear": {"probability": 0.25, "fair_value": 810.76},
                                  "base": {"probability": 0.45, "fair_value": 1063.54},
                                  "bull": {"probability": 0.30, "fair_value": 1388.26}}},
        "dcf_range": {t: {"reported_currency": "USD", "profile": "Semiconductor",
                          "consensus_pt": {"low": 1200.0, "high": 3000.0, "consensus": 1588.85},
                          "cycle": {"cyclical": True, "position": "peak", "mid_cycle_margin": 0.2751,
                                    "latest_margin": 0.7459},
                          "street_consensus": {"eps": {"fy1": 175.61}},
                          "guidance_estimates": {"estimates": {"base": {"eps_fy1": 152.6}}},
                          "methods_unavailable": ["EV/EBIT (norm)"],
                          "pt_bridge": {"capture": 0.5, "capture_reason": "50%: bear, base and bull all sit above "
                                                                          "spot, so the rule adds 15 points (cap 50%)",
                                        "scenarios": {"bear": {"intrinsic_value": 810.76, "target": 702.63},
                                                      "base": {"intrinsic_value": 1063.54, "target": 829.02},
                                                      "bull": {"intrinsic_value": 1388.26, "target": 991.38}}}}},
        "extractor_failures": {t: [{"extractor": "insurance_metrics", "stage": "empty"},
                                   {"extractor": "guidance_estimates", "stage": "empty"}]},
        "value_trap_analysis": {t: {"overall_verdict": "TRAP RISK NOT ASSESSED"}},
        "consistency_flags": {t: "⚠ conviction capped at 0.75: risk-off | entry_range [1, 2] implausible vs spot"},
        "research_degraded": True,
        "research_degraded_reasons": [{"source": "books_divergence", "text": "The research quotes revenue 72% below "
                                                                             "the reported figure."}],
    }


def test_the_disclosures_payload_carries_every_item():
    from src.utils.report_disclosures import build_report_disclosures
    d = build_report_disclosures(_run(), "MU")
    assert d["horizon"] == "12 months" and "500 bps" in d["rating_definition"]
    assert d["benchmark"]["name"] == "S&P 500" and d["model"] == "claude-test" and "claude-test" in d["ai_disclosure"]
    assert d["as_of"] == "2026-11-21" and d["data_sources"] and d["limitations"]
    tm = d["target_methodology"]
    assert tm["capture"] == 0.5 and "15 percentage points" in tm["text"] and "base US$1,064 (45%)" in tm["text"]
    vc = d["vs_consensus"]
    assert vc["applies"] and vc["driver"] == "cycle_normalisation"
    assert "below the Street range of US$1,200–US$3,000" in vc["text"] and "13% below consensus" in vc["text"]
    lim = d["data_limitations"]
    assert "The research quotes revenue 72% below the reported figure." in lim
    assert any("EV/EBIT (normalised)" in x for x in lim)
    assert any("guidance estimates" in x for x in lim) and not any("insurance" in x for x in lim)
    assert any("value-trap checklist could not be run" in x for x in lim)
    assert any("Entry_range" in x or "entry_range" in x for x in lim) and not any("conviction" in x for x in lim)
    inside = build_report_disclosures(_run(target=1500.0), "MU")["vs_consensus"]
    assert inside["applies"] is False and inside["text"] is None


def test_the_pipeline_publishes_report_disclosures():
    from src import pipeline
    src = inspect.getsource(pipeline)
    assert '"report_disclosures": _report_disclosures,' in src and "_build_disclosures(" in src
    assert '"research_degraded_reasons": state["data"].get("research_degraded_reasons", [])' in src


def test_the_pdf_prints_the_horizon_disclosures_methodology_street_and_risks(tmp_path):
    fitz = pytest.importorskip("fitz")
    from src.utils import pdf_report
    orig = pdf_report._fetch_company_name
    pdf_report._fetch_company_name = lambda t: t
    try:
        path = str(tmp_path / "r.pdf")
        pdf_report.generate_pdf_report(_run(), path, open_after=False)
    finally:
        pdf_report._fetch_company_name = orig
    text = " ".join("\n".join(p.get_text() for p in fitz.open(path)).split())
    assert "Horizon: 12 months" in text and "Medium" not in text
    assert "Target methodology. 12-month target = price + 50% × (intrinsic value − price)" in text
    assert "Versus the Street. Our 12-month target of US$846 is below the Street range" in text
    assert "RISKS TO RATING" in text and "Downside risk: bear case." in text
    assert "SECTION 5 — DATA LIMITATIONS AND DISCLOSURES" in text
    for s in ("Rating definition", "AI-generated text", "claude-test", "Data as of", "2026-11-21",
              "S&P 500: expected 12-month return 8.4%", "The research quotes revenue 72% below"):
        assert s in text, s


# ── 7. research degraded on a books divergence ───────────────────────────────

def test_a_books_divergence_beyond_the_threshold_degrades_for_disclosure():
    from src.agents.industry.deep_research import divergence_degradation as dd
    assert dd({"revenue_magnitude": {"books_raw": 133.2e9, "research_claim": 37.4e9, "closest_ratio": 0.28,
                                     "claims_checked": 2, "claims_matching_books": 0}})[0]["check"] == \
        "revenue_magnitude"
    # one stray figure beside a correct revenue citation is a parser catch, not wrong research
    assert dd({"revenue_magnitude": {"books_raw": 133.2e9, "research_claim": 85e9, "closest_ratio": 0.64,
                                     "claims_checked": 5, "claims_matching_books": 2}}) == []
    assert dd({"revenue_magnitude": {"books_raw": 1e9, "research_claim": 0.9e9}}) == []          # legacy flag
    assert dd({"revenue_growth": {"divergence_pp": 200.2}})[0]["check"] == "revenue_growth"
    assert dd({"revenue_growth": {"divergence_pp": 15.1}}) == []


def test_a_prior_year_revenue_citation_and_a_three_digit_growth_rate_are_not_divergences():
    from src.agents.industry.deep_research import _check_research_financial_consistency as chk
    raw = {"FY2025": {"revenue": 37.378e9, "net_income": 8e9}, "FY2026": {"revenue": 133.188e9, "net_income": 85e9}}
    sec = {"2a": "FY2026 revenue was $133.2B. FY2026 YoY revenue growth was 256.1% vs FY2025 $37.4B."}
    assert chk(sec, raw, "MU") == {}


def test_a_divergence_degrade_is_disclosed_but_does_not_move_the_decision():
    assert pm._search_degraded({"research_degraded": True}) is True                      # legacy flag
    assert pm._search_degraded({"research_degraded": True, "research_degraded_reasons": [
        {"source": "books_divergence", "text": "x"}]}) is False
    assert pm._search_degraded({"research_degraded": True, "research_degraded_reasons": [
        {"source": "search_floor", "text": "x"}, {"source": "books_divergence", "text": "y"}]}) is True
    from src.agents.industry import deep_research as dr
    src = inspect.getsource(dr.run_deep_research_agent)
    assert 'state["data"]["research_degraded_reasons"] = _deg_reasons' in src

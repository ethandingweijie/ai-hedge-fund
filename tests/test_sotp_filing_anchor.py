"""Guards for the filing -> SOTP bridge (FX, forward re-basing, overlay).

No network and no LLM: every test drives the pure functions with fixture-shaped
dicts. Values come from the live filings so a regression reads as a concrete
wrong number.

The two behaviours most worth protecting:

* `test_consensus_is_converted_before_comparison` -- FMP consensus arrives in
  the filer's REPORTING currency while group revenue is USD. Comparing them
  unconverted implies +629% growth for JD and +665% for BABA. Same asymmetry
  that `_fmp_segment_anchor` has by dropping `reported_currency`.
* `test_overlay_preserves_names_and_multiples` -- filing names largely fail
  `classify_archetype`, so swapping them in would strip the multiple basis from
  the largest segments and turn a single-variable experiment into a two-variable
  one. Only the numbers may change.
"""
from __future__ import annotations

import pytest

from src.agents.analysis import sotp_filing_anchor as fa


# ── name matching ───────────────────────────────────────────────────────────

def test_jaccard_ignores_structural_words():
    assert fa._jaccard("Cloud intelligence group", "Cloud Intelligence Group") == 1.0
    assert fa._jaccard("JD Retail", "JD Retail Group") == 1.0


def test_jaccard_separates_different_businesses():
    """A wide restated perimeter is not the same unit as the narrow one."""
    score = fa._jaccard("Alibaba China E-commerce Group",
                        "Taobao and Tmall Group (China Commerce)")
    assert score < fa._JACCARD_FLOOR


@pytest.mark.parametrize("filing_name,note_name,rule", [
    ("All others", "All Others", "exact"),
    ("Cloud intelligence group", "Cloud Intelligence Group", "exact"),
    ("Alibaba International Digital Commerce Group",
     "Alibaba International Digital Commerce", "containment"),
])
def test_match_ladder(filing_name, note_name, rule):
    idx, got = fa._match(filing_name, [{"name": note_name}], set())
    assert idx == 0 and got.startswith(rule)


def test_match_refuses_to_pair_on_a_guess():
    idx, rule = fa._match("Alibaba China E-commerce Group",
                          [{"name": "Cainiao Smart Logistics"}], set())
    assert idx is None and rule is None


def test_match_never_reuses_a_candidate():
    cands = [{"name": "AWS"}, {"name": "AWS"}]
    i1, _ = fa._match("AWS", cands, set())
    i2, _ = fa._match("AWS", cands, {i1})
    assert i1 != i2


# ── FX resolution ───────────────────────────────────────────────────────────

def test_fx_identity_for_usd_filers():
    assert fa._resolve_fx({"reporting_currency": "USD"}, None) == (1.0, "identity")


def test_fx_prefers_the_filings_own_convenience_rate():
    """The issuer's stated rate reconciles to the filing's own USD totals."""
    rate, src = fa._resolve_fx(
        {"reporting_currency": "CNY", "usd_convenience_rate": 0.144971}, None)
    assert rate == pytest.approx(0.144971)
    assert src == "filing_convenience_translation"


def test_fx_falls_back_to_a_quote_when_the_filing_has_no_translation(monkeypatch):
    monkeypatch.setattr("src.tools.api.get_fx_rate", lambda *a, **k: 0.1401)
    rate, src = fa._resolve_fx({"reporting_currency": "CNY"}, None)
    assert rate == pytest.approx(0.1401) and src == "fx_rate_api"


# ── forward bridge ──────────────────────────────────────────────────────────

def _filing(**over):
    base = {
        "segments": [
            {"name": "A", "revenue": 600.0, "profit": 120.0, "margin": 0.20},
            {"name": "B", "revenue": 400.0, "profit": -40.0, "margin": -0.10},
        ],
        "consolidated_revenue": 900.0,       # < sum: intersegment eliminations
        "reporting_currency": "USD", "warnings": [],
        "form": "10-K", "accession": "x", "report_file": "R1.htm",
        "period_end": "2025-12-31", "profit_metric": "us-gaap_OperatingIncomeLoss",
        "profit_is_gaap_operating_income": True, "sum_vs_consolidated": 1.111,
    }
    base.update(over)
    return base


def _anchor(monkeypatch, filing, fwd=None, group=None):
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: filing)
    return fa.filing_segment_anchor("T", "2026-08-16", None,
                                    fwd_est=fwd, group_revenue_usd=group)


def test_mix_normalisation_pins_the_sum_to_consensus(monkeypatch):
    """Uniform growth would carry the elimination inflation into NAV."""
    a = _anchor(monkeypatch, _filing(), fwd={"revenue_avg": 1000.0}, group=900.0)
    assert sum(s["revenue_fwd"] for s in a["segments"]) == pytest.approx(1000.0)
    assert a["bridge"]["mode"] == "mix_to_consensus"


def test_reported_margin_survives_the_rebasing(monkeypatch):
    """The margin is the filing's real contribution; re-basing must not move it."""
    a = _anchor(monkeypatch, _filing(), fwd={"revenue_avg": 1000.0}, group=900.0)
    for seg, expected in zip(a["segments"], (0.20, -0.10)):
        assert seg["ebit_fwd"] / seg["revenue_fwd"] == pytest.approx(expected)


def test_consensus_is_converted_before_comparison(monkeypatch):
    """Consensus is in the reporting currency; group revenue is USD."""
    filing = _filing(reporting_currency="CNY", usd_convenience_rate=0.145)
    a = _anchor(monkeypatch, filing,
                fwd={"revenue_avg": 1000.0},      # CNY
                group=900.0 * 0.145)              # USD
    # 1000 CNY -> 145 USD against a 130.5 USD group = +11%, not +666%.
    assert a["bridge"]["implied_group_growth"] == pytest.approx(0.111, abs=0.02)
    assert a["bridge"]["clamped"] is False


def test_absurd_growth_is_clamped_and_reported(monkeypatch):
    a = _anchor(monkeypatch, _filing(), fwd={"revenue_avg": 9000.0}, group=900.0)
    assert a["bridge"]["clamped"] is True
    assert any("clamped" in w for w in a["warnings"])


def test_no_consensus_falls_back_to_trailing(monkeypatch):
    a = _anchor(monkeypatch, _filing(), fwd=None, group=900.0)
    assert a["bridge"]["mode"] == "trailing_as_forward"
    assert sum(s["revenue_fwd"] for s in a["segments"]) == pytest.approx(1000.0)


def test_scale_mismatch_drops_the_anchor(monkeypatch):
    """Catches a units misread, an inverted rate or a fiscal mix-up at once."""
    assert _anchor(monkeypatch, _filing(), fwd=None, group=50.0) is None


def test_single_segment_filing_is_rejected(monkeypatch):
    one = _filing(segments=[{"name": "A", "revenue": 900.0,
                             "profit": 90.0, "margin": 0.1}])
    assert _anchor(monkeypatch, one, fwd=None, group=900.0) is None


def test_missing_filing_returns_none(monkeypatch):
    assert _anchor(monkeypatch, None) is None


# ── overlay ─────────────────────────────────────────────────────────────────

def _note():
    return [
        {"name": "Cloud Intelligence Group", "revenue_fwd": 20e9,
         "ev_rev_multiple": 5.0, "rationale": "note"},
        {"name": "All Others", "revenue_fwd": 22e9,
         "ev_rev_multiple": 1.0, "rationale": "note"},
    ]


def _live_anchor():
    return {"form": "20-F", "accession": "acc", "report_file": "R123.htm",
            "period_end": "2026-03-31", "profit_metric": "AdjustedEBITA",
            "profit_is_gaap_operating_income": False,
            "bridge": {"mode": "mix_to_consensus"},
            "segments": [
                {"name": "Cloud intelligence group", "revenue_ttm": 22.9e9,
                 "profit_ttm": 2.1e9, "margin": 0.090,
                 "revenue_fwd": 23.4e9, "ebit_fwd": 2.1e9},
                {"name": "All others", "revenue_ttm": 36.9e9,
                 "profit_ttm": -5.2e9, "margin": -0.140,
                 "revenue_fwd": 37.6e9, "ebit_fwd": -5.3e9}]}


def test_overlay_preserves_names_and_multiples():
    out, _ = fa.apply_filing_overlay(_note(), _live_anchor())
    assert [e["name"] for e in out] == ["Cloud Intelligence Group", "All Others"]
    assert [e["ev_rev_multiple"] for e in out] == [5.0, 1.0]
    assert all(e["rationale"] == "note" for e in out)


def test_overlay_writes_reported_numbers_and_provenance():
    out, detail = fa.apply_filing_overlay(_note(), _live_anchor())
    assert out[0]["revenue_fwd"] == pytest.approx(23.4e9)
    assert out[0]["ebit"] == pytest.approx(2.1e9)
    assert out[0]["source"] == "sec_segment_footnote"
    assert "R123.htm" in out[0]["evidence"]
    assert detail["status"] == "applied" and detail["coverage"] == pytest.approx(1.0)


def test_overlay_clears_the_stale_margin():
    """A margin from the previous revenue would contradict the new EBIT."""
    note = _note()
    note[0]["ebit_margin"] = 0.45
    out, _ = fa.apply_filing_overlay(note, _live_anchor())
    assert out[0]["ebit_margin"] is None


def test_setting_ebit_preempts_the_learned_margin_model():
    """This is the mechanism -- apply_margin_basis skips entries with an ebit."""
    from src.agents.analysis.sotp_multiple_basis import apply_margin_basis
    out, _ = fa.apply_filing_overlay(_note(), _live_anchor())
    # A truthy artifact is enough: overlaid entries carry an `ebit` and hit the
    # `researched_kept` continue before the model is ever consulted. Passing
    # None would short-circuit to "no_artifact" and prove nothing.
    _segs, detail = apply_margin_basis(out, [], china=True,
                                       _artifact={"stub": True})
    assert detail["summary"] == "filled:0"
    for name in ("Cloud Intelligence Group", "All Others"):
        assert detail["segments"][name]["status"] == "researched_kept"


def _mismatched():
    """A restated perimeter alongside the old narrow segment."""
    note = _note() + [{"name": "Taobao and Tmall Group (China Commerce)",
                       "revenue_fwd": 67e9, "pe_multiple": 12.0}]
    anchor = _live_anchor()
    anchor["segments"].append(
        {"name": "Alibaba China E-commerce Group", "revenue_ttm": 80.3e9,
         "profit_ttm": 15.6e9, "margin": 0.194,
         "revenue_fwd": 81.9e9, "ebit_fwd": 15.9e9})
    return note, anchor


def test_mismatch_is_reported_not_forced_when_canonical_is_disallowed():
    """A wide restated unit must never be pinned onto the old narrow one."""
    note, anchor = _mismatched()
    out, detail = fa.apply_filing_overlay(note, anchor, allow_canonical=False)
    assert detail["status"] == "partial_taxonomy_mismatch"
    assert "Alibaba China E-commerce Group" in detail["unmatched_filing"]
    taobao = next(e for e in out if e["name"].startswith("Taobao"))
    assert taobao["revenue_fwd"] == pytest.approx(67e9)   # untouched
    assert "source" not in taobao


def test_low_coverage_rebuilds_from_the_filing_map():
    """Otherwise the largest reported segment is silently dropped.

    Observed live on BABA arm C: the LLM produced no China-commerce unit, so
    the filing's $81.9B / 19.4% segment matched nothing and vanished from the
    SOTP, collapsing the target price.
    """
    note, anchor = _mismatched()
    out, detail = fa.apply_filing_overlay(note, anchor)
    assert detail["status"] == "filing_canonical"
    assert detail["names_from_filing"] is True
    assert {e["name"] for e in out} == {s["name"] for s in anchor["segments"]}
    assert all(e["source"] == "sec_segment_footnote" for e in out)
    assert not detail["unmatched_filing"]


def test_canonical_rebuild_is_flagged_because_it_costs_the_multiple_basis():
    """Filing names largely miss classify_archetype -- callers must know."""
    from src.agents.analysis.sotp_multiple_basis import classify_archetype
    note, anchor = _mismatched()
    _out, detail = fa.apply_filing_overlay(note, anchor)
    assert detail["names_from_filing"] is True
    assert classify_archetype("Alibaba China E-commerce Group") is None
    assert classify_archetype("All others") is None


def test_overlay_is_a_no_op_without_a_filing():
    note = _note()
    out, detail = fa.apply_filing_overlay(note, None)
    assert detail["status"] == "no_filing"
    assert out == note


def test_overlay_does_not_mutate_the_input():
    note = _note()
    fa.apply_filing_overlay(note, _live_anchor())
    assert "source" not in note[0] and note[0]["revenue_fwd"] == 20e9


def test_non_gaap_profit_metric_travels_into_the_detail():
    """AdjustedEBITA is pre-SBC: the engine must not treat it as EBIT silently."""
    _out, detail = fa.apply_filing_overlay(_note(), _live_anchor())
    assert detail["profit_is_gaap_operating_income"] is False
    assert detail["profit_metric"] == "AdjustedEBITA"


# ── period fix: NTM+1 ───────────────────────────────────────────────────────

class _Est:
    def __init__(self, pe, rev):
        self.period_end, self.revenue_avg, self.ebit_avg = pe, rev, None


def _stub_estimates(monkeypatch, rows):
    monkeypatch.setattr("src.tools.api.get_analyst_estimates",
                        lambda *a, **k: rows)


def test_forward_estimate_picks_ntm_plus_one(monkeypatch):
    """The notes value NTM+1; anchoring on NTM compares different years."""
    _stub_estimates(monkeypatch, [_Est("2026-12-31", 828.5e9),
                                  _Est("2027-12-31", 949.5e9),
                                  _Est("2025-12-31", 716.9e9)])
    got = fa.forward_estimate("AMZN", "2026-08-16", None, years_ahead=2)
    assert got["period_end"] == "2027-12-31"
    assert got["revenue_avg"] == pytest.approx(949.5e9)
    assert got["years_ahead"] == 2


def test_forward_estimate_falls_back_to_the_furthest_published_year(monkeypatch):
    _stub_estimates(monkeypatch, [_Est("2026-12-31", 828.5e9)])
    got = fa.forward_estimate("X", "2026-08-16", None, years_ahead=3)
    assert got["period_end"] == "2026-12-31" and got["years_ahead"] == 1


def test_forward_estimate_ignores_periods_at_or_before_the_run_date(monkeypatch):
    _stub_estimates(monkeypatch, [_Est("2025-12-31", 1.0)])
    assert fa.forward_estimate("X", "2026-08-16", None) is None


@pytest.mark.parametrize("filing,target,years", [
    ("2025-12-31", "2027-12-31", 2),
    ("2026-03-31", "2027-03-31", 1),
    ("2025-12-31", "2025-12-31", 0),
    (None, "2027-12-31", 0),
    ("2025-12-31", None, 0),
])
def test_years_between(filing, target, years):
    assert fa._years_between(filing, target) == years


# ── per-segment growth allocation ───────────────────────────────────────────

def test_segment_growth_reads_the_filing_history():
    seg = {"revenue_by_period": {"2025-12-31": 128.7, "2024-12-31": 107.6}}
    assert fa._segment_growth(seg) == pytest.approx(0.196, abs=0.002)


def test_segment_growth_is_clamped_at_the_extremes():
    """JD's New Businesses printed +157%; projecting that would dominate."""
    hot = {"revenue_by_period": {"2025-12-31": 257.0, "2024-12-31": 100.0}}
    cold = {"revenue_by_period": {"2025-12-31": 10.0, "2024-12-31": 100.0}}
    assert fa._segment_growth(hot) == pytest.approx(0.60)
    assert fa._segment_growth(cold) == pytest.approx(-0.30)


@pytest.mark.parametrize("hist", [
    {"2025-12-31": 100.0},                       # one period only
    {"2025-12-31": 100.0, "2024-12-31": 0.0},    # zero base
    {},
])
def test_segment_growth_returns_none_without_a_usable_base(hist):
    assert fa._segment_growth({"revenue_by_period": hist}) is None


def _two_speed_filing():
    """A fast, high-multiple segment beside a slow one -- the AMZN shape."""
    return {
        "segments": [
            {"name": "Slow", "revenue": 600.0, "profit": 60.0, "margin": 0.10,
             "revenue_by_period": {"2025-12-31": 600.0, "2024-12-31": 560.0}},
            {"name": "Fast", "revenue": 400.0, "profit": 140.0, "margin": 0.35,
             "revenue_by_period": {"2025-12-31": 400.0, "2024-12-31": 320.0}},
        ],
        "consolidated_revenue": 1000.0, "reporting_currency": "USD",
        "warnings": [], "form": "10-K", "accession": "a", "report_file": "R1.htm",
        "period_end": "2025-12-31", "profit_metric": "us-gaap_OperatingIncomeLoss",
        "profit_is_gaap_operating_income": True, "sum_vs_consolidated": 1.0,
    }


def test_growth_allocation_shifts_mix_to_the_faster_segment(monkeypatch):
    """Constant mix starves the segment that usually carries the multiple."""
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: _two_speed_filing())
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    assert a["bridge"]["allocation"] == "segment_growth"
    assert a["bridge"]["years_projected"] == 2
    by = {s["name"]: s for s in a["segments"]}
    fast_mix = by["Fast"]["revenue_fwd"] / a["bridge"]["target_revenue_usd"]
    assert fast_mix > 0.40, "fast segment should gain share vs its 40% base"
    assert by["Fast"]["growth_used"] == pytest.approx(0.25)
    assert by["Slow"]["growth_used"] == pytest.approx(0.0714, abs=1e-3)


def test_allocation_still_lands_exactly_on_consensus(monkeypatch):
    """Rescaling keeps this an allocation of the market number, not a forecast."""
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: _two_speed_filing())
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    assert sum(s["revenue_fwd"] for s in a["segments"]) == pytest.approx(1200.0)


def test_falls_back_to_constant_mix_without_history(monkeypatch):
    f = _two_speed_filing()
    for s in f["segments"]:
        s["revenue_by_period"] = {"2025-12-31": s["revenue"]}
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: f)
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    assert a["bridge"]["allocation"] == "constant_mix"
    assert any("constant-mix" in w for w in a["warnings"])
    by = {s["name"]: s for s in a["segments"]}
    assert by["Fast"]["revenue_fwd"] / 1200.0 == pytest.approx(0.40)


def test_margin_stays_at_the_reported_level_without_history_or_consensus(monkeypatch):
    """No margin history and no consensus EBIT -> the reported margin stands.

    The basis label has to say so: a consumer must be able to tell a
    reconciled margin from a bare realised one.
    """
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: _two_speed_filing())
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    assert a["bridge"]["margin_basis"] == "reported_trailing_with_trend"
    assert a["bridge"]["margin_shift_pp"] is None
    assert any("not reconciled" in w for w in a["warnings"])
    for s in a["segments"]:
        assert s["ebit_fwd"] / s["revenue_fwd"] == pytest.approx(s["margin"])


# ── margin path ─────────────────────────────────────────────────────────────

def test_margin_trend_from_the_filing_history():
    """AWS margin fell 37.0% -> 35.4%, so its trend is negative."""
    seg = {"revenue_by_period": {"2025-12-31": 128.7, "2024-12-31": 107.6},
           "profit_by_period":  {"2025-12-31": 45.6,  "2024-12-31": 39.8}}
    assert fa._margin_trend(seg) == pytest.approx(-0.0154, abs=0.002)


def test_margin_trend_is_clamped_against_perimeter_changes():
    """BABA's China E-commerce reads 38.0% -> 19.4% because it was redefined."""
    seg = {"revenue_by_period": {"2026-03-31": 554.2, "2025-03-31": 508.4},
           "profit_by_period":  {"2026-03-31": 107.5, "2025-03-31": 193.2}}
    assert fa._margin_trend(seg) == pytest.approx(-fa._MARGIN_TREND_CAP)


@pytest.mark.parametrize("hist", [
    ({"2025-12-31": 100.0}, {"2025-12-31": 10.0}),
    ({}, {}),
])
def test_margin_trend_needs_two_periods(hist):
    rbp, pbp = hist
    assert fa._margin_trend({"revenue_by_period": rbp,
                             "profit_by_period": pbp}) is None


def test_projected_margin_follows_the_trend_then_stops():
    assert fa._project_margin(0.10, 0.02, 2) == pytest.approx(0.14)
    # drift cap keeps a steep slope from running away over a long horizon
    assert fa._project_margin(0.10, 0.03, 5) == pytest.approx(0.10 + fa._MARGIN_DRIFT_CAP)


def test_projected_margin_is_identity_without_a_trend_or_horizon():
    assert fa._project_margin(0.10, None, 2) == 0.10
    assert fa._project_margin(0.10, 0.02, 0) == 0.10
    assert fa._project_margin(None, 0.02, 2) is None


def test_ebit_reconciles_to_the_consensus_level():
    """Filing supplies the split; the market supplies the level."""
    segs = [{"revenue_fwd": 600.0, "margin": 0.10, "margin_fwd": 0.10,
             "ebit_fwd": 60.0},
            {"revenue_fwd": 400.0, "margin": 0.30, "margin_fwd": 0.30,
             "ebit_fwd": 120.0}]
    shift = fa._reconcile_ebit(segs, target_ebit=200.0)
    assert shift == pytest.approx(0.02)                       # +2pp uniform
    assert sum(s["ebit_fwd"] for s in segs) == pytest.approx(200.0)
    assert segs[0]["margin_fwd"] == pytest.approx(0.12)
    assert segs[1]["margin_fwd"] == pytest.approx(0.32)


def test_reconciliation_lifts_a_loss_maker_toward_break_even():
    """A uniform pp shift is why EBIT is not scaled multiplicatively.

    Scaling would drive a negative segment further negative while lifting the
    profitable ones -- the wrong direction for exactly the segments the engine
    ends up valuing on revenue.
    """
    segs = [{"revenue_fwd": 900.0, "margin": 0.10, "margin_fwd": 0.10,
             "ebit_fwd": 90.0},
            {"revenue_fwd": 100.0, "margin": -0.50, "margin_fwd": -0.50,
             "ebit_fwd": -50.0}]
    fa._reconcile_ebit(segs, target_ebit=80.0)
    assert segs[1]["ebit_fwd"] > -50.0
    assert sum(s["ebit_fwd"] for s in segs) == pytest.approx(80.0)


def test_no_consensus_ebit_leaves_the_trend_unreconciled():
    segs = [{"revenue_fwd": 600.0, "margin": 0.10, "margin_fwd": 0.10,
             "ebit_fwd": 60.0}]
    assert fa._reconcile_ebit(segs, target_ebit=None) is None
    assert segs[0]["ebit_fwd"] == pytest.approx(60.0)


def _margin_filing():
    f = _two_speed_filing()
    for s, prev_m in zip(f["segments"], (0.08, 0.30)):
        p = sorted(s["revenue_by_period"], reverse=True)
        s["profit_by_period"] = {p[0]: s["revenue"] * s["margin"],
                                 p[1]: s["revenue_by_period"][p[1]] * prev_m}
    return f


def test_bridge_reconciles_segments_to_consensus_ebit(monkeypatch):
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: _margin_filing())
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "ebit_avg": 240.0,
                 "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    b = a["bridge"]
    assert b["margin_basis"] == "trend_then_reconciled_to_consensus"
    assert sum(s["ebit_fwd"] for s in a["segments"]) == pytest.approx(240.0)
    assert b["segment_ebit_target_usd"] == pytest.approx(240.0)


def test_corporate_overhead_is_removed_from_the_segment_target(monkeypatch):
    """Consensus EBIT is after unallocated cost; segments must target the rest.

    This is the first real consumer of the corporate line the parser captures.
    """
    f = _margin_filing()
    f["corporate_unallocated"] = {"name": "Corporate", "profit": -40.0}
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: f)
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "ebit_avg": 240.0,
                 "period_end": "2027-12-31"},
        group_revenue_usd=1000.0)
    assert a["bridge"]["segment_ebit_target_usd"] == pytest.approx(280.0)
    assert sum(s["ebit_fwd"] for s in a["segments"]) == pytest.approx(280.0)


def test_consensus_ebit_is_currency_converted(monkeypatch):
    """Like revenue, consensus EBIT arrives in the reporting currency."""
    f = _margin_filing()
    f.update(reporting_currency="CNY", usd_convenience_rate=0.145)
    monkeypatch.setattr("src.tools.sec_segments.get_segment_footnote",
                        lambda *a, **k: f)
    a = fa.filing_segment_anchor(
        "T", "2026-08-16", None,
        fwd_est={"revenue_avg": 1200.0, "ebit_avg": 240.0,
                 "period_end": "2027-12-31"},
        group_revenue_usd=1000.0 * 0.145)
    assert a["bridge"]["consensus_ebit_usd"] == pytest.approx(240.0 * 0.145)

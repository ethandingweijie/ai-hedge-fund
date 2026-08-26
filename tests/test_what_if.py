"""
tests/test_what_if.py
======================
P5 gates — what-if crisis simulator. Deterministic math + classification are
tested fully offline (injected llm_caller + empty price_fetcher); the LLM is
NEVER called here.

Backward-gate semantics baked in:
  • determinism — identical inputs → byte-identical JSON
  • classification never turns plain equities into products
  • decay formula sanity anchors: k=+1 tracks exactly, k=−1 decays extra
"""
import json
import math

import pytest

from src.portfolio import what_if as wi


# ── Decay formula ────────────────────────────────────────────────────────────

class TestLeveragedScenarioReturn:
    def test_long_1x_tracks_underlying_no_drag(self):
        out = wi.leveraged_scenario_return_pct(-20.0, 30.0, 90, +1.0)
        assert out["est_return_pct"] == pytest.approx(-20.0, abs=0.01)
        assert out["decay_drag_pp"] == pytest.approx(0.0, abs=0.01)

    def test_inverse_1x_decays_extra(self):
        # Hand-computed: L=ln(0.8)=-0.22314 → kL=+0.22314;
        # sigma_d^2=(0.30/sqrt(252))^2=3.571e-4; N*sigma^2=0.03214;
        # log_ret=0.19100 → est +21.04% vs +25.0% without decay.
        out = wi.leveraged_scenario_return_pct(-20.0, 30.0, 90, -1.0)
        assert out["est_return_pct"] == pytest.approx(21.04, abs=0.15)
        assert out["no_decay_return_pct"] == pytest.approx(25.0, abs=0.1)
        assert out["decay_drag_pp"] == pytest.approx(-3.96, abs=0.15)

    def test_long_2x_classic_decay(self):
        # L=ln(1.1)=0.09531; drag = 1*90*(0.2/sqrt(252))^2 = 0.014286
        out = wi.leveraged_scenario_return_pct(10.0, 20.0, 90, +2.0)
        assert out["est_return_pct"] == pytest.approx(19.28, abs=0.15)
        assert out["decay_drag_pp"] == pytest.approx(-1.72, abs=0.1)

    def test_zero_vol_no_drag(self):
        out = wi.leveraged_scenario_return_pct(-15.0, 0.0, 252, -1.0)
        assert out["decay_drag_pp"] == pytest.approx(0.0, abs=0.01)
        assert out["est_return_pct"] == pytest.approx(17.65, abs=0.1)  # 1/0.85-1

    def test_extreme_loss_clamped(self):
        out = wi.leveraged_scenario_return_pct(-90.0, 55.0, 180, +2.0)
        assert out["est_return_pct"] >= -99.99
        assert out["est_return_pct"] < 0

    def test_inverse_benefits_from_underlying_fall(self):
        out = wi.leveraged_scenario_return_pct(-40.0, 20.0, 30, -1.0)
        assert out["est_return_pct"] > 40.0  # more than the mirror move
        assert out["decay_drag_pp"] < 0      # but still less than kL mirror


# ── Product classification ───────────────────────────────────────────────────

class TestClassifyProduct:
    def test_psq_confirmed(self):
        c = wi.classify_product("PSQ")
        assert c["classified"] and c["confidence"] == "confirmed"
        assert c["underlying"] == "QQQ" and c["leverage"] == -1.0

    def test_mud_assumed_flagged(self):
        c = wi.classify_product("MUD")
        assert c["classified"] and c["confidence"] == "assumed"
        assert c["underlying"] == "MSFT" and c["leverage"] == -1.0

    def test_cord_unknown_without_notes(self):
        c = wi.classify_product("CORD")
        assert not c["classified"] and c["needs_classification"]

    def test_notes_parse_inverse_with_leverage(self):
        c = wi.classify_product("XYZF", "inverse MSFT 2x position")
        assert c["classified"] and c["confidence"] == "notes"
        assert c["underlying"] == "MSFT" and c["leverage"] == -2.0

    def test_notes_parse_short_without_leverage(self):
        c = wi.classify_product("WXYZ", "short QQQ hedge")
        assert c["classified"] and c["leverage"] == -1.0
        assert c["underlying"] == "QQQ"

    def test_short_term_notes_not_a_short_declaration(self):
        c = wi.classify_product("ANYTK", "short-term trade idea for Q3")
        assert not c["classified"] and c["needs_classification"]

    def test_leverage_band_clamped(self):
        c = wi.classify_product("XYZF", "bear NVDA 9x")
        assert c["classified"] and c["leverage"] == -3.0  # clamped to band


# ── Sector mapping ───────────────────────────────────────────────────────────

class TestSectorToGics:
    @pytest.mark.parametrize("pipeline_sector,expected", [
        ("Hyperscaler / Tech Conglomerate", "Technology"),
        ("Cybersecurity SaaS", "Technology"),
        ("Money Center Bank", "Financials"),
        ("BTC Treasury / Proxy", "Financials"),
        ("Consumer Growth", "Consumer Discretionary"),
        ("Managed Care", "Health Care"),
        ("REIT", "Real Estate"),
        ("Digital Asset Mining", "Financials"),
    ])
    def test_mappings(self, pipeline_sector, expected):
        assert wi.sector_to_gics(pipeline_sector) == expected

    def test_unmatchable_and_none(self):
        assert wi.sector_to_gics("Quantum Basket Weaving") is None
        assert wi.sector_to_gics(None) is None


# ── Search heuristic ─────────────────────────────────────────────────────────

def _cls(classified=True, needs=False, ticker="PSQ"):
    return {"classified": classified, "needs_classification": needs,
            "ticker": ticker}


class TestDecideSearch:
    def test_no_search_needed_all_known_with_reference(self):
        from src.portfolio.event_library import get_event
        d = wi.decide_search("AI Capex Meltdown", "long concerns text",
                             [_cls()], get_event("dotcom_2000"), "auto")
        assert d["recommended"] is False

    def test_unknown_product_forces_recommendation(self):
        from src.portfolio.event_library import get_event
        d = wi.decide_search("AI Capex Meltdown", "concerns",
                             [_cls(needs=True, ticker="CORD")],
                             get_event("dotcom_2000"), "auto")
        assert d["recommended"] is True
        assert any("CORD" in r for r in d["reasons"])

    def test_no_reference_forces_recommendation(self):
        d = wi.decide_search("US Bond Destabilisation", "concerns", [_cls()],
                             None, "auto")
        assert d["recommended"] is True

    def test_overrides_win(self):
        never = wi.decide_search("X", "y", [_cls(needs=True, ticker="C")],
                                 None, "never")
        assert never["recommended"] is False
        always = wi.decide_search("X", "y", [_cls()], None, "always")
        assert always["recommended"] is True


# ── Lenient LLM output parsing (live-observed field-name drift) ─────────────

class TestLenientOutputParsing:
    def test_live_drifted_payload_parses(self):
        # Shapes observed from a live deepseek-v4-flash run: return_pct/note
        # instead of est_return_pct/rationale, item/quarter instead of
        # metric/watch_for/timing, tool instead of instrument, and a null
        # est_impact_pct for the unclassifiable product.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s" * 90,
            "sector_impacts": [
                {"sector": "Information Technology", "symbol": "XLK",
                 "return_pct": -71.3, "anchor_pct": -71.3,
                 "note": "Reference anchor."}],
            "assumptions_to_watch": [
                {"item": "Hyperscaler capex guidance turns cautious",
                 "quarter": "Q3 2026"}],
            "most_affected_sectors": ["Information Technology"],
            "hedged_sectors": ["Utilities"],
            "holding_impacts": [
                {"ticker": "CORD", "kind": "unknown_product",
                 "est_impact_pct": None, "note": "Unknown product."},
                {"ticker": "PSQ", "est_impact_pct": 270.9,
                 "note": "Direct QQQ hedge."}],
            "recommendations": [
                {"action": "GOLD", "tool": "GLD / physical gold",
                 "rationale": "risk-off", "confidence": 0.55}],
            "search_evidence_used": False,
        })
        assert out.sector_impacts[0].est_return_pct == -71.3
        assert out.sector_impacts[0].rationale == "Reference anchor."
        assert out.assumptions_to_watch[0].metric.startswith("Hyperscaler")
        assert out.assumptions_to_watch[0].timing == "Q3 2026"
        assert out.holding_impacts[0].est_impact_pct is None
        assert out.recommendations[0].instrument == "GLD / physical gold"

    def test_canonical_names_still_parse(self):
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "sector_impacts": [{"sector": "Energy", "est_return_pct": -10.0,
                                "rationale": "r"}],
            "assumptions_to_watch": [{"metric": "m", "watch_for": "w",
                                      "timing": "t"}],
            "most_affected_sectors": [], "hedged_sectors": [],
            "holding_impacts": [{"ticker": "A", "est_impact_pct": -5.0,
                                 "rationale": "r"}],
            "recommendations": [{"action": "BUY", "instrument": "XLP",
                                 "rationale": "r", "confidence": 0.6}],
        })
        assert out.sector_impacts[0].est_return_pct == -10.0
        assert out.holding_impacts[0].est_impact_pct == -5.0

    def test_comment_and_indicator_variants_parse(self):
        # Second live run drifted further: 'comment' everywhere instead of
        # rationale/note, assumptions as {quarter, indicator} sentences.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "sector_impacts": [
                {"sector": "Energy", "symbol": "XLE", "return_pct": -10.6,
                 "reference_anchor_pct": -10.6, "comment": "Dotcom anchor."}],
            "assumptions_to_watch": [
                {"quarter": "Q3 2025",
                 "indicator": "Hyperscaler capex guidance — cuts confirm."}],
            "most_affected_sectors": ["Information Technology"],
            "hedged_sectors": ["Utilities"],
            "holding_impacts": [
                {"ticker": "PSQ", "kind": "product", "est_impact_pct": 270.9,
                 "anchor_pct": -76.1, "comment": "Confirmed short QQQ."}],
            "recommendations": [
                {"action": "HOLD", "instrument": "PSQ",
                 "rationale": "hedge", "confidence": 0.7}],
        })
        assert out.sector_impacts[0].est_return_pct == -10.6
        assert out.sector_impacts[0].rationale == "Dotcom anchor."
        assert out.assumptions_to_watch[0].metric.startswith("Hyperscaler")
        assert out.assumptions_to_watch[0].timing == "Q3 2025"
        assert out.holding_impacts[0].rationale == "Confirmed short QQQ."

    def test_structural_drift_dict_sectors_and_swapped_recs(self):
        # Third live run drifted structurally: sector_impacts as an OBJECT
        # keyed by sector name (impact_pct values), most_affected_sectors as
        # objects, and recommendations with the action enum in 'tool'.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "sector_impacts": {
                "Information Technology": {"impact_pct": -75.0,
                                           "anchor_pct": -71.3,
                                           "rationale": "worse than dotcom"},
                "Utilities": {"impact_pct": 12.3, "rationale": "defensive"},
            },
            "assumptions_to_watch": [
                {"assumption": "Big Tech capex gets cut.",
                 "quarter": "2025 Q3"}],
            "most_affected_sectors": [
                {"sector": "Information Technology", "note": "x"}],
            "hedged_sectors": ["Consumer Staples"],
            "holding_impacts": [
                {"ticker": "PSQ", "est_impact_pct": 270.9,
                 "rationale": "cleanest hedge"}],
            "recommendations": [
                {"tool": "HOLD", "instrument": "PSQ",
                 "action": "Maintain short QQQ position.",
                 "rationale": "QQQ tracks Nasdaq-100.", "confidence": 0.85}],
            "search_evidence_used": False,
        })
        by_sector = {s.sector: s for s in out.sector_impacts}
        assert by_sector["Information Technology"].est_return_pct == -75.0
        assert by_sector["Utilities"].est_return_pct == 12.3
        assert out.most_affected_sectors == ["Information Technology"]
        assert out.recommendations[0].action == "HOLD"
        assert out.recommendations[0].instrument == "PSQ"

    def test_one_bad_entry_never_kills_the_block(self):
        # A single malformed entry (missing its load-bearing key under ANY
        # alias) must be dropped, not raise — the rest of the section and
        # the whole scenario block must survive.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "sector_impacts": [
                {"sector": "Energy", "est_return_pct": -10.0,
                 "rationale": "good row"},
                {"sector": "Broken", "rationale": "no value key anywhere"},
            ],
            "assumptions_to_watch": [
                {"metric": "good", "watch_for": "w"},
                {"watch_for": "orphan with no metric key"},
            ],
            "most_affected_sectors": ["Energy"],
            "hedged_sectors": [],
            "holding_impacts": [
                {"ticker": "PSQ", "est_impact_pct": 270.9, "rationale": "ok"},
                {"est_impact_pct": 5.0, "rationale": "no ticker"},
            ],
            "recommendations": [
                {"action": "GOLD", "instrument": "GLD", "confidence": 0.6},
                {"instrument": "orphan with no action or tool"},
            ],
        })
        assert [s.sector for s in out.sector_impacts] == ["Energy"]
        assert [a.metric for a in out.assumptions_to_watch] == ["good"]
        assert [h.ticker for h in out.holding_impacts] == ["PSQ"]
        assert [r.action for r in out.recommendations] == ["GOLD"]

    def test_rationale_omission_and_sector_alias_parses(self):
        # Live drift (P6 E2E round 3, 2026-08-26): deepseek-v4-flash
        # echoed the precomputed skeleton rows verbatim as holding_impacts
        # (ticker/kind/sector/gics/est_impact_pct/anchor_pct/weight_basis —
        # all numbers, no prose) and emitted sector rows with
        # 'linked_sector' instead of 'sector'. EVERY row lacked rationale,
        # and the strict field rejected the whole payload twice (json_mode
        # then raw retry) → llm=None. rationale is optional prose now; the
        # numbers are load-bearing and must survive.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "sector_impacts": [
                {"linked_sector": "Financials", "symbol": "XLF",
                 "est_return_pct": -41.0, "anchor_pct": -41.0},
                {"sector": "Information Technology",
                 "est_return_pct": -70.0},
            ],
            "assumptions_to_watch": [{"metric": "m", "watch_for": "w"}],
            "most_affected_sectors": ["Financials"],
            "hedged_sectors": [],
            "holding_impacts": [
                {"ticker": "JPM", "kind": "equity",
                 "sector": "Financial Services", "gics": "Financials",
                 "est_impact_pct": -41.0, "anchor_pct": -41.0,
                 "weight_basis": 12345.0},
                {"ticker": "PSQ", "est_impact_pct": 270.9},
            ],
            "recommendations": [
                {"action": "HOLD", "instrument": "PSQ", "confidence": 0.7}],
        })
        assert out.sector_impacts[0].sector == "Financials"
        assert out.sector_impacts[0].rationale == ""
        assert out.sector_impacts[1].sector == "Information Technology"
        assert out.holding_impacts[0].ticker == "JPM"
        assert out.holding_impacts[0].est_impact_pct == -41.0
        assert out.holding_impacts[0].rationale == ""
        assert out.holding_impacts[1].rationale == ""


# ── Assumption sensitivity (v6): lenient fields + deterministic shift math ──

class TestResolveGics:
    @pytest.mark.parametrize("label,expected", [
        ("Technology", "Technology"),
        ("technology", "Technology"),
        ("Health Care", "Health Care"),
        ("XLK", "Technology"),
        ("xle", "Energy"),
        ("Cybersecurity SaaS", "Technology"),   # keyword match
        ("  Financials  ", "Financials"),
    ])
    def test_resolves(self, label, expected):
        assert wi._resolve_gics(label) == expected

    def test_unresolvable(self):
        assert wi._resolve_gics(None) is None
        assert wi._resolve_gics("") is None
        assert wi._resolve_gics("Quantum Basket Weaving") is None


class TestAssumptionFieldsParsing:
    def test_canonical_new_fields_parse(self):
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "assumptions_to_watch": [
                {"metric": "Hyperscaler capex",
                 "linked_sector": "Technology", "if_true_shift_pp": -12.0}],
        })
        a = out.assumptions_to_watch[0]
        assert a.linked_sector == "Technology"
        assert a.if_true_shift_pp == -12.0

    def test_drifted_aliases_parse(self):
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "assumptions_to_watch": [
                {"metric": "capex cuts", "affected_sector": "Technology",
                 "shift_pp": -12.0},
                {"metric": "rates", "gics_sector": "XLK",
                 "sensitivity_pp": -8},
                {"metric": "margins", "sector": "Financials",
                 "impact_pp": 5.5},
            ],
        })
        rows = out.assumptions_to_watch
        assert rows[0].linked_sector == "Technology" and rows[0].if_true_shift_pp == -12.0
        assert rows[1].linked_sector == "XLK" and rows[1].if_true_shift_pp == -8.0
        assert rows[2].linked_sector == "Financials" and rows[2].if_true_shift_pp == 5.5

    def test_old_shape_degrades_to_none(self):
        # Pre-v6 payload: fields omitted entirely → None, never fatal.
        out = wi._WhatIfLLMOutput(**{
            "scenario_summary": "s",
            "assumptions_to_watch": [{"metric": "old shape", "timing": "Q3"}],
        })
        a = out.assumptions_to_watch[0]
        assert a.linked_sector is None and a.if_true_shift_pp is None
        assert a.timing == "Q3"

    def test_run_result_carries_fields_and_degrades(self):
        res = _run()   # _mock_llm omits the new fields
        row = res["llm"]["assumptions_to_watch"][0]
        assert "linked_sector" in row and row["linked_sector"] is None
        assert "if_true_shift_pp" in row and row["if_true_shift_pp"] is None

        def llm_with_fields(sys_p, usr_p):
            out = wi._WhatIfLLMOutput(
                scenario_summary="s",
                assumptions_to_watch=[
                    wi._AssumptionWatch(metric="capex", linked_sector="Technology",
                                        if_true_shift_pp=-12.0)],
            )
            return out, 0.001

        res2 = _run(llm_caller=llm_with_fields)
        row2 = res2["llm"]["assumptions_to_watch"][0]
        assert row2["linked_sector"] == "Technology"
        assert row2["if_true_shift_pp"] == -12.0


def _rows(*rows):
    return list(rows)


def _equity(tkr, gics, est, weight):
    return {"ticker": tkr, "kind": "equity", "sector": None, "gics": gics,
            "est_impact_pct": est, "anchor_pct": est, "weight_basis": weight}


def _product(tkr, underlying, anchor, vol, lev, est, weight, days=90):
    return {"ticker": tkr, "kind": "product", "gics": None,
            "product": {"underlying": underlying, "leverage": lev,
                        "confidence": "confirmed"},
            "est_impact_pct": est, "anchor_pct": anchor, "vol_pct": vol,
            "horizon_days": days, "weight_basis": weight}


class TestApplyAssumptionShift:
    def test_equity_shift_weighted_average(self):
        rows = _rows(_equity("TECHX", "Technology", -71.3, 1000.0),
                     _equity("MOH", "Health Care", -29.7, 1000.0))
        out = wi.apply_assumption_shift(rows, "Technology", -10.0)
        assert out["linked_gics"] == "Technology"
        assert out["base_portfolio_est_pct"] == -50.5
        # only TECHX moves: (-81.3 - 29.7) / 2
        assert out["adjusted_portfolio_est_pct"] == -55.5
        assert out["delta_pp"] == -5.0
        assert out["affected_tickers"] == ["TECHX"]

    def test_single_name_product_recomputes_closed_form(self):
        base_est = wi.leveraged_scenario_return_pct(-71.3, 32.0, 90, -1.0)["est_return_pct"]
        rows = _rows(_product("MUD", "MSFT", -71.3, 32.0, -1.0, base_est, 1000.0))
        out = wi.apply_assumption_shift(rows, "Technology", -10.0)
        expect = wi.leveraged_scenario_return_pct(-81.3, 32.0, 90, -1.0)["est_return_pct"]
        assert out["adjusted_portfolio_est_pct"] == expect
        assert out["delta_pp"] == round(expect - base_est, 2)
        assert out["delta_pp"] > 0        # inverse product gains on deeper fall
        assert out["affected_tickers"] == ["MUD"]

    def test_spy_qqq_products_unaffected_by_sector_shift(self):
        # Index dilution: PSQ tracks QQQ, not any single sector.
        psq_est = wi.leveraged_scenario_return_pct(-76.1, 32.0, 90, -1.0)["est_return_pct"]
        rows = _rows(_product("PSQ", "QQQ", -76.1, 32.0, -1.0, psq_est, 1000.0),
                     _equity("MOH", "Health Care", -29.7, 1000.0))
        out = wi.apply_assumption_shift(rows, "Technology", -10.0)
        assert out["affected_tickers"] == []
        assert out["delta_pp"] == 0.0
        assert out["adjusted_portfolio_est_pct"] == out["base_portfolio_est_pct"]

    def test_unestimated_rows_untouched(self):
        rows = _rows(_equity("CORD", None, None, 500.0),
                     _equity("TECHX", "Technology", -71.3, 500.0))
        out = wi.apply_assumption_shift(rows, "Technology", -10.0)
        assert out["affected_tickers"] == ["TECHX"]
        # base/adjusted average over the covered row only
        assert out["base_portfolio_est_pct"] == -71.3
        assert out["adjusted_portfolio_est_pct"] == -81.3

    def test_unresolvable_sector_or_missing_shift_is_noop(self):
        rows = _rows(_equity("TECHX", "Technology", -71.3, 1000.0))
        for sector, shift in (("Quantum Basket Weaving", -10.0),
                              ("Technology", None), (None, -10.0),
                              ("Technology", 0.0)):
            out = wi.apply_assumption_shift(rows, sector, shift)
            assert out["delta_pp"] == 0.0
            assert out["affected_tickers"] == []
            assert out["adjusted_portfolio_est_pct"] == -71.3

    def test_empty_rows(self):
        out = wi.apply_assumption_shift([], "Technology", -10.0)
        assert out["base_portfolio_est_pct"] is None
        assert out["adjusted_portfolio_est_pct"] is None
        assert out["delta_pp"] == 0.0

    def test_symbol_label_resolves(self):
        rows = _rows(_equity("TECHX", "Technology", -71.3, 1000.0))
        out = wi.apply_assumption_shift(rows, "XLK", -10.0)
        assert out["linked_gics"] == "Technology"
        assert out["affected_tickers"] == ["TECHX"]

    def test_non_numeric_shift_is_noop(self):
        rows = _rows(_equity("TECHX", "Technology", -71.3, 1000.0))
        out = wi.apply_assumption_shift(rows, "Technology", "lots")
        assert out["delta_pp"] == 0.0


# ── Full run with mocked LLM (skeleton + integration shape) ─────────────────

def _mock_llm(system_prompt, user_prompt):
    out = wi._WhatIfLLMOutput(
        scenario_summary="AI capex collapse hits tech hardest.",
        sector_impacts=[
            wi._SectorImpact(sector="Technology", symbol="XLK",
                             est_return_pct=-60.0, rationale="anchor-based"),
            wi._SectorImpact(sector="Consumer Staples", symbol="XLP",
                             est_return_pct=10.0, rationale="defensive"),
        ],
        assumptions_to_watch=[
            wi._AssumptionWatch(metric="Hyperscaler capex guidance",
                                watch_for="cuts > 20% confirm",
                                timing="Q3 earnings"),
        ],
        most_affected_sectors=["Technology"],
        hedged_sectors=["Consumer Staples"],
        holding_impacts=[
            wi._HoldingImpact(ticker="PSQ", est_impact_pct=300.0,
                              rationale="precomputed inverse math"),
        ],
        recommendations=[
            wi._Recommendation(action="GOLD", instrument="GLD",
                               rationale="risk-off hedge", confidence=0.7),
        ],
        search_evidence_used=False,
    )
    return out, 0.001


HOLDINGS = [
    {"ticker": "MOH", "quantity": 3524.0, "avg_cost": 170.0, "notes": None},
    {"ticker": "TECHX", "quantity": 10.0, "avg_cost": 100.0, "notes": None},
    {"ticker": "PSQ", "quantity": 1110.0, "avg_cost": 26.0, "notes": None},
    {"ticker": "MUD", "quantity": 4230.0, "avg_cost": 11.51, "notes": None},
    {"ticker": "CORD", "quantity": 5300.0, "avg_cost": 3.25, "notes": None},
]
SECTORS = {"MOH": "Managed Care", "TECHX": "Hyperscaler / Tech Conglomerate"}


def _no_prices(ticker, start, end):
    return []


def _run(override="auto", search_results=None, **kw):
    return wi.run_what_if(
        HOLDINGS, "AI Capex Meltdown",
        "Rising rates pressure AI data centres; circular financing; "
        "multiple compression in memory stocks.",
        reference_key=kw.pop("reference_key", "dotcom_2000"),
        search_override=override, horizon_days=90, sectors_map=SECTORS,
        price_fetcher=_no_prices,
        search_fn=(lambda q, d, m: list(search_results or [])),
        llm_caller=kw.pop("llm_caller", _mock_llm),
        **kw,
    )


class TestRunWhatIf:
    def test_skeleton_anchors_and_products(self):
        res = _run()
        by_tkr = {r["ticker"]: r for r in res["skeleton"]["holdings"]}

        # Regular equity → anchored to its GICS sector's dotcom return
        assert by_tkr["MOH"]["kind"] == "equity"
        assert by_tkr["MOH"]["gics"] == "Health Care"
        assert by_tkr["MOH"]["est_impact_pct"] == -29.7
        assert by_tkr["TECHX"]["est_impact_pct"] == -71.3  # XLK anchor

        # PSQ → inverse QQQ with decay: dotcom QQQ −76.1, vol default 32%
        psq = by_tkr["PSQ"]
        assert psq["kind"] == "product" and psq["product"]["leverage"] == -1.0
        assert psq["anchor_pct"] == -76.1
        assert 250.0 < psq["est_impact_pct"] < 360.0
        assert psq["decay_drag_pp"] < 0
        assert psq["vol_source"].startswith("regime_default")

        # MUD → assumed inverse MSFT via XLK anchor, flagged assumed
        mud = by_tkr["MUD"]
        assert mud["product"]["confidence"] == "assumed"
        assert mud["est_impact_pct"] > 200.0

        # CORD → unknown product, no estimate
        cord = by_tkr["CORD"]
        assert cord["kind"] == "unknown_product"
        assert cord["est_impact_pct"] is None

    def test_portfolio_estimate_excludes_uncovered(self):
        res = _run()
        assert res["skeleton"]["portfolio_est_impact_pct"] is not None
        assert res["skeleton"]["covered_weight_pct"] < 100.0

    def test_warnings_flag_assumed_and_unknown(self):
        res = _run()
        joined = " | ".join(res["warnings"])
        assert "ASSUMPTION" in joined and "MUD" in joined
        assert "CORD" in joined and "note" in joined

    def test_search_degrades_gracefully_when_empty(self):
        # CORD unknown → recommended; stub returns nothing → unavailable
        res = _run(search_results=[])
        assert res["search"]["recommended"] is True
        assert res["search"]["used"] is False
        assert res["search"]["unavailable"] is True

    def test_search_used_when_results_present(self):
        res = _run(search_results=[{"title": "CORD is a fund",
                                    "content": "CORD details…"}])
        assert res["search"]["used"] is True
        assert res["search"]["unavailable"] is False

    def test_search_scope_excludes_plain_equities(self):
        # Regression: equities (MOH, TECHX…) must never be flagged as
        # unclassified short products — only true product candidates (CORD).
        res = _run(search_results=[])
        joined = " ".join(res["search"]["reasons"])
        assert "CORD" in joined
        for tkr in ("MOH", "TECHX", "BABA"):
            assert tkr not in joined

    def test_equity_only_portfolio_with_reference_no_search(self):
        res = wi.run_what_if(
            [{"ticker": "MOH", "quantity": 10.0, "avg_cost": 170.0,
              "notes": None}],
            "AI Capex Meltdown", "concerns text long enough",
            reference_key="dotcom_2000", search_override="auto",
            sectors_map=SECTORS, price_fetcher=_no_prices,
            search_fn=lambda q, d, m: [], llm_caller=_mock_llm)
        assert res["search"]["recommended"] is False

    def test_llm_block_shape(self):
        res = _run()
        llm = res["llm"]
        assert llm["scenario_summary"]
        assert {s["sector"] for s in llm["sector_impacts"]} >= {"Technology"}
        assert llm["recommendations"][0]["action"] == "GOLD"
        assert llm["assumptions_to_watch"][0]["metric"]

    def test_llm_unavailable_degrades_to_skeleton(self):
        res = _run(llm_caller=lambda s, u: (None, 0.0))
        assert res["llm"] is None
        assert any("LLM unavailable" in w for w in res["warnings"])
        assert res["skeleton"]["holdings"]   # skeleton still served

    def test_no_reference_event_degrades(self):
        res = _run(reference_key=None)
        assert res["reference_event"] is None
        by_tkr = {r["ticker"]: r for r in res["skeleton"]["holdings"]}
        assert by_tkr["MOH"]["est_impact_pct"] is None   # no anchor
        assert by_tkr["PSQ"]["est_impact_pct"] is None
        assert res["search"]["recommended"] is True      # no anchor → search

    def test_notes_classification_flows_into_skeleton(self):
        holdings = HOLDINGS[:-1] + [
            {"ticker": "CORD", "quantity": 5300.0, "avg_cost": 3.25,
             "notes": "inverse MSFT"}]
        res = wi.run_what_if(
            holdings, "AI Capex Meltdown", "concerns text",
            reference_key="dotcom_2000", search_override="never",
            sectors_map=SECTORS, price_fetcher=_no_prices,
            search_fn=lambda q, d, m: [], llm_caller=_mock_llm)
        cord = next(r for r in res["skeleton"]["holdings"] if r["ticker"] == "CORD")
        assert cord["kind"] == "product"
        assert cord["product"]["confidence"] == "notes"
        assert cord["est_impact_pct"] is not None and cord["est_impact_pct"] > 0
        assert not any("CORD" in w for w in res["warnings"])

    def test_equity_with_short_term_note_stays_equity(self):
        holdings = [{"ticker": "BABA", "quantity": 5850.0, "avg_cost": 122.0,
                     "notes": "short-term trade idea"}]
        res = wi.run_what_if(
            holdings, "US-China Geopolitics", "concerns text",
            reference_key="gfc_2008", search_override="never",
            sectors_map={"BABA": "E-commerce"}, price_fetcher=_no_prices,
            search_fn=lambda q, d, m: [], llm_caller=_mock_llm)
        row = res["skeleton"]["holdings"][0]
        assert row["kind"] == "equity"

    def test_determinism(self):
        kwargs = dict(search_results=[], )
        a = json.dumps(_run(**kwargs), sort_keys=True)
        b = json.dumps(_run(**kwargs), sort_keys=True)
        assert a == b


# ── Service layer (store + scoping + scenario hash) ─────────────────────────

class TestWhatIfStore:
    def test_roundtrip_and_user_scope(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "whatif.db"))
        from app.backend.services import portfolio_service as ps

        ps.save_what_if(None, "hashA", {"category": "X"})
        assert ps.get_cached_what_if(None, "hashA") == {"category": "X"}
        assert ps.get_cached_what_if(7, "hashA") is None     # scope isolation

        ps.save_what_if(7, "hashA", {"category": "Y"})
        assert ps.get_cached_what_if(7, "hashA") == {"category": "Y"}
        assert ps.get_cached_what_if(None, "hashA") == {"category": "X"}
        assert ps.get_cached_what_if(None, "other") is None

    def test_scenario_hash_sensitivity(self):
        from app.backend.services import portfolio_service as ps
        holdings = [{"ticker": "A", "quantity": 1, "avg_cost": 100,
                     "notes": None}]
        base = ps.compute_scenario_hash("Custom", "concerns text",
                                        "gfc_2008", "auto", 90, holdings)
        # whitespace-normalized concerns → same hash
        assert base == ps.compute_scenario_hash(
            "Custom", "  concerns   text ", "gfc_2008", "auto", 90, holdings)
        # any real input change → different hash
        assert base != ps.compute_scenario_hash(
            "Custom", "different concerns", "gfc_2008", "auto", 90, holdings)
        assert base != ps.compute_scenario_hash(
            "Custom", "concerns text", None, "auto", 90, holdings)
        assert base != ps.compute_scenario_hash(
            "Custom", "concerns text", "gfc_2008", "never", 90, holdings)
        assert base != ps.compute_scenario_hash(
            "Custom", "concerns text", "gfc_2008", "auto", 180, holdings)
        # notes matter (they drive product classification)
        assert base != ps.compute_scenario_hash(
            "Custom", "concerns text", "gfc_2008", "auto", 90,
            [{"ticker": "A", "quantity": 1, "avg_cost": 100,
              "notes": "short SPY"}])

    def test_scenario_hash_version_sensitive(self, monkeypatch):
        from app.backend.services import portfolio_service as ps
        holdings = [{"ticker": "A", "quantity": 1, "avg_cost": 100,
                     "notes": None}]
        before = ps.compute_scenario_hash("Custom", "c", None, "auto", 90,
                                          holdings)
        monkeypatch.setattr(wi, "SCENARIO_VERSION", wi.SCENARIO_VERSION + 1)
        assert ps.compute_scenario_hash("Custom", "c", None, "auto", 90,
                                        holdings) != before

    def test_what_if_job_user_scope(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "jobs.db"))
        from app.backend.services import complacency_job_store as job_store
        from app.backend.services import portfolio_service as ps

        job_id = job_store.create_job("what_if", ticker=None, user_id=7)
        assert ps.get_what_if_job(job_id, 7) is not None
        assert ps.get_what_if_job(job_id, None) is None
        assert ps.get_what_if_job(job_id, 8) is None
        other = job_store.create_job("portfolio_replay", ticker=None, user_id=7)
        assert ps.get_what_if_job(other, 7) is None   # kind guard

    def test_meta_helpers(self):
        from app.backend.services import portfolio_service as ps
        cats = ps.list_what_if_categories()
        assert "AI Capex Meltdown" in cats and "Custom" in cats
        pm = {p["ticker"]: p for p in ps.product_knowledge()}
        assert pm["PSQ"]["confidence"] == "confirmed"
        assert pm["MUD"]["confidence"] == "assumed"
        assert pm["CORD"]["confidence"] == "unknown"

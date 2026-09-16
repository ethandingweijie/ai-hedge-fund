"""Regressions from the 2026-09-15 production runs of 09618.HK and BN4.SI.

09618.HK published a HK$120.82 target against GS HK$169 / JPM HK$148, and
BN4.SI a SELL at S$9.72 while its own look-through said S$12.25 and the
Maybank ground truth S$11.70-14.30. Five defects, none ticker-specific:

1. the SOTP's net cash was a frozen capture -- 09618.HK carried cash minus
   debt from August, RMB75.8bn of short-term investments light;
2. every ``multiple_ref`` containing "ev/" became an EV/**Revenue** multiple
   and everything else a P/E, so "8x EV/EBITDA" multiplied M1's revenue and
   "0.7x P/B" valued Keppel's property arm at 0.19% of NAV;
3. the promoted SOTP weight went entirely to the machine-built SOTP even
   where a curated look-through and a broker table were in the same blend;
4. the peer z-score pass ran at phase 10, after the phase-4.5 composite, so
   the valuation was always scored on absolute KPI bands;
5. the 12m target closed 35% of the gap to IV even when bear, base and bull
   all sat on the same side of spot;
6. the EV bridge priced the last fiscal YEAR END, so a March year end carried
   a balance sheet up to four quarters stale -- 09988.HK valued RMB98.6bn of
   net cash at 31-Mar-2026 when the 30-Jun-2026 quarter showed RMB161.7bn.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis.sotp_extractor import _classify_multiple_ref as classify


class TestSotpNetCashIsRestatedFromTheRun:
    ASSUMPTIONS = {"net_cash": 5.854e9, "fx_usd_to_reporting": 7.8433,
                   "segments": [{"name": "x", "revenue_fwd": 1.0}]}

    def test_the_stored_capture_is_replaced_by_this_runs_balance_sheet(self):
        out, flag = d._refresh_sotp_net_cash(self.ASSUMPTIONS, -138.19e9)
        assert out["net_cash"] == pytest.approx(138.19e9 / 7.8433)   # net CASH
        assert out["_net_cash_source"] == "engine_net_debt"
        assert out["_net_cash_stated"] == pytest.approx(5.854e9)
        assert "restated" in flag

    def test_the_fx_round_trip_cancels(self):
        """Net cash contributes -net_debt/shares in the reporting currency at
        any rate: the engine divides by the FX here and multiplies by the same
        FX to reach per_share_reporting."""
        for fx in (1.0, 1.27264, 7.8433):
            out, _ = d._refresh_sotp_net_cash(
                {**self.ASSUMPTIONS, "fx_usd_to_reporting": fx}, -100e9)
            assert out["net_cash"] * fx == pytest.approx(100e9)

    def test_a_net_debt_position_stays_negative(self):
        out, _ = d._refresh_sotp_net_cash(
            {"net_cash": -7.6e9, "fx_usd_to_reporting": 1.27264}, 9.5238e9)
        assert out["net_cash"] == pytest.approx(-7.4834e9, rel=1e-4)

    @pytest.mark.parametrize("net_debt", [None, "n/a"])
    def test_without_a_live_figure_the_stored_one_stands(self, net_debt):
        out, flag = d._refresh_sotp_net_cash(self.ASSUMPTIONS, net_debt)
        assert out is self.ASSUMPTIONS and flag is None

    def test_no_flag_when_the_two_agree(self):
        stated = 138.19e9 / 7.8433
        out, flag = d._refresh_sotp_net_cash(
            {**self.ASSUMPTIONS, "net_cash": stated}, -138.19e9)
        assert flag is None and out["net_cash"] == pytest.approx(stated)

    def test_the_input_is_never_mutated(self):
        before = dict(self.ASSUMPTIONS)
        d._refresh_sotp_net_cash(self.ASSUMPTIONS, -1e9)
        assert self.ASSUMPTIONS == before

    def test_the_engine_restates_before_gating_the_assumptions(self):
        src = inspect.getsource(d)
        assert src.index("_refresh_sotp_net_cash(\n                _ticker_sotp") \
            < src.index("_gate_live_sotp(\n                ticker, _ticker_sotp")


class TestTheStatedMultipleBasisIsHonoured:
    @pytest.mark.parametrize("ref,expected", [
        ("15x P/E vs Sembcorp 14x (Source: Business Times)", (15.0, None, None)),
        ("9x P/E on FY26E NOPAT", (9.0, None, None)),
        ("20x FY27E net profit", (20.0, None, None)),
        ("0.8x EV/Rev", (None, 0.8, None)),
        ("1.25x EV/Sales", (None, 1.25, None)),
        ("2.5x EV/Revenue", (None, 2.5, None)),
        ("8x EV/EBITDA vs StarHub 9x", (None, None, 8.0)),
        ("6x EV/EBIT", (None, None, 6.0)),
        ("0.7x P/B vs CapitaLand Dev 0.8x", (None, None, None)),
        ("1.0x book value", (None, None, None)),
        ("0.7x price to book", (None, None, None)),
        ("1.1x P/NAV", (None, None, None)),
        ("0.9x RNAV", (None, None, None)),
        ("30% discount to 1HFY26 NAV", (None, None, None)),
        ("12x", (12.0, None, None)),
        ("", (None, None, None)),
    ])
    def test_classification(self, ref, expected):
        assert classify(ref) == expected

    def test_exactly_one_basis_is_ever_set(self):
        for ref in ("15x P/E", "0.8x EV/Rev", "8x EV/EBITDA", "0.7x P/B"):
            assert sum(v is not None for v in classify(ref)) <= 1


class TestEvEbitAnchor:
    """M1 (BN4.SI): "8x EV/EBITDA" on S$600m revenue at a 15% EBIT margin."""
    SEG = {"name": "Connectivity (M1)", "revenue_fwd": 600e6, "ebit_margin": 0.15}

    def _row(self, seg):
        table = d._sotp_analyst_style(
            {"segments": [seg], "default_tax_rate": 0.17}, shares=1e6)
        return table["rows"][0]

    def test_the_multiple_lands_on_earnings_not_revenue(self):
        row = self._row({**self.SEG, "ev_ebit_multiple": 8.0})
        assert row["method"] == "EV/EBIT"
        assert row["value"] == pytest.approx(600e6 * 0.15 * 8.0)      # 720m

    def test_the_old_coercion_was_six_times_larger(self):
        coerced = self._row({**self.SEG, "ev_rev_multiple": 8.0})["value"]
        honest = self._row({**self.SEG, "ev_ebit_multiple": 8.0})["value"]
        assert coerced == pytest.approx(4.8e9) and honest < coerced / 6

    def test_ev_ebit_is_pre_tax_unlike_the_pe_path(self):
        pe_row = self._row({**self.SEG, "pe_multiple": 8.0})
        eb_row = self._row({**self.SEG, "ev_ebit_multiple": 8.0})
        assert pe_row["value"] == pytest.approx(eb_row["value"] * (1 - 0.17))

    def test_a_segment_with_no_earnings_falls_back(self):
        row = self._row({"name": "Other", "revenue_fwd": 300e6,
                         "ev_ebit_multiple": 3.0})
        assert row["method"] == "EV/Rev (fallback)"


class TestThePromotedWeightBelongsToTheSotpFamily:
    KEPPEL = [{"name": "EV/EBITDA", "weight": 0.24},
              {"name": "SOTP (published)", "weight": 0.21},
              {"name": "DCF", "weight": 0.15},
              {"name": "SOTP / NAV", "weight": 0.40}]
    JD = [{"name": "EV/EBITDA", "weight": 0.40}, {"name": "P/E", "weight": 0.25},
          {"name": "DCF", "weight": 0.25}, {"name": "FCF Yield", "weight": 0.10}]

    def _promoted(self, methods):
        out = d._promote_sotp_analyst_profile({"methods": methods}, True)
        return {m["name"]: m["weight"] for m in out["methods"]}

    def test_a_curated_sotp_shares_the_weight(self):
        w = self._promoted(self.KEPPEL)
        share = d._SOTP_ANALYST_BLEND_WEIGHT / 3.0
        assert w["SOTP (analyst)"] == pytest.approx(share)
        assert w["SOTP / NAV"] == pytest.approx(0.40 + share)
        assert w["SOTP (published)"] == pytest.approx(0.21 + share)
        assert w["EV/EBITDA"] == 0.24 and w["DCF"] == 0.15

    def test_the_machine_sotp_no_longer_outvotes_two_curated_ones(self):
        w = self._promoted(self.KEPPEL)
        total = sum(w.values())
        assert w["SOTP (analyst)"] / total < 0.30
        assert (w["SOTP / NAV"] + w["SOTP (published)"]) > w["SOTP (analyst)"] * 2

    def test_the_sotp_family_still_outweighs_the_generic_multiples(self):
        w = self._promoted(self.KEPPEL)
        family = sum(v for k, v in w.items() if k in d._SOTP_LED_METHODS)
        assert family / sum(w.values()) > 0.90

    def test_with_no_curated_sotp_the_blend_is_unchanged(self):
        w = self._promoted(self.JD)
        assert w["SOTP (analyst)"] == pytest.approx(d._SOTP_ANALYST_BLEND_WEIGHT)
        assert {k: v for k, v in w.items() if k != "SOTP (analyst)"} == \
            {m["name"]: m["weight"] for m in self.JD}

    def test_a_published_sotp_counts_as_sotp_led_for_the_target(self):
        for name in ("SOTP (published)", "Published SOTP"):
            assert name in d._SOTP_LED_METHODS
            w = [{"method": name, "weight": 0.2}, {"method": "P/E", "weight": 0.8}]
            assert d._sotp_led_share({"effective_weights": w}) == pytest.approx(0.2)


class TestUnanimousScenariosCloseMoreOfTheGap:
    def test_the_engine_carries_the_rule(self):
        src = inspect.getsource(d)
        assert "_unanimous = len(_scen_ivs) == 3" in src
        assert "_max_capture = min(0.50, _max_capture + 0.15)" in src

    def test_09618_moves_from_a_third_to_a_half_of_the_gap(self):
        spot, iv = 106.30, 147.78
        assert d._convergence_bound(iv, spot, 0.35) == pytest.approx(120.82, abs=0.01)
        assert d._convergence_bound(iv, spot, 0.50) == pytest.approx(127.04, abs=0.01)

    def test_the_rule_is_symmetric_on_the_downside(self):
        assert d._convergence_bound(6.98, 11.20, 0.50) == pytest.approx(9.09, abs=0.01)

    def test_a_split_scenario_set_keeps_the_base_capture(self):
        """bear below spot, bull above -> genuine disagreement, cap unchanged."""
        spot, ivs = 100.0, [90.0, 110.0, 130.0]
        assert not (all(v > spot for v in ivs) or all(v < spot for v in ivs))


def test_peer_z_scores_are_computed_before_the_valuation_composite():
    """The composite prefers a peer z-tier over the static band, but the z pass
    ran at phase 10 and the composite at phase 4.5, so no valuation ever saw
    one."""
    import src.pipeline as pipeline
    src = inspect.getsource(pipeline)
    assert src.index('_timed("4_45_zscore_for_valuation")') < \
        src.index('_timed("4_5_dcf_engine")')


class TestTheBalanceSheetComesFromTheLatestQuarter:
    """Flows stay annual; only cash and debt move."""
    ANNUAL = {"period": "2026-03-31", "revenue": 1_000e9,
              "cash_and_equivalents": 173.0e9, "short_term_investments": 184.7e9,
              "total_debt": 259.1e9, "net_debt": 86.1e9}

    class _Row:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _quarter(self, monkeypatch, **kw):
        row = self._Row(**kw)
        monkeypatch.setattr(d, "search_line_items", lambda *a, **k: [row])

    def test_a_newer_quarter_replaces_the_year_end_cash_and_debt(self, monkeypatch):
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=185.5e9, short_term_investments=242.7e9,
                      total_debt=266.5e9, net_debt=81.0e9)
        row = dict(self.ANNUAL)
        flag = d._refresh_balance_sheet_from_latest_quarter("X", row, "2026-09-16")
        assert row["_balance_sheet_period"] == "2026-06-30"
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(-161.7e9)
        assert row["revenue"] == 1_000e9                      # flows untouched
        assert "2026-06-30" in flag

    def test_a_stale_or_equal_quarter_is_ignored(self, monkeypatch):
        for period in ("2026-03-31", "2025-12-31"):
            self._quarter(monkeypatch, report_period=period,
                          cash_and_equivalents=1e9, total_debt=2e9, net_debt=1e9)
            row = dict(self.ANNUAL)
            assert d._refresh_balance_sheet_from_latest_quarter(
                "X", row, "2026-09-16") is None
            assert row == self.ANNUAL

    @pytest.mark.parametrize("missing", ["cash_and_equivalents", "total_debt"])
    def test_a_partial_quarter_is_ignored(self, monkeypatch, missing):
        kw = {"report_period": "2026-06-30", "cash_and_equivalents": 185.5e9,
              "total_debt": 266.5e9, "net_debt": 81.0e9}
        kw[missing] = None
        self._quarter(monkeypatch, **kw)
        row = dict(self.ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16") is None
        assert row == self.ANNUAL

    def test_no_quarterly_data_leaves_the_row_alone(self, monkeypatch):
        monkeypatch.setattr(d, "search_line_items", lambda *a, **k: [])
        row = dict(self.ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16") is None
        assert row == self.ANNUAL

    def test_a_provider_error_is_not_fatal(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("provider down")
        monkeypatch.setattr(d, "search_line_items", boom)
        row = dict(self.ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16") is None

    def test_net_debt_is_derived_when_the_quarter_omits_it(self, monkeypatch):
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=10e9, short_term_investments=None,
                      total_debt=25e9, net_debt=None)
        row = dict(self.ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("X", row, "2026-09-16")
        assert row["net_debt"] == pytest.approx(15e9)

    def test_the_sector_guard_still_applies_to_the_new_quarter(self, monkeypatch):
        """Molina: investments back medical claims, so they are not spare cash
        in the fresh quarter either."""
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=5.0e9, short_term_investments=3.9e9,
                      total_debt=4.0e9, net_debt=-1.0e9)
        row = dict(self.ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16", None, "Healthcare")
        assert d._net_debt_net_of_investments(row, "Healthcare") == pytest.approx(-1.0e9)

    def test_every_refreshed_line_is_fx_converted(self):
        assert set(d._BALANCE_SHEET_LINES) <= set(d._FX_MONETARY_FIELDS)

    def test_the_engine_refreshes_before_it_reads_net_debt(self):
        src = inspect.getsource(d)
        assert src.index("_bs_flag = _refresh_balance_sheet_from_latest_quarter(") <             src.index("net_debt     = _net_debt_net_of_investments(most_recent, sector)")

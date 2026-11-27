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
   net cash at 31-Mar-2026 when the 30-Jun-2026 quarter showed RMB161.7bn;
7. the holdco discount was re-decided by the research LLM every run, so one
   company carried two: 25% on the BABA ADR line in August, 15% on the
   09988.HK line in September -- about HK$21 a share.
"""
import inspect
import json

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

    def test_a_segment_with_no_earnings_is_degraded(self):
        # Owner, 2026-09-24 (item 1): no classifier constant; the reason names
        # the cited multiple that could not price.
        row = self._row({"name": "Other", "revenue_fwd": 300e6,
                         "ev_ebit_multiple": 3.0})
        assert row["method"] == "Degraded" and row["value"] is None
        assert "EV/EBIT 3x cited but EBIT is not stated" in row["degraded_reason"]


class TestThePromotedWeightBelongsToTheSotpFamily:
    """2026-09-15: the machine SOTP was outvoting two curated SOTPs 75% to 15%
    (BN4.SI). 2026-09-26: the machine SOTP no longer enters any blend -- the
    SOTP family a profile carries is the one it declares, and the extractor's
    output is an unweighted cross-check (GATE_SOTP_EXTRACTOR_CROSSCHECK)."""

    def test_the_machine_sotp_cannot_outvote_a_curated_one_because_it_is_never_weighted(self):
        assert not hasattr(d, "_promote_sotp_analyst_profile")
        src = inspect.getsource(d.run_dcf_agent)
        assert '"gate_id": "GATE_SOTP_EXTRACTOR_CROSSCHECK"' in src
        assert 'most_recent["sotp_assumptions_extractor"] = _ticker_sotp_extractor' in src
        # the extractor's output never becomes the leg's source
        assert "_ticker_sotp = None\n        if True:" in src

    def test_keppel_style_profiles_declare_their_sotp_family(self):
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
        conglomerate = P["Industrials"]["Conglomerate / Industrial (SG)"]["methods"]
        assert any(m["name"] == "SOTP (published)" for m in conglomerate)
        assert not any(m["name"] == "SOTP (analyst)" for m in conglomerate)

    def test_a_published_sotp_counts_as_sotp_led_for_the_target(self):
        for name in ("SOTP (published)", "Published SOTP"):
            assert name in d._SOTP_LED_METHODS
            w = [{"method": name, "weight": 0.2}, {"method": "P/E", "weight": 0.8}]
            assert d._sotp_led_share({"effective_weights": w}) == pytest.approx(0.2)


class TestUnanimousScenariosCloseMoreOfTheGap:
    def test_the_engine_carries_the_rule(self):
        src = inspect.getsource(d)
        assert "_unanimity_capture(_base_capture, _scen_ivs, _spot_for_cap)" in src
        assert (d._UNANIMITY_BAND, d._UNANIMITY_BONUS, d._UNANIMITY_CAP) == (0.10, 0.15, 0.50)

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
        assert d._unanimity_capture(0.35, ivs, spot) == (0.35, 0.0, None)


def _target(base_capture, ivs, spot):
    """The probability-free core of the 12m rule per scenario: spot + capture x (IV - spot)."""
    cap = d._unanimity_capture(base_capture, ivs, spot)[0]
    return [d._convergence_bound(v, spot, cap) for v in ivs]


class TestUnanimityCaptureRamp:
    """Owner, 2026-11-27: V1 nearest-scenario ramp, 10% band. The bonus is 15pt x clamp(d / 10%, 0, 1) where d is
    the distance beyond spot of the scenario nearest it, so the capture no longer jumps when that scenario crosses
    spot (U11.SI: bear 34.62 on a 35.44 spot cost the full 15 points)."""

    def test_09618_still_closes_half_the_gap(self):
        # bear HK$134 on a HK$106.30 spot: 26% beyond it, well past the band.
        cap, near, side = d._unanimity_capture(0.35, [134.0, 147.78, 170.0], 106.30)
        assert cap == 0.50 and side == "above" and near == pytest.approx(134.0 / 106.30 - 1.0)

    @pytest.mark.parametrize("ivs,spot", [
        ([126.48, 151.15, 176.94], 336.64),    # AAPL, 47% below
        ([338.23, 454.63, 580.45], 922.76),    # COST, 37% below
        ([37.2, 45.08, 53.9], 75.65),          # D05.SI, 29% below
        ([110.0, 120.0, 140.0], 100.0),        # exactly 10% above: the band edge
        ([60.0, 70.0, 85.0], 100.0),           # nearest 15% below
    ])
    def test_clearly_unanimous_names_keep_exactly_half(self, ivs, spot):
        assert d._unanimity_capture(0.35, ivs, spot)[0] == 0.50

    def test_the_bonus_never_lifts_past_the_cap(self):
        assert d._unanimity_capture(0.45, [200.0, 250.0, 300.0], 100.0)[0] == 0.50
        assert d._unanimity_capture(0.20, [200.0, 250.0, 300.0], 100.0)[0] == pytest.approx(0.35)

    def test_partial_bonus_in_the_band(self):
        # 02888.HK: bear 245.59 on a 231.00 spot, 6.3% above -> 35% + 15pt x 0.632.
        cap, near, side = d._unanimity_capture(0.35, [245.59, 302.43, 364.04], 231.00)
        assert side == "above" and near == pytest.approx(245.59 / 231.0 - 1.0)
        assert cap == pytest.approx(0.35 + 0.15 * (245.59 / 231.0 - 1.0) / 0.10)
        assert round(cap, 3) == 0.445
        # C38U.SI: bull 2.18 on a 2.26 spot, 3.5% below.
        assert round(d._unanimity_capture(0.35, [1.33, 1.72, 2.18], 2.26)[0], 3) == 0.403

    def test_continuous_as_the_nearest_scenario_crosses_spot(self):
        """UOB-like sweep: walk the bear IV through spot in fine steps; neither the capture nor the bear / base /
        bull targets may jump."""
        spot, base, bull = 35.44, 41.86, 49.93
        step = 0.001
        prev_cap = prev_t = None
        bear = spot * 0.95
        while bear <= spot * 1.05:
            cap = d._unanimity_capture(0.35, [bear, base, bull], spot)[0]
            t = _target(0.35, [bear, base, bull], spot)
            if prev_cap is not None:
                # the ramp's slope is 15pt per 10% of spot: one step moves the capture by at most that much
                assert abs(cap - prev_cap) <= 0.15 * (step / spot) / 0.10 + 1e-12
                for a, b in zip(t, prev_t):
                    assert abs(a - b) < 0.01
            prev_cap, prev_t = cap, t
            bear += step
        # at the crossing itself the bonus is zero from both sides
        assert d._unanimity_capture(0.35, [spot * (1 - 1e-9), base, bull], spot)[0] == pytest.approx(0.35, abs=1e-6)
        assert d._unanimity_capture(0.35, [spot * (1 + 1e-9), base, bull], spot)[0] == pytest.approx(0.35, abs=1e-6)

    def test_raising_any_iv_never_lowers_any_target(self):
        import random
        rng = random.Random(20261127)
        for _ in range(20000):
            spot = rng.uniform(5.0, 500.0)
            ivs = sorted(spot * rng.uniform(0.6, 1.4) for _ in range(3))
            base_cap = rng.choice((0.20, 0.30, 0.35))
            i = rng.randrange(3)
            bumped = list(ivs)
            bumped[i] *= 1.0 + rng.uniform(1e-4, 0.2)
            before, after = _target(base_cap, ivs, spot), _target(base_cap, bumped, spot)
            for a, b in zip(before, after):
                assert b >= a - 1e-9, (spot, ivs, bumped, base_cap, before, after)

    def test_symmetric_up_and_down(self):
        import random
        rng = random.Random(7)
        for _ in range(2000):
            spot = rng.uniform(5.0, 500.0)
            ivs = [spot * (1 + rng.uniform(-0.3, 0.3)) for _ in range(3)]
            mirrored = [2 * spot - v for v in ivs]
            up = d._unanimity_capture(0.35, ivs, spot)
            dn = d._unanimity_capture(0.35, mirrored, spot)
            assert up[0] == pytest.approx(dn[0]) and up[1] == pytest.approx(dn[1])
            if up[2] is None:
                assert dn[2] is None
            else:
                assert {up[2], dn[2]} == {"above", "below"}

    def test_missing_scenarios_or_spot_keep_the_base_capture(self):
        assert d._unanimity_capture(0.35, [120.0, 130.0], 100.0) == (0.35, 0.0, None)
        assert d._unanimity_capture(0.35, [120.0, 130.0, 140.0], 0.0) == (0.35, 0.0, None)

    def test_capture_displays_one_decimal_when_fractional(self):
        assert d._cap_pct(0.4454) == "44.5%" and d._cap_pct(0.35) == "35%" and d._cap_pct(0.5) == "50%"


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

    def test_an_all_zero_quarter_is_an_unreported_period(self, monkeypatch):
        """D05.SI at 2026-03-31 comes back with cash and debt both 0.0, with
        S$150bn of cash in the quarters either side."""
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=0.0, short_term_investments=0.0,
                      total_debt=0.0, net_debt=0.0)
        row = dict(self.ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16") is None
        assert row == self.ANNUAL

    def test_a_genuinely_debt_free_quarter_still_applies(self, monkeypatch):
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=40e9, short_term_investments=None,
                      total_debt=0.0, net_debt=-40e9)
        row = dict(self.ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "X", row, "2026-09-16") is not None
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(-40e9)

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
        assert src.index("_bs_flag = _refresh_balance_sheet_from_latest_quarter(") <             src.index("net_debt, _net_debt_basis = _valuation_net_debt(most_recent, sector, ticker, reported_currency, _nd_industry)")


class TestOneHoldcoDiscountPerCompany:
    def test_both_listings_of_a_company_resolve_to_the_same_value(self):
        from src.agents.analysis.sotp_snapshot import curated_holdco_discount
        for adr, hk in (("BABA", "09988.HK"), ("JD", "09618.HK")):
            a, h = curated_holdco_discount(adr), curated_holdco_discount(hk)
            assert a is not None and h is not None
            assert a[0] == pytest.approx(h[0])

    def test_the_hk_line_resolves_through_its_adr_snapshot(self):
        from src.agents.analysis.sotp_snapshot import curated_holdco_discount
        pct, source = curated_holdco_discount("09988.HK")
        assert pct == pytest.approx(0.15) and "BABA" in source

    def test_a_look_through_template_supplies_a_sourced_discount(self):
        from src.agents.analysis.sotp_snapshot import curated_holdco_discount
        pct, source = curated_holdco_discount("BN4.SI")
        assert pct == pytest.approx(0.10) and "Maybank" in source

    def test_a_company_with_no_curated_source_keeps_the_run_value(self):
        a, flag = d._pin_sotp_holdco_discount(
            "NVDA", {"holdco_discount_pct": 0.30, "segments": []})
        assert a["holdco_discount_pct"] == pytest.approx(0.30) and flag is None

    def test_the_august_adr_pick_is_overridden(self):
        a, flag = d._pin_sotp_holdco_discount(
            "BABA", {"holdco_discount_pct": 0.25, "segments": []})
        assert a["holdco_discount_pct"] == pytest.approx(0.15)
        assert a["_holdco_stated"] == pytest.approx(0.25)
        assert "pinned to 15%" in flag

    def test_no_flag_and_no_copy_when_the_run_already_agrees(self):
        src = {"holdco_discount_pct": 0.15, "segments": []}
        out, flag = d._pin_sotp_holdco_discount("09988.HK", src)
        assert out is src and flag is None

    def test_the_input_is_never_mutated(self):
        src = {"holdco_discount_pct": 0.25, "segments": []}
        d._pin_sotp_holdco_discount("BABA", src)
        assert src["holdco_discount_pct"] == pytest.approx(0.25)

    def test_the_discount_reaches_the_engine_before_the_table_is_built(self):
        src = inspect.getsource(d)
        call = "_ticker_sotp, _holdco_flag = _pin_sotp_holdco_discount("
        assert call in src          # the call site, not the def
        assert src.index(call) < src.index(
            'most_recent["sotp_assumptions"] = _ticker_sotp')

    def test_a_pinned_discount_moves_the_value_by_the_expected_ratio(self):
        base = {"segments": [{"name": "s", "revenue_fwd": 100.0, "ev_rev_multiple": 1.0}],
                "holdco_discount_pct": 0.25}
        at25 = d._sotp_analyst_style(base, shares=1.0)["per_share_reporting"]
        pinned, _ = d._pin_sotp_holdco_discount("BABA", base)
        at15 = d._sotp_analyst_style(pinned, shares=1.0)["per_share_reporting"]
        assert at15 == pytest.approx(at25 * 0.85 / 0.75)



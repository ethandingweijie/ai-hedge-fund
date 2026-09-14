"""Research rating vs trade action: the mapping, the benchmark, TSR, and compliance."""
from datetime import date

import pytest

from src.decisions import ratings as r


class TestMapping:
    def test_rating_and_action_map_one_to_one(self):
        for rating, action in r.RATING_TO_ACTION_MAP.items():
            assert r.ACTION_TO_RATING_MAP[action] is rating
            assert r.to_action(rating) is action
            assert r.to_rating(action) is rating

    @pytest.mark.parametrize("action,rating", [
        ("BUY", "OVERWEIGHT"), ("HOLD", "NEUTRAL"), ("SELL", "UNDERWEIGHT"),
        ("SHORT", "UNDERWEIGHT"), ("COVER", "NEUTRAL"), ("buy", "OVERWEIGHT"),
    ])
    def test_legacy_rows_normalize_on_read_without_mutation(self, action, rating):
        row = {"action": action, "price_target": 10.0}
        out = r.normalize_legacy_rating(row)
        assert out["research_rating"] == rating
        assert out["rating_source"] == "legacy_action"
        assert "research_rating" not in row                    # history untouched

    def test_new_rows_are_left_as_they_are(self):
        row = {"action": "SELL", "research_rating": "OVERWEIGHT"}
        assert r.normalize_legacy_rating(row)["research_rating"] == "OVERWEIGHT"

    def test_an_unrecognised_action_is_not_guessed(self):
        assert "research_rating" not in r.normalize_legacy_rating({"action": "PASS"})


class TestBenchmark:
    @pytest.mark.parametrize("ticker,sector,code", [
        ("0700.HK", "Tech", "HSTECH"), ("0005.HK", "Financials", "HSI"),
        ("D05.SI", "Financials", "STI"), ("AAPL", "Tech", "SPX"),
        ("BABA", "Tech", "SPX"),
    ])
    def test_sector_aware_per_market(self, ticker, sector, code):
        assert r.resolve_benchmark(ticker, sector)["code"] == code

    def test_expected_return_is_the_market_cost_of_equity(self):
        from src.data.sector_profiles import market_crp
        hk = r.resolve_benchmark("0700.HK", "Tech")
        assert hk["expected_return"] == pytest.approx(0.0395 + 0.0446 + market_crp("HKSE"), abs=1e-4)
        us = r.resolve_benchmark("AAPL", "Tech")
        assert us["expected_return"] == pytest.approx(0.0841, abs=1e-4)


class TestReturnAndRating:
    def test_tsr_includes_the_dividend(self):
        # the framework's own example: $100 price, $96 target, $15 dividend
        tr = r.total_return(100.0, 96.0, 15.0)
        assert tr["capital_gain"] == pytest.approx(-0.04)
        assert tr["dividend_yield"] == pytest.approx(0.15)
        assert tr["tsr"] == pytest.approx(0.11)

    def test_unknown_dividend_counts_as_zero_and_says_so(self):
        tr = r.total_return(100.0, 110.0, None)
        assert tr["tsr"] == pytest.approx(0.10) and tr["dividend_known"] is False

    def test_missing_inputs_give_no_return(self):
        assert r.total_return(0, 10, 1) is None
        assert r.total_return(10, None, 1) is None

    @pytest.mark.parametrize("tsr,expected", [
        (0.13, "OVERWEIGHT"), (0.1299, "NEUTRAL"), (0.03, "UNDERWEIGHT"),
        (0.0301, "NEUTRAL"), (0.08, "NEUTRAL"),
    ])
    def test_500_bps_thresholds_are_inclusive(self, tsr, expected):
        assert r.rate(tsr, 0.08).value == expected


def _view(**kw):
    base = dict(ticker="09988.HK", sector="Tech", price=105.90, price_as_of="2026-09-14",
                target_12m=89.57, intrinsic_value=189.33, dps=0.0)
    base.update(kw)
    return r.build_research_view(**base)


class TestResearchView:
    def test_the_9988_case_rates_the_12_month_view_and_shows_the_structural_one(self):
        v = _view(near_term_catalyst="forward EV/EBITDA on local peers prices the stock below spot")
        assert v["tactical_rating"] == "UNDERWEIGHT"
        assert v["structural_rating"] == "OVERWEIGHT"
        assert v["research_rating"] == "UNDERWEIGHT" and v["trade_action"] == "SELL"
        assert v["target_12m"] == pytest.approx(89.57)          # never an intrinsic value
        assert v["compliance"]["status"] == "explained_divergence"
        assert "forward EV/EBITDA" in v["compliance"]["notes"][0]

    def test_a_dividend_can_carry_a_below_price_target_to_overweight(self):
        # -4% capital + 20% dividend = +16% TSR vs STI 8.9% -> +709 bps
        v = _view(ticker="D05.SI", sector="Financials", price=100.0, target_12m=96.0,
                  intrinsic_value=120.0, dps=20.0)
        assert v["capital_gain_12m"] < 0
        assert v["research_rating"] == "OVERWEIGHT" and v["trade_action"] == "BUY"
        assert "-4.0%" in v["callout"] and "+20.0%" in v["callout"] and "+16.0%" in v["callout"]

    def test_a_high_absolute_tsr_can_still_be_neutral_against_its_benchmark(self):
        # the framework's absolute example (+11% TSR) is only +209 bps over the STI
        v = _view(ticker="D05.SI", sector="Financials", price=100.0, target_12m=96.0,
                  intrinsic_value=100.0, dps=15.0)
        assert v["tsr_12m"] == pytest.approx(0.11)
        assert v["research_rating"] == "NEUTRAL"

    def test_a_real_catalyst_explains_the_divergence(self):
        v = _view(near_term_catalyst="guidance cut on 14 Aug",
                  next_earnings="2026-09-25", today=date(2026, 9, 14))
        assert v["under_review"] is False
        assert v["compliance"]["status"] == "explained_divergence"
        assert "guidance cut" in v["compliance"]["notes"][0]

    def test_earnings_ahead_without_a_catalyst_puts_the_rating_under_review(self):
        v = _view(methodology_gap="forward multiples on local peers sit below long-run value",
                  next_earnings="2026-09-25", today=date(2026, 9, 14))
        assert v["under_review"] is True and v["rating_label"] == "Under Review"
        assert v["research_rating"] == "NEUTRAL" and v["trade_action"] == "HOLD"
        assert v["compliance"]["status"] == "under_review"

    def test_no_earnings_ahead_states_the_methodology_bridge(self):
        v = _view(methodology_gap="forward multiples on local peers sit below long-run value",
                  next_earnings="2026-12-01", today=date(2026, 9, 14))
        assert v["under_review"] is False
        assert "forward multiples on local peers" in v["compliance"]["notes"][0]

    def test_with_nothing_to_explain_it_the_report_is_told_to_state_one(self):
        v = _view()
        assert "must state one" in v["compliance"]["notes"][0]

    def test_a_far_sotp_value_is_disclosed_alongside_the_target(self):
        # views agree (both Overweight) so the SOTP disclosure is the status
        v = _view(target_12m=130.0, intrinsic_value=150.0, sotp_per_share=60.0)
        assert v["compliance"]["status"] == "holdco_disclosure"
        assert any("SOTP / NAV" in n for n in v["compliance"]["notes"])

    def test_the_sotp_note_is_kept_even_when_a_divergence_sets_the_status(self):
        v = _view(target_12m=120.0, intrinsic_value=125.0, sotp_per_share=60.0,
                  methodology_gap="bridge")
        assert v["compliance"]["status"] == "explained_divergence"
        assert any("SOTP / NAV" in n for n in v["compliance"]["notes"])

    def test_agreeing_views_are_clean(self):
        v = _view(target_12m=130.0, intrinsic_value=150.0)
        assert v["structural_rating"] == v["tactical_rating"] == "OVERWEIGHT"
        assert v["compliance"] == {"status": "clean", "notes": []}

    def test_no_target_no_view(self):
        assert _view(target_12m=None) is None

    def test_definition_and_disclaimer_travel_with_every_view(self):
        v = _view(target_12m=130.0, intrinsic_value=150.0)
        assert "500 bps" in v["rating_definition"]
        assert v["disclaimer"].startswith("Under regulatory reporting guidelines")

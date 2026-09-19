"""Regressions from the 2026-09-15 production 09988.HK run, which published
Underweight / SELL against a HK$158.53 IV (+47%):

1. industry routing rode the holdco look-through flag and re-profiled Alibaba
   as Traditional Retail (covered in test_industry_profile_map.py);
2. net debt ignored RMB184.7bn of short-term investments (+86.1bn net debt
   instead of ~98.6bn net cash);
3. the 12m target came from one peer EV/EBITDA multiple while the IV was
   SOTP-led, so the two pointed in opposite directions.
"""
import pytest

from src.agents.analysis import dcf_agent as d

BABA_FY2026 = {"net_debt": 86.1e9, "total_debt": 259.1e9, "cash_and_equivalents": 173.0e9,
               "short_term_investments": 184.7e9}


class TestNetDebtCountsShortTermInvestments:
    def test_alibaba_is_net_cash(self):
        assert d._net_debt_net_of_investments(BABA_FY2026, "Tech") == pytest.approx(-98.6e9)

    def test_financials_keep_the_feed_figure(self):
        for sector in ("Financials", "Insurance", "Banks"):
            assert d._net_debt_net_of_investments(BABA_FY2026, sector) == pytest.approx(86.1e9)

    def test_managed_care_investments_are_not_spare_cash(self):
        """Molina (MOH): -0.30bn net debt would have become -4.31bn, 36% of its
        market cap, from investments that back medical claims."""
        moh = {"net_debt": -0.30e9, "total_debt": 3.72e9, "cash_and_equivalents": 4.02e9,
               "short_term_investments": 4.01e9}
        assert d._net_debt_net_of_investments(moh, "Healthcare") == pytest.approx(-0.30e9)

    @pytest.mark.parametrize("ticker,nd,td,cash,sti,after", [
        ("00700.HK", 258.49e9, 400.0e9, 141.51e9, 285.83e9, -27.34e9),   # Tencent: sign flips to net cash
        ("JD", -42.55e9, 60.0e9, 102.55e9, 75.79e9, -118.34e9),
    ])
    def test_audit_names_move_to_their_net_cash(self, ticker, nd, td, cash, sti, after):
        row = {"net_debt": nd, "total_debt": td, "cash_and_equivalents": cash, "short_term_investments": sti}
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(after, rel=1e-3)

    def test_a_feed_that_already_netted_investments_is_not_netted_twice(self):
        row = {**BABA_FY2026, "net_debt": 86.1e9 - 184.7e9}
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(-98.6e9)

    @pytest.mark.parametrize("missing", ["short_term_investments", "total_debt", "cash_and_equivalents"])
    def test_without_the_pieces_the_feed_figure_stands(self, missing):
        row = {k: v for k, v in BABA_FY2026.items() if k != missing}
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(86.1e9)

    def test_no_net_debt_is_zero(self):
        assert d._net_debt_net_of_investments({}, "Tech") == 0.0

    def test_investments_are_fx_converted_with_the_rest_of_the_balance_sheet(self):
        assert "short_term_investments" in d._FX_MONETARY_FIELDS


class TestSotpLedTwelveMonthTarget:
    RUN_0915_WEIGHTS = [
        {"method": "EV/EBITDAR", "weight": 0.102564}, {"method": "P/E", "weight": 0.064103},
        {"method": "EV/Revenue", "weight": 0.038462}, {"method": "ROIC vs WACC", "weight": 0.025641},
        {"method": "SOTP (analyst)", "weight": 0.769231},
    ]

    def test_share_of_the_blend(self):
        assert d._sotp_led_share({"effective_weights": self.RUN_0915_WEIGHTS}) == pytest.approx(0.769231, rel=1e-4)
        assert d._sotp_led_share({"effective_weights": self.RUN_0915_WEIGHTS}) > d._SOTP_LED_PT_MIN_WEIGHT

    def test_no_sotp_is_not_sotp_led(self):
        assert d._sotp_led_share({"effective_weights": self.RUN_0915_WEIGHTS[:4]}) == 0.0
        assert not d._sotp_led_share({}) > d._SOTP_LED_PT_MIN_WEIGHT

    @pytest.mark.parametrize("method", ["SOTP / NAV (look-through)", "SOTP / NAV", "SOTP (segments)"])
    def test_every_sotp_is_preferred_to_a_generic_multiple(self, method):
        """Owner policy: SOTP always beats a generic multiple across a business
        group -- holdco look-throughs included, at any positive weight."""
        w = [{"method": method, "weight": 0.1}, {"method": "P/B", "weight": 0.9}]
        assert d._sotp_led_share({"effective_weights": w}) == pytest.approx(0.1)
        assert d._sotp_led_share({"effective_weights": w}) > d._SOTP_LED_PT_MIN_WEIGHT

    def test_09988_target_lands_between_spot_and_iv(self):
        """The convergence path at 35% puts the base target at ~HK$125 on the
        0915 inputs, above spot like the IV -- not HK$91.58 below it."""
        pt = d._convergence_bound(156.07, 107.80, 0.35)
        assert 107.80 < pt < 156.07 and pt == pytest.approx(124.69, abs=0.01)

    def test_the_engine_applies_the_rule_in_the_convergence_block(self):
        import inspect
        src = inspect.getsource(d)
        assert "_sotp_led = _sotp_led_share(" in src
        assert "convergence toward SOTP-led intrinsic value" in src


def test_decision_inputs_carry_the_executed_trade_action():
    import inspect
    from src.agents import portfolio_manager as pm
    src = inspect.getsource(pm)
    assert '"trade_action": (research_view or {}).get("trade_action")' in src

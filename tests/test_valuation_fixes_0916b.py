"""Regressions from the 2026-09-16 MELI and MU production runs.

1. MELI published an IV of $7,701 against a $1,829 price. The "Hyper-Growth
   Platform" profile is a tech sub-type, so EV/NTM Revenue used the mature
   SaaS terminal multiple (10x) for a marketplace whose peers trade at 2.65x
   sales -- $10,513 a share from that leg alone. A software terminal multiple
   now requires software gross margins.

2. MU published price $927.60, IV $360.24 (normalised through-cycle
   earnings) and a target of $578.91 from the forward multiple (NTM
   consensus at a peak-cycle peer multiple): two earnings bases, and a target
   61% of the way to IV in one year because the convergence cap only
   tightens on the spot side. A normalised-earnings anchor now takes the
   convergence path, as a SOTP-led one already does.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d


class TestGrossMargin:
    def test_from_gross_profit(self):
        assert d._gross_margin({"revenue": 100.0, "gross_profit": 45.0}) == pytest.approx(0.45)

    def test_from_cost_of_revenue_when_gross_profit_is_absent(self):
        assert d._gross_margin({"revenue": 100.0, "cost_of_revenue": 30.0}) == pytest.approx(0.70)

    @pytest.mark.parametrize("row", [{}, {"revenue": 0.0, "gross_profit": 1.0},
                                     {"revenue": 100.0}, None])
    def test_unknown_when_the_row_cannot_say(self, row):
        assert d._gross_margin(row) is None


class TestSaasTerminalMultipleNeedsSoftwareMargins:
    PEER = {"ev_revenue": 2.65}
    PROFILE = "Hyper-Growth Platform"

    def _mult(self, row, sector="Technology", peer=None, profile=None):
        return d._qualified_ev_revenue_multiple(
            sector, profile or self.PROFILE, "base",
            self.PEER if peer is None else peer, row)

    def test_a_marketplace_anchors_to_its_peers(self):
        """MELI FY2025: gross margin 44.5%."""
        m, basis = self._mult({"revenue": 28.89e9, "gross_profit": 12.86e9})
        assert m == pytest.approx(2.65) and "peer median" in basis

    @pytest.mark.parametrize("gm", [0.672, 0.717, 0.747, 0.824])   # SNOW MDB CRWD PLTR
    def test_software_keeps_the_terminal_multiple(self, gm):
        m, basis = self._mult({"revenue": 100.0, "gross_profit": 100.0 * gm})
        assert m == pytest.approx(d._terminal_multiple_ev_revenue(self.PROFILE, "base"))
        assert basis == "SaaS terminal"

    def test_the_floor_is_inclusive(self):
        m, _ = self._mult({"revenue": 100.0, "gross_profit": 65.0})
        assert m == pytest.approx(d._terminal_multiple_ev_revenue(self.PROFILE, "base"))

    def test_without_a_peer_multiple_the_terminal_is_scaled_by_margin(self):
        m, basis = self._mult({"revenue": 100.0, "gross_profit": 40.0}, peer={})
        terminal = d._terminal_multiple_ev_revenue(self.PROFILE, "base")
        assert m == pytest.approx(terminal * (0.40 / 0.80) ** 2)
        assert "scaled" in basis

    def test_an_unknown_margin_changes_nothing(self):
        m, _ = self._mult({})
        assert m == pytest.approx(d._terminal_multiple_ev_revenue(self.PROFILE, "base"))

    def test_a_non_tech_profile_still_uses_the_peer_multiple(self):
        m, basis = self._mult({"revenue": 100.0, "gross_profit": 90.0},
                              sector="Consumer Cyclical")
        assert m == pytest.approx(2.65) and basis == "peer"

    def test_meli_lands_at_its_price_instead_of_six_times_it(self):
        """NTM revenue $33.2bn, net debt $7.45bn, 50.7m shares."""
        per_share = lambda mult: (33.2e9 * mult - 7.446e9) / 50.7e6
        assert per_share(10.0) > 6_000
        m, _ = self._mult({"revenue": 28.89e9, "gross_profit": 12.86e9})
        assert per_share(m) == pytest.approx(1_588, rel=0.01)

    def test_both_revenue_multiple_methods_go_through_the_qualification(self):
        src = inspect.getsource(d._compute_method_value) if hasattr(
            d, "_compute_method_value") else inspect.getsource(d)
        assert src.count("_qualified_ev_revenue_multiple(") >= 2
        assert "base_mult = _terminal_multiple_ev_revenue(profile_name, scenario)" not in src


class TestNormalisedEarningsTargetConverges:
    """The normalised-earnings convergence path and which PT labels reach it.

    Reinforced by owner decision 2, 2026-09-17 — golden re-baseline reason:
    "Deprecate GGM book target dispatch for non-bank financials; eliminate
    synthetic positive tangible book fallback."

    The exclusion asserted below is unchanged by that decision (the label set is
    not edited), but the decision makes it load-bearing in a second way. The
    GGM book target used to be reachable by any Financials-sector name, because
    the 12m dispatch tested the sector. A fee-driven franchise routed onto it
    had no positive tangible book, ``_compute_bank_metrics`` synthesized one,
    and Visa published a 12m target of $12.84 against a base IV of $428.47 —
    3.0% of its own valuation. The dispatch now reads
    ``_is_balance_sheet_financial``, and a non-positive tangible book fails the
    method outright rather than being floored to 70% of equity. So a name that
    still lands on this label is a balance-sheet business, which is the only
    case where a book-value target is a forward-consensus target at all.
    ``tests/test_valuation_fixes_0917.py`` pins both halves.
    """

    def test_forward_consensus_paths_are_recognised(self):
        assert "EV/EBITDA or EV/Revenue forward multiple" in d._FORWARD_CONSENSUS_PT_LABELS
        assert "forward P/E x Year-1 EPS" in d._FORWARD_CONSENSUS_PT_LABELS
        # Not a forward-consensus path: it is a book-value target, correct only
        # for a balance-sheet business. See the class docstring — the dispatch
        # narrowing is what keeps a non-bank from reaching it.
        assert "GGM target P/B x book value per share" not in d._FORWARD_CONSENSUS_PT_LABELS

    def test_the_engine_carries_the_rule(self):
        src = inspect.getsource(d)
        assert '"(norm)" in (_anchor_method or "")' in src
        assert "convergence toward normalised-earnings intrinsic value" in src
        # the SOTP-led branch is untouched and still wins when both apply
        assert "not _sotp_led" in src

    def test_mu_target_sits_between_iv_and_spot(self):
        spot, iv = 927.60, 360.24
        for capture in (0.35, 0.50):
            pt = d._convergence_bound(iv, spot, capture)
            assert iv < pt < spot
        assert d._convergence_bound(iv, spot, 0.50) == pytest.approx(643.92, abs=0.01)

    def test_the_published_target_overshot_every_allowed_capture(self):
        spot, iv, published = 927.60, 360.24, 578.91
        travelled = (spot - published) / (spot - iv)
        assert travelled == pytest.approx(0.615, abs=0.005)
        assert travelled > 0.50

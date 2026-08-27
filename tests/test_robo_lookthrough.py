"""
tests/test_robo_lookthrough.py
==============================
Cover for the Robo Strategy equity look-through.

The plan tells a user they hold VTI and KWEB; it does not tell them their
largest company exposure is NVDA, or that Tencent arrives through two funds.
calculate_equity_lookthrough resolves equity funds to their constituents so
fund choice and company exposure are both visible.

The tests here mostly guard ONE decision. Allocation is distributed strictly
in proportion to the weights available, with the shortfall named as
`uncovered_pct` — never renormalised over the visible rows. Renormalising
assumes the unseen tail resembles the visible head, which holds at 99%
coverage and fails badly below it: BNDX reports 3 usable rows totalling
0.01% of the fund, and renormalising put a single bond at 4.6% of the whole
portfolio. Proportional allocation can understate a position but can never
invent one.
"""
from __future__ import annotations

import pytest

from app.backend.services.robo_strategy_service import (
    _EQUITY_BUCKETS, calculate_equity_lookthrough,
)


def _fund(ticker, pct, holdings, bucket="stock", covered=None):
    return {
        "ticker": ticker, "name": ticker, "category": bucket,
        "allocationPercent": pct,
        "sectorWeights": {"Technology": 100.0},
        "regionWeights": {"US": 100.0},
        "holdings": [{"asset": a, "name": n, "weightPercentage": w}
                     for a, n, w in holdings],
        "holdingsCount": len(holdings),
        "holdingsCoveredPct": (covered if covered is not None
                               else sum(w for _, _, w in holdings)),
    }


def _stock(symbol, pct):
    return {"symbol": symbol, "name": symbol, "allocationPercent": pct}


class TestProportionalAllocation:
    def test_weights_scale_by_fund_allocation(self):
        out = calculate_equity_lookthrough(
            [_fund("VTI", 50.0, [("NVDA", "Nvidia", 10.0), ("AAPL", "Apple", 5.0)],
                   covered=15.0)], total_investment=1000.0)
        by = {p["symbol"]: p for p in out["positions"]}
        assert by["NVDA"]["allocationPercent"] == pytest.approx(5.0)   # 50% * 10%
        assert by["AAPL"]["allocationPercent"] == pytest.approx(2.5)
        assert by["NVDA"]["amount"] == pytest.approx(50.0)

    def test_shortfall_is_named_not_spread(self):
        """The BNDX failure in miniature: 3 rows totalling 0.01% of a fund
        must not absorb the fund's entire plan weight."""
        out = calculate_equity_lookthrough(
            [_fund("THIN", 9.5, [("X", "X Bond", 0.01)], covered=0.01)])
        top = out["positions"][0]
        # allocationPercent is rounded to 4dp, so compare at that resolution.
        assert top["allocationPercent"] == pytest.approx(9.5 * 0.01 / 100, abs=1e-4)
        assert top["allocationPercent"] < 0.01, "must not be inflated to 9.5%"
        assert out["uncovered_pct"] == pytest.approx(9.5, abs=0.01)

    def test_full_coverage_leaves_nothing_uncovered(self):
        out = calculate_equity_lookthrough(
            [_fund("X", 100.0, [("A", "A", 60.0), ("B", "B", 40.0)])])
        assert out["resolved_pct"] == pytest.approx(100.0)
        assert out["uncovered_pct"] == pytest.approx(0.0, abs=0.01)

    def test_accounting_always_sums_to_the_plan(self):
        items = [
            _fund("EQ", 40.0, [("A", "A", 50.0)], covered=50.0),
            _fund("BOND", 35.0, [("Z", "Z 2028", 0.01)], bucket="bond"),
            _fund("GOLD", 25.0, [], bucket="commodity"),
        ]
        out = calculate_equity_lookthrough(items)
        total = out["resolved_pct"] + out["uncovered_pct"] + out["non_equity_pct"]
        assert total == pytest.approx(100.0, abs=0.05)


class TestNonEquityFunds:
    @pytest.mark.parametrize("bucket", ["bond", "commodity"])
    def test_excluded_from_an_equity_lookthrough(self, bucket):
        """BNDX's constituents are instruments like 'Dexia SA 01/21/2028' and
        GLD's is bullion — neither is a company holding."""
        out = calculate_equity_lookthrough(
            [_fund("F", 30.0, [("DXBGY", "Dexia SA 01/21/2028", 60.0)],
                   bucket=bucket)])
        assert out["positions"] == []
        assert out["non_equity_pct"] == pytest.approx(30.0)
        assert "F" not in out["coverage"]

    def test_equity_buckets_are_stock_and_reit(self):
        assert _EQUITY_BUCKETS == {"stock", "reit"}

    def test_reits_are_looked_through(self):
        out = calculate_equity_lookthrough(
            [_fund("VNQ", 100.0, [("PLD", "Prologis", 10.0)], bucket="reit")])
        assert out["positions"][0]["symbol"] == "PLD"
        assert out["non_equity_pct"] == 0.0


class TestAggregationAcrossFunds:
    def test_same_company_via_two_funds_is_merged(self):
        """The headline case: Tencent held through two different funds."""
        out = calculate_equity_lookthrough([
            _fund("FXI", 50.0, [("0700.HK", "Tencent", 10.0)], covered=10.0),
            _fund("KWEB", 50.0, [("0700.HK", "Tencent", 8.0)], covered=8.0),
        ])
        tencent = [p for p in out["positions"] if p["symbol"] == "0700.HK"]
        assert len(tencent) == 1, "must aggregate, not duplicate"
        assert tencent[0]["allocationPercent"] == pytest.approx(9.0)  # 5.0 + 4.0
        assert {v["ticker"] for v in tencent[0]["viaFunds"]} == {"FXI", "KWEB"}

    def test_via_funds_sorted_heaviest_first(self):
        out = calculate_equity_lookthrough([
            _fund("SMALL", 10.0, [("A", "A", 10.0)], covered=10.0),
            _fund("BIG", 90.0, [("A", "A", 10.0)], covered=10.0),
        ])
        via = out["positions"][0]["viaFunds"]
        assert [v["ticker"] for v in via] == ["BIG", "SMALL"]

    def test_repeat_rows_from_one_fund_collapse(self):
        """Some funds list a company twice (multiple share lines)."""
        out = calculate_equity_lookthrough(
            [_fund("F", 100.0, [("EMAAR.AE", "Emaar", 1.0),
                                ("EMAAR.AE", "Emaar", 0.5)], covered=1.5)])
        pos = out["positions"][0]
        assert pos["allocationPercent"] == pytest.approx(1.5)
        assert len(pos["viaFunds"]) == 1
        assert pos["viaFunds"][0]["contributionPct"] == pytest.approx(1.5)

    def test_positions_sorted_by_weight(self):
        out = calculate_equity_lookthrough(
            [_fund("F", 100.0, [("A", "A", 1.0), ("B", "B", 9.0),
                                ("C", "C", 5.0)], covered=15.0)])
        assert [p["symbol"] for p in out["positions"]] == ["B", "C", "A"]

    def test_top_n_caps_positions_but_not_the_count(self):
        holdings = [(f"S{i}", f"S{i}", 1.0) for i in range(40)]
        out = calculate_equity_lookthrough([_fund("F", 100.0, holdings, covered=40.0)],
                                           top_n=10)
        assert len(out["positions"]) == 10
        assert out["position_count"] == 40


class TestIndividualStocks:
    def test_a_stock_resolves_to_itself(self):
        out = calculate_equity_lookthrough([_stock("AAPL", 25.0)])
        assert out["positions"][0]["symbol"] == "AAPL"
        assert out["positions"][0]["allocationPercent"] == pytest.approx(25.0)
        assert out["positions"][0]["viaFunds"][0]["direct"] is True

    def test_stocks_and_funds_combine(self):
        out = calculate_equity_lookthrough([
            _stock("NVDA", 20.0),
            _fund("VTI", 80.0, [("NVDA", "Nvidia", 10.0)], covered=10.0),
        ])
        nvda = out["positions"][0]
        assert nvda["symbol"] == "NVDA"
        assert nvda["allocationPercent"] == pytest.approx(28.0)  # 20 + 8


class TestDegradation:
    def test_empty_plan(self):
        out = calculate_equity_lookthrough([])
        assert out["positions"] == []
        assert out["position_count"] == 0

    def test_fund_with_no_holdings_is_uncovered_not_dropped(self):
        out = calculate_equity_lookthrough([_fund("MUB", 40.0, [], bucket="stock")])
        assert out["positions"] == []
        assert out["uncovered_pct"] == pytest.approx(40.0)

    def test_zero_allocation_ignored(self):
        out = calculate_equity_lookthrough([_fund("F", 0.0, [("A", "A", 100.0)])])
        assert out["positions"] == []

    def test_rows_without_an_asset_symbol_skipped(self):
        out = calculate_equity_lookthrough(
            [_fund("F", 100.0, [(None, "Cash", 5.0), ("A", "A", 10.0)],
                   covered=15.0)])
        assert [p["symbol"] for p in out["positions"]] == ["A"]

    def test_coverage_reported_per_fund(self):
        out = calculate_equity_lookthrough([
            _fund("DEEP", 50.0, [("A", "A", 99.0)], covered=99.0),
            _fund("THIN", 50.0, [("B", "B", 20.0)], covered=20.0),
        ])
        assert out["coverage"]["DEEP"] == pytest.approx(99.0)
        assert out["coverage"]["THIN"] == pytest.approx(20.0)


class TestPortfolioWiring:
    def test_generate_portfolio_exposes_the_lookthrough(self, monkeypatch):
        """The payload key the frontend reads must exist."""
        from app.backend.services import robo_strategy_service as rs
        monkeypatch.setattr(rs.etf_metadata_service, "get_etf_universe", lambda *a, **k: [])
        monkeypatch.setattr(rs, "_get_stock_candidates", lambda: [])
        out = rs.generate_portfolio({
            "risk_tolerance": "moderate", "time_horizon": "long",
            "sector_preferences": {}, "geography_preferences": {},
            "investment_amount": 1000.0,
        })
        assert "etf_equity_lookthrough" in out
        lt = out["etf_equity_lookthrough"]
        for key in ("positions", "position_count", "resolved_pct",
                    "uncovered_pct", "non_equity_pct", "coverage"):
            assert key in lt

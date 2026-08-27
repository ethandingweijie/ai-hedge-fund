"""
tests/test_intl_provider.py
===========================
Cover for src/tools/intl_provider.py — the FMP-primary / legacy-fallback
dispatch for the HK and SG markets.

The contract that matters: nothing that worked before the migration may
regress. Any empty or failed FMP response must fall through to the provider
that served the request previously (AKShare for HK, yfinance for SG), and
the per-market kill-switch must be able to take FMP out of the path
entirely.

Offline — conftest strips live keys and every FMP call here is a stub.
"""
from __future__ import annotations

import pytest

from src.tools import intl_provider as ip


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Each test starts from the default (fmp) for both markets."""
    monkeypatch.delenv("HK_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("SG_DATA_PROVIDER", raising=False)
    yield


# ── Market detection ────────────────────────────────────────────────────────

class TestDetectMarket:
    @pytest.mark.parametrize("ticker,expected", [
        ("00700.HK", "hk"),
        ("0700.HK", "hk"),
        ("700", "hk"),
        ("9988", "hk"),
        ("D05.SI", "sg"),
        ("D05", "sg"),          # known-code registry
        ("AAPL", None),
        ("MSFT", None),
        ("", None),
        (None, None),
    ])
    def test_detection(self, ticker, expected):
        assert ip.detect_market(ticker) == expected

    def test_hk_is_tested_before_sg(self):
        """HK codes are purely numeric, so HK must win the ordering — the
        same constraint documented in src/tools/sg/ticker.py."""
        assert ip.detect_market("00700") == "hk"


# ── Symbol mapping ──────────────────────────────────────────────────────────

class TestSymbols:
    @pytest.mark.parametrize("ticker,expected", [
        ("00700.HK", "0700.HK"),   # FMP rejects the 5-digit canonical
        ("700", "0700.HK"),
        ("09988.HK", "9988.HK"),
        ("80700", "80700.HK"),     # genuine 5-digit RMB counter survives
    ])
    def test_hk_fmp_symbol(self, ticker, expected):
        assert ip.fmp_symbol(ticker, "hk") == expected

    @pytest.mark.parametrize("ticker,expected", [
        ("D05", "D05.SI"),
        ("D05.SI", "D05.SI"),
    ])
    def test_sg_fmp_symbol(self, ticker, expected):
        assert ip.fmp_symbol(ticker, "sg") == expected

    def test_canonical_differs_from_fmp_for_hk(self):
        """The whole reason relabelling exists."""
        assert ip.fmp_symbol("700", "hk") == "0700.HK"
        assert ip.canonical_symbol("700", "hk") == "00700.HK"

    def test_canonical_matches_fmp_for_sg(self):
        assert ip.fmp_symbol("D05", "sg") == ip.canonical_symbol("D05", "sg")

    def test_unknown_market(self):
        assert ip.fmp_symbol("AAPL", "us") is None
        assert ip.canonical_symbol("AAPL", "us") is None


# ── Kill switch ─────────────────────────────────────────────────────────────

class TestKillSwitch:
    def test_default_is_fmp(self):
        assert ip.provider_for("hk") == "fmp"
        assert ip.provider_for("sg") == "fmp"
        assert ip.use_fmp("hk") and ip.use_fmp("sg")

    def test_legacy_disables_fmp(self, monkeypatch):
        monkeypatch.setenv("HK_DATA_PROVIDER", "legacy")
        assert not ip.use_fmp("hk")
        assert ip.use_fmp("sg"), "SG must be unaffected by the HK switch"

    def test_switch_is_per_market(self, monkeypatch):
        monkeypatch.setenv("SG_DATA_PROVIDER", "legacy")
        assert ip.use_fmp("hk")
        assert not ip.use_fmp("sg")

    def test_case_and_whitespace_tolerant(self, monkeypatch):
        monkeypatch.setenv("HK_DATA_PROVIDER", "  LEGACY  ")
        assert not ip.use_fmp("hk")

    def test_kill_switch_skips_the_call_entirely(self, monkeypatch):
        """Legacy mode must not spend an FMP round-trip before falling back."""
        monkeypatch.setenv("HK_DATA_PROVIDER", "legacy")
        calls = []
        served = ip.try_fmp("hk", "00700.HK",
                            lambda s: calls.append(s) or ["row"])
        assert served is None
        assert calls == []


# ── Fallback behaviour ──────────────────────────────────────────────────────

class _Row:
    """Stand-in for a pydantic row carrying a ticker field."""
    def __init__(self, ticker):
        self.ticker = ticker


class TestTryFmp:
    def test_serves_from_fmp_on_success(self):
        seen = {}

        def call(symbol):
            seen["symbol"] = symbol
            return [_Row(symbol)]

        served = ip.try_fmp("hk", "00700.HK", call)
        assert served is not None
        assert seen["symbol"] == "0700.HK", "FMP must receive the 4-digit form"

    def test_relabels_to_canonical(self):
        """Downstream consumers key on the canonical ticker, so an FMP row
        stamped 0700.HK must come back as 00700.HK."""
        served = ip.try_fmp("hk", "00700.HK", lambda s: [_Row(s)])
        assert [r.ticker for r in served] == ["00700.HK"]

    def test_relabels_dict_rows(self):
        served = ip.try_fmp("hk", "700", lambda s: [{"ticker": s, "v": 1}])
        assert served[0]["ticker"] == "00700.HK"
        assert served[0]["v"] == 1

    def test_relabel_can_be_disabled(self):
        served = ip.try_fmp("hk", "00700.HK", lambda s: [_Row(s)], relabel=False)
        assert served[0].ticker == "0700.HK"

    def test_rows_without_ticker_pass_through(self):
        served = ip.try_fmp("hk", "00700.HK", lambda s: [{"close": 1.0}])
        assert served == [{"close": 1.0}]

    def test_empty_response_falls_back(self):
        assert ip.try_fmp("hk", "00700.HK", lambda s: []) is None
        assert ip.try_fmp("hk", "00700.HK", lambda s: None) is None

    def test_exception_falls_back_not_raises(self):
        def boom(symbol):
            raise RuntimeError("FMP exploded")
        assert ip.try_fmp("hk", "00700.HK", boom) is None

    def test_custom_validate(self):
        """market_cap returns a bare float; 0 must count as not-served."""
        assert ip.try_fmp("hk", "00700.HK", lambda s: 0.0,
                          validate=lambda v: bool(v), relabel=False) is None
        assert ip.try_fmp("hk", "00700.HK", lambda s: 1.5,
                          validate=lambda v: bool(v), relabel=False) == 1.5

    def test_unmappable_symbol_falls_back(self):
        assert ip.try_fmp("us", "AAPL", lambda s: ["x"]) is None


# ── The re-entrancy flag ────────────────────────────────────────────────────

class TestForceFlag:
    def test_flag_is_set_during_the_call_and_cleared_after(self):
        observed = []
        ip.try_fmp("hk", "00700.HK",
                   lambda s: observed.append(ip.fmp_forced()) or ["row"])
        assert observed == [True], "HK/SG branches must step aside during the call"
        assert ip.fmp_forced() is False, "flag must not leak past the call"

    def test_flag_cleared_after_exception(self):
        def boom(symbol):
            assert ip.fmp_forced() is True
            raise RuntimeError("boom")
        ip.try_fmp("hk", "00700.HK", boom)
        assert ip.fmp_forced() is False

    def test_flag_is_false_by_default(self):
        assert ip.fmp_forced() is False

    def test_nested_calls_restore_prior_state(self):
        def outer(symbol):
            inner = ip.try_fmp("sg", "D05.SI", lambda s2: ["inner"])
            assert inner is not None
            # still inside the outer call, so the flag must still be set
            assert ip.fmp_forced() is True
            return ["outer"]
        assert ip.try_fmp("hk", "00700.HK", outer) is not None
        assert ip.fmp_forced() is False

    def test_flag_is_thread_local(self):
        import threading
        result = {}

        def other_thread():
            result["forced"] = ip.fmp_forced()

        def call(symbol):
            t = threading.Thread(target=other_thread)
            t.start()
            t.join()
            return ["row"]

        ip.try_fmp("hk", "00700.HK", call)
        assert result["forced"] is False, "force flag must not leak across threads"


# ── api.py wiring ───────────────────────────────────────────────────────────

class TestApiRouting:
    """The branches in api.py must consult the force flag, or the re-entrant
    FMP call would recurse into the HK/SG legacy path forever."""

    def test_branches_are_guarded(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent
               / "src" / "tools" / "api.py").read_text(encoding="utf-8")
        # Every routing branch that now has an FMP arm must be flag-guarded.
        assert src.count("and not fmp_forced()") >= 12
        assert "from src.tools.intl_provider import fmp_forced, try_fmp" in src

    def test_us_tickers_never_touch_the_dispatcher(self, monkeypatch):
        """A US ticker must not be classified into either market."""
        for t in ("AAPL", "BRK.B", "GOOGL"):
            assert ip.detect_market(t) is None

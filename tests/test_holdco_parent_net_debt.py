"""Parent-level net debt for the holdco look-through.

A majority-owned listed stake is marked at market with its own debt already
inside the market cap, while consolidated net debt carries 100% of that
subsidiary's borrowings. Subtracting consolidated net debt counted it twice
(Jardine C&C: NAV 49% below price with Astra's borrowings taken off a stake
valued at market).
"""
import pytest

from src.agents.analysis import holdco_sotp as h


@pytest.fixture(autouse=True)
def templates(monkeypatch):
    tpls = {"templates": {
        "C07.SI": {"currency": "SGD", "holdco_discount": [0.1, 0.1], "divisions": [
            {"name": "PT Astra International", "basis": "market_stake", "listed": "ASII.JK", "stake_pct": 0.5011},
            {"name": "Direct Motor Interests", "basis": "ev_ebitda_range", "multiple_range": [6, 8]}]},
        "P15.SI": {"currency": "SGD", "holdco_discount": [0.45, 0.45], "divisions": [
            {"name": "PCCW", "basis": "market_stake", "listed": "00008.HK", "stake_pct": 0.2269}]},
        "X.HK": {"currency": "HKD", "divisions": [
            {"name": "Sub A", "basis": "market_stake", "listed": "A.HK", "stake_pct": 0.75},
            {"name": "Sub B", "basis": "market_stake", "listed": "B.HK", "stake_pct": 0.60},
            {"name": "Unsourced", "basis": "market_stake", "listed": "C.HK", "stake_pct": None}]},
    }}
    monkeypatch.setattr(h, "_CACHE", tpls)
    monkeypatch.setattr(h, "template_for", lambda t: tpls["templates"].get(t))


def test_a_consolidated_listed_subsidiarys_debt_is_removed_in_full(monkeypatch):
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: {"ASII.JK": (40_000e9, "IDR")}.get(t))
    monkeypatch.setattr(h, "_fx", lambda a, b: {("IDR", "SGD"): 0.00008}.get((a, b), 1.0))
    # consolidated SGD 5.0bn includes Astra's IDR 40,000bn (= SGD 3.2bn), all of it
    assert h.parent_net_debt("C07.SI", "2026-09-15", 5.0e9, "SGD") == pytest.approx(1.8e9)


def test_minority_stakes_are_equity_accounted_so_nothing_is_removed(monkeypatch):
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: pytest.fail("fetched an associate's debt"))
    assert h.parent_net_debt("P15.SI", "2026-09-15", 0.3e9, "SGD") == 0.3e9


def test_every_majority_stake_is_removed_and_currencies_converted(monkeypatch):
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: {"A.HK": (10e9, "HKD"), "B.HK": (2e9, "CNY")}.get(t))
    monkeypatch.setattr(h, "_fx", lambda a, b: {("CNY", "HKD"): 1.08}.get((a, b), 1.0))
    assert h.parent_net_debt("X.HK", "2026-09-15", 50e9, "HKD") == pytest.approx(50e9 - 10e9 - 2e9 * 1.08)


def test_an_unverifiable_subtraction_is_refused(monkeypatch):
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: None)
    assert h.parent_net_debt("C07.SI", "2026-09-15", 5.0e9, "SGD") is None
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: (1e9, "IDR"))
    monkeypatch.setattr(h, "_fx", lambda a, b: None)
    assert h.parent_net_debt("C07.SI", "2026-09-15", 5.0e9, "SGD") is None


class TestTickerAllowlist:
    """The four names within 30% of price go live; everything else stays off."""

    def test_flag_off_means_nobody(self, monkeypatch):
        monkeypatch.delenv(h.FLAG, raising=False)
        monkeypatch.setenv(h.TICKERS_ENV, "C07.SI")
        assert h.enabled_for("C07.SI") is False

    def test_flag_on_without_allowlist_means_everyone(self, monkeypatch):
        monkeypatch.setenv(h.FLAG, "true")
        monkeypatch.delenv(h.TICKERS_ENV, raising=False)
        assert h.enabled_for("VC2.SI") is True

    @pytest.mark.parametrize("ticker,expected", [
        ("C07.SI", True), ("148.HK", True), ("00148.HK", True), ("Y92.SI", True), ("F34.SI", True),
        ("00001.HK", False), ("VC2.SI", False), ("S08.SI", False), ("01548.HK", False), ("P15.SI", False),
    ])
    def test_flag_on_with_allowlist_means_only_those(self, monkeypatch, ticker, expected):
        monkeypatch.setenv(h.FLAG, "true")
        monkeypatch.setenv(h.TICKERS_ENV, "C07.SI, 00148.HK,Y92.SI,F34.SI")
        assert h.enabled_for(ticker) is expected


def test_removing_more_than_the_consolidated_debt_is_refused(monkeypatch):
    """CITIC: CITIC Bank's borrowings exceed the group's consolidated net debt."""
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: {"A.HK": (2_500e9, "HKD"), "B.HK": (600e9, "HKD")}.get(t))
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    assert h.parent_net_debt("X.HK", "2026-09-15", 1_989e9, "HKD") is None


def test_a_net_cash_group_is_left_as_computed(monkeypatch):
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: (1e9, "SGD"))
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    assert h.parent_net_debt("C07.SI", "2026-09-15", -2e9, "SGD") == pytest.approx(-3e9)


def test_a_dated_template_figure_replaces_the_lagging_feed(monkeypatch):
    """Olam: FMP's S$13.83bn predates the Tranche 1 proceeds; the template states S$8,880.6m."""
    tpl = {"currency": "SGD", "divisions": [{"name": "ofi", "basis": "ev_ebit_range"}],
           "net_debt_override": {"value": 8880.6, "currency": "SGD", "units_multiplier": 1000000,
                                 "as_of": "2026-06-30"}}
    monkeypatch.setattr(h, "template_for", lambda t: tpl)
    monkeypatch.setattr(h, "_net_debt_of", lambda t, d: pytest.fail("fetched with an override present"))
    assert h.parent_net_debt("VC2.SI", "2026-09-15", 13.83e9, "SGD") == pytest.approx(8880.6e6)
    assert h.parent_net_debt("VC2.SI", "2026-09-15", None, "SGD") == pytest.approx(8880.6e6)


def test_an_override_in_another_currency_is_converted(monkeypatch):
    tpl = {"currency": "SGD", "divisions": [],
           "net_debt_override": {"value": -100.0, "currency": "USD", "units_multiplier": 1000000}}
    monkeypatch.setattr(h, "template_for", lambda t: tpl)
    monkeypatch.setattr(h, "_fx", lambda a, b: {("USD", "SGD"): 1.3}.get((a, b)))
    assert h.parent_net_debt("S08.SI", "2026-09-15", 5e9, "SGD") == pytest.approx(-130e6)
    monkeypatch.setattr(h, "_fx", lambda a, b: None)
    assert h.parent_net_debt("S08.SI", "2026-09-15", 5e9, "SGD") is None


def test_the_committed_templates_carry_the_approved_calibration():
    import json
    from pathlib import Path
    doc = json.loads((Path(h.__file__).resolve().parents[2] / "data" / "holdco_sotp_templates.json")
                     .read_text(encoding="utf-8"))
    t = doc["templates"]
    assert t["P15.SI"]["holdco_discount"] == [0.2, 0.3]
    assert t["01548.HK"]["divisions"][1]["multiple_range"] == [17.0, 23.0]
    assert not any("Australia" in d["name"] for d in t["S08.SI"]["divisions"])
    assert t["S08.SI"]["net_debt_override"]["as_of"] == "2026-03-31"
    assert t["VC2.SI"]["net_debt_override"]["value"] == 8880.6
    assert "RMI" in t["VC2.SI"]["net_debt_override"]["source"]


def test_no_consolidated_figure_or_template_gives_none():
    assert h.parent_net_debt("C07.SI", "2026-09-15", None, "SGD") is None
    assert h.parent_net_debt("MSFT", "2026-09-15", 1e9, "USD") is None

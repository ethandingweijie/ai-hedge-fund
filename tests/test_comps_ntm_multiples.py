"""Forward (NTM) peer multiples in the weekly comps refresh, and the forward
legs that may price on them.

Owner rule, 2026-09-21: "Default to NTM EV/EBITDA or FY1/FY2 blended P/E rather
than trailing LTM figures to remove non-recurring restructuring or impairment
noise." Owner go-ahead the same day for the extra FMP call per name.

Two halves, deliberately separable. The refresh MEASURES pe_ntm and
ev_ebitda_ntm for every basket. The forward legs CONSUME them only behind
NTM_FORWARD_MULTIPLES_ENABLED, which is off by default: a forward multiple is
lower than a trailing one for any growing basket, so switching it on re-prices
every Forward P/E leg in the book downward, and that ships once it is measured.
"""
from datetime import date

import pytest

from src.agents.analysis import dcf_agent
from src.data import regional_comps as rc

TODAY = date(2026, 9, 21)                      # 101 days to a 31 December year end
W = 101 / 365.0


def _rows(**over):
    """FMP annual analyst-estimate rows, FSLR as measured live on 2026-09-21."""
    rows = [
        {"date": "2025-12-31", "epsAvg": 14.0, "ebitdaAvg": 2.1e9, "netIncomeAvg": 1.5e9, "numAnalystsEps": 24},
        {"date": "2026-12-31", "epsAvg": 17.43877, "ebitdaAvg": 3357424399, "netIncomeAvg": 1894863540,
         "numAnalystsEps": 24},
        {"date": "2027-12-31", "epsAvg": 23.42567, "ebitdaAvg": 4405041557, "netIncomeAvg": 2508860560,
         "numAnalystsEps": 24},
        {"date": "2028-12-31", "epsAvg": 29.48914, "ebitdaAvg": 5396971112, "netIncomeAvg": 3082141600,
         "numAnalystsEps": 25},
    ]
    for r in rows:
        r.update(over.get(r["date"], {}))
    return rows


# ── the blend ────────────────────────────────────────────────────────────────

def test_ntm_is_fy1_and_fy2_weighted_by_the_days_left_in_fy1():
    got = rc.ntm_blend(_rows(), "epsAvg", TODAY)
    assert got == pytest.approx(W * 17.43877 + (1 - W) * 23.42567)


def test_a_fiscal_year_that_has_ended_is_history_even_while_fmp_still_serves_its_row():
    """D05.SI carried its FY2025 row in September 2026. Were it taken as FY1 the
    'forward' figure would be last year's."""
    assert rc.ntm_blend(_rows(), "epsAvg", TODAY) > 17.43877
    # On the year-end date itself that year is over: FY1 is the next one.
    assert rc.ntm_blend(_rows(), "epsAvg", date(2026, 12, 31)) == pytest.approx(23.42567)
    assert rc.ntm_blend(_rows(), "epsAvg", date(2027, 1, 1)) == pytest.approx(
        (364 / 365) * 23.42567 + (1 / 365) * 29.48914)


@pytest.mark.parametrize("over", [
    {"2026-12-31": {"epsAvg": -1.2}},          # a forward loss: not a cheap stock, not a comp
    {"2026-12-31": {"epsAvg": None}},
    {"2027-12-31": {"epsAvg": 0.0}},           # FY2 needed (FY1 covers 28% of the window) and unusable
    {"2026-12-31": {"numAnalystsEps": 1}},     # one broker's model is not a consensus
])
def test_a_line_that_is_missing_non_positive_or_thinly_covered_leaves_the_median(over):
    assert rc.ntm_blend(_rows(**over), "epsAvg", TODAY, analysts_key="numAnalystsEps") is None


def test_fy1_alone_stands_in_only_when_it_covers_most_of_the_window():
    only = [r for r in _rows() if r["date"] == "2026-12-31"]
    assert rc.ntm_blend(only, "epsAvg", TODAY) is None                                   # 28% of the window
    assert rc.ntm_blend(only, "epsAvg", date(2026, 1, 15)) == pytest.approx(17.43877)    # 96%
    assert rc.ntm_blend([], "epsAvg", TODAY) is None
    assert rc.ntm_blend([{"date": "not a date", "epsAvg": 5}], "epsAvg", TODAY) is None


# ── the multiples, and the currency they are stated in ───────────────────────

def test_the_price_is_rebuilt_in_the_reporting_currency_so_no_fx_step_exists():
    """Tencent, measured 2026-09-21: quote HKD 430, but FMP's TTM ratios are in
    CNY -- P/E 14.166 x EPS 26.010 = CNY 368.5 -- and so are the estimates.
    Dividing the HKD quote by CNY EPS would overstate the multiple by HKD/CNY."""
    rt = {"priceToEarningsRatioTTM": 14.16588, "netIncomePerShareTTM": 26.0105}
    est = [{"date": "2026-12-31", "epsAvg": 28.90181, "ebitdaAvg": 376668135187, "numAnalystsEps": 32},
           {"date": "2027-12-31", "epsAvg": 30.68585, "ebitdaAvg": 408324572331, "numAnalystsEps": 33}]
    km = {"marketCap": 3304913959533.0, "enterpriseValueTTM": 3562035866533.0}
    got = rc.ntm_multiples(km, rt, est, TODAY)
    eps_ntm = W * 28.90181 + (1 - W) * 30.68585
    assert got["pe_ntm"] == pytest.approx(14.16588 * 26.0105 / eps_ntm)
    assert got["pe_ntm"] < 430 / eps_ntm                       # what the HKD quote would have said
    assert got["ev_ebitda_ntm"] == pytest.approx(
        3562035866533.0 / (W * 376668135187 + (1 - W) * 408324572331))


def test_a_loss_making_trailing_year_reaches_the_same_ratio_from_market_cap():
    rt = {"priceToEarningsRatioTTM": None, "netIncomePerShareTTM": -2.0}
    km = {"marketCap": 2.0e10, "enterpriseValueTTM": 2.2e10}
    got = rc.ntm_multiples(km, rt, _rows(), TODAY)
    assert got["pe_ntm"] == pytest.approx(2.0e10 / (W * 1894863540 + (1 - W) * 2508860560))


def test_nothing_is_invented_when_the_inputs_are_absent():
    assert rc.ntm_multiples({}, {}, _rows(), TODAY) == {}
    assert rc.ntm_multiples({"enterpriseValueTTM": -5e9}, {}, _rows(), TODAY) == {}
    assert rc.ntm_multiples({"enterpriseValueTTM": 2e10}, {}, [], TODAY) == {}


def test_the_new_fields_are_banded_like_their_trailing_counterparts_and_reach_every_reader():
    assert rc._BANDS["pe_ntm"] == rc._BANDS["pe"]
    assert rc._BANDS["ev_ebitda_ntm"] == rc._BANDS["ev_ebitda"]
    assert {"pe_ntm", "ev_ebitda_ntm"} <= set(rc.FIELDS)       # medians, members, history, get_regional_multiples
    assert rc._clean("pe_ntm", [9.0, 250.0, -4.0, None, float("nan")]) == [9.0]


# ── the refresh call, and its kill switch ────────────────────────────────────

def _stub_fmp(calls):
    def fake(url, params, api_key=None):
        ep = url.rsplit("/", 1)[-1]
        calls.append(ep)
        return {
            "key-metrics-ttm": [{"evToEBITDATTM": 8.06, "enterpriseValueTTM": 1.966e10, "marketCap": 2.115e10}],
            "ratios-ttm": [{"priceToEarningsRatioTTM": 12.10578, "netIncomePerShareTTM": 16.24773}],
            "financial-growth": [{"revenueGrowth": 0.2}],
            "analyst-estimates": _rows(),
        }[ep]
    return fake


def test_the_refresh_makes_one_extra_call_per_name_and_forms_both_multiples(monkeypatch):
    calls: list = []
    monkeypatch.delenv("COMPS_NTM_DISABLED", raising=False)
    monkeypatch.setattr(rc, "_fmp_get", _stub_fmp(calls))
    out = rc.fetch_name_multiples("FSLR")
    assert calls == ["key-metrics-ttm", "ratios-ttm", "financial-growth", "analyst-estimates"]
    assert out["pe"] == pytest.approx(12.10578) and out["pe_ntm"] < out["pe"]
    assert out["ev_ebitda_ntm"] < out["ev_ebitda"]


def test_the_kill_switch_drops_the_call_and_the_fields(monkeypatch):
    calls: list = []
    monkeypatch.setenv("COMPS_NTM_DISABLED", "true")
    monkeypatch.setattr(rc, "_fmp_get", _stub_fmp(calls))
    out = rc.fetch_name_multiples("FSLR")
    assert "analyst-estimates" not in calls and "pe_ntm" not in out and out["pe"] == pytest.approx(12.10578)


# ── the forward legs ─────────────────────────────────────────────────────────

PEER = {"pe": 20.0, "pe_ntm": 15.0, "ev_ebitda": 12.0, "ev_ebitda_ntm": 9.0}
KW = dict(most_recent={"net_income": 1e9}, revenue_base=1e10, shares=1e9, net_debt=0.0, market_cap=2e10,
          wacc=0.08, growth_base=0.05, fcf_margin_base=0.1, tgr=0.02, fcf_floor=0.0, sector="Tech",
          scenario="base", forward_consensus={"eps": {"base": 2.0}, "ebitda": {"base": 3e9}})


def _leg(name, monkeypatch, peer=PEER):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: dict(peer))
    return dcf_agent._traced_method_value(method_name=name, **KW)


def test_with_the_flag_off_the_forward_legs_are_exactly_what_they_were(monkeypatch):
    monkeypatch.delenv(dcf_agent.NTM_FORWARD_FLAG, raising=False)
    v, tr = _leg("Forward P/E", monkeypatch)
    assert v == pytest.approx(2.0 * 20.0) and tr["multiple_parts"]["peer_source"] == "peer median pe"
    v, tr = _leg("Forward EV/EBITDA", monkeypatch)
    assert v == pytest.approx(3e9 * 12.0 / 1e9) and tr["multiple_parts"]["peer_source"] == "peer median ev_ebitda"


def test_with_the_flag_on_a_forward_metric_meets_a_forward_multiple_and_the_trace_says_so(monkeypatch):
    monkeypatch.setenv(dcf_agent.NTM_FORWARD_FLAG, "true")
    v, tr = _leg("Forward P/E", monkeypatch)
    assert v == pytest.approx(2.0 * 15.0)
    assert tr["multiple_parts"]["peer_multiple"] == 15.0
    assert tr["multiple_parts"]["peer_source"] == "peer median pe_ntm (forward basis)"
    v, tr = _leg("Forward EV/EBITDA", monkeypatch)
    assert v == pytest.approx(3e9 * 9.0 / 1e9)
    assert tr["multiple_parts"]["peer_source"] == "peer median ev_ebitda_ntm (forward basis)"


def test_a_basket_with_no_forward_median_keeps_the_trailing_one_even_with_the_flag_on(monkeypatch):
    monkeypatch.setenv(dcf_agent.NTM_FORWARD_FLAG, "true")
    v, tr = _leg("Forward P/E", monkeypatch, peer={"pe": 20.0})
    assert v == pytest.approx(40.0) and tr["multiple_parts"]["peer_source"] == "peer median pe"


def test_the_trailing_legs_never_read_the_forward_multiple(monkeypatch):
    """The mismatch runs both ways: a forward multiple on trailing earnings
    understates. P/E and EV/EBITDA stay on the trailing medians, flag or no flag."""
    monkeypatch.setenv(dcf_agent.NTM_FORWARD_FLAG, "true")
    v, tr = _leg("P/E", monkeypatch)
    assert tr["multiple_parts"]["peer_multiple"] == 20.0
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: dict(PEER))
    kw = {**KW, "most_recent": {"ebitda": 2e9}}
    v, tr = dcf_agent._traced_method_value(method_name="EV/EBITDA", **kw)
    assert tr["multiple_parts"]["peer_multiple"] == 12.0

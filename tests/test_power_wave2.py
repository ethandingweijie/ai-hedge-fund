"""Wave 2 (power & transition): the engine work that lands ahead of the
owner-confirmed method tables.

P/Rate Base used to sit in the P/BV set and price book value x peer P/B under
a rate-base label (Stage 0, 2026-09-21: NEE, DUK and SO each carried half their
surviving weight on it). It is now the regulator's own arithmetic, and it is
inert for every ticker until two things exist: an owner-ACCEPTED rate base with
its allowed ROE, and an owner-SET cost of equity for the profile. Until then
the profile's declared P/BV proxy prices the weight, exactly as before.
"""
import pytest

from src.agents.analysis import dcf_agent
from src.data import valuation_constants as vc

PROFILE = "Regulated Utility"


def _value(row, monkeypatch, coe=0.085, scenario="base", tgr=0.02, method="P/Rate Base"):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pb": 2.0})
    monkeypatch.setattr(vc, "cost_of_equity", lambda profile, market="US", doc=None: coe)
    return dcf_agent._compute_method_value(
        method_name=method, most_recent=row, revenue_base=2.5e10, shares=2e9, net_debt=9e10,
        market_cap=1.5e11, wacc=0.045, growth_base=0.04, fcf_margin_base=0.05, tgr=tgr,
        fcf_floor=0.0, sector="Energy", scenario=scenario, profile_name=PROFILE)


def _row(**over):
    row = {"book_value_per_share": 25.0, "total_equity": 5e10, "total_debt": 9e10,
           "rate_base_accepted": 7.1e10,
           "_rate_base_detail": {"value": 7.1e10, "allowed_roe": 0.108, "equity_ratio": 0.596}}
    row.update(over)
    return row


J = (0.108 - 0.02) / (0.085 - 0.02)          # the justified multiple used throughout


def test_the_regulated_layer_takes_the_justified_multiple_and_the_rest_of_book_the_peer_pb(monkeypatch):
    """Book equity 50bn; equity rate base 71bn x 59.6% = 42.3bn; the 7.7bn the
    regulator sets no return on keeps the proxy's basis, book x peer P/B (2.0)."""
    v = _value(_row(), monkeypatch)
    eq_rb = 7.1e10 * 0.596
    assert v == pytest.approx((eq_rb * J + (5e10 - eq_rb) * 2.0) / 2e9)


def test_a_pure_utility_is_the_planned_formula_exactly(monkeypatch):
    """Equity rate base >= book equity: nothing is left over, and the leg is
    equity rate base / shares x (allowed ROE - g) / (CoE - g)."""
    row = _row(total_equity=4e10)
    assert _value(row, monkeypatch) == pytest.approx(7.1e10 * 0.596 / 2e9 * J)


def test_the_trace_is_one_multiple_on_book_so_the_workbook_rebuilds_it(monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pb": 2.0})
    monkeypatch.setattr(vc, "cost_of_equity", lambda profile, market="US", doc=None: 0.085)
    v, tr = dcf_agent._traced_method_value(
        method_name="P/Rate Base", most_recent=_row(), revenue_base=2.5e10, shares=2e9, net_debt=9e10,
        market_cap=1.5e11, wacc=0.045, growth_base=0.04, fcf_margin_base=0.05, tgr=0.02,
        fcf_floor=0.0, sector="Energy", scenario="bull", profile_name=PROFILE)
    parts = tr["multiple_parts"]
    numeric = {k: x for k, x in parts.items() if isinstance(x, (int, float))}
    assert set(numeric) == {"peer_multiple", "scenario_band"}      # nothing else may multiply
    assert tr["per_share_metric"] * parts["peer_multiple"] * parts["scenario_band"] == pytest.approx(v)
    assert tr["rate_base_inputs"]["justified_multiple"] == pytest.approx(J)


def test_net_debt_is_not_subtracted(monkeypatch):
    """The equity layer is already the equity-funded slice of the rate base."""
    a = _value(_row(), monkeypatch)
    monkeypatch.undo()
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pb": 2.0})
    monkeypatch.setattr(vc, "cost_of_equity", lambda profile, market="US", doc=None: 0.085)
    b = dcf_agent._compute_method_value(
        method_name="P/Rate Base", most_recent=_row(), revenue_base=2.5e10, shares=2e9, net_debt=0.0,
        market_cap=1.5e11, wacc=0.045, growth_base=0.04, fcf_margin_base=0.05, tgr=0.02,
        fcf_floor=0.0, sector="Energy", scenario="base", profile_name=PROFILE)
    assert a == b


@pytest.mark.parametrize("row,coe", [
    (_row(rate_base_accepted=None, _rate_base_detail={}), 0.085),          # nothing accepted
    (_row(_rate_base_detail={"value": 7.1e10, "allowed_roe": None, "equity_ratio": 0.5}), 0.085),  # SoC: no ROE
    (_row(), None),                                                        # no cost of equity authored
])
def test_a_missing_input_declines_so_the_declared_proxy_prices_the_weight(row, coe, monkeypatch):
    assert _value(row, monkeypatch, coe=coe) is None


def test_it_is_no_longer_book_value_under_another_name(monkeypatch):
    """The pre-Wave-2 behaviour: bvps 25 x peer pb 2.0 = 50, identical to P/BV."""
    assert _value(_row(), monkeypatch, method="P/BV") == pytest.approx(50.0)
    assert _value(_row(), monkeypatch) != pytest.approx(50.0)
    assert _value(_row(rate_base_accepted=None, _rate_base_detail={}), monkeypatch) is None


def test_no_authorised_equity_ratio_falls_to_the_book_capital_structure(monkeypatch):
    row = _row(_rate_base_detail={"value": 7.1e10, "allowed_roe": 0.108, "equity_ratio": None})
    v = _value(row, monkeypatch)
    eq_rb = 7.1e10 * (5e10 / (5e10 + 9e10))
    assert v == pytest.approx((eq_rb * J + (5e10 - eq_rb) * 2.0) / 2e9)


def test_an_allowed_roe_at_or_below_growth_declines(monkeypatch):
    row = _row(_rate_base_detail={"value": 7.1e10, "allowed_roe": 0.02, "equity_ratio": 0.5})
    assert _value(row, monkeypatch) is None


def test_the_scenarios_stay_ordered(monkeypatch):
    bear, base, bull = (_value(_row(), monkeypatch, scenario=s) for s in ("bear", "base", "bull"))
    assert bear <= base <= bull


def test_the_real_method_is_requested_beside_its_proxy():
    assert "P/Rate Base" in dcf_agent._PER_TICKER_METHODS
    assert dcf_agent._LOOKTHROUGH_METHODS <= dcf_agent._PER_TICKER_METHODS


def test_the_cost_of_equity_is_owner_set_per_market_at_the_midpoint_of_its_band():
    """Owner, 2026-09-21: USD models 7.0-7.5%; RMB/HKD Asian regulated power
    5.8-6.2%. The rate follows the currency of the cash flows."""
    doc = vc.load()["cost_of_equity"]["profiles"][PROFILE]
    for market, band in (("US", (0.070, 0.075)), ("HKSE", (0.058, 0.062))):
        v = vc.cost_of_equity(PROFILE, market)
        assert tuple(doc[market]["band"]) == band and v == pytest.approx(sum(band) / 2)
    assert vc.cost_of_equity(PROFILE, "SES") is None          # none authored: the leg declines
    assert vc.cost_of_equity("Merchant Power", "US") is None
    assert [vc.market_key(t) for t in ("NEE", "00002.HK", "U96.SI", None)] == ["US", "HKSE", "SES", "US"]


def test_the_profile_still_declares_the_proxy_so_no_weight_is_lost():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    leg = next(m for m in P["Energy"][PROFILE]["methods"] if m["name"] == "P/Rate Base")
    assert leg["implementable"] is False and leg["proxy"] == "P/BV"


# ── C3: a business model is never inferred from a ratio ──────────────────────

def test_an_unmapped_industrial_is_capital_goods_whatever_its_growth_or_leverage():
    """The old ladder sent D/E > 1.5 to Automotive (OEM) and CAGR >= 8% to
    Aerospace & Defense. NuScale (SMR) was valued as a defence contractor in the
    Wave 2 Stage 0 baseline on exactly that rule."""
    from itertools import product
    from src.data.sector_profiles import classify_valuation_profile
    for cagr, fcf, de, pre in product((-0.10, 0.03, 0.08, 0.45), (-0.20, 0.04, 0.25),
                                      (0.0, 1.4, 1.6, 9.0), (False, True)):
        assert classify_valuation_profile("Industrials", cagr, fcf, de, is_pre_revenue=pre,
                                          revenue_base=5e9) == "Capital Goods"


def test_car_makers_and_defence_names_are_still_reached_by_what_they_are():
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as pins
    assert {pins[t][1] for t in ("GM", "F", "TM")} == {"Automotive (OEM)"}
    assert {pins[t][1] for t in ("LMT", "RTX", "BA", "GE")} == {"Aerospace & Defense"}


# -- the owner-confirmed tables, routing and pins (2026-09-21) ----------------

OEM = "Clean Tech / Power Equipment OEM"

#: (method, weight, anchor) per profile, as confirmed.
TABLE = {
    "Regulated Utility": [("P/Rate Base", 0.35, False), ("P/E", 0.30, True), ("DDM", 0.25, False), ("DCF", 0.10, False)],
    "Merchant Power": [("EV/EBITDA", 0.45, True), ("FCF Yield", 0.25, False), ("Forward P/E", 0.15, False),
                       ("Power Price DCF", 0.15, False)],
    "IPP": [("PPA-backed DCF", 0.40, True), ("EV/EBITDA", 0.35, False), ("P/BV", 0.15, False), ("DDM", 0.10, False)],
    OEM: [("EV/EBITDA (norm)", 0.35, True), ("Forward P/E", 0.25, False), ("DCF", 0.25, False),
          ("EV/Revenue", 0.15, False)],
}

#: FMP industry label -> profile, measured live 2026-09-21
#: (docs/waves_2_5_stage2_probe.md): NEE DUK SO 00002.HK | 00006.HK VST CEG NRG
#: 01816.HK | ENPH FSLR NXT.
WAVE_ROWS = {"Regulated Electric": ("Energy", "Regulated Utility"),
             "Independent Power Producers": ("Energy", "IPP"),
             "Solar": ("Energy", OEM)}


@pytest.mark.parametrize("profile", sorted(TABLE))
def test_each_profile_is_the_confirmed_table_leg_by_leg(profile):
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    got = [(m["name"], m["weight"], bool(m.get("anchor"))) for m in P["Energy"][profile]["methods"]]
    assert got == TABLE[profile]
    assert sum(w for _, w, _ in got) == pytest.approx(1.0)


def test_no_wave_two_anchor_resolves_to_a_proxy_or_routing_would_decline_the_profile():
    """The flag on P/Rate Base sent CLP back to Mature SaaS: industry routing
    refuses a profile whose anchor is not implementable, and it is right to."""
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    for profile in TABLE:
        anchors = [m for m in P["Energy"][profile]["methods"] if m.get("anchor")]
        assert len(anchors) == 1 and anchors[0]["implementable"] is True, profile


def test_the_measured_labels_route_and_are_in_scope_and_the_shared_labels_are_not():
    from src.data import industry_profile_map as m
    for label, target in WAVE_ROWS.items():
        assert m.profile_for_industry(label) == target and label in m.routing_scope()
    # Bloom, NuScale and GE Vernova share these with hundreds of unrelated industrials.
    assert not {"Electrical Equipment & Parts", "Industrial - Machinery"} & m.routing_scope()
    assert m.profile_for_industry("Uranium") is None       # no profile yet: P/NAV needs inputs


def test_the_pins_agree_with_the_owner_decisions():
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as pins
    want = {"ENPH": OEM, "FSLR": OEM, "BE": OEM, "VST": "Merchant Power", "CEG": "Merchant Power",
            "NRG": "Merchant Power", "00006.HK": "Regulated Utility", "01816.HK": "Regulated Utility",
            "GEV": "Capital Goods", "SMR": "Energy Tech Licensor"}
    assert {t: pins[t][1] for t in want} == want


def test_bloom_is_never_a_licensor_and_the_oem_profile_is_cyclical_at_its_own_rate():
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as pins, _ENERGY_PROFILE_WACC
    assert pins["BE"][1] != "Energy Tech Licensor"
    assert OEM in dcf_agent._CYCLICAL_PROFILES
    assert _ENERGY_PROFILE_WACC[OEM] == pytest.approx(0.090)   # Damodaran Jan 2026 Electrical Equipment 8.99%


def test_utility_pe_no_longer_exists_as_an_ev_multiple_under_a_pe_label():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    assert "Utility P/E" not in dcf_agent._EV_MULTIPLE_METHODS
    assert not [m for prof in P["Energy"].values() for m in prof["methods"] if m["name"] == "Utility P/E"]


def test_a_ticker_pinned_against_its_label_takes_the_basket_of_the_business_it_is():
    from src.data.industry_profile_map import comps_industry_for
    assert comps_industry_for("01816.HK") == comps_industry_for("00006.HK") == "Regulated Electric"
    assert comps_industry_for("00002.HK") is None and comps_industry_for("NEE") is None


def test_cgn_power_takes_the_owner_discount_as_its_own_part_and_nobody_else_does(monkeypatch):
    assert vc.ticker_multiple_discount("01816.HK") == pytest.approx(0.875)
    lo, hi = vc.load()["ticker_multiple_discounts"]["tickers"]["01816.HK"]["band"]
    assert (lo, hi) == (0.85, 0.90) and vc.ticker_multiple_discount("00002.HK") == 1.0
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pe": 13.0, "pb": 0.9})
    kw = dict(most_recent={"net_income": 1e10, "book_value_per_share": 2.5}, revenue_base=8e10, shares=5e10,
              net_debt=3e11, market_cap=1.5e11, wacc=0.06, growth_base=0.04, fcf_margin_base=0.1, tgr=0.02,
              fcf_floor=0.0, sector="Energy", scenario="base", profile_name=PROFILE, is_hk=True)
    v, tr = dcf_agent._traced_method_value(method_name="P/E", ticker="01816.HK", **kw)
    assert v == pytest.approx(0.2 * 13.0 * 0.875)
    assert tr["multiple_parts"]["peer_multiple"] == 13.0                  # the basket median is untouched
    assert tr["multiple_parts"]["owner_multiple_discount"] == 0.875
    v2, tr2 = dcf_agent._traced_method_value(method_name="P/E", ticker="00002.HK", **kw)
    assert v2 == pytest.approx(0.2 * 13.0) and "owner_multiple_discount" not in tr2["multiple_parts"]
    pb, _ = dcf_agent._traced_method_value(method_name="P/BV", ticker="01816.HK", **kw)
    assert pb == pytest.approx(2.5 * 0.9 * 0.875)


def test_every_quoted_peak_line_is_guarded_against_a_name_with_no_profitable_year():
    """`_peak['max_line']` is None when no year had positive EPS. e07002b guarded
    the diagnostic; the forward flag formatted it regardless and crashed the whole
    run for Bloom Energy the moment Wave 2 put it on a cyclical profile."""
    import inspect
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    quoted = src.count("_peak['max_line']:.2f")
    guarded = src.count('_peak["max_line"] is not None')
    assert quoted >= 2 and guarded == quoted, (quoted, guarded)


# -- the AI-power trade: a recorded regime deviation, never a number ----------

AI_POWER = ["VST", "CEG", "NRG", "BE", "GEV", "CCJ", "LEU", "SMR"]


def test_the_ai_power_names_carry_the_owner_recorded_regime_and_nobody_else_does():
    for t in AI_POWER:
        r = vc.regime_deviation(t)
        assert r and r["key"] == "ai_power" and "through-cycle by design" in r["note"], t
    for t in ("NEE", "DUK", "SO", "ENPH", "FSLR", "NXT", "00002.HK", "01816.HK", None, ""):
        assert vc.regime_deviation(t) is None, t


def test_a_regime_is_a_note_and_changes_no_constant_any_leg_reads():
    """It must never become a lever: no multiple, discount or rate may hang off it."""
    e = vc.load()["regime_deviations"]["regimes"]["ai_power"]
    assert set(e) == {"label", "tickers", "recorded", "note", "evidence", "review"}
    assert all(vc.ticker_multiple_discount(t) == 1.0 for t in AI_POWER)


def test_the_note_is_appended_before_the_scenario_loop_so_it_reaches_every_scenario():
    import inspect
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    note_at = src.index("_vc_reg.regime_deviation(ticker)")
    snapshot_at = src.index("forward_flags = list(ticker_forward_flags)")
    assert note_at < snapshot_at

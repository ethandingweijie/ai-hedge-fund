"""Review-gated industry inputs (PV-10, backlog, maintenance capex).

Owner decision 2026-09-20: figures FMP does not carry are pre-filled by a cited
Gemini call, checked against FMP, and used in a valuation only once the owner
accepts exactly those figures.
"""
import pytest

from src.data import industry_inputs as ii


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(ii, "_reviews_ready_key", None)
    yield
    _db.close_all_connections()


def _doc(value=1009.0, scale="mn", ccy="USD"):
    return {"version": 1, "tickers": {"KMI": {"maintenance_capex": {
        "data": {"value": {"value": value, "currency": ccy, "scale": scale, "period": "FY2024",
                           "source_url": "https://ir.kindermorgan.com/x", "quote": "sustaining capex of $1,009 million"},
                 "definition": "sustaining capital expenditures"},
        "checks": [], "ok": True, "company": "Kinder Morgan"}}}}


USD = lambda ccy: 1.0 if ccy == "USD" else None  # noqa: E731


def test_a_pending_figure_is_never_used(store):
    assert ii.accepted_amount("KMI", "maintenance_capex", "USD", doc=_doc(), fx=USD) is None


def test_an_accepted_figure_is_used_in_full_units(store):
    doc = _doc()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    assert ii.accepted_amount("KMI", "maintenance_capex", "USD", doc=doc, fx=USD) == pytest.approx(1.009e9)


def test_changed_figures_need_a_new_acceptance(store):
    doc = _doc()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    rebuilt = _doc(value=1100.0)
    e = ii.entry("KMI", "maintenance_capex", rebuilt)
    assert ii.review_for("KMI", "maintenance_capex", e)["status"] == "changed_since_acceptance"
    assert ii.accepted_amount("KMI", "maintenance_capex", "USD", doc=rebuilt, fx=USD) is None


def test_revoked_is_not_used(store):
    doc = _doc()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    ii.set_review("KMI", "maintenance_capex", "revoked", "owner", doc=doc)
    assert ii.accepted_amount("KMI", "maintenance_capex", "USD", doc=doc, fx=USD) is None


def test_no_entry_cannot_be_reviewed(store):
    with pytest.raises(KeyError):
        ii.set_review("XOM", "pv10", "accepted", "owner", doc=_doc())


def test_a_wrong_scale_fails_its_check():
    """bn read as mn lands 1000x out: 1.009bn maintenance capex against 2.5bn D&A
    is 0.4x; read as thousands it is 0.0004x and fails."""
    ok = ii.reconcile("maintenance_capex", 1.009e9, {"depreciation_and_amortization": 2.5e9,
                                                    "operating_cash_flow": 6.3e9})
    assert all(c["ok"] for c in ok)
    bad = ii.reconcile("maintenance_capex", 1.009e6, {"depreciation_and_amortization": 2.5e9})
    assert any(c["ok"] is False for c in bad)


def test_an_uncited_or_missing_amount_fails():
    assert ii.reconcile("pv10", None, {"market_cap": 1e11})[0]["ok"] is False
    assert any(c["ok"] is False for c in ii.reconcile("backlog", 0.0, {"revenue": 1e10}))


def test_ui_rows_carry_the_citation_and_status(store):
    rows = ii.ui_summary(doc=_doc())["rows"]
    assert rows[0]["ticker"] == "KMI" and rows[0]["status"] == "pending"
    assert rows[0]["source_url"].startswith("http") and "1,009" in rows[0]["quote"]


def test_the_midstream_leg_uses_the_accepted_figure(monkeypatch):
    """The engine hook sets `maintenance_capex_accepted`; the Distributable CF
    Yield leg then subtracts it instead of D&A."""
    from src.agents.analysis import dcf_agent
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"fcf_yield": 0.06})
    row = {"revenue": 1e10, "operating_cash_flow": 3e9, "depreciation_and_amortization": 1e9,
           "maintenance_capex_accepted": 0.5e9, "net_debt": 1e9, "shares_outstanding": 1e8}
    v = dcf_agent._compute_method_value(
        method_name="Distributable CF Yield", most_recent=row, revenue_base=1e10, shares=1e8,
        net_debt=1e9, market_cap=1e10, wacc=0.07, growth_base=0.02, fcf_margin_base=0.1, tgr=0.02,
        fcf_floor=0.0, sector="Energy", scenario="base", profile_name="Midstream / Pipelines")
    from src.data import valuation_constants as vc
    assert v == pytest.approx(((3e9 - 0.5e9) / 1e8)
                              / vc.target_dcf_yield("Midstream / Pipelines"))


# ── forward overlay: actuals are the baseline, guidance is a delta ──────────
# Owner decision 2026-09-20, after Williams' pre-fill answered with FY2026
# guidance and then FY2020 actuals.

def _doc_with_overlay(delta_pct=0.10):
    d = _doc()
    d["tickers"]["KMI"]["maintenance_capex"]["basis"] = "actual"
    d["tickers"]["KMI"]["maintenance_capex"]["overlay"] = {
        "delta_pct": delta_pct, "guidance_value": 1110.0, "currency": "USD", "scale": "mn",
        "period": "FY2026E", "source_url": "https://ir.kindermorgan.com/guidance",
        "quote": "2026 sustaining capital of $1,110 million", "note": "guidance FY2026E vs actual FY2024"}
    return d


def test_the_overlay_is_off_by_default(store, monkeypatch):
    monkeypatch.delenv(ii.OVERLAY_FLAG, raising=False)
    doc = _doc_with_overlay()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc, overlay=True)
    d = ii.accepted_detail("KMI", "maintenance_capex", "USD", doc=doc, fx=USD)
    assert d["value"] == pytest.approx(1.009e9) and d["overlay_applied"] is False


def test_the_toggle_applies_an_accepted_overlay_with_its_audit_trail(store, monkeypatch):
    monkeypatch.setenv(ii.OVERLAY_FLAG, "1")
    doc = _doc_with_overlay()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc, overlay=True)
    d = ii.accepted_detail("KMI", "maintenance_capex", "USD", doc=doc, fx=USD)
    assert d["baseline"] == pytest.approx(1.009e9)
    assert d["value"] == pytest.approx(1.009e9 * 1.10)
    assert d["overlay_applied"] and d["delta_pct"] == pytest.approx(0.10)


def test_an_unaccepted_overlay_never_applies(store, monkeypatch):
    monkeypatch.setenv(ii.OVERLAY_FLAG, "1")
    doc = _doc_with_overlay()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    d = ii.accepted_detail("KMI", "maintenance_capex", "USD", doc=doc, fx=USD)
    assert d["value"] == pytest.approx(1.009e9) and not d["overlay_applied"]


def test_the_baseline_and_the_overlay_are_reviewed_separately(store):
    doc = _doc_with_overlay()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    e = ii.entry("KMI", "maintenance_capex", doc)
    assert ii.review_for("KMI", "maintenance_capex", e)["status"] == "accepted"
    assert ii.review_for("KMI", "maintenance_capex", e, overlay=True)["status"] == "pending"


def test_a_changed_overlay_needs_a_new_acceptance(store, monkeypatch):
    monkeypatch.setenv(ii.OVERLAY_FLAG, "1")
    doc = _doc_with_overlay()
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc)
    ii.set_review("KMI", "maintenance_capex", "accepted", "owner", doc=doc, overlay=True)
    moved = _doc_with_overlay(delta_pct=0.25)
    d = ii.accepted_detail("KMI", "maintenance_capex", "USD", doc=moved, fx=USD)
    assert not d["overlay_applied"], "a re-stated overlay is not the one that was accepted"


def test_the_standardized_measure_never_takes_an_overlay(store):
    """It is defined by proved reserves at trailing SEC prices; a price-deck
    delta on it would report a rigid measure as a forward one."""
    assert "pv10" in ii.NO_OVERLAY
    doc = {"version": 1, "tickers": {"COP": {"pv10": {
        "data": {"value": {"value": 55962.0, "currency": "USD", "scale": "mn", "period": "FY2025",
                           "source_url": "https://x", "quote": "standardized measure"}},
        "overlay": {"delta_pct": 0.3}}}}}
    with pytest.raises(ValueError):
        ii.set_review("COP", "pv10", "accepted", "owner", doc=doc, overlay=True)


# ── the two approved uses (owner, 2026-09-20) ──────────────────────────────

def test_the_reserve_value_is_a_cross_check_and_a_bear_floor_not_a_leg():
    """Blending it would cut every E&P value: COP, DVN and OXY all sit 75-79%
    below price on the standardized measure."""
    from src.agents.analysis import dcf_agent as d
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    for prof in d._RESERVE_FLOOR_PROFILES:
        names = {m["name"] for m in P["Resources"][prof]["methods"]}
        assert "Reserve NPV (PV-10)" not in names, prof
    scen = {"bear": {"intrinsic_value": 20.0, "method_iv_table": {"EV/OCF": 25.0},
                     "effective_weights": [{"method": "EV/OCF", "value_key": "EV/OCF"}],
                     "forward_flags": []},
            "base": {"intrinsic_value": 40.0, "method_iv_table": {"EV/OCF": 40.0},
                     "effective_weights": [{"method": "EV/OCF", "value_key": "EV/OCF"}]}}
    rec = d._apply_reserve_floor(scen, 32.02, {"period": "FY2025", "source_url": "https://x"})
    # Published on every scenario, weighted on none.
    assert scen["base"]["method_iv_table"]["Reserve NPV (PV-10)"] == 32.02
    assert "Reserve NPV (PV-10)" in scen["base"]["cross_check_methods"]
    # The bear case is lifted to the reserve value, and says so.
    assert scen["bear"]["intrinsic_value"] == 32.02 and rec["before"] == 20.0
    assert "floored at the reserve value" in scen["bear"]["forward_flags"][0]
    # A bear above the floor is left alone.
    scen2 = {"bear": {"intrinsic_value": 50.0, "method_iv_table": {}, "forward_flags": []}}
    assert d._apply_reserve_floor(scen2, 32.02) is None
    assert scen2["bear"]["intrinsic_value"] == 50.0


def test_backlog_bounds_only_the_bear_decline():
    from src.agents.analysis import dcf_agent as d
    assert d._BACKLOG_VISIBILITY_PROFILES == frozenset({"Oilfield Services & Drilling"})
    src = __import__("inspect").getsource(d.run_dcf_agent)
    assert 'if scenario == "bear" and _backlog_cov is not None and g < 0:' in src
    assert "-(1.0 - min(_backlog_cov, 1.0))" in src


# ── rate base (Wave 2: regulated utilities) ──────────────────────────────────

def _rate_base_doc(roe=0.108, equity=0.596, amount=71.0):
    data = {"value": {"value": amount, "currency": "USD", "scale": "bn", "period": "FY2025",
                      "source_url": "https://investor.nexteraenergy.com/x",
                      "quote": "regulatory capital employed of approximately $71 billion"},
            "jurisdiction": "Florida PSC", "basis": "year-end regulatory capital employed"}
    if roe is not None:
        data["allowed_roe"] = {"value": roe, "period": "2025", "source_url": "https://x", "quote": "10.8%"}
    if equity is not None:
        data["equity_ratio"] = {"value": equity, "period": "2025", "source_url": "https://x", "quote": "59.6%"}
    return {"version": 1, "tickers": {"NEE": {"rate_base": {"data": data, "checks": [], "ok": True}}}}


def test_rate_base_is_a_kind_the_generic_accept_route_already_serves():
    from src.agents.industry import gemini_params as gp
    assert "rate_base" in ii.KINDS and "rate_base" in ii.BOUNDS
    assert set(gp.INDUSTRY_INPUT_SCHEMAS) == set(ii.KINDS) == set(gp._INDUSTRY_ASK) == set(gp._OVERLAY_ASK)


def test_a_pending_rate_base_is_never_used_and_an_accepted_one_carries_its_rate_order(store):
    doc = _rate_base_doc()
    assert ii.accepted_detail("NEE", "rate_base", "USD", doc=doc, fx=USD) is None
    ii.set_review("NEE", "rate_base", "accepted", "owner", doc=doc)
    d = ii.accepted_detail("NEE", "rate_base", "USD", doc=doc, fx=USD)
    assert d["value"] == pytest.approx(71e9)
    assert d["allowed_roe"] == pytest.approx(0.108) and d["equity_ratio"] == pytest.approx(0.596)
    assert d["jurisdiction"] == "Florida PSC"


def test_editing_the_allowed_roe_revokes_the_acceptance(store):
    doc = _rate_base_doc()
    ii.set_review("NEE", "rate_base", "accepted", "owner", doc=doc)
    assert ii.accepted_detail("NEE", "rate_base", "USD", doc=_rate_base_doc(roe=0.118), fx=USD) is None


def test_a_percentage_read_as_a_decimal_fails_its_rate_order_check():
    data = _rate_base_doc(roe=10.8)["tickers"]["NEE"]["rate_base"]["data"]
    checks = ii.reconcile("rate_base", 71e9, {"net_ppe": 140e9}, data=data)
    assert [c["ok"] for c in checks if c["check"] == "allowed_roe"] == [False]
    assert [c["ok"] for c in checks if c["check"] == "rate_base / net_ppe"] == [True]
    # bn read as mn: 0.0005x of net plant.
    assert any(c["ok"] is False for c in ii.reconcile("rate_base", 71e6, {"net_ppe": 140e9}, data=data))


def test_a_regime_with_no_allowed_roe_is_reported_not_failed(store):
    """Hong Kong's Scheme of Control permits a return on net fixed assets and
    sets no ROE. The entry is still reviewable; the method falls back without it."""
    doc = _rate_base_doc(roe=None, equity=None)
    data = doc["tickers"]["NEE"]["rate_base"]["data"]
    checks = ii.reconcile("rate_base", 71e9, {"net_ppe": 140e9}, data=data)
    assert [c["ok"] for c in checks if c["check"] in ("allowed_roe", "equity_ratio")] == [None, None]
    ii.set_review("NEE", "rate_base", "accepted", "owner", doc=doc)
    d = ii.accepted_detail("NEE", "rate_base", "USD", doc=doc, fx=USD)
    assert d["allowed_roe"] is None and d["equity_ratio"] is None

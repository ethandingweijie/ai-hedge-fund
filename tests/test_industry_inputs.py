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
    assert v == pytest.approx(((3e9 - 0.5e9) / 1e8) / 0.06)

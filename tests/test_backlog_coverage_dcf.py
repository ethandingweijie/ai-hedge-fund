"""Backlog-coverage DCF (Wave 3 design, built early at the owner's priority).

The core DCF projection with years 1-3 of its revenue growth bounded by work
already under contract:

    coverage = accepted backlog / revenue base
    floor_t  = -(1 - min(coverage / t, 1))      year t is covered iff coverage >= t
    ceil_t   = book_to_bill - 1  (when cited)   orders, not hope, cap the ramp

Review-gated like every industry input: a pending figure never binds, and
without an accepted one the leg is the core DCF exactly, and says so.
"""
import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _backlog_growth_bounds as bounds
from src.agents.analysis.dcf_agent import _bound_growth_schedule as bound


# ── the bounds ───────────────────────────────────────────────────────────────

def test_a_year_is_covered_if_and_only_if_the_backlog_reaches_it():
    assert bounds(1.5) == [(0.0, None), (pytest.approx(-0.25), None), (pytest.approx(-0.5), None)]
    assert bounds(4.63) == [(0.0, None)] * 3               # GE Vernova: every bounded year covered
    assert bounds(0.4)[0] == (pytest.approx(-0.6), None)
    assert bounds(0.0) == [(-1.0, None)] * 3               # no contracted work bounds nothing


def test_year_one_is_the_bear_only_floor_already_live_so_the_two_cannot_double_apply():
    for cov in (0.18, 0.5, 1.0, 1.7):
        assert bounds(cov)[0][0] == pytest.approx(-(1.0 - min(cov, 1.0)))


def test_the_ceiling_is_book_to_bill_and_nothing_else():
    assert [c for _, c in bounds(2.0, book_to_bill=1.25)] == [pytest.approx(0.25)] * 3
    assert [c for _, c in bounds(2.0)] == [None] * 3
    # A "revenue <= backlog" ceiling would assume zero new awards: a prime with
    # 1.5x coverage would be driven to zero revenue in year 3. There is no such cap.
    sched, moved = bound([0.05] * 10, bounds(1.5))
    assert sched == [0.05] * 10 and moved == []


def test_only_years_one_to_three_move_and_each_move_is_recorded():
    sched, moved = bound([-0.20, -0.20, -0.20, -0.20, 0.02], bounds(1.5))
    assert sched == [0.0, pytest.approx(-0.20), pytest.approx(-0.20), -0.20, 0.02]
    assert moved == [{"year": 1, "from": -0.20, "to": 0.0, "bound": "floor"}]
    sched, moved = bound([0.40, 0.30, 0.10, 0.40], bounds(3.0, book_to_bill=1.2))
    assert sched == [pytest.approx(0.20), pytest.approx(0.20), 0.10, 0.40]
    assert [m["bound"] for m in moved] == ["ceiling", "ceiling"]


def test_contracted_work_wins_when_a_ceiling_would_sit_below_the_floor():
    """Book-to-bill 0.7 caps growth at -30%; full coverage floors year 1 at 0%."""
    sched, moved = bound([0.05, 0.05, 0.05], bounds(1.0, book_to_bill=0.7))
    assert sched[0] == 0.0 and sched[1] == pytest.approx(-0.30)


# ── the leg ──────────────────────────────────────────────────────────────────

KW = dict(revenue_base=4e10, shares=2.7e8, net_debt=-9e9, market_cap=1.7e11, wacc=0.0825, growth_base=-0.10,
          fcf_margin_base=0.08, tgr=0.02, fcf_floor=0.0, sector="Industrials", scenario="bear",
          profile_name="Capital Goods",
          projection={"growth_schedule": [-0.10, -0.08, -0.05, 0.0, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02]})


def _leg(name, row, monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    return dcf_agent._traced_method_value(method_name=name, most_recent=row, **KW)


@pytest.mark.parametrize("name", ["Backlog DCF", "Backlog-coverage DCF", "Contracted-backlog DCF"])
def test_without_an_accepted_backlog_the_leg_is_the_core_dcf_exactly(name, monkeypatch):
    v, tr = _leg(name, {}, monkeypatch)
    core, core_tr = _leg("DCF", {}, monkeypatch)
    assert v == core
    assert tr == core_tr                                   # byte-identical trace: no bound, no extra key
    assert name in dcf_agent._DCF_PROJECTION_FAMILY        # so the OE<=0 gate still knocks it out


def test_an_accepted_backlog_floors_the_covered_years_and_the_trace_shows_what_moved(monkeypatch):
    row = {"backlog_accepted": 1.76e11, "_backlog_detail": {"book_to_bill": None, "backlog_kind": "rpo",
                                                            "period": "Q2 2026"}}
    v, tr = _leg("Backlog-coverage DCF", row, monkeypatch)
    core, _ = _leg("DCF", {}, monkeypatch)
    assert v > core                                        # a decline the order book rules out
    assert tr["growth_schedule"][:4] == [0.0, 0.0, 0.0, 0.0] and tr["growth_schedule"][4] == 0.02
    b = tr["backlog_bound"]
    assert b["coverage_years"] == pytest.approx(4.4) and b["backlog_kind"] == "rpo"
    assert [m["year"] for m in b["moved"]] == [1, 2, 3] and {m["bound"] for m in b["moved"]} == {"floor"}
    assert tr["kind"] == "dcf" and len(tr["projection_rows"]) == 10


def test_a_growing_name_with_full_coverage_is_not_moved_at_all(monkeypatch):
    """GE Vernova's base case: growth already positive, 4.6 years covered, no
    book-to-bill cited. The bound exists and does not bind -- the leg is the core
    DCF, and the trace says the backlog was seen and moved nothing."""
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    kw = {**KW, "scenario": "base", "growth_base": 0.086,
          "projection": {"growth_schedule": [0.086, 0.053, 0.037, 0.028, 0.024, 0.022, 0.021, 0.0205, 0.02, 0.02]}}
    row = {"backlog_accepted": 1.76e11, "_backlog_detail": {}}
    v, tr = dcf_agent._traced_method_value(method_name="Backlog-coverage DCF", most_recent=row, **kw)
    core, _ = dcf_agent._traced_method_value(method_name="DCF", most_recent={}, **kw)
    assert v == core and tr["backlog_bound"]["moved"] == []


def test_the_contracted_book_variant_has_no_ceiling_even_when_a_book_to_bill_is_cited(monkeypatch):
    """Uranium and SWU books are fixed-volume multi-year contracts: there is no
    order-intake ratio to cap a ramp with."""
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    kw = {**KW, "scenario": "bull", "growth_base": 0.30, "projection": {"growth_schedule": [0.30] * 10}}
    row = {"backlog_accepted": 8e10, "_backlog_detail": {"book_to_bill": 1.05}}
    capped, tr_c = dcf_agent._traced_method_value(method_name="Backlog-coverage DCF", most_recent=row, **kw)
    free, tr_f = dcf_agent._traced_method_value(method_name="Contracted-backlog DCF", most_recent=row, **kw)
    assert tr_c["growth_schedule"][:3] == [pytest.approx(0.05)] * 3 and tr_f["growth_schedule"][:3] == [0.30] * 3
    assert capped < free and tr_f["backlog_bound"]["book_to_bill"] is None


# ── the review gate ──────────────────────────────────────────────────────────

def test_an_accepted_backlog_carries_its_kind_and_book_to_bill_and_a_pending_one_carries_nothing(tmp_path, monkeypatch):
    from src.data import db as _db
    from src.data import industry_inputs as ii
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(ii, "_reviews_ready_key", None)
    doc = {"version": 1, "tickers": {"LMT": {"backlog": {"data": {
        "kind": "funded", "book_to_bill": 1.3,
        "value": {"value": 160.0, "currency": "USD", "scale": "bn", "period": "FY2025",
                  "source_url": "https://x", "quote": "backlog of $160 billion"}}, "checks": [], "ok": True}}}}
    usd = lambda c: 1.0 if c == "USD" else None  # noqa: E731
    try:
        assert ii.accepted_detail("LMT", "backlog", "USD", doc=doc, fx=usd) is None
        ii.set_review("LMT", "backlog", "accepted", "owner", doc=doc)
        d = ii.accepted_detail("LMT", "backlog", "USD", doc=doc, fx=usd)
        assert d["value"] == pytest.approx(1.6e11) and d["backlog_kind"] == "funded" and d["book_to_bill"] == 1.3
    finally:
        _db.close_all_connections()

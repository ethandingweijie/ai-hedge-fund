"""
tests/test_what_if_library.py
==============================
P6 gates — joint scenario memory + assumption tracking (service layer).

Covers: publish/dedupe-by-content-hash, fork lineage + build_count,
community notes, viewer-compare skeleton + sensitivity, assumption-check
ledger (both methods), verdict normalization, and the deterministic
market-data window. All offline — tmp sqlite via the dual-mode fixture
pattern; LLM/FMP seams monkeypatched at the service boundary.

Division-of-labor assertion baked into the tests: sensitivity numbers
(author_delta / assumption_sensitivities) come from Python re-running the
skeleton math (apply_assumption_shift), never from mocked LLM output.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.services import portfolio_service as ps
from app.backend.services import complacency_job_store as job_store
from src.data import db as _db


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Dual-mode archive pointed at a throwaway sqlite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "p6_test.db"))
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def db():
    """In-memory SQLAlchemy session for user_holdings (compare path)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _assumption(metric="10Y refi rate spikes", watch="treasury 10Y",
                timing="weeks", linked="Financials", shift=-4.0):
    a = {"metric": metric, "watch_for": watch, "timing": timing}
    if linked is not None:
        a["linked_sector"] = linked
    if shift is not None:
        a["if_true_shift_pp"] = shift
    return a


def _result(skeleton_rows=None, assumptions=None, summary="A credit shock."):
    """A minimal full what-if result shaped like run_what_if's output."""
    rows = skeleton_rows if skeleton_rows is not None else [
        {"ticker": "BABA", "kind": "equity", "sector": "Consumer Cyclical",
         "gics": "Consumer Discretionary", "est_impact_pct": -30.0,
         "anchor_pct": -30.0, "weight_basis": 10000.0},
        {"ticker": "JPM", "kind": "equity", "sector": "Financial Services",
         "gics": "Financials", "est_impact_pct": -40.0,
         "anchor_pct": -40.0, "weight_basis": 10000.0},
    ]
    port = None
    covered = [r for r in rows if r.get("est_impact_pct") is not None]
    tw = sum(r["weight_basis"] for r in rows)
    if covered:
        port = round(sum(r["weight_basis"] * r["est_impact_pct"]
                         for r in covered) / sum(r["weight_basis"] for r in covered), 2)
    return {
        "skeleton": {
            "holdings": rows,
            "portfolio_est_impact_pct": port,
            "covered_weight_pct": 100.0 if tw else None,
        },
        "llm": {
            "scenario_summary": summary,
            "assumptions_to_watch": assumptions if assumptions is not None
            else [_assumption()],
        },
        "warnings": [],
    }


def _publish(category="Credit shock", concerns="Refinancing wall hits banks",
             reference_key=None, horizon_days=90, parent_id=None,
             result=None, user_id=1, user_name="alice"):
    return ps.publish_what_if_scenario(
        user_id, user_name, category, concerns, reference_key,
        horizon_days, parent_id, result if result is not None else _result())


# ── publish + dedupe ─────────────────────────────────────────────────────────

class TestPublishAndDedupe:
    def test_publish_creates_library_row_and_assumption_rows(self, tmp_db):
        sid = _publish()
        assert sid

        rows = ps.list_what_if_library()
        assert len(rows) == 1
        entry = rows[0]
        assert entry["scenario_id"] == sid
        assert entry["category"] == "Credit shock"
        assert entry["created_by_name"] == "alice"
        assert entry["author_portfolio_est_pct"] == pytest.approx(-35.0)
        assert entry["build_count"] == 0
        assert "credit shock" in entry["summary_excerpt"].lower()

        detail = ps.get_what_if_scenario(sid)
        assert detail["result"]["llm"]["scenario_summary"] == "A credit shock."
        assert len(detail["assumptions"]) == 1
        a = detail["assumptions"][0]
        assert a["metric"] == "10Y refi rate spikes"
        assert a["linked_sector"] == "Financials"
        assert a["if_true_shift_pp"] == pytest.approx(-4.0)
        assert a["status"] == "open"
        # Author sensitivity computed deterministically at publish time:
        # JPM (Financials, w=10000) shifts -40 → -44; BABA untouched.
        assert a["author_delta"] is not None
        assert a["author_delta"]["base_portfolio_est_pct"] == pytest.approx(-35.0)
        assert a["author_delta"]["adjusted_portfolio_est_pct"] == pytest.approx(-37.0)
        assert a["author_delta"]["delta_pp"] == pytest.approx(-2.0)
        assert a["author_delta"]["affected_tickers"] == ["JPM"]

    def test_republish_same_content_is_idempotent_first_author_kept(self, tmp_db):
        sid1 = _publish(user_id=1, user_name="alice")
        sid2 = _publish(user_id=2, user_name="bob")   # same content, new user
        assert sid1 == sid2
        assert len(ps.list_what_if_library()) == 1
        detail = ps.get_what_if_scenario(sid1)
        assert detail["created_by_name"] == "alice"   # first author kept
        assert detail["build_count"] == 0

    def test_hash_version_gated_and_content_normalized(self, tmp_db):
        h1 = ps.compute_library_hash("Credit shock", "  Refi   wall  ",
                                     None, 90)
        h2 = ps.compute_library_hash("Credit shock", "Refi wall", None, 90)
        assert h1 == h2                               # whitespace-normalized
        h3 = ps.compute_library_hash("Credit shock", "Refi wall", None, 180)
        assert h1 != h3                               # horizon is part of key

    def test_assumption_without_sensitivity_fields_gets_null_delta(self, tmp_db):
        """Pre-v6 payloads degrade: assumption rows still land, no delta."""
        sid = _publish(result=_result(assumptions=[
            {"metric": "spreads widen", "watch_for": "HY OAS",
             "timing": "weeks"}]))
        detail = ps.get_what_if_scenario(sid)
        a = detail["assumptions"][0]
        assert a["linked_sector"] is None
        assert a["if_true_shift_pp"] is None
        assert a["author_delta"] is None

    def test_non_dict_and_metricless_assumptions_skipped(self, tmp_db):
        sid = _publish(result=_result(assumptions=[
            _assumption(), "prose garbage", {"watch_for": "no metric here"}]))
        detail = ps.get_what_if_scenario(sid)
        assert len(detail["assumptions"]) == 1        # only the valid one


# ── degraded runs (llm=None) never publish ───────────────────────────────────

class TestDegradedNotPublished:
    """Shared memory is narrative + trackable assumptions; a skeleton-only
    result offers neither. Live incident (P6 E2E round 3): a degraded run
    slipped into the library because only the job path guarded on
    result["llm"] — publish itself now refuses them."""

    def test_publish_refuses_result_without_llm(self, tmp_db):
        res = _result()
        res["llm"] = None                       # degraded: LLM call failed
        assert _publish(result=res) is None
        assert ps.list_what_if_library() == []

    def test_publish_refuses_result_missing_llm_key(self, tmp_db):
        res = _result()
        del res["llm"]
        assert _publish(result=res) is None
        assert ps.list_what_if_library() == []

    def test_publish_refuses_empty_result(self, tmp_db):
        assert _publish(result={}) is None
        assert ps.publish_what_if_scenario(
            1, "alice", "Credit shock", "x", None, 90, None, None) is None
        assert ps.list_what_if_library() == []

    def test_degraded_publish_never_blocks_a_good_one(self, tmp_db):
        res = _result()
        res["llm"] = None
        assert _publish(result=res) is None
        sid = _publish()                        # same content, full result
        assert sid and len(ps.list_what_if_library()) == 1


# ── fork lineage + build_count ───────────────────────────────────────────────

class TestForkLineage:
    def test_fork_links_parent_and_bumps_build_count_only_on_insert(self, tmp_db):
        parent = _publish()
        fork = _publish(concerns="Same shock but with a housing twist",
                        parent_id=parent)
        assert fork != parent

        p = ps.get_what_if_scenario(parent)
        assert p["build_count"] == 1
        assert p["children_count"] == 1
        f = ps.get_what_if_scenario(fork)
        assert f["parent"]["scenario_id"] == parent
        assert f["parent"]["created_by_name"] == "alice"

        # Re-forking identical content returns the existing fork row and
        # does NOT bump the parent again.
        again = _publish(concerns="Same shock but with a housing twist",
                         parent_id=parent, user_id=3, user_name="carol")
        assert again == fork
        assert ps.get_what_if_scenario(parent)["build_count"] == 1

    def test_unknown_parent_silently_dropped(self, tmp_db):
        sid = _publish(parent_id="does-not-exist")
        assert ps.get_what_if_scenario(sid)["parent"] is None

    def test_get_unknown_scenario_returns_none(self, tmp_db):
        assert ps.get_what_if_scenario("nope") is None


# ── community notes ──────────────────────────────────────────────────────────

class TestNotes:
    def test_add_note_and_read_back(self, tmp_db):
        sid = _publish()
        note = ps.add_what_if_note(sid, 2, "bob", "HY OAS already +80bps")
        assert note and note["user_name"] == "bob"

        detail = ps.get_what_if_scenario(sid)
        assert len(detail["notes"]) == 1
        assert detail["notes"][0]["note"] == "HY OAS already +80bps"
        assert ps.list_what_if_library()[0]["notes_count"] == 1

    def test_note_validation(self, tmp_db):
        sid = _publish()
        with pytest.raises(ValueError):
            ps.add_what_if_note(sid, 1, "alice", "   ")
        with pytest.raises(ValueError):
            ps.add_what_if_note(sid, 1, "alice", "x" * 2001)
        assert ps.add_what_if_note("unknown-scenario", 1, "alice", "hi") is None


# ── viewer compare (deterministic skeleton on the viewer's holdings) ────────

class TestCompareToHoldings:
    def test_compare_builds_viewer_skeleton_and_sensitivity(
            self, tmp_db, db, monkeypatch):
        sid = _publish(reference_key="gfc_2008")
        ps.upsert_holding(db, 7, "BABA", 100, 120.0)
        ps.upsert_holding(db, 7, "JPM", 50, 200.0)

        # Give the viewer's tickers sectors (fresh tmp archive has none).
        monkeypatch.setattr(ps, "_latest_signals", lambda tickers: {
            "BABA": {"sector": "Consumer Discretionary"},
            "JPM": {"sector": "Financials"},
        })

        out = ps.compare_what_if_to_holdings(db, 7, sid)
        assert out["scenario_id"] == sid
        sk = out["skeleton"]
        tickers = {r["ticker"] for r in sk["holdings"]}
        assert tickers == {"BABA", "JPM"}
        # GFC anchor → every equity gets an estimate (deterministic).
        assert all(r["est_impact_pct"] is not None for r in sk["holdings"])
        assert sk["portfolio_est_impact_pct"] is not None

        sens = {s["metric"]: s for s in out["assumption_sensitivities"]}
        assert len(sens) == 1
        s = sens["10Y refi rate spikes"]
        # Viewer holds JPM (Financials) → shift moves the portfolio needle.
        assert s["linked_gics"] == "Financials"
        assert s["delta_pp"] != 0.0
        assert "JPM" in s["affected_tickers"]
        assert s["adjusted_portfolio_est_pct"] == pytest.approx(
            s["base_portfolio_est_pct"] + s["delta_pp"])

    def test_compare_no_holdings(self, tmp_db, db):
        sid = _publish()
        assert ps.compare_what_if_to_holdings(db, 42, sid) == \
            {"error": "no_holdings"}

    def test_compare_unknown_scenario(self, tmp_db, db):
        assert ps.compare_what_if_to_holdings(db, 1, "nope") is None


# ── assumption checks (ledger + job execution) ───────────────────────────────

class TestAssumptionChecks:
    def _scenario_with_assumption(self, tmp_db):
        sid = _publish()
        detail = ps.get_what_if_scenario(sid)
        return sid, detail["assumptions"][0], {
            "scenario_id": sid, "category": "Credit shock",
            "concerns": "Refinancing wall hits banks",
            "created_at": detail["created_at"],
        }

    def test_start_check_rejects_bad_inputs(self, tmp_db):
        with pytest.raises(ValueError):
            ps.start_assumption_check(1, "alice", "missing", "market_data")
        sid, a, _ = self._scenario_with_assumption(tmp_db)
        with pytest.raises(ValueError):
            ps.start_assumption_check(1, "alice", a["assumption_id"],
                                      "vibes")

    def test_start_check_dedupes_in_flight(self, tmp_db, monkeypatch):
        sid, a, _ = self._scenario_with_assumption(tmp_db)
        aid = a["assumption_id"]

        started = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                started.append(kwargs)

            def start(self):
                pass

        monkeypatch.setattr(ps.threading, "Thread", FakeThread)
        first = ps.start_assumption_check(1, "alice", aid, "market_data")
        second = ps.start_assumption_check(2, "bob", aid, "deep_research")
        assert first["deduped"] is False
        assert second["deduped"] is True
        assert second["job_id"] == first["job_id"]
        assert len(started) == 1                      # one thread only

    def test_market_data_check_writes_ledger_and_sets_status(
            self, tmp_db, monkeypatch):
        sid, a, scenario = self._scenario_with_assumption(tmp_db)
        aid = a["assumption_id"]
        assumption_row = dict(a)
        assumption_row["assumption_id"] = aid

        monkeypatch.setattr(
            ps, "_market_data_reading",
            lambda assumption, scen: ("10Y 4.1% → 4.9%", "FMP test"))
        monkeypatch.setattr(
            ps, "_verdict_via_llm",
            lambda assumption, reading: ("confirmed", "10Y rose 80bps"))

        job_id = job_store.create_job("what_if_check", ticker=aid, user_id=1)
        ps._execute_assumption_check_job(job_id, 1, "alice", assumption_row,
                                         scenario, "market_data")

        job = job_store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["result"]["verdict"] == "confirmed"
        assert job["result"]["method"] == "market_data"
        assert job["result"]["source"] == "FMP test"

        detail = ps.get_what_if_scenario(sid)
        a2 = detail["assumptions"][0]
        assert a2["status"] == "confirmed"
        assert a2["checks_count"] == 1
        assert a2["latest_check"]["verdict"] == "confirmed"
        assert a2["latest_check"]["user_name"] == "alice"

    def test_market_data_no_reading_yields_no_data_without_llm(
            self, tmp_db, monkeypatch):
        sid, a, scenario = self._scenario_with_assumption(tmp_db)
        assumption_row = dict(a)

        monkeypatch.setattr(ps, "_market_data_reading",
                            lambda assumption, scen: (None, None))

        def _boom(*args, **kwargs):
            raise AssertionError("no LLM call expected when no reading")

        monkeypatch.setattr(ps, "_verdict_via_llm", _boom)

        job_id = job_store.create_job("what_if_check", ticker=a["assumption_id"],
                                      user_id=1)
        ps._execute_assumption_check_job(job_id, 1, "alice", assumption_row,
                                         scenario, "market_data")
        job = job_store.get_job(job_id)
        assert job["result"]["verdict"] == "no_data"
        # no_data does NOT overwrite the assumption's open status
        assert ps.get_what_if_scenario(sid)["assumptions"][0]["status"] == "open"

    def test_deep_research_check_path(self, tmp_db, monkeypatch):
        sid, a, scenario = self._scenario_with_assumption(tmp_db)
        assumption_row = dict(a)

        monkeypatch.setattr(
            ps, "_deep_research_check",
            lambda assumption, scen: ("inconclusive", "mixed signals",
                                      "qwen_web_search"))

        job_id = job_store.create_job("what_if_check", ticker=a["assumption_id"],
                                      user_id=2)
        ps._execute_assumption_check_job(job_id, 2, "bob", assumption_row,
                                         scenario, "deep_research")
        job = job_store.get_job(job_id)
        assert job["result"]["verdict"] == "inconclusive"
        a2 = ps.get_what_if_scenario(sid)["assumptions"][0]
        assert a2["status"] == "inconclusive"
        assert a2["latest_check"]["method"] == "deep_research"

    def test_failed_check_marks_job_failed(self, tmp_db, monkeypatch):
        sid, a, scenario = self._scenario_with_assumption(tmp_db)
        assumption_row = dict(a)

        def _boom(*args, **kwargs):
            raise RuntimeError("fmp down")

        monkeypatch.setattr(ps, "_market_data_reading", _boom)
        job_id = job_store.create_job("what_if_check", ticker=a["assumption_id"],
                                      user_id=1)
        ps._execute_assumption_check_job(job_id, 1, "alice", assumption_row,
                                         scenario, "market_data")
        job = job_store.get_job(job_id)
        assert job["status"] == "failed"
        assert "fmp down" in (job.get("error_msg") or job.get("error") or "")


class TestCheckHelpers:
    @pytest.mark.parametrize("raw,expected", [
        ("confirmed", "confirmed"), ("Holding", "confirmed"),
        ("materializing", "confirmed"), ("true", "confirmed"),
        ("refuted", "disconfirmed"), ("not_holding", "disconfirmed"),
        ("no_data", "no_data"), ("", "inconclusive"),
        ("maybe later", "inconclusive"), (None, "inconclusive"),
    ])
    def test_norm_verdict(self, raw, expected):
        assert ps._norm_verdict(raw) == expected

    def test_check_window_caps_at_89_days(self):
        old = (datetime.now(timezone.utc) - timedelta(days=400)) \
            .isoformat()
        frm, to = ps._check_window({"created_at": old})
        d0 = datetime.strptime(frm, "%Y-%m-%d")
        d1 = datetime.strptime(to, "%Y-%m-%d")
        assert (d1 - d0).days <= 89

    def test_check_window_handles_bad_date(self):
        frm, to = ps._check_window({"created_at": "garbage"})
        d0 = datetime.strptime(frm, "%Y-%m-%d")
        d1 = datetime.strptime(to, "%Y-%m-%d")
        assert 0 <= (d1 - d0).days <= 89

    def test_market_data_reading_no_match_returns_none(self, tmp_db,
                                                       monkeypatch):
        """Keywords that match nothing AND no resolvable sector → (None, None)."""
        monkeypatch.setattr("src.tools.api.get_treasury_rates",
                            lambda *a, **k: [])
        monkeypatch.setattr("src.tools.api.get_economic_indicator",
                            lambda *a, **k: [])
        monkeypatch.setattr("src.tools.api.get_prices",
                            lambda *a, **k: [])
        reading, source = ps._market_data_reading(
            {"metric": "consumer sentiment collapses", "watch_for": "",
             "timing": "", "linked_sector": None},
            {"created_at": datetime.now(timezone.utc).isoformat()})
        assert reading is None and source is None


# ── get_what_if_job serves both kinds ────────────────────────────────────────

class TestJobRouteGuard:
    def test_check_job_visible_through_what_if_job_guard(self, tmp_db):
        job_id = job_store.create_job("what_if_check", ticker="a1", user_id=5)
        assert ps.get_what_if_job(job_id, 5) is not None
        assert ps.get_what_if_job(job_id, 6) is None          # other user
        other = job_store.create_job("complacency", ticker="X", user_id=5)
        assert ps.get_what_if_job(other, 5) is None            # wrong kind


# ── heartbeat race: a stale pulse must never overwrite a terminal status ─────
# Regression for the live P6 E2E failure: a what-if heartbeat thread woke in
# the same instant the worker committed complete_job; on PG the heartbeat's
# unconditional UPDATE landed last and flipped the job back to 'running' with
# finished_at already set, so pollers waited until the 30-min watchdog killed
# a job that had actually succeeded.

class TestHeartbeatRace:
    def test_heartbeat_after_complete_is_noop(self, tmp_db):
        job_id = job_store.create_job("what_if", ticker=None, user_id=1)
        job_store.update_progress(job_id, "running", "simulating")
        job_store.complete_job(job_id, {"ok": True})
        # a pulse that passed its stop.wait before stop.set() commits late:
        job_store.update_progress(job_id, "running", "simulating · 1m 0s")
        job = job_store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["result"] == {"ok": True}
        assert job["finished_at"]

    def test_heartbeat_after_fail_is_noop(self, tmp_db):
        job_id = job_store.create_job("what_if_check", ticker="a9", user_id=1)
        job_store.update_progress(job_id, "running", "checking")
        job_store.fail_job(job_id, "boom")
        job_store.update_progress(job_id, "running", "checking · 0m 20s")
        job = job_store.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error"] == "boom"

    def test_heartbeat_while_running_still_updates(self, tmp_db):
        job_id = job_store.create_job("what_if", ticker=None, user_id=1)
        job_store.update_progress(job_id, "running", "phase one")
        job_store.update_progress(job_id, "running", "phase two")
        job = job_store.get_job(job_id)
        assert job["status"] == "running"
        assert job["progress_msg"] == "phase two"

"""
tests/test_research_ideas_dual_mode.py
=======================================
S1 batch — research-ideas storage services on the dual-mode DB
(src/data/db.py). Same regression pattern as
tests/test_knowledge_graph_dual_mode.py: these modules used raw sqlite3
against RUN_ARCHIVE_PATH, so every Railway process (2 web replicas +
worker + scheduler) had its own private file. Refreshes landed in one
process's file while the main page read another's — the "stale summaries
after refresh" bug. All reads/writes now go through the shared
SQLite-local / Postgres-production layer.

Covered services (tables):
  sw46_storage            (sw46_runs)
  fundflow_storage        (fundflow_runs)
  complacency_storage     (complacency_runs)
  qualitative_storage     (complacency_qualitative)
  complacency.web_research (complacency_web_research)
  hk50_storage            (hk50_runs)
  momentum_storage        (momentum_runs)
  contrarian_storage      (contrarian_ideas/_idea_chat/_shortlist)
  sector_medians_storage  (sector_medians)
Plus the complacency runner's qual-cache rehydration fast path, which was
a separate raw-sqlite call site against the same tables.
"""
import json

import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "s1_test.db"))
    _db.close_all_connections()
    yield
    _db.close_all_connections()


def _fresh(module):
    """Reset the DDL memo so the module re-creates tables on the tmp DB."""
    module._tables_ready_key = None
    return module


@pytest.fixture()
def sw46(tmp_db):
    from app.backend.services import sw46_storage
    return _fresh(sw46_storage)


@pytest.fixture()
def fundflow(tmp_db):
    from app.backend.services import fundflow_storage
    return _fresh(fundflow_storage)


@pytest.fixture()
def complacency(tmp_db):
    from app.backend.services import complacency_storage
    return _fresh(complacency_storage)


@pytest.fixture()
def qualitative(tmp_db):
    from app.backend.services import qualitative_storage
    return _fresh(qualitative_storage)


@pytest.fixture()
def web_research(tmp_db):
    from src.research_ideas.complacency import web_research
    return _fresh(web_research)


@pytest.fixture()
def hk50(tmp_db):
    from app.backend.services import hk50_storage
    return _fresh(hk50_storage)


@pytest.fixture()
def momentum(tmp_db):
    from app.backend.services import momentum_storage
    return _fresh(momentum_storage)


@pytest.fixture()
def contrarian(tmp_db):
    from app.backend.services import contrarian_storage
    return _fresh(contrarian_storage)


@pytest.fixture()
def medians(tmp_db):
    from app.backend.services import sector_medians_storage
    return _fresh(sector_medians_storage)


# ── sw46 ──────────────────────────────────────────────────────────────────────

def _mk_sw46(run_id="r1", created="2026-08-16T00:00:00+00:00", **kw):
    from src.research_ideas.sw46.schemas import SW46CohortResult
    return SW46CohortResult(run_id=run_id, created_at=created, **kw)


def test_sw46_roundtrip(sw46):
    sw46.save_sw46_run(_mk_sw46(pooled_delta_e=0.043, ticker_count=7,
                                failed_tickers=[{"ticker": "X", "reason": "429"}]))
    latest = sw46.get_latest_sw46_run()
    assert latest["run_id"] == "r1"
    assert latest["cohort_pooled_delta_e"] == 0.043
    assert latest["ticker_count"] == 7
    assert latest["failed_tickers"] == [{"ticker": "X", "reason": "429"}]
    assert latest["results"] == []

    assert sw46.get_sw46_run("r1")["run_id"] == "r1"
    assert sw46.get_sw46_run("nope") is None
    assert [r["run_id"] for r in sw46.list_sw46_runs()] == ["r1"]


def test_sw46_upsert_replaces_not_duplicates(sw46):
    sw46.save_sw46_run(_mk_sw46(pooled_delta_e=0.01))
    sw46.save_sw46_run(_mk_sw46(pooled_delta_e=0.09))
    rows = _db.query("SELECT COUNT(*) AS n FROM sw46_runs")
    assert rows[0]["n"] == 1
    assert sw46.get_latest_sw46_run()["cohort_pooled_delta_e"] == 0.09


# ── fundflow ──────────────────────────────────────────────────────────────────

def _mk_fundflow(run_id="f1", created="2026-08-16T00:00:00+00:00", **kw):
    from src.research_ideas.fundflow.schemas import FundFlowCohortResult
    return FundFlowCohortResult(run_id=run_id, created_at=created, **kw)


def test_fundflow_roundtrip(fundflow):
    fundflow.save_fundflow_run(_mk_fundflow(
        region_count=6, inflow_count=4, outflow_count=2,
        failed_regions=[{"region": "IN", "reason": "no etf"}],
    ))
    latest = fundflow.get_latest_fundflow_run()
    assert latest["region_count"] == 6
    assert latest["inflow_count"] == 4
    assert latest["failed_regions"] == [{"region": "IN", "reason": "no etf"}]
    assert latest["regions"] == [] and latest["benchmarks"] == []

    assert fundflow.get_fundflow_run("f1")["run_id"] == "f1"
    assert fundflow.get_fundflow_run("nope") is None
    assert [r["run_id"] for r in fundflow.list_fundflow_runs()] == ["f1"]


def test_fundflow_latest_skips_empty_cohort(fundflow):
    """A failed refresh (FMP outage → 0 regions) must never hide the
    previous good run."""
    fundflow.save_fundflow_run(_mk_fundflow("good", "2026-08-15T00:00:00+00:00",
                                            region_count=6))
    fundflow.save_fundflow_run(_mk_fundflow("empty", "2026-08-16T00:00:00+00:00",
                                            region_count=0))
    assert fundflow.get_latest_fundflow_run()["run_id"] == "good"


def test_fundflow_latest_falls_back_to_empty_on_fresh_install(fundflow):
    fundflow.save_fundflow_run(_mk_fundflow("only", region_count=0))
    assert fundflow.get_latest_fundflow_run()["run_id"] == "only"


# ── complacency ───────────────────────────────────────────────────────────────

def _mk_complacency(run_id="c1", created="2026-08-16T00:00:00+00:00", **kw):
    from src.research_ideas.complacency.schemas import ComplacencyCohortResult
    return ComplacencyCohortResult(run_id=run_id, created_at=created, **kw)


def _mk_cticker(ticker="NVDA", **kw):
    from src.research_ideas.complacency.schemas import ComplacencyTickerResult
    return ComplacencyTickerResult(ticker=ticker, name=f"{ticker} Corp", **kw)


def test_complacency_roundtrip_and_skip_empty(complacency):
    complacency.save_complacency_run(_mk_complacency(
        "good", "2026-08-15T00:00:00+00:00",
        ticker_count=2, gate_passers=1, results=[_mk_cticker("NVDA")],
    ))
    complacency.save_complacency_run(_mk_complacency(
        "empty", "2026-08-16T00:00:00+00:00", ticker_count=0,
    ))
    latest = complacency.get_latest_complacency_run()
    assert latest["run_id"] == "good"
    assert latest["ticker_count"] == 2
    assert latest["results"][0]["ticker"] == "NVDA"

    assert complacency.get_complacency_run("empty")["ticker_count"] == 0
    runs = complacency.list_complacency_runs()
    assert [r["run_id"] for r in runs] == ["empty", "good"]


def test_complacency_update_ticker_in_latest_cohort(complacency):
    complacency.save_complacency_run(_mk_complacency(
        results=[_mk_cticker("NVDA", rank=3)],
        ticker_count=1,
    ))
    ok = complacency.update_ticker_in_latest_cohort(
        {"ticker": "nvda", "composite": 7.5, "rank": None})
    assert ok is True
    row = complacency.get_latest_complacency_run()["results"][0]
    assert row["composite"] == 7.5
    assert row["rank"] == 3  # preserved from the existing row

    assert complacency.update_ticker_in_latest_cohort(
        {"ticker": "ABSENT"}) is False
    assert complacency.update_ticker_in_latest_cohort({}) is False


def test_complacency_partial_qual_patcher_recomputes_aggregate(complacency):
    complacency.save_complacency_run(_mk_complacency(
        results=[_mk_cticker("NVDA")], ticker_count=1,
    ))
    ok = complacency.update_ticker_in_latest_cohort_partial_qual(
        "NVDA", "A1_valuation_extremity",
        {"indicator": "A1_valuation_extremity", "score": 4,
         "confidence": 0.9, "summary": "rich", "evidence": []},
    )
    assert ok is True
    r = complacency.get_latest_complacency_run()["results"][0]
    qual = r["qualitative"]
    assert qual["indicators"]["A1_valuation_extremity"]["score"] == 4
    assert qual["composite"] == 4 and qual["max_possible"] == 5
    assert r["aggregate_qual_pts"] == 40.0  # 4/5 * 50

    assert complacency.update_ticker_in_latest_cohort_partial_qual(
        "ABSENT", "A1", {"score": 1}) is False


# ── qualitative cache ─────────────────────────────────────────────────────────

def test_qualitative_roundtrip_and_ttl(qualitative):
    qualitative.save_qualitative_score(
        "NVDA", "A1", 4, 0.9, "rich valuation",
        [{"source": "10-K", "quote": "..."}], "qwen3.6-plus", 0.001)
    cached = qualitative.get_latest_qualitative_score("NVDA", "A1")
    assert cached["score"] == 4
    assert cached["evidence"] == [{"source": "10-K", "quote": "..."}]
    assert cached["age_days"] < 1

    # Backdate the row → stale under the 7-day TTL (deterministic).
    _db.execute(
        "UPDATE complacency_qualitative SET scored_at = ? "
        "WHERE ticker = ? AND indicator = ?",
        ["2020-01-01T00:00:00+00:00", "NVDA", "A1"],
    )
    assert qualitative.get_latest_qualitative_score("NVDA", "A1") is None
    assert qualitative.get_latest_qualitative_score("NVDA", "OTHER") is None


def test_qualitative_list_all_for_ticker(qualitative):
    qualitative.save_qualitative_score("NVDA", "A1", 4, 0.9, "s", [], "m")
    qualitative.save_qualitative_score("NVDA", "B2", 2, 0.5, "s", [], "m")
    qualitative.save_qualitative_score("MSFT", "A1", 1, 0.5, "s", [], "m")
    got = qualitative.list_all_for_ticker("NVDA")
    assert {c["indicator"] for c in got} == {"A1", "B2"}


# ── web-research cache ────────────────────────────────────────────────────────

def test_web_research_cache_roundtrip_and_ttl(web_research):
    payload = {"summary": "Q&A", "digest": "...", "topics_flagged": ["pricing"]}
    web_research._cache_put("NVDA", "earnings_qa", "", payload)
    got = web_research._cache_get("NVDA", "earnings_qa", "", 30)
    assert got["summary"] == "Q&A"
    assert got["_cached_age_days"] < 1

    # Backdate the row → stale under the 30-day TTL (deterministic, no
    # sub-second clock dependence).
    _db.execute(
        "UPDATE complacency_web_research SET cached_at = ? "
        "WHERE ticker = ? AND kind = ?",
        ["2020-01-01T00:00:00+00:00", "NVDA", "earnings_qa"],
    )
    assert web_research._cache_get("NVDA", "earnings_qa", "", 30) is None
    assert web_research._cache_get("NVDA", "deep_indicator", "A1", 30) is None


def test_web_research_cache_upsert_replaces(web_research):
    web_research._cache_put("NVDA", "earnings_qa", "", {"v": 1})
    web_research._cache_put("NVDA", "earnings_qa", "", {"v": 2})
    got = web_research._cache_get("NVDA", "earnings_qa", "", 30)
    assert got["v"] == 2
    rows = _db.query("SELECT COUNT(*) AS n FROM complacency_web_research")
    assert rows[0]["n"] == 1


# ── hk50 ──────────────────────────────────────────────────────────────────────

def _mk_hk50(run_id="h1", created="2026-08-16T00:00:00+00:00", **kw):
    from src.research_ideas.hk50.schemas import HK50CohortResult
    return HK50CohortResult(run_id=run_id, created_at=created, **kw)


def _mk_hkticker(ticker="9961.HK", **kw):
    from src.research_ideas.hk50.schemas import HK50TickerResult
    kw.setdefault("hk_ticker", ticker)
    kw.setdefault("name", f"{ticker} Inc")
    kw.setdefault("in_cohort", True)
    return HK50TickerResult(ticker=ticker, **kw)


def test_hk50_roundtrip_with_cohort_meta(hk50):
    hk50.save_hk50_run(_mk_hk50(
        ticker_count=2, avg_growth=61.0, avg_dividend=4.2,
        median_p_iv15=0.85, lead_growth_count=1,
        eligible_count=100, displayed_count=2,
        enter_threshold=55.0, stay_threshold=45.0,
        promoted=[{"ticker": "A", "name": "A", "lead_score": 60}],
        relegated=[],
        results=[_mk_hkticker(), _mk_hkticker("0700.HK")],
    ))
    latest = hk50.get_latest_hk50_run()
    assert latest["ticker_count"] == 2
    assert latest["eligible_count"] == 100
    assert latest["displayed_count"] == 2
    assert latest["enter_threshold"] == 55.0
    assert latest["promoted"][0]["ticker"] == "A"
    assert len(latest["results"]) == 2

    assert hk50.get_hk50_run("h1")["run_id"] == "h1"
    assert hk50.get_hk50_run("nope") is None
    assert [r["run_id"] for r in hk50.list_hk50_runs()] == ["h1"]


def test_hk50_latest_membership(hk50):
    hk50.save_hk50_run(_mk_hk50(results=[
        _mk_hkticker("9961.HK"),
        _mk_hkticker("0700.HK", in_cohort=False),
    ]))
    assert hk50.get_latest_membership() == {"9961.HK"}


def test_hk50_latest_membership_cold_start_is_empty(hk50):
    assert hk50.get_latest_membership() == set()


def test_hk50_set_ticker_qualitative_in_latest_cohort(hk50):
    hk50.save_hk50_run(_mk_hk50(results=[_mk_hkticker("9961.HK")]))
    ok = hk50.set_ticker_qualitative_in_latest_cohort(
        "9961.HK", {"source": "llm", "incomplete": False})
    assert ok is True
    r = hk50.get_latest_hk50_run()["results"][0]
    assert r["qualitative"] == {"source": "llm", "incomplete": False}

    assert hk50.set_ticker_qualitative_in_latest_cohort("ABSENT", {}) is False


# ── momentum ──────────────────────────────────────────────────────────────────

def _mk_momentum(run_id="m1", created="2026-08-16T00:00:00+00:00", **kw):
    from src.research_ideas.momentum.schemas import MomentumCohortResult
    return MomentumCohortResult(run_id=run_id, created_at=created, **kw)


def test_momentum_roundtrip_and_skip_empty(momentum):
    momentum.save_momentum_run(_mk_momentum(
        "good", "2026-08-15T00:00:00+00:00",
        ticker_count=10, long_count=4, short_count=3,
    ))
    momentum.save_momentum_run(_mk_momentum(
        "empty", "2026-08-16T00:00:00+00:00", ticker_count=0))
    latest = momentum.get_latest_momentum_run()
    assert latest["run_id"] == "good"
    assert latest["long_count"] == 4

    assert momentum.get_momentum_run("empty")["ticker_count"] == 0
    assert [r["run_id"] for r in momentum.list_momentum_runs()] == \
        ["empty", "good"]


# ── contrarian ────────────────────────────────────────────────────────────────

def _mk_idea(idea_id="i1", ticker="INTC", when="2026-08-16T00:00:00+00:00"):
    return {
        "idea_id": idea_id, "ticker": ticker, "company_name": f"{ticker} Corp",
        "generated_at": when, "model_used": "qwen3.6-plus", "cost_usd": 0.01,
        "thesis": "deep value",
    }


def test_contrarian_idea_lifecycle(contrarian):
    contrarian.save_idea(_mk_idea())
    assert contrarian.get_idea("i1")["thesis"] == "deep value"
    assert contrarian.get_latest_idea()["idea_id"] == "i1"
    assert [i["idea_id"] for i in contrarian.list_ideas()] == ["i1"]

    assert contrarian.soft_delete_idea("i1") is True
    assert contrarian.soft_delete_idea("i1") is False  # already deleted
    assert contrarian.get_latest_idea() is None        # hidden when deleted
    assert "_deleted_at" in contrarian.get_idea("i1")
    assert contrarian.list_ideas() == []
    assert [i["idea_id"] for i in contrarian.list_ideas(include_deleted=True)] == ["i1"]


def test_contrarian_resave_keeps_insert_or_replace_semantics(contrarian):
    """The pre-S1 INSERT OR REPLACE reset deleted_at on re-save; the ON
    CONFLICT upsert must behave identically."""
    contrarian.save_idea(_mk_idea())
    contrarian.soft_delete_idea("i1")
    contrarian.save_idea(_mk_idea())  # re-save resurrects
    assert "_deleted_at" not in contrarian.get_idea("i1")
    rows = _db.query("SELECT COUNT(*) AS n FROM contrarian_ideas")
    assert rows[0]["n"] == 1


def test_contrarian_chat_roundtrip(contrarian):
    contrarian.save_idea(_mk_idea())
    msg = contrarian.append_chat_message("i1", "user", "why INTC?", 0.0)
    contrarian.append_chat_message("i1", "assistant", "deep value", 0.002)
    msgs = contrarian.list_chat_messages("i1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["message_id"] == msg["message_id"]
    assert contrarian.list_chat_messages("other") == []


def test_contrarian_shortlist_lifecycle(contrarian):
    contrarian.save_idea(_mk_idea())
    assert contrarian.add_to_shortlist("absent") is None

    entry = contrarian.add_to_shortlist("i1", user_note="watch")
    assert entry["idea_snapshot"]["idea_id"] == "i1"
    assert contrarian.is_shortlisted("i1") is True

    listed = contrarian.list_shortlist()
    assert listed[0]["idea_id"] == "i1"
    assert listed[0]["user_note"] == "watch"

    assert contrarian.remove_from_shortlist("i1") is True
    assert contrarian.remove_from_shortlist("i1") is False
    assert contrarian.is_shortlisted("i1") is False


# ── sector medians ────────────────────────────────────────────────────────────

def _median_rows():
    return [
        {"sector": "Technology", "metric": "ev_sales",
         "median": 5.8, "p25": 3.1, "p75": 9.2, "sample_size": 68},
        {"sector": "Healthcare", "metric": "ev_sales",
         "median": 4.0, "p25": 2.0, "p75": 6.5, "sample_size": 55},
    ]


def test_sector_medians_batch_and_lookup(medians):
    n = medians.save_sector_medians_batch("2026-08-16T00:00:00+00:00",
                                          _median_rows())
    assert n == 2
    got = medians.get_latest_sector_median("Technology", "ev_sales")
    assert got["median"] == 5.8 and got["p25"] == 3.1
    assert got["sample_size"] == 68 and got["age_days"] < 1

    # Backdate the row → stale under the 14-day TTL (deterministic).
    _db.execute(
        "UPDATE sector_medians SET refreshed_at = ? WHERE sector = ?",
        ["2020-01-01T00:00:00+00:00", "Technology"],
    )
    assert medians.get_latest_sector_median("Technology", "ev_sales") is None
    assert medians.get_latest_sector_median("Absent", "ev_sales") is None


def test_sector_medians_latest_per_sector_and_timestamp(medians):
    medians.save_sector_medians_batch("2026-08-01T00:00:00+00:00", [
        {"sector": "Technology", "metric": "ev_sales",
         "median": 5.0, "p25": 2.0, "p75": 8.0, "sample_size": 60},
    ])
    medians.save_sector_medians_batch("2026-08-16T00:00:00+00:00", [
        {"sector": "Technology", "metric": "ev_sales",
         "median": 6.0, "p25": 3.0, "p75": 9.0, "sample_size": 61},
    ])
    latest = medians.list_latest_sector_medians("ev_sales")
    assert len(latest) == 1
    assert latest[0]["median"] == 6.0  # newest batch wins per sector

    assert medians.get_latest_refresh_timestamp() == "2026-08-16T00:00:00+00:00"


def test_sector_medians_empty_batch_writes_nothing(medians):
    assert medians.save_sector_medians_batch("2026-08-16T00:00:00+00:00", []) == 0
    assert medians.get_latest_refresh_timestamp() is None


# ── runner qual-cache rehydration fast path ──────────────────────────────────

def test_runner_rehydrate_qual_from_cache(tmp_db, qualitative):
    """The complacency runner's single-query fast path was raw sqlite3
    against RUN_ARCHIVE_PATH; it must now read through the dual-mode DB."""
    from src.research_ideas.complacency.runner import _rehydrate_qual_from_cache

    for ind, score in (("A1", 4), ("B2", 2)):
        qualitative.save_qualitative_score("NVDA", ind, score, 0.8, "s", [], "m")

    assessment = _rehydrate_qual_from_cache("NVDA")
    assert assessment is not None
    assert assessment.composite == 6
    assert assessment.max_possible == 10
    assert assessment.incomplete is True  # < 10 indicators

    assert _rehydrate_qual_from_cache("ABSENT") is None


# ── PG-compatible SQL shape ───────────────────────────────────────────────────

def test_save_sql_is_pg_compatible():
    """Every S1 upsert uses the ON CONFLICT(...) DO UPDATE SET form, which
    works on BOTH SQLite and Postgres; no SQLite-only INSERT OR REPLACE."""
    from app.backend.services import (
        sw46_storage, fundflow_storage, complacency_storage,
        qualitative_storage, hk50_storage, momentum_storage,
        contrarian_storage, sector_medians_storage,
    )
    from src.research_ideas.complacency import web_research

    upserts = [
        sw46_storage._SAVE_SQL,
        fundflow_storage._SAVE_SQL,
        complacency_storage._SAVE_SQL,
        qualitative_storage._SAVE_SQL,
        hk50_storage._SAVE_SQL,
        momentum_storage._SAVE_SQL,
        contrarian_storage._SAVE_IDEA_SQL,
        contrarian_storage._SAVE_SHORTLIST_SQL,
        sector_medians_storage._SAVE_SQL,
        web_research._CACHE_PUT_SQL,
    ]
    for sql in upserts:
        assert "INSERT OR REPLACE" not in sql
        assert "ON CONFLICT" in sql and "DO UPDATE SET" in sql
        assert "?" in sql  # placeholders survive to db._translate

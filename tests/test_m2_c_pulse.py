"""
tests/test_m2_c_pulse.py
========================
M2 Track C1 — GET /analysis/pulse (SSE two-beat instant recall).

Contract pinned here:
  * beat 1 (pulse_prior) is DB-only and renders the latest recap — action,
    price target, age, thesis — or a discovery-mode event when the ticker
    has no prior coverage;
  * beat 2 (pulse_delta) streams one freshness search anchored on the prior
    report (or a discovery brief), and every failure soft-fails into a
    well-formed event — the stream ALWAYS terminates with pulse_complete;
  * same-day repeats serve beat 2 from the complacency_web_research cache
    (kind='pulse') without re-searching;
  * concurrent pulses for one ticker are single-flighted (one search).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.data import db as _db
import app.backend.routes.analysis as A


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "pulse_test.db"))
    monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def pulse_env(tmp_db):
    from src.memory import report_recap
    from src.research_ideas.complacency import web_research
    report_recap._tables_ready_key = None    # re-create tables on the tmp DB
    web_research._tables_ready_key = None
    return {"search_calls": []}


def _seed_recap(ticker: str = "BABA", action: str = "SHORT") -> None:
    from src.memory import report_recap
    report_recap._ensure_table()          # lazy DDL — direct INSERT needs it
    _db.execute(
        "INSERT INTO report_recaps (ticker, run_id, run_at, price_at_run, "
        "final_action, signal_score, recap_json, recap_text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ticker.upper(), "run-prior-1", "2026-08-19T10:00:00+00:00",
            127.48, action, -6.0,
            json.dumps({"price_target": 134.76,
                        "catalysts": ["Q1 results"], "assumptions": ["a"]}),
            "Cloud growth decelerating; commerce margins under pressure.",
            "2026-08-19T10:05:00+00:00",
        ],
    )


def _fake_delta(ticker, prior, since_date=None, tavily_key=None,
                request_timeout_s=45.0, _calls=None):
    return {
        "material": True,
        "events": [{"headline": "Guidance cut", "date": "2026-08-20",
                    "relevance": "hurts thesis"}],
        "verdict": "Prior thesis at risk",
        "based_on_run": (prior or {}).get("run_id"),
        "prior_run_at": (prior or {}).get("run_at"),
    }


async def _drain(resp) -> list[tuple[str, dict]]:
    events = []
    async for chunk in resp.body_iterator:
        name, data = None, None
        for line in chunk.strip().splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((name, data))
    return events


def _patch_search(monkeypatch, calls: list):
    def fake(**kwargs):
        calls.append(kwargs)
        return _fake_delta(kwargs["ticker"], kwargs.get("prior"))
    monkeypatch.setattr(
        "src.memory.freshness.run_freshness_search", fake)


# ── beat 1 shape ──────────────────────────────────────────────────────────────

def test_beat1_renders_prior_recap(pulse_env, monkeypatch):
    _seed_recap()
    calls = pulse_env["search_calls"]
    _patch_search(monkeypatch, calls)

    resp = asyncio.run(A.pulse(request=None, ticker="BABA"))
    events = asyncio.run(_drain(resp))
    names = [n for n, _ in events]

    assert names[0] == "pulse_prior"
    assert names[-1] == "pulse_complete"
    prior = events[0][1]
    assert prior["covered"] is True
    assert prior["final_action"] == "SHORT"
    assert prior["price_target"] == 134.76
    assert prior["price_at_run"] == 127.48
    assert prior["run_id"] == "run-prior-1"
    assert prior["catalysts"] == ["Q1 results"]
    assert "Cloud growth decelerating" in prior["recap_text"]


def test_beat2_streams_freshness_delta(pulse_env, monkeypatch):
    _seed_recap()
    calls = pulse_env["search_calls"]
    _patch_search(monkeypatch, calls)

    resp = asyncio.run(A.pulse(request=None, ticker="BABA"))
    events = asyncio.run(_drain(resp))
    delta = dict(events)["pulse_delta"]

    assert delta["material"] is True
    assert delta["events"][0]["headline"] == "Guidance cut"
    assert delta["verdict"] == "Prior thesis at risk"
    assert delta["based_on_run"] == "run-prior-1"
    assert delta["from_cache"] is False
    assert len(calls) == 1
    assert calls[0]["ticker"] == "BABA"


# ── discovery mode ────────────────────────────────────────────────────────────

def test_no_prior_coverage_enters_discovery_mode(pulse_env, monkeypatch):
    calls = pulse_env["search_calls"]
    _patch_search(monkeypatch, calls)          # must NOT be called
    monkeypatch.setattr(
        "src.research_ideas.complacency.web_research.qwen_web_search",
        lambda *a, **k: "2026-08-19: New tariff probe opened.")

    resp = asyncio.run(A.pulse(request=None, ticker="NEWTICK"))
    events = asyncio.run(_drain(resp))
    names = [n for n, _ in events]

    prior = dict(events)["pulse_prior"]
    assert prior["covered"] is False
    assert "discovery" in prior["summary"].lower()

    delta = dict(events)["pulse_delta"]
    assert delta["discovery"] is True
    assert "tariff probe" in delta["brief"]
    assert names[-1] == "pulse_complete"
    assert calls == []                          # freshness search not used


# ── same-day cache + single-flight ────────────────────────────────────────────

def test_same_day_repeat_served_from_cache(pulse_env, monkeypatch):
    _seed_recap()
    calls = pulse_env["search_calls"]
    _patch_search(monkeypatch, calls)

    r1 = asyncio.run(A.pulse(request=None, ticker="BABA"))
    e1 = asyncio.run(_drain(r1))
    r2 = asyncio.run(A.pulse(request=None, ticker="BABA"))
    e2 = asyncio.run(_drain(r2))

    assert len(calls) == 1                      # second pulse skipped the search
    d1, d2 = dict(e1)["pulse_delta"], dict(e2)["pulse_delta"]
    assert d1["from_cache"] is False
    assert d2["from_cache"] is True
    assert d2["verdict"] == d1["verdict"]


def test_concurrent_pulses_single_flighted(pulse_env, monkeypatch):
    _seed_recap()
    calls = pulse_env["search_calls"]

    def slow_fake(**kwargs):
        import time
        time.sleep(0.05)                        # let the second caller queue up
        calls.append(kwargs)
        return _fake_delta(kwargs["ticker"], kwargs.get("prior"))

    monkeypatch.setattr(
        "src.memory.freshness.run_freshness_search", slow_fake)

    async def run_both():
        r1 = await A.pulse(request=None, ticker="BABA")
        r2 = await A.pulse(request=None, ticker="BABA")
        return await asyncio.gather(_drain(r1), _drain(r2))

    e1, e2 = asyncio.run(run_both())

    assert len(calls) == 1                      # ONE search for two callers
    assert dict(e1)["pulse_delta"]["verdict"] == "Prior thesis at risk"
    assert dict(e2)["pulse_delta"]["verdict"] == "Prior thesis at risk"
    # exactly one of the two ran the live search; the other read the cache
    from_cache = {dict(e1)["pulse_delta"]["from_cache"],
                  dict(e2)["pulse_delta"]["from_cache"]}
    assert from_cache == {True, False}


# ── soft-fail: the stream always terminates ───────────────────────────────────

def test_recap_failure_soft_fails_to_error_events(pulse_env, monkeypatch):
    def boom(ticker, max_age_days=None):
        raise RuntimeError("db offline")

    monkeypatch.setattr("src.memory.report_recap.get_recent_recap", boom)

    resp = asyncio.run(A.pulse(request=None, ticker="BABA"))
    events = asyncio.run(_drain(resp))
    names = [n for n, _ in events]

    assert names[-1] == "pulse_complete"        # stream still closes cleanly
    assert "pulse_error" in names
    assert "db offline" in dict(events)["pulse_error"]["error"]


def test_search_outage_still_emits_well_formed_delta(pulse_env, monkeypatch):
    """run_freshness_search is soft-fail by contract; when it returns the
    base shape (no results), beat 2 still renders a well-formed event."""
    _seed_recap()
    from src.memory import freshness

    monkeypatch.setattr(
        "src.memory.freshness.run_freshness_search",
        lambda **k: freshness.base_delta(k.get("prior")))

    resp = asyncio.run(A.pulse(request=None, ticker="BABA"))
    events = asyncio.run(_drain(resp))
    delta = dict(events)["pulse_delta"]

    assert delta["material"] is None
    assert delta["events"] == []
    assert delta["verdict"] == "check unavailable"
    assert dict(events)["pulse_complete"]["ticker"] == "BABA"

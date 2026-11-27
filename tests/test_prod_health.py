"""Roadmap #4 (2026-11-28): the daily production health report."""
from datetime import datetime, timedelta, timezone

import pytest

from src.monitoring import prod_health as ph


def _run(ticker, *, iv=10.0, fields=None, no_peer=False, fx_note="", weights=("EV/EBITDA",)):
    dr = {"base": {"intrinsic_value": iv, "effective_weights": [{"method": w, "value_key": w} for w in weights],
                   "forward_flags": []},
          "multiples_used": {"fields": fields or {}, "no_peer_multiples": no_peer}, "fx_note": fx_note}
    return {"run_id": ticker, "ticker": ticker, "run_at": "2026-11-27T02:06:00+00:00",
            "payload": {"data": {"dcf_range": {ticker: dr}}}}


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_clean_runs_are_ok():
    c = _by_id(ph.run_checks([_run("AAPL", fields={"pe": {"basis": "industry"}})]))
    assert c["engine_failures"]["status"] == ph.OK
    assert c["static_multiples"]["status"] == ph.OK
    assert c["static_fx"]["status"] == ph.OK


def test_static_multiples_warn_then_fail():
    one = _by_id(ph.run_checks([_run("Z74.SI", no_peer=True), _run("AAPL"), _run("MSFT"), _run("V")]))
    assert one["static_multiples"]["status"] == ph.WARN
    assert "Z74.SI" in one["static_multiples"]["items"][0]
    half = _by_id(ph.run_checks([_run("A", fields={"ev_ebitda": {"basis": "static"}}),
                                 _run("B", no_peer=True), _run("C")]))
    assert half["static_multiples"]["status"] == ph.FAIL


def test_engine_failure_and_static_fx():
    bad = _run("Q0F.SI", iv=None)
    fx = _run("5225.KL", fx_note="MYR->USD at 0.24 (static fallback rate -- live FX unavailable)")
    c = _by_id(ph.run_checks([bad, fx]))
    assert c["engine_failures"]["status"] == ph.FAIL and c["engine_failures"]["value"] == 1
    assert c["static_fx"]["status"] == ph.WARN


def test_missing_valuation_is_a_failure():
    r = {"run_id": "x", "ticker": "X", "run_at": "2026-11-27T00:00:00+00:00", "payload": {"data": {}}}
    assert _by_id(ph.run_checks([r]))["engine_failures"]["status"] == ph.FAIL


def test_queue_checks():
    assert ph.queue_checks(None)[0]["status"] == ph.UNKNOWN
    assert ph.queue_checks({"redis_ready": False})[0]["status"] == ph.FAIL
    assert ph.queue_checks({"redis_ready": True, "worker_health": ""})[0]["status"] == ph.FAIL
    q = {"redis_ready": True, "worker_health": "Nov-27 16:01 j_complete=3 j_failed=0 j_ongoing=0 queued=1",
         "in_progress": ["arq:in-progress:abc123", "arq:in-progress:sched:news_fast:2026-11-27-15m-56"]}
    c = _by_id(ph.queue_checks(q))
    assert c["worker"]["status"] == ph.OK
    assert c["queue_phantoms"]["status"] == ph.WARN and c["queue_phantoms"]["items"] == ["arq:in-progress:abc123"]


def test_scheduled_freshness(monkeypatch):
    from src.data import db
    now = datetime.now(timezone.utc)
    ages = {"t_ok": now - timedelta(hours=3), "t_warn": now - timedelta(hours=40), "t_fail": now - timedelta(hours=80)}
    monkeypatch.setattr(db, "query_one", lambda sql, *a: {"newest": ages[sql.split("FROM ")[1].split()[0]].isoformat()})
    rows = ph.scheduled_checks((("a", "t_ok", "c", 30, 54, "a"), ("b", "t_warn", "c", 30, 54, "b"),
                                ("c", "t_fail", "c", 30, 54, "c")))
    assert [r["status"] for r in rows] == [ph.OK, ph.WARN, ph.FAIL]


def test_a_broken_check_reports_unknown_not_silence():
    out = ph._safe("x", lambda: 1 / 0)
    assert out[0]["status"] == ph.UNKNOWN and "ZeroDivisionError" in out[0]["summary"]


def test_slack_only_on_problems(monkeypatch):
    sent = []

    class _R:
        status_code = 200
    import requests
    monkeypatch.setattr(requests, "post", lambda url, json, timeout: sent.append(json) or _R())
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.delenv("PROD_HEALTH_SLACK_ALWAYS", raising=False)
    ok = {"status": ph.OK, "generated_at": "t", "counts": {ph.OK: 1, ph.WARN: 0, ph.FAIL: 0, ph.UNKNOWN: 0},
          "checks": [{"id": "a", "status": ph.OK, "summary": "fine"}]}
    assert ph.post_to_slack(ok) is False and not sent
    bad = dict(ok, status=ph.WARN, checks=[{"id": "b", "status": ph.WARN, "summary": "stale", "items": ["HKSE"]}])
    assert ph.post_to_slack(bad) is True and "stale" in sent[0]["text"]


def test_scheduler_registers_the_report():
    from app.backend import scheduler_service as ss
    names = {s.name: s for s in ss._specs()} if hasattr(ss, "_specs") else None
    if names is None:
        import inspect
        assert '"prod_health"' in inspect.getsource(ss)
    else:
        assert names["prod_health"].task == "run_prod_health_task"
    from app.backend import worker
    assert worker.run_prod_health_task in worker.WorkerSettings.functions

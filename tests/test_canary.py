"""Roadmap #4 part 2 (2026-11-28): the nightly valuation canary."""
from datetime import datetime, timedelta, timezone

import pytest

from src.monitoring import canary as cn
from src.monitoring import prod_health as ph

SNAP = {"_meta": {"generated_at": "2026-11-27"},
        "AAPL": {"base_iv": 200.0, "projection": {"scenarios.base.intrinsic_value": 200.0, "wacc": 0.09}}}


def _replay_ok(iv=200.0, wacc=0.09):
    return lambda name, d: {"fixture": name, "ok": True, "ticker": name, "base_iv": iv,
                            "projection": {"scenarios.base.intrinsic_value": iv, "wacc": wacc},
                            "diagnostics": {"misses": []}}


@pytest.fixture
def snap(monkeypatch):
    from src.memory import golden_snapshots as gs
    monkeypatch.setattr(gs, "load", lambda: SNAP)


def test_a_reproduced_baseline_is_ok(snap):
    res = cn.golden_canary(["AAPL"], replay=_replay_ok())
    assert res["status"] == ph.OK and res["results"][0]["verdict"] == "ok"


def test_a_move_off_the_baseline_fails_with_the_iv_move(snap):
    res = cn.golden_canary(["AAPL"], replay=_replay_ok(iv=240.0))
    r = res["results"][0]
    assert res["status"] == ph.FAIL and r["verdict"] == "drift" and r["iv_move"] == pytest.approx(0.2)
    chk = cn._as_check("canary_golden", res)
    assert "AAPL: DRIFT" in chk["items"][0] and "+20.0%" in chk["items"][0]


def test_a_crashed_replay_is_an_error_not_silence(snap):
    res = cn.golden_canary(["AAPL"], replay=lambda n, d: {"fixture": n, "ok": False, "error": "ImportError: x"})
    assert res["status"] == ph.FAIL and res["results"][0]["verdict"] == "error"


def test_the_real_fixtures_and_snapshot_line_up():
    from src.memory import golden_capture as gc
    from src.memory import golden_snapshots as gs
    doc = gs.load()
    for name in gc.available_tickers():
        assert isinstance(doc[name]["base_iv"], (int, float)), name


def _row(**kw):
    base = {"run_id": "r", "ticker": "AAPL", "run_at": "2026-11-20T00:00:00", "market": "US", "profile": "P",
            "routing_winner": "w", "param_version": "v1", "calibration_version": "c1", "pt_calibration_version": "p1",
            "iv_base": 100.0, "engine_version": "pipeline 2.0|dcf 3|abc1234",
            "agent_rg_fy1_base": 0.05, "agent_em_fy1_base": 0.30}
    base.update(kw)
    return base


def test_explain_names_each_cause():
    assert cn.explain(_row(), _row(run_at="2026-11-27T00:00:00")) == []
    assert cn.explain(_row(), _row(engine_version="pipeline 2.0|dcf 3|def5678"))[0].startswith("deploy abc1234")
    assert any("owner constants" in w for w in cn.explain(_row(), _row(param_version="v2")))
    assert any("agent FY+1 growth" in w for w in cn.explain(_row(), _row(agent_rg_fy1_base=0.09)))
    assert cn.explain(_row(), _row(agent_rg_fy1_base=0.055)) == []                # under 2pp is noise
    assert any("comps refreshed" in w for w in
               cn.explain(_row(), _row(run_at="2026-11-27T00:00:00"), "2026-11-21T01:00:00+00:00"))


def test_live_drift_lists_only_the_unexplained(monkeypatch):
    from src.data import db
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cur = [_row(ticker="AAPL", run_at=now.isoformat(), iv_base=130.0),                       # +30%, nothing changed
           _row(ticker="MSFT", run_at=now.isoformat(), iv_base=130.0, param_version="v2"),   # +30%, constants moved
           _row(ticker="V", run_at=now.isoformat(), iv_base=105.0)]                          # +5%, under threshold
    prev = {t: _row(ticker=t, run_at=(now - timedelta(days=3)).isoformat()) for t in ("AAPL", "MSFT", "V")}
    monkeypatch.setattr(db, "query", lambda sql, p=None: cur)
    monkeypatch.setattr(db, "query_one", lambda sql, p=None: prev[p[0]])
    res = cn.live_drift(comps_at=lambda m: None)
    assert res["checked"] == 3 and res["status"] == ph.WARN
    assert len(res["unexplained"]) == 1 and res["unexplained"][0].startswith("AAPL")
    assert res["explained"][0].startswith("MSFT") and "owner constants" in res["explained"][0]


def test_a_move_with_no_recorded_commit_is_unattributable_not_a_finding(monkeypatch):
    from src.data import db
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cur = [_row(run_at=now.isoformat(), iv_base=150.0, engine_version=None)]
    monkeypatch.setattr(db, "query", lambda sql, p=None: cur)
    monkeypatch.setattr(db, "query_one", lambda sql, p=None: _row(engine_version=None))
    res = cn.live_drift(comps_at=lambda m: None)
    assert res["status"] == ph.OK and not res["unexplained"] and len(res["unattributable"]) == 1


def test_scheduler_and_worker_register_the_canary():
    import inspect
    from app.backend import scheduler_service as ss
    from app.backend import worker
    assert '"run_canary_task"' in inspect.getsource(ss)
    assert worker.run_canary_task in worker.WorkerSettings.functions
    assert ss.CANARY_FIRE_HOUR_UTC < ss.PROD_HEALTH_FIRE_HOUR_UTC      # the report reads the canary's row


def test_health_report_reads_the_latest_canary(monkeypatch):
    from src.data import db
    monkeypatch.setattr(cn, "_ensure", lambda: None)
    monkeypatch.setattr(db, "query_one", lambda sql, p=None: None)
    assert cn.latest_checks()[0]["status"] == ph.WARN
    old = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
    monkeypatch.setattr(db, "query_one", lambda sql, p=None: {"generated_at": old, "report_json": "{}"})
    assert cn.latest_checks()[0]["status"] == ph.FAIL
    fresh = datetime.now(timezone.utc).isoformat()
    rep = '{"checks": [{"id": "canary_golden", "status": "ok", "summary": "s", "detail": {"x": 1}}]}'
    monkeypatch.setattr(db, "query_one", lambda sql, p=None: {"generated_at": fresh, "report_json": rep})
    got = cn.latest_checks()
    assert got == [{"id": "canary_golden", "status": "ok", "summary": "s"}]


def test_replay_clears_operator_feature_flags(monkeypatch):
    """2026-11-28: prod's FEATURE_RESOURCE_HOLDCO_* flags moved BN4.SI / U96.SI off the pinned baseline and the
    canary called a deliberate setting drift. A replay runs every FEATURE_* flag at its code default, and the
    caller's environment comes back intact afterwards."""
    import os
    from src.memory import golden_capture as gc
    monkeypatch.setenv("FEATURE_RESOURCE_HOLDCO_MAP_V2", "true")
    monkeypatch.setenv("FEATURE_RESOURCE_HOLDCO_TICKERS", "BN4.SI,U96.SI")
    with gc.pinned_env():
        assert not [k for k in os.environ if k.startswith(gc.FLAG_ENV_PREFIX) and k not in gc.PINNED_ENV]
    assert os.environ["FEATURE_RESOURCE_HOLDCO_MAP_V2"] == "true"
    assert os.environ["FEATURE_RESOURCE_HOLDCO_TICKERS"] == "BN4.SI,U96.SI"

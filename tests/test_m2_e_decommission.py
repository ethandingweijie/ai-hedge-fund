"""M2 Track E — decommission regression tests.

The investor committee (12 personas + debate round) is decommissioned, not
dormant. These tests pin the removal so a partial revert cannot quietly
re-introduce the committee surface:

- POST /analysis/run ignores a legacy ``agents`` body key (old frontends).
- The arq worker ACCEPTS the legacy ``selected_agents`` kwarg carried by
  jobs enqueued before the deploy, but never forwards it to the pipeline.
- The dedup slot released by the worker is per-ticker even when the legacy
  kwarg is present.
- The pipeline module exposes no investor/debate surface, and the deleted
  modules stay deleted.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest

import app.backend.routes.analysis as A
from app.backend import worker
from app.backend.services import queue_client


# ── API ignores the legacy agents body key ────────────────────────────────────

def _parse_sse(chunk: str):
    name, data = None, None
    for line in chunk.strip().splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            import json as _json
            data = _json.loads(line[6:])
    return name, data


def _drain(body_iterator) -> list:
    async def _collect():
        return [c async for c in body_iterator]
    return asyncio.run(_collect())


def test_run_endpoint_ignores_legacy_agents_body_key(monkeypatch):
    """An older frontend posting {"agents": [...]} must get the same per-ticker
    behaviour as a new client — the cached-run lookup is agent-agnostic."""
    calls = []

    def fake_cached(ticker, within_minutes=None):
        calls.append((ticker, within_minutes))
        return {"run_id": "cached-1", "ticker": ticker,
                "run_at": "2026-08-20T00:00:00"}

    monkeypatch.setattr(A.analysis_service, "get_cached_run", fake_cached)
    monkeypatch.setattr(A, "_get_user", lambda request, db: None)

    resp = asyncio.run(A.run_analysis(
        {"ticker": "MSFT", "model": "m",
         "agents": ["graham", "burry"]},          # legacy key — ignored
        request=None, db=None))

    assert resp.media_type == "text/event-stream"
    chunks = _drain(resp.body_iterator)
    events = [_parse_sse(c) for c in chunks]
    names = [n for n, _ in events]
    assert "cached" in names                      # served from the per-ticker cache
    cached_evt = dict(events)["cached"]
    assert cached_evt["run_id"] == "cached-1"
    # the lookup carried ticker + recency window ONLY — no agent dimension
    assert calls == [("MSFT", 30)]


# ── worker tolerates pre-deploy enqueued jobs (legacy kwarg) ─────────────────

def test_worker_accepts_legacy_kwarg_but_never_forwards_it(monkeypatch):
    captured = {}

    async def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return kwargs.get("run_id"), {}

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline",
        fake_pipeline)

    out = asyncio.run(worker.run_analysis_pipeline_task(
        {}, ticker="msft", model_name="m", api_keys={},
        selected_agents=["warren_buffett_agent"],   # pre-deploy enqueue payload
        run_id="run-legacy"))

    assert out == {"run_id": "run-legacy", "ticker": "msft", "ok": True}
    assert "selected_agents" not in captured        # never forwarded
    assert captured["ticker"] == "msft"


def test_worker_releases_per_ticker_key_despite_legacy_kwarg(monkeypatch):
    class _Tracker:
        def __init__(self):
            self.deleted = []

        async def delete(self, key):
            self.deleted.append(key)

    tracker = _Tracker()

    async def _get_redis():
        return tracker

    monkeypatch.setattr(
        "app.backend.services.queue_client.get_redis", _get_redis)

    async def fake_pipeline(**kwargs):
        return kwargs.get("run_id"), {}

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline",
        fake_pipeline)

    asyncio.run(worker.run_analysis_pipeline_task(
        {}, ticker="baba", model_name="m", api_keys={},
        selected_agents=["a", "b", "c"], run_id="run-x"))

    assert tracker.deleted == ["analysis_dedup:baba"]


# ── committee surface stays removed ──────────────────────────────────────────

def test_pipeline_module_exposes_no_committee_surface():
    import src.pipeline as P

    for attr in ("_resolve_investor_panel", "_run_investor_agents_parallel",
                 "_DEFAULT_INVESTOR_SIX", "_investor_max_workers",
                 "PIPELINE_INVESTOR_PERSONAS"):
        assert not hasattr(P, attr), f"src.pipeline.{attr} resurrected"

    sig = inspect.signature(P.run_advanced_pipeline)
    assert "selected_agents" not in sig.parameters


def test_deleted_committee_modules_stay_deleted():
    for mod in ("src.pipeline_investors", "src.agents.analysis.debate_round"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_dedup_key_is_per_ticker():
    assert queue_client.build_dedup_key("BABA") == "BABA"
    assert queue_client.build_dedup_key("0700.HK") == "0700.HK"

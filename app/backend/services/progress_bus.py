"""
app/backend/services/progress_bus.py
=====================================
Run-progress event bus bridging task executors and SSE endpoints.

Dual-mode:
- Redis available → events go through Redis pub/sub + a trimmed replay
  buffer, so the executor and the streaming endpoint can live in
  DIFFERENT processes (web vs worker) or different replicas.
- Redis unavailable → pure in-process hub (local dev, single instance).

Keys (Redis):
- progress:{run_id}          pub/sub channel of live events (JSON)
- progress_buf:{run_id}      LIST, last 200 events for late subscribers (TTL 1h)
- livestatus:{ticker}        HASH phase->event JSON + __latest__ (TTL 10 min)

Usage (publisher, e.g. worker or in-process runner):
    await progress_bus.publish_event(run_id, {"phase": ..., ...})
    await progress_bus.set_phase_event(ticker, phase, event)

Usage (subscriber, e.g. SSE endpoint):
    async for event in progress_bus.iter_events(run_id):
        yield to_sse(event)
        if is_terminal(event): break
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import AsyncIterator, Optional

from app.backend.services.redis_client import get_redis, redis_ready

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "progress:"
_BUFFER_PREFIX = "progress_buf:"
_LIVE_PREFIX = "livestatus:"
_BUFFER_MAX = 200
_BUFFER_TTL = 3600          # 1 h — long enough for reconnects
_LIVE_TTL = 600             # 10 min — matches the old 120s cleanup + grace

# ── In-process fallback state ─────────────────────────────────────────────────
_local_hubs: dict[str, set[asyncio.Queue]] = {}
_local_buf: dict[str, deque] = {}
_local_live: dict[str, dict] = {}


def _encode(event: dict) -> str:
    return json.dumps(event, default=str)


def _decode(raw) -> Optional[dict]:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


# ── Publish ───────────────────────────────────────────────────────────────────

async def publish_event(run_id: str, event: dict) -> None:
    """Publish one progress event for a run."""
    payload = _encode(event)

    # Local hub always (harmless when queue mode is active; needed otherwise)
    buf = _local_buf.setdefault(run_id, deque(maxlen=_BUFFER_MAX))
    buf.append(payload)
    for q in list(_local_hubs.get(run_id, ())):
        try:
            q.put_nowait(payload)
        except Exception:
            pass

    if await redis_ready():
        try:
            r = await get_redis()
            pipe = r.pipeline()
            pipe.publish(_CHANNEL_PREFIX + run_id, payload)
            pipe.rpush(_BUFFER_PREFIX + run_id, payload)
            pipe.ltrim(_BUFFER_PREFIX + run_id, -_BUFFER_MAX, -1)
            pipe.expire(_BUFFER_PREFIX + run_id, _BUFFER_TTL)
            await pipe.execute()
        except Exception as exc:
            logger.warning("progress_bus: redis publish failed (%s) — local only", exc)


async def set_phase_event(ticker: str, phase: str, event: dict) -> None:
    """Store the latest event per phase for a ticker (status endpoint + reconnects)."""
    payload = _encode(event)
    key = ticker.upper()

    local = _local_live.setdefault(key, {})
    local[phase] = payload
    local["__latest__"] = payload

    if await redis_ready():
        try:
            r = await get_redis()
            live_key = _LIVE_PREFIX + key
            pipe = r.pipeline()
            pipe.hset(live_key, phase, payload)
            pipe.hset(live_key, "__latest__", payload)
            pipe.expire(live_key, _LIVE_TTL)
            await pipe.execute()
        except Exception as exc:
            logger.warning("progress_bus: redis hset failed (%s) — local only", exc)


# ── Subscribe / read ──────────────────────────────────────────────────────────

async def iter_events(run_id: str) -> AsyncIterator[dict]:
    """Yield events for a run: buffered (replay) first, then live.

    Never terminates on its own — the caller breaks when it sees a
    terminal event. On Redis failure mid-stream the iterator ends.
    """
    if await redis_ready():
        async for event in _iter_redis(run_id):
            yield event
        return

    # In-process mode
    q: asyncio.Queue = asyncio.Queue()
    _local_hubs.setdefault(run_id, set()).add(q)
    try:
        for payload in list(_local_buf.get(run_id, ())):
            event = _decode(payload)
            if event is not None:
                yield event
        while True:
            payload = await q.get()
            event = _decode(payload)
            if event is not None:
                yield event
    finally:
        hub = _local_hubs.get(run_id)
        if hub is not None:
            hub.discard(q)
            if not hub:
                _local_hubs.pop(run_id, None)


async def _iter_redis(run_id: str) -> AsyncIterator[dict]:
    r = await get_redis()
    pubsub = r.pubsub()
    seen: set[str] = set()
    try:
        await pubsub.subscribe(_CHANNEL_PREFIX + run_id)
        # Replay buffered events (published before we subscribed)
        raw_buf = await r.lrange(_BUFFER_PREFIX + run_id, 0, -1)
        for raw in raw_buf:
            seen.add(raw if isinstance(raw, str) else raw.decode())
            event = _decode(raw)
            if event is not None:
                yield event
        # Live events
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
            if msg is None:
                continue  # caller decides heartbeats/termination
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            key = data if isinstance(data, str) else (data.decode() if data else "")
            if key in seen:
                continue
            seen.add(key)
            event = _decode(data)
            if event is not None:
                yield event
    finally:
        try:
            await pubsub.unsubscribe(_CHANNEL_PREFIX + run_id)
            await pubsub.aclose()
        except Exception:
            pass


async def get_phase_map(ticker: str) -> dict:
    """All stored phase events for a ticker: {phase: event, '__latest__': event}."""
    key = ticker.upper()
    if await redis_ready():
        try:
            r = await get_redis()
            raw = await r.hgetall(_LIVE_PREFIX + key)
            if raw:
                return {phase: _decode(payload) for phase, payload in raw.items()}
        except Exception:
            pass
        return {}
    local = _local_live.get(key, {})
    return {phase: _decode(payload) for phase, payload in local.items()}


async def get_latest_phase(ticker: str) -> Optional[dict]:
    """The most recent phase event for a ticker, or None."""
    phase_map = await get_phase_map(ticker)
    return phase_map.get("__latest__")


async def clear_ticker(ticker: str) -> None:
    """Drop stored phase state for a ticker (end-of-run cleanup)."""
    key = ticker.upper()
    _local_live.pop(key, None)
    if await redis_ready():
        try:
            r = await get_redis()
            await r.delete(_LIVE_PREFIX + key)
        except Exception:
            pass


def drop_run_buffer(run_id: str) -> None:
    """Free in-process buffers for a finished run (Redis TTLs handle themselves)."""
    _local_buf.pop(run_id, None)
    _local_hubs.pop(run_id, None)

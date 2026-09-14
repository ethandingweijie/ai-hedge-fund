"""Fan-out bus for news items, so an open page learns without polling.

Deliberately the same shape as progress_bus.py -- Redis pub/sub with a trimmed
replay buffer, and a pure in-process hub when Redis is absent. That is not
copy-paste for its own sake: the dual mode is what lets the worker publish and
a web replica subscribe in production, while local dev with no Redis still
works end to end.

One difference from progress_bus, and it is the reason this is a separate
module rather than a parameter: progress is keyed by RUN and has exactly one
subscriber shape, whereas news is keyed by TICKER and a single page subscribes
to many at once (a watchlist). `iter_items` therefore fans in across channels.

Keys:
  news:{TICKER}       pub/sub channel of item JSON
  news_buf:{TICKER}   LIST, last 50 items for a late subscriber (TTL 2 h)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import AsyncIterator, Iterable, Optional

from app.backend.services.redis_client import get_redis, redis_ready

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "news:"
_BUFFER_PREFIX = "news_buf:"
#: Smaller than progress's 200 — a reconnecting client wants the last few
#: headlines, not an hour of them; the store holds the history.
_BUFFER_MAX = 50
_BUFFER_TTL = 7200          # 2 h

_local_hubs: dict[str, set[asyncio.Queue]] = {}
_local_buf: dict[str, deque] = {}
#: Last publish time per ticker, so an idle ticker's buffer can be dropped.
#: The Redis buffer expires on its own (_BUFFER_TTL); without this the
#: in-process one only ever went away on a restart.
_local_seen: dict[str, float] = {}
_LOCAL_BUFFER_TTL_S = 7200.0        # matches the Redis TTL


def _buffer_local(key: str, payload: str) -> None:
    """Keep `payload` in the in-process replay buffer.

    Called when Redis is absent, and also when a publish to a supposedly-live
    Redis raises -- in both cases nothing else is holding replay, so the local
    copy is the only one. In the healthy case it is never called, which is the
    point: progress_bus keeps both unconditionally and that meant a 50-item
    deque per ticker in every process that nothing ever read.
    """
    _local_buf.setdefault(key, deque(maxlen=_BUFFER_MAX)).append(payload)
    _local_seen[key] = time.monotonic()
    _evict_idle_buffers()


def _evict_idle_buffers() -> None:
    cutoff = time.monotonic() - _LOCAL_BUFFER_TTL_S
    for key in [k for k, seen in _local_seen.items() if seen < cutoff]:
        # Never drop a buffer someone is still subscribed to.
        if not _local_hubs.get(key):
            _local_buf.pop(key, None)
            _local_seen.pop(key, None)


def _encode(item: dict) -> str:
    return json.dumps(item, default=str)


def _decode(raw) -> Optional[dict]:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:                                      # noqa: BLE001
        return None


def _norm(ticker: str) -> str:
    return (ticker or "").strip().upper()


async def publish(ticker: str, item: dict) -> None:
    """Announce one NEW item. Never raises.

    Only genuinely new items should reach here -- news_store.upsert_items
    returns the new-id count precisely so a corrected headline is not
    re-announced to a page that already shows it.
    """
    key = _norm(ticker)
    if not key:
        return
    payload = _encode(item)
    live = await redis_ready()

    # Subscribers in THIS process are always fed directly -- that is not a
    # buffer, it is the delivery path for anyone already listening here.
    for q in list(_local_hubs.get(key, ())):
        try:
            q.put_nowait(payload)
        except Exception:                                  # noqa: BLE001
            pass

    # The replay BUFFER is only kept when Redis is not serving it.
    if not live:
        _buffer_local(key, payload)
    else:
        try:
            r = await get_redis()
            pipe = r.pipeline()
            pipe.publish(_CHANNEL_PREFIX + key, payload)
            pipe.rpush(_BUFFER_PREFIX + key, payload)
            pipe.ltrim(_BUFFER_PREFIX + key, -_BUFFER_MAX, -1)
            pipe.expire(_BUFFER_PREFIX + key, _BUFFER_TTL)
            await pipe.execute()
        except Exception as exc:                           # noqa: BLE001
            # redis_ready() said yes but the write lost the connection, so
            # Redis is NOT holding replay after all and "local only" has to be
            # true rather than just logged.
            logger.warning("news_bus: redis publish failed (%s) — local only", exc)
            _buffer_local(key, payload)


async def publish_many(items: Iterable[dict]) -> int:
    """Publish a batch, returning how many were sent."""
    n = 0
    for item in items:
        tk = item.get("ticker")
        if tk:
            await publish(tk, item)
            n += 1
    return n


async def iter_items(tickers: Iterable[str], *,
                     replay: bool = False) -> AsyncIterator[dict]:
    """Yield items for any of `tickers` as they arrive.

    `replay` defaults to FALSE, the opposite of progress_bus. A progress
    subscriber joining late needs the phases it missed to rebuild the bar; a
    news page has already loaded its snapshot from the store and would render
    the same headlines twice. Replay is opt-in for a client that reconnects
    without re-fetching.

    Never terminates on its own -- the caller decides when to stop.
    """
    keys = [k for k in (_norm(t) for t in tickers) if k]
    if not keys:
        return
    if await redis_ready():
        async for item in _iter_redis(keys, replay=replay):
            yield item
        return

    # In-process: one queue fed by every requested ticker's hub.
    q: asyncio.Queue = asyncio.Queue()
    for key in keys:
        _local_hubs.setdefault(key, set()).add(q)
    try:
        if replay:
            for key in keys:
                for payload in list(_local_buf.get(key, ())):
                    item = _decode(payload)
                    if item is not None:
                        yield item
        while True:
            payload = await q.get()
            item = _decode(payload)
            if item is not None:
                yield item
    finally:
        for key in keys:
            hub = _local_hubs.get(key)
            if hub is not None:
                hub.discard(q)
                if not hub:
                    _local_hubs.pop(key, None)


async def _iter_redis(keys: list[str], *,
                      replay: bool) -> AsyncIterator[dict]:
    r = await get_redis()
    pubsub = r.pubsub()
    seen: set[str] = set()
    channels = [_CHANNEL_PREFIX + k for k in keys]
    try:
        await pubsub.subscribe(*channels)
        if replay:
            for key in keys:
                try:
                    raw_buf = await r.lrange(_BUFFER_PREFIX + key, 0, -1)
                except Exception:                          # noqa: BLE001
                    continue
                for raw in raw_buf:
                    seen.add(raw if isinstance(raw, str) else raw.decode())
                    item = _decode(raw)
                    if item is not None:
                        yield item
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True,
                                           timeout=30.0)
            if msg is None or msg.get("type") != "message":
                continue           # caller owns heartbeats and termination
            data = msg.get("data")
            marker = data if isinstance(data, str) else (
                data.decode() if data else "")
            if marker in seen:
                continue
            seen.add(marker)
            item = _decode(data)
            if item is not None:
                yield item
    finally:
        try:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()
        except Exception:                                  # noqa: BLE001
            pass


def reset_local_state() -> None:
    """Drop in-process hubs and buffers. For tests only."""
    _local_hubs.clear()
    _local_buf.clear()
    _local_seen.clear()

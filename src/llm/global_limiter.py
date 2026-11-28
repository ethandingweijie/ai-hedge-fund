"""Global, self-tuning rate limiter for Qwen calls, shared by every process through Redis (owner, 2026-11-28, item 3).

Why: the old limiter (src/research_ideas/complacency/qwen_throttle.py) was a token bucket INSIDE each process -- all
jobs in one worker shared 0.5 calls/s, and two workers held two buckets, so the combined rate DashScope saw was
unknown. Here one bucket per model lives in Redis; every process and every replica draws from it.

Two budgets per model:
  * REQUESTS -- DashScope's RPM is dynamic, so the rate is LEARNED (AIMD): start at LLM_RATE_START req/s, add
    LLM_RATE_STEP every LLM_RATE_STEP_EVERY successful calls up to LLM_RATE_MAX, HALVE on a 429 (floor LLM_RATE_MIN) and
    pause every process for the 429's Retry-After (default LLM_COOLDOWN_S).
  * TOKENS -- the fixed TPM quota (qwen3.8-flash: 2,500,000, owner 2026-11-28). Calls are charged their ACTUAL tokens
    after the response (the cost meter's hook); the balance may go negative, which makes the next callers wait.
Priority: batch work (scheduled refreshes, backfills, sweeps) may only take a request token while the bucket holds more
than LLM_BATCH_RESERVE of its burst -- the rest is kept for customers' interactive runs.

Fallback: no Redis, or any Redis error -> the caller uses the process-local bucket exactly as before. A limiter
outage never stops a run. Kill switch: GLOBAL_LLM_LIMITER=off.
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: "interactive" (a customer is waiting) or "batch" (scheduled / backfill). Set by the worker per job.
PRIORITY: contextvars.ContextVar[str] = contextvars.ContextVar("llm_priority", default="interactive")

#: TPM quota per model (owner-provided). Unknown models get no token budget (requests only).
TPM_QUOTA = {"qwen3.8-flash": 2_500_000}


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def cfg() -> dict:
    return {
        "rate_start": _f("LLM_RATE_START", 2.0), "rate_min": _f("LLM_RATE_MIN", 0.5),
        "rate_max": _f("LLM_RATE_MAX", 20.0), "rate_step": _f("LLM_RATE_STEP", 0.25),
        "step_every": int(_f("LLM_RATE_STEP_EVERY", 20)), "burst_s": _f("LLM_BURST_SECONDS", 2.0),
        "cooldown_s": _f("LLM_COOLDOWN_S", 5.0), "batch_reserve": _f("LLM_BATCH_RESERVE", 0.3),
        "tpm_headroom": _f("LLM_TPM_HEADROOM", 0.85),
    }


def enabled() -> bool:
    return os.environ.get("GLOBAL_LLM_LIMITER", "on").strip().lower() not in ("0", "off", "false", "no")


# ── Redis (sync client; LLM calls run in worker threads) ───────────────────

_CLIENT: dict = {"c": None, "down_until": 0.0}
_LOCK = threading.Lock()


def _redis():
    now = time.monotonic()
    if now < _CLIENT["down_until"]:
        return None
    with _LOCK:
        if _CLIENT["c"] is None:
            url = os.environ.get("REDIS_URL")
            if not url:
                _CLIENT["down_until"] = now + 300
                return None
            try:
                import redis
                c = redis.Redis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)
                c.ping()
                _CLIENT["c"] = c
            except Exception as exc:                        # noqa: BLE001
                logger.warning("global limiter: Redis unavailable (%s) -- local fallback for 60 s", exc)
                _CLIENT["down_until"] = now + 60
                return None
        return _CLIENT["c"]


def _mark_down(exc) -> None:
    logger.warning("global limiter: Redis error (%s) -- local fallback for 60 s", exc)
    with _LOCK:
        _CLIENT["c"] = None
        _CLIENT["down_until"] = time.monotonic() + 60


# KEYS: state hash, cooldown key. ARGV: now, cost, min_fill_fraction, rate_start, burst_s, tpm_per_s, tpm_burst.
# Returns wait seconds (0 = granted).
_ACQUIRE = """
local h = KEYS[1]
local now = tonumber(ARGV[1]); local cost = tonumber(ARGV[2]); local minfill = tonumber(ARGV[3])
local cd = redis.call('PTTL', KEYS[2])
if cd and cd > 0 then return tostring(cd / 1000.0) end
local st = redis.call('HMGET', h, 'rate', 'req', 'tok', 'ts')
local rate = tonumber(st[1]) or tonumber(ARGV[4])
local burst = math.max(1.0, rate * tonumber(ARGV[5]))
local req = tonumber(st[2]) or burst
local tpm_s = tonumber(ARGV[6]); local tburst = tonumber(ARGV[7])
local tok = tonumber(st[3]) or tburst
local ts = tonumber(st[4]) or now
local dt = math.max(0, now - ts)
req = math.min(burst, req + dt * rate)
if tpm_s > 0 then tok = math.min(tburst, tok + dt * tpm_s) end
local wait = 0
if req < cost + minfill * burst then wait = (cost + minfill * burst - req) / rate end
if tpm_s > 0 and tok < 0 then wait = math.max(wait, -tok / tpm_s) end
if wait <= 0 then req = req - cost end
redis.call('HSET', h, 'rate', rate, 'req', req, 'tok', tok, 'ts', now)
redis.call('EXPIRE', h, 3600)
return tostring(wait)
"""

# KEYS: state hash, cooldown key, success counter. ARGV: event ('ok'|'429'), tokens, rate_start, rate_min, rate_max,
# rate_step, step_every, cooldown_ms.
_FEEDBACK = """
local h = KEYS[1]
local rate = tonumber(redis.call('HGET', h, 'rate')) or tonumber(ARGV[3])
if ARGV[1] == '429' then
  rate = math.max(tonumber(ARGV[4]), rate / 2.0)
  redis.call('SET', KEYS[2], '1', 'PX', tonumber(ARGV[8]))
  redis.call('SET', KEYS[3], 0)
  redis.call('HINCRBY', h, 'n429', 1)
else
  local n = redis.call('INCR', KEYS[3])
  if n >= tonumber(ARGV[7]) then
    rate = math.min(tonumber(ARGV[5]), rate + tonumber(ARGV[6]))
    redis.call('SET', KEYS[3], 0)
  end
  local t = tonumber(ARGV[2])
  if t and t > 0 then redis.call('HINCRBYFLOAT', h, 'tok', -t) end
  redis.call('HINCRBY', h, 'nok', 1)
end
redis.call('HSET', h, 'rate', rate)
redis.call('EXPIRE', h, 3600)
return tostring(rate)
"""


def _keys(model: str) -> tuple[str, str, str]:
    base = f"llm:limiter:{model}"
    return base, base + ":cooldown", base + ":ok"


def _tpm(model: str) -> tuple[float, float]:
    q = TPM_QUOTA.get(model)
    if not q:
        return 0.0, 0.0
    per_min = q * cfg()["tpm_headroom"]
    return per_min / 60.0, per_min / 4.0            # refill per second; burst = 15 s of quota


def try_acquire(model: str, cost: float = 1.0, max_wait_s: float = 120.0) -> Optional[float]:
    """Block until the shared bucket grants `cost` requests for `model`; returns seconds waited.
    None = the global limiter is unavailable (caller falls back to the local bucket)."""
    if not enabled():
        return None
    r = _redis()
    if r is None:
        return None
    c = cfg()
    minfill = c["batch_reserve"] if PRIORITY.get() == "batch" else 0.0
    tpm_s, tburst = _tpm(model)
    h, cd, _ = _keys(model)
    waited, start = 0.0, time.monotonic()
    try:
        script = r.register_script(_ACQUIRE)
        while True:
            w = float(script(keys=[h, cd], args=[time.time(), cost, minfill, c["rate_start"], c["burst_s"],
                                                 tpm_s, tburst]))
            if w <= 0:
                return waited
            if waited + w > max_wait_s:
                logger.warning("global limiter: %s waited %.0fs -- proceeding", model, waited)
                return waited
            time.sleep(min(w, 5.0))
            waited = time.monotonic() - start
    except Exception as exc:                                # noqa: BLE001
        _mark_down(exc)
        return None


def feedback(model: Optional[str], status: int, tokens: float = 0.0,
             retry_after_s: Optional[float] = None) -> None:
    """Report one finished call: success (adds rate, charges tokens) or 429 (halves rate, pauses everyone)."""
    if not enabled() or not model:
        return
    r = _redis()
    if r is None:
        return
    c = cfg()
    ev = "429" if status == 429 else ("ok" if 200 <= status < 300 else None)
    if ev is None:
        return
    h, cd, okk = _keys(model)
    cool_ms = int(1000 * (retry_after_s if retry_after_s and retry_after_s > 0 else c["cooldown_s"]))
    try:
        r.register_script(_FEEDBACK)(keys=[h, cd, okk], args=[ev, float(tokens or 0.0), c["rate_start"],
                                                              c["rate_min"], c["rate_max"], c["rate_step"],
                                                              c["step_every"], cool_ms])
    except Exception as exc:                                # noqa: BLE001
        _mark_down(exc)


def snapshot(model: str = "qwen3.8-flash") -> dict:
    """For /admin/diag: the learned rate, buckets, 429 / success counts, cooldown."""
    r = _redis()
    if r is None:
        return {"enabled": enabled(), "redis": False}
    h, cd, _ = _keys(model)
    try:
        st = r.hgetall(h) or {}
        d = {k.decode(): v.decode() for k, v in st.items()}
        return {"enabled": enabled(), "redis": True, "model": model, "rate_rps": d.get("rate"),
                "request_tokens": d.get("req"), "tpm_tokens": d.get("tok"), "ok": d.get("nok"),
                "429": d.get("n429"), "cooldown_ms": r.pttl(cd), "tpm_quota": TPM_QUOTA.get(model)}
    except Exception as exc:                                # noqa: BLE001
        return {"enabled": enabled(), "redis": False, "error": str(exc)[:200]}

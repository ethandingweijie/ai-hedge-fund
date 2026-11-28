"""
src/llm/cost_meter.py
=====================
What each run actually costs in LLM tokens and web searches (pricing review, 2026-11-28).

Why: credit prices (app/backend/services/billing.py CREDIT_COSTS) were set from ESTIMATES that could be 2-3x off.
This records the real usage of every run so the credit weights and the plan margins can be checked against
measured numbers before BILLING_ENFORCED goes on.

How: one hook at the HTTP layer instead of at the ~40 call sites. Every LLM SDK the pipeline uses (anthropic,
openai -> DashScope / DeepSeek, langchain on top of both, google-genai) sends through httpx; a few direct calls use
requests. `install()` wraps httpx.Client.send / httpx.AsyncClient.send / requests.Session.send; for a request to a
known LLM or search host it reads the provider's own usage block from the response (streamed responses are teed:
the stream is passed through untouched and only lines mentioning usage are kept). Anything else passes straight
through. The hook never raises: a parse failure records a call with no usage, it never breaks a request.

Attribution: `metered_run(...)` sets a ContextVar holding the run's meter. The pipeline fans out through
run_config.spawn / run_config.submit, which copy the context, so every worker thread of that run reports to the
same meter. Calls made outside a run (scheduled screens, what-if, chat) accumulate per process and are flushed to
`llm_spend_daily`, so total spend is visible too.

Storage keeps RAW TOKENS, not dollars: `cost_of` prices them on read from PRICES, so a corrected price re-prices
history and an unknown model shows up as "unpriced" instead of a silent $0.
"""
from __future__ import annotations

import contextlib
import contextvars
import gzip
import json
import logging
import os
import sys
import threading
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ── prices (USD) ───────────────────────────────────────────────────────────
#: per 1M tokens: input, output, cache_read, cache_write. Looked up by longest model-name prefix.
#: Sources, 2026-11-28: Anthropic -- claude-api skill model table (Sonnet 4.6 $3/$15; cache read 0.1x, 5-min
#: write 1.25x). Alibaba Model Studio international (Singapore) price page -- qwen3.8-flash $0.15/$0.47,
#: qwen3.6-plus $0.50/$3 (<=256K tier). DeepSeek -- third-party trackers disagree ($0.14/$0.28 vs $0.22/$0.66) and
#: report deepseek-v4-flash retired 2026-09-10; the higher pair is used (conservative). Cached input on Qwen is
#: priced at the full input rate (conservative; no published intl cache price found).
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4-6": (5.00, 25.00, 0.50, 6.25),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "qwen3.8-flash": (0.15, 0.47, 0.15, 0.15),
    "qwen3.6-plus": (0.50, 3.00, 0.50, 0.50),
    "deepseek-v4-flash": (0.22, 0.66, 0.0028, 0.22),
}
#: per web search / search call.
SEARCH_PRICES: dict[str, float] = {
    "anthropic": 10.00 / 1000,      # web_search server tool
    "qwen": 10.00 / 1000,           # Model Studio web_search tool, Singapore (dashscope-intl) rate
    "tavily": 0.008,                # basic search credit (estimate)
}

#: host fragment -> provider. Only these hosts are inspected; everything else passes through untouched.
LLM_HOSTS = (
    ("api.anthropic.com", "anthropic"),
    ("aliyuncs.com", "qwen"),
    ("api.deepseek.com", "deepseek"),
    ("generativelanguage.googleapis.com", "gemini"),
    ("api.openai.com", "openai"),
    ("openrouter.ai", "openrouter"),
    ("api.tavily.com", "tavily"),
)

_MAX_BODY = 4 * 1024 * 1024
_FIELDS = ("calls", "calls_no_usage", "input", "output", "cache_read", "cache_write", "searches")


def _provider_for(host: str) -> Optional[str]:
    host = (host or "").lower()
    for frag, prov in LLM_HOSTS:
        if frag in host:
            return prov
    return None


# ── usage extraction (pure; unit-tested) ─────────────────────────────────────

def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _usage_from(u: dict) -> dict:
    """One provider usage block -> {input, output, cache_read, cache_write, searches}. Input excludes cached
    tokens on every provider (Anthropic reports them apart; OpenAI-style APIs include them, so they are split)."""
    if not isinstance(u, dict):
        return {}
    out = {k: 0 for k in ("input", "output", "cache_read", "cache_write", "searches")}
    if "promptTokenCount" in u or "candidatesTokenCount" in u:                 # Gemini usageMetadata
        cached = _int(u.get("cachedContentTokenCount"))
        out["input"] = max(_int(u.get("promptTokenCount")) - cached, 0)
        out["cache_read"] = cached
        out["output"] = _int(u.get("candidatesTokenCount")) + _int(u.get("thoughtsTokenCount"))
        return out
    if "prompt_tokens" in u or "completion_tokens" in u:                       # OpenAI chat completions
        cached = _int((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
        out["input"] = max(_int(u.get("prompt_tokens")) - cached, 0)
        out["cache_read"] = cached
        out["output"] = _int(u.get("completion_tokens"))
    else:                                                                      # Anthropic / Responses API
        details = u.get("input_tokens_details") or {}
        cached_resp = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        if "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u:
            out["input"] = _int(u.get("input_tokens"))
            out["cache_read"] = _int(u.get("cache_read_input_tokens"))
            out["cache_write"] = _int(u.get("cache_creation_input_tokens"))
        else:
            out["input"] = max(_int(u.get("input_tokens")) - cached_resp, 0)
            out["cache_read"] = cached_resp
        out["output"] = _int(u.get("output_tokens"))
    stu = u.get("server_tool_use") or {}
    if isinstance(stu, dict):
        out["searches"] += _int(stu.get("web_search_requests"))
    xt = u.get("x_tools") or u.get("plugins") or {}
    if isinstance(xt, dict):
        for k, v in xt.items():
            if "search" in str(k).lower() and isinstance(v, dict):
                out["searches"] += _int(v.get("count"))
    return out


def _merge_max(acc: dict, new: dict) -> dict:
    """Streams report usage in pieces (Anthropic: input on message_start, cumulative output on message_delta);
    the largest value of each field over the stream is the total."""
    for k, v in (new or {}).items():
        acc[k] = max(acc.get(k, 0), v)
    return acc


def _find_usage(obj: Any) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    for key in ("usage", "usageMetadata"):
        if isinstance(obj.get(key), dict):
            return obj[key]
    for key in ("message", "response"):                    # anthropic message_start / responses .completed
        inner = obj.get(key)
        if isinstance(inner, dict):
            for k2 in ("usage", "usageMetadata"):
                if isinstance(inner.get(k2), dict):
                    return inner[k2]
    return None


def _find_model(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("model"), str):
        return obj["model"]
    for key in ("message", "response"):
        inner = obj.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("model"), str):
            return inner["model"]
    return obj.get("modelVersion") if isinstance(obj.get("modelVersion"), str) else None


def _count_web_search_items(obj: Any) -> int:
    """Responses API: each web_search_call output item is one search."""
    if not isinstance(obj, dict):
        return 0
    out = obj.get("output") or ((obj.get("response") or {}).get("output") if isinstance(obj.get("response"), dict)
                                else None)
    if not isinstance(out, list):
        return 0
    return sum(1 for it in out if isinstance(it, dict) and it.get("type") == "web_search_call")


def parse_json_body(body: bytes) -> tuple[Optional[str], Optional[dict]]:
    """A non-streamed response body -> (model, usage) or (None, None)."""
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return None, None
    u = _find_usage(obj)
    usage = _usage_from(u) if u is not None else None
    n = _count_web_search_items(obj)
    if n:
        usage = usage or {k: 0 for k in ("input", "output", "cache_read", "cache_write", "searches")}
        usage["searches"] = max(usage["searches"], n)
    return _find_model(obj), usage


def parse_sse_lines(lines: list[str]) -> tuple[Optional[str], Optional[dict]]:
    """The relevant `data:` lines of an SSE stream -> (model, usage) or (None, None)."""
    model, usage, searches, seen = None, {}, 0, False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        model = model or _find_model(obj)
        if isinstance(obj, dict) and obj.get("type") == "response.web_search_call.completed":
            searches += 1
        u = _find_usage(obj)
        if u is not None:
            seen = True
            _merge_max(usage, _usage_from(u))
        n = _count_web_search_items(obj)
        if n:
            searches = max(searches, n)
    if searches:
        seen = True
        usage["searches"] = max(usage.get("searches", 0), searches)
    return model, (usage if seen else None)


# ── meters ─────────────────────────────────────────────────────────────────

class Meter:
    """Usage per model, thread-safe (a run's worker threads share one)."""

    def __init__(self, **meta):
        self.meta = meta
        self.started_at = datetime.now(timezone.utc)
        self._lock = threading.Lock()
        self.by_model: dict[str, dict[str, int]] = {}

    def add(self, provider: str, model: Optional[str], usage: Optional[dict]) -> None:
        key = f"{provider}:{(model or 'unknown').lower()}"
        with self._lock:
            row = self.by_model.setdefault(key, {f: 0 for f in _FIELDS})
            row["calls"] += 1
            if usage is None:
                row["calls_no_usage"] += 1
                return
            for f in ("input", "output", "cache_read", "cache_write", "searches"):
                row[f] += _int(usage.get(f))

    def totals(self) -> dict[str, int]:
        with self._lock:
            return {f: sum(r[f] for r in self.by_model.values()) for f in _FIELDS}

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {k: dict(v) for k, v in self.by_model.items()}


_CURRENT: contextvars.ContextVar[Optional[Meter]] = contextvars.ContextVar("llm_cost_meter", default=None)
_UNATTRIBUTED = Meter(kind="unattributed")
_FLUSH = {"at": time.monotonic(), "calls": 0}
_FLUSH_EVERY_S = 600
_FLUSH_EVERY_CALLS = 200


def current() -> Optional[Meter]:
    return _CURRENT.get()


def bind(fn):
    """Carry ONLY the current run's meter into a bare executor.submit() worker. For pools that must not inherit
    the rest of the run context (run_config.submit copies the settings overlay too, which would change behaviour)."""
    m = _CURRENT.get()

    def _bound(*args, **kwargs):
        token = _CURRENT.set(m)
        try:
            return fn(*args, **kwargs)
        finally:
            _CURRENT.reset(token)
    return _bound


def record(provider: str, model: Optional[str], usage: Optional[dict]) -> None:
    """Credit one LLM / search call to the current run, else to this process's unattributed bucket."""
    try:
        m = _CURRENT.get()
        if m is not None:
            m.add(provider, model, usage)
            return
        _UNATTRIBUTED.add(provider, model, usage)
        _FLUSH["calls"] += 1
        if _FLUSH["calls"] >= _FLUSH_EVERY_CALLS or time.monotonic() - _FLUSH["at"] > _FLUSH_EVERY_S:
            flush_unattributed()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cost_meter.record failed: %s", exc)


# ── the HTTP hook ──────────────────────────────────────────────────────────

def _request_model(content: Optional[bytes], path: str) -> Optional[str]:
    if "/models/" in path:                                     # Gemini: /v1beta/models/<model>:generateContent
        return path.split("/models/", 1)[1].split(":", 1)[0] or None
    if not content or len(content) > _MAX_BODY:
        return None
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return None
    return obj.get("model") if isinstance(obj, dict) and isinstance(obj.get("model"), str) else None


def _decode(body: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(body)
        if "deflate" in enc:
            return zlib.decompress(body)
    except Exception:  # noqa: BLE001
        return b""
    return body


def observe(provider: str, path: str, req_content: Optional[bytes], status: int,
            body: Optional[bytes], content_type: str = "") -> None:
    """Record one completed, non-streamed call (also the entry point for tests)."""
    try:
        if provider == "tavily":
            if 200 <= status < 300 and "/search" in path:
                record("tavily", "search", {"searches": 1})
            return
        if status >= 400:
            return                                             # a rejected call is not billed
        model, usage = (None, None)
        if body:
            if "event-stream" in content_type:
                model, usage = parse_sse_lines(body.decode("utf-8", "replace").splitlines())
            else:
                model, usage = parse_json_body(body)
        record(provider, model or _request_model(req_content, path), usage)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cost_meter.observe failed: %s", exc)


class _Tee:
    """Collects what a streamed response needs for usage, passing every chunk through unchanged."""

    def __init__(self, provider: str, path: str, req_content: Optional[bytes], content_type: str, encoding: str):
        self.provider, self.path, self.req = provider, path, req_content
        self.sse = "event-stream" in (content_type or "")
        self.encoding = encoding
        self.content_type = content_type
        self.rest = ""
        self.lines: list[str] = []
        self.raw = bytearray()
        self.done = False

    def feed(self, chunk: bytes) -> None:
        try:
            if self.sse and not self.encoding:
                text = self.rest + chunk.decode("utf-8", "replace")
                parts = text.split("\n")
                self.rest = parts.pop()
                self.lines.extend(p for p in parts if "usage" in p or "web_search" in p or '"model"' in p)
            elif len(self.raw) < _MAX_BODY:
                self.raw.extend(chunk)
        except Exception:  # noqa: BLE001
            pass

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            if self.sse and not self.encoding:
                if self.rest:
                    self.lines.append(self.rest)
                model, usage = parse_sse_lines(self.lines)
                record(self.provider, model or _request_model(self.req, self.path), usage)
            else:
                observe(self.provider, self.path, self.req, 200, _decode(bytes(self.raw), self.encoding),
                        self.content_type)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cost_meter tee finish failed: %s", exc)


def _wrap_httpx_response(response, provider: str, path: str, req_content: Optional[bytes],
                         is_async: bool = False) -> None:
    import httpx
    ct = response.headers.get("content-type", "")
    enc = response.headers.get("content-encoding", "")
    if enc.lower() in ("identity",):
        enc = ""
    tee = _Tee(provider, path, req_content, ct, enc)
    inner = response.stream

    if is_async:
        class _A(httpx.AsyncByteStream):
            async def __aiter__(self):
                async for chunk in inner:
                    tee.feed(chunk)
                    yield chunk
                tee.finish()

            async def aclose(self):
                try:
                    await inner.aclose()
                finally:
                    tee.finish()
        response.stream = _A()
    else:
        class _S(httpx.SyncByteStream):
            def __iter__(self):
                for chunk in inner:
                    tee.feed(chunk)
                    yield chunk
                tee.finish()

            def close(self):
                try:
                    inner.close()
                finally:
                    tee.finish()
        response.stream = _S()


def _req_content(request) -> Optional[bytes]:
    try:
        return request.content
    except Exception:  # noqa: BLE001  (a streamed request body has no .content)
        return None


_ORIG: dict[str, Any] = {}


def _under_pytest() -> bool:
    return "pytest" in sys.modules and not os.environ.get("COST_METER_IN_TESTS")


def install(force: bool = False) -> bool:
    """Hook httpx and requests once per process. Skipped under pytest unless forced; COST_METER_DISABLED=1 is
    the kill switch. Returns whether the hooks are in place."""
    if os.environ.get("COST_METER_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return False
    if _ORIG and not force:
        return True
    if _under_pytest() and not force:
        return False
    try:
        import httpx
    except ImportError:  # pragma: no cover
        httpx = None
    if httpx is not None and "httpx_sync" not in _ORIG:
        _ORIG["httpx_sync"] = httpx.Client.send
        _ORIG["httpx_async"] = httpx.AsyncClient.send

        def send(self, request, *args, **kwargs):
            resp = _ORIG["httpx_sync"](self, request, *args, **kwargs)
            _after_httpx(request, resp, kwargs, False)
            return resp

        async def asend(self, request, *args, **kwargs):
            resp = await _ORIG["httpx_async"](self, request, *args, **kwargs)
            _after_httpx(request, resp, kwargs, True)
            return resp

        httpx.Client.send = send
        httpx.AsyncClient.send = asend
    try:
        import requests
    except ImportError:  # pragma: no cover
        requests = None
    if requests is not None and "requests" not in _ORIG:
        _ORIG["requests"] = requests.Session.send

        def rsend(self, request, **kwargs):
            resp = _ORIG["requests"](self, request, **kwargs)
            try:
                from urllib.parse import urlsplit
                parts = urlsplit(request.url or "")
                prov = _provider_for(parts.hostname or "")
                if prov:
                    body = None if kwargs.get("stream") else resp.content
                    rc = request.body if isinstance(request.body, (bytes, bytearray)) else (
                        request.body.encode() if isinstance(request.body, str) else None)
                    observe(prov, parts.path, rc, resp.status_code, body, resp.headers.get("content-type", ""))
            except Exception as exc:  # noqa: BLE001
                logger.debug("cost_meter requests hook failed: %s", exc)
            return resp

        requests.Session.send = rsend
    import atexit
    atexit.register(flush_unattributed)
    logger.info("cost_meter: LLM usage hooks installed")
    return True


def _after_httpx(request, resp, kwargs, is_async: bool) -> None:
    try:
        prov = _provider_for(request.url.host)
        if not prov:
            return
        path = request.url.path
        rc = _req_content(request)
        # A streamed response whose body is already in memory (e.g. a transport that returns content) is read
        # like any other; only a live stream is teed.
        if kwargs.get("stream") and not hasattr(resp, "_content"):
            if resp.status_code < 400:
                _wrap_httpx_response(resp, prov, path, rc, is_async)
            return
        observe(prov, path, rc, resp.status_code, resp.content, resp.headers.get("content-type", ""))
    except Exception as exc:  # noqa: BLE001
        logger.debug("cost_meter httpx hook failed: %s", exc)


def uninstall() -> None:
    """Tests only: restore the originals."""
    import httpx
    if "httpx_sync" in _ORIG:
        httpx.Client.send = _ORIG.pop("httpx_sync")
        httpx.AsyncClient.send = _ORIG.pop("httpx_async")
    if "requests" in _ORIG:
        import requests
        requests.Session.send = _ORIG.pop("requests")


# ── pricing ────────────────────────────────────────────────────────────────

def _price(model: str) -> Optional[tuple[float, float, float, float]]:
    m = (model or "").lower()
    best = None
    for key, p in PRICES.items():
        if m.startswith(key) and (best is None or len(key) > len(best[0])):
            best = (key, p)
    return best[1] if best else None


def cost_of(breakdown: dict[str, dict[str, int]]) -> dict:
    """{provider:model -> usage} -> {usd, tokens_usd, search_usd, unpriced: [...], calls, calls_no_usage}."""
    tok = srch = 0.0
    unpriced: list[str] = []
    calls = no_usage = 0
    for key, u in (breakdown or {}).items():
        provider, _, model = key.partition(":")
        calls += _int(u.get("calls"))
        no_usage += _int(u.get("calls_no_usage"))
        if provider != "tavily":
            p = _price(model)
            tokens = sum(_int(u.get(f)) for f in ("input", "output", "cache_read", "cache_write"))
            if p:
                tok += (_int(u.get("input")) * p[0] + _int(u.get("output")) * p[1]
                        + _int(u.get("cache_read")) * p[2] + _int(u.get("cache_write")) * p[3]) / 1e6
            elif tokens:
                unpriced.append(key)
        n = _int(u.get("searches"))
        if n:
            sp = SEARCH_PRICES.get(provider)
            if sp is None:
                unpriced.append(f"{key} (search)")
            else:
                srch += n * sp
    return {"usd": round(tok + srch, 6), "tokens_usd": round(tok, 6), "search_usd": round(srch, 6),
            "unpriced": unpriced, "calls": calls, "calls_no_usage": no_usage}


# ── storage ────────────────────────────────────────────────────────────────

_DDL = (
    "CREATE TABLE IF NOT EXISTS run_costs (run_id TEXT PRIMARY KEY, user_id INTEGER, ticker TEXT, model_name TEXT, "
    "run_type TEXT, status TEXT, started_at TEXT NOT NULL, finished_at TEXT, service TEXT, calls INTEGER, "
    "calls_no_usage INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
    "cache_write_tokens INTEGER, searches INTEGER, breakdown_json TEXT)",
    "CREATE TABLE IF NOT EXISTS llm_spend_daily (day TEXT NOT NULL, service TEXT NOT NULL, model TEXT NOT NULL, "
    "calls INTEGER, calls_no_usage INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
    "cache_write_tokens INTEGER, searches INTEGER, PRIMARY KEY (day, service, model))",
)
_READY = {"v": False}


def ensure_tables() -> None:
    if _READY["v"]:
        return
    from src.data import db
    for ddl in _DDL:
        db.ensure_table(ddl)
    db.execute("CREATE INDEX IF NOT EXISTS idx_run_costs_started ON run_costs (started_at)")
    _READY["v"] = True


def _service() -> str:
    name = os.environ.get("RAILWAY_SERVICE_NAME") or "local"
    rep = (os.environ.get("RAILWAY_REPLICA_ID") or "")[:8]
    return f"{name}:{rep}" if rep else name


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def save_run(meter: Meter, status: str) -> None:
    from src.data import db
    ensure_tables()
    meta = meter.meta
    t = meter.totals()
    params = [meta.get("run_id"), meta.get("user_id"), meta.get("ticker"), meta.get("model_name"),
              meta.get("run_type"), status, _iso(meter.started_at), _iso(datetime.now(timezone.utc)), _service(),
              t["calls"], t["calls_no_usage"], t["input"], t["output"], t["cache_read"], t["cache_write"],
              t["searches"], json.dumps(meter.snapshot(), sort_keys=True)]
    db.execute("DELETE FROM run_costs WHERE run_id = ?", [meta.get("run_id")])
    db.execute("INSERT INTO run_costs (run_id, user_id, ticker, model_name, run_type, status, started_at, finished_at, "
               "service, calls, calls_no_usage, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
               "searches, breakdown_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", params)


def flush_unattributed(force: bool = False) -> int:
    """Add this process's unattributed usage to llm_spend_daily (additive per day/service/model). Returns rows."""
    if _under_pytest() and not force:
        return 0
    snap = _UNATTRIBUTED.snapshot()
    with _UNATTRIBUTED._lock:
        _UNATTRIBUTED.by_model.clear()
    _FLUSH.update(at=time.monotonic(), calls=0)
    if not snap:
        return 0
    try:
        from src.data import db
        ensure_tables()
        day, svc = datetime.now(timezone.utc).strftime("%Y-%m-%d"), _service()
        cols = ("calls", "calls_no_usage", "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "searches")
        for model, u in snap.items():
            vals = [u["calls"], u["calls_no_usage"], u["input"], u["output"], u["cache_read"], u["cache_write"],
                    u["searches"]]
            if db.query_one("SELECT day FROM llm_spend_daily WHERE day = ? AND service = ? AND model = ?",
                            [day, svc, model]):
                db.execute("UPDATE llm_spend_daily SET " + ", ".join(f"{c} = {c} + ?" for c in cols)
                           + " WHERE day = ? AND service = ? AND model = ?", [*vals, day, svc, model])
            else:
                db.execute("INSERT INTO llm_spend_daily (day, service, model, " + ", ".join(cols)
                           + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [day, svc, model, *vals])
        return len(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost_meter: unattributed flush failed (%s)", exc)
        return 0


@contextlib.contextmanager
def metered_run(run_id: str, **meta) -> Iterator[Meter]:
    """Attribute every LLM / search call made in this context (and in threads spawned through run_config) to
    one run, and save the run's row when it ends -- success, error or cancel alike."""
    m = Meter(run_id=run_id, **meta)
    token = _CURRENT.set(m)
    status = "ok"
    try:
        yield m
    except BaseException as exc:
        status = "cancelled" if type(exc).__name__ == "RunCancelled" else "error"
        raise
    finally:
        _CURRENT.reset(token)
        try:
            save_run(m, status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cost_meter: could not save run %s (%s)", run_id, exc)


# ── report ─────────────────────────────────────────────────────────────────

def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


def cost_report(days: int = 30, rows: Optional[list[dict]] = None, daily: Optional[list[dict]] = None) -> dict:
    """Measured cost per run type vs the credit weights, and what a plan costs at full use."""
    from app.backend.services import billing
    if rows is None or daily is None:
        from src.data import db
        ensure_tables()
        since = _iso(datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - days * 86400, tz=timezone.utc))
        if rows is None:
            rows = [dict(r) for r in (db.query(
                "SELECT run_id, run_type, model_name, status, calls, calls_no_usage, breakdown_json, started_at "
                "FROM run_costs WHERE started_at >= ?", [since]) or [])]
        if daily is None:
            daily = [dict(r) for r in (db.query(
                "SELECT day, service, model, calls, calls_no_usage, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, searches FROM llm_spend_daily WHERE day >= ?", [since[:10]]) or [])]
    by_type: dict[str, list[float]] = {}
    unpriced: set[str] = set()
    calls = no_usage = 0
    for r in rows:
        if r.get("status") != "ok":
            continue
        c = cost_of(json.loads(r.get("breakdown_json") or "{}"))
        unpriced.update(c["unpriced"])
        calls += c["calls"]
        no_usage += c["calls_no_usage"]
        by_type.setdefault(r.get("run_type") or "unknown", []).append(c["usd"])
    types = {}
    for t, xs in sorted(by_type.items()):
        credits = billing.CREDIT_COSTS.get(t)
        types[t] = {"runs": len(xs), "median_usd": round(_pct(xs, 0.5), 4), "p90_usd": round(_pct(xs, 0.9), 4),
                    "max_usd": round(max(xs), 4), "mean_usd": round(sum(xs) / len(xs), 4), "credits": credits,
                    "p90_usd_per_credit": round(_pct(xs, 0.9) / credits, 4) if credits else None}
    worst_per_credit = max((v["p90_usd_per_credit"] or 0) for v in types.values()) if types else None
    plans = {}
    for pid in billing.PLAN_ORDER:
        p = billing.PLANS[pid]
        net = p["price_monthly"] * (1 - 0.034) - (0.40 if p["price_monthly"] else 0)
        if worst_per_credit is not None:
            worst = p["credits"] * worst_per_credit
            plans[pid] = {"price": p["price_monthly"], "credits": p["credits"], "worst_case_cogs_usd": round(worst, 2),
                          "worst_case_margin": round((net - worst) / net, 3) if net > 0 else None}
    unattributed = {}
    for d in daily:
        key = d.get("model")
        u = {"calls": d.get("calls"), "calls_no_usage": d.get("calls_no_usage"), "input": d.get("input_tokens"),
             "output": d.get("output_tokens"), "cache_read": d.get("cache_read_tokens"),
             "cache_write": d.get("cache_write_tokens"), "searches": d.get("searches")}
        c = cost_of({key: u})
        unpriced.update(c["unpriced"])
        unattributed[key] = round(unattributed.get(key, 0) + c["usd"], 4)
    return {"days": days, "runs": sum(len(x) for x in by_type.values()), "by_run_type": types,
            "worst_usd_per_credit": worst_per_credit, "plans_at_full_use": plans,
            "usage_coverage": round(1 - no_usage / calls, 3) if calls else None,
            "unattributed_usd_by_model": unattributed,
            "unattributed_usd_total": round(sum(unattributed.values()), 2),
            "unpriced": sorted(unpriced)}

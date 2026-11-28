"""Answer a question about a recent market development, and carry it into an analysis (owner, 2026-11-28).

POST /analysis/ask -> ask(): one Qwen web search answers the question and names the listed companies it affects.
The development is VERIFIED only when the answer dates it (within MAX_AGE_DAYS) and cites at least
MIN_SOURCE_DOMAINS independent source domains (owner: verify by search -- an unverified claim never touches the shared
analysis everyone sees). Each affected ticker comes back with its latest shared analysis (if any) and what updating it
costs: 1 credit when covered, 2 when new (billing.run_type). The answer itself is free, capped per plan per day.

POST /analysis/run {ticker, ask_id} -> check_bindable() before credits, bind_run() once the run id exists; the worker
reads development_for_run(run_id) and the pipeline folds it in (src/memory/user_development.py). The run's result is the
newest research for that ticker, so it supersedes the earlier shared analysis.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 400
MAX_AGE_DAYS = 60
MIN_SOURCE_DOMAINS = 2
MAX_TICKERS = 4
#: an ask can start an update run for this long.
ASK_TTL_HOURS = 24
#: answers per user per UTC day (admins exempt). An answer is one web search (~$0.01-0.02).
ASKS_PER_DAY = {"free": 5, "starter": 30, "professional": 100}

_DDL = """CREATE TABLE IF NOT EXISTS market_asks (
    ask_id TEXT PRIMARY KEY,
    user_id INTEGER,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    run_ticker TEXT,
    run_id TEXT,
    run_bound_at TEXT
)"""
_READY = {"v": False}


class AskError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def ensure_tables() -> None:
    if _READY["v"]:
        return
    from src.data import db
    db.ensure_table(_DDL)
    db.execute("CREATE INDEX IF NOT EXISTS idx_market_asks_user ON market_asks (user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_market_asks_run ON market_asks (run_id)")
    _READY["v"] = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _row(r, key: str, idx: int):
    if r is None:
        return None
    try:
        return r[key]
    except (KeyError, IndexError, TypeError):
        return r[idx]


# ── question shape ─────────────────────────────────────────────────────────

def looks_like_question(text: str) -> bool:
    """A landing-box entry is a question (not a ticker/company search) when it ends in '?' or has 4+ words."""
    t = (text or "").strip()
    return t.endswith("?") or len(t.split()) >= 4


def clean_question(q) -> str:
    if not isinstance(q, str):
        raise AskError(422, "question must be text")
    q = re.sub(r"\s+", " ", q.replace("\x00", "")).strip()
    if len(q) < 8:
        raise AskError(422, "Ask a question about a market development, e.g. "
                            "'What does the new US chip export rule mean for AMAT?'")
    return q[:MAX_QUESTION_CHARS]


# ── rate limit ─────────────────────────────────────────────────────────────

def _asks_today(user_id: int) -> int:
    from src.data import db
    since = _iso(_now().replace(hour=0, minute=0, second=0, microsecond=0))
    r = db.query_one("SELECT COUNT(*) AS n FROM market_asks WHERE user_id = ? AND created_at >= ?", [user_id, since])
    return int(_row(r, "n", 0) or 0)


def check_rate(user) -> None:
    from app.backend.services import billing
    if user is None or billing._is_admin(user):
        return
    billing.ensure_tables()
    plan = billing.plan_for(user)
    cap = ASKS_PER_DAY.get(plan, ASKS_PER_DAY["free"])
    if _asks_today(user.id) >= cap:
        raise AskError(429, {"code": "ask_limit", "plan": plan, "limit": cap,
                             "message": f"You have used today's {cap} market questions on the "
                                        f"{billing.PLANS[plan]['name']} plan. They reset at 00:00 UTC."})


# ── the search ─────────────────────────────────────────────────────────────

_SYSTEM = ("You are an equity research analyst answering questions about recent market developments. "
           "Search the web for current, dated reporting. Never invent a source, date or figure.")


def _prompt(question: str, today: str, ticker_hint: Optional[str]) -> str:
    hint = f"\nThe user was looking at ticker {ticker_hint}; include it if it is affected.\n" if ticker_hint else ""
    return (
        f"Today is {today}. Question: {question}\n{hint}\n"
        "Search the web, then answer. Identify the specific market development the question is about.\n"
        "Respond in JSON format with exactly this schema:\n"
        '{"answer": "3-6 sentence plain-English answer with figures",\n'
        ' "development": {"headline": "one line naming the event", "date": "YYYY-MM-DD it happened or was announced",\n'
        '                 "summary": "1-2 sentences of the facts"} or null if the question names no real event,\n'
        ' "corroborated": true only if at least two independent outlets report it,\n'
        ' "sources": [{"title": "...", "url": "https://...", "date": "YYYY-MM-DD", "fact": "what it reports"}],\n'
        ' "tickers": [{"symbol": "Yahoo symbol, e.g. NVDA, 9988.HK, D05.SI", "name": "company",\n'
        '              "why": "one line on how the development affects it"}]}\n'
        f"List at most {MAX_TICKERS} listed companies most directly affected (US, Hong Kong or Singapore listings "
        "only). Use only sources you actually found."
    )


def _parse_json(text: Optional[str]) -> dict:
    if not text:
        return {}
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                v = json.loads(m.group(0))
                return v if isinstance(v, dict) else {}
            except ValueError:
                return {}
    return {}


def _domain(url: str) -> Optional[str]:
    try:
        u = urlparse(str(url or "").strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    host = u.netloc.lower().split("@")[-1].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    # registrable-ish domain: last two labels, three for e.g. co.uk / com.sg / com.hk
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _parse_date(v) -> Optional[date]:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def verify(parsed: dict, today: date) -> tuple[bool, str]:
    """(verified, reason). The model's own 'corroborated' is necessary, never sufficient."""
    dev = parsed.get("development")
    if not isinstance(dev, dict) or not str(dev.get("headline") or "").strip():
        return False, "The question doesn't name a specific development we could find reporting on."
    d = _parse_date(dev.get("date"))
    if d is None:
        return False, "We couldn't establish when this happened."
    if d > today + timedelta(days=1):
        return False, "The reported date is in the future."
    if (today - d).days > MAX_AGE_DAYS:
        return False, f"This happened more than {MAX_AGE_DAYS} days ago; a regular analysis already covers it."
    domains = {_domain(s.get("url")) for s in parsed.get("sources") or [] if isinstance(s, dict)} - {None}
    if len(domains) < MIN_SOURCE_DOMAINS or parsed.get("corroborated") is not True:
        return False, ("We found fewer than two independent sources reporting this, so it can't be added "
                       "to an analysis.")
    return True, f"Reported by {len(domains)} independent sources."


# ── tickers ────────────────────────────────────────────────────────────────

_US_SYM = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")


def _supported_symbol(sym: str) -> Optional[str]:
    from src.tools.ticker_canonical import canonical_ticker
    s = str(sym or "").strip().upper()
    if not s:
        return None
    if s.endswith(".HK") or s.endswith(".SI"):
        return canonical_ticker(s) or None
    return s if _US_SYM.match(s) else None


def _ticker_rows(parsed: dict, ticker_hint: Optional[str], verified: bool,
                 recap_for: Callable, run_type_for: Callable) -> list[dict]:
    from app.backend.services import billing
    seen, rows = set(), []
    cands = list(parsed.get("tickers") or [])
    if ticker_hint:
        cands.insert(0, {"symbol": ticker_hint, "name": "", "why": ""})
    for c in cands:
        if not isinstance(c, dict):
            continue
        sym = _supported_symbol(c.get("symbol"))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        prior = None
        try:
            prior = recap_for(sym)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("ask: recap lookup failed for %s: %s", sym, exc)
        try:
            kind = run_type_for(sym)
        except Exception:                                   # noqa: BLE001
            kind = "covered" if prior else "new"
        row = {"ticker": sym, "name": str(c.get("name") or "")[:120], "why": str(c.get("why") or "")[:240],
               "run_type": kind, "credits": billing.CREDIT_COSTS.get(kind, 1), "can_update": verified,
               "latest": None}
        if prior:
            rj = prior.get("recap_json") or {}
            row["latest"] = {"run_id": prior.get("run_id"), "run_at": str(prior.get("run_at") or ""),
                             "final_action": prior.get("final_action"), "price_target": rj.get("price_target")}
        rows.append(row)
        if len(rows) >= MAX_TICKERS:
            break
    return [r for r in rows if r["name"] or r["latest"] or r["ticker"] == ticker_hint] or rows


def _default_recap(sym: str):
    from src.memory import report_recap
    return report_recap.get_recent_recap(sym)


def _default_run_type(sym: str) -> str:
    from app.backend.services import billing
    return billing.run_type(sym)


def _default_search(question: str, today: str, ticker_hint: Optional[str]) -> Optional[str]:
    from src.research_ideas.complacency.web_research import qwen_web_search
    return qwen_web_search(_prompt(question, today, ticker_hint), system_prompt=_SYSTEM,
                           max_chars=12000, retries=1, request_timeout_s=90)


def ask(user, question, ticker_hint: Optional[str] = None, *, search: Callable = _default_search,
        recap_for: Callable = _default_recap, run_type_for: Callable = _default_run_type,
        now: Optional[datetime] = None) -> dict:
    q = clean_question(question)
    ensure_tables()
    check_rate(user)
    hint = _supported_symbol(ticker_hint) if ticker_hint else None
    now = now or _now()
    today = now.date()
    raw = search(q, today.isoformat(), hint)
    parsed = _parse_json(raw)
    if not parsed.get("answer"):
        raise AskError(503, "We couldn't research that question just now. Please try again.")
    verified, reason = verify(parsed, today)
    dev = parsed.get("development") if isinstance(parsed.get("development"), dict) else None
    sources = [{"title": str(s.get("title") or "")[:200], "url": str(s.get("url") or "")[:500],
                "date": str(s.get("date") or "")[:10], "fact": str(s.get("fact") or "")[:300]}
               for s in parsed.get("sources") or [] if isinstance(s, dict) and _domain(s.get("url"))][:8]
    out = {
        "ask_id": str(uuid.uuid4()),
        "question": q,
        "answer": str(parsed.get("answer"))[:3000],
        "development": None if not dev else {
            "headline": str(dev.get("headline") or "")[:200], "date": str(dev.get("date") or "")[:10],
            "summary": str(dev.get("summary") or "")[:500]},
        "verified": verified,
        "verification": reason,
        "sources": sources,
        "tickers": _ticker_rows(parsed, hint, verified, recap_for, run_type_for),
        "asked_at": _iso(now),
    }
    from src.data import db
    db.execute("INSERT INTO market_asks (ask_id, user_id, question, created_at, verified, payload) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               [out["ask_id"], getattr(user, "id", None), q, out["asked_at"], 1 if verified else 0,
                json.dumps(out, default=str)])
    return out


# ── binding an ask to a run ────────────────────────────────────────────────

def _load(ask_id: str) -> Optional[dict]:
    from src.data import db
    ensure_tables()
    r = db.query_one("SELECT ask_id, user_id, created_at, verified, payload, run_id FROM market_asks "
                     "WHERE ask_id = ?", [ask_id])
    if r is None:
        return None
    payload = _row(r, "payload", 4)
    try:
        p = json.loads(payload) if isinstance(payload, str) else (payload or {})
    except ValueError:
        p = {}
    return {"ask_id": _row(r, "ask_id", 0), "user_id": _row(r, "user_id", 1),
            "created_at": _row(r, "created_at", 2), "verified": bool(_row(r, "verified", 3)),
            "payload": p, "run_id": _row(r, "run_id", 5)}


def check_bindable(ask_id, user, ticker: str, now: Optional[datetime] = None) -> dict:
    """The stored ask, if this user may update `ticker` with it; AskError otherwise. Reads only the server's copy."""
    if not isinstance(ask_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", ask_id):
        raise AskError(422, "ask_id is invalid")
    a = _load(ask_id)
    if a is None:
        raise AskError(404, "That question has expired. Ask it again to update the analysis.")
    uid = getattr(user, "id", None)
    if a["user_id"] is not None and a["user_id"] != uid:
        raise AskError(404, "That question has expired. Ask it again to update the analysis.")
    if not a["verified"]:
        raise AskError(422, "This development couldn't be verified, so it can't be added to an analysis.")
    if a["run_id"]:
        raise AskError(409, "This question has already updated an analysis.")
    created = datetime.fromisoformat(str(a["created_at"])).replace(tzinfo=timezone.utc)
    if (now or _now()) - created > timedelta(hours=ASK_TTL_HOURS):
        raise AskError(410, "That question is more than a day old. Ask it again to update the analysis.")
    tickers = {t.get("ticker") for t in a["payload"].get("tickers") or [] if isinstance(t, dict)}
    if ticker not in tickers:
        raise AskError(422, f"{ticker} isn't one of the companies this development affects.")
    return a


def bind_run(ask_id: str, ticker: str, run_id: str) -> bool:
    """Attach the ask to the run that will carry it; False if it was bound in the meantime (single use)."""
    from src.data import db
    n = db.execute("UPDATE market_asks SET run_ticker = ?, run_id = ?, run_bound_at = ? "
                   "WHERE ask_id = ? AND run_id IS NULL", [ticker, run_id, _iso(_now()), ask_id])
    return bool(n)


def development_for_run(run_id: str) -> dict:
    """{ticker: development} for the pipeline; {} when the run carries none. Soft-fail."""
    if not run_id:
        return {}
    try:
        from src.data import db
        ensure_tables()
        r = db.query_one("SELECT ask_id, run_ticker, payload FROM market_asks WHERE run_id = ?", [run_id])
        if r is None:
            return {}
        p = _row(r, "payload", 2)
        p = json.loads(p) if isinstance(p, str) else (p or {})
        dev = p.get("development") or {}
        if not p.get("verified") or not dev.get("headline"):
            return {}
        t = _row(r, "run_ticker", 1)
        why = next((x.get("why") for x in p.get("tickers") or [] if isinstance(x, dict) and x.get("ticker") == t), "")
        return {t: {**dev, "ask_id": _row(r, "ask_id", 0), "question": p.get("question"),
                    "sources": p.get("sources") or [], "relevance": why or dev.get("summary")}}
    except Exception as exc:                                # noqa: BLE001
        logger.warning("development_for_run(%s) failed: %s", run_id, exc)
        return {}

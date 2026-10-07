"""Agency credit ratings for the cost of debt (owner, 2026-10-08).

The default for US tickers: the run researches the company's S&P / Moody's / Fitch long-term issuer ratings and
prices the cost of debt on them instead of the rating implied by interest cover. Agency pages cannot be fetched
(S&P 403, Moody's / Fitch script-rendered), so an agency's rating is CONFIRMED when either
  - the company's own latest 10-K or earnings 8-K states it beside the agency's name (EDGAR text), or
  - a second, independently worded search returns the same rating.

Status:
  VERIFIED  two or more confirmed agencies within one notch -- prices automatically (owner rule)
  PROPOSED  a rating was found but the rule is not met (one agency, a split rating, unconfirmed) -- waits for the owner
  UNRATED   the search found no agency rating -- the rating implied by interest cover stays, flagged
  FAILED    the search returned nothing usable -- retried after a week
  ACCEPTED / REVOKED  the owner's decision (scripts/credit_ratings.py), which always wins

Composite: three agencies -> the middle one; two -> the lower (the Basel convention). An ACCEPTED entry in
valuation_constants.credit_ratings (the owner's file registry) overrides everything here.
Stored in table credit_ratings (one row per ticker; new table, no ALTER).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

AGENCIES = ("S&P", "Moody's", "Fitch")
PRICED = ("VERIFIED", "ACCEPTED")
#: Days before an entry is researched again, by status.
REFRESH_DAYS = {"VERIFIED": 180, "PROPOSED": 90, "UNRATED": 30, "FAILED": 7}
WAIT_S = 120.0

_SP = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-",
       "CCC+", "CCC", "CCC-", "CC", "C", "D"]
_MOODYS = ["Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3", "Ba1", "Ba2", "Ba3", "B1", "B2", "B3",
           "Caa1", "Caa2", "Caa3", "Ca", "C"]

_DDL = """
CREATE TABLE IF NOT EXISTS credit_ratings (
    ticker         TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    rating         TEXT,
    record_json    TEXT NOT NULL,
    researched_at  TEXT NOT NULL,
    reviewer       TEXT,
    reviewed_at    TEXT
)
"""
_ready_key = None
_cache: dict[str, dict] = {}
_inflight: dict[str, threading.Thread] = {}
_lock = threading.Lock()


# ── the rating scale ─────────────────────────────────────────────────────────

def notch(agency: str, rating: Optional[str]) -> Optional[int]:
    """1 = AAA / Aaa ... ; None when the string is not a rating on that agency's scale."""
    if not rating:
        return None
    r = str(rating).strip().replace(" ", "").replace("–", "-").replace("−", "-")
    if agency == "Moody's":
        r = r[:1].upper() + r[1:].lower() if r else r
        return _MOODYS.index(r) + 1 if r in _MOODYS else None
    r = r.upper()
    return _SP.index(r) + 1 if r in _SP else None


def normalize(agency: str, rating: Optional[str]) -> Optional[str]:
    n = notch(agency, rating)
    if n is None:
        return None
    return _MOODYS[n - 1] if agency == "Moody's" else _SP[n - 1]


def composite(confirmed: dict[str, str]) -> Optional[str]:
    """Three agencies -> the middle notch; two -> the lower; one -> itself. On the S&P scale."""
    ns = sorted(n for a, r in confirmed.items() if (n := notch(a, r)) is not None)
    if not ns:
        return None
    n = ns[1] if len(ns) == 3 else ns[-1]
    return _SP[n - 1]


# ── research ─────────────────────────────────────────────────────────────────

_SYS = "You are a credit analyst. Report only ratings you can cite. Respond in JSON format."


def _ask_all(company: str, ticker: str) -> str:
    return ("Find the CURRENT long-term issuer credit ratings of " + company + " (US-listed, ticker " + ticker + ") from "
            "S&P Global Ratings, Moody's and Fitch. For each agency give the rating exactly as the agency writes it, the "
            "outlook, the date of the latest rating action and the URL of the page that states it (the company's "
            "investor-relations credit ratings page, the agency press release, or a SEC filing). If an agency does not "
            "rate the company, give rating null. Respond in JSON format: "
            '{"company": str, "ratings": [{"agency": "S&P"|"Moody\'s"|"Fitch", "rating": str|null, "outlook": str|null, '
            '"action_date": "YYYY-MM-DD"|null, "source_url": str|null}]}')


def _ask_confirm(company: str, ticker: str, agencies: list[str]) -> str:
    return ("Credit check for " + company + " (NYSE/Nasdaq: " + ticker + "). What long-term senior unsecured / issuer "
            "rating does each of these agencies currently assign: " + ", ".join(agencies) + "? Use the most recent rating "
            "action you can find. Respond in JSON format: {\"<agency>\": \"<rating or null>\", ...} with exactly these keys: "
            + ", ".join(agencies) + ".")


def _json(text: Optional[str]):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:                                      # noqa: BLE001
        return None


def _default_search(prompt: str) -> Optional[str]:
    """One DashScope native-search call. Tested 2026-10-08: qwen3.6-plus with the "agent" strategy found the ratings
    (3-7 minutes a ticker); qwen3.8-flash answered null for rated issuers with thinking off and did not finish in
    400s with it on, and does not support "agent". Hence the slow model, started in the background at run start."""
    from src.research_ideas.complacency import qwen_throttle
    from src.research_ideas.complacency.web_research import _qwen_client
    client = _qwen_client()
    if client is None:
        return None
    try:
        client = client.with_options(timeout=480, max_retries=0)
    except Exception:                                      # noqa: BLE001
        pass
    qwen_throttle.acquire(weight=2.0)
    stream = client.chat.completions.create(
        model=os.getenv("CREDIT_RATING_MODEL", "qwen3.6-plus"),
        messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
        extra_body={"enable_search": True, "search_options": {"search_strategy": os.getenv("CREDIT_RATING_SEARCH", "agent")}},
        stream=True)
    text = ""
    for chunk in stream:
        delta = getattr(chunk.choices[0] if chunk.choices else None, "delta", None)
        c = getattr(delta, "content", None) if delta else None
        if c:
            text += c
    return text.strip() or None


def _default_filings(ticker: str) -> list[dict]:
    from src.data import pipeline_sources as ps
    out = []
    for f in (ps.latest_10k, ps.latest_earnings_8k):
        try:
            d = f(ticker)
            if d and d.get("text"):
                out.append(d)
        except Exception:                                  # noqa: BLE001
            pass
    return out


_AGENCY_RX = {"S&P": r"S&P|Standard\s*&\s*Poor'?s", "Moody's": r"Moody[’']?s", "Fitch": r"Fitch"}


def filing_states(text: str, agency: str, rating: str) -> bool:
    """The filing names the agency and the rating within one sentence-sized window ("S&P, Moody's and Fitch assigned
    ... BBB+, Baa1 and BBB+" counts: the rating need only sit in the same 240 characters)."""
    if not text or not rating:
        return False
    text = text.replace("–", "-").replace("−", "-")
    rx = re.compile(r"(?<![A-Za-z])" + re.escape(rating) + r"(?![A-Za-z0-9+\-])")
    for m in re.finditer(_AGENCY_RX[agency], text):
        if rx.search(text[max(0, m.start() - 60): m.end() + 240]):
            return True
    return False


def research(ticker: str, company: Optional[str] = None, *, search: Optional[Callable[[str], Optional[str]]] = None,
             filings: Optional[Callable[[str], list]] = None) -> dict:
    """One ticker's ratings with the confirmation trail and the status. Never raises."""
    search = search or _default_search
    filings = filings or _default_filings
    t = str(ticker).upper()
    company = company or t
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec: dict = {"ticker": t, "company": company, "researched_at": now, "agencies": {}, "status": "FAILED",
                 "rating": None, "basis": None}
    try:
        a = _json(search(_ask_all(company, t)))
    except Exception as exc:                               # noqa: BLE001
        rec["basis"] = f"search failed: {type(exc).__name__}"
        return rec
    rows = (a or {}).get("ratings") if isinstance(a, dict) else None
    if not isinstance(rows, list):
        rec["basis"] = "the search returned no structured answer"
        return rec
    found: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ag = {"s&p": "S&P", "s&p global ratings": "S&P", "standard & poor's": "S&P", "moody's": "Moody's",
              "moodys": "Moody's", "fitch": "Fitch", "fitch ratings": "Fitch"}.get(str(r.get("agency") or "").strip().lower())
        nr = normalize(ag, r.get("rating")) if ag else None
        if ag and nr:
            found[ag] = {"rating": nr, "outlook": r.get("outlook"), "action_date": r.get("action_date"),
                         "source_url": r.get("source_url"), "confirmed_by": []}
    if not found:
        rec.update(status="UNRATED", basis="the search found no S&P, Moody's or Fitch rating")
        return rec
    # confirmation 1: the company's own filings
    docs = filings(t) or []
    for ag, f in found.items():
        for d in docs:
            if filing_states(d.get("text") or "", ag, f["rating"] if ag != "Moody's" else f["rating"]):
                f["confirmed_by"].append({"kind": d.get("form") or "filing", "date": d.get("date"), "url": d.get("url")})
                break
    # confirmation 2: an independently worded search for what the filings did not confirm
    rest = [ag for ag, f in found.items() if not f["confirmed_by"]]
    if rest:
        try:
            b = _json(search(_ask_confirm(company, t, rest))) or {}
        except Exception:                                  # noqa: BLE001
            b = {}
        for ag in rest:
            if isinstance(b, dict) and normalize(ag, b.get(ag)) == found[ag]["rating"]:
                found[ag]["confirmed_by"].append({"kind": "second search"})
    rec["agencies"] = found
    confirmed = {ag: f["rating"] for ag, f in found.items() if f["confirmed_by"]}
    ns = [notch(ag, r) for ag, r in confirmed.items()]
    if len(confirmed) >= 2 and max(ns) - min(ns) <= 1:
        rec.update(status="VERIFIED", rating=composite(confirmed),
                   basis=f"{len(confirmed)} agencies confirmed within one notch; composite = "
                         + ("the middle of three" if len(confirmed) == 3 else "the lower of two"))
    else:
        why = ("one agency" if len(found) == 1 else
               "agencies more than one notch apart" if len(confirmed) >= 2 else
               f"{len(confirmed)} of {len(found)} agencies confirmed")
        rec.update(status="PROPOSED", rating=composite(confirmed or {ag: f["rating"] for ag, f in found.items()}),
                   basis=f"not priced automatically ({why}); waits for the owner")
    return rec


# ── storage ──────────────────────────────────────────────────────────────────

def _ensure() -> None:
    global _ready_key
    from src.data import db as _db
    k = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if k != _ready_key:
        _db.ensure_table(_DDL)
        _ready_key = k


def stored(ticker: str) -> Optional[dict]:
    from src.data import db as _db
    _ensure()
    row = _db.query_one("SELECT status, rating, record_json, researched_at, reviewer, reviewed_at FROM credit_ratings "
                        "WHERE ticker = ?", [str(ticker).upper()])
    if not row:
        return None
    rec = json.loads(row["record_json"])
    rec.update(status=row["status"], rating=row["rating"], researched_at=row["researched_at"],
               reviewer=row["reviewer"], reviewed_at=row["reviewed_at"])
    return rec


def save(rec: dict) -> None:
    from src.data import db as _db
    _ensure()
    t = rec["ticker"]
    prev = stored(t)
    # an owner decision survives a refresh unless the research moved the rating
    if prev and prev.get("status") in ("ACCEPTED", "REVOKED") and prev.get("rating") == rec.get("rating"):
        rec = {**rec, "status": prev["status"], "reviewer": prev.get("reviewer"), "reviewed_at": prev.get("reviewed_at")}
    _db.execute("DELETE FROM credit_ratings WHERE ticker = ?", [t])
    _db.execute("INSERT INTO credit_ratings (ticker, status, rating, record_json, researched_at, reviewer, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [t, rec["status"], rec.get("rating"), json.dumps({k: v for k, v in rec.items()
                                                                  if k not in ("status", "rating", "reviewer", "reviewed_at")}),
                 rec["researched_at"], rec.get("reviewer"), rec.get("reviewed_at")])
    with _lock:
        _cache.pop(t, None)


def set_status(ticker: str, status: str, reviewer: str, rating: Optional[str] = None) -> dict:
    """The owner's decision: ACCEPTED (optionally with a corrected rating) or REVOKED."""
    if status not in ("ACCEPTED", "REVOKED"):
        raise ValueError(status)
    rec = stored(ticker)
    if not rec:
        raise KeyError(ticker)
    if rating is not None:
        r = normalize("S&P", rating)
        if not r:
            raise ValueError(f"not an S&P-scale rating: {rating}")
        rec["rating"] = r
    rec.update(status=status, reviewer=reviewer, reviewed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    from src.data import db as _db
    _db.execute("UPDATE credit_ratings SET status = ?, rating = ?, reviewer = ?, reviewed_at = ? WHERE ticker = ?",
                [status, rec["rating"], reviewer, rec["reviewed_at"], str(ticker).upper()])
    with _lock:
        _cache.pop(str(ticker).upper(), None)
    return rec


# ── the run-time lookup ──────────────────────────────────────────────────────

def is_us_ticker(ticker: str) -> bool:
    t = str(ticker or "").upper()
    return bool(t) and "." not in t and re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t) is not None


def research_enabled() -> bool:
    return (os.getenv("CREDIT_RATING_RESEARCH", "1") != "0" and not os.getenv("PYTEST_CURRENT_TEST"))


def _stale(rec: Optional[dict]) -> bool:
    if not rec:
        return True
    if rec.get("status") in ("ACCEPTED", "REVOKED"):
        return False
    try:
        at = datetime.fromisoformat(str(rec.get("researched_at")))
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except Exception:                                      # noqa: BLE001
        return True
    return datetime.now(timezone.utc) - at > timedelta(days=REFRESH_DAYS.get(rec.get("status"), 30))


def _company(ticker: str) -> Optional[str]:
    try:
        from src.data.regional_comps import get_fmp_classification as _gfc
        return _gfc(ticker).get("name") or _gfc(ticker).get("companyName")
    except Exception:                                      # noqa: BLE001
        return None


def _work(ticker: str) -> None:
    try:
        save(research(ticker, _company(ticker)))
    except Exception as exc:                               # noqa: BLE001
        logger.warning("credit rating research failed for %s: %s", ticker, exc)
    finally:
        with _lock:
            _inflight.pop(ticker, None)


def prefetch(ticker: str) -> None:
    """Start the research in the background when a US ticker has no fresh entry (called at run start)."""
    t = str(ticker or "").upper()
    if not (is_us_ticker(t) and research_enabled()):
        return
    try:
        if not _stale(stored(t)):
            return
    except Exception:                                      # noqa: BLE001
        return
    with _lock:
        if t in _inflight:
            return
        th = threading.Thread(target=_work, args=(t,), name=f"credit-rating-{t}", daemon=True)
        _inflight[t] = th
    th.start()


def _owner_registry(ticker: str) -> Optional[dict]:
    try:
        from src.data import valuation_constants as _vc
        return ((_vc.load().get("credit_ratings") or {}).get("entries") or {}).get(str(ticker or "").upper())
    except Exception:                                      # noqa: BLE001
        return None


def lookup(ticker: str, *, wait_s: float = WAIT_S) -> dict:
    """{rating (priced or None), status, record, source}. Waits for a running (or newly started) research up to
    wait_s; past that the run prices on the rating implied by interest cover and the research lands for the next run."""
    t = str(ticker or "").upper()
    with _lock:
        if t in _cache:
            return _cache[t]
    out = {"rating": None, "status": None, "record": None, "source": None}
    reg = _owner_registry(t)
    if reg and str(reg.get("status") or "").upper() in ("ACCEPTED", "REVOKED"):
        st = str(reg["status"]).upper()
        out.update(rating=reg.get("rating") if st == "ACCEPTED" else None, status=st, record=reg,
                   source="owner registry (valuation_constants.credit_ratings)")
    else:
        try:
            prefetch(t)
            th = _inflight.get(t)
            if th is not None:
                th.join(timeout=wait_s)
            rec = stored(t)
        except Exception:                                  # noqa: BLE001
            rec = None
        if rec:
            out.update(rating=rec.get("rating") if rec.get("status") in PRICED else None, status=rec.get("status"),
                       record=rec, source="researched (credit_ratings)")
        elif _inflight.get(t) is not None:
            out.update(status="PENDING")
    with _lock:
        if out["status"] != "PENDING":
            _cache[t] = out
    return out


def describe(lk: dict) -> Optional[str]:
    """One line for the run's flags and the WACC tab."""
    rec, st = lk.get("record") or {}, lk.get("status")
    if not st:
        return None
    ags = "; ".join(f"{a} {f.get('rating')}" + (f" ({f.get('outlook')})" if f.get("outlook") else "")
                    + (" confirmed by " + ", ".join(c.get("kind", "?") for c in f.get("confirmed_by") or [])
                       if f.get("confirmed_by") else " unconfirmed")
                    for a, f in (rec.get("agencies") or {}).items())
    if lk.get("rating"):
        return (f"Cost of debt on the agency rating {lk['rating']} ({st.lower()}: {ags or lk.get('source')}) -- "
                "not the rating implied by interest cover")
    if st == "PENDING":
        return "Agency rating research still running: cost of debt on the rating implied by interest cover for this run"
    if st == "UNRATED":
        return "No agency rating found (unrated): cost of debt on the rating implied by interest cover"
    if st == "REVOKED":
        return "Agency rating revoked by the owner: cost of debt on the rating implied by interest cover"
    if st == "FAILED":
        return "Agency rating research failed: cost of debt on the rating implied by interest cover"
    return (f"Agency rating {rec.get('rating')} PROPOSED, not priced ({rec.get('basis')}; {ags}): cost of debt on the "
            "rating implied by interest cover until the owner accepts it")


def reset_cache() -> None:
    with _lock:
        _cache.clear()

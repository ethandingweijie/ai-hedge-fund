"""Daily production health report (roadmap #4, 2026-11-28).

Every defect found in the 2026-11-27 audit failed QUIETLY: stale comps fell back to static multiples, a terminal-growth
flag crashed the engine on some names, a Malaysian line was valued in USD, a weekly refresh could skip. Each left a
trace in data that already exists. This module reads those traces and says, per check, ok / warn / fail.

    report = compute_health(queue=None)       # queue: the /admin/diag Redis snapshot, when the caller has one
    post_to_slack(report)                     # only when something is warn/fail (or PROD_HEALTH_SLACK_ALWAYS)

Read-only apart from `record()`, which keeps one row per report in `prod_health_reports`. Never raises: a check that
cannot be computed reports status "unknown" with the error, so the report itself cannot become a silent failure.
"""
from __future__ import annotations

import json
import logging
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_RANK = {OK: 0, UNKNOWN: 1, WARN: 2, FAIL: 3}

WINDOW_HOURS = 24
#: comps: the weekly refresh runs Saturday; MAX_AGE_DAYS (14) is where every field ages out to static tables.
COMPS_WARN_DAYS, COMPS_FAIL_DAYS = 8.0, 14.0
#: daily scheduled jobs: one missed day warns, two fail.
DAILY_WARN_H, DAILY_FAIL_H = 30.0, 54.0
#: owner-set constants on a quarterly clock.
REVIEW_WARN_DAYS, REVIEW_FAIL_DAYS = 95.0, 125.0

#: (check id, table, timestamp column, warn hours, fail hours, what it is)
SCHEDULED_TABLES: tuple[tuple[str, str, str, float, float, str], ...] = (
    ("job_iv15_sweep", "iv15_sweep_log", "run_at", DAILY_WARN_H, DAILY_FAIL_H, "IV15 alert sweep (daily 00:00 UTC)"),
    ("job_hundred_q", "hq_runs", "created_at", DAILY_WARN_H, DAILY_FAIL_H, "100-Question screener sweep (daily)"),
    ("job_valuation_outcomes", "valuation_outcome_sweeps", "finished_at", DAILY_WARN_H, DAILY_FAIL_H,
     "valuation outcome labels (daily 04:40 UTC)"),
    ("job_news_poll", "news_poll_state", "polled_at", 2.0, 6.0, "news poll (every 15 min)"),
)
# macro_regime_daily is written on demand by a run, not on a schedule (2026-11-28): not a freshness check.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v) -> Optional[datetime]:
    """ISO text or datetime -> aware UTC. Naive values are read as UTC (prod runs on UTC)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _check(cid: str, status: str, summary: str, *, value=None, threshold=None, items=None) -> dict:
    out = {"id": cid, "status": status, "summary": summary}
    if value is not None:
        out["value"] = value
    if threshold is not None:
        out["threshold"] = threshold
    if items:
        out["items"] = items[:25]
    return out


def _safe(cid: str, fn: Callable[[], list]) -> list:
    try:
        return fn()
    except Exception as exc:                               # noqa: BLE001
        logger.warning("prod_health %s failed: %s", cid, exc)
        return [_check(cid, UNKNOWN, f"check could not run: {type(exc).__name__}: {exc}",
                       items=[traceback.format_exc()[-600:]])]


def _row_get(row, key, idx=0):
    try:
        return row[key]
    except Exception:                                      # noqa: BLE001
        return row[idx]


# ── Runs in the window ─────────────────────────────────────────────────────

def _runs_in_window(hours: float) -> list[dict]:
    from src.data import db
    since = _now() - timedelta(hours=hours)
    # run_at is ISO text (naive UTC on prod), so the window is a text comparison in SQL -- a payload is ~370KB and
    # only the window's rows are read. A day's slack in the SQL bound; the exact cut is made after parsing.
    lo = (since - timedelta(days=1)).replace(tzinfo=None).isoformat()
    rows = db.query("SELECT run_id, ticker, run_at, full_result_json FROM web_runs "
                    "WHERE is_checkpoint = 0 AND run_at >= ? ORDER BY run_at DESC LIMIT 200", [lo]) or []
    out = []
    for r in rows:
        at = _parse_ts(_row_get(r, "run_at", 2))
        if at is None or at < since:
            continue
        try:
            payload = json.loads(_row_get(r, "full_result_json", 3) or "{}")
        except (TypeError, ValueError):
            payload = {}
        out.append({"run_id": _row_get(r, "run_id", 0), "ticker": _row_get(r, "ticker", 1),
                    "run_at": at.isoformat(), "payload": payload})
    return out


_STATIC_FX_RE = re.compile(r"static (FX )?fallback", re.I)


def run_checks(runs: list[dict]) -> list[dict]:
    """Per-run traces of silent fallbacks: static multiples, static FX, engine failure."""
    static_mult, static_fx, failed, ok_runs = [], [], [], 0
    for run in runs:
        data = (run["payload"] or {}).get("data", run["payload"] or {})
        dr = (data.get("dcf_range") or {}).get(run["ticker"])
        tag = f"{run['ticker']} {run['run_at'][:16]}"
        if not isinstance(dr, dict):
            failed.append(f"{tag}: no valuation in the payload")
            continue
        base = dr.get("base") or {}
        if not isinstance(base.get("intrinsic_value"), (int, float)):
            failed.append(f"{tag}: base intrinsic value missing")
            continue
        ok_runs += 1
        mu = dr.get("multiples_used") or {}
        fields = mu.get("fields") or {}
        weighted = {str(e.get("value_key") or e.get("method") or "") for e in (base.get("effective_weights") or [])}
        st = sorted(f for f, m in fields.items() if isinstance(m, dict) and m.get("basis") == "static")
        if mu.get("no_peer_multiples") or st:
            static_mult.append(f"{tag}: static {', '.join(st) or 'all fields'}"
                               + (" (blend has multiples legs)" if any("/" in w for w in weighted) else ""))
        flags = " ".join(str(f) for f in (base.get("forward_flags") or []))
        if _STATIC_FX_RE.search(str(dr.get("fx_note") or "")) or _STATIC_FX_RE.search(flags):
            static_fx.append(f"{tag}: {str(dr.get('fx_note') or '')[:140]}")
    n = len(runs)
    out = [_check("runs_window", OK, f"{n} completed run(s) in the last {WINDOW_HOURS}h", value=n)]
    out.append(_check("engine_failures", FAIL if failed else OK,
                      f"{len(failed)} run(s) with no valuation" if failed else "every run produced a valuation",
                      value=len(failed), items=failed))
    frac = (len(static_mult) / ok_runs) if ok_runs else 0.0
    out.append(_check("static_multiples", FAIL if frac >= 0.5 and len(static_mult) >= 2 else (WARN if static_mult else OK),
                      (f"{len(static_mult)} of {ok_runs} run(s) priced a field on the static table (comps missing or "
                       f"stale)") if static_mult else "every run priced its multiples on live comps",
                      value=len(static_mult), threshold="0 warns; half the runs fails", items=static_mult))
    out.append(_check("static_fx", WARN if static_fx else OK,
                      f"{len(static_fx)} run(s) converted on the static FX fallback table" if static_fx
                      else "no run used the static FX table", value=len(static_fx), items=static_fx))
    return out


# ── Freshness of stores and scheduled jobs ─────────────────────────────────

def comps_checks() -> list[dict]:
    from src.data import db
    from src.data.regional_comps import CROSS_MARKET_EXCHANGE, EXCHANGES, latest_refresh_age_days
    items, worst = [], OK
    for ex in EXCHANGES:
        age = latest_refresh_age_days(ex)
        st = FAIL if (age is None or age > COMPS_FAIL_DAYS) else (WARN if age > COMPS_WARN_DAYS else OK)
        worst = max(worst, st, key=_RANK.get)
        items.append(f"{ex}: {'never' if age is None else f'{age:.1f} days'}{'' if st == OK else ' -- ' + st.upper()}")
    row = db.query_one("SELECT MAX(computed_at) AS newest FROM regional_comps_members WHERE exchange = ?",
                       [CROSS_MARKET_EXCHANGE])
    xt = _parse_ts(_row_get(row, "newest", 0)) if row else None
    xage = (_now() - xt).total_seconds() / 86400 if xt else None
    xst = FAIL if (xage is None or xage > COMPS_FAIL_DAYS) else (WARN if xage > COMPS_WARN_DAYS else OK)
    worst = max(worst, xst, key=_RANK.get)
    items.append(f"{CROSS_MARKET_EXCHANGE} (cross-market baskets): {'never' if xage is None else f'{xage:.1f} days'}"
                 f"{'' if xst == OK else ' -- ' + xst.upper()}")
    return [_check("comps_freshness", worst,
                   "regional comps fresh in every market" if worst == OK else
                   "a market's comps are stale: its runs fall back to static multiples",
                   threshold=f"warn > {COMPS_WARN_DAYS:.0f}d, fail > {COMPS_FAIL_DAYS:.0f}d", items=items)]


def scheduled_checks(tables=SCHEDULED_TABLES) -> list[dict]:
    from src.data import db
    out = []
    for cid, table, col, warn_h, fail_h, what in tables:
        def _one(cid=cid, table=table, col=col, warn_h=warn_h, fail_h=fail_h, what=what):
            row = db.query_one(f"SELECT MAX({col}) AS newest FROM {table}")
            at = _parse_ts(_row_get(row, "newest", 0)) if row else None
            if at is None:
                return [_check(cid, FAIL, f"{what}: no run on record")]
            h = (_now() - at).total_seconds() / 3600
            st = FAIL if h > fail_h else (WARN if h > warn_h else OK)
            return [_check(cid, st, f"{what}: last ran {h:.1f}h ago", value=round(h, 1),
                           threshold=f"warn > {warn_h:.0f}h, fail > {fail_h:.0f}h")]
        out += _safe(cid, _one)
    return out


def review_checks() -> list[dict]:
    """Owner-set constants on a quarterly clock (valuation_constants `reviewed.last_reviewed`)."""
    from src.data import valuation_constants as vc
    doc = vc.load()
    items, worst = [], OK
    for block, label in (("commodity_price_deck", "commodity price deck"), ("risk_free_by_market", "risk-free rates")):
        b = doc.get(block) or {}
        last = (b.get("reviewed") or {}).get("last_reviewed")
        at = _parse_ts(last) if last else None
        if at is None:
            st, txt = WARN, f"{label}: never reviewed"
        else:
            d = max((_now() - at).total_seconds() / 86400, 0.0)
            st = FAIL if d > REVIEW_FAIL_DAYS else (WARN if d > REVIEW_WARN_DAYS else OK)
            txt = f"{label}: reviewed {last} ({d:.0f} days ago)"
        worst = max(worst, st, key=_RANK.get)
        items.append(txt + ("" if st == OK else " -- " + st.upper()))
    return [_check("owner_reviews", worst, "owner-set constants within their quarterly review" if worst == OK
                   else "an owner-set constant is past its quarterly review",
                   threshold=f"warn > {REVIEW_WARN_DAYS:.0f}d, fail > {REVIEW_FAIL_DAYS:.0f}d", items=items)]


_ONGOING_RE = re.compile(r"j_ongoing=(\d+)")


def queue_checks(queue: Optional[dict]) -> list[dict]:
    """From the caller's Redis snapshot (the /admin/diag `queue` section shape)."""
    if not queue:
        return [_check("worker", UNKNOWN, "no queue snapshot (run from the web or worker process to include it)")]
    if not queue.get("redis_ready"):
        return [_check("worker", FAIL, "Redis is not reachable from the web process")]
    hc = str(queue.get("worker_health") or "")
    out = [_check("worker", OK if hc else FAIL,
                  f"worker heartbeat: {hc}" if hc else "no worker heartbeat: queued jobs will not run")]
    m = _ONGOING_RE.search(hc)
    stale = [k for k in (queue.get("in_progress") or []) if ":sched:" not in str(k)]
    if m and int(m.group(1)) == 0 and stale:
        out.append(_check("queue_phantoms", WARN, f"{len(stale)} in-progress key(s) with no job running",
                          items=[str(k) for k in stale]))
    else:
        out.append(_check("queue_phantoms", OK, "no orphaned in-progress keys"))
    return out


# ── Report ─────────────────────────────────────────────────────────────────

def compute_health(queue: Optional[dict] = None, hours: float = WINDOW_HOURS) -> dict:
    checks: list[dict] = []
    checks += _safe("runs", lambda: run_checks(_runs_in_window(hours)))
    checks += _safe("comps_freshness", comps_checks)
    checks += scheduled_checks()
    checks += _safe("owner_reviews", review_checks)
    checks += _safe("worker", lambda: queue_checks(queue))
    status = max((c["status"] for c in checks), key=_RANK.get, default=OK)
    return {"generated_at": _now().isoformat(timespec="seconds"), "window_hours": hours, "status": status,
            "counts": {s: sum(1 for c in checks if c["status"] == s) for s in (OK, WARN, FAIL, UNKNOWN)},
            "checks": checks}


_DDL_PG = ("CREATE TABLE IF NOT EXISTS prod_health_reports (id BIGSERIAL PRIMARY KEY, generated_at TEXT NOT NULL, "
           "status TEXT NOT NULL, report_json TEXT NOT NULL)")
_DDL_SQLITE = ("CREATE TABLE IF NOT EXISTS prod_health_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "generated_at TEXT NOT NULL, status TEXT NOT NULL, report_json TEXT NOT NULL)")


def record(report: dict) -> None:
    from src.data import db
    db.ensure_table(_DDL_PG if db.is_postgres() else _DDL_SQLITE)
    db.execute("INSERT INTO prod_health_reports (generated_at, status, report_json) VALUES (?, ?, ?)",
               [report["generated_at"], report["status"], json.dumps(report, default=str)])


_ICON = {OK: ":white_check_mark:", WARN: ":warning:", FAIL: ":red_circle:", UNKNOWN: ":grey_question:"}


def format_text(report: dict, only_problems: bool = True) -> str:
    lines = [f"Prod health {report['status'].upper()} -- {report['generated_at']} "
             f"({report['counts'][FAIL]} fail, {report['counts'][WARN]} warn, {report['counts'][UNKNOWN]} unknown)"]
    for c in report["checks"]:
        if only_problems and c["status"] == OK:
            continue
        lines.append(f"{_ICON.get(c['status'], '')} {c['id']}: {c['summary']}")
        for it in (c.get("items") or [])[:10]:
            lines.append(f"    - {it}")
    return "\n".join(lines)


def post_to_slack(report: dict) -> bool:
    """Posts when something is wrong (or always, with PROD_HEALTH_SLACK_ALWAYS). No-op without SLACK_WEBHOOK_URL."""
    url = os.environ.get("PROD_HEALTH_SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL")
    always = os.environ.get("PROD_HEALTH_SLACK_ALWAYS", "").strip().lower() in ("1", "true", "yes")
    if not url or (report["status"] == OK and not always):
        return False
    try:
        import requests
        r = requests.post(url, json={"text": format_text(report, only_problems=not always)}, timeout=10)
        return r.status_code < 300
    except Exception as exc:                               # noqa: BLE001
        logger.warning("prod_health slack post failed: %s", exc)
        return False

"""DD Alerts routes — backend for the Equitable web dashboard + admin trigger.

Phase C of the vertical slice (plan: mighty-gliding-graham.md).

Routes:
  POST /admin/dd-trigger        — synthesize a DD alert end-to-end (alert_dedup
                                  → mark_alerted → web_runs row → optional Slack
                                  post). Drives the slice without needing the
                                  real LLM agent or cron.
  GET  /api/dd-alerts           — list (paginated, filterable by direction/tier/date)
  GET  /api/dd-alerts/digest/today — today's aggregate (top drops/pumps/clusters)
  GET  /api/dd-alerts/{dd_run_id}  — single alert detail (hydrated from web_runs)

Admin endpoint gates on DB_UPLOAD_SECRET (same env var as other /admin/* routes
in app/backend/routes/admin.py — keeps the secret-management story consistent).

Slack delivery is best-effort: if SLACK_WEBHOOK_URL is unset, the trigger still
creates the alert + web_runs row and returns success — the Slack post just gets
logged as 'skipped'. This lets you smoke-test the dashboard without configuring
Slack first.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.backend.services.analysis_service import _connect, _ensure_web_runs_table

logger = logging.getLogger(__name__)
router = APIRouter()

# Same secret used by other admin routes (admin.py, db_upload.py, etc.)
ADMIN_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _row_to_alert_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Map dd_alerts row (with optional joined web_runs.full_result_json)
    → API response dict. Hydrates the report payload when present."""
    out = {
        "ticker":            row["ticker"],
        "last_direction":    row["last_direction"],
        "trigger_pct":       row["trigger_pct"],
        "trigger_price":     row["trigger_price"],
        "last_triggered_at": row["last_triggered_at"],
        "tier":              row["tier"],
        "alert_reason":      row["alert_reason"],
        "cluster_id":        row["cluster_id"],
        "dd_run_id":         row["dd_run_id"],
        "sent_status":       row["sent_status"],
    }
    # Hydrate report content from joined web_runs row, if present
    raw = None
    try:
        raw = row["full_result_json"]
    except (IndexError, KeyError):
        pass
    if raw:
        try:
            out["report"] = json.loads(raw).get("report")
        except Exception:
            out["report"] = None
    else:
        out["report"] = None
    return out


def _synthetic_report(ticker: str, pct_change: float, direction: str,
                      reason: str) -> dict[str, Any]:
    """Build a placeholder DD report for the admin trigger. Future Phase 3
    work replaces this with the real LLM agent's output."""
    sign = "+" if pct_change > 0 else ""
    return {
        "cause_summary": (
            f"[SYNTHETIC] {ticker} moved {sign}{pct_change*100:.1f}%. "
            f"Trigger reason: {reason}. This is a placeholder report from the "
            f"admin trigger — replace with real DD agent output in Phase 3."
        ),
        "thesis_impact": "thesis_under_review (synthetic admin trigger)",
        "recommended_action": (
            f"Review the {direction.lower()} catalyst. This synthetic alert "
            f"verifies the alert_dedup → mark_alerted → web_runs → Slack pipeline."
        ),
        "news_drivers": [
            {"title": "Synthetic news driver — replace with real news_search MCP output",
             "url": "https://example.com/synthetic-news",
             "publishedDate": datetime.now(timezone.utc).isoformat()},
        ],
        "filings": [
            {"form": "8-K", "filing_date": datetime.now(timezone.utc).date().isoformat(),
             "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
             "summary": "Synthetic 8-K — replace with real sec_edgar MCP output"},
        ],
        "insider_signal": "n/a (synthetic — no FMP call made)",
    }


def _insert_synthetic_web_run(run_id: str, ticker: str, report: dict,
                              trigger: dict) -> None:
    """Write a minimal web_runs row so the GET /api/dd-alerts list+detail
    JOIN can hydrate the report payload. Mirrors the analysis_service
    _save_web_run shape for forward-compat with the real agent."""
    _ensure_web_runs_table()  # idempotent — ensures table exists in fresh DBs
    full_result_json = json.dumps({
        "report": report,
        "trigger": trigger,
        "data": {"sector": "_synthetic", "profile_name": "_admin_trigger"},
    }, default=str)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO web_runs "
            "(run_id, run_at, ticker, model_name, full_result_json, "
            " is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                ticker.upper(),
                "synthetic-dd-trigger",
                full_result_json,
            ),
        )
        conn.commit()


def _ensure_tables_for_read() -> None:
    """Idempotent: ensure both web_runs and dd_alerts tables exist before
    a read query. Lets fresh-DB endpoints (no triggers ever fired) return
    empty lists / 404s gracefully instead of throwing OperationalError."""
    _ensure_web_runs_table()
    # Importing alert_dedup here ensures its DDL runs (via its _conn helper)
    from src.agents.dd import alert_dedup
    with alert_dedup._conn():
        pass   # _conn's context manager calls _ensure_table on entry


def _try_post_slack(*, ticker: str, pct: float, direction: str, reason: str,
                    report: dict, run_id: str) -> dict:
    """Best-effort Slack post. Returns a status dict (never raises).
    Skips silently if SLACK_WEBHOOK_URL not configured."""
    if not os.environ.get("SLACK_WEBHOOK_URL"):
        return {"posted": False, "reason": "SLACK_WEBHOOK_URL not set"}
    try:
        from src.agents.dd.slack_delivery import post_dd_report
        app_base_url = os.environ.get("APP_BASE_URL")  # optional deep-link
        resp = post_dd_report(
            ticker=ticker, pct_change=pct, direction=direction,
            reason=reason, report=report, run_id=run_id,
            app_base_url=app_base_url,
        )
        return {"posted": True, "status_code": resp.status_code}
    except Exception as exc:
        logger.exception(f"[dd-alerts] Slack post failed for {ticker}: {exc}")
        return {"posted": False, "reason": f"{type(exc).__name__}: {exc}"}


# ── Admin trigger ───────────────────────────────────────────────────────────

@router.post("/admin/dd-trigger")
def admin_dd_trigger(
    secret: str = "",
    ticker: str = Query(..., description="Ticker symbol, e.g. PEGA"),
    pct: float = Query(..., description="Signed pct change as decimal, e.g. -0.11 for -11%"),
    direction: str = Query("auto", description="DROP | PUMP | auto (infer from sign of pct)"),
    price: float | None = Query(None, description="Optional current price; defaults to 100*(1+pct)"),
    tier: str = Query("admin_trigger", description="Tier label for the alert row"),
    force: bool = Query(False, description="Bypass cooldown gate (for testing)"),
):
    """Synthesize a DD alert end-to-end. Used by the vertical slice to verify
    alert_dedup → mark_alerted → web_runs → Slack delivery without needing
    the real LLM agent / cron / MCP servers.

    Returns a summary dict with the resolved direction, eligibility decision,
    dd_run_id, and Slack delivery status.

    Auth: gated on DB_UPLOAD_SECRET env var (same as other /admin/* endpoints).
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Resolve direction
    if direction == "auto":
        direction = "DROP" if pct < 0 else "PUMP"
    if direction not in ("DROP", "PUMP"):
        raise HTTPException(status_code=400, detail=f"direction must be DROP, PUMP, or auto; got {direction}")

    # Resolve price (default: assume prior close was $100)
    current_price = price if price is not None else round(100.0 * (1.0 + pct), 4)
    if current_price <= 0:
        raise HTTPException(status_code=400, detail=f"current_price must be > 0; got {current_price}")

    # Late import to avoid circular dependencies + keep route module light
    from src.agents.dd import alert_dedup

    # Cooldown check (or bypass)
    if force:
        eligible, reason = True, "admin_force_override"
    else:
        eligible, reason = alert_dedup.check_alert_eligibility(
            ticker, current_pct=pct, current_price=current_price,
        )

    if not eligible:
        return {
            "ok": True,
            "fired": False,
            "ticker": ticker.upper(),
            "direction": direction,
            "pct": pct,
            "price": current_price,
            "eligibility_reason": reason,
            "note": "Alert blocked by cooldown. Pass &force=true to bypass.",
        }

    # Synthesize report + write web_runs row
    run_id = str(uuid.uuid4())
    report = _synthetic_report(ticker, pct, direction, reason)
    trigger_meta = {
        "source": "admin_trigger",
        "ticker": ticker.upper(),
        "pct": pct,
        "price": current_price,
        "direction": direction,
        "reason": reason,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
    _insert_synthetic_web_run(run_id, ticker, report, trigger_meta)

    # Best-effort Slack post (silent skip if SLACK_WEBHOOK_URL absent)
    slack_status = _try_post_slack(
        ticker=ticker, pct=pct, direction=direction, reason=reason,
        report=report, run_id=run_id,
    )

    # Persist the alert row (records cooldown + visible to dashboard)
    alert_dedup.mark_alerted(
        ticker=ticker, direction=direction, pct=pct, price=current_price,
        tier=tier, reason=reason,
        quote={"changesPercentage": pct * 100, "price": current_price, "_source": "admin_trigger"},
        dd_run_id=run_id,
        sent_status="sent" if slack_status.get("posted") else "pending",
    )

    return {
        "ok": True,
        "fired": True,
        "ticker": ticker.upper(),
        "direction": direction,
        "pct": pct,
        "price": current_price,
        "eligibility_reason": reason,
        "dd_run_id": run_id,
        "slack": slack_status,
    }


# ── Read endpoints (web dashboard backend) ──────────────────────────────────

@router.get("/api/dd-alerts")
def list_dd_alerts(
    since: str | None = Query(None, description="ISO date or datetime; alerts at or after this time"),
    until: str | None = Query(None, description="ISO date or datetime; alerts strictly before this time"),
    direction: str | None = Query(None, description="DROP | PUMP | None (all)"),
    tier: str | None = Query(None, description="e.g. tier1_held, tier2_active, news_trigger, admin_trigger"),
    ticker: str | None = Query(None, description="Optional ticker filter"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Paginated list of DD alerts. Joins with web_runs to hydrate the
    report payload when dd_run_id is set. Most-recent first."""
    where = ["1=1"]
    params: list = []
    if since:     where.append("a.last_triggered_at >= ?"); params.append(since)
    if until:     where.append("a.last_triggered_at <  ?"); params.append(until)
    if direction: where.append("a.last_direction = ?");      params.append(direction)
    if tier:      where.append("a.tier = ?");                params.append(tier)
    if ticker:    where.append("a.ticker = ?");              params.append(ticker.upper())

    sql = f"""
        SELECT a.ticker, a.last_direction, a.trigger_price, a.trigger_pct,
               a.last_triggered_at, a.tier, a.alert_reason, a.cluster_id,
               a.dd_run_id, a.sent_status, w.full_result_json
        FROM dd_alerts a
        LEFT JOIN web_runs w ON w.run_id = a.dd_run_id
        WHERE {' AND '.join(where)}
        ORDER BY a.last_triggered_at DESC
        LIMIT ?
    """
    params.append(limit)
    _ensure_tables_for_read()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_alert_dict(r) for r in rows]


@router.get("/api/dd-alerts/digest/today")
def todays_digest():
    """Aggregate today's alerts: top 10 drops + top 10 pumps + active sector
    clusters. Used by the dashboard's EOD digest panel.

    'Today' = UTC date for now. (DST-aware EST/ET handling is a Phase 2 follow-up
    in the full plan; the slice uses UTC for simplicity.)"""
    today = datetime.now(timezone.utc).date().isoformat()
    _ensure_tables_for_read()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        drops = conn.execute(
            "SELECT a.*, w.full_result_json FROM dd_alerts a "
            "LEFT JOIN web_runs w ON w.run_id = a.dd_run_id "
            "WHERE a.last_triggered_at >= ? AND a.last_direction = 'DROP' "
            "ORDER BY a.trigger_pct ASC LIMIT 10",
            (today,),
        ).fetchall()
        pumps = conn.execute(
            "SELECT a.*, w.full_result_json FROM dd_alerts a "
            "LEFT JOIN web_runs w ON w.run_id = a.dd_run_id "
            "WHERE a.last_triggered_at >= ? AND a.last_direction = 'PUMP' "
            "ORDER BY a.trigger_pct DESC LIMIT 10",
            (today,),
        ).fetchall()
        clusters = conn.execute(
            "SELECT cluster_id, last_direction, COUNT(*) as n, "
            "       AVG(trigger_pct) as median_pct "
            "FROM dd_alerts WHERE last_triggered_at >= ? "
            "AND cluster_id IS NOT NULL "
            "GROUP BY cluster_id, last_direction",
            (today,),
        ).fetchall()
    return {
        "date":     today,
        "drops":    [_row_to_alert_dict(r) for r in drops],
        "pumps":    [_row_to_alert_dict(r) for r in pumps],
        "clusters": [
            {"cluster_id": r["cluster_id"], "direction": r["last_direction"],
             "n": r["n"], "median_pct": r["median_pct"]}
            for r in clusters
        ],
    }


@router.get("/api/dd-alerts/{dd_run_id}")
def get_alert_detail(dd_run_id: str):
    """Single full DD report (hydrated from web_runs)."""
    _ensure_tables_for_read()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT a.*, w.full_result_json FROM dd_alerts a "
            "LEFT JOIN web_runs w ON w.run_id = a.dd_run_id "
            "WHERE a.dd_run_id = ? "
            "ORDER BY a.last_triggered_at DESC LIMIT 1",
            (dd_run_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No alert with dd_run_id={dd_run_id}")
    return _row_to_alert_dict(row)

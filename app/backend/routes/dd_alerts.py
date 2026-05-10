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
import threading
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
    """Build a placeholder DD report — used as the in-flight 'agent generating'
    payload AND as the fallback when the real LLM agent fails."""
    sign = "+" if pct_change > 0 else ""
    return {
        "cause_summary": (
            f"[SYNTHETIC FALLBACK] {ticker} moved {sign}{pct_change*100:.1f}%. "
            f"Trigger reason: {reason}. The real LLM agent did not produce a "
            f"parseable report — this placeholder preserves the alert."
        ),
        "thesis_impact": "thesis_under_review (LLM agent fell back to synthetic)",
        "recommended_action": (
            f"Review the {direction.lower()} catalyst manually — the automated "
            f"DD agent could not synthesize a report this run."
        ),
        "news_drivers": [],
        "filings":      [],
        "insider_signal": "n/a (synthetic fallback)",
    }


def _agent_in_flight_report(ticker: str, pct_change: float, direction: str,
                            reason: str) -> dict[str, Any]:
    """Placeholder report inserted IMMEDIATELY when an alert fires with
    agent_mode=real. The dashboard shows this until the background agent
    finishes (~30-90s) and updates the row."""
    sign = "+" if pct_change > 0 else ""
    return {
        "cause_summary": (
            f"⏳ DD agent generating report for {ticker} ({sign}{pct_change*100:.1f}%) — "
            f"web search + synthesis in progress. Refresh in ~60s."
        ),
        "thesis_impact": "(pending — agent running)",
        "recommended_action": "(pending — agent running)",
        "news_drivers": [],
        "filings":      [],
        "insider_signal": "(pending — agent running)",
    }


def _upsert_dd_report(run_id: str, ticker: str, report: dict,
                      trigger: dict, model_name: str) -> None:
    """Write/replace a row in dd_reports (NOT web_runs).

    Phase 2B refactor: DD reports live in their own table now. The
    History tab queries web_runs and never sees DD content; the Auto
    Due-D dashboard JOINs dd_alerts → dd_reports for full hydration.

    Used in 3 places:
      1. Initial 'agent in flight' placeholder when alert fires
      2. Background thread on agent SUCCESS — replaces with real report
      3. Background thread on agent FAILURE — replaces with synthetic fallback
    """
    from src.agents.dd import alert_dedup
    full_result_json = json.dumps({
        "report": report,
        "trigger": trigger,
        "data": {"sector": "_dd_alert", "profile_name": "_dd_agent"},
    }, default=str)
    alert_dedup.upsert_dd_report(
        run_id=run_id,
        ticker=ticker,
        model_name=model_name,
        full_result_json=full_result_json,
    )


def _update_alert_sent_status(run_id: str, sent_status: str) -> None:
    """Patch dd_alerts.sent_status after the background agent finishes.
    Bypasses alert_dedup.mark_alerted to avoid touching the cooldown PK row's
    other fields. Idempotent — silent no-op if row doesn't exist."""
    from src.agents.dd import alert_dedup
    with alert_dedup._conn() as conn:
        conn.execute(
            "UPDATE dd_alerts SET sent_status = ? WHERE dd_run_id = ?",
            (sent_status, run_id),
        )
        conn.commit()


def _ensure_tables_for_read() -> None:
    """Idempotent: ensure dd_alerts + dd_reports tables exist before any
    read query. Lets fresh-DB endpoints (no triggers ever fired) return
    empty lists / 404s gracefully instead of throwing OperationalError.

    Phase 2B: web_runs is no longer touched by DD reads — DD content
    lives in dd_reports. We still call _ensure_web_runs_table for
    backward compatibility with admin/list_dd_alerts callers that may
    coincidentally have an old DB without web_runs.
    """
    _ensure_web_runs_table()
    # Importing alert_dedup here ensures its DDL runs (creates BOTH
    # dd_alerts and dd_reports via the _conn context manager).
    from src.agents.dd import alert_dedup
    with alert_dedup._conn():
        pass


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


# ── Sector cluster helpers (Phase 2C) ──────────────────────────────────────


def _synthetic_sector_report(sector: str, direction: str, members: list[str],
                             median_pct: float) -> dict[str, Any]:
    """Placeholder used as fallback when the sector LLM agent fails."""
    sign = "+" if median_pct >= 0 else ""
    return {
        "sector": sector,
        "direction": direction,
        "cluster_members": members,
        "cause_summary": (
            f"[SYNTHETIC FALLBACK] {len(members)} {sector} names moved "
            f"{sign}{median_pct*100:.1f}% (median) in the same direction today. "
            f"The sector LLM agent did not produce a parseable report — manual "
            f"review recommended."
        ),
        "thesis_impact": "thesis_under_review (sector LLM agent fell back to synthetic)",
        "recommended_action": (
            f"Review sector-wide catalysts manually. Consider sector ETF "
            f"action, recent macro / regulatory news, and cohort earnings."
        ),
        "news_drivers": [],
        "filings":      [],
        "insider_signal": "n/a (synthetic fallback)",
    }


def _agent_in_flight_sector_report(sector: str, direction: str, members: list[str],
                                   median_pct: float) -> dict[str, Any]:
    """Placeholder shown to the dashboard while the background agent runs."""
    sign = "+" if median_pct >= 0 else ""
    return {
        "sector": sector,
        "direction": direction,
        "cluster_members": members,
        "cause_summary": (
            f"⏳ Sector DD agent generating report for {sector}/{direction} "
            f"({len(members)} names, median {sign}{median_pct*100:.1f}%). "
            f"Web search + synthesis in progress. Refresh in ~60s."
        ),
        "thesis_impact": "(pending — sector agent running)",
        "recommended_action": "(pending — sector agent running)",
        "news_drivers": [],
        "filings":      [],
        "insider_signal": "(pending — sector agent running)",
    }


def _try_post_sector_slack(*, sector: str, direction: str, members: list[str],
                           median_pct: float, report: dict, run_id: str) -> dict:
    """Best-effort Slack post for a sector cluster. Never raises."""
    if not os.environ.get("SLACK_WEBHOOK_URL"):
        return {"posted": False, "reason": "SLACK_WEBHOOK_URL not set"}
    try:
        from src.agents.dd.slack_delivery import post_sector_cluster
        app_base_url = os.environ.get("APP_BASE_URL")
        resp = post_sector_cluster(
            sector=sector, direction=direction, members=members,
            median_pct=median_pct, report=report, run_id=run_id,
            app_base_url=app_base_url,
        )
        return {"posted": True, "status_code": resp.status_code}
    except Exception as exc:
        logger.exception(f"[dd-alerts] Sector Slack post failed for {sector}/{direction}: {exc}")
        return {"posted": False, "reason": f"{type(exc).__name__}: {exc}"}


def _real_sector_agent_worker(
    *, cluster_id: str, sector: str, direction: str,
    members: list[tuple[str, float, float]],   # [(ticker, pct, price), ...]
    median_pct: float, trigger_meta: dict,
) -> None:
    """Background-thread entry point for the sector LLM agent.

    Mirrors _real_agent_worker but for sector clusters: runs the sector
    DD agent, replaces the in-flight placeholder, posts the cluster Slack
    message. On any failure, falls back to a synthetic report so the
    cluster alert is preserved.
    """
    try:
        from src.agents.dd.sector_dd_agent import run_sector_dd_agent
        from src.agents.dd.dd_agent import DDAgentError

        members_tickers = [m[0] for m in members]
        logger.info(f"[sector-agent] starting {sector}/{direction} (n={len(members)})")

        try:
            report = run_sector_dd_agent(sector=sector, direction=direction, members=members)
            report_dict = report.model_dump()
            model_name  = "dd_sector_agent_qwen"
            log_status  = "sent"
            logger.info(f"[sector-agent] {sector}/{direction} succeeded — posting to Slack")
        except DDAgentError as exc:
            logger.warning(f"[sector-agent] {sector}/{direction} failed ({exc}) — synthetic fallback")
            report_dict = _synthetic_sector_report(sector, direction, members_tickers, median_pct)
            model_name  = "dd_sector_agent_qwen_FALLBACK"
            log_status  = "sent_synthetic_fallback"
        except Exception as exc:
            logger.exception(f"[sector-agent] {sector}/{direction} unexpected error: {exc}")
            report_dict = _synthetic_sector_report(sector, direction, members_tickers, median_pct)
            model_name  = "dd_sector_agent_qwen_ERROR"
            log_status  = "sent_synthetic_fallback"

        try:
            _upsert_dd_report(cluster_id, sector, report_dict, trigger_meta, model_name)
        except Exception as exc:
            logger.exception(f"[sector-agent] {sector}/{direction} dd_reports upsert failed: {exc}")

        slack_status = _try_post_sector_slack(
            sector=sector, direction=direction, members=members_tickers,
            median_pct=median_pct, report=report_dict, run_id=cluster_id,
        )
        if not slack_status.get("posted"):
            log_status = log_status + "_no_slack"

        # Patch each member's sent_status to reflect cluster delivery
        try:
            from src.agents.dd import alert_dedup
            with alert_dedup._conn() as conn:
                conn.execute(
                    "UPDATE dd_alerts SET sent_status = ? WHERE cluster_id = ?",
                    (log_status, cluster_id),
                )
                conn.commit()
        except Exception as exc:
            logger.exception(f"[sector-agent] {sector}/{direction} member sent_status update failed: {exc}")

        logger.info(f"[sector-agent] {sector}/{direction} background run COMPLETE (status={log_status})")
    except Exception as outer_exc:
        logger.exception(f"[sector-agent] catastrophic failure for {sector}/{direction}: {outer_exc}")


def _real_agent_worker(
    *,
    run_id:          str,
    ticker:          str,
    direction:       str,
    pct:             float,
    current_price:   float,
    prior_direction: str | None,
    reason:          str,
    trigger_meta:    dict,
) -> None:
    """Background-thread entry point for the real LLM DD agent.

    Sequence:
      1. Run the LLM agent (web search + Qwen synthesis, ~30-90s)
      2. Replace the in-flight web_runs placeholder with the real report
      3. Post to Slack (with the real report content)
      4. Patch dd_alerts.sent_status

    On any exception:
      • Replace the placeholder with a synthetic fallback report
      • Post Slack with the fallback so the alert still notifies
      • Mark sent_status="sent_synthetic_fallback" so dashboard / monitoring
        can distinguish real vs degraded runs

    Never raises — this runs in a daemon thread, so an unhandled exception
    would just vanish into the void.
    """
    try:
        from src.agents.dd.dd_agent import run_dd_agent, DDAgentError

        logger.info(f"[dd-agent] starting background run for {ticker} (run_id={run_id})")
        try:
            dd_report = run_dd_agent(
                ticker=ticker,
                direction=direction,
                pct_change=pct,
                current_price=current_price,
                prior_direction=prior_direction,
                reason=reason,
            )
            report_dict = dd_report.model_dump()
            model_name  = "dd_agent_qwen"
            sent_status = "sent"
            logger.info(f"[dd-agent] {ticker} succeeded — posting to Slack")
        except DDAgentError as exc:
            logger.warning(f"[dd-agent] {ticker} failed ({exc}) — using synthetic fallback")
            report_dict = _synthetic_report(ticker, pct, direction, reason)
            model_name  = "dd_agent_qwen_FALLBACK"
            sent_status = "sent_synthetic_fallback"
        except Exception as exc:
            logger.exception(f"[dd-agent] {ticker} unexpected error — using synthetic fallback: {exc}")
            report_dict = _synthetic_report(ticker, pct, direction, reason)
            model_name  = "dd_agent_qwen_ERROR"
            sent_status = "sent_synthetic_fallback"

        # 2. Replace the in-flight placeholder web_runs row
        try:
            _upsert_dd_report(run_id, ticker, report_dict, trigger_meta, model_name)
        except Exception as exc:
            logger.exception(f"[dd-agent] {ticker} web_runs upsert failed: {exc}")

        # 3. Post to Slack (best-effort)
        slack_status = _try_post_slack(
            ticker=ticker, pct=pct, direction=direction, reason=reason,
            report=report_dict, run_id=run_id,
        )
        if not slack_status.get("posted"):
            sent_status = sent_status + "_no_slack"

        # 4. Patch dd_alerts.sent_status
        try:
            _update_alert_sent_status(run_id, sent_status)
        except Exception as exc:
            logger.exception(f"[dd-agent] {ticker} sent_status update failed: {exc}")

        logger.info(f"[dd-agent] {ticker} background run COMPLETE (sent_status={sent_status})")
    except Exception as outer_exc:
        # Catastrophic failure — log so we have visibility, but never raise.
        logger.exception(f"[dd-agent] catastrophic failure for {ticker}: {outer_exc}")


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
    agent_mode: str = Query(
        "real",
        description="real | synthetic | off — 'real' runs the LLM DD agent in a background "
                    "thread (Slack posts ~30-90s later with the full report). "
                    "'synthetic' uses the placeholder + Slacks immediately (legacy behavior). "
                    "'off' records the alert but skips both agent and Slack.",
    ),
):
    """Fire a DD alert end-to-end.

    Default (agent_mode=real, async per Phase 2A):
      1. Eligibility check → cooldown gate
      2. Capture prior_direction (for prompt routing)
      3. Insert dd_alerts row + 'agent in flight' web_runs placeholder
      4. Spawn background thread → real LLM agent → real Slack post
      5. Return immediately (~50ms) with dd_run_id and status='agent_running'

    The dashboard polls /api/dd-alerts every 5min, so the in-flight placeholder
    is visible during the 30-90s the agent runs, then auto-replaced with the
    real report on the next refresh.

    Synthetic mode (agent_mode=synthetic):
      Original sync behavior — instant Slack post with placeholder content.
      Used by tests and as a manual escape hatch.

    Off mode (agent_mode=off):
      Records the alert and cooldown but skips both LLM agent AND Slack post.
      Useful for backfilling or testing the cooldown engine in isolation.

    Auth: gated on DB_UPLOAD_SECRET env var (same as other /admin/* endpoints).
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    if agent_mode not in ("real", "synthetic", "off"):
        raise HTTPException(status_code=400, detail=f"agent_mode must be real|synthetic|off; got {agent_mode}")

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

    # Capture prior_direction BEFORE check_alert_eligibility / mark_alerted
    # because those touch the latest-alert row. The DD agent needs to know
    # what direction the previous alert went to pick the right system prompt
    # (e.g. DROP-after-PUMP gets the Reversal "Narrative Shift" prompt).
    prior = alert_dedup.get_latest_alert(ticker)
    prior_direction = prior.last_direction if prior else None

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

    run_id = str(uuid.uuid4())
    trigger_meta = {
        "source": "admin_trigger",
        "ticker": ticker.upper(),
        "pct": pct,
        "price": current_price,
        "direction": direction,
        "reason": reason,
        "prior_direction": prior_direction,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Branch on agent_mode ───────────────────────────────────────────────

    if agent_mode == "synthetic":
        # Sync flow — original Phase C behavior. Useful for tests and manual
        # verification without burning Qwen tokens.
        report = _synthetic_report(ticker, pct, direction, reason)
        _upsert_dd_report(run_id, ticker, report, trigger_meta, "dd_synthetic_trigger")
        slack_status = _try_post_slack(
            ticker=ticker, pct=pct, direction=direction, reason=reason,
            report=report, run_id=run_id,
        )
        alert_dedup.mark_alerted(
            ticker=ticker, direction=direction, pct=pct, price=current_price,
            tier=tier, reason=reason,
            quote={"changesPercentage": pct * 100, "price": current_price, "_source": "admin_trigger"},
            dd_run_id=run_id,
            sent_status="sent" if slack_status.get("posted") else "pending",
        )
        return {
            "ok": True, "fired": True,
            "ticker": ticker.upper(), "direction": direction, "pct": pct,
            "price": current_price, "eligibility_reason": reason,
            "prior_direction": prior_direction,
            "dd_run_id": run_id,
            "agent_mode": "synthetic",
            "slack": slack_status,
        }

    if agent_mode == "off":
        # Record alert + cooldown only. No web_runs row, no Slack, no agent.
        alert_dedup.mark_alerted(
            ticker=ticker, direction=direction, pct=pct, price=current_price,
            tier=tier, reason=reason,
            quote={"changesPercentage": pct * 100, "price": current_price, "_source": "admin_trigger"},
            dd_run_id=run_id,
            sent_status="recorded_no_delivery",
        )
        return {
            "ok": True, "fired": True,
            "ticker": ticker.upper(), "direction": direction, "pct": pct,
            "price": current_price, "eligibility_reason": reason,
            "prior_direction": prior_direction,
            "dd_run_id": run_id,
            "agent_mode": "off",
            "slack": {"posted": False, "reason": "agent_mode=off"},
        }

    # agent_mode == "real" — async LLM agent flow
    placeholder = _agent_in_flight_report(ticker, pct, direction, reason)
    _upsert_dd_report(run_id, ticker, placeholder, trigger_meta, "dd_agent_pending")
    alert_dedup.mark_alerted(
        ticker=ticker, direction=direction, pct=pct, price=current_price,
        tier=tier, reason=reason,
        quote={"changesPercentage": pct * 100, "price": current_price, "_source": "admin_trigger"},
        dd_run_id=run_id,
        sent_status="agent_running",
    )

    # Spawn the background thread. daemon=True means it doesn't block app
    # shutdown if uvicorn is restarted mid-flight (the alert is already
    # persisted; the worst case is a missed Slack post + stuck placeholder
    # row that a re-trigger would overwrite anyway).
    worker = threading.Thread(
        target=_real_agent_worker,
        kwargs=dict(
            run_id=run_id, ticker=ticker, direction=direction, pct=pct,
            current_price=current_price, prior_direction=prior_direction,
            reason=reason, trigger_meta=trigger_meta,
        ),
        daemon=True,
        name=f"dd_agent_{ticker}_{run_id[:8]}",
    )
    worker.start()

    return {
        "ok": True, "fired": True,
        "ticker": ticker.upper(), "direction": direction, "pct": pct,
        "price": current_price, "eligibility_reason": reason,
        "prior_direction": prior_direction,
        "dd_run_id": run_id,
        "agent_mode": "real",
        "agent_status": "running",
        "note": "LLM agent dispatched in background. Slack post (with real report) will fire ~30-90s after this response. Poll GET /api/dd-alerts/{dd_run_id} or refresh /#/dd-alerts to see the populated report.",
    }


# ── Sector cluster trigger (Phase 2C) ──────────────────────────────────────


@router.post("/admin/dd-trigger-cluster")
def admin_dd_trigger_cluster(
    secret: str = "",
    sector: str = Query(..., description="Sector name, e.g. 'Tech', 'Banks', 'Semiconductor'"),
    direction: str = Query(..., description="DROP | PUMP"),
    members: str = Query(..., description="Comma-separated tickers in the cluster, e.g. 'NVDA,AMD,AVGO'"),
    pcts: str = Query(..., description="Comma-separated decimal pct changes matching members order, e.g. '-0.13,-0.11,-0.10'"),
    prices: str = Query(..., description="Comma-separated prices matching members order, e.g. '87,89,91'"),
    cluster_id: str | None = Query(None, description="Optional override; auto-built from sector+direction+date if absent"),
    agent_mode: str = Query("real", description="real | synthetic | off"),
):
    """Fire a sector-cluster alert. Called by the cron dispatcher after
    detecting ≥3 same-sector same-direction breaches in one tick.

    Behavior:
      1. Build cluster_id (or use override) — stable per (sector, direction, UTC-date)
      2. Insert one dd_alerts row per member with cluster_id populated and
         sent_status='cluster_member' (so individual cooldown still applies
         but Slack delivery is skipped per-member — the cluster covers them)
      3. Insert ONE dd_reports placeholder row (run_id == cluster_id) for
         the sector-level brief
      4. Spawn background thread → run_sector_dd_agent → real Slack post
      5. Return immediately with cluster_id

    Auth: gated on DB_UPLOAD_SECRET.
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if agent_mode not in ("real", "synthetic", "off"):
        raise HTTPException(status_code=400, detail=f"agent_mode must be real|synthetic|off; got {agent_mode}")
    if direction not in ("DROP", "PUMP"):
        raise HTTPException(status_code=400, detail=f"direction must be DROP or PUMP; got {direction}")

    # Parse parallel arrays
    member_list = [t.strip().upper() for t in members.split(",") if t.strip()]
    try:
        pct_list = [float(p.strip()) for p in pcts.split(",") if p.strip()]
        price_list = [float(p.strip()) for p in prices.split(",") if p.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid pcts/prices: {exc}")

    if not (len(member_list) == len(pct_list) == len(price_list)):
        raise HTTPException(
            status_code=400,
            detail=f"members ({len(member_list)}), pcts ({len(pct_list)}), prices ({len(price_list)}) must be same length",
        )
    if len(member_list) < 2:
        raise HTTPException(status_code=400, detail="A cluster needs at least 2 members")

    from src.agents.dd import alert_dedup, sector_clustering
    cid = cluster_id or sector_clustering.build_cluster_id(sector, direction)

    # Mark each member as cluster_member (records cooldown but skips Slack)
    for ticker, pct, price in zip(member_list, pct_list, price_list):
        # Each member individually checks eligibility — if a member is in
        # cooldown, we still tag it as cluster_member but skip the new alert
        # row write to avoid duplicating cooldown bookkeeping.
        eligible, reason = alert_dedup.check_alert_eligibility(
            ticker, current_pct=pct, current_price=price,
        )
        if not eligible:
            logger.info(
                f"[cluster] {ticker} skipped within cluster {cid} (reason={reason})"
            )
            continue
        alert_dedup.mark_alerted(
            ticker=ticker, direction=direction, pct=pct, price=price,
            tier="tier_cluster_member", reason=reason,
            quote={"changesPercentage": pct * 100, "price": price, "_source": "sector_cluster"},
            cluster_id=cid,
            dd_run_id=cid,         # all members link to the cluster's report
            sent_status="cluster_member",
        )

    members_for_agent = list(zip(member_list, pct_list, price_list))
    pcts_sorted = sorted(pct_list)
    median_pct = pcts_sorted[len(pcts_sorted) // 2]

    trigger_meta = {
        "source": "admin_dd_trigger_cluster",
        "sector": sector,
        "direction": direction,
        "members": member_list,
        "median_pct": median_pct,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }

    if agent_mode == "off":
        return {
            "ok": True, "fired": True, "agent_mode": "off",
            "sector": sector, "direction": direction, "members": member_list,
            "cluster_id": cid,
            "slack": {"posted": False, "reason": "agent_mode=off"},
        }

    if agent_mode == "synthetic":
        report = _synthetic_sector_report(sector, direction, member_list, median_pct)
        _upsert_dd_report(cid, sector, report, trigger_meta, "dd_synthetic_sector_cluster")
        slack_status = _try_post_sector_slack(
            sector=sector, direction=direction, members=member_list,
            median_pct=median_pct, report=report, run_id=cid,
        )
        return {
            "ok": True, "fired": True, "agent_mode": "synthetic",
            "sector": sector, "direction": direction, "members": member_list,
            "cluster_id": cid,
            "slack": slack_status,
        }

    # agent_mode == "real" — async background dispatch
    placeholder = _agent_in_flight_sector_report(sector, direction, member_list, median_pct)
    _upsert_dd_report(cid, sector, placeholder, trigger_meta, "dd_sector_agent_pending")

    worker = threading.Thread(
        target=_real_sector_agent_worker,
        kwargs=dict(
            cluster_id=cid, sector=sector, direction=direction,
            members=members_for_agent, median_pct=median_pct,
            trigger_meta=trigger_meta,
        ),
        daemon=True,
        name=f"dd_sector_agent_{sector[:8]}_{cid[-8:]}",
    )
    worker.start()

    return {
        "ok": True, "fired": True, "agent_mode": "real", "agent_status": "running",
        "sector": sector, "direction": direction, "members": member_list,
        "cluster_id": cid, "median_pct": median_pct,
        "note": "Sector cluster LLM agent dispatched in background. Slack will fire ~30-90s after this response.",
    }


# ── Phase 3: Performance attribution (forward-return grading) ─────────────


@router.post("/admin/dd-grade-pending")
def admin_dd_grade_pending(
    secret: str = "",
    min_age_days: int = Query(7, ge=0, le=90, description="Only grade alerts older than this many days (0 to grade everything regardless of age — useful for backfill)"),
    max_rows: int = Query(50, ge=1, le=500, description="Cap on rows graded per call"),
):
    """Grade pending dd_alerts rows by computing forward returns from FMP
    and matching against the LLM's recommended_action.

    Phase 3 attribution: closes the loop on whether the agent's action
    suggestions correlate with subsequent price moves.

    For each row whose forward_5d_return IS NULL and is at least
    `min_age_days` old:
      1. Read recommended_action from dd_reports.full_result_json.report
      2. Parse to ActionCategory (ADD/TRIM/EXIT/HOLD/WATCH/UNCLEAR)
      3. Fetch FMP daily prices T..T+35d
      4. Compute forward_{1,5,22}d_return decimals
      5. Grade action_outcome (correct/incorrect/neutral/no_data)
      6. Persist via alert_dedup.set_alert_grade()

    Idempotent — safe to call repeatedly. Subsequent calls on the same row
    refresh the grade (useful when 22d data becomes available later).

    Auth: gated on DB_UPLOAD_SECRET.
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    from src.agents.dd import alert_dedup
    from src.agents.dd.performance import (
        ActionCategory, ActionOutcome,
        compute_forward_returns, grade_action, parse_recommended_action,
    )

    pending = alert_dedup.get_pending_grade_rows(min_age_days=min_age_days)[:max_rows]

    n_graded     = 0
    n_no_action  = 0
    n_no_data    = 0
    by_outcome:  dict[str, int] = {}

    for row in pending:
        # Look up the recommended_action from the joined dd_reports payload
        rec_action_text = ""
        if row["dd_run_id"]:
            with _connect() as conn:
                rep_row = conn.execute(
                    "SELECT full_result_json FROM dd_reports WHERE run_id = ?",
                    (row["dd_run_id"],),
                ).fetchone()
            if rep_row and rep_row[0]:
                try:
                    payload = json.loads(rep_row[0])
                    rec_action_text = (
                        (payload.get("report") or {}).get("recommended_action") or ""
                    )
                except Exception:
                    rec_action_text = ""

        action = parse_recommended_action(rec_action_text)
        if action == ActionCategory.UNCLEAR:
            n_no_action += 1

        # Compute forward returns (synchronous FMP fetch)
        try:
            trigger_dt = datetime.fromisoformat(
                row["last_triggered_at"].replace("Z", "+00:00")
            )
        except Exception:
            trigger_dt = datetime.now(timezone.utc)

        fr = compute_forward_returns(
            ticker        = row["ticker"],
            trigger_date  = trigger_dt,
            trigger_price = row["trigger_price"],
        )

        if fr.fwd_5d is None:
            outcome = ActionOutcome.NO_DATA
            n_no_data += 1
        else:
            outcome = grade_action(action, fr.fwd_5d)

        alert_dedup.set_alert_grade(
            ticker            = row["ticker"],
            last_direction    = row["last_direction"],
            last_triggered_at = row["last_triggered_at"],
            forward_1d_return = fr.fwd_1d,
            forward_5d_return = fr.fwd_5d,
            forward_22d_return= fr.fwd_22d,
            action_category   = action.value,
            action_outcome    = outcome.value,
        )
        n_graded += 1
        by_outcome[outcome.value] = by_outcome.get(outcome.value, 0) + 1

    return {
        "ok": True,
        "n_pending":  len(pending),
        "n_graded":   n_graded,
        "n_no_action": n_no_action,
        "n_no_data":  n_no_data,
        "by_outcome": by_outcome,
        "min_age_days": min_age_days,
    }


@router.get("/api/dd-alerts/performance")
def get_dd_performance(
    since: str | None = Query(None, description="ISO date; alerts at or after this time"),
    until: str | None = Query(None, description="ISO date; alerts strictly before this time"),
):
    """Aggregate Phase 3 attribution: hit rates by action / direction /
    reason + agent-vs-naive-baseline alpha.

    Naive baseline = "if you had done nothing" — the mean forward return
    of all alerts. Agent alpha = mean forward return weighted by action
    direction (ADD = +return signal, TRIM/EXIT = -return signal,
    HOLD = held position, WATCH/UNCLEAR = excluded).
    """
    where = ["1=1"]
    params: list = []
    if since: where.append("last_triggered_at >= ?"); params.append(since)
    if until: where.append("last_triggered_at <  ?"); params.append(until)
    where.append("forward_5d_return IS NOT NULL")   # only graded alerts

    sql = f"""
      SELECT action_category, action_outcome, last_direction, alert_reason,
             forward_1d_return, forward_5d_return, forward_22d_return
      FROM dd_alerts
      WHERE {' AND '.join(where)}
    """
    _ensure_tables_for_read()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return {
            "n_alerts_graded": 0,
            "by_action":       {},
            "by_direction":    {},
            "by_reason":       {},
            "alpha":           None,
            "since": since, "until": until,
        }

    def _bucket(rows_subset):
        """Return {n, hit_rate (correct / non-neutral), mean_5d, mean_1d, mean_22d}."""
        if not rows_subset:
            return None
        n = len(rows_subset)
        n_correct   = sum(1 for r in rows_subset if r["action_outcome"] == "correct")
        n_incorrect = sum(1 for r in rows_subset if r["action_outcome"] == "incorrect")
        n_decisive  = n_correct + n_incorrect
        hit_rate    = (n_correct / n_decisive) if n_decisive else None

        def _mean(field):
            vals = [r[field] for r in rows_subset if r[field] is not None]
            return (sum(vals) / len(vals)) if vals else None

        return {
            "n":           n,
            "n_correct":   n_correct,
            "n_incorrect": n_incorrect,
            "hit_rate":    round(hit_rate, 4) if hit_rate is not None else None,
            "mean_1d_return":  round(_mean("forward_1d_return") or 0, 4) if _mean("forward_1d_return") is not None else None,
            "mean_5d_return":  round(_mean("forward_5d_return") or 0, 4) if _mean("forward_5d_return") is not None else None,
            "mean_22d_return": round(_mean("forward_22d_return") or 0, 4) if _mean("forward_22d_return") is not None else None,
        }

    rows_list = [dict(r) for r in rows]

    by_action: dict[str, dict] = {}
    for category in ("ADD", "TRIM", "EXIT", "HOLD", "WATCH", "UNCLEAR"):
        subset = [r for r in rows_list if r["action_category"] == category]
        bucket = _bucket(subset)
        if bucket:
            by_action[category] = bucket

    by_direction = {}
    for d in ("DROP", "PUMP"):
        subset = [r for r in rows_list if r["last_direction"] == d]
        b = _bucket(subset)
        if b:
            by_direction[d] = b

    # Reason buckets — group by category prefix so all "direction_flip_*"
    # variants roll up
    reasons = {"first_breach", "direction_flip", "high_water_mark",
               "cooldown_expired", "admin_force_override"}
    by_reason = {}
    for prefix in reasons:
        subset = [r for r in rows_list if (r["alert_reason"] or "").startswith(prefix)]
        b = _bucket(subset)
        if b:
            by_reason[prefix] = b

    # Naive baseline = mean fwd_5d across all graded alerts
    fwd_5d_vals = [r["forward_5d_return"] for r in rows_list if r["forward_5d_return"] is not None]
    naive_mean_5d = (sum(fwd_5d_vals) / len(fwd_5d_vals)) if fwd_5d_vals else 0

    # Agent alpha = signed return weighted by action direction
    #   ADD: full credit for positive returns (long bias)
    #   TRIM/EXIT: credit for negative returns (short / no-position bias)
    #   HOLD: forward return × 0 (holding cash-equivalent in the alert window)
    #   WATCH/UNCLEAR: excluded from alpha calc
    alpha_5d_terms = []
    for r in rows_list:
        if r["forward_5d_return"] is None:
            continue
        cat = r["action_category"]
        ret = r["forward_5d_return"]
        if cat == "ADD":
            alpha_5d_terms.append(ret)        # long: capture the upside
        elif cat in ("TRIM", "EXIT"):
            alpha_5d_terms.append(-ret)       # short / no-position: capture avoided downside
        elif cat == "HOLD":
            alpha_5d_terms.append(0.0)        # held: no alpha contribution
        # WATCH/UNCLEAR excluded
    agent_mean_5d = (sum(alpha_5d_terms) / len(alpha_5d_terms)) if alpha_5d_terms else 0

    return {
        "n_alerts_graded":   len(rows_list),
        "by_action":         by_action,
        "by_direction":      by_direction,
        "by_reason":         by_reason,
        "naive_mean_5d_return": round(naive_mean_5d, 4),
        "agent_mean_5d_alpha":  round(agent_mean_5d, 4),
        "alpha_vs_naive":       round(agent_mean_5d - naive_mean_5d, 4),
        "since": since, "until": until,
    }


# ── Phase 2E: EOD digest ───────────────────────────────────────────────────


@router.post("/admin/dd-digest")
def admin_dd_digest(
    secret: str = "",
    utc_date: str | None = Query(None, description="ISO date YYYY-MM-DD; defaults to today UTC"),
    agent_mode: str = Query("real", description="real | synthetic | off"),
):
    """Generate the EOD digest for a given UTC date (default: today).

    Web-only delivery — no Slack post. Writes ONE dd_reports row keyed by
    `digest_<utc_date>` so re-running on the same date overwrites.

    Auth: gated on DB_UPLOAD_SECRET (same as other /admin/* endpoints).

    Behavior:
      - real: runs Qwen with web_search; falls back to synthetic on failure
      - synthetic: writes a minimal placeholder digest (used by tests + cron
        when LLM credentials aren't available)
      - off: aggregates today's data without any LLM call or DB write —
        useful for testing the aggregator in isolation
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if agent_mode not in ("real", "synthetic", "off"):
        raise HTTPException(status_code=400, detail=f"agent_mode must be real|synthetic|off; got {agent_mode}")

    from src.agents.dd.digest_agent import (
        gather_today_aggregates, run_digest_agent,
        upsert_digest_row, upsert_synthetic_digest,
    )
    from src.agents.dd.dd_agent import DDAgentError

    if utc_date is None:
        utc_date = datetime.now(timezone.utc).date().isoformat()

    aggregates = gather_today_aggregates(utc_date)

    if agent_mode == "off":
        return {
            "ok": True, "agent_mode": "off",
            "utc_date": utc_date, "aggregates": aggregates,
            "note": "No LLM call made; dd_reports row not written.",
        }

    if agent_mode == "synthetic":
        run_id = upsert_synthetic_digest(utc_date=utc_date, aggregates=aggregates)
        return {
            "ok": True, "agent_mode": "synthetic",
            "utc_date": utc_date, "run_id": run_id,
            "n_drops": aggregates["n_drops"], "n_pumps": aggregates["n_pumps"],
            "n_clusters": aggregates["n_clusters"],
        }

    # agent_mode == "real" — synchronous (digest LLM call is fast enough,
    # and the cron tick can wait ~30-60s after market close)
    try:
        narrative = run_digest_agent(utc_date=utc_date)
        run_id = upsert_digest_row(
            utc_date=utc_date, narrative=narrative, aggregates=aggregates,
        )
        return {
            "ok": True, "agent_mode": "real",
            "utc_date": utc_date, "run_id": run_id,
            "narrative_preview": narrative.narrative[:200],
            "key_themes_count": len(narrative.key_themes),
            "macro_or_micro": narrative.macro_or_micro,
            "n_drops": aggregates["n_drops"], "n_pumps": aggregates["n_pumps"],
            "n_clusters": aggregates["n_clusters"],
        }
    except DDAgentError as exc:
        # Fallback: write a synthetic digest so the dashboard still has SOMETHING
        logger.warning(f"[digest] {utc_date} LLM failed ({exc}) — synthetic fallback")
        run_id = upsert_synthetic_digest(utc_date=utc_date, aggregates=aggregates)
        return {
            "ok": True, "agent_mode": "real_fell_back_to_synthetic",
            "utc_date": utc_date, "run_id": run_id,
            "fallback_reason": str(exc),
            "n_drops": aggregates["n_drops"], "n_pumps": aggregates["n_pumps"],
            "n_clusters": aggregates["n_clusters"],
        }


# ── Daily retention cleanup ─────────────────────────────────────────────────


@router.post("/admin/dd-cleanup")
def admin_dd_cleanup(
    secret: str = "",
    retention_days: int = Query(7, ge=0, le=365, description="Keep alerts newer than this many days. Pass 0 to wipe everything (one-shot reset)."),
):
    """Delete dd_alerts + paired web_runs rows older than `retention_days`.

    Phase 2B UX decision: Auto Due-D is a daily-refreshing feed (vs. the
    permanent History tab). The cron dispatcher hits this endpoint once per
    UTC day to keep the dd_alerts table small + dashboard queries fast.

    Auth: same DB_UPLOAD_SECRET as other /admin/* endpoints.

    Safe to invoke manually too — it's idempotent and logs the count of rows
    deleted. Won't touch web_runs rows whose model_name doesn't start with
    'dd_' (so ticker-research history is never accidentally pruned).
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    from src.agents.dd import alert_dedup
    return alert_dedup.cleanup_old_alerts(retention_days=retention_days)


@router.post("/admin/dd-purge-legacy-web-runs")
def admin_dd_purge_legacy(secret: str = ""):
    """One-shot housekeeping: remove DD-prefixed rows that the pre-Phase-2B
    architecture wrote into web_runs. After Phase 2B all new DD reports go
    to the dedicated dd_reports table, but rows already on disk from prior
    runs (e.g. smoke tests) need to be cleaned up so the History tab is
    fully cut over.

    Idempotent. Safe to call multiple times — second call is a no-op.
    Only deletes rows where model_name LIKE 'dd_%' OR model_name =
    'synthetic-dd-trigger' — never touches user ticker-research history.

    Auth: same DB_UPLOAD_SECRET as other /admin/* endpoints.
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    from src.agents.dd import alert_dedup
    return alert_dedup.purge_legacy_dd_rows_from_web_runs()


# ── Universe endpoint (consumed by the dd-dispatcher cron service) ─────────


@router.get("/api/dd-universe/tier1")
def get_dd_universe_tier1():
    """Return the Tier 1 ticker list for the dd-dispatcher cron service.

    Tier 1 = the user's watchlist (canonical curated set, same source the
    Watchlist UI tab reads from).

    The cron service calls this endpoint instead of reading the SQLite DB
    directly — keeps the dispatcher stateless and avoids needing to share
    the Railway volume between two services.

    No auth required: this leaks ticker symbols only (no secrets, no PII,
    no positions). Intentionally cheap so the cron can hit it every 5 min.
    """
    from src.agents.dd.universe import get_watchlist_tickers
    tickers = sorted(get_watchlist_tickers())
    return {"tier": "tier1_watchlist", "tickers": tickers, "count": len(tickers)}


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
        LEFT JOIN dd_reports w ON w.run_id = a.dd_run_id
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
            "LEFT JOIN dd_reports w ON w.run_id = a.dd_run_id "
            "WHERE a.last_triggered_at >= ? AND a.last_direction = 'DROP' "
            "ORDER BY a.trigger_pct ASC LIMIT 10",
            (today,),
        ).fetchall()
        pumps = conn.execute(
            "SELECT a.*, w.full_result_json FROM dd_alerts a "
            "LEFT JOIN dd_reports w ON w.run_id = a.dd_run_id "
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
    # Phase 2E: hydrate the LLM-narrated digest if today's row exists
    narrative_payload = None
    digest_run_id = f"digest_{today}"
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT full_result_json, model_name FROM dd_reports WHERE run_id = ?",
                (digest_run_id,),
            ).fetchone()
        if row:
            try:
                narrative_payload = json.loads(row[0])
                # Tag whether this came from the real LLM or a fallback
                narrative_payload["_model_name"] = row[1]
            except Exception:
                narrative_payload = None
    except Exception:
        # Table missing on a brand-new DB — graceful no-op
        narrative_payload = None

    return {
        "date":     today,
        "drops":    [_row_to_alert_dict(r) for r in drops],
        "pumps":    [_row_to_alert_dict(r) for r in pumps],
        "clusters": [
            {"cluster_id": r["cluster_id"], "direction": r["last_direction"],
             "n": r["n"], "median_pct": r["median_pct"]}
            for r in clusters
        ],
        "narrative": narrative_payload,    # null if no digest run yet
    }


@router.get("/api/dd-alerts/{dd_run_id}")
def get_alert_detail(dd_run_id: str):
    """Single full DD report (hydrated from web_runs)."""
    _ensure_tables_for_read()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT a.*, w.full_result_json FROM dd_alerts a "
            "LEFT JOIN dd_reports w ON w.run_id = a.dd_run_id "
            "WHERE a.dd_run_id = ? "
            "ORDER BY a.last_triggered_at DESC LIMIT 1",
            (dd_run_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No alert with dd_run_id={dd_run_id}")
    return _row_to_alert_dict(row)

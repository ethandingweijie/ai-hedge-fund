"""
app/backend/services/portfolio_service.py
==========================================
P1 — holdings storage (user_holdings) + indicator dashboard.

Storage follows the watchlist scoping pattern: user_id nullable (NULL =
unscoped/local holdings), CRUD via SQLAlchemy session.

The dashboard is a READ-ONLY enrichment over data the system already
computes — no new LLM / valuation work:

  • live prices        — one batched FMP /stable/quote call
                         (reuses watchlist_service._batch_fetch_prices)
  • latest signals     — newest ticker_signals row per holding from the
                         dual-mode archive (final_action, dcf_base_iv,
                         sector, price_target, dcf_range/scenario/vgpm
                         blobs) — one SQL query, PG-DISTINCT-ON /
                         sqlite-GROUP-BY split like watchlist's
                         _get_pipeline_vgpm
  • portfolio math     — market value, weights, unrealized P&L, sector
                         exposure, IV-vs-price (pure, deterministic)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.backend.database.models import UserHolding
from src.data import db as _db

logger = logging.getLogger(__name__)


# ── CRUD ─────────────────────────────────────────────────────────────────────

def _scope_filter(user_id: Optional[int]):
    """user_id scoping that treats None as an exact NULL scope (never
    cross-user — same guard as watchlist's _write_vgpm_to_watchlist)."""
    if user_id is not None:
        return UserHolding.user_id == user_id
    return UserHolding.user_id.is_(None)


def list_holdings(db: Session, user_id: Optional[int]) -> list[UserHolding]:
    return (
        db.query(UserHolding)
        .filter(_scope_filter(user_id))
        .order_by(UserHolding.created_at.desc(), UserHolding.id.desc())
        .all()
    )


def upsert_holding(db: Session, user_id: Optional[int], ticker: str,
                   quantity: float, avg_cost: float,
                   opened_at: Optional[datetime] = None,
                   notes: Optional[str] = None) -> UserHolding:
    """Add a position, or replace the quantity/cost when the (user, ticker)
    row already exists (idempotent — the composite unique constraint is the
    backstop)."""
    ticker = ticker.strip().upper()
    row = (
        db.query(UserHolding)
        .filter(_scope_filter(user_id), UserHolding.ticker == ticker)
        .first()
    )
    if row is None:
        row = UserHolding(user_id=user_id, ticker=ticker)
        db.add(row)
    row.quantity = float(quantity)
    row.avg_cost = float(avg_cost)
    if opened_at is not None:
        row.opened_at = opened_at
    if notes is not None:
        row.notes = notes
    db.commit()
    db.refresh(row)
    return row


def update_holding(db: Session, user_id: Optional[int], holding_id: int,
                   quantity: Optional[float] = None,
                   avg_cost: Optional[float] = None,
                   opened_at: Optional[datetime] = None,
                   notes: Optional[str] = None) -> Optional[UserHolding]:
    row = (
        db.query(UserHolding)
        .filter(_scope_filter(user_id), UserHolding.id == holding_id)
        .first()
    )
    if row is None:
        return None
    if quantity is not None:
        row.quantity = float(quantity)
    if avg_cost is not None:
        row.avg_cost = float(avg_cost)
    if opened_at is not None:
        row.opened_at = opened_at
    if notes is not None:
        row.notes = notes
    db.commit()
    db.refresh(row)
    return row


def delete_holding(db: Session, user_id: Optional[int], holding_id: int) -> bool:
    row = (
        db.query(UserHolding)
        .filter(_scope_filter(user_id), UserHolding.id == holding_id)
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _holding_dict(row: UserHolding) -> dict:
    return {
        "id":        row.id,
        "ticker":    row.ticker,
        "quantity":  row.quantity,
        "avg_cost":  row.avg_cost,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "notes":     row.notes,
        "added_at":  row.created_at.isoformat() if row.created_at else None,
    }


# ── Signals join (dual-mode archive) ────────────────────────────────────────

_SIGNAL_COLS = (
    "ts.final_action, ts.position_size_pct, ts.price_target, "
    "ts.dcf_base_iv, ts.price_at_run, r.sector, "
    "ts.dcf_range_json, ts.scenario_json, ts.vgpm_json"
)


def _latest_signals(tickers: list[str]) -> dict[str, dict]:
    """Newest ticker_signals row per ticker (same PG/sqlite split as
    watchlist_service._get_pipeline_vgpm). Returns {} on any failure —
    the dashboard degrades to holdings-with-prices only."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    try:
        if _db.is_postgres():
            rows = _db.query(
                f"SELECT DISTINCT ON (ts.ticker) ts.ticker, r.run_at, {_SIGNAL_COLS} "
                f"FROM ticker_signals ts JOIN runs r ON ts.run_id = r.run_id "
                f"WHERE ts.ticker IN ({placeholders}) "
                f"ORDER BY ts.ticker, r.run_at DESC",
                list(tickers),
            )
        else:
            rows = _db.query(
                f"SELECT ts.ticker, MAX(r.run_at) AS run_at, {_SIGNAL_COLS} "
                f"FROM ticker_signals ts JOIN runs r ON ts.run_id = r.run_id "
                f"WHERE ts.ticker IN ({placeholders}) "
                f"GROUP BY ts.ticker",
                list(tickers),
            )
    except Exception as exc:
        logger.warning("portfolio signals query failed: %s", exc)
        return {}

    def _load(raw) -> dict | None:
        if not raw:
            return None
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except (TypeError, ValueError):
            return None

    out: dict[str, dict] = {}
    for row in rows:
        tkr = row.get("ticker")
        if not tkr:
            continue
        dcf_range = _load(row.get("dcf_range_json")) or {}
        scenario = _load(row.get("scenario_json")) or {}
        vgpm = _load(row.get("vgpm_json")) or {}
        ivs = {}
        for scen in ("bear", "base", "bull"):
            block = dcf_range.get(scen)
            if isinstance(block, dict) and isinstance(block.get("intrinsic_value"), (int, float)):
                ivs[f"iv_{scen}"] = block["intrinsic_value"]
        out[tkr] = {
            "run_at":           row.get("run_at"),
            "decision":         row.get("final_action"),
            "position_size_pct": row.get("position_size_pct"),
            "price_target":     row.get("price_target"),
            "dcf_base_iv":      row.get("dcf_base_iv"),
            "price_at_run":     row.get("price_at_run"),
            "sector":           row.get("sector") or None,
            "wacc":             dcf_range.get("wacc"),
            "anchor_method":    dcf_range.get("anchor_method"),
            "profile":          dcf_range.get("profile"),
            "expected_value":   scenario.get("expected_value"),
            "price_target_12m": scenario.get("12m_price_target"),
            "assumption_divergence": scenario.get("assumption_divergence") or [],
            "vgpm_composite":   _vgpm_composite(vgpm),
            **ivs,
        }
    return out


def _vgpm_composite(vgpm: dict) -> Optional[int]:
    if not vgpm:
        return None
    scores = [v.get("score") for v in vgpm.values()
              if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    return round(sum(scores) / len(scores)) if scores else None


# ── Dashboard assembly (pure math — unit-testable) ──────────────────────────

def build_dashboard(holdings: list[dict],
                    prices: dict[str, Optional[float]],
                    signals: dict[str, dict]) -> dict:
    """Assemble the enriched portfolio view. All inputs are plain dicts —
    no DB/network access here."""
    rows = []
    total_mv = 0.0
    total_cost = 0.0
    for h in holdings:
        price = prices.get(h["ticker"])
        sig = signals.get(h["ticker"], {})
        qty = h["quantity"] or 0.0
        cost = h["avg_cost"] or 0.0
        mv = qty * price if price is not None else None
        cost_basis = qty * cost
        pnl = (mv - cost_basis) if mv is not None else None
        rows.append({
            **h,
            "price":          price,
            "market_value":   mv,
            "cost_basis":     cost_basis,
            "unrealized_pnl": pnl,
            "pnl_pct":        (pnl / cost_basis * 100) if (pnl is not None and cost_basis) else None,
            "iv_upside_pct":  ((sig.get("dcf_base_iv") / price - 1) * 100)
                              if (sig.get("dcf_base_iv") and price) else None,
            "signals":        sig or None,
        })
        if mv is not None:
            total_mv += mv
        total_cost += cost_basis

    # Portfolio weight per row (None market value → weight None)
    for r in rows:
        r["weight_pct"] = (r["market_value"] / total_mv * 100) if (r["market_value"] is not None and total_mv) else None

    # Sector exposure (weight-attributed; holdings without price/sector drop out)
    sector_exposure: dict[str, float] = {}
    for r in rows:
        sector = (r.get("signals") or {}).get("sector") or "Unclassified"
        if r["weight_pct"] is not None:
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + r["weight_pct"]
    sector_exposure = {k: round(v, 2) for k, v in
                       sorted(sector_exposure.items(), key=lambda kv: -kv[1])}

    total_pnl = (total_mv - total_cost) if total_mv else None
    return {
        "holdings": rows,
        "summary": {
            "total_market_value": round(total_mv, 2) if total_mv else None,
            "total_cost_basis":   round(total_cost, 2) if total_cost else None,
            "total_unrealized_pnl": round(total_pnl, 2) if total_pnl is not None else None,
            "total_pnl_pct":      round(total_pnl / total_cost * 100, 2)
                                  if (total_pnl is not None and total_cost) else None,
            "position_count":     len(rows),
            "top_weight_pct":     max((r["weight_pct"] for r in rows
                                       if r["weight_pct"] is not None), default=None),
        },
        "sector_exposure": sector_exposure,
        "prices_at": datetime.now(timezone.utc).isoformat(),
    }


def get_dashboard(db: Session, user_id: Optional[int]) -> dict:
    """Full pipeline: holdings → live prices → latest signals → assembly."""
    rows = list_holdings(db, user_id)
    holdings = [_holding_dict(r) for r in rows]
    if not holdings:
        return build_dashboard([], {}, {})

    tickers = [h["ticker"] for h in holdings]

    # Live prices via the screener's proven multi-symbol path — FMP
    # /stable/quote is single-symbol only (batch returns []), and HK
    # names route through yfinance there. {symbol: {price, volume, …}}
    try:
        from app.backend.services.screener_service import get_live_quotes
        quotes = get_live_quotes(tickers)
        prices: dict[str, Optional[float]] = {
            t: quotes[t].get("price") for t in tickers if t in quotes}
    except Exception as exc:
        logger.warning("portfolio price fetch failed: %s", exc)
        prices = {}

    signals = _latest_signals(tickers)
    return build_dashboard(holdings, prices, signals)


# ── P2: crisis replay (portfolio_replays cache + job orchestration) ─────────

from src.portfolio import replay as _replay
from src.portfolio.event_library import EVENTS as _EVENTS

_REPLAY_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_replays (
    user_id       INTEGER,
    snapshot_hash TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    computed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_replays_lookup
    ON portfolio_replays(user_id, snapshot_hash);
"""


def replay_enabled() -> bool:
    return os.getenv("PORTFOLIO_REPLAY", "true").strip().lower() != "false"


def _ensure_replay_table() -> None:
    _db.ensure_table(_REPLAY_DDL)


def _user_where(user_id: Optional[int]) -> tuple[str, list]:
    """SQL scoping identical to _scope_filter (NULL = anon scope)."""
    if user_id is not None:
        return "user_id = ?", [user_id]
    return "user_id IS NULL", []


def get_cached_replay(user_id: Optional[int], snap_hash: str) -> Optional[dict]:
    _ensure_replay_table()
    where, params = _user_where(user_id)
    row = _db.query_one(
        f"SELECT result_json FROM portfolio_replays "
        f"WHERE snapshot_hash = ? AND {where} "
        f"ORDER BY computed_at DESC LIMIT 1",
        [snap_hash, *params],
    )
    if not row:
        return None
    try:
        return json.loads(row["result_json"])
    except (TypeError, ValueError, KeyError):
        return None


def save_replay(user_id: Optional[int], snap_hash: str, result: dict) -> None:
    _ensure_replay_table()
    _db.execute(
        "INSERT INTO portfolio_replays (user_id, snapshot_hash, result_json, computed_at) "
        "VALUES (?, ?, ?, ?)",
        [user_id, snap_hash, json.dumps(result),
         datetime.now(timezone.utc).isoformat()],
    )


def _load_today_regime() -> dict:
    """Latest macro regime snapshot (regime_state.json's `regime` block).
    Degrades to {} — similarity scoring then reports zero matches."""
    try:
        from pathlib import Path
        path = Path(__file__).parent.parent.parent.parent / "src" / "data" / "regime_state.json"
        with open(path, encoding="utf-8") as fh:
            return dict(json.load(fh).get("regime") or {})
    except Exception:
        return {}


# In-flight guard: (user_id, snapshot_hash) → job_id. The cache check runs
# BEFORE this, so a second request for an unchanged portfolio either hits
# the cache or dedupes onto the running job.
_RUNNING_REPLAYS: dict[tuple, str] = {}
_RUNNING_LOCK = threading.Lock()


def start_replay(db: Session, user_id: Optional[int]) -> dict:
    """Serve a cached replay when the holdings snapshot is unchanged, else
    create a tracked job and compute it in a background thread."""
    from app.backend.services import complacency_job_store as job_store

    holdings = [_holding_dict(r) for r in list_holdings(db, user_id)]
    if not holdings:
        return {"error": "no_holdings"}
    snap = _replay.snapshot_hash(holdings)

    cached = get_cached_replay(user_id, snap)
    if cached is not None:
        return {"cached": True, "snapshot_hash": snap, "result": cached}

    key = (user_id, snap)
    with _RUNNING_LOCK:
        running_id = _RUNNING_REPLAYS.get(key)
        if running_id and job_store.get_job(running_id) and \
                job_store.get_job(running_id).get("status") in ("pending", "running"):
            return {"cached": False, "running": True, "job_id": running_id,
                    "snapshot_hash": snap}

    job_id = job_store.create_job("portfolio_replay", ticker=None, user_id=user_id)
    with _RUNNING_LOCK:
        _RUNNING_REPLAYS[key] = job_id
    thread = threading.Thread(
        target=_execute_replay_job, args=(job_id, user_id, snap, holdings),
        name=f"replay-{job_id[:8]}", daemon=True)
    thread.start()
    return {"cached": False, "job_id": job_id, "snapshot_hash": snap}


def _execute_replay_job(job_id: str, user_id: Optional[int], snap: str,
                        holdings: list[dict]) -> None:
    from app.backend.services import complacency_job_store as job_store

    stop = threading.Event()
    start = datetime.now(timezone.utc)

    def _pulse():
        while not stop.wait(30):
            elapsed = int((datetime.now(timezone.utc) - start).total_seconds())
            mm, ss = divmod(elapsed, 60)
            try:
                job_store.update_progress(
                    job_id, "running",
                    f"replaying {len(holdings)} holdings × {len(_EVENTS)} events "
                    f"· {mm}m {ss}s elapsed")
            except Exception:
                pass

    hb = threading.Thread(target=_pulse, daemon=True)
    hb.start()
    try:
        job_store.update_progress(
            job_id, "running",
            f"replaying {len(holdings)} holdings × {len(_EVENTS)} events")
        result = _replay.replay_portfolio(holdings, today_regime=_load_today_regime())
        save_replay(user_id, snap, result)
        job_store.complete_job(job_id, {"snapshot_hash": snap, "result": result})
        logger.info("portfolio replay %s done: %d events, %d holdings",
                    job_id, result.get("event_count", 0), len(holdings))
    except Exception as exc:
        logger.error("portfolio replay %s failed: %s", job_id, exc)
        try:
            job_store.fail_job(job_id, str(exc)[:500])
        except Exception:
            pass
    finally:
        stop.set()
        with _RUNNING_LOCK:
            _RUNNING_REPLAYS.pop((user_id, snap), None)


def get_replay_job(job_id: str, user_id: Optional[int]) -> Optional[dict]:
    """Job status scoped to the requesting user (portfolio data is personal,
    unlike the shared complacency jobs).

    user_id comes from a direct query: the job store's shared get_job()
    dict deliberately omits it (shared research jobs stay globally visible
    — attribution is for rate limits only), so replay scoping reads the
    column itself. get_job() still runs first for its stuck-job watchdog.
    """
    from app.backend.services import complacency_job_store as job_store

    job = job_store.get_job(job_id)
    if not job or job.get("kind") != "portfolio_replay":
        return None
    row = _db.query_one(
        "SELECT user_id FROM complacency_jobs WHERE job_id = ?", [job_id])
    if row is None:
        return None
    # Row type differs by backend (dict on PG, sqlite3.Row locally) —
    # key access works on both, .get does not.
    if row["user_id"] != user_id:
        return None
    return job


def list_events() -> list[dict]:
    """The curated event library (read-only metadata for the frontend)."""
    from src.portfolio.event_library import events_as_dicts
    return events_as_dicts()

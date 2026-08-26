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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backend.database.models import User, UserHolding
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


# ── P5: what-if crisis simulator ─────────────────────────────────────────────

from src.portfolio import what_if as _what_if

_WHAT_IF_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_what_ifs (
    user_id       INTEGER,
    scenario_hash TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    computed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_what_ifs_lookup
    ON portfolio_what_ifs(user_id, scenario_hash);
"""


def what_if_enabled() -> bool:
    return os.getenv("PORTFOLIO_WHAT_IF", "true").strip().lower() != "false"


def _ensure_what_if_table() -> None:
    _db.ensure_table(_WHAT_IF_DDL)


def get_cached_what_if(user_id: Optional[int], scen_hash: str) -> Optional[dict]:
    _ensure_what_if_table()
    where, params = _user_where(user_id)
    row = _db.query_one(
        f"SELECT result_json FROM portfolio_what_ifs "
        f"WHERE scenario_hash = ? AND {where} "
        f"ORDER BY computed_at DESC LIMIT 1",
        [scen_hash, *params],
    )
    if not row:
        return None
    try:
        return json.loads(row["result_json"])
    except (TypeError, ValueError, KeyError):
        return None


def save_what_if(user_id: Optional[int], scen_hash: str, result: dict) -> None:
    _ensure_what_if_table()
    _db.execute(
        "INSERT INTO portfolio_what_ifs (user_id, scenario_hash, result_json, computed_at) "
        "VALUES (?, ?, ?, ?)",
        [user_id, scen_hash, json.dumps(result),
         datetime.now(timezone.utc).isoformat()],
    )


def compute_scenario_hash(category: str, concerns: str,
                          reference_key: Optional[str],
                          search_override: str, horizon_days: int,
                          holdings: list[dict]) -> str:
    """Cache key for a what-if scenario. Notes are part of the key because
    they drive product classification; holdings quantities/costs drive the
    cost-basis weights. Bumping _what_if.SCENARIO_VERSION invalidates all
    cached scenarios (prompt/model/anchor changes)."""
    import hashlib
    payload = {
        "scenario_version": _what_if.SCENARIO_VERSION,
        "category": category.strip(),
        "concerns": " ".join(concerns.split()),     # whitespace-normalized
        "reference_key": reference_key,
        "search_override": search_override,
        "horizon_days": horizon_days,
        "holdings": sorted(
            (str(h["ticker"]).upper(), round(float(h.get("quantity") or 0), 6),
             round(float(h.get("avg_cost") or 0), 6),
             " ".join(str(h.get("notes") or "").split()))
            for h in holdings
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def list_what_if_categories() -> list[str]:
    return list(_what_if.CATEGORIES)


def product_knowledge() -> list[dict]:
    """Public summary of the short-product knowledge base (the UI hints at
    what is confirmed vs assumed vs unknown — no sensitive data)."""
    out = [
        {"ticker": t, "name": m["name"], "underlying": m["underlying"],
         "leverage": m["leverage"], "confidence": m["confidence"]}
        for t, m in sorted(_what_if.INVERSE_PRODUCT_MAP.items())
    ]
    out += [{"ticker": t, "confidence": "unknown"}
            for t in sorted(_what_if.UNCLASSIFIED_PRODUCTS)]
    return out


def start_what_if(db: Session, user_id: Optional[int], category: str,
                  concerns: str, reference_key: Optional[str],
                  search_override: str, horizon_days: int,
                  share: bool = True, parent_id: Optional[str] = None) -> dict:
    """Serve a cached scenario when inputs are unchanged, else create a
    tracked job and simulate in a background thread. share=True publishes
    the completed scenario to the joint scenario memory (idempotent by
    content hash); parent_id links it as a fork of an existing scenario."""
    from app.backend.services import complacency_job_store as job_store
    from src.portfolio.event_library import get_event

    category = (category or "").strip()
    if category not in _what_if.CATEGORIES:
        raise ValueError(f"unknown category '{category}'")
    concerns = (concerns or "").strip()
    if len(concerns) < 10:
        raise ValueError("describe your concerns (at least a sentence)")
    if reference_key:
        if get_event(reference_key) is None:
            raise ValueError(f"unknown reference crisis '{reference_key}'")
    else:
        reference_key = None
    search_override = (search_override or "auto").strip().lower()
    if search_override not in ("auto", "always", "never"):
        search_override = "auto"
    horizon_days = min(max(int(horizon_days or 90), 5), 365)

    holdings = [_holding_dict(r) for r in list_holdings(db, user_id)]
    if not holdings:
        return {"error": "no_holdings"}

    user_name = _user_display_name(db, user_id)

    scen = compute_scenario_hash(category, concerns, reference_key,
                                 search_override, horizon_days, holdings)
    cached = get_cached_what_if(user_id, scen)
    if cached is not None:
        if share and cached.get("llm"):
            # Idempotent by content hash — re-publishing an unchanged
            # scenario is a no-op (first author kept, no build_count bump).
            # Degraded cached runs (no LLM narrative) are never published.
            try:
                publish_what_if_scenario(user_id, user_name, category,
                                         concerns, reference_key,
                                         horizon_days, parent_id, cached)
            except Exception:
                logger.exception("what-if publish on cache hit failed "
                                 "(personal cache unaffected)")
        return {"cached": True, "scenario_hash": scen, "result": cached}

    key = (user_id, scen)
    with _RUNNING_LOCK:
        running_id = _RUNNING_WHAT_IFS.get(key)
        if running_id and job_store.get_job(running_id) and \
                job_store.get_job(running_id).get("status") in ("pending", "running"):
            return {"cached": False, "running": True, "job_id": running_id,
                    "scenario_hash": scen}

    # Sector map resolved NOW (request thread owns the db session) — the
    # simulation thread only receives plain data.
    sectors_map = {t: s.get("sector") for t, s in
                   _latest_signals([h["ticker"] for h in holdings]).items()
                   if s.get("sector")}

    job_id = job_store.create_job("what_if", ticker=None, user_id=user_id)
    with _RUNNING_LOCK:
        _RUNNING_WHAT_IFS[key] = job_id
    thread = threading.Thread(
        target=_execute_what_if_job,
        args=(job_id, user_id, scen, holdings, category, concerns,
              reference_key, search_override, horizon_days, sectors_map,
              bool(share), parent_id, user_name),
        name=f"whatif-{job_id[:8]}", daemon=True)
    thread.start()
    return {"cached": False, "job_id": job_id, "scenario_hash": scen}


# In-flight guard mirrors _RUNNING_REPLAYS (same dedupe semantics).
_RUNNING_WHAT_IFS: dict[tuple, str] = {}


def _execute_what_if_job(job_id: str, user_id: Optional[int], scen: str,
                         holdings: list[dict], category: str, concerns: str,
                         reference_key: Optional[str], search_override: str,
                         horizon_days: int, sectors_map: dict,
                         share: bool = True, parent_id: Optional[str] = None,
                         user_name: Optional[str] = None) -> None:
    from app.backend.services import complacency_job_store as job_store

    stop = threading.Event()
    start = datetime.now(timezone.utc)

    def _pulse():
        while not stop.wait(20):
            elapsed = int((datetime.now(timezone.utc) - start).total_seconds())
            mm, ss = divmod(elapsed, 60)
            try:
                job_store.update_progress(
                    job_id, "running",
                    f"simulating '{category}' · {mm}m {ss}s elapsed")
            except Exception:
                pass

    hb = threading.Thread(target=_pulse, daemon=True)
    hb.start()
    try:
        job_store.update_progress(job_id, "running",
                                  f"simulating '{category}' scenario")
        result = _what_if.run_what_if(
            holdings, category, concerns,
            reference_key=reference_key,
            search_override=search_override,
            horizon_days=horizon_days,
            sectors_map=sectors_map)
        save_what_if(user_id, scen, result)
        published_id = None
        if share and result.get("llm"):
            try:
                published_id = publish_what_if_scenario(
                    user_id, user_name, category, concerns, reference_key,
                    horizon_days, parent_id, result)
            except Exception:
                logger.exception("what-if publish failed (personal cache "
                                 "unaffected)")
        elif share:
            # Shared memory only gains from complete scenarios: a degraded
            # run (LLM failed, skeleton-only) has no narrative and no
            # assumptions to track, so it stays in the personal cache only.
            logger.info("what-if %s not published: degraded run "
                        "(no LLM narrative)", job_id)
        payload = {"scenario_hash": scen, "result": result}
        if published_id:
            payload["library_scenario_id"] = published_id
        job_store.complete_job(job_id, payload)
        logger.info("what-if %s done: category='%s' llm=%s warnings=%d published=%s",
                    job_id, category,
                    bool(result.get("llm")), len(result.get("warnings") or []),
                    published_id)
    except Exception as exc:
        logger.error("what-if %s failed: %s", job_id, exc)
        try:
            job_store.fail_job(job_id, str(exc)[:500])
        except Exception:
            pass
    finally:
        stop.set()
        with _RUNNING_LOCK:
            _RUNNING_WHAT_IFS.pop((user_id, scen), None)


def get_what_if_job(job_id: str, user_id: Optional[int]) -> Optional[dict]:
    """User-scoped job status (same pattern/rationale as get_replay_job).
    Covers both simulation jobs and assumption-check jobs."""
    from app.backend.services import complacency_job_store as job_store

    job = job_store.get_job(job_id)
    if not job or job.get("kind") not in ("what_if", "what_if_check"):
        return None
    row = _db.query_one(
        "SELECT user_id FROM complacency_jobs WHERE job_id = ?", [job_id])
    if row is None:
        return None
    if row["user_id"] != user_id:
        return None
    return job


# ── P6: joint scenario memory + assumption tracking ──────────────────────────
#
# Shared library of what-if scenarios (auto-published unless the user opts
# out). The narrative is portfolio-independent, so the dedupe key EXCLUDES
# holdings: same category+concerns+reference+horizon = one shared row,
# first author kept. Assumption rows carry the deterministic sensitivity
# (portfolio delta if the assumption holds, computed at publish time on the
# AUTHOR's skeleton). Checks append to a ledger; the newest verdict sets the
# assumption status. LLM never does arithmetic anywhere in this block.

_WHAT_IF_LIBRARY_DDL = """
CREATE TABLE IF NOT EXISTS what_if_scenario_library (
    scenario_id     TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL UNIQUE,
    created_by      INTEGER,
    created_by_name TEXT,
    created_at      TEXT NOT NULL,
    category        TEXT NOT NULL,
    concerns        TEXT NOT NULL,
    reference_key   TEXT,
    horizon_days    INTEGER,
    parent_id       TEXT,
    build_count     INTEGER NOT NULL DEFAULT 0,
    result_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_what_if_library_created
    ON what_if_scenario_library(created_at);
CREATE TABLE IF NOT EXISTS what_if_assumptions (
    assumption_id    TEXT PRIMARY KEY,
    scenario_id      TEXT NOT NULL,
    idx              INTEGER NOT NULL,
    metric           TEXT NOT NULL,
    watch_for        TEXT NOT NULL DEFAULT '',
    timing           TEXT NOT NULL DEFAULT '',
    linked_sector    TEXT,
    if_true_shift_pp REAL,
    author_delta_json TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_what_if_assumptions_scenario
    ON what_if_assumptions(scenario_id);
CREATE TABLE IF NOT EXISTS what_if_assumption_checks (
    check_id      TEXT PRIMARY KEY,
    assumption_id TEXT NOT NULL,
    user_id       INTEGER,
    user_name     TEXT,
    checked_at    TEXT NOT NULL,
    method        TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    evidence      TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_what_if_checks_assumption
    ON what_if_assumption_checks(assumption_id);
CREATE TABLE IF NOT EXISTS what_if_scenario_notes (
    note_id     TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    user_id     INTEGER,
    user_name   TEXT,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_what_if_notes_scenario
    ON what_if_scenario_notes(scenario_id);
"""


def _ensure_what_if_library_tables() -> None:
    _db.ensure_table(_WHAT_IF_LIBRARY_DDL)


def _user_display_name(db: Session, user_id: Optional[int]) -> Optional[str]:
    if user_id is None:
        return None
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            return None
        return u.name or (u.email.split("@")[0] if u.email else None) \
            or f"user-{user_id}"
    except Exception:
        logger.exception("user display-name lookup failed")
        return None


def compute_library_hash(category: str, concerns: str,
                         reference_key: Optional[str],
                         horizon_days: int) -> str:
    """Dedupe key for the shared library. Holdings and search_override are
    EXCLUDED — the narrative is portfolio-independent, so the same scenario
    from any user is one shared row. Version-gated like the personal cache
    (pre-v6 results lack sensitivity fields and never collide with v6+)."""
    import hashlib
    payload = {
        "scenario_version": _what_if.SCENARIO_VERSION,
        "category": category.strip(),
        "concerns": " ".join(concerns.split()),
        "reference_key": reference_key,
        "horizon_days": horizon_days,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def publish_what_if_scenario(user_id: Optional[int], user_name: Optional[str],
                             category: str, concerns: str,
                             reference_key: Optional[str], horizon_days: int,
                             parent_id: Optional[str],
                             result: dict) -> Optional[str]:
    """Idempotent upsert into the joint memory. Returns the scenario_id
    (existing row's when the content hash is already published — first
    author kept). build_count bumps on the parent only when this run
    inserts a NEW library row (forking without changing anything does not
    count as a build).

    Degraded runs (llm=None — the LLM call failed and only the
    deterministic skeleton exists) are refused: shared memory is
    narrative + trackable assumptions, and a skeleton-only row offers
    neither. Callers guard too; this is the safety net."""
    if not isinstance(result, dict) or not result.get("llm"):
        return None
    _ensure_what_if_library_tables()
    content_hash = compute_library_hash(category, concerns, reference_key,
                                        horizon_days)
    existing = _db.query_one(
        "SELECT scenario_id FROM what_if_scenario_library WHERE content_hash = ?",
        [content_hash])
    if existing:
        return existing["scenario_id"]

    if parent_id:
        prow = _db.query_one(
            "SELECT scenario_id FROM what_if_scenario_library "
            "WHERE scenario_id = ?", [parent_id])
        if prow is None:
            parent_id = None

    scenario_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    try:
        _db.execute(
            "INSERT INTO what_if_scenario_library "
            "(scenario_id, content_hash, created_by, created_by_name, "
            " created_at, category, concerns, reference_key, horizon_days, "
            " parent_id, build_count, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            [scenario_id, content_hash, user_id, user_name, now,
             category.strip(), " ".join((concerns or "").split()),
             reference_key, horizon_days, parent_id, json.dumps(result)])
    except Exception:
        # Concurrent publish of the same content hash — first writer wins.
        logger.info("what-if library insert raced for hash %s — reusing "
                    "existing row", content_hash[:12])
        row = _db.query_one(
            "SELECT scenario_id FROM what_if_scenario_library "
            "WHERE content_hash = ?", [content_hash])
        return row["scenario_id"] if row else None

    llm = (result or {}).get("llm") or {}
    skeleton_rows = ((result or {}).get("skeleton") or {}).get("holdings") or []
    inserted_assumptions = 0
    for idx, a in enumerate(llm.get("assumptions_to_watch") or []):
        if not isinstance(a, dict) or not a.get("metric"):
            continue
        linked = a.get("linked_sector")
        shift = a.get("if_true_shift_pp")
        delta_json = None
        if linked and shift is not None:
            try:
                delta_json = json.dumps(_what_if.apply_assumption_shift(
                    skeleton_rows, linked, shift))
            except Exception:
                logger.exception("author sensitivity compute failed")
        _db.execute(
            "INSERT INTO what_if_assumptions "
            "(assumption_id, scenario_id, idx, metric, watch_for, timing, "
            " linked_sector, if_true_shift_pp, author_delta_json, status, "
            " updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            [uuid.uuid4().hex, scenario_id, idx, str(a["metric"])[:500],
             str(a.get("watch_for") or "")[:500],
             str(a.get("timing") or "")[:200],
             linked, shift, delta_json, now])
        inserted_assumptions += 1

    if parent_id:
        _db.execute(
            "UPDATE what_if_scenario_library "
            "SET build_count = build_count + 1 WHERE scenario_id = ?",
            [parent_id])
    logger.info("what-if library: published '%s' as %s (parent=%s, %d "
                "assumptions)", category, scenario_id[:8], parent_id,
                inserted_assumptions)
    return scenario_id


def _assumption_status_tally(scenario_ids: list[str]) -> dict:
    if not scenario_ids:
        return {}
    placeholders = ",".join("?" * len(scenario_ids))
    rows = _db.query(
        f"SELECT scenario_id, status, COUNT(*) AS c FROM what_if_assumptions "
        f"WHERE scenario_id IN ({placeholders}) "
        f"GROUP BY scenario_id, status", list(scenario_ids))
    tally: dict = {}
    for r in rows:
        tally.setdefault(r["scenario_id"], {})[r["status"]] = r["c"]
    return tally


def list_what_if_library(limit: int = 50) -> list:
    _ensure_what_if_library_tables()
    rows = _db.query(
        "SELECT s.scenario_id, s.created_by_name, s.created_at, s.category, "
        "s.concerns, s.reference_key, s.horizon_days, s.parent_id, "
        "s.build_count, s.result_json, "
        "(SELECT COUNT(*) FROM what_if_scenario_notes n "
        "  WHERE n.scenario_id = s.scenario_id) AS notes_count "
        "FROM what_if_scenario_library s "
        "ORDER BY s.created_at DESC LIMIT ?", [int(limit)])
    tally = _assumption_status_tally([r["scenario_id"] for r in rows])
    out = []
    for r in rows:
        try:
            result = json.loads(r["result_json"])
        except (TypeError, ValueError):
            result = {}
        out.append({
            "scenario_id": r["scenario_id"],
            "category": r["category"],
            "concerns_excerpt": (r["concerns"] or "")[:240],
            "reference_key": r["reference_key"],
            "horizon_days": r["horizon_days"],
            "created_by_name": r["created_by_name"],
            "created_at": r["created_at"],
            "parent_id": r["parent_id"],
            "build_count": r["build_count"] or 0,
            "notes_count": r["notes_count"] or 0,
            "author_portfolio_est_pct":
                ((result.get("skeleton") or {}).get("portfolio_est_impact_pct")),
            "summary_excerpt":
                (((result.get("llm") or {}).get("scenario_summary")) or "")[:280],
            "assumption_status_tally": tally.get(r["scenario_id"], {}),
        })
    return out


def get_what_if_scenario(scenario_id: str) -> Optional[dict]:
    _ensure_what_if_library_tables()
    row = _db.query_one(
        "SELECT * FROM what_if_scenario_library WHERE scenario_id = ?",
        [scenario_id])
    if row is None:
        return None
    try:
        result = json.loads(row["result_json"])
    except (TypeError, ValueError):
        result = {}

    a_rows = _db.query(
        "SELECT * FROM what_if_assumptions WHERE scenario_id = ? ORDER BY idx",
        [scenario_id])
    assumption_ids = [a["assumption_id"] for a in a_rows]
    checks: list = []
    if assumption_ids:
        placeholders = ",".join("?" * len(assumption_ids))
        checks = _db.query(
            f"SELECT * FROM what_if_assumption_checks "
            f"WHERE assumption_id IN ({placeholders}) ORDER BY checked_at",
            assumption_ids)
    latest_check: dict = {}
    check_counts: dict = {}
    for c in checks:
        latest_check[c["assumption_id"]] = {
            "check_id": c["check_id"], "user_name": c["user_name"],
            "checked_at": c["checked_at"], "method": c["method"],
            "verdict": c["verdict"], "evidence": c["evidence"],
            "source": c["source"],
        }
        check_counts[c["assumption_id"]] = \
            check_counts.get(c["assumption_id"], 0) + 1

    assumptions = []
    for a in a_rows:
        delta = None
        if a["author_delta_json"]:
            try:
                delta = json.loads(a["author_delta_json"])
            except (TypeError, ValueError):
                delta = None
        assumptions.append({
            "assumption_id": a["assumption_id"], "idx": a["idx"],
            "metric": a["metric"], "watch_for": a["watch_for"],
            "timing": a["timing"], "linked_sector": a["linked_sector"],
            "if_true_shift_pp": a["if_true_shift_pp"],
            "author_delta": delta, "status": a["status"],
            "updated_at": a["updated_at"],
            "latest_check": latest_check.get(a["assumption_id"]),
            "checks_count": check_counts.get(a["assumption_id"], 0),
        })

    notes = _db.query(
        "SELECT note_id, user_id, user_name, note, created_at "
        "FROM what_if_scenario_notes WHERE scenario_id = ? "
        "ORDER BY created_at", [scenario_id])

    parent = None
    if row["parent_id"]:
        prow = _db.query_one(
            "SELECT scenario_id, category, created_at, created_by_name "
            "FROM what_if_scenario_library WHERE scenario_id = ?",
            [row["parent_id"]])
        if prow:
            parent = {"scenario_id": prow["scenario_id"],
                      "category": prow["category"],
                      "created_at": prow["created_at"],
                      "created_by_name": prow["created_by_name"]}
    children = _db.query_one(
        "SELECT COUNT(*) AS c FROM what_if_scenario_library "
        "WHERE parent_id = ?", [scenario_id])

    return {
        "scenario_id": row["scenario_id"],
        "category": row["category"],
        "concerns": row["concerns"],
        "reference_key": row["reference_key"],
        "horizon_days": row["horizon_days"],
        "created_by": row["created_by"],
        "created_by_name": row["created_by_name"],
        "created_at": row["created_at"],
        "build_count": row["build_count"] or 0,
        "parent": parent,
        "children_count": (children["c"] if children else 0),
        "result": result,
        "assumptions": assumptions,
        "notes": [dict(n) if not isinstance(n, dict) else n for n in notes],
    }


def add_what_if_note(scenario_id: str, user_id: Optional[int],
                     user_name: Optional[str], text: str) -> Optional[dict]:
    """Append a community note. Returns None when the scenario is unknown."""
    _ensure_what_if_library_tables()
    text = (text or "").strip()
    if not text:
        raise ValueError("note text is empty")
    if len(text) > 2000:
        raise ValueError("note too long (max 2000 characters)")
    row = _db.query_one(
        "SELECT scenario_id FROM what_if_scenario_library WHERE scenario_id = ?",
        [scenario_id])
    if row is None:
        return None
    note = {"note_id": uuid.uuid4().hex, "scenario_id": scenario_id,
            "user_id": user_id, "user_name": user_name, "note": text,
            "created_at": datetime.now(timezone.utc).isoformat()}
    _db.execute(
        "INSERT INTO what_if_scenario_notes "
        "(note_id, scenario_id, user_id, user_name, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [note["note_id"], scenario_id, user_id, user_name, text,
         note["created_at"]])
    return note


def compare_what_if_to_holdings(db: Session, user_id: Optional[int],
                                scenario_id: str) -> Optional[dict]:
    """Deterministic skeleton of the VIEWER's holdings under a shared
    scenario's reference/horizon + per-assumption sensitivity on the
    viewer's portfolio. Zero LLM calls."""
    _ensure_what_if_library_tables()
    row = _db.query_one(
        "SELECT reference_key, horizon_days FROM what_if_scenario_library "
        "WHERE scenario_id = ?", [scenario_id])
    if row is None:
        return None
    holdings = [_holding_dict(r) for r in list_holdings(db, user_id)]
    if not holdings:
        return {"error": "no_holdings"}

    from src.portfolio.event_library import get_event
    from src.tools.api import get_prices

    ref = get_event(row["reference_key"]) if row["reference_key"] else None
    horizon_days = int(row["horizon_days"] or 90)
    sectors_map = {t: s.get("sector") for t, s in
                   _latest_signals([h["ticker"] for h in holdings]).items()
                   if s.get("sector")}
    skeleton = _what_if._build_skeleton(holdings, ref, sectors_map,
                                        get_prices, horizon_days, {})

    a_rows = _db.query(
        "SELECT assumption_id, metric, linked_sector, if_true_shift_pp "
        "FROM what_if_assumptions WHERE scenario_id = ? ORDER BY idx",
        [scenario_id])
    sensitivities = []
    for a in a_rows:
        delta = _what_if.apply_assumption_shift(
            skeleton["holdings"], a["linked_sector"], a["if_true_shift_pp"])
        sensitivities.append({
            "assumption_id": a["assumption_id"], "metric": a["metric"],
            "linked_sector": a["linked_sector"],
            "if_true_shift_pp": a["if_true_shift_pp"], **delta})
    return {
        "scenario_id": scenario_id,
        "horizon_days": horizon_days,
        "skeleton": {
            "holdings": skeleton["holdings"],
            "portfolio_est_impact_pct": skeleton["portfolio_est_impact_pct"],
            "covered_weight_pct": skeleton["covered_weight_pct"],
        },
        "assumption_sensitivities": sensitivities,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── P6: assumption checks (market data + deep research) ─────────────────────

_CHECK_METHODS = ("deep_research", "market_data")


def start_assumption_check(user_id: Optional[int], user_name: Optional[str],
                           assumption_id: str, method: str) -> dict:
    """Kick a tracked what_if_check job. Deduped per assumption via the
    job store's ticker column (which carries the assumption_id)."""
    from app.backend.services import complacency_job_store as job_store
    _ensure_what_if_library_tables()

    method = (method or "").strip().lower()
    if method not in _CHECK_METHODS:
        raise ValueError("method must be 'deep_research' or 'market_data'")
    a = _db.query_one(
        "SELECT * FROM what_if_assumptions WHERE assumption_id = ?",
        [assumption_id])
    if a is None:
        raise ValueError("unknown assumption")
    s = _db.query_one(
        "SELECT * FROM what_if_scenario_library WHERE scenario_id = ?",
        [a["scenario_id"]])
    if s is None:
        raise ValueError("parent scenario missing")

    inflight = job_store.find_in_flight_job("what_if_check",
                                            ticker=assumption_id)
    if inflight:
        return {"running": True, "deduped": True,
                "job_id": inflight["job_id"]}

    job_id = job_store.create_job("what_if_check", ticker=assumption_id,
                                  user_id=user_id)
    thread = threading.Thread(
        target=_execute_assumption_check_job,
        args=(job_id, user_id, user_name, dict(a), dict(s), method),
        name=f"whatifchk-{job_id[:8]}", daemon=True)
    thread.start()
    return {"running": True, "deduped": False, "job_id": job_id}


def _check_window(scenario: dict) -> tuple:
    """(from, to) date strings since the scenario was created, capped at
    the 90-day window the FMP endpoints accept."""
    today = datetime.now(timezone.utc)
    since_raw = (scenario.get("created_at") or "")[:10]
    try:
        since = datetime.strptime(since_raw, "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        since = today - timedelta(days=7)
    since = max(since, today - timedelta(days=89))
    return since.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _market_data_reading(assumption: dict,
                         scenario: dict) -> tuple:
    """Deterministic FMP reading for an assumption (keyword registry →
    treasury curve / inflation / sector-ETF performance since scenario
    creation). (reading, source) or (None, None) when nothing matches."""
    from src.tools.api import (get_economic_indicator, get_prices,
                               get_treasury_rates)

    text = " ".join(str(assumption.get(k) or "")
                    for k in ("metric", "watch_for", "timing")).lower()
    frm, to = _check_window(scenario)
    parts: list = []

    if any(k in text for k in ("rate", "yield", "treasury", "bond",
                               "refinanc", "interest", "borrow", "fed ")):
        try:
            rows = get_treasury_rates(frm, to) or []
        except Exception:
            rows = []
        if rows:
            latest, base = rows[0], rows[-1]
            spread = round(float(latest.get("year10") or 0)
                           - float(latest.get("year2") or 0), 2)
            parts.append(
                f"US Treasury curve {base.get('date')} → {latest.get('date')}: "
                f"3M {base.get('month3')}% → {latest.get('month3')}%, "
                f"2Y {base.get('year2')}% → {latest.get('year2')}%, "
                f"10Y {base.get('year10')}% → {latest.get('year10')}% "
                f"(2Y10Y now {spread:+.2f}pp)")
    if any(k in text for k in ("cpi", "inflation", "price level")):
        try:
            rows = get_economic_indicator("inflationRate", frm, to) or []
        except Exception:
            rows = []
        if rows:
            parts.append(
                f"US inflation rate: {rows[-1].get('value')}% "
                f"({rows[-1].get('date')}) → {rows[0].get('value')}% "
                f"({rows[0].get('date')})")

    gics = _what_if._resolve_gics(assumption.get("linked_sector"))
    symbol = _what_if.GICS_SECTOR_SYMBOLS.get(gics) if gics else None
    if symbol:
        try:
            px = get_prices(symbol, frm, to) or []
        except Exception:
            px = []
        closes = sorted(((str(getattr(p, "time", "")),
                          float(getattr(p, "close", 0) or 0)) for p in px),
                        key=lambda t: t[0])
        closes = [(d, c) for d, c in closes if frm <= d <= to and c > 0]
        if len(closes) >= 2:
            (d0, c0), (d1, c1) = closes[0], closes[-1]
            parts.append(
                f"{symbol} ({gics} sector ETF): {c0:.2f} on {d0} → "
                f"{c1:.2f} on {d1} ({(c1 / c0 - 1) * 100:+.1f}%)")

    if not parts:
        return None, None
    return ("; ".join(parts),
            f"FMP treasury-rates/economic-indicators/prices {frm}→{to}")


class _CheckVerdict(BaseModel):
    verdict: str = "inconclusive"
    evidence: str = ""


def _norm_verdict(raw) -> str:
    v = str(raw or "").strip().lower().replace(" ", "_")
    if v in ("confirmed", "true", "yes", "holding", "materializing",
             "materialised", "materialized", "validated", "correct"):
        return "confirmed"
    if v in ("disconfirmed", "false", "no", "not_holding", "broken",
             "invalidated", "wrong", "refuted"):
        return "disconfirmed"
    if v == "no_data":
        return "no_data"
    return "inconclusive"


def _verdict_via_llm(assumption: dict, reading: str) -> tuple:
    """One cheap DeepSeek judgement call over a DETERMINISTIC reading —
    the numbers come from FMP, the LLM only classifies them."""
    import os
    from src.llm.models import ModelProvider, get_model

    model_name = os.environ.get("WHAT_IF_MODEL", _what_if.WHAT_IF_MODEL_NAME)
    try:
        llm = get_model(model_name, ModelProvider.DEEPSEEK, None)
    except Exception:
        llm = None
    if llm is None:
        return "inconclusive", "verdict model unavailable"
    user_prompt = (
        f"ASSUMPTION: {assumption.get('metric')}\n"
        f"WHAT WOULD CONFIRM IT: {assumption.get('watch_for') or '(unspecified)'}\n"
        f"MARKET READING (deterministic, trusted): {reading}\n\n"
        "Does the reading show the assumption is materialising? Respond in "
        'JSON format: {"verdict": "confirmed" | "disconfirmed" | '
        '"inconclusive", "evidence": "<= 200 chars quoting the reading"}')
    try:
        structured = llm.with_structured_output(_CheckVerdict,
                                                method="json_mode")
        out = structured.invoke([
            ("system", "You classify whether a scenario assumption is "
                       "materialising, based ONLY on the market reading "
                       "provided. Respond in JSON format."),
            ("human", user_prompt)])
        return _norm_verdict(out.verdict), (out.evidence or reading)[:500]
    except Exception:
        logger.exception("check verdict LLM call failed")
        return "inconclusive", f"verdict call failed; reading: {reading[:300]}"


def _deep_research_check(assumption: dict, scenario: dict) -> tuple:
    """One qwen web-search call (DashScope native search — Tavily-free).
    Lenient-parsed JSON verdict. Returns (verdict, evidence, source)."""
    from src.research_ideas.complacency.web_research import qwen_web_search

    prompt = (
        f"A what-if scenario '{scenario.get('category')}' was created on "
        f"{(scenario.get('created_at') or '')[:10]}.\n"
        f"SCENARIO CONCERNS: {(scenario.get('concerns') or '')[:600]}\n\n"
        f"ASSUMPTION TO VERIFY: {assumption.get('metric')}\n"
        f"WHAT WOULD CONFIRM IT: {assumption.get('watch_for') or '(unspecified)'}\n"
        f"EXPECTED VISIBILITY: {assumption.get('timing') or '(unspecified)'}\n\n"
        "Search for CURRENT evidence (news, data releases, company "
        "statements). Is this assumption beginning to hold true?\n"
        'Respond in JSON format: {"status": "confirmed" | "disconfirmed" | '
        '"inconclusive", "evidence": "<= 300 chars with concrete datapoints"}')
    text = qwen_web_search(
        prompt,
        system_prompt=("You verify macro/sector assumptions against current "
                       "web evidence. Respond in JSON format."),
        retries=1, request_timeout_s=240)
    source = "qwen_web_search (DashScope)"
    if not text:
        return "inconclusive", "web search unavailable or timed out", source
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                raw = obj.get("status") or obj.get("verdict") or obj.get("state")
                evidence = str(obj.get("evidence") or obj.get("summary")
                               or obj.get("answer") or "")[:500]
                return _norm_verdict(raw), evidence or text[:300], source
        except (TypeError, ValueError):
            pass
    return "inconclusive", text[:500], source


def _execute_assumption_check_job(job_id: str, user_id: Optional[int],
                                  user_name: Optional[str], assumption: dict,
                                  scenario: dict, method: str) -> None:
    from app.backend.services import complacency_job_store as job_store

    stop = threading.Event()
    start = datetime.now(timezone.utc)
    label = str(assumption.get("metric") or "assumption")[:60]

    def _pulse():
        while not stop.wait(20):
            elapsed = int((datetime.now(timezone.utc) - start).total_seconds())
            mm, ss = divmod(elapsed, 60)
            try:
                job_store.update_progress(
                    job_id, "running",
                    f"checking '{label}' ({method}) · {mm}m {ss}s elapsed")
            except Exception:
                pass

    hb = threading.Thread(target=_pulse, daemon=True)
    hb.start()
    try:
        job_store.update_progress(
            job_id, "running", f"checking '{label}' via {method}")
        source: Optional[str] = None
        if method == "market_data":
            reading, source = _market_data_reading(assumption, scenario)
            if reading is None:
                verdict = "no_data"
                evidence = ("No market-data series matched this assumption "
                            "(keywords: rates/yield/inflation, or a linked "
                            "GICS sector ETF).")
            else:
                verdict, evidence = _verdict_via_llm(assumption, reading)
        else:
            verdict, evidence, source = _deep_research_check(assumption,
                                                             scenario)

        check = {"check_id": uuid.uuid4().hex,
                 "assumption_id": assumption["assumption_id"],
                 "user_id": user_id, "user_name": user_name,
                 "checked_at": datetime.now(timezone.utc).isoformat(),
                 "method": method, "verdict": verdict,
                 "evidence": evidence, "source": source or ""}
        _db.execute(
            "INSERT INTO what_if_assumption_checks "
            "(check_id, assumption_id, user_id, user_name, checked_at, "
            " method, verdict, evidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [check["check_id"], check["assumption_id"], user_id, user_name,
             check["checked_at"], method, verdict, evidence, source or ""])
        if verdict in ("confirmed", "disconfirmed", "inconclusive"):
            _db.execute(
                "UPDATE what_if_assumptions SET status = ?, updated_at = ? "
                "WHERE assumption_id = ?",
                [verdict, check["checked_at"], assumption["assumption_id"]])
        job_store.complete_job(job_id, check)
        logger.info("what-if check %s done: %s via %s → %s",
                    job_id, label, method, verdict)
    except Exception as exc:
        logger.error("what-if check %s failed: %s", job_id, exc)
        try:
            job_store.fail_job(job_id, str(exc)[:500])
        except Exception:
            pass
    finally:
        stop.set()

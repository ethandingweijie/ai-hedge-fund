"""Outcome labels for valuations (B2).

A run records a prediction. This module records how it turned out, so error
can be measured, attributed to a cause, and eventually learned from.

One row per run and label:
  consensus_0d      the Street 12-month target at the time of the run
  px_30d .. px_365d the realised close on the first trading day on or after
                    run date + N days

Errors are log ratios, prediction over label: +0.20 means the model sat 22%
above the label. Logs so that over- and under-shooting by the same factor
score the same, and so a single 10x miss cannot dominate an average.

The 12-month target is the prediction compared against every label -- it is
the number written for a horizon -- with base IV scored alongside, because the
IV-vs-consensus gap is the bias the HK/SG sweeps measured.

Where a prediction is read from (2026-11-27): the run_features ledger, which every
run writes at save time and which no report deletion touches; the archive tables
(ticker_signals JOIN runs) fill in runs the ledger has not caught and supply the
consensus recorded at run time. Before this the sweep read only the archive, so a
run whose archive rows had gone could never receive a price label.

One label per ticker per day: the day's LATEST run is its prediction, earlier
same-day reruns are not scored (and a label written for one is removed).

A horizon whose close never arrives -- the window long past and nothing traded
since -- is recorded in valuation_outcome_terminal rather than retried forever
or dropped without trace.

What is deliberately NOT scored:
  * replayed dcf_range entries: flagged is_cache_copy, or content identical to
    an earlier run on the same ticker (rows that predate the flag). Counting
    them would weight a ticker by how often it happened to be re-run.
  * a label more than 5x from price_at_run either way: that is a mis-scaled
    feed (the MMG and Kingboard failure modes), not a valuation error.
  * HK/SG consensus for runs more than two days old. The only source is the
    page as it stands today, and today's consensus is not the one at the run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Iterator, Optional

from src.data import db as _db

logger = logging.getLogger(__name__)

CONSENSUS = "consensus_0d"
HORIZON_DAYS: dict[str, int] = {"px_30d": 30, "px_90d": 90,
                                "px_180d": 180, "px_365d": 365}
HORIZONS: tuple[str, ...] = (CONSENSUS, *HORIZON_DAYS)

#: Weight of each label in the blended score. Consensus exists at once, so it
#: is the whole score for a new run; realised prices take over as they mature,
#: and the 12-month close -- the horizon the targets are written for -- counts
#: most. Reported next to every blended figure.
BLEND_WEIGHTS: dict[str, float] = {CONSENSUS: 1.0, "px_30d": 0.25,
                                   "px_90d": 0.75, "px_180d": 1.5,
                                   "px_365d": 3.0}

_LABEL_SANITY = 5.0
_PRICE_WINDOW_DAYS = 7
_CONSENSUS_FRESH_DAYS = 2
#: Days past a horizon's price window before a missing close is recorded as terminal.
TERMINAL_GRACE_DAYS = 14

#: Keys that differ between a replay and its original without the prediction
#: differing. Excluded from the content hash used to find pre-flag copies.
_VOLATILE_KEYS = frozenset({"is_cache_copy", "cache_source_run_at",
                            "_engine_cache_version", "consensus_at_run",
                            "param_version", "ledger_schema"})

_GROUPABLE = frozenset({"market", "sector", "profile", "routing_winner",
                        "param_version"})
#: Provenance groupings, read from run_features beside each label (2026-11-27): which
#: model ran the pipeline and researched, and which engine build. Scorecard only.
_PROVENANCE_GROUPS = frozenset({"pipeline_model", "research_model", "research_tier",
                                "engine_version"})

_COLUMNS = ["outcome_key", "run_id", "ticker", "horizon", "run_date",
            "label_date", "label_value", "label_source", "price_at_run",
            "base_iv", "pt_12m", "pm_target", "bear_iv", "bull_iv",
            "iv_log_err", "pt_log_err", "pm_log_err", "in_band",
            "direction_hit", "market", "sector", "profile", "routing_winner",
            "param_version", "scored_at"]

_DDL = """
CREATE TABLE IF NOT EXISTS valuation_outcomes (
    outcome_key    TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    horizon        TEXT NOT NULL,
    run_date       TEXT NOT NULL,
    label_date     TEXT,
    label_value    REAL NOT NULL,
    label_source   TEXT,
    price_at_run   REAL,
    base_iv        REAL,
    pt_12m         REAL,
    pm_target      REAL,
    bear_iv        REAL,
    bull_iv        REAL,
    iv_log_err     REAL,
    pt_log_err     REAL,
    pm_log_err     REAL,
    in_band        INTEGER,
    direction_hit  INTEGER,
    market         TEXT,
    sector         TEXT,
    profile        TEXT,
    routing_winner TEXT,
    param_version  TEXT,
    scored_at      TEXT NOT NULL
)
"""
_DDL_IDX = """
CREATE INDEX IF NOT EXISTS idx_valuation_outcomes_horizon
    ON valuation_outcomes (horizon, run_date)
"""
_DDL_SWEEPS = """
CREATE TABLE IF NOT EXISTS valuation_outcome_sweeps (
    sweep_date  TEXT PRIMARY KEY,
    finished_at TEXT NOT NULL,
    report      TEXT
)
"""

#: Horizons that can never be labelled: no close in the window, the window long past,
#: and nothing traded since (delisted / halted). Kept apart from valuation_outcomes so
#: no reader of label_value ever meets a made-up label.
_DDL_TERMINAL = """
CREATE TABLE IF NOT EXISTS valuation_outcome_terminal (
    outcome_key     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    run_date        TEXT NOT NULL,
    target_date     TEXT NOT NULL,
    reason          TEXT NOT NULL,
    last_close      REAL,
    last_close_date TEXT,
    recorded_at     TEXT NOT NULL
)
"""
_TERMINAL_COLUMNS = ["outcome_key", "run_id", "ticker", "horizon", "run_date", "target_date",
                     "reason", "last_close", "last_close_date", "recorded_at"]

_tables_ready_key: Optional[tuple] = None


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL)
    _db.ensure_table(_DDL_SWEEPS)
    _db.ensure_table(_DDL_TERMINAL)
    _db.execute(_DDL_IDX)
    _tables_ready_key = key


def _upsert_sql(table: str, conflict_col: str, columns: list[str]) -> str:
    ph = ", ".join("?" * len(columns))
    if _db.is_postgres():
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns
                            if c != conflict_col)
        return (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({ph}) "
                f"ON CONFLICT ({conflict_col}) DO UPDATE SET {updates}")
    return (f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
            f"VALUES ({ph})")


# ── helpers ─────────────────────────────────────────────────────────────────

def _pos(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 and math.isfinite(f) else None


def _loads(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def market_of(ticker: str) -> str:
    t = (ticker or "").upper()
    for suffix, market in ((".HK", "HK"), (".SI", "SG"), (".T", "JP"),
                           (".KS", "KR"), (".KQ", "KR"), (".SS", "CN"),
                           (".SZ", "CN")):
        if t.endswith(suffix):
            return market
    return "US"


def _content_hash(dcf_entry: dict) -> str:
    stable = {k: v for k, v in dcf_entry.items() if k not in _VOLATILE_KEYS}
    blob = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def is_unrated(dr: Optional[dict]) -> bool:
    """True when the engine withheld this valuation (Unrated / Pre-Revenue)."""
    return ((dr or {}).get("rating_state") or {}).get("state") == "unrated"


def _prediction(row, ticker: str, dr: dict) -> Optional[dict]:
    try:
        run_date = date.fromisoformat(str(row["run_at"])[:10])
    except (TypeError, ValueError):
        return None
    # An unrated name made no prediction. Tested BEFORE the three figures,
    # because a row written by an older portfolio manager can still carry a
    # `price_target` beside an unrated valuation, and scoring that would judge the
    # model on a number it declined to publish.
    if is_unrated(dr):
        return None
    # Owner, 2026-10-03: the tracker scores the AGENT's estimates, never a user's. A user's
    # estimate override is applied on read and never reaches these archive rows; a run the
    # pipeline built on a CARRIED user override says so in its dcf_range and is not scored.
    if dr.get("estimate_override_carried"):
        return None
    scenario = _loads(row["scenario_json"]) or {}
    iv = _pos((dr.get("base") or {}).get("intrinsic_value"))
    # Two targets, scored separately: the engine's deterministic 12-month
    # target, which a calibration can change, and the portfolio manager's,
    # which is the one the report shows. They can be far apart -- one local
    # CRWD row carries an engine target of 31.71 and a PM target of 420.
    pt = _pos(scenario.get("12m_price_target"))
    pm = _pos(row["price_target"])
    if iv is None and pt is None and pm is None:
        return None
    return {
        "run_id": row["run_id"], "ticker": ticker, "run_date": run_date,
        # price_at_run is empty on many archived rows; the scenario agent
        # records the price it saw. Without a price the mis-scale guard and
        # the direction check would silently do nothing.
        "price_at_run": (_pos(row["price_at_run"])
                         or _pos(scenario.get("current_price"))),
        "base_iv": iv, "pt_12m": pt, "pm_target": pm,
        "bear_iv": _pos((dr.get("bear") or {}).get("intrinsic_value")),
        "bull_iv": _pos((dr.get("bull") or {}).get("intrinsic_value")),
        "market": market_of(ticker),
        "sector": row["sector"],
        "profile": dr.get("profile"),
        "routing_winner": (dr.get("routing_trace") or {}).get("winner"),
        "param_version": dr.get("param_version"),
    }


def _key(pred: dict, horizon: str) -> str:
    return f"{pred['run_id']}|{pred['ticker']}|{horizon}"


def _default_closes(ticker: str, start: str, end: str) -> list[tuple[str, float]]:
    from src.tools.api import get_prices
    return [(p.time[:10], float(p.close))
            for p in (get_prices(ticker, start, end) or []) if p.close]


def _default_consensus(ticker: str) -> Optional[dict]:
    from src.tools.target_price import get_consensus_target
    return get_consensus_target(ticker)


def _consensus_label(ticker: str, dr: dict, run_date: date, today: date,
                     consensus_fn: Callable[[str], Optional[dict]]):
    """(target, label_date, source) or None."""
    rec = dr.get("consensus_at_run") or {}
    if rec.get("status") == "recorded" and _pos(rec.get("target")):
        return (float(rec["target"]),
                str(rec.get("fetched_at") or run_date.isoformat())[:10],
                "fmp_at_run")
    legacy = dr.get("consensus_pt") or {}
    if not rec and _pos(legacy.get("consensus")):
        # Rows before the ledger: consensus_pt was fetched during the run.
        return float(legacy["consensus"]), run_date.isoformat(), "fmp_at_run"
    if (ticker.endswith((".HK", ".SI", ".KS", ".KQ"))
            and (today - run_date).days <= _CONSENSUS_FRESH_DAYS):
        found = consensus_fn(ticker) or {}
        if not found.get("suspect") and _pos(found.get("target")):
            return (float(found["target"]), today.isoformat(),
                    "stockanalysis_within_2d")
    return None


def _close_on_or_after(series: list[tuple[str, float]], target: date):
    limit = target + timedelta(days=_PRICE_WINDOW_DAYS)
    for d, close in series:
        try:
            day = date.fromisoformat(d[:10])
        except ValueError:
            continue
        if target <= day <= limit and _pos(close):
            return day.isoformat(), float(close)
    return None


def _labelled_row(pred: dict, horizon: str, label: float, label_date: str,
                  source: str, now_iso: str) -> dict:
    def log_err(p):
        return round(math.log(p / label), 6) if p else None

    bear, bull = pred["bear_iv"], pred["bull_iv"]
    in_band = (int(min(bear, bull) <= label <= max(bear, bull))
               if bear and bull else None)
    price, iv = pred["price_at_run"], pred["base_iv"]
    direction = None
    if price and iv and iv != price and label != price:
        direction = int((iv - price) * (label - price) > 0)
    return {
        "outcome_key": _key(pred, horizon), "run_id": pred["run_id"],
        "ticker": pred["ticker"], "horizon": horizon,
        "run_date": pred["run_date"].isoformat(), "label_date": label_date,
        "label_value": label, "label_source": source,
        "price_at_run": price, "base_iv": iv, "pt_12m": pred["pt_12m"],
        "pm_target": pred["pm_target"],
        "bear_iv": bear, "bull_iv": bull,
        "iv_log_err": log_err(iv), "pt_log_err": log_err(pred["pt_12m"]),
        "pm_log_err": log_err(pred["pm_target"]),
        "in_band": in_band, "direction_hit": direction,
        "market": pred["market"], "sector": pred["sector"],
        "profile": pred["profile"], "routing_winner": pred["routing_winner"],
        "param_version": pred["param_version"], "scored_at": now_iso,
    }


def _iter_signal_rows(page_size: int) -> Iterator:
    """ticker_signals with a dcf_range, OLDEST first, so the original of a
    replayed entry is always seen before its copies."""
    from src.memory import run_archive
    offset = 0
    while True:
        page = run_archive._fetch(
            "SELECT ts.run_id, ts.ticker, r.run_at, r.sector, ts.price_at_run, "
            "ts.price_target, ts.dcf_range_json, ts.scenario_json "
            "FROM ticker_signals ts JOIN runs r ON r.run_id = ts.run_id "
            "WHERE ts.dcf_range_json IS NOT NULL "
            "ORDER BY r.run_at ASC, ts.run_id ASC LIMIT ? OFFSET ?",
            [page_size, offset])
        if not page:
            return
        yield from page
        if len(page) < page_size:
            return
        offset += page_size


# ── scoring ─────────────────────────────────────────────────────────────────

def _ledger_rows() -> list[dict]:
    """run_features, every row (the exclusions are counted here, not filtered in SQL).
    Empty when the ledger is switched off or unreadable: the archive path still runs."""
    try:
        from src.memory import run_features as rf
        return rf.rows(exclude_user=False)
    except Exception as exc:                               # noqa: BLE001
        logger.warning("valuation_outcomes: run_features unavailable: %s", exc)
        return []


def _ledger_prediction(f: dict) -> Optional[dict]:
    """The prediction a run_features row recorded, shaped like _prediction's. The ledger
    keeps the same figures the archive blobs hold (spot, base/bear/bull IV, the engine's
    12-month target, the PM's target) and it outlives the archive tables."""
    try:
        run_date = date.fromisoformat(str(f.get("run_date") or "")[:10])
    except ValueError:
        return None
    iv, pt, pm = _pos(f.get("iv_base")), _pos(f.get("pt_12m")), _pos(f.get("pm_target"))
    if iv is None and pt is None and pm is None:
        return None
    ticker = (f.get("ticker") or "").upper()
    return {
        "run_id": f["run_id"], "ticker": ticker, "run_date": run_date,
        "run_at": str(f.get("run_at") or run_date.isoformat()),
        "price_at_run": _pos(f.get("spot")),
        "base_iv": iv, "pt_12m": pt, "pm_target": pm,
        "bear_iv": _pos(f.get("iv_bear")), "bull_iv": _pos(f.get("iv_bull")),
        "market": f.get("market") or market_of(ticker),
        "sector": f.get("sector"), "profile": f.get("profile"),
        "routing_winner": f.get("routing_winner"),
        "param_version": f.get("param_version"),
    }


def _canonical(preds: dict[str, dict]) -> tuple[dict[str, dict], set[str]]:
    """One prediction per ticker per day.

    The rule: of a ticker's runs on the same calendar day (UTC date of run_at), the
    LATEST run is the day's prediction -- the one the owner was left looking at -- and
    the earlier ones are reruns (a fix, a provider toggle, a retry). Ties on run_at go to
    the larger run_id so the choice is stable. Without this a ticker re-run five times in
    an afternoon carried five times the weight of one run once.

    Returns (kept, superseded run|ticker keys)."""
    best: dict[tuple[str, date], tuple[str, dict]] = {}
    for key, p in preds.items():
        day = (p["ticker"], p["run_date"])
        cur = best.get(day)
        if cur is None or (p.get("run_at") or "", p["run_id"]) > (cur[1].get("run_at") or "", cur[1]["run_id"]):
            best[day] = (key, p)
    kept = {k: p for k, p in best.values()}
    return kept, set(preds) - set(kept)


def _terminal_candidates_ok(series_by_ticker: dict[str, Optional[list]]) -> bool:
    """A terminal label is only recorded when the price feed is demonstrably answering:
    some ticker in the same sweep came back with closes. An empty answer for every name
    is an outage, not a market full of delistings."""
    return any(bool(v) for v in series_by_ticker.values())


_REPORT_KEYS = ("runs_seen", "ledger_rows", "archive_only", "ledger_only",
                "skipped_no_prediction", "skipped_unrated",
                "skipped_override_carried", "skipped_cache_copy",
                "skipped_same_day_rerun", "skipped_mis_scaled",
                "no_consensus", "not_matured", "no_price", "terminal_no_price",
                "superseded_removed")


def _gather(page_size: int, report: dict) -> tuple[dict[str, dict], dict[str, dict], set[str]]:
    """Every scoreable prediction, one per ticker per day: (preds by run|ticker, the
    archive dcf_range by run|ticker for the consensus label, superseded run|ticker keys).
    Counts each exclusion into `report`."""
    preds: dict[str, dict] = {}
    archive_dr: dict[str, dict] = {}
    archive_copy: set[str] = set()

    # 1. the archive: consensus-at-run, the content-hash copy check, and any run the
    #    ledger lacks. OLDEST first so an original is seen before its replays.
    seen: dict[str, set[str]] = {}
    archive_preds: dict[str, dict] = {}
    archive_skips: dict[str, str] = {}
    for row in _iter_signal_rows(page_size):
        ticker = (row["ticker"] or "").upper()
        dr = _loads(row["dcf_range_json"])
        rk = f"{row['run_id']}|{ticker}"
        if not ticker or not isinstance(dr, dict) or not dr:
            archive_skips[rk] = "skipped_no_prediction"
            continue
        archive_dr[rk] = dr
        digest = _content_hash(dr)
        if dr.get("is_cache_copy") or digest in seen.setdefault(ticker, set()):
            archive_copy.add(rk)
            archive_skips[rk] = "skipped_cache_copy"
            continue
        seen[ticker].add(digest)
        pred = _prediction(row, ticker, dr)
        if pred is None:
            archive_skips[rk] = ("skipped_unrated" if is_unrated(dr) else
                                 "skipped_override_carried" if dr.get("estimate_override_carried")
                                 else "skipped_no_prediction")
            continue
        pred["run_at"] = str(row["run_at"])
        archive_preds[rk] = pred

    # 2. the ledger is the authority for every run it holds.
    ledger_keys: set[str] = set()
    for f in _ledger_rows():
        rk = f"{f['run_id']}|{(f.get('ticker') or '').upper()}"
        ledger_keys.add(rk)
        report["ledger_rows"] += 1
        if rk not in archive_dr and rk not in archive_skips:
            report["ledger_only"] += 1
        if int(f.get("cache_copy") or 0) or rk in archive_copy:
            report["skipped_cache_copy"] += 1
            continue
        if int(f.get("unrated") or 0):
            report["skipped_unrated"] += 1
            continue
        if int(f.get("override_carried") or 0):
            report["skipped_override_carried"] += 1
            continue
        pred = _ledger_prediction(f)
        if pred is None:
            report["skipped_no_prediction"] += 1
            continue
        preds[rk] = pred
    for rk, reason in archive_skips.items():
        if rk not in ledger_keys:
            report[reason] += 1
    for rk, pred in archive_preds.items():
        if rk not in ledger_keys:
            report["archive_only"] += 1
            preds[rk] = pred
    report["runs_seen"] = len(ledger_keys | set(archive_dr) | set(archive_skips))

    # 3. one prediction per ticker per day
    preds, superseded = _canonical(preds)
    report["skipped_same_day_rerun"] = len(superseded)
    return preds, archive_dr, superseded


def score_matured(*, today: Optional[date] = None,
                  closes_fn: Optional[Callable] = None,
                  consensus_fn: Optional[Callable] = None,
                  write: bool = True, page_size: int = 200) -> dict:
    """Label every run horizon that has matured and is not yet labelled.

    Where the prediction comes from: the run_features ledger first -- it holds every
    run's spot, IVs and targets and is never deleted with a report -- and the archive
    (ticker_signals JOIN runs) for any run the ledger has not caught yet. The archive
    also supplies the consensus recorded at run time and the content-hash cache-copy
    check. A run whose archive rows are gone is still labelled from the ledger.

    One label per ticker per day (see _canonical); a label already written for a
    superseded rerun is removed on a writing sweep.

    A price horizon with no close in its window is retried while the window is recent.
    Once it is TERMINAL_GRACE_DAYS past and the ticker has had no close since the target
    date (delisted, halted, renamed), a terminal record is written to
    valuation_outcome_terminal -- with the last close before the target where there was
    one -- so the name is counted, not silently dropped, and is not refetched daily.

    Idempotent: a labelled or terminal (run, horizon) is never rescored. The first sweep
    is therefore the backfill -- it labels all history at once."""
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    closes_fn = closes_fn or _default_closes
    consensus_fn = consensus_fn or _default_consensus
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {r["outcome_key"]
                for r in _db.query("SELECT outcome_key FROM valuation_outcomes")}
    terminal = {r["outcome_key"]
                for r in _db.query("SELECT outcome_key FROM valuation_outcome_terminal")}

    report = {k: 0 for k in _REPORT_KEYS}
    preds, archive_dr, superseded = _gather(page_size, report)
    out: list[dict] = []

    needs: dict[str, list[tuple[dict, str, date]]] = {}

    def add(pred, horizon, label, label_date, source):
        price = pred["price_at_run"]
        if price and not (1 / _LABEL_SANITY <= label / price <= _LABEL_SANITY):
            report["skipped_mis_scaled"] += 1
            return
        out.append(_labelled_row(pred, horizon, label, label_date, source,
                                 now_iso))

    for rk, pred in sorted(preds.items(), key=lambda kv: (kv[1]["run_date"], kv[0])):
        ticker = pred["ticker"]
        if _key(pred, CONSENSUS) not in existing:
            label = _consensus_label(ticker, archive_dr.get(rk) or {}, pred["run_date"],
                                     today, consensus_fn)
            if label is None:
                report["no_consensus"] += 1
            else:
                add(pred, CONSENSUS, *label)

        for horizon, days in HORIZON_DAYS.items():
            key = _key(pred, horizon)
            if key in existing or key in terminal:
                continue
            target = pred["run_date"] + timedelta(days=days)
            if target >= today:
                report["not_matured"] += 1
                continue
            needs.setdefault(ticker, []).append((pred, horizon, target))

    # One fetch per ticker, from its earliest run date to today: it holds every window,
    # and it is also what tells a data gap (closes after the target) from a name that
    # stopped trading (none), and the last close before the target for the record.
    series_by_ticker: dict[str, Optional[list]] = {}
    misses: list[tuple[dict, str, date, list]] = []
    for ticker, items in needs.items():
        start = min(p["run_date"] for p, _, _ in items)
        try:
            series = sorted(closes_fn(ticker, start.isoformat(), today.isoformat()) or [])
        except Exception as exc:                           # noqa: BLE001
            logger.warning("valuation_outcomes: prices for %s failed: %s",
                           ticker, exc)
            series = None
        series_by_ticker[ticker] = series
        for pred, horizon, target in items:
            hit = _close_on_or_after(series or [], target)
            if hit is None:
                if series is not None:                     # a failed fetch is never terminal
                    misses.append((pred, horizon, target, series))
                else:
                    report["no_price"] += 1
                continue
            add(pred, horizon, hit[1], hit[0], "close")

    # 4. terminal: past the grace period, and nothing has traded since the target date
    terminal_rows: list[list] = []
    feed_alive = _terminal_candidates_ok(series_by_ticker)
    for pred, horizon, target, hist in misses:
        overdue = today > target + timedelta(days=_PRICE_WINDOW_DAYS + TERMINAL_GRACE_DAYS)
        after = [1 for d, c in hist if str(d)[:10] >= target.isoformat() and _pos(c)]
        if not (overdue and feed_alive) or after:          # recent, an outage, or a data gap
            report["no_price"] += 1
            continue
        before = [(d, c) for d, c in hist if str(d)[:10] < target.isoformat() and _pos(c)]
        last_d, last_c = (str(before[-1][0])[:10], float(before[-1][1])) if before else (None, None)
        report["terminal_no_price"] += 1
        terminal_rows.append([_key(pred, horizon), pred["run_id"], pred["ticker"], horizon,
                              pred["run_date"].isoformat(), target.isoformat(),
                              "no_close_since_target" if before else "no_close_after_run",
                              last_c, last_d, now_iso])

    report["labels"] = len(out)
    report["terminal"] = len(terminal_rows)
    report["written"] = 0
    if write:
        if out:
            _db.executemany(_upsert_sql("valuation_outcomes", "outcome_key", _COLUMNS),
                            [[r[c] for c in _COLUMNS] for r in out])
            report["written"] = len(out)
        if terminal_rows:
            _db.executemany(_upsert_sql("valuation_outcome_terminal", "outcome_key",
                                        _TERMINAL_COLUMNS), terminal_rows)
        if superseded:
            stale = [k.split("|", 1) for k in superseded]
            labelled = {(r["run_id"], r["ticker"]) for r in _db.query(
                "SELECT DISTINCT run_id, ticker FROM valuation_outcomes")}
            gone = [[rid, t] for rid, t in stale if (rid, t) in labelled]
            if gone:
                _db.executemany("DELETE FROM valuation_outcomes WHERE run_id = ? AND ticker = ?",
                                gone)
            report["superseded_removed"] = len(gone)
    return report


def terminal_counts() -> dict[str, int]:
    """Terminal (unlabellable) horizons by horizon, for the scorecard and diagnostics."""
    _ensure_tables()
    return {r["horizon"]: int(r["n"]) for r in _db.query(
        "SELECT horizon, COUNT(*) AS n FROM valuation_outcome_terminal GROUP BY horizon")}

def label_plan(*, today: Optional[date] = None, page_size: int = 200) -> dict:
    """What a sweep would have to label, with no price fetch: the canonical predictions,
    how many have at least one matured price horizon not yet labelled or terminal, and
    the count per horizon. The backfill script prints it before it fetches anything."""
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    report = {k: 0 for k in _REPORT_KEYS}
    preds, _dr, superseded = _gather(page_size, report)
    done = {r["outcome_key"] for r in _db.query("SELECT outcome_key FROM valuation_outcomes")}
    done |= {r["outcome_key"] for r in _db.query(
        "SELECT outcome_key FROM valuation_outcome_terminal")}
    by_h = {h: 0 for h in HORIZON_DAYS}
    with_any = 0
    for pred in preds.values():
        hit = False
        for horizon, days in HORIZON_DAYS.items():
            if _key(pred, horizon) in done or pred["run_date"] + timedelta(days=days) >= today:
                continue
            by_h[horizon] += 1
            hit = True
        with_any += int(hit)
    return {"today": today.isoformat(), "predictions": len(preds),
            "same_day_reruns": len(superseded), "with_matured_horizon": with_any,
            "matured_by_horizon": by_h,
            "sources": {k: report[k] for k in ("ledger_rows", "ledger_only", "archive_only")},
            "excluded": {k: report[k] for k in ("skipped_no_prediction", "skipped_unrated",
                                                "skipped_override_carried", "skipped_cache_copy")}}



def score_actions(*, today: Optional[date] = None,
                  closes_fn: Optional[Callable] = None,
                  min_age_days: int = 30) -> dict:
    """Fill ticker_signals.outcome (the ±5% action scoring) for runs at least
    `min_age_days` old.

    That scorer existed but its only caller is off in production, so every
    outcome stayed PENDING and the M1 lesson loop, which triggers on an
    INCORRECT prior run, never fired. ACTION_OUTCOME_SCORING=false disables."""
    if os.environ.get("ACTION_OUTCOME_SCORING", "true").strip().lower() in (
            "0", "false", "no", "off"):
        return {"enabled": False}
    from src.memory import run_archive
    today = today or datetime.now(timezone.utc).date()
    closes_fn = closes_fn or _default_closes
    cutoff = (today - timedelta(days=min_age_days)).isoformat()
    rows = run_archive._fetch(
        "SELECT DISTINCT ts.ticker FROM ticker_signals ts "
        "JOIN runs r ON r.run_id = ts.run_id "
        "WHERE ts.outcome = 'PENDING' AND substr(r.run_at, 1, 10) <= ?",
        [cutoff])
    scored, no_price = 0, 0
    for r in rows:
        ticker = r["ticker"]
        try:
            series = sorted(closes_fn(ticker, (today - timedelta(days=10)).isoformat(),
                                      today.isoformat()) or [])
        except Exception:                                  # noqa: BLE001
            series = []
        if not series:
            no_price += 1
            continue
        scored += run_archive.update_outcomes(
            ticker, series[-1][1], today.isoformat(), days_back=min_age_days)
    return {"enabled": True, "tickers": len(rows), "rows_scored": scored,
            "no_price": no_price}


def mark_swept(report: dict, today: Optional[date] = None) -> None:
    _ensure_tables()
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    _db.execute(
        _upsert_sql("valuation_outcome_sweeps", "sweep_date",
                    ["sweep_date", "finished_at", "report"]),
        [day, datetime.now(timezone.utc).isoformat(timespec="seconds"),
         json.dumps(report, default=str)[:4000]])


def swept_today(today: Optional[date] = None) -> bool:
    _ensure_tables()
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    return _db.query_one(
        "SELECT sweep_date FROM valuation_outcome_sweeps WHERE sweep_date = ?",
        [day]) is not None


# ── scorecard ───────────────────────────────────────────────────────────────

def _pct(log_value: Optional[float]) -> Optional[float]:
    return None if log_value is None else round((math.exp(log_value) - 1) * 100, 1)


def _factor(abs_log_value: Optional[float]) -> Optional[float]:
    """Typical miss as a multiple, 1.0 = exact. A percentage misleads here:
    being 13.7x too low reads as "-92.7%" signed but "+1265%" if the absolute
    log error is turned back into a percentage."""
    return None if abs_log_value is None else round(math.exp(abs_log_value), 2)


def _rate(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def scorecard(group_by: tuple[str, ...] = ("market",)) -> dict:
    """Accuracy per group and horizon, plus a blended score per group.

    Per-horizon: n, median signed and absolute error of the 12-month target
    (and of base IV), bear-bull band coverage and direction hit rate.
    Blended: each run's available labels averaged with BLEND_WEIGHTS, then
    the median across runs."""
    allowed = _GROUPABLE | _PROVENANCE_GROUPS
    bad = [g for g in group_by if g not in allowed]
    if bad:
        raise ValueError(f"cannot group by {bad}; allowed: {sorted(allowed)}")
    _ensure_tables()
    cols = sorted(set(group_by) | {"run_id", "ticker", "horizon", "iv_log_err",
                                   "pt_log_err", "pm_log_err", "in_band",
                                   "direction_hit"})
    prov = [g for g in group_by if g in _PROVENANCE_GROUPS]
    if prov:
        from src.memory import run_features as rf
        rf._ensure_tables()
        select = ", ".join(f"f.{c}" if c in _PROVENANCE_GROUPS else f"o.{c}" for c in cols)
        rows = _db.query(f"SELECT {select} FROM valuation_outcomes o LEFT JOIN run_features f "
                         "ON f.run_id = o.run_id AND f.ticker = o.ticker")
    else:
        rows = _db.query(f"SELECT {', '.join(cols)} FROM valuation_outcomes")

    groups: dict[str, dict] = {}
    for r in rows:
        label = " / ".join(str(r[g] or "?") for g in group_by) or "all"
        g = groups.setdefault(label, {"horizons": {}, "runs": {}})
        h = g["horizons"].setdefault(r["horizon"], {"pt": [], "iv": [], "pm": [],
                                                    "band": [], "dir": []})
        if r["pt_log_err"] is not None:
            h["pt"].append(r["pt_log_err"])
        if r["iv_log_err"] is not None:
            h["iv"].append(r["iv_log_err"])
        if r["pm_log_err"] is not None:
            h["pm"].append(r["pm_log_err"])
        h["band"].append(r["in_band"])
        h["dir"].append(r["direction_hit"])
        err = r["pt_log_err"] if r["pt_log_err"] is not None else r["iv_log_err"]
        if err is not None:
            g["runs"].setdefault((r["run_id"], r["ticker"]), {})[r["horizon"]] = err

    out_groups = {}
    for label, g in sorted(groups.items()):
        horizons = {}
        for hz in HORIZONS:
            h = g["horizons"].get(hz)
            if not h:
                continue
            horizons[hz] = {
                "n": max(len(h["pt"]), len(h["iv"])),
                "median_signed_pct": _pct(median(h["pt"])) if h["pt"] else None,
                "median_miss_factor": _factor(median(abs(x) for x in h["pt"])) if h["pt"] else None,
                "median_iv_signed_pct": _pct(median(h["iv"])) if h["iv"] else None,
                "median_pm_signed_pct": _pct(median(h["pm"])) if h["pm"] else None,
                "band_coverage": _rate(h["band"]),
                "direction_hit_rate": _rate(h["dir"]),
            }
        blended = []
        for per_run in g["runs"].values():
            w = sum(BLEND_WEIGHTS[hz] for hz in per_run)
            if w:
                blended.append(sum(BLEND_WEIGHTS[hz] * e for hz, e in per_run.items()) / w)
        out_groups[label] = {
            "horizons": horizons,
            "blended": {"n_runs": len(blended),
                        "median_signed_pct": _pct(median(blended)) if blended else None,
                        "median_miss_factor": _factor(median(abs(x) for x in blended)) if blended else None},
        }
    return {"group_by": list(group_by), "blend_weights": BLEND_WEIGHTS,
            "error_basis": "12-month target where recorded, else base IV",
            "groups": out_groups}

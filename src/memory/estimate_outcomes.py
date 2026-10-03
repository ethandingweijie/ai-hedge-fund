"""Loop 1 of the self-learning layer: the agent's FY+1 estimates against the prints.

What is scored. Every rated, original run that carried a guidance-derived FY+1 estimate
(run_features: the agent's bear / base / bull revenue growth, EBITDA margin and EPS, with
consensus and management's guided midpoint beside them) is compared with what the company
then reported:

  fy        the annual print for fiscal_year_1 (fetched only once the year can have been
            reported: fiscal-year end + MIN_DAYS_AFTER_FYE); growth is against the prior
            year's revenue, margin is EBITDA / revenue, EPS is the reported figure
  q_track   tracking only: the latest reported quarter inside FY+1, year on year, against
            the agent's FY+1 growth -- an early read, not a verdict

Errors are signed so the scorecard shows bias, not just size: log((1+g)/(1+g_actual)) for
growth, the difference in points for margins, log(eps/eps_actual) for EPS. A sign flip in
EPS is unscorable and counted as such.

What is learned (family "est" of the calibration versions, promoted by the owner on the
Model Accuracy page like every other calibration):

  guidance_growth_adj    per market / sector: the median of (actual - guided) revenue
                         growth, shrunk n/(n+SHRINK) toward the pooled figure, kept only
                         when it also improves the newer 30% of runs it was not fitted on
  archetype_growth_adj   per forecast archetype: the same for (actual - agent base)

A proposal moves an adjustment by at most ADJ_MOVE_CAP per cycle and needs MIN_PROPOSAL_N
runs in a scope. The engine reads the ACTIVE version through src.memory.calibration; a
proposal in shadow changes nothing. Price labels are never used here, and the agent-only
filter of run_features applies throughout (a carried user override is never scored).

Kill switch: ESTIMATE_OUTCOMES_DISABLED=true. Storage is dual-mode through src.data.db.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Optional

from src.data import db as _db
from src.memory import run_features as rf
from src.memory import valuation_outcomes as vo

logger = logging.getLogger(__name__)

FIELDS = ("revenue_growth", "ebitda_margin", "eps")
PERIOD_KINDS = ("fy", "q_track")
HORIZON_LABEL = "fy_print"            # the "horizon" an est calibration version carries

MIN_DAYS_AFTER_FYE = 45
MIN_SCORECARD_N = 5
MIN_PROPOSAL_N = 20
MIN_HOLDOUT = 5
HOLDOUT_SHARE = 0.30
HOLDOUT_MIN_GAIN = 0.005              # half a point of MAE on revenue growth
ADJ_MOVE_CAP = 0.02                   # two points per cycle
ADJ_ABS_CAP = 0.10
SHRINK = 10.0
_Q_MATCH_DAYS = 20

_DDL = """
CREATE TABLE IF NOT EXISTS estimate_outcomes (
    outcome_key       TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    run_date          TEXT NOT NULL,
    fiscal_year_1     INTEGER NOT NULL,
    field             TEXT NOT NULL,
    period_kind       TEXT NOT NULL,
    agent_bear REAL, agent_base REAL, agent_bull REAL,
    consensus         REAL,
    guidance_mid      REAL,
    actual            REAL NOT NULL,
    actual_period_end TEXT,
    actual_source     TEXT,
    err_base          REAL,
    err_consensus     REAL,
    in_band           INTEGER,
    agent_closer_than_consensus INTEGER,
    archetype TEXT, confidence TEXT, profile TEXT, market TEXT, sector TEXT,
    scored_at         TEXT NOT NULL
)
"""
_DDL_IDX = ("CREATE INDEX IF NOT EXISTS idx_estimate_outcomes_grp "
            "ON estimate_outcomes (field, period_kind, archetype)",
            "CREATE INDEX IF NOT EXISTS idx_estimate_outcomes_ticker "
            "ON estimate_outcomes (ticker, fiscal_year_1)")
_COLUMNS = ["outcome_key", "run_id", "ticker", "run_date", "fiscal_year_1", "field",
            "period_kind", "agent_bear", "agent_base", "agent_bull", "consensus", "guidance_mid",
            "actual", "actual_period_end", "actual_source", "err_base", "err_consensus",
            "in_band", "agent_closer_than_consensus", "archetype", "confidence", "profile",
            "market", "sector", "scored_at"]
_tables_ready_key: Optional[tuple] = None


def enabled() -> bool:
    return os.environ.get("ESTIMATE_OUTCOMES_DISABLED", "false").strip().lower() not in (
        "1", "true", "yes", "on")


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL)
    for ddl in _DDL_IDX:
        _db.execute(ddl)
    _tables_ready_key = key


# ── fiscal calendar ──────────────────────────────────────────────────────────

def fy_end(fiscal_year: int, fye_month: Optional[int]) -> date:
    """The last day of the fiscal year named by its end year; December when unknown."""
    m = int(fye_month) if fye_month and 1 <= int(fye_month) <= 12 else 12
    return date(int(fiscal_year), m, calendar.monthrange(int(fiscal_year), m)[1])


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ── actuals (injectable) ─────────────────────────────────────────────────────

def _li_rows(items, *, with_eps: bool) -> list[dict]:
    out = []
    for li in items or []:
        rev = rf._num(getattr(li, "revenue", None))
        end = _d(getattr(li, "report_period", None))
        if end is None or rev is None or rev <= 0:
            continue
        row = {"period_end": end.isoformat(), "revenue": rev,
               "ebitda": rf._num(getattr(li, "ebitda", None)),
               "net_income": rf._num(getattr(li, "net_income", None)),
               "free_cash_flow": rf._num(getattr(li, "free_cash_flow", None))}
        if with_eps:
            eps = rf._num(getattr(li, "earnings_per_share", None))
            if eps is None:
                ni, sh = row["net_income"], rf._num(getattr(li, "shares_outstanding", None))
                eps = ni / sh if ni is not None and sh and sh > 0 else None
            row["eps"] = eps
        out.append(row)
    return out


def _default_annuals(ticker: str, end_date: str) -> list[dict]:
    from src.tools.api import search_line_items
    items = search_line_items(ticker, ["revenue", "ebitda", "net_income", "earnings_per_share",
                                       "shares_outstanding", "free_cash_flow"], end_date,
                              period="annual", limit=3)
    return _li_rows(items, with_eps=True)


def _default_quarters(ticker: str, end_date: str) -> list[dict]:
    from src.tools.api import search_line_items
    items = search_line_items(ticker, ["revenue", "net_income"], end_date,
                              period="quarterly", limit=6)
    return _li_rows(items, with_eps=False)


# ── row maths ────────────────────────────────────────────────────────────────

def _err(field: str, pred: Optional[float], actual: Optional[float]) -> Optional[float]:
    if pred is None or actual is None:
        return None
    if field == "revenue_growth":
        if pred <= -0.99 or actual <= -0.99:
            return None
        return round(math.log((1.0 + pred) / (1.0 + actual)), 6)
    if field == "ebitda_margin":
        return round(pred - actual, 6)
    if field == "eps":
        if pred <= 0 or actual <= 0:
            return None
        return round(math.log(pred / actual), 6)
    return None


def _key(run_id: str, ticker: str, field: str, kind: str) -> str:
    return f"{run_id}|{ticker}|{field}|{kind}"


def _row(feat: dict, field: str, kind: str, actual: float, period_end: str, source: str,
         now_iso: str) -> Optional[dict]:
    short = {"revenue_growth": "rg", "ebitda_margin": "em", "eps": "eps"}[field]
    a_bear, a_base, a_bull = (rf._num(feat.get(f"agent_{short}_fy1_{s}")) for s in ("bear", "base", "bull"))
    if a_base is None:
        return None
    err = _err(field, a_base, actual)
    if err is None:
        return None                                      # unscorable (EPS sign flip or bad growth)
    cons = rf._num(feat.get({"revenue_growth": "cons_rg_fy1", "eps": "cons_eps_fy1"}.get(field, "_")))
    guid = rf._num(feat.get({"revenue_growth": "guid_rg_mid", "eps": "guid_eps_mid"}.get(field, "_")))
    err_c = _err(field, cons, actual) if cons is not None else None
    band = None
    if a_bear is not None and a_bull is not None:
        band = int(min(a_bear, a_bull) <= actual <= max(a_bear, a_bull))
    return {
        "outcome_key": _key(feat["run_id"], feat["ticker"], field, kind),
        "run_id": feat["run_id"], "ticker": feat["ticker"], "run_date": feat["run_date"],
        "fiscal_year_1": int(feat["fiscal_year_1"]), "field": field, "period_kind": kind,
        "agent_bear": a_bear, "agent_base": a_base, "agent_bull": a_bull,
        "consensus": cons, "guidance_mid": guid,
        "actual": round(float(actual), 6), "actual_period_end": period_end, "actual_source": source,
        "err_base": err, "err_consensus": err_c, "in_band": band,
        "agent_closer_than_consensus": (int(abs(err) < abs(err_c)) if err_c is not None else None),
        "archetype": feat.get("archetype"), "confidence": feat.get("confidence"),
        "profile": feat.get("profile"), "market": feat.get("market"), "sector": feat.get("sector"),
        "scored_at": now_iso,
    }


def _fy_actuals(annuals: list[dict], fy1: int) -> Optional[dict]:
    """{revenue_growth, ebitda_margin, eps, period_end} from the FY+1 and FY print rows."""
    by_year = {}
    for r in annuals:
        y = _d(r.get("period_end"))
        if y:
            by_year.setdefault(y.year, r)
    cur, prev = by_year.get(fy1), by_year.get(fy1 - 1)
    if not cur:
        return None
    out = {"period_end": cur["period_end"], "revenue_growth": None, "ebitda_margin": None,
           "eps": rf._num(cur.get("eps"))}
    if prev and prev.get("revenue") and cur.get("revenue"):
        out["revenue_growth"] = cur["revenue"] / prev["revenue"] - 1.0
    if cur.get("ebitda") is not None and cur.get("revenue"):
        out["ebitda_margin"] = cur["ebitda"] / cur["revenue"]
    return out


def _q_track(quarters: list[dict], fy0_end: date, fy1_end: date) -> Optional[dict]:
    """Year-on-year revenue growth of the latest reported quarter inside FY+1."""
    rows = [(d, r) for r in quarters for d in [_d(r.get("period_end"))] if d and r.get("revenue")]
    inside = [(d, r) for d, r in rows if fy0_end < d <= fy1_end]
    if not inside:
        return None
    d1, r1 = max(inside, key=lambda x: x[0])
    want = d1 - timedelta(days=365)
    prior = [(abs((d - want).days), r) for d, r in rows if abs((d - want).days) <= _Q_MATCH_DAYS]
    if not prior:
        return None
    r0 = min(prior, key=lambda x: x[0])[1]
    if not r0.get("revenue") or r0["revenue"] <= 0:
        return None
    return {"revenue_growth": r1["revenue"] / r0["revenue"] - 1.0, "period_end": d1.isoformat()}


# ── the sweep ────────────────────────────────────────────────────────────────

def score_matured(*, today: Optional[date] = None, annuals_fn: Optional[Callable] = None,
                  quarters_fn: Optional[Callable] = None, write: bool = True,
                  max_tickers: int = 60) -> dict:
    """Score every agent FY+1 estimate whose print has arrived. Idempotent: a scored
    (run, field, kind) is never rescored, so the first sweep is the backfill."""
    report = {k: 0 for k in ("candidates", "tickers_fetched", "fy_written", "q_written",
                             "no_print_yet", "unscorable", "fetch_errors")}
    if not enabled():
        report["disabled"] = True
        return report
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    annuals_fn = annuals_fn or _default_annuals
    quarters_fn = quarters_fn or _default_quarters
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {r["outcome_key"] for r in _db.query("SELECT outcome_key FROM estimate_outcomes")}
    feats = rf.rows(where="fiscal_year_1 IS NOT NULL AND agent_rg_fy1_base IS NOT NULL")
    report["candidates"] = len(feats)

    by_ticker: dict[str, list[dict]] = {}
    for f in feats:
        by_ticker.setdefault(f["ticker"], []).append(f)

    out: list[dict] = []
    fetched = 0
    for ticker, items in sorted(by_ticker.items()):
        need_fy, need_q = [], []
        for f in items:
            fy1 = int(f["fiscal_year_1"])
            end1 = fy_end(fy1, f.get("fye_month"))
            if any(_key(f["run_id"], ticker, fld, "fy") not in existing for fld in FIELDS):
                if today >= end1 + timedelta(days=MIN_DAYS_AFTER_FYE):
                    need_fy.append(f)
                else:
                    report["no_print_yet"] += 1
            if _key(f["run_id"], ticker, "revenue_growth", "q_track") not in existing \
                    and today > end1 - timedelta(days=365) + timedelta(days=MIN_DAYS_AFTER_FYE):
                need_q.append(f)
        if not need_fy and not need_q:
            continue
        if fetched >= max_tickers:
            break
        fetched += 1
        end_date = today.isoformat()
        annuals = quarters = None
        try:
            annuals = annuals_fn(ticker, end_date) if need_fy else []
        except Exception as exc:                           # noqa: BLE001
            report["fetch_errors"] += 1
            logger.warning("estimate_outcomes: annuals for %s failed: %s", ticker, exc)
        try:
            quarters = quarters_fn(ticker, end_date) if need_q else []
        except Exception as exc:                           # noqa: BLE001
            report["fetch_errors"] += 1
            logger.warning("estimate_outcomes: quarters for %s failed: %s", ticker, exc)

        for f in need_fy:
            act = _fy_actuals(annuals or [], int(f["fiscal_year_1"]))
            if not act:
                report["no_print_yet"] += 1
                continue
            source = "statements" + ("" if f.get("fye_month") else ";fye_assumed_dec")
            for fld in FIELDS:
                if _key(f["run_id"], ticker, fld, "fy") in existing or act.get(fld) is None:
                    continue
                row = _row(f, fld, "fy", act[fld], act["period_end"], source, now_iso)
                if row is None:
                    report["unscorable"] += 1
                    continue
                out.append(row)
                report["fy_written"] += 1
        for f in need_q:
            fy1 = int(f["fiscal_year_1"])
            qt = _q_track(quarters or [], fy_end(fy1 - 1, f.get("fye_month")), fy_end(fy1, f.get("fye_month")))
            if not qt:
                continue
            row = _row(f, "revenue_growth", "q_track", qt["revenue_growth"], qt["period_end"],
                       "quarterly_yoy;tracking_only", now_iso)
            if row is None:
                report["unscorable"] += 1
                continue
            out.append(row)
            report["q_written"] += 1

    report["tickers_fetched"] = fetched
    report["rows"] = len(out)
    report["written"] = 0
    if write and out:
        _db.executemany(vo._upsert_sql("estimate_outcomes", "outcome_key", _COLUMNS),
                        [[r[c] for c in _COLUMNS] for r in out])
        report["written"] = len(out)
    return report


# ── reading ──────────────────────────────────────────────────────────────────

def _rows(where: str = "", params: Optional[list] = None) -> list[dict]:
    _ensure_tables()
    sql = "SELECT * FROM estimate_outcomes"
    if where:
        sql += " WHERE " + where
    return [dict(r) for r in _db.query(sql + " ORDER BY run_date ASC, outcome_key ASC",
                                       list(params or []))]


def _stats(errs: list[float]) -> dict:
    return {"n": len(errs), "bias": round(median(errs), 4) if errs else None,
            "mae": round(sum(abs(e) for e in errs) / len(errs), 4) if errs else None}


GROUPS = ("archetype", "confidence", "profile", "market", "sector")


def scorecard(group_by: str = "archetype", *, period_kind: str = "fy") -> dict:
    """Per group and field: n, bias (median signed error), MAE, the share of runs where the
    agent was closer than consensus, and the share inside the bear-bull band. Groups under
    MIN_SCORECARD_N are reported as insufficient rather than as numbers."""
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {GROUPS}")
    rows = _rows("period_kind = ?", [period_kind])
    table: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        table.setdefault(str(r.get(group_by) or "unknown"), {}).setdefault(r["field"], []).append(r)
    out = {}
    for g, by_field in sorted(table.items()):
        out[g] = {}
        for field, rs in by_field.items():
            errs = [float(r["err_base"]) for r in rs if r["err_base"] is not None]
            cell = _stats(errs)
            if len(errs) < MIN_SCORECARD_N:
                cell.update(status="insufficient", need=MIN_SCORECARD_N, bias=None, mae=None)
            else:
                cell["status"] = "ok"
            vs = [r["agent_closer_than_consensus"] for r in rs if r["agent_closer_than_consensus"] is not None]
            cell["beat_consensus_share"] = round(sum(vs) / len(vs), 3) if vs and len(errs) >= MIN_SCORECARD_N else None
            bands = [r["in_band"] for r in rs if r["in_band"] is not None]
            cell["in_band_share"] = round(sum(bands) / len(bands), 3) if bands and len(errs) >= MIN_SCORECARD_N else None
            out[g][field] = cell
    return {"group_by": group_by, "period_kind": period_kind, "n_rows": len(rows), "groups": out}


def guidance_credibility(ticker: Optional[str] = None) -> dict:
    """How management's guidance has held up: per ticker and per market, the share of
    guided figures met or beaten, the share within band, and n. Reads this ledger's FY rows
    that carried a guided midpoint, in a union with the steward's assumption_scorecard
    (quarterly guidance vs prints)."""
    _ensure_tables()
    recs: list[tuple[str, str, float, float]] = []   # ticker, market, guided, actual
    for r in _rows("period_kind = 'fy' AND guidance_mid IS NOT NULL"
                   + (" AND ticker = ?" if ticker else ""), [ticker.upper()] if ticker else []):
        recs.append((r["ticker"], r["market"], float(r["guidance_mid"]), float(r["actual"]),))
    try:
        from src.memory import assumption_store
        assumption_store.ensure_assumption_tables()
        sql = "SELECT ticker, predicted, actual FROM assumption_scorecard WHERE source = 'earnings'"
        params: list = []
        if ticker:
            sql += " AND ticker = ?"
            params.append(ticker.upper())
        for r in _db.query(sql, params):
            p, a = rf._num(r["predicted"]), rf._num(r["actual"])
            if p is None or a is None:
                continue
            recs.append((r["ticker"], vo.market_of(r["ticker"]), p, a))
    except Exception as exc:                               # noqa: BLE001
        logger.debug("guidance_credibility: assumption_scorecard unavailable: %s", exc)

    def summarise(items):
        n = len(items)
        if n == 0:
            return {"n": 0, "status": "insufficient"}
        beat = sum(1 for _, _, g, a in items if a >= g)
        hit = sum(1 for _, _, g, a in items
                  if (abs(a - g) <= 0.02 if abs(g) < 1.0 else abs(a - g) <= 0.10 * abs(g)))
        return {"n": n, "beat_rate": round(beat / n, 3), "hit_rate": round(hit / n, 3),
                "status": "ok" if n >= MIN_SCORECARD_N else "insufficient"}

    by_t: dict[str, list] = {}
    by_m: dict[str, list] = {}
    for rec in recs:
        by_t.setdefault(rec[0], []).append(rec)
        by_m.setdefault(rec[1] or "?", []).append(rec)
    tickers = {t: summarise(v) for t, v in sorted(by_t.items())}
    ranked = sorted((t for t, s in tickers.items() if s["status"] == "ok"),
                    key=lambda t: tickers[t]["beat_rate"])
    return {"n": len(recs), "tickers": tickers,
            "markets": {m: summarise(v) for m, v in sorted(by_m.items())},
            "worst": ranked[:5], "best": list(reversed(ranked[-5:]))}


# ── proposals (family "est") ─────────────────────────────────────────────────

def _shrunk_scopes(samples: list[tuple[str, float]], *, current: dict) -> tuple[dict, dict]:
    """{scope: adj} and the evidence per scope. Pooled median shrunk toward zero, each scope's
    median shrunk toward the pooled figure, the move from the current adjustment capped."""
    pooled = [v for _, v in samples]
    if not pooled:
        return {}, {}
    n_p = len(pooled)
    pooled_med = median(pooled)
    pooled_adj = pooled_med * n_p / (n_p + SHRINK)
    by_scope: dict[str, list[float]] = {}
    for scope, v in samples:
        by_scope.setdefault(scope, []).append(v)
    out, evidence = {}, {"pooled": {"n": n_p, "median": round(pooled_med, 4), "shrunk": round(pooled_adj, 4)}}
    for scope, vs in sorted(by_scope.items()):
        n = len(vs)
        med = median(vs)
        target = pooled_adj + (med - pooled_adj) * n / (n + SHRINK)
        cur = float(current.get(scope) or 0.0)
        step = max(-ADJ_MOVE_CAP, min(ADJ_MOVE_CAP, target - cur))
        adj = max(-ADJ_ABS_CAP, min(ADJ_ABS_CAP, cur + step))
        evidence[scope] = {"n": n, "median": round(med, 4), "target": round(target, 4),
                           "current": cur, "proposed": round(adj, 4),
                           "status": "ok" if n >= MIN_PROPOSAL_N else "insufficient_data"}
        if n >= MIN_PROPOSAL_N and abs(adj - cur) >= 0.0005:
            out[scope] = round(adj, 4)
    return out, evidence


def _holdout(rows: list[dict], scope_of: Callable, base_of: Callable, *, current: dict) -> dict:
    """Fit on the older 70%, test on the newer 30%: MAE of (base + adj) vs base. Returns
    the scopes that improved and the per-scope gain."""
    rows = [r for r in rows if base_of(r) is not None]
    if len(rows) < MIN_PROPOSAL_N + MIN_HOLDOUT:
        return {"status": "insufficient_data", "n": len(rows), "kept": {}}
    cut = max(MIN_HOLDOUT, int(round(len(rows) * HOLDOUT_SHARE)))
    train, test = rows[:-cut], rows[-cut:]
    fit, _ = _shrunk_scopes([(scope_of(r), float(r["actual"]) - base_of(r)) for r in train],
                            current=current)
    kept, gains = {}, {}
    for scope, adj in fit.items():
        t = [r for r in test if scope_of(r) == scope]
        if not t:
            continue
        live = sum(abs(base_of(r) - float(r["actual"])) for r in t) / len(t)
        cand = sum(abs(base_of(r) + adj - float(r["actual"])) for r in t) / len(t)
        gains[scope] = {"n_test": len(t), "live_mae": round(live, 4), "cand_mae": round(cand, 4)}
        if live - cand >= HOLDOUT_MIN_GAIN:
            kept[scope] = adj
    return {"status": "ok", "n_train": len(train), "n_test": len(test), "kept": kept, "gains": gains}


def _scopes_of(r: dict) -> list[str]:
    out = []
    if r.get("sector"):
        out.append(f"sector:{r['sector']}")
    if r.get("market"):
        out.append(f"market:{r['market']}")
    return out


def propose_est(*, today: Optional[date] = None, write: bool = True) -> dict:
    """Fit the est-family proposal from the FY revenue-growth outcomes and store it in
    shadow. Nothing is active until the owner promotes it."""
    from src.memory import calibration as cal
    from src.memory import calibration_fit as cf
    _ensure_tables()
    cf._ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    rows = _rows("period_kind = 'fy' AND field = 'revenue_growth'")
    active = cal.active_version("est")
    cur_g = dict((active or {}).get("params", {}).get("guidance_growth_adj") or {})
    cur_a = dict((active or {}).get("params", {}).get("archetype_growth_adj") or {})

    # guidance: (actual - guided), one sample per (row, scope)
    g_rows = [r for r in rows if r.get("guidance_mid") is not None]
    g_samples = [(s, float(r["actual"]) - float(r["guidance_mid"])) for r in g_rows for s in _scopes_of(r)]
    g_fit, g_evidence = _shrunk_scopes(g_samples, current=cur_g)
    g_hold = {}
    for scope in list(g_fit):
        h = _holdout([r for r in g_rows if scope in _scopes_of(r)], lambda r, s=scope: s,
                     lambda r: float(r["guidance_mid"]), current=cur_g)
        g_hold[scope] = h
        if scope not in h.get("kept", {}):
            g_fit.pop(scope)

    # archetype: (actual - agent base)
    a_rows = [r for r in rows if r.get("archetype") and r.get("agent_base") is not None]
    a_samples = [(f"archetype:{r['archetype']}", float(r["actual"]) - float(r["agent_base"])) for r in a_rows]
    a_fit, a_evidence = _shrunk_scopes(a_samples, current={f"archetype:{k}": v for k, v in cur_a.items()})
    a_hold = {}
    for scope in list(a_fit):
        code = scope.split(":", 1)[1]
        h = _holdout([r for r in a_rows if r["archetype"] == code], lambda r, s=scope: s,
                     lambda r: float(r["agent_base"]), current={scope: cur_a.get(code, 0.0)})
        a_hold[scope] = h
        if scope not in h.get("kept", {}):
            a_fit.pop(scope)

    params = {"guidance_growth_adj": g_fit,
              "archetype_growth_adj": {s.split(":", 1)[1]: v for s, v in a_fit.items()}}
    fit = {"horizon": HORIZON_LABEL, "n_rows": len(rows), "guidance": g_evidence,
           "archetype": a_evidence, "holdout": {"guidance": g_hold, "archetype": a_hold}}
    if not params["guidance_growth_adj"] and not params["archetype_growth_adj"]:
        status = "insufficient_data" if len(rows) < MIN_PROPOSAL_N else "no_change"
        return {"status": status, "n_rows": len(rows), "min_rows": MIN_PROPOSAL_N, **fit}
    digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    version_id = f"calest-{today.isoformat()}-{digest}"
    backtest = {"verdict": {"passed": True, "reasons": []}, "method": "time holdout on reported prints"}
    if write:
        cf.record_version(version_id=version_id, horizon=HORIZON_LABEL, status="shadow",
                          params=params, fit=fit, backtest=backtest, family="est", today=today)
    return {"status": "shadow", "version_id": version_id, "params": params, "written": write, **fit}


def shadow_report(version_id: str, *, today: Optional[date] = None) -> dict:
    """An est proposal is eligible once MIN_PROPOSAL_N new prints per touched scope have
    been scored since it was proposed, after SHADOW_MIN_DAYS, and the adjusted estimate
    beats the unadjusted one on them."""
    from src.memory import calibration_fit as cf
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    row = _db.query_one("SELECT version_id, created_at, status, params_json FROM "
                        "calibration_versions WHERE version_id = ?", [version_id])
    if row is None:
        raise KeyError(version_id)
    params = vo._loads(row["params_json"]) or {}
    created = str(row["created_at"])[:10]
    days = (today - date.fromisoformat(created)).days
    reasons: list[str] = []
    if days < cf.SHADOW_MIN_DAYS:
        reasons.append(f"{days} day(s) in shadow; need {cf.SHADOW_MIN_DAYS}")
    fresh = _rows("period_kind = 'fy' AND field = 'revenue_growth' AND substr(scored_at, 1, 10) > ?",
                  [created])
    by_key: dict[str, dict] = {}
    live_all, cand_all = [], []
    for scope, adj in (params.get("guidance_growth_adj") or {}).items():
        t = [r for r in fresh if r.get("guidance_mid") is not None and scope in _scopes_of(r)]
        live = [abs(float(r["guidance_mid"]) - float(r["actual"])) for r in t]
        cand = [abs(float(r["guidance_mid"]) + float(adj) - float(r["actual"])) for r in t]
        by_key[scope] = {"n": len(t), "live_mae": round(sum(live) / len(live), 4) if live else None,
                         "cand_mae": round(sum(cand) / len(cand), 4) if cand else None}
        live_all += live; cand_all += cand
        if len(t) < MIN_PROPOSAL_N:
            reasons.append(f"{scope}: {len(t)} print(s) scored in shadow; need {MIN_PROPOSAL_N}")
    for code, adj in (params.get("archetype_growth_adj") or {}).items():
        t = [r for r in fresh if r.get("archetype") == code and r.get("agent_base") is not None]
        live = [abs(float(r["agent_base"]) - float(r["actual"])) for r in t]
        cand = [abs(float(r["agent_base"]) + float(adj) - float(r["actual"])) for r in t]
        by_key[f"archetype:{code}"] = {"n": len(t), "live_mae": round(sum(live) / len(live), 4) if live else None,
                                       "cand_mae": round(sum(cand) / len(cand), 4) if cand else None}
        live_all += live; cand_all += cand
        if len(t) < MIN_PROPOSAL_N:
            reasons.append(f"archetype:{code}: {len(t)} print(s) scored in shadow; need {MIN_PROPOSAL_N}")
    if live_all and sum(cand_all) >= sum(live_all):
        reasons.append("candidate not below live on the prints scored in shadow")
    eligible = not reasons and row["status"] == "shadow"
    if row["status"] != "shadow":
        reasons.append(f"status is {row['status']!r}, not 'shadow'")
    return {"version_id": version_id, "status": row["status"], "created_at": created,
            "days_in_shadow": days,
            "horizons": {HORIZON_LABEL: {"n_touched": len(live_all), "by_key": by_key}},
            "eligible_for_promotion": eligible, "reasons": reasons}


def counts() -> dict:
    _ensure_tables()
    rows = _db.query("SELECT period_kind, field, COUNT(*) AS n FROM estimate_outcomes "
                     "GROUP BY period_kind, field")
    return {f"{r['period_kind']}:{r['field']}": int(r["n"]) for r in rows}

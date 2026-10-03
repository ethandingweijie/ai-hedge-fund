"""Loop 6 of the self-learning layer: did a gate's firing help, once the year printed?

Every gate the engine fires writes a record with the figure it refused (path A, the raw
input) and the figure it used instead (path B, the gated output). When the fiscal year the
run forecast has been reported, this ledger scores each record with gate_backtest's verdict:

  HELPED        the gated figure was closer to what printed, by more than the metric's epsilon
  FALSE_ALARM   the raw figure was closer: the gate moved the projection away from reality
  NEUTRAL       inside the epsilon either way
  UNSCORABLE    no reported counterpart for the metric, or a record without a path B
                (the OE cascade that disabled the DCF family), counted but not judged

Only two metric families have a reported counterpart today: FCF-margin gates (scored in
dollars, path x revenue of the printed year, so the relative epsilon on free cash flow
holds) and revenue-growth gates (scored as revenues off the prior year). Every other
gate is reported as coverage, which is itself the finding: a gate with no scorable claim
is a gate nobody can learn from.

Nothing moves on its own. When a gate's false-alarm share exceeds REVIEW_SHARE over
REVIEW_MIN_N scored firings, a "gate_threshold_review" calibration version (family est)
is written for the owner: accepting it records the review; the threshold is a code change.

Kill switch: GATE_OUTCOMES_DISABLED=true. Storage is dual-mode through src.data.db.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from src.data import db as _db
from src.memory import estimate_outcomes as eo
from src.memory import run_features as rf
from src.memory import valuation_outcomes as vo
from src.memory.gate_backtest import delta_error_verdict

logger = logging.getLogger(__name__)

REVIEW_SHARE = 0.5
REVIEW_MIN_N = 20
MIN_REPORT_N = 20

#: metric on the gate record -> (how to score it, the epsilon metric for the verdict)
#:   margin   path x revenue_fy1 vs free_cash_flow_fy1
#:   growth   revenue_fy0 x (1 + path) vs revenue_fy1
SCORABLE: dict[str, tuple[str, str]] = {
    "fcf_margin_base": ("margin", "free_cash_flow"),
    "fcf_margin": ("margin", "free_cash_flow"),
    "revenue_growth": ("growth", "revenue"),
}

_DDL = """
CREATE TABLE IF NOT EXISTS gate_outcomes (
    outcome_key       TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    run_date          TEXT NOT NULL,
    fiscal_year_1     INTEGER NOT NULL,
    gate_id           TEXT NOT NULL,
    metric            TEXT,
    applied           INTEGER,
    path_a            REAL,
    path_b            REAL,
    actual            REAL,
    actual_metric     TEXT,
    actual_period_end TEXT,
    epsilon           REAL,
    delta_error_pct   REAL,
    verdict           TEXT NOT NULL,
    profile TEXT, market TEXT,
    scored_at         TEXT NOT NULL
)
"""
_DDL_IDX = ("CREATE INDEX IF NOT EXISTS idx_gate_outcomes_gate ON gate_outcomes (gate_id, verdict)",)
_COLUMNS = ["outcome_key", "run_id", "ticker", "run_date", "fiscal_year_1", "gate_id", "metric",
            "applied", "path_a", "path_b", "actual", "actual_metric", "actual_period_end",
            "epsilon", "delta_error_pct", "verdict", "profile", "market", "scored_at"]
_tables_ready_key: Optional[tuple] = None


def enabled() -> bool:
    return os.environ.get("GATE_OUTCOMES_DISABLED", "false").strip().lower() not in (
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


def _fy_rows(annuals: list[dict], fy1: int) -> tuple[Optional[dict], Optional[dict]]:
    by_year: dict[int, dict] = {}
    for r in annuals or []:
        d = eo._d(r.get("period_end"))
        if d:
            by_year.setdefault(d.year, r)
    return by_year.get(fy1), by_year.get(fy1 - 1)


def score_record(rec: dict, cur: Optional[dict], prev: Optional[dict]) -> dict:
    """The verdict for one gate record against the printed year. Pure."""
    metric = str(rec.get("metric") or "")
    a, b = rf._num(rec.get("a")), rf._num(rec.get("b"))
    how = SCORABLE.get(metric)
    out = {"metric": metric or None, "path_a": a, "path_b": b, "actual": None, "actual_metric": None,
           "actual_period_end": (cur or {}).get("period_end"), "epsilon": None,
           "delta_error_pct": None, "verdict": "UNSCORABLE"}
    if how is None or cur is None or a is None or b is None:
        return out
    kind, eps_metric = how
    rev1 = rf._num(cur.get("revenue"))
    if kind == "margin":
        fcf1 = rf._num(cur.get("free_cash_flow"))
        if not rev1 or fcf1 is None:
            return out
        v = delta_error_verdict(a * rev1, b * rev1, fcf1, eps_metric)
        out.update(actual=round(fcf1 / rev1, 6), actual_metric="fcf_margin_reported")
    else:
        rev0 = rf._num((prev or {}).get("revenue"))
        if not rev1 or not rev0:
            return out
        v = delta_error_verdict(rev0 * (1.0 + a), rev0 * (1.0 + b), rev1, eps_metric)
        out.update(actual=round(rev1 / rev0 - 1.0, 6), actual_metric="revenue_growth_reported")
    out.update(epsilon=v["epsilon"], verdict=v["verdict"],
               delta_error_pct=(round(v["delta_error_pct"], 6) if v["delta_error_pct"] is not None else None))
    return out


def score_matured(*, today: Optional[date] = None, annuals_fn: Optional[Callable] = None,
                  write: bool = True, max_tickers: int = 60) -> dict:
    """Score every gate record of every agent-only run whose forecast year has printed.
    Idempotent per (run, ticker, gate)."""
    report = {k: 0 for k in ("candidates", "tickers_fetched", "written", "no_print_yet",
                             "fetch_errors")}
    if not enabled():
        report["disabled"] = True
        return report
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    annuals_fn = annuals_fn or eo._default_annuals
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {r["outcome_key"] for r in _db.query("SELECT outcome_key FROM gate_outcomes")}
    feats = rf.rows(where="fiscal_year_1 IS NOT NULL AND gates_json IS NOT NULL AND gates_json != '[]'")
    report["candidates"] = len(feats)
    by_ticker: dict[str, list[dict]] = {}
    for f in feats:
        by_ticker.setdefault(f["ticker"], []).append(f)

    out: list[dict] = []
    verdicts: dict[str, int] = {}
    fetched = 0
    for ticker, items in sorted(by_ticker.items()):
        pending = []
        for f in items:
            gates = vo._loads(f.get("gates_json")) or []
            todo = [g for g in gates if isinstance(g, dict) and g.get("gate_id")
                    and f"{f['run_id']}|{ticker}|{g['gate_id']}" not in existing]
            if not todo:
                continue
            if today < eo.fy_end(int(f["fiscal_year_1"]), f.get("fye_month")) + timedelta(days=eo.MIN_DAYS_AFTER_FYE):
                report["no_print_yet"] += 1
                continue
            pending.append((f, todo))
        if not pending:
            continue
        if fetched >= max_tickers:
            break
        fetched += 1
        try:
            annuals = annuals_fn(ticker, today.isoformat())
        except Exception as exc:                           # noqa: BLE001
            report["fetch_errors"] += 1
            logger.warning("gate_outcomes: annuals for %s failed: %s", ticker, exc)
            continue
        for f, todo in pending:
            cur, prev = _fy_rows(annuals, int(f["fiscal_year_1"]))
            if cur is None:
                report["no_print_yet"] += 1
                continue
            seen_gate = set()
            for g in todo:
                gid = str(g["gate_id"])
                if gid in seen_gate:
                    continue
                seen_gate.add(gid)
                sc = score_record(g, cur, prev)
                verdicts[sc["verdict"]] = verdicts.get(sc["verdict"], 0) + 1
                out.append({
                    "outcome_key": f"{f['run_id']}|{ticker}|{gid}", "run_id": f["run_id"],
                    "ticker": ticker, "run_date": f["run_date"], "fiscal_year_1": int(f["fiscal_year_1"]),
                    "gate_id": gid, "applied": (None if g.get("applied") is None else int(bool(g.get("applied")))),
                    "profile": f.get("profile"), "market": f.get("market"), "scored_at": now_iso, **sc,
                })
    report["tickers_fetched"] = fetched
    report["rows"] = len(out)
    report["verdicts"] = verdicts
    if write and out:
        _db.executemany(vo._upsert_sql("gate_outcomes", "outcome_key", _COLUMNS),
                        [[r[c] for c in _COLUMNS] for r in out])
        report["written"] = len(out)
    return report


def report() -> dict:
    """Per gate: scored firings by verdict, the false-alarm share among the judged ones,
    and insufficient under MIN_REPORT_N judged firings."""
    _ensure_tables()
    rows = _db.query("SELECT gate_id, verdict, COUNT(*) AS n FROM gate_outcomes GROUP BY gate_id, verdict")
    gates: dict[str, dict] = {}
    for r in rows:
        g = gates.setdefault(r["gate_id"], {"HELPED": 0, "FALSE_ALARM": 0, "NEUTRAL": 0, "UNSCORABLE": 0})
        g[r["verdict"]] = int(r["n"])
    for gid, g in gates.items():
        judged = g["HELPED"] + g["FALSE_ALARM"] + g["NEUTRAL"]
        g["n_judged"] = judged
        g["false_alarm_share"] = round(g["FALSE_ALARM"] / judged, 3) if judged else None
        g["helped_share"] = round(g["HELPED"] / judged, 3) if judged else None
        g["status"] = "ok" if judged >= MIN_REPORT_N else "insufficient"
        g["scorable"] = judged > 0 or g["UNSCORABLE"] == 0
    return {"gates": dict(sorted(gates.items())), "min_n": MIN_REPORT_N}


def propose(*, today: Optional[date] = None, write: bool = True) -> dict:
    """A gate_threshold_review version (family est) for every gate whose false-alarm share
    exceeds REVIEW_SHARE over at least REVIEW_MIN_N judged firings. A code-change prompt."""
    from src.memory import calibration_fit as cf
    rep = report()
    flagged = {gid: {"n": g["n_judged"], "false_alarm_share": g["false_alarm_share"],
                     "helped_share": g["helped_share"]}
               for gid, g in rep["gates"].items()
               if g["n_judged"] >= REVIEW_MIN_N and (g["false_alarm_share"] or 0) > REVIEW_SHARE}
    if not flagged:
        return {"status": "no_change", "gates": rep["gates"]}
    params = {"gate_threshold_review": flagged}
    today = today or datetime.now(timezone.utc).date()
    digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    version_id = f"calgate-{today.isoformat()}-{digest}"
    if write:
        cf.record_version(version_id=version_id, horizon=eo.HORIZON_LABEL, status="shadow",
                          params=params, fit={"gates": rep["gates"]},
                          backtest={"verdict": {"passed": True, "reasons": []},
                                    "method": "false-alarm share over scored firings"},
                          family="est", today=today)
    return {"status": "shadow", "version_id": version_id, "params": params, "written": write}


def counts() -> dict:
    _ensure_tables()
    rows = _db.query("SELECT verdict, COUNT(*) AS n FROM gate_outcomes GROUP BY verdict")
    return {r["verdict"]: int(r["n"]) for r in rows}

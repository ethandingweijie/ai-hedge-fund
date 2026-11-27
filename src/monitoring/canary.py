"""Nightly valuation canary (roadmap #4, part 2, 2026-11-28).

Two questions, neither needing an LLM call:

1. **Does the DEPLOYED engine reproduce the pinned baseline?** (`golden_canary`)
   The 14 golden fixtures (tests/fixtures/golden) are replayed offline, one fresh interpreter each, on the code,
   dependencies and data files actually running in the container, and compared with tests/golden/snapshots.json.
   A commit that means to move a valuation must move the snapshot with a GOLDEN_UPDATE_REASON, so a deployed
   replay that disagrees is, by construction, a move no commit explains: dependency drift (the 2026-08-15 wheel
   incident), a module or data file missing from the image (the 2026-08-16 SOTP partial deploy), an env var that
   differs from CI.

2. **Did a live run's IV jump with nothing to explain it?** (`live_drift`)
   Every completed run writes a run_features row (iv_base, engine_version = pipeline|dcf|commit, the constants and
   calibration versions, profile, agent estimates). For each run in the window, the previous run of the same
   ticker is the reference; a base-IV move beyond DRIFT_THRESHOLD is EXPLAINED when the deployed commit, an owner
   constants / calibration version, the profile or routing, the agent's FY+1 estimates, or the market's comps
   refresh changed between the two. Otherwise it is listed as unexplained.

Results are stored in `canary_runs`; the daily health report reads the latest row as its `canary` check, so the
owner gets one morning message.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Optional

from src.monitoring.prod_health import (FAIL, OK, UNKNOWN, WARN, _check, _now, _parse_ts, _row_get, _safe)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
#: per-fixture child timeout; the suite's is 420s.
CHILD_TIMEOUT_S = 420
#: a live base-IV move beyond this, with no explanation, is listed.
DRIFT_THRESHOLD = float(os.environ.get("CANARY_DRIFT_THRESHOLD", "0.15") or 0.15)
#: agent FY+1 revenue growth / EBIT margin change (absolute) that counts as "the estimates moved".
_EST_MOVE_ABS = 0.02
#: secrets never reach a replay child (the replay is offline by construction; this is belt and braces).
_CHILD_STRIP = ("DATABASE_URL", "REDIS_URL", "JWT_SECRET_KEY", "DB_UPLOAD_SECRET", "SLACK_WEBHOOK_URL",
                "PROD_HEALTH_SLACK_WEBHOOK_URL", "SMTP_PASS")


# ── 1. golden replay on the deployed code ──────────────────────────────────

def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in _CHILD_STRIP and not k.endswith("_API_KEY") and not k.endswith("_TOKEN")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def replay_one(name: str, out_dir: Path) -> dict:
    out = out_dir / f"{name}.json"
    try:
        proc = subprocess.run([sys.executable, "-m", "src.memory.golden_replay", name, str(out)],
                              cwd=str(ROOT), env=_child_env(), capture_output=True, text=True,
                              timeout=CHILD_TIMEOUT_S, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"fixture": name, "ok": False, "error": f"replay timed out after {CHILD_TIMEOUT_S}s"}
    if not out.exists():
        return {"fixture": name, "ok": False, "error": f"no output (exit {proc.returncode}): {proc.stderr[-400:]}"}
    return json.loads(out.read_text(encoding="utf-8"))


def compare(name: str, result: dict, snapshot: dict) -> dict:
    """One fixture's verdict: ok | drift | error, with the base-IV move and the first differing fields."""
    from src.memory import golden_snapshots as gs
    pinned = (snapshot.get(name) or {}).get("projection") or {}
    if not result.get("ok"):
        return {"fixture": name, "verdict": "error",
                "detail": str(result.get("error") or result.get("skip_reason") or "replay failed")[:300]}
    diag = result.get("diagnostics") or {}
    rows = gs.diff_fields(pinned, result.get("projection") or {})
    exp_iv = (snapshot.get(name) or {}).get("base_iv", pinned.get("scenarios.base.intrinsic_value"))
    act_iv = result.get("base_iv")
    move = (act_iv / exp_iv - 1.0) if isinstance(exp_iv, (int, float)) and exp_iv and \
        isinstance(act_iv, (int, float)) else None
    out = {"fixture": name, "ticker": result.get("ticker"), "pinned_iv": exp_iv, "replay_iv": act_iv,
           "iv_move": None if move is None else round(move, 4),
           "misses": len(diag.get("misses") or []), "fields_off": len(rows)}
    if rows or out["misses"]:
        out["verdict"] = "drift"
        out["detail"] = "; ".join(f"{r['path']} {r['expected']}->{r['actual']}" for r in rows[:4]) or \
            f"{out['misses']} unrecorded call(s): the engine asked for an input the fixture lacks"
    else:
        out["verdict"] = "ok"
    return out


def golden_canary(fixtures: Optional[list[str]] = None, replay=replay_one) -> dict:
    from src.memory import golden_capture as gc
    from src.memory import golden_snapshots as gs
    names = fixtures or gc.available_tickers()
    if not names:
        return {"status": UNKNOWN, "summary": "no golden fixtures in the image", "results": []}
    snapshot = gs.load()
    with tempfile.TemporaryDirectory(prefix="canary_") as td:
        results = [compare(n, replay(n, Path(td)), snapshot) for n in names]
    bad = [r for r in results if r["verdict"] != "ok"]
    return {"status": FAIL if bad else OK,
            "summary": (f"{len(bad)} of {len(results)} reference valuation(s) moved off the pinned baseline on the "
                        f"deployed code -- no commit explains it") if bad else
                       f"all {len(results)} reference valuations reproduce the pinned baseline",
            "snapshot_generated_at": (snapshot.get("_meta") or {}).get("generated_at"),
            "results": results}


# ── 2. unexplained IV moves between live runs ──────────────────────────────

_RF_COLS = ("run_id", "ticker", "run_at", "market", "profile", "routing_winner", "param_version",
            "calibration_version", "pt_calibration_version", "iv_base", "engine_version",
            "agent_rg_fy1_base", "agent_em_fy1_base")


def _commit_of(engine_version: Optional[str]) -> Optional[str]:
    parts = [p.strip() for p in str(engine_version or "").split("|")]
    tail = parts[-1] if parts else ""
    return tail if tail and not tail.startswith(("pipeline", "dcf")) else None


def explain(prev: dict, cur: dict, comps_refreshed_at=None) -> list[str]:
    """Why the IV moved between two runs of one ticker; [] when nothing on record explains it."""
    why = []
    pc, cc = _commit_of(prev.get("engine_version")), _commit_of(cur.get("engine_version"))
    if pc != cc:
        why.append(f"deploy {pc or '?'} -> {cc or '?'}")
    elif prev.get("engine_version") != cur.get("engine_version"):
        why.append(f"engine {prev.get('engine_version')} -> {cur.get('engine_version')}")
    for col, label in (("param_version", "owner constants"), ("calibration_version", "calibration"),
                       ("pt_calibration_version", "PT calibration"), ("profile", "profile"),
                       ("routing_winner", "routing")):
        if (prev.get(col) or None) != (cur.get(col) or None):
            why.append(f"{label} {prev.get(col)} -> {cur.get(col)}")
    for col, label in (("agent_rg_fy1_base", "agent FY+1 growth"), ("agent_em_fy1_base", "agent FY+1 margin")):
        a, b = prev.get(col), cur.get(col)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(b - a) >= _EST_MOVE_ABS:
            why.append(f"{label} {a:.1%} -> {b:.1%}")
        elif (a is None) != (b is None):
            why.append(f"{label} {'added' if a is None else 'dropped'}")
    pa, ca = _parse_ts(prev.get("run_at")), _parse_ts(cur.get("run_at"))
    ct = _parse_ts(comps_refreshed_at)
    if ct and pa and ca and pa < ct <= ca:
        why.append(f"comps refreshed {ct.isoformat()[:10]}")
    return why


def _comps_refreshed(market: Optional[str]):
    """Latest comps write for the run's market (the store keeps the newest refresh only)."""
    from src.data import db
    from src.data.regional_comps import CROSS_MARKET_EXCHANGE
    ex = {"US": "US", "HK": "HKSE", "SG": "SES"}.get(str(market or "").upper(), str(market or "").upper())
    row = db.query_one("SELECT MAX(computed_at) AS newest FROM regional_comps_members WHERE exchange IN (?, ?)",
                       [ex, CROSS_MARKET_EXCHANGE])
    return _row_get(row, "newest", 0) if row else None


def live_drift(hours: float = 24.0, threshold: float = DRIFT_THRESHOLD, comps_at=_comps_refreshed) -> dict:
    from src.data import db
    lo = (_now() - timedelta(hours=hours)).replace(tzinfo=None).isoformat()
    cols = ", ".join(_RF_COLS)
    cur_rows = db.query(f"SELECT {cols} FROM run_features WHERE run_at >= ? AND cache_copy = 0 "
                        f"AND iv_base IS NOT NULL ORDER BY run_at DESC LIMIT 200", [lo]) or []
    checked, moves, unexplained, explained, unattributable = 0, [], [], [], []
    for r in cur_rows:
        cur = {c: _row_get(r, c, i) for i, c in enumerate(_RF_COLS)}
        prow = db.query_one(f"SELECT {cols} FROM run_features WHERE ticker = ? AND run_at < ? AND cache_copy = 0 "
                            f"AND iv_base IS NOT NULL ORDER BY run_at DESC LIMIT 1", [cur["ticker"], cur["run_at"]])
        if not prow:
            continue
        prev = {c: _row_get(prow, c, i) for i, c in enumerate(_RF_COLS)}
        if not prev.get("iv_base"):
            continue
        checked += 1
        mv = cur["iv_base"] / prev["iv_base"] - 1.0
        if abs(mv) < threshold:
            continue
        why = explain(prev, cur, comps_at(cur.get("market")))
        tag = (f"{cur['ticker']}: IV {prev['iv_base']:.2f} -> {cur['iv_base']:.2f} ({mv:+.0%}) "
               f"{str(prev['run_at'])[:10]} -> {str(cur['run_at'])[:10]}")
        moves.append(tag)
        if why:
            explained.append(tag + f" [{'; '.join(why)}]")
        elif not (_commit_of(prev.get("engine_version")) and _commit_of(cur.get("engine_version"))):
            # run_features recorded no deployed commit before 2026-11-27: a deploy between the runs cannot be
            # ruled out, so the move is neither explained nor a finding.
            unattributable.append(tag + " [deployed commit not recorded]")
        else:
            unexplained.append(tag)
    return {"status": WARN if unexplained else OK, "checked": checked, "threshold": threshold,
            "summary": (f"{len(unexplained)} run(s) moved IV > {threshold:.0%} vs the ticker's previous run with no "
                        f"deploy, constants, routing, estimate or comps change between") if unexplained else
                       f"{checked} run(s) compared with their previous run; no unexplained IV move > {threshold:.0%}",
            "unexplained": unexplained, "explained": explained, "unattributable": unattributable}


# ── run + store ────────────────────────────────────────────────────────────

def run_canary(*, golden: bool = True, hours: float = 24.0) -> dict:
    from src.memory.run_features import deployed_commit
    checks = []
    if golden:
        checks += _safe("canary_golden", lambda: [_as_check("canary_golden", golden_canary())])
    checks += _safe("canary_live_drift", lambda: [_as_check("canary_live_drift", live_drift(hours))])
    from src.monitoring.prod_health import _RANK
    status = max((c["status"] for c in checks), key=_RANK.get, default=OK)
    return {"generated_at": _now().isoformat(timespec="seconds"), "commit": deployed_commit(),
            "status": status, "checks": checks}


def _as_check(cid: str, res: dict) -> dict:
    if cid == "canary_golden":
        items = [f"{r['fixture']}: {r['verdict'].upper()}"
                 + (f" IV {r.get('pinned_iv')} -> {r.get('replay_iv')} ({r['iv_move']:+.1%})"
                    if isinstance(r.get("iv_move"), (int, float)) else "")
                 + (f" -- {r['detail']}" if r.get("detail") else "")
                 for r in res.get("results", []) if r["verdict"] != "ok"]
    else:
        items = (res.get("unexplained", []) + [f"(explained) {e}" for e in res.get("explained", [])]
                 + [f"(unattributable) {e}" for e in res.get("unattributable", [])])
    out = _check(cid, res["status"], res["summary"], items=items)
    out["detail"] = {k: v for k, v in res.items() if k not in ("status", "summary")}
    return out


_DDL_PG = ("CREATE TABLE IF NOT EXISTS canary_runs (id BIGSERIAL PRIMARY KEY, generated_at TEXT NOT NULL, "
           "status TEXT NOT NULL, commit_sha TEXT, report_json TEXT NOT NULL)")
_DDL_SQLITE = ("CREATE TABLE IF NOT EXISTS canary_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT "
               "NOT NULL, status TEXT NOT NULL, commit_sha TEXT, report_json TEXT NOT NULL)")


def _ensure():
    from src.data import db
    db.ensure_table(_DDL_PG if db.is_postgres() else _DDL_SQLITE)


def record(report: dict) -> None:
    from src.data import db
    _ensure()
    db.execute("INSERT INTO canary_runs (generated_at, status, commit_sha, report_json) VALUES (?, ?, ?, ?)",
               [report["generated_at"], report["status"], report.get("commit"), json.dumps(report, default=str)])


#: the health report fires an hour after the canary; older than this, the canary itself did not run.
LATEST_MAX_AGE_H = 30.0


def latest_checks() -> list[dict]:
    """The health report's view: the newest canary row's checks, or a fail when none ran recently."""
    from src.data import db
    _ensure()
    row = db.query_one("SELECT generated_at, report_json FROM canary_runs ORDER BY id DESC LIMIT 1")
    if not row:
        return [_check("canary", WARN, "the nightly canary has not run yet")]
    at = _parse_ts(_row_get(row, "generated_at", 0))
    age = (_now() - at).total_seconds() / 3600 if at else None
    if age is None or age > LATEST_MAX_AGE_H:
        return [_check("canary", FAIL, f"the nightly canary last ran {age:.0f}h ago" if age else
                       "the canary row carries no timestamp")]
    rep = json.loads(_row_get(row, "report_json", 1) or "{}")
    return [{k: v for k, v in c.items() if k != "detail"} for c in rep.get("checks", [])]

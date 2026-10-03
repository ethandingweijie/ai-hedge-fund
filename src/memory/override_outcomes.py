"""Loop 5 of the self-learning layer: the user's estimate overrides as a labelled disagreement.

The accuracy tracker scores the agent only (owner, 2026-10-03), and that stands: nothing
here touches valuation_outcomes or estimate_outcomes. This is the other side of the same
coin -- when a user overrode the agent's estimates, who turned out closer?

Each saved override keeps the agent's intrinsic value and 12-month target before the
override and the user's after it. At each price horizon that has matured (never the
consensus label: the user's judgement is not graded against the Street), both are scored
against the realised close, and the row says whether the user was closer. The ledger also
counts which fields users change and in which profiles, so a systematic disagreement
(say, fade years on software names) is visible as a pattern rather than a memory of one
ticker. Report only: nothing here proposes or changes a number.

Override rows are keyed on the WEB run id; the archive and the outcome ledger are keyed on
the archive run id, so web_runs.archive_run_id is the bridge.

Kill switch: OVERRIDE_OUTCOMES_DISABLED=true. Storage is dual-mode through src.data.db.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Optional

from src.data import db as _db
from src.memory import valuation_outcomes as vo

logger = logging.getLogger(__name__)

MIN_REPORT_N = 5

_DDL = """
CREATE TABLE IF NOT EXISTS override_outcomes (
    outcome_key      TEXT PRIMARY KEY,
    override_id      TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    archive_run_id   TEXT,
    ticker           TEXT NOT NULL,
    user_id          INTEGER,
    run_date         TEXT NOT NULL,
    horizon          TEXT NOT NULL,
    label_value      REAL NOT NULL,
    label_date       TEXT,
    label_source     TEXT,
    before_iv REAL, after_iv REAL, before_pt REAL, after_pt REAL,
    before_iv_log_err REAL, after_iv_log_err REAL, before_pt_log_err REAL, after_pt_log_err REAL,
    user_closer      INTEGER,
    fields_json      TEXT,
    profile TEXT, market TEXT,
    scored_at        TEXT NOT NULL
)
"""
_COLUMNS = ["outcome_key", "override_id", "run_id", "archive_run_id", "ticker", "user_id", "run_date",
            "horizon", "label_value", "label_date", "label_source", "before_iv", "after_iv",
            "before_pt", "after_pt", "before_iv_log_err", "after_iv_log_err", "before_pt_log_err",
            "after_pt_log_err", "user_closer", "fields_json", "profile", "market", "scored_at"]
_tables_ready_key: Optional[tuple] = None


def enabled() -> bool:
    return os.environ.get("OVERRIDE_OUTCOMES_DISABLED", "false").strip().lower() not in (
        "1", "true", "yes", "on")


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL)
    _tables_ready_key = key


def _web_run(run_id: str) -> Optional[dict]:
    """{archive_run_id, run_at} for a web run id; the row itself when the id is already an
    archive id (an override saved against an archive run)."""
    try:
        r = _db.query_one("SELECT run_id, archive_run_id, run_at FROM web_runs WHERE run_id = ?", [run_id])
        if r:
            return {"archive_run_id": r["archive_run_id"] or r["run_id"], "run_at": r["run_at"]}
    except Exception:                                      # noqa: BLE001
        pass
    try:
        r = _db.query_one("SELECT run_id, run_at FROM runs WHERE run_id = ?", [run_id])
        if r:
            return {"archive_run_id": r["run_id"], "run_at": r["run_at"]}
    except Exception:                                      # noqa: BLE001
        pass
    return None


def _log_err(pred, label: float) -> Optional[float]:
    p = vo._pos(pred)
    return round(math.log(p / label), 6) if p and label > 0 else None


def _pick(d: dict, *keys) -> Optional[float]:
    for k in keys:
        v = vo._pos((d or {}).get(k))
        if v is not None:
            return v
    return None


def score_matured(*, today: Optional[date] = None, closes_fn: Optional[Callable] = None,
                  write: bool = True) -> dict:
    """Score every active override at each matured price horizon. Idempotent per
    (override, horizon). Labels come from valuation_outcomes when the agent's run was
    labelled there, else from the price series; consensus is never a label here."""
    report = {k: 0 for k in ("overrides", "unmapped", "not_matured", "no_price", "written")}
    if not enabled():
        report["disabled"] = True
        return report
    _ensure_tables()
    vo._ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    closes_fn = closes_fn or vo._default_closes
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from src.data import estimate_override_store as store
        store.ensure_table()
        recs = _db.query("SELECT id, run_id, ticker, user_id, overrides_json, result_json, created_at "
                         "FROM estimate_overrides WHERE active = 1 ORDER BY created_at ASC")
    except Exception as exc:                               # noqa: BLE001
        logger.warning("override_outcomes: estimate_overrides unavailable: %s", exc)
        return report
    existing = {r["outcome_key"] for r in _db.query("SELECT outcome_key FROM override_outcomes")}
    labels = {(r["run_id"], r["ticker"], r["horizon"]): dict(r) for r in _db.query(
        "SELECT run_id, ticker, horizon, label_value, label_date, label_source, market, profile "
        "FROM valuation_outcomes WHERE horizon != ?", [vo.CONSENSUS])}

    out: list[dict] = []
    needs: dict[str, list[tuple[dict, str, date]]] = {}
    for rec in recs:
        report["overrides"] += 1
        link = _web_run(rec["run_id"])
        if not link or not link.get("run_at"):
            report["unmapped"] += 1
            continue
        try:
            run_date = date.fromisoformat(str(link["run_at"])[:10])
        except ValueError:
            report["unmapped"] += 1
            continue
        res = vo._loads(rec["result_json"]) or {}
        before, after = res.get("before") or {}, res.get("after") or {}
        base = {
            "override_id": rec["id"], "run_id": rec["run_id"], "archive_run_id": link["archive_run_id"],
            "ticker": (rec["ticker"] or "").upper(), "user_id": rec["user_id"], "run_date": run_date.isoformat(),
            "before_iv": _pick(before, "intrinsic_value"), "after_iv": _pick(after, "intrinsic_value"),
            "before_pt": _pick(before, "12m_price_target", "target"),
            "after_pt": _pick(after, "12m_price_target", "target"),
            "fields_json": json.dumps(res.get("fields") or []),
            "profile": None, "market": vo.market_of((rec["ticker"] or "").upper()),
        }
        for horizon, days in vo.HORIZON_DAYS.items():
            key = f"{rec['id']}|{horizon}"
            if key in existing:
                continue
            target = run_date + timedelta(days=days)
            if target >= today:
                report["not_matured"] += 1
                continue
            lab = labels.get((link["archive_run_id"], base["ticker"], horizon))
            if lab is not None:
                out.append(_row(base, horizon, float(lab["label_value"]), lab["label_date"],
                                lab["label_source"] or "close", lab.get("profile"), now_iso))
            else:
                needs.setdefault(base["ticker"], []).append((base, horizon, target))

    for ticker, items in needs.items():
        start = min(t for _, _, t in items)
        end = min(max(t for _, _, t in items) + timedelta(days=vo._PRICE_WINDOW_DAYS), today)
        try:
            series = sorted(closes_fn(ticker, start.isoformat(), end.isoformat()) or [])
        except Exception as exc:                           # noqa: BLE001
            logger.warning("override_outcomes: prices for %s failed: %s", ticker, exc)
            series = []
        for base, horizon, target in items:
            hit = vo._close_on_or_after(series, target)
            if hit is None:
                report["no_price"] += 1
                continue
            out.append(_row(base, horizon, hit[1], hit[0], "close", None, now_iso))

    report["rows"] = len(out)
    if write and out:
        _db.executemany(vo._upsert_sql("override_outcomes", "outcome_key", _COLUMNS),
                        [[r[c] for c in _COLUMNS] for r in out])
        report["written"] = len(out)
    return report


def _row(base: dict, horizon: str, label: float, label_date, source: str, profile, now_iso: str) -> dict:
    b_iv, a_iv = _log_err(base["before_iv"], label), _log_err(base["after_iv"], label)
    b_pt, a_pt = _log_err(base["before_pt"], label), _log_err(base["after_pt"], label)
    closer = None
    if b_pt is not None and a_pt is not None:
        closer = int(abs(a_pt) < abs(b_pt))
    elif b_iv is not None and a_iv is not None:
        closer = int(abs(a_iv) < abs(b_iv))
    return {**base, "outcome_key": f"{base['override_id']}|{horizon}", "horizon": horizon,
            "label_value": label, "label_date": label_date, "label_source": source,
            "before_iv_log_err": b_iv, "after_iv_log_err": a_iv, "before_pt_log_err": b_pt,
            "after_pt_log_err": a_pt, "user_closer": closer, "profile": profile or base.get("profile"),
            "scored_at": now_iso}


def _field_names(overrides: dict) -> list[str]:
    names: set[str] = set()
    for sc, block in (overrides.get("scenarios") or {}).items():
        if isinstance(block, dict):
            names.update(f"{k}" for k, v in block.items() if v is not None)
    for k, v in (overrides.get("shared") or {}).items():
        if v is not None:
            names.add(k)
    if overrides.get("medium_term_target"):
        names.add("medium_term_target")
    return sorted(names)


def report() -> dict:
    """Agent vs user per horizon (median |log error| of each, the share where the user was
    closer, n), and which fields users change, overall and by the run's profile."""
    _ensure_tables()
    rows = [dict(r) for r in _db.query("SELECT * FROM override_outcomes")]
    by_h: dict[str, list[dict]] = {}
    for r in rows:
        by_h.setdefault(r["horizon"], []).append(r)
    horizons = {}
    for h in vo.HORIZON_DAYS:
        rs = by_h.get(h, [])
        a = [abs(r["before_pt_log_err"]) for r in rs if r["before_pt_log_err"] is not None]
        u = [abs(r["after_pt_log_err"]) for r in rs if r["after_pt_log_err"] is not None]
        closer = [r["user_closer"] for r in rs if r["user_closer"] is not None]
        horizons[h] = {"n": len(rs),
                       "agent_median_miss_pct": round((math.exp(median(a)) - 1) * 100, 2) if a else None,
                       "user_median_miss_pct": round((math.exp(median(u)) - 1) * 100, 2) if u else None,
                       "user_closer_share": round(sum(closer) / len(closer), 3) if closer else None,
                       "status": "ok" if len(rs) >= MIN_REPORT_N else "insufficient"}
    fields: dict[str, int] = {}
    by_profile: dict[str, dict[str, int]] = {}
    n_overrides = 0
    try:
        from src.data import estimate_override_store as store
        store.ensure_table()
        for rec in _db.query("SELECT run_id, ticker, overrides_json FROM estimate_overrides WHERE active = 1"):
            n_overrides += 1
            ov = vo._loads(rec["overrides_json"]) or {}
            profile = None
            try:
                from src.memory import run_features as rf
                link = _web_run(rec["run_id"])
                feat = rf.latest_for_ticker(rec["ticker"]) if link else None
                if feat and link and feat.get("run_id") == link.get("archive_run_id"):
                    profile = feat.get("profile")
                elif feat:
                    profile = feat.get("profile")
            except Exception:                              # noqa: BLE001
                profile = None
            for name in _field_names(ov):
                fields[name] = fields.get(name, 0) + 1
                by_profile.setdefault(profile or "unknown", {})
                by_profile[profile or "unknown"][name] = by_profile[profile or "unknown"].get(name, 0) + 1
    except Exception as exc:                               # noqa: BLE001
        logger.debug("override_outcomes.report: overrides unavailable: %s", exc)
    return {"n_overrides": n_overrides, "n_scored_rows": len(rows), "horizons": horizons,
            "fields_changed": dict(sorted(fields.items(), key=lambda kv: -kv[1])),
            "fields_by_profile": by_profile, "min_n": MIN_REPORT_N}


def estimate_field_outcomes() -> dict:
    """For overrides that changed FY+1 revenue growth, EBITDA margin or EPS in the base case:
    the user's figure against the same print the agent was scored on (estimate_outcomes),
    and who was closer. Report only."""
    _ensure_tables()
    out = {"n": 0, "user_closer": 0, "rows": []}
    try:
        from src.data import estimate_override_store as store
        store.ensure_table()
        recs = _db.query("SELECT id, run_id, ticker, overrides_json FROM estimate_overrides WHERE active = 1")
        scored = {(r["run_id"], r["ticker"], r["field"]): dict(r) for r in _db.query(
            "SELECT run_id, ticker, field, agent_base, actual FROM estimate_outcomes WHERE period_kind = 'fy'")}
    except Exception as exc:                               # noqa: BLE001
        logger.debug("estimate_field_outcomes unavailable: %s", exc)
        return out
    field_map = {"revenue_growth_fy1": "revenue_growth", "ebitda_margin_fy1": "ebitda_margin", "eps_fy1": "eps"}
    for rec in recs:
        link = _web_run(rec["run_id"])
        if not link:
            continue
        ov = vo._loads(rec["overrides_json"]) or {}
        base = ((ov.get("scenarios") or {}).get("base") or {})
        for ov_key, field in field_map.items():
            user_v = base.get(ov_key)
            if not isinstance(user_v, (int, float)):
                continue
            s = scored.get((link["archive_run_id"], (rec["ticker"] or "").upper(), field))
            if not s or s.get("actual") is None or s.get("agent_base") is None:
                continue
            actual, agent = float(s["actual"]), float(s["agent_base"])
            closer = abs(float(user_v) - actual) < abs(agent - actual)
            out["n"] += 1
            out["user_closer"] += int(closer)
            out["rows"].append({"override_id": rec["id"], "ticker": rec["ticker"], "field": field,
                                "agent": agent, "user": float(user_v), "actual": actual, "user_closer": closer})
    out["user_closer_share"] = round(out["user_closer"] / out["n"], 3) if out["n"] else None
    return out


def counts() -> dict:
    _ensure_tables()
    rows = _db.query("SELECT horizon, COUNT(*) AS n FROM override_outcomes GROUP BY horizon")
    return {r["horizon"]: int(r["n"]) for r in rows}

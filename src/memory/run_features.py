"""The flat per-run ledger every learning loop reads (self-learning layer, Phase A).

One row per (archive run_id, ticker) with the figures the scorers group on and
recompute from: the three intrinsic values and targets, the capture, the
scenario probabilities, the blend (method values and weights), the gate records,
and the agent's FY+1 estimates beside consensus and management guidance.

Why a table rather than more blob parsing: the probabilities live in
ticker_signals.scenario_json, everything else in dcf_range_json, and the
estimate, gate, override and scenario scorers all group on archetype,
confidence, fiscal year, profile and market -- columns, not JSON paths. The
blobs stay the record. `features_version` says which extractor wrote a row;
`backfill(force=True)` rewrites older rows, so a change here is a re-run, not a
migration.

Doctrine carried in `rows(exclude_user=True)`: a run built on a carried user
override, an unrated valuation and a cache copy are never training data.

Kill switch: RUN_FEATURES_DISABLED=true makes record() and backfill() no-ops
and rows() return []. Storage is dual-mode through src.data.db.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from src.data import db as _db
from src.memory import valuation_outcomes as vo

logger = logging.getLogger(__name__)

FEATURES_VERSION = 4          # 4: + fy0_end (the last reported annual period end; the scorers' anchor)
#          # 2: + pt_calibration_version (Phase C); 3: FYE month from financials_used, balance-sheet families keep no forecast EPS / margin

_DDL = """
CREATE TABLE IF NOT EXISTS run_features (
    feature_key          TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    run_at               TEXT NOT NULL,
    run_date             TEXT NOT NULL,
    features_version     INTEGER NOT NULL,
    market               TEXT,
    sector               TEXT,
    profile              TEXT,
    routing_winner       TEXT,
    param_version        TEXT,
    calibration_version  TEXT,
    pt_calibration_version TEXT,
    regime_risk          TEXT,
    research_tier        TEXT,
    spot                 REAL,
    iv_bear REAL, iv_base REAL, iv_bull REAL,
    pt_bear REAL, pt_base REAL, pt_bull REAL,
    pt_12m               REAL,
    pm_target            REAL,
    capture              REAL,
    prob_bear REAL, prob_base REAL, prob_bull REAL,
    methods_json         TEXT,
    gates_json           TEXT,
    archetype            TEXT,
    confidence           TEXT,
    fiscal_year_1        INTEGER,
    fye_month            INTEGER,
    fy0_end              TEXT,
    agent_rg_fy1_bear REAL, agent_rg_fy1_base REAL, agent_rg_fy1_bull REAL,
    agent_em_fy1_bear REAL, agent_em_fy1_base REAL, agent_em_fy1_bull REAL,
    agent_eps_fy1_bear REAL, agent_eps_fy1_base REAL, agent_eps_fy1_bull REAL,
    agent_source         TEXT,
    cons_rg_fy1          REAL,
    cons_eps_fy1         REAL,
    guid_rg_mid          REAL,
    guid_eps_mid         REAL,
    guid_source          TEXT,
    override_carried     INTEGER NOT NULL DEFAULT 0,
    unrated              INTEGER NOT NULL DEFAULT 0,
    cache_copy           INTEGER NOT NULL DEFAULT 0,
    recorded_at          TEXT NOT NULL
)
"""
_DDL_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_run_features_ticker ON run_features (ticker, run_date)",
    "CREATE INDEX IF NOT EXISTS idx_run_features_cell ON run_features (market, profile, run_date)",
    "CREATE INDEX IF NOT EXISTS idx_run_features_fy ON run_features (fiscal_year_1, ticker)",
)

COLUMNS = [
    "feature_key", "run_id", "ticker", "run_at", "run_date", "features_version",
    "market", "sector", "profile", "routing_winner", "param_version", "calibration_version",
    "pt_calibration_version", "regime_risk", "research_tier", "spot",
    "iv_bear", "iv_base", "iv_bull", "pt_bear", "pt_base", "pt_bull", "pt_12m", "pm_target",
    "capture", "prob_bear", "prob_base", "prob_bull", "methods_json", "gates_json",
    "archetype", "confidence", "fiscal_year_1", "fye_month", "fy0_end",
    "agent_rg_fy1_bear", "agent_rg_fy1_base", "agent_rg_fy1_bull",
    "agent_em_fy1_bear", "agent_em_fy1_base", "agent_em_fy1_bull",
    "agent_eps_fy1_bear", "agent_eps_fy1_base", "agent_eps_fy1_bull",
    "agent_source", "cons_rg_fy1", "cons_eps_fy1", "guid_rg_mid", "guid_eps_mid", "guid_source",
    "override_carried", "unrated", "cache_copy", "recorded_at",
]

#: The filter every learning reader applies: the agent's own, rated, original runs.
AGENT_ONLY_WHERE = "override_carried = 0 AND unrated = 0 AND cache_copy = 0"

_SCENARIOS = ("bear", "base", "bull")
_tables_ready_key: Optional[tuple] = None


def enabled() -> bool:
    return os.environ.get("RUN_FEATURES_DISABLED", "false").strip().lower() not in (
        "1", "true", "yes", "on")


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL)
    _db.add_column_if_missing("run_features", "pt_calibration_version", "TEXT")
    _db.add_column_if_missing("run_features", "fy0_end", "TEXT")
    for ddl in _DDL_IDX:
        _db.execute(ddl)
    _tables_ready_key = key


# ── small parsers ────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """A finite float or None (zero and negatives allowed: growth and EPS can be either)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fy_int(v) -> Optional[int]:
    """'FY2026' | 'FY2026E' | '2026-12' | 2026 | 2026.0 -> 2026."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        y = int(v)
        return y if 1990 <= y <= 2100 else None
    m = re.search(r"(19|20)\d{2}", str(v))
    return int(m.group(0)) if m else None


def _month_of(v) -> Optional[int]:
    m = re.match(r"^(19|20)\d{2}-(\d{2})", str(v or ""))
    if not m:
        return None
    month = int(m.group(2))
    return month if 1 <= month <= 12 else None


def _fye_month(dr: dict) -> Optional[int]:
    """Fiscal-year-end month: the latest annual row the engine used (financials_used.rows,
    e.g. NKE 2026-05-31 -> 5), else the opening balance sheet's period; None means December
    is assumed (the scorer flags that assumption)."""
    rows = (dr.get("financials_used") or {}).get("rows") if isinstance(dr.get("financials_used"), dict) else None
    if isinstance(rows, list):
        months = [_month_of((r or {}).get("period")) for r in rows if isinstance(r, dict)]
        months = [m for m in months if m]
        if months:
            return months[-1]
    opening = ((dr.get("forecast_context") or {}).get("opening_balance_sheet") or {})
    for key in ("period_end", "report_period", "date", "period"):
        m = _month_of(opening.get(key))
        if m:
            return m
    return None


def _fy0_end(dr: dict) -> Optional[str]:
    """The period-end date of the latest ANNUAL row the engine used (financials_used.rows),
    else the opening balance sheet's. FY+1 is the fiscal year that ends one year after this
    date -- the scorers anchor on it, never on the FY label, because labels disagree across
    sources (a retailer's "FY2026" ends in early 2027 by its own naming and in early 2026 by
    the data vendor's)."""
    rows = (dr.get("financials_used") or {}).get("rows") if isinstance(dr.get("financials_used"), dict) else None
    if isinstance(rows, list):
        dates = [str((r or {}).get("period") or "")[:10] for r in rows if isinstance(r, dict)]
        dates = sorted(d for d in dates if re.match(r"^(19|20)\d{2}-\d{2}-\d{2}$", d))
        if dates:
            return dates[-1]
    opening = ((dr.get("forecast_context") or {}).get("opening_balance_sheet") or {})
    for key in ("period_end", "report_period", "date", "period"):
        d = str(opening.get(key) or "")[:10]
        if re.match(r"^(19|20)\d{2}-\d{2}-\d{2}$", d):
            return d
    return None


def _balance_sheet_family(dr: dict) -> bool:
    """Banks, insurers and property: the generic forecast's EPS and EBITDA margin do not
    describe them, so the ledger keeps only the research's estimates (or the bank model's)."""
    fcx = dr.get("forecast_context") if isinstance(dr.get("forecast_context"), dict) else {}
    if fcx.get("statements_family_ok") is False or fcx.get("bank_model"):
        return True
    if dr.get("bank_breakdown"):
        return True
    ts = dr.get("three_statements") if isinstance(dr.get("three_statements"), dict) else {}
    return bool(ts.get("bank_metrics") or ts.get("steady_rote") or ts.get("kind") in ("bank", "insurer"))


def _prob(case) -> Optional[float]:
    p = _num((case or {}).get("probability")) if isinstance(case, dict) else None
    if p is None:
        return None
    if p > 1.0:
        p = p / 100.0
    return p if 0.0 <= p <= 1.0 else None


def _methods(base: dict) -> Optional[str]:
    table = base.get("method_iv_table") or {}
    if not isinstance(table, dict) or not table:
        return None
    weights: dict[str, float] = {}
    for e in base.get("effective_weights") or []:
        if not isinstance(e, dict):
            continue
        w = _num(e.get("weight"))
        if w is None:
            continue
        for k in (e.get("value_key"), e.get("method")):
            if k:
                weights[str(k)] = w
    out = {}
    for name, value in table.items():
        v = _num(value)
        if v is None:
            continue
        out[str(name)] = {"value": v, "weight": weights.get(str(name))}
    return json.dumps(out, sort_keys=True) if out else None


def _gates(dr: dict) -> Optional[str]:
    recs = dr.get("gate_evaluations")
    if not isinstance(recs, list):
        return None
    out = []
    for g in recs:
        if not isinstance(g, dict) or not g.get("gate_id"):
            continue
        out.append({
            "gate_id": str(g.get("gate_id")),
            "metric": g.get("metric"),
            "applied": (bool(g["applied"]) if "applied" in g else None),
            "a": _num(g.get("raw_input_path_a")),
            "b": _num(g.get("gated_output_path_b")),
        })
    return json.dumps(out)


def _first_row(fc) -> dict:
    rows = (fc or {}).get("rows") if isinstance(fc, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _agent_fy1(dr: dict) -> tuple[dict, Optional[str]]:
    """{scenario: {rg, em, eps}} for FY+1, and where the figures came from.

    Preference: the deterministic forecast's first row (the numbers the DCF actually ran
    on); the research block's estimates when there is no forecast. The EBITDA margin is
    only in the research block; when it is missing the forecast's EBIT margin stands in
    and the source says so."""
    est = ((dr.get("guidance_estimates") or {}).get("estimates") or {})
    fc_base = dr.get("guidance_forecast") if isinstance(dr.get("guidance_forecast"), dict) else None
    fc_sc = dr.get("guidance_forecast_scenarios") or {}
    balance_sheet = _balance_sheet_family(dr)
    fcx = dr.get("forecast_context") if isinstance(dr.get("forecast_context"), dict) else {}
    bank_eps = _num((fcx.get("bank_model") or {}).get("eps_fy1")) if isinstance(fcx.get("bank_model"), dict) else None
    out: dict = {}
    used_fc = used_est = used_proxy = used_bank = False
    for sc in _SCENARIOS:
        row = _first_row(fc_base if sc == "base" else fc_sc.get(sc))
        e = est.get(sc) if isinstance(est.get(sc), dict) else {}
        rg = _num(row.get("growth")) if row else None
        eps = _num(row.get("eps")) if (row and not balance_sheet) else None
        if rg is not None or eps is not None:
            used_fc = True
        if rg is None and _num(e.get("revenue_growth_fy1")) is not None:
            rg = _num(e.get("revenue_growth_fy1")); used_est = True
        if balance_sheet and sc == "base" and bank_eps is not None:
            eps = bank_eps; used_bank = True
        if eps is None and _num(e.get("eps_fy1")) is not None:
            eps = _num(e.get("eps_fy1")); used_est = True
        em = _num(e.get("ebitda_margin_fy1")) if not balance_sheet else None
        if em is not None:
            used_est = True
        elif row and not balance_sheet and _num(row.get("ebit_margin")) is not None:
            em = _num(row.get("ebit_margin")); used_proxy = True
        out[sc] = {"rg": rg, "em": em, "eps": eps}
    parts = []
    if used_fc:
        parts.append("guidance_forecast.rows[0]")
    if used_est:
        parts.append("guidance_estimates")
    if used_bank:
        parts.append("bank_model.eps_fy1")
    if used_proxy:
        parts.append("ebit_margin_proxy")
    if balance_sheet:
        parts.append("balance_sheet_family")
    return out, ("+".join(parts) or None)


def _pt_version(bridge: dict, scenario: dict) -> Optional[str]:
    """The pt calibration a run was made under: pt_bridge.capture_source 'calibration:<id>'
    or the scenario agent's probability_shrink.version_id."""
    src = str((bridge or {}).get("capture_source") or "")
    if src.startswith("calibration:"):
        return src.split(":", 1)[1] or None
    ps = (scenario or {}).get("probability_shrink") if isinstance(scenario, dict) else None
    if isinstance(ps, dict) and ps.get("version_id"):
        return str(ps["version_id"])
    return None


def _guidance_mid(block: dict, field: str) -> Optional[float]:
    g = (block.get("guidance") or {}).get(field) if isinstance(block.get("guidance"), dict) else None
    if not isinstance(g, dict):
        return None
    mid = _num(g.get("mid"))
    if mid is not None:
        return mid
    lo, hi = _num(g.get("low")), _num(g.get("high"))
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    return lo if lo is not None else hi


# ── extraction ───────────────────────────────────────────────────────────────

def extract(run_id: str, ticker: str, dr: dict, scenario: Optional[dict], *,
            run_at: str, sector: Optional[str] = None, price_at_run=None,
            pm_target=None, research_tier: Optional[str] = None,
            regime_risk: Optional[str] = None, cache_copy: bool = False) -> dict:
    """Pure: the run_features row for one (run, ticker). Raises on a malformed dr."""
    if not isinstance(dr, dict) or not dr:
        raise ValueError("dcf_range entry missing")
    ticker = (ticker or "").upper()
    if not ticker or not run_id:
        raise ValueError("run_id and ticker are required")
    run_date = str(run_at)[:10]
    datetime.fromisoformat(run_date)                      # validates the date
    scenario = scenario if isinstance(scenario, dict) else {}
    base = dr.get("base") if isinstance(dr.get("base"), dict) else {}
    pt = dr.get("12m_targets") if isinstance(dr.get("12m_targets"), dict) else {}
    bridge = dr.get("pt_bridge") if isinstance(dr.get("pt_bridge"), dict) else {}
    ge = dr.get("guidance_estimates") if isinstance(dr.get("guidance_estimates"), dict) else {}
    fcx = dr.get("forecast_context") if isinstance(dr.get("forecast_context"), dict) else {}
    agent, agent_source = _agent_fy1(dr)
    fy1 = (_fy_int(fcx.get("fiscal_year_1")) or _fy_int(ge.get("fiscal_year_1"))
           or (_fy_int(((fcx.get("opening_balance_sheet") or {}).get("fiscal_year")) or 0) or 0) + 1
           or None)
    if fy1 is not None and fy1 < 1991:
        fy1 = None
    spot = (vo._pos(bridge.get("spot")) or vo._pos(price_at_run)
            or vo._pos(scenario.get("current_price")))
    cons = ge.get("consensus") if isinstance(ge.get("consensus"), dict) else {}
    gsrc = (ge.get("guidance") or {}).get("source") if isinstance(ge.get("guidance"), dict) else None
    calib = dr.get("calibration") if isinstance(dr.get("calibration"), dict) else {}
    return {
        "feature_key": f"{run_id}|{ticker}", "run_id": run_id, "ticker": ticker,
        "run_at": str(run_at), "run_date": run_date, "features_version": FEATURES_VERSION,
        "market": vo.market_of(ticker), "sector": sector,
        "profile": dr.get("profile"),
        "routing_winner": (dr.get("routing_trace") or {}).get("winner"),
        "param_version": dr.get("param_version"),
        "calibration_version": calib.get("version_id"),
        "pt_calibration_version": _pt_version(bridge, scenario),
        "regime_risk": regime_risk, "research_tier": research_tier,
        "spot": spot,
        "iv_bear": vo._pos((dr.get("bear") or {}).get("intrinsic_value")),
        "iv_base": vo._pos(base.get("intrinsic_value")),
        "iv_bull": vo._pos((dr.get("bull") or {}).get("intrinsic_value")),
        "pt_bear": vo._pos(pt.get("bear")), "pt_base": vo._pos(pt.get("base")),
        "pt_bull": vo._pos(pt.get("bull")),
        "pt_12m": vo._pos(scenario.get("12m_price_target")),
        "pm_target": vo._pos(pm_target),
        "capture": _num(bridge.get("capture")),
        "prob_bear": _prob(scenario.get("bear")), "prob_base": _prob(scenario.get("base")),
        "prob_bull": _prob(scenario.get("bull")),
        "methods_json": _methods(base), "gates_json": _gates(dr),
        "archetype": ((dr.get("guidance_forecast") or {}).get("archetype")
                      if isinstance(dr.get("guidance_forecast"), dict) else None),
        "confidence": ge.get("confidence"),
        "fiscal_year_1": fy1, "fye_month": _fye_month(dr), "fy0_end": _fy0_end(dr),
        "agent_rg_fy1_bear": agent["bear"]["rg"], "agent_rg_fy1_base": agent["base"]["rg"],
        "agent_rg_fy1_bull": agent["bull"]["rg"],
        "agent_em_fy1_bear": agent["bear"]["em"], "agent_em_fy1_base": agent["base"]["em"],
        "agent_em_fy1_bull": agent["bull"]["em"],
        "agent_eps_fy1_bear": agent["bear"]["eps"], "agent_eps_fy1_base": agent["base"]["eps"],
        "agent_eps_fy1_bull": agent["bull"]["eps"],
        "agent_source": agent_source,
        "cons_rg_fy1": _num(cons.get("revenue_growth_fy1")),
        "cons_eps_fy1": _num(cons.get("eps_fy1")),
        "guid_rg_mid": _guidance_mid(ge, "revenue_growth"),
        "guid_eps_mid": _guidance_mid(ge, "eps"),
        "guid_source": gsrc,
        "override_carried": int(bool(dr.get("estimate_override_carried"))),
        "unrated": int(vo.is_unrated(dr)),
        "cache_copy": int(bool(cache_copy or dr.get("is_cache_copy"))),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _write(rows: list[dict]) -> int:
    if not rows:
        return 0
    _db.executemany(vo._upsert_sql("run_features", "feature_key", COLUMNS),
                    [[r[c] for c in COLUMNS] for r in rows])
    return len(rows)


def record(run_id: str, ticker: str, dr: dict, scenario: Optional[dict], **ctx) -> bool:
    """Write one row at save time. Never raises: the daily backfill is the source of truth."""
    if not enabled():
        return False
    try:
        _ensure_tables()
        _write([extract(run_id, ticker, dr, scenario, **ctx)])
        return True
    except Exception as exc:                               # noqa: BLE001
        logger.warning("run_features: record(%s, %s) skipped: %s", run_id, ticker, exc)
        return False


# ── backfill ─────────────────────────────────────────────────────────────────

def _iter_rows(page_size: int) -> Iterator:
    """ticker_signals with a dcf_range, OLDEST first (so the original of a replayed entry
    precedes its copies), with the run-level fields the ledger keeps."""
    from src.memory import run_archive
    offset = 0
    while True:
        page = run_archive._fetch(
            "SELECT ts.run_id, ts.ticker, r.run_at, r.sector, r.research_tier, "
            "r.regime_risk_appetite, ts.price_at_run, ts.price_target, "
            "ts.dcf_range_json, ts.scenario_json "
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


def backfill(*, page_size: int = 200, force: bool = False, write: bool = True) -> dict:
    """Row for every archived (run, ticker). Idempotent: rows already at FEATURES_VERSION
    are skipped unless force. Cache copies are detected exactly as the outcome sweep does:
    the dcf_range flag, or an identical content hash seen earlier for the ticker."""
    report = {k: 0 for k in ("seen", "written", "skipped_current", "cache_copies", "errors")}
    if not enabled():
        report["disabled"] = True
        return report
    _ensure_tables()
    current = {r["feature_key"] for r in _db.query(
        "SELECT feature_key FROM run_features WHERE features_version >= ?", [FEATURES_VERSION])}
    seen: dict[str, set[str]] = {}
    out: list[dict] = []
    for row in _iter_rows(page_size):
        report["seen"] += 1
        ticker = (row["ticker"] or "").upper()
        dr = vo._loads(row["dcf_range_json"])
        if not ticker or not isinstance(dr, dict) or not dr:
            report["errors"] += 1
            continue
        digest = vo._content_hash(dr)
        copy = bool(dr.get("is_cache_copy")) or digest in seen.setdefault(ticker, set())
        seen[ticker].add(digest)
        if copy:
            report["cache_copies"] += 1
        key = f"{row['run_id']}|{ticker}"
        if key in current and not force:
            report["skipped_current"] += 1
            continue
        try:
            out.append(extract(
                row["run_id"], ticker, dr, vo._loads(row["scenario_json"]),
                run_at=row["run_at"], sector=row["sector"], price_at_run=row["price_at_run"],
                pm_target=row["price_target"], research_tier=row["research_tier"],
                regime_risk=row["regime_risk_appetite"], cache_copy=copy))
        except Exception as exc:                           # noqa: BLE001
            report["errors"] += 1
            logger.warning("run_features: backfill %s skipped: %s", key, exc)
    report["candidates"] = len(out)
    if write:
        report["written"] = _write(out)
    return report


# ── readers ──────────────────────────────────────────────────────────────────

def rows(*, where: str = "", params: Optional[list] = None,
         exclude_user: bool = True, order: str = "run_at ASC") -> list[dict]:
    """run_features rows as dicts. exclude_user (the default) keeps only the agent's own,
    rated, original runs -- the filter every learning loop must apply."""
    if not enabled():
        return []
    _ensure_tables()
    clauses = []
    if exclude_user:
        clauses.append(AGENT_ONLY_WHERE)
    if where:
        clauses.append(f"({where})")
    sql = "SELECT * FROM run_features"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {order}"
    return [dict(r) for r in _db.query(sql, list(params or []))]


def latest_for_ticker(ticker: str) -> Optional[dict]:
    """The ticker's newest row regardless of exclusions (a lookup, not training data)."""
    if not enabled():
        return None
    _ensure_tables()
    r = _db.query_one("SELECT * FROM run_features WHERE ticker = ? ORDER BY run_at DESC LIMIT 1",
                      [ticker.upper()])
    return dict(r) if r else None


def counts() -> dict:
    """Diagnostics: rows, and how many each exclusion removes."""
    _ensure_tables()
    r = _db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(override_carried),0) AS oc, "
        "COALESCE(SUM(unrated),0) AS un, COALESCE(SUM(cache_copy),0) AS cc, "
        "MAX(recorded_at) AS last FROM run_features")
    return {"rows": int(r["n"] or 0), "override_carried": int(r["oc"] or 0),
            "unrated": int(r["un"] or 0), "cache_copy": int(r["cc"] or 0),
            "last_recorded_at": r["last"], "features_version": FEATURES_VERSION}

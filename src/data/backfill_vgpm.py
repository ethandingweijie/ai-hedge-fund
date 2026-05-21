"""
backfill_vgpm.py — recompute VGPM for historical web_runs using the new
sector-aware sub-score bands (commit 74a0b26, src/utils/vgpm_thresholds.py).

Background: the pre-2026-05-21 VGPM scorer in src/utils/pdf_report.py used
cross-sector universal threshold bands. Result: every analysed ticker
collapsed to B-band letter grades post-2026-04-25. The 74a0b26 fix made
4 sub-scores (v3 / g1 / p1 / p2) sector-aware via per-sector tables.

This module re-runs `_compute_vgpm()` against the inputs already saved
in each historical web_runs row (DCF outputs, scenario analysis, raw
financials, insider summary) using the ticker's persisted sector.
Replaces `full_result_json.data.vgpm[ticker]` and `full_result_json.vgpm[ticker]`
in place. Idempotent — re-runs against an already-backfilled DB are no-ops
(same inputs → same outputs).

Public surface:
  backfill_vgpm_for_runs(*, db_path=None, since_iso=..., dry_run=True) → dict

Returns a summary dict with:
  - runs_examined        : total web_runs in window
  - runs_with_vgpm       : runs that had VGPM (the ones we processed)
  - runs_updated         : runs whose VGPM JSON actually changed
  - tickers_updated      : ticker-level count
  - tickers_skipped      : missing dcf_range or scenario inputs
  - grade_changes        : sample of pre/post grade transitions
  - errors               : per-run error log
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# v3.21 cutoff per the user's "after Apr 25" request. Excludes pre-fix runs
# that had not yet acquired the B-band collapse symptom.
DEFAULT_SINCE_ISO = "2026-04-25T00:00:00+00:00"

# Max number of (ticker, dim, before, after) entries to capture for the
# grade_changes diagnostic. Caps response payload size on large windows.
MAX_GRADE_CHANGE_SAMPLES = 50


def _resolve_db_path(db_path: str | None) -> str:
    """Mirror src/data/zscore_engine.py::_resolve_db_path. Uses
    RUN_ARCHIVE_PATH env var first, then default location."""
    if db_path:
        return db_path
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    return str(Path(__file__).resolve().parent / "run_archive.db")


def _build_vgpm_inputs(
    data: dict, ticker: str
) -> tuple[dict, dict, dict, dict, str, str | None] | None:
    """Extract the inputs `_compute_vgpm()` needs from a saved run's data dict.

    Returns (dcf_ticker, scen_ticker, raw_financials, dcf_cal,
             insider_summary, sector) or None when essential inputs are
    missing (in which case the ticker is skipped — we can't recompute
    VGPM without the underlying numbers).
    """
    dcf_range  = data.get("dcf_range")  or {}
    scen_all   = data.get("scenario_analysis") or {}
    dcf_t  = dcf_range.get(ticker) or {}
    scen_t = scen_all.get(ticker) or {}

    # Need either dcf_range OR scenario_analysis populated to compute anything
    if not dcf_t and not scen_t:
        return None

    raw_fin = data.get("raw_financials") or {}
    base    = dcf_t.get("base") or {}
    dcf_cal = {
        "margin_direction": base.get("margin_direction", "stable"),
        "risk_flag":        base.get("risk_flag", ""),
    }

    # Insider summary — match the pipeline's flattening logic
    analyst_signals = data.get("analyst_signals") or {}
    insider_raw = (analyst_signals.get("insider_activity_agent") or {}).get(ticker, {})
    if isinstance(insider_raw, dict):
        insider_sum = insider_raw.get("summary", "") or ""
    else:
        insider_sum = str(insider_raw or "")

    # Sector — for sector-aware bands. Match the pipeline's lookup path
    sectors_map = data.get("sectors") or {}
    sector = sectors_map.get(ticker) if isinstance(sectors_map, dict) else None

    return dcf_t, scen_t, raw_fin, dcf_cal, insider_sum, sector


def _recompute_one_ticker_vgpm(data: dict, ticker: str) -> dict | None:
    """Run the new sector-aware _compute_vgpm against this ticker's data.
    Returns the new VGPM dict, or None if inputs were insufficient."""
    inputs = _build_vgpm_inputs(data, ticker)
    if inputs is None:
        return None
    dcf_t, scen_t, raw_fin, dcf_cal, insider_sum, sector = inputs

    # Late import — keep this module importable without pulling pdf_report
    # (which has reportlab dependencies that may not be present in all envs)
    from src.utils.pdf_report import _compute_vgpm
    try:
        return _compute_vgpm(
            dcf_ticker=dcf_t,
            scen_ticker=scen_t,
            raw_financials=raw_fin,
            dcf_cal=dcf_cal,
            insider_summary=insider_sum,
            sector=sector,
        )
    except Exception as exc:
        logger.warning(
            "backfill_vgpm: _compute_vgpm raised for %s: %s", ticker, exc
        )
        return None


def _vgpm_grade_summary(vgpm: dict | None) -> dict[str, str]:
    """Extract just dim → grade for diagnostic comparison."""
    if not isinstance(vgpm, dict):
        return {}
    return {
        dim: (data.get("grade") or "?")
        for dim, data in vgpm.items()
        if isinstance(data, dict)
    }


def _vgpm_changed(old: dict | None, new: dict | None) -> bool:
    """True iff any dim's score OR grade differs."""
    if not isinstance(old, dict) and not isinstance(new, dict):
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return True
    for dim in ("valuation", "growth", "profitability", "momentum"):
        o = (old.get(dim) or {})
        n = (new.get(dim) or {})
        if o.get("score") != n.get("score") or o.get("grade") != n.get("grade"):
            return True
    return False


def backfill_vgpm_for_runs(
    *,
    db_path: str | None = None,
    since_iso: str = DEFAULT_SINCE_ISO,
    dry_run: bool = True,
    sample_limit: int = MAX_GRADE_CHANGE_SAMPLES,
) -> dict[str, Any]:
    """Recompute VGPM for every web_run with run_at >= since_iso.

    Args:
      db_path:      explicit path to run_archive.db; defaults to env / module default
      since_iso:    ISO-8601 lower bound for run_at. Pre-cutoff rows untouched.
      dry_run:      when True, computes the new VGPM and reports diffs but
                    does NOT write back. Default True to make accidental
                    invocation safe.
      sample_limit: cap on detailed before/after samples in the return payload

    Returns a summary dict (see module docstring).
    """
    resolved_db = _resolve_db_path(db_path)
    if not Path(resolved_db).exists():
        return {
            "ok": False,
            "error": f"DB not found at {resolved_db}",
            "runs_examined": 0,
        }

    started_at = datetime.now(timezone.utc).isoformat()

    # Read rows. Cohort fetch logic is independent of write — we read the
    # rows we'll consider, decide what to update in-memory, then apply.
    try:
        # Always open read-write so we can persist on dry_run=False. The
        # uri=mode=ro form would block writes; not what we want.
        conn = sqlite3.connect(resolved_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, run_id, ticker, run_at, full_result_json
            FROM web_runs
            WHERE run_at >= ?
              AND full_result_json IS NOT NULL
            ORDER BY run_at ASC
            """,
            (since_iso,),
        ).fetchall()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "error": f"DB read failed: {exc}",
            "runs_examined": 0,
        }

    runs_examined = len(rows)
    runs_with_vgpm = 0
    runs_updated = 0
    tickers_updated = 0
    tickers_skipped = 0
    grade_changes: list[dict] = []
    errors: list[dict] = []
    updates_pending: list[tuple[int, str]] = []   # [(row_id, new_json_str), ...]

    for row in rows:
        try:
            payload = json.loads(row["full_result_json"])
        except (TypeError, ValueError) as exc:
            errors.append({
                "run_id": row["run_id"],
                "error":  f"json decode failed: {exc}",
            })
            continue

        # Find existing VGPM (may be at top level, under data, or both)
        top_vgpm  = payload.get("vgpm") if isinstance(payload.get("vgpm"), dict) else None
        data_dict = payload.get("data") if isinstance(payload.get("data"), dict) else None
        data_vgpm = data_dict.get("vgpm") if isinstance(data_dict, dict) and isinstance(data_dict.get("vgpm"), dict) else None
        existing_tickers = set((top_vgpm or {}).keys()) | set((data_vgpm or {}).keys())

        # If there's no data dict at all, can't recompute (no inputs)
        if not isinstance(data_dict, dict):
            continue

        # Use existing VGPM tickers as the canonical list when present;
        # otherwise fall back to dcf_range / scenario keys.
        if not existing_tickers:
            existing_tickers = set((data_dict.get("dcf_range") or {}).keys()) \
                             | set((data_dict.get("scenario_analysis") or {}).keys())

        if not existing_tickers:
            continue

        runs_with_vgpm += 1
        row_changed = False
        new_vgpm_map: dict[str, dict] = {}

        for ticker in existing_tickers:
            new_vgpm = _recompute_one_ticker_vgpm(data_dict, ticker)
            if new_vgpm is None:
                tickers_skipped += 1
                continue
            new_vgpm_map[ticker] = new_vgpm

            old_vgpm = (top_vgpm or {}).get(ticker) or (data_vgpm or {}).get(ticker)
            if _vgpm_changed(old_vgpm, new_vgpm):
                tickers_updated += 1
                row_changed = True
                if len(grade_changes) < sample_limit:
                    grade_changes.append({
                        "run_id":  row["run_id"],
                        "ticker":  ticker,
                        "run_at":  row["run_at"],
                        "before":  _vgpm_grade_summary(old_vgpm),
                        "after":   _vgpm_grade_summary(new_vgpm),
                    })

        if row_changed:
            runs_updated += 1
            # Write the new VGPM map to both surfaces (top-level + data nested)
            payload["vgpm"] = new_vgpm_map
            if isinstance(data_dict, dict):
                data_dict["vgpm"] = new_vgpm_map
            updates_pending.append((row["id"], json.dumps(payload)))

    # Apply updates if not dry-run
    if not dry_run and updates_pending:
        try:
            conn.executemany(
                "UPDATE web_runs SET full_result_json = ? WHERE id = ?",
                [(j, rid) for (rid, j) in updates_pending],
            )
            conn.commit()
        except sqlite3.Error as exc:
            errors.append({"error": f"batch update failed: {exc}"})
        finally:
            try:
                conn.close()
            except Exception:
                pass
    else:
        try:
            conn.close()
        except Exception:
            pass

    finished_at = datetime.now(timezone.utc).isoformat()

    return {
        "ok":                True,
        "dry_run":           dry_run,
        "since_iso":         since_iso,
        "db_path":           resolved_db,
        "started_at":        started_at,
        "finished_at":       finished_at,
        "runs_examined":     runs_examined,
        "runs_with_vgpm":    runs_with_vgpm,
        "runs_updated":      runs_updated,
        "tickers_updated":   tickers_updated,
        "tickers_skipped":   tickers_skipped,
        "grade_changes":     grade_changes,
        "grade_changes_capped_at": sample_limit,
        "errors":            errors,
    }

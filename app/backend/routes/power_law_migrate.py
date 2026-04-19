"""
Secret-gated one-shot migration: rescale stored power_law_json rows from the
legacy 0-2 dimension scale to the new 0-10 scale and recompute total_score
via the backend's Helmer-weighted mean.

Safe to re-run — legacy detection (every dim in [0, 2]) means rows already on
the 0-10 scale are skipped. Use ?tickers=PLTR,FRSH,D05.SI,ZM to restrict the
update to specific tickers.

Call once after Railway redeploys the new code:
    curl -X POST "https://<railway>/admin/rescore-power-law?secret=$DB_UPLOAD_SECRET"

Or target specific tickers:
    curl -X POST "https://<railway>/admin/rescore-power-law?secret=$DB_UPLOAD_SECRET&tickers=PLTR,FRSH,D05.SI,ZM"
"""
import json
import logging
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")

# Must match src/agents/analysis/power_law_agent.py POWER_LAW_WEIGHTS
WEIGHTS = {
    "scale_economies":  0.25,
    "network_effects":  0.20,
    "winner_take_most": 0.20,
    "switching_costs":  0.20,
    "data_ip_moat":     0.15,
}
DIM_KEYS = tuple(WEIGHTS.keys())


def _get_db_path() -> str:
    """Same resolution as db_upload.py — /data/run_archive.db on Railway."""
    return os.environ.get("RUN_ARCHIVE_PATH", "/data/run_archive.db")


def _coerce_int(v: Any, lo: int = 0, hi: int = 10) -> int | None:
    """Best-effort integer coercion clamped to [lo, hi]. None if not numeric."""
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def _is_legacy(dims: list[int]) -> bool:
    """All five dimensions in [0, 2] with at least one non-zero value."""
    return (
        len(dims) == 5
        and all(0 <= v <= 2 for v in dims)
        and max(dims) > 0
    )


def _compute_total(dims: dict[str, int]) -> int:
    """Weighted mean → integer in [0, 10] (mirrors power_law_agent._compute_total_score)."""
    total = sum(WEIGHTS[k] * float(dims.get(k, 0)) for k in WEIGHTS)
    return max(0, min(10, round(total)))


def _interpretation_for(score: int) -> str:
    if score >= 8: return "category king"
    if score >= 6: return "solid compounder"
    if score >= 4: return "average"
    return "commodity risk"


def _rescore_single_dict(
    pl: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Apply rescale + recompute to one power_law dict.

    Returns (new_dict_or_None, action, change_meta). `action` is one of:
      'rescale_and_recompute' — legacy 0-2 → 0-10
      'recompute_total'       — dims already 0-10 but total_score disagrees
      'skip_already_new'      — already on 0-10 scale and total agrees
      'skip_invalid'          — dims missing or non-numeric
    """
    raw_dims = [_coerce_int(pl.get(k), 0, 10) for k in DIM_KEYS]
    if any(v is None for v in raw_dims):
        return None, "skip_invalid", {"reason": "non-numeric dim", "dims": raw_dims}
    dims_clean: list[int] = [int(v) for v in raw_dims if v is not None]  # type: ignore[arg-type]
    old_total = pl.get("total_score")

    if _is_legacy(dims_clean):
        rescaled = [v * 5 for v in dims_clean]
        new_dims = {k: rescaled[i] for i, k in enumerate(DIM_KEYS)}
        new_total = _compute_total(new_dims)
        new_interp = _interpretation_for(new_total)
        new_pl = dict(pl)
        for k in DIM_KEYS:
            new_pl[k] = new_dims[k]
        new_pl["total_score"] = new_total
        new_pl["interpretation"] = new_interp
        return new_pl, "rescale_and_recompute", {
            "old_total": old_total, "new_total": new_total,
            "old_dims": dims_clean, "new_dims": rescaled,
        }

    # Already on 0-10 — recompute total via weighted mean for internal consistency.
    new_dims = {k: dims_clean[i] for i, k in enumerate(DIM_KEYS)}
    new_total = _compute_total(new_dims)
    if old_total == new_total:
        return None, "skip_already_new", {"total": new_total, "dims": list(new_dims.values())}
    new_interp = _interpretation_for(new_total)
    new_pl = dict(pl)
    new_pl["total_score"] = new_total
    new_pl["interpretation"] = new_interp
    return new_pl, "recompute_total", {
        "old_total": old_total, "new_total": new_total,
        "dims": list(new_dims.values()),
    }


@router.post("/admin/rescore-power-law")
async def rescore_power_law(
    secret: str = Query("", description="Must match DB_UPLOAD_SECRET env var"),
    tickers: str = Query("", description="Optional comma-separated ticker filter"),
    dry_run: bool = Query(False, description="If true, report what would change without writing"),
):
    """Rescale legacy 0-2 power_law analysis inside web_runs.full_result_json
    to 0-10 and recompute total_score via the Helmer-weighted mean.

    Also handles ticker_signals.power_law_json for self-hosted archives.
    """
    if not UPLOAD_SECRET or secret != UPLOAD_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing secret")

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        raise HTTPException(status_code=500, detail=f"DB not found at {db_path}")

    ticker_filter = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()

    scanned = 0
    updated = 0
    skipped_already_new = 0
    skipped_invalid = 0
    skipped_no_powerlaw = 0
    changes: list[dict[str, Any]] = []

    # ── web_runs.full_result_json (the path used by the web backend) ────────
    if ticker_filter:
        placeholders = ",".join("?" for _ in ticker_filter)
        web_rows = cur.execute(
            f"SELECT run_id, ticker, full_result_json FROM web_runs "
            f"WHERE full_result_json IS NOT NULL AND UPPER(ticker) IN ({placeholders})",
            ticker_filter,
        ).fetchall()
    else:
        web_rows = cur.execute(
            "SELECT run_id, ticker, full_result_json FROM web_runs "
            "WHERE full_result_json IS NOT NULL"
        ).fetchall()

    for run_id, ticker, full_json in web_rows:
        scanned += 1
        try:
            result = json.loads(full_json)
        except json.JSONDecodeError:
            skipped_invalid += 1
            continue
        if not isinstance(result, dict):
            skipped_invalid += 1
            continue

        # Look for power_law_analysis nested under data (pipeline structure) or at top.
        data_block = result.get("data") if isinstance(result.get("data"), dict) else None
        pl_root = None
        path_used = None
        if data_block and isinstance(data_block.get("power_law_analysis"), dict):
            pl_root = data_block["power_law_analysis"]
            path_used = "data.power_law_analysis"
        elif isinstance(result.get("power_law_analysis"), dict):
            pl_root = result["power_law_analysis"]
            path_used = "power_law_analysis"
        elif isinstance(result.get("power_law"), dict):
            pl_root = result["power_law"]
            path_used = "power_law"

        if pl_root is None:
            skipped_no_powerlaw += 1
            continue

        # pl_root may be a single dict or {ticker: dict}. Detect.
        run_any_changed = False
        per_run_changes: list[dict[str, Any]] = []

        def _maybe_update(target_dict: dict[str, Any]) -> None:
            nonlocal run_any_changed
            new_pl, action, meta = _rescore_single_dict(target_dict)
            per_run_changes.append({"action": action, **meta})
            if action == "skip_already_new":
                pass
            elif action == "skip_invalid":
                pass
            elif new_pl is not None:
                target_dict.clear()
                target_dict.update(new_pl)
                run_any_changed = True

        looks_like_ticker_map = all(isinstance(v, dict) for v in pl_root.values()) \
            and not any(k in pl_root for k in DIM_KEYS)
        if looks_like_ticker_map:
            for tk, tk_pl in list(pl_root.items()):
                if isinstance(tk_pl, dict):
                    _maybe_update(tk_pl)
        else:
            _maybe_update(pl_root)

        # Aggregate per-run result
        actions = [c["action"] for c in per_run_changes]
        if run_any_changed:
            updated += 1
            if not dry_run:
                cur.execute(
                    "UPDATE web_runs SET full_result_json = ? WHERE run_id = ?",
                    (json.dumps(result), run_id),
                )
            changes.append({
                "source": "web_runs",
                "path": path_used,
                "run_id": run_id,
                "ticker": ticker,
                "changes": per_run_changes,
            })
        else:
            if all(a == "skip_already_new" for a in actions):
                skipped_already_new += 1
            elif all(a == "skip_invalid" for a in actions):
                skipped_invalid += 1

    # ── ticker_signals.power_law_json (secondary path) ──────────────────────
    try:
        if ticker_filter:
            placeholders = ",".join("?" for _ in ticker_filter)
            ts_rows = cur.execute(
                f"SELECT id, ticker, run_id, power_law_json, power_law_score "
                f"FROM ticker_signals WHERE power_law_json IS NOT NULL "
                f"AND UPPER(ticker) IN ({placeholders})",
                ticker_filter,
            ).fetchall()
        else:
            ts_rows = cur.execute(
                "SELECT id, ticker, run_id, power_law_json, power_law_score "
                "FROM ticker_signals WHERE power_law_json IS NOT NULL"
            ).fetchall()

        for row_id, ticker, run_id, pl_json, _old_total in ts_rows:
            scanned += 1
            try:
                pl = json.loads(pl_json) if pl_json else {}
            except json.JSONDecodeError:
                skipped_invalid += 1
                continue
            if not isinstance(pl, dict):
                skipped_invalid += 1
                continue
            new_pl, action, meta = _rescore_single_dict(pl)
            if action == "skip_already_new":
                skipped_already_new += 1
                continue
            if action == "skip_invalid" or new_pl is None:
                skipped_invalid += 1
                continue
            if not dry_run:
                cur.execute(
                    "UPDATE ticker_signals SET power_law_json = ?, power_law_score = ? WHERE id = ?",
                    (json.dumps(new_pl), new_pl["total_score"], row_id),
                )
            updated += 1
            changes.append({
                "source": "ticker_signals", "id": row_id,
                "ticker": ticker, "run_id": run_id,
                "action": action, **meta,
            })
    except sqlite3.OperationalError:
        # Table missing or schema mismatch on this DB — ignore.
        pass

    if not dry_run:
        conn.commit()
    conn.close()

    return {
        "status": "dry_run" if dry_run else "ok",
        "db_path": db_path,
        "ticker_filter": ticker_filter or None,
        "scanned": scanned,
        "updated": updated,
        "skipped_already_new_scale": skipped_already_new,
        "skipped_invalid_json": skipped_invalid,
        "skipped_no_powerlaw_block": skipped_no_powerlaw,
        "changes": changes[:50],
        "changes_truncated": len(changes) > 50,
    }

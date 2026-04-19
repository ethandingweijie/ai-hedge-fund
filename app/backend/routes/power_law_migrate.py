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


@router.post("/admin/rescore-power-law")
async def rescore_power_law(
    secret: str = Query("", description="Must match DB_UPLOAD_SECRET env var"),
    tickers: str = Query("", description="Optional comma-separated ticker filter"),
    dry_run: bool = Query(False, description="If true, report what would change without writing"),
):
    """Rescale legacy 0-2 power_law_json rows in ticker_signals to 0-10 and
    recompute total_score via the Helmer-weighted mean."""
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

    # Pull all candidates with a non-null power_law_json, optionally filtered by ticker.
    if ticker_filter:
        placeholders = ",".join("?" for _ in ticker_filter)
        query = (
            f"SELECT id, ticker, run_id, power_law_json, power_law_score "
            f"FROM ticker_signals WHERE power_law_json IS NOT NULL "
            f"AND UPPER(ticker) IN ({placeholders})"
        )
        rows = cur.execute(query, ticker_filter).fetchall()
    else:
        rows = cur.execute(
            "SELECT id, ticker, run_id, power_law_json, power_law_score "
            "FROM ticker_signals WHERE power_law_json IS NOT NULL"
        ).fetchall()

    scanned = len(rows)
    updated = 0
    skipped_already_new = 0
    skipped_invalid = 0
    changes: list[dict[str, Any]] = []

    for row_id, ticker, run_id, pl_json, old_total in rows:
        try:
            data = json.loads(pl_json) if pl_json else {}
        except json.JSONDecodeError:
            skipped_invalid += 1
            continue
        if not isinstance(data, dict):
            skipped_invalid += 1
            continue

        raw_dims = [_coerce_int(data.get(k), 0, 10) for k in DIM_KEYS]
        if any(v is None for v in raw_dims):
            skipped_invalid += 1
            continue
        dims_clean: list[int] = [int(v) for v in raw_dims if v is not None]  # type: ignore[arg-type]

        if not _is_legacy(dims_clean):
            # Already on 0-10 scale — but we still want total_score to be the
            # new weighted mean (LLM may have given a different value).
            new_dims = {k: dims_clean[i] for i, k in enumerate(DIM_KEYS)}
            new_total = _compute_total(new_dims)
            if new_total == (old_total or 0) and data.get("total_score") == new_total:
                skipped_already_new += 1
                continue
            # total_score needs recompute but dims are already 0-10 — write back.
            new_interp = _interpretation_for(new_total)
            data["total_score"] = new_total
            data["interpretation"] = new_interp
            if not dry_run:
                cur.execute(
                    "UPDATE ticker_signals SET power_law_json = ?, power_law_score = ? WHERE id = ?",
                    (json.dumps(data), new_total, row_id),
                )
            updated += 1
            changes.append({
                "id": row_id, "ticker": ticker, "run_id": run_id,
                "action": "recompute_total",
                "old_total": old_total, "new_total": new_total,
                "dims": list(new_dims.values()),
            })
            continue

        # Legacy 0-2 → rescale to 0-10.
        rescaled = [v * 5 for v in dims_clean]
        new_dims = {k: rescaled[i] for i, k in enumerate(DIM_KEYS)}
        new_total = _compute_total(new_dims)
        new_interp = _interpretation_for(new_total)

        for k in DIM_KEYS:
            data[k] = new_dims[k]
        data["total_score"] = new_total
        data["interpretation"] = new_interp

        if not dry_run:
            cur.execute(
                "UPDATE ticker_signals SET power_law_json = ?, power_law_score = ? WHERE id = ?",
                (json.dumps(data), new_total, row_id),
            )
        updated += 1
        changes.append({
            "id": row_id, "ticker": ticker, "run_id": run_id,
            "action": "rescale_and_recompute",
            "old_total": old_total, "new_total": new_total,
            "old_dims": dims_clean, "new_dims": rescaled,
        })

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
        "changes": changes[:50],  # cap payload size
        "changes_truncated": len(changes) > 50,
    }

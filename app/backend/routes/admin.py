"""
Admin dashboard — browse cloud database tables with HTML UI.
Protected by DB_UPLOAD_SECRET env var.
"""
import os
import sys
import sqlite3
import json
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter()

ADMIN_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")


def _get_db_paths() -> dict[str, str]:
    return {
        "hedge_fund": os.environ.get("DATABASE_PATH", "hedge_fund.db"),
        "run_archive": os.environ.get("RUN_ARCHIVE_PATH", "run_archive.db"),
    }


@router.post("/admin/backfill-convergence-cap")
async def backfill_convergence_cap(
    secret: str = "",
    ticker: str = "",
    tickers: str = "",
    dry_run: bool = True,
):
    """Apply Convergence Velocity cap + bear floor to historical web_runs rows
    without re-running the full pipeline.

    Mirrors the live cap logic in src/agents/analysis/dcf_agent.py:
      - 12m_targets[scen] capped at spot + max_capture × (scen_iv − spot)
      - Bear floor: 12m_targets[bear] ≤ spot × 0.85 for high-SBC profiles
      - Updates dcf_range[ticker]['12m_targets'] AND
        scenario_analysis[ticker]['12m_targets_by_scenario'] AND
        re-blends scenario_analysis[ticker]['12m_price_target']
        (probability-weighted)

    Query params:
      secret    — DB_UPLOAD_SECRET (required)
      ticker    — single ticker filter (e.g. 'MDB')
      tickers   — CSV list (e.g. 'MDB,MNDY,NET') — overrides `ticker` if both given
      dry_run   — default True. When True, returns proposed changes without
                  writing. Pass dry_run=false to actually persist.
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    paths = _get_db_paths()
    db_path = paths.get("run_archive")
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="run_archive DB not found")

    fmp_key = (os.environ.get("FMP_API_KEY") or "").strip()
    if not fmp_key:
        raise HTTPException(status_code=400, detail="FMP_API_KEY not configured on server")

    # Mirror of _HIGH_SBC_PROFILES in src/agents/analysis/dcf_agent.py.
    # Kept inline to avoid heavy import path; update both if either changes.
    HIGH_SBC = {
        "Growth SaaS",
        "Hyper-Growth Platform",
        "High-Growth Tech / AI",
        "Cybersecurity / Mission-Critical SaaS",
    }

    # Resolve filter set
    target_tickers: list[str] = []
    if tickers:
        target_tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    elif ticker:
        target_tickers = [ticker.strip().upper()]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if target_tickers:
        placeholders = ",".join("?" * len(target_tickers))
        rows = conn.execute(
            f"SELECT run_id, run_at, ticker, full_result_json FROM web_runs "
            f"WHERE ticker IN ({placeholders}) "
            f"AND full_result_json IS NOT NULL "
            f"AND json_extract(full_result_json, '$.checkpoint') IS NULL "
            f"ORDER BY run_at DESC",
            target_tickers,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT run_id, run_at, ticker, full_result_json FROM web_runs "
            "WHERE full_result_json IS NOT NULL "
            "AND json_extract(full_result_json, '$.checkpoint') IS NULL "
            "ORDER BY run_at DESC"
        ).fetchall()

    summary = {
        "dry_run": dry_run,
        "tickers_filter": target_tickers or "all",
        "rows_processed": 0,
        "rows_updated": 0,
        "rows_skipped_no_dcf": 0,
        "rows_skipped_spot_failed": 0,
        "rows_skipped_no_change": 0,
        "changes": [],
    }

    import requests
    # Cache spot prices per ticker so a single backfill run doesn't fan out
    # one FMP call per row when the same ticker has many runs.
    spot_cache: dict[str, float | None] = {}

    def _get_spot(t: str) -> float | None:
        if t in spot_cache:
            return spot_cache[t]
        try:
            r = requests.get(
                "https://financialmodelingprep.com/stable/quote",
                params={"symbol": t, "apikey": fmp_key},
                timeout=10,
            )
            if r.status_code != 200:
                spot_cache[t] = None
                return None
            data = r.json()
            if data and isinstance(data, list) and data:
                p = float(data[0].get("price") or 0)
                spot_cache[t] = p if p > 0 else None
                return spot_cache[t]
        except Exception:
            pass
        spot_cache[t] = None
        return None

    for row in rows:
        summary["rows_processed"] += 1
        run_id = row["run_id"]
        t = row["ticker"]

        try:
            full = json.loads(row["full_result_json"])
        except Exception:
            continue

        data = full.get("data", {}) or {}
        dcf_range_map = data.get("dcf_range", {}) or {}
        dcf_t = dcf_range_map.get(t) or {}
        if not dcf_t:
            summary["rows_skipped_no_dcf"] += 1
            continue

        bear_iv = (dcf_t.get("bear") or {}).get("intrinsic_value")
        base_iv = (dcf_t.get("base") or {}).get("intrinsic_value")
        bull_iv = (dcf_t.get("bull") or {}).get("intrinsic_value")
        twelve = dcf_t.get("12m_targets") or {}
        bear_pt = twelve.get("bear")
        base_pt = twelve.get("base")
        bull_pt = twelve.get("bull")

        if not all(isinstance(v, (int, float)) and v > 0 for v in
                   (bear_iv, base_iv, bull_iv, bear_pt, base_pt, bull_pt)):
            summary["rows_skipped_no_dcf"] += 1
            continue

        spot = _get_spot(t)
        if spot is None or spot <= 0:
            summary["rows_skipped_spot_failed"] += 1
            continue

        # Profile resolution waterfall:
        #   1. data.profile_names[ticker]   — per-ticker map
        #   2. data.profile_name            — singular, primary ticker
        #   3. TICKER_SECTOR_LOOKUP[ticker] — fallback for older runs that
        #      predate profile_name persistence (e.g. yesterday's MNDY run
        #      stored '' even though MNDY is configured as Growth SaaS)
        profile_name = (data.get("profile_names") or {}).get(t) or data.get("profile_name") or ""
        if not profile_name:
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get(t.upper())
                if _entry and len(_entry) >= 2:
                    profile_name = _entry[1] or ""
            except Exception:
                pass
        is_high_sbc = profile_name in HIGH_SBC
        max_capture = 0.20 if is_high_sbc else 0.35

        new_targets: dict[str, float] = {}
        any_changed = False
        for scen, scen_iv, scen_pt in [
            ("bear", bear_iv, bear_pt),
            ("base", base_iv, base_pt),
            ("bull", bull_iv, bull_pt),
        ]:
            new_pt = float(scen_pt)
            # Convergence Velocity cap (only when IV is above spot — gap exists)
            if scen_iv > spot and scen_pt > spot:
                gap = scen_iv - spot
                cap = spot + max_capture * gap
                if scen_pt > cap:
                    new_pt = round(cap, 2)
            # Bear floor for high-SBC names
            if scen == "bear" and is_high_sbc:
                bear_floor = spot * 0.85
                if new_pt > bear_floor:
                    new_pt = round(bear_floor, 2)
            if new_pt != scen_pt:
                any_changed = True
            new_targets[scen] = new_pt

        if not any_changed:
            summary["rows_skipped_no_change"] += 1
            continue

        # Re-blend probability-weighted 12m PT (same formula as scenario_agent.py:243)
        scen_map = (data.get("scenario_analysis") or {}).get(t) or {}
        bull_p = (scen_map.get("bull") or {}).get("probability") or 0.25
        base_p = (scen_map.get("base") or {}).get("probability") or 0.50
        bear_p = (scen_map.get("bear") or {}).get("probability") or 0.25
        # Snapshot OLD blended BEFORE we overwrite scen_map (was showing identical
        # old/new in dry-run output otherwise)
        old_blended_pt = scen_map.get("12m_price_target")
        new_blended_pt = round(
            new_targets["bull"] * bull_p
            + new_targets["base"] * base_p
            + new_targets["bear"] * bear_p,
            2,
        )

        # Update structures in place
        dcf_t["12m_targets"] = new_targets
        dcf_range_map[t] = dcf_t
        data["dcf_range"] = dcf_range_map
        if scen_map:
            scen_map["12m_targets_by_scenario"] = new_targets
            scen_map["12m_price_target"] = new_blended_pt
            sa_map = data.get("scenario_analysis", {}) or {}
            sa_map[t] = scen_map
            data["scenario_analysis"] = sa_map
        full["data"] = data

        if not dry_run:
            conn.execute(
                "UPDATE web_runs SET full_result_json = ? WHERE run_id = ?",
                (json.dumps(full), run_id),
            )

        summary["rows_updated"] += 1
        summary["changes"].append({
            "run_id": run_id,
            "run_at": row["run_at"],
            "ticker": t,
            "spot": spot,
            "profile": profile_name,
            "high_sbc": is_high_sbc,
            "max_capture": max_capture,
            "12m_pt": {
                "bear": {"old": bear_pt, "new": new_targets["bear"]},
                "base": {"old": base_pt, "new": new_targets["base"]},
                "bull": {"old": bull_pt, "new": new_targets["bull"]},
                "blended_old": scen_map.get("12m_price_target") if scen_map else None,
                "blended_new": new_blended_pt,
            },
        })

    if not dry_run:
        conn.commit()
    conn.close()
    return summary


@router.delete("/admin/row")
async def delete_row(secret: str = "", db: str = "run_archive", table: str = "", key_col: str = "", key_val: str = ""):
    """Delete a single row by primary key."""
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if not table or not key_col or not key_val:
        raise HTTPException(status_code=400, detail="table, key_col, key_val required")

    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found")

    # Whitelist allowed key columns to prevent SQL injection
    allowed_keys = ["run_id", "id", "rowid", "ticker", "email", "provider", "cache_key"]
    if key_col not in allowed_keys:
        raise HTTPException(status_code=400, detail=f"key_col must be one of: {allowed_keys}")

    # For web_runs, use the cascading delete that also cleans up
    # runs, ticker_signals, agent_signals, and research_summary_cache
    if table == "web_runs" and key_col == "run_id":
        from app.backend.services.analysis_service import delete_run
        success = delete_run(key_val)
        # Also clean research_summary_cache in hedge_fund.db
        try:
            hf_path = _get_db_paths().get("hedge_fund")
            if hf_path and os.path.exists(hf_path):
                hf_conn = sqlite3.connect(hf_path)
                hf_conn.execute("DELETE FROM research_summary_cache WHERE run_id = ?", (key_val,))
                hf_conn.commit()
                hf_conn.close()
        except Exception:
            pass
        return {"deleted": 1 if success else 0, "table": table, "key": f"{key_col}={key_val}", "cascaded": True}

    # For other tables, simple row delete
    conn = sqlite3.connect(db_path)
    cur = conn.execute(f"DELETE FROM [{table}] WHERE [{key_col}] = ?", (key_val,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return {"deleted": deleted, "table": table, "key": f"{key_col}={key_val}"}


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(secret: str = "", db: str = "run_archive", table: str = "", limit: int = 50, offset: int = 0):
    """HTML admin dashboard — one link to see everything."""
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all tables
    tables_info = []
    all_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for (name,) in all_tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
        tables_info.append({"name": name, "count": count})

    # Default to web_runs if no table selected
    if not table:
        table = "web_runs"

    # Validate table
    table_names = [t["name"] for t in tables_info]
    if table not in table_names:
        table = table_names[0] if table_names else ""

    # Determine primary key column for delete button
    pk_col = "rowid"
    if table == "web_runs":
        pk_col = "run_id"
    elif table in ("watchlist", "users", "api_keys", "hedge_fund_flows", "hedge_fund_flow_runs"):
        pk_col = "id"

    # Query rows
    rows = []
    columns = []
    total = 0
    if table:
        cols_info = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
        columns = [r["name"] for r in cols_info]

        # For web_runs, exclude full_result_json (too large)
        display_cols = [c for c in columns if c != "full_result_json"]
        # Include rowid explicitly for tables that use it as pk_col (not in PRAGMA columns)
        if pk_col == "rowid" and "rowid" not in display_cols:
            display_cols = ["rowid"] + display_cols
        col_list = ", ".join(display_cols)

        total = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]

        # Determine sort order
        order = "run_at DESC" if "run_at" in columns else "rowid DESC"
        if "added_at" in columns:
            order = "added_at DESC"
        if "cached_at" in columns:
            order = "cached_at DESC"

        raw_rows = conn.execute(
            f"SELECT {col_list} FROM [{table}] ORDER BY {order} LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        rows = [dict(r) for r in raw_rows]
        columns = display_cols

    conn.close()

    # Build HTML
    base_url = f"/admin/dashboard?secret={secret}"

    # DB selector tabs
    db_tabs = ""
    for db_name in ["run_archive", "hedge_fund"]:
        active = "background:#2563eb;color:white;" if db_name == db else "background:#334155;color:#94a3b8;"
        db_tabs += f'<a href="{base_url}&db={db_name}" style="padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;{active}">{db_name}</a> '

    # Table tabs
    table_tabs = ""
    for t in tables_info:
        tname = t["name"]
        tcount = t["count"]
        active = "background:#1e40af;color:white;" if tname == table else "background:#1e293b;color:#64748b;border:1px solid #334155;"
        table_tabs += f'<a href="{base_url}&db={db}&table={tname}" style="padding:4px 12px;border-radius:4px;text-decoration:none;font-size:11px;font-weight:500;{active}">{tname} ({tcount})</a> '

    # Table rows
    table_html = ""
    if rows:
        # Header
        table_html += "<tr>"
        table_html += '<th style="padding:8px 6px;border-bottom:2px solid #334155;font-size:11px;color:#94a3b8;width:30px;"></th>'
        for col in columns:
            table_html += f'<th style="padding:8px 12px;text-align:left;border-bottom:2px solid #334155;font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;white-space:nowrap;">{col}</th>'
        table_html += "</tr>"

        # Rows
        for i, row in enumerate(rows):
            bg = "#0f172a" if i % 2 == 0 else "#1e293b"
            pk_val = row.get(pk_col, "")
            row_id = f"row-{i}"
            table_html += f'<tr id="{row_id}" style="background:{bg};">'

            # Delete button
            table_html += f'''<td style="padding:4px 6px;border-bottom:1px solid #1e293b;text-align:center;">
                <button onclick="deleteRow('{db}', '{table}', '{pk_col}', '{pk_val}', '{row_id}')"
                    style="background:none;border:1px solid #ef4444;color:#ef4444;border-radius:4px;padding:2px 6px;font-size:10px;cursor:pointer;opacity:0.6;"
                    onmouseover="this.style.opacity='1';this.style.background='#ef4444';this.style.color='white';"
                    onmouseout="this.style.opacity='0.6';this.style.background='none';this.style.color='#ef4444';"
                    title="Delete this row">&#x2715;</button>
            </td>'''

            for col in columns:
                val = row.get(col, "")
                display = str(val) if val is not None else ""
                if len(display) > 80:
                    display = display[:77] + "..."
                style = "padding:6px 12px;font-size:12px;color:#e2e8f0;border-bottom:1px solid #1e293b;white-space:nowrap;"
                if col == "ticker":
                    style += "font-weight:700;color:#60a5fa;"
                elif col == "final_action":
                    colors = {"BUY": "#34d399", "SELL": "#f87171", "HOLD": "#fbbf24", "SHORT": "#fb923c"}
                    style += f"font-weight:700;color:{colors.get(str(val), '#94a3b8')};"
                elif col == "user_id" and val:
                    style += "color:#a78bfa;"
                table_html += f'<td style="{style}">{display}</td>'
            table_html += "</tr>"

    # Pagination
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    pagination = f"""
        <div style="display:flex;align-items:center;gap:12px;margin-top:12px;">
            <span style="font-size:11px;color:#64748b;">Showing {offset+1}-{min(offset+limit, total)} of {total}</span>
            {'<a href="' + base_url + '&db=' + db + '&table=' + table + '&offset=' + str(prev_offset) + '&limit=' + str(limit) + '" style="color:#60a5fa;font-size:12px;text-decoration:none;">&larr; Prev</a>' if offset > 0 else ''}
            {'<a href="' + base_url + '&db=' + db + '&table=' + table + '&offset=' + str(next_offset) + '&limit=' + str(limit) + '" style="color:#60a5fa;font-size:12px;text-decoration:none;">Next &rarr;</a>' if next_offset < total else ''}
        </div>
    """

    html = f"""<!DOCTYPE html>
<html><head>
<title>AI Hedge Fund — Admin Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
    <h1 style="font-size:20px;margin:0 0 16px 0;color:white;">AI Hedge Fund — Database</h1>

    <div style="margin-bottom:12px;display:flex;gap:8px;">{db_tabs}</div>
    <div style="margin-bottom:16px;display:flex;flex-wrap:wrap;gap:6px;">{table_tabs}</div>

    <div style="overflow-x:auto;border-radius:8px;border:1px solid #334155;">
        <table style="border-collapse:collapse;width:100%;min-width:600px;">
            {table_html}
        </table>
    </div>
    {pagination}

    <div style="margin-top:20px;font-size:10px;color:#475569;">
        Cloud DB path: {paths.get(db, '?')} | Tables: {len(tables_info)}
    </div>

    <script>
    async function deleteRow(db, table, keyCol, keyVal, rowId) {{
        if (!confirm('Delete row ' + keyCol + '=' + keyVal + ' from ' + table + '?')) return;
        try {{
            const res = await fetch(
                '/admin/row?secret={secret}&db=' + db + '&table=' + table + '&key_col=' + keyCol + '&key_val=' + encodeURIComponent(keyVal),
                {{ method: 'DELETE' }}
            );
            const data = await res.json();
            if (data.deleted > 0) {{
                document.getElementById(rowId).style.display = 'none';
            }} else {{
                alert('Row not found');
            }}
        }} catch (e) {{
            alert('Delete failed: ' + e.message);
        }}
    }}
    </script>
</body></html>"""

    return HTMLResponse(content=html)


# ── REIT breakdown backfill ────────────────────────────────────────────────
# One-shot migration endpoint that re-derives dcf_range.reit_breakdown for
# archived REIT runs created before commit 2d4843b (which first emitted the
# field). Runs the same logic as scripts/backfill_reit_breakdown.py but
# inside the already-running backend process, so it automatically targets
# the correct database path (Railway persistent volume) and uses the env's
# FMP_API_KEY. Remove once the backfill is complete.

@router.post("/admin/backfill-reit-breakdown")
async def backfill_reit_breakdown(
    secret: str = "",
    ticker: str = "",
    dry_run: bool = True,
    force: bool = False,
):
    """
    Retroactively populate reit_breakdown on archived REIT ticker_signals rows.

    Query params:
      secret   — DB_UPLOAD_SECRET (required)
      ticker   — optional, limit to one ticker (e.g. DLR)
      dry_run  — true (default) shows what would change without writing
      force    — true re-derives even when reit_breakdown already exists

    Example:
      curl -X POST 'https://BACKEND/admin/backfill-reit-breakdown?secret=XXX&ticker=DLR&dry_run=true'
      curl -X POST 'https://BACKEND/admin/backfill-reit-breakdown?secret=XXX&ticker=DLR&dry_run=false'
      curl -X POST 'https://BACKEND/admin/backfill-reit-breakdown?secret=XXX&dry_run=false'   # all REITs
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Ensure repo root is on sys.path so `scripts.backfill_reit_breakdown` resolves.
    # When Railway runs the app with the repo root as WORKDIR this is a no-op;
    # when uvicorn is started from app/backend it needs to be added.
    _REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    try:
        from scripts.backfill_reit_breakdown import backfill
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot import backfill module: {exc}")

    try:
        result = backfill(
            dry_run=dry_run,
            target_ticker=ticker.upper() if ticker else None,
            force=force,
        )
    except Exception as exc:
        logger.exception("backfill_reit_breakdown failed")
        raise HTTPException(status_code=500, detail=f"Backfill failed: {exc}")

    # Redact the DB path from response — it's an internal filesystem path
    result.pop("db_path", None)
    return result


# ── Bank breakdown backfill ────────────────────────────────────────────────
# Mirror of the REIT backfill endpoint, targeting bank_breakdown. Uses the
# same bank ticker whitelist (_BANK_PROFILE_CALIBRATION entries) as the
# scripts/backfill_bank_breakdown.py CLI.

@router.post("/admin/backfill-bank-breakdown")
async def backfill_bank_breakdown(
    secret: str = "",
    ticker: str = "",
    dry_run: bool = True,
    force: bool = False,
):
    """
    Retroactively populate bank_breakdown on archived bank runs.

    Query params (same as /admin/backfill-reit-breakdown):
      secret, ticker, dry_run, force

    Example:
      curl -X POST 'https://BACKEND/admin/backfill-bank-breakdown?secret=XXX&ticker=JPM&dry_run=true'
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    _REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    try:
        from scripts.backfill_bank_breakdown import backfill as _bank_backfill
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot import bank backfill: {exc}")

    try:
        result = _bank_backfill(
            dry_run=dry_run,
            target_ticker=ticker.upper() if ticker else None,
            force=force,
        )
    except Exception as exc:
        logger.exception("backfill_bank_breakdown failed")
        raise HTTPException(status_code=500, detail=f"Backfill failed: {exc}")

    result.pop("db_path", None)
    return result


# ── Backfill profile_name (v2.0.2 sub-sector column) ─────────────────────────
# Populates the new profile_name column (and inner full_result_json.data.
# profile_name field) on historic web_runs rows. Runs before /admin/reextract-
# metrics so the sector-extractor gate can fire correctly on pre-v2.0 runs.

@router.post("/admin/backfill-profile-name")
async def backfill_profile_name(
    secret: str = "",
    ticker: str = "",
    dry_run: bool = True,
    force: bool = False,
):
    """
    Backfill profile_name column + inner full_result_json.data.profile_name
    on historic web_runs rows archived before the strategic_router profile
    pre-classification feature (v2.0) landed.

    Query params:
      secret   — DB_UPLOAD_SECRET (required)
      ticker   — optional: limit to one ticker
      dry_run  — true (default) shows what would change without writing
      force    — true re-derives even when profile_name is already set

    Resolution tree (first non-empty wins):
      1. state.data.profile_name
      2. state.data.profile_names[ticker]
      3. TICKER_SECTOR_LOOKUP[ticker] — canonical fallback

    Examples:
      POST /admin/backfill-profile-name?secret=X&dry_run=true
      POST /admin/backfill-profile-name?secret=X&ticker=DDOG&dry_run=false
      POST /admin/backfill-profile-name?secret=X&force=true&dry_run=false
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    _REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    try:
        from scripts.backfill_profile_name import backfill
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot import backfill: {exc}")

    try:
        result = backfill(
            dry_run=dry_run,
            target_ticker=ticker.upper() if ticker else None,
            force=force,
        )
    except Exception as exc:
        logger.exception("backfill_profile_name failed")
        raise HTTPException(status_code=500, detail=f"Backfill failed: {exc}")

    # Strip per-row detail from the summary response when there are many
    # rows — keeps response payload reasonable. Caller can still see
    # aggregates (by_profile, by_source, updated count).
    if len(result.get("rows", [])) > 50:
        result["rows"] = result["rows"][:50]
        result["rows_truncated"] = True

    return result


# ── Re-extract metrics (v2.0.1 _parse_llm_json recovery) ──────────────────────
# Re-runs the LLM extractor chain against EXISTING stored deep research in
# web_runs.full_result_json without triggering a fresh pipeline run. Use
# case: the v2.0.1 parser fix (commit 60489d1) recovers Qwen preamble-
# wrapped extractor responses the old parser silently dropped — this
# endpoint retrofits historic runs so the frontend sees the new fields
# without re-running the expensive research pipeline.

@router.post("/admin/reextract-metrics")
async def reextract_metrics(
    secret: str = "",
    ticker: str = "",
    tickers: str = "",
    run_id: str = "",
    limit: int = 1,
    dry_run: bool = True,
    verbose: bool = False,
    provider: str = "auto",
):
    """
    Re-run LLM extractors against stored deep research for one or more runs.

    Query params:
      secret   — DB_UPLOAD_SECRET (required)
      run_id   — target a specific web_runs.run_id UUID
      ticker   — process last N runs for one ticker (e.g. DDOG)
      tickers  — comma-separated tickers (e.g. DDOG,SNOW)
      limit    — per-ticker run count when using ticker/tickers (default 1)
      dry_run  — true (default) shows diff without writing; false writes

    Exactly one of {run_id, ticker, tickers} must be provided.

    Examples:
      POST /admin/reextract-metrics?secret=XXX&ticker=DDOG&dry_run=true
      POST /admin/reextract-metrics?secret=XXX&tickers=DDOG,SNOW&dry_run=false
      POST /admin/reextract-metrics?secret=XXX&run_id=abc-123&dry_run=false
    """
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Mutual exclusion
    specified = sum(1 for x in (run_id, ticker, tickers) if x)
    if specified != 1:
        raise HTTPException(
            status_code=400,
            detail="Exactly one of run_id, ticker, tickers must be provided",
        )

    _REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    try:
        from src.memory.reextract_metrics import (
            reextract_by_ticker,
            reextract_for_run,
        )
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot import reextract module: {exc}")

    # Validate provider
    if provider not in {"auto", "qwen", "anthropic"}:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of 'auto', 'qwen', 'anthropic' (got {provider!r})",
        )

    try:
        if run_id:
            results = [reextract_for_run(run_id, dry_run=dry_run, verbose=verbose, provider=provider)]
        elif ticker:
            results = reextract_by_ticker(ticker, dry_run=dry_run, limit=limit,
                                           verbose=verbose, provider=provider)
        else:
            # tickers: comma-separated
            _list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
            results = []
            for t in _list:
                results.extend(reextract_by_ticker(
                    t, dry_run=dry_run, limit=limit,
                    verbose=verbose, provider=provider,
                ))
    except Exception as exc:
        logger.exception("reextract_metrics failed")
        raise HTTPException(status_code=500, detail=f"Re-extract failed: {exc}")

    # Summary
    ok       = sum(1 for r in results if r.get("ok"))
    updated  = sum(1 for r in results if r.get("updated"))
    would_up = sum(1 for r in results if r.get("would_update"))

    return {
        "dry_run": dry_run,
        "count": len(results),
        "succeeded": ok,
        "updated": updated,
        "would_update": would_up,
        "results": results,
    }

"""
Admin dashboard — browse cloud database tables with HTML UI.
Protected by DB_UPLOAD_SECRET env var.
"""
import os
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

    # Query rows
    rows = []
    columns = []
    total = 0
    if table:
        cols_info = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
        columns = [r["name"] for r in cols_info]

        # For web_runs, exclude full_result_json (too large)
        display_cols = [c for c in columns if c != "full_result_json"]
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
        active = "background:#1e40af;color:white;" if t["name"] == table else "background:#1e293b;color:#64748b;border:1px solid #334155;"
        table_tabs += f'<a href="{base_url}&db={db}&table={t[\"name\"]}" style="padding:4px 12px;border-radius:4px;text-decoration:none;font-size:11px;font-weight:500;{active}">{t["name"]} ({t["count"]})</a> '

    # Table rows
    table_html = ""
    if rows:
        # Header
        table_html += "<tr>"
        for col in columns:
            table_html += f'<th style="padding:8px 12px;text-align:left;border-bottom:2px solid #334155;font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;white-space:nowrap;">{col}</th>'
        table_html += "</tr>"

        # Rows
        for i, row in enumerate(rows):
            bg = "#0f172a" if i % 2 == 0 else "#1e293b"
            table_html += f'<tr style="background:{bg};">'
            for col in columns:
                val = row.get(col, "")
                # Truncate long values
                display = str(val) if val is not None else ""
                if len(display) > 80:
                    display = display[:77] + "..."
                # Color-code certain values
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
</body></html>"""

    return HTMLResponse(content=html)


# Keep JSON endpoints for programmatic access
@router.get("/admin/db")
async def list_tables(secret: str = "", db: str = "run_archive"):
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found")
    conn = sqlite3.connect(db_path)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    result = []
    for (name,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info([{name}])").fetchall()]
        result.append({"table": name, "rows": count, "columns": cols})
    conn.close()
    return {"database": db, "path": db_path, "tables": result}


@router.get("/admin/db/query")
async def query_table(
    secret: str = "", db: str = "run_archive", table: str = "web_runs",
    limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
    order_by: str = "rowid DESC", columns: str = "",
):
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    table_names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if table not in table_names:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found")
    if columns:
        col_list = ", ".join(c.strip() for c in columns.split(","))
    else:
        col_list = "*"
    if col_list == "*" and table == "web_runs":
        all_cols = [row[1] for row in conn.execute(f"PRAGMA table_info([{table}])").fetchall()]
        col_list = ", ".join(c for c in all_cols if c != "full_result_json")
    total = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    allowed_orders = ["rowid DESC", "rowid ASC", "run_at DESC", "run_at ASC",
                      "ticker ASC", "ticker DESC", "added_at DESC", "created_at DESC", "cached_at DESC"]
    if order_by not in allowed_orders:
        order_by = "rowid DESC"
    rows = conn.execute(f"SELECT {col_list} FROM [{table}] ORDER BY {order_by} LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return {"database": db, "table": table, "total": total, "offset": offset, "limit": limit, "rows": result}

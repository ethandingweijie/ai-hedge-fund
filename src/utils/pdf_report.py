"""
Auto PDF report — Industry Intelligence Brief + full Result Summary.

No truncation: every field is written verbatim from the pipeline result dict.
A post-generation validation pass confirms no trailing '...' or broken
sentences remain in the collected text.

Usage:
    from src.utils.pdf_report import generate_pdf_report
    path = generate_pdf_report(result)          # auto-named in cwd
    path = generate_pdf_report(result, "out.pdf")
"""

import html
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

from src.utils.company_name import fetch_company_name as _fetch_company_name_shared

# Dual-mode DB layer — production run records live in Postgres.
from src.data import db as _db

# Project root = two levels up from this file (src/utils/pdf_report.py)
_PROJECT_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPORTS_FOLDER = os.path.join(_PROJECT_ROOT, "Generated Reports")
_ARCHIVE_DB     = os.path.join(_PROJECT_ROOT, "src", "data", "run_archive.db")

# Sentinel prefix written by specialist.py when brief_text is empty
_BRIEF_ERROR_PREFIX = "[Industry brief generation incomplete"


def _fetch_db_brief(tickers: list[str]) -> str:
    """
    Query the run archive for the most recent deep_research_text that covers
    all requested tickers.  Returns "" if not found or DB unavailable.

    Dual-mode: production run records live in Postgres (run_archive.save_run
    writes via src.data.db), so reading only the SQLite file would never see
    them.  Locally (no DATABASE_URL) this falls back to the SQLite file.
    """
    if not tickers:
        return ""
    sql = (
        "SELECT tickers, deep_research_text FROM runs "
        "WHERE deep_research_text IS NOT NULL "
        "ORDER BY run_at DESC LIMIT 50"
    )
    try:
        if _db.is_postgres():
            rows = _db.query(sql)
        else:
            if not os.path.exists(_ARCHIVE_DB):
                return ""
            conn = sqlite3.connect(_ARCHIVE_DB)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql).fetchall()
            finally:
                conn.close()
        import json
        for row in rows:
            try:
                stored = json.loads(row["tickers"] or "[]")
            except (json.JSONDecodeError, TypeError):
                stored = []
            if all(t in stored for t in tickers):
                return row["deep_research_text"] or ""
        return ""
    except Exception:
        return ""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    FrameBreak,
    HRFlowable,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ── Company name lookup (FMP /stable/profile, cached) ──────────────────────────
# _fetch_company_name is now provided by src.utils.company_name (shared with
# deep_research.py and strategic_router.py to ensure consistent name resolution).
_fetch_company_name = _fetch_company_name_shared


# ── ANSI strip ─────────────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(text) -> str:
    """Remove ANSI escape codes, strip whitespace, and escape XML entities.

    ReportLab Paragraph uses a mini XML parser — bare <, >, & in LLM-generated
    text corrupt the PDF and cause viewer crashes (Windows 0xc06d007e).
    html.escape() converts them to &lt; &gt; &amp; which ReportLab renders
    correctly as the original characters.
    quote=False: single quotes must NOT be escaped to &#x27; because ReportLab's
    XML parser does not decode hex numeric character references, so they render
    as literal text (e.g. "Burry&#x27;s" instead of "Burry's").
    """
    cleaned = _ANSI_RE.sub("", str(text or "")).strip()
    return html.escape(cleaned, quote=False)


# ── Colour palette ─────────────────────────────────────────────────────────────
C_NAVY  = colors.HexColor("#0d1b2a")
C_BLUE  = colors.HexColor("#1565c0")
C_GREEN = colors.HexColor("#1b5e20")
C_RED   = colors.HexColor("#b71c1c")
C_AMBER = colors.HexColor("#e65100")
C_GREY  = colors.HexColor("#424242")
C_LGREY = colors.HexColor("#e0e0e0")
C_PALE  = colors.HexColor("#f5f5f5")

# ── Price currency (per report) ────────────────────────────────────────────────
# Per-share figures (price, targets, intrinsic values) are in the listing's
# trading currency: an ANTA report is in HK$, not $. Set once per report from
# the valuation's currency; every per-share amount renders through _cs().
# Statement amounts (key financials) carry their own currency label instead.
_CCY_SYMBOLS = {"USD": "$", "HKD": "HK$", "SGD": "S$", "CNY": "RMB ", "CNH": "RMB ",
                "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5", "KRW": "KRW ",
                "TWD": "NT$", "AUD": "A$", "CAD": "C$", "INR": "INR "}
_PX_SYM = "$"


def _cs() -> str:
    return _PX_SYM


def _set_price_currency(ccy) -> None:
    global _PX_SYM
    code = str(ccy or "USD").upper()
    _PX_SYM = _CCY_SYMBOLS.get(code, f"{code} ")

# ── Helper: bold text for table header cells (light-gray background) ───────────
# All data table headers now use C_PALE background with dark bold text.
# The sensitivity heatmap is the only table that keeps navy — it uses _wh_s()
# defined locally inside _sensitivity_table().
def _wh(txt: str) -> str:
    return f'<b>{txt}</b>'


# ── Style registry ─────────────────────────────────────────────────────────────
def _build_styles():
    base = getSampleStyleSheet()

    def _add(name, **kw):
        base.add(ParagraphStyle(name=name, **kw))

    _add("RptTitle",
         fontName="Helvetica-Bold", fontSize=16, leading=20,
         textColor=C_NAVY, spaceAfter=3)
    _add("RptSubtitle",
         fontName="Helvetica", fontSize=8, leading=11,
         textColor=C_GREY, spaceAfter=6)
    _add("RptSection",          # now used only for bold section labels (no backColor)
         fontName="Helvetica-Bold", fontSize=9.5, leading=12,
         textColor=C_NAVY, spaceAfter=2, spaceBefore=8)
    _add("RptSubsection",
         fontName="Helvetica-Bold", fontSize=9, leading=11,
         textColor=C_BLUE, spaceAfter=2, spaceBefore=6)
    _add("RptBody",
         fontName="Helvetica", fontSize=8, leading=11,
         textColor=colors.black, spaceAfter=2)
    _add("RptLabel",
         fontName="Helvetica-Bold", fontSize=7.5, leading=10,
         textColor=C_NAVY)
    _add("RptValue",
         fontName="Helvetica", fontSize=7.5, leading=10,
         textColor=colors.black)
    _add("RptSource",           # source/footnote attribution line
         fontName="Helvetica-Oblique", fontSize=7, leading=9,
         textColor=C_GREY, spaceAfter=4, spaceBefore=4)
    _add("RptPriceLine",        # compact price data line under title
         fontName="Helvetica", fontSize=8, leading=10,
         textColor=C_GREY, spaceAfter=4)
    # Executive summary block styles
    _add("RptExecLine",
         fontName="Helvetica-Oblique", fontSize=9, leading=12,
         textColor=C_NAVY, spaceAfter=4)
    _add("RptExecHeader",
         fontName="Helvetica-Bold", fontSize=9, leading=11,
         textColor=C_NAVY, spaceAfter=3, spaceBefore=6)
    _add("RptBullet",
         fontName="Helvetica", fontSize=8, leading=11,
         textColor=colors.black, spaceAfter=2,
         leftIndent=10, firstLineIndent=0)
    # Signal colours
    _add("SigBUY",   fontName="Helvetica-Bold", fontSize=8.5, textColor=C_GREEN)
    _add("SigSELL",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_RED)
    _add("SigSHORT", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_RED)
    _add("SigHOLD",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_AMBER)
    _add("SigCOVER", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_GREEN)
    # Value-trap status colours
    _add("TrapRED",   fontName="Helvetica-Bold", fontSize=8, textColor=C_RED)
    _add("TrapAMBER", fontName="Helvetica-Bold", fontSize=8, textColor=C_AMBER)
    _add("TrapGREEN", fontName="Helvetica-Bold", fontSize=8, textColor=C_GREEN)
    return base


_AGENT_DISPLAY = {
    "buffett":       "Warren Buffett",
    "munger":        "Charlie Munger",
    "graham":        "Ben Graham",
    "damodaran":     "Aswath Damodaran",
    "lynch":         "Peter Lynch",
    "fisher":        "Phil Fisher",
    "ackman":        "Bill Ackman",
    "cathie_wood":   "Cathie Wood",
    "burry":         "Michael Burry",
    "pabrai":        "Mohnish Pabrai",
    "druckenmiller": "Stanley Druckenmiller",
    "jhunjhunwala":  "Rakesh Jhunjhunwala",
}

_SIG_STYLE_MAP = {
    "BUY": "SigBUY", "SELL": "SigSELL", "SHORT": "SigSHORT",
    "HOLD": "SigHOLD", "COVER": "SigCOVER",
}
_TRAP_STYLE_MAP = {"RED": "TrapRED", "AMBER": "TrapAMBER", "GREEN": "TrapGREEN"}

_TRAP_CHECKS = [
    "dividend_sustainability",
    "structural_decline",
    "earnings_cashflow_mismatch",
    "insider_behaviour",
    "balance_sheet_deterioration",
]

_SKIP_AGENTS = {"risk_management_agent", "advanced_risk_manager"}

# ── Price history cache + fetch (6D) ─────────────────────────────────────────────
_PRICE_HISTORY_CACHE: dict[str, list] = {}


def _fetch_price_history(ticker: str, months: int = 12) -> list[tuple[str, float]]:
    """Fetch 12-month daily EOD prices from FMP.
    Returns [(date_str, close_price), ...] in chronological order.
    Returns [] if the API key is absent or the request fails.
    """
    if ticker in _PRICE_HISTORY_CACHE:
        return _PRICE_HISTORY_CACHE[ticker]
    try:
        import requests as _req
        from datetime import datetime as _dt, timedelta as _td
        key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
        if not key:
            return []
        end_d   = _dt.now()
        start_d = end_d - _td(days=months * 31)
        resp = _req.get(
            "https://financialmodelingprep.com/stable/historical-price-eod/light",
            params={
                "symbol": ticker,
                "from":   start_d.strftime("%Y-%m-%d"),
                "to":     end_d.strftime("%Y-%m-%d"),
                "apikey": key,
            },
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                pairs = [
                    (row["date"], float(row["close"] or row.get("price", 0)))
                    for row in data
                    if row.get("date") and (row.get("close") or row.get("price"))
                ]
                pairs.sort(key=lambda x: x[0])
                _PRICE_HISTORY_CACHE[ticker] = pairs
                return pairs
    except Exception:
        pass
    _PRICE_HISTORY_CACHE[ticker] = []
    return []


# ── Helpers ────────────────────────────────────────────────────────────────────
def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=C_LGREY, spaceAfter=4, spaceBefore=2)


def _kv_table(rows: list, col1_w: float, col2_w: float, styles) -> Table:
    """Build a 2-column label-value table from plain string pairs."""
    data = []
    for label, value in rows:
        data.append([
            Paragraph(_strip(label), styles["RptLabel"]),
            Paragraph(_strip(value), styles["RptValue"]),
        ])
    t = Table(data, colWidths=[col1_w, col2_w], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    return t


def _fmt_billions(v) -> str:
    """Format a statement amount as XB / XM (currency stated by the table)."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e9:
        return f"{v/1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.0f}M"
    return f"{v:,.0f}"


# ── Price Sparkline Flowable (item 6D) ─────────────────────────────────────────
class _PriceSparkline(Flowable):
    """12-month price history line chart drawn with raw canvas operations.
    Renders gracefully as an empty box if fewer than 3 data points are supplied.
    """
    _H = 88  # total flowable height in points

    def __init__(self, price_data: list[tuple[str, float]], price_target, width: float):
        Flowable.__init__(self)
        self._data  = price_data
        self._pt    = price_target
        self.width  = width
        self.height = self._H

    def draw(self) -> None:
        c = self.canv
        if len(self._data) < 3:
            c.setFont("Helvetica", 7)
            c.setFillColor(colors.HexColor("#aaaaaa"))
            c.drawCentredString(self.width / 2, self._H / 2, "Price history unavailable")
            return

        narrow = self.width < 260          # page-1 side column
        PAD_L, PAD_R, PAD_T, PAD_B = (30, 34, 8, 22) if narrow else (44, 54, 8, 22)
        cw = self.width - PAD_L - PAD_R
        ch = self._H - PAD_T - PAD_B

        prices = [p for _, p in self._data]
        mn, mx = min(prices), max(prices)
        pt = self._pt
        if isinstance(pt, (int, float)) and pt > 0:
            mx = max(mx, pt * 1.01)
        p_range = mx - mn or 1

        def xp(i):   return PAD_L + (i / max(len(self._data) - 1, 1)) * cw
        def yp(val): return PAD_B + ((val - mn) / p_range) * ch

        # Background
        c.setFillColor(colors.HexColor("#f7fafd"))
        c.rect(PAD_L, PAD_B, cw, ch, fill=1, stroke=0)

        # Horizontal grid
        c.setStrokeColor(colors.HexColor("#dde8f0"))
        c.setLineWidth(0.25)
        for frac in [0.25, 0.5, 0.75]:
            yg = PAD_B + frac * ch
            c.line(PAD_L, yg, PAD_L + cw, yg)

        # Fill under price line
        c.setFillColor(colors.HexColor("#dceaf7"))
        fp = c.beginPath()
        fp.moveTo(PAD_L, PAD_B)
        for i in range(len(self._data)):
            fp.lineTo(xp(i), yp(prices[i]))
        fp.lineTo(xp(len(self._data) - 1), PAD_B)
        fp.close()
        c.drawPath(fp, stroke=0, fill=1)

        # Price target dashed line
        if isinstance(pt, (int, float)) and pt > 0 and mn <= pt <= mx * 1.05:
            y_pt = yp(pt)
            c.setStrokeColor(colors.HexColor("#1a7a4a"))
            c.setLineWidth(0.7)
            c.setDash(3, 3)
            c.line(PAD_L, y_pt, PAD_L + cw, y_pt)
            c.setDash()
            c.setFont("Helvetica", 5.5)
            c.setFillColor(colors.HexColor("#1a7a4a"))
            c.drawString(PAD_L + cw + 3, y_pt - 3, f"PT {_cs()}{pt:.0f}")

        # Price line
        c.setStrokeColor(colors.HexColor("#0a2342"))
        c.setLineWidth(1.3)
        lp = c.beginPath()
        lp.moveTo(xp(0), yp(prices[0]))
        for i in range(1, len(prices)):
            lp.lineTo(xp(i), yp(prices[i]))
        c.drawPath(lp, stroke=1, fill=0)

        # Y-axis labels
        c.setFont("Helvetica", 5.5)
        c.setFillColor(colors.HexColor("#555555"))
        for frac, val in [(0.0, mn), (0.5, (mn + mx) / 2), (1.0, mx)]:
            c.drawRightString(PAD_L - 2, PAD_B + frac * ch - 3, f"{_cs()}{val:.0f}")

        # Current price dot + label
        last_p = prices[-1]
        xl, yl = xp(len(prices) - 1), yp(last_p)
        c.setFillColor(colors.HexColor("#0a2342"))
        c.circle(xl, yl, 2.5, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 6)
        lbl_y = yl + 5 if yl + 14 < PAD_B + ch else yl - 11
        c.drawCentredString(xl, lbl_y, f"{_cs()}{last_p:.0f}")

        # Date labels
        c.setFont("Helvetica", 5.5)
        c.setFillColor(colors.HexColor("#888888"))
        c.drawString(PAD_L, PAD_B - 12, self._data[0][0][:7])
        c.drawRightString(PAD_L + cw, PAD_B - 12, self._data[-1][0][:7])

        # Border
        c.setStrokeColor(colors.HexColor("#c8d8e8"))
        c.setLineWidth(0.35)
        c.rect(PAD_L, PAD_B, cw, ch, stroke=1, fill=0)


# ── Forward Financial Model — Year 1–5 (item 5A) ───────────────────────────────
def _forward_financial_model(dcf_ticker: dict, styles, page_w) -> list:
    """Clean 5-year forward estimates table built from DCF base-case projection rows."""
    proj_rows = (dcf_ticker.get("projection_rows") or [])[:5]
    if not proj_rows:
        return []

    hdr = [
        Paragraph(_wh("FORWARD ESTIMATES (Base Case)"), styles["RptLabel"]),
        Paragraph(_wh("Revenue"),      styles["RptLabel"]),
        Paragraph(_wh("Growth"),       styles["RptLabel"]),
        Paragraph(_wh("FCF Margin"),   styles["RptLabel"]),
        Paragraph(_wh("Free Cash Flow"), styles["RptLabel"]),
        Paragraph(_wh("PV of FCF"),    styles["RptLabel"]),
    ]
    cw = [page_w * 0.14, page_w * 0.18, page_w * 0.12,
          page_w * 0.14, page_w * 0.22, page_w * 0.20]
    table_rows = [hdr]
    for r in proj_rows:
        yr     = _strip(str(r.get("year_label", "")))
        rev    = r.get("revenue")
        growth = r.get("growth_pct")
        margin = r.get("fcf_margin")
        fcf    = r.get("fcf")
        pv_fcf = r.get("pv_fcf")
        g_s    = f"{growth:+.0%}"  if isinstance(growth, (int, float)) else "—"
        m_s    = f"{margin:.0%}"   if isinstance(margin, (int, float)) else "—"
        table_rows.append([
            Paragraph(yr,                    styles["RptBody"]),
            Paragraph(_fmt_billions(rev),    styles["RptValue"]),
            Paragraph(g_s,                   styles["RptValue"]),
            Paragraph(m_s,                   styles["RptValue"]),
            Paragraph(_fmt_billions(fcf),    styles["RptValue"]),
            Paragraph(_fmt_billions(pv_fcf), styles["RptValue"]),
        ])

    t = Table(table_rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
    ]))
    return [t, Spacer(1, 6)]


# ── Industry Peer Comparison Table (item B) ────────────────────────────────────
def _peer_comparison_table(
    peer_data: dict,      # {ticker: row_dict} for one subject
    subject: str,
    styles,
    page_w: float,
) -> list:
    """
    Render a horizontal peer comparison table.

    Columns: Metric | Subject | Peer1 | Peer2 | ...
    Rows:    P/E, EV/EBITDA, EV/Revenue, FCF Yield, ROIC, Rev Growth, Gross Margin
    Subject column highlighted in navy; peers in alternating white/pale.
    Returns [] if peer_data is empty or has only the subject.
    """
    if not peer_data or len(peer_data) < 2:
        return []

    # Metric definitions: (label, key, formatter)
    # Use `v is not None` guards — 0.0 is a valid value and must not render as "—"
    _METRICS: list[tuple[str, str, callable]] = [
        ("P / E",          "pe_ratio",       lambda v: f"{v:.1f}x"     if v is not None else "—"),
        ("EV / EBITDA",    "ev_ebitda",      lambda v: f"{v:.1f}x"     if v is not None else "—"),
        ("EV / Revenue",   "ev_revenue",     lambda v: f"{v:.1f}x"     if v is not None else "—"),
        ("FCF Yield",      "fcf_yield",      lambda v: f"{v*100:.1f}%" if v is not None else "—"),
        ("ROIC",           "roic",           lambda v: f"{v*100:.1f}%" if v is not None else "—"),
        ("Rev Growth",     "revenue_growth", lambda v: f"{v*100:.1f}%" if v is not None else "—"),
        ("Gross Margin",   "gross_margin",   lambda v: f"{v*100:.1f}%" if v is not None else "—"),
    ]

    # Order: subject first, then peers sorted alphabetically
    ordered = [subject] + sorted(t for t in peer_data if t != subject)

    # Column widths — metric label takes 22%, rest split equally among tickers
    n_tickers = len(ordered)
    label_w   = page_w * 0.22
    tick_w    = (page_w - label_w) / n_tickers

    # Header row — all cells white-on-navy
    hdr = [Paragraph(_wh("Metric"), styles["RptLabel"])]
    for t in ordered:
        row = peer_data.get(t, {})
        cap = row.get("market_cap")
        cap_s = (
            f"{_cs()}{cap/1e12:.1f}T" if cap and cap >= 1e12 else
            f"{_cs()}{cap/1e9:.0f}B"  if cap and cap >= 1e9  else
            f"{_cs()}{cap/1e6:.0f}M"  if cap and cap >= 1e6  else ""
        )
        inner = f"{t}" + (f" {cap_s}" if cap_s else "")
        hdr.append(Paragraph(_wh(inner), styles["RptLabel"]))

    rows: list[list] = [hdr]
    for metric_label, key, fmt in _METRICS:
        row_cells = [Paragraph(metric_label, styles["RptLabel"])]
        for t in ordered:
            val = peer_data.get(t, {}).get(key)
            try:
                cell_text = fmt(val) if val is not None else "—"
            except Exception:
                cell_text = "—"
            row_cells.append(Paragraph(cell_text, styles["RptValue"]))
        rows.append(row_cells)

    col_widths = [label_w] + [tick_w] * n_tickers
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)

    # Style — subject column (index 1) gets navy background
    ts = TableStyle([
        # Header row
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        # Subject column highlight
        ("BACKGROUND",    (1, 1), (1, -1), colors.HexColor("#e8f0e8")),
        ("FONTNAME",      (1, 1), (1, -1), "Helvetica-Bold"),
        # Metric label column
        ("BACKGROUND",    (0, 1), (0, -1), C_PALE),
        ("FONTNAME",      (0, 1), (0, -1), "Helvetica-Bold"),
        # Alternating rows for peer columns
        ("ROWBACKGROUNDS",(2, 1), (-1, -1), [colors.white, C_PALE]),
        # Global
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_BLUE),
    ])
    tbl.setStyle(ts)
    return [tbl, Spacer(1, 6)]


# ── Key Financials Table (item 15) ─────────────────────────────────────────────
def _key_financials_table(raw_financials: dict, styles, page_w, years: int = 5) -> "Table | None":
    """Compact multi-year historical financials table (Revenue / Net Income / FCF / Net Debt).
    Returns None if raw_financials is absent or contains no parseable year-keyed data.
    `page_w` is the width available; `years` the most recent fiscal years shown.
    """
    if not raw_financials or not isinstance(raw_financials, dict):
        return None

    # Accept keys like "FY2020", "FY2021", "2020", "2021", etc.
    fy_keys = sorted(k for k in raw_financials if isinstance(raw_financials.get(k), dict))
    if not fy_keys:
        return None
    fy_keys = fy_keys[-years:]      # the most recent fiscal years

    def _get(fy, key):
        v = raw_financials.get(fy, {})
        return v.get(key) if isinstance(v, dict) else None

    def _fcf(fy):
        # Prefer the direct free_cash_flow field (most reliable)
        fcf_direct = _get(fy, "free_cash_flow")
        if fcf_direct is not None:
            try:
                return float(fcf_direct)
            except (TypeError, ValueError):
                pass
        # Fallback: OCF - capex (field is "capital_expenditure", not "capex")
        ocf = _get(fy, "operating_cash_flow")
        cap = _get(fy, "capital_expenditure")
        if ocf is not None and cap is not None:
            try:
                return float(ocf) - abs(float(cap))
            except (TypeError, ValueError):
                pass
        return None

    _ccy = _strip(str(raw_financials.get("currency") or "")).upper()
    hdr = [Paragraph(_wh(f"{_ccy} bn" if _ccy else "Key financials"), styles["RptLabel"])]
    for fy in fy_keys:
        hdr.append(Paragraph(_wh(_strip(str(fy))), styles["RptLabel"]))

    def _data_row(label, values):
        return [Paragraph(label, styles["RptBody"])] + [
            Paragraph(_fmt_billions(v), styles["RptValue"]) for v in values
        ]

    rows = [
        hdr,
        _data_row("Revenue",          [_get(fy, "revenue")          for fy in fy_keys]),
        _data_row("Net Income",        [_get(fy, "net_income")        for fy in fy_keys]),
        _data_row("FCF",               [_fcf(fy)                      for fy in fy_keys]),
        _data_row("Net debt", [_get(fy, "net_debt")          for fy in fy_keys]),
    ]

    label_w = page_w * (0.34 if page_w < 260 else 0.22)
    data_w  = (page_w - label_w) / len(fy_keys)
    t = Table(rows, colWidths=[label_w] + [data_w] * len(fy_keys), hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
    ]))
    return t


# ── Intelligence Signals summary (Phase 2.5 — item I) ─────────────────────────
def _getv(obj, key, default=None):
    """Uniform getter: works on both Pydantic model instances and plain dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _flag_colour(flag: str) -> str:
    """Return an HTML hex colour string matching the flag severity."""
    f = str(flag).upper()
    if f in ("RED", "HIGH", "HEAVILY_SHORTED", "INCREASING"):
        return "#c0392b"
    if f in ("AMBER", "MEDIUM", "MODERATELY_SHORTED", "STABLE"):
        return "#d35400"
    if f in ("GREEN", "LOW", "LOW_SHORT_INTEREST", "DECREASING", "IMPROVING"):
        return "#1a7a4a"
    return "#555555"


def _flag_cell(flag: str, styles) -> "Paragraph":
    colour = _flag_colour(flag)
    return Paragraph(f'<font color="{colour}"><b>{_strip(str(flag))}</b></font>', styles["RptValue"])


def _intel_summary(
    ticker: str,
    short_int: dict,
    earn_q: dict,
    insider_act: dict = {},
    news_sent: dict = {},
    analyst_rev: dict = {},
    styles = None,
    page_w: float = 500,
) -> list:
    """Compact intelligence table: all five Phase 2.5 signal rows.
    Rows: Earnings Quality, Short Interest, Insider Activity, News Sentiment, Analyst Revisions.
    Returns [] if all sources are empty / absent.
    """
    if not short_int and not earn_q and not insider_act and not news_sent and not analyst_rev:
        return []

    rows = [[
        Paragraph(_wh("INTELLIGENCE"),       styles["RptLabel"]),
        Paragraph(_wh("Signal"),             styles["RptLabel"]),
        Paragraph(_wh("Score / Value"),      styles["RptLabel"]),
        Paragraph(_wh("Key Metrics"),        styles["RptLabel"]),
        Paragraph(_wh("Flags"),              styles["RptLabel"]),
    ]]

    # ── Earnings Quality row ──────────────────────────────────────────────────
    if earn_q:
        score   = _getv(earn_q, "overall_quality_score")
        verdict = _strip(str(_getv(earn_q, "quality_verdict", "—")))
        accrual = _strip(str(_getv(earn_q, "accrual_flag",       "—")))
        cash_cv = _strip(str(_getv(earn_q, "cash_conversion_flag","—")))
        fcf_ni  = _strip(str(_getv(earn_q, "fcf_ni_divergence",   "—")))
        sbc     = _strip(str(_getv(earn_q, "sbc_drag_flag",       "—")))
        score_s = f"{score:.1f}/10" if isinstance(score, (int, float)) else "—"
        metrics = f"Accrual: {accrual}  |  Cash Conv: {cash_cv}  |  DSO: {_strip(str(_getv(earn_q,'dso_trend','—')))}"
        flags_s = f"FCF/NI: {fcf_ni}  |  SBC drag: {sbc}"
        rows.append([
            Paragraph("Earnings Quality",   styles["RptBody"]),
            _flag_cell(verdict,              styles),
            Paragraph(score_s,              styles["RptValue"]),
            Paragraph(metrics,              styles["RptBody"]),
            Paragraph(flags_s,              styles["RptBody"]),
        ])

    # ── Short Interest row ────────────────────────────────────────────────────
    if short_int:
        signal  = _strip(str(_getv(short_int, "signal",               "—")))
        sf_pct  = _getv(short_int, "short_float_pct")
        dtc     = _getv(short_int, "days_to_cover")
        trend   = _strip(str(_getv(short_int, "short_interest_trend",  "—")))
        squeeze = bool(_getv(short_int, "squeeze_risk",  False))
        crowded = bool(_getv(short_int, "crowded_trade", False))
        sf_s    = f"{sf_pct:.1f}% of float" if isinstance(sf_pct, (int, float)) else "—"
        dtc_s   = f"DTC {dtc:.1f}d" if isinstance(dtc, (int, float)) else ""
        score_s = f"{sf_s}  |  {dtc_s}" if dtc_s else sf_s
        extra   = []
        if squeeze:  extra.append("⚠ Squeeze risk")
        if crowded:  extra.append("⚠ Crowded trade")
        flags_s = "  |  ".join(extra) if extra else f"Trend: {trend}"
        rows.append([
            Paragraph("Short Interest",     styles["RptBody"]),
            _flag_cell(signal,               styles),
            Paragraph(score_s,              styles["RptValue"]),
            Paragraph(f"Trend: {trend}",    styles["RptBody"]),
            Paragraph(flags_s,              styles["RptBody"]),
        ])

    # ── Insider Activity row (Phase 2.5) ──────────────────────────────────────
    if insider_act:
        ia_signal   = _strip(str(_getv(insider_act, "signal",             "—")))
        net_12m     = _getv(insider_act, "net_buying_12m_usd", 0.0)
        bsr         = _getv(insider_act, "buy_sell_ratio_12m", 0.0)
        cluster     = bool(_getv(insider_act, "cluster_buy", False))
        conv_sell   = bool(_getv(insider_act, "conviction_sell_flag", False))
        src         = _strip(str(_getv(insider_act, "data_source", "—")))
        net_s       = (
            f"{_cs()}{net_12m/1e6:+.1f}M net 12m" if isinstance(net_12m, (int, float)) else "—"
        )
        bsr_s       = f"B/S ratio: {bsr:.1f}x" if isinstance(bsr, (int, float)) else ""
        score_s     = f"{net_s}  |  {bsr_s}" if bsr_s else net_s
        extra_ia    = []
        if cluster:    extra_ia.append("Cluster buy")
        if conv_sell:  extra_ia.append("⚠ Conviction sell")
        flags_s     = "  |  ".join(extra_ia) if extra_ia else f"Source: {src}"
        rows.append([
            Paragraph("Insider Activity",   styles["RptBody"]),
            _flag_cell(ia_signal,            styles),
            Paragraph(score_s,              styles["RptValue"]),
            Paragraph(f"Source: {src}",     styles["RptBody"]),
            Paragraph(flags_s,              styles["RptBody"]),
        ])

    # ── News Sentiment row (Phase 2.5) ────────────────────────────────────────
    if news_sent:
        ns_signal   = _strip(str(_getv(news_sent, "signal",               "—")))
        composite   = _getv(news_sent, "composite_score", 0.0)
        art_count   = _getv(news_sent, "article_count",   0)
        pr_signal   = _strip(str(_getv(news_sent, "press_release_signal", "—")))
        vol_spike   = bool(_getv(news_sent, "volume_spike", False))
        headlines   = _getv(news_sent, "top_headlines", [])
        comp_s      = f"{composite:+.3f}" if isinstance(composite, (int, float)) else "—"
        art_s       = f"{art_count} articles" if isinstance(art_count, int) else ""
        score_s     = f"Score: {comp_s}  |  {art_s}" if art_s else f"Score: {comp_s}"
        hl_s        = headlines[0] if headlines else f"PR signal: {pr_signal}"
        extra_ns    = ["⚠ Vol spike"] if vol_spike else []
        flags_s     = "  |  ".join(extra_ns) if extra_ns else f"PR: {pr_signal}"
        rows.append([
            Paragraph("News Sentiment",     styles["RptBody"]),
            _flag_cell(ns_signal,            styles),
            Paragraph(score_s,              styles["RptValue"]),
            Paragraph(hl_s[:80] if hl_s else "—", styles["RptBody"]),
            Paragraph(flags_s,              styles["RptBody"]),
        ])

    # ── Analyst Revisions row (Phase 2.5) ─────────────────────────────────────
    if analyst_rev:
        rev_dir     = _strip(str(_getv(analyst_rev, "revision_direction",  "—")))
        streak      = _getv(analyst_rev, "surprise_streak", 0)
        streak_dir  = _strip(str(_getv(analyst_rev, "surprise_direction",  "—")))
        dispersion  = _strip(str(_getv(analyst_rev, "estimate_dispersion", "—")))
        ana_count   = _getv(analyst_rev, "analyst_count", 0)
        streak_s    = (
            f"{streak:+d} {'beats' if streak > 0 else 'misses'}" if streak != 0 else "No streak"
        )
        score_s     = f"{streak_s}  |  {ana_count} analysts" if ana_count else streak_s
        metrics_s   = f"Direction: {streak_dir}  |  Dispersion: {dispersion}"
        rows.append([
            Paragraph("Analyst Revisions",  styles["RptBody"]),
            _flag_cell(rev_dir,              styles),
            Paragraph(score_s,              styles["RptValue"]),
            Paragraph(metrics_s,            styles["RptBody"]),
            Paragraph(f"Dispersion: {dispersion}", styles["RptBody"]),
        ])

    if len(rows) == 1:
        return []   # header only — nothing to show

    cw = [page_w * 0.18, page_w * 0.18, page_w * 0.16, page_w * 0.28, page_w * 0.20]
    t  = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    return [t, Spacer(1, 6)]


# ── VGPM Scorecard ─────────────────────────────────────────────────────────────

# Grade colour palette
_GRADE_STYLES = {
    "A+": (colors.HexColor("#15803d"), colors.white),        # dark green / white
    "A":  (colors.HexColor("#22c55e"), colors.white),        # green / white
    "A-": (colors.HexColor("#bbf7d0"), colors.HexColor("#14532d")),  # light green / dark
    "B+": (colors.HexColor("#eab308"), colors.HexColor("#1c1917")),  # yellow / dark
    "B":  (colors.HexColor("#eab308"), colors.HexColor("#1c1917")),
    "B-": (colors.HexColor("#eab308"), colors.HexColor("#1c1917")),
    "C":  (colors.HexColor("#7f1d1d"), colors.white),        # maroon / white
    "D":  (colors.HexColor("#dc2626"), colors.white),        # red / white
}

def _score_to_grade(score: float) -> str:
    """Map 0–100 composite score to letter grade."""
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "A-"
    if score >= 60: return "B+"
    if score >= 50: return "B"
    if score >= 40: return "B-"
    if score >= 28: return "C"
    return "D"


def _compute_vgpm(
    dcf_ticker: dict,
    scen_ticker: dict,
    raw_financials: dict,
    dcf_cal: dict,
    insider_summary: str,
    sector: str | None = None,
) -> dict:
    """
    Compute Valuation / Growth / Profitability / Momentum scores (0–100 each).
    Returns a dict keyed by dimension with 'score', 'grade', and 'subs' (sub-metric lines).
    All inputs are already available in state at PDF render time — no extra API calls.

    `sector` (added 2026-05-21) routes 4 of the 12 sub-scores to per-sector
    threshold tables (v3 / g1 / p1 / p2). Pre-fix these were cross-sector
    universal — a Bank with 8% growth and a Tech with 8% growth both hit
    the same B-band sub-score, washing out sector dynamics and collapsing
    letter grades to the B/B+/B- band. Sector-aware bands restore signal:
    Utility 5%-growth = A, Tech 5%-growth = B-band. See
    src/utils/vgpm_thresholds.py for the per-sector bands.

    When sector is None / unknown, falls back to "Technology" (the safe
    default — most aggressive thresholds, no regressions on saved runs
    that lack sector metadata).
    """
    from src.utils.vgpm_thresholds import (
        score_fcf_margin,
        score_growth,
        score_p_fcf,
        score_roic_spread,
    )
    base       = dcf_ticker.get("base") or {}
    current_p  = scen_ticker.get("current_price") or 0
    base_iv    = base.get("intrinsic_value") or 0
    wacc       = dcf_ticker.get("wacc") or 0.10
    shares     = dcf_ticker.get("shares_outstanding") or 0
    rev_base   = dcf_ticker.get("revenue_base") or 0
    fcf_margin = dcf_ticker.get("fcf_margin_base") or 0
    growth     = base.get("growth_rate") or 0
    upside_pct = scen_ticker.get("upside_pct") or 0
    bull_fv    = (scen_ticker.get("bull") or {}).get("fair_value") or 0
    bear_fv    = (scen_ticker.get("bear") or {}).get("fair_value") or 0

    # ── Helper: clamp score to 0-100 ────────────────────────────────────────
    def _clamp(v): return max(0.0, min(100.0, float(v)))

    # ══════════════════════════════════════════════════════════════════════════
    # VALUATION  (DCF MoS 40% · EV Upside 35% · P/FCF 25%)
    # ══════════════════════════════════════════════════════════════════════════
    # Sub 1: DCF Margin of Safety
    mos = ((base_iv - current_p) / current_p * 100) if current_p > 0 and base_iv else 0
    if   mos >  40: v1 = 95
    elif mos >  20: v1 = 80
    elif mos >   0: v1 = 62
    elif mos > -20: v1 = 42
    elif mos > -40: v1 = 22
    else:           v1 = 8
    mos_lbl = f"DCF MoS: {mos:+.0f}%"

    # Sub 2: Scenario EV Upside
    if   upside_pct >  35: v2 = 95
    elif upside_pct >  20: v2 = 80
    elif upside_pct >   5: v2 = 65
    elif upside_pct >  -5: v2 = 50
    elif upside_pct > -20: v2 = 30
    else:                  v2 = 12
    ev_lbl = f"EV (Expected Value) upside: {upside_pct:+.1f}%"

    # Sub 3: P/FCF (forward; skip if negative FCF). SECTOR-AWARE bands.
    if shares > 0 and rev_base > 0 and fcf_margin > 0:
        fcf_ps  = rev_base * fcf_margin / shares
        p_fcf   = current_p / fcf_ps if fcf_ps > 0 else None
    else:
        p_fcf = None
    v3 = score_p_fcf(p_fcf, sector)
    if p_fcf is None:
        pfcf_lbl = "P/FCF: N/A (neg)"
    else:
        pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"

    val_score = _clamp(v1 * 0.40 + v2 * 0.35 + v3 * 0.25)

    # ══════════════════════════════════════════════════════════════════════════
    # GROWTH  (Revenue growth 45% · Bull/Bear asymmetry 30% · Data confidence 25%)
    # ══════════════════════════════════════════════════════════════════════════
    # Sub 1: Revenue growth rate used in DCF (guided > analyst > historical).
    # SECTOR-AWARE: Utility 5% = A; Tech 5% = B-band. See vgpm_thresholds.
    gr_pct = growth * 100
    g1 = score_growth(gr_pct, sector)
    gr_lbl = f"Rev CAGR: {gr_pct:+.1f}%"

    # Sub 2: Bull / Bear asymmetry (upside leverage)
    if bear_fv and bear_fv > 0 and bull_fv:
        asym = bull_fv / bear_fv
        if   asym > 3.0: g2 = 92
        elif asym > 2.0: g2 = 78
        elif asym > 1.5: g2 = 62
        elif asym > 1.0: g2 = 48
        else:            g2 = 25
        asym_lbl = f"Bull/Bear: {asym:.1f}×"
    else:
        g2 = 50; asym_lbl = "Bull/Bear: N/A"

    # Sub 3: Growth data confidence (guided=95, analyst=75, historical=50)
    data_src = dcf_ticker.get("data_source", "historical")
    if   data_src == "guided":   g3 = 92; src_lbl = "Source: guided"
    elif data_src == "analyst":  g3 = 74; src_lbl = "Source: analyst est"
    else:                        g3 = 50; src_lbl = "Source: historical"

    grw_score = _clamp(g1 * 0.45 + g2 * 0.30 + g3 * 0.25)

    # ══════════════════════════════════════════════════════════════════════════
    # PROFITABILITY  (FCF Margin 50% · ROIC proxy 30% · Margin trend 20%)
    # ══════════════════════════════════════════════════════════════════════════
    # Sub 1: FCF Margin. SECTOR-AWARE bands — Tech 12% = B+, Utility 12% = A+.
    fm_pct = fcf_margin * 100
    p1 = score_fcf_margin(fm_pct, sector)
    fm_lbl = f"FCF margin: {fm_pct:.1f}%"

    # Sub 2: ROIC proxy from most recent raw_financials
    # ROIC ≈ net_income / (revenue × 0.35)  — rough invested-capital proxy
    _ni, _rev = 0.0, 0.0
    for yr in sorted(raw_financials.keys(), reverse=True)[:1]:
        yr_data = raw_financials.get(yr) or {}
        if isinstance(yr_data, dict):
            _ni  = float(yr_data.get("net_income") or 0)
            _rev = float(yr_data.get("revenue")    or 0)
    if _rev > 0:
        roic_proxy = _ni / (_rev * 0.35)
        roic_spread = roic_proxy - wacc
        # SECTOR-AWARE: Bank +2pp spread = A; Tech needs +15pp for the
        # same letter grade. Reflects sector ROIC norms.
        p2 = score_roic_spread(roic_spread, sector)
        roic_lbl = f"ROIC−WACC: {roic_spread*100:+.1f}pp"
    else:
        p2 = 45; roic_lbl = "ROIC−WACC: N/A"

    # Sub 3: Margin trend from DCF calibration signal
    margin_dir = (dcf_cal.get("margin_direction") or "stable").lower()
    if   "expan" in margin_dir: p3 = 85;  mtrd_lbl = "Margin trend: ↑"
    elif "comp"  in margin_dir: p3 = 25;  mtrd_lbl = "Margin trend: ↓"
    else:                       p3 = 55;  mtrd_lbl = "Margin trend: →"

    prof_score = _clamp(p1 * 0.50 + p2 * 0.30 + p3 * 0.20)

    # ══════════════════════════════════════════════════════════════════════════
    # MOMENTUM  (Scenario upside 40% · Insider activity 35% · Risk flag 25%)
    # ══════════════════════════════════════════════════════════════════════════
    # Sub 1: Scenario EV Upside (reused from valuation — forward-looking signal)
    if   upside_pct >  35: m1 = 95
    elif upside_pct >  20: m1 = 80
    elif upside_pct >   5: m1 = 62
    elif upside_pct >  -5: m1 = 50
    elif upside_pct > -20: m1 = 30
    else:                  m1 = 12
    mom_ev_lbl = f"EV (Expected Value) upside: {upside_pct:+.1f}%"

    # Sub 2: Insider activity from insider_summary text
    # Guard: insider_summary may arrive as a dict if the pipeline stores structured
    # insider data — stringify before calling .lower() to prevent AttributeError.
    _ins_raw_inner = insider_summary if isinstance(insider_summary, str) else str(insider_summary or "")
    _ins = _ins_raw_inner.lower()
    _buy_words  = ["buy", "purchas", "acquir", "accumul"]
    _sell_words = ["sell", "sold", "disposed", "transfer"]
    _n_buy  = sum(_ins.count(w) for w in _buy_words)
    _n_sell = sum(_ins.count(w) for w in _sell_words)
    if   _n_buy  > _n_sell + 1: m2 = 82; ins_lbl = "Insider: net buying ↑"
    elif _n_sell > _n_buy  + 1: m2 = 22; ins_lbl = "Insider: net selling ↓"
    else:                        m2 = 50; ins_lbl = "Insider: neutral →"

    # Sub 3: Deep-research risk flag
    risk_flag = (dcf_cal.get("risk_flag") or "MEDIUM").upper()
    if   risk_flag == "LOW":    m3 = 80; rf_lbl = "Risk flag: LOW"
    elif risk_flag == "HIGH":   m3 = 22; rf_lbl = "Risk flag: HIGH"
    else:                       m3 = 52; rf_lbl = "Risk flag: MEDIUM"

    mom_score = _clamp(m1 * 0.40 + m2 * 0.35 + m3 * 0.25)

    def _dim(score, subs):
        return {"score": round(score, 1), "grade": _score_to_grade(score), "subs": subs}

    return {
        "valuation":     _dim(val_score,  [mos_lbl, ev_lbl,    pfcf_lbl]),
        "growth":        _dim(grw_score,  [gr_lbl,  asym_lbl,  src_lbl]),
        "profitability": _dim(prof_score, [fm_lbl,  roic_lbl,  mtrd_lbl]),
        "momentum":      _dim(mom_score,  [mom_ev_lbl, ins_lbl, rf_lbl]),
    }


def _fmt_hex(colour) -> str:
    """Convert a ReportLab Color to hex string for inline XML markup."""
    try:
        r = int(colour.red   * 255)
        g = int(colour.green * 255)
        b = int(colour.blue  * 255)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#888888"


def _margin_delta_abs(scenario: dict) -> float:
    """The ONE-SHOT absolute FCF-margin delta the engine applied to every year.

    Published as ``margin_delta_absolute`` since 2026-09-17. Archived runs carry
    only the earlier misnomer ``margin_delta_per_year`` — but that key always
    held this same one-shot value: dcf_agent called ``_project_dcf`` with
    ``margin_delta_per_year=0.0`` (commented "superseded by md_abs") and
    ``margin_delta_absolute=md_abs``, then published ``md_abs`` under the
    per-year name. So both keys are read with identical semantics, and neither
    is ever scaled by ``t``.

    That scaling was the bug this helper exists to prevent. Both sensitivity
    grids below rebuilt the projection as ``margin + delta * t``, drifting the
    margin over ten years when the engine holds it flat after one step.

    How far that reached is worth stating precisely, because it is narrower than
    it looks and wider than nothing. Both grids read ``dcf_ticker["base"]``, so
    the delta they mis-scaled is the BASE scenario's — which is
    ``guidance_margin_adj``, and is 0 for every one of the 14 golden fixtures and
    for any run where management guidance does not move the margin. ``0 * t`` is
    still 0, so for those runs the two formulas agree exactly and the misreading
    produced identical output. It was latent, not inert.

    For a guided name whose base delta is non-zero it was very much live. On a
    synthetic base with ``md = -0.04`` the old grid's centre cell read -$0.75
    against the engine's +$4.14 (a 118% divergence); at ``md = +0.04``, +$13.93
    against +$6.26 (123%). And the divergence the grid's own ``_sens_warn`` check
    exists to report would have fired with a diagnosis blaming "revenue_base or
    shares_outstanding unit mismatch (check FX conversion)" — sending the reader
    to two fields that were perfectly fine. ``tests/test_valuation_fixes_0917e.py``
    pins the corrected identity and transcribes the old closure as the control.

    The third consumer, ``_section_2f``'s traceability table, had the arithmetic
    right all along (``base + delta``, one step) and the LABEL wrong: it printed
    ``±X.XX%/yr`` under a row headed "Margin delta / year", telling the reader
    the margin kept moving every year when the engine moves it once. That one was
    unconditional — it misdescribed every PDF ever produced.
    """
    v = scenario.get("margin_delta_absolute")
    if v is None:
        v = scenario.get("margin_delta_per_year", 0.0)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ── Markdown rendering helpers ──────────────────────────────────────────────

def _md_inline(text: str) -> str:
    """Convert **bold** and *italic* markdown in already-HTML-escaped text.
    Must be called AFTER html.escape() so the tags we insert aren't re-escaped.
    """
    # Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    # Italic: *text* (not already wrapped by bold)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    return text


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_separator_row(line: str) -> bool:
    if not _is_table_row(line):
        return False
    cells = [c.strip() for c in line.strip()[1:-1].split("|")]
    return all(re.fullmatch(r"[-:]+", c) for c in cells if c)


def _parse_md_table(lines: list, page_w: float, styles) -> "Table | None":
    """Convert a list of raw markdown table lines into a styled ReportLab Table."""
    data_rows: list = []
    is_header = True
    for line in lines:
        if _is_separator_row(line):
            is_header = False
            continue
        cells = [c.strip() for c in line.strip()[1:-1].split("|")]
        rl_row = []
        for c in cells:
            formatted = _md_inline(html.escape(c))
            if is_header:
                rl_row.append(Paragraph(f"<b>{formatted}</b>", styles["RptLabel"]))
            else:
                rl_row.append(Paragraph(formatted, styles["RptBody"]))
        data_rows.append(rl_row)

    if not data_rows:
        return None

    ncols = max(len(r) for r in data_rows)
    for row in data_rows:          # normalise ragged rows
        while len(row) < ncols:
            row.append(Paragraph("", styles["RptBody"]))

    col_w = page_w / ncols
    t = Table(data_rows, colWidths=[col_w] * ncols, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("GRID",          (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    return t


_BRIEF_TITLE_RE = re.compile(
    r"\s*#*\s*(?:SECTION\s+\d+\s*[\u2014\-]\s*)?INDUSTRY INTELLIGENCE BRIEF\s*", re.I)


def _render_md_block(
    text: str,
    story: list,
    styles,
    page_w: float,
    collector=None,
) -> None:
    """Render a block of LLM-generated markdown into the ReportLab story.

    Handles:
      - Markdown tables  (| col | col |  +  |---|---|  separator rows)
      - ATX headings     (# / ## / ###)
      - Horizontal rules (--- or === lines)
      - Inline bold      (**text**)
      - Inline italic    (*text*)
      - Blank lines      → small spacer
    """
    lines = [l for l in _ANSI_RE.sub("", text).splitlines() if not _BRIEF_TITLE_RE.fullmatch(l)]
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not stripped:
            story.append(Spacer(1, 3))
            i += 1
            continue

        # ── Markdown table: collect all consecutive table/separator lines ──
        if _is_table_row(stripped):
            table_lines = []
            while i < len(lines):
                tl = lines[i].strip()
                if _is_table_row(tl) or _is_separator_row(tl):
                    table_lines.append(tl)
                    i += 1
                else:
                    break
            tbl = _parse_md_table(table_lines, page_w, styles)
            if tbl:
                story.append(tbl)
                story.append(Spacer(1, 4))
            continue

        # ── Standalone horizontal rule ──
        if re.fullmatch(r"[-=\u2500-\u257f\u25a0]{3,}\s*", stripped):
            story.append(_hr())
            i += 1
            continue

        # ── ATX headings ──
        for prefix, n in (("### ", 3), ("## ", 2), ("# ", 1)):
            if stripped.startswith(prefix):
                content = _md_inline(html.escape(stripped[n + 1:]))
                story.append(Paragraph(content, styles["RptSubsection"]))
                i += 1
                break
        else:
            # ── Regular body text with inline markdown ──
            if collector:
                collector(stripped)
            formatted = _md_inline(html.escape(stripped))
            story.append(Paragraph(formatted, styles["RptBody"]))
            i += 1


# ── Valuation Analysis (DCF, blend, assumptions, projection) ─────────────────

def _section_2f(
    ticker: str,
    dcf_data: dict,
    decision: dict,
    scenario: dict,
    styles,
    page_w: float,
) -> list:
    """Build Section 2f — Valuation Model flowables. Returns [] if no DCF data."""
    story = []
    if not dcf_data or not dcf_data.get("base"):
        story.append(Paragraph(
            "Valuation model not available for this ticker (insufficient financial history).",
            styles["RptBody"],
        ))
        return story

    bear  = dcf_data.get("bear", {})
    base  = dcf_data.get("base", {})
    bull  = dcf_data.get("bull", {})
    wacc  = dcf_data.get("wacc", 0.0)
    c_mac = dcf_data.get("c_macro", 0.0)
    profile  = dcf_data.get("profile", "—")
    data_src = dcf_data.get("data_source", "—")
    cal_err  = dcf_data.get("calibration_error", False)
    cal_note = dcf_data.get("calibration_note", "")
    # Owner, 2026-09-26 (audit): flags live on each scenario; the ticker level
    # carried none, so no flag ever printed. Base first, then the others, deduped.
    fwd_flags = list(dcf_data.get("forward_flags") or [])
    for _sc in ("base", "bear", "bull"):
        for _f in ((dcf_data.get(_sc) or {}).get("forward_flags") or []):
            if _f not in fwd_flags:
                fwd_flags.append(_f)
    shares   = dcf_data.get("shares_outstanding", 0)
    net_debt = dcf_data.get("net_debt", None)
    revenue_base = dcf_data.get("revenue_base", 0)
    proj_rows = dcf_data.get("projection_rows", [])
    reported_currency = dcf_data.get("reported_currency", "USD") or "USD"
    fx_rate   = dcf_data.get("fx_rate", 1.0) or 1.0
    fx_note   = dcf_data.get("fx_note", "") or ""

    # ── Header info block ────────────────────────────────────────────────────
    # The T-1 gate re-runs today's methodology on the previous fiscal year and
    # compares it with the price at that year end: a backtest of the method,
    # not today's valuation. A skipped test is neither a pass nor a miss.
    if cal_err:
        cal_status, cal_color = "MISS", C_AMBER
    elif str(cal_note).startswith("Skipped"):
        cal_status, cal_color = "SKIPPED", colors.black
    else:
        cal_status, cal_color = "PASS", C_GREEN
    flag_text  = ("  |  Flags: " + "; ".join(fwd_flags)) if fwd_flags else ""

    ds_display = (data_src.replace("analyst", "Analyst consensus")
                          .replace("historical", "Historical CAGR")
                          .replace("guided", "Company guidance"))

    header_rows = [
        ("Profile",        _strip(profile)),
        ("Data Source",    _strip(ds_display)),
        ("Macro modifier", f"C_macro = {c_mac:+.3f}"),
        ("Methodology Backtest", f"{cal_status}  {_strip(cal_note[:120])}"),
    ]
    if reported_currency != "USD":
        fx_display = f"{dcf_data.get('source_currency') or reported_currency}→{dcf_data.get('trading_currency') or reported_currency} @ {fx_rate:.4f}  |  {_strip(fx_note[:100])}"
        header_rows.insert(1, ("Currency (FX)", fx_display))
    if flag_text:
        header_rows.append(("Forward flags", _strip(flag_text)))

    col1 = page_w * 0.22
    col2 = page_w * 0.78
    h_data = []
    for label, val in header_rows:
        is_cal = label == "Methodology Backtest"
        val_style = ParagraphStyle(
            name=f"_cal_{label}",
            parent=styles["RptValue"],
            textColor=cal_color if is_cal else colors.black,
            fontName="Helvetica-Bold" if is_cal else "Helvetica",
        )
        h_data.append([
            Paragraph(label, styles["RptLabel"]),
            Paragraph(val, val_style),
        ])
    ht = Table(h_data, colWidths=[col1, col2], hAlign="LEFT")
    ht.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
    ]))
    story.append(ht)
    story.append(Spacer(1, 6))

    # ── A. Multi-Method Blended Intrinsic Value (§6 of valuation framework) ─────
    # Each method has DISTINCT bear/base/bull values — blending occurs at the BOTTOM.
    # Per-method values come from dcf_agent's method_iv_table (stored per scenario).
    story.append(Paragraph("A — Multi-Method Blended Intrinsic Value (§6)", styles["RptSubsection"]))

    # P1.1 — Valuation profile: anchor method + rationale
    _anchor_method    = dcf_data.get("anchor_method", "")
    _profile_rationale = dcf_data.get("profile_rationale", "")
    if _anchor_method or _profile_rationale:
        _prof_header = (
            f"<b>Valuation Profile:</b> {profile}"
            + (f"  |  <b>Primary Anchor:</b> {_anchor_method}" if _anchor_method else "")
        )
        story.append(Paragraph(_prof_header, styles["RptBody"]))
        if _profile_rationale:
            story.append(Paragraph(
                f"<i>Profile rationale:</i> {_profile_rationale}",
                styles["RptBody"],
            ))
        story.append(Spacer(1, 4))

    bear_iv = bear.get("intrinsic_value", 0)
    base_iv = base.get("intrinsic_value", 0)
    bull_iv = bull.get("intrinsic_value", 0)
    methods_count = base.get("methods_count", 1)

    current_price = scenario.get("current_price") or 0.0
    try:
        current_price = float(current_price)
    except (TypeError, ValueError):
        current_price = 0.0

    # P0.1 — scenario probabilities for EV-per-method column
    _bear_p = float(((scenario.get("bear") or {}).get("probability")) or 0.25)
    _base_p = float(((scenario.get("base") or {}).get("probability")) or 0.50)
    _bull_p = float(((scenario.get("bull") or {}).get("probability")) or 0.25)

    def _updown(iv):
        if not current_price or not iv:
            return "—"
        pct = (float(iv) - current_price) / current_price * 100
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.1f}%"

    def _fmtiv(v):
        try:
            return f"{_cs()}{float(v):.0f}" if v else "—"
        except Exception:
            return "—"

    # Build per-method rows using the individual method_iv_table from each scenario
    bear_method_ivs = bear.get("method_iv_table", {})
    base_method_ivs = base.get("method_iv_table", {})
    bull_method_ivs = bull.get("method_iv_table", {})

    # Build weight lookup from profile_weights stored on base scenario
    _pw_list = base.get("profile_weights", [])
    _pw_map: dict[str, float] = {pw["name"]: pw["weight"] for pw in _pw_list if "name" in pw and "weight" in pw}
    total_weight = sum(_pw_map.values()) or 1.0

    # Determine all methods that produced at least one non-None value across scenarios
    all_method_names: list[str] = []
    seen: set[str] = set()
    for scen_ivs in (bear_method_ivs, base_method_ivs, bull_method_ivs):
        for mn in scen_ivs:
            if mn not in seen:
                all_method_names.append(mn)
                seen.add(mn)

    method_header = [
        Paragraph(_wh("Method"),        styles["RptLabel"]),
        Paragraph(_wh("Wt"),            styles["RptLabel"]),
        Paragraph(_wh("Bear IV"),       styles["RptLabel"]),
        Paragraph(_wh("Base IV"),       styles["RptLabel"]),
        Paragraph(_wh("Bull IV"),       styles["RptLabel"]),
        Paragraph(_wh("EV (prob-wtd)"), styles["RptLabel"]),  # P0.1
    ]
    aw6 = [page_w * 0.25, page_w * 0.08, page_w * 0.17, page_w * 0.17, page_w * 0.17, page_w * 0.16]
    method_rows = [method_header]

    # Legs computed but carrying no weight are cross-checks: listed after the
    # blend under their own heading, never interleaved with the legs that vote.
    _xchk = set(base.get("cross_check_methods") or [])
    _in_blend = [mn for mn in all_method_names if mn not in _xchk]
    _xchk_names = [mn for mn in all_method_names if mn in _xchk]
    _ordered_names = _in_blend + ([None] if _xchk_names else []) + _xchk_names
    for mn in _ordered_names:
        if mn is None:
            method_rows.append([
                Paragraph("<i>Cross-checks (computed, not in the blend)</i>", styles["RptBody"]),
                "", "", "", "", ""])
            continue
        w = _pw_map.get(mn) if mn not in _xchk else None
        wt_str = (f"{w/total_weight:.0%}" if w else "—") if mn not in _xchk else "x-check"
        b_iv  = bear_method_ivs.get(mn)
        ba_iv = base_method_ivs.get(mn)
        bu_iv = bull_method_ivs.get(mn)
        # P0.1: per-method probability-weighted expected value
        _ev_m = None
        if b_iv is not None and ba_iv is not None and bu_iv is not None:
            _ev_m = float(b_iv) * _bear_p + float(ba_iv) * _base_p + float(bu_iv) * _bull_p
        method_rows.append([
            Paragraph(_strip(mn), styles["RptBody"]),
            Paragraph(wt_str, styles["RptValue"]),
            Paragraph(_fmtiv(b_iv),  styles["RptValue"]),
            Paragraph(_fmtiv(ba_iv), styles["RptValue"]),
            Paragraph(_fmtiv(bu_iv), styles["RptValue"]),
            Paragraph(_fmtiv(_ev_m), styles["RptValue"]),  # P0.1
        ])

    # Blended IV row — only 1 method available → label clearly
    blended_label = (
        "<b>Blended IV</b>" if methods_count > 1
        else "<b>Blended IV (single method)</b>"
    )
    # P0.1: probability-weighted blended EV across all methods
    _blended_ev = (
        float(bear_iv or 0) * _bear_p
        + float(base_iv or 0) * _base_p
        + float(bull_iv or 0) * _bull_p
    )
    method_rows.append([
        Paragraph(blended_label, styles["RptLabel"]),
        Paragraph("100%", styles["RptLabel"]),
        Paragraph(f"<b>{_fmtiv(bear_iv)}</b>", styles["RptLabel"]),
        Paragraph(f"<b>{_fmtiv(base_iv)}</b>", styles["RptLabel"]),
        Paragraph(f"<b>{_fmtiv(bull_iv)}</b>", styles["RptLabel"]),
        Paragraph(f"<b>{_fmtiv(_blended_ev)}</b>", styles["RptLabel"]),  # P0.1
    ])
    if current_price:
        method_rows.append([
            Paragraph("Current price", styles["RptBody"]),
            Paragraph("—", styles["RptValue"]),
            Paragraph(f"{_cs()}{current_price:.2f}", styles["RptValue"]),
            Paragraph(f"{_cs()}{current_price:.2f}", styles["RptValue"]),
            Paragraph(f"{_cs()}{current_price:.2f}", styles["RptValue"]),
            Paragraph("—", styles["RptValue"]),
        ])
        method_rows.append([
            Paragraph("<b>Upside / Downside vs Current Price</b>", styles["RptLabel"]),
            Paragraph("—", styles["RptLabel"]),
            Paragraph(f"<b>{_updown(bear_iv)}</b>", styles["RptLabel"]),
            Paragraph(f"<b>{_updown(base_iv)}</b>", styles["RptLabel"]),
            Paragraph(f"<b>{_updown(bull_iv)}</b>", styles["RptLabel"]),
            Paragraph(f"<b>{_updown(_blended_ev)}</b>", styles["RptLabel"]),  # P0.1
        ])

    mt = Table(method_rows, colWidths=aw6, hAlign="LEFT", repeatRows=1)
    mt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -4), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("BACKGROUND",    (0, -3), (-1, -1), C_PALE),
    ]))
    story.append(mt)
    story.append(Spacer(1, 6))

    # ── B. DCF Key Assumptions ───────────────────────────────────────────────
    story.append(Paragraph("B — DCF Key Assumptions", styles["RptSubsection"]))

    def _pct(v):
        try:
            return f"{float(v)*100:.1f}%"
        except Exception:
            return "—"

    bear_gr  = bear.get("growth_rate", 0)
    base_gr  = base.get("growth_rate", 0)
    bull_gr  = bull.get("growth_rate", 0)
    bear_fcf = bear.get("fcf_margin_start", 0)
    base_fcf = base.get("fcf_margin_start", 0)
    bull_fcf = bull.get("fcf_margin_start", 0)
    bear_tgr = bear.get("tgr", 0)
    base_tgr = base.get("tgr", 0)
    bull_tgr = bull.get("tgr", 0)
    bear_tv  = bear.get("tv_pct", 0)
    base_tv  = base.get("tv_pct", 0)
    bull_tv  = bull.get("tv_pct", 0)

    shares_str = (
        f"{shares/1e9:.2f}B" if shares and shares >= 1e9
        else (f"{shares/1e6:.0f}M" if shares else "—")
    )

    assump_hdr = [
        Paragraph(_wh("Parameter"), styles["RptLabel"]),
        Paragraph(_wh("Bear"),      styles["RptLabel"]),
        Paragraph(_wh("Base"),      styles["RptLabel"]),
        Paragraph(_wh("Bull"),      styles["RptLabel"]),
    ]
    # CHECK 2 traceability: pull the scenario margin delta for each scenario.
    # It is a ONE-SHOT absolute shift applied from Year 1 and held flat to
    # Year 10 — not a per-year drift. It was published under the name
    # `margin_delta_per_year` and this table labelled it "%/yr" accordingly,
    # which told the reader the margin kept moving every year when the engine
    # moves it once.
    bear_md = _margin_delta_abs(bear)
    base_md = _margin_delta_abs(base)
    bull_md = _margin_delta_abs(bull)

    def _pct_delta(v):
        """Format the one-shot margin delta as ±X.XXpp for traceability."""
        try:
            f = float(v) * 100
            return f"{f:+.2f}pp"
        except Exception:
            return "—"

    # Year-1 projected FCF margins = fcf_margin_start + the one-shot delta,
    # which is also the Year-2..10 margin — the engine holds it flat.
    bear_yr1_fcf = (float(bear_fcf or 0) + float(bear_md or 0)) * 100
    base_yr1_fcf = (float(base_fcf or 0) + float(base_md or 0)) * 100
    bull_yr1_fcf = (float(bull_fcf or 0) + float(bull_md or 0)) * 100

    def _pct_abs(v):
        try:
            return f"{float(v):.1f}%"
        except Exception:
            return "—"

    assump_rows = [
        assump_hdr,
        [Paragraph("Revenue Base", styles["RptBody"]),
         Paragraph(_fmt_billions(revenue_base), styles["RptValue"]),
         Paragraph(_fmt_billions(revenue_base), styles["RptValue"]),
         Paragraph(_fmt_billions(revenue_base), styles["RptValue"])],
        [Paragraph("Revenue Growth (applied)", styles["RptBody"]),
         Paragraph(_pct(bear_gr), styles["RptValue"]),
         Paragraph(_pct(base_gr), styles["RptValue"]),
         Paragraph(_pct(bull_gr), styles["RptValue"])],
        # CHECK 2 FIX: split margin into base-year anchor + one-shot delta + Year-1 projected
        [Paragraph("FCF Margin (base year, pre-delta)", styles["RptBody"]),
         Paragraph(_pct(bear_fcf), styles["RptValue"]),
         Paragraph(_pct(base_fcf), styles["RptValue"]),
         Paragraph(_pct(bull_fcf), styles["RptValue"])],
        [Paragraph("Margin delta (one-shot, Y1–Y10)", styles["RptBody"]),
         Paragraph(_pct_delta(bear_md), styles["RptValue"]),
         Paragraph(_pct_delta(base_md), styles["RptValue"]),
         Paragraph(_pct_delta(bull_md), styles["RptValue"])],
        [Paragraph("FCF Margin — Year 1 (projected)", styles["RptBody"]),
         Paragraph(_pct_abs(bear_yr1_fcf), styles["RptValue"]),
         Paragraph(_pct_abs(base_yr1_fcf), styles["RptValue"]),
         Paragraph(_pct_abs(bull_yr1_fcf), styles["RptValue"])],
        [Paragraph("WACC", styles["RptBody"]),
         Paragraph(_pct(wacc), styles["RptValue"]),
         Paragraph(_pct(wacc), styles["RptValue"]),
         Paragraph(_pct(wacc), styles["RptValue"])],
        [Paragraph("Terminal Growth Rate", styles["RptBody"]),
         Paragraph(_pct(bear_tgr), styles["RptValue"]),
         Paragraph(_pct(base_tgr), styles["RptValue"]),
         Paragraph(_pct(bull_tgr), styles["RptValue"])],
        [Paragraph("Terminal Value % of total", styles["RptBody"]),
         Paragraph(_pct(bear_tv) if bear_tv else "—", styles["RptValue"]),
         Paragraph(_pct(base_tv) if base_tv else "—", styles["RptValue"]),
         Paragraph(_pct(bull_tv) if bull_tv else "—", styles["RptValue"])],
        [Paragraph("Shares outstanding", styles["RptBody"]),
         Paragraph(shares_str, styles["RptValue"]),
         Paragraph("", styles["RptValue"]),
         Paragraph("", styles["RptValue"])],
        [Paragraph("Data source", styles["RptBody"]),
         Paragraph(_strip(data_src), styles["RptValue"]),
         Paragraph("", styles["RptValue"]),
         Paragraph("", styles["RptValue"])],
    ]

    aw2 = [page_w * 0.40, page_w * 0.20, page_w * 0.20, page_w * 0.20]
    at = Table(assump_rows, colWidths=aw2, hAlign="LEFT", repeatRows=1)
    at.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    story.append(at)
    story.append(Spacer(1, 6))

    # ── C. 10-Year Projection Table (base case) ──────────────────────────────
    if proj_rows:
        story.append(Paragraph(
            "C — 10-Year Revenue &amp; FCF Projection (Base Case)",
            styles["RptSubsection"],
        ))

        proj_hdr = [
            Paragraph(_wh("Year"),        styles["RptLabel"]),
            Paragraph(_wh("Revenue"),     styles["RptLabel"]),
            Paragraph(_wh("Growth"),      styles["RptLabel"]),
            Paragraph(_wh("FCF Margin"),  styles["RptLabel"]),
            Paragraph(_wh("FCF"),         styles["RptLabel"]),
            Paragraph(_wh("Disc. Factor"),styles["RptLabel"]),
            Paragraph(_wh("PV of FCF"),   styles["RptLabel"]),
        ]
        proj_table_rows = [proj_hdr]

        for row in proj_rows:
            proj_table_rows.append([
                Paragraph(str(row.get("year_label", "—")), styles["RptValue"]),
                Paragraph(_fmt_billions(row.get("revenue")),  styles["RptValue"]),
                Paragraph(_pct(row.get("growth_pct")),        styles["RptValue"]),
                Paragraph(_pct(row.get("fcf_margin")),        styles["RptValue"]),
                Paragraph(_fmt_billions(row.get("fcf")),      styles["RptValue"]),
                Paragraph(f"{row.get('discount_factor', 0):.3f}", styles["RptValue"]),
                Paragraph(_fmt_billions(row.get("pv_fcf")),   styles["RptValue"]),
            ])

        # Summary rows
        pv_fcf_base = dcf_data.get("pv_fcf_base", 0)
        pv_tv_base  = dcf_data.get("pv_tv_base", 0)
        if pv_fcf_base and shares:
            pv_fcf_abs = pv_fcf_base * shares
            pv_tv_abs  = pv_tv_base  * shares if pv_tv_base else None
            ev_abs     = (pv_fcf_abs + pv_tv_abs) if pv_tv_abs else None

            def _sum_row(label, val, bold=False):
                sty = styles["RptLabel"] if bold else styles["RptBody"]
                val_text = f"<b>{_fmt_billions(val)}</b>" if bold else _fmt_billions(val)
                return [
                    Paragraph(f"<b>{label}</b>" if bold else label, sty),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph(val_text, sty),
                ]

            proj_table_rows.append(_sum_row("PV of FCFs (Yr 1-10)", pv_fcf_abs, bold=True))
            if pv_tv_abs:
                proj_table_rows.append(_sum_row("Terminal Value (PV)", pv_tv_abs, bold=True))
            if ev_abs:
                proj_table_rows.append(_sum_row("Enterprise Value", ev_abs, bold=True))
            if net_debt is not None:
                proj_table_rows.append(_sum_row("Less: Net debt / (cash)", net_debt))
                eq_val = ev_abs - net_debt if ev_abs else None
                if eq_val:
                    proj_table_rows.append(_sum_row("Equity Value", eq_val, bold=True))
            if shares:
                shares_disp = (f"{shares/1e9:.2f}B" if shares >= 1e9
                               else f"{shares/1e6:.0f}M")
                proj_table_rows.append([
                    Paragraph("Shares outstanding", styles["RptBody"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph(shares_disp, styles["RptValue"]),
                ])
            if base_iv:
                proj_table_rows.append([
                    Paragraph("<b>Base Intrinsic Value / share</b>", styles["RptLabel"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph("", styles["RptValue"]),
                    Paragraph(f"<b>{_cs()}{base_iv:.2f}</b>", styles["RptLabel"]),
                ])

        pw = [page_w * 0.10, page_w * 0.15, page_w * 0.10, page_w * 0.13,
              page_w * 0.15, page_w * 0.12, page_w * 0.25]
        pt = Table(proj_table_rows, colWidths=pw, hAlign="LEFT", repeatRows=1)
        pt.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ]))
        story.append(pt)
        story.append(Spacer(1, 6))

    return story



# ── Report building blocks (page 1, valuation summary, decision) ──────────────

def _company_label(ticker: str, raw_financials: dict) -> str:
    """Company name: live lookup, else the name stored with the run, else the ticker."""
    try:
        name = _fetch_company_name(ticker)
    except Exception:  # noqa: BLE001
        name = None
    if name and name != ticker:
        return _strip(name)
    stored = (raw_financials or {}).get("company") if isinstance(raw_financials, dict) else None
    return _strip(stored) if stored else ticker


def _money(v, dp: int = 2) -> str:
    try:
        return f"{_cs()}{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _pct_s(v, dp: int = 1, signed: bool = True) -> str:
    try:
        return f"{float(v):+.{dp}f}%" if signed else f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return "—"


_BULLET_MARK = re.compile(r"^(?:[•·▪◦‣*-]|\d+[.)])\s+")


def _rationale_points(text: str) -> list[str]:
    """The PM rationale as its themes: one point per line/paragraph, markers dropped.

    Same split the web report uses (RationaleBlock): themes are separated by
    newlines; a leading bullet or number marker is removed.
    """
    lines = [l.strip() for l in re.split(r"\n+", _strip(text or "")) if l.strip()]
    return [_BULLET_MARK.sub("", l) for l in lines]


def _block_table(rows, col_w, styles, header: bool = True, total_rows: int = 0) -> Table:
    t = Table(rows, colWidths=col_w, hAlign="LEFT", repeatRows=1 if header else 0)
    st = [
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), C_PALE), ("TEXTCOLOR", (0, 0), (-1, 0), C_NAVY)]
    if total_rows:
        st += [("BACKGROUND", (0, -total_rows), (-1, -1), C_PALE)]
    t.setStyle(TableStyle(st))
    return t


def _decision_block(decision: dict, scen: dict, dcf_t: dict, styles, width: float) -> list:
    """Page 1, left column: rating, headline numbers, the PM thesis as bullets."""
    out: list = []
    rv = decision.get("research_view") or {}
    action = _strip(decision.get("action", "")).upper() or "—"
    rating = _strip(rv.get("rating_label") or decision.get("rating_label") or "")
    pill_txt = f"{rating.upper()}  ·  {action}" if rating else action
    pill = Table([[Paragraph(f'<font color="white"><b>{pill_txt}</b></font>', styles["RptLabel"])]],
                 hAlign="LEFT")
    pill.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    out += [pill, Spacer(1, 5)]

    price = scen.get("current_price") or rv.get("price")
    _unrated = ((dcf_t.get("rating_state") or {}).get("state") == "unrated")
    target = None if _unrated else (decision.get("price_target") or scen.get("12m_price_target"))
    iv = None if _unrated else ((scen.get("reconciliation") or {}).get("blended_iv") or rv.get("intrinsic_value")
                                or ((dcf_t.get("base") or {}).get("intrinsic_value")))
    up = ((float(target) - float(price)) / float(price) * 100
          if isinstance(target, (int, float)) and isinstance(price, (int, float)) and price else None)
    weight = decision.get("position_size_pct")
    tsr = rv.get("tsr_12m")
    cells = [
        ("12m target", _money(target), f"{_pct_s(up)} vs price" if up is not None else ""),
        ("Fair value (IV)", _money(iv), "blended intrinsic value"),
        ("Price", _money(price), _strip(str(rv.get("price_as_of") or ""))),
        ("Weight", f"{float(weight):.1%}" if isinstance(weight, (int, float)) else "—",
         _strip(decision.get("time_horizon", "")).title()),
    ]
    cw = width / len(cells)
    row = [[Paragraph(f'<font size="6.5" color="#555555">{a}</font><br/>'
                      f'<font size="11"><b>{b}</b></font><br/>'
                      f'<font size="6.5" color="#555555">{c}</font>', styles["RptBody"])
            for a, b, c in cells]]
    kt = Table(row, colWidths=[cw] * len(cells), hAlign="LEFT")
    kt.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, C_LGREY), ("INNERGRID", (0, 0), (-1, -1), 0.25, C_LGREY),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out += [kt, Spacer(1, 4)]
    if rv.get("callout"):
        out.append(Paragraph(_strip(rv["callout"]), styles["RptSource"]))
    points = _rationale_points(decision.get("rationale") or decision.get("reasoning") or "")
    for p in points:
        out.append(Paragraph(p, styles["RptBullet"], bulletText="•"))
    return out


def _p_of(scen: dict, s: str) -> str:
    v = (scen.get(s) or {}).get("probability")
    return f"{float(v):.0%}" if isinstance(v, (int, float)) else "—"


def _scenario_block(scen: dict, dcf_t: dict, styles, width: float) -> list:
    """Page 1, left column: bear / base / bull value, probability, 12m target."""
    if not scen:
        return []
    by12 = scen.get("12m_targets_by_scenario") or {}
    pb = (dcf_t.get("pt_bridge") or {}).get("scenarios") or {}
    names = ("bear", "base", "bull")
    # Unrated / Pre-Revenue: the scenarios are qualitative. The LLM's per-scenario
    # fair values and its expected value are illustrations, and printing them
    # under "Fair value (IV)" would publish the number the engine withheld.
    _rs = (dcf_t or {}).get("rating_state") or {}
    if _rs.get("state") == "unrated":
        out = [Paragraph(f"<b>{_strip(_rs.get('label') or 'Unrated')}</b>. No intrinsic value or 12-month "
                         f"target is published: {_strip(_rs.get('reason') or 'no valuation could be formed')}. "
                         "The scenarios below are qualitative.", styles["RptBody"]), Spacer(1, 4)]
        for s in names:
            a = _strip((scen.get(s) or {}).get("assumptions") or "")
            if a:
                out.append(Paragraph(f"<b>{s.title()}</b> ({_p_of(scen, s)}): {a}", styles["RptBody"]))
        return out

    def _p(s):
        v = (scen.get(s) or {}).get("probability")
        return f"{float(v):.0%}" if isinstance(v, (int, float)) else "—"

    def _fv(s):
        return (scen.get(s) or {}).get("fair_value") or (dcf_t.get(s) or {}).get("intrinsic_value")

    def _t(s):
        return by12.get(s) if by12.get(s) is not None else (pb.get(s) or {}).get("target")

    hdr = [Paragraph(_wh(x), styles["RptLabel"]) for x in ("", "Bear", "Base", "Bull")]
    rows = [hdr,
            [Paragraph("Probability", styles["RptBody"])] + [Paragraph(_p(s), styles["RptValue"]) for s in names],
            [Paragraph("Fair value (IV)", styles["RptBody"])] + [Paragraph(_money(_fv(s)), styles["RptValue"]) for s in names],
            [Paragraph("12m target", styles["RptBody"])] + [Paragraph(_money(_t(s)), styles["RptValue"]) for s in names]]
    tot = 0
    if scen.get("12m_price_target") is not None:
        rows.append([Paragraph("<b>12m target (probability-weighted)</b>", styles["RptLabel"]),
                     "", Paragraph(f"<b>{_money(scen['12m_price_target'])}</b>", styles["RptLabel"]), ""])
        tot += 1
    if scen.get("expected_value") is not None:
        up = scen.get("upside_pct")
        rows.append([Paragraph("<b>Expected value (probability-weighted IV)</b>", styles["RptLabel"]),
                     "", Paragraph(f"<b>{_money(scen['expected_value'])}</b>"
                                   + (f"  ({_pct_s(up)})" if up is not None else ""), styles["RptLabel"]), ""])
        tot += 1
    t = _block_table(rows, [width * 0.40, width * 0.20, width * 0.20, width * 0.20], styles, total_rows=tot)
    out = [t, Spacer(1, 4)]
    for s in names:
        a = _strip((scen.get(s) or {}).get("assumptions") or "")
        if a:
            out.append(Paragraph(f"<b>{s.title()}:</b> {a}", styles["RptBody"]))
    return out


def _intel_compact(short_int: dict, earn_q: dict, insider_act: dict, news_sent: dict,
                   analyst_rev: dict, styles, width: float) -> list:
    """Page 1, side column: one line per intelligence signal."""
    rows = [[Paragraph(_wh("Signals"), styles["RptLabel"]), Paragraph(_wh("Reading"), styles["RptLabel"])]]
    if earn_q:
        sc = _getv(earn_q, "overall_quality_score")
        v = _strip(str(_getv(earn_q, "quality_verdict", "—")))
        rows.append(["Earnings quality", f"{v} ({sc:.1f}/10)" if isinstance(sc, (int, float)) else v])
    if short_int:
        sf = _getv(short_int, "short_float_pct")
        v = _strip(str(_getv(short_int, "signal", "—")))
        rows.append(["Short interest", f"{v} ({sf:.1f}% float)" if isinstance(sf, (int, float)) else v])
    if insider_act:
        rows.append(["Insider activity", _strip(str(_getv(insider_act, "signal", "—")))])
    if news_sent:
        rows.append(["News sentiment", _strip(str(_getv(news_sent, "signal", "—")))])
    if analyst_rev:
        rows.append(["Analyst revisions", _strip(str(_getv(analyst_rev, "revision_direction", "—")))])
    if len(rows) == 1:
        return []
    body = [rows[0]] + [[Paragraph(a, styles["RptBody"]), Paragraph(b.replace("_", " "), styles["RptValue"])]
                        for a, b in rows[1:]]
    return [_block_table(body, [width * 0.50, width * 0.50], styles)]


_SEG_TYPE_LABEL = {
    "refining": "Refining", "midstream": "Midstream", "chemicals": "Chemicals",
    "renewable_fuels": "Renewable fuels", "ethanol": "Ethanol",
    "fuel_marketing": "Fuel marketing", "upstream": "Upstream",
    "oilfield_services": "Oilfield services", "equity_method": "Equity-accounted",
    "non_business": "Not a business",
}


def _degraded_sotp_block_pdf(dcf_t: dict, styles, width: float) -> list:
    """Owner, 2026-09-26: a Degraded analyst SOTP prints its rows and the reason
    each segment could not price, and says plainly that no per-share value is
    published. Before this the block returned nothing and the reason lived only
    in a flag the PDF never printed."""
    d = dcf_t.get("sotp_analyst_degraded") or {}
    rows = d.get("rows") or []
    if not rows:
        return []
    st_l = ParagraphStyle("_dsl", fontName="Helvetica", fontSize=6.5, leading=8)
    st_lb = ParagraphStyle("_dslb", parent=st_l, fontName="Helvetica-Bold")

    def _bn(v):
        try:
            return f"{float(v) / 1e9:,.2f}bn"
        except (TypeError, ValueError):
            return "—"
    body = [[Paragraph(_wh(h), st_lb) for h in ("Segment", "Fwd revenue (USD)", "Method", "Value (USD)", "Reason")]]
    for r in rows:
        body.append([Paragraph(_strip(str(r.get("name") or "")), st_l), Paragraph(_bn(r.get("revenue_fwd")), st_l),
                     Paragraph(_strip(str(r.get("method") or "")), st_l),
                     Paragraph(_bn(r.get("value")) if r.get("value") is not None else "not priced", st_l),
                     Paragraph(_strip(str(r.get("degraded_reason") or "")), st_l)])
    t = Table(body, colWidths=[width * 0.24, width * 0.14, width * 0.12, width * 0.12, width * 0.38], hAlign="LEFT")
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 6.5), ("LINEBELOW", (0, 0), (-1, 0), 0.5, C_NAVY),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [Paragraph("<b>Analyst sum-of-the-parts — Degraded, not published</b>", styles["RptLabel"]),
            Paragraph(_strip(str(d.get("degraded_reason") or "")), st_l), Spacer(1, 3), t, Spacer(1, 4)]


def _analyst_sotp_block_pdf(dcf_t: dict, styles, width: float) -> list:
    """The analyst sum-of-the-parts (dcf_range.sotp_breakdown): each segment's
    forward revenue, the method and multiple it was valued on, its value, then
    the bridge -- associates, net cash, holdco discount -- to a per-share figure.
    Web (both render paths) and the Excel model already show it; the PDF did not.
    """
    b = dcf_t.get("sotp_breakdown") or {}
    rows = b.get("rows") or []
    if not rows or b.get("per_share_reporting") is None:
        return _degraded_sotp_block_pdf(dcf_t, styles, width)
    ccy = b.get("reporting_currency") or ""
    st_l = ParagraphStyle("_asl", fontName="Helvetica", fontSize=6.5, leading=8)
    st_lb = ParagraphStyle("_aslb", parent=st_l, fontName="Helvetica-Bold")
    st_v = ParagraphStyle("_asv", parent=st_l, alignment=2)

    def _bn(v):
        try:
            return f"{float(v) / 1e9:,.2f}bn"
        except (TypeError, ValueError):
            return "—"

    def _x(v):
        try:
            return f"{float(v):.1f}x"
        except (TypeError, ValueError):
            return "—"

    hdr = [Paragraph(_wh(h), st_lb) for h in ("Segment", "Fwd revenue (USD)", "Method", "Multiple", "Value (USD)")]
    body = [hdr]
    for r in rows:
        body.append([Paragraph(_strip(str(r.get("name") or "")), st_l), Paragraph(_bn(r.get("revenue_fwd")), st_v),
                     Paragraph(_strip(str(r.get("method") or "")), st_l), Paragraph(_x(r.get("multiple")), st_v),
                     Paragraph(_bn(r.get("value")), st_v)])
    for label, key in (("Sum of segments", "segment_value"), ("+ Associates / investments", "associates"),
                       ("+ Net cash", "net_cash"), ("= NAV", "nav")):
        body.append([Paragraph(f"<b>{label}</b>", st_l), "", "", "", Paragraph(_bn(b.get(key)), st_v)])
    body.append([Paragraph(f"<b>- Holdco discount ({_pct_s(b.get('holdco_discount_pct'), signed=False)})</b>", st_l),
                 "", "", "", Paragraph(_bn(b.get("holdco_discount")), st_v)])
    body.append([Paragraph("<b>= Equity value</b>", st_l), "", "", "", Paragraph(_bn(b.get("final")), st_v)])
    body.append([Paragraph(f"<b>Per share ({ccy})</b>", st_l), "", "", "",
                 Paragraph(f"<b>{_money(b.get('per_share_reporting'))}</b>", st_v)])
    lab_w = width * 0.30
    col_w = (width - lab_w) / 4.0
    t = Table(body, colWidths=[lab_w, col_w, col_w, col_w, col_w])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black),
                           ("LINEABOVE", (0, len(rows) + 1), (-1, len(rows) + 1), 0.4, colors.black),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    out = [Paragraph("Sum of the parts (analyst)", styles["RptLabel"])]
    if b.get("sentence"):
        out.append(Paragraph(_strip(str(b["sentence"])), styles["RptBody"]))
    out += [t, Spacer(1, 4)]
    if (b.get("sources") or {}).get("all") in ("gemini_grounded", "gemini_accepted"):
        out.append(Paragraph("Segments and multiple ranges: owner-accepted, cited inputs (midpoint of each "
                             "range applied). Source: Financial Modeling Prep for the group anchors.",
                             styles["RptBody"]))
    return out


def _segment_sotp_block_pdf(dcf_t: dict, styles, width: float) -> list:
    """The segment sum-of-the-parts, with the arithmetic behind each part.

    Printed under Valuation Summary (owner, 2026-09-20). Every column is a step
    of the calculation -- revenue, the margin used to estimate segment EBITDA,
    the through-cycle band and where in it the multiple sits -- because the
    figure this replaced was a single unexplained number and it was wrong by
    an order of magnitude.
    """
    b = dcf_t.get("segment_sotp") or {}
    rows = b.get("segments") or []
    if not rows:
        return []
    # The report's price currency is already set for this ticker by
    # _set_price_currency; the block is in that same currency.
    sym = _cs()
    lab_w = width * 0.26
    col_w = (width - lab_w) / 6.0
    st_l = ParagraphStyle("_ssl", fontName="Helvetica", fontSize=6.5, leading=8)
    st_lb = ParagraphStyle("_sslb", parent=st_l, fontName="Helvetica-Bold")
    st_v = ParagraphStyle("_ssv", parent=st_l, alignment=2)
    st_vb = ParagraphStyle("_ssvb", parent=st_v, fontName="Helvetica-Bold")

    def bn(v):
        if not isinstance(v, (int, float)):
            return "--"
        return f"{sym}{v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"{sym}{v / 1e6:,.0f}M"

    def pct(v):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "--"

    def mult(r):
        if (r.get("basis") or "") == "carrying_value":
            return "at book"
        m = r.get("multiple")
        if not isinstance(m, (int, float)):
            return "--"
        band = r.get("band") or []
        return (f"{m:.2f}x ({band[0]:g}-{band[1]:g}x)"
                if len(band) == 2 else f"{m:.2f}x")

    head = ["Segment", "Revenue", "Margin", "EBITDA est.", "Multiple", "EV", "% of EV"]
    data = [[Paragraph(f"<b>{_wh(h)}</b>", st_vb if i else st_lb) for i, h in enumerate(head)]]
    for r in rows:
        seg = _strip(str(r.get("segment") or ""))
        ty = _SEG_TYPE_LABEL.get(r.get("type") or "", r.get("type") or "")
        if str(r.get("multiple_source") or "").startswith("dynamic"):
            ty = f"{ty} - dynamic multiple"
        data.append([
            Paragraph(f"{seg}<br/><font size=5.5 color='#666666'>{_strip(str(ty))}</font>", st_l),
            Paragraph(bn(r.get("revenue")), st_v),
            Paragraph(pct(r.get("ebitda_margin")), st_v),
            Paragraph(bn(r.get("ebitda")), st_v),
            Paragraph(mult(r), st_v),
            Paragraph(bn(r.get("ev")), st_v),
            Paragraph(pct(r.get("share_of_ev")), st_v),
        ])
    _chk = b.get("checks") or {}
    data.append([Paragraph("<b>Total enterprise value</b>", st_lb),
                 Paragraph("", st_v), Paragraph("", st_v), Paragraph("", st_v),
                 Paragraph("", st_v), Paragraph(f"<b>{bn(b.get('total_ev'))}</b>", st_vb),
                 Paragraph(f"<b>{pct(_chk.get('share_of_ev_sum'))}</b>", st_vb)])
    t = Table(data, colWidths=[lab_w] + [col_w] * 6, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 0.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.8),
        ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, C_NAVY),
        ("LINEABOVE", (0, len(data) - 1), (-1, len(data) - 1), 0.5, C_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    ps = b.get("value_per_share")
    lead = ("Sum of the parts by business segment"
            + (f" -- {_money(ps)} per share" if isinstance(ps, (int, float)) else ""))
    out = [Spacer(1, 6), Paragraph(lead, styles["RptLabel"]), Spacer(1, 2), t]
    for _rem in (_chk.get("reminders") or []):
        out += [Spacer(1, 2), Paragraph(f"<b>{_strip(str(_rem))}</b>", styles["RptBody"])]
    # Why each band sits where it does, once per business type.
    _seen: set = set()
    _notes = []
    for r in rows:
        ty = r.get("type") or ""
        if r.get("band_rationale") and ty and ty not in _seen:
            _seen.add(ty)
            _notes.append((_SEG_TYPE_LABEL.get(ty, ty), _strip(str(r["band_rationale"]))))
    if _notes:
        out += [Spacer(1, 3), Paragraph("Band rationale", styles["RptLabel"])]
        for lab, text in _notes:
            out += [Paragraph(f"<font size=5.8><b>{lab}:</b> {text}</font>", styles["RptBody"])]
    note = _strip(str(b.get("basis_note") or ""))
    if note:
        out += [Spacer(1, 2), Paragraph(f"<font size=5.5 color='#666666'>{note}</font>",
                                        styles["RptBody"])]
    return out


def _valuation_summary(dcf_t: dict, scen: dict, styles, page_w: float) -> list:
    """Intrinsic value → 12m target, per scenario, by the one rule every name uses."""
    pb = dcf_t.get("pt_bridge") or {}
    sc = pb.get("scenarios") or {}
    _rs = dcf_t.get("rating_state") or {}
    if _rs.get("state") == "unrated":
        return [Paragraph(f"No 12-month target: Unrated — {_strip(str(_rs.get('reason') or ''))}. "
                          f"Method values below are indicative only.", styles["RptBody"])]
    if not sc:
        return [Paragraph("Target derivation not recorded for this run.", styles["RptBody"])]
    spot, cap = pb.get("spot"), pb.get("capture")
    out = [Paragraph(
        f"12-month target = price + capture × (intrinsic value − price). "
        f"Price {_money(spot)}; capture {float(cap):.0%} of the gap to fair value over 12 months."
        if isinstance(cap, (int, float)) else _strip(pb.get("rule") or ""), styles["RptBody"]), Spacer(1, 3)]
    hdr = [Paragraph(_wh(x), styles["RptLabel"])
           for x in ("Scenario", "Probability", "Intrinsic value", "Gap to price", "12m target", "vs price")]
    rows = [hdr]
    for s in ("bear", "base", "bull"):
        d = sc.get(s) or {}
        iv, tg = d.get("intrinsic_value"), d.get("target")
        p = (scen.get(s) or {}).get("probability")
        gap = (float(iv) - float(spot)) if isinstance(iv, (int, float)) and isinstance(spot, (int, float)) else None
        vs = ((float(tg) - float(spot)) / float(spot) * 100
              if isinstance(tg, (int, float)) and isinstance(spot, (int, float)) and spot else None)
        rows.append([Paragraph(s.title(), styles["RptBody"]),
                     Paragraph(f"{float(p):.0%}" if isinstance(p, (int, float)) else "—", styles["RptValue"]),
                     Paragraph(_money(iv), styles["RptValue"]),
                     Paragraph(_money(gap) if gap is not None else "—", styles["RptValue"]),
                     Paragraph(_money(tg), styles["RptValue"]),
                     Paragraph(_pct_s(vs) if vs is not None else "—", styles["RptValue"])])
    pw = scen.get("12m_price_target")
    rows.append([Paragraph("<b>Probability-weighted</b>", styles["RptLabel"]), "",
                 Paragraph(f"<b>{_money(scen.get('expected_value'))}</b>", styles["RptLabel"]), "",
                 Paragraph(f"<b>{_money(pw)}</b>", styles["RptLabel"]),
                 Paragraph(f"<b>{_pct_s((float(pw) - float(spot)) / float(spot) * 100)}</b>"
                           if isinstance(pw, (int, float)) and isinstance(spot, (int, float)) and spot
                           else "", styles["RptLabel"])])
    out.append(_block_table(rows, [page_w * w for w in (0.20, 0.14, 0.18, 0.16, 0.16, 0.16)],
                            styles, total_rows=1))
    return out


def _decision_section(decision: dict, scen: dict, dcf_t: dict, styles, page_w: float) -> list:
    """Final section: the trade and the rating, as decided."""
    rv = decision.get("research_view") or {}
    er = decision.get("entry_range")
    entry = (f"{_money(er[0])} – {_money(er[1])}"
             if isinstance(er, list) and len(er) == 2 and all(isinstance(x, (int, float)) for x in er) else "—")
    _rs = dcf_t.get("rating_state") or {}
    _unrated = _rs.get("state") == "unrated"
    iv = (scen.get("reconciliation") or {}).get("blended_iv") or rv.get("intrinsic_value")
    w = decision.get("position_size_pct")
    rows = [
        ("Rating", "UNRATED" if _unrated else _strip(rv.get("rating_label") or decision.get("rating_label") or "—")),
        ("Trade action", _strip(decision.get("action", "—")).upper()),
        # Owner, 2026-09-26: an Unrated name publishes no target and no fair value.
        ("12-month target", f"N/A — Unrated: {_strip(str(_rs.get('reason') or ''))}" if _unrated
         else _money(decision.get("price_target") or scen.get("12m_price_target"))),
        ("Fair value (IV)", "N/A (indicative figures in the method table)" if _unrated else _money(iv)),
        ("Price", _money(scen.get("current_price") or rv.get("price"))),
        ("Position weight", f"{float(w):.1%}" if isinstance(w, (int, float)) else "—"),
        ("Entry range", entry),
        ("Stop loss", _money(decision.get("stop_loss"))),
        ("Time horizon", _strip(decision.get("time_horizon", "—")).title()),
    ]
    tsr = rv.get("tsr_12m")
    if isinstance(tsr, (int, float)):
        bm = (rv.get("benchmark") or {})
        rows.append(("12m total return", f"{tsr:+.1%}"
                     + (f" vs {bm.get('name')} {float(bm.get('expected_return')):.1%}"
                        if bm.get("name") and isinstance(bm.get("expected_return"), (int, float)) else "")))
    out = [_block_table([[Paragraph(a, styles["RptLabel"]), Paragraph(b, styles["RptValue"])] for a, b in rows],
                        [page_w * 0.26, page_w * 0.74], styles, header=False), Spacer(1, 4)]
    for key in ("callout", "rating_definition", "disclaimer"):
        if rv.get(key):
            out.append(Paragraph(_strip(rv[key]), styles["RptSource"] if key != "callout" else styles["RptBody"]))
    return out



# ── Financial statements page (sell-side two-column layout) ────────────────────

_PER_SHARE_HINTS = ("eps", "per_share", "bvps", "dps")


def _is_per_share(row: dict) -> bool:
    k = str(row.get("key") or "").lower()
    lab = str(row.get("label") or "").lower()
    return any(h in k for h in _PER_SHARE_HINTS) or "per share" in lab or "eps" in lab


def _fs_num(v, per_share: bool) -> str:
    """Millions to one decimal (per-share lines unscaled, two decimals); negatives in parentheses."""
    if not isinstance(v, (int, float)):
        return "\u2013"
    x = float(v) if per_share else float(v) / 1e6
    body = f"{abs(x):,.2f}" if per_share else f"{abs(x):,.1f}"
    return f"({body})" if x < 0 else body


def _fs_pct(v) -> str:
    """A ratio as a percentage to one decimal; negatives in parentheses."""
    if not isinstance(v, (int, float)):
        return "–"
    x = float(v) * 100
    return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"


def _fs_table(title: str, periods: list, rows: list, styles, width: float,
              pct_rows: bool = False) -> list:
    """One statement block: title rule, period header, rows (bold = subtotal)."""
    lab_w = width * 0.40
    col_w = (width - lab_w) / max(len(periods), 1)
    st_l = ParagraphStyle("_fsl", fontName="Helvetica", fontSize=6.5, leading=8)
    st_lb = ParagraphStyle("_fslb", parent=st_l, fontName="Helvetica-Bold")
    st_v = ParagraphStyle("_fsv", parent=st_l, alignment=2)
    st_vb = ParagraphStyle("_fsvb", parent=st_v, fontName="Helvetica-Bold")
    data = [[Paragraph("", st_l)] + [Paragraph(f"<b>{_strip(str(p)).replace('FY', 'FY')}</b>", st_vb)
                                      for p in periods]]
    bold_rows = []
    for r in rows:
        emph = bool(r.get("emphasis"))
        ind = "&nbsp;&nbsp;" * int(r.get("indent") or 0)
        vals = r.get("values") or {}
        if pct_rows:
            cells = [_fs_pct(vals.get(p)) for p in periods]
        else:
            ps = _is_per_share(r)
            cells = [_fs_num(vals.get(p), ps) for p in periods]
        data.append([Paragraph(ind + _strip(str(r.get("label") or "")), st_lb if emph else st_l)]
                    + [Paragraph(c, st_vb if emph else st_v) for c in cells])
        if emph:
            bold_rows.append(len(data) - 1)
    t = Table(data, colWidths=[lab_w] + [col_w] * len(periods), hAlign="LEFT")
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 0.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, C_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    t.setStyle(TableStyle(style))
    head = ParagraphStyle("_fsh", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=C_NAVY)
    return [Paragraph(title, head), HRFlowable(width="100%", thickness=0.75, color=C_NAVY, spaceAfter=2),
            t, Spacer(1, 8)]


def _fs_ratio_rows(stmts: dict, periods: list) -> list:
    """Growth & margins derived from the statement rows themselves."""
    def row(section, key):
        for r in (stmts.get(section) or {}).get("rows") or []:
            if r.get("key") == key:
                return r.get("values") or {}
        return {}

    rev = row("income", "revenue")
    out = []

    def add(label, fn):
        vals = {}
        for i, p in enumerate(periods):
            try:
                v = fn(i, p)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):   # line absent that year
                v = None
            vals[p] = v
        if any(isinstance(v, (int, float)) for v in vals.values()):
            out.append({"label": label, "values": vals})

    def growth(src):
        return lambda i, p: (src[p] / src[periods[i - 1]] - 1) if i > 0 and src.get(periods[i - 1]) else None

    def margin(src):
        return lambda i, p: src[p] / rev[p] if rev.get(p) else None

    ebitda, ni = row("income", "ebitda"), row("income", "net_income")
    gp, op = row("income", "gross_profit"), row("income", "operating_income")
    fcf = row("cashflow", "free_cash_flow")
    eq = row("balance", "shareholders_equity") or row("balance", "total_equity")
    add("Revenue growth", growth(rev))
    add("EBITDA growth", growth(ebitda))
    add("Net income growth", growth(ni))
    add("Gross margin", margin(gp))
    add("EBITDA margin", margin(ebitda))
    add("Operating margin", margin(op))
    add("Net margin", margin(ni))
    add("FCF margin", margin(fcf))
    add("ROE", lambda i, p: ni[p] / eq[p] if eq.get(p) else None)
    return out


def _financial_statements_page(fs: dict, styles, page_w: float) -> list:
    """Income statement, balance sheet and cash flow, last four fiscal years."""
    if not isinstance(fs, dict) or not fs.get("statements"):
        return []
    stmts = fs["statements"]
    periods = list(fs.get("periods") or [])[-4:]
    if not periods:
        return []
    ccy = _strip(str(fs.get("currency") or "")).upper()
    unit = f"{ccy} mn" if ccy else "mn"
    gutter = 7 * mm
    col = (page_w - gutter) / 2
    left, right = [], []
    ratios = _fs_ratio_rows(stmts, periods)
    if ratios:
        left += _fs_table("Growth &amp; Margins (%)", periods, ratios, styles, col, pct_rows=True)
    if stmts.get("income"):
        left += _fs_table(f"Income Statement ({unit})", periods, stmts["income"].get("rows") or [], styles, col)
    if stmts.get("balance"):
        right += _fs_table(f"Balance Sheet ({unit})", periods, stmts["balance"].get("rows") or [], styles, col)
    if stmts.get("cashflow"):
        right += _fs_table(f"Cash Flow ({unit})", periods, stmts["cashflow"].get("rows") or [], styles, col)
    for k, st in stmts.items():                                  # any further section (e.g. bank layouts)
        if k not in ("income", "balance", "cashflow") and isinstance(st, dict) and st.get("rows"):
            (left if len(left) <= len(right) else right).extend(
                _fs_table(f"{_strip(str(st.get('title') or k))} ({unit})", periods, st["rows"], styles, col))
    grid = Table([[left, right]], colWidths=[col + gutter, col], hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), gutter),
        ("RIGHTPADDING", (1, 0), (1, 0), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [grid, Paragraph("Source: company filings via Financial Modeling Prep. Fiscal years as reported; "
                            "per-share lines unscaled.", styles["RptSource"])]


# ── Full-width section header bar ──────────────────────────────────────────────
def _section_header(text: str, page_w: float) -> list:
    """GS-style section header: bold navy text + 0.75pt navy rule. Returns a list."""
    _sty = ParagraphStyle(
        name="_sh_inner",
        fontName="Helvetica-Bold", fontSize=9.5, leading=12,
        textColor=C_NAVY, spaceBefore=8, spaceAfter=2,
    )
    return [
        Paragraph(text, _sty),
        HRFlowable(width="100%", thickness=0.75, color=C_NAVY, spaceAfter=4),
    ]


# ── Per-ticker splitter ─────────────────────────────────────────────────────────
def generate_pdf_reports_per_ticker(result: dict) -> list[str]:
    """Generate one PDF per ticker from a multi-ticker pipeline result.

    For a single-ticker result this is equivalent to ``generate_pdf_report(result)``
    returning ``[path]``.

    For a multi-ticker result the pipeline's shared context (macro_regime, sector,
    industry_brief, deep_research, etc.) is replicated into every per-ticker
    sub-result, while per-ticker dicts (decisions, dcf_range, scenario_analysis,
    power_law_analysis, value_trap_analysis, analyst_signals,
    short_interest, earnings_quality, insider_activity, news_sentiment,
    analyst_revisions) are sliced so each sub-result contains only that ticker.

    Each sub-result then has ``len(decisions) == 1`` and ``generate_pdf_report``
    automatically selects the full single-ticker cover-page layout.

    Returns:
        List of absolute paths to the generated PDFs (one per ticker, in order).
    """
    decisions = result.get("decisions") or {}
    tickers   = list(decisions.keys())

    if len(tickers) <= 1:
        # Nothing to split — call existing function directly
        return [generate_pdf_report(result)]

    # Keys whose values are {ticker: ...} dicts that must be sliced per run
    _PER_TICKER_KEYS = (
        "decisions",
        "dcf_range",
        "scenario_analysis",
        "power_law_analysis",
        "value_trap_analysis",
        "short_interest",
        "earnings_quality",
        "insider_activity",
        "news_sentiment",
        "analyst_revisions",
    )

    # analyst_signals has shape {agent_key: {ticker: signal}} — needs separate handling
    analyst_signals_full = result.get("analyst_signals") or {}

    paths: list[str] = []
    for ticker in tickers:
        # Start with all non-per-ticker shared fields (macro, sector, brief, etc.)
        sub: dict = {
            k: v for k, v in result.items()
            if k not in _PER_TICKER_KEYS and k != "analyst_signals"
        }

        # Slice per-ticker dicts → only keep entries for this ticker
        for key in _PER_TICKER_KEYS:
            full = result.get(key) or {}
            sub[key] = {ticker: full[ticker]} if ticker in full else {}

        # Slice analyst_signals: {agent_key: {ticker: signal}} → keep only this ticker
        sub["analyst_signals"] = {
            agent_key: {ticker: sig_map[ticker]}
            for agent_key, sig_map in analyst_signals_full.items()
            if isinstance(sig_map, dict) and ticker in sig_map
        }

        paths.append(generate_pdf_report(sub))

    return paths


# ── Main entry point ───────────────────────────────────────────────────────────
def generate_pdf_report(result: dict, output_path: str | None = None,
                        open_after: bool = True) -> str:
    """
    Generate an untruncated PDF investment report from the advanced pipeline
    result dict.

    Args:
        result:      The dict returned by run_advanced_pipeline().
        output_path: Optional file path.  Defaults to
                     report_<TICKERS>_<YYYYMMDD_HHMM>.pdf in the cwd.
        open_after:  Open the file in the desktop viewer (CLI use). The web
                     export passes False: a server must never launch a viewer.

    Returns:
        Absolute path of the saved PDF.

    Side-effects:
        Prints a truncation-validation summary to stdout after saving.
    """
    styles = _build_styles()

    # ── Resolve output path ──────────────────────────────────────────────────
    if output_path is None:
        os.makedirs(_REPORTS_FOLDER, exist_ok=True)
        tickers    = list(result.get("decisions", {}).keys())
        ticker_str = "_".join(tickers) if tickers else "report"
        date_str   = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(_REPORTS_FOLDER, f"report_{ticker_str}_{date_str}.pdf")
    output_path = os.path.abspath(output_path)

    # ── Page geometry ────────────────────────────────────────────────────────
    margin  = 16 * mm
    page_w = A4[0] - 2 * margin
    col1   = page_w * 0.26
    col2   = page_w * 0.74
    _top, _bot = 20 * mm, 18 * mm                  # room for running header/footer
    _body_h = A4[1] - _top - _bot
    # Page 1: title band, then the decision (wide, left) beside a narrow column
    # of market context (price chart, key financials, signals) on the right.
    _title_h = 20 * mm
    _gutter  = 5 * mm
    left_w   = page_w * 0.63
    side_w   = page_w - left_w - _gutter
    _np = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    _cover = PageTemplate(id="cover", frames=[
        Frame(margin, _bot + _body_h - _title_h, page_w, _title_h, id="title", **_np),
        Frame(margin + left_w + _gutter, _bot, side_w, _body_h - _title_h - 2 * mm, id="side", **_np),
        Frame(margin, _bot, left_w, _body_h - _title_h - 2 * mm, id="main", **_np),
    ])
    _body = PageTemplate(id="body", frames=[Frame(margin, _bot, page_w, _body_h, id="body", **_np)])
    doc = BaseDocTemplate(
        output_path, pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=_top, bottomMargin=_bot,
    )

    # ── Text collector for truncation validation ─────────────────────────────
    _all_text: list[str] = []

    def _collect(text: str) -> str:
        """Strip ANSI, record for validation, return clean string."""
        t = _strip(text)
        if t:
            _all_text.append(t)
        return t

    story: list = []

    # ── Unpack top-level result keys ─────────────────────────────────────────
    decisions       = result.get("decisions", {})
    macro           = result.get("macro_regime") or {}
    sector          = result.get("sector", "—")
    _ib_raw         = result.get("industry_brief", "") or ""
    # If the LLM failed to populate brief_text the specialist writes an error
    # sentinel.  In that case prefer deep_research (live result), then fall back
    # to deep_research_text stored in the SQLite archive for this run.
    if not _ib_raw or _ib_raw.startswith(_BRIEF_ERROR_PREFIX):
        _ib_raw = (
            result.get("deep_research", "")
            or _fetch_db_brief(list(decisions.keys()))
            or _ib_raw   # keep error sentinel only as last resort
        )
    industry_brief  = _ib_raw
    analyst_signals = result.get("analyst_signals", {})
    scenario        = result.get("scenario_analysis") or {}
    power_law       = result.get("power_law_analysis") or {}
    value_trap      = result.get("value_trap_analysis") or {}
    raw_financials   = result.get("raw_financials") or {}
    short_interest   = result.get("short_interest") or {}
    earnings_qual    = result.get("earnings_quality") or {}
    insider_activity = result.get("insider_activity") or {}
    news_sentiment   = result.get("news_sentiment") or {}
    analyst_revisions = result.get("analyst_revisions") or {}
    tickers          = list(decisions.keys())
    run_date        = datetime.now().strftime("%d %B %Y  %H:%M")
    _single = len(tickers) == 1
    doc.addPageTemplates([_cover, _body] if _single else [_body])
    _set_price_currency(
        ((result.get("dcf_range") or {}).get(tickers[0]) or {}).get("reported_currency")
        if tickers else None)

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — INVESTMENT DECISION + SCENARIO ANALYSIS (page 1)
    # Decision first and widest; market context in a narrow right-hand column.
    # ═══════════════════════════════════════════════════════════════════════════
    _ph_pipeline = result.get("price_history", {})

    def _side_column(t: str, width: float) -> list:
        col: list = []
        _ph_raw = _ph_pipeline.get(t, [])
        _ph = [(p["date"], p["close"]) for p in _ph_raw] if _ph_raw else _fetch_price_history(t)
        if _ph:
            col += [Paragraph("12-month price", styles["RptLabel"]), Spacer(1, 2),
                    _PriceSparkline(_ph, decisions.get(t, {}).get("price_target"), width), Spacer(1, 6)]
        _kf = _key_financials_table(raw_financials, styles, width, years=3)
        if _kf:
            col += [Paragraph("Key financials", styles["RptLabel"]), Spacer(1, 2), _kf, Spacer(1, 6)]
        col += _intel_compact(short_interest.get(t) or {}, earnings_qual.get(t) or {},
                              insider_activity.get(t) or {}, news_sentiment.get(t) or {},
                              analyst_revisions.get(t) or {}, styles, width)
        return col

    _dash = "\u2014"
    _bar = "|"
    for _i, _t in enumerate(tickers):
        _dec   = decisions.get(_t, {})
        _scn   = scenario.get(_t, {})
        _dcf_t = (result.get("dcf_range") or {}).get(_t, {})
        _collect(_dec.get("rationale", _dec.get("reasoning", "")))
        _price = _scn.get("current_price")
        _tgt   = _dec.get("price_target") or _scn.get("12m_price_target")
        _up    = ((_tgt - _price) / _price * 100
                  if isinstance(_tgt, (int, float)) and isinstance(_price, (int, float)) and _price else None)
        _up_s  = _pct_s(_up) if _up is not None else _dash
        _hz    = _strip(_dec.get("time_horizon", _dash)).title()
        _title = [
            Paragraph(f"{_company_label(_t, raw_financials)} ({_t})", styles["RptTitle"]),
            Paragraph(
                f"12m target: <b>{_money(_tgt)}</b>  {_bar}  Price: <b>{_money(_price)}</b>  {_bar}  "
                f"Upside: <b>{_up_s}</b>  {_bar}  Horizon: <b>{_hz}</b>",
                styles["RptPriceLine"]),
        ]
        _w = left_w if _single else page_w
        _main = (_section_header("INVESTMENT DECISION", _w)
                 + _decision_block(_dec, _scn, _dcf_t, styles, _w)
                 + [Spacer(1, 6)]
                 + _section_header("SCENARIO ANALYSIS", _w)
                 + _scenario_block(_scn, _dcf_t, styles, _w))
        if _single:
            story += _title + [FrameBreak()] + _side_column(_t, side_w) + [FrameBreak()]
            story += [NextPageTemplate("body")] + _main
        else:
            if _i > 0:
                story.append(PageBreak())
            story += _title + _main + [Spacer(1, 6)] + _side_column(_t, page_w)

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — INDUSTRY INTELLIGENCE BRIEF
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(CondPageBreak(90 * mm))
    story.extend(_section_header("SECTION 2 — INDUSTRY INTELLIGENCE BRIEF", page_w))

    # Determine source tier for attribution (deep_research = live web; else LLM knowledge)
    _ib_src_raw = result.get("industry_brief", "") or ""
    _used_deep  = (not _ib_src_raw or _ib_src_raw.startswith(_BRIEF_ERROR_PREFIX))
    _ib_source  = (
        "Anthropic Web Search (live) + Financial Modeling Prep"
        if _used_deep else
        "AI Hedge Fund Phase 3 Industry Specialist · Financial Modeling Prep · "
        "Sector KPI framework (internal)"
    )

    if industry_brief:
        _render_md_block(industry_brief, story, styles, page_w, collector=_collect)
    else:
        story.append(Paragraph("Industry Intelligence Brief not available.", styles["RptBody"]))

    # ── Footnote block — rendered below the brief body ─────────────────────
    _fn_list: list[dict] = result.get("industry_footnotes", []) or []
    if not _fn_list:
        # Fall back to citation_registry verified entries
        _fn_list = [
            e for e in (result.get("citation_registry", []) or [])
            if e.get("verified") and e.get("source_name")
        ][:20]

    if _fn_list:
        story.append(HRFlowable(width="100%", thickness=0.3, color=C_LGREY, spaceAfter=2))
        story.append(Paragraph("References", styles["RptLabel"]))
        story.append(Spacer(1, 2))

        fn_rows = []
        for _seq, fn in enumerate(sorted(_fn_list, key=lambda x: x.get("ref_id") or 9999), start=1):
            rid       = fn.get("ref_id") or _seq   # fallback to sequential position
            src_name  = _strip(fn.get("source_name", ""))
            src_type  = _strip(fn.get("source_type", ""))
            date      = _strip(fn.get("date", ""))
            speaker   = _strip(fn.get("speaker", ""))
            claim     = _strip(fn.get("claim", ""))[:80]
            quote_raw = _strip(fn.get("quote", ""))[:120]

            # Build label: [n]
            label = f"[{rid}]"

            # Build attribution line
            attribution_parts = [src_name]
            if date:
                attribution_parts.append(date)
            if speaker:
                attribution_parts.append(speaker)
            attribution = " · ".join(p for p in attribution_parts if p)
            if src_type and src_type not in ("knowledge_base", "web_search"):
                attribution = f"({src_type}) {attribution}"

            # Build body: claim + optional quote
            body_parts = [claim] if claim else []
            if quote_raw:
                body_parts.append(f'"{quote_raw}"')
            body = " — ".join(body_parts) if body_parts else attribution

            fn_rows.append([
                Paragraph(label, styles["RptLabel"]),
                Paragraph(f"{attribution}", styles["RptSource"]),
                Paragraph(body, styles["RptBody"]),
            ])

        if fn_rows:
            _fn_col_w = [page_w * 0.05, page_w * 0.38, page_w * 0.57]
            fn_tbl = Table(fn_rows, colWidths=_fn_col_w, hAlign="LEFT")
            fn_tbl.setStyle(TableStyle([
                ("FONTSIZE",      (0, 0), (-1, -1), 6.5),
                ("LEADING",       (0, 0), (-1, -1), 8),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING",   (0, 0), (-1, -1), 2),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
                ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, C_PALE]),
                ("LINEBELOW",     (0, 0), (-1, -1), 0.2, C_LGREY),
            ]))
            story.append(fn_tbl)
            story.append(Spacer(1, 4))

    # Source attribution — enterprise requirement
    story.append(HRFlowable(width="100%", thickness=0.4, color=C_LGREY, spaceAfter=2))
    story.append(Paragraph(
        f"Source: {_ib_source}",
        styles["RptSource"],
    ))
    story.append(Spacer(1, 8))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — VALUATION AND RISK
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.extend(_section_header("SECTION 3 — VALUATION AND RISK", page_w))

    for i, (ticker, decision) in enumerate(decisions.items()):
        if i > 0:
            story.append(PageBreak())
        if len(decisions) > 1:
            story.append(Paragraph(f"Ticker: {ticker}", styles["RptSubsection"]))
            story.append(_hr())
        scen = scenario.get(ticker, {})
        pl   = power_law.get(ticker, {})
        trap = value_trap.get(ticker, {})
        dcf_ticker = (result.get("dcf_range") or {}).get(ticker, {})

        # ── Valuation Analysis ──
        story.append(Paragraph("Valuation Analysis", styles["RptSubsection"]))
        story.extend(_section_2f(
            ticker   = ticker,
            dcf_data = dcf_ticker,
            decision = decision,
            scenario = scen,
            styles   = styles,
            page_w   = page_w,
        ))
        _fwd_rows = _forward_financial_model(dcf_ticker, styles, page_w)
        if _fwd_rows:
            story.append(Paragraph(
                "Forward Financial Estimates \u2014 DCF Base Case (Year 1\u20135)", styles["RptLabel"]))
            story.append(Spacer(1, 2))
            story.extend(_fwd_rows)
            story.append(Spacer(1, 4))
        _peer_rows = _peer_comparison_table(
            (result.get("peer_comparison") or {}).get(ticker, {}), ticker, styles, page_w)
        if _peer_rows:
            story.append(Paragraph("Industry Peer Comparison", styles["RptLabel"]))
            story.append(Spacer(1, 2))
            story.extend(_peer_rows)

        # ── Financial statements: their own page after the valuation model ──
        _fs_raw = result.get("financial_statements") or {}
        _fs_t = _fs_raw.get(ticker) if isinstance(_fs_raw, dict) and ticker in _fs_raw else _fs_raw
        _fs_page = _financial_statements_page(_fs_t, styles, page_w)
        if _fs_page:
            story.append(PageBreak())
            story.extend(_section_header("FINANCIAL STATEMENTS", page_w))
            story.extend(_fs_page)
            story.append(PageBreak())

        # ── Valuation Summary: intrinsic value to 12-month target ──
        story.append(Paragraph("Valuation Summary", styles["RptSubsection"]))
        story.extend(_valuation_summary(dcf_ticker, scen, styles, page_w))
        story.extend(_segment_sotp_block_pdf(dcf_ticker, styles, page_w))
        story.extend(_analyst_sotp_block_pdf(dcf_ticker, styles, page_w))
        story.append(Spacer(1, 8))

        # ── Risk Assessment (value-trap checks + risk flags) ──
        story.append(Paragraph("Risk Assessment", styles["RptSubsection"]))

        # Risk flags only: position sizing is the decision's (Section 4), not
        # the risk manager's earlier pre-decision size, which contradicted it.
        risk      = analyst_signals.get("advanced_risk_manager", {}).get(ticker, {})
        risk_rows = []
        for flag in (risk.get("level1_flags", []) + risk.get("sector_flags", [])) if risk else []:
            risk_rows.append(["Risk Flag", _collect(flag)])

        trap_verdict = _strip(trap.get("overall_verdict", "—"))
        risk_rows.append(["Value Trap Verdict", trap_verdict])
        for check_key in _TRAP_CHECKS:
            check_data = trap.get(check_key, {})
            if isinstance(check_data, dict):
                status   = _strip(check_data.get("status", "—"))
                evidence = _collect(check_data.get("evidence", ""))
                label    = check_key.replace("_", " ").title()
                risk_rows.append([label, f"{status} — {evidence}"])

        if risk_rows:
            story.append(_kv_table(risk_rows, col1, col2, styles))
        else:
            story.append(Paragraph("Risk data not available for this ticker.", styles["RptBody"]))
        story.append(Spacer(1, 8))

        # ── Power Law Analysis ──
        story.append(Paragraph("Power Law Analysis", styles["RptSubsection"]))

        pl_score  = pl.get("total_score", "—")
        pl_interp = _collect(pl.get("interpretation", ""))

        # Key conclusion shown ABOVE the table
        if pl_interp:
            story.append(Paragraph(pl_interp, styles["RptBody"]))
            story.append(Spacer(1, 4))

        # Dimension table: Dimension | Score | Justification
        _dim_labels = {
            "scale_economies": "Scale economies",
            "network_effects": "Network effects",
            "winner_take_most": "Winner-take-most",
            "switching_costs": "Switching costs",
            "data_ip_moat": "Data / IP moat",
        }
        pl_dim_rows = [[
            Paragraph(_wh("Dimension"),     styles["RptLabel"]),
            Paragraph(_wh("Score"),         styles["RptLabel"]),
            Paragraph(_wh("Justification"), styles["RptLabel"]),
        ]]
        _dim_vals = [pl.get(k) for k in _dim_labels if isinstance(pl.get(k), (int, float))]
        _dim_scale = 10 if any(v > 2 for v in _dim_vals) else 2
        for dim_key, dim_label in _dim_labels.items():
            score_val = pl.get(dim_key, "?")
            # Use agent-provided interpretation if available in the pl dict, else fallback
            justif = _strip(str(pl.get(f"{dim_key}_note", "") or "\u2014"))
            pl_dim_rows.append([
                Paragraph(dim_label,             styles["RptBody"]),
                Paragraph(f"{score_val} / {_dim_scale}", styles["RptValue"]),
                Paragraph(justif,                styles["RptBody"]),
            ])
        pl_dim_rows.append([
            Paragraph("<b>Total Score</b>",      styles["RptLabel"]),
            Paragraph(f"<b>{pl_score} / 10</b>", styles["RptLabel"]),
            Paragraph("",                        styles["RptBody"]),
        ])

        pl_tbl = Table(
            pl_dim_rows,
            colWidths=[page_w * 0.22, page_w * 0.10, page_w * 0.68],
            hAlign="LEFT",
        )
        pl_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, C_PALE]),
            ("BACKGROUND",    (0, -1), (-1, -1), C_PALE),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
            ("ALIGN",         (1, 0), (1, -1), "CENTER"),
        ]))
        story.append(pl_tbl)
        story.append(Spacer(1, 6))


    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — DECISION
    # ═══════════════════════════════════════════════════════════════════════════
    story.extend(_section_header("SECTION 4 \u2014 DECISION", page_w))
    for ticker, decision in decisions.items():
        if len(decisions) > 1:
            story.append(Paragraph(f"Ticker: {ticker}", styles["RptSubsection"]))
        story.extend(_decision_section(decision, scenario.get(ticker, {}),
                                       (result.get("dcf_range") or {}).get(ticker, {}),
                                       styles, page_w))
        story.append(Spacer(1, 6))

    # ── Build PDF with running headers + page numbers (item E) ───────────────
    # Header text is captured in closure; _NumberedCanvas draws on every page.
    _hdr_left  = f"{', '.join(tickers)} — {_strip(sector)}"
    _hdr_right = f"Equitable Research  |  {run_date}"
    _W, _H     = A4
    _footer_txt = "For research purposes only. Not investment advice."

    class _NumberedCanvas(_rl_canvas.Canvas):
        """Two-pass canvas: draws running header + Page N of M on every page."""
        def __init__(self, *args, **kwargs):
            _rl_canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states: list = []

        def showPage(self):                             # called at each page break
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):                                  # called once at the end
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_hf(total)
                _rl_canvas.Canvas.showPage(self)
            _rl_canvas.Canvas.save(self)

        def _draw_hf(self, total: int) -> None:
            pn = self._pageNumber
            self.saveState()

            # ── Header ──────────────────────────────────────────────────────
            hy = _H - 9 * mm
            self.setFont("Helvetica", 6.5)
            self.setFillColor(colors.HexColor("#0a2342"))
            self.drawString(margin, hy, _hdr_left)
            self.drawRightString(_W - margin, hy, _hdr_right)
            # thin rule under header
            self.setStrokeColor(colors.HexColor("#c8d8e8"))
            self.setLineWidth(0.4)
            self.line(margin, hy - 1.5 * mm, _W - margin, hy - 1.5 * mm)

            # ── Footer ──────────────────────────────────────────────────────
            fy = 6 * mm
            self.setFont("Helvetica", 6.5)
            self.setFillColor(colors.HexColor("#888888"))
            # Line 1 (top): disclaimer + page number
            self.drawString(margin, fy + 8, _footer_txt)
            self.drawRightString(_W - margin, fy + 8, f"Page {pn} of {total}")
            # Line 2 (bottom): data source attribution
            self.setFont("Helvetica", 5.5)
            self.drawString(
                margin, fy,
                "Sources: Financial Modeling Prep · Anthropic Web Search · Internal Sector KPI Framework",
            )

            self.restoreState()

    doc.build(story, canvasmaker=_NumberedCanvas)

    # ── Truncation validation ─────────────────────────────────────────────────
    _validate_no_truncation(_all_text, output_path)

    # ── Auto-open ─────────────────────────────────────────────────────────────
    if open_after:
        _open_pdf(output_path)

    return output_path


def _open_pdf(path: str) -> None:
    """Open the PDF in the system default viewer (non-blocking, fully detached)."""
    try:
        abs_path = os.path.abspath(path)
        if sys.platform == "win32":
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP fully isolates the
            # viewer — any viewer-side DLL crash (0xc06d007e) is suppressed
            # and does not surface as a Windows error dialog.
            subprocess.Popen(
                ["cmd", "/c", "start", "/b", "", abs_path],
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])
    except Exception as exc:
        print(f"  Note: could not auto-open PDF ({exc})")


# ── Validation ─────────────────────────────────────────────────────────────────
def _validate_no_truncation(texts: list[str], pdf_path: str) -> None:
    """
    Scan every collected text string for truncation indicators:
      • Trailing '...'         — canonical ellipsis cut-off
      • Ends mid-word          — last char alpha + no terminal punctuation
                                 in the trailing 10 chars (heuristic)

    Prints a PASS / WARNING summary to stdout.
    """
    def _safe(s: str) -> str:
        return s.encode("ascii", errors="replace").decode("ascii")

    issues: list[str] = []
    for i, text in enumerate(texts):
        t = text.rstrip()
        if not t:
            continue
        # 1. Trailing ellipsis
        if t.endswith("..."):
            issues.append(f"  [field {i}] trailing '...' tail: {_safe(t[-80:])!r}")
        # 2. Probable broken sentence: text is long (>150 chars, clearly a paragraph
        #    not a short label/bullet), ends with an alpha char, and has no terminal
        #    punctuation in the last 15 characters.  Short phrases like risk bullets
        #    ("Mean reversion in multiples") are intentionally period-free.
        elif len(t) > 150 and t[-1].isalpha():
            tail = t[-15:]
            if not any(c in tail for c in ".!?):\"'"):
                issues.append(f"  [field {i}] possible break-off tail: {_safe(t[-80:])!r}")

    border = "-" * 62
    print(f"\n{border}")
    print(f"  PDF Truncation Validation")
    print(f"  Report : {pdf_path}")
    print(f"  Fields : {len(texts)} text segments checked")
    if issues:
        print(f"  STATUS : WARNING - {len(issues)} potential truncation(s):")
        for iss in issues:
            print(iss)
    else:
        print("  STATUS : PASS - no '...' or broken sentences detected")
    print(f"{border}\n")

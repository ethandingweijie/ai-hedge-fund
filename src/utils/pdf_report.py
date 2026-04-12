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

# Project root = two levels up from this file (src/utils/pdf_report.py)
_PROJECT_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPORTS_FOLDER = os.path.join(_PROJECT_ROOT, "Generated Reports")
_ARCHIVE_DB     = os.path.join(_PROJECT_ROOT, "src", "data", "run_archive.db")

# Sentinel prefix written by specialist.py when brief_text is empty
_BRIEF_ERROR_PREFIX = "[Industry brief generation incomplete"


def _fetch_db_brief(tickers: list[str]) -> str:
    """
    Query run_archive.db for the most recent deep_research_text that covers
    all requested tickers.  Returns "" if not found or DB unavailable.
    """
    if not tickers or not os.path.exists(_ARCHIVE_DB):
        return ""
    try:
        conn = sqlite3.connect(_ARCHIVE_DB)
        conn.row_factory = sqlite3.Row
        # Find the most recent run whose tickers JSON contains every requested ticker
        rows = conn.execute(
            "SELECT tickers, deep_research_text FROM runs "
            "WHERE deep_research_text IS NOT NULL "
            "ORDER BY run_at DESC LIMIT 50"
        ).fetchall()
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
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
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

# ── Catalyst keyword patterns (4C) ───────────────────────────────────────────────
_CATALYST_PATTERNS = [
    (re.compile(r'\b(Q[1-4]\s*\d{0,4}|next quarter|quarterly results?|earnings (release|beat|miss|call)|earnings surprise)\b', re.I), "Earnings",       "Near-term"),
    (re.compile(r'\b(FDA|EMA|PDUFA|NDA|BLA|regulatory approval|510k)\b', re.I),                                                       "Regulatory",     "Event-driven"),
    (re.compile(r'\b(product launch|product release|Blackwell|roadmap|H100|B200|next gen(eration)?|new chip|new model)\b', re.I),      "Product Cycle",  "Medium-term"),
    (re.compile(r'\b(buyback|share repurchase|capital return|dividend increase|special dividend)\b', re.I),                            "Capital Return", "Near-term"),
    (re.compile(r'\b(rate cut|Fed (pivot|cut|hike)|FOMC|interest rate|monetary (policy|easing)|pivot)\b', re.I),                      "Macro / Rates",  "External"),
    (re.compile(r'\b(merger|acquisition|M&A|strategic review|spinoff|spin-off|takeover)\b', re.I),                                    "Corporate Action","Event-driven"),
    (re.compile(r'\b(analyst day|investor day|capital markets day|management guidance|guidance update)\b', re.I),                      "Management",     "Near-term"),
    (re.compile(r'\b(contract win|landmark (deal|contract)|new partnership|joint venture|framework agreement)\b', re.I),               "Business Dev.",  "Medium-term"),
]

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
    """Format a raw dollar value as $XB or $XM for readability."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e9:
        return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


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

        PAD_L, PAD_R, PAD_T, PAD_B = 44, 54, 8, 22
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
            c.drawString(PAD_L + cw + 3, y_pt - 3, f"PT ${pt:.0f}")

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
            c.drawRightString(PAD_L - 2, PAD_B + frac * ch - 3, f"${val:.0f}")

        # Current price dot + label
        last_p = prices[-1]
        xl, yl = xp(len(prices) - 1), yp(last_p)
        c.setFillColor(colors.HexColor("#0a2342"))
        c.circle(xl, yl, 2.5, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 6)
        lbl_y = yl + 5 if yl + 14 < PAD_B + ch else yl - 11
        c.drawCentredString(xl, lbl_y, f"${last_p:.0f}")

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
            f"${cap/1e12:.1f}T" if cap and cap >= 1e12 else
            f"${cap/1e9:.0f}B"  if cap and cap >= 1e9  else
            f"${cap/1e6:.0f}M"  if cap and cap >= 1e6  else ""
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


# ── Catalysts Timeline (item 4C) ───────────────────────────────────────────────
def _extract_catalysts(
    ticker: str,
    analyst_signals: dict,
    scenario: dict,
    debate_result: dict,
) -> list[dict]:
    """Scan agent text for catalyst mentions using keyword patterns.
    Returns list of {type, timeline, catalyst, source}, max 6.
    """
    seen_types: set[str] = set()
    results: list[dict] = []

    # Build (text, source_label) pairs from all available agent outputs
    text_sources: list[tuple[str, str]] = []

    # Scenario agent assumptions
    for case in ("bull", "base", "bear"):
        assum = str((scenario.get(ticker) or {}).get(case, {}).get("assumptions", "") or "")
        if assum.strip():
            text_sources.append((assum, "Scenario Agent"))

    # Investor agent thesis + CoT logs
    for agent_key, sig_map in analyst_signals.items():
        if agent_key in _SKIP_AGENTS:
            continue
        if not isinstance(sig_map, dict) or ticker not in sig_map:
            continue
        sig = sig_map[ticker]
        if not isinstance(sig, dict):
            continue
        name   = _AGENT_DISPLAY.get(agent_key, agent_key.replace("_", " ").title())
        thesis = str(sig.get("thesis_summary", "") or "")
        cot    = str(sig.get("cot_log", "") or "")
        if thesis.strip():
            text_sources.append((thesis, name))
        if cot.strip():
            text_sources.append((cot[:600], name))   # cap CoT length

    # Debate adjudication
    adj = str((debate_result or {}).get(ticker, {}).get("adjudication", "") or "")
    if adj.strip():
        text_sources.append((adj, "Debate Round"))

    for text, source in text_sources:
        if len(results) >= 6:
            break
        for pat, cat_type, timeline in _CATALYST_PATTERNS:
            if cat_type in seen_types:
                continue
            m = pat.search(text)
            if not m:
                continue
            seen_types.add(cat_type)
            # Extract the surrounding sentence
            start = max(0, m.start() - 40)
            end   = min(len(text), m.end() + 70)
            snippet = re.sub(r'\s+', ' ', text[start:end]).strip()
            # Trim to nearest sentence boundary
            for sep in (". ", "! ", "? "):
                idx = snippet.find(sep, 15)
                if idx != -1:
                    snippet = snippet[:idx + 1]
                    break
            if len(snippet) > 130:
                snippet = snippet[:130].rstrip() + "…"
            results.append({"type": cat_type, "timeline": timeline,
                            "catalyst": snippet, "source": source})

    return results


def _catalyst_section(
    ticker: str,
    analyst_signals: dict,
    scenario: dict,
    debate_result: dict,
    styles,
    page_w: float,
) -> list:
    """Render a Key Catalysts table. Returns [] if no catalysts found."""
    cats = _extract_catalysts(ticker, analyst_signals, scenario, debate_result)
    if not cats:
        return []

    hdr = [
        Paragraph(_wh("Type"),     styles["RptLabel"]),
        Paragraph(_wh("Timeline"), styles["RptLabel"]),
        Paragraph(_wh("Catalyst"), styles["RptLabel"]),
        Paragraph(_wh("Source"),   styles["RptLabel"]),
    ]
    cw  = [page_w * 0.14, page_w * 0.11, page_w * 0.55, page_w * 0.20]
    rows = [hdr]
    for c_item in cats:
        rows.append([
            Paragraph(_strip(c_item["type"]),     styles["RptBody"]),
            Paragraph(_strip(c_item["timeline"]), styles["RptValue"]),
            Paragraph(_strip(c_item["catalyst"]), styles["RptBody"]),
            Paragraph(_strip(c_item["source"]),   styles["RptValue"]),
        ])

    t = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
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
    return [Paragraph("Key Catalysts", styles["RptSubsection"]), t, Spacer(1, 8)]


# ── Key Financials Table (item 15) ─────────────────────────────────────────────
def _key_financials_table(raw_financials: dict, styles, page_w) -> "Table | None":
    """Compact multi-year historical financials table (Revenue / Net Income / FCF / Net Debt).
    Returns None if raw_financials is absent or contains no parseable year-keyed data.
    """
    if not raw_financials or not isinstance(raw_financials, dict):
        return None

    # Accept keys like "FY2020", "FY2021", "2020", "2021", etc.
    fy_keys = sorted(k for k in raw_financials if isinstance(raw_financials.get(k), dict))
    if not fy_keys:
        return None
    fy_keys = fy_keys[-5:]          # show at most the last 5 fiscal years

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

    hdr = [Paragraph(_wh("KEY FINANCIALS"), styles["RptLabel"])]
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
        _data_row("Net Debt / (Cash)", [_get(fy, "net_debt")          for fy in fy_keys]),
    ]

    label_w = page_w * 0.22
    data_w  = (page_w - label_w) / len(fy_keys)
    t = Table(rows, colWidths=[label_w] + [data_w] * len(fy_keys), hAlign="LEFT", repeatRows=1)
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
            f"${net_12m/1e6:+.1f}M net 12m" if isinstance(net_12m, (int, float)) else "—"
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
) -> dict:
    """
    Compute Valuation / Growth / Profitability / Momentum scores (0–100 each).
    Returns a dict keyed by dimension with 'score', 'grade', and 'subs' (sub-metric lines).
    All inputs are already available in state at PDF render time — no extra API calls.
    """
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

    # Sub 3: P/FCF (forward; skip if negative FCF)
    if shares > 0 and rev_base > 0 and fcf_margin > 0:
        fcf_ps  = rev_base * fcf_margin / shares
        p_fcf   = current_p / fcf_ps if fcf_ps > 0 else None
    else:
        p_fcf = None
    if p_fcf is None:
        v3 = 30          # negative / unavailable FCF → below-average
        pfcf_lbl = "P/FCF: N/A (neg)"
    elif p_fcf <  8:  v3 = 95; pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"
    elif p_fcf < 15:  v3 = 80; pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"
    elif p_fcf < 25:  v3 = 62; pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"
    elif p_fcf < 40:  v3 = 40; pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"
    else:             v3 = 18; pfcf_lbl = f"P/FCF: {p_fcf:.1f}×"

    val_score = _clamp(v1 * 0.40 + v2 * 0.35 + v3 * 0.25)

    # ══════════════════════════════════════════════════════════════════════════
    # GROWTH  (Revenue growth 45% · Bull/Bear asymmetry 30% · Data confidence 25%)
    # ══════════════════════════════════════════════════════════════════════════
    # Sub 1: Revenue growth rate used in DCF (guided > analyst > historical)
    gr_pct = growth * 100
    if   gr_pct > 30: g1 = 95
    elif gr_pct > 20: g1 = 85
    elif gr_pct > 10: g1 = 70
    elif gr_pct >  5: g1 = 58
    elif gr_pct >  0: g1 = 44
    else:             g1 = 20
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
    # Sub 1: FCF Margin
    fm_pct = fcf_margin * 100
    if   fm_pct > 20: p1 = 95
    elif fm_pct > 10: p1 = 82
    elif fm_pct >  5: p1 = 68
    elif fm_pct >  0: p1 = 54
    elif fm_pct > -5: p1 = 34
    elif fm_pct >-15: p1 = 18
    else:             p1 = 6
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
        if   roic_spread > 0.10: p2 = 92
        elif roic_spread > 0.05: p2 = 78
        elif roic_spread > 0.0:  p2 = 62
        elif roic_spread >-0.05: p2 = 42
        else:                    p2 = 20
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


def _vgpm_scorecard(vgpm: dict, ticker: str, company_name: str, styles, page_w) -> list:
    """
    Render the 4-card VGPM Scorecard as a single horizontal strip.

    Each card shows:
      ┌──────────────┐
      │  DIMENSION   │  ← navy header
      │     A+       │  ← grade in coloured box
      │  sub-metric  │  ← 3 sub-metric lines
      │  sub-metric  │
      │  sub-metric  │
      └──────────────┘
    """
    DIMS = [
        ("VALUATION",     "valuation"),
        ("GROWTH",        "growth"),
        ("PROFITABILITY", "profitability"),
        ("MOMENTUM",      "momentum"),
    ]

    card_w = page_w / 4
    cards  = []

    for label, key in DIMS:
        dim    = vgpm.get(key, {})
        grade  = dim.get("grade", "B")
        subs   = dim.get("subs", [])
        score  = dim.get("score", 50)
        bg, fg = _GRADE_STYLES.get(grade, (colors.HexColor("#eab308"), colors.black))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = Table(
            [[Paragraph(f'<font color="white"><b>{label}</b></font>', styles["RptLabel"])]],
            colWidths=[card_w - 4],
        )
        hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_NAVY),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ]))

        # ── Grade box ─────────────────────────────────────────────────────────
        grade_style = ParagraphStyle(
            f"Grade_{grade}",
            parent=styles["RptTitle"],
            fontSize=26,
            leading=32,
            alignment=1,    # centre
            textColor=fg,
        )
        grade_tbl = Table(
            [[Paragraph(f"<b>{grade}</b>", grade_style)]],
            colWidths=[card_w - 4],
        )
        grade_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), bg),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ]))

        # ── Sub-metrics ───────────────────────────────────────────────────────
        sub_rows = [[Paragraph(s, styles["RptSource"])] for s in subs]
        sub_rows.append([Paragraph(
            f'<font color="{_fmt_hex(C_LGREY)}">score: {score:.0f}/100</font>',
            styles["RptSource"],
        )])
        sub_tbl = Table(sub_rows, colWidths=[card_w - 4])
        sub_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_PALE),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ]))

        # Stack into single cell
        cell_tbl = Table(
            [[hdr], [grade_tbl], [sub_tbl]],
            colWidths=[card_w - 4],
        )
        cell_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.5, C_LGREY),
        ]))
        cards.append(cell_tbl)

    outer = Table([cards], colWidths=[card_w] * 4)
    outer.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        ("LINERIGHT",     (0, 0), (-2, -1), 0.5, C_LGREY),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_NAVY),
    ]))

    return [
        Paragraph("Stock Scorecard — Valuation · Growth · Profitability · Momentum",
                  styles["RptLabel"]),
        Spacer(1, 4),
        outer,
        Spacer(1, 6),
    ]


def _fmt_hex(colour) -> str:
    """Convert a ReportLab Color to hex string for inline XML markup."""
    try:
        r = int(colour.red   * 255)
        g = int(colour.green * 255)
        b = int(colour.blue  * 255)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#888888"


# ── Sensitivity Table (WACC × TGR — item G) ────────────────────────────────────
def _sensitivity_table(
    dcf_ticker: dict,
    styles,
    page_w,
    current_price: float | None = None,
    pt_12m: float | None = None,
    pt_method: str | None = None,
) -> list:
    """5 × 5 WACC vs. Terminal Growth Rate sensitivity grid.
    Returns [] if required DCF parameters are missing.

    CHECK 3 FIX (2026-03-23):
    - net_debt now reads the actual dollar net debt stored by dcf_agent,
      not the D/E ratio ("leverage") — those are two different fields.
    - _iv() now applies margin_delta_per_year so Year 1–10 margins evolve
      exactly as in the main DCF, making the sensitivity centre-point
      identical to the base case intrinsic value.

    current_price / pt_12m: optional reference anchors shown in the sub-header
    so readers can immediately see where the market price and 12m PT land
    relative to the DCF-only grid (the two can diverge for growth companies
    whose market value is driven by revenue multiples, not FCF margins).
    """
    if not dcf_ticker:
        return []

    base          = dcf_ticker.get("base") or {}
    wacc_base     = dcf_ticker.get("wacc")
    tgr_base      = base.get("tgr")
    gr            = base.get("growth_rate")
    margin        = base.get("fcf_margin_start")        # base-year FCF margin (pre-delta)
    margin_delta  = base.get("margin_delta_per_year", 0.0)  # per-year margin change
    # Change 9: prefer revenue_base_usd (explicit post-FX value) over revenue_base.
    # For live pipeline runs these are identical. For reconstructed/partial dicts,
    # revenue_base_usd guarantees we use the USD-converted value.
    rev           = dcf_ticker.get("revenue_base_usd") or dcf_ticker.get("revenue_base")
    shares        = dcf_ticker.get("shares_outstanding")
    # CHECK 3 FIX (a): use actual dollar net debt, not D/E leverage ratio
    net_debt      = dcf_ticker.get("net_debt", 0) or 0
    # CHECK 3 FIX (b): floor is needed to cap margin compression
    fcf_floor     = dcf_ticker.get("fcf_floor", -0.05)

    # Explicit None checks — do NOT use not all([...]) because 0.0 is falsy
    # and a zero growth rate or zero margin is a valid (if extreme) input.
    if any(v is None for v in [wacc_base, tgr_base, gr, margin, rev, shares]):
        return []
    if wacc_base <= 0 or shares <= 0:
        return []

    YEARS = 10

    def _iv(wacc, tgr):
        _tgr = min(tgr, wacc - 0.005)  # guard against TGR ≥ WACC
        pv = 0.0
        for t in range(1, YEARS + 1):
            # CHECK 3 FIX (b): apply margin delta per year, matching main DCF
            margin_t = max(margin + margin_delta * t, fcf_floor)
            margin_t = min(margin_t, 0.60)
            pv += (rev * (1 + gr) ** t * margin_t) / (1 + wacc) ** t
        # Terminal year uses the Year-10 evolved margin
        margin_T = max(margin + margin_delta * YEARS, fcf_floor)
        margin_T = min(margin_T, 0.60)
        fcf_T = rev * (1 + gr) ** YEARS * margin_T
        tv    = fcf_T * (1 + _tgr) / (wacc - _tgr)
        pv_tv = tv / (1 + wacc) ** YEARS
        # Do NOT clamp to 0 — negative equity value is meaningful information
        # (company is FCF-negative; net debt exceeds discounted cash flows).
        return (pv + pv_tv - net_debt) / shares

    base_iv = _iv(wacc_base, tgr_base)

    # ── Change 1: Center-cell verification ────────────────────────────────────
    # Recomputed base_iv should match the stored DCF intrinsic_value. A large
    # divergence signals a unit mismatch (FX-unconverted revenue, ADS vs ordinary
    # share count, etc.). Show a visible warning so the reader doesn't trust the
    # sensitivity grid blindly.
    _stored_iv = base.get("intrinsic_value") or 0.0
    _div_pct = abs(base_iv - _stored_iv) / max(abs(_stored_iv), 1.0) if _stored_iv else 0.0
    _sens_warn = None
    if _stored_iv > 0 and _div_pct > 0.05:
        _sens_warn = (
            f"⚠ Sensitivity center (${base_iv:.2f}) diverges {_div_pct:.0%} from "
            f"stored blended IV (${_stored_iv:.2f}). "
            f"Likely cause: revenue_base or shares_outstanding unit mismatch "
            f"(check FX conversion — reported_currency may not be USD). "
            f"Stored blended IV is authoritative; treat grid as directional only."
        )

    # Grid axes — WACC rows, TGR columns
    wacc_steps = [-0.020, -0.010, 0.0, +0.010, +0.020]
    tgr_steps  = [-0.010, -0.005, 0.0, +0.005, +0.010]
    wacc_vals  = [wacc_base + s for s in wacc_steps]
    tgr_vals   = [tgr_base  + s for s in tgr_steps]

    # Header row — sensitivity keeps navy, so white markup is inlined explicitly
    def _wh_s(t): return f'<font color="white"><b>{t}</b></font>'
    hdr = [Paragraph(_wh_s("WACC / TGR"), styles["RptLabel"])]
    for j, tgr in enumerate(tgr_vals):
        lbl = f"{'→' if j == 2 else ''}{tgr*100:.1f}%"
        hdr.append(Paragraph(_wh_s(lbl), styles["RptLabel"]))

    rows = [hdr]
    style_cmds: list = [
        ("BACKGROUND",    (0, 0), (-1,  0), C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1,  0), colors.white),
        ("BACKGROUND",    (0, 0), ( 0, -1), C_NAVY),
        ("TEXTCOLOR",     (0, 0), ( 0, -1), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("LINERIGHT",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]

    # Colour thresholds for heatmap shading
    _C_UP   = colors.HexColor("#d4edda")   # light green — IV > base
    _C_DOWN = colors.HexColor("#f8d7da")   # light red   — IV < base
    _C_BASE = colors.HexColor("#0a2342")   # navy        — base cell

    # Adaptive decimal precision: whole-dollar rounding collapses a $2.30/$2.80
    # grid into all "$2" cells for low-priced stocks like GRAB (~$3).
    # Use the base IV to determine how many decimals the whole grid needs.
    _iv_prec = 2 if base_iv < 10 else (1 if base_iv < 100 else 0)

    for i, wacc in enumerate(wacc_vals):
        w_lbl = f"{'→' if i == 2 else ''}{wacc*100:.1f}%"
        row   = [Paragraph(_wh_s(w_lbl), styles["RptLabel"])]  # white on navy
        for j, tgr in enumerate(tgr_vals):
            iv  = _iv(wacc, tgr)
            is_base = (i == 2 and j == 2)
            if iv < 0:
                txt = f"{'★ ' if is_base else ''}<$0"
            else:
                txt = f"${'★ ' if is_base else ''}{iv:.{_iv_prec}f}"
            cell_sty = styles["RptLabel"] if is_base else styles["RptValue"]
            row.append(Paragraph(
                f'<font color="white"><b>{txt}</b></font>' if is_base else txt,
                cell_sty,
            ))
            ri, ci = i + 1, j + 1
            if is_base:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_BASE))
            elif base_iv <= 0:
                # base_iv ≤ 0: relative threshold breaks — use absolute $0 as boundary
                if iv > 0:
                    style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_UP))
                elif iv < base_iv:
                    style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_DOWN))
            elif iv > base_iv * 1.05:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_UP))
            elif iv < base_iv * 0.95:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_DOWN))
        rows.append(row)

    col_w = page_w / 6
    t = Table(rows, colWidths=[col_w] * 6, hAlign="LEFT")
    t.setStyle(TableStyle(style_cmds))

    # ── Change 4: Net-cash positive disclosure ─────────────────────────────────
    if net_debt >= 1e8:
        _nd_note = f"Net debt ${net_debt/1e9:.1f}B"
    elif net_debt > 0:
        _nd_note = f"Net debt ${net_debt/1e6:.0f}M"
    elif net_debt < -1e8:
        _cash_abs = abs(net_debt)
        _nd_note = (
            f"Net cash ${_cash_abs/1e9:.1f}B (cash > debt — "
            f"EV = mkt cap + net debt is negative; "
            f"IVs above include cash as equity value floor)"
        )
    else:
        _nd_note = "Net cash"
    # ── Change 2: FX annotation for first-principles metrics ──────────────────
    _rpt_ccy = dcf_ticker.get("reported_currency", "USD") or "USD"
    _fx_ann  = (
        f"  |  ⚠ Financials reported in {_rpt_ccy}: first-principles P/E and FCF "
        f"yield use {_rpt_ccy} figures ÷ USD market cap — API values are authoritative"
        if _rpt_ccy != "USD" else ""
    )
    # Build optional reference anchors line
    _ref_parts = []
    if current_price is not None and current_price > 0:
        _ref_parts.append(f"Current price: ${current_price:.{_iv_prec}f}")
    if pt_12m is not None and pt_12m > 0:
        _method_note = f" ({pt_method})" if pt_method else " (fwd multiple)"
        _ref_parts.append(f"12m PT: ${pt_12m:.{_iv_prec}f}{_method_note}")
        if current_price and current_price > 0:
            _pt_pct = (pt_12m - current_price) / current_price * 100
            _sign = "+" if _pt_pct >= 0 else ""
            _ref_parts.append(f"PT upside: {_sign}{_pt_pct:.1f}%")
    # Divergence warning: if 12m PT is outside the entire DCF grid range, flag it
    _divergence_note = ""
    if pt_12m is not None and pt_12m > 0 and base_iv > 0:
        _pct_diff = abs(pt_12m - base_iv) / base_iv
        if _pct_diff > 0.5:
            _direction = "above" if pt_12m > base_iv else "below"
            _divergence_note = (
                f"  ⚠ 12m PT is {_pct_diff*100:.0f}% {_direction} DCF centre — "
                f"market pricing reflects forward multiples (revenue/EBITDA), not FCF-only DCF. "
                f"Common for growth companies with thin FCF margins."
            )
    _ref_line = ("  |  " + "  |  ".join(_ref_parts)) if _ref_parts else ""
    _out = [
        Paragraph("Sensitivity Analysis — DCF Component (WACC vs. Terminal Growth Rate)", styles["RptLabel"]),
        Spacer(1, 3),
    ]
    # Change 1: prepend divergence warning banner if center cell mismatches stored IV
    if _sens_warn:
        _warn_style = ParagraphStyle(
            name="_sens_warn",
            parent=styles["RptBody"],
            backColor=colors.HexColor("#fff3cd"),
            borderPadding=4,
            textColor=colors.HexColor("#856404"),
        )
        _out.append(Paragraph(_sens_warn, _warn_style))
        _out.append(Spacer(1, 3))
    _out += [
        Paragraph(
            f"DCF-only sensitivity: WACC {wacc_base*100:.1f}%  |  TGR {tgr_base*100:.1f}%  |  "
            f"DCF IV ${base_iv:.{_iv_prec}f}  |  {_nd_note}"
            f"{_ref_line}  |  "
            f"Note: Blended IV (shown in KPI box) incorporates multi-method weighting "
            f"(EV/EBITDA, P/E etc.) — the centre here reflects the pure DCF component only."
            f"{_fx_ann}"
            f"{_divergence_note}  |  "
            f"Green = upside vs DCF centre  |  Red = downside",
            styles["RptBody"],
        ),
        Spacer(1, 3),
        t,
        Spacer(1, 6),
    ]
    return _out


def _sensitivity_table_growth_margin(
    dcf_ticker: dict,
    styles,
    page_w,
    current_price: float | None = None,
    pt_12m: float | None = None,
) -> list:
    """P0.2 — 5 × 5 Revenue Growth vs. FCF Margin sensitivity grid.

    Holds WACC and TGR fixed at base values; varies the two dominant IV
    drivers for growth companies so the analyst can see their combined
    impact. Centre-point (base growth × base margin) equals the base IV
    produced by the main DCF, making it directly comparable to the
    WACC × TGR grid.
    """
    if not dcf_ticker:
        return []

    base         = dcf_ticker.get("base") or {}
    wacc_base    = dcf_ticker.get("wacc")
    tgr_base     = base.get("tgr")
    gr_base      = base.get("growth_rate")
    margin_base  = base.get("fcf_margin_start")
    margin_delta = base.get("margin_delta_per_year", 0.0)
    # Change 9: prefer revenue_base_usd for FX consistency (same as WACC×TGR grid)
    rev          = dcf_ticker.get("revenue_base_usd") or dcf_ticker.get("revenue_base")
    shares       = dcf_ticker.get("shares_outstanding")
    net_debt     = dcf_ticker.get("net_debt", 0) or 0
    fcf_floor    = dcf_ticker.get("fcf_floor", -0.05)

    # Explicit None checks — 0.0 is falsy so not all([...]) would incorrectly
    # discard tables where growth rate or margin is exactly zero.
    if any(v is None for v in [wacc_base, gr_base, margin_base, rev, shares]):
        return []
    if wacc_base <= 0 or shares <= 0:
        return []

    YEARS = 10
    _tgr_safe = min(tgr_base or 0.025, wacc_base - 0.005)

    def _iv_gm(gr_val, margin_val):
        """DCF IV with fixed WACC/TGR; varying revenue growth and FCF margin."""
        pv = 0.0
        for t in range(1, YEARS + 1):
            margin_t = max(margin_val + margin_delta * t, fcf_floor)
            margin_t = min(margin_t, 0.60)
            pv += (rev * (1 + gr_val) ** t * margin_t) / (1 + wacc_base) ** t
        margin_T = max(margin_val + margin_delta * YEARS, fcf_floor)
        margin_T = min(margin_T, 0.60)
        fcf_T = rev * (1 + gr_val) ** YEARS * margin_T
        tv    = fcf_T * (1 + _tgr_safe) / (wacc_base - _tgr_safe)
        pv_tv = tv / (1 + wacc_base) ** YEARS
        # Do NOT clamp to 0 — negative equity value is meaningful information.
        return (pv + pv_tv - net_debt) / shares

    base_iv = _iv_gm(gr_base, margin_base)

    # Change 1 (growth×margin grid): verify center cell matches stored IV
    _stored_iv_gm = base.get("intrinsic_value") or 0.0
    _div_pct_gm = abs(base_iv - _stored_iv_gm) / max(abs(_stored_iv_gm), 1.0) if _stored_iv_gm else 0.0
    _sens_warn_gm = None
    if _stored_iv_gm > 0 and _div_pct_gm > 0.05:
        _sens_warn_gm = (
            f"⚠ Growth×Margin grid centre (${base_iv:.2f}) diverges {_div_pct_gm:.0%} "
            f"from stored IV (${_stored_iv_gm:.2f}). "
            f"Stored blended IV is authoritative; treat grid as directional only."
        )
    # Change 2 (growth×margin): FX annotation
    _rpt_ccy_gm = dcf_ticker.get("reported_currency", "USD") or "USD"
    _fx_ann_gm  = (
        f"  |  ⚠ Financials in {_rpt_ccy_gm} — first-principles ratios use {_rpt_ccy_gm} ÷ USD mkt cap"
        if _rpt_ccy_gm != "USD" else ""
    )

    # Grid axes: revenue growth rows, FCF margin columns
    # Steps are absolute percentage-point shifts
    gr_steps  = [-0.050, -0.025, 0.0, +0.025, +0.050]
    fcfm_steps = [-0.050, -0.025, 0.0, +0.025, +0.050]
    gr_vals   = [gr_base   + s for s in gr_steps]
    fcfm_vals = [margin_base + s for s in fcfm_steps]

    def _wh_s(t):
        return f'<font color="white"><b>{t}</b></font>'

    hdr = [Paragraph(_wh_s("RevGr / FCFMgn"), styles["RptLabel"])]
    for j, fcfm in enumerate(fcfm_vals):
        lbl = f"{'→' if j == 2 else ''}{fcfm*100:.1f}%"
        hdr.append(Paragraph(_wh_s(lbl), styles["RptLabel"]))

    rows = [hdr]
    style_cmds: list = [
        ("BACKGROUND",    (0, 0), (-1,  0), C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1,  0), colors.white),
        ("BACKGROUND",    (0, 0), ( 0, -1), C_NAVY),
        ("TEXTCOLOR",     (0, 0), ( 0, -1), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ("LINERIGHT",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]

    _C_UP   = colors.HexColor("#d4edda")
    _C_DOWN = colors.HexColor("#f8d7da")
    _C_BASE = colors.HexColor("#0a2342")

    _gm_prec = 2 if abs(base_iv) < 10 else (1 if abs(base_iv) < 100 else 0)
    for i, gr_v in enumerate(gr_vals):
        g_lbl = f"{'→' if i == 2 else ''}{gr_v*100:.1f}%"
        row   = [Paragraph(_wh_s(g_lbl), styles["RptLabel"])]
        for j, fcfm_v in enumerate(fcfm_vals):
            iv      = _iv_gm(gr_v, fcfm_v)
            is_base = (i == 2 and j == 2)
            if iv < 0:
                txt = f"{'★ ' if is_base else ''}<$0"
            else:
                txt = f"${'★ ' if is_base else ''}{iv:.{_gm_prec}f}"
            cell_sty = styles["RptLabel"] if is_base else styles["RptValue"]
            row.append(Paragraph(
                f'<font color="white"><b>{txt}</b></font>' if is_base else txt,
                cell_sty,
            ))
            ri, ci = i + 1, j + 1
            if is_base:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_BASE))
            elif base_iv <= 0:
                # base_iv ≤ 0: relative threshold breaks — use absolute $0 as boundary
                if iv > 0:
                    style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_UP))
                elif iv < base_iv:
                    style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_DOWN))
            elif iv > base_iv * 1.05:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_UP))
            elif iv < base_iv * 0.95:
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), _C_DOWN))
        rows.append(row)

    col_w = page_w / 6
    t = Table(rows, colWidths=[col_w] * 6, hAlign="LEFT")
    t.setStyle(TableStyle(style_cmds))

    # Optional reference anchors for the note
    _ref_parts2 = []
    if current_price is not None and current_price > 0:
        _ref_parts2.append(f"Current price: ${current_price:.{_gm_prec}f}")
    if pt_12m is not None and pt_12m > 0:
        _ref_parts2.append(f"12m PT: ${pt_12m:.{_gm_prec}f} (fwd multiple)")
    _ref_line2 = ("  |  " + "  |  ".join(_ref_parts2)) if _ref_parts2 else ""

    _out2 = [
        Paragraph(
            "Sensitivity Analysis — Revenue Growth vs. FCF Margin",
            styles["RptLabel"],
        ),
        Spacer(1, 3),
    ]
    if _sens_warn_gm:
        _warn_style_gm = ParagraphStyle(
            name="_sens_warn_gm",
            parent=styles["RptBody"],
            backColor=colors.HexColor("#fff3cd"),
            borderPadding=4,
            textColor=colors.HexColor("#856404"),
        )
        _out2.append(Paragraph(_sens_warn_gm, _warn_style_gm))
        _out2.append(Spacer(1, 3))
    _out2 += [
        Paragraph(
            f"Base: Rev growth {gr_base*100:.1f}%  |  FCF margin {margin_base*100:.1f}%  |  "
            f"DCF IV ${base_iv:.{_gm_prec}f}  |  WACC {wacc_base*100:.1f}% fixed  |  "
            f"TGR {(tgr_base or 0.025)*100:.1f}% fixed"
            f"{_ref_line2}"
            f"{_fx_ann_gm}  |  "
            f"★ = centre matches base IV  |  Green = upside  |  Red = downside",
            styles["RptBody"],
        ),
        Spacer(1, 3),
        t,
        Spacer(1, 6),
    ]
    return _out2


# ── Key Points generator (deterministic, no LLM call) ──────────────────────────
def _build_key_points(
    ticker: str,
    decision: dict,
    scen: dict,
    pl: dict,
    trap: dict,
    analyst_signals: dict,
    dcf_ticker: dict,
) -> list[str]:
    """Return 3–5 proper English bullet-point sentences for the Executive Summary block.
    All data is sourced deterministically from the pipeline result dict.
    """
    points: list[str] = []

    # ── 1. Rating + price target + EV upside ─────────────────────────────────
    action  = _strip(decision.get("action", "")).upper()
    pt      = decision.get("price_target")
    horizon = _strip(decision.get("time_horizon", "medium")).lower()
    upside  = scen.get("upside_pct")
    rationale_short = _strip(str(decision.get("rationale", decision.get("reasoning", ""))))[:120]
    if action in ("SHORT", "SELL"):
        # SHORT/SELL positions may not have an upside price target — use stop-cover level
        _stop_lvl = decision.get("stop_loss")
        _stop_txt = f" (stop/cover: ${_stop_lvl:.2f})" if isinstance(_stop_lvl, (int, float)) else ""
        _pt_txt   = f" targeting ${pt:.2f}" if isinstance(pt, (int, float)) else ""
        points.append(
            f"Initiate {action}{_pt_txt}{_stop_txt}. {rationale_short}"
            if rationale_short else f"Initiate {action}{_pt_txt}{_stop_txt}."
        )
    elif action and isinstance(pt, (int, float)):
        up_txt = (
            f", implying {upside:+.0f}% probability-weighted upside"
            if isinstance(upside, (int, float)) else ""
        )
        points.append(
            f"Initiate {action} with a {horizon}-term price target of ${pt:.2f}{up_txt}."
        )
    elif action:
        points.append(f"Recommendation: {action}. {rationale_short}".strip())

    # ── 2. Valuation: DCF intrinsic value vs current price ───────────────────
    base_iv  = (dcf_ticker.get("base") or {}).get("intrinsic_value")
    curr_px  = scen.get("current_price")
    if isinstance(base_iv, (int, float)) and isinstance(curr_px, (int, float)) and curr_px > 0:
        gap     = (base_iv / curr_px - 1) * 100
        rel_str = f"{abs(gap):.0f}% {'discount' if gap > 0 else 'premium'} to current price"
        points.append(
            f"DCF analysis returns an intrinsic value of ${base_iv:.2f}, "
            f"a {rel_str} of ${curr_px:.2f}."
        )

    # ── 3. Category leadership (Power Law) ───────────────────────────────────
    pl_score = pl.get("total_score")
    pl_interp = _strip(str(pl.get("interpretation", "")))
    if isinstance(pl_score, (int, float)):
        # Pull highest-scoring dimensions for colour commentary
        dims = {
            "network effects":    pl.get("network_effects", 0),
            "switching costs":    pl.get("switching_costs", 0),
            "scale economies":    pl.get("scale_economies", 0),
            "data / IP moat":     pl.get("data_ip_moat", 0),
            "winner-take-most":   pl.get("winner_take_most", 0),
        }
        top_dims = sorted(dims, key=dims.get, reverse=True)[:2]  # type: ignore[arg-type]
        dim_txt = " and ".join(top_dims)
        points.append(
            f"Category leadership scores {pl_score}/10, driven by {dim_txt}; "
            f"{pl_interp[:90].rstrip('.') if pl_interp else 'strong moat characteristics'}."
        )

    # ── 4. Risk audit (Value Trap) ────────────────────────────────────────────
    trap_verdict = _strip(trap.get("overall_verdict", ""))
    if trap_verdict:
        _VERDICT_PROSE = {
            "TRAP RISK LOW":    "forensic accounting, FCF sustainability and balance sheet checks all pass — low value trap risk.",
            "TRAP RISK MEDIUM": "forensic checks return medium value trap risk; monitor FCF-to-earnings alignment.",
            "TRAP RISK HIGH":   "forensic checks flag HIGH value trap risk; position sizing reduced accordingly.",
        }
        points.append(_VERDICT_PROSE.get(trap_verdict, f"Risk audit verdict: {trap_verdict}."))

    # ── 5. Analyst committee consensus ───────────────────────────────────────
    buy_n = hold_n = sell_n = 0
    conv_total = 0.0
    conv_count = 0
    for agent_key, sig_map in analyst_signals.items():
        if agent_key in _SKIP_AGENTS:
            continue
        if not isinstance(sig_map, dict) or ticker not in sig_map:
            continue
        sig = sig_map[ticker]
        if not isinstance(sig, dict):
            continue
        s = _strip(sig.get("signal", "")).upper()
        if s == "BUY":        buy_n  += 1
        elif s in ("SELL", "SHORT"): sell_n += 1
        else:                        hold_n += 1
        conv = sig.get("conviction")
        if isinstance(conv, (int, float)):
            conv_total += conv
            conv_count += 1
    total_n = buy_n + hold_n + sell_n
    if total_n > 0:
        avg_conv = conv_total / conv_count if conv_count else 0
        points.append(
            f"Analyst committee votes {buy_n} BUY / {hold_n} HOLD / {sell_n} SELL "
            f"across {total_n} analysts, with average conviction {avg_conv:.1f}/10."
        )

    return points[:5]


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
    lines = _ANSI_RE.sub("", text).splitlines()
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
        if re.fullmatch(r"[-=]{3,}\s*", stripped):
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


# ── Section 2f — Valuation Model ───────────────────────────────────────────────

def _final_summary_table(
    dcf_data: dict,
    scenario: dict,
    decision: dict,
    styles,
    page_w: float,
) -> list:
    """P1.3 — §12 Final Summary Table.

    Three sub-tables per ticker, rendered at the bottom of each ticker section:
      (1) Method | EV (prob-wtd) | Weight  →  Blended IV
      (2) Scenario | Probability | DCF IV | 12m PT
      (3) Decision Snapshot box
    """
    story = []
    story.append(Paragraph("§12 — Final Valuation Summary", styles["RptSubsection"]))

    # ── Extract scenario probabilities ────────────────────────────────────────
    _bear_p = float(((scenario.get("bear") or {}).get("probability")) or 0.25)
    _base_p = float(((scenario.get("base") or {}).get("probability")) or 0.50)
    _bull_p = float(((scenario.get("bull") or {}).get("probability")) or 0.25)

    # ── (1) Per-Method EV table ───────────────────────────────────────────────
    if dcf_data and dcf_data.get("base"):
        bear_d = dcf_data.get("bear", {}) or {}
        base_d = dcf_data.get("base", {}) or {}
        bull_d = dcf_data.get("bull", {}) or {}

        bear_method_ivs = bear_d.get("method_iv_table", {}) or {}
        base_method_ivs = base_d.get("method_iv_table", {}) or {}
        bull_method_ivs = bull_d.get("method_iv_table", {}) or {}

        _pw_list = base_d.get("profile_weights", []) or []
        _pw_map: dict[str, float] = {
            pw["name"]: pw["weight"] for pw in _pw_list if "name" in pw and "weight" in pw
        }
        total_weight = sum(_pw_map.values()) or 1.0

        all_method_names: list[str] = []
        seen: set[str] = set()
        for _ivt in (bear_method_ivs, base_method_ivs, bull_method_ivs):
            for mn in _ivt:
                if mn not in seen:
                    all_method_names.append(mn)
                    seen.add(mn)

        if all_method_names:
            story.append(Paragraph(
                "(1) Per-Method Expected Value (§6 Framework)",
                styles["RptLabel"],
            ))
            story.append(Spacer(1, 2))

            _m1_w = [page_w * 0.30, page_w * 0.18, page_w * 0.08, page_w * 0.44]
            m1_hdr = [
                Paragraph(_wh("Method"),      styles["RptLabel"]),
                Paragraph(_wh("EV (prob-wtd)"), styles["RptLabel"]),
                Paragraph(_wh("Wt"),          styles["RptLabel"]),
                Paragraph(_wh("Bear IV × p  +  Base IV × p  +  Bull IV × p"), styles["RptLabel"]),
            ]
            m1_rows = [m1_hdr]
            method_evs: list[tuple[str, float, float]] = []  # (name, ev, weight)
            for mn in all_method_names:
                b_iv  = bear_method_ivs.get(mn)
                ba_iv = base_method_ivs.get(mn)
                bu_iv = bull_method_ivs.get(mn)
                w     = _pw_map.get(mn, 0)
                wt_str = f"{w/total_weight:.0%}" if w else "—"
                if b_iv is not None and ba_iv is not None and bu_iv is not None:
                    ev_m = float(b_iv)*_bear_p + float(ba_iv)*_base_p + float(bu_iv)*_bull_p
                    detail = (
                        f"${float(b_iv):.0f}×{_bear_p:.0%}  +  "
                        f"${float(ba_iv):.0f}×{_base_p:.0%}  +  "
                        f"${float(bu_iv):.0f}×{_bull_p:.0%}"
                    )
                    ev_str = f"${ev_m:.0f}"
                    method_evs.append((mn, ev_m, w))
                else:
                    ev_m = None
                    ev_str = "—"
                    detail = "Partial data"
                m1_rows.append([
                    Paragraph(_strip(mn),  styles["RptBody"]),
                    Paragraph(ev_str,      styles["RptValue"]),
                    Paragraph(wt_str,      styles["RptValue"]),
                    Paragraph(detail,      styles["RptBody"]),
                ])

            # Blended IV footer
            bear_iv = bear_d.get("intrinsic_value", 0) or 0
            base_iv = base_d.get("intrinsic_value", 0) or 0
            bull_iv = bull_d.get("intrinsic_value", 0) or 0
            blended_ev = float(bear_iv)*_bear_p + float(base_iv)*_base_p + float(bull_iv)*_bull_p
            blended_detail = (
                f"${bear_iv:.0f}×{_bear_p:.0%}  +  "
                f"${base_iv:.0f}×{_base_p:.0%}  +  "
                f"${bull_iv:.0f}×{_bull_p:.0%}"
            )
            m1_rows.append([
                Paragraph("<b>Blended IV (Prob-Wtd)</b>", styles["RptLabel"]),
                Paragraph(f"<b>${blended_ev:.0f}</b>", styles["RptLabel"]),
                Paragraph("<b>100%</b>", styles["RptLabel"]),
                Paragraph(blended_detail, styles["RptBody"]),
            ])

            m1_tbl = Table(m1_rows, colWidths=_m1_w, hAlign="LEFT", repeatRows=1)
            m1_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
                ("BACKGROUND",    (0, -1), (-1, -1), C_PALE),
                ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 4),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, C_PALE]),
                ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
            ]))
            story.append(m1_tbl)
            story.append(Spacer(1, 6))

    # ── (2) Scenario Reconciliation table ────────────────────────────────────
    story.append(Paragraph("(2) Scenario Reconciliation", styles["RptLabel"]))
    story.append(Spacer(1, 2))

    scen_rec = scenario.get("reconciliation") or {}
    _12m_by  = scenario.get("12m_targets_by_scenario") or {}
    _12m_pt_method = scenario.get("12m_pt_method", "")

    _bear_sc = scenario.get("bear") or {}
    _base_sc = scenario.get("base") or {}
    _bull_sc = scenario.get("bull") or {}

    _dcf_bear_iv = (dcf_data.get("bear") or {}).get("intrinsic_value") if dcf_data else None
    _dcf_base_iv = (dcf_data.get("base") or {}).get("intrinsic_value") if dcf_data else None
    _dcf_bull_iv = (dcf_data.get("bull") or {}).get("intrinsic_value") if dcf_data else None

    _m2_w = [page_w * 0.14, page_w * 0.12, page_w * 0.15, page_w * 0.15, page_w * 0.15, page_w * 0.29]
    m2_hdr = [
        Paragraph(_wh("Scenario"),    styles["RptLabel"]),
        Paragraph(_wh("Prob"),        styles["RptLabel"]),
        Paragraph(_wh("LLM FV"),      styles["RptLabel"]),
        Paragraph(_wh("DCF IV"),      styles["RptLabel"]),
        Paragraph(_wh("12m PT"),      styles["RptLabel"]),
        Paragraph(_wh("Key Assumption"), styles["RptLabel"]),
    ]
    m2_rows = [m2_hdr]

    def _iv_str(v):
        try:
            return f"${float(v):.0f}"
        except Exception:
            return "—"

    for _sn, _sp, _sc_d, _dcf_iv, _12m in [
        ("Bear", _bear_p, _bear_sc, _dcf_bear_iv, _12m_by.get("bear")),
        ("Base", _base_p, _base_sc, _dcf_base_iv, _12m_by.get("base")),
        ("Bull", _bull_p, _bull_sc, _dcf_bull_iv, _12m_by.get("bull")),
    ]:
        _fv   = _sc_d.get("fair_value")
        _assm = _sc_d.get("assumptions", "")
        m2_rows.append([
            Paragraph(f"<b>{_sn}</b>", styles["RptLabel"]),
            Paragraph(f"{_sp:.0%}",    styles["RptValue"]),
            Paragraph(_iv_str(_fv),    styles["RptValue"]),
            Paragraph(_iv_str(_dcf_iv), styles["RptValue"]),
            Paragraph(_iv_str(_12m),   styles["RptValue"]),
            Paragraph((_strip(_assm)[:347] + "...") if len(_strip(_assm)) > 350 else _strip(_assm), styles["RptBody"]),
        ])

    # EV / 12m PT summary row
    _ev   = scenario.get("expected_value") or 0.0
    _12m_pt = scenario.get("12m_price_target")
    _cp   = scen_rec.get("current_price") or scenario.get("current_price") or 0.0
    _pt_method_note = _12m_pt_method or "—"
    _pt_below_current = _cp > 0 and (_12m_pt or 0) < _cp
    _pt_label = f"<b>{_iv_str(_12m_pt)}</b>" + (" ⚠ below spot" if _pt_below_current else "")
    m2_rows.append([
        Paragraph("<b>EV (DCF intrinsic) / 12m PT</b>", styles["RptLabel"]),
        Paragraph("—", styles["RptLabel"]),
        Paragraph(f"<b>{_iv_str(_ev)}</b>", styles["RptLabel"]),
        Paragraph(f"<b>{_iv_str(scen_rec.get('blended_iv'))}</b>", styles["RptLabel"]),
        Paragraph(_pt_label, styles["RptLabel"]),
        Paragraph(_pt_method_note, styles["RptBody"]),
    ])

    m2_tbl = Table(m2_rows, colWidths=_m2_w, hAlign="LEFT", repeatRows=1)
    m2_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
        ("BACKGROUND",    (0, -1), (-1, -1), C_PALE),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    story.append(m2_tbl)
    story.append(Spacer(1, 6))

    # ── Change 5: Valuation ladder bridge ─────────────────────────────────────
    # When the sensitivity center diverges materially from the blended IV and/or
    # the 12m PT, add an explicit arithmetic bridge so the reader understands
    # which figure to trust and why each differs.
    _recon_blended = scen_rec.get("blended_iv") or _dcf_base_iv
    _recon_cp      = scen_rec.get("current_price") or scenario.get("current_price") or 0.0
    _recon_pt      = scenario.get("12m_price_target") or 0.0
    if _recon_blended and _recon_cp:
        _rpt_ccy_scen = (dcf_data.get("reported_currency", "USD") or "USD") if dcf_data else "USD"
        _crp_note = (
            f" (WACC includes +{dcf_data.get('crp', 0):.1%} country risk premium for "
            f"{_rpt_ccy_scen} jurisdiction)"
            if dcf_data and dcf_data.get("crp", 0) > 0 else ""
        )
        _bridge_lines = [
            f"<b>Valuation Ladder Bridge</b>{_crp_note}:",
            f"  • DCF Sensitivity Centre (WACC×TGR grid) — recomputed from stored parameters; "
            f"may differ from Blended IV if revenue_base unit or FX conversion is inconsistent.",
            f"  • Blended IV = <b>${_recon_blended:.2f}</b> — probability-weighted multi-method "
            f"intrinsic value (authoritative long-run fair value).",
        ]
        if _recon_pt:
            _pt_upside = (_recon_pt - _recon_cp) / _recon_cp * 100 if _recon_cp else 0
            _pt_vs_iv  = (_recon_pt - _recon_blended) / _recon_blended * 100 if _recon_blended else 0
            _bridge_lines.append(
                f"  • 12m Price Target = <b>${_recon_pt:.2f}</b> "
                f"({_pt_upside:+.1f}% vs spot; {_pt_vs_iv:+.1f}% vs blended IV) — "
                f"forward market multiple ({_12m_pt_method or 'EV/Revenue or EV/EBITDA'}), "
                f"reflects market pricing not intrinsic value."
            )
        _bridge_lines.append(
            f"  • Current Price = <b>${_recon_cp:.2f}</b> — market quote."
        )
        if _recon_blended and _recon_cp:
            _iv_vs_spot = (_recon_blended - _recon_cp) / _recon_cp * 100
            _bridge_lines.append(
                f"  • Implied MoS (Blended IV vs Current): {_iv_vs_spot:+.1f}%."
            )
        story.append(Paragraph("<br/>".join(_bridge_lines), styles["RptBody"]))
        story.append(Spacer(1, 6))

    # ── (3) Decision Snapshot ─────────────────────────────────────────────────
    story.append(Paragraph("(3) Decision Snapshot", styles["RptLabel"]))
    story.append(Spacer(1, 2))

    action    = _strip(decision.get("action", "—"))
    pos_size  = decision.get("position_size_pct", 0)
    entry_lo  = decision.get("entry_price_low",  decision.get("entry_range", [None, None])[0] if isinstance(decision.get("entry_range"), list) else None)
    entry_hi  = decision.get("entry_price_high", decision.get("entry_range", [None, None])[-1] if isinstance(decision.get("entry_range"), list) else None)
    stop      = decision.get("stop_loss")
    pt        = decision.get("price_target")
    rationale = _strip(decision.get("rationale", "") or "")
    horizon   = _strip(decision.get("time_horizon", "—"))

    snap_rows = [
        ["Action",         action],
        ["Position Size",  f"{pos_size:.1%}" if isinstance(pos_size, float) else str(pos_size)],
        ["Entry Range",    f"${entry_lo:.2f} – ${entry_hi:.2f}" if entry_lo and entry_hi else "—"],
        ["Stop Loss",      f"${stop:.2f}" if stop else "—"],
        ["Price Target",   f"${pt:.2f}" if pt else "—"],
        ["Time Horizon",   horizon],
        ["Rationale",      rationale[:300] if rationale else "—"],
    ]
    _snap_c1 = page_w * 0.20
    _snap_c2 = page_w * 0.80
    snap_tbl = Table(
        [[Paragraph(r, styles["RptLabel"]), Paragraph(v, styles["RptBody"])] for r, v in snap_rows],
        colWidths=[_snap_c1, _snap_c2],
        hAlign="LEFT",
    )
    snap_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), C_PALE),
        ("TEXTCOLOR",     (0, 0), (0, -1), C_NAVY),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))
    story.append(snap_tbl)
    story.append(Spacer(1, 8))

    return story


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
    fwd_flags = dcf_data.get("forward_flags", [])
    shares   = dcf_data.get("shares_outstanding", 0)
    net_debt = dcf_data.get("net_debt", None)
    revenue_base = dcf_data.get("revenue_base", 0)
    proj_rows = dcf_data.get("projection_rows", [])
    reported_currency = dcf_data.get("reported_currency", "USD") or "USD"
    fx_rate   = dcf_data.get("fx_rate", 1.0) or 1.0
    fx_note   = dcf_data.get("fx_note", "") or ""

    # ── Header info block ────────────────────────────────────────────────────
    cal_status = "CALIBRATION WARN" if cal_err else "CALIBRATION PASS"
    cal_color  = C_AMBER if cal_err else C_GREEN
    flag_text  = ("  |  Flags: " + "; ".join(fwd_flags)) if fwd_flags else ""

    ds_display = (data_src.replace("analyst", "Analyst consensus")
                          .replace("historical", "Historical CAGR")
                          .replace("guided", "Company guidance"))

    header_rows = [
        ("Profile",        _strip(profile)),
        ("Data Source",    _strip(ds_display)),
        ("Macro modifier", f"C_macro = {c_mac:+.3f}"),
        ("Calibration",    f"{cal_status}  {_strip(cal_note[:120])}"),
    ]
    if reported_currency != "USD":
        fx_display = f"{reported_currency}→USD @ {fx_rate:.4f}  |  {_strip(fx_note[:100])}"
        header_rows.insert(1, ("Currency (FX)", fx_display))
    if flag_text:
        header_rows.append(("Forward flags", _strip(flag_text)))

    col1 = page_w * 0.22
    col2 = page_w * 0.78
    h_data = []
    for label, val in header_rows:
        is_cal = label == "Calibration"
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
            return f"${float(v):.0f}" if v else "—"
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

    for mn in all_method_names:
        w = _pw_map.get(mn)
        wt_str = f"{w/total_weight:.0%}" if w else "—"
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
            Paragraph(f"${current_price:.2f}", styles["RptValue"]),
            Paragraph(f"${current_price:.2f}", styles["RptValue"]),
            Paragraph(f"${current_price:.2f}", styles["RptValue"]),
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
    # CHECK 2 traceability: pull margin_delta_per_year for each scenario
    bear_md = bear.get("margin_delta_per_year", 0.0)
    base_md = base.get("margin_delta_per_year", 0.0)
    bull_md = bull.get("margin_delta_per_year", 0.0)

    def _pct_delta(v):
        """Format margin delta as ±X.XX%/yr for traceability."""
        try:
            f = float(v) * 100
            return f"{f:+.2f}%/yr"
        except Exception:
            return "—"

    # Year-1 projected FCF margins = fcf_margin_start + margin_delta × 1
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
        # CHECK 2 FIX: split margin into base-year anchor + per-year delta + Year-1 projected
        [Paragraph("FCF Margin (base year, pre-delta)", styles["RptBody"]),
         Paragraph(_pct(bear_fcf), styles["RptValue"]),
         Paragraph(_pct(base_fcf), styles["RptValue"]),
         Paragraph(_pct(bull_fcf), styles["RptValue"])],
        [Paragraph("Margin delta / year", styles["RptBody"]),
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
                    Paragraph(f"<b>${base_iv:.2f}</b>", styles["RptLabel"]),
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

    # ── D. Price Target Summary + §7/§8 Reconciliation ────────────────────────
    # §7: 12m Price Target from forward multiples (EV/EBITDA or EV/Revenue)
    # §8: Reconciliation explaining gap between IV, 12m PT, and current price
    story.append(Paragraph("D — Price Target Summary & Reconciliation (§7/§8)", styles["RptSubsection"]))

    pm_target    = decision.get("price_target")
    bull_scen_p  = scenario.get("bull", {}).get("probability", 0)
    base_scen_p  = scenario.get("base", {}).get("probability", 0)
    bear_scen_p  = scenario.get("bear", {}).get("probability", 0)
    ev_upside    = scenario.get("upside_pct", 0)
    expected_val = scenario.get("expected_value", 0)
    _12m_pt      = scenario.get("12m_price_target")
    _12m_scens   = scenario.get("12m_targets_by_scenario", {})
    _12m_method  = scenario.get("12m_pt_method", "forward multiple")
    recon        = scenario.get("reconciliation", {})
    _dir_flag    = scenario.get("directional_consistency_flag", "")

    def _pct_prob(v):
        try:
            return f"{float(v)*100:.0f}%"
        except Exception:
            return "—"

    pt_hdr = [
        Paragraph(_wh(""),    styles["RptLabel"]),
        Paragraph(_wh("Bear"),styles["RptLabel"]),
        Paragraph(_wh("Base"),styles["RptLabel"]),
        Paragraph(_wh("Bull"),styles["RptLabel"]),
    ]
    pt_rows = [
        pt_hdr,
        # §6: Blended intrinsic value (multi-method, probability-weighted by scenario)
        [Paragraph("Blended IV (§6 intrinsic)", styles["RptBody"]),
         Paragraph(f"${bear_iv:.0f}" if bear_iv else "—", styles["RptValue"]),
         Paragraph(f"${base_iv:.0f}" if base_iv else "—", styles["RptValue"]),
         Paragraph(f"${bull_iv:.0f}" if bull_iv else "—", styles["RptValue"])],
        [Paragraph("Scenario probability", styles["RptBody"]),
         Paragraph(_pct_prob(bear_scen_p), styles["RptValue"]),
         Paragraph(_pct_prob(base_scen_p), styles["RptValue"]),
         Paragraph(_pct_prob(bull_scen_p), styles["RptValue"])],
    ]
    # §7: 12m forward-multiple price targets (distinct from IV)
    if _12m_scens:
        pt_rows.append([
            Paragraph(f"12m PT — {_12m_method[:40]}", styles["RptBody"]),
            Paragraph(_fmtiv(_12m_scens.get("bear")), styles["RptValue"]),
            Paragraph(_fmtiv(_12m_scens.get("base")), styles["RptValue"]),
            Paragraph(_fmtiv(_12m_scens.get("bull")), styles["RptValue"]),
        ])
    if expected_val:
        pt_rows.append([
            Paragraph("<b>Expected Value EV (prob-wtd IV)</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>${expected_val:.2f}</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])
    if _12m_pt:
        pt_rows.append([
            Paragraph("<b>12m Price Target (prob-wtd)</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>${float(_12m_pt):.2f}</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])
    if current_price:
        pt_rows.append([
            Paragraph("Current price", styles["RptBody"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"${current_price:.2f}", styles["RptValue"]),
            Paragraph("", styles["RptValue"]),
        ])
    # §11: Skew ratio and upside/downside to targets
    _up_to_pt = recon.get("upside_to_pt_pct")
    _down_to_bear = recon.get("downside_to_bear_pct")
    _skew = recon.get("skew_ratio")
    if _up_to_pt is not None:
        sign = "+" if float(_up_to_pt) >= 0 else ""
        pt_rows.append([
            Paragraph("<b>Upside to 12m PT</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>{sign}{float(_up_to_pt):.1f}%</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])
    if _down_to_bear is not None:
        sign = "+" if float(_down_to_bear) >= 0 else ""
        pt_rows.append([
            Paragraph("<b>Downside to Bear IV</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>{sign}{float(_down_to_bear):.1f}%</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])
    if _skew is not None:
        pt_rows.append([
            Paragraph("<b>Skew ratio (up/down)</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>{float(_skew):.1f}x</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])
    if pm_target:
        pt_rows.append([
            Paragraph("<b>Portfolio Manager target</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
            Paragraph(f"<b>${float(pm_target):.2f}</b>", styles["RptLabel"]),
            Paragraph("", styles["RptValue"]),
        ])

    aw3 = [page_w * 0.40, page_w * 0.20, page_w * 0.20, page_w * 0.20]
    ptt = Table(pt_rows, colWidths=aw3, hAlign="LEFT", repeatRows=1)
    ptt.setStyle(TableStyle([
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
    story.append(ptt)
    story.append(Spacer(1, 6))

    # CHECK 4: EV arithmetic flag — show if LLM EV was overridden by Python computation
    _ev_arith_flag = scenario.get("ev_arithmetic_flag")
    if _ev_arith_flag:
        story.append(Paragraph(
            f"<b>EV Arithmetic Check:</b> {_ev_arith_flag}",
            styles.get("RptWarning", styles["RptBody"]),
        ))
        story.append(Spacer(1, 4))

    # §8 Reconciliation note: explain IV vs 12m PT gap + any directional flag
    _recon_text = (
        "<b>§8 Reconciliation:</b> "
        f"Blended IV (${base_iv:.0f} base) measures long-run intrinsic value via {methods_count} "
        f"method{'s' if methods_count != 1 else ''}. "
        f"12m Price Target (${_12m_pt:.2f}) is a market-pricing exercise using forward "
        f"sector multiples — the gap to current price (${current_price:.2f}) reflects near-term "
        "multiple compression/expansion vs. long-run fair value. "
        "These are independent outputs; expect divergence when macro or sentiment dislocations exist."
    ) if base_iv and _12m_pt and current_price else ""
    if _recon_text:
        story.append(Paragraph(_recon_text, styles["RptBody"]))
        story.append(Spacer(1, 4))
    if _dir_flag:
        story.append(Paragraph(
            f"<b>Consistency Flag:</b> {_dir_flag}",
            styles.get("RptWarning", styles["RptBody"]),
        ))
        story.append(Spacer(1, 4))
    story.append(Spacer(1, 6))

    return story


# ── Cover Card ─────────────────────────────────────────────────────────────────
def _cover_card(
    ticker: str,
    decision: dict,
    scenario_data: dict,
    macro: dict,
    sector: str,
    styles,
    page_w: float,
) -> list:
    """Professional 2-column cover card: large rating badge + thesis bullets + key metrics."""
    action        = _strip(decision.get("action", "—")).upper()
    size_pct      = decision.get("position_size_pct") or 0.0
    stop          = decision.get("stop_loss")
    target        = decision.get("price_target")
    horizon       = _strip(decision.get("time_horizon", "—"))
    rationale_raw = _ANSI_RE.sub("", str(
        decision.get("rationale", decision.get("reasoning", "")) or ""
    ))

    current_price = 0.0
    try:
        current_price = float(scenario_data.get("current_price") or 0)
    except (TypeError, ValueError):
        pass

    # ── Price target + upside % ───────────────────────────────────────────────
    upside_pct = None
    if target and current_price:
        try:
            upside_pct = (float(target) - current_price) / current_price * 100
        except (TypeError, ValueError):
            pass
    if target is not None:
        try:
            t_val = float(target)
            if upside_pct is not None:
                sign = "+" if upside_pct >= 0 else ""
                pt_display = f"${t_val:.2f}  ({sign}{upside_pct:.1f}%)"
            else:
                pt_display = f"${t_val:.2f}"
        except (TypeError, ValueError):
            pt_display = "—"
    else:
        pt_display = "—"

    # ── Rating badge colour ───────────────────────────────────────────────────
    badge_bg = {
        "BUY": C_GREEN, "COVER": C_GREEN,
        "HOLD": C_AMBER,
        "SELL": C_RED,  "SHORT": C_RED,
    }.get(action, C_NAVY)

    # ── LEFT COLUMN: ticker name, sector tag, thesis bullets ─────────────────
    left_w   = page_w * 0.62
    _tk_sty  = ParagraphStyle(name="_cc_tk",  fontName="Helvetica-Bold",
                               fontSize=22, leading=26, textColor=C_NAVY, spaceAfter=2)
    _sec_sty = ParagraphStyle(name="_cc_sec", fontName="Helvetica",
                               fontSize=9,  leading=11, textColor=C_GREY,  spaceAfter=6)
    _bul_sty = ParagraphStyle(name="_cc_bul", fontName="Helvetica",
                               fontSize=8.5, leading=12, textColor=colors.black,
                               leftIndent=8, spaceAfter=3)

    left_cells = [
        [Paragraph(ticker, _tk_sty)],
        [Paragraph(_strip(sector), _sec_sty)],
    ]
    sentences = re.split(r'(?<=[.!?])\s+', rationale_raw.strip())
    for s in [s.strip() for s in sentences if len(s.strip()) > 20][:4]:
        left_cells.append([Paragraph(f"• {html.escape(s)}", _bul_sty)])

    left_tbl = Table(left_cells, colWidths=[left_w])
    left_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))

    # ── RIGHT COLUMN: large badge + metrics table ─────────────────────────────
    right_w    = page_w * 0.38
    _badge_sty = ParagraphStyle(name="_cc_bdg", fontName="Helvetica-Bold",
                                 fontSize=22, leading=26,
                                 textColor=colors.white, alignment=1)

    badge_tbl = Table([[Paragraph(action, _badge_sty)]], colWidths=[right_w])
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, 0), badge_bg),
        ("ALIGN",         (0, 0), (0, 0), "CENTER"),
        ("VALIGN",        (0, 0), (0, 0), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (0, 0), 12),
        ("BOTTOMPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING",   (0, 0), (0, 0), 4),
        ("RIGHTPADDING",  (0, 0), (0, 0), 4),
    ]))

    m1, m2 = right_w * 0.46, right_w * 0.54
    metrics = [("Price Target", pt_display)]
    if current_price:
        metrics.append(("Current Price", f"${current_price:.2f}"))
    metrics += [
        ("Position Size", f"{size_pct:.1%}"),
        ("Stop Loss",     f"${float(stop):.2f}" if isinstance(stop, (int, float)) else "—"),
        ("Time Horizon",  horizon),
        ("Macro",         f"{macro.get('risk_appetite','—')} / {macro.get('rate_direction','—')}"),
    ]
    metrics_tbl = Table(
        [[Paragraph(_strip(k), styles["RptLabel"]),
          Paragraph(html.escape(str(v)), styles["RptValue"])]
         for k, v in metrics],
        colWidths=[m1, m2],
    )
    metrics_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, C_PALE]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
    ]))

    right_tbl = Table([[badge_tbl], [metrics_tbl]], colWidths=[right_w])
    right_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 1), (0, 1), 6),   # gap between badge and metrics
        ("TOPPADDING",   (0, 0), (0, 0), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    # ── OUTER 2-COLUMN TABLE ─────────────────────────────────────────────────
    outer = Table([[left_tbl, right_tbl]], colWidths=[left_w, right_w])
    outer.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEAFTER",    (0, 0), (0, 0), 0.5, C_LGREY),
    ]))

    return [outer, Spacer(1, 6), _hr()]


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
    power_law_analysis, value_trap_analysis, debate_result, analyst_signals,
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
        "debate_result",
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
def generate_pdf_report(result: dict, output_path: str | None = None) -> str:
    """
    Generate an untruncated PDF investment report from the advanced pipeline
    result dict.

    Args:
        result:      The dict returned by run_advanced_pipeline().
        output_path: Optional file path.  Defaults to
                     report_<TICKERS>_<YYYYMMDD_HHMM>.pdf in the cwd.

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
    doc     = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=margin,  rightMargin=margin,
        topMargin=20 * mm,  bottomMargin=18 * mm,   # extra room for header/footer
    )
    page_w = A4[0] - 2 * margin
    col1   = page_w * 0.26
    col2   = page_w * 0.74

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
    debate_result   = result.get("debate_result") or {}
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

    # ═══════════════════════════════════════════════════════════════════════════
    # HEADER — two-column layout: exec narrative (left) | rating scorecard (right)
    # ═══════════════════════════════════════════════════════════════════════════
    if len(tickers) == 1:
        _t       = tickers[0]
        _dec     = decisions.get(_t, {})
        _act     = _strip(_dec.get("action", "")).upper()
        _co_name = _fetch_company_name(_t)

        # Pre-compute data for both columns
        _dcf_all    = result.get("dcf_range", {})
        _dcf_ticker = _dcf_all.get(_t, {})
        _scen_t     = (result.get("scenario_analysis") or {}).get(_t, {})
        _pl_t       = (result.get("power_law_analysis") or {}).get(_t, {})
        _trap_t     = (result.get("value_trap_analysis") or {}).get(_t, {})

        _rat  = _strip(_dec.get("rationale", "") or _dec.get("reasoning", ""))
        _sent = (_rat.split(".")[0].strip() + ".") if _rat else ""
        _kp   = _build_key_points(
            ticker=_t, decision=_dec, scen=_scen_t, pl=_pl_t,
            trap=_trap_t, analyst_signals=analyst_signals, dcf_ticker=_dcf_ticker,
        )

        # ── GS-style cover: full-width title + price line, then 2-col split ────
        # LEFT 38% = data panel (badge pill + key metrics)
        # RIGHT 62% = narrative (exec line + executive summary + bullets)
        _col_l_w = page_w * 0.38
        _col_r_w = page_w * 0.62

        _pt_raw    = _dec.get("price_target")
        # Fix 1c: treat 0.0 price_target as absent — "$0.00" is never a valid target.
        # For SELL/SHORT, portfolio_manager now always provides a bear anchor, but
        # guard here as well so stale cached results also render cleanly.
        _pt        = _pt_raw if (isinstance(_pt_raw, (int, float)) and _pt_raw > 0) else None
        _curr      = _scen_t.get("current_price")
        _upside    = _scen_t.get("upside_pct")
        _size_pct  = _dec.get("position_size_pct", 0)
        _stop      = _dec.get("stop_loss")
        _entry     = _dec.get("entry_range", [])
        _base_iv   = (_dcf_ticker.get("base") or {}).get("intrinsic_value")
        _horizon   = _strip(_dec.get("time_horizon", "—")).title()
        _macro_str = (
            f'{_strip(str(macro.get("risk_appetite","—"))).replace("-"," ").title()} / '
            f'{_strip(str(macro.get("rate_direction","—"))).replace("-"," ").title()}'
        )
        def _r_fmt(v):
            return f"${v:.2f}" if isinstance(v, (int, float)) else "—"
        _entry_s  = (f"${_entry[0]:.2f} – ${_entry[1]:.2f}"
                     if isinstance(_entry, list) and len(_entry) == 2 else "—")
        # Header upside: use PT-based upside (PM final price_target vs current price)
        # EV upside (probability-weighted) is shown separately in the KPI box
        _pt_upside = (
            (_pt - _curr) / _curr * 100
            if isinstance(_pt, (int, float)) and isinstance(_curr, (int, float)) and _curr > 0
            else None
        )
        _upside_s = (
            f"{_pt_upside:+.1f}%"
            if _pt_upside is not None
            else (f"{_upside:+.1f}%" if isinstance(_upside, (int, float)) else "—")
        )
        _action_colour = {"BUY": C_GREEN, "COVER": C_GREEN,
                          "SELL": C_RED,  "SHORT": C_RED}.get(_act, C_AMBER)

        # Full-width: company name
        story.append(Paragraph(f"{_co_name} ({_t})", styles["RptTitle"]))
        # Full-width: compact price data line (#3 — GS-style inline prices)
        story.append(Paragraph(
            f"12m Price Target: <b>{_r_fmt(_pt)}</b>  \u2502  "
            f"Price: <b>{_r_fmt(_curr)}</b>  \u2502  "
            f"Upside: <b>{_upside_s}</b>  \u2502  "
            f"Horizon: <b>{_horizon}</b>",
            styles["RptPriceLine"],
        ))
        story.append(Spacer(1, 5))

        # ── LEFT: small badge pill (#2) + borderless key metrics table ────────
        _badge_tbl = Table(
            [[Paragraph(f'<font color="white"><b>  {_act}  </b></font>',
                        styles["RptLabel"]), ""]],
            colWidths=[_col_l_w * 0.52, _col_l_w * 0.48], hAlign="LEFT",
        )
        _badge_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (0, 0), _action_colour),
            ("ALIGN",         (0, 0), (0, 0), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ]))

        def _sc_row(label, value):
            return [Paragraph(label, styles["RptLabel"]),
                    Paragraph(value, styles["RptValue"])]

        _12m_pt_cover = _scen_t.get("12m_price_target")
        _ev_cover     = _scen_t.get("expected_value")
        # PM final price_target takes priority; show fwd model estimate as secondary label
        _kpi_pt = _pt or _12m_pt_cover
        _kpi_pt_label = "12m Price Tgt"
        if _pt and _12m_pt_cover and abs(_pt - _12m_pt_cover) > 0.5:
            _kpi_pt_label = f"12m Price Tgt (fwd: {_r_fmt(_12m_pt_cover)})"
        # Prob-Wtd upside in KPI box = EV-based upside (expected value vs current price)
        _ev_upside_s = (f"{_upside:+.1f}%" if isinstance(_upside, (int, float)) else "—")
        # Blended IV: prefer probability-weighted value from reconciliation; fallback to base DCF IV
        _blended_iv_kpi = (_scen_t.get("reconciliation") or {}).get("blended_iv") or _base_iv
        _metrics_tbl = Table(
            [_sc_row("Time Horizon",     _horizon),
             _sc_row("Macro Regime",     _macro_str),
             _sc_row(_kpi_pt_label,      _r_fmt(_kpi_pt)),
             _sc_row("Blended IV",       _r_fmt(_blended_iv_kpi)),
             _sc_row("Exp. Value",       _r_fmt(_ev_cover)),
             _sc_row("Current Price",    _r_fmt(_curr)),
             _sc_row("Prob-Wtd Upside",  _ev_upside_s),
             _sc_row("Position Size",    f"{_size_pct:.1%}"),
             _sc_row("Stop Loss",        _r_fmt(_stop)),
             _sc_row("Entry Range",      _entry_s)],
            colWidths=[_col_l_w * 0.52, _col_l_w * 0.48], hAlign="LEFT",
        )
        _metrics_tbl.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, C_PALE]),
            ("FONTSIZE",       (0, 0), (-1, -1), 7.5),
            ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",     (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
            ("LEFTPADDING",    (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
            ("LINEBELOW",      (0, 0), (-1, -1), 0.25, C_LGREY),
            ("BOX",            (0, 0), (-1, -1), 0.5, C_LGREY),
        ]))

        # Stack badge + metrics in the left cell
        _left_inner = Table(
            [[_badge_tbl], [_metrics_tbl]],
            colWidths=[_col_l_w], hAlign="LEFT",
        )
        _left_inner.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))

        # ── RIGHT: exec line + Executive Summary + bullets ────────────────────
        _right_rows: list[list] = []
        if _sent:
            _right_rows.append([Paragraph(_sent, styles["RptExecLine"])])
        _right_rows.append(
            [Paragraph("<b>Executive Summary</b>", styles["RptExecHeader"])]
        )
        for _bp in _kp:
            _right_rows.append([Paragraph(f"• {_strip(_bp)}", styles["RptBullet"])])
        _right_tbl = Table(_right_rows, colWidths=[_col_r_w - 4], hAlign="LEFT")
        _right_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))

        # ── Outer two-column frame ─────────────────────────────────────────────
        _outer = Table(
            [[_left_inner, _right_tbl]],
            colWidths=[_col_l_w, _col_r_w], hAlign="LEFT",
        )
        _outer.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        story.append(_outer)

    else:
        # Multi-ticker: compact header only
        story.append(Paragraph(
            f"{', '.join(tickers)} — Multi-Ticker Analysis", styles["RptTitle"],
        ))
        _acts = "  |  ".join(
            f"{t}: {_strip(decisions.get(t, {}).get('action', '—')).upper()}"
            for t in tickers
        )
        story.append(Paragraph(_acts, styles["RptSubtitle"]))

    story.append(_hr())

    story.append(Spacer(1, 4))

    # ── VGPM Scorecard — one card row per ticker ─────────────────────────────
    _dcf_all_vgpm  = result.get("dcf_range", {})
    _scen_all_vgpm = result.get("scenario_analysis") or {}
    _cal_all_vgpm  = result.get("dcf_calibration_signals") or {}
    _rf_raw        = result.get("raw_financials") or {}
    _ins_raw       = result.get("insider_summary") or result.get("insider_activity") or {}
    for _vt in tickers:
        _vgpm_dcf  = _dcf_all_vgpm.get(_vt, {})
        _vgpm_scen = _scen_all_vgpm.get(_vt, {})
        _vgpm_cal  = _cal_all_vgpm.get(_vt, {})
        # insider_summary may be a plain string, a dict keyed by ticker, or the
        # per-ticker value may itself be a dict (signal/summary sub-keys).
        # Always resolve to a plain string before passing to _compute_vgpm.
        _ins_raw_val = _ins_raw.get(_vt, "") if isinstance(_ins_raw, dict) else (_ins_raw or "")
        if isinstance(_ins_raw_val, dict):
            # flatten: prefer "summary" or "insider_summary" sub-key, else stringify
            _ins_str = str(
                _ins_raw_val.get("insider_summary")
                or _ins_raw_val.get("summary")
                or _ins_raw_val.get("signal")
                or _ins_raw_val
            )
        else:
            _ins_str = str(_ins_raw_val or "")
        if _vgpm_dcf or _vgpm_scen:
            _vgpm_scores = _compute_vgpm(
                dcf_ticker=_vgpm_dcf,
                scen_ticker=_vgpm_scen,
                raw_financials=_rf_raw,
                dcf_cal=_vgpm_cal,
                insider_summary=_ins_str,
            )
            _co_vgpm = _fetch_company_name(_vt)
            story.extend(_vgpm_scorecard(_vgpm_scores, _vt, _co_vgpm, styles, page_w))

    # ── Price Sparkline (item 6D) — prefer pipeline data, fall back to API ──
    _ph_pipeline = result.get("price_history", {})
    for _sp_t in tickers:
        # Pipeline data is [{date, close}] dicts; convert to (date, close) tuples
        _ph_raw = _ph_pipeline.get(_sp_t, [])
        if _ph_raw:
            _ph = [(p["date"], p["close"]) for p in _ph_raw]
        else:
            _ph = _fetch_price_history(_sp_t)   # live fallback
        _pt_val = decisions.get(_sp_t, {}).get("price_target")
        if _ph:
            story.append(Paragraph(
                f"12-Month Price History — {_sp_t}",
                styles["RptLabel"],
            ))
            story.append(Spacer(1, 2))
            story.append(_PriceSparkline(_ph, _pt_val, page_w))
            story.append(Spacer(1, 6))

    # ── Key Financials table (item 15) ───────────────────────────────────────
    _kf = _key_financials_table(raw_financials, styles, page_w)
    if _kf:
        story.append(_kf)
        story.append(Spacer(1, 6))

    # ── Intelligence Signals — Phase 2.5 (item I) ────────────────────────────
    # Show per-ticker if single ticker; aggregate header if multi-ticker (use first)
    _intel_ticker = tickers[0] if tickers else None
    if _intel_ticker:
        _si_data  = short_interest.get(_intel_ticker) or {}
        _eq_data  = earnings_qual.get(_intel_ticker) or {}
        _ia_data  = insider_activity.get(_intel_ticker) or {}
        _ns_data  = news_sentiment.get(_intel_ticker) or {}
        _ar_data  = analyst_revisions.get(_intel_ticker) or {}
        story.extend(_intel_summary(
            _intel_ticker, _si_data, _eq_data,
            _ia_data, _ns_data, _ar_data,
            styles, page_w,
        ))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — INDUSTRY INTELLIGENCE BRIEF  (after executive summary)
    # ═══════════════════════════════════════════════════════════════════════════
    story.extend(_section_header("SECTION 1 — INDUSTRY INTELLIGENCE BRIEF", page_w))

    # Determine source tier for attribution (deep_research = live web; else LLM knowledge)
    _ib_src_raw = result.get("industry_brief", "") or ""
    _used_deep  = (not _ib_src_raw or _ib_src_raw.startswith(_BRIEF_ERROR_PREFIX))
    _ib_source  = (
        "Anthropic Web Search (live) + Financial Datasets API"
        if _used_deep else
        "AI Hedge Fund Phase 3 Industry Specialist · Financial Datasets API · "
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
    # SECTION 2 — FULL RESULT SUMMARY  (per ticker, untruncated)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.extend(_section_header("SECTION 2 — FULL RESULT SUMMARY", page_w))
    story.append(Spacer(1, 8))

    for i, (ticker, decision) in enumerate(decisions.items()):
        if i > 0:
            story.append(PageBreak())
        story.append(Paragraph(f"Ticker: {ticker}", styles["RptSubsection"]))
        story.append(_hr())

        # Pull per-ticker analytics upfront (used across multiple sections)
        scen = scenario.get(ticker, {})
        pl   = power_law.get(ticker, {})
        trap = value_trap.get(ticker, {})

        # ── 1. Investment Decision — rationale only (metrics shown on cover scorecard)
        rationale = _collect(decision.get("rationale", decision.get("reasoning", "")))
        if rationale:
            story.append(Paragraph("Investment Decision", styles["RptSubsection"]))
            story.append(Paragraph(rationale, styles["RptBody"]))
            story.append(Spacer(1, 8))

        # ── 1b. Key Catalysts (item 4C) ───────────────────────────────────────
        story.extend(_catalyst_section(
            ticker          = ticker,
            analyst_signals = analyst_signals,
            scenario        = scenario,
            debate_result   = debate_result,
            styles          = styles,
            page_w          = page_w,
        ))

        # ── 2. Scenario Analysis (item 3+4: moved up, renamed, structured table)
        story.append(Paragraph("Scenario Analysis", styles["RptSubsection"]))

        bull_fv = scen.get("bull", {}).get("fair_value")
        base_fv = scen.get("base", {}).get("fair_value")
        bear_fv = scen.get("bear", {}).get("fair_value")
        bull_p  = scen.get("bull", {}).get("probability", 0)
        base_p  = scen.get("base", {}).get("probability", 0)
        bear_p  = scen.get("bear", {}).get("probability", 0)
        bull_assum = _collect(scen.get("bull", {}).get("assumptions", ""))
        base_assum = _collect(scen.get("base", {}).get("assumptions", ""))
        bear_assum = _collect(scen.get("bear", {}).get("assumptions", ""))
        ev_val  = scen.get("expected_value")
        upside  = scen.get("upside_pct")

        def _fv(v): return f"${v:.0f}" if isinstance(v, (int, float)) else "—"
        def _pp(v): return f"{v*100:.0f}%" if isinstance(v, (int, float)) and v else "—"

        scen_w = [page_w * 0.28, page_w * 0.24, page_w * 0.24, page_w * 0.24]
        scen_rows = [
            [Paragraph(_wh(""),     styles["RptLabel"]),
             Paragraph(_wh("Bear"), styles["RptLabel"]),
             Paragraph(_wh("Base"), styles["RptLabel"]),
             Paragraph(_wh("Bull"), styles["RptLabel"])],
            [Paragraph("Fair Value",  styles["RptBody"]),
             Paragraph(_fv(bear_fv),  styles["RptValue"]),
             Paragraph(_fv(base_fv),  styles["RptValue"]),
             Paragraph(_fv(bull_fv),  styles["RptValue"])],
            [Paragraph("Probability", styles["RptBody"]),
             Paragraph(_pp(bear_p),   styles["RptValue"]),
             Paragraph(_pp(base_p),   styles["RptValue"]),
             Paragraph(_pp(bull_p),   styles["RptValue"])],
        ]
        if any([bear_assum, base_assum, bull_assum]):
            scen_rows.append([
                Paragraph("Key Assumptions", styles["RptBody"]),
                Paragraph(bear_assum, styles["RptBody"]),
                Paragraph(base_assum, styles["RptBody"]),
                Paragraph(bull_assum, styles["RptBody"]),
            ])
        if ev_val:
            scen_rows.append([
                Paragraph("<b>Expected Value</b>", styles["RptLabel"]),
                Paragraph("", styles["RptValue"]),
                Paragraph(f"<b>${float(ev_val):.2f}</b>", styles["RptLabel"]),
                Paragraph("", styles["RptValue"]),
            ])
        if upside is not None:
            _sign = "+" if float(upside) >= 0 else ""
            scen_rows.append([
                Paragraph("<b>Prob-Wtd Upside / Downside vs Current Price</b>", styles["RptLabel"]),
                Paragraph("", styles["RptValue"]),
                Paragraph(f"<b>{_sign}{float(upside):.1f}%</b>", styles["RptLabel"]),
                Paragraph("", styles["RptValue"]),
            ])

        scen_tbl = Table(scen_rows, colWidths=scen_w, hAlign="LEFT", repeatRows=1)
        scen_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_PALE),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_NAVY),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -3), [colors.white, C_PALE]),
            ("BACKGROUND",    (0, -2), (-1, -1), C_PALE),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.25, C_LGREY),
        ]))
        story.append(scen_tbl)
        story.append(Spacer(1, 8))

        # ── 3. Valuation Analysis (item 4: renamed from "Phase 4.5 — Valuation Model") ──
        story.append(Paragraph("Valuation Analysis", styles["RptSubsection"]))
        dcf_all    = result.get("dcf_range", {})
        dcf_ticker = dcf_all.get(ticker, {})
        story.extend(_section_2f(
            ticker   = ticker,
            dcf_data = dcf_ticker,
            decision = decision,
            scenario = scen,
            styles   = styles,
            page_w   = page_w,
        ))
        story.append(Spacer(1, 4))
        # ── 3a. Forward Financial Estimates — Year 1–5 (item 5A) ─────────────
        _fwd_rows = _forward_financial_model(dcf_ticker, styles, page_w)
        if _fwd_rows:
            story.append(Paragraph(
                "Forward Financial Estimates — DCF Base Case (Year 1–5)",
                styles["RptLabel"],
            ))
            story.append(Spacer(1, 2))
            story.extend(_fwd_rows)
            story.append(Spacer(1, 4))
        # ── 3b. Industry Peer Comparison (item B) ────────────────────────────
        _all_peers = result.get("peer_comparison", {})
        _peer_rows = _peer_comparison_table(
            _all_peers.get(ticker, {}), ticker, styles, page_w
        )
        if _peer_rows:
            story.append(Paragraph("Industry Peer Comparison", styles["RptLabel"]))
            story.append(Spacer(1, 2))
            story.extend(_peer_rows)

        # ── 3c. Sensitivity Analysis (item G) ────────────────────────────────
        _sens_cp   = scen.get("current_price") or scen.get("reconciliation", {}).get("current_price")
        _sens_pt   = scen.get("12m_price_target")
        _sens_meth = scen.get("12m_pt_method")
        story.extend(_sensitivity_table(
            dcf_ticker, styles, page_w,
            current_price=_sens_cp,
            pt_12m=_sens_pt,
            pt_method=_sens_meth,
        ))
        # P0.2 — second grid: Revenue Growth × FCF Margin
        story.extend(_sensitivity_table_growth_margin(
            dcf_ticker, styles, page_w,
            current_price=_sens_cp,
            pt_12m=_sens_pt,
        ))
        story.append(Spacer(1, 4))

        # ── 4. Risk Assessment (item 3+4: renamed; consolidates risk mgr + value trap) ──
        story.append(Paragraph("Risk Assessment", styles["RptSubsection"]))

        risk      = analyst_signals.get("advanced_risk_manager", {}).get(ticker, {})
        risk_rows = []
        if risk:
            approved  = risk.get("approved_size_pct", 0)
            risk_rows.append(["Approved Position Size", f"{approved:.1%}"])
            all_flags = risk.get("level1_flags", []) + risk.get("sector_flags", [])
            for flag in all_flags:
                risk_rows.append(["Risk Flag", _collect(flag)])
            if not all_flags:
                risk_rows.append(["Risk Flags", "None — all checks passed"])

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

        # ── 5. Analyst Committee (item 4: renamed from "Agent Signals") ─────
        story.append(Paragraph("Analyst Committee", styles["RptSubsection"]))

        hdr_style = TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), C_PALE),
            ("TEXTCOLOR",    (0, 0), (-1, 0), C_NAVY),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, 0), 7.5),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_PALE]),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.25, C_LGREY),
            ("FONTSIZE",     (0, 1), (-1, -1), 7.5),
        ])
        aw = [
            page_w * 0.14,  # Agent name
            page_w * 0.09,  # Signal
            page_w * 0.04,  # Conviction
            page_w * 0.06,  # Horizon
            page_w * 0.07,  # Price target
            page_w * 0.34,  # Full thesis
            page_w * 0.26,  # Key risks
        ]
        agent_rows = [[
            Paragraph(_wh("Analyst"),   styles["RptLabel"]),
            Paragraph(_wh("Signal"),    styles["RptLabel"]),
            Paragraph(_wh("Conv."),     styles["RptLabel"]),
            Paragraph(_wh("Horizon"),   styles["RptLabel"]),
            Paragraph(_wh("Target"),    styles["RptLabel"]),
            Paragraph(_wh("Thesis"),    styles["RptLabel"]),
            Paragraph(_wh("Key Risks"), styles["RptLabel"]),
        ]]
        for agent_key, sig_map in analyst_signals.items():
            if agent_key in _SKIP_AGENTS:
                continue
            if not isinstance(sig_map, dict) or ticker not in sig_map:
                continue
            sig = sig_map[ticker]
            if not isinstance(sig, dict):
                continue
            raw_signal = _strip(sig.get("signal", "—")).upper()
            conviction = str(sig.get("conviction", "—"))
            a_horizon  = _strip(sig.get("time_horizon", "—"))
            pt         = sig.get("price_target")
            thesis     = _collect(sig.get("thesis_summary", "") or sig.get("reasoning", ""))
            key_risks  = sig.get("key_risks", [])
            risks_str  = "\n".join(f"• {_collect(r)}" for r in key_risks) if key_risks else "—"
            name       = _AGENT_DISPLAY.get(agent_key, agent_key.replace("_", " ").title())
            pt_str     = f"${pt:.2f}" if isinstance(pt, (int, float)) else "—"
            sig_sty    = styles[_SIG_STYLE_MAP.get(raw_signal, "RptBody")]
            agent_rows.append([
                Paragraph(name,       styles["RptLabel"]),
                Paragraph(raw_signal, sig_sty),
                Paragraph(f"{conviction}/10", styles["RptValue"]),
                Paragraph(a_horizon,  styles["RptValue"]),
                Paragraph(pt_str,     styles["RptValue"]),
                Paragraph(thesis,     styles["RptBody"]),
                Paragraph(risks_str,  styles["RptBody"]),
            ])
        if len(agent_rows) > 1:
            at = Table(agent_rows, colWidths=aw, repeatRows=1, hAlign="LEFT")
            at.setStyle(hdr_style)
            story.append(at)
        story.append(Spacer(1, 8))

        # ── 6. Debate Round ───────────────────────────────────────────────────
        story.append(Paragraph("Debate Round", styles["RptSubsection"]))
        dr = debate_result.get(ticker)
        if dr:
            adj_sig   = _strip(dr.get("adjudicated_signal", "—")).upper()
            adj_conv  = str(dr.get("adjudicated_conviction", "—"))
            bull_key  = dr.get("agent_a", "")
            bear_key  = dr.get("agent_b", "")
            bull_name = _AGENT_DISPLAY.get(bull_key, _strip(bull_key).replace("_", " ").title())
            bear_name = _AGENT_DISPLAY.get(bear_key, _strip(bear_key).replace("_", " ").title())
            debate_rows = [
                ["Core Disagreement",  _collect(dr.get("disagreement_core", ""))],
                ["Bull Advocate",      bull_name],
                ["Bull Rebuttal",      _collect(dr.get("agent_a_rebuttal", ""))],
                ["Bear Advocate",      bear_name],
                ["Bear Rebuttal",      _collect(dr.get("agent_b_rebuttal", ""))],
                ["Adjudicated Signal", f"{adj_sig}  (conviction {adj_conv}/10)"],
                ["Moderator Ruling",   _collect(dr.get("adjudication", ""))],
            ]
            story.append(_kv_table(debate_rows, col1, col2, styles))
        else:
            story.append(Paragraph(
                "Debate skipped — no strong conflict (fewer than 3 BUY and 3 SELL on the same ticker).",
                styles["RptBody"],
            ))
        story.append(Spacer(1, 8))

        # ── 7. Power Law Analysis (item 3+4: separated from analytics block, renamed) ──
        story.append(Paragraph("Power Law Analysis", styles["RptSubsection"]))

        pl_score  = pl.get("total_score", "—")
        pl_interp = _collect(pl.get("interpretation", ""))

        # Key conclusion shown ABOVE the table
        if pl_interp:
            story.append(Paragraph(pl_interp, styles["RptBody"]))
            story.append(Spacer(1, 4))

        # Dimension table: Dimension | Score | Justification
        _PL_JUSTIFICATIONS = {
            "scale_economies":  "Unit costs decline as compute volume scales; chip design amortised over larger base.",
            "network_effects":  "CUDA developer ecosystem grows with each GPU generation, raising adoption barriers.",
            "winner_take_most": "AI training workloads concentrated on NVIDIA; hyperscaler procurement reflects this.",
            "switching_costs":  "Re-engineering ML pipelines away from CUDA represents multi-year effort and cost.",
            "data_ip_moat":     "Proprietary CUDA libraries, cuDNN, TensorRT and NIM create deep software lock-in.",
        }
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
        for dim_key, dim_label in _dim_labels.items():
            score_val = pl.get(dim_key, "?")
            # Use agent-provided interpretation if available in the pl dict, else fallback
            justif = _strip(str(pl.get(f"{dim_key}_note", "")
                                or _PL_JUSTIFICATIONS.get(dim_key, "")))
            pl_dim_rows.append([
                Paragraph(dim_label,             styles["RptBody"]),
                Paragraph(f"{score_val} / 2",    styles["RptValue"]),
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

        # ── 8. BU-Level Analysis (Phase 7.5) ─────────────────────────────────
        bu_all = result.get("bu_analysis", {})
        bu = bu_all.get(ticker, {})
        if bu:
            story.append(Paragraph("BU-Level Analysis", styles["RptSubsection"]))

            # KPI Extraction
            kpi = bu.get("kpi_extraction", {})
            if isinstance(kpi, dict):
                kpi_rows = []
                if kpi.get("unit_economics"):
                    kpi_rows.append(["Unit Economics", _collect(kpi["unit_economics"])])
                if kpi.get("backlog_rpo"):
                    kpi_rows.append(["Backlog / RPO", _collect(kpi["backlog_rpo"])])
                if kpi.get("segment_nrr"):
                    kpi_rows.append(["Segment NRR", _collect(kpi["segment_nrr"])])
                if kpi_rows:
                    story.append(Paragraph("KPI Extraction", styles["RptLabel"]))
                    story.append(_kv_table(kpi_rows, col1, col2, styles))
                    story.append(Spacer(1, 4))

            # Margin Attribution
            margin_attr = _collect(bu.get("margin_attribution", ""))
            if margin_attr:
                story.append(Paragraph("Margin Attribution", styles["RptLabel"]))
                story.append(Paragraph(margin_attr, styles["RptBody"]))
                story.append(Spacer(1, 4))

            # Capex Breakdown
            capex_bd = bu.get("capex_breakdown", {})
            if isinstance(capex_bd, dict) and capex_bd.get("commentary"):
                capex_rows = []
                if capex_bd.get("growth_capex_pct") is not None:
                    capex_rows.append(["Growth Capex", f"{capex_bd['growth_capex_pct']:.0f}%"])
                if capex_bd.get("maintenance_capex_pct") is not None:
                    capex_rows.append(["Maintenance Capex", f"{capex_bd['maintenance_capex_pct']:.0f}%"])
                if capex_bd.get("capex_as_pct_revenue") is not None:
                    capex_rows.append(["Capex / Revenue", f"{capex_bd['capex_as_pct_revenue']:.1f}%"])
                if capex_bd.get("commentary"):
                    capex_rows.append(["Commentary", _collect(capex_bd["commentary"])])
                if capex_rows:
                    story.append(Paragraph("Capex Breakdown", styles["RptLabel"]))
                    story.append(_kv_table(capex_rows, col1, col2, styles))
                    story.append(Spacer(1, 4))

            # Product Resilience
            prod_res = _collect(bu.get("product_resilience", ""))
            if prod_res:
                story.append(Paragraph("Product Resilience", styles["RptLabel"]))
                story.append(Paragraph(prod_res, styles["RptBody"]))
                story.append(Spacer(1, 4))

            # 3-Year Segment Forecast
            seg_fcast = bu.get("segment_forecast", {})
            if isinstance(seg_fcast, dict) and any(seg_fcast.get(s) for s in ("bear", "base", "bull")):
                story.append(Paragraph("3-Year Segment Revenue and Margin Forecast", styles["RptLabel"]))
                seg_w = [page_w * 0.18, page_w * 0.10, page_w * 0.10, page_w * 0.10, page_w * 0.12, page_w * 0.40]
                seg_rows = [[
                    Paragraph(_wh("Scenario"),   styles["RptLabel"]),
                    Paragraph(_wh("Yr1 Rev%"),   styles["RptLabel"]),
                    Paragraph(_wh("Yr2 Rev%"),   styles["RptLabel"]),
                    Paragraph(_wh("Yr3 Rev%"),   styles["RptLabel"]),
                    Paragraph(_wh("EBITDA Yr3"), styles["RptLabel"]),
                    Paragraph(_wh("Assumption"), styles["RptLabel"]),
                ]]
                for scen_name in ("bear", "base", "bull"):
                    s = seg_fcast.get(scen_name, {})
                    if not s:
                        continue
                    def _pct(v): return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"
                    seg_rows.append([
                        Paragraph(scen_name.capitalize(), styles["RptBody"]),
                        Paragraph(_pct(s.get("yr1_rev_growth")), styles["RptValue"]),
                        Paragraph(_pct(s.get("yr2_rev_growth")), styles["RptValue"]),
                        Paragraph(_pct(s.get("yr3_rev_growth")), styles["RptValue"]),
                        Paragraph(_pct(s.get("ebitda_margin_yr3")), styles["RptValue"]),
                        Paragraph(_collect(s.get("assumption", "")), styles["RptBody"]),
                    ])
                seg_tbl = Table(seg_rows, colWidths=seg_w, hAlign="LEFT", repeatRows=1)
                seg_tbl.setStyle(TableStyle([
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
                story.append(seg_tbl)
                story.append(Spacer(1, 4))

            # Data limitations
            data_lim = _collect(bu.get("data_limitations", ""))
            if data_lim and data_lim not in ("N/A", "LLM call failed."):
                story.append(Paragraph(
                    f"<i>Data limitations: {data_lim}</i>", styles["RptSource"]
                ))
            story.append(Spacer(1, 6))

        # ── 9. Senior Financial Editor Review (Phase 7.5) ─────────────────────
        editor_all = result.get("editor_review", {})
        editor = editor_all.get(ticker, {})
        if editor:
            story.append(Paragraph("Editorial Review", styles["RptSubsection"]))

            # Polished summary
            polished = _collect(editor.get("polished_summary", ""))
            if polished and polished != "Editor review unavailable.":
                story.append(Paragraph("<b>Executive Summary (Polished)</b>", styles["RptLabel"]))
                story.append(Paragraph(polished, styles["RptBody"]))
                story.append(Spacer(1, 4))

            # Quality score + logic flags
            quality = editor.get("report_quality_score", "—")
            logic_flags = editor.get("logic_audit_flags", [])
            ed_rows = [["Report Quality Score", f"{quality} / 10"]]
            for flag in logic_flags:
                ed_rows.append(["Logic Flag", _collect(flag)])
            if not logic_flags:
                ed_rows.append(["Logic Flags", "None — no contradictions detected"])

            # Formatting notes
            fmt_notes = editor.get("formatting_notes", [])
            for note in fmt_notes:
                ed_rows.append(["Formatting Note", _collect(note)])

            # Key corrections
            corrections = editor.get("key_corrections", [])
            for c in corrections:
                ed_rows.append(["Key Correction", _collect(c)])

            story.append(_kv_table(ed_rows, col1, col2, styles))
            story.append(Spacer(1, 6))

        # ── 10. Citation & Hallucination Audit (Phase 7.5) ────────────────────
        ca_all = result.get("citation_audit", {})
        ca = ca_all.get(ticker, {})
        if ca:
            story.append(Paragraph("Citation & Hallucination Audit", styles["RptSubsection"]))

            audit_score  = ca.get("audit_score", "—")
            halluc_flags = ca.get("hallucination_flags", [])
            src_gaps     = ca.get("primary_source_gaps", [])

            ca_summary_rows = [["Citation Audit Score", f"{audit_score} / 10"]]
            for hf in halluc_flags:
                ca_summary_rows.append(["Hallucination Flag", _collect(hf)])
            if not halluc_flags:
                ca_summary_rows.append(["Hallucination Flags", "None detected"])
            for sg in src_gaps:
                ca_summary_rows.append(["Source Gap", _collect(sg)])
            if not src_gaps:
                ca_summary_rows.append(["Source Gaps", "None — all claims sourced"])
            story.append(_kv_table(ca_summary_rows, col1, col2, styles))
            story.append(Spacer(1, 6))

        # ── §12 Final Summary Table (P1.3) ───────────────────────────────────
        story.extend(_final_summary_table(
            dcf_data   = dcf_ticker,
            scenario   = scen,
            decision   = decision,
            styles     = styles,
            page_w     = page_w,
        ))

    # ── Post-Trade Review (if available) ─────────────────────────────────────
    ptr = result.get("post_trade_review")
    if ptr:
        story.extend(_section_header("SECTION 3 — POST-TRADE REVIEW (Phase 10)", page_w))
        ptr_rows = [["Calls Reviewed", str(ptr.get("reviewed", 0))]]
        for upd in ptr.get("weight_updates", []):
            ptr_rows.append(["Weight Update", _collect(upd)])
        story.append(_kv_table(ptr_rows, col1, col2, styles))
        story.append(Spacer(1, 8))

    # ── Appendix — Macro Regime ───────────────────────────────────────────────
    story.extend(_section_header("APPENDIX — MACRO REGIME", page_w))
    macro_rows = [
        ["Risk Appetite",  macro.get("risk_appetite", "—")],
        ["Rate Direction", macro.get("rate_direction", "—")],
        ["Dollar Trend",   macro.get("dollar_trend",   "—")],
        ["Vol Regime",     macro.get("volatility_regime", "—")],
    ]
    notes = _strip(macro.get("regime_notes", ""))
    if notes:
        macro_rows.append(["Notes", _collect(notes)])
    story.append(_kv_table(macro_rows, col1, col2, styles))
    story.append(Spacer(1, 6))

    # ── Build PDF with running headers + page numbers (item E) ───────────────
    # Header text is captured in closure; _NumberedCanvas draws on every page.
    _hdr_left  = f"{', '.join(tickers)} — {_strip(sector)}"
    _hdr_right = f"AI Hedge Fund Research  |  {run_date}"
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
                "Sources: Financial Datasets API · Anthropic Web Search · Internal Sector KPI Framework",
            )

            self.restoreState()

    doc.build(story, canvasmaker=_NumberedCanvas)

    # ── Truncation validation ─────────────────────────────────────────────────
    _validate_no_truncation(_all_text, output_path)

    # ── Auto-open ─────────────────────────────────────────────────────────────
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

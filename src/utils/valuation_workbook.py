"""Institutional valuation workbook: one .xlsx per run, rebuilt with live formulas.

Every number the engine published is rebuilt from the inputs it recorded
(`leg_inputs`, `financials_used`, `wacc_build`, `pt_bridge`,
`calibration_record`, `multiples_used`) with Excel formulas, beside the
engine's own figure and a Check that should read zero.

Formatting standard (owner-specified, 2026-09-19):
  * Blue  #0000FF  hardcoded inputs: historical actuals, assumptions, drivers.
  * Black #000000  formulas and calculations within a tab.
  * Green #008000  formulas that pull from another tab.
  Colour is applied from the cell's CONTENT (constant / same-tab formula /
  cross-tab formula), so a cell cannot be mis-coloured by hand.
  * Every division is wrapped in IFERROR; every engine constant (margin cap,
    terminal spread guard, P/B bounds, sensitivity steps) lives on the
    Assumptions tab and formulas reference it -- no hardcoded numbers inside
    a formula beyond the structural 1 in (1+g).
  * Period headers are DATE() formulas formatted as fiscal years.
  * No circular references: the engine's valuation has none to model.

Scope, deliberately: the engine values a company from revenue growth x FCF
margin (DCF) and peer multiples; it does not forecast a three-statement
model. The IS/BS/CFS tabs therefore show the reported history in full, and
only the lines the engine actually projects (revenue, FCF) carry forecasts,
linked from the DCF. Balancing-plug forecasts would put numbers in the
workbook that the valuation never used.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any, Callable, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SCENARIOS = ("bear", "base", "bull")

BLUE, BLACK, GREEN = "FF0000FF", "FF000000", "FF008000"   # ARGB, opaque
_HDR_FILL = PatternFill("solid", fgColor="D9E1F2")
_SEC_FILL = PatternFill("solid", fgColor="F2F2F2")
_NOTE = Font(italic=True, color="595959")

NUM = "#,##0.00;(#,##0.00)"
MIL = "#,##0.0;(#,##0.0)"
BIG = "#,##0;(#,##0)"
PCT = "0.0%;(0.0%)"
PCT2 = "0.00%;(0.00%)"
MULT = "0.00\"x\""
FY = "\"FY\"yyyy"

try:  # pragma: no cover
    from src.agents.analysis.dcf_agent import _FCF_MARGIN_CAP as _ENGINE_MARGIN_CAP
except Exception:  # noqa: BLE001
    _ENGINE_MARGIN_CAP = 0.60

_TV_SPREAD_GUARD = 0.005          # _project_dcf: terminal WACC >= g + 0.5%
_PB_FLOOR, _PB_CAP = 0.3, 4.0     # _compute_ggm_pb bounds


#: Statement lines checked for gaps: (field, label, severity when missing).
_GAP_LINES = (
    ("revenue", "Revenue", "High"), ("cost_of_revenue", "COGS", "Low"),
    ("selling_general_admin", "SG&A", "Low"), ("research_and_development", "R&D", "Low"),
    ("depreciation_and_amortization", "D&A", "Medium"), ("interest_expense", "Interest expense", "Low"),
    ("income_tax_expense", "Income tax", "Low"), ("shares_outstanding", "Diluted shares", "High"),
    ("current_assets", "Current assets", "Low"), ("current_liabilities", "Current liabilities", "Low"),
    ("total_assets", "Total assets", "Medium"), ("total_liabilities", "Total liabilities", "Medium"),
    ("shareholders_equity", "Equity", "Medium"), ("operating_cash_flow", "Cash from operations", "High"),
    ("capital_expenditure", "Capex", "Medium"),
    ("change_in_working_capital", "Change in working capital", "Low"),
    ("stock_based_compensation", "Stock-based compensation", "Medium"),
)

#: Gaps between the engine and a full institutional three-statement model.
_STRUCTURAL_GAPS = [
    ("Model architecture", "IS forecast is consensus, not modelled line by line",
     "Forecast IS lines are FMP analyst consensus (revenue, EBITDA, EBIT, net income, EPS); "
     "COGS, opex, interest and tax are not forecast, and the DCF uses its own revenue path",
     "Structural"),
    ("Model architecture", "No balance-sheet or cash-flow forecast (no cash/revolver plug)",
     "Statements are historical only; net debt is held at the latest reported figure", "Structural"),
    ("Supporting schedules", "No working-capital, PP&E/depreciation or debt/interest schedules",
     "Reinvestment is implicit in the FCF margin; the growth-reinvestment charge is off", "Structural"),
    ("WACC", "Table-driven sector/profile WACC, not a CAPM build",
     "A CAPM cost of equity (FMP beta, Damodaran rf/ERP) is shown as a cross-check only",
     "Structural"),
    ("Circularity", "None: the valuation has no interest/cash circularity",
     "No iterative calculation needed", "Info"),
]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _mil(v: Any) -> Optional[float]:
    f = _num(v)
    return None if f is None else f / 1e6


def _parse_date(v: Any) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except (TypeError, ValueError):
        return None


class _Sheet:
    """A worksheet with content-driven colouring."""

    def __init__(self, ws):
        self.ws = ws

    def put(self, r: int, c: int, v: Any, fmt: Optional[str] = None, bold: bool = False,
            italic: bool = False):
        cell = self.ws.cell(row=r, column=c, value=v)
        if isinstance(v, str) and v.startswith("="):
            color = GREEN if "!" in v else BLACK
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            color = BLUE
        else:
            color = BLACK
        cell.font = Font(color=color, bold=bold, italic=italic)
        if fmt:
            cell.number_format = fmt
        return cell

    def label(self, r: int, c: int, text: str, bold: bool = False, indent: int = 0):
        cell = self.ws.cell(row=r, column=c, value=text)
        cell.font = Font(bold=bold)
        if indent:
            cell.alignment = Alignment(indent=indent)
        return cell

    def note(self, r: int, c: int, text: str):
        cell = self.ws.cell(row=r, column=c, value=text)
        cell.font = _NOTE
        return cell

    def title(self, text: str, sub: Optional[str] = None):
        self.ws.cell(row=1, column=1, value=text).font = Font(bold=True, size=14)
        if sub:
            self.note(2, 1, sub)

    def header(self, r: int, labels: list, c: int = 1, fmt: Optional[str] = None):
        for i, lab in enumerate(labels):
            cell = self.ws.cell(row=r, column=c + i, value=lab)
            cell.font = Font(bold=True, color=GREEN if isinstance(lab, str) and lab.startswith("=")
                             and "!" in lab else BLACK)
            cell.fill = _HDR_FILL
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            if fmt and isinstance(lab, str) and lab.startswith("="):
                cell.number_format = fmt

    def section(self, r: int, text: str, width: int = 8):
        for c in range(1, width + 1):
            self.ws.cell(row=r, column=c).fill = _SEC_FILL
        self.ws.cell(row=r, column=1, value=text).font = Font(bold=True)

    def widths(self, w: dict):
        for col, width in w.items():
            self.ws.column_dimensions[col].width = width


def _ref(sheet: str, r: int, c: int, absolute: bool = True) -> str:
    col = get_column_letter(c)
    return f"'{sheet}'!${col}${r}" if absolute else f"'{sheet}'!{col}{r}"


_DCF_DRIVER_KEYS = ("revenue_base", "fcf_margin_base", "margin_delta_absolute", "fcf_floor",
                    "tgr", "net_debt", "shares", "wacc")


def _close(a: Any, b: Any) -> bool:
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return a is None and b is None
    # 1e-8 absolute: traces recorded before full-precision recording kept 8 d.p.
    return abs(a - b) <= 1e-8 + 1e-9 * max(abs(a), abs(b))


def _same_projection(a: dict, b: dict) -> bool:
    """True when two DCF traces are the same projection (same drivers, same path)."""
    if not all(_close(a.get(k) or (0.0 if k == "margin_delta_absolute" else None),
                      b.get(k) or (0.0 if k == "margin_delta_absolute" else None))
               for k in _DCF_DRIVER_KEYS):
        return False
    ra, rb = a.get("projection_rows") or [], b.get("projection_rows") or []
    if len(ra) != len(rb):
        return False
    wa, wb = a.get("wacc_schedule") or [], b.get("wacc_schedule") or []
    if len(wa) != len(wb) or not all(_close(x, y) for x, y in zip(wa, wb)):
        return False
    # Owner, 2026-09-26 (audit B5): the per-year FCF margin is part of the path.
    # A Rev DCF (Target Margin) or backlog-fade leg shares the core growth path
    # and differs only here; without this row it was linked to the core DCF cell.
    return all(_close(x.get("growth_pct"), y.get("growth_pct"))
               and _close(x.get("reinvest_margin_deduction") or 0.0, y.get("reinvest_margin_deduction") or 0.0)
               and _close(x.get("fcf_margin"), y.get("fcf_margin"))
               for x, y in zip(ra, rb))


class _Book:
    def __init__(self, ticker: str, run: dict, dr: dict,
                 load_members: Optional[Callable[..., list]] = None,
                 load_statements: Optional[Callable[..., dict]] = None,
                 meta: Optional[dict] = None) -> None:
        self.ticker, self.run, self.dr = ticker, run, dr
        self.data = run.get("data") or {}
        self.ccy = dr.get("reported_currency") or "USD"
        self.load_members, self.load_statements = load_members, load_statements
        self.meta = meta or {}
        self.wb = Workbook()
        self.A: dict[str, str] = {}                    # assumption name -> cell ref
        self.leg_cell: dict[tuple, str] = {}           # (scenario, leg) -> per-share cell
        self.blend_unlinked: set[str] = set()          # blend legs with no rebuilding cell
        self.engine_only: set[str] = set()             # legs shown at engine value, no formula
        self.leg_range: dict[str, dict] = {}           # leg -> {scenario: ref}
        self.dcf: dict[str, dict] = {}                 # scenario -> refs
        self.iv_cell: dict[str, str] = {}
        self.target_cell: Optional[str] = None
        self.tabs: list[tuple[str, str]] = []
        self.statements: Optional[dict] = None

    def scen(self, s: str) -> dict:
        return self.dr.get(s) or {}

    def in_blend(self, leg: str) -> bool:
        """True when the leg carries weight in any scenario (owner, 2026-09-27: the workbook rebuilds
        the valuation that was computed; a leg computed only for display is not rebuilt or listed).
        A run with no weight record at all keeps every leg, as before."""
        blend = getattr(self, "_blend", None)
        if blend is None:
            blend = set()
            seen = False
            for sc in SCENARIOS:
                d = self.scen(sc)
                eff = d.get("effective_weights") or []
                if eff:
                    seen = True
                    blend |= {(e.get("value_key") or e.get("method")) for e in eff if (e.get("weight") or 0) > 0}
                elif d.get("methods_used"):
                    seen = True
                    blend |= set(d.get("methods_used") or [])
            self._blend = blend if seen else None
            blend = self._blend
        return True if blend is None else leg in blend

    def sheet(self, name: str, desc: str) -> _Sheet:
        self.tabs.append((name, desc))
        return _Sheet(self.wb.create_sheet(name))

    # ── build ───────────────────────────────────────────────────────────────
    def build(self) -> bytes:
        cover = _Sheet(self.wb.active)
        cover.ws.title = "Cover"
        summary = self.sheet("Summary", "Valuation output: football field, DCF summary, comps, target")
        if self.load_statements:
            try:
                self.statements = self.load_statements(self.ticker, self.data.get("end_date"))
            except Exception:  # noqa: BLE001
                self.statements = None
        self.assumptions()
        self.income_statement()
        self.balance_sheet()
        self.cash_flow()
        self.wacc_tab()
        self.dcf_tab()
        self.multiples_tab()
        self.comps_tab()
        # Owner, 2026-10-03 (SBUX review, A10): a SOTP tab only when a SOTP leg carries weight;
        # an unweighted SOTP (segments) trace used to create the tab and then empty it.
        if any(tr.get("kind") == "sotp" and self.in_blend(name) for sc in SCENARIOS
               for name, tr in (self.scen(sc).get("leg_inputs") or {}).items()):
            self.sotp_tab()
        if self._has_bank():
            self.banks_tab()
        if (self.dr or {}).get("guidance_estimates"):
            self.guidance_tab()                 # owner, 2026-10-03: guidance → estimates, as the DCF used them
        self.family_tab()
        self.blend_tab()
        self.target_tab()
        self.backtest_tab()
        self.gaps_tab()
        self.summary_tab(summary)
        self.cover_tab(cover)
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()

    def _has_bank(self) -> bool:
        return any(tr.get("kind") == "ggm" for s in SCENARIOS
                   for tr in (self.scen(s).get("leg_inputs") or {}).values())

    # ── Cover ───────────────────────────────────────────────────────────────
    def cover_tab(self, sh: _Sheet) -> None:
        sh.title(f"{self.ticker} — Valuation Model")
        run_at = _parse_date(self.run.get("run_at") or self.data.get("end_date")) or date.today()
        rows = [("Ticker", self.ticker), ("Company", self.data.get("company_name") or ""),
                ("Run ID", self.run.get("run_id") or self.meta.get("run_id") or ""),
                ("Valuation date", f"=DATE({run_at.year},{run_at.month},{run_at.day})"),
                ("Model version", self.dr.get("param_version") or self.dr.get("_engine_cache_version") or ""),
                ("Prepared by", "AI Hedge Fund valuation engine (automated)"),
                ("Currency", f"{self.ccy}; statements in millions unless stated")]
        for i, (k, v) in enumerate(rows):
            sh.label(4 + i, 1, k, bold=True)
            c = sh.put(4 + i, 2, v, "yyyy-mm-dd" if k == "Valuation date" else None)
            if not (isinstance(v, str) and v.startswith("=")):
                c.font = Font(color=BLACK)
        r = 4 + len(rows) + 1
        sh.section(r, "Colour code", 4)
        sh.ws.cell(row=r + 1, column=1, value="Blue: hardcoded input / historical actual").font = Font(color=BLUE)
        sh.ws.cell(row=r + 2, column=1, value="Black: formula within this tab").font = Font(color=BLACK)
        sh.ws.cell(row=r + 3, column=1, value="Green: formula linking another tab").font = Font(color=GREEN)
        r += 5
        sh.section(r, "Contents", 4)
        for i, (name, desc) in enumerate(self.tabs):
            c = sh.ws.cell(row=r + 1 + i, column=1, value=name)
            c.hyperlink = f"#'{name}'!A1"
            c.font = Font(color=BLUE, underline="single")
            sh.ws.cell(row=r + 1 + i, column=2, value=desc)
        sh.note(r + 2 + len(self.tabs), 1,
                "Each supporting tab rebuilds the engine's figures from recorded inputs; every "
                "'Check' should read 0. The engine does not forecast a three-statement model: "
                "statement tabs show reported history, with forecasts only for the lines the "
                "valuation projects (revenue, FCF).")
        sh.widths({"A": 30, "B": 80})

    # ── Assumptions ─────────────────────────────────────────────────────────
    def assumptions(self) -> None:
        sh = self.sheet("Assumptions", "Drivers and constants: FX, discount rate inputs, growth, margins, "
                                       "scenario weights, engine constants")
        sh.title("Assumptions & Drivers", "Every hardcoded driver lives here; all tabs reference these cells.")
        r = 4

        def add(name: str, label: str, value: Any, fmt: Optional[str] = None) -> None:
            nonlocal r
            sh.label(r, 1, label, indent=1)
            sh.put(r, 3, value, fmt)
            self.A[name] = _ref("Assumptions", r, 3)
            r += 1

        pb = self.dr.get("pt_bridge") or {}
        fu = self.dr.get("financials_used") or {}
        sh.section(r, "General", 6); r += 1
        add("spot", f"Share price at valuation ({self.ccy})", _num(pb.get("spot")) or self._spot(), NUM)
        add("fx", f"FX: statement currency ({fu.get('source_currency') or self.ccy}) → {self.ccy}",
            _num(fu.get("fx_rate")) or 1.0, "0.0000")
        add("unit", "Statement unit divisor (millions)", 1_000_000, BIG)
        r += 1

        sh.section(r, "Terminal value and margins", 6); r += 1
        add("margin_cap", "FCF margin cap (engine)", _ENGINE_MARGIN_CAP, PCT)
        add("tv_guard", "Terminal spread guard: WACC ≥ g + (engine)", _TV_SPREAD_GUARD, PCT2)
        r += 1

        sh.section(r, "Scenario drivers", 8); r += 1
        sh.header(r, ["Driver", "", "Bear", "Base", "Bull"]); r += 1
        drivers = [("revenue_base", "Revenue base (last actual, valuation currency)", BIG),
                   ("fcf_margin_base", "FCF margin base (unlevered owner earnings = owner-earnings FCF + after-tax interest; levered owner earnings for banks, insurers, fee financials and property)", PCT2),
                   ("margin_delta_absolute", "Scenario margin change (absolute)", PCT2),
                   ("fcf_floor", "FCF margin floor", PCT2),
                   ("tgr", "Terminal growth", PCT2),
                   ("net_debt", "Net debt (valuation currency)", BIG),
                   ("shares", "Shares outstanding", BIG)]
        for key, lab, fmt in drivers:
            sh.label(r, 1, lab, indent=1)
            for j, s in enumerate(SCENARIOS):
                tr = (self.scen(s).get("leg_inputs") or {}).get("DCF") or {}
                v = tr.get(key)
                if key == "margin_delta_absolute" and v is None and tr:
                    v = 0.0
                sh.put(r, 3 + j, _num(v), fmt)
                self.A[f"{key}:{s}"] = _ref("Assumptions", r, 3 + j)
            r += 1
        sh.label(r, 1, "Revenue growth by projection year", bold=True); r += 1
        sh.header(r, ["Year", "", "Bear", "Base", "Bull"]); r += 1
        for t in range(10):
            sh.label(r, 1, f"Year {t + 1}", indent=1)
            for j, s in enumerate(SCENARIOS):
                rows = ((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("projection_rows") or []
                sh.put(r, 3 + j, _num(rows[t].get("growth_pct")) if t < len(rows) else None, PCT2)
                self.A[f"g{t + 1}:{s}"] = _ref("Assumptions", r, 3 + j)
            r += 1
        sh.label(r, 1, "Discount rate by projection year (staged where the profile stages it)", bold=True); r += 1
        sh.header(r, ["Year", "", "Bear", "Base", "Bull"]); r += 1
        for t in range(10):
            sh.label(r, 1, f"Year {t + 1}", indent=1)
            for j, s in enumerate(SCENARIOS):
                tr = (self.scen(s).get("leg_inputs") or {}).get("DCF") or {}
                staged = tr.get("wacc_schedule")
                if staged and t < len(staged):
                    sh.put(r, 3 + j, _num(staged[t]), PCT2)
                else:
                    sh.note(r, 3 + j, "flat: WACC tab")
                self.A[f"w{t + 1}:{s}"] = _ref("Assumptions", r, 3 + j)
            r += 1
        r += 1
        # DCF-family legs (DCF (5-yr), DCF (LTG), DCF (FCF+), Rev DCF, ...) that
        # project differently from the core DCF get their own drivers; the ones
        # that are the same projection link to the core block on the DCF tab.
        self.dcf_variants: dict[str, dict[str, dict]] = {}
        for s_ in SCENARIOS:
            legs = self.scen(s_).get("leg_inputs") or {}
            core = legs.get("DCF") or {}
            for leg, tr in legs.items():
                if leg == "DCF" or tr.get("kind") != "dcf" or not tr.get("projection_rows"):
                    continue
                if core.get("projection_rows") and _same_projection(tr, core):
                    self.dcf_variants.setdefault(leg, {})[s_] = {"same_as_core": True}
                else:
                    self.dcf_variants.setdefault(leg, {})[s_] = {"trace": tr}
        for leg, per in self.dcf_variants.items():
            own = {s_: v["trace"] for s_, v in per.items() if "trace" in v}
            if not own:
                continue
            sfx = f"|{leg}"
            sh.section(r, f"DCF-family leg: {leg} (projects differently from the core DCF)", 8); r += 1
            sh.header(r, ["Driver", "", "Bear", "Base", "Bull"]); r += 1
            for key, lab, fmt in drivers + [("wacc", "Discount rate (flat, where not staged)", PCT2)]:
                sh.label(r, 1, lab, indent=1)
                for j, s_ in enumerate(SCENARIOS):
                    tr = own.get(s_)
                    if tr is None:
                        continue
                    v = tr.get(key)
                    if key == "margin_delta_absolute" and v is None:
                        v = 0.0
                    sh.put(r, 3 + j, _num(v), fmt)
                    self.A[f"{key}:{s_}{sfx}"] = _ref("Assumptions", r, 3 + j)
                r += 1
            n_max = max(len(tr.get("projection_rows") or []) for tr in own.values())
            for t in range(n_max):
                sh.label(r, 1, f"Revenue growth, year {t + 1}", indent=1)
                for j, s_ in enumerate(SCENARIOS):
                    rows = (own.get(s_) or {}).get("projection_rows") or []
                    if t < len(rows):
                        sh.put(r, 3 + j, _num(rows[t].get("growth_pct")), PCT2)
                        self.A[f"g{t + 1}:{s_}{sfx}"] = _ref("Assumptions", r, 3 + j)
                r += 1
            if any(tr.get("wacc_schedule") for tr in own.values()):
                for t in range(n_max):
                    sh.label(r, 1, f"Discount rate, year {t + 1}", indent=1)
                    for j, s_ in enumerate(SCENARIOS):
                        st = (own.get(s_) or {}).get("wacc_schedule") or []
                        if t < len(st):
                            sh.put(r, 3 + j, _num(st[t]), PCT2)
                            self.A[f"w{t + 1}:{s_}{sfx}"] = _ref("Assumptions", r, 3 + j)
                    r += 1
            r += 1
        sh.section(r, "Target and blend", 6); r += 1
        add("capture", "Share of the IV gap closed in 12 months (capture)", _num(pb.get("capture")), PCT)
        sa = (self.data.get("scenario_analysis") or {}).get(self.ticker) or {}
        for s in SCENARIOS:
            add(f"prob:{s}", f"Scenario probability — {s}", _num((sa.get(s) or {}).get("probability")), PCT)
        add("dp", "Published rounding (decimal places) for IV and targets", 2, "0")
        add("dp_ggm", "GGM value rounding (decimal places, engine)", 4, "0")
        add("tol", "Reconciliation tolerance (one rounding unit)", 0.01, NUM)
        add("dp_comps", "Recorded peer-median precision (decimal places, engine)", 4, "0")
        add("calibration", "Calibration multiplier (market bias correction)",
            _num((self.dr.get("calibration") or {}).get("iv_multiplier")) or 1.0, "0.0000")
        r += 1
        sh.section(r, "Discount-rate constants", 6); r += 1
        bb = (self.dr.get("wacc_build") or {}).get("base_breakdown") or {}
        add("lev_threshold", "Leverage premium threshold (net debt / equity)",
            _num(bb.get("leverage_threshold")) or 1.5, "0.00\"x\"")
        add("lev_slope", "Leverage premium per 1.0x above threshold",
            _num(bb.get("leverage_slope")) or 0.01, PCT2)
        add("rf", "Risk-free rate (Damodaran Jan 2026; CAPM cross-check)", 0.0395, PCT2)
        add("erp", "Equity risk premium (Damodaran Jan 2026; CAPM cross-check)", 0.0446, PCT2)
        add("beta", "Beta (FMP profile; CAPM cross-check)", _num((self.statements or {}).get("beta")), "0.00")
        r += 1
        sh.section(r, "Sensitivity and bank-model constants", 6); r += 1
        add("sens_w", "Sensitivity step: WACC", 0.01, PCT2)
        add("sens_g", "Sensitivity step: terminal growth", 0.005, PCT2)
        add("pb_floor", "Justified P/B floor (engine)", _PB_FLOOR, MULT)
        add("pb_cap", "Justified P/B cap (engine)", _PB_CAP, MULT)
        sh.widths({"A": 58, "B": 2, "C": 16, "D": 16, "E": 16})

    def _spot(self) -> Optional[float]:
        rec = ((self.data.get("scenario_analysis") or {}).get(self.ticker) or {}).get("reconciliation") or {}
        return _num(rec.get("current_price"))

    # ── Statements ──────────────────────────────────────────────────────────
    def _stmt_rows(self) -> list[dict]:
        st = self.statements or {}
        return st.get("rows") or []

    def _period_headers(self, sh: _Sheet, r: int, rows: list[dict], n_fc: int, col0: int = 3) -> int:
        """DATE() headers for actuals, EDATE() for forecasts. Returns last actual column."""
        sh.label(r, 1, "Fiscal year end", bold=True)
        last = col0 - 1
        for i, row in enumerate(rows):
            d = _parse_date(row.get("period"))
            c = col0 + i
            if d:
                sh.put(r, c, f"=DATE({d.year},{d.month},{d.day})", FY, bold=True)
            last = c
        for k in range(n_fc):
            c = last + 1 + k
            prev = get_column_letter(c - 1)
            sh.put(r, c, f"=EDATE({prev}{r},12)", FY, bold=True)
        sh.label(r + 1, 1, "")
        for i in range(len(rows)):
            sh.ws.cell(row=r + 1, column=col0 + i, value="A").alignment = Alignment(horizontal="center")
        for k in range(n_fc):
            sh.ws.cell(row=r + 1, column=last + 1 + k, value="E").alignment = Alignment(horizontal="center")
        return last

    def income_statement(self) -> None:
        sh = self.sheet("IS", "Income statement: reported history; revenue forecast linked from the DCF")
        st = self.statements or {}
        rows = self._stmt_rows()
        cur = st.get("currency") or (self.dr.get("financials_used") or {}).get("source_currency") or ""
        sh.title("Income Statement", f"In {cur} millions, as reported (FMP). Forecast columns carry only "
                                     "what the valuation projects.")
        if not rows:
            sh.note(4, 1, "Reported statements unavailable for this export.")
            return
        n_fc = 5
        last = self._period_headers(sh, 4, rows, n_fc)
        c0 = 3
        r = 6
        U = self.A["unit"]

        def inrow(key: str, label: str, sign: float = 1.0, indent: int = 0) -> int:
            nonlocal r
            sh.label(r, 1, label, indent=indent)
            sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                v = _num(row.get(key))
                sh.put(r, c0 + i, None if v is None else sign * v / 1e6, MIL)
            r += 1
            return r - 1

        def frow(label: str, formula: Callable[[str], str], fmt: str = MIL, bold=False,
                 typ: str = "Formula", cols: Optional[range] = None) -> int:
            nonlocal r
            sh.label(r, 1, label, bold=bold)
            sh.label(r, 2, typ)
            for c in (cols or range(c0, last + 1)):
                sh.put(r, c, formula(get_column_letter(c)), fmt, bold=bold)
            r += 1
            return r - 1

        rev = inrow("revenue", "Revenue")
        g = frow("YoY growth (%)", lambda L: f"=IFERROR({L}{rev}/{get_column_letter(max(c0, _col(L) - 1))}{rev}-1,0)"
                 if _col(L) > c0 else '=""', PCT)
        cogs = inrow("cost_of_revenue", "Cost of goods sold", -1.0)
        gp = frow("Gross profit", lambda L: f"={L}{rev}+{L}{cogs}", bold=True)
        frow("Gross margin (%)", lambda L: f"=IFERROR({L}{gp}/{L}{rev},0)", PCT)
        sga = inrow("selling_general_admin", "SG&A", -1.0, 1)
        rnd = inrow("research_and_development", "Research & development", -1.0, 1)
        opx = inrow("operating_expense", "Total operating expenses (reported)", -1.0)
        ebit = frow("EBIT (operating income)", lambda L: f"={L}{gp}+{L}{opx}", bold=True)
        frow("EBIT margin (%)", lambda L: f"=IFERROR({L}{ebit}/{L}{rev},0)", PCT)
        da = inrow("depreciation_and_amortization", "Depreciation & amortisation")
        ebitda = frow("EBITDA", lambda L: f"={L}{ebit}+{L}{da}", bold=True)
        frow("EBITDA margin (%)", lambda L: f"=IFERROR({L}{ebitda}/{L}{rev},0)", PCT)
        ie = inrow("interest_expense", "Interest expense", -1.0)
        oth = inrow("other_income_expense", "Other income / (expense), incl. interest income")
        ebt = frow("EBT (pre-tax income)", lambda L: f"={L}{ebit}+{L}{oth}", bold=True)
        rep_ebt = inrow("pretax_income", "Pre-tax income (reported)", 1.0, 1)
        tie = frow("Tie-out: EBT vs reported (data consistency)", lambda L: f"={L}{ebt}-{L}{rep_ebt}",
                   MIL, typ="Tie-out")
        self._is_tieout = (tie, [row.get("pretax_income") for row in rows],
                           [(_num(row.get("operating_expense")), _num(row.get("gross_profit")),
                             _num(row.get("other_income_expense"))) for row in rows])
        tax = inrow("income_tax_expense", "Income tax", -1.0)
        frow("Effective tax rate (%)", lambda L: f"=IFERROR(-{L}{tax}/{L}{ebt},0)", PCT)
        ni_c = frow("Net income (EBT − tax)", lambda L: f"={L}{ebt}+{L}{tax}", bold=True)
        ni = inrow("net_income", "Net income attributable (reported)", 1.0, 1)
        frow("Minorities / discontinued (reported − computed)", lambda L: f"={L}{ni}-{L}{ni_c}", MIL, typ="Formula")
        sh_r = inrow("shares_outstanding", "Diluted shares (millions)")
        frow("Diluted EPS", lambda L: f"=IFERROR({L}{ni}/{L}{sh_r},0)", NUM, bold=True)
        self.is_rows = {"rev": rev, "ni": ni, "da": da, "last": last}
        # ── Forecast: FMP consensus (the estimates the forward legs use) and
        # the DCF's own revenue path, side by side. ──
        r += 1
        sh.section(r, "Forecast — FMP analyst consensus (average)", last + n_fc); r += 1
        est = {str(e.get("period_end") or "")[:4]: e for e in (st.get("estimates") or [])}
        fc_years = []
        for k in range(n_fc):
            last_d = _parse_date(rows[-1].get("period"))
            fc_years.append(str(last_d.year + 1 + k) if last_d else "")

        def crow(key, label, sign=1.0, per_share=False):
            nonlocal r
            sh.label(r, 1, label); sh.label(r, 2, "Consensus")
            for k, y in enumerate(fc_years):
                v = _num((est.get(y) or {}).get(key))
                sh.put(r, last + 1 + k, None if v is None else (sign * v if per_share else sign * v / 1e6),
                       NUM if per_share else MIL)
            r += 1
            return r - 1

        c_rev = crow("revenue_avg", "Revenue")
        sh.label(r, 1, "YoY growth (%)"); sh.label(r, 2, "Formula")
        for k in range(n_fc):
            L, P = get_column_letter(last + 1 + k), get_column_letter(last + k)
            prev = f"{P}{rev}" if k == 0 else f"{P}{c_rev}"
            sh.put(r, last + 1 + k, f'=IFERROR({L}{c_rev}/{prev}-1,"")', PCT)
        r += 1
        c_ebitda = crow("ebitda_avg", "EBITDA")
        c_ebit = crow("ebit_avg", "EBIT")
        c_ni = crow("net_income_avg", "Net income")
        crow("eps_avg", "Diluted EPS", per_share=True)
        for lab, num in (("EBITDA margin (%)", c_ebitda), ("EBIT margin (%)", c_ebit),
                         ("Net margin (%)", c_ni)):
            sh.label(r, 1, lab); sh.label(r, 2, "Formula")
            for k in range(n_fc):
                L = get_column_letter(last + 1 + k)
                sh.put(r, last + 1 + k, f'=IFERROR({L}{num}/{L}{c_rev},"")', PCT)
            r += 1
        r += 1
        sh.section(r, "Forecast — engine DCF base case (what the valuation projects)", last + n_fc); r += 1
        dcf_rev = r
        sh.label(r, 1, "Revenue (DCF base, statement currency)"); sh.label(r, 2, "Link"); r += 1
        sh.label(r, 1, "Variance vs consensus revenue (%)"); sh.label(r, 2, "Formula")
        for k in range(n_fc):
            L = get_column_letter(last + 1 + k)
            sh.put(r, last + 1 + k, f'=IFERROR({L}{dcf_rev}/{L}{c_rev}-1,"")', PCT)
        r += 1
        self._is_sheet, self._is_rev_row, self._is_g_row, self._is_last = sh, dcf_rev, None, last
        sh.widths({"A": 46, "B": 10, **{get_column_letter(c): 13 for c in range(c0, last + 1 + n_fc)}})
        sh.ws.freeze_panes = "C6"

    def balance_sheet(self) -> None:
        sh = self.sheet("BS", "Balance sheet: reported history with a balance check")
        rows = self._stmt_rows()
        cur = (self.statements or {}).get("currency") or ""
        sh.title("Balance Sheet", f"In {cur} millions, as reported. 'Other' lines are the reported total less "
                                  "the named lines, so the sheet balances only if the filing does.")
        if not rows:
            sh.note(4, 1, "Reported statements unavailable for this export.")
            return
        last = self._period_headers(sh, 4, rows, 0)
        c0, r = 3, 6

        def inrow(key, label, indent=1):
            nonlocal r
            sh.label(r, 1, label, indent=indent); sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                sh.put(r, c0 + i, _mil(row.get(key)), MIL)
            r += 1
            return r - 1

        def frow(label, f, bold=False, typ="Formula"):
            nonlocal r
            sh.label(r, 1, label, bold=bold); sh.label(r, 2, typ)
            for c in range(c0, last + 1):
                sh.put(r, c, f(get_column_letter(c)), MIL, bold=bold)
            r += 1
            return r - 1

        sh.section(r, "Assets", last); r += 1
        cash = inrow("cash_and_equivalents", "Cash & equivalents")
        sti = inrow("short_term_investments", "Short-term investments")
        ar = inrow("accounts_receivable", "Accounts receivable")
        inv = inrow("inventory", "Inventory")
        tca = inrow("current_assets", "Total current assets (reported)", 0)
        oca = frow("  Other current assets", lambda L: f"={L}{tca}-SUM({L}{cash}:{L}{inv})")
        ppe = inrow("property_plant_equipment", "Property, plant & equipment")
        gw = inrow("goodwill_plus_intangibles", "Goodwill & intangibles")
        ta_rep = inrow("total_assets", "Total assets (reported)", 0)
        onca = frow("  Other non-current assets", lambda L: f"={L}{ta_rep}-{L}{tca}-{L}{ppe}-{L}{gw}")
        ta = frow("Total assets", lambda L: f"={L}{tca}+{L}{ppe}+{L}{gw}+{L}{onca}", bold=True)
        r += 1
        sh.section(r, "Liabilities & equity", last); r += 1
        ap = inrow("accounts_payable", "Accounts payable")
        std = inrow("short_term_debt", "Short-term debt")
        tcl = inrow("current_liabilities", "Total current liabilities (reported)", 0)
        ocl = frow("  Other current liabilities", lambda L: f"={L}{tcl}-{L}{ap}-{L}{std}")
        ltd = inrow("long_term_debt", "Long-term debt")
        tl_rep = inrow("total_liabilities", "Total liabilities (reported)", 0)
        oncl = frow("  Other non-current liabilities", lambda L: f"={L}{tl_rep}-{L}{tcl}-{L}{ltd}")
        tl = frow("Total liabilities", lambda L: f"={L}{tcl}+{L}{ltd}+{L}{oncl}", bold=True)
        eq = inrow("shareholders_equity", "Shareholders' equity")
        mi = inrow("minority_interest", "Minority interest")
        tle = frow("Total liabilities & equity", lambda L: f"={L}{tl}+{L}{eq}+{L}{mi}", bold=True)
        frow("Balance check (assets − liabilities & equity)", lambda L: f"={L}{ta}-{L}{tle}", typ="Check")
        r += 1
        td = frow("Total debt", lambda L: f"={L}{std}+{L}{ltd}")
        frow("Net debt — this tab's formula (debt − cash − short-term investments; leases excluded)", lambda L: f"={L}{td}-{L}{cash}-{L}{sti}", bold=True)
        sh.widths({"A": 50, "B": 10, **{get_column_letter(c): 13 for c in range(c0, last + 1)}})
        sh.ws.freeze_panes = "C6"

    def cash_flow(self) -> None:
        sh = self.sheet("CFS", "Cash flow statement: reported history; FCF bridge to the DCF basis")
        rows = self._stmt_rows()
        cur = (self.statements or {}).get("currency") or ""
        sh.title("Cash Flow Statement", f"In {cur} millions, as reported. Net income and D&A link to the IS.")
        if not rows:
            sh.note(4, 1, "Reported statements unavailable for this export.")
            return
        last = self._period_headers(sh, 4, rows, 0)
        c0, r = 3, 6
        isr = getattr(self, "is_rows", None)

        def inrow(key, label, sign=1.0, indent=1):
            nonlocal r
            sh.label(r, 1, label, indent=indent); sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                v = _num(row.get(key))
                sh.put(r, c0 + i, None if v is None else sign * v / 1e6, MIL)
            r += 1
            return r - 1

        def frow(label, f, bold=False, typ="Formula", fmt=MIL):
            nonlocal r
            sh.label(r, 1, label, bold=bold); sh.label(r, 2, typ)
            for c in range(c0, last + 1):
                sh.put(r, c, f(get_column_letter(c)), fmt, bold=bold)
            r += 1
            return r - 1

        sh.section(r, "Operating", last); r += 1
        if isr:
            ni = frow("Net income", lambda L: f"='IS'!{L}{isr['ni']}", typ="Link")
            da = frow("Depreciation & amortisation", lambda L: f"='IS'!{L}{isr['da']}", typ="Link")
        else:
            ni = inrow("net_income", "Net income"); da = inrow("depreciation_and_amortization", "D&A")
        sbc = inrow("stock_based_compensation", "Stock-based compensation")
        wc = inrow("change_in_working_capital", "Change in working capital")
        cfo_rep = inrow("operating_cash_flow", "Cash from operations (reported)", 1.0, 0)
        oth = frow("  Other operating items", lambda L: f"={L}{cfo_rep}-{L}{ni}-{L}{da}-{L}{sbc}-{L}{wc}")
        cfo = frow("Cash from operations", lambda L: f"={L}{ni}+{L}{da}+{L}{sbc}+{L}{wc}+{L}{oth}", bold=True)
        r += 1
        sh.section(r, "Investing", last); r += 1
        capex = inrow("capital_expenditure", "Capital expenditure")
        acq = inrow("acquisitions_net", "Acquisitions (net)")
        cfi_rep = inrow("investing_cash_flow", "Cash from investing (reported)", 1.0, 0)
        oi = frow("  Other investing items", lambda L: f"={L}{cfi_rep}-{L}{capex}-{L}{acq}")
        cfi = frow("Cash from investing", lambda L: f"={L}{capex}+{L}{acq}+{L}{oi}", bold=True)
        r += 1
        sh.section(r, "Financing", last); r += 1
        div = inrow("dividends_and_distributions", "Dividends paid")
        bb = inrow("share_buyback", "Share repurchases")
        ndi = inrow("net_debt_issuance", "Net debt issuance / (repayment)")
        cff_rep = inrow("financing_cash_flow", "Cash from financing (reported)", 1.0, 0)
        of = frow("  Other financing items", lambda L: f"={L}{cff_rep}-{L}{div}-{L}{bb}-{L}{ndi}")
        cff = frow("Cash from financing", lambda L: f"={L}{div}+{L}{bb}+{L}{ndi}+{L}{of}", bold=True)
        r += 1
        frow("Net change in cash (computed)", lambda L: f"={L}{cfo}+{L}{cfi}+{L}{cff}", bold=True)
        r += 1
        sh.section(r, "Free cash flow (the DCF basis)", last); r += 1
        fcf = frow("Free cash flow (CFO + capex)", lambda L: f"={L}{cfo}+{L}{capex}", bold=True)
        oe = frow("Owner earnings FCF (FCF − SBC)", lambda L: f"={L}{fcf}-{L}{sbc}", bold=True)
        if isr:
            frow("Owner earnings margin (%)", lambda L: f"=IFERROR({L}{oe}/'IS'!{L}{isr['rev']},0)", PCT)
        sh.widths({"A": 46, "B": 10, **{get_column_letter(c): 13 for c in range(c0, last + 1)}})
        sh.ws.freeze_panes = "C6"

    # ── WACC ────────────────────────────────────────────────────────────────
    def wacc_tab(self) -> None:
        sh = self.sheet("WACC", "Discount-rate build")
        sh.title("WACC build", "Table-driven sector/profile base, adjusted for live cost of debt, insider "
                               "activity, research risk, country risk and contracted revenue.")
        b = self.dr.get("wacc_build") or {}
        if not b:
            sh.note(4, 1, "Discount-rate build not recorded for this run.")
            self.A["wacc"] = None
            return
        r = 4
        bb = b.get("base_breakdown") or {}
        base_cell = None
        if bb:
            A = self.A
            sh.section(r, "Base rate: sector / profile assumption", 4); r += 1
            for lab, v in (("Market", bb.get("market")), ("Source table", bb.get("table")),
                           ("Lookup key", bb.get("lookup")), ("Macro regime", bb.get("macro_regime"))):
                sh.label(r, 1, lab, indent=1)
                sh.put(r, 3, v).font = Font(color=BLACK)
                r += 1
            tr_ = r
            sh.label(r, 1, "Table rate (country risk included)", indent=1)
            sh.put(r, 3, _num(bb.get("table_rate")), PCT2); r += 1
            sh.label(r, 1, "  of which country risk premium (memo)", indent=2)
            sh.put(r, 3, _num(bb.get("crp_embedded")), PCT2)
            self._crp_cell = f"C{r}"
            r += 1
            lev_r = r
            sh.label(r, 1, "Leverage (net debt / equity)", indent=1)
            sh.put(r, 3, _num(bb.get("leverage")), "0.00\"x\""); r += 1
            app_r = r
            # Owner, 2026-10-03 (SBUX review, A8): net debt / equity on negative book equity has no
            # meaning; the test is marked n/a and the premium is zero.
            _lev_v = _num(bb.get("leverage"))
            _neg_eq = _lev_v is not None and _lev_v < 0
            sh.label(r, 1, "Leverage premium applies (1 = yes; US REITs exempt"
                           + ("; n/a: book equity is negative, so the ratio has no meaning" if _neg_eq else "") + ")", indent=1)
            sh.put(r, 3, 0 if _neg_eq else (1 if bb.get("leverage_premium_applies") else 0), "0"); r += 1
            prem_r = r
            sh.label(r, 1, "Leverage premium", indent=1)
            sh.put(r, 3, f"=IF(C{app_r}=1,MAX(0,(C{lev_r}-{A['lev_threshold']})*{A['lev_slope']}),0)", PCT2); r += 1
            ov_r = r
            sh.label(r, 1, "Macro-regime overlay", indent=1)
            sh.put(r, 3, _num(bb.get("macro_overlay")), PCT2); r += 1
            cap_r = r
            sh.label(r, 1, "Cap on premium + overlay", indent=1)
            sh.put(r, 3, _num(bb.get("leverage_cap")), PCT2); r += 1
            sh.label(r, 1, "Base WACC (rounded to 4 d.p. as the engine does)", bold=True)
            sh.put(r, 3, f"=ROUND(MIN(C{tr_}+C{prem_r}+C{ov_r},C{tr_}+C{cap_r}),4)", PCT2, bold=True)
            base_cell = f"C{r}"
            r += 1
            sh.label(r, 1, "Engine base WACC", indent=1)
            sh.put(r, 3, _num(bb.get("wacc")), PCT2); r += 1
            sh.label(r, 1, "Check", indent=1)
            sh.put(r, 3, f"={base_cell}-C{r - 1}", PCT2); r += 2
        sh.section(r, "Adjustments to the base rate", 4); r += 1
        comps = [
            ("Sector / profile base WACC", f"={base_cell}" if base_cell else _num(b.get("wacc_base"))),
            ("Cost-of-debt adjustment (hybrid − base)",
             (_num(b.get("wacc")) - _num(b.get("wacc_base")))
             if _num(b.get("wacc")) is not None and _num(b.get("wacc_base")) is not None else None),
            ("Insider-activity overlay", (_num(b.get("insider_bps")) or 0.0) / 10000.0),
            ("Research risk loading" + (f" ({b.get('research_risk_flag')})" if b.get("research_risk_flag") else ""),
             _num(b.get("research_risk_loading")) or 0.0),
            ("Country risk premium", _num(b.get("country_risk_premium")) or 0.0),
            ("Contracted-revenue discount", _num(b.get("contracted_revenue_discount")) or 0.0),
        ]
        first = r
        for lab, v in comps:
            sh.label(r, 1, lab, indent=1)
            sh.put(r, 3, v, PCT2)
            r += 1
        engine = _num(b.get("wacc_final")) or _num(self.dr.get("wacc"))
        # The engine rounds at two intermediate steps; the residual is shown
        # as its own input line rather than hidden inside a formula.
        known = sum((_num(bb.get("wacc")) if isinstance(v, str) else v) or 0.0
                    for _, v in comps if v is not None)
        sh.label(r, 1, "Engine rounding (residual)", indent=1)
        sh.put(r, 3, (engine - known) if engine is not None else 0.0, PCT2)
        r += 1
        sh.label(r, 1, "WACC used", bold=True)
        sh.put(r, 3, f"=SUM(C{first}:C{r - 1})", PCT2, bold=True)
        self.A["wacc"] = _ref("WACC", r, 3)
        r += 2
        A = self.A
        sh.section(r, "CAPM cross-check (not used by the engine)", 4); r += 1
        sh.label(r, 1, "Cost of equity = rf + beta × ERP + country risk", indent=1)
        crp_cell = getattr(self, "_crp_cell", None) if bb else None
        sh.put(r, 3, f"=IFERROR({A['rf']}+{A['beta']}*{A['erp']}+{crp_cell},NA())"
               if crp_cell else f"=IFERROR({A['rf']}+{A['beta']}*{A['erp']},NA())", PCT2)
        r += 1
        sh.note(r, 1, "Shown for comparison: the engine's WACC is table-driven by sector/profile, "
                      "adjusted for live cost of debt, not built from beta.")
        r += 2
        sh.section(r, "Cost of debt detail (hybrid model)", 4); r += 1
        for lab, key, fmt in (("Live cost of debt", "rd_live", PCT2), ("Baseline cost of debt", "rd_baseline", PCT2),
                              ("Debt / (debt + equity)", "dv_ratio", PCT), ("Implied rating", "rating", None),
                              ("Credit spread source", "source", None), ("Leverage (net debt / equity)", "leverage", "0.00"),
                              ("Macro regime", "macro_regime", None)):
            if b.get(key) is None:
                continue
            sh.label(r, 1, lab, indent=1)
            v = b.get(key)
            c = sh.put(r, 3, _num(v) if fmt and _num(v) is not None else v, fmt)
            r += 1
        sh.widths({"A": 48, "B": 2, "C": 14})

    # ── DCF ─────────────────────────────────────────────────────────────────
    def dcf_tab(self) -> None:
        sh = self.sheet("DCF", "Discounted cash flow per scenario, terminal value, sensitivity")
        sh.title("DCF valuation", "Revenue × FCF margin, discounted; drivers from Assumptions, WACC from the "
                                  "WACC tab.")
        r = 4
        for s in SCENARIOS:
            tr = (self.scen(s).get("leg_inputs") or {}).get("DCF")
            if not tr or not tr.get("projection_rows"):
                continue
            r = self._dcf_block(sh, r, s, tr) + 2
        for leg, per in getattr(self, "dcf_variants", {}).items():
            for s in SCENARIOS:
                v = per.get(s)
                if not v:
                    continue
                if v.get("same_as_core"):
                    if s in self.dcf:
                        self.leg_cell[(s, leg)] = self.dcf[s]["iv"]
                    continue
                r = self._dcf_block(sh, r, s, v["trace"], sfx=f"|{leg}", leg=leg) + 2
        if r == 4:
            sh.note(4, 1, "No DCF projection recorded for this run.")
        # Owner, 2026-10-03 (SBUX review, A1): a DCF the blend does not weight says so, and the gap to
        # the blended intrinsic value is reconciled to the profile's choice of methods.
        _base_li = (self.scen("base").get("leg_inputs") or {})
        _weighted = [n for n in _base_li if self.in_blend(n)]
        _dcf_weighted = any(n == "DCF" or n in getattr(self, "dcf_variants", {}) for n in _weighted)
        if r > 4 and not _dcf_weighted:
            _dcf_v = (_base_li.get("DCF") or {}).get("value")
            _iv_v = self.scen("base").get("intrinsic_value")
            sh.note(r, 1, "NOT IN THE BLEND: the " + str(self.dr.get("profile")) + " profile weights "
                          + (", ".join(_weighted) or "other legs") + "; the DCF value per share "
                          + (f"{float(_dcf_v):,.2f}" if isinstance(_dcf_v, (int, float)) else "n/a")
                          + " against the blended intrinsic value "
                          + (f"{float(_iv_v):,.2f}" if isinstance(_iv_v, (int, float)) else "n/a")
                          + " is the profile's choice of methods, recorded here for reconciliation; it is not an input to the target.")
        sh.widths({"A": 42, "B": 14, **{get_column_letter(c): 13 for c in range(3, 14)}})

    def _dcf_block(self, sh: _Sheet, r: int, s: str, tr: dict, sfx: str = "", leg: str = "DCF") -> int:
        """One scenario's projection. `sfx` selects a DCF-family leg's own drivers."""
        A = self.A
        core = not sfx
        sh.section(r, f"{s.upper()} scenario" + ("" if core else f" — {leg}"), 13); r += 1
        rows = tr.get("projection_rows") or []
        n = len(rows)
        c0 = 3
        is_last = getattr(self, "_is_last", None)
        # Year headers: FY dates rolled from the last actual on the IS.
        sh.label(r, 1, "Projection year", bold=True)
        for t in range(n):
            if is_last:
                prev = (f"'IS'!${get_column_letter(is_last)}$4" if t == 0
                        else f"{get_column_letter(c0 + t - 1)}{r}")
                sh.put(r, c0 + t, f"=EDATE({prev},12)", FY, bold=True)
            else:
                sh.put(r, c0 + t, t + 1, "0", bold=True)
        hdr = r
        r += 1
        sh.label(r, 1, "Year index"); yr = r
        for t in range(n):
            sh.put(r, c0 + t, t + 1, "0")
        r += 1
        sh.label(r, 1, "Revenue growth"); gr = r
        for t in range(n):
            sh.put(r, c0 + t, "=" + A[f"g{t + 1}:{s}{sfx}"], PCT2)
        r += 1
        sh.label(r, 1, "Discount rate"); wr = r
        staged = tr.get("wacc_schedule")
        for t in range(n):
            if staged:
                sh.put(r, c0 + t, "=" + A[f"w{t + 1}:{s}{sfx}"], PCT2)
            elif not core:
                sh.put(r, c0 + t, "=" + A[f"wacc:{s}{sfx}"], PCT2)
            elif A.get("wacc"):
                sh.put(r, c0 + t, "=" + A["wacc"], PCT2)
            else:
                sh.put(r, c0 + t, _num(rows[t].get("wacc")), PCT2)
        r += 1
        sh.label(r, 1, "Revenue"); rv = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            prev = A[f"revenue_base:{s}{sfx}"] if t == 0 else f"{get_column_letter(c0 + t - 1)}{r}"
            sh.put(r, c0 + t, f"={prev}*(1+{L}{gr})", BIG)
        r += 1
        sh.label(r, 1, "Reinvestment deduction (charge off: 0)"); rd = r
        for t in range(n):
            sh.put(r, c0 + t, _num(rows[t].get("reinvest_margin_deduction")) or 0.0, PCT2)
        r += 1
        sh.label(r, 1, "FCF margin (base + change, within floor and cap)"); mr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            sh.put(r, c0 + t, f"=MAX(MIN({A[f'fcf_margin_base:{s}{sfx}']}+{A[f'margin_delta_absolute:{s}{sfx}']}"
                              f"-{L}{rd},{A['margin_cap']}),{A[f'fcf_floor:{s}{sfx}']})", PCT2)
        r += 1
        sh.label(r, 1, "Free cash flow"); fr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            sh.put(r, c0 + t, f"={L}{rv}*{L}{mr}", BIG)
        r += 1
        sh.label(r, 1, "Discount factor"); dfr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            prev = "1" if t == 0 else f"{get_column_letter(c0 + t - 1)}{r}"
            sh.put(r, c0 + t, f"=IFERROR({prev}/(1+{L}{wr}),0)", "0.0000")
        r += 1
        sh.label(r, 1, "PV of FCF"); pvr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            sh.put(r, c0 + t, f"={L}{fr}*{L}{dfr}", BIG)
        r += 2
        F, Lc = get_column_letter(c0), get_column_letter(c0 + n - 1)
        tg, nd, shs = A[f"tgr:{s}{sfx}"], A[f"net_debt:{s}{sfx}"], A[f"shares:{s}{sfx}"]
        out = r
        lines = [
            ("Sum of PV of FCF", f"=SUM({F}{pvr}:{Lc}{pvr})", BIG),
            ("Terminal WACC (spread guard applied)", f"=IF({Lc}{wr}<={tg},{tg}+{A['tv_guard']},{Lc}{wr})", PCT2),
            ("Terminal value", f"=IFERROR({Lc}{fr}*(1+{tg})/(B{out + 1}-{tg}),0)", BIG),
            ("PV of terminal value", f"=B{out + 2}*{Lc}{dfr}", BIG),
            ("Enterprise value", f"=B{out}+B{out + 3}", BIG),
            ("Less: net debt", f"=-{nd}", BIG),
            # Owner, 2026-10-03 (Alibaba review, section 1): the engine deducts minority interest and
            # preferred equity in the DCF bridge; the tab did not, so every check read REVIEW
            # (HK$3.965 a share on 09988.HK = the HK$76.3bn of minorities over 19,235m shares).
            ("Less: minority interest", -(_num(tr.get("minority_interest")) or 0.0), BIG),
            ("Less: preferred equity", -(_num(tr.get("preferred_equity")) or 0.0), BIG),
            ("Equity value", f"=B{out + 4}+B{out + 5}+B{out + 6}+B{out + 7}", BIG),
            ("Intrinsic value per share", f"=IFERROR(B{out + 8}/{shs},0)", NUM),
            ("Engine value", _num(tr.get("value")), NUM),
            ("Check", f"=B{out + 9}-B{out + 10}", NUM),
            ("Terminal value share of EV", f"=IFERROR(B{out + 3}/B{out + 4},0)", PCT),
        ]
        for i, (lab, v, fmt) in enumerate(lines):
            sh.label(out + i, 1, lab, bold=lab in ("Intrinsic value per share", "Enterprise value"))
            sh.put(out + i, 2, v, fmt, bold=lab == "Intrinsic value per share")
        if not core:
            self.leg_cell[(s, leg)] = _ref("DCF", out + 9, 2)
            return out + len(lines)
        self.dcf[s] = {"iv": _ref("DCF", out + 9, 2), "pv_fcf": _ref("DCF", out, 2),
                       "pv_tv": _ref("DCF", out + 3, 2), "ev": _ref("DCF", out + 4, 2),
                       "nd": _ref("DCF", out + 5, 2), "mi": _ref("DCF", out + 6, 2), "pe": _ref("DCF", out + 7, 2),
                       "eq": _ref("DCF", out + 8, 2),
                       "fcf_row": fr, "rev_row": rv, "c0": c0, "n": n}
        self.leg_cell[(s, "DCF")] = self.dcf[s]["iv"]
        if s == "base" and getattr(self, "_is_sheet", None) is not None:
            self._link_is_forecast(s)
        r = out + len(lines) + 1
        # Sensitivity: FCF is independent of the discount rate, so a flat WACC
        # re-discounts the same stream exactly.
        sh.label(r, 1, "Sensitivity: value per share, flat WACC × terminal growth", bold=True)
        r += 1
        center_w = A.get("wacc") or f"{Lc}{wr}"
        sh.label(r, 1, "WACC \\ terminal growth")
        for j, k in enumerate((-2, -1, 0, 1, 2)):
            sh.put(r, 2 + j, f"={tg}+({k})*{A['sens_g']}", PCT2, bold=True)
        top = r
        for i, k in enumerate((-2, -1, 0, 1, 2)):
            rr = r + 1 + i
            sh.put(rr, 1, f"={center_w}+({k})*{A['sens_w']}", PCT2, bold=True)
            for j in range(5):
                gc = f"{get_column_letter(2 + j)}${top}"
                wc = f"$A{rr}"
                f = (f"=IFERROR(IF({wc}<={gc},NA(),(SUMPRODUCT(${F}${fr}:${Lc}${fr}/(1+{wc})^${F}${yr}:${Lc}${yr})"
                     f"+${Lc}${fr}*(1+{gc})/({wc}-{gc})/(1+{wc})^${Lc}${yr}-{nd})/{shs}),NA())")
                sh.put(rr, 2 + j, f, NUM)
        return r + 6

    def _link_is_forecast(self, s: str) -> None:
        sh, rev, g, last = self._is_sheet, self._is_rev_row, self._is_g_row, self._is_last
        d = self.dcf[s]
        for t in range(5):
            c = last + 1 + t
            src = get_column_letter(d["c0"] + t)
            sh.put(rev, c, f"=IFERROR('DCF'!{src}{d['rev_row']}/{self.A['unit']}/{self.A['fx']},0)", MIL)
        sh.note(3, 1, "Forecast (E) columns: FMP consensus as published, and the DCF base-case "
                      "revenue path the valuation uses, with the variance between them.")

    # ── Multiples ───────────────────────────────────────────────────────────
    def multiples_tab(self) -> None:
        sh = self.sheet("Multiples", "Each valuation leg: metric × multiple → EV → equity → per share")
        sh.title("Relative valuation legs", "Multiple = peer multiple × scenario band × growth premium × "
                                            "other adjustments (named). Check = rebuilt − engine.")
        cols = ["Scenario", "Leg", "Metric", "Metric value", "Peer multiple", "Scenario band",
                "Growth premium", "Other adj.", "Multiple", "Enterprise value", "Net debt",
                "Minority interest", "Equity value", "Shares", "Value per share", "Engine value",
                "Check", "Notes"]
        sh.header(4, cols)
        r = 5
        recorded = False
        for s in SCENARIOS:
            legs = self.scen(s).get("leg_inputs") or {}
            table = self.scen(s).get("method_iv_table") or {}
            for leg in (list(legs) or list(table)):
                if not self.in_blend(leg):
                    continue
                tr = legs.get(leg) or {}
                kind = tr.get("kind")
                if kind in ("dcf", "ggm", "sotp"):
                    continue
                engine = _num(tr.get("value") if tr else table.get(leg))
                if engine is None:
                    continue
                recorded = recorded or bool(tr)
                sh.put(r, 1, s).font = Font(color=BLACK)
                sh.put(r, 2, leg).font = Font(color=BLACK)
                sh.put(r, 16, engine, NUM)
                parts = tr.get("multiple_parts") or {}
                named = {"peer_multiple", "scenario_band", "growth_premium", "peer_source", "peer_fcf_yield"}
                others = [(k, _num(v)) for k, v in parts.items() if k not in named and _num(v) is not None]
                oprod = 1.0
                for _, v in others:
                    oprod *= v
                notes = "; ".join(f"{k} {v:g}" for k, v in others if v != 1.0)
                if parts.get("peer_source"):
                    notes = f"peer multiple: {parts['peer_source']}" + (f"; {notes}" if notes else "")
                done = True
                if kind == "ev_multiple":
                    sh.put(r, 3, tr.get("metric")).font = Font(color=BLACK)
                    sh.put(r, 4, _mil(tr.get("metric_value")), MIL)
                    sh.put(r, 5, _num(parts.get("peer_multiple")), MULT)
                    sh.put(r, 6, _num(parts.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, _num(parts.get("growth_premium", 1.0)) or 1.0, "0.000")
                    sh.put(r, 8, oprod, "0.000")
                    sh.put(r, 9, f"=E{r}*F{r}*G{r}*H{r}", MULT)
                    sh.put(r, 10, f"=D{r}*I{r}", MIL)
                    sh.put(r, 11, _mil(tr.get("net_debt")), MIL)
                    sh.put(r, 12, _mil(tr.get("minority_interest")), MIL)
                    sh.put(r, 13, f"=J{r}-K{r}-L{r}", MIL)
                    sh.put(r, 14, _mil(tr.get("shares")), MIL)
                    sh.put(r, 15, f"=IFERROR(MAX(M{r}/N{r},0),0)", NUM)
                    notes = "values in millions; " + notes
                elif kind == "equity_multiple":
                    sh.put(r, 3, (tr.get("metric") or "") + ", per share").font = Font(color=BLACK)
                    sh.put(r, 4, _num(tr.get("per_share_metric")), NUM)
                    sh.put(r, 5, _num(parts.get("peer_multiple")), MULT)
                    sh.put(r, 6, _num(parts.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, _num(parts.get("growth_premium", 1.0)) or 1.0, "0.000")
                    sh.put(r, 8, oprod, "0.000")
                    sh.put(r, 9, f"=E{r}*F{r}*G{r}*H{r}", MULT)
                    sh.put(r, 15, f"=D{r}*I{r}", NUM)
                elif kind == "yield":
                    sh.put(r, 3, (tr.get("metric") or "") + ", per share").font = Font(color=BLACK)
                    sh.put(r, 4, _num(tr.get("per_share_metric")), NUM)
                    sh.put(r, 5, _num(parts.get("peer_fcf_yield")), PCT2)
                    sh.put(r, 6, _num(parts.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, _num(parts.get("growth_premium", 1.0)) or 1.0, "0.000")
                    sh.put(r, 9, f"=IFERROR(E{r}/(F{r}*G{r}),0)", PCT2)
                    sh.put(r, 15, f"=IFERROR(D{r}/I{r},0)", NUM)
                    notes = "target FCF yield = peer yield / (band × growth premium)"
                elif kind == "capitalised_earnings":
                    sh.put(r, 3, f"{tr.get('metric')} → NOPAT / WACC").font = Font(color=BLACK)
                    sh.put(r, 4, _mil(tr.get("metric_value")), MIL)
                    sh.put(r, 6, _num(tr.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 8, 1 - (_num(tr.get("tax_rate")) or 0.0), "0.000")
                    sh.put(r, 9, _num(tr.get("capitalisation_rate")), PCT2)
                    sh.put(r, 10, f"=IFERROR(D{r}*F{r}*H{r}/I{r},0)", MIL)
                    sh.put(r, 11, _mil(tr.get("net_debt")), MIL)
                    sh.put(r, 12, _mil(tr.get("minority_interest")), MIL)
                    sh.put(r, 13, f"=J{r}-K{r}-L{r}", MIL)
                    sh.put(r, 14, _mil(tr.get("shares")), MIL)
                    sh.put(r, 15, f"=IFERROR(MAX(M{r}/N{r},0),0)", NUM)
                    notes = "earnings power: EBIT × band × (1 − tax) capitalised at WACC; values in millions"
                else:
                    done = False
                    notes = ("engine value: no metric × multiple form" if tr
                             else "engine value: run predates leg-input recording")
                if done:
                    sh.put(r, 17, f"=O{r}-P{r}", NUM)
                    self.leg_cell[(s, leg)] = _ref("Multiples", r, 15)
                else:
                    self.leg_cell.setdefault((s, leg), _ref("Multiples", r, 16))
                    if tr:
                        self.engine_only.add(leg)
                sh.note(r, 18, notes)
                r += 1
            r += 1
        if not recorded:
            sh.note(3, 1, "This run predates leg-input recording; legs show engine values only.")
        sh.widths({"A": 8, "B": 22, "C": 34, "D": 14, "E": 10, "F": 9, "G": 9, "H": 9, "I": 10,
                   "J": 14, "K": 13, "L": 12, "M": 14, "N": 12, "O": 12, "P": 12, "Q": 9, "R": 60})
        sh.ws.freeze_panes = "C5"

    # ── Comps ───────────────────────────────────────────────────────────────
    def comps_tab(self) -> None:
        sh = self.sheet("Comps", "Named peer baskets behind each peer multiple")
        mu = self.dr.get("multiples_used") or {}
        sh.title("Comparable companies", f"Comps market {mu.get('comp_market')}; medians over in-band members "
                                         "(plausibility band). Engine median shown for the check.")
        labels = {"ev_ebitda": "EV/EBITDA", "pe": "P/E", "ev_revenue": "EV/Revenue", "pb": "P/B",
                  "fcf_yield": "FCF yield"}
        r = 4
        self.comps_summary: list[tuple[str, Any, str]] = []
        for field, info in (mu.get("fields") or {}).items():
            if field not in labels:
                continue
            lab = labels[field]
            sh.section(r, f"{lab}: {info.get('basis')} / cohort {info.get('cohort')}"
                          + (f" — {info.get('exchange')} '{info.get('key')}'" if info.get("key") else ""), 7)
            r += 1
            members = []
            if info.get("key") and self.load_members:
                try:
                    members = self.load_members(info.get("exchange"), info.get("basis"),
                                                info.get("key"), info.get("cohort") or "all")
                except Exception:  # noqa: BLE001
                    members = []
            eng_row = None
            if members:
                sh.header(r, ["Ticker", "Company", "Market cap (m)", lab, "In band", "In-band value"])
                first = r + 1
                for m in members:
                    r += 1
                    mv = (m.get("metrics") or {}).get(field) or {}
                    sh.put(r, 1, m.get("symbol")).font = Font(color=BLACK)
                    sh.put(r, 2, m.get("name")).font = Font(color=BLACK)
                    sh.put(r, 3, _mil(m.get("market_cap")), MIL)
                    sh.put(r, 4, _num(mv.get("value")), "0.00")
                    sh.put(r, 5, "yes" if mv.get("in_band") else "no").font = Font(color=BLUE)
                    sh.put(r, 6, f'=IF(E{r}="yes",D{r},"")', "0.00")
                last = r
                r += 1
                sh.label(r, 5, "Median", bold=True)
                sh.put(r, 6, f"=MEDIAN(F{first}:F{last})", "0.0000", bold=True)
                r += 1
                sh.label(r, 5, "Engine")
                sh.put(r, 6, _num(info.get("value")), "0.0000")
                eng_row = r
                r += 1
                sh.label(r, 5, "Check")
                sh.put(r, 6, f"=ROUND(F{r - 2},{self.A['dp_comps']})-F{r - 1}", "0.0000")
                # Members come from the latest weekly refresh, not frozen with the
                # run: a refresh after the valuation date can move the basket.
                asof = max((str(m.get("computed_at") or "")[:10] for m in members), default="")
                run_day = str(self.run.get("run_at") or self.data.get("end_date") or "")[:10]
                sh.note(r, 7, f"members as of refresh {asof or 'unknown'}; run valued {run_day or 'unknown'}"
                              + ("; basket refreshed after the run, so a non-zero check is basket drift"
                                 if asof and run_day and asof > run_day else ""))
                self.comps_summary.append((lab, _ref("Comps", r - 2, 6), f"{len(members)} named peers"))
            else:
                sh.note(r, 1, "static/dynamic table: no named peer set" if info.get("basis") in
                        ("static", "dynamic", "unknown") else
                        "named members not recorded yet (populated by the weekly comps refresh)")
                r += 1
                sh.label(r, 5, "Engine")
                sh.put(r, 6, _num(info.get("value")), "0.0000")
                self.comps_summary.append((lab, _ref("Comps", r, 6), f"{info.get('basis')}"))
            r += 2
        sh.widths({"A": 14, "B": 42, "C": 16, "D": 12, "E": 10, "F": 14})

    # ── SOTP ────────────────────────────────────────────────────────────────
    def sotp_tab(self) -> None:
        sh = self.sheet("SOTP", "Sum of the parts: segment values, associates, net cash, holdco discount")
        sh.title("Sum of the parts", "Segment values as the analyst SOTP computed them (metric × multiple), "
                                     "flexed per scenario by revenue tree × multiple band where no analyst "
                                     "scenario exists. Values in millions of the SOTP currency.")
        r = 4
        for s in SCENARIOS:
            for name, tr in (self.scen(s).get("leg_inputs") or {}).items():
                if tr.get("kind") != "sotp" or not self.in_blend(name):
                    continue
                t = tr.get("table") or {}
                sh.section(r, f"{s.upper()} — {name} ({tr.get('source')})", 9); r += 1
                sh.header(r, ["Segment", "Method", "Multiple", "Base value", "Revenue factor",
                              "Multiple band", "Scenario value"])
                flex = {f.get("segment"): f for f in (tr.get("flex") or [])}
                first = r + 1
                for row in t.get("rows") or []:
                    r += 1
                    f = flex.get(row.get("name")) or {}
                    sh.put(r, 1, row.get("name")).font = Font(color=BLACK)
                    sh.put(r, 2, row.get("method")).font = Font(color=BLACK)
                    sh.put(r, 3, _num(row.get("multiple")), "0.00")
                    sh.put(r, 4, _mil(row.get("value")), MIL)
                    sh.put(r, 5, _num(f.get("revenue_factor", 1.0)) or 1.0, "0.0000")
                    sh.put(r, 6, _num(f.get("multiple_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, f"=D{r}*E{r}*F{r}", MIL)
                last = r
                r += 1
                seg = r
                sh.label(r, 1, "Segment value", bold=True)
                sh.put(r, 7, f"=SUM(G{first}:G{last})", MIL, bold=True); r += 1
                sh.label(r, 1, "Associates and investments")
                sh.put(r, 7, _mil(t.get("associates")), MIL); asso = r; r += 1
                sh.label(r, 1, "Net cash")
                sh.put(r, 7, _mil(t.get("net_cash")), MIL); nc = r; r += 1
                sh.label(r, 1, "Net asset value", bold=True)
                sh.put(r, 7, f"=G{seg}+G{asso}+G{nc}", MIL, bold=True); nav = r; r += 1
                nav_eng, disc_eng = _num(t.get("nav")), _num(t.get("holdco_discount"))
                sh.label(r, 1, "Holdco discount rate")
                sh.put(r, 7, (disc_eng / nav_eng) if nav_eng else _num(t.get("holdco_discount_pct")), PCT)
                dr_ = r; r += 1
                sh.label(r, 1, "Equity value after holdco discount", bold=True)
                sh.put(r, 7, f"=G{nav}*(1-G{dr_})", MIL, bold=True); fin = r; r += 1
                sh.label(r, 1, "Shares (millions)")
                sh.put(r, 7, _mil(t.get("shares")), MIL); shr = r; r += 1
                sh.label(r, 1, "FX to valuation currency")
                sh.put(r, 7, _num(t.get("fx_to_reporting")) or 1.0, "0.0000"); fx = r; r += 1
                sh.label(r, 1, "Value per share", bold=True)
                sh.put(r, 7, f"=IFERROR(G{fin}/G{shr}*G{fx},0)", NUM, bold=True); ps = r; r += 1
                sh.label(r, 1, "Engine value")
                sh.put(r, 7, _num(tr.get("value")), NUM); r += 1
                sh.label(r, 1, "Check")
                sh.put(r, 7, f"=G{ps}-G{r - 1}", NUM); r += 2
                self.leg_cell[(s, name)] = _ref("SOTP", ps, 7)
        sh.widths({"A": 44, "B": 10, "C": 10, "D": 14, "E": 14, "F": 12, "G": 16})

    # ── Guidance → estimates (owner, 2026-10-03) ──────────────────────────────
    def guidance_tab(self) -> None:
        """Management guidance as stated, consensus, the model's bear / base / bull
        estimates (deep research 2G → guidance_estimates), and the growth path the
        base DCF ran on. Static values in input colour: the research produced them."""
        ge = (self.dr or {}).get("guidance_estimates") or {}
        est = ge.get("estimates") or {}
        g = ge.get("guidance") or {}
        c = ge.get("consensus") or {}
        sh = self.sheet("Guidance", "Management guidance → the model's estimates and how the DCF used them")
        sh.title("Management guidance → estimates",
                 f"Confidence {ge.get('confidence') or '—'}"
                 + (f"; guidance as of {ge.get('as_of')}" if ge.get("as_of") else "")
                 + ". " + ("Applied: the base DCF's first years run on these estimates." if ge.get("applied")
                           else f"Shown, not applied: {ge.get('not_applied_reason') or 'the DCF did not use it'}."))
        fy1, fy2 = ge.get("fiscal_year_1") or "FY+1", ge.get("fiscal_year_2") or "FY+2"
        r = 4
        sh.section(r, f"Guidance as stated ({fy1})", 6); r += 1
        sh.header(r, ["Metric", "Low", "Mid", "High", "Unit", "Basis / status"])
        for label, key, fmt, unit in (("Revenue growth", "revenue_growth", PCT, "yoy"),
                                      ("Revenue", "revenue", NUM, None),
                                      ("EBITDA / operating margin", "ebitda_margin", PCT, "%"),
                                      ("EPS", "eps", NUM, None)):
            rng = g.get(key) or {}
            if not isinstance(rng, dict) or all(rng.get(k) is None for k in ("low", "mid", "high")):
                continue
            r += 1
            sh.label(r, 1, label)
            for col, k in ((2, "low"), (3, "mid"), (4, "high")):
                if rng.get(k) is not None:
                    sh.put(r, col, _num(rng.get(k)), fmt)
            sh.put(r, 5, unit or " ".join(str(x) for x in (rng.get("currency"), rng.get("scale")) if x)).font = Font(color=BLACK)
            sh.put(r, 6, " / ".join(str(x) for x in (g.get("basis"), g.get("status")) if x)).font = Font(color=BLACK)
        if g.get("quote"):
            r += 1
            sh.label(r, 1, f"“{g.get('quote')}”" + (f" — {g.get('source')}" if g.get("source") else ""), indent=1)
        r += 2
        sh.section(r, f"Consensus ({fy1})", 6); r += 1
        sh.label(r, 1, "Revenue growth"); sh.put(r, 2, _num(c.get("revenue_growth_fy1")), PCT); r += 1
        sh.label(r, 1, "EPS"); sh.put(r, 2, _num(c.get("eps_fy1")), NUM); r += 1
        sh.label(r, 1, "Guidance vs consensus"); sh.put(r, 2, _num(ge.get("guidance_vs_consensus_pct")), PCT); r += 1
        if c.get("as_of"):
            sh.label(r, 1, "Consensus as of"); sh.put(r, 2, str(c.get("as_of"))).font = Font(color=BLACK); r += 1
        r += 1
        sh.section(r, "Model estimates", 6); r += 1
        sh.header(r, ["Estimate", "Bear", "Base", "Bull"])
        for label, field, fmt in ((f"Revenue growth {fy1}", "revenue_growth_fy1", PCT),
                                  (f"Revenue growth {fy2}", "revenue_growth_fy2", PCT),
                                  (f"EBITDA margin {fy1}", "ebitda_margin_fy1", PCT),
                                  (f"EBITDA margin {fy2}", "ebitda_margin_fy2", PCT),
                                  (f"EPS {fy1}", "eps_fy1", NUM), (f"EPS {fy2}", "eps_fy2", NUM)):
            vals = [(est.get(sc) or {}).get(field) for sc in ("bear", "base", "bull")]
            if all(v is None for v in vals):
                continue
            r += 1
            sh.label(r, 1, label)
            for col, v in zip((2, 3, 4), vals):
                if v is not None:
                    sh.put(r, col, _num(v), fmt)
        ch = ge.get("channel") or {}
        sched = ch.get("schedule") or []
        if sched:
            r += 2
            sh.section(r, "Base DCF growth path", 6); r += 1
            sh.header(r, ["Year", "Growth used", "Source"])
            E, F = int(ch.get("explicit_years") or 0), int(ch.get("fade_years") or 0)
            for i, v in enumerate(sched, start=1):
                r += 1
                sh.put(r, 1, i, "0")
                sh.put(r, 2, _num(v), PCT)
                sh.put(r, 3, ("guidance-derived estimate" if i <= E else "fade onto the engine path" if i <= E + F
                              else "engine path")).font = Font(color=BLACK)
            r += 1
            sh.label(r, 1, "Engine year-1 growth before the channel"); sh.put(r, 2, _num(ch.get("engine_year1")), PCT); r += 1
        # Owner, 2026-10-03 (five principles): the forecast the DCF ran on.
        fc = (self.dr or {}).get("guidance_forecast") or {}
        if fc.get("rows"):
            r += 2
            _t = fc.get("target") or {}
            sh.section(r, f"Guidance forecast — {fc.get('archetype_name')}: {fc.get('horizon_years')} years to "
                          + (f"the {_t.get('target_year')} {_t.get('metric')} target" if _t else "the FY+2 estimate")
                          + f", then a {fc.get('fade_years')}-year fade", 9); r += 1
            # Owner, 2026-10-03 (interactive agent): the override in force, then how the estimate was built.
            _ovr = fc.get("override") or (self.dr or {}).get("estimate_override") or {}
            if _ovr:
                sh.label(r, 1, f"USER OVERRIDE ({str(_ovr.get('created_at') or '')[:10]}): " + ", ".join(_ovr.get("fields") or [])
                               + (f" — {_ovr.get('note')}" if _ovr.get("note") else ""), bold=True); r += 1
            for _st in fc.get("steps") or []:
                sh.label(r, 1, f"{_st.get('n')}. {_st.get('title')}: {_st.get('detail')}"); r += 1
            r += 1
            sh.header(r, ["Year", "Revenue (mn)", "Growth", "EBIT margin", "EBIT (mn)", "Net income (mn)", "EPS", "UFCF (mn)", "FCF margin", "Phase"])
            for row in fc["rows"]:
                r += 1
                sh.put(r, 1, int(row.get("year") or 0), "0")
                sh.put(r, 2, _mil(row.get("revenue")), MIL); sh.put(r, 3, _num(row.get("growth")), PCT)
                sh.put(r, 4, _num(row.get("ebit_margin")), PCT); sh.put(r, 5, _mil(row.get("ebit")), MIL)
                sh.put(r, 6, _mil(row.get("net_income")), MIL); sh.put(r, 7, _num(row.get("eps")), NUM)
                sh.put(r, 8, _mil(row.get("ufcf")), MIL); sh.put(r, 9, _num(row.get("fcf_margin")), PCT)
                sh.put(r, 10, str(row.get("phase") or "")).font = Font(color=BLACK)
            r += 1
            dec = fc.get("deconstruction") or {}
            for label, key, fmt in (("Target-year revenue", "revenue_T", MIL), ("Implied EBIT, target year", "ebit_T_implied", MIL),
                                    ("Implied tax-and-non-operating take", "implied_tax_rate", PCT), ("Guided revenue CAGR", "guided_cagr", PCT),
                                    ("Market CAGR", "market_cagr", PCT)):
                if dec.get(key) is not None:
                    sh.label(r, 1, label); sh.put(r, 2, (_mil(dec.get(key)) if fmt == MIL else _num(dec.get(key))), fmt); r += 1
            term = fc.get("terminal") or {}
            for label, key, fmt in (("Terminal growth", "tgr", PCT), ("Terminal ROIC", "roic_terminal", PCT), ("Reinvestment rate (g / ROIC)", "reinvestment_rate", PCT),
                                    ("Implied exit EV/EBITDA", "implied_exit_ev_ebitda", "0.0"), ("Peer EV/EBITDA median", "peer_ev_ebitda_median", "0.0")):
                if term.get(key) is not None:
                    sh.label(r, 1, label); sh.put(r, 2, _num(term.get(key)), fmt); r += 1
            for inv in fc.get("invariants") or []:
                mark = "PASS" if inv.get("ok") is True else ("FAIL" if inv.get("ok") is False else "n/a")
                sh.label(r, 1, f"Check {inv.get('id')} {inv.get('name')}: {mark} — {inv.get('detail')}"); r += 1
            for f in fc.get("flags") or []:
                sh.label(r, 1, f"Flag: {f}"); r += 1
        for key, label in (("rationale", "Rationale"), ("track_record", "Track record")):
            if ge.get(key):
                r += 1
                sh.label(r, 1, f"{label}: {ge.get(key)}")
        if ge.get("citations"):
            r += 1
            sh.label(r, 1, "Sources: " + " · ".join(str(x) for x in ge.get("citations")))
        sh.widths({"A": 46, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 12, "H": 14, "I": 12, "J": 10})

    # ── Banks ───────────────────────────────────────────────────────────────
    def family_tab(self) -> None:
        """Owner, 2026-09-27: the profile's report family -- the desk's exposition fields, the
        thesis skeleton, the signals that belong, and the checklist computed from this run's
        valuation record (the same lines the writer narrated). Static values, input colour."""
        try:
            from src.data.report_families import report_family_for, REPORT_FAMILIES
            from src.agents.pm.industry_pm import family_checklist
        except Exception:                                  # noqa: BLE001
            return
        fam = report_family_for(self.dr.get("profile"))
        spec = REPORT_FAMILIES.get(fam) or {}
        sh = self.sheet("Family", f"Report family: {fam} -- exposition, skeleton, signals and the checklist from this run")
        sh.title(f"{self.ticker} — {fam}", f"Profile {self.dr.get('profile')}; anchor {self.dr.get('anchor_method')}")
        r = 4
        for heading, items in (("Valuation exposition (what the write-up must state)", spec.get("exposition") or []),
                               ("Thesis skeleton (theme order)", spec.get("skeleton") or []),
                               ("Signals that belong on the page", spec.get("signals") or []),
                               ("Metrics the family does not use", spec.get("excluded_metrics") or [])):
            sh.section(r, heading, 6); r += 1
            if not items:
                sh.note(r, 1, "(none declared)"); r += 1
            for i, it in enumerate(items, 1):
                sh.label(r, 1, f"{i}. {it}", indent=1); r += 1
            r += 1
        sym = {"USD": "$", "SGD": "S$", "HKD": "HK$", "CNY": "RMB", "EUR": "€", "GBP": "£"}.get(str(self.ccy).upper(), str(self.ccy).upper() + " ")
        lines = family_checklist(fam, self.dr, None, (self.data.get("sector_kpis") or {}), sym)
        sh.section(r, "Checklist from this run's valuation record", 6); r += 1
        if not lines:
            sh.note(r, 1, "(no record lines)"); r += 1
        for line in lines:
            sh.label(r, 1, line, indent=1); r += 1
        sh.widths({"A": 110})

    def banks_tab(self) -> None:
        sh = self.sheet("Banks", "Bank valuation: ROE, cost of equity, growth, book value → justified P/B")
        sh.title("Bank valuation", "Justified P/B = (ROE − g) / (CoE − g), bounded by the engine's floor and cap.")
        r = 4
        A = self.A
        for s in SCENARIOS:
            for name, tr in (self.scen(s).get("leg_inputs") or {}).items():
                if tr.get("kind") != "ggm":
                    continue
                a = tr.get("assumptions") or {}
                sh.section(r, f"{s.upper()} — {name}", 4); r += 1
                items = [("Return on equity (book basis)", _num(a.get("roe_book") or a.get("roe")), PCT2),
                         ("Cost of equity", _num(a.get("coe")), PCT2),
                         ("Long-run growth", _num(a.get("g")), PCT2),
                         ("Book value per share", _num(a.get("bvps")), NUM),
                         ("Scenario band", _num(tr.get("scenario_band")) or 1.0, "0.00")]
                top = r
                for i, (lab, v, fmt) in enumerate(items):
                    sh.label(r + i, 1, lab, indent=1)
                    sh.put(r + i, 3, v, fmt)
                r += len(items)
                lines = [("Justified P/B", f"=MAX({A['pb_floor']},MIN(IFERROR((C{top}-C{top + 2})/(C{top + 1}-C{top + 2}),0),{A['pb_cap']}))", MULT),
                         ("Value per share", f"=ROUND(C{top + 3}*C{r},{A['dp_ggm']})*C{top + 4}", NUM),
                         ("Engine value", _num(tr.get("value")), NUM),
                         ("Check", f"=C{r + 1}-C{r + 2}", NUM)]
                for i, (lab, v, fmt) in enumerate(lines):
                    sh.label(r + i, 1, lab, bold=lab == "Value per share")
                    sh.put(r + i, 3, v, fmt)
                self.leg_cell[(s, name)] = _ref("Banks", r + 1, 3)
                src = a.get("source") or a.get("basis")
                if src:
                    sh.note(r + 4, 1, f"Assumption source: {src}")
                r += 6
        sh.widths({"A": 40, "B": 2, "C": 14})

    # ── Blend ───────────────────────────────────────────────────────────────
    def blend_tab(self) -> None:
        sh = self.sheet("Blend", "Effective weights × leg values → intrinsic value per scenario")
        sh.title("Intrinsic value blend", "Effective weights are each leg's actual share of the blend (sum to 1). "
                                          "No sentiment/quality overlay: the composite was retired on 2026-09-19.")
        r = 4
        for s in SCENARIOS:
            sc = self.scen(s)
            ew = sc.get("effective_weights") or []
            if not ew:
                continue
            sh.section(r, f"{s.upper()} scenario", 6); r += 1
            sh.header(r, ["Leg", "Value from", "Bucket", "Weight", "Leg value", "Contribution"])
            first = r + 1
            for w in ew:
                r += 1
                key = w.get("value_key")
                sh.put(r, 1, w.get("method")).font = Font(color=BLACK)
                sh.put(r, 2, key).font = Font(color=BLACK)
                sh.put(r, 3, w.get("bucket")).font = Font(color=BLACK)
                sh.put(r, 4, _num(w.get("weight")), "0.0000")
                ref = self.leg_cell.get((s, key))
                if ref:
                    sh.put(r, 5, "=" + ref, NUM)
                else:
                    # Not rebuilt anywhere: the engine's unrounded value, flagged on Data Gaps.
                    tr = (sc.get("leg_inputs") or {}).get(key) or {}
                    sh.put(r, 5, _num(tr.get("value")) if tr.get("value") is not None
                           else _num((sc.get("method_iv_table") or {}).get(key)), NUM)
                    self.blend_unlinked.add(key)
                sh.put(r, 6, f"=D{r}*E{r}", NUM)
                self.leg_range.setdefault(key, {})[s] = f"'Blend'!$E${r}"
            last = r
            r += 1
            sh.label(r, 1, "Weights (check = 1)")
            sh.put(r, 4, f"=SUM(D{first}:D{last})", "0.0000")
            sh.label(r, 5, "Blend", bold=True)
            sh.put(r, 6, f"=SUM(F{first}:F{last})", NUM, bold=True)
            r += 1
            sh.label(r, 5, "× Calibration")
            sh.put(r, 6, "=" + self.A["calibration"], "0.0000")
            r += 1
            sh.label(r, 5, "Intrinsic value (published to 2 d.p.)", bold=True)
            sh.put(r, 6, f"=ROUND(F{r - 2}*F{r - 1},{self.A['dp']})", NUM, bold=True)
            iv_row = r
            r += 1
            clamp = sc.get("intrinsic_value_unclamped") is not None
            sh.label(r, 5, "Engine" + (" (ordering clamp applied)" if clamp else ""))
            sh.put(r, 6, _num(sc.get("intrinsic_value")), NUM)
            r += 1
            sh.label(r, 5, "Check" + (" (≠0: clamp)" if clamp else ""))
            sh.put(r, 6, f"=F{iv_row}-F{r - 1}", NUM)
            self.iv_cell[s] = _ref("Blend", r - 1 if clamp else iv_row, 6)
            r += 2
        sh.widths({"A": 30, "B": 22, "C": 8, "D": 10, "E": 26, "F": 14})

    # ── Target ──────────────────────────────────────────────────────────────
    def target_tab(self) -> None:
        sh = self.sheet("Target", "12-month price target bridge and cross-checks")
        sh.title("12-month price target", "Target = spot + capture × (intrinsic value − spot), per scenario, "
                                          "probability-weighted.")
        pb = self.dr.get("pt_bridge") or {}
        A = self.A
        sh.header(4, ["Scenario", "Intrinsic value", "Target", "Engine target", "Check", "Probability",
                      "Status (within tolerance)"])
        targets = self.dr.get("12m_targets") or {}
        for i, s in enumerate(SCENARIOS):
            r = 5 + i
            sh.put(r, 1, s).font = Font(color=BLACK)
            sh.put(r, 2, ("=" + self.iv_cell[s]) if s in self.iv_cell else _num(self.scen(s).get("intrinsic_value")), NUM)
            if pb:
                sh.put(r, 3, f"=ROUND({A['spot']}+{A['capture']}*(B{r}-{A['spot']}),{A['dp']})", NUM)
                sh.put(r, 5, f"=C{r}-D{r}", NUM)
            sh.put(r, 4, _num(targets.get(s)), NUM)
            sh.put(r, 6, "=" + A[f"prob:{s}"], PCT)
            if pb:
                sh.put(r, 7, f'=IF(ABS(E{r})<={A["tol"]},"OK","REVIEW")')
        sh.label(9, 1, "Probability-weighted target", bold=True)
        if pb:
            sh.put(9, 3, f"=ROUND(IFERROR(SUMPRODUCT(C5:C7,F5:F7)/SUM(F5:F7),0),{A['dp']})", NUM, bold=True)
        rec = ((self.data.get("scenario_analysis") or {}).get(self.ticker) or {}).get("reconciliation") or {}
        sh.put(9, 4, _num(rec.get("12m_price_target")), NUM)
        if pb:
            sh.put(9, 5, "=C9-D9", NUM)
            sh.put(9, 7, f'=IF(ABS(E9)<={A["tol"]},"OK","REVIEW")')
        sh.label(10, 1, "Implied return vs spot")
        sh.put(10, 3, f"=IFERROR(C9/{A['spot']}-1,0)" if pb else None, PCT)
        self.target_cell = _ref("Target", 9, 3 if pb else 4)
        cc = pb.get("cross_checks") or {}
        sh.section(12, "Cross-checks (recipes computed, not used for the target)", 6)
        sh.label(13, 1, f"Method: {cc.get('method')}")
        for i, s in enumerate(SCENARIOS):
            sh.put(14 + i, 1, s).font = Font(color=BLACK)
            sh.put(14 + i, 2, _num((cc.get("targets") or {}).get(s)), NUM)
        if (self.dr.get("rating_state") or {}).get("state") == "unrated":
            sh.note(18, 1, f"Unrated — {(self.dr.get('rating_state') or {}).get('reason') or ''}: no target is published.")
        elif not pb:
            sh.note(18, 1, "Run predates the unified target rule: engine targets shown.")
        sh.widths({"A": 30, "B": 16, "C": 14, "D": 14, "E": 10, "F": 12})

    # ── Backtest & flags ────────────────────────────────────────────────────
    def backtest_tab(self) -> None:
        sh = self.sheet("Backtest", "Methodology backtest record, gate ledger and flags")
        sh.title("Methodology backtest (T-1)", "Today's methodology on the previous fiscal year vs the price at "
                                               "that year end; forward score vs the price a year later.")
        rec = self.dr.get("calibration_record") or {}
        r = 4
        for k, v in list(rec.items()) + [(f"forward.{k}", v) for k, v in (rec.get("forward") or {}).items()]:
            if k == "forward":
                continue
            sh.label(r, 1, k, indent=1)
            c = sh.put(r, 2, v if not isinstance(v, (dict, list)) else str(v))
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                c.font = Font(color=BLACK)
            r += 1
        sh.label(r, 1, "Note", indent=1)
        sh.put(r, 2, self.dr.get("calibration_note")).font = Font(color=BLACK)
        r += 2
        sh.section(r, "Gate ledger", 5); r += 1
        sh.header(r, ["Gate", "Metric", "Without", "With", "Applied"])
        for g in self.dr.get("gate_evaluations") or []:
            r += 1
            for j, k in enumerate(("gate_id", "metric", "raw_input_path_a", "gated_output_path_b", "applied")):
                v = g.get(k)
                c = sh.put(r, 1 + j, v if not isinstance(v, (dict, list)) else str(v))
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    c.font = Font(color=BLACK)
        r += 2
        sh.section(r, "Flags (base scenario)", 5)
        for f in self.scen("base").get("forward_flags") or []:
            r += 1
            c = sh.ws.cell(row=r, column=1, value=f)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            sh.ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        # Owner, 2026-10-03 (SBUX review, A8): the build note above stops before the regime
        # overlay; the rate the valuation used is stated with its parts.
        _wb_ = self.dr.get("wacc_build") or {}
        _bb_ = _wb_.get("base_breakdown") or {}
        if isinstance(self.dr.get("wacc"), (int, float)):
            r += 2
            _ov_ = _num(_bb_.get("macro_overlay"))
            sh.ws.cell(row=r, column=1, value=(
                f"WACC used by the valuation {float(self.dr['wacc']):.2%}"
                + (f" = build {float(_wb_['wacc']):.2%}" if isinstance(_wb_.get("wacc"), (int, float)) else "")
                + (f" + macro-regime overlay {_ov_:+.2%}" if _ov_ is not None else "")
                + (f" ({_bb_.get('macro_regime')} regime)" if _bb_.get("macro_regime") else "") + "."))
            sh.ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        sh.widths({"A": 60, "B": 50, "C": 16, "D": 16, "E": 10})

    # ── Data gaps ───────────────────────────────────────────────────────────
    def gaps_tab(self) -> None:
        sh = self.sheet("Data Gaps", "What the institutional format asks for that this run's data "
                                     "or the engine does not provide")
        sh.title("Data gaps", "Detected for this run, then structural gaps between the engine and a "
                              "full three-statement model.")
        gaps = []
        rows = self._stmt_rows()
        if not rows:
            gaps.append(("Statements", "Reported statements could not be loaded for this export",
                         "IS/BS/CFS tabs are empty; valuation tabs unaffected", "High"))
        else:
            years = [str(x.get("period") or "")[:4] for x in rows]
            if len(rows) < 5:
                gaps.append(("Statements", f"Only {len(rows)} annual periods ({', '.join(years)})",
                             "Format asks for 3-5 years of history", "Medium"))
            for x, y in zip(rows, years):
                gp, opx = _num(x.get("gross_profit")), _num(x.get("operating_expense"))
                oth, pt = _num(x.get("other_income_expense")), _num(x.get("pretax_income"))
                if None not in (gp, opx, oth, pt) and abs((gp - opx + oth) - pt) > max(1e6, 0.01 * abs(pt)):
                    gaps.append(("Statements", f"FY{y}: gross profit − opex + other income ≠ reported "
                                 f"pre-tax income by {((gp - opx + oth) - pt) / 1e6:,.0f}m",
                                 "Provider line items do not tie; IS tie-out row shows the gap "
                                 "(valuation unaffected: legs use reported EBIT/EBITDA/net income)", "Low"))
            for key, lab, sev in _GAP_LINES:
                miss = [y for y, x in zip(years, rows) if x.get(key) is None]
                if miss:
                    gaps.append(("Statements", f"{lab} missing for {', '.join(miss)}",
                                 "Line shows blank; dependent subtotals understate", sev))
        legs_any = any(self.scen(s).get("leg_inputs") for s in SCENARIOS)
        untraced = sorted(self.engine_only)
        if not legs_any:
            gaps.append(("Valuation legs", "Run predates leg-input recording",
                         "All legs show engine values only (no live formula)", "High"))
        elif untraced:
            gaps.append(("Valuation legs", "No metric x multiple form: " + ", ".join(untraced),
                         "These legs show the engine value only", "Low"))
        unlinked = sorted(self.blend_unlinked - set(untraced))
        if legs_any and unlinked:
            gaps.append(("Valuation legs", "Blend legs not rebuilt by any tab: " + ", ".join(unlinked),
                         "Blend carries the engine's unrounded value as an input", "Medium"))
        if not self.dr.get("wacc_build"):
            gaps.append(("WACC", "Discount-rate build not recorded (run predates it)",
                         "DCF uses the published per-year rates as inputs", "Medium"))
        for lab, _ref_, basis in getattr(self, "comps_summary", []):
            if "named peers" not in basis:
                gaps.append(("Comps", f"{lab}: no named peer members ({basis})",
                             "Peer median shown without its constituents", "Low"))
        sa = (self.data.get("scenario_analysis") or {}).get(self.ticker) or {}
        if not all(_num((sa.get(s) or {}).get("probability")) for s in SCENARIOS):
            gaps.append(("Target", "Scenario probabilities missing",
                         "Probability-weighted target cannot be rebuilt", "Medium"))
        if not self.dr.get("pt_bridge"):
            gaps.append(("Target", "Run predates the unified 12-month target rule",
                         "Targets shown as engine values", "Medium"))
        st = self.statements or {}
        if rows and not st.get("estimates"):
            gaps.append(("Forecast", "No FMP analyst consensus returned for this ticker",
                         "IS forecast block is empty; the DCF revenue path is still shown", "Medium"))
        elif st.get("estimates"):
            thin = [str(e.get("period_end"))[:4] for e in st["estimates"]
                    if (_num(e.get("analyst_count_revenue")) or 0) and (_num(e.get("analyst_count_revenue")) or 0) < 3]
            if thin:
                gaps.append(("Forecast", f"Consensus from fewer than 3 analysts for {', '.join(thin)}",
                             "Treat those forecast years as indicative", "Low"))
        if rows and st.get("beta") is None:
            gaps.append(("WACC", "No FMP beta returned", "CAPM cross-check cannot be computed", "Low"))
        if not ((self.dr.get("wacc_build") or {}).get("base_breakdown")):
            gaps.append(("WACC", "Base-rate assumption (table, lookup, premia) not recorded for this run",
                         "Base WACC shown as a single input", "Medium"))
        sh.header(4, ["Area", "Gap", "Impact", "Severity"])
        r = 5
        for area, gap, impact, sev in gaps + _STRUCTURAL_GAPS:
            for j, v in enumerate((area, gap, impact, sev)):
                c = sh.ws.cell(row=r, column=1 + j, value=v)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                c.font = Font(bold=(sev == "High" and j == 3))
            r += 1
        sh.widths({"A": 20, "B": 60, "C": 70, "D": 11})

    # ── Summary ─────────────────────────────────────────────────────────────
    def summary_tab(self, sh: _Sheet) -> None:
        sh.title(f"{self.ticker} — Valuation output", f"Currency {self.ccy}. The first value column carries the "
                                                      "run's engine values (static, shown by every viewer); the "
                                                      "'linked' column beside it links to the tab that derives the "
                                                      "figure and recalculates in Excel.")
        A = self.A
        # Owner, 2026-09-27: a phone preview of the JPM workbook showed 0.00 on every
        # Summary line -- openpyxl writes formulas without cached results, so a viewer
        # with no calculation engine renders them as zero. The static engine values sit
        # beside the links; the links stay the audit trail.
        _b = self.dr.get("base") or {}
        _pb = self.dr.get("pt_bridge") or {}
        _spot_v = _pb.get("spot") or self.dr.get("current_price")
        _iv_v = _b.get("intrinsic_value")
        # Owner, 2026-10-03 (SBUX review, A9): the headline target is the probability-weighted one
        # the Target tab links to; the base-case target is its own, named row below.
        _base_tgt_v = ((self.dr.get("12m_targets") or {}).get("base")
                       or ((_pb.get("scenarios") or {}).get("base") or {}).get("target"))
        _rec_sum = ((self.data.get("scenario_analysis") or {}).get(self.ticker) or {}).get("reconciliation") or {}
        _tgt_v = _rec_sum.get("12m_price_target") or _base_tgt_v
        _ret_v = (float(_tgt_v) / float(_spot_v) - 1.0) if _tgt_v and _spot_v else None
        _tables = {s: ((self.dr.get(s) or {}).get("method_iv_table") or {}) for s in SCENARIOS}
        _ivs = {s: (self.dr.get(s) or {}).get("intrinsic_value") for s in SCENARIOS}
        r = 4
        sh.section(r, "Headline", 6); r += 1
        sh.put(r - 1, 2, "engine value").font = Font(color=BLACK, italic=True)
        sh.put(r - 1, 3, "linked (recalculates in Excel)").font = Font(color=BLACK, italic=True)
        # Owner, 2026-09-26: an Unrated name publishes no headline IV and no
        # target; the rebuilt blend stays on its own tab as an indicative figure.
        _rs = self.dr.get("rating_state") or {}
        _unrated = _rs.get("state") == "unrated"
        _hl = (("Share price", "=" + A["spot"], NUM),
               ("Intrinsic value — base", f"N/A — Unrated: {_rs.get('reason') or ''}" if _unrated
                else (("=" + self.iv_cell["base"]) if "base" in self.iv_cell else None), None if _unrated else NUM),
               ("12-month target (probability-weighted)", "N/A — Unrated" if _unrated
                else (("=" + self.target_cell) if self.target_cell else None), None if _unrated else NUM),
               ("Implied return to target", "N/A" if _unrated else f"=IFERROR(C{r + 2}/C{r}-1,0)", None if _unrated else PCT))
        _hl_static = (_spot_v, None if _unrated else _iv_v, None if _unrated else _tgt_v, None if _unrated else _ret_v)
        for (lab, v, fmt), sv in zip(_hl, _hl_static):
            sh.label(r, 1, lab, bold=True)
            if isinstance(sv, (int, float)):
                sh.put(r, 2, float(sv), fmt, bold=True).font = Font(color=BLUE, bold=True)
            elif isinstance(v, str) and not v.startswith("="):
                sh.put(r, 2, v, fmt, bold=True)                     # the Unrated "N/A" text
            sh.put(r, 3, v, fmt)
            r += 1
        if not _unrated:
            sh.label(r, 1, "Base-case target (spot + capture × (IV − spot))")
            if isinstance(_base_tgt_v, (int, float)):
                sh.put(r, 2, float(_base_tgt_v), NUM).font = Font(color=BLUE)
            sh.put(r, 3, "='Target'!$C$6" if _pb else None, NUM)
            r += 1
        # Owner, 2026-10-03 (interactive agent): the headline figures above carry a user's estimate override.
        _ovr = (self.dr or {}).get("estimate_override") or {}
        if _ovr:
            _b, _a = (_ovr.get("before") or {}), (_ovr.get("after") or {})
            sh.label(r, 1, f"USER ESTIMATE OVERRIDE ({str(_ovr.get('created_at') or '')[:10]}): " + ", ".join(_ovr.get("fields") or [])
                           + (f" — {_ovr.get('note')}" if _ovr.get("note") else "")
                           + (f"; agent's base IV {float(_b['intrinsic_value']):,.2f} → {float(_a['intrinsic_value']):,.2f}"
                              if isinstance(_b.get("intrinsic_value"), (int, float)) and isinstance(_a.get("intrinsic_value"), (int, float)) else "")
                           + ". Details on the Guidance tab.", bold=True)
            r += 1
        sh.label(r, 1, "Profile / anchor")
        sh.put(r, 2, f"{self.dr.get('profile')} / {self.dr.get('anchor_method')}").font = Font(color=BLACK)
        r += 2
        # Owner, 2026-09-27: the key metrics the profile's report FAMILY is judged on,
        # read from the run's per-year raw financials (static values, input colour).
        try:
            from src.data.report_families import report_family_for, family_rows, format_value
            _fam = report_family_for(self.dr.get("profile"))
            _fys, _frows = family_rows(((self.run.get("data") or {}).get("raw_financials")), _fam, years=3)
        except Exception:                                  # noqa: BLE001
            _fam, _fys, _frows = None, [], []
        if _frows:
            sh.section(r, f"Key metrics ({_fam})", 6); r += 1
            sh.header(r, ["Metric"] + [str(fy) for fy in _fys]); r += 1
            for label, kind, vals in _frows:
                sh.label(r, 1, label, indent=1)
                for j, v in enumerate(vals):
                    if v is None:
                        sh.put(r, 2 + j, "n/a").font = Font(color=BLUE)
                    else:
                        sh.put(r, 2 + j, float(v) / (1e9 if kind == "bn" else 1.0),
                               {"bn": '#,##0.0"B"', "ps": NUM, "pct": PCT, "x": '0.00"x"'}[kind]).font = Font(color=BLUE)
                r += 1
            r += 1
        # Football field: bear–bull range per leg, plus DCF and the blend.
        sh.section(r, "Football field (value per share, bear to bull)", 6); r += 1
        sh.header(r, ["Method", "Low", "High", "Range", "Base", "Low (linked)", "High (linked)", "Range (linked)", "Base (linked)"])
        ff_first = r + 1
        entries = []
        for leg, by in self.leg_range.items():
            if all(s in by for s in SCENARIOS):
                entries.append((leg, by))
        if "base" in self.iv_cell:
            entries.append(("Blended intrinsic value", {s: self.iv_cell[s] for s in SCENARIOS if s in self.iv_cell}))
        for name, by in entries:
            r += 1
            sh.put(r, 1, name).font = Font(color=BLACK)
            refs = [by[s] for s in SCENARIOS if s in by]
            sh.put(r, 6, "=MIN(" + ",".join(refs) + ")", NUM)
            sh.put(r, 7, "=MAX(" + ",".join(refs) + ")", NUM)
            sh.put(r, 8, f"=G{r}-F{r}", NUM)
            sh.put(r, 9, ("=" + by["base"]) if "base" in by else None, NUM)
            _vals = ([_ivs[s] for s in SCENARIOS] if name == "Blended intrinsic value"
                     else [_tables[s].get(name) for s in SCENARIOS])
            _vals = [float(x) for x in _vals if isinstance(x, (int, float))]
            if _vals:
                for col, x in ((2, min(_vals)), (3, max(_vals)), (4, max(_vals) - min(_vals))):
                    sh.put(r, col, x, NUM).font = Font(color=BLUE)
                _bv = (_ivs.get("base") if name == "Blended intrinsic value" else _tables["base"].get(name))
                if isinstance(_bv, (int, float)):
                    sh.put(r, 5, float(_bv), NUM).font = Font(color=BLUE)
        ff_last = r
        if ff_last >= ff_first:
            chart = BarChart()
            chart.type = "bar"
            chart.grouping = "stacked"
            chart.overlap = 100
            chart.title = "Valuation range by method"
            chart.y_axis.title = f"Value per share ({self.ccy})"
            data = Reference(sh.ws, min_col=2, max_col=2, min_row=ff_first - 1, max_row=ff_last)
            rng = Reference(sh.ws, min_col=4, max_col=4, min_row=ff_first - 1, max_row=ff_last)
            cats = Reference(sh.ws, min_col=1, min_row=ff_first, max_row=ff_last)
            chart.add_data(data, titles_from_data=True)
            chart.add_data(rng, titles_from_data=True)
            chart.set_categories(cats)
            chart.series[0].graphicalProperties.noFill = True
            chart.series[0].graphicalProperties.line.noFill = True
            chart.legend = None
            chart.height, chart.width = 8, 18
            sh.ws.add_chart(chart, f"K4")
        r += 2
        sh.section(r, "DCF summary (base)", 6); r += 1
        d = self.dcf.get("base")
        if d:
            # static engine values from the DCF leg trace (per-share PVs times the share count)
            _li = ((self.dr.get("base") or {}).get("leg_inputs") or {}).get("DCF") or {}
            _sh_n = _li.get("shares") if isinstance(_li.get("shares"), (int, float)) and _li.get("shares") else None
            _pvf = (_li.get("pv_fcf_per_share") or 0.0) * _sh_n if _sh_n and isinstance(_li.get("pv_fcf_per_share"), (int, float)) else None
            _pvt = (_li.get("pv_tv_per_share") or 0.0) * _sh_n if _sh_n and isinstance(_li.get("pv_tv_per_share"), (int, float)) else None
            _ev = (_pvf + _pvt) if (_pvf is not None and _pvt is not None) else None
            _nd = _li.get("net_debt") if isinstance(_li.get("net_debt"), (int, float)) else None
            _mi = float(_li.get("minority_interest") or 0.0) if isinstance(_li.get("minority_interest"), (int, float)) else 0.0
            _pe = float(_li.get("preferred_equity") or 0.0) if isinstance(_li.get("preferred_equity"), (int, float)) else 0.0
            _eq = (_ev - _nd - _mi - _pe) if (_ev is not None and _nd is not None) else None
            # Owner, 2026-10-03 (Alibaba review): the static column carries the SAME sign as the linked
            # "Less:" cells (the engine column had shown +189bn against the link's -189bn).
            _static = {"pv_fcf": _pvf, "pv_tv": _pvt, "ev": _ev, "nd": (-_nd if _nd is not None else None), "mi": -_mi, "pe": -_pe, "eq": _eq,
                       "iv": _li.get("value") if isinstance(_li.get("value"), (int, float)) else None}
            for lab, key in (("PV of forecast FCF", "pv_fcf"), ("PV of terminal value", "pv_tv"),
                             ("Enterprise value", "ev"),
                             (f"Less: net debt — valuation basis (balance sheet {((self.dr.get('financials_used') or {}).get('balance_sheet_period') or 'latest')}, leases excluded)", "nd"),
                             ("Less: minority interest", "mi"), ("Less: preferred equity", "pe"),
                             ("Equity value", "eq"),
                             ("DCF value per share", "iv")):
                sh.label(r, 1, lab, indent=1)
                if isinstance(_static.get(key), (int, float)):
                    sh.put(r, 2, float(_static[key]), NUM if key == "iv" else BIG).font = Font(color=BLUE)
                sh.put(r, 3, "=" + d[key], NUM if key == "iv" else BIG)
                r += 1
        else:
            sh.note(r, 1, "No DCF leg recorded for this run."); r += 1
        r += 1
        sh.section(r, "Comps (peer multiples used)", 6); r += 1
        _mu = ((self.dr.get("multiples_used") or {}).get("fields") or {})
        _mu_key = {"EV/EBITDA": "ev_ebitda", "P/E": "pe", "EV/Revenue": "ev_revenue", "P/B": "pb",
                   "P/BV": "pb", "FCF yield": "fcf_yield", "FCF Yield": "fcf_yield", "EV/EBIT": "ev_ebit"}
        for lab, ref, basis in getattr(self, "comps_summary", []):
            sh.label(r, 1, lab, indent=1)
            _v = (_mu.get(_mu_key.get(str(lab).strip(), "")) or {}).get("value")
            if isinstance(_v, (int, float)):
                sh.put(r, 2, float(_v), "0.00").font = Font(color=BLUE)
            sh.put(r, 3, "=" + ref, "0.00")
            sh.put(r, 4, basis).font = Font(color=BLACK)
            r += 1
        r += 1
        sh.section(r, "Target derivation (base)", 6); r += 1
        _cap_v = _pb.get("capture")
        _bt_v = ((_pb.get("scenarios") or {}).get("base") or {}).get("target")
        for lab, v, fmt, sv in (("Spot", "=" + A["spot"], NUM, _spot_v),
                                ("Capture", "=" + A["capture"], PCT, _cap_v),
                                ("Intrinsic value — base", ("=" + self.iv_cell["base"]) if "base" in self.iv_cell else None, NUM, _iv_v),
                                ("Base target = spot + capture × (IV − spot)", "='Target'!$C$6", NUM, _bt_v),
                                ("Probability-weighted target", ("=" + self.target_cell) if self.target_cell else None, NUM, _tgt_v)):
            sh.label(r, 1, lab, indent=1)
            if isinstance(sv, (int, float)):
                sh.put(r, 2, float(sv), fmt).font = Font(color=BLUE)
            sh.put(r, 3, v, fmt)
            r += 1
        # Owner rule 2 (2026-09-23): a constant running under
        # OWNER_OVERRIDE_PENDING is stated here with the leg's sensitivity.
        pend = [(leg, tr["owner_override"]) for leg, tr in ((self.dr.get("base") or {}).get("leg_inputs") or {}).items()
                if isinstance(tr, dict) and isinstance(tr.get("owner_override"), dict)]
        if pend:
            r += 1
            sh.section(r, "Owner overrides pending", 6); r += 1
            for leg, o in pend:
                lo, hi = o.get("interval") or (None, None)
                sh.label(r, 1, f"{leg}: PEG {o.get('peg')} [{o.get('status')}]", indent=1)
                sh.put(r, 2, o.get("leg_at_low"), NUM)
                sh.put(r, 3, o.get("leg_at_high"), NUM)
                sh.note(r, 4, f"leg at {lo}x / {hi}x; baseline runs at {o.get('peg')}x until signed off")
                r += 1
        sh.widths({"A": 44, "B": 16, "C": 18, "D": 12, "E": 12, "F": 13, "G": 13, "H": 13, "I": 13})


def _col(letter: str) -> int:
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letter)


def build_workbook(run: dict, ticker: str,
                   load_members: Optional[Callable[..., list]] = None,
                   load_statements: Optional[Callable[..., dict]] = None,
                   meta: Optional[dict] = None) -> bytes:
    """Workbook bytes for one ticker of one run payload ({"data": {...}}).

    Raises KeyError when the run carries no valuation for `ticker`.
    """
    dr = ((run.get("data") or {}).get("dcf_range") or {}).get(ticker)
    if not dr:
        raise KeyError(ticker)
    return _Book(ticker, run, dr, load_members=load_members,
                 load_statements=load_statements, meta=meta).build()

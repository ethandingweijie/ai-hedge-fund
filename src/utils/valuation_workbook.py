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


def _driver(tr: dict, key: str) -> Any:
    """A DCF driver from a leg trace. The two timing drivers (owner, 2026-10-04, D2) come from the
    trace's `timing`: a dated trace discounts each year's flows at the middle of their window and
    carries the value from the balance-sheet date to the valuation date; an undated one (older runs,
    the T-1 backtest) discounts whole years at year end with no carry."""
    if key == "disc_point":
        return 0.5 if (tr.get("timing") or {}).get("flow_fractions") else 1.0
    if key == "roll_forward_years":
        return (tr.get("timing") or {}).get("roll_forward_years") or 0.0
    if key == "terminal_loe_multiplier":                   # plan I14: 1 when no LOE input priced
        v = tr.get(key)
        return float(v) if isinstance(v, (int, float)) else (1.0 if tr else None)
    v = tr.get(key)
    if key == "margin_delta_absolute" and v is None and tr:
        return 0.0
    return v


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

    def front_page_flags(self) -> list[str]:
        """Owner, 2026-10-04 (plan 1A.6, 1E.3, 1F.1): what the front page must say about this run -- a
        DCF left out of the blend, an anchor leg that did not compute, a margin the engine replaced, a
        forecast check that failed. Every line reads off the run's own record."""
        out: list[str] = []
        if getattr(self, "dcf_excluded", None):
            out.append(self.dcf_excluded)
        ad = self.dr.get("anchor_degraded") or self.scen("base").get("anchor_degraded") or {}
        if ad:
            out.append(f"DEGRADED: the anchor leg {ad.get('method')} did not compute ({ad.get('reason')}); its "
                       f"{float(ad.get('weight') or 0):.0%} weight went to {ad.get('moved_to') or 'the remaining legs'}.")
        else:
            for f in (self.scen("base").get("forward_flags") or []):
                if str(f).startswith("DEGRADED:"):
                    out.append(str(f))
            for d in (self.scen("base").get("legs_dropped") or []):
                if d.get("method") == self.dr.get("anchor_method"):
                    out.append(f"DEGRADED: the anchor leg {d.get('method')} did not compute ({d.get('reason')}); "
                               f"its {float(d.get('weight') or 0):.0%} weight was spread over the other legs.")
        for g in self.dr.get("gate_evaluations") or []:
            if g.get("gate_id") == "GATE_OE_CASCADE" and g.get("applied"):
                a, b = _num(g.get("raw_input_path_a")), _num(g.get("gated_output_path_b"))
                out.append("FCF MARGIN REPLACED: the trailing owner-earnings margin "
                           + (f"{a:.1%}" if a is not None else "n/a") + " is not positive; the DCF uses "
                           + (f"{b:.1%}" if b is not None else "n/a") + f" ({g.get('basis')}).")
        for inv in ((self.dr.get("guidance_forecast") or {}).get("invariants") or []):
            if inv.get("ok") is False:
                out.append(f"FORECAST CHECK FAILED — {inv.get('name')}: {inv.get('detail')}")
        return out

    def _net_debt_label(self) -> str:
        """Owner, 2026-10-04 (plan 1A.3): the valuation's net debt with the basis the engine recorded --
        its date, whether short-term investments count as cash, and the lease treatment. The old label
        said "leases excluded" while FMP's total debt carried them."""
        fu = self.dr.get("financials_used") or {}
        b = fu.get("net_debt_basis") or {}
        date_ = b.get("balance_sheet_date") or fu.get("balance_sheet_period") or "latest"
        if not b:
            return f"Less: net debt — valuation basis (balance sheet {date_}; leases as in FMP total debt)"
        return (f"Less: net debt — valuation basis (balance sheet {date_}; short-term investments "
                f"{'counted as cash' if b.get('short_term_investments_netted') else 'not netted'}; "
                + (f"long-term marketable securities {float(b['long_term_investments_netted']) / 1e6:,.0f}m counted as cash; "
                   if b.get("long_term_investments_netted") else "")
                + f"{b.get('accounting_basis')}: leases {b.get('leases')})")

    def dcf_weighted(self) -> bool:
        """True when any DCF-family leg (kind "dcf") carries weight in the blend."""
        return any(tr.get("kind") == "dcf" and self.in_blend(name)
                   for sc in SCENARIOS for name, tr in (self.scen(sc).get("leg_inputs") or {}).items())

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
        # Owner, 2026-10-03: the Model tab is built before the statements so their FY+1E..FY+5E
        # columns can link to its rows; it is moved to sit after the CFS tab below.
        self.model_rows: dict = {}
        self.model_cols: list = []
        self.model_kind: Optional[str] = None
        self.model_tab()
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
        if "Model" in self.wb.sheetnames and "CFS" in self.wb.sheetnames:
            _ws = self.wb["Model"]
            _sheets = self.wb._sheets                      # the explicit order: Model directly after CFS
            _sheets.remove(_ws)
            _sheets.insert(_sheets.index(self.wb["CFS"]) + 1, _ws)
            _mt = next((t for t in self.tabs if t[0] == "Model"), None)
            if _mt:
                self.tabs.remove(_mt)
                self.tabs.insert(next(i for i, t in enumerate(self.tabs) if t[0] == "CFS") + 1, _mt)
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
        # Owner, 2026-10-04 (plan 1A.6): the company name from the run or the FMP profile; the
        # valuation date as a date value (a DATE() formula showed as 46299 in viewers that do not
        # calculate).
        rows = [("Ticker", self.ticker),
                ("Company", self.data.get("company_name") or self.dr.get("company_name")
                 or (self.statements or {}).get("company_name") or ""),
                ("Run ID", self.run.get("run_id") or self.meta.get("run_id") or ""),
                ("Valuation date", datetime(run_at.year, run_at.month, run_at.day)),
                ("Model version", self.dr.get("param_version") or self.dr.get("_engine_cache_version") or ""),
                ("Prepared by", "AI Hedge Fund valuation engine (automated)"),
                ("Currency", f"{self.ccy}; statements in millions unless stated")]
        for i, (k, v) in enumerate(rows):
            sh.label(4 + i, 1, k, bold=True)
            c = sh.ws.cell(row=4 + i, column=2, value=v)
            if k == "Valuation date":
                c.number_format = "yyyy-mm-dd"
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

        self.dcf_variants: dict[str, dict[str, dict]] = {}
        # Owner, 2026-10-04 (decision D1): a DCF the blend does not weight is not an output. Its
        # drivers and its tab are left out; the Summary states, in one line, that it was excluded.
        if self.dcf_weighted():
            sh.section(r, "Scenario drivers", 8); r += 1
            sh.header(r, ["Driver", "", "Bear", "Base", "Bull"]); r += 1
            drivers = [("revenue_base", "Revenue base (last actual, valuation currency)", BIG),
                       ("fcf_margin_base", "FCF margin base (unlevered owner earnings = owner-earnings FCF + after-tax interest; levered owner earnings for banks, insurers, fee financials other than payment networks, and property)", PCT2),
                       ("margin_delta_absolute", "Scenario margin change (absolute)", PCT2),
                       ("fcf_floor", "FCF margin floor", PCT2),
                       ("tgr", "Terminal growth", PCT2),
                       ("net_debt", "Net debt (valuation currency)", BIG),
                       ("shares", "Shares outstanding", BIG),
                       ("disc_point", "Discount point within each year's cash-flow window (0.5 = mid-year; 1 = year end)", "0.00"),
                       ("roll_forward_years", "Years from the balance-sheet date to the valuation date (value carried at the year-1 rate)", "0.0000"),
                       ("terminal_loe_multiplier", "Terminal value kept after loss of exclusivity beyond the horizon (1 = no LOE input)", "0.0000")]
            for key, lab, fmt in drivers:
                sh.label(r, 1, lab, indent=1)
                for j, s in enumerate(SCENARIOS):
                    tr = (self.scen(s).get("leg_inputs") or {}).get("DCF") or {}
                    sh.put(r, 3 + j, _num(_driver(tr, key)) if tr else None, fmt)
                    self.A[f"{key}:{s}"] = _ref("Assumptions", r, 3 + j)
                r += 1
            _tm = ((self.scen("base").get("leg_inputs") or {}).get("DCF") or {}).get("timing") or {}
            if _tm:
                sh.note(r, 1, f"Dating: fiscal year {_tm.get('fy0_end')} is the last actual; net debt is struck at "
                              f"{_tm.get('balance_sheet_date')}, so each projection year counts only its flows after that "
                              f"date; valued {_tm.get('valuation_date')}.")
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
            # Owner, 2026-10-04 (plan 1A.1): the margin the engine projected, year by year. A guidance forecast,
            # an FCF-guidance fade or a turnaround path sets it per year; rebuilding a flat base + change put a
            # different projection in the workbook from the one the engine valued (09618.HK 229.44 vs 157.14).
            sh.label(r, 1, "FCF margin by projection year (as projected: guidance path, fade, floor and cap applied)", bold=True); r += 1
            sh.header(r, ["Year", "", "Bear", "Base", "Bull"]); r += 1
            for t in range(10):
                sh.label(r, 1, f"Year {t + 1}", indent=1)
                for j, s in enumerate(SCENARIOS):
                    rows = ((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("projection_rows") or []
                    sh.put(r, 3 + j, _num(rows[t].get("fcf_margin")) if t < len(rows) else None, PCT2)
                    self.A[f"m{t + 1}:{s}"] = _ref("Assumptions", r, 3 + j)
                r += 1
            sh.label(r, 1, "Share of each projection year's cash flow after the balance-sheet date", bold=True); r += 1
            sh.header(r, ["Year", "", "Bear", "Base", "Bull"]); r += 1
            for t in range(10):
                sh.label(r, 1, f"Year {t + 1}", indent=1)
                for j, s in enumerate(SCENARIOS):
                    rows = ((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("projection_rows") or []
                    sh.put(r, 3 + j, (_num(rows[t].get("flow_fraction")) if rows[t].get("flow_fraction") is not None else 1.0)
                           if t < len(rows) else None, "0.0000")
                    self.A[f"f{t + 1}:{s}"] = _ref("Assumptions", r, 3 + j)
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
                        sh.put(r, 3 + j, _num(_driver(tr, key)), fmt)
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
                for t in range(n_max):
                    sh.label(r, 1, f"FCF margin, year {t + 1} (as projected)", indent=1)
                    for j, s_ in enumerate(SCENARIOS):
                        rows = (own.get(s_) or {}).get("projection_rows") or []
                        if t < len(rows):
                            sh.put(r, 3 + j, _num(rows[t].get("fcf_margin")), PCT2)
                            self.A[f"m{t + 1}:{s_}{sfx}"] = _ref("Assumptions", r, 3 + j)
                    r += 1
                for t in range(n_max):
                    sh.label(r, 1, f"Cash-flow share after the balance-sheet date, year {t + 1}", indent=1)
                    for j, s_ in enumerate(SCENARIOS):
                        rows = (own.get(s_) or {}).get("projection_rows") or []
                        if t < len(rows):
                            _f = rows[t].get("flow_fraction")
                            sh.put(r, 3 + j, _num(_f) if _f is not None else 1.0, "0.0000")
                            self.A[f"f{t + 1}:{s_}{sfx}"] = _ref("Assumptions", r, 3 + j)
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
        # Owner, 2026-10-04 (plan 1A.4): the rate is stated with the rule that set it.
        add("capture", "Share of the IV gap closed in 12 months (capture)"
            + (f" — {pb.get('capture_reason')}" if pb.get("capture_reason") else
               f" — source: {pb.get('capture_source')}" if pb.get("capture_source") else ""),
            _num(pb.get("capture")), PCT)
        sa = (self.data.get("scenario_analysis") or {}).get(self.ticker) or {}
        for s in SCENARIOS:
            add(f"prob:{s}", f"Scenario probability — {s}", _num((sa.get(s) or {}).get("probability")), PCT)
        add("dp", "Published rounding (decimal places) for IV and targets", 2, "0")
        add("dp_ggm", "GGM value rounding (decimal places, engine)", 4, "0")
        add("tol", "Reconciliation tolerance (one rounding unit)", 0.01, NUM)
        add("dp_check", "Check precision (decimal places; floating-point residue below it reads zero)", 6, "0")
        add("dp_comps", "Recorded peer-median precision (decimal places, engine)", 4, "0")
        add("calibration", "Calibration multiplier (market bias correction)",
            _num((self.dr.get("calibration") or {}).get("iv_multiplier")) or 1.0, "0.0000")
        r += 1
        sh.section(r, "Discount-rate constants", 6); r += 1
        bb = (self.dr.get("wacc_build") or {}).get("base_breakdown") or {}
        add("lev_threshold", "Leverage premium threshold (leverage as on the WACC tab)",
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
        # Owner, 2026-10-03 (Anta): FY+1E..FY+5E are filled on the reported rows themselves -- inputs
        # link to the Model tab, the tab's own formulas extend across the forecast columns.
        _fc_cols = list(range(last + 1, last + 1 + n_fc)) if self.model_rows else []
        _op = self.model_kind == "operating"
        _is_map = ({"revenue": "rev", "cost_of_revenue": "cogs", "depreciation_and_amortization": "da_assump", "interest_expense": "intexp",
                    "other_income_expense": "intinc", "pretax_income": "pretax", "income_tax_expense": "tax", "net_income": "ni", "shares_outstanding": "shares"}
                   if _op else {"revenue": "total_income", "operating_expense": "operating_expenses", "pretax_income": "pretax", "income_tax_expense": "tax",
                                "net_income": "net_income", "shares_outstanding": "shares"})

        def _link(rr: int, key: str) -> None:
            mk = _is_map.get(key)
            if _fc_cols and mk and (self.model_rows or {}).get(mk):
                for k, c in enumerate(_fc_cols):
                    sh.put(rr, c, f"='Model'!{self.model_cols[k]}{self.model_rows[mk]}", MIL)

        def inrow(key: str, label: str, sign: float = 1.0, indent: int = 0) -> int:
            nonlocal r
            sh.label(r, 1, label, indent=indent)
            sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                v = _num(row.get(key))
                sh.put(r, c0 + i, None if v is None else sign * v / 1e6, MIL)
            _link(r, key)
            r += 1
            return r - 1

        def frow(label: str, formula: Callable[[str], str], fmt: str = MIL, bold=False,
                 typ: str = "Formula", cols: Optional[range] = None, extend: bool = True, link: Optional[str] = None) -> int:
            """`extend`: the formula also runs across the forecast columns; `link`: those columns link to a Model row instead."""
            nonlocal r
            sh.label(r, 1, label, bold=bold)
            sh.label(r, 2, typ)
            for c in (cols or range(c0, last + 1)):
                sh.put(r, c, formula(get_column_letter(c)), fmt, bold=bold)
            if _fc_cols and link and (self.model_rows or {}).get(link):
                for k, c in enumerate(_fc_cols):
                    sh.put(r, c, f"='Model'!{self.model_cols[k]}{self.model_rows[link]}", fmt, bold=bold)
            elif _fc_cols and extend and not cols:
                for c in _fc_cols:
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
        ebit = frow("EBIT (operating income)", lambda L: f"={L}{gp}+{L}{opx}", bold=True, link=("ebit" if _op else "pre_provision_profit"))
        if _fc_cols and _op:
            # the reported total includes D&A; in the forecast it is EBIT less gross profit, so the row ties to the model's EBIT
            for c in _fc_cols:
                L = get_column_letter(c)
                sh.put(opx, c, f"={L}{ebit}-{L}{gp}", MIL)
        frow("EBIT margin (%)", lambda L: f"=IFERROR({L}{ebit}/{L}{rev},0)", PCT)
        da = inrow("depreciation_and_amortization", "Depreciation & amortisation")
        ebitda = frow("EBITDA", lambda L: f"={L}{ebit}+{L}{da}", bold=True)
        frow("EBITDA margin (%)", lambda L: f"=IFERROR({L}{ebitda}/{L}{rev},0)", PCT)
        ie = inrow("interest_expense", "Interest expense", -1.0)
        oth = inrow("other_income_expense", "Other income / (expense), incl. interest income")
        ebt = frow("EBT (pre-tax income)", lambda L: f"={L}{ebit}+{L}{oth}", bold=True, link="pretax")
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
        if _fc_cols:
            sh.note(5, last + 1, "FY+1E..FY+5E: the three-statement model (Model tab), linked line by line; blank = not modelled")
        self.is_rows = {"rev": rev, "ni": ni, "da": da, "last": last}
        # ── Forecast: FMP consensus (the estimates the forward legs use) and
        # the DCF's own revenue path, side by side. ──
        r += 1
        sh.section(r, "Forecast — FMP analyst consensus (average)", last + n_fc); r += 1
        # Owner, 2026-10-04 (plan 1B.4): a consensus year is matched to the forecast column by its
        # fiscal year-END DATE (within 45 days), not by a year label -- the dating rule of 6130d6e2.
        _ests = [(e, _parse_date(e.get("period_end"))) for e in (st.get("estimates") or [])]
        est: dict = {}
        fc_years = []
        last_d = _parse_date(rows[-1].get("period"))
        for k in range(n_fc):
            if not last_d:
                fc_years.append("")
                continue
            try:
                _end = last_d.replace(year=last_d.year + 1 + k)
            except ValueError:
                _end = last_d.replace(year=last_d.year + 1 + k, day=28)
            _y = str(_end.year)
            fc_years.append(_y)
            _hit = [e for e, d_ in _ests if d_ and abs((d_ - _end).days) <= 45]
            if _hit:
                est[_y] = _hit[0]

        def crow(key, label, sign=1.0, per_share=False):
            nonlocal r
            sh.label(r, 1, label); sh.label(r, 2, "Consensus")
            for k, y in enumerate(fc_years):
                v = _num((est.get(y) or {}).get(key))
                v = None if v == 0 else v                     # FMP zero-fills uncovered years: blank, not 0%
                sh.put(r, last + 1 + k, None if v is None else (sign * v if per_share else sign * v / 1e6),
                       NUM if per_share else MIL)
            r += 1
            return r - 1

        c_rev = crow("revenue_avg", "Revenue")
        sh.label(r, 1, "YoY growth (%)"); sh.label(r, 2, "Formula")
        for k in range(n_fc):
            L, P = get_column_letter(last + 1 + k), get_column_letter(last + k)
            prev = f"{P}{rev}" if k == 0 else f"{P}{c_rev}"
            # a blank consensus year stays blank (it read -100%)
            sh.put(r, last + 1 + k, f'=IF(OR({L}{c_rev}="",{prev}=""),"",IFERROR({L}{c_rev}/{prev}-1,""))', PCT)
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
                sh.put(r, last + 1 + k, f'=IF(OR({L}{num}="",{L}{c_rev}=""),"",IFERROR({L}{num}/{L}{c_rev},""))', PCT)
            r += 1
        r += 1
        if self.model_rows and self.model_kind == "bank":
            # a bank's income statement has lines the reported layout does not: shown here, linked
            sh.section(r, "Forecast — bank earnings-and-capital model (Model tab), linked", last + n_fc); r += 1
            for _lab, _key in (("Net interest income", "net_interest_income"), ("Non-interest income", "fee_income"), ("Provisions", "provisions"), ("Dividend per share", "dividend_per_share")):
                sh.label(r, 1, _lab); sh.label(r, 2, "Link")
                if self._model_link(sh, r, _key, last + 1, NUM if _key == "dividend_per_share" else MIL):
                    r += 1
            r += 1
        elif not self.model_rows:
            self._model_note(sh, r, last + 1); r += 2
        sh.section(r, "Forecast — engine DCF base case (what the valuation projects)", last + n_fc); r += 1
        dcf_rev = r
        sh.label(r, 1, "Revenue (DCF base, statement currency)"); sh.label(r, 2, "Link"); r += 1
        sh.label(r, 1, "Variance vs consensus revenue (%)"); sh.label(r, 2, "Formula")
        for k in range(n_fc):
            L = get_column_letter(last + 1 + k)
            # Plan E33 (2026-10-06; Sheng Siong / BLK): no DCF path (the DCF carries no weight) reads blank,
            # not -100%.
            sh.put(r, last + 1 + k, f'=IF(OR({L}{c_rev}="",{L}{dcf_rev}="",{L}{dcf_rev}=0),"",IFERROR({L}{dcf_rev}/{L}{c_rev}-1,""))', PCT)
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
        n_fc = 5 if self.model_rows else 0
        last = self._period_headers(sh, 4, rows, n_fc)
        c0, r = 3, 6
        _fc0 = last + 1
        # Owner, 2026-10-03 (Anta): the forecast columns are the Model tab's balance sheet, linked.
        _bs_map = ({"cash_and_equivalents": "cash", "short_term_investments": "sti", "accounts_receivable": "bs_ar", "inventory": "bs_inv", "oca": "bs_oca", "tca": "tca",
                    "property_plant_equipment": "bs_ppe", "goodwill_plus_intangibles": "gi", "onca": "onca", "ta": "ta", "accounts_payable": "bs_ap", "short_term_debt": "std",
                    "ocl": "ocl", "long_term_debt": "ltd", "oncl": "oncl", "tl": "tl", "shareholders_equity": "eq", "minority_interest": "mi", "tle": "tle", "chk": "chk", "td": None, "nd": "nd"}
                   if self.model_kind == "operating" else {"total_assets": "total_assets", "shareholders_equity": "equity", "ta": "total_assets", "minority_interest": None})

        def _fc(rr, key):
            if n_fc and _bs_map.get(key):
                self._model_link(sh, rr, _bs_map[key], _fc0)

        def inrow(key, label, indent=1):
            nonlocal r
            sh.label(r, 1, label, indent=indent); sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                sh.put(r, c0 + i, _mil(row.get(key)), MIL)
            _fc(r, key)
            r += 1
            return r - 1

        def frow(label, f, bold=False, typ="Formula", key=None):
            nonlocal r
            sh.label(r, 1, label, bold=bold); sh.label(r, 2, typ)
            for c in range(c0, last + 1):
                sh.put(r, c, f(get_column_letter(c)), MIL, bold=bold)
            if key:
                _fc(r, key)
            r += 1
            return r - 1

        if n_fc:
            sh.note(5, _fc0, "FY+1E..FY+5E: the three-statement model's balance sheet (Model tab), linked")
        sh.section(r, "Assets", last + n_fc); r += 1
        cash = inrow("cash_and_equivalents", "Cash & equivalents")
        sti = inrow("short_term_investments", "Short-term investments")
        ar = inrow("accounts_receivable", "Accounts receivable")
        inv = inrow("inventory", "Inventory")
        tca = inrow("current_assets", "Total current assets (reported)", 0)
        oca = frow("  Other current assets", lambda L: f"={L}{tca}-SUM({L}{cash}:{L}{inv})", key="oca")
        ppe = inrow("property_plant_equipment", "Property, plant & equipment")
        gw = inrow("goodwill_plus_intangibles", "Goodwill & intangibles")
        ta_rep = inrow("total_assets", "Total assets (reported)", 0)
        onca = frow("  Other non-current assets", lambda L: f"={L}{ta_rep}-{L}{tca}-{L}{ppe}-{L}{gw}", key="onca")
        ta = frow("Total assets", lambda L: f"={L}{tca}+{L}{ppe}+{L}{gw}+{L}{onca}", bold=True, key="ta")
        r += 1
        sh.section(r, "Liabilities & equity", last); r += 1
        ap = inrow("accounts_payable", "Accounts payable")
        std = inrow("short_term_debt", "Short-term debt")
        tcl = inrow("current_liabilities", "Total current liabilities (reported)", 0)
        ocl = frow("  Other current liabilities", lambda L: f"={L}{tcl}-{L}{ap}-{L}{std}", key="ocl")
        ltd = inrow("long_term_debt", "Long-term debt")
        tl_rep = inrow("total_liabilities", "Total liabilities (reported)", 0)
        oncl = frow("  Other non-current liabilities", lambda L: f"={L}{tl_rep}-{L}{tcl}-{L}{ltd}", key="oncl")
        tl = frow("Total liabilities", lambda L: f"={L}{tcl}+{L}{ltd}+{L}{oncl}", bold=True, key="tl")
        eq = inrow("shareholders_equity", "Shareholders' equity")
        mi = inrow("minority_interest", "Minority interest")
        tle = frow("Total liabilities & equity", lambda L: f"={L}{tl}+{L}{eq}+{L}{mi}", bold=True, key="tle")
        frow("Balance check (assets − liabilities & equity)", lambda L: f"={L}{ta}-{L}{tle}", typ="Check", key="chk")
        r += 1
        td = frow("Total debt", lambda L: f"={L}{std}+{L}{ltd}")
        frow("Net debt — this tab's formula (debt − cash − short-term investments; leases excluded)", lambda L: f"={L}{td}-{L}{cash}-{L}{sti}", bold=True, key="nd")
        # Owner, 2026-10-04 (plan 1A.3): the one figure the valuation used, beside this tab's history,
        # so the three net debts a reader meets reconcile in one place.
        _nd_v = _num(((self.scen("base").get("leg_inputs") or {}).get("DCF") or {}).get("net_debt"))
        if _nd_v is None:
            _nd_v = next((_num(tr.get("net_debt")) for tr in (self.scen("base").get("leg_inputs") or {}).values()
                          if _num(tr.get("net_debt")) is not None), None)
        if _nd_v is not None:
            sh.note(r, 1, self._net_debt_label().replace("Less: net debt", "Net debt the valuation used")
                    + f": {_nd_v / 1e6:,.1f}m in {self.ccy} (valuation currency)")
            r += 1
        if self.model_kind == "operating" and n_fc:
            # the current-asset / liability sub-totals the model carries
            self._model_link(sh, tca, "tca", _fc0)
        sh.widths({"A": 50, "B": 10, **{get_column_letter(c): 13 for c in range(c0, last + 1 + n_fc)}})
        sh.ws.freeze_panes = "C6"

    def cash_flow(self) -> None:
        sh = self.sheet("CFS", "Cash flow statement: reported history; FCF bridge to the DCF basis")
        rows = self._stmt_rows()
        cur = (self.statements or {}).get("currency") or ""
        sh.title("Cash Flow Statement", f"In {cur} millions, as reported. Net income and D&A link to the IS.")
        if not rows:
            sh.note(4, 1, "Reported statements unavailable for this export.")
            return
        n_fc = 5 if (self.model_rows and self.model_kind == "operating") else 0
        last = self._period_headers(sh, 4, rows, n_fc)
        c0, r = 3, 6
        _fc0 = last + 1
        isr = getattr(self, "is_rows", None)
        # Owner, 2026-10-03 (Anta): the forecast columns are the Model tab's cash flow statement, linked.
        _cf_map = {"ni": "cf_ni", "da": "cf_da", "stock_based_compensation": "cf_sbc", "change_in_working_capital": "cf_nwc", "cfo": "cfo", "capital_expenditure": "cf_capex",
                   "acquisitions_net": "cf_acq", "cfi": "cfi", "dividends_and_distributions": "div", "share_buyback": "bbk", "net_debt_issuance": "draw", "cff": "cff", "dcash": "dcash", "fcf": "fcf"}

        def _fc(rr, key):
            if n_fc and _cf_map.get(key):
                self._model_link(sh, rr, _cf_map[key], _fc0)

        def inrow(key, label, sign=1.0, indent=1):
            nonlocal r
            sh.label(r, 1, label, indent=indent); sh.label(r, 2, "Input")
            for i, row in enumerate(rows):
                v = _num(row.get(key))
                sh.put(r, c0 + i, None if v is None else sign * v / 1e6, MIL)
            _fc(r, key)
            r += 1
            return r - 1

        def frow(label, f, bold=False, typ="Formula", fmt=MIL, key=None):
            nonlocal r
            sh.label(r, 1, label, bold=bold); sh.label(r, 2, typ)
            for c in range(c0, last + 1):
                sh.put(r, c, f(get_column_letter(c)), fmt, bold=bold)
            if key:
                _fc(r, key)
            r += 1
            return r - 1

        if n_fc:
            sh.note(5, _fc0, "FY+1E..FY+5E: the three-statement model's cash flow (Model tab), linked")
        sh.section(r, "Operating", last + n_fc); r += 1
        if isr:
            ni = frow("Net income", lambda L: f"='IS'!{L}{isr['ni']}", typ="Link", key="ni")
            da = frow("Depreciation & amortisation", lambda L: f"='IS'!{L}{isr['da']}", typ="Link", key="da")
        else:
            ni = inrow("net_income", "Net income"); da = inrow("depreciation_and_amortization", "D&A")
            _fc(ni, "ni"); _fc(da, "da")
        sbc = inrow("stock_based_compensation", "Stock-based compensation")
        wc = inrow("change_in_working_capital", "Change in working capital")
        cfo_rep = inrow("operating_cash_flow", "Cash from operations (reported)", 1.0, 0)
        oth = frow("  Other operating items", lambda L: f"={L}{cfo_rep}-{L}{ni}-{L}{da}-{L}{sbc}-{L}{wc}")
        cfo = frow("Cash from operations", lambda L: f"={L}{ni}+{L}{da}+{L}{sbc}+{L}{wc}+{L}{oth}", bold=True, key="cfo")
        r += 1
        sh.section(r, "Investing", last); r += 1
        capex = inrow("capital_expenditure", "Capital expenditure")
        acq = inrow("acquisitions_net", "Acquisitions (net)")
        cfi_rep = inrow("investing_cash_flow", "Cash from investing (reported)", 1.0, 0)
        oi = frow("  Other investing items", lambda L: f"={L}{cfi_rep}-{L}{capex}-{L}{acq}")
        cfi = frow("Cash from investing", lambda L: f"={L}{capex}+{L}{acq}+{L}{oi}", bold=True, key="cfi")
        r += 1
        sh.section(r, "Financing", last); r += 1
        div = inrow("dividends_and_distributions", "Dividends paid")
        bb = inrow("share_buyback", "Share repurchases")
        ndi = inrow("net_debt_issuance", "Net debt issuance / (repayment)")
        cff_rep = inrow("financing_cash_flow", "Cash from financing (reported)", 1.0, 0)
        of = frow("  Other financing items", lambda L: f"={L}{cff_rep}-{L}{div}-{L}{bb}-{L}{ndi}")
        cff = frow("Cash from financing", lambda L: f"={L}{div}+{L}{bb}+{L}{ndi}+{L}{of}", bold=True, key="cff")
        r += 1
        frow("Net change in cash (computed)", lambda L: f"={L}{cfo}+{L}{cfi}+{L}{cff}", bold=True, key="dcash")
        r += 1
        sh.section(r, "Free cash flow (the DCF basis)", last); r += 1
        fcf = frow("Free cash flow (CFO + capex)", lambda L: f"={L}{cfo}+{L}{capex}", bold=True, key="fcf")
        oe = frow("Owner earnings FCF (FCF − SBC)", lambda L: f"={L}{fcf}-{L}{sbc}", bold=True)
        if isr:
            frow("Owner earnings margin (%)", lambda L: f"=IFERROR({L}{oe}/'IS'!{L}{isr['rev']},0)", PCT)
        sh.widths({"A": 46, "B": 10, **{get_column_letter(c): 13 for c in range(c0, last + 1 + n_fc)}})
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
            sh.label(r, 1, f"Leverage ({bb.get('leverage_basis') or 'gross debt / book equity'})", indent=1)
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
        # Plan E27 (owner, 2026-10-06; BEAM / NTLA / BLK reviews): the CAPM band (D5 risk-on floor, EV2 risk-off
        # ceiling) moved the rate after the overlays -- BEAM 8.75% held at CAPM 13.88% less 1pp = 12.88% -- and
        # sat inside an unexplained "rounding residual" (+4.13pt). Each is its own line.
        _cp = b.get("capm") or {}
        for _lab, _key in (("CAPM band: risk-on floor (D5)", "risk_on_band"), ("CAPM band: risk-off ceiling (EV2)", "risk_off_band")):
            _bd = _cp.get(_key) or {}
            if isinstance(_bd.get("before"), (int, float)) and isinstance(_bd.get("after"), (int, float)):
                comps.append((_lab + f" -- {_bd['before']:.2%} held at {_bd['after']:.2%}", float(_bd["after"]) - float(_bd["before"])))
        if isinstance(b.get("owner_override_delta"), (int, float)):
            comps.append(("Owner rate override", float(b["owner_override_delta"])))
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
        sh.label(r, 1, "Engine rounding (residual; should be ~0)", indent=1)
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
        r += 1
        _cp = b.get("capm") or {}
        if _cp:
            # Owner, 2026-10-04 (plan 1D.4, D5): what the engine's own CAPM check found.
            sh.note(r, 1, "Engine CAPM check: " + (
                f"beta {_cp.get('beta'):.2f}, cost of equity {_cp.get('cost_of_equity'):.2%}, CAPM WACC {_cp.get('wacc'):.2%}"
                if _cp.get("status") == "used" else str(_cp.get("status") or "no beta"))
                + (f"; risk-on band held WACC at {(_cp.get('risk_on_band') or {}).get('after'):.2%}" if _cp.get("risk_on_band") else "")
                + (f"; risk-off band held WACC at {(_cp.get('risk_off_band') or {}).get('after'):.2%}" if _cp.get("risk_off_band") else "")
                + ("; WACC ABOVE the cost of equity" if _cp.get("wacc_above_cost_of_equity") else ""))
            r += 1
        r += 1
        sh.section(r, "Cost of debt detail (hybrid model)", 4); r += 1
        for lab, key, fmt in (("Live cost of debt", "rd_live", PCT2), ("Baseline cost of debt", "rd_baseline", PCT2),
                              ("Debt / (debt + equity)", "dv_ratio", PCT), ("Synthetic rating (from interest cover; sets only the live-spread overlay, not an agency rating)", "rating", None),
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
        self.dcf_excluded: Optional[str] = None
        if not self.dcf_weighted():
            # Owner, 2026-10-04 (D1): no tab for a DCF the blend does not weight -- one Summary line.
            _li = (self.scen("base").get("leg_inputs") or {})
            _dv = (_li.get("DCF") or {}).get("value")
            _iv = self.scen("base").get("intrinsic_value")
            _w = [n for n in _li if self.in_blend(n)]
            if _li.get("DCF"):
                self.dcf_excluded = (
                    "DCF EXCLUDED from the blend: the " + str(self.dr.get("profile")) + " profile weights "
                    + (", ".join(_w) or "other legs") + ". The DCF gave "
                    + (f"{float(_dv):,.2f}" if isinstance(_dv, (int, float)) else "no value")
                    + " a share against the blended intrinsic value "
                    + (f"{float(_iv):,.2f}" if isinstance(_iv, (int, float)) else "n/a")
                    + "; it is not an input to the target and is not shown.")
            return
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
        # Owner, 2026-10-04 (plan 1A.1 / D2): the margin path the engine projected, the share of each year
        # still to come after the balance-sheet date, and mid-window discounting -- the same arithmetic as
        # `_project_dcf`, so the Check reads zero.
        sh.label(r, 1, "FCF margin (as projected; Assumptions)"); mr = r
        for t in range(n):
            sh.put(r, c0 + t, "=" + A[f"m{t + 1}:{s}{sfx}"], PCT2)
        r += 1
        sh.label(r, 1, "Share of the year after the balance-sheet date"); fsr = r
        for t in range(n):
            sh.put(r, c0 + t, "=" + A[f"f{t + 1}:{s}{sfx}"], "0.0000")
        r += 1
        sh.label(r, 1, "Free cash flow (revenue × margin × share)"); fr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            sh.put(r, c0 + t, f"={L}{rv}*{L}{mr}*{L}{fsr}", BIG)
        r += 1
        pt = A[f"disc_point:{s}{sfx}"]
        sh.label(r, 1, "Discount factor, end of the year's window"); der = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            prev = "1" if t == 0 else f"{get_column_letter(c0 + t - 1)}{r}"
            sh.put(r, c0 + t, f"=IFERROR({prev}/(1+{L}{wr})^{L}{fsr},0)", "0.0000")
        r += 1
        sh.label(r, 1, "Discount factor at the cash-flow point (mid-window when dated)"); dfr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            prev = "1" if t == 0 else f"{get_column_letter(c0 + t - 1)}{der}"
            sh.put(r, c0 + t, f"=IFERROR({prev}/(1+{L}{wr})^({L}{fsr}*{pt}),0)", "0.0000")
        r += 1
        sh.label(r, 1, "Discount time at the cash-flow point (years from the balance-sheet date)"); tpr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            prev_end = "0" if t == 0 else f"SUM(${get_column_letter(c0)}{fsr}:{get_column_letter(c0 + t - 1)}{fsr})"
            sh.put(r, c0 + t, f"={prev_end}+{L}{fsr}*{pt}", "0.0000")
        r += 1
        sh.label(r, 1, "PV of FCF (at the balance-sheet date)"); pvr = r
        for t in range(n):
            L = get_column_letter(c0 + t)
            sh.put(r, c0 + t, f"={L}{fr}*{L}{dfr}", BIG)
        r += 2
        F, Lc = get_column_letter(c0), get_column_letter(c0 + n - 1)
        tg, nd, shs = A[f"tgr:{s}{sfx}"], A[f"net_debt:{s}{sfx}"], A[f"shares:{s}{sfx}"]
        roll = A[f"roll_forward_years:{s}{sfx}"]
        out = r
        lines = [
            ("Sum of PV of FCF (balance-sheet date)", f"=SUM({F}{pvr}:{Lc}{pvr})", BIG),                 # 0
            ("Terminal WACC (spread guard applied)", f"=IF({Lc}{wr}<={tg},{tg}+{A['tv_guard']},{Lc}{wr})", PCT2),  # 1
            ("Terminal value (final full year × (1 + g) × LOE multiplier)", f"=IFERROR({Lc}{rv}*{Lc}{mr}*(1+{tg})/(B{out + 1}-{tg}),0)*{A[f'terminal_loe_multiplier:{s}{sfx}']}", BIG),  # 2
            ("PV of terminal value (balance-sheet date)", f"=B{out + 2}*{Lc}{der}", BIG),                 # 3
            ("Carry to the valuation date: (1 + year-1 rate) ^ years", f"=(1+{F}{wr})^{roll}", "0.0000"),  # 4
            ("PV of FCF (valuation date)", f"=B{out}*B{out + 4}", BIG),                                 # 5
            ("PV of terminal value (valuation date)", f"=B{out + 3}*B{out + 4}", BIG),                  # 6
            ("Enterprise value", f"=B{out + 5}+B{out + 6}", BIG),                                      # 7
            ("Less: net debt (plus net cash when negative)", f"=-{nd}", BIG),                           # 8
            # Owner, 2026-10-03 (Alibaba review, section 1): the engine deducts minority interest and
            # preferred equity in the DCF bridge.
            ("Less: minority interest", -(_num(tr.get("minority_interest")) or 0.0), BIG),             # 9
            ("Less: preferred equity", -(_num(tr.get("preferred_equity")) or 0.0), BIG),               # 10
            ("Equity value", f"=B{out + 7}+B{out + 8}+B{out + 9}+B{out + 10}", BIG),                   # 11
            ("Intrinsic value per share", f"=IFERROR(B{out + 11}/{shs},0)", NUM),                      # 12
            ("Engine value", _num(tr.get("value")), NUM),                                              # 13
            ("Check", f"=B{out + 12}-B{out + 13}", NUM),                                               # 14
            ("Terminal value share of EV", f"=IFERROR(B{out + 6}/B{out + 7},0)", PCT),                 # 15
        ]
        for i, (lab, v, fmt) in enumerate(lines):
            sh.label(out + i, 1, lab, bold=lab in ("Intrinsic value per share", "Enterprise value"))
            sh.put(out + i, 2, v, fmt, bold=lab == "Intrinsic value per share")
        if not core:
            self.leg_cell[(s, leg)] = _ref("DCF", out + 12, 2)
            return out + len(lines)
        self.dcf[s] = {"iv": _ref("DCF", out + 12, 2), "pv_fcf": _ref("DCF", out + 5, 2),
                       "pv_tv": _ref("DCF", out + 6, 2), "ev": _ref("DCF", out + 7, 2),
                       "nd": _ref("DCF", out + 8, 2), "mi": _ref("DCF", out + 9, 2), "pe": _ref("DCF", out + 10, 2),
                       "eq": _ref("DCF", out + 11, 2),
                       "fcf_row": fr, "rev_row": rv, "c0": c0, "n": n}
        self.leg_cell[(s, "DCF")] = self.dcf[s]["iv"]
        if s == "base" and getattr(self, "_is_sheet", None) is not None:
            self._link_is_forecast(s)
        r = out + len(lines) + 1
        # Sensitivity: FCF is independent of the discount rate, so a flat WACC re-discounts the same
        # stream exactly -- at the same discount points, with the same carry and the same bridge
        # (owner, 2026-10-04, plan 1A.3: the grid used to leave out minorities and preferreds,
        # HK$27 a share on 09618.HK).
        sh.label(r, 1, "Sensitivity: value per share, flat WACC × terminal growth", bold=True)
        r += 1
        center_w = A.get("wacc") or f"{Lc}{wr}"
        sh.label(r, 1, "WACC \\ terminal growth")
        for j, k in enumerate((-2, -1, 0, 1, 2)):
            sh.put(r, 2 + j, f"={tg}+({k})*{A['sens_g']}", PCT2, bold=True)
        top = r
        t_end = f"SUM(${F}${fsr}:${Lc}${fsr})"
        bridge = f"-{nd}+$B${out + 9}+$B${out + 10}"
        for i, k in enumerate((-2, -1, 0, 1, 2)):
            rr = r + 1 + i
            sh.put(rr, 1, f"={center_w}+({k})*{A['sens_w']}", PCT2, bold=True)
            for j in range(5):
                gc = f"{get_column_letter(2 + j)}${top}"
                wc = f"$A{rr}"
                f = (f"=IFERROR(IF({wc}<={gc},NA(),((SUMPRODUCT(${F}${fr}:${Lc}${fr}/(1+{wc})^${F}${tpr}:${Lc}${tpr})"
                     f"+${Lc}${rv}*${Lc}${mr}*(1+{gc})/({wc}-{gc})/(1+{wc})^{t_end})*(1+{wc})^{roll}{bridge})/{shs}),NA())")
                sh.put(rr, 2 + j, f, NUM)
        return r + 6

    def _link_is_forecast(self, s: str) -> None:
        sh, rev, g, last = self._is_sheet, self._is_rev_row, self._is_g_row, self._is_last
        d = self.dcf[s]
        for t in range(5):
            c = last + 1 + t
            src = get_column_letter(d["c0"] + t)
            sh.put(rev, c, f"=IFERROR('DCF'!{src}{d['rev_row']}/{self.A['unit']}/{self.A['fx']},\"\")", MIL)
        sh.note(3, 1, "Forecast (E) columns: FMP consensus as published, and the DCF base-case "
                      "revenue path the valuation uses, with the variance between them.")

    # ── Multiples ───────────────────────────────────────────────────────────
    def multiples_tab(self) -> None:
        sh = self.sheet("Multiples", "Each valuation leg: metric × multiple → EV → equity → per share")
        sh.title("Relative valuation legs", "Multiple = peer multiple × scenario band × growth premium × "
                                            "other adjustments (named). Check = rebuilt − engine.")
        cols = ["Scenario", "Leg", "Metric", "Metric value", "Peer multiple", "Scenario band",
                "Growth premium", "Other adj.", "Multiple", "Enterprise value", "Net debt",
                "Minority + preferred", "Equity value", "Shares", "Value per share", "Engine value",
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
                    # Minority interest AND preferred equity: the engine's one bridge deducts both (V: $514m).
                    sh.put(r, 12, _mil((_num(tr.get("minority_interest")) or 0.0) + (_num(tr.get("preferred_equity")) or 0.0)), MIL)
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
                    # Minority interest AND preferred equity: the engine's one bridge deducts both (V: $514m).
                    sh.put(r, 12, _mil((_num(tr.get("minority_interest")) or 0.0) + (_num(tr.get("preferred_equity")) or 0.0)), MIL)
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
            # Plan EN8 (2026-10-04): the members and values the engine's median was taken over, frozen
            # into the run -- the live basket is read only for runs that predate the freeze.
            _frozen = info.get("members_used") or []
            if _frozen:
                members = [{"symbol": m.get("symbol"), "name": m.get("name"), "market_cap": m.get("market_cap"),
                            "metrics": {field: {"value": m.get("value"), "in_band": True}},
                            "computed_at": str(self.run.get("run_at") or "")[:10]} for m in _frozen]
            elif info.get("key") and self.load_members:
                try:
                    members = self.load_members(info.get("exchange"), info.get("basis"),
                                                info.get("key"), info.get("cohort") or "all")
                except Exception:  # noqa: BLE001
                    members = []
            eng_row = None
            if members:
                if info.get("excluded_no_market_cap"):
                    sh.note(r, 1, "Excluded (no market cap in the feed): " + ", ".join(info["excluded_no_market_cap"]))
                    r += 1
                sh.header(r, ["Ticker", "Company", "Market cap (m)", lab, "In band", "In-band value"])
                first = r + 1
                try:
                    from src.data.regional_comps import subject_symbol as _subj_sym
                except Exception:                          # noqa: BLE001
                    _subj_sym = lambda x: str(x or "").upper()   # noqa: E731
                _me = _subj_sym(self.ticker)
                for m in members:
                    r += 1
                    mv = (m.get("metrics") or {}).get(field) or {}
                    # Owner, 2026-10-04 (plan 1F.3): the valued company is listed, never counted.
                    _is_me = _subj_sym(m.get("symbol")) == _me and info.get("subject_excluded") is True
                    sh.put(r, 1, m.get("symbol")).font = Font(color=BLACK)
                    sh.put(r, 2, (m.get("name") or "") + (" (the valued company: not its own peer)" if _is_me else "")).font = Font(color=BLACK)
                    sh.put(r, 3, _mil(m.get("market_cap")), MIL)
                    sh.put(r, 4, _num(mv.get("value")), "0.00")
                    sh.put(r, 5, "yes" if (mv.get("in_band") and not _is_me) else "no").font = Font(color=BLUE)
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
                # Owner, 2026-10-04 (plan 1A.3): the minorities the engine's NAV deducts -- the gap between
                # its segments + associates + net cash and its NAV (09988.HK: RMB9.7bn, HK$3.36 a share the
                # tab used to leave in). Stated as the input it is, so every scenario's NAV takes it.
                _mi_sotp = t.get("minority_interest")
                if _mi_sotp is None and all(_num(t.get(k)) is not None for k in ("segment_value", "nav")):
                    _mi_sotp = ((_num(t.get("segment_value")) or 0.0) + (_num(t.get("associates")) or 0.0)
                                + (_num(t.get("net_cash")) or 0.0) - (_num(t.get("nav")) or 0.0))
                sh.label(r, 1, "Less: minority interest (as the engine's NAV deducts it)")
                sh.put(r, 7, -(_mil(_mi_sotp) or 0.0), MIL); mi_r = r; r += 1
                sh.label(r, 1, "Net asset value", bold=True)
                sh.put(r, 7, f"=G{seg}+G{asso}+G{nc}+G{mi_r}", MIL, bold=True); nav = r; r += 1
                nav_eng, disc_eng = _num(t.get("nav")), _num(t.get("holdco_discount"))
                _seg_eng, _as_eng = _num(t.get("segment_value")), _num(t.get("associates")) or 0.0
                # Plan EN5 (2026-10-04): the discount applies to segments and associates, not to cash.
                # A table priced before the rule recorded a discount on the whole NAV; its rate is read
                # back on that basis so the check still ties.
                _on_assets = (disc_eng is not None and _seg_eng is not None and nav_eng
                              and abs(disc_eng - (_num(t.get("holdco_discount_pct")) or 0.0) * (_seg_eng + _as_eng)) < 1.0)
                sh.label(r, 1, "Holdco discount rate" + (" (on segments and associates)" if _on_assets else " (on NAV)"))
                sh.put(r, 7, (_num(t.get("holdco_discount_pct")) if _on_assets else ((disc_eng / nav_eng) if nav_eng else _num(t.get("holdco_discount_pct")))), PCT)
                dr_ = r; r += 1
                sh.label(r, 1, "Equity value after holdco discount", bold=True)
                sh.put(r, 7, (f"=G{nav}-G{dr_}*(G{seg}+G{asso})" if _on_assets else f"=G{nav}*(1-G{dr_})"), MIL, bold=True); fin = r; r += 1
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
            # Plan E33: a consensus "as of" later than the valuation date is a period-end or a model error,
            # not a date the figures were read -- said so rather than shown as fact (BSL / Sheng Siong).
            _val_d = str(((getattr(self, "run", None) or {}).get("run_at")
                          or (getattr(self, "data", None) or {}).get("end_date") or "")[:10])
            _asof = str(c.get("as_of"))
            _late = bool(_val_d) and _asof[:10] > _val_d
            sh.label(r, 1, "Consensus as of" + (" (AFTER the valuation date: unverified, likely a period end)" if _late else ""))
            sh.put(r, 2, _asof).font = Font(color=BLACK); r += 1
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
            # Owner, 2026-10-04 (plan 1B.4): years before the research's FY+1 run on the engine path.
            _lead = len((((self.dr or {}).get("guidance_forecast") or {}).get("deconstruction") or {}).get("lead_years") or [])
            for i, v in enumerate(sched, start=1):
                r += 1
                sh.put(r, 1, i, "0")
                sh.put(r, 2, _num(v), PCT)
                sh.put(r, 3, ("engine path (before the research's FY+1)" if i <= _lead else
                              "guidance-derived estimate" if i <= E else "fade onto the engine path" if i <= E + F
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
        _sec = [t["source"] for t in (ge.get("source_tiers") or []) if t.get("tier") != "primary"]
        if _sec:
            r += 1
            sh.label(r, 1, ("SECONDARY SOURCES ONLY: " if ge.get("secondary_only") else "Secondary sources (aggregator / market research): ")
                     + " · ".join(_sec), bold=bool(ge.get("secondary_only")))
        sh.widths({"A": 46, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 12, "H": 14, "I": 12, "J": 10})

    # ── Three-statement forecast (owner, 2026-10-03) ─────────────────────────
    def model_tab(self) -> None:
        """IS, CF and BS for FY+1E..FY+5E, live formulas over an assumptions block and the opening
        (latest audited) column. Inputs in blue are the agent's estimates and the history-derived
        assumptions; change one and the three statements move together and still balance."""
        th = (self.dr or {}).get("three_statements") or {}
        if th.get("kind") in ("bank", "insurer") and th.get("fy_labels") and not th.get("skipped"):
            self._bank_model_tab(th)
            return
        if not th or th.get("skipped") or not th.get("fy_labels"):
            if th.get("skipped"):
                sh = self.sheet("Model", "Three-statement forecast: not built for this run")
                sh.title("Three-statement forecast", th["skipped"])
                _rc = th.get("reconciliation") or {}
                if _rc.get("failures"):
                    rr = 4
                    sh.header(rr, ["Year", "Assertion", "Left-hand side", "Right-hand side", "Difference"]); rr += 1
                    for f in _rc["failures"]:
                        sh.put(rr, 1, f["year"]); sh.put(rr, 2, f"#{f['id']} {f['name']}"); sh.put(rr, 3, _mil(f["lhs"]), MIL); sh.put(rr, 4, _mil(f["rhs"]), MIL); sh.put(rr, 5, _mil(f["diff"]), MIL); rr += 1
                    sh.widths({"A": 10, "B": 80, "C": 16, "D": 16, "E": 14})
            return
        sh = self.sheet("Model", "Three-statement forecast FY+1E..FY+5E on the valuation agent's estimates (live formulas)")
        op, a, IS, CF, BS = th["opening"], th["assumptions"], th["income"], th["cashflow"], th["balance"]
        labels = th["fy_labels"]
        n = len(labels)
        ovr = th.get("override") or (self.dr or {}).get("estimate_override") or {}
        sh.title("Three-statement forecast", f"In statement currency, millions. Opening column = {op.get('fy_label')} as filed; forecast columns are formulas over the "
                                             "assumptions block (inputs in blue: the agent's estimates and history-derived assumptions). The balance check is zero by construction."
                                             + (f" USER OVERRIDE ({str(ovr.get('created_at') or '')[:10]}): {', '.join(ovr.get('fields') or [])}." if ovr else ""))
        C0 = 3                                                  # opening column
        cols = [get_column_letter(C0 + 1 + i) for i in range(n)]
        OC = get_column_letter(C0)
        r = 4
        sh.label(r, 1, "Fiscal year", bold=True); sh.put(r, C0, f"{op.get('fy_label')}A").font = Font(bold=True)
        for i, lab in enumerate(labels):
            sh.put(r, C0 + 1 + i, lab).font = Font(bold=True)
        r += 2
        A: dict = {}

        def arow(key, label, values, fmt, scale=1.0):
            nonlocal r
            sh.label(r, 1, label); sh.label(r, 2, "Input")
            for i in range(n):
                v = values[i] if isinstance(values, list) else values
                sh.put(r, C0 + 1 + i, (None if v is None else float(v) * scale), fmt).font = Font(color=BLUE)
            A[key] = r
            r += 1

        av = lambda k: (a.get(k) or {}).get("value")                                    # noqa: E731
        sh.section(r, "Assumptions (per year)", C0 + n); r += 1
        arow("g", "Revenue growth", IS["growth"], PCT)
        arow("gm", "Gross margin (blank = no COGS line filed)", IS.get("gross_margin") or [None] * n, PCT)
        arow("em", "EBIT margin", IS["ebit_margin"], PCT)
        arow("da", "Depreciation & amortisation", [-x for x in IS["da"]], MIL, 1e-6)
        arow("capex", "Capital expenditure", [-x for x in CF["capex"]], MIL, 1e-6)
        arow("nwc", "Working capital absorbed (+ = cash out)", [-x for x in CF["change_nwc"]], MIL, 1e-6)
        arow("sbc", "Stock-based compensation, % of revenue", av("sbc_pct"), PCT)
        arow("tax", "Tax rate", IS["tax_rate"], PCT)
        arow("ir", "Interest rate on opening debt", av("interest_rate"), PCT)
        arow("iir", "Interest income rate on opening cash", av("interest_income_rate"), PCT)
        arow("po", "Dividend payout ratio", av("payout_ratio"), PCT)
        arow("bb", "Share repurchases, target", av("buyback_annual"), MIL, 1e-6)
        # Owner, 2026-10-04 (plan 1B.1): the spot is quoted in the valuation currency; the sheet is the filing's.
        _px = av("buyback_price")
        _fxv = th.get("fx_to_valuation") or 1.0
        arow("px", "Buyback price (spot, held; statement currency)", (float(_px) / float(_fxv)) if isinstance(_px, (int, float)) and _fxv else _px, NUM)
        arow("mincash", "Minimum cash (revolver trigger)", av("min_cash"), MIL, 1e-6)
        arow("ard", "Receivable days (blank = held)", av("receivable_days"), "0.0")
        arow("invd", "Inventory days (blank = held)", av("inventory_days"), "0.0")
        arow("apd", "Payable days (blank = held)", av("payable_days"), "0.0")
        arow("mi", "Minority share of net income", av("minority_share_of_ni"), PCT)
        arow("acq", "Acquisitions", av("acquisitions"), MIL, 1e-6)
        r += 1
        R: dict = {}

        def frow(key, label, f, fmt=MIL, bold=False, opening=None, typ="Formula"):
            """f(L, P) -> formula for column L with previous column P; opening = static value for the opening column."""
            nonlocal r
            R[key] = r                                       # registered first: roll-forward rows reference their own prior column
            sh.label(r, 1, label, bold=bold); sh.label(r, 2, typ)
            if opening is not None:
                sh.put(r, C0, float(opening), fmt, bold=bold).font = Font(color=BLUE, bold=bold)
            for i, L in enumerate(cols):
                P = OC if i == 0 else cols[i - 1]
                sh.put(r, C0 + 1 + i, f(L, P), fmt, bold=bold)
            r += 1
            return r - 1

        M = 1e-6
        # 1. Income statement: revenue and EBIT from the assumptions; interest from the debt schedule below
        sh.section(r, "1. Income statement (revenue & EBIT)", C0 + n); r += 1
        frow("rev", "Revenue", lambda L, P: f"={P}{R['rev']}*(1+{L}{A['g']})", bold=True, opening=(op.get("revenue") or 0) * M)
        frow("cogs", "Cost of goods sold", lambda L, P: f'=IF({L}{A["gm"]}="","",-{L}{R["rev"]}*(1-{L}{A["gm"]}))', opening=(-(abs(op.get("cost_of_revenue") or 0)) * M) if op.get("cost_of_revenue") else None)
        frow("gp", "Gross profit", lambda L, P: f'=IF({L}{R["cogs"]}="","",{L}{R["rev"]}+{L}{R["cogs"]})', bold=True)
        frow("ebit", "EBIT", lambda L, P: f"={L}{R['rev']}*{L}{A['em']}", bold=True, opening=(op.get("ebit") or 0) * M)
        frow("opex", "Operating expenses excl. D&A (derived)", lambda L, P: f'=IF({L}{R["gp"]}="",-({L}{R["rev"]}-{L}{R["ebit"]}-{L}{A["da"]}),-({L}{R["gp"]}-{L}{R["ebit"]}-{L}{A["da"]}))')
        frow("ebitda", "EBITDA", lambda L, P: f"={L}{R['ebit']}+{L}{A['da']}", bold=True)
        frow("intexp", "Interest expense (debt schedule)", lambda L, P: f"=-{L}{{d_int}}", typ="Link")
        frow("intinc", "Interest income (opening cash × rate)", lambda L, P: f"=({P}{{cash}}+{P}{{sti}})*{L}{A['iir']}")
        frow("pretax", "Pre-tax income", lambda L, P: f"={L}{R['ebit']}+{L}{R['intexp']}+{L}{R['intinc']}", bold=True, opening=(op.get("pretax") or 0) * M)
        frow("tax", "Income tax", lambda L, P: f"=-MAX({L}{R['pretax']},0)*{L}{A['tax']}")
        frow("mino", "Minority interest", lambda L, P: f"=-({L}{R['pretax']}+{L}{R['tax']})*{L}{A['mi']}")
        frow("ni", "Net income to common", lambda L, P: f"={L}{R['pretax']}+{L}{R['tax']}+{L}{R['mino']}", bold=True, opening=(op.get("net_income") or 0) * M)
        frow("shares", "Diluted shares (millions)", lambda L, P: f'=IF({L}{A["px"]}>0,{P}{R["shares"]}+({L}{{bbk}}+{L}{{issue}})/{L}{A["px"]},{P}{R["shares"]})', fmt=MIL, opening=(op.get("shares") or 0) * M)
        frow("eps", "Diluted EPS", lambda L, P: f"=IFERROR({L}{R['ni']}/{L}{R['shares']},0)", fmt=NUM, bold=True)
        frow("dps", "Dividend per share", lambda L, P: f"=IFERROR(-{L}{{div}}/{L}{R['shares']},0)", fmt=NUM)
        r += 1
        # 2. Supporting schedules
        sh.section(r, "2. Supporting schedules", C0 + n); r += 1
        sh.label(r, 1, "Working capital (days of revenue / COGS; blank days hold the balance)", bold=True); r += 1
        frow("ar", "Accounts receivable", lambda L, P: f'=IF({L}{A["ard"]}="",{P}{R["ar"]},{L}{R["rev"]}*{L}{A["ard"]}/365)', opening=(op.get("receivables") or 0) * M)
        frow("inv", "Inventory", lambda L, P: f'=IF({L}{A["invd"]}="",{P}{R["inv"]},IF({L}{R["cogs"]}="",{L}{R["rev"]},-{L}{R["cogs"]})*{L}{A["invd"]}/365)', opening=(op.get("inventory") or 0) * M)
        frow("ap", "Accounts payable", lambda L, P: f'=IF({L}{A["apd"]}="",{P}{R["ap"]},IF({L}{R["cogs"]}="",{L}{R["rev"]},-{L}{R["cogs"]})*{L}{A["apd"]}/365)', opening=(op.get("payables") or 0) * M)
        frow("ap_memo", "Named working-capital change (ΔAR + Δinventory − Δpayables)", lambda L, P: f"=({L}{R['ar']}-{P}{R['ar']})+({L}{R['inv']}-{P}{R['inv']})-({L}{R['ap']}-{P}{R['ap']})")
        frow("oca", "Other working capital (carries the forecast's absorption beyond the named lines)", lambda L, P: f"={P}{R['oca']}+{L}{A['nwc']}-{L}{R['ap_memo']}", opening=(op.get("other_current_assets") or 0) * M)
        frow("wc_chg", "Working capital absorbed, total (to the cash flow)", lambda L, P: f"={L}{A['nwc']}", typ="Link")
        sh.label(r, 1, "Capex & D&A (PP&E roll-forward)", bold=True); r += 1
        frow("ppe_o", "PP&E, opening", lambda L, P: f"={P}{{ppe}}", typ="Link")
        frow("ppe_cx", "+ Capital expenditure", lambda L, P: f"={L}{A['capex']}", typ="Link")
        frow("ppe_da", "− Depreciation & amortisation", lambda L, P: f"=-{L}{A['da']}", typ="Link")
        frow("ppe", "PP&E, closing", lambda L, P: f"={L}{R['ppe_o']}+{L}{R['ppe_cx']}+{L}{R['ppe_da']}", bold=True, opening=(op.get("ppe") or 0) * M)
        sh.label(r, 1, "Debt schedule (gross debt held; a revolver draw only to hold the minimum cash)", bold=True); r += 1
        frow("d_open", "Debt, opening (short + long term)", lambda L, P: f"={P}{{std}}+{P}{{ltd}}")
        frow("d_draw", "+ Revolver draw (from the cash flow)", lambda L, P: f"={L}{{draw}}", typ="Link")
        frow("d_close", "Debt, closing", lambda L, P: f"={L}{R['d_open']}+{L}{R['d_draw']}", bold=True)
        frow("d_int", "Interest expense (opening debt × rate)", lambda L, P: f"={L}{R['d_open']}*{L}{A['ir']}")
        r += 1
        # 3. Cash flow statement
        sh.section(r, "3. Cash flow statement (net change in cash & free cash flow)", C0 + n); r += 1
        frow("cf_ni", "Net income", lambda L, P: f"={L}{R['ni']}", typ="Link")
        frow("cf_da", "Depreciation & amortisation", lambda L, P: f"={L}{A['da']}", typ="Link")
        frow("cf_sbc", "Stock-based compensation", lambda L, P: f"={L}{R['rev']}*{L}{A['sbc']}")
        frow("cf_nwc", "Change in working capital", lambda L, P: f"=-{L}{R['wc_chg']}", typ="Link")
        frow("cf_mi", "Minority interest (non-cash)", lambda L, P: f"=-{L}{R['mino']}")
        frow("cfo", "Cash from operations", lambda L, P: f"=SUM({L}{R['cf_ni']}:{L}{R['cf_mi']})", bold=True)
        frow("cf_capex", "Capital expenditure", lambda L, P: f"=-{L}{R['ppe_cx']}", typ="Link")
        frow("cf_acq", "Acquisitions", lambda L, P: f"=-{L}{A['acq']}")
        frow("cfi", "Cash from investing", lambda L, P: f"={L}{R['cf_capex']}+{L}{R['cf_acq']}", bold=True)
        frow("div", "Dividends paid", lambda L, P: f"=-MAX({L}{R['ni']},0)*{L}{A['po']}")
        frow("cashpre", "  Cash before buybacks and revolver", lambda L, P: f"={P}{{cash}}+{L}{R['cfo']}+{L}{R['cfi']}+{L}{R['div']}", typ="Memo")
        frow("bbk", "Share repurchases", lambda L, P: f"=-MAX(0,MIN({L}{A['bb']},{L}{R['cashpre']}-{L}{A['mincash']}))")
        # Plan E34 (2026-10-06): a loss-making year funds its shortfall with equity at the spot price, a profitable
        # year with the revolver -- the engine's rule, reproduced.
        frow("issue", "Equity issued (loss-making year)", lambda L, P: f"=IF({L}{R['ni']}<0,MAX(0,{L}{A['mincash']}-({L}{R['cashpre']}+{L}{R['bbk']})),0)")
        frow("draw", "Revolver draw", lambda L, P: f"=IF({L}{R['ni']}<0,0,MAX(0,{L}{A['mincash']}-({L}{R['cashpre']}+{L}{R['bbk']})))")
        frow("cff", "Cash from financing", lambda L, P: f"={L}{R['div']}+{L}{R['bbk']}+{L}{R['draw']}+{L}{R['issue']}", bold=True)
        frow("dcash", "Net change in cash", lambda L, P: f"={L}{R['cfo']}+{L}{R['cfi']}+{L}{R['cff']}", bold=True)
        frow("fcf", "Free cash flow (CFO − capex)", lambda L, P: f"={L}{R['cfo']}+{L}{R['cf_capex']}", bold=True)
        r += 1
        # 4. Balance sheet
        sh.section(r, "4. Balance sheet (assets = liabilities + equity)", C0 + n); r += 1
        frow("cash", "Cash & equivalents (opening + net change)", lambda L, P: f"={P}{R['cash']}+{L}{R['dcash']}", opening=(op.get("cash") or 0) * M)
        frow("sti", "Short-term investments (held)", lambda L, P: f"={P}{R['sti']}", opening=(op.get("sti") or 0) * M)
        frow("bs_ar", "Accounts receivable (schedule)", lambda L, P: f"={L}{R['ar']}", typ="Link", opening=(op.get("receivables") or 0) * M)
        frow("bs_inv", "Inventory (schedule)", lambda L, P: f"={L}{R['inv']}", typ="Link", opening=(op.get("inventory") or 0) * M)
        frow("bs_oca", "Other current assets (schedule)", lambda L, P: f"={L}{R['oca']}", typ="Link", opening=(op.get("other_current_assets") or 0) * M)
        frow("tca", "Total current assets", lambda L, P: f"={L}{R['cash']}+{L}{R['sti']}+{L}{R['bs_ar']}+{L}{R['bs_inv']}+{L}{R['bs_oca']}", bold=True)
        frow("bs_ppe", "Property, plant & equipment (schedule)", lambda L, P: f"={L}{R['ppe']}", typ="Link", opening=(op.get("ppe") or 0) * M)
        frow("gi", "Goodwill & intangibles (held)", lambda L, P: f"={P}{R['gi']}", opening=(op.get("goodwill_intangibles") or 0) * M)
        frow("onca", "Other non-current assets (held)", lambda L, P: f"={P}{R['onca']}", opening=(op.get("other_noncurrent_assets") or 0) * M)
        frow("ta", "Total assets", lambda L, P: f"={L}{R['tca']}+{L}{R['bs_ppe']}+{L}{R['gi']}+{L}{R['onca']}", bold=True)
        frow("bs_ap", "Accounts payable (schedule)", lambda L, P: f"={L}{R['ap']}", typ="Link", opening=(op.get("payables") or 0) * M)
        frow("std", "Short-term debt incl. revolver (schedule)", lambda L, P: f"={P}{R['std']}+{L}{R['d_draw']}", typ="Link", opening=(op.get("short_term_debt") or 0) * M)
        frow("ocl", "Other current liabilities (held)", lambda L, P: f"={P}{R['ocl']}", opening=(op.get("other_current_liabilities") or 0) * M)
        frow("ltd", "Long-term debt (held)", lambda L, P: f"={P}{R['ltd']}", opening=(op.get("long_term_debt") or 0) * M)
        frow("oncl", "Other non-current liabilities (held)", lambda L, P: f"={P}{R['oncl']}", opening=(op.get("other_noncurrent_liabilities") or 0) * M)
        frow("tl", "Total liabilities", lambda L, P: f"={L}{R['bs_ap']}+{L}{R['std']}+{L}{R['ocl']}+{L}{R['ltd']}+{L}{R['oncl']}", bold=True)
        _re0 = (op.get("retained_earnings") if op.get("retained_earnings") is not None else op.get("equity")) or 0
        frow("re", "Retained earnings (prior + net income − common dividends)", lambda L, P: f"={P}{R['re']}+{L}{R['ni']}+{L}{R['div']}", opening=_re0 * M)
        frow("oeq", "Other equity: APIC, treasury, reserves (prior − buybacks + SBC + equity issued)", lambda L, P: f"={P}{R['oeq']}+{L}{R['bbk']}+{L}{R['cf_sbc']}+{L}{R['issue']}", opening=((op.get("equity") or 0) - _re0) * M)
        frow("eq", "Shareholders' equity", lambda L, P: f"={L}{R['re']}+{L}{R['oeq']}", bold=True, opening=(op.get("equity") or 0) * M)
        frow("mi", "Minority interest", lambda L, P: f"={P}{R['mi']}-{L}{R['mino']}", opening=(op.get("minority_interest") or 0) * M)
        frow("tle", "Total liabilities & equity", lambda L, P: f"={L}{R['tl']}+{L}{R['eq']}+{L}{R['mi']}", bold=True)
        frow("chk", "Balance check (assets − liabilities & equity)", lambda L, P: f"=ROUND({L}{R['ta']}-{L}{R['tle']},3)", typ="Check")
        frow("nd", "Net debt (debt − cash − short-term investments)", lambda L, P: f"={L}{R['std']}+{L}{R['ltd']}-{L}{R['cash']}-{L}{R['sti']}")
        frow("lev", "Net debt / EBITDA", lambda L, P: f"=IFERROR({L}{R['nd']}/{L}{R['ebitda']},0)", fmt="0.0x")
        frow("cover", "Interest cover (EBIT / interest)", lambda L, P: f"=IFERROR(-{L}{R['ebit']}/{L}{R['intexp']},0)", fmt="0.0x")
        r += 1
        # 5. The reconciliation suite (owner's execution mandate): live on every forecast column.
        sh.section(r, "5. Reconciliation suite (every forecast column must read OK)", C0 + n); r += 1
        OK = lambda expr: f'=IF(ABS({expr})<0.01,"OK","FAIL")'                                 # noqa: E731
        frow("c1", "1. ABS(total assets − total liabilities & equity) < 0.01", lambda L, P: OK(f"{L}{R['ta']}-{L}{R['tle']}"), fmt=None, typ="Check")
        frow("c2", "2. Cash ending = cash beginning + net change in cash (CFS)", lambda L, P: OK(f"{L}{R['cash']}-({P}{R['cash']}+{L}{R['dcash']})"), fmt=None, typ="Check")
        frow("c3", "3. Retained earnings ending = prior + net income − common dividends", lambda L, P: OK(f"{L}{R['re']}-({P}{R['re']}+{L}{R['ni']}+{L}{R['div']})"), fmt=None, typ="Check")
        frow("c4", "4. Net PP&E = prior + capex (CFS) − depreciation (IS)", lambda L, P: OK(f"{L}{R['bs_ppe']}-({P}{R['bs_ppe']}-{L}{R['cf_capex']}+{L}{R['ppe_da']})"), fmt=None, typ="Check")
        frow("c5", "5. NWC change (CFS) = −(Δ current assets ex cash − Δ current liabilities ex short debt)",
             lambda L, P: OK(f"{L}{R['cf_nwc']}+(({L}{R['sti']}+{L}{R['bs_ar']}+{L}{R['bs_inv']}+{L}{R['bs_oca']})-({P}{R['sti']}+{P}{R['bs_ar']}+{P}{R['bs_inv']}+{P}{R['bs_oca']})"
                             f"-(({L}{R['bs_ap']}+{L}{R['ocl']})-({P}{R['bs_ap']}+{P}{R['ocl']})))"), fmt=None, typ="Check")
        frow("call", "Suite result", lambda L, P: f'=IF(COUNTIF({L}{R["c1"]}:{L}{R["c5"]},"FAIL")=0,"ALL OK","FAIL — do not use the valuation outputs until traced")', fmt=None, bold=True, typ="Check")
        _rc = th.get("reconciliation") or {}
        sh.label(r, 1, "Engine suite (Python build): " + ("ALL OK on every forecast column" if _rc.get("ok") else "FAILED — " + "; ".join(f"{f['year']} #{f['id']}" for f in _rc.get("failures") or [])), bold=True); r += 1
        # resolve the forward references written as {name} placeholders
        for row in sh.ws.iter_rows(min_row=1, max_row=r, min_col=C0 + 1, max_col=C0 + n):
            for cell in row:
                if isinstance(cell.value, str) and "{" in cell.value and cell.value.startswith("="):
                    cell.value = cell.value.format(**R)
        self.model_rows, self.model_cols, self.model_kind = {**R, "da_assump": A["da"]}, list(cols), "operating"
        r += 1
        sh.section(r, "Where each assumption comes from", C0 + n); r += 1
        for row in th.get("coverage") or []:
            sh.label(r, 1, f"{row.get('assumption')}: {row.get('status')}" + (f" — {row.get('source')}" if row.get("source") else "")); r += 1
        for note in th.get("notes") or []:
            sh.label(r, 1, note); r += 1
        sh.label(r, 1, "Engine check (Python build): balance gaps " + ", ".join(f"{c['year']} {c['balance_gap']:,.0f}" for c in th.get("checks") or [])
                       + "; revolver draws in " + (", ".join(c["year"] for c in (th.get("checks") or []) if c.get("revolver_draw")) or "none") + ".")
        sh.widths({"A": 58, "B": 9, **{get_column_letter(c): 14 for c in range(C0, C0 + n + 1)}})
        sh.ws.freeze_panes = f"{get_column_letter(C0)}5"

    _BANK_ROWS = [("Balance sheet drivers", None), ("Total assets", "total_assets", MIL), ("Asset growth", "asset_growth", PCT), ("Average earning assets", "earning_assets_avg", MIL),
                  ("Net interest margin", "nim", PCT), ("Average loans", "loans_avg", MIL),
                  ("Income statement", None), ("Net interest income", "net_interest_income", MIL), ("Non-interest income", "fee_income", MIL), ("Total income", "total_income", MIL),
                  ("Cost-to-income", "cost_to_income", PCT), ("Operating expenses", "operating_expenses", MIL), ("Pre-provision profit", "pre_provision_profit", MIL),
                  ("Credit cost (bps of loans)", "credit_cost_bps", "0.0"), ("Provisions", "provisions", MIL), ("Pre-tax profit", "pretax", MIL), ("Tax", "tax", MIL), ("Net income", "net_income", MIL),
                  ("Capital", None), ("Dividends", "dividends", MIL), ("Share repurchases", "buybacks", MIL), ("Payout ratio", "payout_ratio", PCT), ("Shareholders' equity", "equity", MIL),
                  ("Tangible equity", "tangible_equity", MIL), ("Risk-weighted assets", "rwa", MIL), ("CET1 capital", "cet1_capital", MIL), ("CET1 ratio", "cet1_ratio", PCT), ("Dividend cut to hold CET1", "distribution_cut", MIL),
                  ("Per share and returns", None), ("Diluted shares (millions)", "shares", MIL), ("EPS", "eps", NUM), ("Dividend per share", "dividend_per_share", NUM), ("Book value per share", "bvps", NUM),
                  ("Tangible book value per share", "tbvps", NUM), ("ROE", "roe", PCT), ("RoTE", "rote", PCT), ("ROA", "roa", PCT)]
    _INSURER_ROWS = [("Premiums", None), ("Net earned premiums", "net_earned_premiums", MIL), ("Premium growth", "premium_growth", PCT), ("Combined ratio", "combined_ratio", PCT),
                     ("Underwriting result", "underwriting_result", MIL), ("Investments", None), ("Average float", "float_avg", MIL), ("Investment yield", "investment_yield", PCT), ("Investment income", "investment_income", MIL),
                     ("Earnings", None), ("Pre-tax profit", "pretax", MIL), ("Tax", "tax", MIL), ("Net income", "net_income", MIL), ("Dividends", "dividends", MIL), ("Shareholders' equity", "equity", MIL), ("Total assets", "total_assets", MIL),
                     ("Per share and returns", None), ("Diluted shares (millions)", "shares", MIL), ("EPS", "eps", NUM), ("Dividend per share", "dividend_per_share", NUM), ("Book value per share", "bvps", NUM),
                     ("Tangible book value per share", "tbvps", NUM), ("ROE", "roe", PCT)]

    def _bank_model_tab(self, th: dict) -> None:
        """The earnings-and-capital model (owner, 2026-10-03): drivers, income statement, capital, per-share
        lines for FY+1E..FY+5E, the assumptions with their sources, and the reconciliation suite."""
        kind = th.get("kind")
        sh = self.sheet("Model", f"{'Bank' if kind == 'bank' else 'Insurer'} earnings-and-capital model FY+1E..FY+5E on the valuation agent's drivers")
        op, a, rows, labels = th.get("opening") or {}, th.get("assumptions") or {}, th.get("rows") or {}, th.get("fy_labels") or []
        n = len(labels)
        ovr = th.get("override") or (self.dr or {}).get("estimate_override") or {}
        sh.title(f"{'Bank' if kind == 'bank' else 'Insurer'} earnings-and-capital model",
                 f"In statement currency, millions. Opening column = {op.get('fy_label')} as filed. Earnings come from the balance sheet (assets × margin, plus fees), costs and provisions "
                 "are ratios, retained earnings build capital against the CET1 target; the GGM (P/B) leg prices the steady-state RoTE and the FY+1 book value, the forward P/E the FY+1 EPS."
                 + (f" USER OVERRIDE ({str(ovr.get('created_at') or '')[:10]}): {', '.join(ovr.get('fields') or [])}." if ovr else "")
                 + (" Guided by management: " + ", ".join(th.get("guided_fields") or []) + "." if th.get("guided_fields") else " No family guidance in the research: drivers from the bank-metrics extraction and the filings."))
        C0 = 3
        r = 4
        sh.label(r, 1, "Fiscal year", bold=True); sh.put(r, C0, f"{op.get('fy_label')}A").font = Font(bold=True)
        for i, lab in enumerate(labels):
            sh.put(r, C0 + 1 + i, lab).font = Font(bold=True)
        r += 2
        sh.section(r, "Assumptions (each with its source)", C0 + n); r += 1
        sh.header(r, ["Assumption", "Value", "Source", "Needed for"]); r += 1
        for k, rec in a.items():
            if k.startswith("_") or not isinstance(rec, dict):
                continue
            v = rec.get("value")
            sh.label(r, 1, k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fmt = PCT if (abs(v) < 1.5 and "bps" not in k and "opening" not in k and k not in ("buyback_annual",)) else (MIL if abs(v) > 1e5 else "0.0")
                sh.put(r, 2, float(v) * (1e-6 if fmt == MIL else 1.0), fmt).font = Font(color=BLUE)
            else:
                sh.put(r, 2, str(v) if v is not None else "—").font = Font(color=BLUE)
            sh.put(r, 3, str(rec.get("source") or "")).font = Font(color=BLACK)
            sh.put(r, 4, str(rec.get("needed_for") or "")).font = Font(color=BLACK)
            r += 1
        r += 1
        opening_map = {"total_assets": op.get("total_assets"), "equity": op.get("equity"), "tangible_equity": op.get("tangible_equity"), "net_income": op.get("net_income"),
                       "total_income": op.get("total_income"), "shares": op.get("shares"), "bvps": op.get("bvps"), "tbvps": op.get("tbvps"), "roe": op.get("roe")}
        _R: dict = {}
        for label, key, *fmt in (self._BANK_ROWS if kind == "bank" else self._INSURER_ROWS):
            if key is None:
                sh.section(r, label, C0 + n); r += 1
                continue
            series = rows.get(key)
            if not series:
                continue
            _R[key] = r
            f = fmt[0]
            sh.label(r, 1, label, bold=key in ("total_income", "net_income", "equity", "eps", "bvps", "cet1_ratio", "roe"))
            sh.label(r, 2, "Model")
            ov = opening_map.get(key)
            if isinstance(ov, (int, float)):
                sh.put(r, C0, float(ov) * (1e-6 if f == MIL else 1.0), f).font = Font(color=BLUE)
            for i, x in enumerate(series[:n]):
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    sh.put(r, C0 + 1 + i, float(x) * (1e-6 if f == MIL else 1.0), f).font = Font(color=BLACK)
            r += 1
        r += 1
        sh.section(r, "Reconciliation suite (engine build)", C0 + n); r += 1
        rc = th.get("reconciliation") or {}
        by_id: dict = {}
        for asrt in rc.get("assertions") or []:
            by_id.setdefault(asrt["id"], (asrt["name"], []))[1].append(asrt["ok"])
        for aid in sorted(by_id):
            name, oks = by_id[aid]
            sh.label(r, 1, f"{aid}. {name}")
            for i, ok in enumerate(oks[:n]):
                sh.put(r, C0 + 1 + i, "OK" if ok else "FAIL").font = Font(color=BLACK, bold=not ok)
            r += 1
        sh.label(r, 1, "Suite result: " + ("ALL OK on every forecast column" if rc.get("ok") else "FAILED"), bold=True); r += 2
        for c in th.get("checks") or []:
            if c.get("roe_over_coe") is not None:
                sh.label(r, 1, f"{c['year']}: ROE {c['roe']:.1%} vs cost of equity {c['coe']:.1%} ({c['roe_over_coe'] * 1e4:+,.0f} bp)"
                               + (f"; CET1 {c['cet1_ratio']:.1%} vs target {c['cet1_target']:.1%}" if c.get("cet1_ratio") is not None else "")); r += 1
        for note in th.get("notes") or []:
            sh.label(r, 1, note); r += 1
        for row in th.get("coverage") or []:
            if str(row.get("status", "")).startswith(("missing", "default")):
                sh.label(r, 1, f"{row.get('assumption')}: {row.get('status')} — {row.get('source')}"); r += 1
        sh.widths({"A": 52, "B": 14, "C": 44, "D": 40, **{get_column_letter(c): 14 for c in range(C0 + 1, C0 + n + 1)}})
        sh.ws.freeze_panes = f"{get_column_letter(C0)}5"
        self.model_rows, self.model_cols, self.model_kind = _R, [get_column_letter(C0 + 1 + i) for i in range(n)], kind

    def _model_link(self, sh: _Sheet, r: int, key: str, first_fc_col: int, fmt: str = MIL) -> bool:
        """FY+1E..FY+5E cells of row `r` linked to the Model tab's `key` row. False when the model has no such row."""
        mr = (self.model_rows or {}).get(key)
        if not mr:
            return False
        for k, L in enumerate(self.model_cols or []):
            sh.put(r, first_fc_col + k, f"='Model'!{L}{mr}", fmt)
        return True

    def _model_note(self, sh: _Sheet, r: int, first_fc_col: int) -> None:
        th = (self.dr or {}).get("three_statements") or {}
        why = th.get("skipped") if isinstance(th, dict) else None
        sh.ws.cell(row=r, column=first_fc_col, value=("Forecast columns: " + (why or "no three-statement forecast on this run"))).font = _NOTE

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
                _pa = sc.get("pipeline_addon") or {}
                _pa_add = (f"+{float(_pa['per_share']):.6f}" if (_pa.get("per_share") and key in (_pa.get("legs") or [])) else "")
                if ref:
                    # Plan IV2: an operating leg carries the pipeline rNPV add-on (per share), stated in the formula.
                    sh.put(r, 5, "=" + ref + _pa_add, NUM)
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
            if sc.get("intrinsic_value") is None:
                # Unrated (pre-revenue, CRSP): the engine publishes no intrinsic value, so there is nothing to tie to.
                sh.label(r, 5, "Unrated: the engine publishes no intrinsic value")
            else:
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
                sh.put(r, 7, f'=IF(ROUND(ABS(E{r}),{A["dp_check"]})<={A["tol"]},"OK","REVIEW")')
        sh.label(9, 1, "Probability-weighted target", bold=True)
        if pb:
            sh.put(9, 3, f"=ROUND(IFERROR(SUMPRODUCT(C5:C7,F5:F7)/SUM(F5:F7),0),{A['dp']})", NUM, bold=True)
        rec = ((self.data.get("scenario_analysis") or {}).get(self.ticker) or {}).get("reconciliation") or {}
        sh.put(9, 4, _num(rec.get("12m_price_target")), NUM)
        if pb:
            sh.put(9, 5, "=C9-D9", NUM)
            sh.put(9, 7, f'=IF(ROUND(ABS(E9),{A["dp_check"]})<={A["tol"]},"OK","REVIEW")')
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
            # Owner, 2026-10-04 (plan 1D.3): the same parts, in the same order, as the WACC tab -- the
            # regime overlay sits INSIDE the base rate; the old wording read as if it were added twice.
            _parts = [("table rate", _num(_bb_.get("table_rate"))),
                      ("leverage premium", _num(_bb_.get("leverage_premium"))),
                      (f"macro-regime overlay ({_bb_.get('macro_regime')})" if _bb_.get("macro_regime") else "macro-regime overlay", _ov_),
                      ("cost-of-debt adjustment", (_num(_wb_.get("wacc")) - _num(_wb_.get("wacc_base")))
                       if _num(_wb_.get("wacc")) is not None and _num(_wb_.get("wacc_base")) is not None else None),
                      ("insider overlay", (_num(_wb_.get("insider_bps")) or 0.0) / 10000.0),
                      ("research risk", _num(_wb_.get("research_risk_loading"))),
                      ("country risk premium", _num(_wb_.get("country_risk_premium"))),
                      ("contracted-revenue discount", _num(_wb_.get("contracted_revenue_discount")))]
            _txt = " ".join(f"{'+' if v >= 0 else '−'} {lab} {abs(v):.2%}" for lab, v in _parts
                            if isinstance(v, (int, float)) and abs(v) > 1e-9).lstrip("+ ")
            sh.ws.cell(row=r, column=1, value=(
                f"WACC used by the valuation {float(self.dr['wacc']):.2%}"
                + (f" = build {float(_wb_['wacc']):.2%}" if isinstance(_wb_.get("wacc"), (int, float)) else "")
                + (f" + macro-regime overlay {_ov_:+.2%}" if _ov_ is not None else "")
                + (f" ({_bb_.get('macro_regime')} regime)" if _bb_.get("macro_regime") else "") + "; parts, as on the WACC tab: "
                + (_txt or "n/a") + " (the regime overlay is inside the base rate, counted once)."))
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
            # Owner, 2026-10-04 (plan EN9): the statement defects the reviews found by eye (D05.SI inventory
            # of -86,700; receivables and payables zeroed in one year; 09988.HK payables 0 -> 0 -> 358.7bn;
            # JD's gross margin doubling on a reclassification) -- listed, not silently carried.
            for key, lab in (("inventory", "Inventory"), ("accounts_receivable", "Receivables"),
                             ("accounts_payable", "Payables"), ("cash_and_equivalents", "Cash")):
                neg = [y for y, x in zip(years, rows) if isinstance(x.get(key), (int, float)) and x.get(key) < 0]
                if neg:
                    gaps.append(("Statements", f"{lab} negative in {', '.join(neg)}", "A balance cannot be negative: a mapping error in the feed", "Medium"))
                vals = [x.get(key) for x in rows]
                for i in range(1, len(vals)):
                    a_, b_ = vals[i - 1], vals[i]
                    if isinstance(a_, (int, float)) and isinstance(b_, (int, float)) and abs(a_) > 0 and b_ == 0:
                        gaps.append(("Statements", f"{lab} drops to zero in FY{years[i]} from {a_ / 1e6:,.0f}m",
                                     "Likely unreported, not zero: working-capital lines that read it are off", "Medium"))
                    if isinstance(a_, (int, float)) and isinstance(b_, (int, float)) and a_ == 0 and abs(b_) > 0 and i >= 2 and vals[i - 2] == 0:
                        gaps.append(("Statements", f"{lab} appears in FY{years[i]} ({b_ / 1e6:,.0f}m) after zero years",
                                     "A reporting change, not a business change: compare like with like", "Medium"))
            _gm = [(_num(x.get("gross_profit")) / _num(x.get("revenue"))) if (_num(x.get("gross_profit")) is not None and _num(x.get("revenue"))) else None
                   for x in rows]
            _prior = [g for g in _gm[:-1] if g is not None]
            if _gm and _gm[-1] is not None and len(_prior) >= 2:
                _med = sorted(_prior)[len(_prior) // 2]
                if _med > 0 and (_gm[-1] / _med > 1.5 or _gm[-1] / _med < 0.67):
                    gaps.append(("Statements", f"FY{years[-1]} gross margin {_gm[-1]:.1%} against {_med:.1%} in prior years",
                                 "A step this size is usually a reclassification of costs; the margin history is not like for like", "Medium"))
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
        # Owner, 2026-10-04 (plan 1A.6): this run's own failures first -- an excluded DCF, a degraded
        # anchor, a replaced margin, a failed forecast check -- not only the structural limits.
        run_gaps = [("This run", line, "Stated on the Summary; read the valuation with it", "High")
                    for line in self.front_page_flags()]
        for f in (self.scen("base").get("forward_flags") or []):
            if any(k in str(f) for k in ("FAILED", "did not build", "DEGRADED", "not compatible")):
                run_gaps.append(("This run", str(f)[:400], "Engine flag", "Medium"))
        sh.header(4, ["Area", "Gap", "Impact", "Severity"])
        r = 5
        for area, gap, impact, sev in run_gaps + gaps + _STRUCTURAL_GAPS:
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
        r += 1
        for _line in self.front_page_flags():
            sh.label(r, 1, _line, bold=True); r += 1
        r += 1
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
            # Owner, 2026-10-04 (plan 1A.6): the units, stated (the statements elsewhere are in millions).
            _sc = (self.dr.get("financials_used") or {}).get("source_currency") or self.ccy   # raw records are as filed
            sh.note(r, 1, f"Units: amounts in {_sc} billions (as filed); per-share figures in {_sc}; ratios as shown."); r += 1
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
        d = self.dcf.get("base")
        if not d and getattr(self, "dcf_excluded", None):
            pass                         # D1: stated once, in the headline flags above
        else:
            sh.section(r, "DCF summary (base)", 6); r += 1
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
                             (self._net_debt_label(), "nd"),
                             ("Less: minority interest", "mi"), ("Less: preferred equity", "pe"),
                             ("Equity value", "eq"),
                             ("DCF value per share", "iv")):
                sh.label(r, 1, lab, indent=1)
                if isinstance(_static.get(key), (int, float)):
                    sh.put(r, 2, float(_static[key]), NUM if key == "iv" else BIG).font = Font(color=BLUE)
                sh.put(r, 3, "=" + d[key], NUM if key == "iv" else BIG)
                r += 1
        elif not getattr(self, "dcf_excluded", None):
            sh.note(r, 1, "No DCF leg recorded for this run."); r += 1
        r += 1
        # Owner, 2026-10-04 (plan EN10): every net-debt figure a reader meets in this workbook, in one
        # place, each with its date and basis -- the reviews found four on four tabs and no reconciliation.
        _fu = self.dr.get("financials_used") or {}
        _rows_nd = []
        _li_b = (self.scen("base").get("leg_inputs") or {})
        _v_nd = next((_num(tr.get("net_debt")) for tr in _li_b.values() if isinstance(tr, dict) and _num(tr.get("net_debt")) is not None), None)
        if _v_nd is not None:
            _rows_nd.append((self._net_debt_label().replace("Less: net debt — ", "Net debt used by every EV leg and the DCF — "), _v_nd, self.ccy))
        _st_rows = self._stmt_rows()
        if _st_rows:
            _x = _st_rows[-1]
            _std = sum(_num(_x.get(k)) or 0.0 for k in ("short_term_debt", "long_term_debt")) - sum(_num(_x.get(k)) or 0.0 for k in ("cash_and_equivalents", "short_term_investments"))
            _rows_nd.append((f"BS tab: debt − cash − short-term investments, FY{str(_x.get('period'))[:4]} as filed (leases excluded)", _std,
                             (self.statements or {}).get("currency") or _fu.get("source_currency") or self.ccy))
        for _n, _tr in _li_b.items():
            if isinstance(_tr, dict) and _tr.get("kind") == "sotp" and isinstance((_tr.get("table") or {}).get("net_cash"), (int, float)):
                _rows_nd.append((f"{_n}: net debt in its NAV (cited net cash where one was accepted)", -float(_tr["table"]["net_cash"]), "SOTP currency"))
        if _rows_nd:
            sh.section(r, "Net debt reconciliation (millions)", 6); r += 1
            for _lab, _v, _c in _rows_nd:
                sh.label(r, 1, _lab, indent=1)
                sh.put(r, 2, _v / 1e6, MIL).font = Font(color=BLUE)
                sh.put(r, 4, _c).font = Font(color=BLACK)
                r += 1
            sh.note(r, 1, "They differ by date (annual vs latest quarter), by what counts as cash (short-term investments; "
                          "regulated or float cash), by leases, and by currency; only the first prices the valuation."); r += 2
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
                                ("Capture" + (f" ({_pb.get('capture_reason')})" if _pb.get("capture_reason") else ""),
                                 "=" + A["capture"], PCT, _cap_v),
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

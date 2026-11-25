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

Scope: the engine values a company from revenue growth x FCF margin (DCF) and
peer multiples. Where the run carries a three-statement forecast it is on the
Model tab (live formulas) and the statements' forecast columns link to it; its
FCF is reconciled to the DCF's (shown, not forced).

Audit 2026-11-25 (CFA / FAST): a Checks tab lists every check with a master
flag on Cover and Summary; peer multiples link to Comps; an unweighted DCF is
built as a labelled shadow; money is in millions throughout; engine outputs
(purple) and reference values (grey) are told apart from inputs (blue); each
Assumptions value carries its source and as-of date; Target carries
sensitivities. The DCF terminal value is the value-driver form (owner,
2026-11-25) when the trace records it, else the Gordon form the run used.
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
# Audit 2026-11-25: an engine OUTPUT recorded in the run (a per-year margin path the engine computed) is not an
# assumption to flex -- purple, not input blue; a recorded constant no formula reads is a reference value -- grey.
ENGINE, GREY = "FF7030A0", "FF808080"
_HDR_FILL = PatternFill("solid", fgColor="D9E1F2")
_OK_FILL = PatternFill("solid", fgColor="E2EFDA")
_FAIL_FILL = PatternFill("solid", fgColor="F8CBAD")
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

#: Gaps between the engine and a full institutional three-statement model -- the claims for a run with NO
#: three-statement build. Audit 2026-11-25 (item 6): Data Gaps now generates its structural lines from what was
#: built (`_Book._structural_gaps`); this list is the no-model fallback only.
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


_SECTIONS: dict[int, list] = {}      # id(worksheet) -> [(row, section title)]

#: Readable names for snake_case payload keys (Valuation paths, Backtest).
_ACRONYMS = {"pv": "PV", "fcf": "FCF", "ev": "EV", "iv": "IV", "wacc": "WACC", "cagr": "CAGR", "npv": "NPV",
             "ppa": "PPA", "mw": "MW", "mwh": "MWh", "eps": "EPS", "ebitda": "EBITDA", "ebit": "EBIT", "yoy": "YoY",
             "ntm": "NTM", "usd": "USD", "pct": "%", "t1": "T-1", "fy": "FY", "capex": "Capex", "opex": "Opex",
             "roic": "ROIC", "irr": "IRR", "tv": "TV", "dcf": "DCF", "id": "ID", "lcoe": "LCOE", "cf": "CF"}


def _humanize(key: Any) -> str:
    """'model_error_pct' -> 'Model error %'; 'forward.price_date' -> 'Forward: price date'."""
    s = str(key)
    if "." in s:
        head, tail = s.split(".", 1)
        return f"{_humanize(head)}: {_humanize(tail)[:1].lower() + _humanize(tail)[1:]}"
    words = [w for w in s.replace("-", "_").split("_") if w]
    out = [_ACRONYMS.get(w.lower(), w) for w in words]
    if out and out[0] == out[0].lower():
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


#: A 2-4 line note at the top of each tab: what it does and how to read it (FAST: every sheet states its purpose).
_METHOD_NOTES = {
    "Summary": "Headline outputs. Column B is the run's engine value (static); column C links to the tab that rebuilds it. "
               "The master check flag reads the Checks tab: ALL OK means every rebuilt figure ties to the engine.",
    "Checks": "One row per check in the workbook. Tie-out checks compare a rebuilt figure with the engine's (0 = ties within one "
              "rounding unit); integrity checks test the model itself (balance, cash, invariants). The master flag feeds Cover and Summary.",
    "Assumptions": "Every driver and constant, with its source and as-of date. Blue = input; purple = engine output recorded in the run "
                   "(not a lever); grey = reference value no formula reads. Money in millions of the valuation currency.",
    "IS": "Reported income statement in millions (FMP). Forecast columns link to the Model tab; the DCF revenue path and consensus sit "
          "below for comparison.",
    "BS": "Reported balance sheet in millions; 'Other' lines are the reported total less the named lines. Forecast columns link to the Model tab.",
    "CFS": "Reported cash flow in millions; net income and D&A link to the IS. Free cash flow and owner earnings (FCF − SBC) are the DCF basis.",
    "Model": "Three-statement forecast on the agent's estimates: assumptions (blue) drive IS → schedules → CF → BS. The reconciliation "
             "suite and the invariants must read OK; the Model-vs-DCF FCF line shows the basis gap, not forced to zero.",
    "WACC": "Discount-rate build: table rate by sector/profile + premia/overlays, then the adjustments the engine applied. "
            "The CAPM line is a cross-check, not the rate used.",
    "DCF": "Revenue × FCF margin per year, discounted at the WACC with mid-window timing; terminal value by Gordon growth. "
           "Money in millions; value per share = equity ÷ diluted shares (millions). Check = rebuilt − engine.",
    "Multiples": "Each relative-valuation leg: metric × (peer multiple from Comps × scenario band × growth premium × other adjustments) "
                 "→ EV → equity → per share. Net debt and shares link to Assumptions. Money in millions.",
    "Comps": "The peer basket behind every peer multiple: members and values where the run froze them, the median, and the engine's "
             "figure. Through-cycle (dynamic) multiples show their source; members are not recorded for them.",
    "SOTP": "Segment values (metric × multiple) flexed per scenario, plus associates and net cash, less minorities and the holdco "
            "discount. Money in millions of the SOTP currency.",
    "Blend": "Intrinsic value = Σ (effective weight × leg value) × calibration, per scenario. Leg values link to the tab that rebuilds them.",
    "Target": "12-month target = spot + capture × (IV − spot) per scenario, probability-weighted. Sensitivities below flex the anchor "
              "leg's peer multiple and metric, the capture and the scenario probabilities, and (cyclicals) the margin.",
    "Backtest": "Today's methodology on the previous fiscal year (T-1) against the price then; the gate ledger; the run's flags and the "
                "WACC the valuation used.",
    "Data Gaps": "Generated from this build: what the data or the engine could not supply, and what the model does not do. "
                 "Lines about the run come first.",
    "Guidance": "Management guidance as stated, consensus, the agent's bear/base/bull estimates and the growth path the base DCF ran on.",
    "Family": "The profile's report family: what the write-up must state, and the checklist computed from this run.",
    "Banks": "Justified P/B = (ROE − g) / (CoE − g), bounded by the engine's floor and cap, × book value per share.",
    "Pipeline": "Each drug asset: peak sales × economics × probability of success, discounted (rNPV). Totals tie to the engine.",
    "Products": "Revenue by product and the cost build at historical ratios, tied to the forecast the DCF runs on.",
    "REIT": "Each REIT leg rebuilt from the run's REIT record and leg traces: S-REIT DDM = capitalised DPU / (CoE − g) × band; "
            "NAV = (NOI / cap rate − debt + cash) / units; P/AFFO = AFFO per unit × sub-type multiple × band × growth premium.",
    "Valuation paths": "The year-by-year cash path each overlay leg (PPA project finance, depleting asset) discounts, as the engine recorded it.",
}


#: Tabs whose Check rows carry one check per column (scenario / product); every formula cell counts. The rest read the
#: first formula cell right of the label. scripts/workbook_tieout.py reads this constant.
_MULTI_COL_CHECK_TABS = ("Products", "REIT")

#: Tabs whose checks are not engine tie-outs: the Model's integrity suite (registered as Integrity) and the reported
#: statements' data-consistency rows (registered as Statement). scripts/workbook_tieout.py reads them from Checks.
_NON_TIEOUT_TABS = ("Model", "IS", "BS", "CFS")


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
        # the Checks tab names each check by the section it sits in
        _SECTIONS.setdefault(id(self.ws), []).append((r, str(text)))

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
    if key == "terminal_vd":
        # Owner, 2026-11-25: a trace that records the value-driver terminal inputs was priced with it
        return (1.0 if any(k in tr for k in ("terminal_nopat_ratio", "terminal_ronic_spread"))
                or tr.get("terminal_formula") == "value_driver" else 0.0) if tr else None
    if key == "terminal_nopat_ratio":
        v = tr.get(key)
        if v is None:
            v = (((tr.get("guidance_forecast") or {}).get("terminal") or {}).get("nopat_to_ufcf"))
        return float(v) if isinstance(v, (int, float)) and v > 0 else (1.0 if tr else None)
    if key == "terminal_ronic_spread":
        v = tr.get(key)
        return float(v) if isinstance(v, (int, float)) else (0.0 if tr else None)
    if key == "terminal_loe_multiplier":                   # plan I14: 1 when no LOE input priced
        v = tr.get(key)
        return float(v) if isinstance(v, (int, float)) else (1.0 if tr else None)
    v = tr.get(key)
    if key == "margin_delta_absolute" and v is None and tr:
        return 0.0
    return v


_DCF_DRIVER_KEYS = ("revenue_base", "fcf_margin_base", "margin_delta_absolute", "fcf_floor",
                    "tgr", "net_debt", "shares", "wacc", "terminal_nopat_ratio", "terminal_ronic_spread")


_TERMINAL_KEYS = ("terminal_nopat_ratio", "terminal_ronic_spread", "terminal_formula")


def _with_core_terminal(tr: dict, core: dict) -> dict:
    """A DCF-family leg's trace with the value-driver terminal inputs it was priced on.

    Tie-out 2026-11-25 (00941.HK, "DCF (2-stage)"): every DCF-family leg projects through `_project_dcf` with the
    scenario's shared projection context -- the same `terminal_nopat_ratio` / `terminal_ronic_spread` the core "DCF"
    trace records -- but the family leg's own trace leaves them out. Read without them, the leg was rebuilt on the
    Gordon terminal (bear 177.56 against the engine's 178.13; base / bull 9.19 / 28.15 apart). A trace that records
    none of them takes the core DCF's, and only when the core DCF records the value-driver form (under the Gordon
    kill switch the leg's own trace already rebuilds as Gordon).
    """
    if not core or not tr or any(k in tr for k in _TERMINAL_KEYS):
        return tr
    if core.get("terminal_formula") != "value_driver":
        return tr
    return {**tr, **{k: core[k] for k in _TERMINAL_KEYS if k in core}}


_MILLION_KEYS = ("revenue_base", "net_debt", "shares")


def _scaled(key: str, v: Any) -> Optional[float]:
    """Audit 2026-11-25 (item 5): money and share counts in millions on every tab."""
    f = _num(v)
    return (f / 1e6) if (f is not None and key in _MILLION_KEYS) else f


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
        self.src: dict[str, tuple] = {}                # assumption name -> (source, as of)
        self.integrity: list[tuple] = []               # (area, description, ref or literal) -- model honesty checks
        self.info_checks: list[tuple] = []             # (area, description, ref) -- shown on Checks, outside the master flag
        self.stmt_checks: list[tuple] = []             # (kind, tab, description, ref) -- reported-statement data consistency
        self.peer_cell: dict[tuple, str] = {}          # (source key, value) -> Comps cell holding the peer multiple
        self.leg_meta: dict[tuple, dict] = {}          # (scenario, leg) -> {"kind", "row"} on Multiples
        self.blend_w: dict[tuple, str] = {}            # (scenario, leg) -> Blend weight cell
        self.blend_raw: dict[str, str] = {}            # scenario -> Blend sum before calibration

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
        # Owner, 2026-10-07: a share-count failure is corrected, not only reported -- the banner says what failed,
        # the three EPS figures, what was done and what the check reads after.
        _sci = (self.dr.get("guidance_forecast") or {}).get("share_count_intervention") or {}
        if _sci:
            _f = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else "n/a"   # noqa: E731
            out.append(f"FORECAST CHECK CORRECTED — Share-count integrity failed on the research EPS ({_sci.get('check_failed')}). "
                       f"Research EPS {_f(_sci.get('eps_research'))}; model net income / diluted shares {_f(_sci.get('eps_model_reported'))} "
                       f"(reported); street {_f(_sci.get('eps_street_adjusted'))} ({_sci.get('street_analysts')} analysts, adjusted; "
                       f"adjusted / reported {_num(_sci.get('adjusted_reported_ratio')) or 1.0:.2f}). Action: {_sci.get('note')}. "
                       f"Check after: {_sci.get('check_after')}.")
        # Audit 2026-11-25: a three-statement build that failed its reconciliation is said on the front page, not
        # only in a static table on the Model tab.
        _th = self.dr.get("three_statements") or {}
        _rc = (_th.get("reconciliation") or {}) if isinstance(_th, dict) else {}
        if _rc.get("failures") or (_rc and _rc.get("ok") is False):
            _f = _rc.get("failures") or []
            out.append("THREE-STATEMENT MODEL FAILED its reconciliation suite ("
                       + ("; ".join(f"{x.get('year')} #{x.get('id')} {x.get('name')}" for x in _f[:4]) or "see the Model tab")
                       + (f"; +{len(_f) - 4} more" if len(_f) > 4 else "")
                       + ")" + (f": {_th.get('skipped')}" if _th.get("skipped") else "")
                       + ". The Model tab's forecast is not used for any figure here; trace before relying on it.")
        return out

    def dcf_present(self) -> bool:
        """Audit 2026-11-25: a DCF projection was recorded (weighted or not). An unweighted one is built as a
        labelled shadow so DCF-specific failures stay traceable."""
        return any(((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("projection_rows") for s in SCENARIOS) \
            or self.dcf_weighted()

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
        self._model_fcf_reconciliation()
        # Audit 2026-11-25: Comps is built first so each leg's peer multiple on Multiples links to the Comps cell
        # it came from; the tab is moved back behind Multiples below.
        self.comps_tab()
        self.reit_tab()
        self.multiples_tab()
        # Owner, 2026-10-07: every drug-developer valuation carries a Pipeline tab -- the assets, phase, peak sales
        # and probability of success the rNPV priced (the frontend's pipeline card, in the workbook).
        if self._has_pipeline():
            self.pipeline_tab()
        # Owner, 2026-10-07: drug developers -- revenue by product (partnership revenue tagged) and the cost build.
        if (self.dr or {}).get("product_build"):
            self.products_tab()
        # Owner, 2026-10-03 (SBUX review, A10): a SOTP tab only when a SOTP leg carries weight;
        # an unweighted SOTP (segments) trace used to create the tab and then empty it.
        if any(tr.get("kind") == "sotp" and self.in_blend(name) for sc in SCENARIOS
               for name, tr in (self.scen(sc).get("leg_inputs") or {}).items()):
            self.sotp_tab()
        if self._has_bank():
            self.banks_tab()
        # Cross-sector audit E7 (2026-10-10): a weighted leg that values its own path (PPA project finance, depleting
        # asset) shows that path -- one revenue / cash path per leg, on a tab, never only inside the trace.
        if self._overlay_paths():
            self.paths_tab()
        if (self.dr or {}).get("guidance_estimates"):
            self.guidance_tab()                 # owner, 2026-10-03: guidance → estimates, as the DCF used them
        self.family_tab()
        self.blend_tab()
        self.target_tab()
        self.backtest_tab()
        self.gaps_tab()
        self.summary_tab(summary)
        self.checks_tab()
        # the explicit order: Model directly after CFS, Multiples before Comps, Checks after Summary
        for _name, _after in (("Model", "CFS"), ("Multiples", "WACC" if "DCF" not in self.wb.sheetnames else "DCF"),
                              ("REIT", "Multiples"), ("Checks", "Summary")):
            self._move_after(_name, _after)
        self._mark_reference_inputs()
        self._methodology_notes()
        self.cover_tab(cover)
        for ws in self.wb.worksheets:
            _SECTIONS.pop(id(ws), None)
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()

    def _move_after(self, name: str, after: str) -> None:
        if name not in self.wb.sheetnames or after not in self.wb.sheetnames:
            return
        _ws = self.wb[name]
        _sheets = self.wb._sheets
        _sheets.remove(_ws)
        _sheets.insert(_sheets.index(self.wb[after]) + 1, _ws)
        _t = next((t for t in self.tabs if t[0] == name), None)
        if _t and any(t[0] == after for t in self.tabs):
            self.tabs.remove(_t)
            self.tabs.insert(next(i for i, t in enumerate(self.tabs) if t[0] == after) + 1, _t)

    # ── Checks (audit 2026-11-25) ───────────────────────────────────────────
    def _scan_checks(self) -> list[tuple]:
        """Every tie-out check cell in the workbook: a cell labelled Check… (each formula cell to its right),
        the Target status cells and the Multiples Check column -- the cells scripts/workbook_tieout.py reads."""
        out = []
        for ws in self.wb.worksheets:
            # Audit 2026-11-25 (HK/SG defect 5): the Model's reconciliation rows are integrity checks (registered in
            # self.integrity: the suite result per column) -- scanning them here labelled them Tie-out and counted
            # Model!D116 twice; the statements' rows are data-consistency checks (registered by _statement_checks).
            if ws.title in ("Checks", "Cover") + _NON_TIEOUT_TABS:
                continue
            secs = sorted(_SECTIONS.get(id(ws), []))
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    if not isinstance(v, str):
                        continue
                    def _ctx(r_: int) -> str:
                        s_ = [t for rr, t in secs if rr <= r_]
                        return s_[-1] if s_ else ""
                    if v.strip().startswith("Check"):
                        for cc in row[c.column:]:
                            if isinstance(cc.value, str) and cc.value.startswith("="):
                                hdr = ""
                                if ws.title in _MULTI_COL_CHECK_TABS:
                                    hdr = f" ({get_column_letter(cc.column)})"
                                out.append((ws.title, _ctx(c.row), v.strip() + hdr, cc.coordinate))
                                if ws.title not in _MULTI_COL_CHECK_TABS:
                                    break
                    elif (v.startswith("=IF(ABS(") or v.startswith("=IF(ROUND(ABS(")) and '"REVIEW"' in v:
                        lab = ws.cell(row=c.row, column=1).value
                        out.append((ws.title, _ctx(c.row), f"Target tie-out: {lab}", c.coordinate))
                    elif ws.title == "Multiples" and c.column == 17 and v.startswith("=") and c.row > 4:
                        out.append((ws.title, "Relative valuation legs",
                                    f"{ws.cell(row=c.row, column=1).value} — {ws.cell(row=c.row, column=2).value}: rebuilt − engine",
                                    c.coordinate))
        return out

    def checks_tab(self) -> None:
        """One row per check across the workbook, each with its status, and the master flag (C4) that Cover and
        Summary show. Tie-out checks (rebuilt − engine) and integrity checks (the model's own consistency) are
        counted apart; information rows (a reconciliation shown, not forced) sit below, outside the flag."""
        sh = self.sheet("Checks", "Every check in the workbook with its status; master OK/FAIL flag")
        sh.title("Checks", "Tie-out: a rebuilt figure against the engine's (0 within one rounding unit). Integrity: the "
                           "model's own consistency (balance, cash, invariants).")
        A = self.A
        tie = self._scan_checks()
        r = 8
        sh.header(r, ["Kind", "Tab", "Section", "Check", "Value", "Status", "Go to"]); r += 1
        first = r

        def status(ref: str) -> str:
            return (f'=IFERROR(IF(ISNUMBER({ref}),IF(ROUND(ABS({ref}),{A["dp_check"]})<={A["tol"]},"OK","FAIL"),'
                    f'IF(OR({ref}="OK",{ref}="ALL OK",{ref}="n/a",{ref}=""),"OK","FAIL")),"FAIL")')

        def row(kind: str, tab: str, sec: str, desc: str, ref: Any) -> None:
            nonlocal r
            sh.put(r, 1, kind).font = Font(color=BLACK)
            sh.put(r, 2, tab).font = Font(color=BLACK)
            sh.put(r, 3, (sec or "")[:90]).font = Font(color=BLACK)
            sh.put(r, 4, str(desc)[:200]).font = Font(color=BLACK)
            if isinstance(ref, str) and "!" in ref:
                sh.put(r, 5, "=" + ref, NUM)
                c = sh.ws.cell(row=r, column=7, value=ref.replace("$", ""))
                c.hyperlink = "#" + ref.replace("$", "")
                c.font = Font(color=BLUE, underline="single")
            else:
                sh.put(r, 5, ref).font = Font(color=BLUE)
            sh.put(r, 6, status(f"E{r}") if kind != "Information" else "info")
            r += 1

        for tab, sec, desc, cell in tie:
            row("Tie-out", tab, sec, desc, _ref(tab, int("".join(ch for ch in cell if ch.isdigit())),
                                                 _col("".join(ch for ch in cell if ch.isalpha()))))
        for area, desc, ref in self.integrity:
            tab = ref.split("!")[0].strip("'") if isinstance(ref, str) and "!" in ref else area
            row("Integrity", tab, area, desc, ref)
        # Audit 2026-11-25 (HK/SG defect 5): the reported statements' own tie-outs count in the master flag -- the flag
        # read ALL OK while IS row 23 (EBT vs reported) was -393.6 on Z74 and -34,858 on 01299.
        for kind, tab, desc, ref in self.stmt_checks:
            row(kind, tab, "Reported statements (FMP)", desc, ref)
        last = r - 1
        if last < first:
            sh.note(r, 1, "No checks were built for this run."); r += 1
        rng = f"F{first}:F{max(first, last)}"
        kinds = f"A{first}:A{max(first, last)}"
        sh.label(4, 1, "Master flag", bold=True)
        sh.put(4, 3, f'=IF(COUNTIF({rng},"FAIL")=0,"ALL OK","FAIL")', bold=True)
        sh.label(5, 1, "Tie-out checks failing (rebuilt − engine)")
        sh.put(5, 3, f'=COUNTIFS({kinds},"Tie-out",{rng},"FAIL")', "0")
        sh.label(6, 1, "Integrity checks failing (model consistency)")
        sh.put(6, 3, f'=COUNTIFS({kinds},"Integrity",{rng},"FAIL")', "0")
        sh.label(7, 1, "Statement checks failing (reported data does not tie; statement mapping for insurers)")
        sh.put(7, 3, f'=COUNTIFS({kinds},"Statement",{rng},"FAIL")+COUNTIFS({kinds},"Statement mapping",{rng},"FAIL")', "0")
        sh.put(4, 4, f"=COUNTA({kinds})", "0")
        sh.note(4, 5, "checks in the workbook")
        self.master_flag = "'Checks'!$C$4"
        if self.info_checks:
            r += 1
            sh.section(r, "Information: reconciliations shown, not forced (outside the master flag)", 7); r += 1
            for area, desc, ref in self.info_checks:
                row("Information", area, "", desc, ref)
        sh.widths({"A": 11, "B": 12, "C": 40, "D": 60, "E": 14, "F": 9, "G": 22})
        sh.ws.freeze_panes = "A9"

    def _statement_checks(self, sh: _Sheet, row: int, rows: list[dict], c0: int, need: str, tab: str, what: str) -> None:
        """Register a reported-statement tie-out row (historical columns where the reported figure exists) for the
        Checks tab. An insurer's FMP income statement does not map to gross profit / operating expenses, so its IS row is
        an explicit "Statement mapping" failure, not a silent omission (audit 2026-11-25, HK/SG defect 5)."""
        try:
            from src.data.report_families import report_family_for
            _ins = report_family_for(self.dr.get("profile")) == "Insurance"
        except Exception:                                  # noqa: BLE001
            _ins = False
        _ins = _ins or getattr(self, "model_kind", None) == "insurer"
        kind = "Statement mapping" if (_ins and tab == "IS") else "Statement"
        for i, x in enumerate(rows):
            if _num(x.get(need)) is None:
                continue
            ref = _ref(tab, row, c0 + i)
            desc = (f"FY{str(x.get('period') or '')[:4]}: {what}"
                    + ("; FMP's insurer income statement does not map to gross profit / opex (valuation unaffected: the "
                       "legs read reported net income, book and embedded value)" if kind == "Statement mapping" else ""))
            self.stmt_checks.append((kind, tab, desc, ref))

    # ── finishing passes ────────────────────────────────────────────────────
    def _mark_reference_inputs(self) -> None:
        """Audit 2026-11-25 (dead inputs): an Assumptions value no formula in the workbook reads is a reference value
        -- grey, said so beside it -- not a blue input that looks like a lever."""
        text = []
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value.startswith("="):
                        text.append(c.value.replace("$", ""))
        blob = "\n".join(text)
        if "Assumptions" not in self.wb.sheetnames:
            return
        ws = self.wb["Assumptions"]
        dead_rows: dict[int, int] = {}
        for name, ref in self.A.items():
            if not ref or not str(ref).startswith("'Assumptions'!"):
                continue
            cell = str(ref).split("!")[1].replace("$", "")
            plain = f"'Assumptions'!{cell}"
            if plain in blob:
                continue
            c = ws[cell]
            if c.value is None or (isinstance(c.value, str) and not c.value.startswith("=")):
                continue
            c.font = Font(color=GREY, italic=True)
            dead_rows[c.row] = dead_rows.get(c.row, 0) + 1
        for rr in dead_rows:
            n = ws.cell(row=rr, column=8)
            if n.value is None:
                n.value = "Reference value: no formula reads it (shown for the record, not a lever)"
                n.font = _NOTE

    def _methodology_notes(self) -> None:
        _fc = (self.dr.get("wacc_build") or {}).get("firm_capm")
        over = {"WACC": "Firm CAPM build (the WACC used): rf, relevered industry beta blended with the Blume-adjusted own beta, "
                        "ERP and CRP for the cost of equity; the after-tax cost of debt at market weights; every input on "
                        "Assumptions. The sector table rate is a memo; the legacy CAPM line is a cross-check."} if _fc else {}
        for ws in self.wb.worksheets:
            note = over.get(ws.title) or _METHOD_NOTES.get(ws.title)
            if not note:
                continue
            c = ws.cell(row=3, column=1)
            c.value = note if c.value in (None, "") else f"{note} | {c.value}"
            c.font = _NOTE
            c.alignment = Alignment(wrap_text=False)

    def _overlay_paths(self) -> list:
        out = []
        for s_ in SCENARIOS:
            for name, tr in (self.scen(s_).get("leg_inputs") or {}).items():
                if (isinstance(tr, dict) and tr.get("kind") in ("ppa_dcf", "depleting_dcf") and tr.get("rows")
                        and self.in_blend(name)):
                    out.append((s_, name, tr))
        return out

    def paths_tab(self) -> None:
        sh = self.sheet("Valuation paths", "The year-by-year path each overlay leg values (PPA project finance, depleting asset)")
        sh.title("Valuation paths", "The cash path each overlay leg discounts, as the engine recorded it (money as recorded).")
        r = 4
        for s_, name, tr in self._overlay_paths():
            rows = [x for x in tr.get("rows") or [] if isinstance(x, dict)]
            if not rows:
                continue
            cols = [k for k in rows[0].keys()]
            sh.section(r, f"{name} -- {s_}", max(4, len(cols)))
            r += 1
            # audit 2026-11-25: readable headers, not the payload's snake_case keys
            sh.header(r, [_humanize(k) for k in cols])
            r += 1
            for row in rows:
                for j, k in enumerate(cols):
                    v = row.get(k)
                    sh.put(r, 1 + j, v if isinstance(v, (int, float, str, bool)) or v is None else str(v))
                r += 1
            r += 1

    def _has_pipeline(self) -> bool:
        return bool(any(((self.scen(s).get("rnpv_audit") or {}).get("assets")) for s in SCENARIOS)
                    or (self.dr or {}).get("pipeline_input")
                    or ((self.data.get("pipeline_assets") or {}).get(self.ticker)))

    def pipeline_tab(self) -> None:
        """Pipeline assets as priced: per asset the phase, launch, peak sales (company share), probability of
        success and risk-adjusted PV (= unrisked PV x PoS, a formula), the totals checked against the engine;
        then the input's status, its peak-sales sources, exclusions and the primary-source check."""
        sh = self.sheet("Pipeline", "Drug pipeline: assets, phase, peak sales, probability of success, risk-adjusted value")
        pin = (self.dr or {}).get("pipeline_input") or {}
        base = self.scen("base")
        aud = base.get("rnpv_audit") or {}
        addon = base.get("pipeline_addon") or {}
        _li_p = base.get("leg_inputs") or {}
        _leg = (_li_p.get("rNPV (Pipeline)") or _li_p.get("rNPV") or {})
        status = pin.get("status") or ("extractor (no owner input on record)" if not aud else "priced")
        role = ("priced as an add-on to every operating leg" if addon.get("per_share") else
                "priced as the rNPV leg" if _leg.get("value") is not None else
                "NOT priced on this run (quarantined until an input is accepted, or not used by this profile)")
        sh.title("Pipeline assets (rNPV)", f"Input status: {status}" + (f"; as of {pin.get('as_of')}" if pin.get("as_of") else "")
                 + f"; {role}")
        r = 4
        assets = aud.get("assets") or []
        if assets:
            sh.section(r, "Priced assets (base scenario, company share)", 14); r += 1
            sh.header(r, ["Asset", "Indication", "Phase", "Launch", "Peak sales (m)", "Economic share", "Royalty payable",
                          "Phase PoS", "Area multiplier", "PoS used", "Years to launch", "Unrisked PV (m)",
                          "Risk-adjusted PV (m)", "PoS basis"]); r += 1
            first = r
            for a in assets:
                sh.put(r, 1, a.get("name")).font = Font(color=BLACK)
                sh.put(r, 2, a.get("indication") or "").font = Font(color=BLACK)
                sh.put(r, 3, a.get("phase") or "").font = Font(color=BLACK)
                sh.put(r, 4, _num(a.get("launch_year")), "0")
                sh.put(r, 5, (_num(a.get("peak_sales_usd")) or 0.0) / 1e6, MIL)
                sh.put(r, 6, _num(a.get("economic_share")), PCT)
                sh.put(r, 7, _num(a.get("royalty_payable")), PCT)
                sh.put(r, 8, _num(a.get("base_phase_pos")), PCT)
                sh.put(r, 9, _num(a.get("ta_multiplier")), "0.00")
                sh.put(r, 10, _num(a.get("effective_pos")), PCT)
                sh.put(r, 11, _num(a.get("years_to_launch")), "0.0")
                _upv = _num(a.get("unrisked_pv"))
                if _upv is None:
                    _upv = _num(a.get("undiscounted_cf"))
                sh.put(r, 12, (_upv or 0.0) / 1e6, MIL)
                sh.put(r, 13, f"=L{r}*J{r}", MIL)
                _pb = a.get("ptrs_basis") or ("cited PTRS" if a.get("ptrs_override") else "phase x therapeutic-area table")
                sh.put(r, 14, str(_pb)[:120]).font = Font(color=BLACK)
                r += 1
            last = r - 1
            sh.label(r, 12, "Pipeline PV", bold=True)
            sh.put(r, 13, f"=SUM(M{first}:M{last})", MIL, bold=True); _tot = r; r += 1
            sh.label(r, 12, "Engine pipeline PV")
            sh.put(r, 13, (_num(aud.get("pipeline_pv")) or 0.0) / 1e6, MIL); _eng = r; r += 1
            sh.label(r, 12, "Check")
            sh.put(r, 13, f"=M{_tot}-M{_eng}", MIL); r += 1
            _shs = _num(aud.get("shares_diluted"))
            if addon.get("per_share") is not None and _shs:
                # the add-on prices the UNAPPROVED assets only: an approved product's sales are in the operating legs
                _unap = [i for i, a in enumerate(assets) if a.get("phase") != "approved"]
                _cells = "+".join(f"M{first + i}" for i in _unap) or "0"
                r += 1
                sh.label(r, 1, "Add-on per diluted share (unapproved assets / diluted shares)", bold=True)
                sh.label(r, 4, "Diluted shares (m)")
                sh.put(r, 5, _shs / 1e6, MIL)
                sh.put(r, 13, f"=({_cells})/E{r}", NUM, bold=True); _ps = r; r += 1
                sh.label(r, 12, "Engine add-on")
                sh.put(r, 13, _num(addon.get("per_share")), NUM); r += 1
                sh.label(r, 12, "Check")
                sh.put(r, 13, f"=M{_ps}-M{r - 1}", NUM); r += 1
                sh.note(r, 1, "The add-on is added to each operating leg (" + ", ".join(addon.get("legs") or [])
                        + "); approved assets are listed but not added, their sales being in the revenue the operating "
                          "legs price."); r += 1
            elif _leg.get("value") is not None and _shs:
                r += 1
                sh.section(r, "rNPV leg: equity bridge (millions)", 6); r += 1
                _b0 = r
                for lab, v in (("Pipeline PV", f"=M{_tot}"), ("Add: cash", (_num(aud.get("cash")) or 0.0) / 1e6),
                               ("Less: debt", -(_num(aud.get("debt")) or 0.0) / 1e6),
                               ("Less: PV of future R&D (the valued assets' share)", -(_num(aud.get("future_rd_pv")) or 0.0) / 1e6),
                               ("Less: PV of G&A to launch", -(_num(aud.get("future_ga_pv")) or 0.0) / 1e6),
                               ("Less: minority and preferred", -(_num(aud.get("minority_and_preferred")) or 0.0) / 1e6)):
                    sh.label(r, 1, lab, indent=1)
                    sh.put(r, 2, v, MIL); r += 1
                sh.label(r, 1, "Equity value", bold=True)
                sh.put(r, 2, f"=SUM(B{_b0}:B{r - 1})", MIL, bold=True); _eq = r; r += 1
                sh.label(r, 1, "Diluted shares (m)")
                sh.put(r, 2, _shs / 1e6, MIL); r += 1
                sh.label(r, 1, "rNPV per share", bold=True)
                sh.put(r, 2, f"=MAX(B{_eq}/B{r - 1},0)", NUM, bold=True); _iv = r; r += 1
                sh.label(r, 1, "Engine rNPV leg")
                sh.put(r, 2, _num(_leg.get("value")), NUM); r += 1
                sh.label(r, 1, "Check")
                sh.put(r, 2, f"=B{_iv}-B{r - 1}", NUM); r += 1
            r += 1
            sh.section(r, "Pipeline PV by scenario (peak sales scaled per scenario)", 4); r += 1
            sh.header(r, ["Scenario", "Peak multiplier", "Assets", "Pipeline PV (m)"]); r += 1
            for sc in SCENARIOS:
                a_ = self.scen(sc).get("rnpv_audit") or {}
                if not a_:
                    continue
                sh.label(r, 1, sc)
                sh.put(r, 2, _num(a_.get("peak_scenario_multiplier")), "0.00")
                sh.put(r, 3, _num(a_.get("n_assets")), "0")
                sh.put(r, 4, (_num(a_.get("pipeline_pv")) or 0.0) / 1e6, MIL)
                r += 1
        else:
            ext = (self.data.get("pipeline_assets") or {}).get(self.ticker) or []
            sh.note(r, 1, "No asset was priced on this run. The research's pipeline, for reference (not valued):"); r += 1
            sh.header(r, ["Asset", "Indication", "Phase", "Launch", "Peak sales (m)", "Evidence"]); r += 1
            for a in ext:
                sh.put(r, 1, a.get("name")).font = Font(color=BLACK)
                sh.put(r, 2, a.get("indication") or "").font = Font(color=BLACK)
                sh.put(r, 3, a.get("phase") or "").font = Font(color=BLACK)
                sh.put(r, 4, _num(a.get("launch_year")), "0")
                sh.put(r, 5, (_num(a.get("peak_sales_usd")) or 0.0) / 1e6, MIL)
                sh.put(r, 6, str(a.get("evidence") or "")[:140]).font = Font(color=BLACK)
                r += 1
        if pin.get("assets"):
            r += 1
            sh.section(r, "Peak-sales sources (owner-reviewed input)", 4); r += 1
            sh.header(r, ["Asset", "Phase", "Peak period", "Source"]); r += 1
            for a in pin["assets"]:
                sh.put(r, 1, a.get("name")).font = Font(color=BLACK)
                sh.put(r, 2, a.get("phase") or "").font = Font(color=BLACK)
                sh.put(r, 3, str(a.get("peak_period") or "")).font = Font(color=BLACK)
                sh.put(r, 4, str(a.get("peak_source") or "")[:160]).font = Font(color=BLACK)
                r += 1
        if pin.get("excluded_assets"):
            r += 1
            sh.section(r, "Excluded programmes (not valued) and why", 2); r += 1
            for x in pin["excluded_assets"]:
                sh.put(r, 1, x.get("name")).font = Font(color=BLACK)
                sh.put(r, 2, str(x.get("reason") or "")[:200]).font = Font(color=BLACK)
                r += 1
        if pin.get("approved_portfolio"):
            r += 1
            sh.label(r, 1, ("Marketed products (in the operating legs' revenue): "
                            + ", ".join(str(x) for x in pin["approved_portfolio"]))[:900])
            r += 1
        if pin.get("primary_sources"):
            r += 1
            sh.section(r, "Primary-source check (10-K, earnings 8-K, earnings call)", 2); r += 1
            for x in pin["primary_sources"]:
                sh.put(r, 1, x.get("name")).font = Font(color=BLACK)
                sh.put(r, 2, str(x.get("verdict") or "")).font = Font(color=BLACK)
                r += 1

    def products_tab(self) -> None:
        """Revenue by product and a line-by-line cost build, both tying to the forecast the DCF runs on."""
        pb = (self.dr or {}).get("product_build") or {}
        years = pb.get("years") or []
        n = len(years)
        if not n:
            return
        sh = self.sheet("Products", "Revenue by product (partnership revenue tagged) and the line-by-line cost build")
        sh.title("Revenue by product and cost build (base scenario, millions)",
                 pb.get("basis") or "No product input on record for this company: the company total and the cost build only")
        c0 = 4
        r = 4
        sh.header(r, ["Line", "Type", "LOE"] + years); r += 1
        first = r
        for ln in pb.get("products") or []:
            sh.put(r, 1, ln.get("name")).font = Font(color=BLACK)
            sh.put(r, 2, ln.get("type") or "").font = Font(color=BLACK)
            sh.put(r, 3, _num(ln.get("loe_year")), "0")
            for t, v in enumerate((ln.get("values") or [])[:n]):
                sh.put(r, c0 + t, (_num(v) or 0.0) / 1e6, MIL)
            r += 1
        if pb.get("other") is not None:
            sh.put(r, 1, "Other products and new launches (remainder)").font = Font(color=BLACK)
            sh.put(r, 2, "remainder").font = Font(color=BLACK)
            for t, v in enumerate(pb["other"][:n]):
                sh.put(r, c0 + t, (_num(v) or 0.0) / 1e6, MIL)
            r += 1
        last = r - 1
        has_lines = last >= first
        L = get_column_letter
        sh.label(r, 1, "Total revenue", bold=True); _tr = r
        for t in range(n):
            if has_lines:
                sh.put(r, c0 + t, f"=SUM({L(c0 + t)}{first}:{L(c0 + t)}{last})", MIL, bold=True)
            else:
                sh.put(r, c0 + t, (_num(pb["revenue_total"][t]) or 0.0) / 1e6, MIL, bold=True)
        r += 1
        sh.label(r, 1, "Forecast revenue (Model / Guidance, the DCF's path)"); _fr = r
        for t in range(n):
            sh.put(r, c0 + t, (_num(pb["revenue_total"][t]) or 0.0) / 1e6, MIL)
        r += 1
        if has_lines:
            sh.label(r, 1, "Check")
            for t in range(n):
                sh.put(r, c0 + t, f"={L(c0 + t)}{_tr}-{L(c0 + t)}{_fr}", MIL)
            r += 1
            _part = [i for i, ln in enumerate(pb.get("products") or []) if ln.get("type") == "partnership"]
            if _part:
                sh.label(r, 1, "Partnership revenue (collaboration / royalty lines)")
                for t in range(n):
                    sh.put(r, c0 + t, "=" + "+".join(f"{L(c0 + t)}{first + i}" for i in _part), MIL)
                r += 1
                sh.label(r, 1, "Partnership share of revenue")
                for t in range(n):
                    sh.put(r, c0 + t, f"=IFERROR({L(c0 + t)}{r - 1}/{L(c0 + t)}{_tr},0)", PCT)
                r += 1
        cs = pb.get("costs")
        if cs:
            r += 1
            ra = cs.get("ratios") or {}
            sh.section(r, "Cost build: historical ratios to revenue (" + ", ".join(ra.get("years") or []) + " average)", 6); r += 1
            sh.header(r, ["Line", "Ratio", ""] + years); r += 1
            _cost_rows = []
            for key, lab in (("cost_of_revenue", "Cost of revenue"), ("research_and_development", "Research and development"),
                             ("sga_and_other", "SG&A and other operating expense")):
                sh.label(r, 1, lab)
                sh.put(r, 2, _num(ra.get(key)), PCT)
                for t in range(n):
                    sh.put(r, c0 + t, f"=$B{r}*{L(c0 + t)}{_fr}", MIL)
                _cost_rows.append(r); r += 1
            sh.label(r, 1, "EBIT at historical cost ratios", bold=True); _eb = r
            for t in range(n):
                sh.put(r, c0 + t, f"={L(c0 + t)}{_fr}-" + "-".join(f"{L(c0 + t)}{x}" for x in _cost_rows), MIL, bold=True)
            r += 1
            sh.label(r, 1, "Operating leverage / margin path (the forecast's margin vs the historical cost ratios)"); _mp = r
            for t, v in enumerate((cs.get("margin_path") or [])[:n]):
                sh.put(r, c0 + t, (_num(v) or 0.0) / 1e6, MIL)
            r += 1
            sh.label(r, 1, "EBIT (forecast)", bold=True); _ef = r
            for t in range(n):
                sh.put(r, c0 + t, f"={L(c0 + t)}{_eb}+{L(c0 + t)}{_mp}", MIL, bold=True)
            r += 1
            sh.label(r, 1, "Engine forecast EBIT"); _ee = r
            for t, v in enumerate((cs.get("ebit_forecast") or [])[:n]):
                sh.put(r, c0 + t, (_num(v) or 0.0) / 1e6, MIL)
            r += 1
            sh.label(r, 1, "Check")
            for t in range(n):
                sh.put(r, c0 + t, f"={L(c0 + t)}{_ef}-{L(c0 + t)}{_ee}", MIL)
            r += 1
            sh.label(r, 1, "EBIT margin")
            for t in range(n):
                sh.put(r, c0 + t, f"=IFERROR({L(c0 + t)}{_ef}/{L(c0 + t)}{_fr},0)", PCT)
            r += 2
            sh.note(r, 1, "The cost lines hold each ratio at its historical average; the margin-path line is how far the "
                          "forecast's margin (guidance, consensus or the archetype curve) departs from that -- read it as the "
                          "operating leverage or cost discipline the forecast assumes.")

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
        # Audit 2026-11-25: the workbook's master check flag, on the front page, linked to the Checks tab.
        if getattr(self, "master_flag", None):
            sh.label(r, 1, "Master check flag (Checks tab)", bold=True)
            sh.put(r, 2, "=" + self.master_flag, bold=True)
            c = sh.ws.cell(row=r, column=3, value="open Checks")
            c.hyperlink = "#'Checks'!A1"
            c.font = Font(color=BLUE, underline="single")
            r += 2
        sh.section(r, "Colour code", 4)
        sh.ws.cell(row=r + 1, column=1, value="Blue: hardcoded input / historical actual").font = Font(color=BLUE)
        sh.ws.cell(row=r + 2, column=1, value="Black: formula within this tab").font = Font(color=BLACK)
        sh.ws.cell(row=r + 3, column=1, value="Green: formula linking another tab").font = Font(color=GREEN)
        sh.ws.cell(row=r + 4, column=1, value="Purple: engine output recorded in the run (not a lever)").font = Font(color=ENGINE)
        sh.ws.cell(row=r + 5, column=1, value="Grey: reference value no formula reads").font = Font(color=GREY, italic=True)
        r += 7
        sh.section(r, "Contents (click a tab name)", 4)
        for i, (name, desc) in enumerate(self.tabs):
            c = sh.ws.cell(row=r + 1 + i, column=1, value=name)
            c.hyperlink = f"#'{name}'!A1"
            c.font = Font(color=BLUE, underline="single")
            sh.ws.cell(row=r + 1 + i, column=2, value=desc)
        _model = "Model" in self.wb.sheetnames and self.model_kind
        sh.note(r + 2 + len(self.tabs), 1,
                "Each supporting tab rebuilds the engine's figures from recorded inputs; every tie-out check should "
                "read 0 and the Checks tab collects them all. "
                + ("The Model tab carries the three-statement forecast (IS, schedules, CF, BS); the statement tabs' "
                   "forecast columns link to it." if _model else
                   "No three-statement forecast was built for this run: statement tabs show reported history, with "
                   "forecasts only for the lines the valuation projects (revenue, FCF)."))
        sh.widths({"A": 34, "B": 80, "C": 14})

    # ── Assumptions ─────────────────────────────────────────────────────────
    def assumptions(self) -> None:
        sh = self.sheet("Assumptions", "Drivers and constants: FX, discount rate inputs, growth, margins, "
                                       "scenario weights, engine constants")
        sh.title("Assumptions & Drivers", "Every hardcoded driver lives here; all tabs reference these cells. Money in "
                                          f"{self.ccy} millions; shares in millions; per-share figures in {self.ccy}.")
        r = 4
        pb = self.dr.get("pt_bridge") or {}
        fu = self.dr.get("financials_used") or {}
        _nb = fu.get("net_debt_basis") or {}
        _run_day = str(self.run.get("run_at") or self.data.get("end_date") or "")[:10]
        _tm0 = ((self.scen("base").get("leg_inputs") or {}).get("DCF") or {}).get("timing") or {}
        _bs_day = _nb.get("balance_sheet_date") or fu.get("balance_sheet_period") or _tm0.get("balance_sheet_date") or ""
        _fy0 = _tm0.get("fy0_end") or ""
        # Audit 2026-11-25 (item 7): every value carries its source and as-of date.
        sh.header(r, ["Assumption", "", "Value", "", "", "Source", "As of", "Note"]); r += 1

        def add(name: str, label: str, value: Any, fmt: Optional[str] = None,
                src: str = "", asof: str = "") -> None:
            nonlocal r
            sh.label(r, 1, label, indent=1)
            sh.put(r, 3, value, fmt)
            self.A[name] = _ref("Assumptions", r, 3)
            sh.put(r, 6, src).font = Font(color=BLACK)
            sh.put(r, 7, asof).font = Font(color=BLACK)
            r += 1

        def srcs(row: int, src: str, asof: str) -> None:
            sh.put(row, 6, src).font = Font(color=BLACK)
            sh.put(row, 7, asof).font = Font(color=BLACK)

        sh.section(r, "General", 8); r += 1
        add("spot", f"Share price at valuation ({self.ccy})", _num(pb.get("spot")) or self._spot(), NUM,
            "Market quote at the run (pt_bridge.spot)", _run_day)
        add("fx", f"FX: statement currency ({fu.get('source_currency') or self.ccy}) → {self.ccy}",
            _num(fu.get("fx_rate")) or 1.0, "0.0000", f"Engine FX ({fu.get('source') or 'financials_used'})", _run_day)
        # Audit 2026-11-25 (item 5): the net debt and share count every EV leg used, once, in millions; the
        # Multiples tab and the Summary's net-debt check link here instead of carrying typed copies.
        _li_b = self.scen("base").get("leg_inputs") or {}
        _nd_used = next((_num(tr.get("net_debt")) for tr in _li_b.values()
                         if isinstance(tr, dict) and _num(tr.get("net_debt")) is not None), None)
        _sh_used = next((_num(tr.get("shares")) for tr in _li_b.values()
                         if isinstance(tr, dict) and _num(tr.get("shares"))), None)
        self._nd_used_v, self._sh_used_v = _nd_used, _sh_used
        if _nd_used is not None:
            add("nd_used", f"Net debt used by the valuation legs ({self.ccy} millions; negative = net cash)",
                _nd_used / 1e6, MIL, "Engine leg trace: " + (_nb.get("accounting_basis") or "") + " "
                + (f"leases {_nb.get('leases')}" if _nb.get("leases") else "feed net debt"), _bs_day)
        if _sh_used:
            add("shares_used", "Diluted shares used by the valuation legs (millions)", _sh_used / 1e6, MIL,
                f"Engine leg trace ({self.dr.get('shares_source') or 'diluted shares'})", _bs_day or _fy0)
        r += 1

        sh.section(r, "Terminal value and margins", 8); r += 1
        add("margin_cap", "FCF margin cap (engine constant; already applied inside the per-year margin path)",
            _ENGINE_MARGIN_CAP, PCT, "Engine constant (dcf_agent._FCF_MARGIN_CAP)", "")
        add("tv_guard", "Terminal spread guard: WACC ≥ g + (engine)", _TV_SPREAD_GUARD, PCT2,
            "Engine constant (_project_dcf)", "")
        r += 1

        self.dcf_variants: dict[str, dict[str, dict]] = {}
        # Owner, 2026-10-04 (decision D1) said a DCF the blend does not weight is not an output. Audit 2026-11-25
        # (item 3): it is still built -- labelled a shadow, weight 0 -- so a DCF-specific failure stays traceable.
        if self.dcf_present():
            sh.section(r, "Scenario drivers" + ("" if self.dcf_weighted() else
                                                " -- SHADOW DCF (weight 0, not in the blend)"), 8); r += 1
            sh.header(r, ["Driver", "", "Bear", "Base", "Bull", "Source", "As of"]); r += 1
            drivers = [("revenue_base", f"Revenue base (last actual, {self.ccy} millions)", MIL),
                       ("fcf_margin_base", "FCF margin base (unlevered owner earnings = owner-earnings FCF + after-tax interest; levered owner earnings for banks, insurers, fee financials other than payment networks, and property)", PCT2),
                       ("margin_delta_absolute", "Scenario margin change (absolute)", PCT2),
                       ("fcf_floor", "FCF margin floor", PCT2),
                       ("tgr", "Terminal growth", PCT2),
                       ("net_debt", f"Net debt ({self.ccy} millions)", MIL),
                       ("shares", "Diluted shares (millions)", MIL),
                       ("disc_point", "Discount point within each year's cash-flow window (0.5 = mid-year; 1 = year end)", "0.00"),
                       ("roll_forward_years", "Years from the balance-sheet date to the valuation date (value carried at the year-1 rate)", "0.0000"),
                       ("terminal_loe_multiplier", "Terminal value kept after loss of exclusivity beyond the horizon (1 = no LOE input)", "0.0000"),
                       # Owner, 2026-11-25: the value-driver terminal value (see the DCF tab)
                       ("terminal_vd", "Value-driver terminal value applies (1 = TV × (1 − g/RONIC); 0 = Gordon, a run priced before 2026-11-25)", "0"),
                       ("terminal_nopat_ratio", "Terminal NOPAT / UFCF ratio (guidance forecast; 1 when absent)", "0.0000"),
                       ("terminal_ronic_spread", "RONIC spread over the terminal WACC (owner; 0 = growth earns its cost of capital)", PCT2)]
            _dsrc = {"revenue_base": ("Engine DCF trace: last filed fiscal year (FMP)", _fy0),
                     "fcf_margin_base": ("Engine DCF trace: trailing owner-earnings margin (FMP)", _fy0),
                     "margin_delta_absolute": ("Engine scenario rule (dcf_agent)", ""),
                     "fcf_floor": ("Engine profile constant", ""),
                     "tgr": ("Engine profile / market terminal growth", ""),
                     "net_debt": ("Engine DCF trace: " + (_nb.get("accounting_basis") or "feed") + " balance sheet", _bs_day),
                     "shares": (f"Engine DCF trace ({self.dr.get('shares_source') or 'diluted shares'})", _bs_day or _fy0),
                     "disc_point": ("Engine timing (dated trace = mid-window)", ""),
                     "roll_forward_years": ("Engine timing: balance-sheet date to valuation date", _run_day),
                     "terminal_loe_multiplier": ("Engine LOE input (1 = none)", ""),
                     "terminal_vd": ("Engine DCF trace: value-driver terminal recorded", ""),
                     "terminal_nopat_ratio": ("Guidance forecast terminal.nopat_to_ufcf", _fy0),
                     "terminal_ronic_spread": ("Owner constant (engine default 0)", "")}
            for key, lab, fmt in drivers:
                sh.label(r, 1, lab, indent=1)
                for j, s in enumerate(SCENARIOS):
                    tr = (self.scen(s).get("leg_inputs") or {}).get("DCF") or {}
                    sh.put(r, 3 + j, _scaled(key, _driver(tr, key)) if tr else None, fmt)
                    self.A[f"{key}:{s}"] = _ref("Assumptions", r, 3 + j)
                srcs(r, *_dsrc.get(key, ("Engine DCF trace", "")))
                r += 1
            _tm = ((self.scen("base").get("leg_inputs") or {}).get("DCF") or {}).get("timing") or {}
            if _tm:
                sh.note(r, 1, f"Dating: fiscal year {_tm.get('fy0_end')} is the last actual; net debt is struck at "
                              f"{_tm.get('balance_sheet_date')}, so each projection year counts only its flows after that "
                              f"date; valued {_tm.get('valuation_date')}.")
                r += 1
            sh.label(r, 1, "Revenue growth by projection year", bold=True)
            srcs(r, "Engine projection path (guidance channel / fade / engine schedule)", _fy0); r += 1
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
            # Audit 2026-11-25 (item 4): these are the engine's OUTPUT (base + scenario change, fade, floor and cap
            # already applied), recorded so the DCF ties -- purple, not input blue; flexing one does not re-run the rule.
            sh.label(r, 1, "FCF margin by projection year (ENGINE OUTPUT as projected: guidance path, fade, floor and cap applied)", bold=True)
            srcs(r, "Engine projection path (projection_rows.fcf_margin)", _fy0); r += 1
            sh.header(r, ["Year", "", "Bear", "Base", "Bull"]); r += 1
            for t in range(10):
                sh.label(r, 1, f"Year {t + 1}", indent=1)
                for j, s in enumerate(SCENARIOS):
                    rows = ((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("projection_rows") or []
                    sh.put(r, 3 + j, _num(rows[t].get("fcf_margin")) if t < len(rows) else None, PCT2).font = Font(color=ENGINE)
                    self.A[f"m{t + 1}:{s}"] = _ref("Assumptions", r, 3 + j)
                r += 1
            sh.note(r, 1, "Purple = engine output. The base margin, scenario change, floor and cap above are the rule's "
                          "inputs, shown for reference; the path is not rebuilt from them in the workbook because the "
                          "guidance channel and fade set it year by year."); r += 1
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
                    tr = _with_core_terminal(tr, core)
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
                        sh.put(r, 3 + j, _scaled(key, _driver(tr, key)), fmt)
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
                            sh.put(r, 3 + j, _num(rows[t].get("fcf_margin")), PCT2).font = Font(color=ENGINE)
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
        sh.section(r, "Target and blend", 8); r += 1
        # Owner, 2026-10-04 (plan 1A.4): the rate is stated with the rule that set it.
        add("capture", "Share of the IV gap closed in 12 months (capture)"
            + (f" — {pb.get('capture_reason')}" if pb.get("capture_reason") else
               f" — source: {pb.get('capture_source')}" if pb.get("capture_source") else ""),
            _num(pb.get("capture")), PCT, "Engine target rule" + (f" ({pb.get('capture_source')})" if pb.get("capture_source") else ""),
            _run_day)
        sa = (self.data.get("scenario_analysis") or {}).get(self.ticker) or {}
        for s in SCENARIOS:
            add(f"prob:{s}", f"Scenario probability — {s}", _num((sa.get(s) or {}).get("probability")), PCT,
                "Scenario analysis (run)", _run_day)
        add("dp", "Published rounding (decimal places) for IV and targets", 2, "0", "Engine publishing rule")
        add("dp_ggm", "GGM value rounding (decimal places, engine)", 4, "0", "Engine constant (_compute_ggm_pb)")
        add("tol", "Reconciliation tolerance (one rounding unit)", 0.01, NUM, "Workbook standard")
        add("dp_check", "Check precision (decimal places; floating-point residue below it reads zero)", 6, "0", "Workbook standard")
        add("dp_comps", "Recorded peer-median precision (decimal places, engine)", 4, "0", "Engine comps store")
        _cal = self.dr.get("calibration") or {}
        add("calibration", "Calibration multiplier (market bias correction)",
            _num(_cal.get("iv_multiplier")) or 1.0, "0.0000",
            "Engine calibration" + (f" ({_cal.get('source') or _cal.get('basis')})" if (_cal.get("source") or _cal.get("basis")) else ""),
            _run_day)
        r += 1
        sh.section(r, "Discount-rate constants", 8); r += 1
        _wb = self.dr.get("wacc_build") or {}
        bb = _wb.get("base_breakdown") or {}
        add("lev_threshold", "Leverage premium threshold (leverage as on the WACC tab)",
            _num(bb.get("leverage_threshold")) or 1.5, "0.00\"x\"", f"Engine WACC table ({bb.get('table') or 'sector table'})")
        add("lev_slope", "Leverage premium per 1.0x above threshold",
            _num(bb.get("leverage_slope")) or 0.01, PCT2, f"Engine WACC table ({bb.get('table') or 'sector table'})")
        _capm = _wb.get("capm") or {}
        _fc = _wb.get("firm_capm") or {}
        # Audit 2026-11-25 (HK/SG workbook): the legacy CAPM line (Damodaran Jan-2026 rf / ERP, FMP beta) is a
        # cross-check only. Where the firm CAPM priced the run it is NOT the WACC used, and its labels say so.
        _xc = ("Legacy CAPM cross-check only -- NOT the WACC used (the firm CAPM below is): " if _fc
               else "CAPM cross-check (not the WACC used): ")
        add("rf", _xc + "risk-free rate (Damodaran Jan 2026)", _num(_capm.get("rf")) or 0.0395, PCT2,
            "Damodaran (engine CAPM check)", "2026-01")
        add("erp", _xc + "equity risk premium (Damodaran Jan 2026)", _num(_capm.get("erp")) or 0.0446, PCT2,
            "Damodaran (engine CAPM check)", "2026-01")
        # Audit 2026-11-25 (item 7): the beta the engine's own CAPM check used when the statements loader gave none --
        # the WACC tab's CAPM cross-check read #N/A on every export without statements.
        _beta_st = _num((self.statements or {}).get("beta"))
        _beta = _beta_st if _beta_st is not None else _num(_capm.get("beta"))
        add("beta", _xc + "beta", _beta, "0.00",
            "FMP profile (statements)" if _beta_st is not None else
            ("FMP beta as recorded by the engine's CAPM check (wacc_build.capm)" if _beta is not None else "not available"),
            _run_day)
        r += 1

        def section(text: str) -> None:
            nonlocal r
            sh.section(r, text, 8)
            r += 1

        def gap() -> None:
            nonlocal r
            r += 1

        if _fc:
            self._firm_capm_inputs(add, section, _fc, _wb, _run_day)
            gap()
        if self._valuation_bridge_inputs(add, section, _bs_day, _run_day):
            gap()
        sh.section(r, "Sensitivity, model-invariant and bank-model constants", 8); r += 1
        add("sens_w", "Sensitivity step: WACC", 0.01, PCT2, "Workbook standard")
        add("sens_g", "Sensitivity step: terminal growth", 0.005, PCT2, "Workbook standard")
        add("sens_m", "Sensitivity step: peer multiple, metric, margin (relative)", 0.10, PCT, "Workbook standard")
        add("sens_c", "Sensitivity step: capture (absolute)", 0.10, PCT, "Workbook standard")
        add("sens_p", "Sensitivity step: probability moved from bear to bull (absolute)", 0.05, PCT, "Workbook standard")
        add("gm_band", "Model invariant: forecast gross margin within this many points of the last filed year", 0.10, PCT,
            "Audit standard (2026-11-25)")
        add("pb_floor", "Justified P/B floor (engine)", _PB_FLOOR, MULT, "Engine constant (_compute_ggm_pb)")
        add("pb_cap", "Justified P/B cap (engine)", _PB_CAP, MULT, "Engine constant (_compute_ggm_pb)")
        sh.widths({"A": 58, "B": 2, "C": 16, "D": 16, "E": 16, "F": 44, "G": 12, "H": 50})
        sh.ws.freeze_panes = "C5"

    def _firm_capm_inputs(self, add: Callable, section: Callable, fc: dict, wb: dict, run_day: str) -> None:
        """Audit 2026-11-25 (HK/SG workbook, defect 2): the firm CAPM (src/data/capm.firm_wacc) priced the run, so
        every input it read is an Assumptions cell with its source; the WACC tab rebuilds the rate from them."""
        import re as _re
        try:
            from src.data.capm import OWN_BETA_BAND as _band, WACC_FLOOR_OVER_RF as _floor
        except Exception:                                  # noqa: BLE001
            _band, _floor = (0.4, 2.5), 0.01
        _src = str(fc.get("source") or "Damodaran")
        _m = _re.findall(r"\d{4}-\d{2}-\d{2}", _src)
        _erp_asof = _m[-1] if _m else ""
        _m = _re.findall(r"\d{4}-\d{2}-\d{2}", str(fc.get("rf_source") or ""))
        _rf_asof = _m[-1] if _m else run_day
        section("Discount rate: firm CAPM -- the WACC used (owner, 2026-11-25; src/data/capm.firm_wacc)")
        add("fc_rf", "Risk-free rate (government 10y in the currency of the cash flows)", _num(fc.get("rf")), PCT2,
            str(fc.get("rf_source") or "engine firm CAPM"), _rf_asof)
        add("fc_erp", "Equity risk premium (Damodaran implied)", _num(fc.get("erp")), PCT2, _src, _erp_asof)
        add("fc_crp", "Country risk premium added to the cost of equity", _num(fc.get("crp")) or 0.0, PCT2,
            "Engine: wacc_build.country_risk_premium_total", run_day)
        add("fc_beta_u", f"Industry unlevered beta, cash-corrected ({fc.get('industry')})", _num(fc.get("beta_unlevered")),
            "0.00", _src, "2026-01")
        add("fc_tax", "Marginal tax rate (market statutory: relevering and after-tax cost of debt)", _num(fc.get("tax_rate")),
            PCT, "Engine constant (capm._TAX by market)", "")
        # The firm CAPM records D/E to 4 d.p.; the table build records the same ratio (net debt / market cap) in full.
        # The full figure is used when it rounds to the recorded one (U96: 1.394963 vs 1.395 moved Ke by 0.1bp).
        _de = _num(fc.get("de_ratio"))
        _bb = wb.get("base_breakdown") or {}
        _lev = _num(_bb.get("leverage"))
        _full = (_de is not None and _lev is not None and "market cap" in str(_bb.get("leverage_basis") or "")
                 and abs(round(max(_lev, 0.0), 4) - _de) < 1e-12)
        add("fc_de", "D/E for relevering = max(net debt, 0) / market capitalisation", max(_lev, 0.0) if _full else _de,
            "0.000000", ("Engine WACC build: net debt / market capitalisation at the run, full precision "
                         "(wacc_build.base_breakdown.leverage; the firm CAPM records it to 4 d.p.)") if _full
            else "Engine firm CAPM (net debt and market cap at the run; recorded to 4 d.p.)", run_day)
        add("fc_beta_own", "Own beta, raw (FMP profile)", _num(fc.get("beta_own_raw")), "0.000",
            "FMP profile beta read by the engine's firm CAPM", run_day)
        add("fc_band_lo", "Own beta usable from (plausibility band, low)", float(_band[0]), "0.00", "Engine constant (capm.OWN_BETA_BAND)")
        add("fc_band_hi", "Own beta usable to (plausibility band, high)", float(_band[1]), "0.00", "Engine constant (capm.OWN_BETA_BAND)")
        add("fc_blume_w", "Blume adjustment: weight on the raw beta", 0.67, "0.00", "Engine constant (capm.firm_wacc; Blume 1971)")
        add("fc_blume_c", "Blume adjustment: weight on a beta of 1.0", 0.33, "0.00", "Engine constant (capm.firm_wacc; Blume 1971)")
        add("fc_blend_w", "Weight on the relevered industry beta when the own beta is usable (rest on Blume own beta)", 0.5,
            "0.00", "Engine constant (capm.firm_wacc)")
        _live_src = ("Engine live cost of debt (wacc_build.rd_live; " + str(wb.get("source") or "")
                     + (f" {wb.get('series_id')}" if wb.get("series_id") else "") + ")")
        _us = next((_num(fc.get(k)) for k in ("us_rf", "rf_us") if _num(fc.get(k)) is not None), None)
        if fc.get("cost_of_debt_rebased") and _num(wb.get("rd_live")) is not None and _us is not None:
            # the engine moves a US-yield Kd onto the local government curve (spread kept): rebuilt on the WACC tab
            add("fc_kd_live", "Live pre-tax cost of debt (US Treasury + the rating's spread)", _num(wb.get("rd_live")), PCT2,
                _live_src, run_day)
            add("fc_us_rf", "US 10y Treasury the live cost of debt is priced over", _us, PCT2, "Engine firm CAPM (FRED DGS10)", run_day)
        else:
            add("fc_kd", "Pre-tax cost of debt" + (" (rebased onto the local government curve by the engine: live Kd − US "
                                                   "10y + local rf, floored at rf; the US 10y is not recorded)"
                                                   if fc.get("cost_of_debt_rebased") else ""),
                _num(fc.get("cost_of_debt")), PCT2,
                _live_src if wb.get("rd_live") is not None else "Engine fallback: risk-free + 1.6% (no live cost of debt)",
                run_day)
        add("fc_contracted", "Contracted-revenue discount", _num(fc.get("contracted_discount")) or 0.0, PCT2,
            "Engine (research keywords; wacc_build.contracted_revenue_discount)", run_day)
        _ms = _num(fc.get("min_spread_over_rf"))
        add("fc_floor", "WACC floor over the risk-free rate", float(max(_floor, _ms or 0.0)), PCT2,
            "Engine constant (capm.WACC_FLOOR_OVER_RF)" + (f", raised to the owner's local-curve minimum spread "
                                                            f"(valuation_constants.risk_free_by_market)" if _ms else ""))
        add("fc_ke_min", "Minimum cost-of-equity spread over the risk-free rate (local curve; 0 = none)", float(_ms or 0.0),
            PCT2, "Owner, 2026-11-25 (valuation_constants.risk_free_by_market.min_spread_over_rf)" if _ms else "None for this market")
        add("fc_dp", "Firm-CAPM WACC rounding (decimal places, engine)", 5, "0", "Engine constant (capm.firm_wacc)")
        _pr = wb.get("pharma_capm_rule") or {}
        if isinstance(_pr.get("floor"), (int, float)):
            add("fc_pharma_floor", "Large-cap pharma WACC floor (owner rule)", float(_pr["floor"]), PCT2,
                "Engine constant (_PHARMA_CAPM_FLOOR)")

    def _valuation_bridge_inputs(self, add: Callable, section: Callable, bs_day: str, run_day: str) -> bool:
        """Audit 2026-11-25 (HK/SG workbook, defect 4): minority interest, preferred equity and a cited embedded value
        per share were typed into the DCF and Multiples rows; they are labelled inputs here, with source and as-of."""
        li = self.scen("base").get("leg_inputs") or {}
        mi = next((_num(tr.get("minority_interest")) for tr in li.values()
                   if isinstance(tr, dict) and _num(tr.get("minority_interest")) is not None), None)
        pe = next((_num(tr.get("preferred_equity")) for tr in li.values()
                   if isinstance(tr, dict) and _num(tr.get("preferred_equity")) is not None), None)
        ev = next(((n, tr) for n, tr in li.items() if isinstance(tr, dict)
                   and str(tr.get("metric") or "").lower().startswith("embedded value per share")
                   and _num(tr.get("per_share_metric")) is not None), None)
        self._mi_used_v, self._pe_used_v = mi, pe
        if mi is None and pe is None and ev is None:
            return False
        section("Equity bridge and cited per-share inputs")
        if mi is not None:
            add("mi_used", f"Minority interest deducted from EV ({self.ccy} millions)", mi / 1e6, MIL,
                "Engine leg trace (FMP balance sheet, minority interest)", bs_day or run_day)
        if pe is not None:
            add("pe_used", f"Preferred equity deducted from EV ({self.ccy} millions)", pe / 1e6, MIL,
                "Engine leg trace (FMP balance sheet, preferred equity)", bs_day or run_day)
        if ev is not None:
            name, tr = ev
            v = float(tr["per_share_metric"])
            cited = None
            try:
                from src.data import industry_inputs as _ii
                _e = _ii.entry(self.ticker, "embedded_value") or {}
                _ps = ((_e.get("data") or {}).get("ev_per_share") or {})
                if isinstance(_ps.get("value"), (int, float)):
                    cited = _ps
            except Exception:                              # noqa: BLE001
                cited = None
            fu = self.dr.get("financials_used") or {}
            fx = _num(fu.get("fx_rate")) or 1.0
            src_ccy = fu.get("source_currency") or self.ccy
            factor = None
            if cited:
                c_ccy = cited.get("currency") or src_ccy
                factor = 1.0 if c_ccy == self.ccy else (fx if c_ccy == src_ccy else None)
                if factor is None or not abs(float(cited["value"]) * factor - v) <= 1e-6 * max(abs(v), 1.0):
                    factor = None
            if factor is not None:
                c_ccy = cited.get("currency") or src_ccy
                add("ev_ps_cited", f"Embedded value per share as cited ({c_ccy}; owner-accepted input)", float(cited["value"]),
                    "0.0000", f"Owner-accepted industry input: {cited.get('quote') or ''} -- {cited.get('source_url') or ''}".strip(),
                    str(cited.get("period") or ""))
                self._ev_ps_formula = (f"={self.A['ev_ps_cited']}*{self.A['fx']}" if factor != 1.0
                                       else f"={self.A['ev_ps_cited']}")
                add("ev_ps", f"Embedded value per share in {self.ccy} (cited × FX)" if factor != 1.0
                    else f"Embedded value per share in {self.ccy}", self._ev_ps_formula, NUM,
                    f"Formula: cited value{' × engine FX' if factor != 1.0 else ''}", run_day)
            else:
                add("ev_ps", f"Embedded value per share in {self.ccy} (owner-accepted input)", v, NUM,
                    f"Engine leg trace ({name}; the cited figure is not in src/data/industry_inputs.json at this value)", run_day)
            self._ev_ps_value = v
        return True

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
        self._statement_checks(sh, tie, rows, c0, "pretax_income", "IS",
                               "EBT rebuilt from the reported lines (gross profit + opex + other) − reported pre-tax income")
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
        if self.model_rows and self.model_kind in ("bank", "insurer"):
            # a bank's or insurer's income statement has lines the reported layout does not: shown here, linked
            sh.section(r, f"Forecast — {self.model_kind} earnings-and-capital model (Model tab), linked", last + n_fc); r += 1
            _lines = ((("Net interest income", "net_interest_income"), ("Non-interest income", "fee_income"), ("Provisions", "provisions"))
                      if self.model_kind == "bank" else
                      (("Net earned premiums", "net_earned_premiums"), ("Underwriting result", "underwriting_result"), ("Investment income", "investment_income")))
            for _lab, _key in _lines + (("Dividend per share", "dividend_per_share"),):
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
        _chk = frow("Balance check (assets − liabilities & equity)", lambda L: f"={L}{ta}-{L}{tle}", typ="Check", key="chk")
        self._statement_checks(sh, _chk, rows, c0, "total_assets", "BS", "reported total assets − (liabilities + equity)")
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
        fc = b.get("firm_capm") or {}
        if fc and self.A.get("fc_rf"):
            sh.title("WACC build", "Firm CAPM (the WACC used): rf + beta × ERP + CRP for the cost of equity, the after-tax "
                                   "cost of debt, at market weights. The sector table rate it replaced is a memo below.")
            r = self._firm_capm_block(sh, r, fc, b)
            sh.section(r, "Memo: the sector table rate the firm CAPM replaced (NOT used)", 4); r += 1
        bb = b.get("base_breakdown") or {}
        base_cell = None
        if bb:
            A = self.A
            sh.section(r, "Base rate: sector / profile assumption" + (" (memo)" if fc else ""), 4); r += 1
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
            # dcf_agent applies the insider overlay only when the firm CAPM does not price the run
            (f"Insider-activity overlay (recorded {(_num(b.get('insider_bps')) or 0.0):+.2f}bp; not applied: the firm CAPM "
             "replaces the overlays)" if (fc and _num(b.get("insider_bps"))) else "Insider-activity overlay",
             0.0 if fc else (_num(b.get("insider_bps")) or 0.0) / 10000.0),
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
        _pr = b.get("pharma_capm_rule") or {}
        if isinstance(_pr.get("before"), (int, float)) and isinstance(_pr.get("after"), (int, float)):
            comps.append((f"Large-cap pharma rule: CAPM {_pr.get('capm', 0):.2%} with a {_pr.get('floor', 0):.1%} floor -- "
                          f"{_pr['before']:.2%} replaced by {_pr['after']:.2%}", float(_pr["after"]) - float(_pr["before"])))
        if isinstance(b.get("owner_override_delta"), (int, float)):
            comps.append(("Owner rate override", float(b["owner_override_delta"])))
        first = r
        for lab, v in comps:
            sh.label(r, 1, lab, indent=1)
            sh.put(r, 3, v, PCT2)
            r += 1
        if fc and self.A.get("fc_rf"):
            # Audit 2026-11-25 (HK/SG, defect 2): the table path ends at the rate the firm CAPM replaced -- recorded by
            # the engine (table_rate_replaced) -- with no residual line; the WACC used is the firm-CAPM build above.
            sh.label(r, 1, "Table WACC the firm CAPM replaced (memo; NOT used)", bold=True)
            sh.put(r, 3, f"=SUM(C{first}:C{r - 1})", PCT2, bold=True); _tr = r; r += 1
            sh.label(r, 1, "Engine: table rate replaced (wacc_build.firm_capm.table_rate_replaced)", indent=1)
            sh.put(r, 3, _num(fc.get("table_rate_replaced")), PCT2); r += 1
            sh.label(r, 1, "Check (basis points)", indent=1)
            sh.put(r, 3, f"=ROUND(C{_tr}-C{r - 1},{self.A['dp_check']})*10000", "0.00"); r += 2
        else:
            engine = _num(b.get("wacc_final")) or _num(self.dr.get("wacc"))
            # The engine rounds at two intermediate steps; the residual is shown
            # as its own input line rather than hidden inside a formula.
            known = sum((_num(bb.get("wacc")) if isinstance(v, str) else v) or 0.0
                        for _, v in comps if v is not None)
            _res = (engine - known) if engine is not None else 0.0
            # Audit 2026-11-25 (HK/SG): a residual above one basis point is not rounding -- it is an engine step the
            # build does not record, and the label says so.
            sh.label(r, 1, "Engine rounding (residual; should be ~0)" if abs(_res) <= 1e-4 else
                     "Residual NOT explained by the recorded build (an engine step wacc_build does not record; not rounding)",
                     indent=1)
            sh.put(r, 3, _res, PCT2)
            r += 1
            sh.label(r, 1, "WACC used", bold=True)
            sh.put(r, 3, f"=SUM(C{first}:C{r - 1})", PCT2, bold=True)
            self.A["wacc"] = _ref("WACC", r, 3)
            r += 2
        A = self.A
        sh.section(r, "Legacy CAPM cross-check (Assumptions rf / ERP / beta; NOT the WACC used)", 4); r += 1
        sh.label(r, 1, "Cost of equity = rf + beta × ERP + country risk", indent=1)
        crp_cell = getattr(self, "_crp_cell", None) if bb else None
        sh.put(r, 3, f"=IFERROR({A['rf']}+{A['beta']}*{A['erp']}+{crp_cell},NA())"
               if crp_cell else f"=IFERROR({A['rf']}+{A['beta']}*{A['erp']},NA())", PCT2)
        r += 1
        sh.note(r, 1, "Shown for comparison only: the WACC used is the firm-CAPM build at the top of this tab (its own rf, "
                      "relevered beta and ERP); this line reads the legacy Damodaran Jan-2026 inputs." if fc else
                "Shown for comparison: the engine's WACC is table-driven by sector/profile, "
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
        # Audit 2026-11-25 (item 7): the label said "Rating priced (agency)" beside the SYNTHETIC rating field (AAA from
        # interest cover) while the agency note said BBB+. The agency rating priced is read from the note; the
        # synthetic one is named as what it is.
        import re as _re
        _note = str(b.get("agency_rating_note") or "")
        _m = _re.search(r"agency rating ([A-D][A-Za-z]*[+\-]?\d?)", _note)
        _agency_priced = bool(_m) and "Cost of debt on the agency rating" in _note
        _rows = [("Live cost of debt", "rd_live", PCT2), ("Baseline cost of debt", "rd_baseline", PCT2),
                 ("Debt / (debt + equity)", "dv_ratio", PCT)]
        for lab, key, fmt in _rows:
            if b.get(key) is None:
                continue
            sh.label(r, 1, lab, indent=1)
            v = b.get(key)
            sh.put(r, 3, _num(v) if fmt and _num(v) is not None else v, fmt)
            r += 1
        if _agency_priced:
            sh.label(r, 1, "Rating priced (agency)", indent=1)
            sh.put(r, 3, _m.group(1)).font = Font(color=BLACK); r += 1
        if b.get("rating") is not None:
            sh.label(r, 1, "Synthetic rating (from interest cover" + ("; NOT priced -- the agency rating above is"
                           if _agency_priced else "; sets only the live-spread overlay, not an agency rating") + ")", indent=1)
            sh.put(r, 3, b.get("rating")).font = Font(color=BLACK); r += 1
        for lab, key, fmt in (("Agency rating (verification note)", "agency_rating_note", None),
                              ("Credit spread source", "source", None), ("Leverage (net debt / equity)", "leverage", "0.00"),
                              ("Macro regime", "macro_regime", None)):
            if b.get(key) is None:
                continue
            sh.label(r, 1, lab, indent=1)
            v = b.get(key)
            sh.put(r, 3, _num(v) if fmt and _num(v) is not None else v, fmt)
            r += 1
        sh.widths({"A": 48, "B": 2, "C": 14})

    def _firm_capm_block(self, sh: _Sheet, r: int, fc: dict, b: dict) -> int:
        """The firm-CAPM WACC rebuilt from its Assumptions inputs (capm.firm_wacc, formula for formula), each
        intermediate against the engine's recorded figure. Sets A["wacc"]; returns the next free row."""
        A = self.A
        sh.section(r, f"Firm CAPM: the WACC used ({fc.get('industry')}; replaces the table rate and its overlays)", 4); r += 1
        sh.header(r, ["Line", "", "Rebuilt", "Engine"]); r += 1
        rows: dict[str, int] = {}

        def line(key: str, label: str, v: Any, fmt: str, engine: Any = None, dp: Optional[int] = None,
                 bold: bool = False) -> None:
            nonlocal r
            sh.label(r, 1, label, bold=bold, indent=0 if bold else 1)
            sh.put(r, 3, v, fmt, bold=bold)
            rows[key] = r
            if engine is not None:
                sh.put(r, 4, _num(engine), fmt)
                r += 1
                # in units of 0.0001 (basis points for rates), so the workbook's tolerance (0.01) is a real test of a
                # rate or a beta. A figure the engine records rounded (4 d.p. betas, 5 d.p. rates) ties when the
                # rebuilt one sits within half a recorded unit of it: the excess beyond that half unit is the check.
                sh.label(r, 1, f"Check: {label.split(' = ')[0].split(' (')[0]} -- rebuilt − engine, in units of 0.0001 "
                               "(basis points for rates)" + ("; 0 within half a unit of the recorded precision"
                                                             if dp is not None else ""), indent=2)
                sh.put(r, 3, (f"=MAX(0,ABS(C{rows[key]}-D{rows[key]})-0.5*10^(-{dp}))*10000" if dp is not None
                              else f"=(C{rows[key]}-D{rows[key]})*10000"), "0.00")
            r += 1

        C = lambda k: f"C{rows[k]}"                         # noqa: E731
        line("rf", "Risk-free rate", "=" + A["fc_rf"], PCT2, fc.get("rf"))
        line("erp", "Equity risk premium", "=" + A["fc_erp"], PCT2)
        line("crp", "Country risk premium", "=" + A["fc_crp"], PCT2)
        line("bu", f"Industry unlevered beta ({fc.get('industry')})", "=" + A["fc_beta_u"], "0.0000", fc.get("beta_unlevered"))
        line("t", "Marginal tax rate", "=" + A["fc_tax"], PCT)
        line("de", "D/E = max(net debt, 0) / market cap", "=" + A["fc_de"], "0.000000", fc.get("de_ratio"), 4)
        line("bl", "Relevered industry beta = βu × (1 + (1 − t) × D/E)", f"={C('bu')}*(1+(1-{C('t')})*{C('de')})", "0.0000",
             fc.get("beta_industry_relevered"), 4)
        line("braw", "Own beta, raw (FMP)", "=" + A["fc_beta_own"], "0.000", fc.get("beta_own_raw"))
        line("use", "Own beta usable (1 = inside the plausibility band)",
             f"=IF(AND(ISNUMBER({C('braw')}),{C('braw')}>={A['fc_band_lo']},{C('braw')}<={A['fc_band_hi']}),1,0)", "0")
        line("badj", "Blume-adjusted own beta = 0.67 × raw + 0.33 (n/a when not usable)",
             f"=IF({C('use')}=1,{A['fc_blume_w']}*{C('braw')}+{A['fc_blume_c']},\"n/a\")", "0.0000",
             fc.get("beta_own_adjusted") if fc.get("beta_own_adjusted") is not None else None,
             4 if fc.get("beta_own_adjusted") is not None else None)
        line("beta", "Beta used = blend of relevered industry and Blume own beta (industry alone when own n/a)",
             f"=IF({C('use')}=1,{A['fc_blend_w']}*{C('bl')}+(1-{A['fc_blend_w']})*{C('badj')},{C('bl')})", "0.0000",
             fc.get("beta_levered"), 4)
        line("ke", "Cost of equity = MAX(rf + β × ERP + CRP, rf + minimum spread when set)",
             f"=IF({A['fc_ke_min']}>0,MAX({C('rf')}+{C('beta')}*{C('erp')}+{C('crp')},{C('rf')}+{A['fc_ke_min']}),"
             f"{C('rf')}+{C('beta')}*{C('erp')}+{C('crp')})", PCT2, fc.get("cost_of_equity"), 5, bold=True)
        if A.get("fc_kd_live") and A.get("fc_us_rf"):
            line("kd", "Pre-tax cost of debt on the local curve = MAX(live Kd − US 10y + rf, rf)",
                 f"=MAX({A['fc_kd_live']}-{A['fc_us_rf']}+{C('rf')},{C('rf')})", PCT2, fc.get("cost_of_debt"), 5)
        else:
            line("kd", "Pre-tax cost of debt", "=" + A["fc_kd"], PCT2, fc.get("cost_of_debt"))
        line("kdt", "After-tax cost of debt = Kd × (1 − t)", f"={C('kd')}*(1-{C('t')})", PCT2)
        line("dv", "D/V = (D/E) / (1 + D/E)", f"=IFERROR({C('de')}/(1+{C('de')}),0)", "0.0000", fc.get("dv_ratio"), 4)
        line("ev", "E/V = 1 − D/V", f"=1-{C('dv')}", "0.0000")
        line("cd", "Contracted-revenue discount", "=" + A["fc_contracted"], PCT2)
        line("pre", "WACC before the floor = E/V × Ke + D/V × Kd after tax + contracted discount",
             f"={C('ev')}*{C('ke')}+{C('dv')}*{C('kdt')}+{C('cd')}", PCT2)
        line("fl", "Floor = rf + floor spread", f"={C('rf')}+{A['fc_floor']}", PCT2)
        line("w", "Firm-CAPM WACC = MAX(before floor, floor), rounded as the engine does",
             f"=ROUND(MAX({C('pre')},{C('fl')}),{A['fc_dp']})", PCT2, fc.get("wacc"), bold=True)
        last = C("w")
        _pr = b.get("pharma_capm_rule") or {}
        if A.get("fc_pharma_floor") and isinstance(_pr.get("after"), (int, float)):
            line("ph", "Large-cap pharma rule: MAX(firm CAPM, floor), 4 d.p.", f"=ROUND(MAX({last},{A['fc_pharma_floor']}),4)",
                 PCT2, _pr.get("after"))
            last = C("ph")
        if isinstance(b.get("owner_override_delta"), (int, float)):
            line("ov", "Owner rate override (delta)", float(b["owner_override_delta"]), PCT2)
            line("ovw", "After the owner override", f"={last}+{C('ov')}", PCT2)
            last = C("ovw")
        line("used", "WACC used", f"={last}", PCT2, _num(b.get("wacc_final")) or _num(self.dr.get("wacc")), bold=True)
        self.A["wacc"] = _ref("WACC", rows["used"], 3)
        sh.note(r, 1, "Every input is an Assumptions cell with its source; intermediates are checked against the engine's "
                      "recorded figure at the precision it records (4 d.p. betas and weights, 5 d.p. rates).")
        r += 2
        return r

    # ── DCF ─────────────────────────────────────────────────────────────────
    def dcf_tab(self) -> None:
        self.dcf_excluded: Optional[str] = None
        self.dcf_shadow = not self.dcf_weighted()
        if self.dcf_shadow:
            # Owner, 2026-10-04 (D1): a DCF the blend does not weight is not an output -- one Summary line says so.
            # Audit 2026-11-25 (item 3): it is still built, as a labelled shadow, so a DCF-specific failure is traceable.
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
                    + "; it is not an input to the target"
                    + ("; shown on the DCF tab as a shadow (weight 0)." if self.dcf_present() else "."))
            if not self.dcf_present():
                return
        sh = self.sheet("DCF", ("SHADOW DCF -- weight 0, not in the blend; " if self.dcf_shadow else "")
                        + "Discounted cash flow per scenario, terminal value, sensitivity")
        sh.title("Shadow DCF -- weight 0, not in the blend" if self.dcf_shadow else "DCF valuation",
                 ("Computed by the engine and rebuilt here so its figures stay traceable; it carries no weight in the "
                  "intrinsic value or the target. " if self.dcf_shadow else "")
                 + f"Revenue × FCF margin, discounted; drivers from Assumptions, WACC from the WACC tab. Money in "
                   f"{self.ccy} millions; per-share values in {self.ccy}.")
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
        sh.ws.freeze_panes = "C4"

    def _dcf_block(self, sh: _Sheet, r: int, s: str, tr: dict, sfx: str = "", leg: str = "DCF") -> int:
        """One scenario's projection. `sfx` selects a DCF-family leg's own drivers."""
        A = self.A
        core = not sfx
        _shadow = core and getattr(self, "dcf_shadow", False)
        sh.section(r, f"{s.upper()} scenario" + ("" if core else f" — {leg}")
                   + (" — SHADOW (weight 0, not in the blend)" if _shadow else "")
                   + f" — {self.ccy} millions", 13); r += 1
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
            sh.put(r, c0 + t, f"={prev}*(1+{L}{gr})", MIL)
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
            sh.put(r, c0 + t, f"={L}{rv}*{L}{mr}*{L}{fsr}", MIL)
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
            sh.put(r, c0 + t, f"={L}{fr}*{L}{dfr}", MIL)
        r += 2
        F, Lc = get_column_letter(c0), get_column_letter(c0 + n - 1)
        tg, nd, shs = A[f"tgr:{s}{sfx}"], A[f"net_debt:{s}{sfx}"], A[f"shares:{s}{sfx}"]
        roll = A[f"roll_forward_years:{s}{sfx}"]
        out = r
        # Owner, 2026-11-25 (CFA audit, engine change in `_project_dcf`): the value-driver terminal value,
        #   TV = FCF_T × NOPAT/UFCF ratio × (1 + g) × (1 − g / RONIC) / (WACC_T − g) × LOE multiplier,
        #   RONIC = WACC_T + spread (spread 0 by default: growth earns its cost of capital, adds no value).
        # A run priced before the change (no value-driver record in its trace) carries flag 0 and the Gordon form,
        # so its Check still ties.
        vd, npr, rsp = (A[f"terminal_vd:{s}{sfx}"], A[f"terminal_nopat_ratio:{s}{sfx}"],
                        A[f"terminal_ronic_spread:{s}{sfx}"])
        K = {k: out + i for i, k in enumerate((
            "pv_sum", "tw", "ronic", "ratio", "reinv", "fcf_t1", "tv", "pv_tv_bs", "carry", "pv_fcf", "pv_tv", "ev", "nd",
            "mi", "pe", "eq", "iv", "eng", "chk", "tv_share", "tv_fcf"))}
        lines = {
            "pv_sum": ("Sum of PV of FCF (balance-sheet date)", f"=SUM({F}{pvr}:{Lc}{pvr})", MIL),
            "tw": ("Terminal WACC (spread guard applied)", f"=IF({Lc}{wr}<={tg},{tg}+{A['tv_guard']},{Lc}{wr})", PCT2),
            "ronic": ("RONIC: return on new invested capital = terminal WACC + spread (Assumptions)", f"=B{K['tw']}+{rsp}", PCT2),
            "ratio": ("NOPAT / UFCF ratio applied to the final year (1 when absent or under the Gordon form)",
                      f"=IF({vd}=1,IF(N({npr})>0,{npr},1),1)", "0.0000"),
            "reinv": ("Reinvestment factor 1 − g / RONIC (1 under the Gordon form)",
                      f"=IF({vd}=1,IF(B{K['ronic']}>0,IFERROR(1-{tg}/B{K['ronic']},1),1),1)", "0.0000"),
            "fcf_t1": ("Terminal cash flow: final full year × ratio × (1 + g) × reinvestment factor",
                       f"={Lc}{rv}*{Lc}{mr}*B{K['ratio']}*(1+{tg})*B{K['reinv']}", MIL),
            "tv": ("Terminal value = terminal cash flow / (WACC − g) × LOE multiplier",
                   f"=IFERROR(B{K['fcf_t1']}/(B{K['tw']}-{tg}),0)*{A[f'terminal_loe_multiplier:{s}{sfx}']}", MIL),
            "pv_tv_bs": ("PV of terminal value (balance-sheet date)", f"=B{K['tv']}*{Lc}{der}", MIL),
            "carry": ("Carry to the valuation date: (1 + year-1 rate) ^ years", f"=(1+{F}{wr})^{roll}", "0.0000"),
            "pv_fcf": ("PV of FCF (valuation date)", f"=B{K['pv_sum']}*B{K['carry']}", MIL),
            "pv_tv": ("PV of terminal value (valuation date)", f"=B{K['pv_tv_bs']}*B{K['carry']}", MIL),
            "ev": ("Enterprise value", f"=B{K['pv_fcf']}+B{K['pv_tv']}", MIL),
            "nd": ("Less: net debt (plus net cash when negative)", f"=-{nd}", MIL),
            # Owner, 2026-10-03 (Alibaba review, section 1): the engine deducts minority interest and
            # preferred equity in the DCF bridge.
            "mi": ("Less: minority interest", self._bridge_ref(tr, "minority_interest", "mi_used", negate=True), MIL),
            "pe": ("Less: preferred equity", self._bridge_ref(tr, "preferred_equity", "pe_used", negate=True), MIL),
            "eq": ("Equity value", f"=B{K['ev']}+B{K['nd']}+B{K['mi']}+B{K['pe']}", MIL),
            "iv": ("Intrinsic value per share", f"=IFERROR(B{K['eq']}/{shs},0)", NUM),
            "eng": ("Engine value", _num(tr.get("value")), NUM),
            "chk": ("Check", f"=B{K['iv']}-B{K['eng']}", NUM),
            "tv_share": ("Terminal value share of EV", f"=IFERROR(B{K['pv_tv']}/B{K['ev']},0)", PCT),
            "tv_fcf": ("Implied terminal multiple: TV / final-year FCF (cross-check)", f"=IFERROR(B{K['tv']}/({Lc}{rv}*{Lc}{mr}),0)", "0.0\"x\""),
        }
        for key, row in K.items():
            lab, v, fmt = lines[key]
            sh.label(row, 1, lab, bold=key in ("ev", "iv"))
            sh.put(row, 2, v, fmt, bold=key == "iv")
        _end = out + len(K)
        # The peer exit multiple is a disclosed cross-check only: the E20 terminal clamp is no longer applied.
        _term = ((tr.get("guidance_forecast") or {}).get("terminal") or {})
        if core and (_term.get("implied_exit_ev_ebitda") is not None or _term.get("peer_ev_ebitda_median") is not None):
            sh.label(_end, 1, "Cross-check only (not applied; the E20 exit-multiple clamp is retired): implied exit EV/EBITDA")
            sh.put(_end, 2, _num(_term.get("implied_exit_ev_ebitda")), "0.0\"x\"")
            sh.label(_end + 1, 1, "Cross-check only: peer EV/EBITDA median")
            sh.put(_end + 1, 2, _num(_term.get("peer_ev_ebitda_median")), "0.0\"x\"")
            _end += 2
        if not core:
            self.leg_cell[(s, leg)] = _ref("DCF", K["iv"], 2)
            return _end
        self.dcf[s] = {"iv": _ref("DCF", K["iv"], 2), "pv_fcf": _ref("DCF", K["pv_fcf"], 2),
                       "pv_tv": _ref("DCF", K["pv_tv"], 2), "ev": _ref("DCF", K["ev"], 2),
                       "nd": _ref("DCF", K["nd"], 2), "mi": _ref("DCF", K["mi"], 2), "pe": _ref("DCF", K["pe"], 2),
                       "eq": _ref("DCF", K["eq"], 2),
                       "fcf_row": fr, "rev_row": rv, "c0": c0, "n": n, "margin_row": mr, "ev_raw": K["ev"],
                       "shares": shs}
        self.leg_cell[(s, "DCF")] = self.dcf[s]["iv"]
        if s == "base" and getattr(self, "_is_sheet", None) is not None:
            self._link_is_forecast(s)
        r = _end + 1
        # REGN review (2026-10-07): why the perpetuity grows after years of decline.
        _tlm = _num(tr.get("terminal_loe_multiplier"))
        if _tlm is not None and _tlm < 0.9999:
            sh.note(r, 1, f"Loss of exclusivity: the projection erodes each named drug after its LOE (the negative growth "
                          f"years); erosion still to come after the final year is taken off the terminal value "
                          f"(x{_tlm:.3f}, Assumptions), so the perpetuity grows at the terminal rate from a post-cliff "
                          "base rather than from the declining trend.")
            r += 2
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
        bridge = f"-{nd}+$B${K['mi']}+$B${K['pe']}"
        loe = A[f"terminal_loe_multiplier:{s}{sfx}"]
        for i, k in enumerate((-2, -1, 0, 1, 2)):
            rr = r + 1 + i
            sh.put(rr, 1, f"={center_w}+({k})*{A['sens_w']}", PCT2, bold=True)
            for j in range(5):
                gc = f"{get_column_letter(2 + j)}${top}"
                wc = f"$A{rr}"
                # the same value-driver terminal value as above, at the flexed WACC and growth (RONIC = WACC + spread)
                _rf = f"IF({vd}=1,IF({wc}+{rsp}>0,1-{gc}/({wc}+{rsp}),1),1)"
                f = (f"=IFERROR(IF({wc}<={gc},NA(),((SUMPRODUCT(${F}${fr}:${Lc}${fr}/(1+{wc})^${F}${tpr}:${Lc}${tpr})"
                     f"+${Lc}${rv}*${Lc}${mr}*$B${K['ratio']}*(1+{gc})*{_rf}/({wc}-{gc})*{loe}/(1+{wc})^{t_end})"
                     f"*(1+{wc})^{roll}{bridge})/{shs}),NA())")
                sh.put(rr, 2 + j, f, NUM)
        return r + 6

    def _link_is_forecast(self, s: str) -> None:
        sh, rev, g, last = self._is_sheet, self._is_rev_row, self._is_g_row, self._is_last
        d = self.dcf[s]
        for t in range(5):
            c = last + 1 + t
            src = get_column_letter(d["c0"] + t)
            # the DCF is in millions of the valuation currency; the statements are in millions of the filing's
            sh.put(rev, c, f"=IFERROR('DCF'!{src}{d['rev_row']}/{self.A['fx']},\"\")", MIL)
        sh.note(3, 1, "Forecast (E) columns: FMP consensus as published, and the DCF base-case "
                      "revenue path the valuation uses, with the variance between them.")

    # ── Multiples ───────────────────────────────────────────────────────────
    def multiples_tab(self) -> None:
        sh = self.sheet("Multiples", "Each valuation leg: metric × multiple → EV → equity → per share")
        sh.title("Relative valuation legs", "Multiple = peer multiple × scenario band × growth premium × "
                                            "other adjustments (named). Check = rebuilt − engine.")
        # Audit 2026-11-25 (item 5): units in the headers; money in millions, per-share in the trading currency.
        cols = ["Scenario", "Leg", "Metric", f"Metric value ({self.ccy} m; per share for equity legs)",
                "Peer multiple (Comps)", "Scenario band", "Growth premium", "Other adj.", "Multiple",
                f"Enterprise value ({self.ccy} m)", f"Net debt ({self.ccy} m)", f"Minority + preferred ({self.ccy} m)",
                f"Equity value ({self.ccy} m)", "Diluted shares (m)", f"Value per share ({self.ccy})",
                f"Engine value ({self.ccy})", "Check", "Notes"]
        A = self.A

        def _peer(rr: int, tr: dict, typed: Optional[float], fmt: str) -> None:
            ref = self.peer_cell.get(self._peer_key(tr) or ())
            sh.put(rr, 5, ("=" + ref) if ref else typed, fmt)

        def _nd_sh(rr: int, tr: dict) -> None:
            nd, shs = _mil(tr.get("net_debt")), _mil(tr.get("shares"))
            _ndu = getattr(self, "_nd_used_v", None)
            _shu = getattr(self, "_sh_used_v", None)
            sh.put(rr, 11, ("=" + A["nd_used"]) if (A.get("nd_used") and nd is not None and _ndu is not None
                                                     and _close(nd, _ndu / 1e6)) else nd, MIL)
            sh.put(rr, 14, ("=" + A["shares_used"]) if (A.get("shares_used") and shs is not None and _shu
                                                         and _close(shs, _shu / 1e6)) else shs, MIL)
        sh.header(4, cols)
        r = 5
        recorded = False
        for s in SCENARIOS:
            legs = self.scen(s).get("leg_inputs") or {}
            table = self.scen(s).get("method_iv_table") or {}
            for leg in (list(legs) or list(table)):
                if not self.in_blend(leg):
                    continue
                if (s, leg) in getattr(self, "reit_legs", set()):
                    continue                    # rebuilt on the REIT tab (audit 2026-11-25, HK/SG defect 3)
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
                    sh.put(r, 3, (tr.get("metric") or "") + f" ({self.ccy} m)").font = Font(color=BLACK)
                    sh.put(r, 4, _mil(tr.get("metric_value")), MIL)
                    _peer(r, tr, _num(parts.get("peer_multiple")), MULT)
                    sh.put(r, 6, _num(parts.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, _num(parts.get("growth_premium", 1.0)) or 1.0, "0.000")
                    sh.put(r, 8, oprod, "0.000")
                    sh.put(r, 9, f"=E{r}*F{r}*G{r}*H{r}", MULT)
                    sh.put(r, 10, f"=D{r}*I{r}", MIL)
                    # Minority interest AND preferred equity: the engine's one bridge deducts both (V: $514m).
                    sh.put(r, 12, self._mi_pe_ref(tr), MIL)
                    sh.put(r, 13, f"=J{r}-K{r}-L{r}", MIL)
                    _nd_sh(r, tr)
                    sh.put(r, 15, f"=IFERROR(MAX(M{r}/N{r},0),0)", NUM)
                    notes = "values in millions; " + notes
                elif kind == "equity_multiple":
                    # Audit 2026-11-25 (item 5): the cell holds a per-share figure; a metric recorded as "Net income
                    # (...)" is labelled as the EPS it is.
                    _met = str(tr.get("metric") or "")
                    sh.put(r, 3, (f"EPS: {_met} ÷ diluted shares" if _met.lower().startswith("net income")
                                  else _met if "per share" in _met.lower() else f"{_met}, per share") + f" ({self.ccy})").font = Font(color=BLACK)
                    # Audit 2026-11-25 (HK/SG defect 4): a cited embedded value per share links to its Assumptions input.
                    _evp = (("=" + A["ev_ps"]) if (A.get("ev_ps") and _met.lower().startswith("embedded value per share")
                                                    and _close(tr.get("per_share_metric"), getattr(self, "_ev_ps_value", None)))
                            else _num(tr.get("per_share_metric")))
                    sh.put(r, 4, _evp, NUM)
                    _peer(r, tr, _num(parts.get("peer_multiple")), MULT)
                    sh.put(r, 6, _num(parts.get("scenario_band", 1.0)) or 1.0, "0.00")
                    sh.put(r, 7, _num(parts.get("growth_premium", 1.0)) or 1.0, "0.000")
                    sh.put(r, 8, oprod, "0.000")
                    sh.put(r, 9, f"=E{r}*F{r}*G{r}*H{r}", MULT)
                    sh.put(r, 15, f"=D{r}*I{r}", NUM)
                elif kind == "yield":
                    sh.put(r, 3, (tr.get("metric") or "") + f", per share ({self.ccy})").font = Font(color=BLACK)
                    sh.put(r, 4, _num(tr.get("per_share_metric")), NUM)
                    _peer(r, tr, _num(parts.get("peer_fcf_yield")), PCT2)
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
                    # Minority interest AND preferred equity: the engine's one bridge deducts both (V: $514m).
                    sh.put(r, 12, self._mi_pe_ref(tr), MIL)
                    sh.put(r, 13, f"=J{r}-K{r}-L{r}", MIL)
                    _nd_sh(r, tr)
                    sh.put(r, 15, f"=IFERROR(MAX(M{r}/N{r},0),0)", NUM)
                    notes = "earnings power: EBIT × band × (1 − tax) capitalised at WACC; values in millions"
                else:
                    done = False
                    notes = ("engine value: no metric × multiple form" if tr
                             else "engine value: run predates leg-input recording")
                if done:
                    sh.put(r, 17, f"=O{r}-P{r}", NUM)
                    self.leg_cell[(s, leg)] = _ref("Multiples", r, 15)
                    self.leg_meta[(s, leg)] = {"kind": kind, "row": r}
                else:
                    self.leg_cell.setdefault((s, leg), _ref("Multiples", r, 16))
                    if tr:
                        self.engine_only.add(leg)
                sh.note(r, 18, notes)
                r += 1
            r += 1
        if not recorded:
            sh.note(3, 1, "This run predates leg-input recording; legs show engine values only.")
        if getattr(self, "reit_legs", None):
            sh.note(r, 1, "REIT legs (" + ", ".join(sorted({l for _, l in self.reit_legs})) + ") are rebuilt on the REIT tab.")
        sh.widths({"A": 8, "B": 22, "C": 34, "D": 14, "E": 10, "F": 9, "G": 9, "H": 9, "I": 10,
                   "J": 14, "K": 13, "L": 12, "M": 14, "N": 12, "O": 12, "P": 12, "Q": 9, "R": 60})
        sh.ws.freeze_panes = "C5"

    # ── REIT (audit 2026-11-25, HK/SG defect 3) ─────────────────────────────
    _REIT_LEGS = ("DDM (S-REIT)", "S-REIT DDM", "NAV (Cap Rates)", "P/AFFO", "P/FFO")
    #: commit 2c6373fa (2026-11-25 14:18 +08): the S-REIT DDM capitalises D1 = DPU × (1 + g_DPU); a run priced before it
    #: capitalised D2 = D1 × (1 + g). The workbook rebuilds the formula the run's engine used and shows the current one.
    _SREIT_D1_FIX_AT = "2026-11-25T06:18:34+00:00"

    def _reit_legs_present(self) -> list[str]:
        out = []
        for leg in self._REIT_LEGS:
            for s in SCENARIOS:
                tr = (self.scen(s).get("leg_inputs") or {}).get(leg)
                if tr is not None and self.in_blend(leg) and tr.get("kind") in (None, "nav") and leg not in out:
                    if leg == "NAV (Cap Rates)" and tr.get("kind") != "nav":
                        continue
                    out.append(leg)
        return out

    def reit_tab(self) -> None:
        """Each REIT leg rebuilt from the inputs the engine recorded (reit_breakdown, the leg traces): the S-REIT DDM
        (DPU forward / (CoE − g)), NAV (NOI / cap rate − debt + cash, per unit) and P/AFFO / P/FFO (per unit × the
        sub-type multiple × scenario band × growth premium), each against the engine's value."""
        legs = self._reit_legs_present()
        self.reit_legs: set = set()
        if not legs:
            return
        rb = self.dr.get("reit_breakdown") or {}
        sh = self.sheet("REIT", "REIT legs: S-REIT DDM, NAV on cap rates, P/AFFO -- rebuilt from inputs")
        sh.title("REIT valuation legs", f"Per-unit values in {self.ccy}; money in {self.ccy} millions. Inputs (blue) carry "
                                         "their source; Check = rebuilt − engine.")
        A = self.A
        run_day = str(self.run.get("run_at") or "")[:10]
        r = 4
        sh.header(r, ["Input", "", "Value", "", "", "Source", "As of"]); r += 1
        inp: dict[str, str] = {}

        def put_in(key: str, label: str, v: Any, fmt: str, src: str, asof: str = "") -> None:
            nonlocal r
            sh.label(r, 1, label, indent=1)
            sh.put(r, 3, v, fmt)
            sh.put(r, 6, src).font = Font(color=BLACK)
            sh.put(r, 7, asof).font = Font(color=BLACK)
            inp[key] = f"$C${r}"
            r += 1

        units = _num(rb.get("shares"))
        if units is None:
            units = next((_num(tr.get("shares")) for tr in (self.scen("base").get("leg_inputs") or {}).values()
                          if isinstance(tr, dict) and _num(tr.get("shares"))), None)
        _shu = getattr(self, "_sh_used_v", None)
        put_in("units", "Units in issue (millions)", ("=" + A["shares_used"]) if (A.get("shares_used") and units and _shu
                                                                                  and _close(units, _shu)) else _mil(units),
               MIL, "Engine REIT record (reit_breakdown.shares) = diluted shares (Assumptions)", run_day)
        _bands = {"bear": 0.75, "base": 1.0, "bull": 1.25}
        sh.section(r, "Per-scenario inputs", 7); r += 1
        sh.header(r, ["Input", "", "Bear", "Base", "Bull", "Source"]); r += 1
        sc_row: dict[str, int] = {}

        def put_sc(key: str, label: str, vals: dict, fmt: str, src: str) -> None:
            nonlocal r
            sh.label(r, 1, label, indent=1)
            for j, s in enumerate(SCENARIOS):
                sh.put(r, 3 + j, vals.get(s), fmt)
            sh.put(r, 6, src).font = Font(color=BLACK)
            sc_row[key] = r
            r += 1

        put_sc("band", "Scenario band", {s: _bands[s] for s in SCENARIOS}, "0.00",
               "Engine constant (dcf_agent scenario_mult: bear 0.75 / base 1.00 / bull 1.25)")
        put_sc("gp", "Growth premium", {s: _num(self.scen(s).get("growth_premium")) or 1.0 for s in SCENARIOS}, "0.000",
               "Engine scenario record (growth_premium)")
        put_sc("gdpu", "DPU growth to the forward year (the DCF's base revenue growth)",
               {s: _num(((self.scen(s).get("leg_inputs") or {}).get("DCF") or {}).get("growth_base")) for s in SCENARIOS},
               PCT2, "Engine DCF trace growth_base (passed to the S-REIT DDM as dpu_growth)")
        col = lambda s: get_column_letter(3 + SCENARIOS.index(s))   # noqa: E731
        r += 1

        def engine(leg: str, s: str) -> Optional[float]:
            tr = (self.scen(s).get("leg_inputs") or {}).get(leg) or {}
            v = _num(tr.get("value"))
            return v if v is not None else _num((self.scen(s).get("method_iv_table") or {}).get(leg))

        def result_rows(leg: str, value_row: int) -> None:
            nonlocal r
            sh.label(r, 1, "Engine value", indent=1)
            for s in SCENARIOS:
                sh.put(r, 3 + SCENARIOS.index(s), engine(leg, s), NUM)
            r += 1
            sh.label(r, 1, "Check", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={L}{value_row}-{L}{r - 1}", NUM)
            r += 1
            for s in SCENARIOS:
                if engine(leg, s) is not None:
                    self.leg_cell[(s, leg)] = _ref("REIT", value_row, 3 + SCENARIOS.index(s))
                    self.reit_legs.add((s, leg))
            r += 1

        # NAV on cap rates
        if "NAV (Cap Rates)" in legs:
            leg = "NAV (Cap Rates)"
            trs = {s: (self.scen(s).get("leg_inputs") or {}).get(leg) or {} for s in SCENARIOS}
            t0 = trs.get("base") or next((t for t in trs.values() if t), {})
            sh.section(r, f"NAV (Cap Rates): NOI / cap rate − debt + cash, per unit ({t0.get('subtype') or ''})", 7); r += 1
            sh.header(r, ["Line", "", "Bear", "Base", "Bull", "Source"]); r += 1
            put_sc("noi", f"NOI ({self.ccy} m; {t0.get('noi_basis') or 'engine NOI'})", {s: _mil(trs[s].get("noi")) for s in SCENARIOS},
                   MIL, "Engine NAV trace (noi)")
            put_sc("cap", "Cap rate", {s: _num(trs[s].get("cap_rate")) for s in SCENARIOS}, PCT2,
                   f"Engine NAV trace ({t0.get('cap_rate_source') or 'cap_rate_source'})")
            put_sc("debt", f"Total debt ({self.ccy} m)", {s: _mil(trs[s].get("total_debt")) for s in SCENARIOS}, MIL,
                   "Engine NAV trace (total_debt)")
            put_sc("cash", f"Cash and equivalents ({self.ccy} m)", {s: _mil(trs[s].get("cash")) for s in SCENARIOS}, MIL,
                   "Engine NAV trace (cash)")
            g0 = r
            sh.label(r, 1, f"Gross asset value = NOI / cap rate ({self.ccy} m)", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"=IFERROR({L}{sc_row['noi']}/{L}{sc_row['cap']},0)", MIL)
            r += 1
            sh.label(r, 1, f"Net asset value = GAV − debt + cash ({self.ccy} m)", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={L}{g0}-{L}{sc_row['debt']}+{L}{sc_row['cash']}", MIL)
            r += 1
            sh.label(r, 1, f"NAV per unit = MAX(NAV / units, 0) ({self.ccy})", bold=True)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"=IFERROR(MAX({L}{r - 1}/{inp['units']},0),0)", NUM, bold=True)
            v = r; r += 1
            result_rows(leg, v)

        # S-REIT DDM
        ddm = next((l for l in ("DDM (S-REIT)", "S-REIT DDM") if l in legs), None)
        if ddm:
            coe = g = None
            src = ""
            try:
                from src.agents.analysis import dcf_agent as _da
                _ovr = _da._SREIT_DDM_OVERRIDES.get(self.ticker.upper())
                if _ovr and _ovr.get("coe") is not None:
                    coe, g = _ovr["coe"], _ovr.get("g")
                    src = f"Engine broker override ({_ovr.get('source')}, {_ovr.get('as_of')})"
                else:
                    _sub = _da._sgx_reit_subtype(self.ticker)
                    _cfg = _da._SREIT_DDM_CALIBRATION.get(_sub) or _da._SREIT_DDM_CALIBRATION["default"]
                    coe, g = _cfg["coe"], _cfg["g"]
                    src = f"Engine S-REIT DDM table, sub-sector {_sub} ({_cfg.get('src')})"
            except Exception:                              # noqa: BLE001
                pass
            _t = (self.scen("base").get("leg_inputs") or {}).get(ddm) or {}
            if _num(_t.get("cost_of_equity")) is not None:
                coe, src = _num(_t["cost_of_equity"]), "Engine DDM trace"
            if _num(_t.get("g")) is not None:
                g = _num(_t["g"])
            _rm = ((self.data.get("reit_metrics") or {}).get(self.ticker) or {})
            sh.section(r, f"{ddm}: value per unit = capitalised distribution / (CoE − g)", 7); r += 1
            put_in("dpu", f"DPU, last full year ({self.ccy} per unit)", _num(rb.get("dps")), "0.000000",
                   "Research DPU (reit_metrics.dpu_cents / 100)" if _rm.get("dpu_cents") is not None
                   else "Engine REIT record (reit_breakdown.dps)", run_day)
            put_in("coe", "Cost of equity (S-REIT DDM)", coe, PCT2, src + " -- the trace does not record it; the Check verifies")
            put_in("g", "Perpetual DPU growth g", g, PCT2, src)
            put_in("clamp", "Clamp on the one-year DPU growth (±)", 0.15, PCT, "Engine constant (_compute_sreit_ddm)")
            _pre_fix = False
            try:
                _ra = datetime.fromisoformat(str(self.run.get("run_at")).replace("Z", "+00:00"))
                _fx_at = datetime.fromisoformat(self._SREIT_D1_FIX_AT)
                _pre_fix = (_ra.tzinfo is not None) and _ra < _fx_at
            except Exception:                              # noqa: BLE001
                _pre_fix = False
            put_in("n", "Years of growth in the capitalised distribution (1 = D1, the current engine; 2 = D2, a run priced "
                        "before the 2026-11-25 D1 fix)", 2 if _pre_fix else 1, "0",
                   "Engine version at the run: " + ("priced before commit 2c6373fa (D2)" if _pre_fix else "D1 (commit 2c6373fa)"),
                   run_day)
            put_in("dp4", "Value rounding before the scenario band (decimal places, engine)", 4, "0",
                   "Engine constant (_compute_sreit_ddm)")
            sh.header(r, ["Line", "", "Bear", "Base", "Bull"]); r += 1
            gc = r
            sh.label(r, 1, "DPU growth used = MAX(MIN(growth, clamp), −clamp)", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"=MAX(MIN({L}{sc_row['gdpu']},{inp['clamp']}),-{inp['clamp']})", PCT2)
            r += 1
            d1 = r
            sh.label(r, 1, "D1 = DPU × (1 + growth used)", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={inp['dpu']}*(1+{L}{gc})", "0.000000")
            r += 1
            dc = r
            sh.label(r, 1, "Capitalised distribution = D1 × (1 + g) ^ (years − 1)", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={L}{d1}*(1+{inp['g']})^({inp['n']}-1)", "0.000000")
            r += 1
            vb = r
            sh.label(r, 1, "Value before the band = capitalised distribution / (CoE − g), engine rounding", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"=IFERROR(ROUND({L}{dc}/({inp['coe']}-{inp['g']}),{inp['dp4']}),0)", NUM)
            r += 1
            sh.label(r, 1, f"Value per unit = value × scenario band ({self.ccy})", bold=True)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={L}{vb}*{L}{sc_row['band']}", NUM, bold=True)
            v = r; r += 1
            result_rows(ddm, v)
            if _pre_fix:
                r -= 1
                sh.label(r, 1, "Memo: the current engine's D1 value (not this run's)", indent=1)
                for s in SCENARIOS:
                    L = col(s)
                    sh.put(r, 3 + SCENARIOS.index(s),
                           f"=IFERROR(ROUND({L}{d1}/({inp['coe']}-{inp['g']}),{inp['dp4']}),0)*{L}{sc_row['band']}", NUM)
                r += 2

        # P/AFFO, P/FFO
        for leg, base_key, mult_key, lab in (("P/AFFO", "affo", "p_affo_peer", "AFFO"), ("P/FFO", "ffo", "p_ffo_peer", "FFO")):
            if leg not in legs or _num(rb.get(base_key)) is None:
                continue
            sh.section(r, f"{leg}: {lab} per unit × (sub-type multiple × scenario band × growth premium)", 7); r += 1
            if base_key == "affo" and _num(rb.get("ffo")) is not None and _num(rb.get("normalized_maintenance_capex")) is not None:
                put_in("ffo", f"FFO ({self.ccy} m)", _mil(rb.get("ffo")), MIL, "Engine REIT record (reit_breakdown.ffo)", run_day)
                put_in("mcx", f"Normalised maintenance capex ({self.ccy} m)", _mil(rb.get("normalized_maintenance_capex")), MIL,
                       "Engine REIT record (reit_breakdown.normalized_maintenance_capex)", run_day)
                sh.label(r, 1, f"AFFO = FFO − maintenance capex ({self.ccy} m)", indent=1)
                sh.put(r, 3, f"={inp['ffo']}-{inp['mcx']}", MIL)
                inp["affo"] = f"$C${r}"; r += 1
                sh.label(r, 1, "Engine AFFO", indent=1); sh.put(r, 3, _mil(rb.get("affo")), MIL); r += 1
                sh.label(r, 1, "Check", indent=1); sh.put(r, 3, f"=C{r - 2}-C{r - 1}", MIL); r += 1
            elif base_key not in inp:
                put_in(base_key, f"{lab} ({self.ccy} m)", _mil(rb.get(base_key)), MIL, f"Engine REIT record (reit_breakdown.{base_key})",
                       run_day)
            put_in(mult_key, f"{leg} multiple (sub-type {rb.get('subtype') or 'default'})", _num(rb.get(mult_key)), MULT,
                   "Engine REIT sub-type table (_REIT_SUBTYPE_MULTIPLES)")
            sh.header(r, ["Line", "", "Bear", "Base", "Bull"]); r += 1
            ps = r
            sh.label(r, 1, f"{lab} per unit ({self.ccy})", indent=1)
            for s in SCENARIOS:
                sh.put(r, 3 + SCENARIOS.index(s), f"=IFERROR({inp[base_key]}/{inp['units']},0)", "0.000000")
            r += 1
            m = r
            sh.label(r, 1, "Multiple = sub-type multiple × band × growth premium", indent=1)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={inp[mult_key]}*{L}{sc_row['band']}*{L}{sc_row['gp']}", MULT)
            r += 1
            sh.label(r, 1, f"Value per unit ({self.ccy})", bold=True)
            for s in SCENARIOS:
                L = col(s)
                sh.put(r, 3 + SCENARIOS.index(s), f"={L}{ps}*{L}{m}", NUM, bold=True)
            v = r; r += 1
            result_rows(leg, v)
        sh.widths({"A": 70, "B": 2, "C": 14, "D": 14, "E": 14, "F": 60, "G": 12})

    def _bridge_ref(self, tr: dict, key: str, name: str, negate: bool = False) -> Any:
        """A leg's minority interest / preferred equity: the Assumptions cell when the leg used that figure (audit
        2026-11-25, HK/SG defect 4), else the trace's own value, typed (a leg on a different bridge)."""
        v = _num(tr.get(key)) or 0.0
        used = getattr(self, "_mi_used_v" if key == "minority_interest" else "_pe_used_v", None)
        if self.A.get(name) and used is not None and _close(v, used):
            return ("=-" if negate else "=") + self.A[name]
        return (-v if negate else v) / 1e6

    def _mi_pe_ref(self, tr: dict) -> Any:
        a, b = self._bridge_ref(tr, "minority_interest", "mi_used"), self._bridge_ref(tr, "preferred_equity", "pe_used")
        if isinstance(a, str) or isinstance(b, str):
            return "=" + "+".join(x[1:] if isinstance(x, str) else repr(float(x)) for x in (a, b)
                                  if isinstance(x, str) or abs(float(x)) > 0)
        return a + b

    # ── Comps ───────────────────────────────────────────────────────────────
    def _peer_key(self, tr: dict) -> Optional[tuple]:
        """The Comps basket a leg's peer multiple came from: (field or source label, value). Audit 2026-11-25
        (item 2): the leg's peer multiple links to that cell, not a typed copy."""
        import re as _re
        if not isinstance(tr, dict) or tr.get("kind") not in ("ev_multiple", "equity_multiple", "yield"):
            return None
        parts = tr.get("multiple_parts") or {}
        pv = _num(parts.get("peer_fcf_yield") if tr.get("kind") == "yield" else parts.get("peer_multiple"))
        if pv is None:
            return None
        fields = (self.dr.get("multiples_used") or {}).get("fields") or {}
        src = str(parts.get("peer_source") or "")
        m = _re.search(r"peer median (\w+)", src)
        # Audit 2026-11-25 (HK/SG defect 7): the field records its median to 4 d.p., a leg may record it to 6
        # (C6L P/BV 1.592597 against pb 1.5926) -- the named field within half a 4th-decimal unit is the same basket.
        if m and m.group(1) in fields and _num((fields[m.group(1)] or {}).get("value")) is not None \
                and abs(float(_num(fields[m.group(1)]["value"])) - pv) <= 0.5e-4 + 1e-12:
            return (m.group(1), round(pv, 8))
        if not src or not _re.search(r"through-cycle|dynamic|static", src):
            if tr.get("kind") == "yield" and _close(_num((fields.get("fcf_yield") or {}).get("value")), pv):
                return ("fcf_yield", round(pv, 8))
            for f in ("pe", "ev_ebitda", "pb", "ev_revenue", "ev_ocf", "ev_ebit", "pe_ntm", "ev_ebitda_ntm", "ev_revenue_ntm"):
                if f in fields and _close(_num((fields[f] or {}).get("value")), pv):
                    return (f, round(pv, 8))
        return (src or "source not recorded", round(pv, 8))

    def comps_tab(self) -> None:
        import statistics
        import re as _re
        sh = self.sheet("Comps", "Named peer baskets behind each peer multiple, incl. the through-cycle legs' sources")
        mu = self.dr.get("multiples_used") or {}
        sh.title("Comparable companies", f"Comps market {mu.get('comp_market')}; medians over in-band members "
                                         "(plausibility band). Engine median shown for the check.")
        # REGN review (2026-10-07): the forward (NTM) fields the forward legs actually use sit beside the trailing
        # ones -- the Forward P/E leg prices the peers' NTM P/E (16.7x), not the trailing P/E (24.8x).
        labels = {"ev_ebitda": "EV/EBITDA (trailing)", "pe": "P/E (trailing)", "ev_revenue": "EV/Revenue (trailing)",
                  "pb": "P/B", "fcf_yield": "FCF yield", "pe_ntm": "P/E (NTM) -- Forward P/E leg",
                  "ev_ebitda_ntm": "EV/EBITDA (NTM) -- Forward EV/EBITDA leg", "ev_revenue_ntm": "EV/Revenue (NTM)",
                  "ev_ocf": "EV/OCF (trailing)", "ev_ebit": "EV/EBIT (trailing)"}
        # which weighted legs price off which basket
        uses: dict[tuple, set] = {}
        for s in SCENARIOS:
            for leg, tr in (self.scen(s).get("leg_inputs") or {}).items():
                if self.in_blend(leg):
                    k = self._peer_key(tr)
                    if k:
                        uses.setdefault(k, set()).add(leg)
        r = 4
        self.comps_summary: list[tuple[str, Any, str]] = []
        field_cell: dict[str, str] = {}
        key_cell: dict[tuple, str] = {}
        for field, info in (mu.get("fields") or {}).items():
            if field not in labels:
                continue
            lab = labels[field]
            _legs = sorted({l for k, ls in uses.items() if k[0] == field for l in ls})
            sh.section(r, f"{lab}: {info.get('basis')} / cohort {info.get('cohort')}"
                          + (f" — {info.get('exchange')} '{info.get('key')}'" if info.get("key") else "")
                          + (f" — used by {', '.join(_legs)}" if _legs else ""), 7)
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
                _vals = []
                for m in members:
                    r += 1
                    mv = (m.get("metrics") or {}).get(field) or {}
                    # Owner, 2026-10-04 (plan 1F.3): the valued company is listed, never counted.
                    _is_me = _subj_sym(m.get("symbol")) == _me and info.get("subject_excluded") is True
                    sh.put(r, 1, m.get("symbol")).font = Font(color=BLACK)
                    sh.put(r, 2, (m.get("name") or "") + (" (the valued company: not its own peer)" if _is_me else "")).font = Font(color=BLACK)
                    sh.put(r, 3, _mil(m.get("market_cap")), MIL)
                    sh.put(r, 4, _num(mv.get("value")), "0.00")
                    _in = bool(mv.get("in_band") and not _is_me)
                    sh.put(r, 5, "yes" if _in else "no").font = Font(color=BLUE)
                    sh.put(r, 6, f'=IF(E{r}="yes",D{r},"")', "0.00")
                    if _in and _num(mv.get("value")) is not None:
                        _vals.append(_num(mv.get("value")))
                last = r
                r += 1
                sh.label(r, 5, "Median", bold=True)
                sh.put(r, 6, f"=MEDIAN(F{first}:F{last})", "0.0000", bold=True); _med = r
                r += 1
                sh.label(r, 5, "Median, engine precision", bold=True)
                sh.put(r, 6, f"=ROUND(F{_med},{self.A['dp_comps']})", "0.0000", bold=True); _rnd = r
                r += 1
                sh.label(r, 5, "Engine")
                sh.put(r, 6, _num(info.get("value")), "0.0000")
                eng_row = r
                r += 1
                sh.label(r, 5, "Check")
                sh.put(r, 6, f"=F{_rnd}-F{eng_row}", "0.0000")
                # Members come from the latest weekly refresh, not frozen with the
                # run: a refresh after the valuation date can move the basket.
                asof = max((str(m.get("computed_at") or "")[:10] for m in members), default="")
                run_day = str(self.run.get("run_at") or self.data.get("end_date") or "")[:10]
                sh.note(r, 7, f"members as of refresh {asof or 'unknown'}; run valued {run_day or 'unknown'}"
                              + ("; basket refreshed after the run, so a non-zero check is basket drift"
                                 if asof and run_day and asof > run_day else ""))
                # the legs link to the basket's own median when it reproduces the engine's; else to the engine cell
                _ties = bool(_vals) and _num(info.get("value")) is not None and \
                    abs(round(statistics.median(_vals), 4) - float(info.get("value"))) < 1e-9
                field_cell[field] = _ref("Comps", _rnd if _ties else eng_row, 6)
                self.comps_summary.append((lab, _ref("Comps", _rnd, 6), f"{len(members)} named peers",
                                           _num(info.get("value"))))
                # legs that recorded the median at a finer precision than the field (6 d.p.): the same median,
                # rounded as the leg recorded it, with its own check
                _rawmed = statistics.median(_vals) if _vals else None
                for k in [k for k in uses if k[0] == field and not _close(k[1], info.get("value"))]:
                    dp = next((d for d in (5, 6, 7, 8) if _rawmed is not None and abs(round(_rawmed, d) - k[1]) < 1e-12
                               and abs(round(k[1], d) - k[1]) < 1e-12), None)
                    if dp is None:
                        continue
                    r += 1
                    sh.label(r, 5, f"Median at the legs' recorded precision ({dp} d.p.): {', '.join(sorted(uses[k]))}", bold=True)
                    sh.put(r, 6, f"=ROUND(F{_med},{dp})", "0.000000", bold=True); _lr = r; r += 1
                    sh.label(r, 5, "Legs' recorded peer multiple")
                    sh.put(r, 6, float(k[1]), "0.000000"); r += 1
                    sh.label(r, 5, "Check")
                    sh.put(r, 6, f"=F{_lr}-F{r - 1}", "0.000000")
                    key_cell[k] = _ref("Comps", _lr, 6)
            else:
                sh.note(r, 1, "static/dynamic table: no named peer set" if info.get("basis") in
                        ("static", "dynamic", "unknown") else
                        "named members not recorded yet (populated by the weekly comps refresh)")
                r += 1
                sh.label(r, 5, "Engine")
                sh.put(r, 6, _num(info.get("value")), "0.0000")
                field_cell[field] = _ref("Comps", r, 6)
                self.comps_summary.append((lab, _ref("Comps", r, 6), f"{info.get('basis')}", _num(info.get("value"))))
            r += 2
        for k in uses:
            if k in key_cell:
                self.peer_cell[k] = key_cell[k]
            elif k[0] in field_cell:
                self.peer_cell[k] = field_cell[k[0]]
        # Audit 2026-11-25 (item 2): the baskets behind the legs whose peer multiple is not one of the fields above --
        # the through-cycle legs (dynamic pe_norm / ev_ebitda_norm medians) and any other recorded source. The run
        # records the median and its source, not the members: said so, and the figure is the input it is.
        _trail = {"pe_norm": "pe", "ev_ebitda_norm": "ev_ebitda", "ev_ebit_norm": "ev_ebit", "ev_revenue_norm": "ev_revenue"}
        for k, legs in uses.items():
            if k in self.peer_cell:
                continue
            src, val = k
            m = _re.search(r"through-cycle (\w+)", src)
            name = m.group(1) if m else None
            title = (f"Through-cycle {name} -- " if name else "Leg peer multiple -- ") + ", ".join(sorted(legs))
            sh.section(r, title, 7); r += 1
            sh.label(r, 1, "Source (as recorded)"); sh.put(r, 2, src).font = Font(color=BLACK); r += 1
            # Audit 2026-11-25 (HK/SG defect 7): a cohort / cross-market basket records its members in the leg trace
            # (multiple_parts.members_used: AIA's P/EV cohort) -- rendered and the median rebuilt from them.
            _mem = self._cohort_members(k)
            if _mem:
                r = self._cohort_table(sh, r, k, legs, _mem)
                continue
            sh.note(r, 1, "Members unavailable: the run records this median and its source, not the constituent peers "
                          "or their values" + ("; the through-cycle median is the peers' multiple on mid-cycle earnings."
                                               if name else "."))
            r += 1
            sh.label(r, 5, "Peer multiple (as recorded)", bold=True)
            sh.put(r, 6, val, "0.0000", bold=True); _vr = r; r += 1
            self.peer_cell[k] = _ref("Comps", _vr, 6)
            _tf = _trail.get(name or "")
            if _tf and _tf in field_cell:
                sh.label(r, 5, "Same industry, trailing median (above)")
                sh.put(r, 6, "=" + field_cell[_tf], "0.0000"); r += 1
                sh.label(r, 5, "Through-cycle ÷ trailing")
                sh.put(r, 6, f"=IFERROR(F{_vr}/F{r - 1},0)", "0.00\"x\""); r += 1
            self.comps_summary.append(((f"{name} (through-cycle)" if name else f"Peer multiple: {', '.join(sorted(legs))}"),
                                       _ref("Comps", _vr, 6), "members not recorded: " + (src or "no source"), val))
            r += 1
        sh.widths({"A": 22, "B": 42, "C": 16, "D": 12, "E": 28, "F": 14})

    def _cohort_members(self, k: tuple) -> list:
        """members_used recorded on any weighted leg whose peer key is k (a cohort / cross-market basket)."""
        for s in SCENARIOS:
            for leg, tr in (self.scen(s).get("leg_inputs") or {}).items():
                if not isinstance(tr, dict) or not self.in_blend(leg) or self._peer_key(tr) != k:
                    continue
                mem = (tr.get("multiple_parts") or {}).get("members_used")
                if isinstance(mem, list) and mem and all(isinstance(m, dict) for m in mem):
                    return mem
        return []

    def _missing_members(self, k: tuple) -> list:
        for s in SCENARIOS:
            for tr in (self.scen(s).get("leg_inputs") or {}).values():
                if isinstance(tr, dict) and self._peer_key(tr) == k:
                    mm = (tr.get("multiple_parts") or {}).get("members_missing")
                    if mm:
                        return list(mm)
        return []

    def _cohort_table(self, sh: _Sheet, r: int, k: tuple, legs: set, mem: list) -> int:
        """Member table for a cohort basket: price / per-share basis rebuilt per member where both are recorded, the
        median at the engine's precision, the engine's figure and the Check. Sets peer_cell[k] and comps_summary."""
        src, val = k
        _den = next((d for d in ("ev_per_share", "book_value_per_share", "bvps", "book_per_share", "eps")
                     if all(isinstance(m.get(d), (int, float)) for m in mem)), None)
        _rec = next((d for d in ("p_ev", "p_b", "p_bv", "pb", "pe", "multiple", "value")
                     if any(isinstance(m.get(d), (int, float)) for m in mem)), None)
        _price = all(isinstance(m.get("price"), (int, float)) for m in mem)
        sh.header(r, ["Ticker", "Currency", "Price", _den or "basis", "Recorded multiple", "Multiple (rebuilt)"]); r += 1
        first = r
        for m in mem:
            sh.put(r, 1, m.get("symbol")).font = Font(color=BLACK)
            sh.put(r, 2, m.get("currency") or "").font = Font(color=BLACK)
            sh.put(r, 3, _num(m.get("price")), NUM)
            sh.put(r, 4, _num(m.get(_den)) if _den else None, "0.0000")
            sh.put(r, 5, _num(m.get(_rec)) if _rec else None, "0.0000")
            if _price and _den:
                # price and basis in the member's own currency, so the ratio is currency-free
                sh.put(r, 6, f'=IFERROR(ROUND(C{r}/D{r},{self.A["dp_comps"]}),"")', "0.0000")
            else:
                sh.put(r, 6, f"=E{r}", "0.0000")
            r += 1
        last = r - 1
        _miss = self._missing_members(k)
        if _miss:
            sh.note(r, 1, "Cohort members without a usable figure (excluded by the engine): " + ", ".join(map(str, _miss)))
            r += 1
        sh.label(r, 5, "Median", bold=True)
        sh.put(r, 6, f"=MEDIAN(F{first}:F{last})", "0.0000", bold=True); _md = r; r += 1
        sh.label(r, 5, "Median, engine precision", bold=True)
        sh.put(r, 6, f"=ROUND(F{_md},{self.A['dp_comps']})", "0.0000", bold=True); _rd = r; r += 1
        sh.label(r, 5, "Engine (leg's peer multiple)")
        sh.put(r, 6, float(val), "0.0000"); r += 1
        sh.label(r, 5, "Check")
        sh.put(r, 6, f"=F{_rd}-F{r - 1}", "0.0000"); r += 1
        self.peer_cell[k] = _ref("Comps", _rd, 6)
        self.comps_summary.append((f"Peer multiple: {', '.join(sorted(legs))}", _ref("Comps", _rd, 6),
                                   f"{len(mem)} named peers (cohort: {src})", val))
        return r + 1

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
                sh.section(r, f"{s.upper()} — {name} ({tr.get('source') or tr.get('metric') or 'segments'})", 9); r += 1
                # Tie-out 2026-11-25 (BN4.SI / U96.SI / VLO): two SOTP traces carry no analyst `table` -- the holdco
                # look-through (template parts) and the segment-EV SOTP (revenue × margin × multiple). The tab used to
                # render an empty table (segment value 0) beside the engine's leg; each is rebuilt from its own parts.
                if not t.get("rows") and (tr.get("lookthrough") or {}).get("parts"):
                    r = self._sotp_lookthrough(sh, r, s, name, tr)
                    continue
                if not t.get("rows") and tr.get("segments"):
                    r = self._sotp_segments(sh, r, s, name, tr)
                    continue
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
                if last >= first:
                    sh.put(r, 7, f"=SUM(G{first}:G{last})", MIL, bold=True)
                else:
                    # Audit 2026-11-25: no segment rows recorded -- the empty range G{first}:G{first-1} included this
                    # cell itself (a circular reference). The engine's segment value is carried as the input it is.
                    sh.put(r, 7, _mil(t.get("segment_value")) or 0.0, MIL, bold=True)
                    sh.note(r, 8, "no segment rows recorded in the run: the engine's segment total, as an input")
                r += 1
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

    def _sotp_check(self, sh: _Sheet, r: int, s: str, name: str, tr: dict, ps: int) -> int:
        sh.label(r, 1, "Engine value")
        sh.put(r, 7, _num(tr.get("value")), NUM); r += 1
        sh.label(r, 1, "Check")
        sh.put(r, 7, f"=G{ps}-G{r - 1}", NUM); r += 2
        self.leg_cell[(s, name)] = _ref("SOTP", ps, 7)
        return r

    def _sotp_lookthrough(self, sh: _Sheet, r: int, s: str, name: str, tr: dict) -> int:
        """The holdco look-through (`holdco_sotp.look_through_value`): each template part is driver × stake or
        multiple × FX (and the template's units) × (1 − the part's own discount); the parts sum to the gross asset
        value, parent net debt comes off, the holdco discount is taken on the remainder, and the NAV is carried to
        the listing currency over the shares."""
        lt = tr.get("lookthrough") or {}
        sh.header(r, ["Division", "Basis", "Driver (millions)", "Stake / multiple", "FX × units",
                      "Part discount", "Value"])
        sh.note(r - 1, 8, f"Template currency {lt.get('reporting_currency') or ''}; values in millions")
        first = r + 1
        for p in lt.get("parts") or []:
            r += 1
            basis = p.get("basis") or ""
            gross = _num(p.get("gross_value")) or 0.0
            mr = p.get("multiple_range")
            lo_hi = (mr if isinstance(mr, (list, tuple)) and len(mr) == 2
                     and all(isinstance(x, (int, float)) for x in mr) else None)
            mid = f"=({float(lo_hi[0])}+{float(lo_hi[1])})/2" if lo_hi else None
            mid_v = (float(lo_hi[0]) + float(lo_hi[1])) / 2.0 if lo_hi else None
            drv, fac, fac_v, fx = None, 1.0, 1.0, None
            if basis == "market_stake" and _num(p.get("stake_pct")) and _num(p.get("fx_to_reporting")):
                stake, rate = float(p["stake_pct"]), float(p["fx_to_reporting"])
                drv, fac, fac_v, fx = gross / (stake * rate) / 1e6, stake, stake, rate     # the stake's market cap
            elif basis == "pe_range" and _num(p.get("net_profit")) is not None and lo_hi:
                drv, fac, fac_v = float(p["net_profit"]), mid, mid_v
            elif basis == "revenue_multiple" and _num(p.get("revenue")) is not None and lo_hi:
                drv, fac, fac_v = float(p["revenue"]), mid, mid_v
            elif basis == "cap_rate" and _num(p.get("ebit")) is not None and p.get("cap_rate_range"):
                c_lo, c_hi = p["cap_rate_range"]
                drv, fac, fac_v = float(p["ebit"]), f"=1/(({float(c_lo)}+{float(c_hi)})/2)", 2.0 / (float(c_lo) + float(c_hi))
            elif lo_hi and _num(p.get("ebit")) is not None:                              # ev_ebit_range
                drv, fac, fac_v = float(p["ebit"]), mid, mid_v
            elif lo_hi and _num(p.get("ebitda")) is not None:                            # EBITDA × multiple band
                drv, fac, fac_v = float(p["ebitda"]), mid, mid_v
            elif basis == "fixed_value" and _num(p.get("amount")) is not None:
                drv = float(p["amount"])
            elif basis == "transaction_anchor" and _num(p.get("enterprise_value")) is not None:
                drv = float(p["enterprise_value"])
            elif basis == "nil":
                drv = 0.0
            if drv is None:
                drv, fac, fac_v, fx = gross / 1e6, 1.0, 1.0, 1.0                           # stated as the part's value
            if fx is None:
                # The template records each figure in its own currency and units (units_multiplier); the factor
                # that takes driver × multiple to template-currency millions is read from the part's gross value.
                _den = drv * fac_v
                fx = (gross / 1e6 / _den) if _den else 1.0
            sh.put(r, 1, p.get("division")).font = Font(color=BLACK)
            sh.put(r, 2, basis).font = Font(color=BLACK)
            sh.put(r, 3, drv, MIL)
            sh.put(r, 4, fac, "0.0000")
            sh.put(r, 5, fx, "0.000000")
            sh.put(r, 6, _num(p.get("discount_pct")) or 0.0, PCT)
            sh.put(r, 7, f"=C{r}*D{r}*E{r}*(1-F{r})", MIL)
        last = r
        r += 1
        sh.label(r, 1, "Gross asset value", bold=True)
        sh.put(r, 7, f"=SUM(G{first}:G{last})" if last >= first else 0.0, MIL, bold=True); gav = r; r += 1
        sh.label(r, 1, "Less: parent net debt (plus net cash when negative)")
        sh.put(r, 7, -(_num(lt.get("parent_net_debt")) or 0.0) / 1e6, MIL); nd = r; r += 1
        sh.label(r, 1, "NAV before the holdco discount", bold=True)
        sh.put(r, 7, f"=G{gav}+G{nd}", MIL, bold=True); pre = r; r += 1
        sh.label(r, 1, "Holdco discount (template band midpoint)")
        sh.put(r, 7, _num(lt.get("holdco_discount")) or 0.0, PCT); dr_ = r; r += 1
        sh.label(r, 1, "Net asset value after the holdco discount", bold=True)
        sh.put(r, 7, f"=G{pre}*(1-G{dr_})", MIL, bold=True); nav = r; r += 1
        sh.label(r, 1, "FX to the listing currency")
        sh.put(r, 7, _num(lt.get("fx_to_listing")) or 1.0, "0.000000"); fx_r = r; r += 1
        sh.label(r, 1, "Shares (millions)")
        sh.put(r, 7, (_num(lt.get("shares")) or 0.0) / 1e6, MIL); shr = r; r += 1
        sh.label(r, 1, "Value per share", bold=True)
        sh.put(r, 7, f"=IFERROR(G{nav}*G{fx_r}/G{shr},0)", NUM, bold=True); ps = r; r += 1
        return self._sotp_check(sh, r, s, name, tr, ps)

    def _sotp_segments(self, sh: _Sheet, r: int, s: str, name: str, tr: dict) -> int:
        """The segment-EV SOTP (`_sotp_parts`): an energy segment is revenue × peer EBITDA margin × the scaler that
        reconciles the parts to company EBITDA × its EV/EBITDA multiple; another is revenue × its EV/Revenue
        multiple; an equity-accounted line is held at carrying value. The EVs sum, take the growth premium, and
        bridge to equity (net debt, minorities, preferred) over the shares, floored at zero."""
        sh.header(r, ["Segment", "Basis", "Revenue", "EBITDA margin", "Reconciliation scaler", "Multiple", "EV"])
        first = r + 1
        for p in tr.get("segments") or []:
            r += 1
            basis = p.get("basis") or ""
            sh.put(r, 1, p.get("segment")).font = Font(color=BLACK)
            sh.put(r, 2, basis).font = Font(color=BLACK)
            sh.put(r, 3, (_num(p.get("revenue")) or 0.0) / 1e6, MIL)
            mult = _num(p.get("multiple"))
            if basis == "ev_ebitda" and mult is not None and _num(p.get("ebitda_margin")) is not None:
                sh.put(r, 4, _num(p.get("ebitda_margin")), PCT2)
                sh.put(r, 5, _num(p.get("reconciliation_scaler")) or 1.0, "0.000000")
                sh.put(r, 6, mult, "0.00")
                sh.put(r, 7, f"=C{r}*D{r}*E{r}*F{r}", MIL)
            elif basis == "ev_revenue" and mult is not None:
                sh.put(r, 6, mult, "0.00")
                sh.put(r, 7, f"=C{r}*F{r}", MIL)
            else:
                # carrying value (equity-accounted), excluded or unpriced: the EV the part states
                sh.put(r, 7, (_num(p.get("carrying_value")) if p.get("carrying_value") is not None
                              else (_num(p.get("ev")) or 0.0)) / 1e6, MIL)
        last = r
        r += 1
        sh.label(r, 1, "Sum of segment EVs", bold=True)
        sh.put(r, 7, f"=SUM(G{first}:G{last})" if last >= first else 0.0, MIL, bold=True); seg = r; r += 1
        sh.label(r, 1, "× Growth premium")
        sh.put(r, 7, _num(tr.get("growth_premium")) or 1.0, "0.0000"); gp = r; r += 1
        sh.label(r, 1, "Enterprise value", bold=True)
        sh.put(r, 7, f"=G{seg}*G{gp}", MIL, bold=True); ev = r; r += 1
        sh.label(r, 1, "Less: net debt (plus net cash when negative)")
        sh.put(r, 7, -(_num(tr.get("net_debt")) or 0.0) / 1e6, MIL); nd = r; r += 1
        sh.label(r, 1, "Less: minority interest")
        sh.put(r, 7, -(_num(tr.get("minority_interest")) or 0.0) / 1e6, MIL); mi = r; r += 1
        sh.label(r, 1, "Less: preferred equity")
        sh.put(r, 7, -(_num(tr.get("preferred_equity")) or 0.0) / 1e6, MIL); pe = r; r += 1
        sh.label(r, 1, "Equity value", bold=True)
        sh.put(r, 7, f"=G{ev}+G{nd}+G{mi}+G{pe}", MIL, bold=True); eq = r; r += 1
        sh.label(r, 1, "Shares (millions)")
        sh.put(r, 7, (_num(tr.get("shares")) or 0.0) / 1e6, MIL); shr = r; r += 1
        sh.label(r, 1, "Value per share (floored at zero)", bold=True)
        sh.put(r, 7, f"=IFERROR(MAX(G{eq}/G{shr},0),0)", NUM, bold=True); ps = r; r += 1
        return self._sotp_check(sh, r, s, name, tr, ps)

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
        # Plan E9 (owner, 2026-10-07): guidance that is not company-level, said beside it.
        for _chk in (ge.get("scope_checks") or []):
            sh.note(r, 1, f"SCOPE CHECK ({_chk.get('check')}): {_chk.get('detail')}"); r += 1
        sh.section(r, f"Consensus quoted by the research ({fy1})", 6); r += 1
        sh.label(r, 1, "Revenue growth"); sh.put(r, 2, _num(c.get("revenue_growth_fy1")), PCT); r += 1
        sh.label(r, 1, "EPS"); sh.put(r, 2, _num(c.get("eps_fy1")), NUM); r += 1
        # REGN review (2026-10-07): the street consensus the forward legs price, beside the research's quote, and the
        # disagreement named -- the research quoted FactSet $32.50 for FY2026 while 15 analysts on FMP sit at $55.94.
        _sc = self.dr.get("street_consensus") or {}
        _se = _sc.get("eps") or {}
        if _se.get("fy1") is not None or _se.get("ntm") is not None:
            r += 1
            sh.section(r, f"Street consensus the forward legs price ({_sc.get('source') or 'FMP'}, "
                          f"{_sc.get('analyst_count_eps') or '?'} analysts on EPS)", 6); r += 1
            sh.header(r, ["Metric", f"FY+1 ({_sc.get('fy1_period') or ''})", f"FY+2 ({_sc.get('fy2_period') or ''})", "NTM"]); r += 1
            for lab, key, fmt in (("EPS", "eps", NUM), ("Revenue", "revenue", BIG)):
                d_ = _sc.get(key) or {}
                sh.label(r, 1, lab)
                for col, k in ((2, "fy1"), (3, "fy2"), (4, "ntm")):
                    if d_.get(k) is not None:
                        sh.put(r, col, _num(d_.get(k)), fmt)
                r += 1
            _q, _s1 = _num(c.get("eps_fy1")), _num(_se.get("fy1"))
            if _q and _s1 and abs(_q / _s1 - 1.0) > 0.25:
                sh.note(r, 1, f"The research's quoted consensus EPS ({_q:,.2f}) is {(_q / _s1 - 1.0):+.0%} from the street FY+1 "
                              f"({_s1:,.2f}): a different basis (GAAP vs adjusted) or a stale or mistaken quote. The Forward P/E "
                              "prices the street figure, the basis of the peers' NTM P/E; the research's quote is shown, not used.")
                r += 1
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
                    # Audit 2026-11-25 (item 6): the failure feeds the Checks tab and the master flag.
                    sh.label(rr, 1, "Engine suite result", bold=True)
                    sh.put(rr, 3, "FAIL").font = Font(bold=True, color=BLACK)
                    self.integrity.append(("Model", f"Three-statement build failed its reconciliation suite "
                                                    f"({len(_rc['failures'])} assertion(s)); not built", _ref("Model", rr, 3)))
                    rr += 2
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
        _rev0, _cogs0 = _num(op.get("revenue")), _num(op.get("cost_of_revenue"))
        if _rev0 and _cogs0:
            sh.put(A["gm"], C0, 1.0 - abs(_cogs0) / _rev0, PCT).font = Font(color=BLUE)          # as filed
        arow("em", "EBIT margin", IS["ebit_margin"], PCT)
        arow("da", "Depreciation & amortisation", [-x for x in IS["da"]], MIL, 1e-6)
        # Plan E1 (owner, 2026-10-07): of which amortisation of acquired intangibles -- it runs off goodwill &
        # intangibles, not PP&E (0 when the forecast is not amortisation-heavy).
        _am = list(((th.get("schedules") or {}).get("gi_amort")) or [0.0] * n)
        arow("amort", "  of which amortisation of acquired intangibles", [-float(x or 0.0) for x in _am][:n] + [0.0] * max(0, n - len(_am)), MIL, 1e-6)
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
        arow("noi", "Associates' share of profit and investment income (recurring, after tax; engine forecast)",
             list(IS.get("associates_income") or [0.0] * n), MIL, 1e-6)
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
        frow("assoc", "Associates and investment income (after tax)", lambda L, P: f"={L}{A['noi']}")
        frow("mino", "Minority interest", lambda L, P: f"=-({L}{R['pretax']}+{L}{R['tax']}+{L}{R['assoc']})*{L}{A['mi']}")
        frow("ni", "Net income to common", lambda L, P: f"={L}{R['pretax']}+{L}{R['tax']}+{L}{R['assoc']}+{L}{R['mino']}", bold=True, opening=(op.get("net_income") or 0) * M)
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
        frow("ppe_da", "− Depreciation (D&A less acquired-intangible amortisation)", lambda L, P: f"=-({L}{A['da']}-{L}{A['amort']})", typ="Link")
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
        frow("gi", "Goodwill & intangibles (less amortisation of acquired intangibles)", lambda L, P: f"={P}{R['gi']}-{L}{A['amort']}", opening=(op.get("goodwill_intangibles") or 0) * M)
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
        # Audit 2026-11-25 (item 6): invariants a forecast must respect -- a gross margin below the EBIT margin, or one
        # far from the last filed year, is a defect in the inputs, not a forecast.
        frow("inv1", "Invariant: gross margin ≥ EBIT margin", lambda L, P:
             f'=IF({L}{A["gm"]}="","n/a",IF({L}{A["gm"]}>={L}{A["em"]},"OK","FAIL"))', fmt=None, typ="Invariant")
        frow("inv2", f"Invariant: gross margin within the Assumptions band of {op.get('fy_label')}A", lambda L, P:
             f'=IF(OR({L}{A["gm"]}="",${OC}${A["gm"]}=""),"n/a",IF(ROUND(ABS({L}{A["gm"]}-${OC}${A["gm"]}),{self.A["dp_check"]})<={self.A["gm_band"]},"OK","FAIL"))',
             fmt=None, typ="Invariant")
        for i, L in enumerate(cols):
            self.integrity.append(("Model", f"{labels[i]}: reconciliation suite (balance, cash, retained earnings, PP&E, NWC)",
                                   _ref("Model", R["call"], C0 + 1 + i)))
            self.integrity.append(("Model", f"{labels[i]}: gross margin ≥ EBIT margin", _ref("Model", R["inv1"], C0 + 1 + i)))
            self.integrity.append(("Model", f"{labels[i]}: gross margin within the band of the last filed year",
                                   _ref("Model", R["inv2"], C0 + 1 + i)))
        _rc = th.get("reconciliation") or {}
        sh.label(r, 1, "Engine suite (Python build): " + ("ALL OK on every forecast column" if _rc.get("ok") else "FAILED — " + "; ".join(f"{f['year']} #{f['id']}" for f in _rc.get("failures") or [])), bold=True)
        sh.put(r, C0, "ALL OK" if _rc.get("ok") or not _rc else "FAIL").font = Font(color=BLACK, bold=True)
        self.integrity.append(("Model", "Engine suite (Python build)", _ref("Model", r, C0))); r += 1
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
        r += 2
        self._model_end = (sh, r, C0, cols, labels, R)
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
                     ("Total revenue (premiums + investment income)", "total_income", MIL), ("Earnings", None), ("Pre-tax profit", "pretax", MIL), ("Tax", "tax", MIL), ("Net income", "net_income", MIL), ("Dividends", "dividends", MIL), ("Shareholders' equity", "equity", MIL), ("Total assets", "total_assets", MIL),
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
        _how = ("Earnings come from the balance sheet (assets × margin, plus fees), costs and provisions are ratios, retained earnings build capital against the CET1 target; "
                "the GGM (P/B) leg prices the steady-state RoTE and the FY+1 book value, the forward P/E the FY+1 EPS." if kind == "bank" else
                "Earnings are the underwriting result (premiums × (1 − combined ratio)) plus investment income (float × yield); retained earnings build book value; "
                "the GGM (P/B) leg prices the steady-state ROE and the FY+1 book value, the forward P/E the FY+1 EPS.")
        sh.title(f"{'Bank' if kind == 'bank' else 'Insurer'} earnings-and-capital model",
                 f"In statement currency, millions. Opening column = {op.get('fy_label')} as filed. " + _how
                 + (f" USER OVERRIDE ({str(ovr.get('created_at') or '')[:10]}): {', '.join(ovr.get('fields') or [])}." if ovr else "")
                 + (" Guided by management: " + ", ".join(th.get("guided_fields") or []) + "." if th.get("guided_fields") else
                    f" No family guidance in the research: drivers from the {'bank' if kind == 'bank' else 'insurance'}-metrics extraction and the filings."))
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
        sh.label(r, 1, "Suite result: " + ("ALL OK on every forecast column" if rc.get("ok") else "FAILED"), bold=True)
        if rc:
            sh.put(r, C0, "ALL OK" if rc.get("ok") else "FAIL").font = Font(color=BLACK, bold=True)
            self.integrity.append(("Model", f"{kind} model reconciliation suite (engine build)", _ref("Model", r, C0)))
        r += 2
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

    def _model_fcf_reconciliation(self) -> None:
        """Audit 2026-11-25 (item 6): the Model's free cash flow beside the DCF's, year by year -- the difference shown,
        not forced. The DCF prices unlevered owner earnings (revenue × FCF margin, full year); the Model's FCF is
        CFO − capex, after interest and with SBC added back, in the statement currency."""
        end = getattr(self, "_model_end", None)
        d = self.dcf.get("base")
        if not end or not d:
            return
        sh, r, C0, cols, labels, R = end
        n = min(len(cols), d["n"])
        sh.section(r, "6. Reconciliation: Model FCF vs DCF FCF (difference shown, not forced)", C0 + len(cols)); r += 1
        sh.label(r, 1, "Model free cash flow (CFO − capex)"); sh.label(r, 2, "Link")
        for i in range(n):
            sh.put(r, C0 + 1 + i, f"={cols[i]}{R['fcf']}", MIL)
        _m = r; r += 1
        sh.label(r, 1, "DCF free cash flow, full year (revenue × FCF margin ÷ FX to the statement currency)"
                       + (" -- SHADOW DCF" if getattr(self, "dcf_shadow", False) else ""))
        sh.label(r, 2, "Link")
        for i in range(n):
            L = get_column_letter(d["c0"] + i)
            sh.put(r, C0 + 1 + i, f"=IFERROR('DCF'!{L}{d['rev_row']}*'DCF'!{L}{d['margin_row']}/{self.A['fx']},0)", MIL)
        _d = r; r += 1
        sh.label(r, 1, "Difference: Model − DCF (basis: interest, SBC, working-capital and capex paths)", bold=True)
        sh.label(r, 2, "Memo")
        for i in range(n):
            L = cols[i]
            sh.put(r, C0 + 1 + i, f"={L}{_m}-{L}{_d}", MIL, bold=True)
            self.info_checks.append(("Model", f"{labels[i]}: Model FCF − DCF FCF ({self.ccy} m; shown, not forced)",
                                     _ref("Model", r, C0 + 1 + i)))
        r += 1
        sh.label(r, 1, "Difference as % of DCF FCF"); sh.label(r, 2, "Memo")
        for i in range(n):
            L = cols[i]
            sh.put(r, C0 + 1 + i, f"=IFERROR({L}{r - 1}/ABS({L}{_d}),0)", PCT)
        r += 1
        sh.note(r, 1, "Not forced to zero: the DCF values unlevered owner earnings (after SBC, before interest) on its "
                      "own margin path; the Model's FCF is levered and adds SBC back. A large, persistent gap means the "
                      "two forecasts tell different stories -- read the valuation with it.")

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
                # Owner, 2026-11-24: a normalised leg carries the cycle-peak windfall (per share), stated in the formula.
                _cw = sc.get("cycle_windfall") or {}
                if _cw.get("per_share") and key in (_cw.get("legs") or []):
                    _pa_add += f"+{float(_cw['per_share']):.6f}"
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
                self.blend_w[(s, key)] = f"'Blend'!$D${r}"
            last = r
            r += 1
            sh.label(r, 1, "Weights (check = 1)")
            sh.put(r, 4, f"=SUM(D{first}:D{last})", "0.0000")
            sh.label(r, 5, "Blend", bold=True)
            sh.put(r, 6, f"=SUM(F{first}:F{last})", NUM, bold=True)
            self.blend_raw[s] = f"'Blend'!$F${r}"
            r += 1
            sh.label(r, 5, "× Calibration")
            sh.put(r, 6, "=" + self.A["calibration"], "0.0000")
            r += 1
            sh.label(r, 5, "Intrinsic value (published to 2 d.p.)", bold=True)
            sh.put(r, 6, f"=ROUND(F{r - 2}*F{r - 1},{self.A['dp']})", NUM, bold=True)
            iv_row = r
            r += 1
            # Tie-out 2026-11-25 (00883.HK): the engine floors the BEAR case at the reserve value
            # (`_apply_reserve_floor`: proved reserves after tax, less net debt, per share; owner 2026-09-20) --
            # it carries no weight, it is a floor. Rebuilt as MAX(blend, floor) so the bear Check ties
            # (24.05 blend -> 26.63 floor) and a run where the floor did not bind still reads the blend.
            _rf = sc.get("reserve_floor") or {}
            _floor = _num(_rf.get("floor_per_share"))
            if _floor is None and s == "bear":
                _floor = _num((sc.get("method_iv_table") or {}).get("Reserve NPV (PV-10)"))
            if s == "bear" and _floor is not None and _floor > 0:
                sh.label(r, 5, "Reserve floor: proved reserves PV, after tax, less net debt, per share")
                sh.put(r, 6, _floor, NUM)
                if _rf.get("period") or _rf.get("source_url"):
                    sh.note(r, 7, " ".join(str(x) for x in (_rf.get("period"), _rf.get("source_url")) if x))
                r += 1
                sh.label(r, 5, "Intrinsic value after the bear reserve floor", bold=True)
                sh.put(r, 6, f"=MAX(F{iv_row},F{r - 1})", NUM, bold=True)
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
        if pb and "base" in self.blend_raw:
            self._target_sensitivities(sh, 20)
        sh.widths({"A": 30, "B": 16, "C": 14, "D": 14, "E": 10, "F": 12, "G": 14, "H": 14})

    def _leg_value_at(self, s: str, leg: str, a: str) -> Optional[str]:
        """Formula for a leg's per-share value with its metric × multiple scaled by the factor expression `a`."""
        meta = self.leg_meta.get((s, leg))
        if meta:
            r = meta["row"]
            if meta["kind"] in ("ev_multiple", "capitalised_earnings"):
                return f"MAX(('Multiples'!$J${r}*{a}-'Multiples'!$K${r}-'Multiples'!$L${r})/'Multiples'!$N${r},0)"
            return f"'Multiples'!$O${r}*{a}"
        d = self.dcf.get(s)
        if leg == "DCF" and d and not getattr(self, "dcf_shadow", False):
            # EV is linear in the margin path: FCF and the terminal value both scale with it
            return f"IFERROR(({d['ev']}*{a}+{d['nd']}+{d['mi']}+{d['pe']})/{d['shares']},0)"
        return None

    def _target_sensitivities(self, sh: _Sheet, r: int) -> None:
        """Audit 2026-11-25 (item 8): the target against the anchor leg's peer multiple × metric, against capture ×
        scenario probabilities, and -- for a cyclical profile -- against the margin (±20%)."""
        A = self.A
        steps = (-2, -1, 0, 1, 2)
        spot, cap, cal, raw = A["spot"], A["capture"], A["calibration"], self.blend_raw["base"]
        legs = [(k, w) for (s_, k), w in self.blend_w.items() if s_ == "base"]
        anchor = self.dr.get("anchor_method")
        mult_legs = [k for k, _ in legs if (("base", k) in self.leg_meta)]
        if mult_legs:
            leg = anchor if anchor in mult_legs else max(mult_legs, key=lambda k: _num(
                next((e.get("weight") for e in self.scen("base").get("effective_weights") or [] if e.get("value_key") == k), 0)) or 0)
            w, old = self.blend_w[("base", leg)], self.leg_cell[("base", leg)]
            sh.section(r, f"Sensitivity: base target vs the {leg} leg's peer multiple (rows) × its metric (columns)", 8); r += 1
            sh.note(r, 1, "The leg is re-priced at the flexed multiple and metric; the other legs are held. "
                          "Centre = the base target before publishing rounding."); r += 1
            sh.label(r, 1, "Multiple factor \\ metric factor")
            for j, k in enumerate(steps):
                sh.put(r, 2 + j, f"=1+({k})*{A['sens_m']}", PCT, bold=True)
            top = r
            for i, k in enumerate(steps):
                rr = r + 1 + i
                sh.put(rr, 1, f"=1+({k})*{A['sens_m']}", PCT, bold=True)
                for j in range(5):
                    fac = f"$A{rr}*{get_column_letter(2 + j)}${top}"
                    val = self._leg_value_at("base", leg, fac)
                    sh.put(rr, 2 + j, f"=IFERROR({spot}+{cap}*(({raw}+{w}*({val}-{old}))*{cal}-{spot}),NA())", NUM)
            r += 7
        # capture × probability
        sh.section(r, "Sensitivity: probability-weighted target vs capture (rows) × probability moved from bear to bull (columns)", 8); r += 1
        sh.label(r, 1, "Capture \\ bear → bull shift")
        for j, k in enumerate(steps):
            sh.put(r, 2 + j, f"=({k})*{A['sens_p']}", PCT, bold=True)
        top = r
        for i, k in enumerate(steps):
            rr = r + 1 + i
            sh.put(rr, 1, f"=MAX(0,MIN(1,{cap}+({k})*{A['sens_c']}))", PCT, bold=True)
            for j in range(5):
                d = f"{get_column_letter(2 + j)}${top}"
                pb_, pu = f"MAX($F$5-{d},0)", f"MAX($F$7+{d},0)"
                sh.put(rr, 2 + j, f"=IFERROR({spot}+$A{rr}*(($B$5*{pb_}+$B$6*$F$6+$B$7*{pu})/({pb_}+$F$6+{pu})-{spot}),0)", NUM)
        r += 7
        cyc = self.dr.get("cycle") or {}
        if cyc.get("cyclical"):
            cols = [(k, self._leg_value_at("base", k, "$A{rr}")) for k, _ in legs]
            cols = [(k, f) for k, f in cols if f]
            sh.section(r, "Cyclical: margin ±20% -- each leg's metric (normalised earnings, EBITDA, the DCF's margin path) "
                          "scaled, other legs held", 8); r += 1
            sh.header(r, ["Margin factor"] + [f"{k} (per share)" for k, _ in cols] + ["Intrinsic value (base)", "Base target"])
            top = r
            for i, k in enumerate(steps):
                rr = r + 1 + i
                sh.put(rr, 1, f"=1+({k})*{A['sens_m']}", PCT, bold=True)
                deltas = []
                for j, (leg, f) in enumerate(cols):
                    sh.put(rr, 2 + j, "=IFERROR(" + f.format(rr=rr) + ",NA())", NUM)
                    deltas.append(f"{self.blend_w[('base', leg)]}*({get_column_letter(2 + j)}{rr}-{self.leg_cell[('base', leg)]})")
                c_iv = 2 + len(cols)
                sh.put(rr, c_iv, f"=({raw}+{'+'.join(deltas) or '0'})*{cal}", NUM)
                sh.put(rr, c_iv + 1, f"={spot}+{cap}*({get_column_letter(c_iv)}{rr}-{spot})", NUM)
            r = top + 7
            sh.note(r, 1, f"Cycle position {cyc.get('position') or 'n/a'}; mid-cycle margin {(_num(cyc.get('mid_cycle_margin')) or 0):.1%} "
                          f"against the latest {(_num(cyc.get('latest_margin')) or 0):.1%}. Net income is scaled in proportion "
                          "(interest and tax held as a share), an approximation for the P/E legs.")

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
            sh.label(r, 1, _humanize(k), indent=1)                 # audit 2026-11-25: readable, not snake_case
            c = sh.put(r, 2, v if not isinstance(v, (dict, list)) else str(v))
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                c.font = Font(color=BLACK)
            r += 1
        sh.label(r, 1, "Note", indent=1)
        sh.put(r, 2, self.dr.get("calibration_note")).font = Font(color=BLACK)
        r += 2
        sh.section(r, "Gate ledger", 5); r += 1
        sh.header(r, ["Gate", "Metric", "Without the gate (path A)", "With the gate (path B)", "Applied"])
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
        # Audit 2026-11-25 (item 7): the rate actually used -- the DCF trace's, the build's final rate, else the record.
        _used = next((v for v in (_num(((self.scen("base").get("leg_inputs") or {}).get("DCF") or {}).get("wacc")),
                                  _num(_wb_.get("wacc_final")), _num(self.dr.get("wacc"))) if v is not None), None)
        if _used is not None:
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
            _rec_w = _num(self.dr.get("wacc"))
            sh.ws.cell(row=r, column=1, value=(
                f"WACC used by the valuation {float(_used):.2%}"
                + (f" (the run's headline record reads {_rec_w:.2%})" if _rec_w is not None and abs(_rec_w - _used) > 5e-5 else "")
                + (f"; WACC tab 'WACC used' {_num(_wb_.get('wacc_final')):.2%}" if _num(_wb_.get("wacc_final")) is not None
                   and abs(_num(_wb_.get("wacc_final")) - _used) > 5e-5 else "")
                + (f" = build {float(_wb_['wacc']):.2%}" if isinstance(_wb_.get("wacc"), (int, float)) else "")
                + (f" + macro-regime overlay {_ov_:+.2%}" if _ov_ is not None else "")
                + (f" ({_bb_.get('macro_regime')} regime)" if _bb_.get("macro_regime") else "") + "; parts, as on the WACC tab: "
                + (_txt or "n/a") + " (the regime overlay is inside the base rate, counted once)."))
            sh.ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        sh.widths({"A": 60, "B": 50, "C": 16, "D": 16, "E": 10})

    # ── Data gaps ───────────────────────────────────────────────────────────
    def _structural_gaps(self) -> list[tuple]:
        """What the model does not do, from what this build actually contains (audit 2026-11-25, item 6): the old
        static list claimed "no balance-sheet forecast" and "no schedules" on workbooks whose Model tab had both."""
        out = []
        kind = getattr(self, "model_kind", None)
        th = self.dr.get("three_statements") or {}
        if kind == "operating":
            out.append(("Model architecture", "Three-statement forecast built (Model tab): IS, working-capital, PP&E and "
                        "debt schedules, CF and BS, live formulas", "The DCF still projects revenue × FCF margin on its own "
                        "path; the Model's FCF is reconciled to it (shown, not forced)", "Info"))
            out.append(("Circularity", "None: interest is charged on opening balances", "No iterative calculation needed", "Info"))
        elif kind in ("bank", "insurer"):
            out.append(("Model architecture", f"{kind.capitalize()} earnings-and-capital model built (Model tab) as the "
                        "engine computed it (values, not live formulas)", "Change a driver in the engine, not the workbook",
                        "Low"))
        else:
            why = th.get("skipped") if isinstance(th, dict) else None
            out.extend([("Model architecture", "No balance-sheet or cash-flow forecast: no three-statement model on this run"
                         + (f" ({why})" if why else ""),
                         "Statements are historical only; the IS forecast block shows FMP consensus; net debt is held at "
                         "the latest reported figure", "Structural"),
                        ("Supporting schedules", "No working-capital, PP&E/depreciation or debt/interest schedules",
                         "Reinvestment is implicit in the FCF margin", "Structural")])
        if (self.dr.get("wacc_build") or {}).get("firm_capm"):
            out.append(("WACC", "Firm CAPM built on the WACC tab from labelled inputs (rf, relevered / Blume-blended beta, "
                        "ERP, CRP, after-tax cost of debt at market weights)", "The sector table rate it replaced is shown as a "
                        "memo; the legacy Damodaran Jan-2026 CAPM line is a cross-check only", "Info"))
        else:
            out.append(_STRUCTURAL_GAPS[3])                 # WACC: table-driven, CAPM as a cross-check
        if not getattr(self, "dcf_weighted", lambda: True)():
            out.append(("DCF", "The DCF carries no weight in the blend", "Shown as a shadow on the DCF tab, outside "
                        "the intrinsic value and the target", "Info"))
        return out

    def gaps_tab(self) -> None:
        sh = self.sheet("Data Gaps", "What the institutional format asks for that this run's data "
                                     "or the engine does not provide")
        sh.title("Data gaps", "Detected for this run, then what this build does and does not model.")
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
        for lab, _ref_, basis, *_ in getattr(self, "comps_summary", []):
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
        for area, gap, impact, sev in run_gaps + gaps + self._structural_gaps():
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
        # Audit 2026-11-25 (item 1): the master check flag, linked to the Checks tab (written there last).
        sh.label(r, 1, "Master check flag (every check in the workbook; Checks tab)", bold=True)
        sh.put(r, 3, "='Checks'!$C$4", bold=True)
        sh.put(r, 4, "='Checks'!$C$5", "0")
        sh.note(r, 5, "tie-out failures")
        sh.put(r, 6, "='Checks'!$C$6", "0")
        sh.note(r, 7, "integrity failures")
        sh.put(r, 8, "='Checks'!$C$7", "0")
        sh.note(r, 9, "statement failures (reported data)")
        r += 1
        for _line in self.front_page_flags():
            sh.label(r, 1, _line, bold=True); r += 1
        r += 1
        # Owner, 2026-09-27: the key metrics the profile's report FAMILY is judged on,
        # read from the run's per-year raw financials (static values, input colour).
        try:
            from src.data.report_families import report_family_for, family_rows, format_value
            _fam = report_family_for(self.dr.get("profile"))
            _fu_k = self.dr.get("financials_used") or {}
            # audit 2026-11-25 (HK/SG defect 6): DPS from the engine's statement rows (valuation currency, so ÷ FX
            # back to the raw records' currency) where the raw records carry none
            _raw = (self.run.get("data") or {}).get("raw_financials")
            # fiscal-year records only: a "notes" dict (01299) was read as a year column
            _raw = ({k: v for k, v in _raw.items() if str(k).upper().startswith("FY") and isinstance(v, dict)}
                    if isinstance(_raw, dict) else _raw)
            _fys, _frows = family_rows(_raw, _fam, years=3,
                                       fallback_rows=_fu_k.get("rows"),
                                       fallback_scale=1.0 / (_num(_fu_k.get("fx_rate")) or 1.0))
        except Exception:                                  # noqa: BLE001
            _fam, _fys, _frows = None, [], []
        if _frows:
            sh.section(r, f"Key metrics ({_fam})", 6); r += 1
            sh.header(r, ["Metric"] + [str(fy) for fy in _fys] + ["Unit"]); r += 1
            _uc = 2 + len(_fys)
            # Owner, 2026-10-04 (plan 1A.6): the units, stated (the statements elsewhere are in millions).
            _sc = (self.dr.get("financials_used") or {}).get("source_currency") or self.ccy   # raw records are as filed
            # Audit 2026-11-25 (HK/SG defect 6): every money and per-share row names its currency; where the filing
            # currency is not the valuation currency, each per-share row is converted beside it at the engine FX
            # (01299: USD per-share figures sat beside HKD valuations unlabelled).
            _xccy = _sc != self.ccy
            sh.note(r, 1, f"Units: amounts in {_sc} billions (as filed); per-share figures in {_sc}"
                          + (f", each converted to {self.ccy} on the line below at the engine FX (Assumptions)" if _xccy else "")
                          + "; ratios as shown."); r += 1
            for label, kind, vals in _frows:
                sh.label(r, 1, label, indent=1)
                sh.put(r, _uc, {"bn": f"{_sc} bn, as filed", "ps": f"{_sc} per share, as filed", "pct": "%",
                                "x": "x"}[kind]).font = Font(color=BLACK)
                for j, v in enumerate(vals):
                    if v is None:
                        sh.put(r, 2 + j, "n/a").font = Font(color=BLUE)
                    else:
                        sh.put(r, 2 + j, float(v) / (1e9 if kind == "bn" else 1.0),
                               {"bn": '#,##0.0"B"', "ps": NUM, "pct": PCT, "x": '0.00"x"'}[kind]).font = Font(color=BLUE)
                r += 1
                if kind == "ps" and _xccy and A.get("fx"):
                    sh.label(r, 1, f"{label} ({self.ccy})", indent=2)
                    sh.put(r, _uc, f"{self.ccy} per share = {_sc} × engine FX (Assumptions)").font = Font(color=BLACK)
                    for j, v in enumerate(vals):
                        L = get_column_letter(2 + j)
                        sh.put(r, 2 + j, f"=IF(ISNUMBER({L}{r - 1}),{L}{r - 1}*{A['fx']},\"n/a\")", NUM)
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
            sh.section(r, ("Shadow DCF summary (base) -- weight 0, NOT in the blend or the target"
                           if getattr(self, "dcf_shadow", False) else "DCF summary (base)")
                       + f" -- {self.ccy} millions", 6); r += 1
        if d:
            # static engine values from the DCF leg trace (per-share PVs times the share count)
            _li = ((self.dr.get("base") or {}).get("leg_inputs") or {}).get("DCF") or {}
            # audit 2026-11-25 (item 5): in millions, as the DCF tab
            _sh_n = (_li.get("shares") / 1e6) if isinstance(_li.get("shares"), (int, float)) and _li.get("shares") else None
            _pvf = (_li.get("pv_fcf_per_share") or 0.0) * _sh_n if _sh_n and isinstance(_li.get("pv_fcf_per_share"), (int, float)) else None
            _pvt = (_li.get("pv_tv_per_share") or 0.0) * _sh_n if _sh_n and isinstance(_li.get("pv_tv_per_share"), (int, float)) else None
            _ev = (_pvf + _pvt) if (_pvf is not None and _pvt is not None) else None
            _nd = _li.get("net_debt") / 1e6 if isinstance(_li.get("net_debt"), (int, float)) else None
            _mi = float(_li.get("minority_interest") or 0.0) / 1e6 if isinstance(_li.get("minority_interest"), (int, float)) else 0.0
            _pe = float(_li.get("preferred_equity") or 0.0) / 1e6 if isinstance(_li.get("preferred_equity"), (int, float)) else 0.0
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
                    sh.put(r, 2, float(_static[key]), NUM if key == "iv" else MIL).font = Font(color=BLUE)
                sh.put(r, 3, "=" + d[key], NUM if key == "iv" else MIL)
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
            # REGN review (2026-10-07): the bridge from the data feed's net debt to the valuation's, as formulas.
            _cmp = ((_fu.get("net_debt_basis") or {}).get("components") or {})
            if isinstance(_cmp.get("feed_net_debt"), (int, float)) and not _cmp.get("regulated_cash_rule"):
                _src_ccy = _fu.get("source_currency") or self.ccy
                # Audit 2026-11-25 (HK/SG defect 6): components whose result IS the net debt used are already in the
                # valuation currency (00941: HKD components labelled "CNY millions" beside × FX = 1) -- the label names
                # the currency they are in.
                _in_val_ccy = (_v_nd is not None and isinstance(_cmp.get("result"), (int, float))
                               and abs(float(_cmp["result"]) - float(_v_nd)) <= 0.005 * max(abs(float(_v_nd)), 1.0))
                _bccy = self.ccy if _in_val_ccy else _src_ccy
                sh.label(r, 1, f"Bridge, {_bccy} millions"
                               + (f" (components already converted from {_src_ccy} at the engine FX)" if _in_val_ccy and _src_ccy != self.ccy else "")
                               + f", balance-sheet date {(_fu.get('net_debt_basis') or {}).get('balance_sheet_date') or ''}",
                         bold=True); r += 1
                _b0 = r
                for _lab, _v in (("Feed net debt (total debt incl. leases − cash and equivalents)", _cmp.get("feed_net_debt")),
                                 ("Less: short-term investments (counted as cash)", -(_cmp.get("short_term_investments") or 0.0)),
                                 ("Less: long-term investments counted as cash (biotech treasury; non-financial holdings, audit E18)", -(_cmp.get("long_term_investments") or 0.0)),
                                 ("Less: lease liabilities (US GAAP: rent is inside EBITDA)", -(_cmp.get("leases_removed") or 0.0)),
                                 ("Add: owner-accepted debt-like items", (_cmp.get("debt_like") or 0.0))):
                    sh.label(r, 1, _lab, indent=1); sh.put(r, 2, float(_v) / 1e6, MIL).font = Font(color=BLUE); r += 1
                sh.label(r, 1, f"Valuation net debt ({_bccy}" + (", filing currency" if _bccy == _src_ccy else "") + ")",
                         indent=1, bold=True)
                sh.put(r, 2, f"=SUM(B{_b0}:B{r - 1})", MIL, bold=True); _bs = r; r += 1
                _fxv = _num(_fu.get("fx_rate")) or 1.0
                # Since the REGN-review bridge, a non-USD reporter's components can already be in the valuation currency
                # (their result IS the net debt used); multiplying by FX again failed every such check (09988.HK, 00177.HK).
                if _in_val_ccy:
                    _fxv = 1.0
                sh.label(r, 1, f"× FX to {self.ccy}" + (f" (1: already in {self.ccy})" if _in_val_ccy else ""), indent=1)
                sh.put(r, 2, _fxv if (_in_val_ccy or not A.get("fx")) else "=" + A["fx"], "0.0000"); _bf = r; r += 1
                if _v_nd is not None:
                    # Audit 2026-11-25 (item 1): the net debt used is linked (Assumptions), not typed into the formula.
                    sh.label(r, 1, "Check: bridge × FX − net debt used (should be ~0)", indent=1)
                    _ndu = A.get("nd_used")
                    sh.put(r, 2, f"=B{_bs}*B{_bf}-{_ndu}" if _ndu else f"=B{_bs}*B{_bf}-{_v_nd / 1e6}", MIL); r += 1
            sh.note(r, 1, "They differ by date (annual vs latest quarter), by what counts as cash (short-term and, for a biotech, "
                          "long-term marketable securities; regulated or float cash), by leases, and by currency; only the "
                          "first prices the valuation. The Model tab's net debt is a PROJECTION (cash builds over the "
                          "forecast years), and the Summary metrics row is the feed's FY-end figure, shown for reference."); r += 2
        # Plan E10 (owner, 2026-10-07): what the price implies.
        _rd = (self.dr or {}).get("reverse_dcf") or {}
        if _rd.get("solved"):
            sh.section(r, "Reverse DCF (what the price implies)", 6); r += 1
            for lab, v, fmt in (("Price", _rd.get("spot"), NUM), ("Implied 10-year revenue CAGR", _rd.get("implied_cagr10"), PCT),
                                ("Model's 10-year revenue CAGR", _rd.get("model_cagr10"), PCT),
                                ("Growth shift on every year (margins and discount rate held)", _rd.get("growth_shift"), PCT)):
                sh.label(r, 1, lab, indent=1); sh.put(r, 2, _num(v), fmt); r += 1
            r += 1
        sh.section(r, "Comps (peer multiples used)", 6); r += 1
        for lab, ref, basis, _v in getattr(self, "comps_summary", []):
            sh.label(r, 1, lab, indent=1)
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

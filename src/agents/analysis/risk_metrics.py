"""Balance sheet & risk metrics, and the deterministic value-trap balance-sheet check.

Owner, 2026-11-27 (contrarian QA items 4, 6, 10): interest cover, the credit rating, volatility and
liquidity were computed during a run and never shown; short interest and SBC reached the desktop only;
net dilution was never computed; the value-trap balance-sheet check went RED only on a three-year rising
trend, so a company sitting at steady high leverage (SNAP) passed GREEN.

Everything here reads values the run already holds (the `data` payload: dcf_range, raw_financials,
price_history, analyst_signals, short_interest, bank metrics). No network calls. The same functions run
in the pipeline (state["data"]) and on a stored payload (on read), so old runs get the card too.

Thresholds (owner):
  net debt / EBITDA   > 3x AMBER, > 5x RED (net debt against negative EBITDA is RED)
  interest cover      < 3x AMBER, < 1.5x RED
  banks               routed to capital: CET1 < 11% AMBER, < 9% RED; loan-to-deposit > 100% AMBER, > 110% RED
  trend               net debt / EBITDA rising every year: 3 years (4 periods) RED, 2 years AMBER, only
                      when the latest level is above 1x
  NOT ASSESSED        fewer than 3 periods for the trend, or the latest statement older than 18 months
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Optional

NOT_ASSESSED = "NOT ASSESSED"

ND_EBITDA_AMBER, ND_EBITDA_RED = 3.0, 5.0
COVER_AMBER, COVER_RED = 3.0, 1.5
CET1_AMBER, CET1_RED = 0.11, 0.09
LDR_AMBER, LDR_RED = 1.00, 1.10
SBC_OCF_BREACH = 0.25          # same bar as the earnings-quality "HIGH" SBC drag
DILUTION_3Y_BREACH = 0.05      # net share count up more than 5% over three years
STALE_DAYS = 548               # 18 months
TREND_MIN_PERIODS = 3

_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}
_VERDICTS = ["TRAP RISK LOW", "TRAP RISK MEDIUM", "TRAP RISK HIGH"]
_TRAP_CHECK_KEYS = ("dividend_sustainability", "structural_decline", "earnings_cashflow_mismatch",
                    "insider_behaviour", "balance_sheet_deterioration")


# ── small helpers ───────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _period_date(key: str, row: dict) -> Optional[date]:
    for k in ("period_end", "period", "date", "fiscal_date_ending"):
        d = _parse_date(row.get(k))
        if d:
            return d
    d = _parse_date(key)                      # "FY2025 (Ending 2025-06-30)"
    if d:
        return d
    m = re.match(r"\s*FY\s*(\d{4})\s*$", str(key or ""), re.I)
    if m:
        return date(int(m.group(1)), 12, 31)   # bare fiscal-year key: assume a December year end
    return None


def _x(v: Optional[float], digits: int = 1) -> str:
    return f"{v:.{digits}f}x"


def _pct(v: Optional[float], digits: int = 0, signed: bool = False) -> str:
    return f"{v * 100:{'+' if signed else ''}.{digits}f}%"


def _money(v: Optional[float]) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:,.1f}M"
    return f"{v:,.0f}"


def _ebitda(r: dict) -> Optional[float]:
    e = _num(r.get("ebitda"))
    if e is not None:
        return e
    da = _num(r.get("depreciation_and_amortization"))
    base = _num(r.get("operating_income"))
    if base is None:
        base = _num(r.get("ebit"))
    if base is not None and da is not None:
        return base + abs(da)
    return None


def annual_rows(data: dict, ticker: str) -> list[dict]:
    """Annual statement rows, NEWEST FIRST, each with a `_date`. The DCF's own FMP rows
    (dcf_range.financials_used.rows) win; the router's raw_financials is the fallback."""
    dr = ((data.get("dcf_range") or {}).get(ticker) or {})
    rows = ((dr.get("financials_used") or {}).get("rows")) or []
    out: list[dict] = []
    if isinstance(rows, list) and rows:
        for r in rows:
            if isinstance(r, dict):
                out.append({**r, "_date": _period_date("", r)})
    if not out:
        rf = data.get("raw_financials") or {}
        if isinstance(rf, dict) and isinstance(rf.get(ticker), dict):
            rf = rf[ticker]                   # a payload keyed by ticker
        for k, v in rf.items() if isinstance(rf, dict) else []:
            if isinstance(v, dict) and ("revenue" in v or "net_income" in v or "net_debt" in v):
                out.append({**v, "_date": _period_date(k, v), "_key": k})
    out.sort(key=lambda r: (r["_date"] or date.min), reverse=True)
    return out


def is_bank(data: dict, ticker: str) -> bool:
    dr = ((data.get("dcf_range") or {}).get(ticker) or {})
    if dr.get("bank_breakdown"):
        return True
    return bool(re.search(r"\bbank\b", str(dr.get("profile") or ""), re.I))


def _bank_capital(data: dict, ticker: str) -> tuple[Optional[float], Optional[float]]:
    dr = ((data.get("dcf_range") or {}).get(ticker) or {})
    bb = dr.get("bank_breakdown") or {}
    bm = ((data.get("bank_metrics_all") or {}).get(ticker)) or ((data.get("bank_metrics") or {}).get(ticker)) or {}
    cet1 = _num(bb.get("cet1_ratio"))
    if cet1 is None:
        cet1 = _num(bm.get("cet1_ratio"))
    ldr = _num(bb.get("loan_to_deposit_ratio"))
    if ldr is None:
        ldr = _num(bm.get("loan_to_deposit_ratio"))
    if cet1 is not None and cet1 > 1.5:      # quoted in percent
        cet1 /= 100.0
    if ldr is not None and ldr > 3.0:
        ldr /= 100.0
    return cet1, ldr


def _as_of(data: dict, as_of) -> date:
    d = _parse_date(as_of) if as_of is not None else None
    if d:
        return d
    for k in ("end_date", "run_at", "as_of"):
        d = _parse_date(data.get(k))
        if d:
            return d
    return date.today()


def _status(value: Optional[float], amber: float, red: float, higher_is_worse: bool = True) -> str:
    if value is None:
        return NOT_ASSESSED
    if higher_is_worse:
        return "RED" if value > red else "AMBER" if value > amber else "GREEN"
    return "RED" if value < red else "AMBER" if value < amber else "GREEN"


def _metric(key: str, label: str, value: Optional[float], display: Optional[str], explain: str,
            reason: Optional[str] = None, status: Optional[str] = None, **extra) -> dict:
    m = {"key": key, "label": label, "value": value, "display": display if value is not None or display else None,
         "explain": explain, "status": status}
    if value is None and display is None:
        m["display"] = None
        m["reason"] = reason or "not in this run's data"
    m.update({k: v for k, v in extra.items() if v is not None})
    return m


# ── the metrics ─────────────────────────────────────────────────────────────

def _interest_cover(data: dict, ticker: str, rows: list[dict]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """(cover, basis, reason-when-missing)."""
    wb = (((data.get("dcf_range") or {}).get(ticker) or {}).get("wacc_build")) or {}
    cov = _num(wb.get("interest_coverage"))
    if cov is not None:
        return cov, "EBIT / interest expense (valuation inputs)", None
    for r in rows[:1]:
        ie = _num(r.get("interest_expense"))
        ebit = _num(r.get("ebit"))
        if ebit is None:
            ebit = _num(r.get("operating_income"))
        if ie is not None and abs(ie) > 0 and ebit is not None:
            return ebit / abs(ie), "EBIT / interest expense (latest year)", None
        if ie is not None and ie == 0:
            return None, None, "no interest expense reported"
    return None, None, "interest expense not in this run's statements"


def _vol_1y(data: dict, ticker: str) -> tuple[Optional[float], Optional[str]]:
    ph = (data.get("price_history") or {}).get(ticker) or []
    closes = [_num(p.get("close")) for p in ph if isinstance(p, dict)]
    closes = [c for c in closes if c and c > 0]
    if len(closes) >= 120:
        rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])][-252:]
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252), f"{len(rets)} daily returns"
    rm = (((data.get("analyst_signals") or {}).get("risk_management_agent") or {}).get(ticker) or {})
    v = _num((rm.get("volatility_metrics") or {}).get("annualized_volatility"))
    if v is not None:
        return v, "60-day, annualised (no 1-year price history in this run)"
    return None, None


def assemble_risk_metrics(data: dict, ticker: str, as_of=None) -> dict:
    """The `data.risk_metrics[ticker]` block: one entry per metric with value, display text, a plain
    one-line explanation, and a reason when missing; plus the balance-sheet check and the
    risks-to-rating line."""
    rows = annual_rows(data, ticker)
    bank = is_bank(data, ticker)
    dr = ((data.get("dcf_range") or {}).get(ticker) or {})
    ccy = (dr.get("financials_used") or {}).get("currency") or dr.get("reported_currency") or ""
    latest = rows[0] if rows else {}
    metrics: list[dict] = []

    # 1. leverage -- banks: capital instead
    if bank:
        cet1, ldr = _bank_capital(data, ticker)
        metrics.append(_metric(
            "cet1_ratio", "CET1 ratio", cet1, _pct(cet1, 1) if cet1 is not None else None,
            "Core equity capital as a share of risk-weighted assets; the regulator's main solvency test.",
            reason="the bank's capital ratio was not extracted this run",
            status=_status(cet1, CET1_AMBER, CET1_RED, higher_is_worse=False) if cet1 is not None else None))
        metrics.append(_metric(
            "loan_to_deposit", "Loan-to-deposit", ldr, _pct(ldr, 0) if ldr is not None else None,
            "Loans as a share of deposits; above 100% the bank leans on wholesale funding.",
            reason="loans and deposits were not extracted this run",
            status=_status(ldr, LDR_AMBER, LDR_RED) if ldr is not None else None))
    else:
        nd, eb = _num(latest.get("net_debt")), _ebitda(latest)
        if nd is None:
            metrics.append(_metric("net_debt_to_ebitda", "Net debt / EBITDA", None, None,
                                   "Years of operating profit needed to repay net debt.",
                                   reason="net debt not in this run's statements"))
        elif nd <= 0:
            metrics.append(_metric("net_debt_to_ebitda", "Net debt / EBITDA", nd / eb if eb and eb > 0 else None,
                                   f"Net cash ({ccy + ' ' if ccy else ''}{_money(-nd)})",
                                   "Cash exceeds debt, so leverage is not a constraint.", status="GREEN"))
        elif eb is None:
            metrics.append(_metric("net_debt_to_ebitda", "Net debt / EBITDA", None, None,
                                   "Years of operating profit needed to repay net debt.",
                                   reason="EBITDA not in this run's statements"))
        elif eb <= 0:
            metrics.append(_metric("net_debt_to_ebitda", "Net debt / EBITDA", None, "n/m (EBITDA negative)",
                                   f"Net debt of {_money(nd)} with negative EBITDA: operating profit does not service the debt.",
                                   status="RED"))
        else:
            r = nd / eb
            metrics.append(_metric("net_debt_to_ebitda", "Net debt / EBITDA", r, _x(r),
                                   "Years of operating profit needed to repay net debt; above 3x is stretched.",
                                   status=_status(r, ND_EBITDA_AMBER, ND_EBITDA_RED)))

    # 2. interest cover
    if bank:
        metrics.append(_metric("interest_cover", "Interest cover", None, None,
                               "Operating profit over interest expense.",
                               reason="not meaningful for a bank: interest paid is its cost of funding"))
    else:
        cov, basis, why = _interest_cover(data, ticker, rows)
        metrics.append(_metric("interest_cover", "Interest cover", cov, _x(cov) if cov is not None else None,
                               "Operating profit over interest expense; below 3x leaves little room.",
                               reason=why, basis=basis,
                               status=_status(cov, COVER_AMBER, COVER_RED, higher_is_worse=False) if cov is not None else None))

    # 3. credit rating (the one the cost of debt priced on)
    wb = dr.get("wacc_build") or {}
    rating = None if bank else wb.get("rating")
    src = wb.get("rating_source")
    if not src and rating:
        note = str(wb.get("agency_rating_note") or "")
        src = ("agency" if note.startswith("Cost of debt on the agency rating")
               else "net cash" if str(wb.get("source") or "") == "no-overlay-net-cash"
               else "implied by interest cover")
    # 2026-11-27: with no interest expense in the valuation inputs the cost of debt is priced
    # at a default AAA (sector_profiles.synthetic_rating(None)); "implied by interest cover"
    # beside an n/a cover read as a contradiction.
    if rating and src == "implied by interest cover" and _num(wb.get("interest_coverage")) is None:
        src = "assumed: no interest expense in the valuation inputs"
    metrics.append(_metric(
        "credit_rating", "Credit rating", None,
        f"{rating} ({src})" if rating else None,
        "The rating the cost of debt is priced on; below BBB- is sub-investment grade.",
        reason="the valuation did not price a cost of debt" if not bank else "banks are not rated on interest cover",
        rating=rating, source=src))

    # 4. volatility
    vol, vbasis = _vol_1y(data, ticker)
    metrics.append(_metric("volatility_1y", "Volatility (1 year)", vol, _pct(vol, 0) if vol is not None else None,
                           "Annualised standard deviation of daily returns; the market runs near 15-20%.",
                           reason="no price history in this run", basis=vbasis))

    # 5. liquidity
    arm = (((data.get("analyst_signals") or {}).get("advanced_risk_manager") or {}).get(ticker) or {})
    days, adv = _num(arm.get("liquidity_days_to_exit")), _num(arm.get("liquidity_adv_dollars"))
    metrics.append(_metric(
        "liquidity", "Liquidity / days to exit", days,
        (f"{'<0.1' if days < 0.1 else f'{days:.1f}'} days; ADV ${_money(adv)}" if days is not None and adv
         else (f"{days:.1f} days" if days is not None else None)),
        "Days to sell the model position trading 20% of average daily value.",
        reason="average daily volume was not available", adv_dollars=adv, flag=arm.get("liquidity_flag")))

    # 6. short interest
    si = ((data.get("short_interest") or {}).get(ticker) or {})
    sf, dtc = _num(si.get("short_float_pct")), _num(si.get("days_to_cover"))
    if sf is None:
        sf = _num(si.get("si_short_float_pct"))
    disp = None
    if sf is not None or dtc is not None:
        disp = " | ".join(x for x in (f"{sf:.1f}% of float" if sf is not None else None,
                                      f"{dtc:.1f} days to cover" if dtc is not None else None) if x)
    metrics.append(_metric("short_interest", "Short interest", sf if sf is not None else dtc, disp,
                           "Shares sold short, and the days of normal volume needed to buy them back.",
                           reason="short interest was not reported for this listing",
                           short_float_pct=sf, days_to_cover=dtc, report_date=si.get("report_date")))

    # 7. SBC / OCF
    sbc, ocf = _num(latest.get("stock_based_compensation")), _num(latest.get("operating_cash_flow"))
    if sbc is None:
        metrics.append(_metric("sbc_pct_ocf", "SBC / operating cash flow", None, None,
                               "Share-based pay as a share of operating cash flow.",
                               reason="share-based pay not in this run's statements"))
    elif ocf is None or ocf <= 0:
        metrics.append(_metric("sbc_pct_ocf", "SBC / operating cash flow", None,
                               "n/m (operating cash flow negative)" if ocf is not None else None,
                               "Share-based pay as a share of operating cash flow.",
                               reason="operating cash flow not in this run's statements",
                               status="RED" if ocf is not None and abs(sbc) > 0 else None))
    else:
        r = abs(sbc) / ocf
        metrics.append(_metric("sbc_pct_ocf", "SBC / operating cash flow", r, _pct(r, 0),
                               "Share-based pay counted as cash flow; above 25% it is a large hidden cost.",
                               status="AMBER" if r > SBC_OCF_BREACH else "GREEN"))

    # 8. 3-year net share count change
    shares = [(r["_date"], _num(r.get("shares_outstanding"))) for r in rows if _num(r.get("shares_outstanding"))]
    chg = None
    if len(shares) >= 4 and shares[0][1] and shares[3][1]:
        chg = shares[0][1] / shares[3][1] - 1.0
    metrics.append(_metric(
        "share_count_change_3y", "Net share count, 3 years", chg, _pct(chg, 1, signed=True) if chg is not None else None,
        "Change in reported shares outstanding after buybacks; positive means dilution (a split or bonus issue also lifts it).",
        reason=f"needs four annual share counts, this run has {len(shares)}",
        status=("AMBER" if chg is not None and chg > DILUTION_3Y_BREACH else "GREEN" if chg is not None else None)))

    bs_check = assess_balance_sheet(data, ticker, as_of=as_of)
    out = {
        "ticker": ticker,
        "is_bank": bank,
        "currency": ccy or None,
        "latest_period": latest.get("_date").isoformat() if latest.get("_date") else None,
        "metrics": metrics,
        "balance_sheet_check": bs_check,
    }
    out["risk_line"] = balance_sheet_risk_line(out)
    return out


# ── the value-trap balance-sheet check ──────────────────────────────────────

def assess_balance_sheet(data: dict, ticker: str, as_of=None) -> dict:
    """{status, evidence, level, trend}: worst of the level and trend tests that could run."""
    rows = annual_rows(data, ticker)
    ref = _as_of(data, as_of)
    parts: list[str] = []
    statuses: list[str] = []

    if is_bank(data, ticker):
        cet1, ldr = _bank_capital(data, ticker)
        if cet1 is not None:
            s = _status(cet1, CET1_AMBER, CET1_RED, higher_is_worse=False)
            statuses.append(s)
            parts.append(f"CET1 {_pct(cet1, 1)} ({s}; amber below {CET1_AMBER:.0%}, red below {CET1_RED:.0%})")
        if ldr is not None:
            s = _status(ldr, LDR_AMBER, LDR_RED)
            statuses.append(s)
            parts.append(f"loan-to-deposit {_pct(ldr, 0)} ({s})")
        if not statuses:
            return {"status": NOT_ASSESSED, "kind": "bank_capital",
                    "evidence": "Not assessed: a bank is judged on capital (CET1, loan-to-deposit), and neither was in this run's data."}
        st = max(statuses, key=lambda x: _RANK[x])
        return {"status": st, "kind": "bank_capital",
                "evidence": "Bank capital check: " + "; ".join(parts) + "."}

    if not rows:
        return {"status": NOT_ASSESSED, "kind": "leverage",
                "evidence": "Not assessed: no annual statements in this run's data."}
    latest_d = rows[0].get("_date")
    if latest_d is None or (ref - latest_d).days > STALE_DAYS:
        when = latest_d.isoformat() if latest_d else "an undated period"
        return {"status": NOT_ASSESSED, "kind": "leverage",
                "evidence": f"Not assessed: the latest statement ({when}) is more than 18 months old."}

    # level: net debt / EBITDA
    nd, eb = _num(rows[0].get("net_debt")), _ebitda(rows[0])
    level_r = None
    if nd is not None and nd <= 0:
        statuses.append("GREEN")
        parts.append(f"net cash of {_money(-nd)} at {latest_d.isoformat()}")
    elif nd is not None and eb is not None and eb <= 0:
        statuses.append("RED")
        parts.append(f"net debt {_money(nd)} against negative EBITDA ({_money(eb)}) at {latest_d.isoformat()} (RED)")
    elif nd is not None and eb:
        level_r = nd / eb
        s = _status(level_r, ND_EBITDA_AMBER, ND_EBITDA_RED)
        statuses.append(s)
        parts.append(f"net debt/EBITDA {_x(level_r)} at {latest_d.isoformat()} ({s}; amber above 3x, red above 5x)")

    # level: interest cover
    cov, _basis, _why = _interest_cover(data, ticker, rows)
    if cov is not None:
        s = _status(cov, COVER_AMBER, COVER_RED, higher_is_worse=False)
        statuses.append(s)
        parts.append(f"interest cover {_x(cov)} ({s}; amber below 3x, red below 1.5x)")

    # trend: net debt / EBITDA rising every year
    ratios: list[float] = []
    for r in rows[:4]:
        n_, e_ = _num(r.get("net_debt")), _ebitda(r)
        if n_ is None or e_ is None or e_ <= 0:
            break
        ratios.append(n_ / e_)
    if len(ratios) < TREND_MIN_PERIODS:
        parts.append(f"trend not assessed ({len(ratios)} consecutive year{'s' if len(ratios) != 1 else ''} "
                     f"with net debt and positive EBITDA, needs {TREND_MIN_PERIODS})")
    else:
        rising = all(a > b for a, b in zip(ratios[:-1], ratios[1:]))
        steps = len(ratios) - 1
        chron = " -> ".join(_x(v) for v in reversed(ratios))
        if rising and ratios[0] > 1.0:
            s = "RED" if steps >= 3 else "AMBER"
            statuses.append(s)
            parts.append(f"net debt/EBITDA rose {steps} years running ({chron}) ({s})")
        else:
            statuses.append("GREEN")
            parts.append(f"no sustained rise in leverage ({chron})")

    if not statuses:
        return {"status": NOT_ASSESSED, "kind": "leverage",
                "evidence": "Not assessed: neither net debt/EBITDA nor interest cover could be computed from this run's data."}
    st = max(statuses, key=lambda x: _RANK[x])
    return {"status": st, "kind": "leverage", "evidence": "Balance sheet check: " + "; ".join(parts) + "."}


def _rule_verdict(checks: dict) -> str:
    sts = [str((checks.get(k) or {}).get("status") or "").upper() for k in _TRAP_CHECK_KEYS]
    assessed = [s for s in sts if s in _RANK]
    if not assessed:
        return "TRAP RISK NOT ASSESSED"
    reds, ambers = assessed.count("RED"), assessed.count("AMBER")
    if reds >= 2:
        return "TRAP RISK HIGH"
    if reds == 1 or ambers >= 2:
        return "TRAP RISK MEDIUM"
    return "TRAP RISK LOW"


def apply_balance_sheet_check(trap: dict, check: dict) -> dict:
    """A copy of the value-trap result with the deterministic balance-sheet check in place of the
    model's, and the verdict moved by exactly the change that check makes under the stated rule
    (the model's own judgement on items outside the five checks is kept)."""
    if not isinstance(trap, dict) or not trap or not check:
        return trap
    old = trap.get("balance_sheet_deterioration") or {}
    if old.get("deterministic") and old.get("status") == check.get("status") and old.get("evidence") == check.get("evidence"):
        return trap                                   # already applied (cache replay)
    new = dict(trap)
    new["balance_sheet_deterioration"] = {"status": check["status"], "evidence": check["evidence"],
                                          "deterministic": True,
                                          "model_status": old.get("model_status", old.get("status"))}
    before, after = _rule_verdict(trap), _rule_verdict(new)
    llm = str(trap.get("overall_verdict") or "")
    if llm in _VERDICTS and before in _VERDICTS and after in _VERDICTS:
        shift = _VERDICTS.index(after) - _VERDICTS.index(before)
        new["overall_verdict"] = _VERDICTS[max(0, min(2, _VERDICTS.index(llm) + shift))]
    elif llm not in _VERDICTS:
        new["overall_verdict"] = after
    if new.get("overall_verdict") != llm:
        new["verdict_before_balance_check"] = llm
    return new


# ── risks to rating ─────────────────────────────────────────────────────────

def balance_sheet_risk_line(rm: dict) -> Optional[str]:
    """One sentence naming each balance-sheet or dilution metric past its threshold; None if none is."""
    by = {m["key"]: m for m in (rm or {}).get("metrics") or []}
    bits: list[str] = []
    m = by.get("net_debt_to_ebitda")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"net debt is {m['display']} EBITDA (above 3x)" if m.get("value") is not None
                    else "net debt with negative EBITDA")
    m = by.get("interest_cover")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"interest cover is {m['display']} (below 3x)")
    m = by.get("cet1_ratio")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"CET1 is {m['display']} (below 11%)")
    m = by.get("loan_to_deposit")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"loans are {m['display']} of deposits (above 100%)")
    m = by.get("sbc_pct_ocf")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"share-based pay is {m['display']} of operating cash flow" if m.get("value") is not None
                    else "share-based pay is paid while operating cash flow is negative")
    m = by.get("share_count_change_3y")
    if m and m.get("status") in ("AMBER", "RED"):
        bits.append(f"the share count rose {m['display'].lstrip('+')} over three years")
    if not bits:
        return None
    s = "; ".join(bits)
    return f"Balance sheet and dilution: {s}."


def risk_metrics_for(data: dict, as_of=None) -> dict:
    """data.risk_metrics for every ticker in the payload; {} entries never raise."""
    tickers = list(data.get("tickers") or []) or list((data.get("decisions") or {}).keys()) \
        or list((data.get("dcf_range") or {}).keys())
    out: dict[str, Any] = {}
    for t in tickers:
        try:
            out[t] = assemble_risk_metrics(data, t, as_of=as_of)
        except Exception as exc:                          # noqa: BLE001 -- disclosure only
            out[t] = {"ticker": t, "metrics": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
    return out

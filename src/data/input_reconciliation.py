"""Every run reconciles its key inputs before they price anything (owner, 2026-11-25, CFA review item 5).

The 2026-11-25 HK/SG audit found five data errors by hand, each worth 20-80% on a valuation leg:
  * FMP's book value per share is TOTAL equity per share (Ping An +41% on the minorities of Ping An Bank);
  * FMP builds a bank's EBITDA with interest added back (OCBC's "core" earnings 2x reported);
  * key-metrics market caps on an A-share price for an H-share line (China Telecom);
  * a reporting-currency cap compared with a listing-currency one (HSBC, AIA);
  * an "embedded value" that was book value per share (PICC).
Nothing caught them because nothing checked the identities the statements must satisfy. These checks do, on the
figures the valuation actually uses, and the run carries the result: each check passes, fails, or could not be
run (inputs missing), with the numbers. A failure is a flag and a disclosed data limitation; it never silently
changes a value.

Pure functions over the engine's `most_recent` row -- no I/O.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

#: Tolerances, per identity.
BVPS_TOL = 0.05            # BVPS x shares vs shareholders' equity
EPS_TOL = 0.10             # NI / shares vs reported EPS (share count, ADS ratio, units)
NET_DEBT_TOL = 0.02        # debt - cash vs the feed's net debt (relative to debt)
NON_OPERATING_SHARE = 0.25 # EBITDA - (operating income + D&A) as a share of EBITDA
DIVIDEND_TOL = 0.25        # DPS x shares vs dividends paid (timing and scrip allow a wide band)
STALE_MONTHS = 15          # latest statement period end vs the run date
INPUT_STALE_MONTHS = 18    # an accepted industry input's period vs the run date


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _check(name: str, status: str, detail: str, **numbers) -> dict:
    return {"check": name, "status": status, "detail": detail,
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in numbers.items()}}


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12)


def _months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month)


def _parse_date(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def check_bvps(row: dict, shares: Optional[float]) -> dict:
    """Book value per share against the equity it claims to divide, on ONE date and ONE share basis.

    The statement's BVPS is shareholders' equity at the period end over that period's share count (FMP: equity
    over `weightedAverageShsOut`, restated on the parent's equity in tools/api.py). The check multiplied it by the
    engine's CURRENT valuation count instead -- V's as-converted 1,880m against the 1,689m Class A count the BVPS
    divides (11.3%), HSBC's count a year of buybacks later (5.7%), Alibaba's current diluted quote count (7.9%) --
    so it failed on every run and caught nothing. Compared here: BVPS x the statement's own count (basic, then
    diluted; the valuation count only when the statement has none) against shareholders' equity attributable to
    the parent (minorities excluded) for the same period. What it still catches: book on total equity (the Ping An
    minorities case), a BVPS in another currency or unit, or from another period. The valuation count's gap is
    reported in the detail, not failed: a buyback or a share class is not a data error."""
    name = "book value per share x shares = shareholders' equity"
    bv = _f(row.get("book_value_per_share"))
    # The engine's `total_equity` IS FMP's totalStockholdersEquity (tools/api.py), the parent's equity.
    eq = _f(row.get("shareholders_equity"))
    if eq is None:
        eq = _f(row.get("total_equity"))
    basis = None
    for key, label in (("shares_outstanding_basic", "period basic shares"),
                       ("shares_outstanding", "period diluted shares")):
        sh = _f(row.get(key))
        if sh and sh > 0:
            basis = label
            break
    if basis is None:
        sh, basis = _f(shares), "valuation share count"
    if not (bv and eq and sh and bv > 0 and eq > 0 and sh > 0):
        return _check(name, "not_run", "inputs missing")
    implied = bv * sh
    r = _rel(implied, eq)
    detail = (f"{bv:,.4f} x {sh / 1e6:,.1f}m ({basis}) = {implied / 1e9:,.2f}bn vs shareholders' equity "
              f"{eq / 1e9:,.2f}bn ({r:.1%} apart)")
    cur = _f(shares)
    if cur and cur > 0 and basis != "valuation share count" and abs(cur / sh - 1.0) > 0.005:
        detail += f"; the valuation counts {cur / 1e6:,.1f}m shares ({cur / sh - 1.0:+.1%} on the period count)"
    if r > BVPS_TOL:
        mi = _f(row.get("minority_interest"))
        if mi and mi > 0 and _rel(implied, eq + mi) <= BVPS_TOL:
            detail += ": the per-share figure is TOTAL equity including minority interests"
    return _check(name, "pass" if r <= BVPS_TOL else "fail", detail, gap=r, share_basis=basis)


def check_eps(row: dict, shares: Optional[float]) -> dict:
    ni, eps, sh = _f(row.get("net_income")), _f(row.get("earnings_per_share")), _f(shares)
    if not (ni and eps and sh and sh > 0 and abs(eps) > 1e-9):
        return _check("net income / shares = reported EPS", "not_run", "inputs missing")
    implied = ni / sh
    r = _rel(implied, eps)
    return _check("net income / shares = reported EPS", "pass" if r <= EPS_TOL else "fail",
                  f"{implied:,.4f} vs reported {eps:,.4f} ({r:.1%} apart): a share count, ADS ratio or units "
                  f"difference" if r > EPS_TOL else f"{implied:,.4f} vs reported {eps:,.4f}", gap=r)


def check_net_debt(row: dict) -> dict:
    td, cash, nd = _f(row.get("total_debt")), _f(row.get("cash_and_equivalents")), _f(row.get("net_debt"))
    if td is None or cash is None or nd is None or not td:
        return _check("total debt - cash = net debt", "not_run", "inputs missing")
    r = abs((td - cash) - nd) / max(abs(td), 1e-12)
    return _check("total debt - cash = net debt", "pass" if r <= NET_DEBT_TOL else "fail",
                  f"{(td - cash) / 1e9:,.2f}bn vs feed {nd / 1e9:,.2f}bn", gap=r)


def check_ebitda_bridge(row: dict, interest_is_cost: bool = False) -> dict:
    eb, opi = _f(row.get("ebitda")), _f(row.get("operating_income"))
    da = _f(row.get("depreciation_and_amortization")) or 0.0
    if not (eb and opi is not None and eb > 0):
        return _check("EBITDA = operating income + D&A", "not_run", "inputs missing")
    gap = eb - (opi + da)
    share = gap / eb
    if abs(share) <= NON_OPERATING_SHARE:
        return _check("EBITDA = operating income + D&A", "pass", f"non-operating share {share:.1%}", share=share)
    if interest_is_cost:
        return _check("EBITDA = operating income + D&A", "fail",
                      f"{gap / 1e9:,.2f}bn ({share:.0%} of EBITDA) above the operating line: the feed added interest back "
                      f"on a deposit- or float-funded business, whose interest is its cost of goods", share=share)
    return _check("EBITDA = operating income + D&A", "warn",
                  f"{gap / 1e9:,.2f}bn ({share:.0%} of EBITDA) sits outside the operating line (associates, investment "
                  f"income or one-offs); EV multiples on this EBITDA price it", share=share)


def check_dividends(row: dict, shares: Optional[float]) -> dict:
    dps, paid, sh = _f(row.get("dividends_per_share")), _f(row.get("dividends_and_distributions")), _f(shares)
    if not (dps and paid and sh and dps > 0 and sh > 0):
        return _check("dividend per share x shares = dividends paid", "not_run", "inputs missing")
    implied = dps * sh
    r = _rel(implied, abs(paid))
    return _check("dividend per share x shares = dividends paid", "pass" if r <= DIVIDEND_TOL else "fail",
                  f"{implied / 1e9:,.2f}bn vs paid {abs(paid) / 1e9:,.2f}bn ({r:.0%} apart)", gap=r)


def check_freshness(row: dict, run_date: Optional[str]) -> dict:
    end, run = _parse_date(row.get("period") or row.get("report_period") or row.get("date")), _parse_date(run_date)
    if not (end and run):
        return _check("latest statements are current", "not_run", "period or run date missing")
    m = _months_between(end, run)
    return _check("latest statements are current", "pass" if m <= STALE_MONTHS else "fail",
                  f"latest period {end.isoformat()}, {m} months before the run", months=m)


def check_accepted_inputs(inputs_used: list[dict], run_date: Optional[str]) -> list[dict]:
    """Each owner-accepted industry input the run priced: a source URL, and a period no older than 18 months."""
    out = []
    run = _parse_date(run_date)
    for i in inputs_used or []:
        kind, period, url = i.get("kind"), str(i.get("period") or ""), str(i.get("source_url") or "")
        problems = []
        if not url.startswith("http"):
            problems.append("no source URL")
        yr = next((int(period[k:k + 4]) for k in range(len(period) - 3) if period[k:k + 4].isdigit()), None)
        if run and yr and (run.year - yr) * 12 + run.month > INPUT_STALE_MONTHS + 6:
            problems.append(f"period {period} is stale")
        out.append(_check(f"accepted {kind} input is sourced and current", "fail" if problems else "pass",
                          "; ".join(problems) or f"{period}, {url[:80]}"))
    return out


def reconcile(row: dict, *, shares: Optional[float], run_date: Optional[str] = None,
              interest_is_cost: bool = False, inputs_used: Optional[list[dict]] = None) -> dict:
    """{'checks': [...], 'failed': n, 'run': n, 'flags': [...]} for the run's key inputs."""
    checks = [check_bvps(row, shares), check_net_debt(row),
              check_ebitda_bridge(row, interest_is_cost), check_dividends(row, shares),
              check_freshness(row, run_date), *check_accepted_inputs(inputs_used or [], run_date)]
    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    return {"checks": checks, "failed": len(failed), "warned": len(warned),
            "run": sum(1 for c in checks if c["status"] != "not_run"),
            "flags": ([f"Input check failed -- {c['check']}: {c['detail']}" for c in failed]
                      + [f"Input note -- {c['check']}: {c['detail']}" for c in warned])}

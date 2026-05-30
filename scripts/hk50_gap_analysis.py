"""
HK50 gap analysis + multi-source resolver + scoring harness.

Goal (per user): for every metric the two screeners need, identify whether the
PRIMARY source (AKShare for HK, FMP for US/ADR) supplies it, and if not, backfill
from yfinance or compute it. Then score from the *resolved* (multi-source) metrics.
Also proves arbitrary user-added tickers can be scored.

Run:
  .venv\\Scripts\\python.exe scripts/hk50_gap_analysis.py                # full HK50
  .venv\\Scripts\\python.exe scripts/hk50_gap_analysis.py NVDA 2330.TW   # + user tickers
"""
from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass

def _wrap_stdout_utf8() -> None:
    """Wrap stdout in a UTF-8 writer for emoji / CJK / box-drawing output when
    this module is run as a script.

    Idempotent + LAZY: only call this from main() / __main__ — NEVER at import
    time. The compute functions (resolve / score_*) are imported by the FastAPI
    backend (src.research_ideas.hk50), where re-wrapping uvicorn's stdout would
    orphan its logging stream. Idempotency guard: skip if stdout is already
    UTF-8 (PYTHONIOENCODING=utf-8 or a sibling script already wrapped it) —
    wrapping twice closes the shared buffer on GC ("I/O operation on closed
    file").
    """
    if getattr(sys.stdout, "encoding", "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


from src.tools.api import get_financial_metrics, get_analyst_estimates  # noqa: E402
from src.tools.hk.ticker import is_hk_ticker  # noqa: E402

END_DATE = "2026-05-30"

# (ticker, name) — the 4 ADRs are detected as non-HK automatically
HK50 = [
    ("0700.HK", "Tencent"), ("9988.HK", "Alibaba"), ("3690.HK", "Meituan"),
    ("1024.HK", "Kuaishou"), ("1810.HK", "Xiaomi"), ("9618.HK", "JD.com"),
    ("9999.HK", "NetEase"), ("9626.HK", "Bilibili"), ("0992.HK", "Lenovo"),
    ("1211.HK", "BYD"), ("0175.HK", "Geely"), ("2015.HK", "Li Auto"),
    ("9868.HK", "XPeng"), ("6862.HK", "Haidilao"), ("6690.HK", "Haier"),
    ("2020.HK", "Anta"), ("2331.HK", "Li Ning"), ("0288.HK", "WH Group"),
    ("2313.HK", "Shenzhou"), ("CHA", "Chagee(ADR)"), ("EH", "EHang(ADR)"),
    ("YUMC", "Yum China(ADR)"), ("TCOM", "Trip.com(ADR)"), ("1801.HK", "Innovent"),
    ("2269.HK", "WuXi Bio"), ("1093.HK", "CSPC"), ("1177.HK", "Sino Biopharm"),
    ("0005.HK", "HSBC"), ("2888.HK", "StanChart"), ("1398.HK", "ICBC"),
    ("0939.HK", "CCB"), ("3988.HK", "BoC"), ("3968.HK", "CMB"),
    ("0011.HK", "Hang Seng Bank"), ("2388.HK", "BOCHK"), ("2318.HK", "Ping An"),
    ("2628.HK", "China Life"), ("1299.HK", "AIA"), ("0941.HK", "China Mobile"),
    ("0762.HK", "China Unicom"), ("0728.HK", "China Telecom"), ("0883.HK", "CNOOC"),
    ("0857.HK", "PetroChina"), ("0386.HK", "Sinopec"), ("1088.HK", "Shenhua"),
    ("0002.HK", "CLP"), ("0006.HK", "Power Assets"), ("0003.HK", "HK&China Gas"),
    ("1109.HK", "CR Land"), ("0823.HK", "Link REIT"),
]

# Metrics the two scorers consume
GROWTH_METRICS = ["revenue_3y_cagr", "eps_fwd_growth", "fcf_growth", "op_income_growth", "peg"]
DIV_METRICS = ["dividend_yield", "dividend_5y_cagr", "payout_ratio", "fcf_coverage", "yield_vs_5yr_avg"]
ALL_METRICS = GROWTH_METRICS + DIV_METRICS


@dataclass
class Resolved:
    value: float | None
    source: str  # "primary" | "yfinance" | "fmp_growth" | "compute" | "missing"


# ---------------------------------------------------------------------------
# Source adapters (each cached once per ticker)
# ---------------------------------------------------------------------------

def _yf_info(ticker: str) -> dict:
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def _yf_div_5y_cagr(ticker: str) -> float | None:
    """Compute 5y dividend/share CAGR from yfinance dividend history."""
    try:
        import yfinance as yf
        divs = yf.Ticker(ticker).dividends
        if divs is None or len(divs) == 0:
            return None
        annual = divs.groupby(divs.index.year).sum()
        years = sorted(annual.index)
        if len(years) < 4:
            return None
        # use last full year vs ~5 years prior
        end_y = years[-1]
        start_y = end_y - 5
        cand = [y for y in years if y <= start_y]
        if not cand:
            start_y = years[0]
        else:
            start_y = cand[-1]
        v_end, v_start = float(annual[end_y]), float(annual[start_y])
        n = end_y - start_y
        if n <= 0 or v_start <= 0 or v_end <= 0:
            return None
        return (v_end / v_start) ** (1.0 / n) - 1.0
    except Exception:
        return None


_YF_STMT_CACHE: dict[str, tuple] = {}


def _yf_statements(ticker: str):
    """Fetch & cache (income_stmt, cashflow) DataFrames once per ticker."""
    if ticker in _YF_STMT_CACHE:
        return _YF_STMT_CACHE[ticker]
    inc = cf = None
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        inc = tk.income_stmt
        cf = tk.cashflow
    except Exception:
        pass
    _YF_STMT_CACHE[ticker] = (inc, cf)
    return inc, cf


def _cagr_from_row(df, *row_names) -> float | None:
    """3y CAGR (or shortest available >=1y) from a statement row; newest col first."""
    if df is None or getattr(df, "empty", True):
        return None
    row = None
    for rn in row_names:
        if rn in df.index:
            row = df.loc[rn].dropna()
            break
    if row is None or len(row) < 2:
        return None
    vals = [float(v) for v in row.values]  # newest first
    n = min(3, len(vals) - 1)
    v_end, v_start = vals[0], vals[n]
    if n <= 0 or v_start <= 0 or v_end <= 0:
        return None
    return (v_end / v_start) ** (1.0 / n) - 1.0


def _cagr_status(df, *row_names) -> tuple[float | None, str]:
    """Like _cagr_from_row but reports WHY it's None.

    Returns (value, status) with status in:
      * "value"      — a real CAGR
      * "turnaround" — the series exists but the base (or latest) is <= 0, so a
                       growth rate is mathematically undefined (e.g. a loss-making
                       company turning profitable). This is a STRUCTURAL N/A, not
                       a data gap.
      * "nodata"     — the row is absent / too short to compute anything.
    """
    if df is None or getattr(df, "empty", True):
        return None, "nodata"
    row = None
    for rn in row_names:
        if rn in df.index:
            row = df.loc[rn].dropna()
            break
    if row is None or len(row) < 2:
        return None, "nodata"
    vals = [float(v) for v in row.values]  # newest first
    n = min(3, len(vals) - 1)
    if n <= 0:
        return None, "nodata"
    v_end, v_start = vals[0], vals[n]
    if v_start <= 0 or v_end <= 0:
        return None, "turnaround"
    return (v_end / v_start) ** (1.0 / n) - 1.0, "value"


def _yf_rev_3y_cagr(ticker: str) -> float | None:
    inc, _ = _yf_statements(ticker)
    return _cagr_from_row(inc, "Total Revenue", "Operating Revenue")


def _yf_op_income_growth(ticker: str) -> float | None:
    inc, _ = _yf_statements(ticker)
    return _cagr_from_row(inc, "Operating Income", "EBIT", "Total Operating Income As Reported")


def _yf_fcf_growth(ticker: str) -> float | None:
    _, cf = _yf_statements(ticker)
    return _cagr_from_row(cf, "Free Cash Flow")


def _fmp_financial_growth(ticker: str) -> dict:
    """FMP financial-growth (annual, latest) — only meaningful for US/ADR."""
    try:
        import os
        import requests
        key = os.environ.get("FMP_API_KEY", "")
        r = requests.get(
            "https://financialmodelingprep.com/stable/financial-growth",
            params={"symbol": ticker, "period": "annual", "limit": 1, "apikey": key},
            timeout=20,
        )
        if r.status_code == 200 and isinstance(r.json(), list) and r.json():
            return r.json()[0]
    except Exception:
        pass
    return {}


def _fmp_avg_div_yield(ticker: str, years: int = 5) -> float | None:
    """Mean annual dividend yield over the last `years` from FMP ratios (annual).

    Dividend yield is a dimensionless ratio, so this is currency-neutral and safe
    to use even for a USD-traded / CNY-reporting ADR. US/ADR only (FMP-backed).
    Closes `yield_vs_5yr_avg` for OTC-pink ADRs whose yfinance `info` is too thin
    to carry fiveYearAvgDividendYield."""
    try:
        import os
        import requests
        key = os.environ.get("FMP_API_KEY", "")
        r = requests.get(
            "https://financialmodelingprep.com/stable/ratios",
            params={"symbol": ticker, "period": "annual", "limit": years, "apikey": key},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return None
        ys = [row.get("dividendYield") for row in rows]
        ys = [y for y in ys if y and y > 0]
        return sum(ys) / len(ys) if ys else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resolver: try primary -> yfinance -> fmp_growth -> compute, record provenance
# ---------------------------------------------------------------------------

def resolve(ticker: str) -> dict[str, Resolved]:
    hk = is_hk_ticker(ticker)
    out: dict[str, Resolved] = {}

    fm_rows = get_financial_metrics(ticker, END_DATE, "ttm", 1)
    fm = fm_rows[0] if fm_rows else None
    yi = _yf_info(ticker)
    fg = {} if hk else _fmp_financial_growth(ticker)

    def fm_get(attr):
        return getattr(fm, attr, None) if fm else None

    def pct(v):  # yfinance dividendYield is in %, normalize to decimal
        return v / 100.0 if v is not None else None

    # ---- dividend_yield ----
    if fm_get("dividend_yield") is not None:
        out["dividend_yield"] = Resolved(fm_get("dividend_yield"), "primary")
    elif yi.get("trailingAnnualDividendYield"):
        out["dividend_yield"] = Resolved(yi["trailingAnnualDividendYield"], "yfinance")
    elif yi.get("dividendYield"):
        out["dividend_yield"] = Resolved(pct(yi["dividendYield"]), "yfinance")
    else:
        out["dividend_yield"] = Resolved(None, "missing")

    # ---- payout_ratio ----
    if fm_get("payout_ratio") is not None:
        out["payout_ratio"] = Resolved(fm_get("payout_ratio"), "primary")
    elif yi.get("payoutRatio") is not None:
        out["payout_ratio"] = Resolved(yi["payoutRatio"], "yfinance")
    else:
        out["payout_ratio"] = Resolved(None, "missing")

    # ---- yield_vs_5yr_avg ----  (current yield / 5yr avg yield)
    cur_y = out["dividend_yield"].value
    avg5 = yi.get("fiveYearAvgDividendYield")  # in %
    if cur_y is not None and avg5:
        out["yield_vs_5yr_avg"] = Resolved(cur_y / (avg5 / 100.0), "yfinance")
    elif cur_y is not None and not hk:
        # FMP fallback: mean annual dividend yield over last 5y (decimal, currency-
        # neutral). Closes OTC-pink ADRs that yfinance can't carry.
        favg = _fmp_avg_div_yield(ticker)
        if favg and favg > 0:
            out["yield_vs_5yr_avg"] = Resolved(cur_y / favg, "fmp_growth")
        else:
            out["yield_vs_5yr_avg"] = Resolved(None, "missing")
    else:
        out["yield_vs_5yr_avg"] = Resolved(None, "missing")

    # ---- dividend_5y_cagr ----
    if fg.get("fiveYDividendperShareGrowthPerShare") not in (None, 0):
        out["dividend_5y_cagr"] = Resolved(fg["fiveYDividendperShareGrowthPerShare"], "fmp_growth")
    else:
        c = _yf_div_5y_cagr(ticker)
        out["dividend_5y_cagr"] = Resolved(c, "compute" if c is not None else "missing")

    # ---- fcf_coverage ----  (FCF / dividends paid)
    # MAIN: FMP-native fcf_coverage = FCF-yield / dividend-yield. Both are
    # price-relative ratios off the same FMP TTM snapshot, so the quotient is
    # FCF-over-dividends (dividend cover) — dimensionless, currency-safe, and free
    # of the share-count / OTC-ADR distortions in the yfinance compute. This is the
    # primary source for the ADR/FMP cohort (FMP is their primary feed).
    fcfy = fm_get("free_cash_flow_yield")
    dy = out["dividend_yield"].value
    if fcfy is not None and fcfy > 0 and dy and dy > 0:
        out["fcf_coverage"] = Resolved(fcfy / dy, "primary")
    else:
        # FALLBACK: compute FCF / dividends-paid from yfinance (FCF, payout rate,
        # share count). Used mainly on the HK/AKShare path, where FMP doesn't
        # populate free_cash_flow_yield.
        fcf = yi.get("freeCashflow")
        dps_rate = yi.get("trailingAnnualDividendRate")
        shares = yi.get("sharesOutstanding")
        if fcf and dps_rate and shares:
            div_paid = dps_rate * shares
            out["fcf_coverage"] = Resolved(fcf / div_paid if div_paid > 0 else None,
                                           "compute" if div_paid > 0 else "missing")
        else:
            out["fcf_coverage"] = Resolved(None, "missing")

    # ---- revenue_3y_cagr ----
    if fg.get("threeYRevenueGrowthPerShare") is not None:
        out["revenue_3y_cagr"] = Resolved(fg["threeYRevenueGrowthPerShare"], "fmp_growth")
    else:
        c = _yf_rev_3y_cagr(ticker)
        if c is not None:
            out["revenue_3y_cagr"] = Resolved(c, "compute")
        elif fm_get("revenue_growth") is not None:
            out["revenue_3y_cagr"] = Resolved(fm_get("revenue_growth"), "primary")  # YoY proxy
        elif yi.get("revenueGrowth") is not None:
            out["revenue_3y_cagr"] = Resolved(yi["revenueGrowth"], "yfinance")  # YoY proxy
        else:
            out["revenue_3y_cagr"] = Resolved(None, "missing")

    # ---- eps_fwd_growth ----  forward EPS growth proxy
    fwd_eps, trl_eps = yi.get("forwardEps"), yi.get("trailingEps")
    est = [] if hk else get_analyst_estimates(ticker, END_DATE, "annual", 1)
    if fwd_eps and trl_eps and trl_eps != 0:
        out["eps_fwd_growth"] = Resolved(fwd_eps / trl_eps - 1.0, "yfinance")
    elif fg.get("epsgrowth") is not None:
        out["eps_fwd_growth"] = Resolved(fg["epsgrowth"], "fmp_growth")  # trailing EPS growth proxy
    elif est:
        out["eps_fwd_growth"] = Resolved(None, "primary")  # placeholder; would parse est
    elif fm_get("earnings_per_share_growth") is not None:
        out["eps_fwd_growth"] = Resolved(fm_get("earnings_per_share_growth"), "primary")
    elif yi.get("earningsGrowth") is not None:
        out["eps_fwd_growth"] = Resolved(yi["earningsGrowth"], "yfinance")
    else:
        out["eps_fwd_growth"] = Resolved(None, "missing")

    # ---- op_income_growth ----
    if fg.get("operatingIncomeGrowth") is not None:
        out["op_income_growth"] = Resolved(fg["operatingIncomeGrowth"], "fmp_growth")
    elif fm_get("operating_income_growth") is not None:
        out["op_income_growth"] = Resolved(fm_get("operating_income_growth"), "primary")
    else:
        inc, _ = _yf_statements(ticker)
        c, st = _cagr_status(inc, "Operating Income", "EBIT",
                             "Total Operating Income As Reported")
        src = "compute" if c is not None else ("structural" if st == "turnaround" else "missing")
        out["op_income_growth"] = Resolved(c, src)

    # ---- fcf_growth ----
    if fg.get("freeCashFlowGrowth") is not None:
        out["fcf_growth"] = Resolved(fg["freeCashFlowGrowth"], "fmp_growth")
    else:
        _, cf = _yf_statements(ticker)
        c, st = _cagr_status(cf, "Free Cash Flow")
        src = "compute" if c is not None else ("structural" if st == "turnaround" else "missing")
        out["fcf_growth"] = Resolved(c, src)

    # ---- peg ----
    if yi.get("trailingPegRatio") is not None:
        out["peg"] = Resolved(yi["trailingPegRatio"], "yfinance")
    elif fm_get("peg_ratio") is not None:
        out["peg"] = Resolved(fm_get("peg_ratio"), "primary")
    else:
        # compute PE / (eps growth %)
        pe = fm_get("price_to_earnings_ratio") or yi.get("trailingPE")
        g = out["eps_fwd_growth"].value
        if pe and g and g > 0:
            out["peg"] = Resolved(pe / (g * 100.0), "compute")
        else:
            out["peg"] = Resolved(None, "missing")

    return out


# ---------------------------------------------------------------------------
# Scoring from resolved metrics (winsorized, pro-rated)
# ---------------------------------------------------------------------------

def _scale(v, lo, hi):
    if v is None or hi == lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def score_growth(r):
    comps = [
        (30, _scale(r["revenue_3y_cagr"].value, 0.0, 0.30)),
        (25, _scale(r["eps_fwd_growth"].value, 0.0, 0.30)),
        (20, _scale(r["fcf_growth"].value, 0.0, 0.30)),
        (15, _scale(r["op_income_growth"].value, 0.0, 0.30)),
        (10, _peg_inv(r["peg"].value)),
    ]
    return _prorate(comps)


def _peg_inv(peg):
    if peg is None or peg <= 0:
        return None
    return max(0.0, min(1.0, (3.0 - peg) / 2.0))


def score_div(r):
    payout = r["payout_ratio"].value
    payout_n = None
    if payout is not None:
        payout_n = 0.0 if (payout <= 0 or payout >= 1.0) else max(0.0, 1.0 - abs(payout - 0.5) / 0.5)
    cover = r["fcf_coverage"].value
    cover_n = None if cover is None else max(0.0, min(1.0, (cover - 1.0) / 1.0))
    yva = r["yield_vs_5yr_avg"].value
    yva_n = None if yva is None else max(0.0, min(1.0, (yva - 0.8) / 0.7))  # 0.8x->0, 1.5x->1
    comps = [
        (30, _scale(r["dividend_yield"].value, 0.0, 0.08)),
        (25, _scale(r["dividend_5y_cagr"].value, 0.0, 0.15)),
        (20, payout_n),
        (15, cover_n),
        (10, yva_n),
    ]
    return _prorate(comps)


def _prorate(comps):
    aw = sum(w for w, n in comps if n is not None)
    if aw == 0:
        return 0.0, 0.0
    got = sum(w * n for w, n in comps if n is not None)
    tw = sum(w for w, _ in comps)
    return round(100.0 * got / aw, 1), round(100.0 * aw / tw, 0)


# ---------------------------------------------------------------------------

def main():
    _wrap_stdout_utf8()
    extra = [(t, "USER-ADDED") for t in sys.argv[1:]]
    universe = HK50 + extra
    print("=" * 110)
    print(f"HK50 GAP ANALYSIS  |  {len(universe)} tickers  |  end={END_DATE}  |  "
          f"sources: primary(AKShare/FMP) + yfinance + fmp_growth + compute")
    print("=" * 110)

    rows = []
    prov = {m: {"primary": 0, "yfinance": 0, "fmp_growth": 0, "compute": 0,
                "structural": 0, "missing": 0} for m in ALL_METRICS}
    for ticker, name in universe:
        t0 = time.time()
        try:
            r = resolve(ticker)
        except Exception as e:
            print(f"[ERR] {ticker:<9} {name:<16} {type(e).__name__}: {e}")
            continue
        for m in ALL_METRICS:
            prov[m][r[m].source] += 1
        g, gc = score_growth(r)
        d, dc = score_div(r)
        rows.append((ticker, name, g, gc, d, dc))
        miss = [m for m in ALL_METRICS if r[m].source == "missing"]
        print(f"[OK ] {ticker:<9} {name:<16} {time.time()-t0:4.1f}s | "
              f"G={g:>5}({gc:.0f}%) D={d:>5}({dc:.0f}%) | missing: {','.join(miss) if miss else 'none'}")

    print("\n" + "=" * 110)
    print("PROVENANCE MATRIX  (how many of %d tickers got each metric from each source)" % len(rows))
    print("=" * 110)
    print(f"{'metric':<20} {'primary':>8} {'yfinance':>9} {'fmp_growth':>11} {'compute':>8} {'struct':>7} {'MISSING':>8}")
    for m in ALL_METRICS:
        p = prov[m]
        print(f"{m:<20} {p['primary']:>8} {p['yfinance']:>9} {p['fmp_growth']:>11} {p['compute']:>8} {p['structural']:>7} {p['missing']:>8}")

    print("\nTOP 10 HIGH GROWTH:")
    for r in sorted(rows, key=lambda x: x[2], reverse=True)[:10]:
        print(f"  {r[2]:>5}  {r[0]:<9} {r[1]:<16} (cov {r[3]:.0f}%)")
    print("\nTOP 10 HIGH DIVIDEND:")
    for r in sorted(rows, key=lambda x: x[4], reverse=True)[:10]:
        print(f"  {r[4]:>5}  {r[0]:<9} {r[1]:<16} (cov {r[5]:.0f}%)")

    if extra:
        print("\n" + "=" * 110)
        print("USER-ADDED TICKER DETAIL (proves arbitrary tickers resolve + score):")
        print("=" * 110)
        for ticker, _ in extra:
            r = resolve(ticker)
            print(f"\n  {ticker}:")
            for m in ALL_METRICS:
                print(f"    {m:<20} = {str(r[m].value)[:18]:<18} [{r[m].source}]")


if __name__ == "__main__":
    main()

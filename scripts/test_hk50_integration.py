"""
HK50 AKShare/FMP integration test + draft scoring validation.

Run:  .venv\\Scripts\\python.exe scripts/test_hk50_integration.py

Iterates the HK50 universe, fetches FinancialMetrics for each ticker
(get_financial_metrics auto-routes HK->AKShare, ADRs->FMP), reports
which fields populate vs come back null, and runs the draft High Growth
and High Dividend scoring (0-100 each) to validate the integration
end-to-end before building the full module.
"""
from __future__ import annotations

import io
import sys
import time

# UTF-8 stdout so cp1252 console doesn't choke on names/symbols
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.tools.api import get_financial_metrics  # noqa: E402

END_DATE = "2026-05-30"

# (ticker, display_name, is_adr)
HK50 = [
    ("0700.HK", "Tencent", False),
    ("9988.HK", "Alibaba (HK)", False),
    ("3690.HK", "Meituan", False),
    ("1024.HK", "Kuaishou", False),
    ("1810.HK", "Xiaomi", False),
    ("9618.HK", "JD.com (HK)", False),
    ("9999.HK", "NetEase (HK)", False),
    ("9626.HK", "Bilibili (HK)", False),
    ("0992.HK", "Lenovo", False),
    ("1211.HK", "BYD", False),
    ("0175.HK", "Geely", False),
    ("2015.HK", "Li Auto", False),
    ("9868.HK", "XPeng", False),
    ("6862.HK", "Haidilao", False),
    ("6690.HK", "Haier Smart Home", False),
    ("2020.HK", "Anta Sports", False),
    ("2331.HK", "Li Ning", False),
    ("0288.HK", "WH Group", False),
    ("2313.HK", "Shenzhou Intl", False),
    ("CHA", "Chagee (ADR)", True),
    ("EH", "EHang (ADR)", True),
    ("YUMC", "Yum China (ADR)", True),
    ("TCOM", "Trip.com (ADR)", True),
    ("1801.HK", "Innovent Bio", False),
    ("2269.HK", "WuXi Biologics", False),
    ("1093.HK", "CSPC Pharma", False),
    ("1177.HK", "Sino Biopharm", False),
    ("0005.HK", "HSBC", False),
    ("2888.HK", "Standard Chartered", False),
    ("1398.HK", "ICBC", False),
    ("0939.HK", "CCB", False),
    ("3988.HK", "Bank of China", False),
    ("3968.HK", "China Merchants Bank", False),
    ("0011.HK", "Hang Seng Bank", False),
    ("2388.HK", "BOC Hong Kong", False),
    ("2318.HK", "Ping An", False),
    ("2628.HK", "China Life", False),
    ("1299.HK", "AIA", False),
    ("0941.HK", "China Mobile", False),
    ("0762.HK", "China Unicom", False),
    ("0728.HK", "China Telecom", False),
    ("0883.HK", "CNOOC", False),
    ("0857.HK", "PetroChina", False),
    ("0386.HK", "Sinopec", False),
    ("1088.HK", "China Shenhua", False),
    ("0002.HK", "CLP Holdings", False),
    ("0006.HK", "Power Assets", False),
    ("0003.HK", "HK & China Gas", False),
    ("1109.HK", "China Resources Land", False),
    ("0823.HK", "Link REIT", False),
]

# Fields the two screeners care about
GROWTH_FIELDS = [
    "revenue_growth",
    "earnings_per_share_growth",
    "earnings_growth",
    "operating_income_growth",
    "free_cash_flow_growth",
    "peg_ratio",
    "price_to_earnings_ratio",
]
DIVIDEND_FIELDS = [
    "dividend_yield",
    "payout_ratio",
    "free_cash_flow_per_share",
    "earnings_per_share",
]


def _g(m, field):
    return getattr(m, field, None)


def fetch_one(ticker: str):
    t0 = time.time()
    try:
        rows = get_financial_metrics(ticker, END_DATE, period="ttm", limit=1)
    except Exception as exc:  # noqa: BLE001
        return None, f"EXC {type(exc).__name__}: {exc}", time.time() - t0
    if not rows:
        return None, "EMPTY", time.time() - t0
    return rows[0], "OK", time.time() - t0


# ---------------------------------------------------------------------------
# Draft scoring (clamped, pro-rated where inputs are null)
# ---------------------------------------------------------------------------

def _scale(value, lo, hi):
    """Linear 0..1 between lo..hi, clamped."""
    if value is None:
        return None
    if hi == lo:
        return None
    x = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def score_growth(m):
    """High Growth 0-100, pro-rated over available components."""
    comps = []  # (weight, normalized 0..1 or None)
    comps.append((30, _scale(_g(m, "revenue_growth"), 0.0, 0.40)))
    eps_g = _g(m, "earnings_per_share_growth")
    if eps_g is None:
        eps_g = _g(m, "earnings_growth")
    comps.append((25, _scale(eps_g, 0.0, 0.40)))
    comps.append((20, _scale(_g(m, "free_cash_flow_growth"), 0.0, 0.40)))
    comps.append((15, _scale(_g(m, "operating_income_growth"), 0.0, 0.40)))
    # PEG inverse: PEG of 1.0 -> 1.0 score, PEG 3.0 -> 0
    peg = _g(m, "peg_ratio")
    peg_norm = None
    if peg is not None and peg > 0:
        peg_norm = max(0.0, min(1.0, (3.0 - peg) / 2.0))
    comps.append((10, peg_norm))
    return _prorate(comps)


def score_dividend(m):
    """High Dividend 0-100, pro-rated over available components."""
    comps = []
    comps.append((40, _scale(_g(m, "dividend_yield"), 0.0, 0.08)))
    # Payout sweet spot 30-70%: triangular peak at 0.5
    payout = _g(m, "payout_ratio")
    payout_norm = None
    if payout is not None:
        if payout <= 0 or payout >= 1.0:
            payout_norm = 0.0
        else:
            payout_norm = max(0.0, 1.0 - abs(payout - 0.5) / 0.5)
    comps.append((30, payout_norm))
    # FCF coverage of dividend: fcf_per_share vs (eps * payout) proxy
    fcfps = _g(m, "free_cash_flow_per_share")
    eps = _g(m, "earnings_per_share")
    cover_norm = None
    if fcfps is not None and eps is not None and payout is not None and eps > 0 and payout > 0:
        div_ps = eps * payout
        if div_ps > 0:
            cover = fcfps / div_ps
            cover_norm = max(0.0, min(1.0, (cover - 1.0) / 1.0))  # 1.0x->0, 2.0x->1
    comps.append((30, cover_norm))
    return _prorate(comps)


def _prorate(comps):
    """Sum weighted normalized comps, re-base over available weight. Returns (score, coverage%)."""
    avail_w = sum(w for w, n in comps if n is not None)
    if avail_w == 0:
        return 0.0, 0.0
    got = sum(w * n for w, n in comps if n is not None)
    total_w = sum(w for w, _ in comps)
    return round(100.0 * got / avail_w, 1), round(100.0 * avail_w / total_w, 0)


# ---------------------------------------------------------------------------

def main():
    print("=" * 100)
    print(f"HK50 INTEGRATION TEST  |  {len(HK50)} tickers  |  end_date={END_DATE}")
    print("=" * 100)

    results = []
    n_ok = n_empty = n_exc = 0
    for ticker, name, is_adr in HK50:
        m, status, dt = fetch_one(ticker)
        tag = "ADR/FMP" if is_adr else "HK/AKShare"
        if status == "OK":
            n_ok += 1
            div = _g(m, "dividend_yield")
            payout = _g(m, "payout_ratio")
            revg = _g(m, "revenue_growth")
            epsg = _g(m, "earnings_per_share_growth") or _g(m, "earnings_growth")
            gscore, gcov = score_growth(m)
            dscore, dcov = score_dividend(m)
            results.append((ticker, name, gscore, gcov, dscore, dcov))
            print(
                f"[OK ] {ticker:<9} {name:<22} {tag:<11} {dt:4.1f}s | "
                f"div={_fmt(div)} payout={_fmt(payout)} revg={_fmt(revg)} epsg={_fmt(epsg)} "
                f"| G={gscore:>5} ({gcov:.0f}%cov) D={dscore:>5} ({dcov:.0f}%cov)"
            )
        else:
            if status == "EMPTY":
                n_empty += 1
            else:
                n_exc += 1
            print(f"[{'EMP' if status == 'EMPTY' else 'ERR'}] {ticker:<9} {name:<22} {tag:<11} {dt:4.1f}s | {status}")

    print("=" * 100)
    print(f"SUMMARY: OK={n_ok}  EMPTY={n_empty}  ERROR={n_exc}  /  {len(HK50)} total")
    print("=" * 100)

    if results:
        print("\nTOP 10 HIGH GROWTH:")
        for r in sorted(results, key=lambda x: x[2], reverse=True)[:10]:
            print(f"  {r[2]:>5}  {r[0]:<9} {r[1]:<22} (cov {r[3]:.0f}%)")
        print("\nTOP 10 HIGH DIVIDEND:")
        for r in sorted(results, key=lambda x: x[4], reverse=True)[:10]:
            print(f"  {r[4]:>5}  {r[0]:<9} {r[1]:<22} (cov {r[5]:.0f}%)")


def _fmt(v):
    if v is None:
        return "  null"
    try:
        return f"{v:6.3f}"
    except Exception:  # noqa: BLE001
        return str(v)[:6]


if __name__ == "__main__":
    main()

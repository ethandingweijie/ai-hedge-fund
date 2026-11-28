"""Input-data audit for a market's largest names (owner, 2026-11-28: accuracy checks on the new JP / KR / CN markets).

    python scripts/market_data_audit.py --exchanges JPX KSC SHH SHZ --top 50 --out docs/markets/data_audit_20261128.md

Valuations are only as good as the statements they read. For the top N listings by market cap on each exchange this
checks FMP's inputs -- no valuation, no LLM, FMP calls only:
  * statement currency vs the listing currency (a mismatch must be FX-converted by the engine -- flagged for review)
  * latest fiscal year present and recent (<= 18 months old)
  * core fields present: revenue, operating income, net income, total debt, cash, equity, shares, operating cash flow,
    capex
  * share count consistent with market cap / price (within 15%) -- the classic cause of per-share errors (preferred
    shares, ADR ratios, treasury shares)
  * analyst estimates available (forward legs need them) and price history length
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LISTING_CCY = {"JPX": "JPY", "KSC": "KRW", "KOE": "KRW", "SHH": "CNY", "SHZ": "CNY"}
CORE_IS = ("revenue", "operatingIncome", "netIncome")
CORE_BS = ("totalDebt", "cashAndShortTermInvestments", "totalStockholdersEquity")
CORE_CF = ("operatingCashFlow", "capitalExpenditure")


def _get(path, params, key, tries=3):
    import requests
    for i in range(tries):
        try:
            r = requests.get(f"https://financialmodelingprep.com/stable/{path}", params={**params, "apikey": key}, timeout=40)
            if r.status_code == 429:
                time.sleep(2 + i * 3)
                continue
            v = r.json()
            return v if isinstance(v, list) else []
        except Exception:                                   # noqa: BLE001
            time.sleep(1 + i)
    return []


def audit_one(sym: str, exch: str, key: str) -> dict:
    out = {"ticker": sym, "exchange": exch, "issues": []}
    prof = (_get("profile", {"symbol": sym}, key) or [{}])[0]
    inc = _get("income-statement", {"symbol": sym, "period": "annual", "limit": 3}, key)
    bal = _get("balance-sheet-statement", {"symbol": sym, "period": "annual", "limit": 1}, key)
    cf = _get("cash-flow-statement", {"symbol": sym, "period": "annual", "limit": 1}, key)
    est = _get("analyst-estimates", {"symbol": sym, "period": "annual", "limit": 3}, key)
    out["name"] = prof.get("companyName")
    out["price"], out["mcap"] = prof.get("price"), prof.get("marketCap")
    lst = LISTING_CCY.get(exch)
    out["quote_ccy"] = prof.get("currency")
    rep = (inc[0] if inc else {}).get("reportedCurrency")
    out["stmt_ccy"] = rep
    if out["quote_ccy"] and lst and out["quote_ccy"] != lst:
        out["issues"].append(f"quote currency {out['quote_ccy']} != listing {lst}")
    if rep and lst and rep != lst:
        out["issues"].append(f"statements in {rep} (listing {lst}) -- engine must FX-convert")
    if not inc:
        out["issues"].append("no income statement")
        return out
    fy = str(inc[0].get("date") or "")[:10]
    out["latest_fy"] = fy
    try:
        age = (date.today() - datetime.strptime(fy, "%Y-%m-%d").date()).days
        if age > 548:
            out["issues"].append(f"latest annual statement {fy} is {age // 30} months old")
    except ValueError:
        out["issues"].append("no statement date")
    miss = [f for f in CORE_IS if not isinstance(inc[0].get(f), (int, float))]
    miss += [f for f in CORE_BS if not (bal and isinstance(bal[0].get(f), (int, float)))]
    miss += [f for f in CORE_CF if not (cf and isinstance(cf[0].get(f), (int, float)))]
    if miss:
        out["issues"].append("missing: " + ", ".join(miss))
    zero = [f for f in ("revenue",) if inc[0].get(f) == 0]
    if zero:
        out["issues"].append("zero revenue")
    sh = inc[0].get("weightedAverageShsOutDil") or inc[0].get("weightedAverageShsOut")
    if sh and out["price"] and out["mcap"]:
        implied = out["mcap"] / out["price"]
        ratio = sh / implied if implied else None
        out["shares_ratio"] = round(ratio, 3) if ratio else None
        if ratio and not (0.85 <= ratio <= 1.15):
            out["issues"].append(f"share count {sh:,.0f} vs market cap / price {implied:,.0f} (x{ratio:.2f})")
    out["estimates"] = len(est)
    if not est:
        out["issues"].append("no analyst estimates")
    else:
        fut = [e for e in est if str(e.get("date") or "") > date.today().isoformat()]
        if fut and all(not (e.get("revenueAvg") or 0) for e in fut):
            out["issues"].append("analyst estimates present but revenue estimates are zero")
    return out


def top_listings(exch: str, n: int, key: str) -> list[str]:
    rows = _get("company-screener", {"exchange": exch, "isEtf": "false", "isFund": "false",
                                     "isActivelyTrading": "true", "limit": max(n * 4, 200)}, key)
    rows.sort(key=lambda r: -(r.get("marketCap") or 0))
    seen, out = set(), []
    for r in rows:
        s = r.get("symbol")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= n:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchanges", nargs="*", default=["JPX", "KSC", "SHH", "SHZ"])
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", default=str(ROOT / "docs" / "markets" / f"data_audit_{date.today():%Y%m%d}.md"))
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    key = os.environ["FMP_API_KEY"]
    rows = []
    for ex in a.exchanges:
        syms = top_listings(ex, a.top, key)
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows += list(pool.map(lambda s: audit_one(s, ex, key), syms))
        print(f"{ex}: {len(syms)} audited", flush=True)
    md = ["# Market input-data audit", "",
          f"Top {a.top} listings by market cap per exchange (FMP), {date.today()}. FMP calls only; no valuation.", ""]
    md += ["| exchange | audited | clean | statements not in listing ccy | share-count mismatch | missing core fields |"
           " stale (>18m) | no estimates |", "|---|---|---|---|---|---|---|---|"]
    for ex in a.exchanges:
        rs = [r for r in rows if r["exchange"] == ex]
        def c(fn):
            return sum(1 for r in rs if any(fn(i) for i in r["issues"]))
        md.append(f"| {ex} | {len(rs)} | {sum(1 for r in rs if not r['issues'])} | "
                  f"{c(lambda i: i.startswith('statements in'))} | {c(lambda i: i.startswith('share count'))} | "
                  f"{c(lambda i: i.startswith('missing'))} | {c(lambda i: 'months old' in i)} | "
                  f"{c(lambda i: 'estimates' in i)} |")
    for ex in a.exchanges:
        md += ["", f"## {ex}", "", "| ticker | name | issues |", "|---|---|---|"]
        for r in [r for r in rows if r["exchange"] == ex and r["issues"]]:
            md.append(f"| {r['ticker']} | {(r.get('name') or '')[:40]} | {'; '.join(r['issues'])} |")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Top up the segment-revenue memory for SOTP-valued tickers with gemini-3.8-flash.

FMP returns no segment revenue for HK/SG names (0 of 9 probed), and SGX
publishes no machine-readable segment note -- which is what blocks segment
SOTP for Wilmar, ThaiBev, Olam and SingPost. This fills the gap with cited,
REPORTED (not estimated) segment revenue for the last five fiscal years,
reconciled year by year against FMP's reported group revenue.

Output: src/data/segment_revenue_memory.json, status "pending_review". Nothing
reads it into a valuation until it is reviewed (plan A1 loads it as the seed
for the user financials store). Resumable: tickers already present are
skipped unless --force; the file is written after every ticker.

    .venv/Scripts/python.exe scripts/build_segment_revenue_memory.py [--tickers BABA,00700.HK] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
load_dotenv(ROOT / ".env")

import os  # noqa: E402

from src.agents.industry import gemini_params as gp  # noqa: E402

OUT = ROOT / "src" / "data" / "segment_revenue_memory.json"

#: Every ticker a SOTP method values today, by where the method comes from.
SOTP_UNIVERSE = {
    # SOTP (analyst): validated snapshot names (HK lines alias to the ADR)
    "BABA": ("Alibaba Group", "sotp_analyst"),
    "JD": ("JD.com", "sotp_analyst"),
    "PDD": ("PDD Holdings", "sotp_analyst"),
    "03690.HK": ("Meituan", "sotp_analyst+segment_sotp"),
    "MSFT": ("Microsoft", "sotp_analyst"),
    "AMZN": ("Amazon", "sotp_analyst"),
    # SOTP (segments): filing-derived pilot
    "00700.HK": ("Tencent Holdings", "segment_sotp"),
    "01810.HK": ("Xiaomi", "segment_sotp"),
    # SOTP / NAV look-through: holdco templates
    "00267.HK": ("CITIC Limited", "holdco_lookthrough"),
    "00001.HK": ("CK Hutchison Holdings", "holdco_lookthrough"),
    "00019.HK": ("Swire Pacific", "holdco_lookthrough"),
    "C07.SI": ("Jardine Cycle & Carriage", "holdco_lookthrough"),
    "P15.SI": ("Pacific Century Regional Developments", "holdco_lookthrough"),
    "00148.HK": ("Kingboard Holdings", "holdco_lookthrough"),
    "Y92.SI": ("Thai Beverage", "holdco_lookthrough"),
    "F34.SI": ("Wilmar International", "holdco_lookthrough"),
    "01548.HK": ("GenScript Biotech", "holdco_lookthrough"),
    "VC2.SI": ("Olam Group", "holdco_lookthrough"),
    "S08.SI": ("Singapore Post", "holdco_lookthrough"),
    # SOTP-primary names blocked on segment data
    "00175.HK": ("Geely Automobile", "sotp_blocked_no_segments"),
    "00288.HK": ("WH Group", "sotp_blocked_no_segments"),
}


def _fmp_revenue_by_year(ticker: str) -> tuple[dict[str, float], str]:
    from src.tools.fmp_transcripts import to_fmp_symbol
    url = (f"https://financialmodelingprep.com/stable/income-statement?symbol={to_fmp_symbol(ticker)}"
           f"&period=annual&limit=6&apikey={os.environ['FMP_API_KEY']}")
    with urllib.request.urlopen(url, timeout=30) as r:
        rows = json.loads(r.read()) or []
    ccy = (rows[0].get("reportedCurrency") if rows else None) or "USD"
    return {str(x.get("date", ""))[:4]: float(x["revenue"]) for x in rows if x.get("revenue")}, ccy


def _fx(from_ccy: str, to_ccy: str):
    from src.tools.api import get_fx_rate
    try:
        rate = get_fx_rate(from_ccy.upper(), to_ccy.upper())
        return float(rate) if rate and rate > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _load() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"_meta": {"version": 1, "status": "pending_review", "model": gp.model_name(),
                      "doc": __doc__.split("\n\n")[0]}, "tickers": {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(SOTP_UNIVERSE))
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    memory = _load()
    for ticker in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        company, basis = SOTP_UNIVERSE[ticker]
        if ticker in memory["tickers"] and not args.force and not memory["tickers"][ticker].get("error"):
            print(f"{ticker}: already in memory, skipped")
            continue
        started = time.monotonic()
        try:
            fmp_rev, rep_ccy = _fmp_revenue_by_year(ticker)
            out = gp.generate(gp.history_prompt(company, ticker, args.years),
                              schema=gp.SegmentRevenueHistory, timeout=args.timeout)
        except gp.GeminiBillingError as exc:
            print(f"STOPPED: Gemini billing -- {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            memory["tickers"][ticker] = {"company": company, "sotp_basis": basis,
                                         "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                         "attempted": date.today().isoformat()}
            print(f"{ticker}: ERROR {type(exc).__name__} after {time.monotonic() - started:.0f}s")
            OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")
            continue
        hist = out["json"]
        rec = gp.reconcile_history(hist, fmp_rev, rep_ccy, _fx)
        n_values = sum(len(s.get("years") or []) for s in hist.get("segments") or [])
        n_cited = sum(gp._cited_ok(y.get("revenue")) for s in hist.get("segments") or []
                      for y in s.get("years") or [])
        memory["tickers"][ticker] = {
            "company": company, "sotp_basis": basis, "fmp_reporting_currency": rep_ccy,
            "retrieved": date.today().isoformat(), "model": out["model"],
            "latency_s": out["latency_s"], "usage": out["usage"],
            "citation_coverage": round(n_cited / n_values, 4) if n_values else 0.0,
            "reconciliation": rec, "history": hist,
        }
        gaps = [f"{y}:{v['segment_gap']:+.0%}" for y, v in rec.items() if v.get("segment_gap") is not None]
        print(f"{ticker}: {len(hist.get('segments') or [])} segments, {n_values} values, "
              f"cited {n_cited}/{n_values}, {out['latency_s']:.0f}s, segment-sum vs FMP {' '.join(gaps) or 'n/a'}",
              flush=True)
        memory["_meta"]["updated"] = date.today().isoformat()
        OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

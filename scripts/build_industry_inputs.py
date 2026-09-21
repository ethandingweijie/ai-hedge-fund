"""Pre-fill review-gated industry inputs with a grounded Gemini call.

Energy and A&D profiles need figures FMP does not report (see
src/data/industry_inputs.py): PV-10 / standardized measure for upstream oil &
gas, contracted backlog for oilfield services and drillers, maintenance capex
for midstream. Each figure is taken exactly as the filing prints it, with its
URL and a verbatim quote, then checked in USD against what FMP reports for the
same company (market cap, revenue, D&A, operating cash flow). Results are
stored "pending"; nothing reaches a valuation until the owner accepts them on
the Model Accuracy page.

Resumable: an existing ticker/kind is skipped unless --force.

    .venv/Scripts/python.exe scripts/build_industry_inputs.py --kind pv10 --tickers COP,EOG
    .venv/Scripts/python.exe scripts/build_industry_inputs.py --wave 1
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
load_dotenv(ROOT / ".env")

from src.agents.industry import gemini_params as gp  # noqa: E402
from src.data import industry_inputs as ii  # noqa: E402

#: Wave 1 (oil, gas & coal): which input each FMP-covered name needs.
WAVE1 = {
    "pv10": ["COP", "EOG", "DVN", "OXY", "00883.HK", "5WH.SI"],
    "maintenance_capex": ["KMI", "WMB", "ET", "OKE"],
    "backlog": ["SLB", "HAL", "BKR", "RIG", "02883.HK", "03337.HK"],
}

#: Wave 2 (power & transition). Rate base for the regulated names, including the
#: two the owner pinned to Regulated Utility against their FMP label (00006.HK,
#: 01816.HK); contracted backlog for the fuel-cycle names and the turbine OEM.
WAVE2 = {
    "rate_base": ["NEE", "DUK", "SO", "00002.HK", "00006.HK", "01816.HK"],
    "backlog": ["CCJ", "LEU", "GEV", "BE"],
    # Owner, 2026-09-22: the FCF guidance overlay, faded (valuation_constants
    # `fcf_guidance_fade`). GE Vernova is the first name that needs it.
    "fcf_guidance": ["GEV"],
}
WAVES = {"1": WAVE1, "2": WAVE2}


def _fmp(path: str, params: dict):
    from src.tools.api import _fmp_get
    return _fmp_get(f"https://financialmodelingprep.com/stable/{path}", params,
                    os.environ.get("FMP_API_KEY")) or []


def fmp_context(ticker: str) -> dict:
    """FMP figures for the checks, all in USD, plus the company name."""
    from src.tools.fmp_transcripts import to_fmp_symbol
    sym = to_fmp_symbol(ticker)
    usd = ii._fx("USD")
    prof = (_fmp("profile", {"symbol": sym}) or [{}])[0]
    inc = (_fmp("income-statement", {"symbol": sym, "limit": 1}) or [{}])[0]
    cf = (_fmp("cash-flow-statement", {"symbol": sym, "limit": 1}) or [{}])[0]
    bs = (_fmp("balance-sheet-statement", {"symbol": sym, "limit": 1}) or [{}])[0]
    rep = (inc.get("reportedCurrency") or cf.get("reportedCurrency") or "USD")
    r_rep, r_list = usd(rep), usd(prof.get("currency") or "USD")

    def conv(v, r):
        return float(v) * r if isinstance(v, (int, float)) and r else None
    return {
        "company": prof.get("companyName") or ticker,
        "period": inc.get("date"),
        "market_cap": conv(prof.get("marketCap"), r_list),
        "revenue": conv(inc.get("revenue"), r_rep),
        "depreciation_and_amortization": conv(cf.get("depreciationAndAmortization")
                                              or inc.get("depreciationAndAmortization"), r_rep),
        "operating_cash_flow": conv(cf.get("operatingCashFlow"), r_rep),
        "net_ppe": conv(bs.get("propertyPlantEquipmentNet"), r_rep),
    }


def build_one(ticker: str, kind: str) -> dict:
    ctx = fmp_context(ticker)
    schema = gp.INDUSTRY_INPUT_SCHEMAS[kind]
    t0 = time.time()
    # Grounded reserve/backlog calls read long filings; 90s (the client default)
    # timed out repeatedly on EOG, Rex and Williams. Two attempts, longer each.
    for attempt, timeout in enumerate((180.0, 300.0)):
        try:
            out = gp.generate(gp.industry_input_prompt(kind, ctx["company"], ticker),
                              schema=schema, grounded=True, timeout=timeout)
            break
        except Exception:                                  # noqa: BLE001
            if attempt:
                raise
    data = out.get("json")
    if not isinstance(data, dict):
        raise gp.GeminiParseError(f"{ticker}/{kind}: no structured answer")
    value_usd = gp.amount((data or {}).get("value"), ii._fx("USD"))
    period = (data.get("value") or {}).get("period")
    checks = ii.reconcile(kind, value_usd, ctx, period=period, data=data)
    return {
        # Audited actual unless a check says the period is not the latest
        # reported one -- a guidance figure must arrive as an overlay, not as
        # the baseline (owner, 2026-09-20).
        "basis": ("actual" if all(c["ok"] is not False for c in checks
                                  if c["check"] == "latest reported period") else "not_actual"),
        "data": data, "company": ctx["company"], "fmp_context_usd": ctx,
        "value_usd": value_usd, "checks": checks,
        "ok": all(c["ok"] is not False for c in checks),
        "grounding_urls": out.get("grounding_urls") or [],
        "model": out.get("model") or gp.model_name(), "secs": round(time.time() - t0, 1),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_overlay(ticker: str, kind: str, base_entry: dict) -> dict:
    """Management guidance for the next year, stored as a delta on the actual."""
    v = (base_entry.get("data") or {}).get("value") or {}
    baseline_txt = f"{v.get('value')} {v.get('currency')} {v.get('scale')} for {v.get('period')}"
    out = gp.generate(gp.industry_overlay_prompt(kind, base_entry.get("company") or ticker,
                                                 ticker, baseline_txt),
                      schema=gp.INDUSTRY_INPUT_SCHEMAS[kind], grounded=True)
    data = out.get("json") or {}
    g = data.get("value") or {}
    usd = ii._fx("USD")
    g_usd, base_usd = gp.amount(g, usd), base_entry.get("value_usd")
    if not g_usd or not base_usd:
        raise ValueError("no cited guidance amount")
    return {"delta_pct": round(g_usd / base_usd - 1.0, 6),
            "guidance_value": g.get("value"), "currency": g.get("currency"), "scale": g.get("scale"),
            "period": g.get("period"), "source_url": g.get("source_url"), "quote": g.get("quote"),
            "note": f"guidance {g.get('period')} vs actual {v.get('period')}",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=ii.KINDS)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--wave", choices=sorted(WAVES))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--overlay", action="store_true",
                    help="capture management guidance as a delta on the stored actual")
    a = ap.parse_args(argv)
    jobs = ([(k, t) for k, ts in WAVES[a.wave].items() for t in ts] if a.wave
            else [(a.kind, t.strip()) for t in a.tickers.split(",") if t.strip()])
    if not jobs or any(k is None for k, _ in jobs):
        ap.error("give --wave N, or --kind with --tickers")
    doc = ii.load() or {"version": 1, "tickers": {}}
    for kind, t in jobs:
        key = ii._key(t)
        if not a.force and not a.overlay and ((doc["tickers"].get(key) or {}).get(kind)):
            print(f"  {t:<10} {kind:<18} skip (present)")
            continue
        if a.overlay:
            base = (doc["tickers"].get(key) or {}).get(kind)
            if base and base.get("overlay") and not a.force:
                print(f"  {t:<10} {kind:<18} overlay present (use --force to rebuild)")
                continue
            if not base:
                print(f"  {t:<10} {kind:<18} no actual to overlay")
                continue
            if kind in ii.NO_OVERLAY:
                print(f"  {t:<10} {kind:<18} overlay refused (measure is defined by trailing prices)")
                continue
            try:
                base["overlay"] = build_overlay(t, kind, base)
            except Exception as exc:  # noqa: BLE001
                print(f"  {t:<10} {kind:<18} overlay FAILED {type(exc).__name__}: {str(exc)[:100]}")
                continue
            ii.save(doc)
            o = base["overlay"]
            print(f"  {t:<10} {kind:<18} overlay {o['delta_pct']:+.1%} ({o['note']})", flush=True)
            continue
        try:
            e = build_one(t, kind)
        except Exception as exc:  # noqa: BLE001
            print(f"  {t:<10} {kind:<18} FAILED {type(exc).__name__}: {str(exc)[:120]}")
            continue
        doc["tickers"].setdefault(key, {})[kind] = e
        ii.save(doc)
        v = e["data"].get("value") or {}
        print(f"  {t:<10} {kind:<18} {v.get('value')} {v.get('currency')} {v.get('scale')} "
              f"({v.get('period')}) -> ${(e['value_usd'] or 0) / 1e9:,.2f}bn "
              f"{'OK' if e['ok'] else 'CHECK FAILED'} "
              + "; ".join(c["detail"] for c in e["checks"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

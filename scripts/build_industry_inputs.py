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

#: Wave 3 (owner framework, 2026-09-22). Backlog for the names whose order
#: book bounds the DCF; the Gemini SOTP inputs -- business segments with a
#: cited multiple range each -- for the SOTP-valued profiles.
WAVE3 = {
    "backlog": ["LMT", "NOC", "GD", "RTX", "LHX", "BA", "GE", "HWM", "TDG", "HEI",
                "KTOS", "AVAV", "RKLB", "02507.HK", "S63.SI"],
    "sotp": ["BA", "02357.HK", "S63.SI"],
}
WAVES = {"1": WAVE1, "2": WAVE2, "3": WAVE3}


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


def _sotp_anchors(ticker: str, ctx: dict) -> dict:
    """The FMP anchors sotp_prompt fixes: next-year consensus revenue, the
    reporting currency, latest revenue. Gemini must not contradict them."""
    from src.tools.fmp_transcripts import to_fmp_symbol
    from datetime import date
    sym = to_fmp_symbol(ticker)
    today = date.today().isoformat()
    est = [e for e in (_fmp("analyst-estimates", {"symbol": sym, "period": "annual", "limit": 10}) or [])
           if str(e.get("date", "")) > today]
    fwd = min(est, key=lambda e: e["date"]) if est else {}
    inc = (_fmp("income-statement", {"symbol": sym, "limit": 1}) or [{}])[0]
    return {"reported_currency": inc.get("reportedCurrency") or "USD",
            "revenue_latest_usd_bn": round((ctx.get("revenue") or 0) / 1e9, 2),
            "revenue_next_fy_usd_bn": (round(float(fwd["revenueAvg"]) * (ctx.get("revenue") or 0)
                                             / float(inc.get("revenue") or 1) / 1e9, 2)
                                       if fwd.get("revenueAvg") and inc.get("revenue") else None),
            "revenue_next_fy_period": fwd.get("date")}


def _url_check(mapping: dict) -> dict:
    """Every grounding wrapper resolved to a canonical source, or which did not."""
    bad = [w for w, c in mapping.items() if not c]
    return {"check": "sources resolve to canonical URLs", "ok": not bad,
            "detail": (f"{len(mapping) - len(bad)} of {len(mapping)} grounding wrapper(s) resolved"
                       + (f"; unresolved: {len(bad)}" if bad else "")) if mapping else "no grounding wrappers"}


def resolve_store_urls(doc: dict) -> dict:
    """Owner, 2026-09-24: existing entries keep their reviewed `data` (the
    content hash the owner accepted must not move), and gain `canonical_urls`
    {wrapper: terminal url} beside it. The gate and the engine read the map."""
    n = 0
    for key, kinds in (doc.get("tickers") or {}).items():
        for kind, e in (kinds or {}).items():
            if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
                continue
            _, mapping = gp.canonicalize_citations(e["data"])
            if mapping:
                e["canonical_urls"] = {**(e.get("canonical_urls") or {}),
                                       **{w: c for w, c in mapping.items() if c}}
                unresolved = [w for w, c in mapping.items() if not c]
                print(f"  {key:<10} {kind:<18} {len(mapping) - len(unresolved)} of {len(mapping)} resolved"
                      + (f"; unresolved {len(unresolved)}" if unresolved else ""))
                n += 1
    print(f"resolved sources on {n} entries")
    return doc


def build_one(ticker: str, kind: str) -> dict:
    ctx = fmp_context(ticker)
    schema = gp.INDUSTRY_INPUT_SCHEMAS[kind]
    t0 = time.time()
    if kind == "sotp":
        # Owner, 2026-09-22: the SOTP-valued profiles take their segments and
        # the multiple range to adopt from Gemini, cited, then review-gated.
        anchors = _sotp_anchors(ticker, ctx)
        out = gp.generate(gp.sotp_prompt(ctx["company"], ticker, anchors), schema=schema,
                          grounded=True, timeout=300.0)
        data = out.get("json")
        if not isinstance(data, dict):
            raise gp.GeminiParseError(f"{ticker}/sotp: no structured answer")
        data, _urls = gp.canonicalize_citations(data)      # owner, 2026-09-24: no wrappers in the store
        conv, conv_checks = gp.to_engine_assumptions(data, fmp_revenue_fwd_usd=None)
        seg_sum = sum(s["revenue_fwd"] for s in conv.get("segments") or [])
        checks = ii.reconcile("sotp", seg_sum or None, ctx, period=data.get("fiscal_year"))
        # Owner, 2026-09-24 (item 4): segments may not sum to more than the
        # group. The comparison base is the FMP forward consensus revenue for
        # the same year when the anchors carry it, else the latest reported
        # revenue; both in USD like seg_sum. One-sided on purpose: segments
        # summing BELOW the group is eliminations and unallocated items.
        from src.data.valuation_constants import sotp_input_thresholds
        _tol = sotp_input_thresholds()["segment_sum_excess_tolerance"]
        _fwd_bn = anchors.get("revenue_next_fy_usd_bn")
        _group = (float(_fwd_bn) * 1e9 if isinstance(_fwd_bn, (int, float)) and _fwd_bn > 0
                  else (ctx.get("revenue") or None))
        _group_label = "FMP FY+1 consensus revenue" if isinstance(_fwd_bn, (int, float)) and _fwd_bn > 0 else "FMP latest revenue"
        if seg_sum and isinstance(_group, (int, float)) and _group > 0:
            _excess = seg_sum / _group - 1.0
            checks.append({"check": "segment revenue vs group revenue", "ok": _excess <= _tol,
                           "detail": (f"segments sum to {seg_sum / 1e9:.1f}bn USD vs {_group_label} "
                                      f"{_group / 1e9:.1f}bn ({_excess:+.1%}; may not exceed by more than {_tol:.0%})"),
                           "ratio": round(seg_sum / _group, 4)})
        else:
            checks.append({"check": "segment revenue vs group revenue", "ok": None,
                           "detail": "no group revenue to check against"})
        # Owner, 2026-09-24 (item 3): every segment figure must be stated for a
        # year AFTER the latest reported one; an actual standing in fails.
        _latest = ii._year(ctx.get("period"))
        _periods = sorted({str((s.get("revenue_fwd") or {}).get("period")) for s in data.get("segments") or []})
        _bad = [p for p in _periods if _latest and (ii._year(p) or 0) <= _latest]
        checks.append({"check": "segment revenue periods", "ok": (not _bad) if _latest else None,
                       "detail": (f"{', '.join(_periods)} (fiscal_year {data.get('fiscal_year')}); "
                                  + (f"latest reported year is {_latest}; not forward: {', '.join(_bad)}"
                                     if _bad else "all forward of the latest reported year"))})
        checks.append({"check": "segments with a cited multiple range", "ok": len(conv.get("segments") or []) >= 2,
                       "detail": (f"{len(conv.get('segments') or [])} usable; dropped "
                                  f"{conv_checks.get('dropped_segments') or 'none'}")})
        checks.append(_url_check(_urls))
        return {"basis": "estimate", "data": data, "company": ctx["company"], "fmp_context_usd": ctx,
                "anchors": anchors, "value_usd": seg_sum or None, "checks": checks,
                "engine_preview": {"segments": conv.get("segments"), "checks": conv_checks},
                "ok": all(c["ok"] is not False for c in checks),
                "grounding_urls": out.get("grounding_urls") or [],
                "model": out.get("model") or gp.model_name(), "secs": round(time.time() - t0, 1),
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
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
    data, _urls = gp.canonicalize_citations(data)          # owner, 2026-09-24: no wrappers in the store
    value_usd = gp.amount((data or {}).get("value"), ii._fx("USD"))
    period = (data.get("value") or {}).get("period")
    checks = ii.reconcile(kind, value_usd, ctx, period=period, data=data) + [_url_check(_urls)]
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
    ap.add_argument("--resolve-urls", action="store_true",
                    help="resolve grounding redirect wrappers on EXISTING entries into canonical_urls "
                         "(reviewed data untouched, acceptances survive)")
    a = ap.parse_args(argv)
    if a.resolve_urls:
        ii.save(resolve_store_urls(ii.load() or {"version": 1, "tickers": {}}))
        return 0
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

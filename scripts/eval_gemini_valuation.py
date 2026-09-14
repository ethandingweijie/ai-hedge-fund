"""Part E -- can gemini-3.8-flash supply SOTP and valuation estimates?

Owner-run and offline; never part of a pipeline run.

Arms per ticker (each repeated --repeats times):
  G1  Gemini grounded SOTP INPUTS -> guardrails -> deterministic engine
  G2  Gemini's own SOTP / fair-value estimate (evaluation only)
Baselines:
  S   validated snapshot inputs through the same engine
  Q   latest non-snapshot (live Qwen-extracted) SOTP in web_runs, when one exists
Reference: GS target (snapshot meta) and the sotp_ground_truth ranges.

    .venv/Scripts/python.exe scripts/eval_gemini_valuation.py --repeats 3 \
        --out <scratchpad>/gemini_eval_2026-09-14.json [--record]

--record writes each raw Gemini response to tests/fixtures/gemini/ so the
offline tests can replay real gemini-3.8-flash output.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
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
import re  # noqa: E402

# --prod-db: read the Qwen baseline from production web_runs (read-only). Must
# be set before src.data.db is imported, which the engine imports pull in.
# Live SOTP extraction exists in prod only for BABA (two 25 Aug runs); every
# other name is compared with the snapshot and the GS target instead.
if "--prod-db" in sys.argv:
    _raw = (Path.home() / ".railway_pg_url").read_text(encoding="utf-8").strip()
    os.environ["DATABASE_URL"] = re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/", _raw)

from src.agents.analysis.dcf_agent import _sotp_analyst_style  # noqa: E402
from src.agents.analysis.sotp_ground_truth import check_table  # noqa: E402
from src.agents.analysis.sotp_snapshot import load_sotp_snapshot, lookup_snapshot  # noqa: E402
from src.agents.industry import gemini_params as gp  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "gemini"
THINKING: dict | None = None        # set from --thinking-level
USDHKD = 7.8

# ticker -> company, listing currency, per-unit label, GS TP in listing currency
UNIVERSE = {
    "BABA": ("Alibaba Group", "USD", "ADS", 186.0),
    "09988.HK": ("Alibaba Group", "HKD", "share", 186.0 * USDHKD / 8),
    "JD": ("JD.com", "USD", "ADS", 43.0),
    "09618.HK": ("JD.com", "HKD", "share", 43.0 * USDHKD / 2),
    "PDD": ("PDD Holdings", "USD", "ADS", 145.0),
    "03690.HK": ("Meituan", "HKD", "share", 123.0),
    "MSFT": ("Microsoft", "USD", "share", 640.0),
    "AMZN": ("Amazon", "USD", "share", 375.0),
    "00700.HK": ("Tencent Holdings", "HKD", "share", None),
    "00005.HK": ("HSBC Holdings", "HKD", "share", None),
    "D05.SI": ("DBS Group", "SGD", "share", None),
    "C38U.SI": ("CapitaLand Integrated Commercial Trust", "SGD", "unit", None),
    "S68.SI": ("Singapore Exchange", "SGD", "share", None),
}
SOTP_NAMES = {"BABA", "09988.HK", "JD", "09618.HK", "PDD", "03690.HK", "MSFT", "AMZN"}

#: Further broker targets in listing currency, reported next to the GS error.
#: JPM BABA $210 / 09988.HK HK$205 (user-supplied 2026-09-15; consistent at
#: 7.8 HKD/USD and 8 shares per ADS).
BROKER_TPS = {"BABA": {"JPM": 210.0}, "09988.HK": {"JPM": 205.0}}


def _fmp(endpoint: str, **params):
    from src.tools.fmp_transcripts import to_fmp_symbol
    if "symbol" in params:
        params["symbol"] = to_fmp_symbol(params["symbol"])
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://financialmodelingprep.com/stable/{endpoint}?{q}&apikey={os.environ['FMP_API_KEY']}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def anchors(ticker: str) -> dict:
    """FMP facts the engine needs: shares (market cap / price, so ADRs come out
    per ADS), forward revenue in USD, and USD->listing FX."""
    from src.tools.api import get_fx_rate
    quote = (_fmp("quote", symbol=ticker) or [{}])[0]
    price, mcap = quote.get("price"), quote.get("marketCap")
    inc = (_fmp("income-statement", symbol=ticker, period="annual", limit=1) or [{}])[0]
    ccy = inc.get("reportedCurrency") or "USD"
    # next fiscal year that has not ended yet (BABA's FY ends March: in
    # September the 2026-03-31 row is history, not a forward estimate)
    today = date.today().isoformat()
    est = [e for e in _fmp("analyst-estimates", symbol=ticker, period="annual", limit=10) or []
           if str(e.get("date", "")) > today]
    fwd = min(est, key=lambda e: e["date"]) if est else {}
    to_usd = 1.0 if ccy == "USD" else get_fx_rate(ccy, "USD")
    listing = UNIVERSE[ticker][1]
    return {
        "price": price, "shares": (mcap / price) if price and mcap else None,
        "reported_currency": ccy,
        "revenue_fwd_usd": (fwd.get("revenueAvg") or 0) * to_usd or None,
        "revenue_fwd_period": fwd.get("date"),
        "analysts": fwd.get("numAnalystsRevenue"),
        "fx_usd_to_listing": 1.0 if listing == "USD" else get_fx_rate("USD", listing),
    }


def _log_err(value, reference):
    if not value or not reference or value <= 0 or reference <= 0:
        return None
    return round(abs(math.log(value / reference)), 4)


def _signed_pct(value, reference):
    if not value or not reference:
        return None
    return round(value / reference - 1, 4)


def _record(name: str, out: dict) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    keep = {k: out.get(k) for k in ("text", "grounding_urls", "queries", "usage", "mode", "model",
                                    "latency_s", "finish_reason", "n_candidates", "block_reason")}
    keep["thinking"] = THINKING
    (FIXTURES / f"{name}_{(THINKING or {}).get('thinkingLevel', 'default')}.json").write_text(
        json.dumps(keep, indent=1), encoding="utf-8")


def engine_value(ticker: str, assumptions: dict, a: dict) -> dict:
    table = _sotp_analyst_style(assumptions, shares=a["shares"], fx_to_reporting=a["fx_usd_to_listing"])
    if not table:
        return {"value": None}
    return {"value": round(table["per_share_reporting"], 2), "ground_truth": check_table(ticker, table)}


def _fingerprint(names_multiples) -> frozenset:
    return frozenset((str(n).strip().lower(), round(float(m or 0), 2)) for n, m in names_multiples)


def qwen_baseline(ticker: str, a: dict, snap: dict | None) -> dict | None:
    """Latest run whose SOTP inputs were NOT the snapshot, i.e. live extraction.

    Older runs carry no origin tag, so a breakdown counts as snapshot-derived
    when its segment names and applied multiples match a snapshot entry --
    v1 or the current file."""
    try:
        from src.data import db
        rows = db.query(
            "SELECT run_at, full_result_json FROM web_runs WHERE UPPER(ticker) = UPPER(?) "
            "AND full_result_json IS NOT NULL ORDER BY run_at DESC LIMIT 40", [ticker])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    snap_fp = None
    if snap:
        snap_fp = _fingerprint((s.get("name"), s.get("pe_multiple") or s.get("ev_rev_multiple"))
                               for s in snap.get("segments") or [])
    snap_names = {n for n, _ in (snap_fp or [])}
    for row in rows or []:
        payload = json.loads(row["full_result_json"] or "{}")
        bd = (((payload.get("data") or {}).get("dcf_range") or {}).get(ticker) or {}).get("sotp_breakdown")
        if not bd or not bd.get("rows"):
            continue
        fp = _fingerprint((r.get("name"), r.get("multiple")) for r in bd["rows"])
        if fp == snap_fp or {n for n, _ in fp} == snap_names:
            continue                                  # snapshot-derived (any version)
        return {"run_at": str(row["run_at"]), "value": bd.get("per_share_reporting"),
                "segments": [(r.get("name"), r.get("method"), r.get("multiple")) for r in bd["rows"]]}
    return None


def run(repeats: int, record: bool, tickers: list[str], timeout: float = gp.TIMEOUT_S) -> dict:
    results = {}
    snapshot = load_sotp_snapshot()
    for ticker in tickers:
        company, listing, per, gs_tp = UNIVERSE[ticker]
        print(f"\n== {ticker} ({company}) ==", flush=True)
        a = anchors(ticker)
        entry = {"anchors": a, "gs_tp": gs_tp, "listing": listing, "per": per,
                 "G1": [], "G2": [], "S": None, "Q": None}
        if ticker in SOTP_NAMES:
            _, snap = lookup_snapshot(snapshot, ticker)
            if snap and a["shares"]:
                entry["S"] = engine_value(ticker, snap, a)
            entry["Q"] = qwen_baseline(ticker, a, snap)
            fmp_anchor = {"revenue_next_fy_usd_bn": round((a["revenue_fwd_usd"] or 0) / 1e9, 2),
                          "period": a["revenue_fwd_period"], "reported_currency": a["reported_currency"]}
            for i in range(repeats):
                started = time.monotonic()
                try:
                    out = gp.generate(gp.sotp_prompt(company, ticker, fmp_anchor), schema=gp.SotpInputs,
                                      timeout=timeout, thinking=THINKING)
                except gp.GeminiBillingError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    entry["G1"].append({"error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                        "wall_s": round(time.monotonic() - started, 2)})
                    print(f"  G1[{i}] ERROR {type(exc).__name__} after "
                          f"{time.monotonic() - started:.0f}s", flush=True)
                    continue
                if record:
                    _record(f"{ticker}_G1_{i}", out)
                assumptions, checks = gp.to_engine_assumptions(
                    out["json"], fmp_revenue_fwd_usd=a["revenue_fwd_usd"])   # FX via get_fx_rate
                val = engine_value(ticker, assumptions, a) if assumptions["segments"] and a["shares"] else {"value": None}
                entry["G1"].append({
                    "value": val["value"], "log_err_vs_gs": _log_err(val["value"], gs_tp),
                    "vs_brokers": {b: _signed_pct(val["value"], tp) for b, tp in BROKER_TPS.get(ticker, {}).items()},
                    "ground_truth": val.get("ground_truth"), "checks": checks, "mode": out["mode"],
                    "latency_s": out["latency_s"], "usage": out["usage"], "queries": len(out["queries"]),
                    "wall_s": round(time.monotonic() - started, 2), "inputs": out["json"],
                })
                print(f"  G1[{i}] value={val['value']} cites={checks['citation_coverage']} "
                      f"{out['latency_s']}s mode={out['mode']} ccy={sorted(set(checks['currencies']))} "
                      f"gap={checks.get('segment_sum_gap')} {checks.get('rejected', '')}", flush=True)
        for i in range(repeats):
            try:
                out = gp.generate(gp.direct_prompt(company, ticker, per), schema=gp.DirectEstimate,
                                  timeout=timeout, thinking=THINKING)
            except gp.GeminiBillingError:
                raise
            except Exception as exc:  # noqa: BLE001
                entry["G2"].append({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                continue
            if record:
                _record(f"{ticker}_G2_{i}", out)
            est = out["json"]
            entry["G2"].append({"sotp_value": est["sotp_value"], "fair_value": est["fair_value"],
                                "currency": est["currency"], "per": est["per"],
                                "log_err_vs_gs": _log_err(est["sotp_value"], gs_tp) if est["currency"] == listing else None,
                                "vs_brokers": ({b: _signed_pct(est["sotp_value"], tp)
                                                for b, tp in BROKER_TPS.get(ticker, {}).items()}
                                               if est["currency"] == listing else {}),
                                "latency_s": out["latency_s"], "usage": out["usage"]})
            print(f"  G2[{i}] sotp={est['sotp_value']} {est['currency']}/{est['per']} {out['latency_s']}s", flush=True)
        results[ticker] = entry
    return results


def summarise(results: dict) -> dict:
    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 4) if xs else None

    def pct(xs, q):
        xs = sorted(x for x in xs if x is not None)
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None

    g1_err, g2_err, s_err, q_err, cites, cvs, lat, gt_in, qwen_available = [], [], [], [], [], [], [], [], 0
    for t, e in results.items():
        vals = [r["value"] for r in e["G1"] if r.get("value")]
        g1_err.append(med([r.get("log_err_vs_gs") for r in e["G1"]]))
        g2_err.append(med([r.get("log_err_vs_gs") for r in e["G2"]]))
        cites += [r["checks"]["citation_coverage"] for r in e["G1"] if "checks" in r]
        lat += [r.get("wall_s") for r in e["G1"]]
        if len(vals) >= 2:
            cvs.append(statistics.pstdev(vals) / statistics.mean(vals))
        for r in e["G1"]:
            gt = r.get("ground_truth")
            if gt:
                segs = gt["segments"]
                gt_in.append(sum(s["status"] == "in_range" for s in segs) / len(segs))
        if e.get("S"):
            s_err.append(_log_err(e["S"]["value"], e["gs_tp"]))
        if e.get("Q") and e["Q"].get("value"):
            qwen_available += 1
            q_err.append(_log_err(e["Q"]["value"], e["gs_tp"]))
    summary = {
        "median_log_err": {"G1": med(g1_err), "G2": med(g2_err), "S": med(s_err), "Q": med(q_err)},
        "qwen_baselines_found": qwen_available,
        "G1_citation_coverage_median": med(cites),
        "G1_cv_max": round(max(cvs), 4) if cvs else None,
        "G1_wall_s_p50": pct(lat, 0.5), "G1_wall_s_p90": pct(lat, 0.9),
        "G1_ground_truth_segment_in_range": med(gt_in),
    }
    q = summary["median_log_err"]["Q"]
    summary["pass"] = {
        "accuracy_vs_qwen": (summary["median_log_err"]["G1"] is not None and q is not None
                             and summary["median_log_err"]["G1"] <= q),
        "citations_90pct": (summary["G1_citation_coverage_median"] or 0) >= 0.90,
        "stability_cv_10pct": summary["G1_cv_max"] is not None and summary["G1_cv_max"] <= 0.10,
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--thinking-level", choices=["minimal", "low", "medium", "high"], default=None,
                    help="generationConfig.thinkingConfig.thinkingLevel; default = model default")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="per-call seconds; the evaluation measures latency rather than enforcing it")
    ap.add_argument("--prod-db", action="store_true",
                    help="read the Qwen baseline from production web_runs (read-only)")
    ap.add_argument("--tickers", default=",".join(UNIVERSE))
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    global THINKING
    if args.thinking_level:
        THINKING = {"thinkingLevel": args.thinking_level}
    try:
        results = run(args.repeats, args.record, tickers, timeout=args.timeout)
    except gp.GeminiBillingError as exc:
        print(f"\nSTOPPED: Gemini billing -- {exc}")
        sys.exit(2)
    report = {"model": gp.model_name(), "date": date.today().isoformat(),
              "repeats": args.repeats, "summary": summarise(results), "results": results}
    Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("\nSUMMARY", json.dumps(report["summary"], indent=1))


if __name__ == "__main__":
    main()

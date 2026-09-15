"""Top up the segment-revenue memory for SOTP-valued tickers.

HK and SG names use gemini-3.8-flash. US / ADR names do not: their 10-K /
20-F segment footnote already gives reportable segments with revenue AND
operating profit (src/tools/sec_segments.py), and FMP's product segmentation
covers single-segment filers like PDD (revenue only).

FMP returns no segment revenue for HK/SG names (0 of 9 probed), and SGX
publishes no machine-readable segment note -- which is what blocks segment
SOTP for Wilmar, ThaiBev, Olam and SingPost. This fills the gap with cited,
REPORTED (not estimated) segment revenue for the last five fiscal years,
reconciled year by year against FMP's reported group revenue.

Segment PROFIT (the measure the company reports, e.g. adjusted EBITA) is
collected alongside revenue so SOTP margins come from filings too. Default
reasoning: in the 2026-09-15 BABA comparison low reasoning misread tables and
high was slower with more empty responses.

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


def is_hk_sg(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith(".HK") or t.endswith(".SI")


def _cited(value: float, ccy: str, period_end: str, url: str, quote: str) -> dict:
    return {"value": float(value), "currency": ccy, "scale": "units", "period": period_end,
            "source_url": url, "quote": quote}


def sec_entry(ticker: str, company: str, basis: str) -> dict | None:
    """US / ADR filers: reportable segments with revenue AND operating profit
    from the latest 10-K / 20-F (src/tools/sec_segments.py). No model involved.
    Covers the three periods the filing presents."""
    from src.tools.sec_segments import get_segment_footnote
    fp = get_segment_footnote(ticker, date.today().isoformat())
    if not fp or not fp.get("segments"):
        return None
    ccy, url = fp["reporting_currency"], fp["source_url"]
    quote = f"{fp['form']} filed {fp['filed']}: {fp['report_short_name']}"
    segments = []
    for s in fp["segments"]:
        years = []
        for end, rev in sorted((s.get("revenue_by_period") or {}).items()):
            prof = (s.get("profit_by_period") or {}).get(end)
            years.append({"fiscal_year": f"FY{end[:4]}", "period_end": end,
                          "revenue": _cited(rev, ccy, end, url, quote),
                          "profit": _cited(prof, ccy, end, url, quote) if prof is not None else None,
                          "profit_measure": (fp.get("profit_metric") or "").split("_")[-1] or None})
        segments.append({"name": s["name"], "years": years})
    return {"source": "sec_segment_footnote", "form": fp["form"], "filed": fp["filed"],
            "history": {"reporting_currency": ccy, "segments": segments, "total_revenue": [],
                        "segment_definition_changes": ""}}


def fmp_product_entry(ticker: str) -> dict | None:
    """Fallback for single-segment filers (PDD): FMP's revenue split by product
    or revenue type. Revenue only -- FMP carries no segment profit."""
    from src.tools.fmp_transcripts import to_fmp_symbol
    url = (f"https://financialmodelingprep.com/stable/revenue-product-segmentation?symbol="
           f"{to_fmp_symbol(ticker)}&period=annual&apikey={os.environ['FMP_API_KEY']}")
    with urllib.request.urlopen(url, timeout=30) as r:
        rows = json.loads(r.read()) or []
    if not rows:
        return None
    _, ccy = _fmp_revenue_by_year(ticker)
    public = url.split("&apikey=")[0]
    by_name: dict[str, list] = {}
    for row in sorted(rows, key=lambda x: str(x.get("date")))[-5:]:
        end = str(row.get("date"))[:10]
        for name, value in (row.get("data") or {}).items():
            if isinstance(value, (int, float)):
                by_name.setdefault(name, []).append({
                    "fiscal_year": f"FY{end[:4]}", "period_end": end, "profit": None, "profit_measure": None,
                    "revenue": _cited(value, ccy, end, public, "FMP revenue-product-segmentation")})
    return {"source": "fmp_product_segmentation",
            "history": {"reporting_currency": ccy, "total_revenue": [], "segment_definition_changes": "",
                        "segments": [{"name": n, "years": ys} for n, ys in by_name.items()]}}


#: Template divisions valued on their OWN stated figure or at market need no
#: EBITDA; everything else in a holdco template does.
_SELF_VALUING = {"market_stake", "transaction_anchor", "cap_rate", "ev_ebit_range", "nil"}


def ebitda_divisions(ticker: str) -> list[str]:
    from src.agents.analysis import holdco_sotp
    tpl = holdco_sotp.template_for(ticker) or {}
    return [d["name"] for d in tpl.get("divisions") or [] if d.get("basis") not in _SELF_VALUING]


def build_division_ebitda(memory: dict, ticker: str, timeout: float) -> None:
    """Reported division EBITDA for a holdco's look-through (CITIC, CK Hutchison,
    Swire). Only divisions named in the template are kept, by exact name."""
    from src.agents.analysis.sotp_multiple_basis import normalize_key
    names = ebitda_divisions(ticker)
    if not names:
        print(f"{ticker}: no template division needs EBITDA")
        return
    company, basis = SOTP_UNIVERSE[ticker]
    entry = memory["tickers"].setdefault(ticker, {"company": company, "sotp_basis": basis})
    if entry.get("error"):
        entry["history_error"] = entry.pop("error")        # the EBITDA call is separate
    try:
        out = gp.generate(gp.division_ebitda_prompt(company, ticker, names),
                          schema=gp.DivisionEbitdaSet, timeout=timeout)
    except gp.GeminiBillingError:
        raise
    except Exception as exc:  # noqa: BLE001
        entry["division_ebitda_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        print(f"{ticker}: division EBITDA ERROR {type(exc).__name__}")
        return
    by_key = {normalize_key(n): n for n in names}
    items, dropped = [], []
    for d in out["json"]["divisions"]:
        name = by_key.get(normalize_key(d["division"]))
        if not name or not gp._cited_ok(d["ebitda"]):
            dropped.append(d["division"])
            continue
        items.append({**d, "division": name})
    entry.pop("division_ebitda_error", None)
    entry["division_ebitda"] = {"retrieved": date.today().isoformat(), "model": out["model"],
                                "latency_s": out["latency_s"], "items": items,
                                "missing": [n for n in names if n not in {i["division"] for i in items}],
                                "dropped": dropped, "notes": out["json"]["notes"]}
    got = ", ".join("{}={:g} {} {}".format(i["division"], i["ebitda"]["value"], i["ebitda"]["currency"],
                                           i["ebitda"]["scale"]) for i in items)
    missing = entry["division_ebitda"]["missing"]
    print(f"{ticker}: division EBITDA {len(items)}/{len(names)} ({got})"
          + (" missing " + ", ".join(missing) if missing else ""), flush=True)


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
    ap.add_argument("--gemini-for-us", action="store_true",
                    help="use Gemini for US/ADR names too (default: SEC segment footnote, then FMP)")
    ap.add_argument("--us-only", action="store_true", help="only the US/ADR names (no Gemini calls)")
    ap.add_argument("--division-ebitda", action="store_true",
                    help="holdco look-through: reported EBITDA for template divisions that need it")
    args = ap.parse_args()

    memory = _load()
    if args.division_ebitda:
        for ticker in [t.strip() for t in args.tickers.split(",") if t.strip()]:
            try:
                build_division_ebitda(memory, ticker, args.timeout)
            except gp.GeminiBillingError as exc:
                print(f"STOPPED: Gemini billing -- {exc}")
                break
            memory["_meta"]["updated"] = date.today().isoformat()
            OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")
        return
    for ticker in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        company, basis = SOTP_UNIVERSE[ticker]
        if args.us_only and is_hk_sg(ticker):
            continue
        entry = memory["tickers"].get(ticker) or {}
        # a US entry still sourced from Gemini is replaced by its filing
        stale_us = (not is_hk_sg(ticker) and not args.gemini_for_us
                    and entry.get("source") not in ("sec_segment_footnote", "fmp_product_segmentation"))
        if entry and not args.force and not entry.get("error") and not stale_us:
            print(f"{ticker}: already in memory, skipped")
            continue
        started = time.monotonic()
        if not is_hk_sg(ticker) and not args.gemini_for_us:
            # US / ADR: filings and FMP already carry segments -- no Gemini call.
            try:
                fmp_rev, rep_ccy = _fmp_revenue_by_year(ticker)
                filed = sec_entry(ticker, company, basis) or fmp_product_entry(ticker)
            except Exception as exc:  # noqa: BLE001
                filed = None
                print(f"{ticker}: SEC/FMP ERROR {type(exc).__name__}: {exc}")
            if not filed:
                memory["tickers"][ticker] = {"company": company, "sotp_basis": basis,
                                             "error": "no SEC segment footnote or FMP segmentation",
                                             "attempted": date.today().isoformat()}
            else:
                hist = filed.pop("history")
                rec = gp.reconcile_history(hist, fmp_rev, rep_ccy, _fx)
                n_values = sum(len(s["years"]) for s in hist["segments"])
                n_profit = sum(1 for s in hist["segments"] for y in s["years"] if y.get("profit"))
                memory["tickers"][ticker] = {
                    "company": company, "sotp_basis": basis, "fmp_reporting_currency": rep_ccy,
                    "retrieved": date.today().isoformat(), "model": None, **filed,
                    "citation_coverage": 1.0 if n_values else 0.0,
                    "profit_coverage": round(n_profit / n_values, 4) if n_values else 0.0,
                    "reconciliation": rec, "history": hist,
                }
                gaps = [f"{y}:{v['segment_gap']:+.0%}" for y, v in rec.items() if v.get("segment_gap") is not None]
                print(f"{ticker}: {filed['source']} {len(hist['segments'])} segments, {n_values} values, "
                      f"profit {n_profit}/{n_values}, segment-sum vs FMP {' '.join(gaps) or 'n/a'}", flush=True)
            memory["_meta"]["updated"] = date.today().isoformat()
            OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")
            continue
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
        if not hist.get("segments"):
            # an empty list is a failed retrieval, and must stay retryable
            memory["tickers"][ticker] = {"company": company, "sotp_basis": basis,
                                         "error": f"no segments returned (mode {out.get('mode')})",
                                         "attempted": date.today().isoformat()}
            print(f"{ticker}: ERROR no segments returned after {time.monotonic() - started:.0f}s")
            OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")
            continue
        rec = gp.reconcile_history(hist, fmp_rev, rep_ccy, _fx)
        n_values = sum(len(s.get("years") or []) for s in hist.get("segments") or [])
        n_cited = sum(gp._cited_ok(y.get("revenue")) for s in hist.get("segments") or []
                      for y in s.get("years") or [])
        n_profit = sum(gp._cited_ok(y.get("profit")) for s in hist.get("segments") or []
                       for y in s.get("years") or [])
        memory["tickers"][ticker] = {
            "company": company, "sotp_basis": basis, "fmp_reporting_currency": rep_ccy,
            "retrieved": date.today().isoformat(), "model": out["model"],
            "latency_s": out["latency_s"], "usage": out["usage"],
            "citation_coverage": round(n_cited / n_values, 4) if n_values else 0.0,
            "profit_coverage": round(n_profit / n_values, 4) if n_values else 0.0,
            "reconciliation": rec, "history": hist,
        }
        gaps = [f"{y}:{v['segment_gap']:+.0%}" for y, v in rec.items() if v.get("segment_gap") is not None]
        print(f"{ticker}: {len(hist.get('segments') or [])} segments, {n_values} values, "
              f"cited {n_cited}/{n_values}, profit {n_profit}/{n_values}, {out['latency_s']:.0f}s, "
              f"segment-sum vs FMP {' '.join(gaps) or 'n/a'}",
              flush=True)
        memory["_meta"]["updated"] = date.today().isoformat()
        OUT.write_text(json.dumps(memory, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

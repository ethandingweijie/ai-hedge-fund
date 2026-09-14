"""Segment revenue and profit memory -- reported history for SOTP-valued names.

Built by scripts/build_segment_revenue_memory.py (gemini-3.8-flash, grounded,
default reasoning) into src/data/segment_revenue_memory.json. Every value is
cited and each year is reconciled to FMP's reported group revenue; the file is
"pending_review" until the owner accepts it.

Two readers:
  latest_mix()   the most recent reported year per segment -- revenue share
                 and margin -- which the SOTP inputs test scales to FMP's
                 forward consensus revenue (our arithmetic, not a model's)
  ui_summary()   the Model Accuracy page's table

HK lines resolve to their ADR entry (09988.HK -> BABA) via the snapshot lookup.
Every failure mode returns empty.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

MEMORY_PATH = Path(__file__).resolve().parent / "segment_revenue_memory.json"


def load(path: Optional[Path] = None) -> dict:
    try:
        doc = json.loads(Path(path or MEMORY_PATH).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) and isinstance(doc.get("tickers"), dict) else {}
    except (OSError, ValueError):
        return {}


def entry_for(ticker: str, memory: Optional[dict] = None) -> Optional[dict]:
    from src.agents.analysis.sotp_snapshot import lookup_snapshot
    doc = memory if memory is not None else load()
    usable = {k: v for k, v in (doc.get("tickers") or {}).items()
              if isinstance(v, dict) and v.get("history") and not v.get("error")}
    return lookup_snapshot(usable, ticker)[1]


def _default_fx(from_ccy: str, to_ccy: str) -> Optional[float]:
    if from_ccy.upper() == to_ccy.upper():
        return 1.0
    try:
        from src.tools.api import get_fx_rate
        rate = get_fx_rate(from_ccy.upper(), to_ccy.upper())
        return float(rate) if rate and rate > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _in(c: Optional[dict], ccy: str, fx_to: Callable[[str, str], Optional[float]]) -> Optional[float]:
    from src.agents.industry.gemini_params import amount
    return amount(c, lambda src: fx_to(src, ccy))


def _years(entry: dict, fx_to) -> dict[str, dict[str, dict]]:
    """{period-end year: {segment: {revenue, profit, profit_measure, urls}}} in the
    FMP reporting currency, cited values only."""
    ccy = entry.get("fmp_reporting_currency") or entry["history"].get("reporting_currency") or "USD"
    out: dict[str, dict[str, dict]] = {}
    for seg in entry["history"].get("segments") or []:
        for y in seg.get("years") or []:
            rev = _in(y.get("revenue"), ccy, fx_to)
            if rev is None:
                continue
            key = str(y.get("period_end", ""))[:4]
            out.setdefault(key, {})[seg["name"]] = {
                "fiscal_year": y.get("fiscal_year"),
                "revenue": rev,
                "profit": _in(y.get("profit"), ccy, fx_to),
                "profit_measure": y.get("profit_measure"),
                "revenue_url": (y.get("revenue") or {}).get("source_url"),
                "revenue_quote": (y.get("revenue") or {}).get("quote"),
                "profit_url": (y.get("profit") or {}).get("source_url"),
            }
    return out


def latest_mix(ticker: str, *, memory: Optional[dict] = None,
               fx_to: Callable[[str, str], Optional[float]] = _default_fx) -> Optional[dict]:
    entry = entry_for(ticker, memory)
    if not entry:
        return None
    years = _years(entry, fx_to)
    if not years:
        return None
    year = max(years)
    segs = years[year]
    total = sum(s["revenue"] for s in segs.values())
    if total <= 0:
        return None
    return {
        "year": year,
        "currency": entry.get("fmp_reporting_currency"),
        "segments": [{
            "name": name, "revenue": s["revenue"], "share": s["revenue"] / total,
            "profit": s["profit"], "profit_measure": s["profit_measure"],
            "margin": (s["profit"] / s["revenue"]) if s["profit"] is not None and s["revenue"] else None,
        } for name, s in sorted(segs.items(), key=lambda kv: -kv[1]["revenue"])],
    }


def ui_summary(*, memory: Optional[dict] = None,
               fx_to: Callable[[str, str], Optional[float]] = _default_fx) -> dict:
    doc = memory if memory is not None else load()
    meta = doc.get("_meta") or {}
    rows = []
    for ticker, entry in sorted((doc.get("tickers") or {}).items()):
        base = {"ticker": ticker, "company": entry.get("company"), "sotp_basis": entry.get("sotp_basis")}
        if entry.get("error") or not entry.get("history"):
            rows.append({**base, "error": entry.get("error") or "no history"})
            continue
        years = _years(entry, fx_to)
        names = sorted({n for segs in years.values() for n in segs},
                       key=lambda n: -max((years[y].get(n) or {}).get("revenue") or 0 for y in years))
        n_years = sum(len(s.get("years") or []) for s in entry["history"].get("segments") or [])
        n_profit = sum(1 for segs in years.values() for s in segs.values() if s["profit"] is not None)
        rows.append({
            **base,
            "currency": entry.get("fmp_reporting_currency"),
            "retrieved": entry.get("retrieved"), "model": entry.get("model"),
            "citation_coverage": entry.get("citation_coverage"),
            "profit_coverage": round(n_profit / n_years, 4) if n_years else 0.0,
            "years": sorted(years),
            "segments": [{
                "name": name,
                "years": [{
                    "year": y, **{k: v for k, v in (years[y].get(name) or {}).items()},
                    "margin": ((years[y][name]["profit"] / years[y][name]["revenue"])
                               if name in years[y] and years[y][name]["profit"] is not None else None),
                } if name in years[y] else {"year": y} for y in sorted(years)],
            } for name in names],
            "reconciliation": entry.get("reconciliation") or {},
            "resegmentation": (entry["history"].get("segment_definition_changes") or "").strip(),
        })
    return {"status": meta.get("status", "missing"), "updated": meta.get("updated"),
            "model": meta.get("model"), "tickers": rows}

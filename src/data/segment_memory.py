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


def _has_segments(entry: dict) -> bool:
    """CITIC, Swire, ThaiBev and GenScript came back from the search-then-format
    fallback as a 'success' holding zero segments; that is not a retrieval."""
    return bool(((entry.get("history") or {}).get("segments")))


def entry_for(ticker: str, memory: Optional[dict] = None) -> Optional[dict]:
    from src.agents.analysis.sotp_snapshot import lookup_snapshot
    doc = memory if memory is not None else load()
    usable = {k: v for k, v in (doc.get("tickers") or {}).items()
              if isinstance(v, dict) and _has_segments(v) and not v.get("error")}
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


#: A segment whose revenue equals the sum of two or more other segments in the
#: same year within this tolerance is a subtotal, not a segment. Xiaomi's
#: "Smartphone x AIoT" (RMB 270.97bn = 157.46 + 80.11 + 30.11 + 3.29) was
#: returned alongside its components and inflated FY2023-25 by 77-100%.
#: With many segments some combination lands close by chance -- CK Hutchison's
#: Retail (209,267) is within 0.14% of Telecom + Infrastructure + Ports -- so a
#: subtotal must match in EVERY year it appears, and a one-year match must be
#: near exact.
SUBTOTAL_TOLERANCE = 0.001
SINGLE_YEAR_TOLERANCE = 0.0001
#: A company whose own reported total sits this far from FMP's consolidated
#: revenue, with the segments agreeing with the company, is on another basis
#: (CK Hutchison reports revenue including its share of associates and JVs).
BASIS_GAP = 0.15
#: A cited company total this far from FMP is flagged (Geely 2024: +14.87%).
TOTAL_CHECK_GAP = 0.10


def _subtotal_names(years: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    """{segment name: years} for rows that are sums of other segments."""
    from itertools import combinations
    errors: dict[str, list[tuple[str, Optional[float]]]] = {}
    for year, segs in years.items():
        for name, s in segs.items():
            target = s["revenue"]
            others = [v["revenue"] for m, v in segs.items() if m != name]
            best: Optional[float] = None
            if target > 0 and len(others) >= 2:
                for k in range(2, len(others) + 1):
                    for combo in combinations(others, k):
                        err = abs(sum(combo) - target) / target
                        best = err if best is None or err < best else best
            errors.setdefault(name, []).append((year, best))
    out: dict[str, list[str]] = {}
    for name, errs in errors.items():
        values = [e for _, e in errs]
        if all(e is not None and e <= SUBTOTAL_TOLERANCE for e in values) and \
                (len(values) >= 2 or values[0] <= SINGLE_YEAR_TOLERANCE):
            out[name] = sorted(y for y, _ in errs)
    return out


def _years(entry: dict, fx_to) -> dict[str, dict[str, dict]]:
    """{period-end year: {segment: {revenue, profit, profit_measure, urls}}} in the
    FMP reporting currency, cited values only, subtotal rows removed."""
    return _years_and_notes(entry, fx_to)[0]


def _years_and_notes(entry: dict, fx_to) -> tuple[dict[str, dict[str, dict]], list[str]]:
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
    notes: list[str] = []
    subtotals = _subtotal_names(out)
    for year in out:
        out[year] = {k: v for k, v in out[year].items() if k not in subtotals}
    for name, years in subtotals.items():
        notes.append(f"'{name}' removed as a subtotal of other segments ({', '.join(years)})")
    return out, notes


def reconciliation(entry: dict, years: dict[str, dict[str, dict]]) -> tuple[dict, list[str]]:
    """Segment sum vs FMP revenue recomputed from the cleaned segments, keeping
    the stored FMP revenue and the company's cited total; plus a basis note."""
    stored = entry.get("reconciliation") or {}
    out: dict[str, dict] = {}
    for year in sorted(set(stored) | set(years)):
        s = stored.get(year) or {}
        fmp = s.get("fmp_revenue")
        seg_sum = sum(v["revenue"] for v in (years.get(year) or {}).values())
        out[year] = {"segment_sum": seg_sum, "fmp_revenue": fmp,
                     "segment_gap": round(seg_sum / fmp - 1, 4) if fmp and seg_sum else None,
                     "total_gap": s.get("total_gap")}
    notes: list[str] = []
    compared = [y for y, v in out.items() if v["fmp_revenue"] and v["segment_gap"] is not None]
    # Company's cited total differs from FMP. Every compared year -> a standing
    # basis difference (CK Hutchison: share of associates and JVs). Only some
    # years -> a specific figure to check: a restatement for discontinued
    # operations (Olam 2025, SingPost FY2025) or a misread number (Geely 2024
    # cited RMB 275.9bn against a reported 240.2bn).
    off = [y for y in compared
           if out[y]["total_gap"] is not None and abs(out[y]["total_gap"]) > TOTAL_CHECK_GAP]
    if off:
        gaps = ", ".join(f"{y} {out[y]['total_gap']:+.0%}" for y in off)
        if len(off) == len(compared) and len(compared) >= 2:
            notes.append("The company's reported total differs from FMP consolidated revenue in every "
                         f"year ({gaps}) and its segments agree with that total: segment revenue is on "
                         "another basis, for example including its share of associates and joint ventures")
        else:
            notes.append(f"The cited total differs from FMP consolidated revenue in {gaps} only: check "
                         "the source for that year -- a restatement (e.g. discontinued operations "
                         "excluded) or a misread figure. Do not value on that year until confirmed")
    # Segments above a total that FMP agrees with: inter-segment eliminations
    # (JD reports RMB 83.7bn; BABA's 20-F has no elimination line).
    elim = [y for y in compared if y not in off
            and 0.02 <= out[y]["segment_gap"] <= BASIS_GAP]
    if elim:
        gaps = ", ".join(f"{y} {out[y]['segment_gap']:+.0%}" for y in elim)
        notes.append(f"Segments sum above group revenue ({gaps}): consistent with inter-segment "
                     "eliminations that are not a segment")
    return out, notes


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
        if entry.get("error") or not _has_segments(entry):
            rows.append({**base, "error": entry.get("error")
                         or "Gemini returned no segments after retries and the search-then-format fallback"})
            continue
        years, notes = _years_and_notes(entry, fx_to)
        rec, basis_notes = reconciliation(entry, years)
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
            "reconciliation": rec,
            "notes": notes + basis_notes,
            "source": entry.get("source") or "gemini_grounded",
            "resegmentation": (entry["history"].get("segment_definition_changes") or "").strip(),
        })
    return {"status": meta.get("status", "missing"), "updated": meta.get("updated"),
            "model": meta.get("model"), "tickers": rows}

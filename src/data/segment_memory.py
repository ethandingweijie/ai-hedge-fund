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


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _has_segments(entry: dict) -> bool:
    """CITIC, Swire, ThaiBev and GenScript came back from the search-then-format
    fallback as a 'success' holding zero segments; that is not a retrieval."""
    return bool(((entry.get("history") or {}).get("segments")))


def _division_items(entry: dict) -> list[dict]:
    return list(((entry.get("division_ebitda") or {}).get("items")) or [])


def _usable(entry: dict) -> bool:
    """Reviewable: segment history, or division EBITDA for a holdco look-through
    (CITIC and Swire have the latter without the former)."""
    return isinstance(entry, dict) and not entry.get("error") and (
        _has_segments(entry) or bool(_division_items(entry)))


def entry_for(ticker: str, memory: Optional[dict] = None) -> Optional[dict]:
    from src.agents.analysis.sotp_snapshot import lookup_snapshot
    doc = memory if memory is not None else load()
    usable = {k: v for k, v in (doc.get("tickers") or {}).items()
              if isinstance(v, dict) and _has_segments(v) and not v.get("error")}
    return lookup_snapshot(usable, ticker)[1]


def division_ebitda_amounts(entry: dict, to_ccy: str,
                            fx_to: Optional[Callable[[str, str], Optional[float]]] = None) -> dict[str, float]:
    """{template division name: EBITDA in `to_ccy`, absolute} for cited items."""
    from src.agents.industry.gemini_params import amount
    fx_to = fx_to or _default_fx
    out = {}
    for item in _division_items(entry):
        value = amount(item.get("ebitda"), lambda src: fx_to(src, to_ccy))
        if value is not None:
            out[item["division"]] = value
    return out


def division_ebitda_for(ticker: str, to_ccy: str, *, memory: Optional[dict] = None,
                        fx_to: Optional[Callable[[str, str], Optional[float]]] = None) -> Optional[dict[str, float]]:
    """Accepted division EBITDA for the holdco look-through, or None."""
    _, entry = accepted_entry(ticker, memory)
    if not entry:
        return None
    return division_ebitda_amounts(entry, to_ccy, fx_to) or None


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
    history = entry.get("history") or {}
    ccy = entry.get("fmp_reporting_currency") or history.get("reporting_currency") or "USD"
    out: dict[str, dict[str, dict]] = {}
    for seg in history.get("segments") or []:
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


#: Live valuations use the 3-year average reported margin: BABA's FY2026 China
#: commerce margin (19.4%, quick-commerce investment) against a 31.9% 3-year
#: average moved the SOTP from $145 to $178/ADS, the latter within 4% of GS.
LIVE_MARGIN_BASIS = "avg3"


def mix_from_entry(entry: dict, *, fx_to: Callable[[str, str], Optional[float]] = _default_fx,
                   margin_basis: str = "latest") -> Optional[dict]:
    """Latest reported year's revenue share per segment, with its margin on the
    chosen basis ("latest" or "avg3": mean of the last three reported years)."""
    years = _years(entry, fx_to)
    if not years:
        return None
    year = max(years)
    segs = years[year]
    total = sum(s["revenue"] for s in segs.values())
    if total <= 0:
        return None
    recent = [y for y in sorted(years) if y <= year][-3:]

    def margins(name: str) -> list[float]:
        out = []
        for y in recent:
            s = years[y].get(name)
            if s and s["profit"] is not None and s["revenue"]:
                out.append(s["profit"] / s["revenue"])
        return out

    rows = []
    for name, s in sorted(segs.items(), key=lambda kv: -kv[1]["revenue"]):
        latest = (s["profit"] / s["revenue"]) if s["profit"] is not None and s["revenue"] else None
        ms = margins(name)
        avg3 = sum(ms) / len(ms) if ms else None
        rows.append({"name": name, "revenue": s["revenue"], "share": s["revenue"] / total,
                     "profit": s["profit"], "profit_measure": s["profit_measure"],
                     "margin_latest": latest, "margin_avg3": avg3, "margin_years": len(ms),
                     "margin": avg3 if margin_basis == "avg3" else latest})
    return {"year": year, "currency": entry.get("fmp_reporting_currency"),
            "margin_basis": margin_basis, "segments": rows}


def latest_mix(ticker: str, *, memory: Optional[dict] = None,
               fx_to: Callable[[str, str], Optional[float]] = _default_fx,
               margin_basis: str = "latest") -> Optional[dict]:
    entry = entry_for(ticker, memory)
    if not entry:
        return None
    return mix_from_entry(entry, fx_to=fx_to, margin_basis=margin_basis)


# ── mapping accepted memory onto live SOTP rows ─────────────────────────────

#: Memory names the shared archetype keywords miss ("Alibaba China E-commerce
#: Group" contains none of taobao / tmall / corecommerce). Local on purpose:
#: ARCHETYPES also drives the extractor's learned multiple basis.
_ARCHETYPE_SUPPLEMENT = {"chinaecommerce": "ecommerce_core"}
#: Current SOTP rows whose revenue no memory segment absorbs, as a share of
#: their revenue. BABA's Cainiao row (9%) folds into "All others" -- allowed;
#: Meituan's Instashopping + In-store (31%) would lose real granularity -- not.
UNMATCHED_ROW_SHARE = 0.15


def _name_key(name: str) -> str:
    from src.agents.analysis.sotp_multiple_basis import normalize_key
    return normalize_key((name or "").split("(")[0])


def _archetype(name: str) -> Optional[str]:
    from src.agents.analysis.sotp_multiple_basis import classify_archetype
    arch = classify_archetype(name)
    if arch:
        return arch
    key = _name_key(name)
    return next((a for kw, a in _ARCHETYPE_SUPPLEMENT.items() if kw in key), None)


def plan_mapping(assumptions: dict, mix: dict) -> tuple[Optional[list[tuple[dict, dict]]], str]:
    """Pair each memory segment with the current SOTP row carrying its multiple:
    same archetype, else the same name. Returns (pairs, "") or (None, reason)."""
    rows = [r for r in (assumptions or {}).get("segments") or [] if isinstance(r, dict) and r.get("name")]
    if not rows:
        return None, "no current SOTP rows to take multiples from"
    pairs, used = [], set()
    for seg in mix.get("segments") or []:
        arch, key = _archetype(seg["name"]), _name_key(seg["name"])
        candidates = []
        for i, row in enumerate(rows):
            rkey = _name_key(row["name"])
            same_arch = arch is not None and _archetype(row["name"]) == arch
            same_name = len(key) >= 5 and len(rkey) >= 5 and (key in rkey or rkey in key)
            if same_arch or same_name:
                candidates.append(i)
        if not candidates:
            return None, f"memory segment '{seg['name']}' has no counterpart among the current SOTP rows"
        best = max(candidates, key=lambda i: rows[i].get("revenue_fwd") or 0.0)
        pairs.append((seg, rows[best]))
        used.add(best)
    total = sum(r.get("revenue_fwd") or 0.0 for r in rows)
    unmatched = sum(rows[i].get("revenue_fwd") or 0.0 for i in range(len(rows)) if i not in used)
    if total > 0 and unmatched / total > UNMATCHED_ROW_SHARE:
        names = ", ".join(rows[i]["name"] for i in range(len(rows)) if i not in used)
        return None, (f"the current SOTP rows are finer than the memory: {unmatched / total:.0%} of their "
                      f"revenue ({names}) has no memory segment")
    return pairs, ""


def apply_to_sotp(assumptions: dict, entry: dict, *, entry_key: str, fwd_revenue_usd: float,
                  fx_to: Callable[[str, str], Optional[float]] = _default_fx) -> tuple[dict, dict]:
    """Assumptions with segment revenue and margin from ACCEPTED memory.

    Forward segment revenue = FMP consensus group revenue x latest reported
    share; margin = 3-year average reported margin (clamped to 0-60%, omitted
    when undisclosed); multiples stay those of the paired current row. Returns
    the input unchanged with the reason when the memory does not map."""
    info = {"applied": False, "memory_key": entry_key, "content_hash": content_hash(entry),
            "margin_basis": LIVE_MARGIN_BASIS}
    mix = mix_from_entry(entry, fx_to=fx_to, margin_basis=LIVE_MARGIN_BASIS)
    if not mix or not fwd_revenue_usd or fwd_revenue_usd <= 0:
        info["reason"] = "no usable memory mix or forward consensus revenue"
        return assumptions, info
    pairs, reason = plan_mapping(assumptions, mix)
    if not pairs:
        info["reason"] = reason
        return assumptions, info
    segments = []
    for seg, row in pairs:
        s = {"name": seg["name"], "revenue_fwd": fwd_revenue_usd * seg["share"],
             "pe_multiple": row.get("pe_multiple"), "ev_rev_multiple": row.get("ev_rev_multiple"),
             "rationale": (f"multiple from '{row['name']}': {row.get('rationale', '')}")[:300],
             "source": "accepted_segment_memory"}
        if seg["margin"] is not None:
            s["ebit_margin"] = min(max(float(seg["margin"]), 0.0), 0.60)
        segments.append(s)
    sources = assumptions.get("_sources") if isinstance(assumptions.get("_sources"), dict) else {}
    info.update(applied=True, mix_year=mix["year"],
                mapping=[{"memory": seg["name"], "row": row["name"], "share": round(seg["share"], 4),
                          "margin": None if seg["margin"] is None else round(seg["margin"], 4)}
                         for seg, row in pairs])
    return ({**assumptions, "segments": segments,
             "_sources": {**sources, "segments": "accepted_segment_memory"},
             "_segment_memory": info}, info)


# ── owner review: accept / revoke ───────────────────────────────────────────

_DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS segment_memory_reviews (
    memory_key   TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reviewer     TEXT,
    reviewed_at  TEXT NOT NULL
)
"""
_reviews_ready_key: Optional[tuple] = None


def _ensure_reviews() -> None:
    global _reviews_ready_key
    from src.data import db as _db
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key != _reviews_ready_key:
        _db.ensure_table(_DDL_REVIEWS)
        _reviews_ready_key = key


def content_hash(entry: dict) -> str:
    """What was reviewed. A rebuilt entry with different figures no longer
    matches, so an acceptance never silently carries over to new numbers."""
    import hashlib
    blob = json.dumps(entry.get("history") or {}, sort_keys=True, ensure_ascii=False)
    items = _division_items(entry)
    if items:
        # Appended only when present, so every acceptance made before division
        # EBITDA existed keeps its hash; adding EBITDA to a name needs a new one.
        blob += json.dumps(items, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def resolve(ticker: str, memory: Optional[dict] = None) -> tuple[Optional[str], Optional[dict]]:
    """(memory key, entry) for any listing of the company."""
    from src.agents.analysis.sotp_snapshot import lookup_snapshot
    doc = memory if memory is not None else load()
    usable = {k: v for k, v in (doc.get("tickers") or {}).items() if _usable(v)}
    return lookup_snapshot(usable, ticker)


def review_for(memory_key: str, entry: dict) -> dict:
    from src.data import db as _db
    _ensure_reviews()
    row = _db.query_one("SELECT status, content_hash, reviewer, reviewed_at FROM segment_memory_reviews "
                        "WHERE memory_key = ?", [memory_key])
    if not row:
        return {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False}
    stale = row["content_hash"] != content_hash(entry)
    status = row["status"]
    if stale and status == "accepted":
        status = "changed_since_acceptance"
    return {"status": status, "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"], "stale": stale}


def set_review(ticker: str, status: str, reviewer: Optional[str], *, memory: Optional[dict] = None) -> dict:
    """Record an owner decision for the company behind `ticker`. KeyError when
    there is no usable memory entry."""
    from datetime import datetime, timezone
    from src.data import db as _db
    if status not in ("accepted", "revoked"):
        raise ValueError(status)
    key, entry = resolve(ticker, memory)
    if not entry:
        raise KeyError(ticker)
    _ensure_reviews()
    _db.execute("DELETE FROM segment_memory_reviews WHERE memory_key = ?", [key])
    _db.execute("INSERT INTO segment_memory_reviews (memory_key, status, content_hash, reviewer, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [key, status, content_hash(entry), reviewer, datetime.now(timezone.utc).isoformat()])
    return {"memory_key": key, **review_for(key, entry)}


def accepted_entry(ticker: str, memory: Optional[dict] = None) -> tuple[Optional[str], Optional[dict]]:
    """(key, entry) only when the owner accepted exactly these figures."""
    try:
        key, entry = resolve(ticker, memory)
        if entry and review_for(key, entry)["status"] == "accepted":
            return key, entry
    except Exception:  # noqa: BLE001
        pass
    return None, None


def live_effect(ticker: str, entry: dict, *, fx_to=_default_fx) -> dict:
    """What acceptance would do to live SOTP inputs, without changing anything."""
    from src.agents.analysis import holdco_sotp
    from src.agents.analysis.sotp_snapshot import load_sotp_snapshot, lookup_snapshot
    tpl = holdco_sotp.template_for(ticker)
    if tpl:
        # Holdco: accepted division EBITDA completes the look-through. Checked
        # without market data -- only whether every division that needs EBITDA
        # has it -- so the page stays fast.
        self_valuing = {"market_stake", "transaction_anchor", "cap_rate", "ev_ebit_range", "nil"}
        needed = [d["name"] for d in tpl.get("divisions") or [] if d.get("basis") not in self_valuing]
        supplied = division_ebitda_amounts(entry, tpl.get("currency") or "USD", fx_to)
        missing = [n for n in needed if n not in supplied]
        base = {"method": "SOTP / NAV (look-through)", "needed": needed,
                "supplied": sorted(supplied), "missing": missing}
        if not holdco_sotp.enabled_for(ticker):
            return {**base, "applies": False,
                    "reason": ("the holdco look-through is switched off" if not holdco_sotp.enabled()
                               else "the holdco look-through is not switched on for this name yet")}
        if not needed:
            return {**base, "applies": True, "reason": "look-through needs no division EBITDA"}
        if missing:
            return {**base, "applies": False, "reason": "no EBITDA for " + ", ".join(missing)}
        return {**base, "applies": True, "reason": "accepted division EBITDA completes the look-through"}
    _, snap = lookup_snapshot(load_sotp_snapshot(), ticker)
    if not snap:
        return {"applies": False, "reason": "no SOTP (analyst) inputs for this name yet -- "
                                            "acceptance is recorded but moves no valuation"}
    mix = mix_from_entry(entry, fx_to=fx_to, margin_basis=LIVE_MARGIN_BASIS)
    if not mix:
        return {"applies": False, "reason": "no usable memory mix"}
    pairs, reason = plan_mapping(snap, mix)
    if not pairs:
        return {"applies": False, "reason": reason}
    return {"applies": True, "margin_basis": LIVE_MARGIN_BASIS, "mapping": [
        {"memory": s["name"], "row": r["name"], "share": round(s["share"], 4),
         "margin_avg3": None if s["margin_avg3"] is None else round(s["margin_avg3"], 4)} for s, r in pairs]}


def ui_summary(*, memory: Optional[dict] = None,
               fx_to: Callable[[str, str], Optional[float]] = _default_fx,
               reviews: Optional[Callable[[str, dict], dict]] = None,
               effects: Optional[Callable[[str, dict], dict]] = None) -> dict:
    from src.data.dual_listings import listings_for
    doc = memory if memory is not None else load()
    reviews = reviews or review_for
    effects = effects or (lambda t, e: live_effect(t, e, fx_to=fx_to))
    meta = doc.get("_meta") or {}
    rows = []
    for ticker, entry in sorted((doc.get("tickers") or {}).items()):
        base = {"ticker": ticker, "company": entry.get("company"), "sotp_basis": entry.get("sotp_basis"),
                "listings": listings_for(ticker)}
        if not _usable(entry):
            rows.append({**base, "error": entry.get("error")
                         or "Gemini returned no segments after retries and the search-then-format fallback"})
            continue
        base["division_ebitda"] = [{
            "division": i["division"], "value": i["ebitda"]["value"], "currency": i["ebitda"]["currency"],
            "scale": i["ebitda"]["scale"], "fiscal_year": i.get("fiscal_year"), "measure": i.get("measure"),
            "includes_share_of_associates": i.get("includes_share_of_associates"),
            "source_url": i["ebitda"].get("source_url"), "quote": i["ebitda"].get("quote"),
        } for i in _division_items(entry)]
        base["division_ebitda_missing"] = (entry.get("division_ebitda") or {}).get("missing") or []
        if entry.get("history_error"):
            base["history_error"] = entry["history_error"]
        years, notes = _years_and_notes(entry, fx_to)
        rec, basis_notes = reconciliation(entry, years)
        names = sorted({n for segs in years.values() for n in segs},
                       key=lambda n: -max((years[y].get(n) or {}).get("revenue") or 0 for y in years))
        n_years = sum(len(s.get("years") or []) for s in (entry.get("history") or {}).get("segments") or [])
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
            "review": _safe(lambda: reviews(ticker, entry), {"status": "unknown"}),
            "live_effect": _safe(lambda: effects(ticker, entry), {"applies": False, "reason": "not evaluated"}),
            "source": entry.get("source") or "gemini_grounded",
            "resegmentation": ((entry.get("history") or {}).get("segment_definition_changes") or "").strip(),
        })
    return {"status": meta.get("status", "missing"), "updated": meta.get("updated"),
            "model": meta.get("model"), "tickers": rows}

"""Industry inputs FMP does not carry -- cited, reconciled, review-gated.

Energy and A&D valuations need figures no FMP endpoint reports: the
discounted value of proved oil & gas reserves (PV-10 / standardized measure),
contracted backlog, and maintenance capex. SEC companyfacts carries almost
none of them as company-level facts (probed 2026-09-20: of COP, EOG, XOM, SLB,
RIG, HAL and KMI, only KMI tags remaining performance obligations and only EOG
ever tagged the standardized measure, last in 2017), so they are pre-filled by
a grounded Gemini call (scripts/build_industry_inputs.py) with every figure
cited as printed, checked against FMP here, and stored "pending".

Nothing reaches a valuation until the owner accepts the exact figures on the
Model Accuracy page (owner decision 2026-09-20: review-gated). An acceptance
is bound to a content hash, so a rebuilt entry with different figures needs a
new one. Every failure mode returns None.

Store: src/data/industry_inputs.json
    {"version": 1, "tickers": {TICKER: {KIND: {
        "data": <Gemini schema output>, "checks": [...], "ok": bool,
        "company": str, "model": str, "built_at": iso}}}}
Reviews: table industry_input_reviews (input_key = "TICKER|KIND").
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

STORE_PATH = Path(__file__).resolve().parent / "industry_inputs.json"
KINDS = ("pv10", "backlog", "maintenance_capex", "rate_base", "fcf_guidance", "sotp", "pipeline")

#: Kinds that ARE guidance. Everywhere else a figure for a year that has not
#: ended fails its period check; here one for a year that HAS ended does.
GUIDANCE_KINDS = ("fcf_guidance",)

#: Kinds whose figures are next-fiscal-year ESTIMATES by construction (a SOTP's
#: segment revenue is forward): the period check accepts a forward year, and a
#: year already reported is reported as such rather than failed.
FORWARD_PERIOD_KINDS = ("sotp",)

#: The overlay toggle. Off by default: a valuation runs on audited actuals
#: unless someone switches the forward view on deliberately.
OVERLAY_FLAG = "FEATURE_FORWARD_OVERLAY"

#: What the reserve figure is, stated wherever it is shown (owner, 2026-09-20).
RESERVE_MEASURE_REMARK = (
    "The standardized measure (ASC 932 / SEC rules) is strictly bound to a 12-month "
    "unweighted historical trailing average price (or unescalated year-end pricing) "
    "and a mandatory 10% discount rate. It is an accounting disclosure, not a fair "
    "market valuation."
)

#: Kinds an overlay may never touch. The SEC standardized measure is defined by
#: proved reserves at trailing SEC prices; a forward price deck applied to it
#: would report a rigid measure as a forward one. Price decks belong on the DCF
#: and NAV curves, where the audit trail can separate price from reserve life.
NO_OVERLAY = ("pv10", "sotp", "pipeline")

#: Checks whose failure blocks acceptance (owner, 2026-09-24, item 4): a
#: pre-fill whose segments sum to more than the group can only be rebuilt.
HARD_CHECKS = ("segment revenue vs group revenue",)

#: Plausibility bounds, each against a figure FMP reports for the same company.
#: A figure outside them is kept for review with the failed check named -- a
#: wrong scale (bn read as mn) lands 1000x out and fails here.
BOUNDS = {
    # PV-10 / market cap: a reserve value is a fraction to a few multiples of equity value.
    "pv10": ("market_cap", 0.05, 10.0),
    # Backlog / annual revenue: weeks of work (short-cycle services) to years (drillers).
    "backlog": ("revenue", 0.05, 15.0),
    # Maintenance capex / D&A: sustaining spend is a fraction of to about three times D&A.
    "maintenance_capex": ("depreciation_and_amortization", 0.1, 3.0),
    # Rate base / net PP&E: the regulated asset base is most of a pure utility's plant and
    # about half of a holding company's with a large unregulated arm (NextEra); it excludes
    # construction work in progress, so it rarely exceeds net plant.
    "rate_base": ("net_ppe", 0.25, 1.5),
    # Guided FCF / latest reported revenue: a margin. Under 1% is a scale error read
    # low; over 45% of LAST year's revenue is bn-for-mn or a cumulative multi-year figure.
    "fcf_guidance": ("revenue", 0.01, 0.45),
    # Segment revenues summed / latest group revenue: reportable segments run a
    # little under the group (eliminations) to a little over (a forward year).
    "sotp": ("revenue", 0.6, 1.6),
    # Summed consensus peak sales of the late-stage pipeline / latest revenue: a
    # handful of Phase 3 assets on a large pharma is a fraction of revenue; a
    # commercial biotech's pipeline can be several times its current sales.
    "pipeline": ("revenue", 0.02, 8.0),
}

#: Ratios a rate order can plausibly carry. An allowed ROE outside 6-14% or an equity
#: layer outside 30-65% is a percentage read as a decimal (or the reverse), or a
#: return on ASSETS reported as a return on equity (Hong Kong's Scheme of Control
#: permits 8% on net fixed assets and sets no ROE at all).
RATE_ORDER_BOUNDS = {"allowed_roe": (0.06, 0.14), "equity_ratio": (0.30, 0.65)}

_DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS industry_input_reviews (
    input_key     TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    reviewer      TEXT,
    reviewed_at   TEXT NOT NULL
)
"""
_reviews_ready_key = None


def load(path: Optional[Path] = None) -> dict:
    try:
        doc = json.loads(Path(path or STORE_PATH).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) and isinstance(doc.get("tickers"), dict) else {}
    except (OSError, ValueError):
        return {}


def save(doc: dict, path: Optional[Path] = None) -> None:
    Path(path or STORE_PATH).write_text(
        json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def overlay_enabled() -> bool:
    """True when the forward overlay is switched on for this process."""
    import os
    return os.getenv(OVERLAY_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _key(ticker: str) -> str:
    from src.tools.ticker_canonical import canonical_ticker
    return canonical_ticker(ticker)


def entry(ticker: str, kind: str, doc: Optional[dict] = None) -> Optional[dict]:
    d = doc if doc is not None else load()
    e = ((d.get("tickers") or {}).get(_key(ticker)) or {}).get(kind)
    return e if isinstance(e, dict) and isinstance(e.get("data"), dict) else None


def content_hash(e: dict, *, overlay: bool = False) -> str:
    """What was reviewed: the baseline figures, or the overlay's delta."""
    part = (e.get("overlay") or {}) if overlay else (e.get("data") or {})
    blob = json.dumps(part, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── amounts ──────────────────────────────────────────────────────────────────

def _fx(to_ccy: str) -> Callable[[str], Optional[float]]:
    to = (to_ccy or "USD").upper()

    def rate(ccy: str) -> Optional[float]:
        c = (ccy or "").upper()
        if not c:
            return None
        if c == to:
            return 1.0
        try:
            from src.tools.api import get_fx_rate
            r = get_fx_rate(c, to)
            return float(r) if r and r > 0 else None
        except Exception:  # noqa: BLE001
            return None
    return rate


def amount(c, rate):
    """`gemini_params.amount`, imported lazily so this module stays importable
    without the Gemini client."""
    from src.agents.industry.gemini_params import amount as _amount
    return _amount(c, rate)


def amount_of(e: dict, to_ccy: str,
              fx: Optional[Callable[[str], Optional[float]]] = None) -> Optional[float]:
    """The entry's headline amount in full units of `to_ccy`, or None."""
    return amount((e.get("data") or {}).get("value"), fx or _fx(to_ccy))


def _year(text: Optional[str]) -> Optional[int]:
    m = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(m.group(0)) if m else None


def ground_truth_check(value_usd: Optional[float], gt: Optional[dict]) -> Optional[dict]:
    """Compare a stored figure with the owner's audited range, in USD.

    The owner audits the panel against the filings (2026-09-20). A figure
    outside the range they checked is not necessarily wrong -- it may be a
    different basis, e.g. consolidated versus including joint ventures -- but
    it must be visible and explained rather than quietly displayed.
    """
    if not gt or value_usd is None:
        return None
    lo, hi = gt.get("low_usd"), gt.get("high_usd")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    ok = lo <= value_usd <= hi
    return {"check": "owner ground truth", "ok": ok,
            "detail": (f"${value_usd / 1e9:,.2f}bn vs audited ${lo / 1e9:,.2f}-${hi / 1e9:,.2f}bn"
                       + ("" if ok else f" -- {gt.get('note') or 'outside the audited range'}"))}


def reconcile(kind: str, value: Optional[float], context: dict,
              period: Optional[str] = None, data: Optional[dict] = None) -> list[dict]:
    """Checks for one amount (full units, the context's currency) against FMP.

    `period` is the fiscal period the figure is stated for: a pre-fill that
    answers with guidance for a future year, or with a year long past, is not
    the latest reported figure even when its magnitude is plausible (Williams,
    2026-09-20: FY2026E guidance, then FY2020 actuals).
    """
    checks: list[dict] = []
    y, latest = _year(period), _year(context.get("period"))
    if y and latest:
        # A backlog is a BALANCE at the latest period end, and that period is
        # usually a quarter past the last annual statement (LEU and GEV, Wave 2:
        # "Q2 2026" beside FY2025 annuals). That is the latest report, not
        # guidance. Flows -- capex, and a rate base projected for a future year
        # -- stay bound to years that have ended.
        hi = latest + 1 if kind == "backlog" else latest
        if kind in GUIDANCE_KINDS or kind in FORWARD_PERIOD_KINDS:
            # The opposite test: guidance is for a year that has NOT ended, and
            # stale guidance for a year already reported is an actual, not this.
            checks.append({"check": "guidance period", "ok": latest < y <= latest + 2,
                           "detail": f"guidance is for {y}; FMP's latest reported year is {latest}"})
        else:
            checks.append({"check": "latest reported period", "ok": latest - 1 <= y <= hi,
                           "detail": f"figure is {y}; FMP's latest reported year is {latest}"})
    if value is None:
        return [{"check": "cited_amount", "ok": False,
                 "detail": "no cited amount of known scale and convertible currency"}]
    if value <= 0:
        checks.append({"check": "positive", "ok": False, "detail": f"amount {value:,.0f} is not positive"})
    field, lo, hi = BOUNDS[kind]
    ref = context.get(field)
    if isinstance(ref, (int, float)) and ref > 0:
        ratio = value / ref
        checks.append({"check": f"{kind} / {field}", "ok": lo <= ratio <= hi,
                       "detail": f"{ratio:.2f}x (plausible {lo}x to {hi}x)", "ratio": round(ratio, 4)})
    else:
        checks.append({"check": f"{kind} / {field}", "ok": None,
                       "detail": f"FMP reports no {field} to check against"})
    if kind == "maintenance_capex":
        ocf = context.get("operating_cash_flow")
        if isinstance(ocf, (int, float)) and ocf > 0:
            checks.append({"check": "maintenance capex < OCF", "ok": value < ocf,
                           "detail": f"{value / ocf:.2f}x of operating cash flow"})
    if kind == "rate_base":
        for name, (r_lo, r_hi) in RATE_ORDER_BOUNDS.items():
            r = ((data or {}).get(name) or {}).get("value")
            if isinstance(r, (int, float)):
                checks.append({"check": name, "ok": r_lo <= r <= r_hi,
                               "detail": f"{r:.2%} (plausible {r_lo:.0%} to {r_hi:.0%})"})
            else:
                checks.append({"check": name, "ok": None,
                               "detail": f"no {name.replace('_', ' ')} stated; the method falls back without it"})
    return checks


# ── reviews ──────────────────────────────────────────────────────────────────

def _ensure_reviews() -> None:
    global _reviews_ready_key
    from src.data import db as _db
    k = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if k != _reviews_ready_key:
        _db.ensure_table(_DDL_REVIEWS)
        _reviews_ready_key = k


def review_for(ticker: str, kind: str, e: dict, *, overlay: bool = False) -> dict:
    from src.data import db as _db
    _ensure_reviews()
    row = _db.query_one("SELECT status, content_hash, reviewer, reviewed_at FROM industry_input_reviews "
                        "WHERE input_key = ?", [_review_key(ticker, kind, overlay)])
    if not row:
        return {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False}
    stale = row["content_hash"] != content_hash(e, overlay=overlay)
    status = row["status"]
    if stale and status == "accepted":
        status = "changed_since_acceptance"
    return {"status": status, "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"], "stale": stale}


def _review_key(ticker: str, kind: str, overlay: bool) -> str:
    return f"{_key(ticker)}|{kind}" + ("|overlay" if overlay else "")


def set_review(ticker: str, kind: str, status: str, reviewer: Optional[str], *,
               doc: Optional[dict] = None, overlay: bool = False) -> dict:
    """Record an owner decision on the baseline, or on its overlay delta.
    KeyError when there is no entry (or no overlay) to review."""
    from datetime import datetime, timezone
    from src.data import db as _db
    if status not in ("accepted", "revoked"):
        raise ValueError(status)
    if kind not in KINDS:
        raise KeyError(kind)
    e = entry(ticker, kind, doc)
    if not e or (overlay and not e.get("overlay")):
        raise KeyError(f"{ticker}|{kind}" + ("|overlay" if overlay else ""))
    if overlay and kind in NO_OVERLAY:
        raise ValueError(f"{kind} may not carry a forward overlay")
    if status == "accepted" and not overlay:
        failed = [c for c in (e.get("checks") or [])
                  if c.get("check") in HARD_CHECKS and c.get("ok") is False]
        if failed:
            raise ValueError("cannot accept: " + "; ".join(
                f"{c['check']} failed ({c.get('detail')})" for c in failed) + " -- rebuild the entry")
    _ensure_reviews()
    key = _review_key(ticker, kind, overlay)
    _db.execute("DELETE FROM industry_input_reviews WHERE input_key = ?", [key])
    _db.execute("INSERT INTO industry_input_reviews (input_key, status, content_hash, reviewer, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [key, status, content_hash(e, overlay=overlay), reviewer,
                 datetime.now(timezone.utc).isoformat()])
    return {"input_key": key, **review_for(ticker, kind, e, overlay=overlay)}


def engine_preview_check(assumptions: Optional[dict]) -> dict:
    """Owner, 2026-09-26: run the engine's SOTP leg on the converted inputs
    (unit shares, no balance sheet) and report which segments would be
    Degraded. Tencent's first pre-fill passed every gate check and could not
    price: two of four segments cited on P/E with no margin."""
    try:
        from src.agents.analysis.dcf_agent import _sotp_analyst_style
        t = _sotp_analyst_style(dict(assumptions or {}), shares=1.0)
    except Exception as exc:  # noqa: BLE001
        return {"check": "segments price in the engine", "ok": None,
                "detail": f"preview unavailable ({type(exc).__name__})"}
    if not t:
        return {"check": "segments price in the engine", "ok": False,
                "detail": "no segment carries a usable forward revenue"}
    bad = t.get("degraded_segments") or []
    n = len(t.get("rows") or [])
    if bad:
        return {"check": "segments price in the engine", "ok": False,
                "detail": (f"{len(bad)} of {n} segment(s) Degraded, the leg would not publish: "
                           + "; ".join(f"{d['name']}: {d['reason']}" for d in bad))}
    return {"check": "segments price in the engine", "ok": True,
            "detail": f"all {n} segment(s) price"
                      + (f" ({', '.join(r['method'] for r in t.get('rows') or [])})" if n else "")}


def sotp_engine_preview(e: Optional[dict]) -> Optional[dict]:
    """The preview check for a stored sotp entry (canonical data through the bridge)."""
    if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
        return None
    try:
        from src.agents.industry.gemini_params import to_engine_assumptions
        conv, _ = to_engine_assumptions(canonical_data(e))
    except Exception as exc:  # noqa: BLE001
        return {"check": "segments price in the engine", "ok": None,
                "detail": f"preview unavailable ({type(exc).__name__})"}
    return engine_preview_check(conv)


def canonical_data(e: Optional[dict]) -> Optional[dict]:
    """The entry's `data` with every grounding wrapper replaced by the
    canonical URL recorded in `canonical_urls` (owner, 2026-09-24). Returns
    the same object when there is nothing to map; never mutates the entry."""
    if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
        return None if not isinstance(e, dict) else e.get("data")
    cmap = e.get("canonical_urls") or {}
    if not cmap:
        return e["data"]

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and k.endswith("source_url") and isinstance(v, str) and v in cmap:
                    out[k] = cmap[v]
                    out[k.replace("source_url", "grounding_url")] = v
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(e["data"])


def accepted_entry(ticker: str, kind: str, doc: Optional[dict] = None) -> Optional[dict]:
    """The stored entry when the owner has accepted exactly its current figures,
    else None. For kinds whose payload is structured (a SOTP's segments and
    multiple ranges), the caller converts it; the gate is the same."""
    try:
        e = entry(ticker, kind, doc)
        if not e or review_for(ticker, kind, e)["status"] != "accepted":
            return None
        return e
    except Exception:  # noqa: BLE001
        return None


def accepted_amount(ticker: str, kind: str, to_ccy: str, *, doc: Optional[dict] = None,
                    fx: Optional[Callable[[str], Optional[float]]] = None,
                    overlay: Optional[bool] = None) -> Optional[float]:
    """The figure a valuation may use, in full units of `to_ccy`, or None.

    The baseline is the accepted audited actual. An accepted overlay is applied
    only when the toggle is on (or `overlay=True` is passed explicitly); see
    `accepted_detail` for the audit trail behind the number.
    """
    d = accepted_detail(ticker, kind, to_ccy, doc=doc, fx=fx, overlay=overlay)
    return d["value"] if d else None


def accepted_detail(ticker: str, kind: str, to_ccy: str, *, doc: Optional[dict] = None,
                    fx: Optional[Callable[[str], Optional[float]]] = None,
                    overlay: Optional[bool] = None) -> Optional[dict]:
    """{value, baseline, overlay_applied, delta_pct, delta_abs, basis, source} or None.

    The audit trail the owner asked for: what the figure would be on audited
    actuals alone, and how much of it the forward overlay added.
    """
    try:
        e = entry(ticker, kind, doc)
        if not e or review_for(ticker, kind, e)["status"] != "accepted":
            return None
        base = amount_of(e, to_ccy, fx)
        if base is None:
            return None
        out = {"value": base, "baseline": base, "overlay_applied": False,
               "delta_pct": None, "delta_abs": None,
               "basis": e.get("basis") or "actual",
               "period": ((e.get("data") or {}).get("value") or {}).get("period"),
               "source_url": ((e.get("data") or {}).get("value") or {}).get("source_url")}
        if kind == "backlog":
            # What the company calls it (funded, total, RPO, ...) and its cited
            # book-to-bill, for the backlog-coverage DCF. Inside the content hash.
            _bd = e.get("data") or {}
            out["backlog_kind"] = _bd.get("kind")
            _b2b = _bd.get("book_to_bill")
            out["book_to_bill"] = float(_b2b) if isinstance(_b2b, (int, float)) and _b2b > 0 else None
            # Orders booked in the latest completed year, so a book-to-bill can be
            # DERIVED (orders / that year's revenue) when the company states none.
            out["orders"] = amount(_bd.get("orders"), fx or _fx(to_ccy)) if _bd.get("orders") else None
            out["orders_period"] = (_bd.get("orders") or {}).get("period")
        if kind == "fcf_guidance":
            _gd = e.get("data") or {}
            _g_rev = amount(_gd.get("revenue"), fx or _fx(to_ccy)) if _gd.get("revenue") else None
            out["guided_revenue"] = _g_rev
            out["guided_margin"] = (base / _g_rev) if (_g_rev and _g_rev > 0) else None
            out["guidance_range"] = _gd.get("guidance_range")
        if kind == "rate_base":
            # Accepted with the amount: the content hash covers the whole entry,
            # so an edited ROE or equity layer revokes the acceptance too.
            for name in RATE_ORDER_BOUNDS:
                r = ((e.get("data") or {}).get(name) or {}).get("value")
                out[name] = float(r) if isinstance(r, (int, float)) else None
            out["jurisdiction"] = (e.get("data") or {}).get("jurisdiction")
        ov = e.get("overlay") or {}
        want = overlay_enabled() if overlay is None else bool(overlay)
        if not (want and ov and kind not in NO_OVERLAY):
            return out
        if review_for(ticker, kind, e, overlay=True)["status"] != "accepted":
            return out
        pct = ov.get("delta_pct")
        abs_amt = amount({"value": ov.get("delta_value"), "currency": ov.get("currency"),
                          "scale": ov.get("scale"), "source_url": ov.get("source_url"),
                          "quote": ov.get("quote")}, fx or _fx(to_ccy)) if ov.get("delta_value") is not None else None
        value = base * (1.0 + float(pct)) if isinstance(pct, (int, float)) else base
        if abs_amt is not None:
            value += abs_amt
        out.update(value=value, overlay_applied=value != base, delta_pct=pct, delta_abs=abs_amt,
                   overlay_source=ov.get("source_url"), overlay_period=ov.get("period"),
                   overlay_note=ov.get("note"))
        return out
    except Exception:  # noqa: BLE001
        return None


def omit(doc: dict, ticker: str, kind: str, reason: str) -> dict:
    """Record that an input is deliberately not carried for this name.

    A figure that was wrong (a different measure, or none disclosed) is removed
    and the omission stated: an input that is silently absent reads the same as
    one nobody looked for.
    """
    from datetime import datetime, timezone
    key = _key(ticker)
    (doc.setdefault("tickers", {}).get(key) or {}).pop(kind, None)
    doc.setdefault("omitted", {}).setdefault(key, {})[kind] = {
        "reason": reason, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return doc


def ui_summary(*, doc: Optional[dict] = None, reviews: Optional[Callable] = None) -> dict:
    """Rows for the Model Accuracy review panel."""
    d = doc if doc is not None else load()
    reviews = reviews or review_for
    rows = []
    for t, kinds in sorted((d.get("omitted") or {}).items()):
        for kind, o in sorted((kinds or {}).items()):
            rows.append({"ticker": t, "kind": kind, "status": "omitted",
                         "omitted_reason": (o or {}).get("reason"), "checks": [],
                         "remark": RESERVE_MEASURE_REMARK if kind == "pv10" else None})
    for t, kinds in sorted((d.get("tickers") or {}).items()):
        for kind, e in sorted((kinds or {}).items()):
            if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
                continue
            data = canonical_data(e)                   # the gate shows canonical sources
            v = data.get("value") or {}
            try:
                rv = reviews(t, kind, e)
            except Exception:  # noqa: BLE001
                rv = {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False}
            ov = e.get("overlay") or {}
            detail = {k: data.get(k) for k in ("measure", "price_basis", "proved_reserves",
                                                "book_to_bill", "definition")
                      if data.get(k) is not None} | ({"backlog_kind": data["kind"]} if data.get("kind") else {})
            if kind == "pipeline":
                # Wave 5 (owner spec, 2026-09-26): every asset with its cited peak
                # sales, period label, PTRS and basis, so the reviewer sees the
                # option value before it prices.
                detail = {
                    "as_of": data.get("as_of"),
                    "assets": [{
                        "name": a.get("name"), "indication": a.get("indication"), "phase": a.get("phase"),
                        "peak_sales": {"value": (a.get("peak_sales") or {}).get("value"),
                                       "currency": (a.get("peak_sales") or {}).get("currency"),
                                       "scale": (a.get("peak_sales") or {}).get("scale"),
                                       "period_label": (a.get("peak_sales") or {}).get("period"),
                                       "source_url": (a.get("peak_sales") or {}).get("source_url"),
                                       "quote": (a.get("peak_sales") or {}).get("quote")},
                        "launch_year": a.get("launch_year"), "patent_expiry": a.get("patent_expiry"),
                        "ptrs": (a.get("ptrs") or {}).get("value") if a.get("ptrs") else None,
                        "ptrs_basis": a.get("ptrs_basis"),
                    } for a in (data.get("assets") or []) if isinstance(a, dict)],
                }
            if kind == "sotp":
                # Owner, 2026-09-24 (item 3): every figure on the gate carries
                # its period label, so trailing actuals standing in for a
                # forward year are visible before acceptance.
                def _cited(c):
                    c = c or {}
                    return {"value": c.get("value"), "currency": c.get("currency"), "scale": c.get("scale"),
                            "period_label": c.get("period"), "source_url": c.get("source_url"),
                            "quote": c.get("quote")}
                detail = {
                    "fiscal_year": data.get("fiscal_year"),
                    "segments": [{
                        "name": s.get("name"),
                        "revenue": _cited(s.get("revenue_fwd")),
                        "margin": ({"value": (s.get("ebit_margin") or {}).get("value"),
                                    "period_label": (s.get("ebit_margin") or {}).get("period")}
                                   if s.get("ebit_margin") else None),
                        "multiple": {"metric": s.get("multiple_metric"), "low": s.get("multiple_low"),
                                     "high": s.get("multiple_high")},
                        "ev_sales_fallback": ({"low": s.get("ev_sales_low"), "high": s.get("ev_sales_high")}
                                              if s.get("ev_sales_low") is not None else None),
                    } for s in (data.get("segments") or []) if isinstance(s, dict)],
                    "net_cash": _cited(data.get("net_cash")) if data.get("net_cash") else None,
                    "associates": _cited(data.get("associates_investments")) if data.get("associates_investments") else None,
                    "holdco_discount_pct": data.get("holdco_discount_pct"),
                }
            rows.append({
                "ticker": t, "company": e.get("company"), "kind": kind,
                "basis": e.get("basis") or "actual",
                "overlay": ({**ov, "status": reviews(t, kind, e, overlay=True)["status"]}
                            if ov and kind not in NO_OVERLAY else None),
                "overlay_allowed": kind not in NO_OVERLAY,
                "value": v.get("value"), "currency": v.get("currency"), "scale": v.get("scale"),
                "period": (v.get("period") or (data.get("fiscal_year") if kind == "sotp" else None)
                           or (data.get("as_of") if kind == "pipeline" else None)),
                "source_url": v.get("source_url"), "quote": v.get("quote"),
                "detail": detail,
                "remark": RESERVE_MEASURE_REMARK if kind == "pv10" else None,
                "checks": ([c for c in (e.get("checks") or [])
                            if not (kind == "sotp" and c.get("check") == "segments price in the engine")]
                           + [c for c in [ground_truth_check(e.get("value_usd"), e.get("ground_truth"))] if c]
                           + ([sotp_engine_preview(e)] if kind == "sotp" else [])),
                "ground_truth": e.get("ground_truth"), "source": e.get("source"),
                "ok": e.get("ok"),
                "model": e.get("model"), "built_at": e.get("built_at"),
                **rv,
            })
    return {"rows": rows, "kinds": list(KINDS)}

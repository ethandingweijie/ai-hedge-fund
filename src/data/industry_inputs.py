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
from pathlib import Path
from typing import Callable, Optional

STORE_PATH = Path(__file__).resolve().parent / "industry_inputs.json"
KINDS = ("pv10", "backlog", "maintenance_capex")

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
}

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


def _key(ticker: str) -> str:
    from src.tools.ticker_canonical import canonical_ticker
    return canonical_ticker(ticker)


def entry(ticker: str, kind: str, doc: Optional[dict] = None) -> Optional[dict]:
    d = doc if doc is not None else load()
    e = ((d.get("tickers") or {}).get(_key(ticker)) or {}).get(kind)
    return e if isinstance(e, dict) and isinstance(e.get("data"), dict) else None


def content_hash(e: dict) -> str:
    blob = json.dumps(e.get("data") or {}, sort_keys=True, ensure_ascii=False)
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


def amount_of(e: dict, to_ccy: str,
              fx: Optional[Callable[[str], Optional[float]]] = None) -> Optional[float]:
    """The entry's headline amount in full units of `to_ccy`, or None."""
    from src.agents.industry.gemini_params import amount
    return amount((e.get("data") or {}).get("value"), fx or _fx(to_ccy))


def reconcile(kind: str, value: Optional[float], context: dict) -> list[dict]:
    """Checks for one amount (full units, the context's currency) against FMP."""
    checks: list[dict] = []
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
    return checks


# ── reviews ──────────────────────────────────────────────────────────────────

def _ensure_reviews() -> None:
    global _reviews_ready_key
    from src.data import db as _db
    k = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if k != _reviews_ready_key:
        _db.ensure_table(_DDL_REVIEWS)
        _reviews_ready_key = k


def review_for(ticker: str, kind: str, e: dict) -> dict:
    from src.data import db as _db
    _ensure_reviews()
    row = _db.query_one("SELECT status, content_hash, reviewer, reviewed_at FROM industry_input_reviews "
                        "WHERE input_key = ?", [f"{_key(ticker)}|{kind}"])
    if not row:
        return {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False}
    stale = row["content_hash"] != content_hash(e)
    status = row["status"]
    if stale and status == "accepted":
        status = "changed_since_acceptance"
    return {"status": status, "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"], "stale": stale}


def set_review(ticker: str, kind: str, status: str, reviewer: Optional[str], *,
               doc: Optional[dict] = None) -> dict:
    """Record an owner decision. KeyError when there is no entry to review."""
    from datetime import datetime, timezone
    from src.data import db as _db
    if status not in ("accepted", "revoked"):
        raise ValueError(status)
    if kind not in KINDS:
        raise KeyError(kind)
    e = entry(ticker, kind, doc)
    if not e:
        raise KeyError(f"{ticker}|{kind}")
    _ensure_reviews()
    key = f"{_key(ticker)}|{kind}"
    _db.execute("DELETE FROM industry_input_reviews WHERE input_key = ?", [key])
    _db.execute("INSERT INTO industry_input_reviews (input_key, status, content_hash, reviewer, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [key, status, content_hash(e), reviewer, datetime.now(timezone.utc).isoformat()])
    return {"input_key": key, **review_for(ticker, kind, e)}


def accepted_amount(ticker: str, kind: str, to_ccy: str, *, doc: Optional[dict] = None,
                    fx: Optional[Callable[[str], Optional[float]]] = None) -> Optional[float]:
    """The accepted figure in full units of `to_ccy`; None unless the owner
    accepted exactly these figures."""
    try:
        e = entry(ticker, kind, doc)
        if not e or review_for(ticker, kind, e)["status"] != "accepted":
            return None
        return amount_of(e, to_ccy, fx)
    except Exception:  # noqa: BLE001
        return None


def ui_summary(*, doc: Optional[dict] = None, reviews: Optional[Callable] = None) -> dict:
    """Rows for the Model Accuracy review panel."""
    d = doc if doc is not None else load()
    reviews = reviews or review_for
    rows = []
    for t, kinds in sorted((d.get("tickers") or {}).items()):
        for kind, e in sorted((kinds or {}).items()):
            if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
                continue
            data = e["data"]
            v = data.get("value") or {}
            try:
                rv = reviews(t, kind, e)
            except Exception:  # noqa: BLE001
                rv = {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False}
            rows.append({
                "ticker": t, "company": e.get("company"), "kind": kind,
                "value": v.get("value"), "currency": v.get("currency"), "scale": v.get("scale"),
                "period": v.get("period"), "source_url": v.get("source_url"), "quote": v.get("quote"),
                "detail": {k: data.get(k) for k in ("measure", "price_basis", "proved_reserves",
                                                     "book_to_bill", "definition")
                           if data.get(k) is not None} | ({"backlog_kind": data["kind"]} if data.get("kind") else {}),
                "checks": e.get("checks") or [], "ok": e.get("ok"),
                "model": e.get("model"), "built_at": e.get("built_at"),
                **rv,
            })
    return {"rows": rows, "kinds": list(KINDS)}

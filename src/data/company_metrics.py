"""One KPI vector per company, shared across its listings.

A company has one income statement. The framework KPIs the composite reads --
operating margin, revenue growth, segment growth, FCF margin -- are properties
of that statement, not of the venue the shares trade on. They are extracted by
an LLM per run, so nothing was holding the two readings together: on
2026-09-16, seven minutes apart, Alibaba's GAAP operating margin came back as
+10.0% for 09988.HK and -0.3% for BABA. That single field took the composite
from 1.266 to 1.0363 and opened an 18.7% gap between the two listings' blended
IVs ($209.03 against $176.05 per ADS) on a price that agreed to 0.6%.

This module binds the vector to ``company_key`` instead. The first run of a
company inside the freshness window establishes its KPI vector; a later run of
*the other listing* adopts that vector rather than re-extracting it. Listing
specific work stays where it belongs -- share parity and spot FX -- and the
composite becomes a deterministic function of the company.

Scope is deliberately narrow: only tickers in ``DUAL_LISTINGS`` can adopt, and
only from a run of a DIFFERENT listing of the same company. A single-listed
company keeps its own extraction on every run, exactly as before.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import src.data.db as _db

#: How far back a sibling run may be and still supply the vector. Shorter than
#: a reporting quarter, so a fresh set of results is re-extracted rather than
#: inherited from the previous period.
DEFAULT_MAX_AGE_DAYS = 35

#: State buckets a framework KPI vector can live in.
_METRIC_BUCKETS = ("framework_metrics_all", "framework_metrics",
                   "insurance_metrics_all", "bank_metrics_all")


def _company_of(ticker: str) -> Optional[str]:
    try:
        from src.data.dual_listings import company_key
        return company_key(ticker)
    except Exception:
        return None


def _siblings(ticker: str) -> list[str]:
    """Other listings of the same company; empty for a single-listed name."""
    try:
        from src.data.dual_listings import listings_for
        return [t for t in (listings_for(ticker) or [])
                if t.upper() != (ticker or "").upper()]
    except Exception:
        return []


def _metrics_from_payload(payload: Any, ticker: str) -> Optional[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    for bucket_key in _METRIC_BUCKETS:
        bucket = data.get(bucket_key)
        if not isinstance(bucket, dict):
            continue
        metrics = bucket.get(ticker) or bucket.get(ticker.upper())
        if isinstance(metrics, dict) and any(
                isinstance(k, str) and not k.startswith("_") and k != "evidence"
                for k in metrics):
            return metrics
    return None


def latest_sibling_metrics(
    ticker: str,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """Most recent KPI vector archived for another listing of this company.

    Returns ``(metrics, source_ticker, run_at)``, or ``(None, None, None)``
    when the company is single-listed, nothing is archived inside the window,
    or the lookup fails. Every failure path degrades to "extract as usual".
    """
    siblings = _siblings(ticker)
    if not siblings:
        return None, None, None
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=max_age_days)).isoformat()
    placeholders = ", ".join(["?"] * len(siblings))
    sql = (f"SELECT ticker, run_at, full_result_json FROM web_runs "
           f"WHERE ticker IN ({placeholders}) AND run_at >= ? "
           f"AND full_result_json IS NOT NULL "
           f"ORDER BY run_at DESC LIMIT 5")
    params: list = [*siblings, cutoff]
    try:
        if _db.is_postgres():
            rows = [(r["ticker"], r["run_at"], r["full_result_json"])
                    for r in _db.query(sql, params)]
        else:
            rows = [tuple(r) for r in _db.query(sql, params)]
    except Exception:
        return None, None, None
    for src_ticker, run_at, payload in rows or []:
        metrics = _metrics_from_payload(payload, src_ticker)
        if metrics:
            return metrics, src_ticker, str(run_at)
    return None, None, None


def resolve_company_metrics(
    ticker: str,
    metrics: Optional[dict],
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> tuple[dict, Optional[str]]:
    """Return the KPI vector this ticker should be scored on, plus a flag.

    Adopts a sibling listing's archived vector when one exists inside the
    window; otherwise returns ``metrics`` untouched. The adopted copy carries
    ``_metrics_company`` / ``_metrics_source`` so a run records whose numbers
    it used, and any private keys the current run computed for itself
    (``_z_scores``, completeness annotations) are left to be recomputed.
    """
    current = dict(metrics or {})
    shared, source, run_at = latest_sibling_metrics(
        ticker, max_age_days=max_age_days)
    if not shared:
        return current, None
    company = _company_of(ticker) or ticker
    adopted = {k: v for k, v in shared.items() if not str(k).startswith("_")}
    if not adopted:
        return current, None
    changed = sorted(
        k for k in set(adopted) | {k for k in current if not str(k).startswith("_")}
        if adopted.get(k) != current.get(k) and k != "evidence"
    )
    adopted["_metrics_company"] = company
    adopted["_metrics_source"] = f"{source} {str(run_at)[:19]}"
    if not changed:
        return adopted, None
    detail = ", ".join(
        f"{k} {current.get(k)!r}→{adopted.get(k)!r}" for k in changed[:4])
    return adopted, (
        f"Framework KPIs taken from {company} ({source}, "
        f"{str(run_at)[:10]}) so both listings score the same company: {detail}"
    )


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "latest_sibling_metrics",
    "resolve_company_metrics",
]

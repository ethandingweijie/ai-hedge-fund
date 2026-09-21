"""Reconstruct yearly basket medians for every comps basket (Phase 2, Step 1).

`regional_comps` is upserted in place, so the weekly refresh never kept a
series. `regional_comps_history` now keeps every weekly median (source
"refresh"), but the weekly job sees only one year of each member's earnings and
so cannot form the THROUGH-CYCLE multiples the dynamic multiples engine fits
on. This module rebuilds them from each member's annual key-metrics and income
statements, and runs quarterly in production (owner, 2026-09-21) so each new
fiscal year enters the series as its filings land.

What it can and cannot give you:

  * DEPTH -- FMP returns five annual rows per company on this plan.
  * SURVIVORSHIP -- membership is TODAY's basket; companies that left the
    universe are absent from past years. Rows carry source="backfill" so a
    reader can tell them from a measured refresh, which always wins a tie.
  * CALENDAR -- members are bucketed by the calendar year of their fiscal year
    end, so the current year's bucket holds only early filers until the year
    closes. The engine does not fit on an incomplete year.

A re-run REPLACES earlier backfill rows (late filers change a year's median);
it never touches a "refresh" row.
"""
from __future__ import annotations

import logging
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.data import regional_comps as rc

logger = logging.getLogger(__name__)

#: Markets the quarterly job covers -- the three FMP-served markets whose
#: universes the backfill has been validated on. Override with a comma list.
DEFAULT_MARKETS = ("US", "HKSE", "SES")
#: A run within this many days counts as this quarter's (same window as the
#: 100-Q quarterly backstop).
IDEMPOTENCY_DAYS = 80


_S = "https://financialmodelingprep.com/stable"

#: key-metrics (annual) field -> comps field
_KM_FIELDS = {"evToEBITDA": "ev_ebitda", "returnOnInvestedCapital": "roic",
              "evToOperatingCashFlow": "ev_ocf", "evToSales": "ev_revenue"}

#: The dynamic multiples engine covers EVERY industry (owner, 2026-09-21);
#: energy is only the first calibrated set. `--preset energy` limits a run to
#: it for a quick check.
ENERGY_KEYS = sorted(rc.INDUSTRY_FAMILIES["Oil, Gas & Coal (family)"]) + [
    "Oil, Gas & Coal (family)", "Chemicals", "Chemicals - Specialty"]


def member_history(symbol: str) -> dict[str, dict[str, float]]:
    """{calendar_year: {comps_field: value}} from annual key-metrics.

    Also derives `ev_ebitda_norm` -- EV in the year over the member's MEAN
    EBITDA across the window. The TTM multiple of a cyclical moves inversely
    with its own earnings (refining large cohort: 3.17x in 2022 at 16.5% ROIC,
    8.35x in 2021 at 5.7%), because a peak year inflates the denominator. That
    variation is the earnings cycle, not a re-rating, and fitting the dynamic
    multiple's factors to it would reproduce peak anchoring. The normalised
    multiple holds the denominator at through-cycle earnings, so what is left
    moving is the price the market puts on them -- the same basis the owner's
    through-cycle bands are written on.
    """
    rows = rc._fmp_get(f"{_S}/key-metrics",
                       {"symbol": symbol, "period": "annual", "limit": 10}, api_key=None)
    inc = rc._fmp_get(f"{_S}/income-statement",
                      {"symbol": symbol, "period": "annual", "limit": 10}, api_key=None)
    inc = inc if isinstance(inc, list) else []
    # Both sides of a through-cycle ratio must be in one currency. FMP reports
    # key-metrics EV in the statement currency (CNOOC and Tencent both RMB on
    # both sides; DBS SGD), so they agree today -- but a HKD quote against RMB
    # statements would put an FX rate inside the multiple, so the derived
    # fields are skipped for any company whose two sources disagree.
    km_ccy = {str(r.get("reportedCurrency") or "") for r in (rows if isinstance(rows, list) else [])}
    inc_ccy = {str(r.get("reportedCurrency") or "") for r in inc}
    ccy_ok = (len(km_ccy) == 1 and km_ccy == inc_ccy)
    ebitda = {str(r.get("date") or "")[:4]: rc._safe_float(r.get("ebitda")) for r in inc}
    eb_vals = [v for v in ebitda.values() if v is not None and v > 0]
    eb_mean = (sum(eb_vals) / len(eb_vals)) if len(eb_vals) >= 3 else None
    # The same normalisation for industries that anchor on earnings rather than
    # EBITDA (banks, insurers): market cap over mean net income. A mean that is
    # not positive has no multiple, rather than a negative one.
    ni_vals = [rc._safe_float(r.get("netIncome")) for r in inc]
    ni_vals = [v for v in ni_vals if v is not None]
    ni_mean = (sum(ni_vals) / len(ni_vals)) if len(ni_vals) >= 3 else None
    if ni_mean is not None and ni_mean <= 0:
        ni_mean = None
    out: dict[str, dict[str, float]] = {}
    for r in rows if isinstance(rows, list) else []:
        yr = str(r.get("date") or "")[:4]
        if not yr.isdigit():
            continue
        vals = {cf: rc._safe_float(r.get(kf)) for kf, cf in _KM_FIELDS.items()}
        ev = rc._safe_float(r.get("enterpriseValue"))
        if ccy_ok and ev and ev > 0 and eb_mean:
            vals["ev_ebitda_norm"] = ev / eb_mean
        mc = rc._safe_float(r.get("marketCap"))
        if ccy_ok and mc and mc > 0 and ni_mean:
            vals["pe_norm"] = mc / ni_mean
        out[yr] = {k: v for k, v in vals.items() if v is not None}
    return out


def build(exchange: str = "US", preset: str | None = None) -> list[dict]:
    # Same exclusion as the live refresh: a Singapore listing whose primary
    # line is in Hong Kong belongs to the HK baskets, not SG's.
    exclude: set[str] = set()
    if exchange == "SES":
        exclude = {rc.normalize_name(r["name"]) for r in rc.fetch_universe("HKSE")}
    universe = rc.dedupe_universe(rc.fetch_universe(exchange), exclude_names=exclude)
    industry, sector, _syms = rc.build_baskets(universe)
    levels = [("industry", industry, rc.MIN_INDUSTRY_PEERS),
              ("sector", sector, rc.MIN_SECTOR_PEERS)]
    if preset == "energy":
        levels = [("industry", {k: v for k, v in industry.items() if k in ENERGY_KEYS},
                   rc.MIN_INDUSTRY_PEERS)]
    cache: dict[str, dict] = {}
    rows: list[dict] = []
    for level, baskets, floor in levels:
        rows.extend(_basket_rows(level, baskets, floor, cache))
    logger.info("[comps_history] %s: %d members fetched across %d baskets",
                exchange, len(cache), sum(len(b) for _, b, _ in levels))
    return rows


def _basket_rows(level: str, baskets: dict, floor: int, cache: dict) -> list[dict]:
    rows: list[dict] = []
    for key, members in sorted(baskets.items()):
        for cohort, group in rc.split_cohorts(members).items():
            by_year: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
            for m in group:
                sym = m["symbol"]
                if sym not in cache:
                    cache[sym] = member_history(sym)
                for yr, vals in cache[sym].items():
                    for f, v in vals.items():
                        by_year[yr][f].append(v)
            for yr in sorted(by_year):
                for f, raw_vals in by_year[yr].items():
                    vals = rc._clean(f, raw_vals)
                    if len(vals) < floor:
                        continue
                    rows.append({"level": level, "key": key, "cohort": cohort,
                                 "field": f, "value": round(statistics.median(vals), 6),
                                 "peer_count": len(vals), "as_of": f"{yr}-12-31"})
    return rows


def run_market(exchange: str, preset: Optional[str] = None) -> dict:
    """Backfill one market and write it. Returns a small summary."""
    rows = build(exchange, preset)
    written = 0
    for as_of in sorted({r["as_of"] for r in rows}):
        written += rc.save_history(exchange, [r for r in rows if r["as_of"] == as_of],
                                   as_of=as_of, source="backfill", replace=True)
    return {"exchange": exchange, "medians": len(rows), "written": written}


def markets() -> tuple[str, ...]:
    raw = os.environ.get("COMPS_HISTORY_MARKETS", "").strip()
    return tuple(m.strip() for m in raw.split(",") if m.strip()) or DEFAULT_MARKETS


def already_ran_this_quarter() -> bool:
    """True when a backfill row was recorded within IDEMPOTENCY_DAYS."""
    try:
        rc._ensure_table()
        from src.data import db as _db
        row = _db.query_one("SELECT MAX(recorded_at) AS m FROM regional_comps_history "
                            "WHERE source = ?", ["backfill"])
        last = row["m"] if row else None
        if not last:
            return False
        ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts < timedelta(days=IDEMPOTENCY_DAYS)
    except Exception as exc:                                   # noqa: BLE001
        logger.warning("[comps_history] gate check failed: %s", exc)
        return False


def run_quarterly_backfill(force: bool = False) -> Optional[dict]:
    """The scheduled entry point. None when this quarter already ran."""
    if not force and already_ran_this_quarter():
        logger.info("[comps_history] backfill already ran this quarter -- skipping")
        return None
    out: dict[str, dict] = {}
    for m in markets():
        try:
            out[m] = run_market(m)
            logger.info("[comps_history] %s: %s", m, out[m])
        except Exception as exc:                               # noqa: BLE001
            logger.exception("[comps_history] %s failed: %s", m, exc)
            out[m] = {"error": str(exc)[:200]}
    return out

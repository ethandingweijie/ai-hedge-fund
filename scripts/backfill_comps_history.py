"""Reconstruct yearly basket medians for every comps basket (Phase 2, Step 1).

`regional_comps` kept only today's median until `regional_comps_history`
existed, so the dynamic multiples engine had nothing to backtest against. This
rebuilds the series backwards from each member's annual key-metrics.

What it can and cannot give you, stated here so nobody reads more into it:

  * DEPTH -- FMP returns five annual key-metrics rows per company on this plan
    (FY2021-FY2025). That is five points per basket, not ten years.
  * SURVIVORSHIP -- membership is TODAY's basket. A company that left the
    universe (delisted, acquired, fell below the cap floor) is absent from
    every past year, so past medians are drawn from the survivors. Rows are
    written with source="backfill" and every reader can tell them apart from
    a measured refresh.
  * CALENDAR -- members are bucketed by the calendar year of their fiscal
    year end, so a June year end and a December year end share a bucket.

The same plausibility bands and peer floors as the live refresh apply, so a
backfilled median is comparable to a measured one in construction if not in
provenance.

    python scripts/backfill_comps_history.py            # dry run: prints the series
    python scripts/backfill_comps_history.py --commit   # writes source="backfill"
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=False)

from src.data import regional_comps as rc  # noqa: E402

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
    print(f"[backfill] {len(cache)} members fetched across "
          f"{sum(len(b) for _, b, _ in levels)} baskets", flush=True)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--exchange", default="US")
    ap.add_argument("--preset", choices=["energy"], default=None)
    ap.add_argument("--quiet", action="store_true", help="skip the per-series print")
    a = ap.parse_args()
    rows = build(a.exchange, a.preset)
    by = defaultdict(list)
    for r in rows:
        by[(r["key"], r["cohort"], r["field"])].append(r)
    for (key, cohort, field), rs in sorted(by.items()):
        if a.quiet or field not in ("ev_ebitda", "ev_ebitda_norm", "pe_norm", "roic"):
            continue
        series = "  ".join(f"{r['as_of'][:4]}:{r['value']:.3f}(n={r['peer_count']})" for r in rs)
        print(f"{key[:34]:36s}{cohort:6s}{field:15s}{series}")
    print(f"\n{len(rows)} basket-year medians")
    if a.commit:
        n = 0
        for as_of in sorted({r["as_of"] for r in rows}):
            n += rc.save_history(a.exchange, [r for r in rows if r["as_of"] == as_of],
                                 as_of=as_of, source="backfill")
        print(f"written: {n} rows, source=backfill")
    else:
        print("dry run -- pass --commit to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backfill one comps field for the members of selected labels, without a full weekly refresh.

Wave 8b step 3 (owner decision A, 2026-09-27): the trailing synthetic FFO multiple, price / (net income +
D&A), for every member of the REIT-labelled industry baskets, so the REIT profiles' P/FFO leg can read a
live cohort. One cash-flow call per member; the market cap is the member's stored one when the store has
it, else one key-metrics call. Medians are recomputed for the touched labels (cohorts all and large, the
band in `regional_comps._BANDS`) and for any curated profile basket whose members were touched.

    python scripts/backfill_comps_field.py --exchange US --field p_ffo --prefix "REIT"            # dry run
    python scripts/backfill_comps_field.py --exchange US --field p_ffo --prefix "REIT" --commit

Read-only apart from the comps store it is asked to write. DATABASE_URL is dropped, so this touches the
LOCAL store only; production runs its own weekly job, which carries the field from the next refresh.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
os.environ.pop("DATABASE_URL", None)

from src.data import db as _db  # noqa: E402
from src.data import regional_comps as rc  # noqa: E402


def _members(exchange: str, prefix: str) -> list[dict]:
    rows = _db.query(
        "SELECT exchange, level, key, cohort, symbol, name, market_cap, metrics_json, computed_at "
        "FROM regional_comps_members WHERE exchange = ? AND level = 'industry' AND key LIKE ?",
        [exchange, prefix + "%"]) or []
    return [dict(r) for r in rows]


def _fetch_field(field: str, symbol: str, market_cap) -> float | None:
    if field == "p_ffo":
        mc = market_cap
        if not mc:
            km = rc._fmp_get(f"{rc._STABLE}/key-metrics-ttm", {"symbol": symbol}, api_key=None)
            mc = rc._safe_float(km[0].get("marketCap")) if isinstance(km, list) and km else None
        return rc.fetch_p_ffo(symbol, mc)
    raise SystemExit(f"no fetcher for field {field!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="US")
    ap.add_argument("--field", default="p_ffo")
    ap.add_argument("--prefix", default="REIT")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    if a.field not in rc._BANDS:
        raise SystemExit(f"{a.field!r} is not a comps field")
    lo, hi = rc._BANDS[a.field]
    rows = _members(a.exchange, a.prefix)
    symbols = sorted({r["symbol"] for r in rows})
    print(f"{len(rows)} member rows, {len(symbols)} symbols under {a.exchange} {a.prefix}*")
    values: dict[str, float | None] = {}
    for i, s in enumerate(symbols, 1):
        mc = next((r["market_cap"] for r in rows if r["symbol"] == s and r["market_cap"]), None)
        try:
            values[s] = _fetch_field(a.field, s, mc)
        except Exception as exc:  # noqa: BLE001
            values[s] = None
            print(f"  {s}: fetch failed {type(exc).__name__}: {exc}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(symbols)}")
    got = {s: v for s, v in values.items() if v is not None}
    inband = {s: v for s, v in got.items() if lo <= v <= hi}
    print(f"fetched {len(got)}, in band {len(inband)} (band {lo}-{hi})")
    for s in sorted(inband, key=lambda k: -inband[k])[:10]:
        print(f"  {s:8s} {a.field} {inband[s]:.1f}x")

    # medians per (key, cohort) over the members that carry the field, with the store's cohorts
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        by_key.setdefault((r["key"], r["cohort"]), []).append(r)
    medians = []
    for (key, cohort), mem in sorted(by_key.items()):
        vals = [inband[r["symbol"]] for r in mem if r["symbol"] in inband]
        floor = rc.MIN_INDUSTRY_PEERS
        if len(vals) >= floor:
            medians.append((key, cohort, round(statistics.median(vals), 6), len(vals),
                            min((r["market_cap"] or 0) for r in mem)))
    for key, cohort, med, n, _ in medians:
        print(f"  median {key:32s} {cohort:5s} {a.field} {med:.2f}x (n={n})")
    if not a.commit:
        print("dry run; --commit writes the member cells and the medians")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    written_cells = 0
    for r in rows:
        v = values.get(r["symbol"])
        if v is None:
            continue
        m = json.loads(r["metrics_json"] or "{}")
        m[a.field] = {"value": v, "in_band": (not math.isnan(v)) and lo <= v <= hi}
        _db.execute("UPDATE regional_comps_members SET metrics_json = ? WHERE exchange = ? AND level = ? AND key = ? AND cohort = ? AND symbol = ?",
                    [json.dumps(m), r["exchange"], r["level"], r["key"], r["cohort"], r["symbol"]])
        written_cells += 1
    for key, cohort, med, n, min_mc in medians:
        _db.execute("DELETE FROM regional_comps WHERE exchange = ? AND level = 'industry' AND key = ? AND cohort = ? AND field = ?",
                    [a.exchange, key, cohort, a.field])
        _db.execute("INSERT INTO regional_comps (exchange, level, key, cohort, field, value, peer_count, min_market_cap, computed_at) "
                    "VALUES (?, 'industry', ?, ?, ?, ?, ?, ?, ?)", [a.exchange, key, cohort, a.field, med, n, min_mc, now])
    print(f"wrote {written_cells} member cells and {len(medians)} medians for {a.field}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

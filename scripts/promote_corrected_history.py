"""Promote the corrected through-cycle history into another database (Step A).

The through-cycle multiple's basis was corrected on 2026-09-21 (75b797f):
EV / (mean margin x revenue), not EV / mean EBITDA level. History rebuilt on
the corrected basis in the SOURCE store (the local development database) is
copied into the TARGET (production) with replace, and every target row the
corrected rebuild did NOT reproduce is relabelled `backfill_superseded`: kept
for the audit, never read (see regional_comps.load_history). Nothing is
deleted.

Only rows written at or after --since are taken from the source: the corrected
rebuild began after the fix, and the old-basis rows in the source were all
written before it.

    python scripts/promote_corrected_history.py --target-url <prod proxy url>            # dry run
    python scripts/promote_corrected_history.py --target-url <prod proxy url> --commit
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)            # the SOURCE is the local store

from src.data import db as _db  # noqa: E402
from src.data.regional_comps import _HISTORY_DDL, _HISTORY_INDEX  # noqa: E402

COLS = ["exchange", "level", "key", "cohort", "field", "value", "peer_count",
        "as_of", "source", "recorded_at"]
NORM_FIELDS = ("ev_ebitda_norm", "pe_norm")
KEYCOLS = ("exchange", "level", "key", "cohort", "field", "as_of")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-url", required=True)
    ap.add_argument("--since", default="2026-09-21T09:00")
    ap.add_argument("--markets", default="US,HKSE,SES")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    markets = [m.strip() for m in a.markets.split(",") if m.strip()]
    assert not _db.is_postgres(), "the source must be the local store"

    ph = ",".join("?" * len(markets))
    src = [dict(r) for r in _db.query(
        f"SELECT {', '.join(COLS)} FROM regional_comps_history WHERE source = 'backfill' "
        f"AND recorded_at >= ? AND exchange IN ({ph})", [a.since, *markets])]
    by_market = {m: sum(1 for r in src if r["exchange"] == m) for m in markets}
    print(f"source: {len(src)} corrected rows since {a.since} {by_market}")
    missing = [m for m, n in by_market.items() if n == 0]
    if missing:
        print(f"REFUSING: no corrected rows for {missing} -- the rebuild has not finished")
        return 1
    corrected_keys = {tuple(r[c] for c in KEYCOLS) for r in src}

    with psycopg.connect(a.target_url, autocommit=False) as con:
        con.execute(_HISTORY_DDL)
        con.execute(_HISTORY_INDEX)
        tph = ",".join(["%s"] * len(markets))
        target_norm = con.execute(
            f"SELECT {', '.join(KEYCOLS)} FROM regional_comps_history WHERE source = 'backfill' "
            f"AND field IN (%s, %s) AND exchange IN ({tph})",
            [*NORM_FIELDS, *markets]).fetchall()
        orphans = [t for t in target_norm if tuple(t) not in corrected_keys]
        print(f"target: {len(target_norm)} through-cycle backfill rows; "
              f"{len(orphans)} not reproduced by the corrected rebuild -> superseded")
        if not a.commit:
            con.rollback()
            print("dry run -- nothing written; pass --commit")
            return 0
        with con.cursor() as cur:
            cur.executemany(
                f"INSERT INTO regional_comps_history ({', '.join(COLS)}) "
                f"VALUES ({', '.join(['%s'] * len(COLS))}) "
                "ON CONFLICT (exchange, level, key, cohort, field, as_of, source) DO UPDATE SET "
                "value = excluded.value, peer_count = excluded.peer_count, "
                "recorded_at = excluded.recorded_at",
                [[r[c] for c in COLS] for r in src])
            cur.executemany(
                "UPDATE regional_comps_history SET source = 'backfill_superseded' "
                "WHERE source = 'backfill' AND exchange = %s AND level = %s AND key = %s "
                "AND cohort = %s AND field = %s AND as_of = %s",
                [list(t) for t in orphans])
        con.commit()
        for r in con.execute("SELECT exchange, source, COUNT(*) FROM regional_comps_history "
                             "GROUP BY exchange, source ORDER BY exchange, source").fetchall():
            print("   ", r)
    print("WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

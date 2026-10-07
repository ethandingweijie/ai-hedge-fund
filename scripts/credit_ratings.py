"""Agency credit ratings: research, list, accept, revoke (owner, 2026-10-08).

    python scripts/credit_ratings.py --research AMGN,GILD        # research now (overwrites unless the owner decided)
    python scripts/credit_ratings.py --research-us-prod           # every US ticker run in the last 120 days
    python scripts/credit_ratings.py --list
    python scripts/credit_ratings.py --accept KMI [--rating BBB]  # price a PROPOSED entry (optionally corrected)
    python scripts/credit_ratings.py --revoke KMI                 # back to the rating implied by interest cover

Against production: set DATABASE_URL to the proxy URL first.
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import dotenv_values
    for _k, _v in dotenv_values(Path(__file__).resolve().parents[1] / ".env.local").items():
        if _v and _k not in os.environ and _k != "DATABASE_URL":
            os.environ[_k] = _v
except Exception:                                          # noqa: BLE001
    pass

from src.data import credit_ratings as cr  # noqa: E402


def _row(rec: dict) -> str:
    ags = ", ".join(f"{a} {f.get('rating')}{'*' if f.get('confirmed_by') else ''}" for a, f in (rec.get("agencies") or {}).items())
    return f"{rec['ticker']:<6} {rec.get('status') or '':<9} {rec.get('rating') or '-':<5} {ags:<40} {rec.get('basis') or ''}"


def _us_prod_tickers() -> list[str]:
    from src.data import db
    rows = db.query("SELECT DISTINCT ticker FROM web_runs WHERE is_checkpoint = 0 AND run_at > ?", [_since()])
    return sorted({r["ticker"].upper() for r in rows if cr.is_us_ticker(r["ticker"])})


def _since() -> str:
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research")
    ap.add_argument("--research-us-prod", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--accept")
    ap.add_argument("--revoke")
    ap.add_argument("--rating")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if a.research or a.research_us_prod:
        ts = [t.strip().upper() for t in (a.research or "").split(",") if t.strip()] or _us_prod_tickers()
        print(f"researching {len(ts)}: {' '.join(ts)}")

        def one(t):
            rec = cr.research(t, cr._company(t))
            cr.save(rec)
            return cr.stored(t) or rec
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for rec in ex.map(one, ts):
                print(_row(rec), flush=True)
    if a.accept:
        print(_row(cr.set_status(a.accept, "ACCEPTED", "owner", rating=a.rating)))
    if a.revoke:
        print(_row(cr.set_status(a.revoke, "REVOKED", "owner")))
    if a.list:
        from src.data import db
        cr._ensure()
        for r in db.query("SELECT ticker FROM credit_ratings ORDER BY ticker"):
            print(_row(cr.stored(r["ticker"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

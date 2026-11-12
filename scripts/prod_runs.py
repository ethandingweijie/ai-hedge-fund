"""Submit and wait for production analysis runs (owner tooling; no secrets are written anywhere).

    python scripts/prod_runs.py submit F GM DOW            # POST /analysis/run per ticker (queue mode)
    python scripts/prod_runs.py wait 2026-11-12T03:00:00 F GM   # poll web_runs for each ticker's final row after T (UTC)
    python scripts/prod_runs.py age F GM                    # age recent rows 45 min so the 30-min endpoint cache misses
    python scripts/prod_runs.py dump 2026-11-12T03:00:00 OUTDIR F GM   # save each final payload as OUTDIR/<T>.json

The JWT is minted in memory from the web service's JWT_SECRET_KEY (railway CLI). DATABASE_URL must point at the
Postgres public proxy (see the railway topology note). Model: RUN_MODEL env, default qwen3.8-flash.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

PROD = "https://ai-hedge-fund-production-7131.up.railway.app"
RAILWAY = ["-p", "050389fd-bcb9-490c-899d-e6a32214ab3e", "-e", "51c55952-3eaa-4827-a1ed-308b771e8a25"]


def _token() -> str:
    import jwt
    out = subprocess.run(["railway", "variables", "-s", "ai-hedge-fund", *RAILWAY, "--kv"], capture_output=True,
                         text=True, check=True, shell=(os.name == "nt")).stdout
    secret = next(l.split("=", 1)[1] for l in out.splitlines() if l.startswith("JWT_SECRET_KEY="))
    return jwt.encode({"sub": "1", "email": "owner", "name": "owner", "avatar": "",
                       "exp": int(time.time()) + 6 * 3600}, secret, algorithm="HS256")


def submit(tickers):
    tok, model = _token(), os.getenv("RUN_MODEL", "qwen3.8-flash")
    for t in tickers:
        try:
            r = requests.post(f"{PROD}/analysis/run", json={"ticker": t, "model": model},
                              headers={"Authorization": f"Bearer {tok}"}, stream=True, timeout=(15, 20))
            first = []
            for line in r.iter_lines(decode_unicode=True):
                if line:
                    first.append(line)
                if len(first) >= 2:
                    break
            r.close()
            print(t, r.status_code, first[:2])
        except Exception as exc:                           # noqa: BLE001 -- the stream closing early is normal
            print(t, "submit stream closed:", type(exc).__name__)


def _db():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"])


def finals(since: str, tickers):
    with _db() as c, c.cursor() as cur:
        cur.execute("SELECT ticker, run_id, run_at FROM web_runs WHERE is_checkpoint = 0 AND run_at > %s "
                    "AND ticker = ANY(%s) ORDER BY run_at", [since, list(tickers)])
        return {t: (rid, at) for t, rid, at in cur.fetchall()}


def wait(since: str, tickers, timeout_s: int = 3600):
    pending, t0 = set(tickers), time.time()
    while pending and time.time() - t0 < timeout_s:
        for t, (rid, at) in finals(since, pending).items():
            print("DONE", t, rid, at, flush=True)
            pending.discard(t)
        if pending:
            time.sleep(30)
    print("PENDING", sorted(pending))


def age(tickers):
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    with _db() as c, c.cursor() as cur:
        cur.execute("UPDATE web_runs SET run_at = to_char(run_at::timestamp - interval '45 minutes', "
                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US') WHERE ticker = ANY(%s) AND run_at > %s", [list(tickers), since])
        print("aged", cur.rowcount)
        c.commit()


def dump(since: str, outdir: str, tickers):
    os.makedirs(outdir, exist_ok=True)
    with _db() as c, c.cursor() as cur:
        for t in tickers:
            cur.execute("SELECT full_result_json FROM web_runs WHERE is_checkpoint = 0 AND run_at > %s AND ticker = %s "
                        "ORDER BY run_at DESC LIMIT 1", [since, t])
            row = cur.fetchone()
            if not row:
                print(t, "no final row")
                continue
            p = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            with open(os.path.join(outdir, f"{t}.json"), "w", encoding="utf-8") as f:
                json.dump(p, f)
            print(t, "saved")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "submit":
        submit(args)
    elif cmd == "wait":
        wait(args[0], args[1:])
    elif cmd == "age":
        age(args)
    elif cmd == "dump":
        dump(args[0], args[1], args[2:])

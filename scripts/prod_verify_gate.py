"""Production verification for a valuation-engine change (plan: Verification).

Push -> confirm services -> re-run the basket. The first two are manual; this
is the third. It triggers real pipeline runs on prod and checks the deployed
engine emits what the golden baseline says it should.

Why not just trust the golden suite: the suite replays RECORDED prod state in a
subprocess on this machine. It proves the engine in the working tree behaves.
It cannot prove the container Railway is serving contains that engine. Only a
live run against the deployed artefact closes that gap.

Scope is deliberately a targeted pair, not all fourteen names. A full live
basket is ~12 min/name of paid LLM research; the two defaults below are the
names the defect table cites for Phase 1.1, one that must fire and one that
must stay quiet, which is the whole of what "the gate works in prod" means.
Retarget it per phase with --tickers / --gate-id / --flag-prefix.

  python scripts/prod_verify_gate.py
  python scripts/prod_verify_gate.py --tickers BN4.SI
  python scripts/prod_verify_gate.py --tickers MELI --expect-quiet MELI
  python scripts/prod_verify_gate.py --not-before 2026-09-16T16:30:00

The JWT secret is read from `railway variables` into memory and never printed,
echoed into a file, or committed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

import jwt

BASE = "https://ai-hedge-fund-production-7131.up.railway.app"

#: Railway ids for project modest-vitality / env production / service
#: ai-hedge-fund (web). The CLI is linked to a DIFFERENT project on this
#: machine, so the flags are mandatory rather than a convenience.
RAILWAY_PROJECT = "050389fd-bcb9-490c-899d-e6a32214ab3e"
RAILWAY_ENV = "51c55952-3eaa-4827-a1ed-308b771e8a25"
RAILWAY_SERVICE = "abf51694-e23e-4b52-bc01-1f3c38cf4851"

#: BN4.SI is defect 1's named ticker: a +20.3% one-year consensus jump held
#: flat for ten years against a -2.5% five-year CAGR. The gate must fire.
#: U96.SI is the second cited name (+22%). MELI is the control — ~42% CAGR
#: sitting near consensus, so a gate that fires there is a gate that caps
#: everything rather than divergences.
DEFAULT_TICKERS = ["BN4.SI", "U96.SI"]

#: Defaults are Phase 1.1's. Override per phase — every later item in the
#: valuation-hardening plan lands its own gate id and flag text.
GATE_ID = "GATE_GROWTH_CAGR_DIVERGENCE"
FLAG_PREFIX = "CAGR-divergence gate:"

_PER_RUN_TIMEOUT = 1500  # deep research + valuation can take 10-12 min


def _railway_bin() -> str:
    """`railway` is an npm shim: a POSIX script plus a .cmd beside it.

    subprocess on Windows cannot exec the script, so resolve the .cmd
    explicitly. shutil.which finds it because PATHEXT includes .CMD.
    """
    import shutil
    for cand in ("railway.cmd", "railway.exe", "railway"):
        found = shutil.which(cand)
        if found:
            return found
    raise SystemExit("railway CLI not on PATH")


def _secret() -> str:
    env = os.environ.get("JWT_SECRET_KEY")
    if env and len(env) >= 32:
        return env
    out = subprocess.run(
        [_railway_bin(), "variables", "-p", RAILWAY_PROJECT,
         "-e", RAILWAY_ENV, "-s", RAILWAY_SERVICE, "--kv"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("JWT_SECRET_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(val) >= 32:
                return val
    raise SystemExit("JWT_SECRET_KEY not found in railway variables")


def _headers(secret: str) -> dict:
    token = jwt.encode({"sub": "1", "exp": int(time.time()) + 7200},
                       secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


def _get(path: str, h: dict, timeout: int = 60):
    req = urllib.request.Request(BASE + path, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def trigger(ticker: str, h: dict) -> dict:
    """POST /analysis/run and drain the SSE stream to completion."""
    body = json.dumps({"ticker": ticker}).encode()
    req = urllib.request.Request(BASE + "/analysis/run", data=body,
                                headers=h, method="POST")
    events: list[tuple[str, dict]] = []
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=_PER_RUN_TIMEOUT) as resp:
            cur_event = "message"
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    cur_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        payload = json.loads(line.split(":", 1)[1].strip())
                    except Exception:
                        payload = {"raw": line[:200]}
                    events.append((cur_event, payload))
                    if cur_event in ("complete", "error", "cached"):
                        print(f"    [{time.time() - t0:6.1f}s] {cur_event}: "
                              f"{json.dumps(payload)[:220]}")
                    elif cur_event == "progress":
                        print(f"    [{time.time() - t0:6.1f}s] "
                              f"{str(payload.get('phase'))[:34]:<34} "
                              f"{str(payload.get('status'))[:9]:<9} "
                              f"{str(payload.get('summary'))[:78]}")
    except Exception as exc:
        print(f"    stream ended after {time.time() - t0:.1f}s: "
              f"{type(exc).__name__}: {exc}")
    kinds = [k for k, _ in events]
    return {
        "events": events,
        "complete": any(k == "complete" for k in kinds),
        "cached": any(k == "cached" for k in kinds),
        "error": next((p for k, p in events if k == "error"), None),
        "run_id": next((p.get("run_id") for k, p in events
                        if p.get("run_id")), None),
        "elapsed": time.time() - t0,
    }


def _dsn() -> str:
    """Prod Postgres, read-only, via the public proxy host."""
    raw = open(os.path.expanduser("~/.railway_pg_url")).read().strip()
    return re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/", raw)


def latest_run_at(ticker: str) -> str | None:
    """Newest archived ``run_at`` for a ticker, captured BEFORE triggering.

    Railway drops the SSE stream after roughly 30 s, so the ``run_id`` from the
    stream is often absent and the read-back has to fall back to "latest row
    for this ticker". Without this fence that fallback silently picks up a
    PRE-deploy row — BN4.SI last ran at 2026-09-16T07:06 with zero gates — and
    the check reports a confident verdict about the old engine. A verification
    that can read yesterday's answer as today's is worse than no verification.
    """
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=30) as cx, cx.cursor() as cur:
        cur.execute("SELECT max(run_at) FROM web_runs WHERE ticker = %s",
                    (ticker,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def read_back(ticker: str, run_id: str | None,
              *, not_before: str | None = None,
              gate_id: str = GATE_ID,
              flag_prefix: str = FLAG_PREFIX) -> dict | None:
    """Read the run's own row back from prod Postgres (read-only).

    The SSE payload and the archived row can disagree — the row is what the
    rest of the product reads, so it is what gets verified.
    """
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=30) as cx, cx.cursor() as cur:
        if run_id:
            cur.execute("SELECT run_id, ticker, run_at, full_result_json "
                        "FROM web_runs WHERE run_id = %s", (run_id,))
        else:
            cur.execute("SELECT run_id, ticker, run_at, full_result_json "
                        "FROM web_runs WHERE ticker = %s AND run_at > %s "
                        "ORDER BY run_at DESC LIMIT 1",
                        (ticker, not_before or ""))
        row = cur.fetchone()
    if not row:
        return None
    d = json.loads(row[3]) if row[3] else {}
    rng = ((d.get("data") or {}).get("dcf_range") or {}).get(row[1]) or {}
    if not rng:
        # The pipeline archives CHECKPOINT rows as it goes, and a checkpoint
        # written before the DCF phase carries no dcf_range at all. Returning
        # that row would report base_iv=None as though the engine produced
        # nothing, and break the poll loop one second into a twelve-minute
        # run. None here means "not finished yet — keep waiting".
        return None
    base = rng.get("base") or {}
    gates = [g for g in (rng.get("gate_evaluations") or [])
             if isinstance(g, dict)]
    return {
        "run_id": row[0], "ticker": row[1], "run_at": row[2],
        "profile": rng.get("profile"),
        "anchor_method": rng.get("anchor_method"),
        "base_iv": base.get("intrinsic_value"),
        "bear_iv": (rng.get("bear") or {}).get("intrinsic_value"),
        "bull_iv": (rng.get("bull") or {}).get("intrinsic_value"),
        "growth_rate": base.get("growth_rate"),
        "gate_ids": sorted({str(g.get("gate_id")) for g in gates}),
        "gate": next((g for g in gates if g.get("gate_id") == gate_id), None),
        "flags": [f for f in (base.get("forward_flags") or [])
                  if str(f).startswith(flag_prefix)],
        "method_iv_table": base.get("method_iv_table"),
        # The legs, not just the blend. A blended IV can hold still while the
        # legs underneath it are being deleted: Phase 1.1's prod check found
        # base IV moving only 7.87 -> 7.83 on BN4.SI, which looked like a
        # no-op until iv_dcf/weight_dcf showed the DCF leg had gone to
        # None/0.0 in every scenario and the valuation had become
        # multiples-only. The headline is the least informative field here.
        "iv_dcf": base.get("iv_dcf"),
        "iv_multi": base.get("iv_multi"),
        "weight_dcf": base.get("weight_dcf"),
        "weight_multi": base.get("weight_multi"),
        "methods_used": base.get("methods_used"),
        "methods_unavailable": rng.get("methods_unavailable"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    ap.add_argument("--expect-fire", nargs="*", default=None,
                    help="tickers the gate must fire on "
                         "(default: all but the controls)")
    ap.add_argument("--expect-quiet", nargs="*", default=[],
                    help="tickers the gate must NOT fire on")
    ap.add_argument("--gate-id", default=GATE_ID,
                    help=f"gate_id to look for in gate_evaluations "
                         f"(default {GATE_ID})")
    ap.add_argument("--flag-prefix", default=FLAG_PREFIX,
                    help="forward_flags prefix the gate must emit "
                         f"(default {FLAG_PREFIX!r})")
    ap.add_argument("--not-before", default=None,
                    help="read back runs archived after this ISO timestamp "
                         "instead of triggering new ones. Railway drops the "
                         "SSE stream after ~30 s while the pipeline keeps "
                         "running server-side, so a run started by an earlier "
                         "invocation is verified with this rather than paid "
                         "for twice.")
    args = ap.parse_args()

    fire = set(args.expect_fire if args.expect_fire is not None
               else [t for t in args.tickers if t not in args.expect_quiet])
    quiet = set(args.expect_quiet)
    gate_id, flag_prefix = args.gate_id, args.flag_prefix

    h = _headers(_secret())
    st, who = _get("/auth/me", h)
    print(f"auth /auth/me -> {st} "
          f"{json.dumps(who)[:120] if isinstance(who, dict) else who}")
    if st != 200:
        return 2
    print(f"looking for gate_id={gate_id} flag_prefix={flag_prefix!r}")

    failures: list[str] = []
    warnings: list[str] = []
    results: dict[str, dict] = {}
    for ticker in args.tickers:
        print(f"\n=== {ticker} ===")
        if args.not_before:
            fence = args.not_before
            run = {"error": None, "cached": False, "run_id": None,
                   "events": [], "complete": False, "elapsed": 0.0}
            print(f"    read-only: verifying the newest row after {fence}")
        else:
            fence = latest_run_at(ticker)
            print(f"    newest archived run before this one: {fence}")
            run = trigger(ticker, h)
        if run["error"]:
            failures.append(f"{ticker}: run errored — {run['error']}")
        if run["cached"]:
            # A 30-min cache hit re-arms nothing: the row it points at predates
            # the fence, so read_back would find nothing and the failure below
            # would read like a missing archive rather than a cache hit.
            failures.append(
                f"{ticker}: prod served a CACHED run (within 30 min) — no new "
                f"pipeline executed, nothing verified. Wait out the cache.")
        back = None
        for _attempt in range(120):   # up to 20 min: full runs take 10-12
            back = read_back(ticker, run["run_id"], not_before=fence,
                             gate_id=gate_id, flag_prefix=flag_prefix)
            if back:
                break
            time.sleep(10)
        if not back:
            failures.append(
                f"{ticker}: no web_runs row newer than {fence} after the run "
                f"(stream reported run_id={run['run_id']})")
            continue
        results[ticker] = back
        print(f"    run_at={back['run_at']}  profile={back['profile']}")
        print(f"    anchor={back['anchor_method']}")
        print(f"    base_iv={back['base_iv']}  "
              f"(bear {back['bear_iv']} / bull {back['bull_iv']})")
        print(f"    base growth_rate={back['growth_rate']}")
        print(f"    legs: iv_dcf={back['iv_dcf']} iv_multi={back['iv_multi']} "
              f"w_dcf={back['weight_dcf']} w_multi={back['weight_multi']}")
        print(f"    methods_used={back['methods_used']}")
        print(f"    methods_unavailable={back['methods_unavailable']}")
        print(f"    method_iv_table={back['method_iv_table']}")
        print(f"    gate_ids={back['gate_ids']}")
        for f in back["flags"]:
            print(f"    flag: {f}")
        if back["gate"]:
            g = back["gate"]
            print(f"    gate metric={g.get('metric')} applied={g.get('applied')} "
                  f"{g.get('raw_input_path_a')} -> {g.get('gated_output_path_b')}")
            print(f"    basis: {g.get('basis')}")

        fired = gate_id in back["gate_ids"]
        if ticker in fire and not fired:
            failures.append(f"{ticker}: expected {gate_id} to fire, it did not")
        if ticker in quiet and fired:
            failures.append(f"{ticker}: expected {gate_id} QUIET, it fired")
        if fired and not back["flags"]:
            failures.append(f"{ticker}: gate fired but no forward flag was emitted")

        # methods_used is what the published report claims. A method named
        # there but absent from method_iv_table contributed nothing to the
        # number on the page — the reporting bug that hid Phase 1.1's silent
        # DCF drop. Surfaced as a warning rather than a failure because it
        # pre-dates this plan and is chipped separately; it must not be
        # allowed to become invisible again.
        table = back["method_iv_table"] or {}
        claimed = [m for m in (back["methods_used"] or []) if m not in table]
        if claimed:
            warnings.append(
                f"{ticker}: methods_used claims {claimed} but they are absent "
                f"from method_iv_table ({sorted(table)})")
        if back["iv_dcf"] is None and "DCF" in (back["methods_used"] or []):
            warnings.append(
                f"{ticker}: the DCF leg was dropped (iv_dcf=None, "
                f"weight_dcf={back['weight_dcf']}) yet methods_used still "
                f"names DCF, and methods_unavailable="
                f"{back['methods_unavailable']} does not record it")

    print("\n" + "=" * 68)
    for t, r in results.items():
        print(f"  {t:<12} iv={str(r['base_iv']):<10} "
              f"iv_dcf={str(r['iv_dcf']):<9} w_dcf={str(r['weight_dcf']):<6} "
              f"gates={r['gate_ids']}")
    if warnings:
        print(f"\n{len(warnings)} WARNING(S) — reporting, not valuation:")
        for w in warnings:
            print("  WARN " + w)
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print("  FAIL " + f)
        return 1
    print("\nPASS — deployed engine matches the expected gate behaviour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

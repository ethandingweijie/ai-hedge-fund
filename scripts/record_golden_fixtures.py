"""Record golden valuation fixtures (Phase 0, item 7).

One live ``run_dcf_agent`` per ticker, capturing:

  * every raw FMP response, unparsed (``raw/calls.json``)
  * every DB/file-backed read the engine makes, frozen (same file)
  * the archived production ``web_runs`` row for that ticker
    (``web_run.json``), which supplies the LLM-only inputs replay freezes
  * capture metadata — commit, date, targets hit (``meta.json``)

Read-only against production Postgres: the row is SELECTed via the same host
swap ``scripts/eval_gemini_valuation.py --prod-db`` uses
(``postgres.railway.internal`` is unreachable off-platform, so the public
proxy host is substituted). Nothing is written to prod.

    python scripts/record_golden_fixtures.py AAPL MELI
    python scripts/record_golden_fixtures.py --all

Each ticker is recorded in its own subprocess — see :func:`_child_main` for
why that is a correctness requirement and not a style choice.

Re-recording is expected whenever the basket or the engine's input surface
changes; the golden test compares against ``tests/golden/snapshots.json``,
not against these fixtures, so a re-record alone moves no baseline.

Proving the suite can actually fail — this scales every peer multiple the
engine resolves by 1.10, and the golden test must catch it::

    PYTHONPATH=tests/golden/perturb pytest tests/test_golden_valuations.py -x
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
load_dotenv(ROOT / ".env")

from src.memory import golden_capture as gc  # noqa: E402

#: The basket — every profile family and market the 2026-09 production runs
#: exposed a defect in.
BASKET = [
    "AAPL",        # Large-cap tech
    "MELI",        # Hyper-growth platform
    "MU",          # Memory (cyclical peak routing)
    "COST",        # August FY end
    "V",           # Payment networks (Tier 1-allowed financial)
    "SCHW",        # Brokerage (Tier 1 zero-EV)
    "02888.HK",    # HK bank
    "D05.SI",      # SG bank
    "BABA",        # Dual listing (ADR)
    "09988.HK",    # Dual listing (HK)
    "BN4.SI",      # Conglomerate (Keppel — growth divergence + stale SOTP)
    "U96.SI",      # Utility / IPP (growth divergence)
    "C38U.SI",     # REIT
    "FCX",         # Mining (cyclical)
]


def _prod_dsn() -> str:
    """Public-proxy DSN for read-only production access."""
    raw = (Path.home() / ".railway_pg_url").read_text(encoding="utf-8").strip()
    return re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/", raw)


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def fetch_web_run(ticker: str) -> tuple[dict, str] | None:
    """Latest production row for ``ticker`` plus its ``end_date``.

    ``end_date`` is not persisted in ``web_runs``; it is ``runs.analysis_date``
    joined on ``archive_run_id``. Returns None when the ticker has never been
    run in production — such a ticker cannot be recorded from an archive and
    needs a live pipeline run first.
    """
    import psycopg
    with psycopg.connect(_prod_dsn(), connect_timeout=20) as conn:
        row = conn.execute(
            "SELECT run_id, run_at, archive_run_id, full_result_json "
            "FROM web_runs WHERE ticker = %s AND is_checkpoint = 0 "
            "ORDER BY run_at DESC LIMIT 1", [ticker]).fetchone()
        if not row:
            return None
        run_id, run_at, archive_run_id, payload = row
        end_date = None
        if archive_run_id:
            r = conn.execute(
                "SELECT analysis_date FROM runs WHERE run_id = %s",
                [archive_run_id]).fetchone()
            end_date = r[0] if r else None
    doc = json.loads(payload)
    data = doc.get("data") or {}
    meta = {
        "run_id": run_id,
        "run_at": str(run_at),
        "archive_run_id": archive_run_id,
        "end_date": str(end_date) if end_date else None,
    }
    return {"data": data, "top": {k: v for k, v in doc.items() if k != "data"},
            "meta": meta}


def record_one(ticker: str, *, end_date: str | None = None,
               quiet: bool = False) -> dict:
    """Capture one ticker. Returns the meta doc written to disk."""
    from src.memory.golden_state import build_state, build_state_from_lookups

    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        raise SystemExit("FMP_API_KEY missing — cannot record live fixtures")

    archived = fetch_web_run(ticker)

    if archived is not None:
        # A checkpoint row would replay a partial pipeline; is_checkpoint=0 is
        # filtered in SQL. end_date defaults to the archived analysis date.
        ed = end_date or archived["meta"]["end_date"]
        if not ed:
            raise SystemExit(f"{ticker}: no end_date recoverable from runs table")
        state = build_state(ticker, archived["data"], ed, api_key=api_key)
        source = "prod_web_run"
    else:
        # No production run to freeze LLM inputs from. Recorded from the
        # curated routing tables at today's date; the fixture then covers the
        # deterministic engine only (see build_state_from_lookups).
        ed = end_date or datetime.now(timezone.utc).date().isoformat()
        state = build_state_from_lookups(ticker, ed, api_key=api_key)
        source = "lookup_tables"
        print(f"  {ticker:<11} note: no prod web_runs row — recording from "
              f"lookup tables (no LLM inputs frozen)")

    # Import after env is loaded so module-level dotenv re-arms nothing.
    from src.agents.analysis.dcf_agent import run_dcf_agent

    with gc.Recorder() as rec:
        out = run_dcf_agent(state)

    entry = (out["data"].get("dcf_range") or {}).get(ticker) or {}
    skip = (out["data"].get("dcf_skip_reasons") or {}).get(ticker)

    doc = {
        "ticker": ticker,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _commit(),
        "end_date": ed,
        "state_source": source,
        "source_run_id": archived["meta"]["run_id"] if archived else None,
        "source_run_at": archived["meta"]["run_at"] if archived else None,
        "archive_run_id": archived["meta"]["archive_run_id"] if archived else None,
        "targets_hit": dict(sorted(rec.hits.items())),
        "recorded_calls": len(rec.calls),
        "replay": {
            "base_iv": (entry.get("base") or {}).get("intrinsic_value"),
            "profile": entry.get("profile"),
            "skip_reason": skip,
        },
        "calls": rec.calls,
    }

    calls_path = gc.write_fixture(ticker, doc)

    # Persist the archived row so replay's LLM-only inputs are frozen with the
    # fixture rather than re-read from prod (which moves).
    written = [calls_path, gc.fixture_dir(ticker) / "meta.json"]
    if archived is not None:
        written.append(gc.write_web_run(
            ticker, {"data": archived["data"], "top": archived["top"],
                     "meta": archived["meta"]}))

    # A fixture is committed test data and is NOT gitignored, so a leaked key
    # here is a credential in git history. Fail the record pass rather than
    # let it reach a commit.
    leaked = gc.assert_no_secrets(written)
    if leaked:
        for line in leaked:
            print(f"  {ticker:<11} ⚠ SECRET LEAK — {line}")
        for p in written:
            if p.exists():
                p.unlink()
        raise SystemExit(f"{ticker}: fixture deleted after secret leak")

    if not quiet:
        size = calls_path.stat().st_size / 1024
        print(f"  {ticker:<11} {doc['recorded_calls']:>4} calls  "
              f"{size:>7,.0f} KB  profile={doc['replay']['profile']!r} "
              f"base_iv={doc['replay']['base_iv']}")
        if skip:
            print(f"              ⚠ SKIPPED: {skip}")
    return doc


def _child_main(ticker: str, end_date: str | None) -> int:
    """Record exactly one ticker in this process, print a JSON summary.

    Run as a subprocess by :func:`main`. Recording one ticker per interpreter
    is not a performance choice — it is what makes the recording match the
    replay. The valuation path has ~ten process-lifetime caches
    (``regional_comps._CLASSIFICATION_CACHE``, ``api._INDUSTRY_CACHE``,
    ``api._LISTING_CCY_CACHE``, ``hk.currency._fx_cache``,
    ``sg.currency._cached_rate``, ``src.data.cache._cache``, ...). Recording
    14 tickers in one process warms them from ticker 1 onward, so later
    tickers never issue the calls a cold replay then asks for. That is exactly
    what the first basket recording produced: 13 of 14 fixtures replayed with
    misses on ``/stable/profile`` and ``/stable/batch-forex-quotes``, and the
    engine's broad ``except Exception`` handlers swallowed the ReplayError and
    quietly degraded to empty regional comps instead of failing.

    Replay already runs one subprocess per ticker (``src/memory/golden_replay``);
    this makes record symmetric with it.
    """
    try:
        doc = record_one(ticker, end_date=end_date, quiet=True)
    except SystemExit as exc:
        print(json.dumps({"ticker": ticker, "ok": False, "error": str(exc)}))
        return 1
    except Exception as exc:                            # noqa: BLE001
        print(json.dumps({"ticker": ticker, "ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    entry = doc["replay"]
    size = (gc.fixture_dir(ticker) / "raw" / "calls.json").stat().st_size / 1024
    print(json.dumps({
        "ticker": ticker, "ok": True, "calls": doc["recorded_calls"],
        "kb": round(size), "profile": entry["profile"],
        "base_iv": entry["base_iv"], "skip_reason": entry["skip_reason"],
        "state_source": doc["state_source"],
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tickers", nargs="*", help="tickers to record (default: --all)")
    ap.add_argument("--all", action="store_true", help="record the whole basket")
    ap.add_argument("--list", action="store_true", help="print the basket and exit")
    ap.add_argument("--end-date", default=None,
                    help="override end_date (default: archived analysis_date)")
    ap.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args._child:
        if len(args.tickers) != 1:
            ap.error("--_child records exactly one ticker")
        return _child_main(args.tickers[0], args.end_date)

    if args.list:
        for t in BASKET:
            mark = "✓" if gc.fixture_dir(t).exists() else " "
            print(f" {mark} {t}")
        return 0

    tickers = BASKET if args.all else args.tickers
    if not tickers:
        ap.error("give tickers, or --all")

    print(f"Recording {len(tickers)} golden fixture(s) at {_commit()[:8]} "
          f"— one subprocess each")
    failures: list[tuple[str, str]] = []
    for t in tickers:
        cmd = [sys.executable, str(Path(__file__).resolve()), t, "--_child"]
        if args.end_date:
            cmd += ["--end-date", args.end_date]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=1800)
        summary = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                except json.JSONDecodeError:
                    pass
        if summary and summary.get("ok"):
            note = ("  (no prod web_runs row — lookup tables, no LLM inputs)"
                    if summary.get("state_source") == "lookup_tables" else "")
            print(f"  {t:<11} {summary['calls']:>4} calls  {summary['kb']:>5} KB  "
                  f"profile={summary['profile']!r} base_iv={summary['base_iv']}"
                  f"{note}")
            if summary.get("skip_reason"):
                print(f"              ⚠ SKIPPED: {summary['skip_reason']}")
        else:
            why = (summary or {}).get("error") or (
                f"child exited {proc.returncode}\n"
                f"{(proc.stderr or proc.stdout or '')[-1500:]}")
            failures.append((t, why))
            print(f"  {t:<11} FAIL: {why}")

    print()
    if failures:
        print(f"{len(failures)} ticker(s) failed:")
        for t, why in failures:
            print(f"  {t}: {why}")
        return 1
    print("All fixtures recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

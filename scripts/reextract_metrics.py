"""
scripts/reextract_metrics.py
=============================
CLI wrapper around src/memory/reextract_metrics.py. Re-runs the LLM
extractor chain against EXISTING stored runs (no web searches, no report
synthesis) and patches web_runs.full_result_json in place so the
frontend sees the recovered fields on next page load.

Primary use case: the v2.0.1 _parse_llm_json migration recovers Qwen
preamble-wrapped extractor responses that the old parser silently
dropped. Use this script to retrofit historic runs (DDOG, SNOW, etc.)
without re-running the expensive research pipeline.

Usage
-----
    # Dry run — show diff for one ticker's most recent run
    python -m scripts.reextract_metrics --ticker DDOG --dry-run

    # Actually patch the DB
    python -m scripts.reextract_metrics --ticker DDOG

    # Process multiple tickers in one invocation
    python -m scripts.reextract_metrics --tickers DDOG,SNOW --dry-run

    # Target a specific run by UUID
    python -m scripts.reextract_metrics --run-id abc-123-def

    # Process last N runs for a ticker
    python -m scripts.reextract_metrics --ticker DDOG --limit 3

Requires (same as the live pipeline)
------------------------------------
    DEEP_RESEARCH_API_KEY      — DashScope API key (preferred for Qwen)
    DEEP_RESEARCH_BASE_URL     — DashScope endpoint
    DEEP_RESEARCH_SYNTHESIS_MODEL (optional, defaults to qwen3-max)
    ANTHROPIC_API_KEY          — fallback if no DashScope creds
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make top-level `src` imports resolve when run as `python -m scripts.reextract_metrics`
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.reextract_metrics import (  # noqa: E402
    reextract_by_ticker,
    reextract_for_run,
)


def _print_result(r: dict) -> None:
    """Pretty-print a single reextract result for CLI output."""
    if not r.get("ok"):
        print(f"  ✗ FAILED — {r.get('error', 'unknown error')}")
        return
    print(f"  ✓ {r['ticker']:6s} · {r['sector']:12s} · profile={r['profile_name'] or '(none)'}")
    print(f"    run_id: {r['run_id']}")
    print(f"    extractors run: {', '.join(r['extractors_run'])}")
    print(f"    BEFORE: {json.dumps(r['before'], default=str)}")
    print(f"    AFTER:  {json.dumps(r['after'], default=str)}")
    if r.get("dry_run"):
        status = "WOULD UPDATE" if r.get("would_update") else "no gain — would skip"
        print(f"    [dry-run] {status}")
    else:
        if r.get("updated"):
            print(f"    ✓ DB UPDATED")
        else:
            print(f"    - no update ({r.get('note', 'skipped')})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-run extractors against stored runs without fresh pipeline"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id", type=str, help="Target a specific run by UUID")
    g.add_argument("--ticker", type=str, help="Process last N run(s) for ONE ticker")
    g.add_argument("--tickers", type=str,
                   help="Comma-separated tickers (e.g. DDOG,SNOW). Processes most recent run for each.")

    ap.add_argument("--limit", type=int, default=1,
                    help="How many recent runs to process per ticker (default: 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show diff without patching the DB")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Actually patch the DB (default: dry-run)")
    ap.set_defaults(dry_run=True)

    args = ap.parse_args()

    print("=" * 70)
    print(f"Re-extract metrics — dry_run={args.dry_run}")
    print("=" * 70)

    all_results: list[dict] = []

    if args.run_id:
        r = reextract_for_run(args.run_id, dry_run=args.dry_run)
        _print_result(r)
        all_results.append(r)
    elif args.ticker:
        results = reextract_by_ticker(args.ticker, dry_run=args.dry_run, limit=args.limit)
        print(f"\n== {args.ticker.upper()} ({len(results)} run(s)) ==")
        for r in results:
            _print_result(r)
        all_results.extend(results)
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        for t in tickers:
            results = reextract_by_ticker(t, dry_run=args.dry_run, limit=args.limit)
            print(f"\n== {t} ({len(results)} run(s)) ==")
            for r in results:
                _print_result(r)
            all_results.extend(results)

    # Summary
    print()
    print("=" * 70)
    ok = sum(1 for r in all_results if r.get("ok"))
    updated = sum(1 for r in all_results if r.get("updated"))
    would_update = sum(1 for r in all_results if r.get("would_update"))
    print(f"Summary: {ok}/{len(all_results)} succeeded | "
          f"{updated} updated | {would_update} would-update (dry run)")
    print("=" * 70)

    return 0 if ok == len(all_results) else 1


if __name__ == "__main__":
    sys.exit(main())

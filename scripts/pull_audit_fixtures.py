"""
pull_audit_fixtures.py — Phase 0 of the Card QA Agent rollout.

Pulls 10 specific production runs from `/analysis/runs/{run_id}` and saves
each as a JSON fixture under `tests/audit/fixtures/`. The fixtures drive
the Phase 10 eval set: 3 known-broken + 7 known-healthy cases that
together exercise Meta-Check, card-specific audits, and false-positive
checks across sector profiles.

Run from repo root:
    python scripts/pull_audit_fixtures.py

The (ticker, run_id) pairs are curated in this script (see FIXTURES below).
Selection rationale lives in the plan: mighty-gliding-graham.md → Phase 0.

The script also prints a summary table flagging:
  * empty `dcf_range[ticker]`               → broken signal
  * mismatched sector vs expected profile   → Meta-Check fodder
  * missing `deep_research`                 → fixture won't drive judge tests
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Production base URL. Override via env var if testing against staging.
import os
BASE_URL = os.environ.get(
    "ANALYSIS_RUNS_BASE_URL",
    "https://ai-hedge-fund-production-7131.up.railway.app",
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "audit" / "fixtures"

# (ticker, run_id, category, notes). Category drives expected-label generation
# in the summary report.
FIXTURES: list[tuple[str, str, str, str]] = [
    # ─ Broken (3) ──────────────────────────────────────────────────────────
    ("MRNA", "0182e126-cfbc-40ff-b684-28f5862c98a5", "broken",
     "Most recent MRNA; dcf_range={} → Pipeline rNPV blank"),
    ("ZTS",  "b91aa9b4-cbc6-47c2-8d9d-d1ee86867173", "broken",
     "Misclassified as HealthcareServices (Managed Care) — animal pharma"),
    ("MRNA", "70b7d8b1-bcc6-4c25-9718-3bdb673fcdf8", "broken",
     "Historical MRNA — proves bug is reproducible, not transient"),
    # ─ Healthy (7) ─────────────────────────────────────────────────────────
    ("AAPL", "8a81be97-2134-4dae-a63c-fa499a946db6", "healthy", "Tech mega-cap"),
    ("MSFT", "f616514d-35dd-4fd1-b1d2-5f03e6613751", "healthy", "Tech mega-cap"),
    ("JPM",  "f58865fb-8aca-4d3d-99be-2dec86de68ff", "healthy", "Bank (CET1/TBV/NIM)"),
    ("MOH",  "cebfa77e-ff37-43f5-9894-1f0d32c53a04", "healthy",
     "Real Managed Care — contrast to ZTS misclassification"),
    ("DLR",  "e4ecbe13-744e-445e-8d92-aa0eca29534d", "healthy", "REIT (NAV/P-FFO)"),
    ("NVO",  "3a5d11f5-41eb-405d-a517-0d3d98cd0fbc", "healthy", "Foreign large-cap pharma"),
    ("INTU", "869c6dfe-a984-41b3-aa00-86f83e0fa36c", "healthy",
     "Tech SaaS (NRR/Rule-of-40)"),
]


def _fetch_run(run_id: str) -> dict:
    """GET /analysis/runs/{run_id} → parsed JSON dict. Raises on HTTP error."""
    url = f"{BASE_URL}/analysis/runs/{run_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "phase0-fixture-puller/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _fixture_path(ticker: str, run_id: str) -> Path:
    """Filename collision-proof for the 2-MRNA case: TICKER__{first8}.json"""
    short = run_id.split("-")[0]
    return FIXTURE_DIR / f"{ticker}__{short}.json"


def _summarize(ticker: str, run_id: str, payload: dict) -> dict:
    """Extract diagnostic counters from a run payload."""
    data = payload.get("data") or {}
    dcf_range = data.get("dcf_range") or {}
    dcf_ticker = dcf_range.get(ticker, {}) if isinstance(dcf_range, dict) else {}
    sectors = data.get("sectors") or {}
    profile_names = data.get("profile_names") or {}
    deep_research = data.get("deep_research") or ""
    pipeline_assets = data.get("pipeline_assets") or {}
    pipeline_assets_ticker = (
        pipeline_assets.get(ticker, []) if isinstance(pipeline_assets, dict) else []
    )

    return {
        "ticker":            ticker,
        "run_id_short":      run_id[:8],
        "size_bytes":        len(json.dumps(payload)),
        "sector":            sectors.get(ticker, "?"),
        "profile":           profile_names.get(ticker, "?"),
        "dcf_range_empty":   not bool(dcf_ticker),
        "deep_research_kb":  round(len(deep_research) / 1024, 1) if deep_research else 0,
        "pipeline_assets":   len(pipeline_assets_ticker) if isinstance(pipeline_assets_ticker, list) else "?",
        "dcf_engine_error":  "dcf_engine_error" in data,
    }


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Pulling {len(FIXTURES)} fixtures from {BASE_URL}")
    print(f"Saving to: {FIXTURE_DIR}")
    print()

    summaries: list[dict] = []
    failures: list[tuple[str, str, str]] = []

    for ticker, run_id, category, notes in FIXTURES:
        out_path = _fixture_path(ticker, run_id)
        try:
            payload = _fetch_run(run_id)
        except urllib.error.HTTPError as exc:
            failures.append((ticker, run_id, f"HTTP {exc.code}: {exc.reason}"))
            continue
        except Exception as exc:
            failures.append((ticker, run_id, f"{type(exc).__name__}: {exc}"))
            continue

        # Save raw payload — Layer A code will load + parse from these files.
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        s = _summarize(ticker, run_id, payload)
        s["category"] = category
        s["notes"] = notes
        s["filename"] = out_path.name
        summaries.append(s)
        print(f"  saved {out_path.name}  ({s['size_bytes']:>9,} bytes)")

    print()
    print("=" * 110)
    print("Fixture summary:")
    print("=" * 110)
    print(
        f"{'Ticker':<7} {'RunID':<10} {'Cat':<8} {'Sector':<24} {'Profile':<22} "
        f"{'dcf={}':<6} {'DR(kb)':<7} {'Pipe':<5} {'Err':<4}"
    )
    print("-" * 110)
    for s in summaries:
        print(
            f"{s['ticker']:<7} {s['run_id_short']:<10} {s['category']:<8} "
            f"{str(s['sector'])[:23]:<24} {str(s['profile'])[:21]:<22} "
            f"{'Y' if s['dcf_range_empty'] else 'N':<6} "
            f"{s['deep_research_kb']:<7} {str(s['pipeline_assets']):<5} "
            f"{'Y' if s['dcf_engine_error'] else 'N':<4}"
        )

    if failures:
        print()
        print("FAILURES:")
        for t, r, msg in failures:
            print(f"  {t} {r[:8]}: {msg}")

    print()
    print(f"Total saved: {len(summaries)} / {len(FIXTURES)}")
    print(f"Next step: populate tests/audit/fixtures/_labels.yaml per the plan file format.")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

"""Signed valuation bias table (owner, 2026-11-28, D4: measure before any methodology change).

    python scripts/signed_bias.py                  # wave baselines + golden snapshots (local files)
    python scripts/signed_bias.py --runs           # + prod run_features (read-only; DATABASE_URL must be set)
    python scripts/signed_bias.py --out docs/baselines/signed_bias.md

Unsigned error ("50% within +-30%") hides direction. This reports, per market and per profile, the MEDIAN of
log(IV / spot) and log(IV / consensus) with the inter-quartile range, so a systematic tilt (e.g. US mega-caps valued
well below the market) shows as a sign, not as noise. exp(median log) - 1 is the typical % gap.

Sources (each row carries its source, never mixed silently):
  baseline  docs/baselines/after_*.json, latest row per ticker (wave engines, Sept 2026, before the CFA overhaul)
  golden    tests/golden/snapshots.json (current engine on frozen inputs)
  runs      prod run_features, latest run per ticker (current engine, live inputs; no consensus column)
Nothing is written anywhere except --out. No multiple, beta or constant is chosen here.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _market(t: str) -> str:
    t = t.upper()
    return "HK" if t.endswith(".HK") else "SG" if t.endswith(".SI") else "US"


def _log(a, b):
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    return math.log(a / b) if a > 0 and b > 0 else None


def baseline_rows() -> list[dict]:
    latest: dict[str, tuple[str, dict]] = {}
    for f in sorted(glob.glob(str(ROOT / "docs" / "baselines" / "after_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        as_of = str(d.get("as_of") or "")
        for t, r in (d.get("tickers") or {}).items():
            if isinstance(r, dict) and (t not in latest or as_of >= latest[t][0]):
                latest[t] = (as_of, r)
    out = []
    for t, (_, r) in latest.items():
        out.append({"source": "baseline", "ticker": t, "market": _market(t), "profile": r.get("profile") or "?",
                    "iv": r.get("base"), "spot": r.get("spot"), "consensus": r.get("consensus"),
                    "verdict": r.get("backtest_forward_verdict")})
    return out


def golden_rows() -> list[dict]:
    p = ROOT / "tests" / "golden" / "snapshots.json"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, s in d.items():
        if name.startswith("_") or not isinstance(s, dict):
            continue
        proj = s.get("projection") or {}
        t = s.get("ticker") or name.replace("_", ".")
        out.append({"source": "golden", "ticker": t, "market": _market(t), "profile": s.get("profile") or "?",
                    "iv": s.get("base_iv", proj.get("scenarios.base.intrinsic_value")),
                    "spot": proj.get("pt_bridge.spot") or proj.get("current_price") or s.get("spot"),
                    "consensus": proj.get("consensus_pt.consensus"), "verdict": None})
    return out


def run_rows() -> list[dict]:
    if not os.environ.get("DATABASE_URL"):
        print("  [runs] DATABASE_URL not set -- skipped", file=sys.stderr)
        return []
    from src.data import db
    rows = db.query("SELECT ticker, run_at, market, profile, iv_base, spot FROM run_features "
                    "WHERE unrated = 0 AND cache_copy = 0 ORDER BY run_at") or []
    latest = {}
    for r in rows:
        latest[r["ticker"] if hasattr(r, "keys") else r[0]] = r
    out = []
    for t, r in latest.items():
        g = (lambda k, i: r[k] if hasattr(r, "keys") else r[i])
        out.append({"source": "runs", "ticker": t, "market": g("market", 2) or _market(t),
                    "profile": g("profile", 3) or "?", "iv": g("iv_base", 4), "spot": g("spot", 5),
                    "consensus": None, "verdict": None})
    return out


def _stats(xs: list[float]) -> str:
    if not xs:
        return "—"
    med = statistics.median(xs)
    if len(xs) >= 4:
        q = statistics.quantiles(xs, n=4)
        return f"{math.exp(med) - 1:+.0%} (IQR {math.exp(q[0]) - 1:+.0%} … {math.exp(q[2]) - 1:+.0%})"
    return f"{math.exp(med) - 1:+.0%}"


def table(rows: list[dict], key: str, min_n: int = 3) -> list[str]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["source"], r[key])].append(r)
    lines = [f"| source | {key} | n | IV vs spot (median, IQR) | IV vs consensus | within ±30% of consensus |"
             f" market closer (T-1) |", "|---|---|---|---|---|---|---|"]
    for (src, k), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        if len(rs) < min_n and key == "profile":
            continue
        vs = [x for x in (_log(r["iv"], r["spot"]) for r in rs) if x is not None]
        vc = [x for x in (_log(r["iv"], r["consensus"]) for r in rs) if x is not None]
        w30 = (f"{sum(abs(math.exp(x) - 1) <= 0.30 for x in vc) / len(vc):.0%} of {len(vc)}" if vc else "—")
        ver = [r["verdict"] for r in rs if r["verdict"]]
        mc = f"{sum(v == 'MARKET_CLOSER' for v in ver) / len(ver):.0%} of {len(ver)}" if ver else "—"
        lines.append(f"| {src} | {k} | {len(rs)} | {_stats(vs)} | {_stats(vc)} | {w30} | {mc} |")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", action="store_true", help="include prod run_features (needs DATABASE_URL)")
    ap.add_argument("--out", help="write the markdown here")
    a = ap.parse_args(argv)
    rows = baseline_rows() + golden_rows() + (run_rows() if a.runs else [])
    md = ["# Signed valuation bias", "",
          "Typical gap = exp(median log ratio) − 1. Negative = our base IV sits BELOW the comparator.", "",
          "## By market", "", *table(rows, "market"), "", "## By profile (n ≥ 3)", "", *table(rows, "profile"), ""]
    text = "\n".join(md)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

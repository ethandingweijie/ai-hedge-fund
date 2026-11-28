"""Pre-launch sweep + profile pin audit (owner, 2026-11-28, item 5). Engine only: no LLM calls, no cost beyond FMP.

    python scripts/prelaunch_sweep.py                          # top 500 US, 100 HK, 50 SG
    python scripts/prelaunch_sweep.py --us 50 --hk 10 --sg 10  # a quick slice
    python scripts/prelaunch_sweep.py --tickers AAPL MSFT D05.SI

Universe: S&P 500 + Nasdaq-100 constituents ranked by market cap (US), and the largest listings on HKSE and SES by
market cap (FMP). Each ticker runs the CURRENT valuation engine (scripts/wave_baseline.baseline_one) in its own worker
process on the LOCAL store -- refresh local regional_comps from production first, or the sweep values on stale peers.

Per ticker it records what a customer would see and whether the profile was chosen deliberately:
  * WITHHELD   -- the engine withholds the valuation (unrated / currency check)
  * RANGE      -- weighted methods disagree beyond the dispersion cap (the report shows a range, credit returned)
  * PIN        -- not in the tuned coverage AND routed by fallback (ladder / router / FMP-label map row): a candidate for
                  an owner pin, with the profile it fell into
  * OUTLIER    -- base IV more than 3x or less than 1/3 of the price
Writes docs/prelaunch/sweep_<date>.json (every row) and .md (the lists above, for owner review).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PYTHONUTF8"] = "1"


def _fmp(path: str, params: dict) -> list:
    import requests
    key = os.environ.get("FMP_API_KEY")
    r = requests.get(f"https://financialmodelingprep.com/stable/{path}", params={**params, "apikey": key}, timeout=60)
    r.raise_for_status()
    v = r.json()
    return v if isinstance(v, list) else []


def universe(n_us: int, n_hk: int, n_sg: int) -> list[str]:
    out: list[str] = []
    if n_us:
        rows = _fmp("sp500-constituent", {}) + _fmp("nasdaq-constituent", {})
        syms = list(dict.fromkeys(r["symbol"] for r in rows if r.get("symbol")))
        caps = {}
        for i in range(0, len(syms), 100):
            for q in _fmp("batch-quote", {"symbols": ",".join(syms[i:i + 100])}):
                caps[q.get("symbol")] = q.get("marketCap") or 0
        out += sorted(syms, key=lambda s: -(caps.get(s) or 0))[:n_us]
    for ex, n in (("HKSE", n_hk), ("SES", n_sg)):
        if n:
            rows = _fmp("company-screener", {"exchange": ex, "isEtf": "false", "isFund": "false",
                                             "isActivelyTrading": "true", "limit": max(n * 3, 100)})
            rows.sort(key=lambda r: -(r.get("marketCap") or 0))
            out += [r["symbol"] for r in rows[:n] if r.get("symbol")]
    return out


def _worker(args):
    ticker, end = args
    import contextlib
    import io
    import logging
    logging.disable(logging.CRITICAL)
    os.environ.pop("DATABASE_URL", None)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(ROOT / "scripts"))
    from src.tools.ticker_canonical import canonical_ticker
    t = canonical_ticker(ticker) or ticker
    t0 = time.monotonic()
    try:
        import wave_baseline as wb
        with contextlib.redirect_stdout(io.StringIO()):
            r = wb.baseline_one(t, end, os.environ.get("FMP_API_KEY"))
    except Exception as exc:                                # noqa: BLE001
        r = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    try:
        from src.data.coverage import is_tuned
        r["tuned"] = is_tuned(t)
    except Exception:                                       # noqa: BLE001
        r["tuned"] = None
    r["seconds"] = round(time.monotonic() - t0, 1)
    return t, r


def _pct(v) -> str:
    return "" if not isinstance(v, (int, float)) else f"{v:+.0%}"


def classify(t: str, r: dict, cap: float) -> list[str]:
    if r.get("error"):
        return ["ERROR"]
    tags = []
    if r.get("rating_state") == "unrated":
        tags.append("WITHHELD")
    d = r.get("leg_dispersion") or {}
    if isinstance(d.get("ratio"), (int, float)) and d["ratio"] > cap:
        tags.append("RANGE")
    if r.get("tuned") is False and r.get("routing_winner") in ("ladder", "router", "industry_map", None):
        tags.append("PIN")
    ivs = r.get("iv_vs_spot")
    if isinstance(ivs, (int, float)) and (ivs > 2.0 or ivs < -0.667):
        tags.append("OUTLIER")
    return tags


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--us", type=int, default=500)
    ap.add_argument("--hk", type=int, default=100)
    ap.add_argument("--sg", type=int, default=50)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    os.environ.pop("DATABASE_URL", None)
    from src.decisions.ratings import range_publication_ratio
    end = date.today().isoformat()
    names = a.tickers or universe(a.us, a.hk, a.sg)
    print(f"sweeping {len(names)} tickers with {a.workers} workers", flush=True)
    rows: dict[str, dict] = {}
    t0 = time.monotonic()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=10) as pool:
        for i, (t, r) in enumerate(pool.imap_unordered(_worker, [(n, end) for n in names]), 1):
            r["tags"] = classify(t, r, range_publication_ratio())
            rows[t] = r
            if i % 25 == 0 or i == len(names):
                print(f"  {i}/{len(names)}  {time.monotonic() - t0:.0f}s", flush=True)
    stem = a.out or str(ROOT / "docs" / "prelaunch" / f"sweep_{end.replace('-', '')}")
    Path(stem).parent.mkdir(parents=True, exist_ok=True)
    Path(stem + ".json").write_text(json.dumps({"as_of": end, "rows": rows}, indent=1, default=str), encoding="utf-8")

    def lst(tag):
        return sorted((t, r) for t, r in rows.items() if tag in r["tags"])
    md = [f"# Pre-launch sweep {end}", "",
          f"{len(rows)} tickers (engine only, local store refreshed from production). "
          f"Withheld {len(lst('WITHHELD'))} · range-only {len(lst('RANGE'))} · pin candidates {len(lst('PIN'))} · "
          f"outliers {len(lst('OUTLIER'))} · errors {len(lst('ERROR'))}.", ""]
    md += ["## Withheld (no valuation published)", "", "| ticker | reason | profile |", "|---|---|---|"]
    md += [f"| {t} | {r.get('rating_state_code')} | {r.get('profile')} |" for t, r in lst("WITHHELD")]
    md += ["", "## Range-only (methods disagree)", "", "| ticker | ratio | high leg | low leg | profile |",
           "|---|---|---|---|---|"]
    md += [f"| {t} | {(r.get('leg_dispersion') or {}).get('ratio')} | {(r.get('leg_dispersion') or {}).get('hi_leg')} | "
           f"{(r.get('leg_dispersion') or {}).get('lo_leg')} | {r.get('profile')} |"
           for t, r in sorted(lst("RANGE"), key=lambda x: -((x[1].get('leg_dispersion') or {}).get('ratio') or 0))]
    md += ["", "## Pin candidates (untuned, routed by fallback) — owner to confirm a profile", "",
           "| ticker | routed by | profile it fell into | IV vs spot |", "|---|---|---|---|"]
    md += [f"| {t} | {r.get('routing_winner')} | {r.get('profile')} | {_pct(r.get('iv_vs_spot'))} |"
           for t, r in lst("PIN")]
    md += ["", "## Outliers (base IV > 3x or < 1/3 of price)", "", "| ticker | IV vs spot | profile |", "|---|---|---|"]
    md += [f"| {t} | {_pct(r.get('iv_vs_spot'))} | {r.get('profile')} |" for t, r in lst("OUTLIER")]
    md += ["", "## Errors", ""] + [f"- {t}: {r.get('error')}" for t, r in lst("ERROR")]
    Path(stem + ".md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {stem}.json / .md in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Stage 0 baseline for a closed-loop wave: run the CURRENT engine over a
universe and record, per ticker, what every later change is judged against.

    python scripts/wave_baseline.py --wave 2 NEE DUK SO 00002.HK VST ENPH CCJ
    python scripts/wave_baseline.py --wave 2 --out baseline_wave2.json <tickers>

Read-only apart from the usage record a valuation makes. DATABASE_URL is
dropped, so the run reads the LOCAL store and never production.

Per ticker: routed profile and how it was reached, the anchor, the share of the
blend sitting on proxied legs (a leg whose value came from a different method
than its label), dropped legs and the weight that survived, bear/base/bull and
their ordering, base IV and the 12m target against consensus, and the
methodology backtest. The table printed at the end goes verbatim into the
wave's commit message, the way Wave 1's did.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PYTHONUTF8"] = "1"
os.environ.pop("DATABASE_URL", None)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
os.environ.pop("DATABASE_URL", None)


def _pct(a, b):
    return (a / b - 1.0) if (a and b) else None


def baseline_one(ticker: str, end: str, key: str) -> dict:
    from src.agents.analysis import dcf_agent as d
    from src.memory.golden_state import build_state_from_lookups

    st = build_state_from_lookups(ticker, end, api_key=key)
    with contextlib.redirect_stdout(io.StringIO()):
        dr = (d.run_dcf_agent(st)["data"]["dcf_range"].get(ticker) or {})
    base = dr.get("base") or {}
    ew = base.get("effective_weights") or []
    proxied = [e for e in ew if e.get("value_key") and e.get("value_key") != e.get("method")]
    dropped = base.get("legs_dropped") or []
    cons = (dr.get("consensus_pt") or {}).get("consensus")
    iv = base.get("intrinsic_value")
    bear = (dr.get("bear") or {}).get("intrinsic_value")
    bull = (dr.get("bull") or {}).get("intrinsic_value")
    pt = (dr.get("12m_targets") or {}).get("base")
    cal = dr.get("calibration_record") or {}
    fwd = cal.get("forward") or {}
    rt = dr.get("routing_trace") or {}
    ordered = None if None in (bear, iv, bull) else bool(bear <= iv <= bull)
    return {
        "profile": dr.get("profile"),
        "routing_winner": rt.get("winner"),
        "industry_routing": (rt.get("industry_routing") or {}).get("enabled"),
        "profile_fallback_used": dr.get("profile_fallback_used"),
        "anchor_method": dr.get("anchor_method"),
        "anchor_in_blend": any(e.get("method") == dr.get("anchor_method") for e in ew),
        "effective_weights": [
            {"method": e.get("method"), "value_key": e.get("value_key"), "weight": e.get("weight")} for e in ew],
        "proxied_weight": round(sum(e.get("weight") or 0 for e in proxied), 4),
        "proxied_legs": [f"{e.get('method')} <- {e.get('value_key')}" for e in proxied],
        "dropped_legs": [
            {"method": x.get("method"), "weight": x.get("weight"), "reason": x.get("reason")} for x in dropped],
        "weight_surviving": base.get("weight_surviving"),
        "methods_unavailable": dr.get("methods_unavailable"),
        "bear": bear, "base": iv, "bull": bull, "scenarios_ordered": ordered,
        "target_12m_base": pt,
        "consensus": cons,
        "iv_vs_consensus": _pct(iv, cons),
        "pt_vs_consensus": _pct(pt, cons),
        "pt_bridge_present": bool(dr.get("pt_bridge")),
        "no_peer_multiples": bool((dr.get("multiples_used") or {}).get("no_peer_multiples")),
        "backtest_status": cal.get("status"),
        "backtest_status_basis": cal.get("status_basis"),
        "backtest_t1_gap": cal.get("error_pct"),
        "backtest_forward_verdict": fwd.get("verdict"),
        "backtest_note": dr.get("calibration_note"),
        "currency": dr.get("reported_currency"),
    }


def _f(v, pct=False):
    if v is None:
        return "   -  "
    return f"{v:+6.0%}" if pct else f"{v:9.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.disable(logging.CRITICAL)
    key = os.environ.get("FMP_API_KEY")
    end = date.today().isoformat()
    out = {"wave": a.wave, "as_of": end,
           "dynamic_multiples_enabled": os.environ.get("DYNAMIC_MULTIPLES_ENABLED", "default"),
           "tickers": {}}
    for t in a.tickers:
        try:
            out["tickers"][t] = baseline_one(t, end, key)
        except Exception as exc:  # noqa: BLE001
            out["tickers"][t] = {"error": f"{type(exc).__name__}: {exc}"}
        r = out["tickers"][t]
        print(f"{t:10s} {str(r.get('profile'))[:28]:28s} "
              + (f"ERR {r['error']}" if r.get("error") else
                 f"IV {_f(r['base'])} cons {_f(r['consensus'])} {_f(r['iv_vs_consensus'], True)}  "
                 f"proxied {r['proxied_weight']:.0%}  surviving {r['weight_surviving']}  "
                 f"backtest {r['backtest_status']}"), flush=True)
    path = ROOT / (a.out or f"baseline_wave{a.wave}.json")
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf8")

    rows = [(t, r) for t, r in out["tickers"].items() if not r.get("error")]
    print(f"\n| Ticker | Profile | Anchor (in blend) | Proxied wt | Dropped | Base IV | Consensus | IV vs cons | 12m vs cons | Bear<=Base<=Bull | Backtest |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for t, r in rows:
        dl = ", ".join(f"{x['method']} {x['weight']:.0%} ({x['reason']})" for x in r["dropped_legs"]) or "-"
        print(f"| {t} | {r['profile']} | {r['anchor_method']} ({'yes' if r['anchor_in_blend'] else 'NO'}) | "
              f"{r['proxied_weight']:.0%} | {dl} | {_f(r['base']).strip()} | {_f(r['consensus']).strip()} | "
              f"{_f(r['iv_vs_consensus'], True).strip()} | {_f(r['pt_vs_consensus'], True).strip()} | "
              f"{'ok' if r['scenarios_ordered'] else 'VIOLATED' if r['scenarios_ordered'] is False else '-'} | "
              f"{r['backtest_status']} / {r['backtest_forward_verdict'] or '-'} |")
    withc = [r for _, r in rows if r["iv_vs_consensus"] is not None]
    within = sum(1 for r in withc if abs(r["iv_vs_consensus"]) <= 0.30)
    passed = sum(1 for _, r in rows if r["backtest_status"] == "passed")
    print(f"\nwithin +/-30% of consensus: {within} of {len(withc)}   "
          f"backtest passed: {passed} of {len(rows)}   "
          f"anchor missing from blend: {sum(1 for _, r in rows if not r['anchor_in_blend'])}   "
          f"errors: {len(out['tickers']) - len(rows)}")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

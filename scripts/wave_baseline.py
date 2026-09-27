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


def _regime(ticker):
    from src.data import valuation_constants as vc
    return vc.regime_deviation(ticker)


def _pct(a, b):
    return (a / b - 1.0) if (a and b) else None


def _label_sector(ticker: str) -> tuple:
    """(FMP industry label, the sector of that label's routing row) from the local comps store.

    Production classifies an unpinned name's sector with the LLM router before the pin table; this
    harness has no router, and build_state_from_lookups falls back to "Tech" for any unpinned name,
    which sends consumer, pharma and utility names down the Tech ladder (Wave 10 Stage 0, 2026-09-27).
    Seeding the sector from the name's own label row is the router's best case, stated as such."""
    import json as _json
    from src.data import db as _db
    sym = ticker
    if ticker.endswith(".HK"):
        sym = (ticker.split(".")[0].lstrip("0") or "0").zfill(4) + ".HK"
    row = _db.query_one("SELECT key FROM regional_comps_members WHERE level='industry' AND symbol=? LIMIT 1", [sym])
    label = dict(row)["key"] if row else None
    m = _json.loads((ROOT / "src/data/industry_profile_map.json").read_text(encoding="utf-8"))
    hit = (m.get("map") or {}).get(label or "")
    return label, (hit[0] if hit else None)


def baseline_one(ticker: str, end: str, key: str, sector_from_label: bool = False) -> dict:
    from src.agents.analysis import dcf_agent as d
    from src.memory.golden_state import build_state_from_lookups

    st = build_state_from_lookups(ticker, end, api_key=key)
    seeded = None
    if sector_from_label and not (st["data"].get("profile_names") or {}).get(ticker):
        label, sec = _label_sector(ticker)
        if sec:
            st["data"]["sector"] = sec
            st["data"]["sectors"] = {ticker: sec}
            seeded = {"label": label, "sector": sec}
    with contextlib.redirect_stdout(io.StringIO()):
        dr = (d.run_dcf_agent(st)["data"]["dcf_range"].get(ticker) or {})
    base = dr.get("base") or {}
    ew = base.get("effective_weights") or []
    proxied = [e for e in ew if e.get("value_key") and e.get("value_key") != e.get("method")]
    dropped = base.get("legs_dropped") or []
    cons = (dr.get("consensus_pt") or {}).get("consensus")
    spot = (dr.get("pt_bridge") or {}).get("spot")
    iv = base.get("intrinsic_value")
    bear = (dr.get("bear") or {}).get("intrinsic_value")
    bull = (dr.get("bull") or {}).get("intrinsic_value")
    pt = (dr.get("12m_targets") or {}).get("base")
    cal = dr.get("calibration_record") or {}
    fwd = cal.get("forward") or {}
    rt = dr.get("routing_trace") or {}
    ordered = None if None in (bear, iv, bull) else bool(bear <= iv <= bull)
    return {
        "seeded_sector": seeded,
        "profile": dr.get("profile"),
        "routing_winner": rt.get("winner"),
        "industry_routing": (rt.get("industry_routing") or {}).get("enabled"),
        "profile_fallback_used": dr.get("profile_fallback_used"),
        "anchor_method": dr.get("anchor_method"),
        # A trailing P/E anchor that the normalisation swap moved to P/E (norm) is
        # still the anchor, under the name it was priced on.
        "anchor_in_blend": any(e.get("method") in (dr.get("anchor_method"),
                                                   d._PE_NORM_SWAP_LEGS.get(dr.get("anchor_method") or ""))
                               for e in ew),
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
        # Owner, 2026-09-26 (dual score): the engine's own story against spot,
        # and the sell-side premium the consensus band carries.
        "spot": spot,
        "iv_vs_spot": _pct(iv, spot),
        "consensus_spread": dr.get("consensus_spread") if dr.get("consensus_spread") is not None else _pct(cons, spot),
        "regime_flag": dr.get("regime_flag"),
        "pt_vs_consensus": _pct(pt, cons),
        "pt_bridge_present": bool(dr.get("pt_bridge")),
        "no_peer_multiples": bool((dr.get("multiples_used") or {}).get("no_peer_multiples")),
        "backtest_status": cal.get("status"),
        "backtest_status_basis": cal.get("status_basis"),
        "backtest_t1_gap": cal.get("error_pct"),
        "backtest_forward_verdict": fwd.get("verdict"),
        "backtest_note": dr.get("calibration_note"),
        "currency": dr.get("reported_currency"),
        # Owner-recorded structural regime deviation, if any: counted apart in
        # the scorecard, because one decision is not eight misses.
        "regime": (_regime(ticker) or {}).get("key"),
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
    ap.add_argument("--sector-from-label", action="store_true",
                    help="seed an unpinned name's sector from its FMP label's routing row (the router's best case)")
    a = ap.parse_args()
    logging.disable(logging.CRITICAL)
    key = os.environ.get("FMP_API_KEY")
    end = date.today().isoformat()
    out = {"wave": a.wave, "as_of": end,
           "dynamic_multiples_enabled": os.environ.get("DYNAMIC_MULTIPLES_ENABLED", "default"),
           "tickers": {}}
    for t in a.tickers:
        try:
            out["tickers"][t] = baseline_one(t, end, key, sector_from_label=a.sector_from_label)
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
    print(f"\n| Ticker | Profile | Anchor (in blend) | Proxied wt | Dropped | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | 12m vs cons | Bear<=Base<=Bull | Backtest |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for t, r in rows:
        dl = ", ".join(f"{x['method']} {x['weight']:.0%} ({x['reason']})" for x in r["dropped_legs"]) or "-"
        print(f"| {t} | {r['profile']} | {r['anchor_method']} ({'yes' if r['anchor_in_blend'] else 'NO'}) | "
              f"{r['proxied_weight']:.0%} | {dl} | {_f(r['base']).strip()} | {_f(r['consensus']).strip()} | "
              f"{_f(r['iv_vs_consensus'], True).strip()} | {_f(r.get('iv_vs_spot'), True).strip()} | "
              f"{_f(r.get('consensus_spread'), True).strip()}{' ' + r['regime_flag'] if r.get('regime_flag') else ''} | "
              f"{_f(r['pt_vs_consensus'], True).strip()} | "
              f"{'ok' if r['scenarios_ordered'] else 'VIOLATED' if r['scenarios_ordered'] is False else '-'} | "
              f"{r['backtest_status']} / {r['backtest_forward_verdict'] or '-'} |")
    withc = [r for _, r in rows if r["iv_vs_consensus"] is not None]
    within = sum(1 for r in withc if abs(r["iv_vs_consensus"]) <= 0.30)
    passed = sum(1 for _, r in rows if r["backtest_status"] == "passed")
    core = [r for r in withc if not r.get("regime")]
    print(f"\nex recorded regime deviations: {sum(1 for r in core if abs(r['iv_vs_consensus']) <= 0.30)} "
          f"of {len(core)} within +/-30%   "
          f"(regime names: {', '.join(t for t, r in rows if r.get('regime')) or 'none'})")
    withs = [r for _, r in rows if r.get("iv_vs_spot") is not None]
    within_spot = sum(1 for r in withs if abs(r["iv_vs_spot"]) <= 0.30)
    flagged = [t for t, r in rows if r.get("regime_flag")]
    spreads = sorted(r["consensus_spread"] for r in withc if r.get("consensus_spread") is not None)
    med_spread = spreads[len(spreads) // 2] if spreads else None
    print(f"within +/-30% of spot: {within_spot} of {len(withs)}   "
          f"median consensus spread: {_f(med_spread, True).strip()}   "
          f"Growth_Inflection_Speculative: {', '.join(flagged) or 'none'}")
    print(f"within +/-30% of consensus: {within} of {len(withc)}   "
          f"backtest passed: {passed} of {len(rows)}   "
          f"anchor missing from blend: {sum(1 for _, r in rows if not r['anchor_in_blend'])}   "
          f"errors: {len(out['tickers']) - len(rows)}")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

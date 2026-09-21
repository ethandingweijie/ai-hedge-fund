"""IV with the dynamic multiples OFF vs ON, for a list of tickers. No writes
except the usage record a valuation makes. See docs/dynamic_multiples_handoff.md.

    python scripts/measure_dynamic_multiples_impact.py AAPL V MU VLO PSX
    python scripts/measure_dynamic_multiples_impact.py --prod AAPL V MU VLO PSX

Without --prod, DATABASE_URL is dropped so the run reads the LOCAL store: the
default is the safe one. With --prod it is kept, for a run inside a Railway
container where DATABASE_URL is the internal host -- the only place production
Postgres is reachable from a cloud session. The usage record then lands in
production, which is what the Model Accuracy "reached valuations" count reads.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
os.environ["PYTHONUTF8"] = "1"
PROD = "--prod" in sys.argv
if not PROD:
    os.environ.pop("DATABASE_URL", None)
from dotenv import load_dotenv  # noqa: E402

# Never let a .env.local DATABASE_URL override the one the container was given.
load_dotenv(ROOT / ".env.local", override=not PROD)
if not PROD:
    os.environ.pop("DATABASE_URL", None)

from src.agents.analysis import dcf_agent as d  # noqa: E402
from src.memory.golden_state import build_state_from_lookups  # noqa: E402

KEY = os.environ.get("FMP_API_KEY")
END = date.today().isoformat()
TICKERS = [a for a in sys.argv[1:] if not a.startswith("--")] or [
    "AAPL", "MSFT", "NVDA", "COST", "V", "MU", "LMT", "JPM", "VLO", "PSX", "00700.HK", "D05.SI"]
out = {}
for t in TICKERS:
    rec = {}
    for flag in ("false", "true"):
        os.environ["DYNAMIC_MULTIPLES_ENABLED"] = flag
        try:
            st = build_state_from_lookups(t, END, api_key=KEY)
            dr = (d.run_dcf_agent(st)["data"]["dcf_range"].get(t) or {})
            b = dr.get("base") or {}
            li = b.get("leg_inputs") or {}
            legs = {}
            for leg in ("EV/EBITDA (norm)", "P/E (norm)"):
                x = li.get(leg) or {}
                mp = x.get("multiple_parts") or {}
                if x:
                    legs[leg] = {"value": x.get("value"), "mult": mp.get("peer_multiple"),
                                 "src": mp.get("peer_source")}
            w = {e.get("method"): e.get("weight") for e in (b.get("effective_weights") or [])}
            rec[flag] = {"iv": b.get("intrinsic_value"), "legs": legs, "profile": dr.get("profile"),
                         "weights": {k: w.get(k) for k in legs}}
        except Exception as exc:  # noqa: BLE001
            rec[flag] = {"error": f"{type(exc).__name__}: {exc}"}
    out[t] = rec
    off, on = rec.get("false", {}), rec.get("true", {})
    ivo, ivn = off.get("iv"), on.get("iv")
    chg = (ivn / ivo - 1) if (ivo and ivn) else None
    print(f"\n{t:9s} {str(on.get('profile'))[:26]:28s} IV {ivo} -> {ivn}"
          + (f"  ({chg:+.1%})" if chg is not None else "") + (f"  ERR {on.get('error')}" if on.get("error") else ""),
          flush=True)
    for leg, v in (on.get("legs") or {}).items():
        o = (off.get("legs") or {}).get(leg) or {}
        print(f"     {leg:17s} weight {on['weights'].get(leg)}  multiple {o.get('mult')} -> {v.get('mult')}"
              f"  leg {o.get('value')} -> {v.get('value')}  [{str(v.get('src'))[:60]}]", flush=True)
json.dump(out, open(ROOT / ".cache" / "dm_impact.json", "w"), indent=1, default=str)

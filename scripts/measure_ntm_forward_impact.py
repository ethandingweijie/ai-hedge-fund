"""Base IV with NTM_FORWARD_MULTIPLES_ENABLED off vs on, for a list of tickers.

Forward P/E and Forward EV/EBITDA multiply an NTM consensus figure by a
TRAILING peer multiple today. With the flag on they read the basket's forward
median (pe_ntm, ev_ebitda_ntm) measured by the weekly comps refresh. The flag is
off by default until this measurement has been seen: a forward multiple is lower
than a trailing one for a growing basket, so every forward leg re-prices down.

    python scripts/measure_ntm_forward_impact.py GEV VST ENPH BLK 01810.HK

Reads the LOCAL store (DATABASE_URL is dropped), which must hold a comps refresh
made since the NTM fields landed. No writes except the usage record a valuation
makes. Per ticker: base IV off -> on, and per forward leg its weight, the
trailing and forward multiple, and the leg value either way.
"""
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
logging.disable(logging.CRITICAL)

from src.agents.analysis import dcf_agent as d  # noqa: E402
from src.memory.golden_state import build_state_from_lookups  # noqa: E402

LEGS = ("Forward P/E", "Forward EV/EBITDA")
KEY = os.environ.get("FMP_API_KEY")
END = date.today().isoformat()
TICKERS = [a for a in sys.argv[1:] if not a.startswith("--")]
out = {}
for t in TICKERS:
    rec = {}
    for flag in ("false", "true"):
        os.environ[d.NTM_FORWARD_FLAG] = flag
        try:
            st = build_state_from_lookups(t, END, api_key=KEY)
            with contextlib.redirect_stdout(io.StringIO()):
                dr = d.run_dcf_agent(st)["data"]["dcf_range"].get(t) or {}
            b = dr.get("base") or {}
            w = {e.get("method"): e.get("weight") for e in (b.get("effective_weights") or [])}
            legs = {}
            for leg in LEGS:
                x = (b.get("leg_inputs") or {}).get(leg) or {}
                mp = x.get("multiple_parts") or {}
                if x:
                    legs[leg] = {"value": x.get("value"), "mult": mp.get("peer_multiple"),
                                 "src": mp.get("peer_source"), "weight": w.get(leg)}
            rec[flag] = {"iv": b.get("intrinsic_value"), "profile": dr.get("profile"), "legs": legs,
                         "target": (dr.get("12m_targets") or {}).get("base")}
        except Exception as exc:  # noqa: BLE001
            rec[flag] = {"error": f"{type(exc).__name__}: {exc}"}
    out[t] = rec
    off, on = rec["false"], rec["true"]
    ivo, ivn = off.get("iv"), on.get("iv")
    chg = (ivn / ivo - 1) if (ivo and ivn) else None
    print(f"\n{t:9s} {str(on.get('profile'))[:34]:36s} IV {ivo} -> {ivn}"
          + (f"  ({chg:+.1%})" if chg is not None else "")
          + (f"  ERR {on.get('error') or off.get('error')}" if (on.get("error") or off.get("error")) else ""), flush=True)
    for leg, v in (on.get("legs") or {}).items():
        o = (off.get("legs") or {}).get(leg) or {}
        wt = v.get("weight")
        print(f"     {leg:18s} weight {wt if wt is not None else 'cross-check':>11}  multiple "
              f"{(o.get('mult') or 0):6.2f} -> {(v.get('mult') or 0):6.2f}   leg {(o.get('value') or 0):9.2f} -> "
              f"{(v.get('value') or 0):9.2f}   [{str(v.get('src'))[:44]}]", flush=True)
os.environ.pop(d.NTM_FORWARD_FLAG, None)
(ROOT / ".cache").mkdir(exist_ok=True)
json.dump(out, open(ROOT / ".cache" / "ntm_forward_impact.json", "w"), indent=1, default=str)

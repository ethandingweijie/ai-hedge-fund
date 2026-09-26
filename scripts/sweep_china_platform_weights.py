"""Owner, 2026-09-26: measure the DRAFT "China Internet Platform" profile at three
SOTP (analyst) weights before it touches the registry.

    python scripts/sweep_china_platform_weights.py --sotp-weight 0.35 \
        --out docs/baselines/china_platform_w35.json

The draft is injected at runtime (registry untouched), the five names are pinned to
it, and the filing segment-note promotion is switched off for them -- the change set
retires it, and a 0.40 anchor inserted by data would make the sweep unreadable.
Read-only apart from the usage record a valuation makes; DATABASE_URL is dropped.
"""
from __future__ import annotations

import argparse
import json
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

PROFILE = "China Internet Platform"
NAMES = ["BABA", "PDD", "JD", "03690.HK", "00700.HK"]
#: The proposed shape (owner conversation, 2026-09-26). The SOTP weight is the
#: swept parameter; the other three keep their ratios.
BASE_METHODS = [
    {"name": "DCF",            "weight": 0.30, "anchor": True,  "implementable": True},
    {"name": "SOTP (analyst)", "weight": 0.35, "anchor": False, "implementable": True},
    {"name": "Forward P/E",    "weight": 0.20, "anchor": False, "implementable": True},
    {"name": "EV/EBITDA",      "weight": 0.15, "anchor": False, "implementable": True},
]


def draft_profile(sotp_weight: float) -> dict:
    others = [m for m in BASE_METHODS if m["name"] != "SOTP (analyst)"]
    scale = (1.0 - sotp_weight) / sum(m["weight"] for m in others)
    methods = [{**m, "weight": round(m["weight"] * scale, 4)} for m in others]
    methods.insert(1, {**BASE_METHODS[1], "weight": sotp_weight})
    return {
        "methods": methods,
        "excluded": ["EV/Revenue", "P/BV"],
        "rationale": ("DRAFT. China internet platforms are conglomerates: a core commerce or "
                      "gaming business at 8-16x earnings beside cloud at 4-7x sales and "
                      "loss-making international, logistics or new-initiative arms. The "
                      "owner-accepted Gemini SOTP is the one leg that prices the parts "
                      "separately; DCF anchors, the two multiples cross-check."),
    }


def install(sotp_weight: float) -> None:
    from src.data import sector_profiles as sp
    from src.agents.analysis import dcf_agent as d
    sp.INDUSTRY_VALUATION_PROFILES["Tech"][PROFILE] = draft_profile(sotp_weight)
    for t in NAMES:
        old = sp.TICKER_SECTOR_LOOKUP.get(t)
        sp.TICKER_SECTOR_LOOKUP[t] = ("Tech", PROFILE, (old[2] if old else "Internet Platform"),
                                      (old[3] if old else t) + " [sweep pin]")
    d._SEGMENT_SOTP_TICKERS = frozenset(x for x in d._SEGMENT_SOTP_TICKERS if x not in NAMES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sotp-weight", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    install(a.sotp_weight)
    from scripts.wave_baseline import baseline_one
    from src.agents.analysis import dcf_agent as d
    from src.memory.golden_state import build_state_from_lookups
    import contextlib, io
    key = os.environ.get("FMP_API_KEY")
    end = date.today().isoformat()
    out = {"profile": PROFILE, "sotp_weight": a.sotp_weight, "methods": draft_profile(a.sotp_weight)["methods"],
           "as_of": end, "tickers": {}}
    for t in NAMES:
        try:
            rec = baseline_one(t, end, key)
            # the SOTP leg's own story: value, flags, whether the gate replaced it
            st = build_state_from_lookups(t, end, api_key=key)
            with contextlib.redirect_stdout(io.StringIO()):
                dr = d.run_dcf_agent(st)["data"]["dcf_range"].get(t) or {}
            base = dr.get("base") or {}
            tbl = base.get("method_iv_table") or {}
            rec["sotp_leg"] = tbl.get("SOTP (analyst)")
            rec["method_iv_table"] = {k: v for k, v in tbl.items()}
            rec["sotp_flags"] = [f for f in (dr.get("forward_flags") or base.get("forward_flags") or [])
                                 if "SOTP" in str(f)]
        except Exception as exc:  # noqa: BLE001
            rec = {"error": f"{type(exc).__name__}: {exc}"}
        out["tickers"][t] = rec
        r = rec
        print(f"{t:10s} w={a.sotp_weight:.2f} base={str(r.get('base'))[:9]:9s} cons={str(r.get('consensus'))[:8]:8s} "
              f"sotp_leg={str(r.get('sotp_leg'))[:9]:9s} surviving={r.get('weight_surviving')} "
              f"ew={[(e['method'], round(e['weight'], 2)) for e in (r.get('effective_weights') or [])]}", flush=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf8")
    print("written:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

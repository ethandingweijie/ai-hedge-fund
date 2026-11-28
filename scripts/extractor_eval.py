"""Extractor evaluation (owner, 2026-11-28, item 4): does extraction on qwen3.8-flash (D5) read the same numbers as the
larger model, and are they in range?

    python scripts/extractor_eval.py                     # all golden fixtures with research text, flash vs plus
    python scripts/extractor_eval.py --models qwen3.8-flash --tickers D05_SI 02888_HK
    python scripts/extractor_eval.py --out docs/extractor_eval/2026-11-28.md

Inputs are FIXED: each golden fixture's recorded deep-research text (tests/fixtures/golden/<T>/web_run.json), so the
only thing that varies is the extraction model. Families run: guidance estimates (every ticker -- it feeds DCF years
1-2), bank metrics (banks), SaaS metrics (platform / software names) and insurance metrics (insurers).

Per field it reports each model's value, whether they AGREE (within 2% relative, or 0.002 absolute for ratios), and
whether each value passes the field's plausibility range. Disagreements are the ground-truth worklist: the owner
confirms the filed figure (source URL) and the case joins tests/fixtures/extractor_eval/ as a verified expectation.

Cost: real Qwen calls (~4 extractors x fixtures x models, a few cents). Never touches production data.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.pop("DATABASE_URL", None)

#: plausibility ranges by field-name fragment (decimal units)
RANGES = {
    "cost_to_income": (0.15, 0.95), "credit_cost_bps": (0.0, 400.0), "bps": (0.0, 2000.0),
    "cet1": (0.05, 0.30), "nim": (0.003, 0.08), "efficiency": (0.15, 0.95), "npl": (0.0, 0.20),
    "roe": (-0.5, 0.6), "ldr": (0.3, 2.0), "nrr": (0.5, 2.0), "gross_margin": (-0.5, 1.0),
    "margin": (-1.0, 1.0), "growth": (-0.6, 3.0), "combined_ratio": (0.5, 1.5), "solvency": (0.5, 5.0),
}


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("value", "mid", "base"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def flatten(obj, prefix="") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            n = _num(v)
            if n is not None and not isinstance(v, dict):
                out[f"{prefix}{k}"] = n
            elif isinstance(v, dict):
                out.update(flatten(v, f"{prefix}{k}."))
    return out


def in_range(field: str, v: float):
    f = field.lower()
    for frag, (lo, hi) in RANGES.items():
        if frag in f:
            return lo <= v <= hi
    return None


def agree(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if abs(a) <= 1.0 and abs(b) <= 1.0:
        return abs(a - b) <= 0.002
    return abs(a - b) <= 0.02 * max(abs(a), abs(b))


def families_for(data: dict, ticker: str) -> list[str]:
    fams = ["guidance_estimates"]
    if (data.get("bank_metrics") or {}).get(ticker) is not None or "bank" in str(data.get("profile_name", "")).lower():
        fams.append("bank_metrics")
    if (data.get("saas_metrics") or {}).get(ticker) is not None:
        fams.append("saas_metrics")
    if (data.get("insurance_metrics") or {}).get(ticker) is not None:
        fams.append("insurance_metrics")
    return fams


def run_one(dr, client, model, fam, sections, text, ticker):
    fn = {"guidance_estimates": dr._extract_guidance_estimates, "bank_metrics": dr._extract_bank_metrics,
          "saas_metrics": dr._extract_saas_metrics, "insurance_metrics": dr._extract_insurance_metrics}[fam]
    t0 = time.monotonic()
    try:
        out = fn(client, model, sections, text, ticker)
    except Exception as exc:                                # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}", "s": time.monotonic() - t0}
    return {"values": flatten(out if isinstance(out, dict) else {}), "s": time.monotonic() - t0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["qwen3.8-flash", "qwen3.6-plus"])
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    os.environ.pop("DATABASE_URL", None)
    from src.agents.industry import deep_research as dr
    client = dr.make_sdk_client(api_key=os.environ.get("DEEP_RESEARCH_API_KEY"),
                                base_url=os.environ.get("DEEP_RESEARCH_BASE_URL"), timeout=240, max_retries=2)
    cases = []
    for f in sorted(glob.glob(str(ROOT / "tests" / "fixtures" / "golden" / "*" / "web_run.json"))):
        name = Path(f).parent.name
        if a.tickers and name not in a.tickers:
            continue
        d = json.load(open(f, encoding="utf-8"))
        data = d.get("data") or d
        text = str(data.get("deep_research") or "")
        if len(text) < 2000:
            continue
        ticker = json.load(open(Path(f).parent / "meta.json", encoding="utf-8"))["ticker"]
        sections = data.get("deep_research_sections") or dr._extract_sections(text)
        for fam in families_for(data, ticker):
            cases.append((name, ticker, fam, sections, text))

    jobs = [(c, m) for c in cases for m in a.models]
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda cm: run_one(dr, client, cm[1], cm[0][2], cm[0][3], cm[0][4], cm[0][1]), jobs))
    by = {}
    for (c, m), r in zip(jobs, res):
        by.setdefault((c[0], c[2]), {})[m] = r

    lines = ["# Extractor evaluation", "", f"Models: {', '.join(a.models)}. Fixed inputs: golden fixtures' recorded "
             "research text. AGREE = within 2% (0.002 absolute for ratios). Range = plausibility bounds.", ""]
    tot = agree_n = range_bad = errors = 0
    worklist = []
    for (name, fam), per in sorted(by.items()):
        lines += [f"## {name} — {fam}", ""]
        for m, r in per.items():
            if r.get("error"):
                errors += 1
                lines.append(f"- {m}: ERROR {r['error']} ({r['s']:.0f}s)")
        vals = {m: (r.get("values") or {}) for m, r in per.items()}
        fields = sorted(set().union(*[set(v) for v in vals.values()]))
        if not fields:
            lines += ["- no numeric fields extracted", ""]
            continue
        head = "| field | " + " | ".join(a.models) + " | agree | range |"
        lines += [head, "|" + "---|" * (len(a.models) + 3)]
        for fld in fields:
            vs = [vals[m].get(fld) for m in a.models]
            ok = all(agree(vs[0], v) for v in vs[1:]) if len(vs) > 1 else None
            rng = [in_range(fld, v) for v in vs if v is not None]
            rng_txt = "—" if not rng or all(x is None for x in rng) else ("ok" if all(x is not False for x in rng) else "OUT")
            tot += 1
            agree_n += bool(ok)
            range_bad += rng_txt == "OUT"
            if ok is False or rng_txt == "OUT":
                worklist.append(f"{name} {fam} {fld}: " + ", ".join(f"{m}={v}" for m, v in zip(a.models, vs)))
            cells = " | ".join("—" if v is None else f"{v:.4g}" for v in vs)
            lines.append(f"| {fld} | {cells} | {'yes' if ok else ('—' if ok is None else 'NO')} | {rng_txt} |")
        secs = " · ".join(f"{m} {r.get('s', 0):.0f}s" for m, r in per.items())
        lines += ["", f"_time: {secs}_", ""]
    summary = (f"**{len(by)} fixture-family cases, {tot} fields: {agree_n} agree ({(agree_n / tot if tot else 0):.0%}), "
               f"{range_bad} out of range, {errors} extractor errors.**")
    lines[3:3] = [summary, ""]
    lines += ["## Ground-truth worklist (owner to confirm the filed figure)", ""] + [f"- {w}" for w in worklist]
    text = "\n".join(lines)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Check drug-pipeline inputs against the company's own 10-K, earnings 8-K and earnings-call transcript.

    python scripts/pipeline_source_check.py REGN ALNY NTLA            # the stored pipeline entries
    python scripts/pipeline_source_check.py REGN --file draft.json    # a research draft before --ingest
    python scripts/pipeline_source_check.py REGN --json out.json      # full report with snippets

Advisory (src/data/pipeline_sources.py): per asset, the stage and regulatory date the documents print next to
it; and late-stage programmes the documents name that the input neither values, lists as approved nor excludes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(".env.local")
    except Exception:                                      # noqa: BLE001
        pass
    from src.data import industry_inputs as ii
    from src.data import pipeline_sources as ps

    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--file", help="a research draft (pipeline JSON) instead of the stored entry; one ticker")
    ap.add_argument("--json", help="write the full report here")
    a = ap.parse_args()
    report = {}
    for t in a.tickers:
        if a.file:
            data = json.load(open(a.file, encoding="utf-8"))
        else:
            e = ii.entry(t, "pipeline")
            data = (e or {}).get("data")
        if not data:
            print(f"== {t}: no pipeline entry")
            continue
        r = ps.check(data, ps.source_texts(t))
        report[t] = r
        print(f"== {t}")
        for k, v in r["sources"].items():
            print(f"   {k:10} " + (f"{v['date']}  {v['url']}" if v else "not available"))
        for x in r["assets"]:
            srcs = ", ".join(f"{k}: {v['stage'] or '-'}" + (f" ({v['date']})" if v.get("date") else "")
                             for k, v in x["sources"].items())
            print(f"   asset  {x['name'][:48]:48} input {x['phase']:9} -> {x['verdict']}" + (f"  [{srcs}]" if srcs else ""))
        unc = r["uncovered_late_stage"]
        print(f"   uncovered late-stage names in the documents: {len(unc)}")
        for m in unc[:20]:
            print(f"     {m['identifier']:22} {m['stage']:9} in {', '.join(m['sources'])}")
    if a.json:
        json.dump(report, open(a.json, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

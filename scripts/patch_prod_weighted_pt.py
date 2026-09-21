"""Third pass: the probability-weighted 12m target, wherever it was quoted.

The first two passes substituted the scenario IVs and scenario targets. The
weighted target is a different number (VLO 349.22 = 0.20x322.23 + 0.55x345.51
+ 0.25x378.99) and it was copied into research_view.target_12m,
decision_inputs.quantitative.price_target_12m, the prose, and the
reconciliation flag -- in BOTH decision blocks.

The old values are the ones read from the pre-amendment backups at the time
(OLD_WEIGHTED_PT below), never guessed.
The progress log (a record of what the original run did) and the amendment
record (which states the previous values on purpose) are left as they are.

    python patch_prod_weighted_pt.py            # dry run
    python patch_prod_weighted_pt.py --commit
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
COMMIT = "--commit" in sys.argv
TICKERS = [a for a in sys.argv[1:] if not a.startswith("-")] or ["VLO", "KMI", "COP"]
PG = re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/",
            Path(os.path.expanduser("~/.railway_pg_url")).read_text().strip())
#: The weighted 12m targets the three runs carried BEFORE the 2026-09-20
#: amendment, read from the pre-amendment backups taken at the time
#: (scenario_analysis[ticker]["12m_price_target"]). Recorded here so this pass
#: does not depend on backup files living in one machine's scratch directory.
OLD_WEIGHTED_PT = {"VLO": 349.22, "KMI": 32.4, "COP": 136.13}
SKIP = ("/data/progress_log", "/data/amendment")
TARGET_KEYS = ("/price_target", "/target_12m", "/price_target_12m")
TEXT_SCOPE = ("/decisions/", "/data/decisions/", "/data/scenario_analysis/")


def forms(v: float) -> list[str]:
    """Every way the payload prints this number: 349.22, 32.40 and 32.4."""
    out = {f"{v:,.2f}", f"{v:.2f}"}
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    if s.count(".") and len(s.split(".")[1]) >= 1:
        out.add(s)
    return sorted(out, key=len, reverse=True)


with psycopg.connect(PG, autocommit=True) as con:
    if not COMMIT:
        con.read_only = True
    for t in TICKERS:
        old_pt = OLD_WEIGHTED_PT.get(t)
        if old_pt is None:
            print(f"{t}: no recorded old weighted target -- refusing to guess")
            continue

        run_id, raw = con.execute(
            "SELECT run_id, full_result_json FROM web_runs WHERE ticker=%s AND is_checkpoint=0 "
            "ORDER BY run_at DESC LIMIT 1", [t]).fetchone()
        doc = json.loads(raw)
        new_pt = ((doc["data"].get("scenario_analysis") or {}).get(t) or {}).get("12m_price_target")
        if not isinstance(old_pt, (int, float)) or not isinstance(new_pt, (int, float)):
            print(f"{t}: missing weighted target (old {old_pt}, new {new_pt})")
            continue
        old_forms, new_s = forms(old_pt), f"{new_pt:,.2f}"
        n_str = n_num = 0

        def fix(o, p=""):
            global n_str, n_num
            if any(p.startswith(s) for s in SKIP):
                return o
            if isinstance(o, dict):
                return {k: fix(v, f"{p}/{k}") for k, v in o.items()}
            if isinstance(o, list):
                return [fix(v, f"{p}[{i}]") for i, v in enumerate(o)]
            if isinstance(o, bool):
                return o
            # Numbers are rewritten ONLY at the fields that hold the target. A
            # value-equality match alone is not enough: KMI's price history has
            # a close of exactly 32.40 on the day its target was 32.40, and an
            # entry-range bound can coincide too. Market data is never touched.
            if isinstance(o, (int, float)) and abs(o - old_pt) < 0.005:
                if p.endswith(TARGET_KEYS):
                    n_num += 1
                    return new_pt
                return o
            if isinstance(o, str) and not p.startswith(TEXT_SCOPE):
                return o
            if isinstance(o, str):
                s = o
                for f in old_forms:
                    # word-bounded so 32.4 never rewrites 32.45
                    s, k = re.subn(rf"(?<![\d.]){re.escape(f)}(?![\d])", new_s, s)
                    n_str += k
                return s
            return o

        doc = fix(doc)
        am = doc["data"].setdefault("amendment", {})
        am.setdefault("previous", {})["weighted_12m_target"] = old_pt
        print(f"== {t}  weighted 12m target {old_pt} -> {new_pt}  "
              f"({n_num} numeric, {n_str} in text)")
        if COMMIT:
            con.execute("UPDATE web_runs SET full_result_json=%s WHERE run_id=%s",
                        [json.dumps(doc, default=str), run_id])
print("WRITTEN" if COMMIT else "dry run -- nothing written")

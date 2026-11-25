"""Workbook tie-out: rebuild a run's valuation workbook, recalculate every formula, and list
each Check cell against the engine (plan 2026-10, Phase 0 step 5).

    python scripts/workbook_tieout.py RUN.json [RUN2.json ...] [--statements] [--keep DIR]

RUN.json is {"run_id", "run_at", "data": {...}} -- the web_runs payload. Without --statements
the IS/BS/CFS tabs are left empty (no FMP calls); the valuation tabs do not depend on them.
A Check passes when |value| <= 0.01 (one published rounding unit). Exit status 1 when any fails.

The workbook's own Checks tab (audit 2026-11-25) collects these tie-out checks plus the model's integrity
checks under a master flag; it is not re-counted here, but its flag is printed and must agree with this
script's verdict on the tie-out checks (a disagreement is reported as a failure). The Model's integrity suite and the
reported statements' own tie-outs (IS EBT vs reported, BS balance; "Statement mapping" for an insurer's IS) are not engine
tie-outs: the script reads them from the Checks tab, prints every failing row, and leaves them out of the exit status.

--statements loads the FMP statements, so the key in .env.local (then .env) is loaded first -- without overriding a
variable already set, and never under pytest (tests/test_env_isolation.py: the suite must not reach a live provider).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TOL = 0.01
ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> bool:
    """Load .env.local then .env from the repo root without overriding the environment. No-op under pytest."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    try:
        from dotenv import load_dotenv
    except Exception:  # noqa: BLE001
        return False
    loaded = False
    for name in (".env.local", ".env"):
        f = ROOT / name
        if f.is_file():
            loaded = load_dotenv(f, override=False) or loaded
    return loaded


def _checks(xlsx_path: str, other: list | None = None) -> list[tuple[str, str, object]]:
    """[(sheet, cell, value)] for every recalculated cell labelled Check or reading OK/REVIEW, plus the Checks tab's
    master cells (C4..C7). `other`, when given, receives (kind, "<desc> @ <go to>", status) for each Integrity /
    Statement row of the Checks tab -- the checks that are not engine tie-outs."""
    import formulas
    from openpyxl import load_workbook
    from src.utils.valuation_workbook import _MULTI_COL_CHECK_TABS, _NON_TIEOUT_TABS

    wb = load_workbook(xlsx_path)
    targets: list[tuple[str, str]] = []
    for ws in wb.worksheets:
        if ws.title in _NON_TIEOUT_TABS:
            continue
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str):
                    continue
                if v.strip().startswith("Check"):
                    # the value sits to the right of the label (same row), first formula cell
                    for cc in row[c.column:]:
                        if isinstance(cc.value, str) and cc.value.startswith("="):
                            targets.append((ws.title, cc.coordinate))
                            if ws.title not in _MULTI_COL_CHECK_TABS:
                                break
                elif (v.startswith("=IF(ABS(") or v.startswith("=IF(ROUND(ABS(")) and '"REVIEW"' in v:
                    targets.append((ws.title, c.coordinate))
                elif ws.title == "Multiples" and c.column == 17 and v.startswith("="):
                    targets.append((ws.title, c.coordinate))
    model = formulas.ExcelModel().loads(xlsx_path).finish()
    sol = {k.upper(): v for k, v in model.calculate().items()}
    name = Path(xlsx_path).name.upper()
    targets = [(s, c) for s, c in targets if s != "Checks"]
    if "Checks" in wb.sheetnames:
        targets.extend([("Checks", "C4"), ("Checks", "C5"), ("Checks", "C6"), ("Checks", "C7")])

    def value(sheet: str, cell: str):
        val = sol.get(f"'[{name}]{sheet.upper()}'!{cell}".upper())
        v = getattr(val, "value", val)
        try:
            v = v[0][0] if hasattr(v, "__getitem__") and not isinstance(v, str) else v
        except Exception:  # noqa: BLE001
            pass
        return v

    out = [(sheet, cell, value(sheet, cell)) for sheet, cell in targets]
    if "Checks" in wb.sheetnames and other is not None:
        ws = wb["Checks"]
        for rr in range(9, ws.max_row + 1):
            kind = ws.cell(row=rr, column=1).value
            if kind in ("Integrity", "Statement", "Statement mapping"):
                other.append((kind, f"{ws.cell(row=rr, column=4).value} @ {ws.cell(row=rr, column=7).value}",
                              value("Checks", f"F{rr}")))
    return out


def tieout(run_path: str, statements: bool = False, keep: str | None = None) -> int:
    from src.utils.valuation_workbook import build_workbook

    run = json.load(open(run_path, encoding="utf-8"))
    data = run.get("data") or {}
    fails = 0
    for ticker in (data.get("dcf_range") or {}):
        loaders = {}
        if statements:
            from app.backend.services.workbook_export_service import _members, load_statements
            loaders = {"load_members": _members, "load_statements": load_statements}
        blob = build_workbook(run, ticker, **loaders)
        d = keep or tempfile.mkdtemp()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{ticker.replace('.', '_')}_tieout.xlsx")
        Path(path).write_bytes(blob)
        other: list = []
        rows = _checks(path, other)
        master = {c: v for s, c, v in rows if s == "Checks"}
        rows = [x for x in rows if x[0] != "Checks"]
        bad = []
        for sheet, cell, v in rows:
            ok = (isinstance(v, str) and v.strip() in ("OK", "ALL OK")) or (
                isinstance(v, (int, float)) and round(abs(float(v)), 6) <= TOL)
            if not ok:
                bad.append((sheet, cell, v))
        fails += len(bad)
        print(f"{ticker}: {len(rows) - len(bad)}/{len(rows)} checks pass  ({path})")
        for sheet, cell, v in bad:
            print(f"   FAIL {sheet}!{cell} = {v}")
        if master:
            tie_fail = master.get("C5")
            agrees = isinstance(tie_fail, (int, float)) and int(tie_fail) == len(bad)
            print(f"   Checks tab master flag: {master.get('C4')} (tie-out failing {tie_fail}, integrity failing "
                  f"{master.get('C6')}, statement failing {master.get('C7') if not isinstance(master.get('C7'), float) else int(master['C7'])})"
                  f"{'' if agrees else ' -- DISAGREES with this tie-out'}")
            if not agrees:
                fails += 1
        for kind, desc, status in other:
            if status != "OK":
                print(f"   {kind.upper()} FAIL (not an engine tie-out; outside the exit status): {desc}")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--statements", action="store_true")
    ap.add_argument("--keep")
    a = ap.parse_args()
    if a.statements:
        _load_env()
    total = sum(tieout(p, a.statements, a.keep) for p in a.runs)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()

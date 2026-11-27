"""Quarterly commodity price deck for the depleting-asset DCF (owner, 2026-11-27, I2 option 3A).

The owner downloads one day's settlements -- CME Group for WTI (CL) and Henry Hub (NG), ICE Report Center for
Brent (B) and Newcastle coal -- and this script turns them into the deck in valuation_constants.json:

    python scripts/commodity_deck.py --settlements WTI=cl.csv HENRY_HUB=ng.csv BRENT=brent.csv \
        --long-run BRENT=70 WTI=65 HENRY_HUB=3.6 --base-from-fmp
    python scripts/commodity_deck.py --accept --reviewer owner          # records the deck as ACCEPTED
    python scripts/commodity_deck.py --accept-mix COP,OXY --reviewer owner

Proposing never changes a valuation: the engine reads the deck only once it is ACCEPTED, and a ticker only once
its mix is ACCEPTED (dcf_agent.commodity_price_index). Every write sets the deck back to PROPOSED unless --accept.

A settlement file is any CSV with a contract-month column (header containing "month" or "contract": JAN 27,
Jan-27, Jan27, 2027-01, January 2027) and a settlement column (header containing "settle"). The strip for a
calendar year is the mean of its contract months; only years after the as-of year, up to three, are kept.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CONSTANTS = ROOT / "src" / "data" / "valuation_constants.json"
FMP_FRONT_MONTH = {"BRENT": "BZUSD", "WTI": "CLUSD", "HENRY_HUB": "NGUSD"}
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                       "DEC"], start=1)}


def parse_month(text: str) -> tuple[int, int] | None:
    """(year, month) from a contract label, or None."""
    s = str(text or "").strip().upper()
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^([A-Z]{3})[A-Z]*[\s\-/]*'?(\d{2,4})$", s)
    if m and m.group(1) in _MONTHS:
        y = int(m.group(2))
        return (2000 + y if y < 100 else y), _MONTHS[m.group(1)]
    return None


def strip_from_csv(path: str, as_of_year: int, years: int = 3) -> dict[str, float]:
    """{"2027": mean settle, ...} for the calendar years after `as_of_year` present in the file."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: no rows")
    cols = list(rows[0].keys())
    mcol = next((c for c in cols if c and ("month" in c.lower() or "contract" in c.lower())), None)
    scol = next((c for c in cols if c and "settle" in c.lower()), None)
    if not (mcol and scol):
        raise ValueError(f"{path}: need a contract-month and a settlement column, got {cols}")
    by_year: dict[int, list[float]] = {}
    for r in rows:
        ym = parse_month(r.get(mcol))
        try:
            v = float(str(r.get(scol) or "").replace(",", "").rstrip("'AB"))
        except ValueError:
            continue
        if ym and v > 0:
            by_year.setdefault(ym[0], []).append(v)
    out = {}
    for y in range(as_of_year + 1, as_of_year + 1 + years):
        vals = by_year.get(y) or []
        if len(vals) >= 6:                       # a thin back end is not a calendar average
            out[str(y)] = round(statistics.mean(vals), 4)
    return out


def fmp_trailing_average(commodity: str, as_of: date) -> tuple[float, str] | None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local", override=True)
    from src.data.regional_comps import _fmp_get, _STABLE
    sym = FMP_FRONT_MONTH.get(commodity)
    if not sym:
        return None
    frm = (as_of - timedelta(days=365)).isoformat()
    rows = _fmp_get(f"{_STABLE}/historical-price-eod/light", {"symbol": sym, "from": frm, "to": as_of.isoformat()},
                    api_key=None)
    px = [float(r.get("price") or r.get("close")) for r in (rows or []) if (r.get("price") or r.get("close"))]
    if len(px) < 150:
        return None
    return round(statistics.mean(px), 4), f"FMP {sym} front-month, mean of daily closes {frm}..{as_of.isoformat()}"


def _kv(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if not v:
            raise SystemExit(f"expected KEY=VALUE, got {it!r}")
        out[k.strip().upper()] = v.strip()
    return out


def _write_block(raw: str, block: dict) -> str:
    """Replace the commodity_price_deck block in the file text, leaving every other block's formatting alone."""
    doc = json.loads(raw)
    if "commodity_price_deck" not in doc:
        raise SystemExit("valuation_constants.json has no commodity_price_deck block")
    if list(doc)[-1] != "commodity_price_deck":
        raise SystemExit("commodity_price_deck must be the file's last block (this writer replaces to the end)")
    start = raw.index('\n  "commodity_price_deck": ') + 1
    body = json.dumps(block, indent=2, ensure_ascii=False)
    body = "\n".join("  " + ln if i else ln for i, ln in enumerate(body.split("\n")))
    new = raw[:start] + '  "commodity_price_deck": ' + body + "\n}\n"
    if json.loads(new)["commodity_price_deck"] != block:
        raise SystemExit("write check failed")
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settlements", nargs="*", default=[], help="COMMODITY=path.csv")
    ap.add_argument("--long-run", nargs="*", default=[], help="COMMODITY=price")
    ap.add_argument("--spot", nargs="*", default=[], help="COMMODITY=price (for a commodity with no strip)")
    ap.add_argument("--base-from-fmp", action="store_true", help="trailing 12m base prices for Brent/WTI/Henry Hub")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--accept", action="store_true", help="owner: record the deck as ACCEPTED")
    ap.add_argument("--accept-mix", default="", help="owner: comma-separated tickers whose mixes to accept")
    ap.add_argument("--reviewer", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    raw = CONSTANTS.read_text(encoding="utf-8")
    block = json.loads(raw)["commodity_price_deck"]
    comms = block.setdefault("commodities", {})
    as_of = date.fromisoformat(a.as_of)
    changed = []

    for c, path in _kv(a.settlements).items():
        strip = strip_from_csv(path, as_of.year)
        if not strip:
            raise SystemExit(f"{c}: no calendar year after {as_of.year} has 6+ contract months in {path}")
        comms.setdefault(c, {})["strip"] = strip
        comms[c]["source"] = f"Settlements file {Path(path).name}, as of {as_of.isoformat()} (scripts/commodity_deck.py)"
        changed.append(f"{c} strip {strip}")
    for c, v in _kv(a.long_run).items():
        comms.setdefault(c, {})["long_run"] = float(v)
        changed.append(f"{c} long run {v}")
    for c, v in _kv(a.spot).items():
        comms.setdefault(c, {})["spot"] = float(v)
        changed.append(f"{c} spot {v}")
    if a.base_from_fmp:
        for c in FMP_FRONT_MONTH:
            got = fmp_trailing_average(c, as_of)
            if got:
                comms.setdefault(c, {})["base_price"], comms[c]["base_price_basis"] = got
                changed.append(f"{c} base {got[0]}")

    if changed:
        block["as_of"] = as_of.isoformat()
        block["status"] = "PROPOSED"           # any new figure goes back to the owner
    if a.accept:
        if not a.reviewer:
            raise SystemExit("--accept needs --reviewer")
        block["status"] = "ACCEPTED"
        block["reviewed"] = {"last_reviewed": date.today().isoformat(), "reviewer": a.reviewer,
                             "cadence": "quarterly"}
        changed.append("deck ACCEPTED")
    for tk in [t.strip().upper() for t in a.accept_mix.split(",") if t.strip()]:
        if not a.reviewer:
            raise SystemExit("--accept-mix needs --reviewer")
        mix = (block.get("mixes") or {}).get(tk)
        if not mix:
            raise SystemExit(f"no proposed mix for {tk}")
        mix["status"], mix["accepted"] = "ACCEPTED", f"{a.reviewer}, {date.today().isoformat()}"
        changed.append(f"{tk} mix ACCEPTED")

    for c, spec in sorted(comms.items()):
        print(f"  {c:10} base {spec.get('base_price')!s:8} strip {spec.get('strip')} long run {spec.get('long_run')}")
    print(f"  status {block.get('status')}  as of {block.get('as_of')}")
    if not changed:
        print("(nothing to change)")
        return 0
    print("changes: " + "; ".join(changed))
    if a.dry_run:
        print("(dry run: not written)")
        return 0
    CONSTANTS.write_text(_write_block(raw, block), encoding="utf-8", newline="\n")
    print(f"written {CONSTANTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

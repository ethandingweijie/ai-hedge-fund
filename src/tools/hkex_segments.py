"""Segment footnote from an HKEX annual-report PDF.

HK primary listings have no SEC route and no FMP segmentation -- FMP's
`revenue-product-segmentation` and `revenue-geographic-segmentation` both
return [] for HK and SG, verified live. The annual report PDF on HKEXnews is
the only mechanical source, and it is a real one: the reports are text-based
(not scans), 3-14MB, and current.

This mirrors `src/tools/sec_segments.py` and returns the SAME schema, so the
SOTP bridge does not care which market a segment map came from.

Why coordinates rather than text
--------------------------------
A PDF has no table structure. `get_text()` returns a flat token stream, and a
segment table's header is split across several lines -- Tencent's reads
"FinTech and" / "Marketing Business" / "VAS Services Services Others Total"
over three y-bands. Reading that as text mis-assigns every column. So numeric
rows are found first, their geometry defines the columns, and header words are
then assigned to whichever column they physically sit above.

The profit measure is NOT assumed
---------------------------------
IFRS 8 reports whatever the CODM reviews. Tencent's segment measure is GROSS
PROFIT, AIA's is operating profit, Alibaba's is adjusted EBITA. An engine that
computes `ebit x (1-tax) x multiple` on gross profit overstates enormously, so
the label the filer actually used is carried out with the number and never
normalised here.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path(os.environ.get(
    "HKEX_REPORT_CACHE_DIR",
    Path(__file__).resolve().parents[2] / ".cache" / "hkex_reports"))

_NUM_RE = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$")
_SEG_PAGE_RE = re.compile(
    r"segment information|reportable segment|segment revenue", re.I)
_YEAR_BLOCK_RE = re.compile(
    r"(?:year|period)\s+ended\s+(\d{1,2}\s+\w+\s+20\d\d)|"
    r"year\s+ended\s+\w+\s+(\d{1,2}),?\s+(20\d\d)", re.I)

# Row labels, by what they measure. Order matters: the profit patterns are
# tried before revenue so "gross profit" is never read as a revenue line.
_PROFIT_ROW = re.compile(
    r"^(?:segment\s+)?(?:gross profit|operating profit|adjusted ebita|"
    r"segment (?:profit|result|results)|profit from operations|"
    r"operating income)", re.I)
_REVENUE_ROW = re.compile(
    r"^(?:segment\s+)?(?:revenues?|turnover|net sales|"
    r"revenue from external customers)", re.I)
_DA_ROW = re.compile(r"^(?:depreciation|amortisation|amortization)", re.I)
_TOTAL_NAME = re.compile(r"^(?:total|group|consolidated)$", re.I)
# The apostrophe in "RMB’Million" is U+2019, not ASCII. Matching only the
# ASCII form left the units token glued onto every column name, which in
# turn stopped "Total" being recognised and lost both the consolidated
# revenue and the reporting currency.
_APOS = "['‘’´`]?"
_UNIT_RE = re.compile(r"(RMB|HK\$|US\$|S\$|USD|HKD|CNY|SGD|¥|€)"
                      r"\s*" + _APOS + r"\s*"
                      r"(million|m|billion|bn|000|thousand)", re.I)


def _units_and_ccy(text: str) -> tuple[float, Optional[str]]:
    m = _UNIT_RE.search(text or "")
    if not m:
        return 1.0, None
    ccy = {"RMB": "CNY", "HK$": "HKD", "US$": "USD", "S$": "SGD"}.get(
        m.group(1).upper().replace("HK$", "HK$"), m.group(1).upper())
    scale = m.group(2).lower()
    mult = (1e9 if scale.startswith(("billion", "bn"))
            else 1e3 if scale in ("000", "thousand") else 1e6)
    return mult, ccy


def _to_float(tok: str) -> Optional[float]:
    t = (tok or "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _lines(page) -> list[dict]:
    """Page words grouped into visual lines, each keeping word geometry."""
    buckets: dict[int, list] = defaultdict(list)
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        buckets[round(y0 / 3.0)].append((x0, x1, w))
    out = []
    for key in sorted(buckets):
        cells = sorted(buckets[key])
        out.append({"y": key * 3.0,
                    "words": cells,
                    "text": " ".join(w for _, _, w in cells)})
    return out


def _numeric_cells(words) -> list[tuple[float, float, float]]:
    """(x0, x1, value) for every numeric token on a line."""
    out = []
    for x0, x1, w in words:
        if _NUM_RE.match(w):
            v = _to_float(w)
            if v is not None:
                out.append((x0, x1, v))
    return out


def _label_of(words) -> str:
    parts = []
    for _x0, _x1, w in words:
        if _NUM_RE.match(w):
            break
        parts.append(w)
    return " ".join(parts).strip()


def _column_centers(data_lines: list[list[tuple[float, float, float]]]
                    ) -> Optional[list[float]]:
    """Column centres inferred from the numeric rows themselves.

    Centres, not right edges: a header word sits centred over its column while
    the numbers below are right-aligned, so matching a header against a right
    edge biases every assignment leftward.
    """
    widths = [len(c) for c in data_lines if c]
    if not widths:
        return None
    n = max(set(widths), key=widths.count)          # modal column count
    mids: list[list[float]] = [[] for _ in range(n)]
    for cells in data_lines:
        if len(cells) != n:
            continue
        for i, (x0, x1, _v) in enumerate(cells):
            mids[i].append((x0 + x1) / 2.0)
    if not all(mids):
        return None
    return [sum(m) / len(m) for m in mids]


def _header_names(header_lines: list[dict],
                  centers: list[float]) -> list[str]:
    """Stitch a multi-line header into one name per column.

    Words are assigned to the NEAREST column centre, not to a bounded range.
    Tencent's "FinTech and" is centred over column 2 but its first word sits at
    x=389, two points inside column 1's range -- a containment test puts it in
    the wrong column and yields "FinTech and Marketing Services".

    Reading order is restored per column by y then x, so a name split over
    three lines ("FinTech and" / "Business" / "Services") comes back in the
    order a human reads it.
    """
    picked: list[list[tuple[float, float, str]]] = [[] for _ in centers]
    for ln in header_lines:
        for x0, x1, w in ln["words"]:
            if _NUM_RE.match(w) or _UNIT_RE.search(w):
                continue
            mid = (x0 + x1) / 2.0
            i = min(range(len(centers)), key=lambda k: abs(centers[k] - mid))
            picked[i].append((ln["y"], x0, w))
    return [" ".join(w for _y, _x, w in sorted(col)).strip() for col in picked]


def _parse_page(page) -> list[dict]:
    """Every year-block table on one page.

    A single HKEX page routinely carries the current year and the comparative
    stacked vertically -- which is a gain over the SEC route, because segment
    growth history comes out of the same parse.
    """
    lines = _lines(page)
    page_mult, page_ccy = _units_and_ccy(page.get_text())

    # Split the page into blocks, each starting at a "Year ended ..." heading.
    starts = [i for i, ln in enumerate(lines)
              if _YEAR_BLOCK_RE.search(ln["text"])]
    blocks = []
    if starts:
        for j, s in enumerate(starts):
            e = starts[j + 1] if j + 1 < len(starts) else len(lines)
            blocks.append((lines[s]["text"], lines[s:e]))
    else:
        blocks.append(("", lines))

    out = []
    for heading, blk in blocks:
        metric_rows, header_lines = [], []
        for ln in blk:
            cells = _numeric_cells(ln["words"])
            label = _label_of(ln["words"])
            if len(cells) >= 2 and label:
                kind = ("profit" if _PROFIT_ROW.match(label)
                        else "revenue" if _REVENUE_ROW.match(label)
                        else "da" if _DA_ROW.match(label) else None)
                if kind:
                    metric_rows.append({"kind": kind, "label": label,
                                        "cells": cells, "y": ln["y"]})
                continue
            if not cells:
                header_lines.append(ln)
        if not metric_rows:
            continue
        centers = _column_centers([r["cells"] for r in metric_rows])
        if not centers or len(centers) < 3:    # >=2 segments plus a total
            continue
        # The header is whatever sits above the FIRST METRIC row. Cutting at
        # the first line containing any number instead puts the cutoff on the
        # "Year ended 31 December 2025" heading, because "31" parses as one --
        # which excluded every header line and produced blank column names.
        first_metric_y = min(r["y"] for r in metric_rows)
        hdr = [ln for ln in header_lines if ln["y"] < first_metric_y]
        names = _header_names(hdr[-6:], centers)
        mult, ccy = _units_and_ccy(" ".join(l["text"] for l in hdr)) \
            if any(_UNIT_RE.search(l["text"]) for l in hdr) else (page_mult,
                                                                 page_ccy)
        out.append({"heading": heading.strip(), "names": names,
                    "rows": metric_rows, "units_multiplier": mult,
                    "currency": ccy})
    return out


def _block_to_segments(block: dict) -> Optional[dict]:
    """Turn one parsed year-block into segments + the filer's profit label."""
    names, mult = block["names"], block["units_multiplier"]
    n = len(names)
    rev = next((r for r in block["rows"] if r["kind"] == "revenue"), None)
    if rev is None or len(rev["cells"]) != n:
        return None
    prof = next((r for r in block["rows"]
                 if r["kind"] == "profit" and len(r["cells"]) == n), None)

    segs, total_rev = [], None
    for i, raw in enumerate(names):
        name = re.sub(r"\s+", " ", raw).strip()
        value = rev["cells"][i][2] * mult
        if not name or _TOTAL_NAME.match(name):
            total_rev = value
            continue
        p = prof["cells"][i][2] * mult if prof else None
        segs.append({"name": name, "revenue": value, "profit": p,
                     "margin": (p / value) if (p is not None and value) else None,
                     "assets": None, "member": ""})
    if len(segs) < 2:
        return None
    return {"segments": segs,
            "consolidated_revenue": total_rev,
            "profit_label": (prof["label"] if prof else None),
            "currency": block["currency"],
            "heading": block["heading"]}


def _report_path(ticker: str, url: str) -> Path:
    import hashlib
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "_" for c in ticker)
    return _CACHE_DIR / f"{safe}_{key}.pdf"


def _fetch_report(ticker: str, url: str) -> Optional[Path]:
    """Annual report PDF, cached on disk -- these are 3-14MB."""
    p = _report_path(ticker, url)
    if p.exists() and p.stat().st_size > 10_000:
        return p
    try:
        import requests
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=180)
        if r.status_code != 200 or not r.content:
            return None
        p.write_bytes(r.content)
        return p
    except Exception as exc:                           # noqa: BLE001
        print(f"  [hkex_segments] {ticker}: download failed "
              f"({type(exc).__name__})")
        return None


def _candidate_pages(doc, limit: int = 6) -> list[int]:
    """Pages that look like a segment table: the words AND enough numbers."""
    scored = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if not _SEG_PAGE_RE.search(text):
            continue
        nums = len(re.findall(r"\d{1,3}(?:,\d{3})+", text))
        if nums >= 12:
            scored.append((nums, i))
    scored.sort(reverse=True)
    return [i for _n, i in scored[:limit]]


def get_segment_footnote(ticker: str, end_date: str) -> Optional[dict]:
    """Segment map for an HKEX listing, or None. Never raises."""
    try:
        import fitz                                    # PyMuPDF
    except ImportError:
        print("  [hkex_segments] PyMuPDF not installed")
        return None
    try:
        from src.tools.hkex_api import get_hkex_filing_refs
        ref = get_hkex_filing_refs(ticker) or {}
    except Exception as exc:                           # noqa: BLE001
        print(f"  [hkex_segments] {ticker}: filing lookup failed "
              f"({type(exc).__name__})")
        return None
    url = ref.get("filing_url")
    if not url:
        return None
    path = _fetch_report(ticker, url)
    if not path:
        return None

    try:
        doc = fitz.open(path)
    except Exception:                                  # noqa: BLE001
        return None

    from src.tools.segment_normalize import (
        _drop_hierarchy_parents, _filter_segments, _is_geographic,
    )

    best = None
    for page_no in _candidate_pages(doc):
        for block in _parse_page(doc[page_no]):
            built = _block_to_segments(block)
            if not built:
                continue
            segs = _filter_segments(
                _drop_hierarchy_parents(built["segments"],
                                        built.get("consolidated_revenue")),
                built.get("consolidated_revenue"))
            if len(segs) < 2:
                continue
            built["segments"] = segs
            built["page"] = page_no
            # Prefer the block whose revenue reconciles best to the group.
            cons = built.get("consolidated_revenue")
            gap = (abs(sum(s["revenue"] for s in segs) / cons - 1.0)
                   if cons else 1.0)
            if best is None or gap < best[0]:
                best = (gap, built)
    if best is None:
        return None
    gap, built = best
    if gap > 0.6:
        print(f"  [hkex_segments] {ticker}: segments sum {gap:+.0%} from the "
              f"group total -- rejected rather than half-parsed")
        return None

    segs = built["segments"]
    return {
        "ticker": ticker,
        "segments": segs,
        "n_segments": len(segs),
        "consolidated_revenue": built.get("consolidated_revenue"),
        "segment_sum_revenue": sum(s["revenue"] for s in segs),
        "sum_vs_consolidated": (
            sum(s["revenue"] for s in segs) / built["consolidated_revenue"]
            if built.get("consolidated_revenue") else None),
        "profit_label": built.get("profit_label"),
        # Tencent reports GROSS profit by segment. Saying so is the difference
        # between a valuation and a fiction, because the engine multiplies
        # whatever it is handed by (1-tax) and a P/E.
        "profit_is_operating_income": bool(
            built.get("profit_label") and
            re.search(r"operating (?:profit|income)",
                      built["profit_label"], re.I)),
        "profit_disclosed": any(s.get("profit") is not None for s in segs),
        "assets_disclosed": False,
        "reported_currency": built.get("currency"),
        "period_end": ref.get("filing_date") or end_date,
        "fiscal_period": built.get("heading"),
        "segment_axis": ("geographic" if _is_geographic(segs)
                         else "business_line"),
        "source_url": url,
        "report_page": built.get("page"),
        "form": ref.get("filing_type") or "Annual Report",
        "warnings": [],
    }

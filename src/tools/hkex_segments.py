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
_SEG_WORD_RE = re.compile(r"segment", re.I)
_NUM_ANY_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_SEG_PAGE_RE = re.compile(
    r"segment information|reportable segment|segment revenue", re.I)
_YEAR_BLOCK_RE = re.compile(
    r"(?:year|period)\s+ended\s+(\d{1,2}\s+\w+\s+20\d\d)|"
    r"year\s+ended\s+\w+\s+(\d{1,2}),?\s+(20\d\d)", re.I)

# Row labels, by what they measure. Order matters: the profit patterns are
# tried before revenue so "gross profit" is never read as a revenue line.
# HK filings are bilingual: a row reads "Operating income from external
# transactions 對外交易收入". Anchored matching fails on the Chinese suffix, so it
# is stripped before a label is classified.
_CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+")


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", _CJK.sub(" ", label or "")).strip(" :-")


# PRC filers say "operating income" where an IFRS filer says REVENUE --
# YOFC's revenue line is "Operating income from external transactions". The
# profit patterns used to prefix-match "operating income", so that revenue row
# was classified as profit and the table came back with no revenue at all.
# Anything naming external customers is revenue, and it is tested FIRST.
_EXTERNAL_REV_ROW = re.compile(
    r"^(?:operating income|revenues?|sales|turnover)[\s,]*"
    r"(?:from|to)?[\s]*external", re.I)
_PROFIT_ROW = re.compile(
    r"^(?:segment\s+)?(?:gross profit(?:/?\(loss\))?|"
    r"operating profit(?:/?\(loss\))?|adjusted ebita|"
    r"segment (?:profit|result|results|operating profit)"
    r"(?:/?\(loss\))?(?:,.*)?|"
    r"profit from operations?|operating income)$", re.I)
_REVENUE_ROW = re.compile(
    r"^(?:total\s+|segment\s+|external\s+)?"
    r"(?:revenues?|turnover|net sales|sales|operating income)"
    r"(?:\s+from\s+(?:external\s+)?(?:customers|contracts).*)?$", re.I)
_DA_ROW = re.compile(r"^(?:including:\s*)?"
                     r"(?:depreciation|amortisation|amortization)", re.I)
_TOTAL_NAME = re.compile(r"^(?:total|group|consolidated)$", re.I)
# Units come in two tokenisations and BOTH occur. Tencent writes
# "RMB’Million" as one token (with a U+2019 apostrophe, not ASCII); Cathay
# writes "HK$M", which PyMuPDF splits into "HK$" and "M". A combined
# currency-plus-scale pattern matches neither of Cathay's halves, so the units
# multiplier stayed 1.0 -- every revenue read as ~0 -- and the leftover tokens
# glued onto the column names as "Total HK$M HK$M". Match the two parts
# independently and a split token is no longer a special case.
_APOS = "['‘’´`]?"
_CCY_RE = re.compile(r"(RMB|HK\$|US\$|S\$|USD|HKD|CNY|SGD|¥|€)", re.I)
_SCALE_RE = re.compile(r"(millions?|bn\b|billions?|thousands?|m\b|000)", re.I)
_CCY_TOKEN = re.compile(r"^" + _APOS + r"(RMB|HK\$|US\$|S\$|USD|HKD|CNY|SGD|"
                        r"¥|€)" + _APOS + r"$", re.I)
_SCALE_TOKEN = re.compile(r"^" + _APOS +
                          r"(millions?|bn|billions?|thousands?|m|000)$", re.I)
# A single token carrying both, e.g. "RMB’Million" or "HK$M".
_UNIT_RE = re.compile(r"(RMB|HK\$|US\$|S\$|USD|HKD|CNY|SGD|¥|€)"
                      r"\s*" + _APOS + r"\s*"
                      r"(millions?|m\b|billions?|bn\b|000|thousands?)", re.I)


def _is_unit_token(w: str) -> bool:
    """True for any token that is units plumbing rather than a segment name."""
    return bool(_UNIT_RE.search(w) or _CCY_TOKEN.match(w)
                or _SCALE_TOKEN.match(w))


def _units_and_ccy(text: str) -> tuple[float, Optional[str]]:
    """(multiplier, currency) from a units caption, halves matched separately."""
    t = text or ""
    cm, sm = _CCY_RE.search(t), _SCALE_RE.search(t)
    if not sm:
        return 1.0, None
    ccy = None
    if cm:
        raw = cm.group(1).upper()
        ccy = {"RMB": "CNY", "HK$": "HKD", "US$": "USD", "S$": "SGD",
               "¥": "CNY", "€": "EUR"}.get(raw, raw)
    scale = sm.group(1).lower()
    mult = (1e9 if scale.startswith(("billion", "bn"))
            else 1e3 if scale in ("000", "thousand", "thousands") else 1e6)
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


# A nil entry prints as a dash, not a zero. Skipping it makes a row carry
# FEWER cells than its neighbours, the modal column count goes ambiguous,
# and the block is discarded -- YOFC's segment table has rows of 4, 5 and
# 6 cells for exactly this reason. A standalone dash is a zero.
_DASH_RE = re.compile(r"^[-\u2010-\u2015\u2212]+$")


def _numeric_cells(words) -> list[tuple[float, float, float]]:
    """(x0, x1, value) for every numeric token on a line."""
    out = []
    for x0, x1, w in words:
        if _DASH_RE.match(w):
            out.append((x0, x1, 0.0))
            continue
        if _NUM_RE.match(w):
            v = _to_float(w)
            if v is not None:
                out.append((x0, x1, v))
    return out


def _label_of(words) -> str:
    parts = []
    for _x0, _x1, w in words:
        if _NUM_RE.match(w) or _DASH_RE.match(w):
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


# Period words sit in the same x-bands as the column headers -- "Year ended
# 31 December 2025" spans the table -- and get glued onto segment names as
# "Year ended Smartphone x AIoT" or "December Other related business". They
# are never part of a segment's name, so they are dropped before assignment.
_PERIOD_WORD = re.compile(
    r"^(?:year|years|period|periods|ended|ending|as|at|of|for|the|note|notes|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|20\d\d|\(?[ivx]+\)?)$", re.I)


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
            if (_NUM_RE.match(w) or _is_unit_token(w)
                    or _PERIOD_WORD.match(w)):
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
                lab = _clean_label(label)
                kind = ("revenue" if _EXTERNAL_REV_ROW.match(lab)
                        else "profit" if _PROFIT_ROW.match(lab)
                        else "revenue" if _REVENUE_ROW.match(lab)
                        else "da" if _DA_ROW.match(lab) else None)
                if kind:
                    metric_rows.append({"kind": kind, "label": lab,
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
        name = _clean_label(raw)
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


# A filer that declares ONE operating segment has no SOTP by construction, and
# that is a different answer from "the parser could not read this". MiniMax:
# "the Group has only one single operating segment and no further analysis of
# the single segment"; CATL: "the management believes that the Company has only
# one operating segment and does not need to prepare a segment report".
# Reporting both as "no segment map" would send someone hunting a parser bug
# that does not exist, and would hide that the valuation method is wrong for
# the company rather than merely unavailable.
_SINGLE_SEGMENT_RE = re.compile(
    r"(?:only |has |as )(?:a )?(?:one|single)(?: single)?\s+"
    r"(?:operating|reportable|business)\s+segment|"
    r"one reportable segment", re.I)

_LAST_REASON: dict = {}


def last_reason(ticker: str) -> str:
    """Why the last lookup produced nothing, for diagnostics."""
    return _LAST_REASON.get(ticker, "")


def declares_single_segment(doc) -> bool:
    """True when the filing states it has one operating segment."""
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if not re.search(r"segment", text, re.I):
            continue
        if _SINGLE_SEGMENT_RE.search(text):
            return True
    return False


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


def _structure_score(page) -> tuple[int, int, int]:
    """How much a page LOOKS like a segment income table.

    Returns (has_both, n_metric_rows, n_numbers). Keyword-plus-number-density
    ranked Ping An's segment BALANCE SHEET above its income table and BYD's
    ASC 606 timing table above its segment note -- both are dense with numbers
    and both say "segment". What actually distinguishes the table wanted is
    structural: a revenue row AND a profit row, over the same columns.
    """
    rev = prof = 0
    for ln in _lines(page):
        cells = _numeric_cells(ln["words"])
        if len(cells) < 2:
            continue
        lab = _clean_label(_label_of(ln["words"]))
        if not lab:
            continue
        if _EXTERNAL_REV_ROW.match(lab) or _REVENUE_ROW.match(lab):
            rev += 1
        elif _PROFIT_ROW.match(lab):
            prof += 1
    nums = len(_NUM_ANY_RE.findall(page.get_text()))
    return (1 if (rev and prof) else 0, rev + prof, nums)


def _candidate_pages(doc, limit: int = 8) -> list[int]:
    """Pages worth parsing, best first.

    Stage one is a cheap text filter on the word "segment" -- narrower phrase
    matching missed these filings entirely, since Ping An mentions
    "segment information" on only two of 382 pages. Stage two ranks what
    survives by structure rather than by density.
    """
    scored = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if not _SEG_WORD_RE.search(text):
            continue
        if len(_NUM_ANY_RE.findall(text)) < 6:
            continue
        both, rows, nums = _structure_score(doc[i])
        # Structure RANKS, it never excludes. Requiring two recognised metric
        # rows to be a candidate at all dropped Lenovo and SMIC from three
        # candidate pages to none -- a page whose rows this does not recognise
        # yet is exactly the page worth still trying.
        scored.append((both, rows, nums, i))
    scored.sort(reverse=True)
    return [i for _b, _r, _n, i in scored[:limit]]


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
        _LAST_REASON[ticker] = (
            "filer declares a single operating segment -- SOTP is not "
            "applicable, this is not a parse failure"
            if declares_single_segment(doc)
            else "no page parsed to >=2 reconciling segments")
        return None
    gap, built = best
    if gap > 0.6:
        print(f"  [hkex_segments] {ticker}: segments sum {gap:+.0%} from the "
              f"group total -- rejected rather than half-parsed")
        return None

    segs = built["segments"]

    # If the filer's own measure is already operating income there is nothing
    # to derive. Where it is gross profit (Tencent) or another pre-opex
    # measure, size the gap from the consolidated income statement and split
    # it by an explicit rule -- an unconverted 60% gross margin reaching an
    # engine that computes `ebit x (1-tax) x multiple` overstates enormously.
    label = built.get("profit_label") or ""
    is_opinc = bool(re.search(r"operating (?:profit|income)", label, re.I))
    opex_detail = None
    if not is_opinc and any(s.get("profit") is not None for s in segs):
        income = _income_statement_page(doc)
        if income:
            opex_detail = _allocate_opex(segs, income)
        if opex_detail is None:
            print(f"  [hkex_segments] {ticker}: segment measure is "
                  f"'{label}' and no income statement was parsed -- profit "
                  f"left as reported, NOT operating income")

    return {
        "ticker": ticker,
        "segments": segs,
        "n_segments": len(segs),
        "consolidated_revenue": built.get("consolidated_revenue"),
        "segment_sum_revenue": sum(s["revenue"] for s in segs),
        "sum_vs_consolidated": (
            sum(s["revenue"] for s in segs) / built["consolidated_revenue"]
            if built.get("consolidated_revenue") else None),
        "profit_label": ("Operating profit (derived from "
                         f"{built.get('profit_label')})" if opex_detail
                         else built.get("profit_label")),
        "reported_profit_label": built.get("profit_label"),
        # Tencent reports GROSS profit by segment. Saying so is the difference
        # between a valuation and a fiction, because the engine multiplies
        # whatever it is handed by (1-tax) and a P/E. Once central opex has
        # been allocated the number IS an operating profit -- but a derived
        # one, and `profit_basis` says so.
        "profit_is_operating_income": bool(is_opinc or opex_detail),
        "profit_basis": ("reported" if is_opinc
                         else "derived_gross_less_central_opex" if opex_detail
                         else "reported_not_operating_income"),
        "opex_allocation": opex_detail,
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


# ── Gross profit -> operating profit ────────────────────────────────────────
# Tencent's segment measure is GROSS profit, and the filing says why in as many
# words: "selling and marketing and administrative expenses ... are managed
# centrally ... therefore, they are not included in the measure of segment
# performance." So there is no segment opex to extract -- the filer does not
# allocate it, and any allocation is OUR assumption.
#
# What the filing does give, on the consolidated income statement, is every
# line needed to size the gap: gross profit, the central opex lines, and the
# operating profit they reconcile to. So the total is disclosed and only the
# SPLIT is assumed, which is the same discipline used everywhere else here --
# the filing supplies the level, an explicit rule supplies the split.
#
# The default rule is pro-rata to revenue. Selling cost broadly scales with
# sales, and revenue is the only segment-level base the filer discloses that
# is not already inside gross profit (the segment D&A lines sit within cost of
# revenues, so they cannot allocate opex). It is stated, not hidden: every
# derived figure carries `profit_basis` saying exactly how it was produced.

_IS_ROWS = {
    # Filers write these lines several ways. "Operating profit/(loss)" and
    # "Profit from operations" are the same line as "Operating profit"; missing
    # the variant means the whole derivation is skipped and a gross margin
    # reaches the engine as though it were operating.
    "gross_profit": re.compile(r"^gross profit(?:/?\(loss\))?$", re.I),
    "selling": re.compile(r"^selling and (?:marketing|distribution) "
                          r"(?:expenses?|costs?)$", re.I),
    "admin": re.compile(r"^(?:general and )?administrative expenses?$", re.I),
    "other_gains": re.compile(r"^other gains?/?\(?losses?\)?,?\s*net$", re.I),
    "operating_profit": re.compile(
        r"^(?:operating profit(?:/?\(loss\))?|profit from operations?|"
        r"operating (?:profit|income)(?:\s*/\s*\(loss\))?)$", re.I),
    "revenues": re.compile(r"^(?:revenues?|turnover)$", re.I),
}


def _parse_income_statement(page) -> Optional[dict]:
    """Group income-statement lines from the consolidated statement page."""
    mult, ccy = _units_and_ccy(page.get_text())
    out: dict[str, float] = {}
    for ln in _lines(page):
        label = _label_of(ln["words"])
        cells = _numeric_cells(ln["words"])
        if not label or not cells:
            continue
        for key, rx in _IS_ROWS.items():
            if key in out or not rx.match(label.strip()):
                continue
            # A "Note" column holds a small reference integer, never money.
            money = [c for c in cells if abs(c[2]) >= 100]
            if money:
                out[key] = money[0][2] * mult
    if "gross_profit" not in out or "operating_profit" not in out:
        return None
    out["currency"] = ccy
    return out


def _income_statement_page(doc) -> Optional[dict]:
    hits = []
    for i in range(doc.page_count):
        t = doc[i].get_text()
        # IFRS filers title it "Statement of Profit or Loss"; only US-style
        # filers say "Income Statement". Requiring the latter found the
        # statement for Tencent and missed it for Cathay and Xiaomi, so
        # both were left carrying gross profit as though it were EBIT.
        if not re.search(r"income statement|profit or loss|"
                         r"statement of comprehensive income", t, re.I):
            continue
        if not re.search(r"operating (?:profit|loss)", t, re.I):
            continue
        hits.append(i)
    for i in hits:
        parsed = _parse_income_statement(doc[i])
        if parsed:
            parsed["page"] = i
            return parsed
    return None


def _allocate_opex(segments: list[dict], income: dict,
                   weights: Optional[dict] = None) -> Optional[dict]:
    """Derive segment operating profit from gross profit and central opex.

    Returns the allocation detail, and mutates each segment to carry both the
    reported gross profit and the derived operating profit. The reported
    figure is never overwritten -- a derived number that cannot be traced back
    to what the filer actually said is worse than no number.
    """
    gp = income.get("gross_profit")
    op = income.get("operating_profit")
    if not gp or op is None:
        return None
    seg_rev = sum(s["revenue"] for s in segments if s.get("revenue"))
    if seg_rev <= 0:
        return None

    # `weights` lets a better split override revenue pro-rata -- sell-side
    # models do publish segment operating margins, and the filer's silence is
    # what makes an outside estimate worth having here rather than a liberty.
    # It is normalised and labelled, never blended: a valuation should be able
    # to say which split produced it.
    basis = "pro_rata_revenue"
    if weights:
        picked = {s["name"]: float(weights[s["name"]]) for s in segments
                  if s.get("name") in weights and weights[s["name"]] is not None}
        if len(picked) == len(segments) and sum(picked.values()) > 0:
            total_w = sum(picked.values())
            shares = {k: v / total_w for k, v in picked.items()}
            basis = "supplied_weights"
        else:
            shares = None
    else:
        shares = None

    # Central costs are what stands between the two disclosed lines. Taking
    # the difference rather than summing the expense lines keeps this right
    # for filers whose statement carries lines we do not enumerate.
    central = gp - op
    for s in segments:
        s["gross_profit"] = s.get("profit")
        s["gross_margin"] = s.get("margin")
        share = (shares[s["name"]] if shares
                 else (s["revenue"] / seg_rev) if s.get("revenue") else 0.0)
        if s.get("profit") is None:
            s["operating_profit"] = None
            continue
        s["operating_profit"] = s["profit"] - central * share
        s["profit"] = s["operating_profit"]
        s["margin"] = (s["operating_profit"] / s["revenue"]
                       if s.get("revenue") else None)
    return {
        "central_opex": central,
        "group_gross_profit": gp,
        "group_operating_profit": op,
        "basis": basis,
        "income_statement_page": income.get("page"),
    }

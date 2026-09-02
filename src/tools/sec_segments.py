"""SEC segment-footnote extraction (ASC 280 / IFRS 8).

Reads the reportable-segment table straight out of a filer's 10-K or 20-F and
returns segment revenue AND segment profit. This is the only source in the repo
that carries segment *economics* -- FMP's `revenue-product-segmentation` gives
revenue only and returns nothing at all for HK/SG venues, so today segment
margins are either invented by an LLM from research prose or model-filled by
`apply_margin_basis`.

Route
-----
`data.sec.gov/submissions/CIK##########.json`  -> newest 10-K / 20-F <= end_date
`Archives/edgar/data/{cik}/{accn}/FilingSummary.xml` -> the segment R-file
`.../R##.htm`                                  -> one small rendered table

SEC's `companyfacts` API is deliberately NOT used: its facts carry no
dimension/member field (keys are accn/end/filed/form/fp/frame/fy/start/val), so
segment axes are unreachable there. The rendered R-files are the cheapest place
the dimensional data is actually exposed.

Parsing
-------
Keyed on the XBRL QNames the renderer leaves in the DOM rather than on English
labels, which makes it work across filers and filing agents:

    <tr class="rh">  group header, defref "<axis>=<member>"
                     e.g. us-gaap_StatementBusinessSegmentsAxis=aapl_AmericasSegmentMember
                          srt_ConsolidationItemsAxis=us-gaap_CorporateNonSegmentMember
    <tr class="re">  data row, defref "<element>"
                     e.g. us-gaap_Revenues, us-gaap_OperatingIncomeLoss
    td.nump          positive number      td.num  negative      td.text  blank

Value columns are identified positionally from the trailing `num|nump|text`
cells, because a blank cell is still a cell: BABA's USD convenience column is
populated ONLY on consolidated rows and renders as `td.text` on every segment
row. Filtering blanks out silently shifts every segment onto the prior year's
number.

Soft-fail throughout: every failure path returns None rather than raising or
emitting a partial map, so callers can treat absence as "no filing route".
"""
from __future__ import annotations

import re
import time
import urllib.request
from typing import Any, Optional

from bs4 import BeautifulSoup

# SEC blocks requests without a real User-Agent and monitors request rate.
_SEC_UA = "AI-Hedge-Fund (research@aihedgefund.local)"
_MIN_INTERVAL_SEC = 0.12
_LAST_CALL_TS: float = 0.0

# Parsed results, keyed (filer_ticker, end_date). None is cached too: a filer
# with one reportable segment (PDD) or no SEC listing (3690.HK) must not be
# re-fetched for every ticker in a multi-ticker run.
_SEG_CACHE: dict[tuple[str, str], Optional[dict]] = {}
_LAST_REJECTION: dict[str, str] = {}

# HK/other cross-listings that borrow a US filer's 20-F. Keyed on the CANONICAL
# form so 4-digit, 5-digit and bare-numeric inputs all resolve to one entry --
# this repo has been bitten repeatedly by 4-vs-5-digit HK keys (the SOTP
# snapshot's "3690.HK" can never attach because the pipeline canonicalises to
# "03690.HK"). An explicit None means "known to have no SEC filer" and
# short-circuits before any network call.
_ADR_FILER_ALIAS: dict[str, Optional[str]] = {
    "09988.HK": "BABA",   # Alibaba
    "09618.HK": "JD",     # JD.com
    "09888.HK": "BIDU",   # Baidu
    "01810.HK": None,     # Xiaomi   -- no SEC filer
    "03690.HK": None,     # Meituan  -- MPNGY is unsponsored, no SEC filing
    "00700.HK": None,     # Tencent  -- TCEHY is unsponsored
}

_ANNUAL_FORMS = ("10-K", "20-F")

# /segment/i alone is not selective: MSFT's 10-K yields 5 candidates and AMZN's
# 10. Exclude the segment-flavoured tables that are not the operating table,
# then score, then prove by parsing.
# Always excluded: a narrative or schedule block, never the numbers.
_HARD_EXCLUDE_RE = re.compile(r"\(tables?\)|\(polic", re.IGNORECASE)
# Excluded unless a strong positive signal overrides -- a title can legitimately
# combine subjects, e.g. AAPL's correct table is "Segment Information and
# GEOGRAPHIC Data - Information by Reportable Segment (Details)". Excluding on
# "geographic" alone would drop the very report being looked for.
_SOFT_EXCLUDE_RE = re.compile(
    r"goodwill|intangible|long-lived|property|equipment|depreciation|"
    r"amortization expense|unearned|deferred|countries|disaggregation|"
    r"additional information|balance sheet|assets",
    re.IGNORECASE,
)
_STRONG_RE = re.compile(
    r"reportable segment|schedule of segment|reconciliation|operating income|"
    r"revenue,\s*cost of revenue", re.IGNORECASE)
_SCORE_TERMS = (
    (re.compile(r"reportable", re.I), 3),
    (re.compile(r"schedule of segment", re.I), 3),
    (re.compile(r"reconciliation", re.I), 3),
    (re.compile(r"operating income", re.I), 3),
    (re.compile(r"revenue,\s*cost of revenue", re.I), 3),
    (re.compile(r"information by reportable segment", re.I), 3),
    (re.compile(r"\(detail", re.I), 1),
)

# Group-header labels that qualify a segment rather than name one.
_MEMBER_TAIL = re.compile(r"\s*\[(?:member|domain|axis)\]\s*$", re.I)

_QUALIFIERS = {
    "operating segments", "total segments", "reportable segments",
    "segment, continuing operations", "consolidation, eliminations",
    "operating segments, excluding intersegment elimination",
}
_CORPORATE_MEMBERS = re.compile(
    r"CorporateNonSegment|CorporateAndOther|UnallocatedAmount", re.I)
_ELIMINATION_MEMBERS = re.compile(
    r"IntersegmentElimination|MaterialReconcilingItems|Elimination", re.I)
_CORPORATE_LABELS = {
    "corporate non-segment", "unallocated items", "corporate and other",
    "unallocated", "corporate", "unallocated corporate",
}
_ELIMINATION_LABELS = {
    "inter-segment", "intersegment", "intersegment elimination",
    "eliminations", "reconciling items", "inter-segment elimination",
}

# Metric identification, QName first (language-independent), label as fallback.
_REVENUE_QNAME = re.compile(
    r"(?:^|_)Revenues?$|RevenueFromContractWithCustomer|"
    r"SegmentReportingInformationRevenue|RevenueNet|NetRevenues", re.I)
_PROFIT_QNAME = re.compile(
    r"OperatingIncomeLoss|AdjustedEBITA|SegmentOperatingIncome|"
    r"ProfitLossFromOperating|IncomeLossFromOperations", re.I)
_REVENUE_LABEL = re.compile(
    r"^(?:total\s+)?(?:net\s+)?(?:revenues?|net sales|total revenues?)$", re.I)
_PROFIT_LABEL = re.compile(
    r"^(?:income/?\(?loss\)?\s+from operations|operating income(?:\s*\(loss\))?|"
    r"adjusted ebita|segment (?:profit|result))", re.I)
_SKIP_QNAME = re.compile(r"LineItems$|Abstract$|NumberOf.*Segments?", re.I)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _throttle() -> None:
    """Block until _MIN_INTERVAL_SEC has passed since the last SEC call."""
    global _LAST_CALL_TS
    elapsed = time.time() - _LAST_CALL_TS
    if elapsed < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - elapsed)
    _LAST_CALL_TS = time.time()


def _get_text(url: str, timeout: int = 30) -> Optional[str]:
    """GET an SEC resource as text. None on any failure -- never raises."""
    try:
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": _SEC_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:      # noqa: BLE001 -- soft-fail by contract
        print(f"  [sec_segments] fetch failed {url[:90]}: {type(exc).__name__}")
        return None


def _resolve_filer(ticker: str) -> Optional[str]:
    """Map a ticker to the SEC filer whose segment footnote covers it.

    Returns None for anything with no SEC route -- an HK/SG primary listing, or
    a cross-listing explicitly known to have no filer.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return None
    try:
        from src.tools.ticker_canonical import canonical_ticker
        canon = canonical_ticker(t)
    except Exception:             # noqa: BLE001
        canon = t
    for key in (canon, t):
        if key in _ADR_FILER_ALIAS:
            return _ADR_FILER_ALIAS[key]
    try:
        from src.tools.hk.ticker import is_hk_ticker
        from src.tools.sg.ticker import is_sg_ticker
        if is_hk_ticker(t) or is_sg_ticker(t):
            return None
    except Exception:             # noqa: BLE001
        pass
    # SEC writes a share class with a HYPHEN -- BRK-B, BF-B, LEN-B -- and no
    # SEC ticker contains a dot. A dotted class ticker resolves to no CIK at
    # all, which silently reads as "this filer has no segment footnote".
    return t.replace(".", "-")


def _parse_units(header: str) -> float:
    """USD/CNY multiplier declared in the table header ('$ in Millions')."""
    h = (header or "").lower()
    if "in billions" in h:
        return 1e9
    if "in millions" in h:
        return 1e6
    if "in thousands" in h:
        return 1e3
    return 1.0


def _parse_period(text: str) -> Optional[str]:
    """'Sep. 27, 2025' or '2025-09-27' -> ISO date string."""
    s = (text or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"([A-Za-z]{3})[a-z.]*\.?\s+(\d{1,2}),\s*(\d{4})", s)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"
    return None


def _parse_ccy(text: str) -> Optional[str]:
    """'CNY (yuan)' -> 'CNY'. Non-currency column labels return None."""
    m = re.search(r"\b([A-Z]{3})\b", (text or "").upper())
    return m.group(1) if m else None


def _cell_value(td) -> Optional[float]:
    """Numeric value of a value cell, honouring the renderer's own sign class.

    td.num marks a negative (parenthesised) figure and td.nump a positive one,
    so the sign never has to be inferred from glyphs -- which matters because
    currency symbols vary by filer.
    """
    classes = td.get("class") or []
    kind = classes[0] if classes else ""
    if kind == "text":
        return None
    raw = td.get_text(" ", strip=True).replace("\xa0", " ")
    body = re.sub(r"[^\d.]", "", raw.replace("(", "").replace(")", ""))
    if not body or body in {".", ""}:
        return None
    try:
        val = float(body)
    except ValueError:
        return None
    if kind == "num" or raw.strip().startswith("("):
        val = -abs(val)
    return val


def _group_kind(defref: str, label: str) -> tuple[str, Optional[str]]:
    """Classify a <tr class="rh"> group header.

    Returns (kind, name) where kind is segment | corporate | elimination |
    role_marker. A role marker is a bare qualifier such as 'Total segments',
    whose rows are the all-segments aggregate and must never be emitted as a
    segment of its own.
    """
    if _CORPORATE_MEMBERS.search(defref) or label.lower().strip() in _CORPORATE_LABELS:
        return "corporate", label
    if _ELIMINATION_MEMBERS.search(defref) or label.lower().strip() in _ELIMINATION_LABELS:
        return "elimination", label
    parts = [p.strip() for p in label.split("|") if p.strip()]
    residual = [p for p in parts if p.lower() not in _QUALIFIERS]
    if not residual:
        return "role_marker", None
    name = residual[-1] if len(parts) > 1 else residual[0]
    return "segment", _strip_member_suffix(name)


def _strip_member_suffix(name: str) -> str:
    """Drop the XBRL '[Member]' / '[Domain]' tail from a presentation label.

    Filers that label segment rows off the member itself carry the axis tail
    through to the name -- BRK-B reports 'BNSF [Member]', 'McLane [Member]'.
    It is XBRL plumbing, not the segment's name, and it reaches the report and
    the archetype classifier verbatim.
    """
    return _MEMBER_TAIL.sub("", name or "").strip() or (name or "").strip()


def _metric_of(defref: str, label: str) -> Optional[str]:
    """'revenue' | 'profit' | None for a data row."""
    if _SKIP_QNAME.search(defref):
        return None
    if _PROFIT_QNAME.search(defref):
        return "profit"
    if _REVENUE_QNAME.search(defref):
        return "revenue"
    lab = (label or "").strip()
    if _PROFIT_LABEL.match(lab):
        return "profit"
    if _REVENUE_LABEL.match(lab):
        return "revenue"
    return None


def _defref(tr) -> str:
    a = tr.find("a")
    if not a:
        return ""
    m = re.search(r"defref_([A-Za-z0-9_=.\-]+)", a.get("onclick", "") or "")
    return m.group(1) if m else ""


def _row_label(tr) -> str:
    a = tr.find("a")
    return a.get_text(" ", strip=True) if a else tr.get_text(" ", strip=True)


def _parse_segment_table(html: str) -> Optional[dict]:
    """Parse one rendered R-file into segments, corporate and eliminations."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="report")
    if table is None:
        return None
    rows = table.find_all("tr")
    if len(rows) < 3:
        return None

    title = rows[0].get_text(" ", strip=True)
    units = _parse_units(title)

    columns: list[dict] = []
    for th in rows[1].find_all(["th", "td"]):
        divs = [d.get_text(" ", strip=True) for d in th.find_all("div")]
        text = th.get_text(" ", strip=True)
        period = _parse_period(divs[0] if divs else text)
        unit = divs[1] if len(divs) > 1 else None
        ccy = _parse_ccy(unit) if unit else None
        # A column carrying an explicit non-currency unit is a COUNT column,
        # not money -- JD's segment table leads with "Mar. 31, 2024 | segment"
        # holding `Number of Reportable Segments = 3`. It parses as a period,
        # so testing the date alone would admit it and shift every value one
        # column left. A column with no unit div at all inherits the table's
        # single currency (AAPL) and is monetary.
        monetary = period is not None and (unit is None or ccy is not None)
        columns.append({"period_end": period, "currency": ccy,
                        "unit": unit, "monetary": monetary})
    n_cols = len(columns)
    if n_cols == 0:
        return None

    groups: dict[str, dict] = {}
    order: list[str] = []
    consolidated: dict[str, dict] = {}
    current: Optional[str] = None
    profit_qname: str = ""

    for tr in rows[2:]:
        classes = tr.get("class") or []
        kind_cls = classes[0] if classes else ""
        defref = _defref(tr)
        label = _row_label(tr)
        if kind_cls in ("rh", "rhu"):
            kind, name = _group_kind(defref, label)
            if kind == "role_marker" or not name:
                current = None
                continue
            # One key per HEADER OCCURRENCE, not per name: a filer may report
            # the same axis member twice -- JD emits "JD Logistics" (external
            # revenue only, 80,314m) and "JD Logistics | Operating segments"
            # (total including intersegment, 217,146m). Collapsing them here
            # would silently keep whichever came first, a 2.7x error. They are
            # reconciled by _dedupe_groups once all rows are read.
            key = f"{kind}:{name}:{len(order)}"
            groups[key] = {"name": name, "kind": kind, "defref": defref,
                           "revenue": {}, "profit": {}, "raw_label": label,
                           "qualified": "|" in label}
            order.append(key)
            current = key
            continue
        metric = _metric_of(defref, label)
        if metric is None:
            continue
        value_cells = [td for td in tr.find_all("td")
                       if (td.get("class") or [""])[0] in ("num", "nump", "text")]
        if len(value_cells) < n_cols:
            continue
        value_cells = value_cells[-n_cols:]
        by_period: dict[str, float] = {}
        for col, td in zip(columns, value_cells):
            if not col["monetary"] or col["period_end"] is None:
                continue
            val = _cell_value(td)
            if val is None:
                continue
            by_period[f"{col['period_end']}|{col['currency'] or ''}"] = val * units
        if not by_period:
            continue
        if metric == "profit" and defref and not profit_qname:
            # Which profit line the filer actually reports. BABA's is
            # AdjustedEBITA (pre-SBC, pre-amortisation) rather than
            # OperatingIncomeLoss, and the engine multiplies whatever it gets
            # by (1-tax) x P/E -- so the distinction has to survive the parse.
            profit_qname = defref
        target = (groups[current][metric] if current
                  else consolidated.setdefault(metric, {}))
        for k, v in by_period.items():
            target.setdefault(k, v)

    return {"title": title, "units_multiplier": units, "columns": columns,
            "groups": _dedupe_groups([groups[k] for k in order]),
            "consolidated": consolidated, "profit_qname": profit_qname}


def _dedupe_groups(groups: list[dict]) -> list[dict]:
    """Collapse repeated headers for the same segment.

    JD reports `JD Logistics` twice under one axis member: bare (external
    revenue only, 80,314m) and `JD Logistics | Operating segments` (total
    including intersegment, 217,146m). Keeping the first occurrence understates
    that segment by 2.7x. Prefer the qualified header, then the one carrying
    more metrics, then the larger revenue.
    """
    def _score(g: dict) -> tuple:
        n_metrics = sum(1 for m in ("revenue", "profit") if g.get(m))
        top_rev = max(g["revenue"].values()) if g["revenue"] else 0.0
        return (1 if g.get("qualified") else 0, n_metrics, top_rev)

    best: dict[str, dict] = {}
    order: list[str] = []
    for g in groups:
        key = f"{g['kind']}:{re.sub(r'[^a-z0-9]', '', g['name'].lower())}"
        if key not in best:
            best[key] = g
            order.append(key)
        elif _score(g) > _score(best[key]):
            best[key] = g
    return [best[k] for k in order]


def _score_segment_report(short_name: str, long_name: str = "") -> Optional[int]:
    """Rank a FilingSummary report as a segment-table candidate.

    None means hard-excluded. A bare /segment/i match is not selective enough:
    MSFT's 10-K offers 5 candidates and AMZN's 10, most of them segment-sliced
    goodwill, assets or D&A tables. The score only orders the fetch queue --
    acceptance is decided by actually parsing the table.
    """
    text = f"{short_name} {long_name}"
    if not re.search(r"segment", text, re.I):
        return None
    short = short_name or ""
    if _HARD_EXCLUDE_RE.search(short):
        return None
    if _SOFT_EXCLUDE_RE.search(short) and not _STRONG_RE.search(short):
        return None
    return sum(pts for rx, pts in _SCORE_TERMS if rx.search(text))


def _filing_summary_reports(cik: str, accession: str) -> list[dict]:
    """[{short_name, long_name, html_file}] from a filing's FilingSummary.xml."""
    acc_nd = accession.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{acc_nd}/FilingSummary.xml")
    xml = _get_text(url)
    return _parse_filing_summary_xml(xml) if xml else []


def _parse_filing_summary_xml(xml: str) -> list[dict]:
    """Pure parse of FilingSummary.xml -- kept separate so it is testable."""
    out: list[dict] = []
    for block in re.findall(r"<Report[^>]*>(.*?)</Report>", xml, re.S):
        html_file = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", block, re.S)
        if not html_file:                       # some reports are XML-only
            continue
        short = re.search(r"<ShortName>(.*?)</ShortName>", block, re.S)
        long_ = re.search(r"<LongName>(.*?)</LongName>", block, re.S)
        out.append({"short_name": (short.group(1).strip() if short else ""),
                    "long_name": (long_.group(1).strip() if long_ else ""),
                    "html_file": html_file.group(1).strip()})
    return out


def _latest_annual_filing(cik: str, end_date: str) -> Optional[dict]:
    """Newest 10-K/20-F filed on or before end_date.

    The point-in-time filter is not optional: the baseline snapshot was built
    at a fixed end_date, and without it the evaluation silently drifts as new
    filings land.
    """
    import json
    subs = _get_text(
        f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")
    if not subs:
        return None
    try:
        data = json.loads(subs)
    except Exception:                            # noqa: BLE001
        return None
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    best: Optional[dict] = None
    for i, form in enumerate(forms):
        if form not in _ANNUAL_FORMS:
            continue
        filed = dates[i] if i < len(dates) else ""
        if end_date and filed and filed > end_date:
            continue
        cand = {"form": form, "filed": filed,
                "accession": accs[i] if i < len(accs) else ""}
        if best is None or cand["filed"] > best["filed"]:
            best = cand
    return best


def _implied_usd_rate(parsed: dict, ccy_key: str,
                      usd_key: str) -> Optional[float]:
    """The filing's own convenience-translation rate, from a row carrying both.

    Preferred over a live FX quote because it is the rate the issuer actually
    used, so segment values reconcile to the filing's own stated USD totals.
    Only consolidated rows carry the USD column -- every segment row renders it
    blank -- so it has to be read from the consolidated block.
    """
    for metric in ("revenue", "profit"):
        vals = (parsed.get("consolidated") or {}).get(metric) or {}
        ccy_val, usd_val = vals.get(ccy_key), vals.get(usd_key)
        if ccy_val and usd_val and ccy_val != 0:
            rate = usd_val / ccy_val
            if 0.0001 < rate < 10000:
                return rate
    return None


def get_segment_footnote(ticker: str, end_date: str) -> Optional[dict]:
    """Reportable-segment revenue and profit from the latest 10-K / 20-F.

    Returns None -- never a partial map, never an exception -- when the ticker
    has no SEC filer, no annual filing on or before `end_date`, no recognisable
    segment table, or fewer than two reportable segments. A single-segment
    filer (PDD) is a legitimate None: there is nothing to sum.
    """
    filer = _resolve_filer(ticker)
    if not filer:
        _LAST_REJECTION[ticker] = "no SEC filer for this listing"
        return None
    cache_key = (filer, end_date or "")
    if cache_key in _SEG_CACHE:
        cached = _SEG_CACHE[cache_key]
        return dict(cached, ticker=ticker) if cached else None
    result = _build_segment_footnote(ticker, filer, end_date)
    _SEG_CACHE[cache_key] = result
    return result


def _build_segment_footnote(ticker: str, filer: str,
                            end_date: str) -> Optional[dict]:
    try:
        from src.tools.api import _get_cik
        cik = _get_cik(filer)
    except Exception:                            # noqa: BLE001
        cik = None
    if not cik:
        _LAST_REJECTION[ticker] = f"no CIK for {filer}"
        return None

    filing = _latest_annual_filing(cik, end_date)
    if not filing:
        _LAST_REJECTION[ticker] = f"no 10-K/20-F on or before {end_date}"
        return None

    reports = _filing_summary_reports(cik, filing["accession"])
    scored = []
    for rep in reports:
        score = _score_segment_report(rep["short_name"], rep["long_name"])
        if score is not None:
            scored.append((score, rep))
    if not scored:
        _LAST_REJECTION[ticker] = "no segment report in FilingSummary"
        return None
    scored.sort(key=lambda x: -x[0])

    acc_nd = filing["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nd}"
    built = None
    for _score, rep in scored[:3]:
        html = _get_text(f"{base}/{rep['html_file']}")
        if not html:
            continue
        parsed = _parse_segment_table(html)
        if not parsed:
            continue
        built = _assemble(parsed, ticker, filer, cik, filing, rep, base)
        if built:
            break
    if not built:
        _LAST_REJECTION[ticker] = "no candidate report parsed to >=2 segments"
        return None

    built["segment_axis"] = ("geographic"
                             if _is_geographic(built["segments"])
                             else "business_line")
    built["profit_disclosed"] = any(s.get("profit") is not None
                                    for s in built["segments"])
    if built["segment_axis"] != "geographic":
        return built

    # The reportable segments are places. Look for the revenue disaggregation
    # instead -- iPhone/Mac/Services rather than Americas/Europe. It is
    # revenue-only by construction (ASC 606 requires the split, ASC 280
    # requires profit only for reportable segments), so the margin layer has
    # to supply economics for these names. That is a real limitation, not a
    # parser gap: no filer discloses iPhone operating income.
    swapped = _business_line_segments(cik, filing, reports, base,
                                      built.get("consolidated_revenue"))
    if swapped:
        built["segments"] = swapped["segments"]
        built["n_segments"] = len(swapped["segments"])
        built["report_file"] = swapped["report_file"]
        built["report_short_name"] = swapped["short_name"]
        built["source_url"] = f"{base}/{swapped['report_file']}"
        built["segment_axis"] = "business_line"
        built["profit_disclosed"] = False
        built["segment_sum_revenue"] = sum(x["revenue"] for x in swapped["segments"])
        cons = built.get("consolidated_revenue")
        built["sum_vs_consolidated"] = (
            built["segment_sum_revenue"] / cons if cons else None)
        built.setdefault("warnings", []).append(
            "reportable segments are geographic; segment map taken from the "
            "revenue disaggregation, which discloses no segment profit")
    return built


def _business_line_segments(cik: str, filing: dict, reports: list[dict],
                            base: str,
                            group_revenue: Optional[float] = None
                            ) -> Optional[dict]:
    """Revenue split by product or business line, when one is disclosed."""
    cands = [r for r in reports
             if _BUSINESS_LINE_RE.search(r["short_name"])
             and re.search(r"detail", r["short_name"], re.I)
             and not _HARD_EXCLUDE_RE.search(r["short_name"])]
    for rep in cands[:4]:
        html = _get_text(f"{base}/{rep['html_file']}")
        if not html:
            continue
        parsed = _parse_segment_table(html)
        if not parsed:
            continue
        monetary = [c for c in parsed["columns"] if c["monetary"]]
        if not monetary:
            continue
        key = f"{monetary[0]['period_end']}|{monetary[0]['currency'] or ''}"
        segs = []
        for g in parsed["groups"]:
            if g["kind"] != "segment":
                continue
            rev = g["revenue"].get(key)
            if rev is None or rev <= 0:
                continue
            segs.append({"name": g["name"], "revenue": rev, "profit": None,
                         "margin": None, "member": g.get("defref", ""),
                         "revenue_by_period": g.get("revenue", {}),
                         "profit_by_period": {}})
        # A disaggregation table usually has no pre-group consolidated row --
        # its total sits INSIDE the table as a row (COST prints "Net Sales"
        # beside the merchandise categories). Validate against the group
        # revenue from the reportable-segment table instead: it is the same
        # number whichever way the revenue is split.
        cons = ((parsed["consolidated"].get("revenue") or {}).get(key)
                or group_revenue)
        segs = _filter_segments(segs, cons)
        if len(segs) >= 2 and not _is_geographic(segs):
            return {"segments": segs, "report_file": rep["html_file"],
                    "short_name": rep["short_name"]}
    return None
    _LAST_REJECTION[ticker] = "no candidate report parsed to >=2 segments"
    return None


_GEO_AXIS_RE = re.compile(r"GeographicalAxis|GeographicAreas", re.I)
_GEO_NAME_RE = re.compile(
    r"^(americas|europe|emea|apjc|apac|asia|japan|china|greater china|united states|u\.s\.|us|canada|international|other international|rest of |north america|latin america|emerging markets|domestic|foreign)", re.I)

# Tables that disaggregate revenue by product or business line. ASC 606
# requires the split but NOT segment profit, so these are revenue-only --
# which is exactly why the reportable-segment table gets picked first.
_BUSINESS_LINE_RE = re.compile(
    r"disaggregat|by product|product category|item category|net revenue for group|revenue from contract", re.I)


def _is_geographic(segments: list[dict]) -> bool:
    """Whether a segment set describes places rather than businesses.

    A sum-of-the-parts values lines of business: you cannot apply a different
    multiple to "Americas" than to "Europe" for the same company, so a SOTP
    built on geography produces a number that is not a valuation. Apple,
    Cisco and Costco all report GEOGRAPHIC operating segments -- that is
    genuinely their ASC 280 disclosure -- so the geography has to be detected
    rather than assumed away.
    """
    if not segments:
        return False
    geo = 0
    for seg in segments:
        member = seg.get("member") or ""
        name = (seg.get("name") or "").strip()
        if _GEO_AXIS_RE.search(member) or _GEO_NAME_RE.match(name):
            geo += 1
    # Unanimity, not a majority. AMZN reports North America / International /
    # AWS -- two geographies and one business -- and the street values it
    # exactly that way, because AWS is a genuinely separable business with
    # its own multiple. A majority rule would reject that split. Only a set
    # where EVERY member is a place is unusable for a SOTP.
    return geo == len(segments)


def _drop_hierarchy_parents(segments: list[dict],
                           consolidated: Optional[float]) -> list[dict]:
    """Reduce overlapping revenue cuts to the finest set that adds up.

    A disaggregation table often carries several views of the SAME revenue.
    CSCO's prints three at once:

        Product 41.6 + Services 15.1                       = 56.7  (2 rows)
        Networking 28.3 + Security 8.1 + Collab 4.2
            + Observability 1.1                            = Product
        Subscription 31.5 = Subscription,Product 17.8
            + Subscription,Service 13.7                    (an orthogonal cut)

    Counting them together triples the revenue. This is not a tree, so removing
    "parents" cannot express it -- the Subscription rows are a cross-cut, not
    children. The general statement is simpler: pick the subset of rows that
    sums to consolidated revenue, preferring the one with the MOST members
    because that is the finest genuine split.

    The reconciliation requirement is also the safety net. An earlier
    value-matching version deleted AMD's Datacenter -- its largest segment --
    on a coincidental subset sum. Here AMD's rows reach only 0.42x group
    revenue, so no subset qualifies and the whole set is returned untouched.
    """
    if not consolidated or consolidated <= 0 or len(segments) < 3:
        return segments
    revs = [s.get("revenue") or 0.0 for s in segments]
    tol = 0.02 * consolidated
    if abs(sum(revs) - consolidated) <= tol:
        return segments                       # already a clean split
    if len(segments) > 16:                    # keep the search bounded
        return segments

    import itertools

    def _redundant(value: float, pool: list[float]) -> bool:
        """Is `value` just a sum of rows we are keeping?"""
        return any(abs(sum(c) - value) <= max(0.01 * value, 1.0)
                   for k in range(2, min(len(pool), 8) + 1)
                   for c in itertools.combinations(pool, k))

    # Reconciling on value alone over-fits: it will happily drop Intel Foundry
    # or Berkshire's BNSF to force a fit, and those are real businesses. Every
    # dropped row must ALSO be provably redundant -- equal to a sum of the rows
    # being kept -- which is what distinguishes an aggregate ("Net Sales",
    # "Product") from a segment that merely happens to make the arithmetic work.
    best: Optional[tuple] = None
    for mask in range(1, 1 << len(segments)):
        idx = [i for i in range(len(segments)) if mask & (1 << i)]
        if len(idx) < 2:
            continue
        if abs(sum(revs[i] for i in idx) - consolidated) > tol:
            continue
        kept_revs = [revs[i] for i in idx]
        if not all(_redundant(revs[j], kept_revs)
                   for j in range(len(segments)) if j not in idx):
            continue
        if best is None or len(idx) > len(best):
            best = tuple(idx)
    if best is None:
        return segments

    dropped = [segments[i].get("name") for i in range(len(segments))
               if i not in best]
    if dropped:
        print(f"  [sec_segments] overlapping revenue cuts: keeping the "
              f"{len(best)}-way split, dropping {dropped}")
    return [segments[i] for i in best]


def _filter_segments(segments: list[dict],
                     consolidated: Optional[float] = None) -> list[dict]:
    """Keep one reporting axis and drop subtotals.

    Two contaminations show up in real filings and both inflate the revenue
    sum, which then propagates into every downstream mix and margin:

    * MIXED AXES -- AVGO's table carries two business segments on
      `StatementBusinessSegmentsAxis` AND five geographies on
      `srt_StatementGeographicalAxis`. Both describe the same revenue, so the
      sum lands at exactly 2.000x consolidated. Business segments win: a SOTP
      values lines of business, not countries.
    * SUBTOTALS -- INTC reports "Total Intel Products" ($49.1B) alongside its
      components CCG ($32.2B) and DCAI ($16.9B). It sits on the same axis as a
      real segment and is only identifiable by being the sum of its siblings.
    """
    if len(segments) < 2:
        return segments

    def axis(seg: dict) -> str:
        return (seg.get("member") or "").split("=")[0]

    by_axis: dict[str, list[dict]] = {}
    for seg in segments:
        by_axis.setdefault(axis(seg), []).append(seg)
    if len(by_axis) > 1:
        for preferred in ("us-gaap_StatementBusinessSegmentsAxis",
                          "srt_ConsolidationItemsAxis"):
            if len(by_axis.get(preferred, [])) >= 2:
                segments = by_axis[preferred]
                break
        else:
            segments = max(by_axis.values(), key=len)

    # A subtotal must BOTH be named like one and add up like one. Value alone
    # is not enough: across five or more segments some subset almost always
    # sums to another within tolerance, and on AMD that false positive deleted
    # Datacenter -- the largest segment -- leaving a map covering 42% of
    # revenue. Requiring the name keeps INTC's "Total Intel Products" while
    # leaving genuine segments alone.
    import itertools
    kept: list[dict] = []
    for i, seg in enumerate(segments):
        rev = seg.get("revenue") or 0.0
        name = (seg.get("name") or "").lower()
        looks_like_subtotal = bool(re.search(r"\btotal\b|\bsubtotal\b", name))
        if not looks_like_subtotal or rev <= 0:
            kept.append(seg)
            continue
        others = [s.get("revenue") or 0.0 for j, s in enumerate(segments) if j != i]
        adds_up = any(
            abs(sum(c) - rev) <= 0.01 * rev
            for k in range(2, min(len(others), 6) + 1)
            for c in itertools.combinations(others, k))
        if adds_up:
            print(f"  [sec_segments] dropping subtotal segment "
                  f"{seg.get('name')!r} (= sum of siblings)")
            continue
        kept.append(seg)
    kept = kept if len(kept) >= 2 else segments
    return _drop_hierarchy_parents(kept, consolidated)


def _assemble(parsed: dict, ticker: str, filer: str, cik: str,
              filing: dict, rep: dict, base: str) -> Optional[dict]:
    """Turn a parsed table into the public schema, or None if it is not one."""
    monetary = [c for c in parsed["columns"] if c["monetary"]]
    if not monetary:
        return None
    latest = monetary[0]
    ccy = latest["currency"]
    key = f"{latest['period_end']}|{ccy or ''}"

    segments = []
    profit_qname = None
    for g in parsed["groups"]:
        if g["kind"] != "segment":
            continue
        rev = g["revenue"].get(key)
        if rev is None:
            continue
        profit = g["profit"].get(key)
        if profit is not None and profit_qname is None:
            profit_qname = g.get("profit_qname") or ""
        segments.append({
            "name": g["name"], "revenue": rev, "profit": profit,
            "margin": (profit / rev) if (profit is not None and rev) else None,
            "member": g.get("defref", ""),
            # Prior years in the SAME currency. Segments grow at very
            # different rates -- AWS +19.7% against North America +10.0%,
            # Intelligent Cloud +29.7% against More Personal Computing
            # -1.1% -- so a forward bridge that holds the mix constant is
            # biased, not merely imprecise. The filing already carries the
            # history needed to allocate growth per segment.
            "revenue_by_period": {k.split("|")[0]: v
                                  for k, v in g["revenue"].items()
                                  if k.endswith(f"|{ccy or ''}")},
            "profit_by_period": {k.split("|")[0]: v
                                 for k, v in g["profit"].items()
                                 if k.endswith(f"|{ccy or ''}")},
        })
    group_rev_early = (parsed["consolidated"].get("revenue") or {}).get(key)
    segments = _filter_segments(segments, group_rev_early)
    if len(segments) < 2:
        _LAST_REJECTION[ticker] = (
            f"{rep['html_file']}: {len(segments)} segment(s) -- "
            f"single-segment filer or wrong table")
        return None

    corporate = next((g for g in parsed["groups"] if g["kind"] == "corporate"), None)
    elimination = next((g for g in parsed["groups"] if g["kind"] == "elimination"), None)
    group_rev = (parsed["consolidated"].get("revenue") or {}).get(key)
    seg_sum = sum(s["revenue"] for s in segments)

    usd_rate = None
    if ccy and ccy != "USD":
        usd_col = next((c for c in monetary
                        if c["currency"] == "USD"
                        and c["period_end"] == latest["period_end"]), None)
        if usd_col:
            usd_rate = _implied_usd_rate(parsed, key,
                                         f"{usd_col['period_end']}|USD")

    # BABA reports Adjusted EBITA, which is pre-SBC and pre-amortisation and
    # therefore NOT EBIT. The engine applies ebit x (1-tax) x P/E to whatever
    # it is handed, so this flag has to travel with the numbers.
    profit_metric = parsed.get("profit_qname") or profit_qname or ""
    is_gaap_oi = bool(re.search(r"OperatingIncomeLoss", profit_metric or "", re.I))

    warnings: list[str] = []
    if group_rev and seg_sum:
        ratio = seg_sum / group_rev
        if not (0.5 <= ratio <= 1.6):
            warnings.append(
                f"segment revenue sum is {ratio:.2f}x consolidated revenue")
    if profit_metric and not is_gaap_oi:
        warnings.append(
            f"segment profit is {profit_metric} -- a non-GAAP measure, not EBIT")
    if any(s["profit"] is None for s in segments):
        warnings.append("one or more segments have no reported profit")

    return {
        "ticker": ticker, "filer_ticker": filer, "cik": str(cik),
        "form": filing["form"], "filed": filing["filed"],
        "accession": filing["accession"],
        "report_file": rep["html_file"], "report_short_name": rep["short_name"],
        "source_url": f"{base}/{rep['html_file']}",
        "period_end": latest["period_end"],
        "periods": [c["period_end"] for c in monetary
                    if c["currency"] == ccy],
        "reporting_currency": ccy or "USD",
        "units_multiplier": parsed["units_multiplier"],
        "usd_convenience_rate": usd_rate,
        "profit_metric": profit_metric or "unknown",
        "profit_is_gaap_operating_income": is_gaap_oi,
        "segments": segments,
        "consolidated_revenue": group_rev,
        "segment_sum_revenue": seg_sum,
        "sum_vs_consolidated": (seg_sum / group_rev) if group_rev else None,
        "n_segments": len(segments),
        "corporate_unallocated": ({
            "name": corporate["name"],
            "revenue": corporate["revenue"].get(key),
            "profit": corporate["profit"].get(key)} if corporate else None),
        "eliminations": ({
            "name": elimination["name"],
            "revenue": elimination["revenue"].get(key),
            "profit": elimination["profit"].get(key)} if elimination else None),
        "warnings": warnings,
    }

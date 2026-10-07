"""Primary-source check for drug-pipeline inputs (owner, 2026-10-07).

A pipeline pre-fill is researched from the open web and ClinicalTrials.gov. This module reads the company's own
documents -- the latest 10-K, the latest earnings 8-K (its press-release exhibit) and the latest earnings-call
transcript -- and checks the input against them:

* corroboration: each asset (name, aliases, INN, code) is found in the documents, with the stage words and
  regulatory dates printed near it (an asset the input calls Phase 3 that the 8-K calls approved is stale);
* completeness: late-stage programmes the documents name (an identifier near "Phase 3", "BLA", "PDUFA", ...)
  that the input neither values, lists as approved, nor excludes.

Deterministic (regex over the documents' text, no model call). SEC requests carry the project's generic
User-Agent (src.data.sec_edgar) and no personal data.
"""
from __future__ import annotations

import html as _html
import re
from typing import Optional

from src.data import sec_edgar as _sec

#: Stage words, most advanced first. A mention's stage is the first of these within the window around it.
_STAGES = (
    ("approved", re.compile(r"(?<!if )(?<!potential )(?<!upon )(?<!pending )(?<!seek )(?<!seeking )(?<!expected )(?<!anticipated )"
                             r"\b(?:approved (?:by|in|for)|received (?:FDA |EC |regulatory )?approval|launched|now marketed|commercial launch)\b", re.I)),
    ("filed", re.compile(r"\b(?:PDUFA|target action date|accepted for (?:priority )?review|under (?:priority )?(?:FDA )?review|"
                          r"(?<!plan to )(?<!expect to )(?<!intend to )submitted (?:a |an |the |our )?(?:s?BLA|s?NDA|MAA|application|marketing))\b", re.I)),
    ("phase_3", re.compile(r"\b(?:Phase\s*3|Phase\s*III|pivotal|registrational)\b", re.I)),
    ("phase_2", re.compile(r"\b(?:Phase\s*2|Phase\s*II)\b", re.I)),
)
_LATE = {"approved", "filed", "phase_3"}
#: Drug identifiers: development codes (REGN5713, NTLA-2001, ALN-AGT01, LY3437943, VX-880) and INNs by stem.
_CODE_RE = re.compile(r"\b(?:[A-Z]{2,6}-?\d{3,7}[A-Z]?|[A-Z]{2,5}-[A-Z]{1,4}\d{1,4}[A-Z]?\d?)\b")
_INN_RE = re.compile(
    r"\b[a-z]{3,}(?:mab|siran|tide|caftor|glipron|tinib|ciclib|lisib|parib|rafenib|ersen|rsen|cel|gene|vec|cept|stat|gliflozin|tamab)\b",
    re.I)
_WINDOW = 220
#: A stage word counts for a drug only within this many characters of it, inside the same sentence.
_STAGE_REACH = 150
#: Another company's drug named alongside (a combination partner or a comparator) is not a pipeline asset.
_COMPARATOR_RE = re.compile(r"(?:in combination with|combined with|plus|versus|vs\.?|compared (?:to|with)|competitor|competing|"
                            r"standard of care)\W+(?:\w+\W+){0,3}$", re.I)
_WORD_STOP = {"concept", "intercept", "accept", "except", "precept", "percept", "excel", "parcel", "cancel", "marcel"}
_PDUFA_RE = re.compile(r"(?:PDUFA|target action date)[^.]{0,90}?((?:January|February|March|April|May|June|July|August|"
                       r"September|October|November|December)\s+\d{1,2},\s+\d{4}|(?:first|second|third|fourth)\s+quarter\s+of\s+\d{4}|"
                       r"(?:Q[1-4]|[12]H)\s*\d{4})", re.I)
_STOP_CODES = {"COVID-19", "SARS-CoV-2", "ICH-E6", "ISO-9001", "FORM-10"}


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _recent_filings(ticker: str) -> Optional[dict]:
    cik = _sec.resolve_cik(ticker)
    if not cik:
        return None
    try:
        sub = _sec._http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception:                                      # noqa: BLE001
        return None
    rec = (sub.get("filings") or {}).get("recent") or {}
    return {"cik": int(sub.get("cik", cik)), "form": rec.get("form", []), "date": rec.get("filingDate", []),
            "acc": rec.get("accessionNumber", []), "doc": rec.get("primaryDocument", []), "items": rec.get("items", [])}


def latest_10k(ticker: str) -> Optional[dict]:
    """{url, date, text} of the latest 10-K's primary document (20-F for a foreign filer), else None."""
    f = _recent_filings(ticker)
    if not f:
        return None
    for i, form in enumerate(f["form"]):
        if form in ("10-K", "20-F"):
            url = f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{f['acc'][i].replace('-', '')}/{f['doc'][i]}"
            try:
                return {"form": form, "url": url, "date": f["date"][i], "text": html_to_text(_sec._http_get_text(url, timeout=60))}
            except Exception:                              # noqa: BLE001
                return None
    return None


def latest_earnings_8k(ticker: str) -> Optional[dict]:
    """{url, date, text} of the press-release exhibit (EX-99.1) of the latest 8-K under Item 2.02, else None."""
    f = _recent_filings(ticker)
    if not f:
        return None
    for i, form in enumerate(f["form"]):
        items = f["items"][i] if i < len(f["items"]) else ""
        if form != "8-K" or "2.02" not in str(items):
            continue
        base = f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{f['acc'][i].replace('-', '')}"
        try:
            idx = _sec._http_get_json(f"{base}/index.json")
            names = [it.get("name") for it in ((idx.get("directory") or {}).get("item") or [])]
            # Exhibit names vary (ex99-1.htm, exhibit991q22026.htm, q226lillysalesandearningsp.htm): any HTML
            # document that is not the cover 8-K, an XBRL viewer page (R1.htm) or an index.
            htm = [n for n in names if n and n.lower().endswith((".htm", ".html")) and n != f["doc"][i]
                   and not re.fullmatch(r"R\d+\.htm", n) and "index" not in n.lower()]
            ex = (next((n for n in htm if re.search(r"(?:ex|exhibit)[-_]?99(?:[-_.]?1)?", n, re.I)), None)
                  or next((n for n in htm if re.search(r"99|earning|release|press|result", n, re.I)), None)
                  or (htm[0] if htm else None))
            if not ex:
                continue
            url = f"{base}/{ex}"
            return {"form": "8-K", "url": url, "date": f["date"][i], "text": html_to_text(_sec._http_get_text(url, timeout=60))}
        except Exception:                                  # noqa: BLE001
            continue
    return None


def latest_transcript(ticker: str) -> Optional[dict]:
    try:
        from src.tools.fmp_transcripts import fetch_recent_transcripts
        rows = fetch_recent_transcripts(ticker, n=1) or []
    except Exception:                                      # noqa: BLE001
        rows = []
    if not rows:
        return None
    r = rows[0]
    txt = r.get("content") or r.get("text") or ""
    if not txt:
        return None
    return {"form": "transcript", "url": f"FMP earnings call {r.get('year')} Q{r.get('quarter')}",
            "date": str(r.get("date") or "")[:10], "text": re.sub(r"\s+", " ", txt)}


def source_texts(ticker: str, fetchers: Optional[dict] = None) -> dict[str, Optional[dict]]:
    f = fetchers or {"10-K": latest_10k, "8-K": latest_earnings_8k, "transcript": latest_transcript}
    out = {}
    for k, fn in f.items():
        try:
            out[k] = fn(ticker)
        except Exception:                                  # noqa: BLE001
            out[k] = None
    return out


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence holding text[start:end] (bounded by '. ' / '? ' / '; ' or the window)."""
    lo = max(0, start - _WINDOW)
    hi = min(len(text), end + _WINDOW)
    left = max(text.rfind(". ", lo, start), text.rfind("? ", lo, start), text.rfind("; ", lo, start))
    right_c = [i for i in (text.find(". ", end, hi), text.find("? ", end, hi), text.find("; ", end, hi)) if i >= 0]
    return text[(left + 2 if left >= 0 else lo): (min(right_c) + 1 if right_c else hi)]


def _stage_in(sent: str, at: int) -> Optional[str]:
    """The most advanced stage word within _STAGE_REACH characters of position `at` in a sentence."""
    lo, hi = max(0, at - _STAGE_REACH), at + _STAGE_REACH
    for name, rx in _STAGES:
        if any(lo <= m.start() <= hi for m in rx.finditer(sent)):
            return name
    return None


def _stage_near(text: str, start: int, end: int) -> Optional[str]:
    sent = _sentence(text, start, end)
    k = sent.find(text[start:end])
    return _stage_in(sent, k if k >= 0 else 0)


_RANK = [s_ for s_, _ in _STAGES]


def _stage_of_mentions(text: str, term: str) -> tuple[Optional[str], int, Optional[str], Optional[str]]:
    """(stage, mentions, date, snippet) over EVERY mention of `term`: the most advanced stage that appears in at
    least two of its sentences (else in one, when the term is mentioned only once or twice) -- one stray
    'approved' about another drug in the same paragraph does not promote it."""
    low = text.lower()
    counts: dict[str, int] = {}
    n, date, snip, i = 0, None, None, low.find(term)
    while i >= 0 and n < 200:
        n += 1
        sent = _sentence(text, i, i + len(term))
        snip = snip or sent[:300]
        k = sent.lower().find(term)
        st = _stage_in(sent, k if k >= 0 else 0)
        if st:
            counts[st] = counts.get(st, 0) + 1
        if date is None:
            pd = _PDUFA_RE.search(sent)
            date = pd.group(1) if pd else None
        i = low.find(term, i + len(term))
    if not n:
        return None, 0, None, None
    need = 2 if n > 2 else 1
    for name in _RANK:
        if counts.get(name, 0) >= need:
            return name, n, date, snip
    best = min(counts, key=_RANK.index) if counts else None
    return best, n, date, snip


def late_stage_mentions(text: str) -> dict[str, dict]:
    """{identifier: {stage, count, snippet}} for drug identifiers printed near a late-stage word."""
    out: dict[str, dict] = {}
    for rx in (_CODE_RE, _INN_RE):
        for m in rx.finditer(text or ""):
            tok = m.group(0)
            if tok.upper() in _STOP_CODES or tok.lower() in _WORD_STOP:
                continue
            if _COMPARATOR_RE.search(text[max(0, m.start() - 60): m.start()]):
                continue
            st = _stage_near(text, m.start(), m.end())
            if st not in _LATE:
                continue
            key = tok.lower()
            rec = out.setdefault(key, {"identifier": tok, "stage": st, "count": 0, "kind": "code" if rx is _CODE_RE else "inn",
                                       "snippet": text[max(0, m.start() - 120): m.end() + 160]})
            rec["count"] += 1
            if [s for s, _ in _STAGES].index(st) < [s for s, _ in _STAGES].index(rec["stage"]):
                rec["stage"] = st
    return out


def _asset_terms(a: dict) -> list[str]:
    terms = [a.get("name"), a.get("inn")] + list(a.get("aliases") or [])
    out = []
    for t in terms:
        if not t:
            continue
        for part in re.split(r"[(),/;]| and ", str(t)):
            part = part.strip()
            if len(part) >= 4:
                out.append(part.lower())
    return sorted(set(out), key=len, reverse=True)


def _covered_terms(entry: dict) -> set[str]:
    terms: set[str] = set()
    for a in (entry.get("assets") or []):
        terms.update(_asset_terms(a))
    for key in ("approved_portfolio", "excluded_assets"):
        for a in entry.get(key) or []:
            if isinstance(a, dict):
                terms.update(_asset_terms(a))
                for k in ("code", "codes", "reason", "note"):
                    v = a.get(k)
                    if isinstance(v, str):
                        terms.update(m.group(0).lower() for m in _CODE_RE.finditer(v))
                        if k in ("code", "codes"):
                            terms.add(v.lower())
                    elif isinstance(v, list):
                        terms.update(str(x).lower() for x in v)
            elif isinstance(a, str):
                terms.update(_asset_terms({"name": a}))
                terms.update(m.group(0).lower() for m in _CODE_RE.finditer(a))
    return terms


def check(entry: dict, sources: dict[str, Optional[dict]]) -> dict:
    """Corroboration per asset and uncovered late-stage identifiers per source."""
    texts = {k: v for k, v in sources.items() if v and v.get("text")}
    assets_out = []
    for a in entry.get("assets") or []:
        terms = _asset_terms(a)
        found = {}
        for src, doc in texts.items():
            best = None
            for t in terms:
                st, n, date, snip = _stage_of_mentions(doc["text"], t)
                if n and (best is None or n > best["mentions"]):
                    best = {"term": t, "stage": st, "mentions": n, "date": date, "snippet": snip}
            if best:
                found[src] = best
        rank = _RANK
        # The most recent document speaks last: the stage of the latest-dated source that names a stage.
        # Same-day tie (the 8-K and the call on results day): the filed release outranks the spoken call, which
        # also discusses partners' trials and plans (REGN: Hansoh's China Phase 3 for olatorepatide).
        _pri = {"transcript": 0, "10-K": 1, "8-K": 2}
        dated = sorted((v.get("date") or "", _pri.get(k, 0), k) for k, v in texts.items() if k in found and found[k].get("stage"))
        dated = [(d_, k) for d_, _, k in dated]
        latest_src = dated[-1][1] if dated else None
        top = found[latest_src]["stage"] if latest_src else None
        inp = str(a.get("phase") or "")
        verdict = ("not found in the primary sources" if not found else
                   "corroborated" if top == inp else
                   (f"CHECK: the latest source ({latest_src}) calls it {top}") if top and inp in rank and rank.index(top) < rank.index(inp) else
                   (f"input ahead of the filings ({latest_src} says {top}): confirm the newer event") if top else
                   "named, no stage words in its sentences")
        assets_out.append({"name": a.get("name"), "phase": inp, "verdict": verdict, "sources": found})
    covered = _covered_terms(entry)
    missing = {}
    for src, doc in texts.items():
        for key, rec in late_stage_mentions(doc["text"]).items():
            if any(key in c or c in key for c in covered if len(c) >= 4):
                continue
            if (rec["stage"] == "approved" or rec.get("kind") == "inn") and rec["count"] < 2:
                continue
            missing.setdefault(key, {"identifier": rec["identifier"], "stage": rec["stage"], "sources": [], "snippet": rec["snippet"]})
            missing[key]["sources"].append(src)
    return {"sources": {k: ({"url": v["url"], "date": v["date"], "chars": len(v["text"])} if v else None) for k, v in sources.items()},
            "assets": assets_out,
            "uncovered_late_stage": sorted(missing.values(), key=lambda r: (-len(r["sources"]), r["identifier"]))}

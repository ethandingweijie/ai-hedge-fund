"""Workstream R1 — edgar_earnings offline tests (fixtures + mocks only).

Exercises the FPI 6-K walk + domestic 8-K Item 2.02 click-through with
canned submissions JSON / cover pages / filing indexes / exhibits — no
network. The mocks stand in for `_submissions` (SEC submissions JSON) and
`_edgar_get_text` (cover / index / exhibit HTML).
"""
from __future__ import annotations

import pytest

from src.tools import edgar_earnings as ee


# ── Static helpers ────────────────────────────────────────────────────────────

def test_earnings_cover_regex():
    # Real BABA 6-K cover phrasing + variants
    assert ee._EARNINGS_COVER_RE.search(
        "Press Release — June Quarter 2026 Results")
    assert ee._EARNINGS_COVER_RE.search(
        "Alibaba Group Announces March Quarter 2026 Results")
    assert ee._EARNINGS_COVER_RE.search("Q2 FY2026 Earnings Release")
    assert ee._EARNINGS_COVER_RE.search("Quarterly Results Announcement")
    # AGM / corporate-update 6-K covers must NOT match
    assert not ee._EARNINGS_COVER_RE.search(
        "Notice of Annual General Meeting")
    assert not ee._EARNINGS_COVER_RE.search(
        "Update on Share Repurchase Program")


def test_recent_filings_filter_order_limit():
    subs = {"filings": {"recent": {
        "form":           ["6-K", "40-F", "6-K", "6-K"],
        "filingDate":     ["2026-08-20", "2026-07-01", "2026-08-01",
                           "2026-06-15"],
        "accessionNumber": ["a1", "a2", "a3", "a4"],
        "primaryDocument": ["p1.htm", "p2.htm", "p3.htm", "p4.htm"],
        "items":           ["", "", "", ""],
    }}}
    got = ee._recent_filings(subs, "6-K", 2)
    assert [f["accession"] for f in got] == ["a1", "a3"]
    assert got[0]["filed"] == "2026-08-20"
    assert got[0]["primary"] == "p1.htm"


def test_exhibit_from_index_prefers_ex991():
    index_html = """
    <table><tr><td><a href="form6k_cover.htm">Cover</a></td></tr>
    <tr><td><a href="tm2623667d1_ex99-1.htm">EX-99.1 press release</a></td></tr>
    <tr><td><a href="ex99-2.htm">EX-99.2 other</a></td></tr></table>
    """
    base = "https://www.sec.gov/Archives/edgar/data/1577552/000110465926099220/acc-index.htm"
    got = ee._exhibit_from_index(index_html, base)
    assert got is not None
    assert got.endswith("tm2623667d1_ex99-1.htm")
    assert got.startswith("https://www.sec.gov/Archives/edgar/data/1577552/")
    # No exhibit at all → None
    assert ee._exhibit_from_index(
        "<html><a href='cover.htm'>cover</a></html>", base) is None


def test_exhibit_from_index_root_relative_href():
    """Live BABA case (2026-08-24): the index href is SITE-ROOT-relative
    (/Archives/...) — must resolve against sec.gov, not stack onto the
    index directory (that produced a 404 with a doubled /Archives path)."""
    index_html = ('<table><tr><td><a href="/Archives/edgar/data/1577552/'
                  '000110465926099220/tm2623667d1_ex99-1.htm">EX-99.1</a>'
                  '</td></tr></table>')
    base = ("https://www.sec.gov/Archives/edgar/data/1577552/"
            "000110465926099220/acc-index.htm")
    got = ee._exhibit_from_index(index_html, base)
    assert got == ("https://www.sec.gov/Archives/edgar/data/1577552/"
                   "000110465926099220/tm2623667d1_ex99-1.htm")


def test_html_to_text_preserves_tables():
    html = """
    <html><body>
    <p>Revenue by segment:</p>
    <table>
      <tr><th>Segment</th><th>Revenue</th><th>YoY</th></tr>
      <tr><td>Cloud Intelligence</td><td>Rmb33.4bn</td><td>+26%</td></tr>
    </table>
    <script>var junk = 1;</script>
    </body></html>
    """
    text = ee._html_to_text(html)
    assert "Cloud Intelligence | Rmb33.4bn | +26%" in text
    assert "junk" not in text
    # Header row preserved too
    assert "Segment | Revenue | YoY" in text


# ── FPI 6-K walk (BABA-shaped) ───────────────────────────────────────────────

_SUBS_FPI = {"filings": {"recent": {
    # Newest first: an AGM 6-K filed AFTER the earnings one — the walk
    # must fetch its cover, reject it, and continue to the earnings 6-K.
    "form":           ["6-K", "6-K", "6-K"],
    "filingDate":     ["2026-08-25", "2026-08-20", "2026-07-20"],
    "accessionNumber": ["0001104659-26-088888", "0001104659-26-099220",
                        "0001104659-26-077777"],
    "primaryDocument": ["cover_agm.htm", "cover_earnings.htm",
                        "cover_update.htm"],
    "items":           ["", "", ""],
}}}

_COVER_EARNINGS = ("<html><body>EXHIBIT 99.1 — Press Release — June Quarter "
                   "2026 Results of Alibaba Group</body></html>")
_COVER_AGM = "<html><body>Notice of Annual General Meeting</body></html>"
_INDEX = ('<html><a href="cover_earnings.htm">Cover</a>'
          '<a href="tm2623667d1_ex99-1.htm">EX-99.1</a></html>')
_EXHIBIT = ("<html><body><p>June quarter revenue Rmb247.8bn.</p>"
            "<table><tr><td>Cloud</td><td>Rmb33.4bn</td></tr></table>"
            + "<p>guidance text " + "x" * 600 + "</p></body></html>")


def test_fetch_6k_walks_past_non_earnings_covers(monkeypatch):
    fetched: list[str] = []

    def fake_get_text(url):
        fetched.append(url)
        if url.endswith("cover_agm.htm") or url.endswith("cover_update.htm"):
            return _COVER_AGM
        if url.endswith("cover_earnings.htm"):
            return _COVER_EARNINGS
        if url.endswith("-index.htm"):
            return _INDEX
        if url.endswith("tm2623667d1_ex99-1.htm"):
            return _EXHIBIT
        return None

    monkeypatch.setattr(ee, "_submissions", lambda cik: _SUBS_FPI)
    monkeypatch.setattr(ee, "_edgar_get_text", fake_get_text)
    monkeypatch.setattr(ee.time, "sleep", lambda _s: None)

    got = ee._fetch_6k_press_release("1577552", "BABA")
    assert got is not None
    assert got["form"] == "6-K"
    assert got["filed"] == "2026-08-20"
    assert got["source"] == "edgar_6k_ex99"
    assert got["accession"] == "0001104659-26-099220"
    assert "Rmb247.8bn" in got["text"]
    assert "Cloud | Rmb33.4bn" in got["text"]
    # The AGM cover was fetched and rejected BEFORE clicking its index
    assert any(u.endswith("cover_agm.htm") for u in fetched)
    assert not any("000110465926088888" in u and u.endswith("-index.htm")
                   for u in fetched)


def test_fetch_6k_none_when_no_earnings_cover(monkeypatch):
    subs = {"filings": {"recent": {
        "form": ["6-K"], "filingDate": ["2026-08-05"],
        "accessionNumber": ["0001104659-26-088888"],
        "primaryDocument": ["cover_agm.htm"], "items": [""],
    }}}
    monkeypatch.setattr(ee, "_submissions", lambda cik: subs)
    monkeypatch.setattr(ee, "_edgar_get_text", lambda url: _COVER_AGM)
    assert ee._fetch_6k_press_release("1577552", "BABA") is None


# ── Domestic 8-K Item 2.02 (CRWD-shaped) ─────────────────────────────────────

_SUBS_DOM = {"filings": {"recent": {
    "form":           ["8-K", "8-K"],
    "filingDate":     ["2026-09-03", "2026-06-05"],
    "accessionNumber": ["0001535527-26-000111", "0001535527-26-000099"],
    "primaryDocument": ["crwd-8k.htm", "crwd-8k-old.htm"],
    "items":           ["5.02", "2.02,9.01"],
}}}

_EXHIBIT_DOM = ("<html><body><p>Fiscal Q2 2027 revenue $1.1B.</p>"
                + "<p>details " + "y" * 600 + "</p></body></html>")


def test_fetch_8k_requires_item_202(monkeypatch):
    def fake_get_text(url):
        if url.endswith("-index.htm"):
            return ('<html><a href="crwd-8k.htm">Cover</a>'
                    '<a href="ex991-q2.htm">EX-99.1</a></html>')
        if url.endswith("ex991-q2.htm"):
            return _EXHIBIT_DOM
        return None

    monkeypatch.setattr(ee, "_submissions", lambda cik: _SUBS_DOM)
    monkeypatch.setattr(ee, "_edgar_get_text", fake_get_text)
    monkeypatch.setattr(ee.time, "sleep", lambda _s: None)

    got = ee._fetch_8k_press_release("0001535527", "CRWD")
    assert got is not None
    # The 5.02-only 8-K was skipped; the 2.02 one matched
    assert got["accession"] == "0001535527-26-000099"
    assert got["source"] == "edgar_8k_ex99"
    assert "2.02" in got["title_hint"]
    assert "Fiscal Q2 2027 revenue" in got["text"]


# ── Router: get_earnings_press_release ───────────────────────────────────────

def test_router_no_cik_clean_none(monkeypatch):
    monkeypatch.setattr(ee, "_get_cik", lambda t: None)
    assert ee.get_earnings_press_release("3690.HK") is None


def test_router_prefers_6k_path_for_fpi(monkeypatch):
    monkeypatch.setattr(ee, "_get_cik", lambda t: "1577552")
    monkeypatch.setattr(ee, "_submissions", lambda cik: _SUBS_FPI)
    calls = {"6k": 0, "8k": 0}

    def fake_6k(cik, ticker):
        calls["6k"] += 1
        return {"ticker": ticker, "form": "6-K", "text": "ok",
                "source": "edgar_6k_ex99"}

    def fake_8k(cik, ticker):
        calls["8k"] += 1
        return None

    monkeypatch.setattr(ee, "_fetch_6k_press_release", fake_6k)
    monkeypatch.setattr(ee, "_fetch_8k_press_release", fake_8k)
    got = ee.get_earnings_press_release("BABA")
    assert got and got["form"] == "6-K"
    assert calls == {"6k": 1, "8k": 0}


def test_router_falls_back_to_8k_when_no_6k(monkeypatch):
    subs = {"filings": {"recent": {
        "form": ["8-K"], "filingDate": ["2026-09-03"],
        "accessionNumber": ["x"], "primaryDocument": ["y"], "items": ["2.02"],
    }}}
    monkeypatch.setattr(ee, "_get_cik", lambda t: "0001535527")
    monkeypatch.setattr(ee, "_submissions", lambda cik: subs)
    monkeypatch.setattr(ee, "_fetch_6k_press_release",
                        lambda cik, t: pytest.fail("6-K path must not run"))
    monkeypatch.setattr(ee, "_fetch_8k_press_release",
                        lambda cik, t: {"form": "8-K"})
    got = ee.get_earnings_press_release("CRWD")
    assert got == {"form": "8-K"}


def test_router_exception_is_soft_fail(monkeypatch):
    def boom(t):
        raise RuntimeError("network down")
    monkeypatch.setattr(ee, "_get_cik", boom)
    assert ee.get_earnings_press_release("BABA") is None


# ── Period hint ───────────────────────────────────────────────────────────────

def test_reported_period_hint(monkeypatch):
    monkeypatch.setattr(
        ee, "get_earnings_press_release",
        lambda t: {"title_hint": "June Quarter 2026 Results of Operations"})
    hint = ee.get_reported_period_hint("BABA")
    assert hint is not None
    assert "june quarter" in hint.lower()

    monkeypatch.setattr(ee, "get_earnings_press_release", lambda t: None)
    assert ee.get_reported_period_hint("BABA") is None

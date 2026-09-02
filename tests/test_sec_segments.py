"""Guards for the SEC segment-footnote parser (ASC 280 / IFRS 8).

Every test runs off captured fixtures with no network. The values pinned here
were read off the live filings, so a drift in the parser shows up as a concrete
wrong number rather than a vague shape failure.

Three of these encode bugs that a naive parser hits and that cost real
debugging time:

* `test_baba_blank_usd_column_does_not_shift_values` -- BABA's USD convenience
  column is populated ONLY on consolidated rows and renders as `td.text` on
  every segment row. Filtering blanks out shifts each segment onto the prior
  year: China E-commerce reads 508,380 (FY2025) instead of 554,217 (FY2026).
* `test_jd_logistics_dedupes_to_the_total_row` -- JD emits `JD Logistics`
  twice under one axis member, bare (external revenue 80,314m) and qualified
  (total 217,146m). First-match wins understates it 2.7x.
* `test_jd_count_column_is_not_treated_as_money` -- JD's table leads with a
  `Mar. 31, 2024 | segment` COUNT column holding `Number of Reportable
  Segments = 3`. It parses as a date, so admitting it shifts every value one
  column left.
"""
from __future__ import annotations

import os

import pytest

from src.tools import sec_segments as ss

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sec_segments")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _parsed(name: str) -> dict:
    out = ss._parse_segment_table(_fixture(name))
    assert out is not None, f"{name} failed to parse"
    return out


def _latest_key(parsed: dict) -> str:
    col = next(c for c in parsed["columns"] if c["monetary"])
    return f"{col['period_end']}|{col['currency'] or ''}"


def _segments(parsed: dict) -> dict[str, dict]:
    key = _latest_key(parsed)
    return {g["name"]: {"revenue": g["revenue"].get(key),
                        "profit": g["profit"].get(key)}
            for g in parsed["groups"] if g["kind"] == "segment"}


# ── unit helpers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("$ in Millions", 1e6),
    ("USD ($) $ in Millions", 1e6),
    ("CNY in Millions, $ in Millions", 1e6),
    ("$ in Thousands", 1e3),
    ("in Billions", 1e9),
    ("", 1.0),
])
def test_parse_units(header, expected):
    assert ss._parse_units(header) == expected


@pytest.mark.parametrize("text,expected", [
    ("Sep. 27, 2025", "2025-09-27"),
    ("Mar. 31, 2026", "2026-03-31"),
    ("Dec. 31, 2025", "2025-12-31"),
    ("2025-09-27", "2025-09-27"),
    ("segment", None),
])
def test_parse_period(text, expected):
    assert ss._parse_period(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("CNY (¥)", "CNY"), ("USD ($)", "USD"), ("segment", None), ("", None),
])
def test_parse_ccy(text, expected):
    assert ss._parse_ccy(text) == expected


# ── AAPL: US 10-K, corporate unallocated, exact reconciliation ──────────────

def test_aapl_segments_and_margins():
    segs = _segments(_parsed("AAPL_R68.htm"))
    assert set(segs) == {"Americas", "Europe", "Greater China", "Japan",
                         "Rest of Asia Pacific"}
    assert segs["Americas"]["revenue"] == pytest.approx(178_353e6)
    assert segs["Americas"]["profit"] == pytest.approx(72_480e6)
    assert segs["Greater China"]["revenue"] == pytest.approx(64_377e6)


def test_aapl_corporate_unallocated_is_captured_not_a_segment():
    """Corporate overhead is the input the deferred bridge work needs."""
    parsed = _parsed("AAPL_R68.htm")
    key = _latest_key(parsed)
    corp = next(g for g in parsed["groups"] if g["kind"] == "corporate")
    assert corp["profit"][key] == pytest.approx(-42_627e6)
    assert "Corporate non-segment" not in _segments(parsed)


def test_aapl_reconciles_to_the_consolidated_totals():
    """The filing's own identity: segments + corporate == group."""
    parsed = _parsed("AAPL_R68.htm")
    key = _latest_key(parsed)
    segs = _segments(parsed)
    corp = next(g for g in parsed["groups"] if g["kind"] == "corporate")
    assert (sum(s["revenue"] for s in segs.values())
            == pytest.approx(parsed["consolidated"]["revenue"][key]))
    assert (sum(s["profit"] for s in segs.values()) + corp["profit"][key]
            == pytest.approx(parsed["consolidated"]["profit"][key]))


# ── BABA: 20-F, CNY, blank USD column, non-GAAP profit ─────────────────────

def test_baba_blank_usd_column_does_not_shift_values():
    """The regression that cost the most: blanks are cells and must be kept."""
    segs = _segments(_parsed("BABA_R123.htm"))
    assert len(segs) == 4
    china = segs["Alibaba China E-commerce Group"]
    assert china["revenue"] == pytest.approx(554_217e6)   # NOT 508,380 (FY2025)
    assert china["profit"] == pytest.approx(107_509e6)


def test_baba_margins_and_negative_profit():
    segs = _segments(_parsed("BABA_R123.htm"))
    assert segs["Cloud intelligence group"]["profit"] / \
        segs["Cloud intelligence group"]["revenue"] == pytest.approx(0.090, abs=1e-3)
    # Parenthesised negatives must survive as negatives.
    assert segs["All others"]["profit"] == pytest.approx(-35_737e6)
    assert segs["Alibaba International Digital Commerce Group"]["profit"] < 0


def test_baba_role_marker_is_not_emitted_as_a_segment():
    """'Total segments' with no residual name is the all-segments aggregate."""
    assert "Total segments" not in _segments(_parsed("BABA_R123.htm"))


def test_baba_profit_metric_is_flagged_non_gaap():
    """AdjustedEBITA is pre-SBC and pre-amortisation -- it is not EBIT.

    The engine multiplies whatever it gets by (1-tax) x P/E, so treating this
    as operating income silently overstates segment NAV.
    """
    parsed = _parsed("BABA_R123.htm")
    assert "AdjustedEBITA" in parsed["profit_qname"]
    assert "OperatingIncomeLoss" not in parsed["profit_qname"]


def test_baba_implied_usd_rate_from_the_filing_itself():
    """The issuer's own convenience translation beats a live FX quote."""
    parsed = _parsed("BABA_R123.htm")
    key = _latest_key(parsed)
    rate = ss._implied_usd_rate(parsed, key, "2026-03-31|USD")
    assert rate == pytest.approx(148_401 / 1_023_670, rel=1e-4)


def test_baba_sum_exceeds_consolidated_by_eliminations():
    parsed = _parsed("BABA_R123.htm")
    key = _latest_key(parsed)
    ratio = (sum(s["revenue"] for s in _segments(parsed).values())
             / parsed["consolidated"]["revenue"][key])
    assert ratio == pytest.approx(1.085, abs=0.005)


# ── JD: count column, duplicate member, reconciling groups ─────────────────

def test_jd_count_column_is_not_treated_as_money():
    parsed = _parsed("JD_R120.htm")
    first = parsed["columns"][0]
    assert first["unit"] == "segment"
    assert first["monetary"] is False
    assert _latest_key(parsed) == "2025-12-31|CNY"


def test_jd_logistics_dedupes_to_the_total_row():
    segs = _segments(_parsed("JD_R120.htm"))
    assert len(segs) == 3
    assert segs["JD Logistics"]["revenue"] == pytest.approx(217_146e6)
    assert segs["JD Logistics"]["revenue"] != pytest.approx(80_314e6)


def test_jd_reconciling_groups_are_routed_not_counted():
    parsed = _parsed("JD_R120.htm")
    key = _latest_key(parsed)
    kinds = {g["kind"] for g in parsed["groups"]}
    assert {"segment", "corporate", "elimination"} <= kinds
    corp = next(g for g in parsed["groups"] if g["kind"] == "corporate")
    elim = next(g for g in parsed["groups"] if g["kind"] == "elimination")
    assert corp["profit"][key] == pytest.approx(-7_256e6)
    assert elim["revenue"][key] == pytest.approx(-83_742e6)
    assert "Inter-segment" not in _segments(parsed)


def test_jd_profit_metric_is_gaap_operating_income():
    assert "OperatingIncomeLoss" in _parsed("JD_R120.htm")["profit_qname"]


# ── report selection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("short_name", [
    "Segment Information and Geographic Data (Tables)",
    "Goodwill by Segment (Details)",
    "Segment Assets (Details)",
    "Long-Lived Assets by Segment",
    "Segment Information - Net Sales for Countries (Details)",
])
def test_selection_excludes_segment_flavoured_non_operating_tables(short_name):
    assert ss._score_segment_report(short_name) is None


def test_selection_ignores_reports_without_segment_in_the_name():
    assert ss._score_segment_report("Consolidated Balance Sheets") is None


def test_selection_ranks_the_operating_table_first():
    """A bare /segment/i match is not selective: AMZN's 10-K offers 10."""
    xml = _fixture("AMZN_FilingSummary.xml")
    reports = ss._parse_filing_summary_xml(xml)
    scored = [(ss._score_segment_report(r["short_name"], r["long_name"]), r)
              for r in reports]
    scored = [(s, r) for s, r in scored if s is not None]
    assert len(scored) >= 3, "expected several segment-flavoured candidates"
    scored.sort(key=lambda x: -x[0])
    assert "R87" in scored[0][1]["html_file"]


def test_selection_ranks_aapl_operating_table_first():
    reports = ss._parse_filing_summary_xml(_fixture("AAPL_FilingSummary.xml"))
    scored = [(ss._score_segment_report(r["short_name"], r["long_name"]), r)
              for r in reports]
    scored = [(s, r) for s, r in scored if s is not None]
    scored.sort(key=lambda x: -x[0])
    assert "R68" in scored[0][1]["html_file"]


# ── cross-listing alias ─────────────────────────────────────────────────────

@pytest.mark.parametrize("form", ["9988", "9988.HK", "09988.HK", "9988.hk"])
def test_alias_resolves_every_hk_ticker_spelling(form):
    """The 4-vs-5-digit collision has bitten this repo repeatedly.

    The snapshot's '3690.HK' entry can never attach because the pipeline
    canonicalises to '03690.HK'. Keying on the canonical form is what stops
    that class of bug recurring here.
    """
    assert ss._resolve_filer(form) == "BABA"


@pytest.mark.parametrize("ticker", ["3690.HK", "03690.HK", "0700.HK", "00700.HK"])
def test_alias_returns_none_for_hk_names_with_no_sec_filer(ticker):
    assert ss._resolve_filer(ticker) is None


@pytest.mark.parametrize("ticker,expected", [
    ("AAPL", "AAPL"), ("baba", "BABA"), ("D05.SI", None), ("U96.SI", None),
    ("", None),
])
def test_alias_passthrough_and_rejection(ticker, expected):
    assert ss._resolve_filer(ticker) == expected


def test_known_no_filer_short_circuits_before_any_network_call(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "_get_text", lambda *a, **k: called.append(a) or None)
    assert ss.get_segment_footnote("03690.HK", "2026-08-16") is None
    assert called == [], "a known-no-filer ticker must not hit the network"


# ── soft-fail contract ──────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    "", "<html><body>no table here</body></html>",
    "<table class='report'><tr><td>only one row</td></tr></table>",
    "not html at all",
])
def test_parser_returns_none_rather_than_a_partial_map(html):
    assert ss._parse_segment_table(html) is None


def test_fetch_failure_yields_none_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(ss, "_get_text", lambda *a, **k: None)
    ss._SEG_CACHE.clear()
    assert ss.get_segment_footnote("AAPL", "2026-08-16") is None


# ── business lines vs geography ─────────────────────────────────────────────

_AX = "us-gaap_StatementBusinessSegmentsAxis=x"


def _segs(*names):
    return [{"name": n, "member": _AX} for n in names]


@pytest.mark.parametrize("names", [
    ("Americas", "Europe", "Greater China", "Japan", "Rest of Asia Pacific"),
    ("Americas", "EMEA", "APJC"),
    ("United States", "Canada", "Other International"),
])
def test_all_geographic_segment_sets_are_detected(names):
    """A SOTP values businesses; you cannot multiple 'Europe' differently."""
    assert ss._is_geographic(_segs(*names)) is True


@pytest.mark.parametrize("names", [
    ("Family of Apps", "Reality Labs"),
    ("Compute & Networking", "Graphics"),
    ("Semiconductor Solutions", "Infrastructure Software"),
    ("iPhone", "Mac", "iPad", "Services"),
])
def test_business_line_sets_are_not_flagged(names):
    assert ss._is_geographic(_segs(*names)) is False


def test_a_business_line_among_geographies_is_not_geographic():
    """AMZN reports North America / International / AWS.

    Two are places, but AWS is a genuinely separable business with its own
    multiple, and the street values AMZN exactly this way. A majority rule
    would reject a legitimate split, so detection requires unanimity.
    """
    assert ss._is_geographic(_segs("North America", "International", "AWS")) is False


def test_geographic_detection_needs_segments():
    assert ss._is_geographic([]) is False


def test_geographic_axis_qname_is_honoured_over_the_name():
    geo_axis = [{"name": "Segment One",
                 "member": "srt_StatementGeographicalAxis=country_US"},
                {"name": "Segment Two",
                 "member": "srt_StatementGeographicalAxis=country_DE"}]
    assert ss._is_geographic(geo_axis) is True


@pytest.mark.parametrize("short_name,matches", [
    ("Revenue - Disaggregation of Revenue (Details)", True),
    ("Segment Reporting Information by Item Category (Details)", True),
    ("Revenue - Disaggregated Net Sales (Details)", True),
    ("Segment Information - Reportable Segments (Details)", False),
    ("Goodwill by Segment (Details)", False),
])
def test_business_line_table_is_recognised(short_name, matches):
    assert bool(ss._BUSINESS_LINE_RE.search(short_name)) is matches


def test_class_share_ticker_uses_the_sec_hyphen_form():
    """SEC lists Berkshire as BRK-B; the dotted form resolves to no CIK.

    The failure mode is silent -- a missing CIK is indistinguishable from a
    filer that simply does not report segments -- so BRK.B dropped out of the
    filing route entirely rather than erroring.
    """
    from src.tools.sec_segments import _resolve_filer
    assert _resolve_filer("BRK.B") == "BRK-B"
    assert _resolve_filer("BF.B") == "BF-B"
    # unclassed US tickers are untouched
    assert _resolve_filer("AAPL") == "AAPL"
    # and HK/SG still short-circuit before this
    assert _resolve_filer("00700.HK") is None


def test_xbrl_member_suffix_is_stripped_from_segment_names():
    """'BNSF [Member]' is XBRL plumbing, not the name of the segment."""
    from src.tools.sec_segments import _strip_member_suffix
    assert _strip_member_suffix("BNSF [Member]") == "BNSF"
    assert _strip_member_suffix("Pilot [Domain]") == "Pilot"
    assert _strip_member_suffix("Electricity and Natural Gas [Member]") == \
        "Electricity and Natural Gas"
    # a name that is only the tail must not be emptied
    assert _strip_member_suffix("[Member]") == "[Member]"
    # ordinary names untouched, including legitimate internal brackets
    assert _strip_member_suffix("AWS") == "AWS"
    assert _strip_member_suffix("Data Center [1]") == "Data Center [1]"

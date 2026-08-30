"""A sector note's comparative table is next to its coverage table.

Run live against Goldman's 2026 Global eCommerce Handbook, the extractor
returned ten rows of "12-Month Price Target" as peer multiples — Amazon's
$186 target would have been banked as a multiple of 186x, and a price target
is not a multiple of anything.

Filtered at the boundary rather than by sharpening the prompt. That is the
lesson from the scenario-narrative work: drift is fixed with normalisation
layers, because a stricter prompt drifts back and a filter does not.

The extraction itself is a single call per DOCUMENT, not per route — an
industry note makes one set of claims about one industry, so a second call
would re-read the same pages for the same answer. The routes only decide
where the one result is filed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.memory import assumption_extract as ae


# ── Only rows that are a multiple of something survive ───────────────────

@pytest.mark.parametrize("metric, value, expected", [
    ("P/E",            "18.4x",  "pe"),
    ("EV/EBITDA",      "11.2",   "ev_ebitda"),
    ("EV/Sales",       "3.1x",   "ev_sales"),
    ("EV/GMV",         "0.9x",   "ev_gmv"),
    ("P/B",            "1.3x",   "pb"),
    ("2026E P/E (X)",  "22.0",   "pe"),
])
def test_a_real_multiple_is_kept_and_canonicalised(metric, value, expected):
    got = ae._clean_peer_multiples([{"company": "A", "metric": metric,
                                     "value": value}])
    assert len(got) == 1
    assert got[0]["metric"] == expected
    assert got[0]["value_num"] > 0


@pytest.mark.parametrize("metric, value", [
    ("12-Month Price Target", "$186"),      # the live failure
    ("Price Target",          "US$34"),
    ("Rating",                "Buy"),
    ("Dividend Yield",        "3.2%"),      # a yield, not a multiple
    ("FCF Yield",             "5.1%"),
    ("Market Cap",            "$1,900bn"),
    ("EV/EBITDA",             "N/A"),
    ("EV/EBITDA",             ""),
])
def test_anything_that_is_not_a_multiple_is_dropped(metric, value):
    assert ae._clean_peer_multiples(
        [{"company": "A", "metric": metric, "value": value}]) == []


def test_a_price_wearing_a_multiple_s_label_is_still_dropped():
    """The failure mode that survives a correct metric name: the model
    labels the column P/E and copies the price target underneath it."""
    assert ae._clean_peer_multiples(
        [{"company": "A", "metric": "P/E", "value": "US$186"}]) == []


def test_an_implausible_multiple_is_dropped():
    """A comparative table above this is a data-entry artefact — a market cap
    or a share count that landed in the multiple column."""
    assert ae._clean_peer_multiples(
        [{"company": "A", "metric": "P/E", "value": "1900"}]) == []
    assert ae._clean_peer_multiples(
        [{"company": "A", "metric": "P/E", "value": "-4.2"}]) == []


def test_the_live_handbook_rows_are_all_rejected():
    """Verbatim from the live run: the handbook's valuation content is chart
    exhibits ("EV/FCF Historical Trading Range"), so the model reached for
    the coverage table instead. Nothing is the right answer here."""
    live = [
        {"company": "Amazon", "ticker": "AMZN", "metric": "EV/GMV",
         "value": "N/A", "fiscal_year_label": "2026E"},
        {"company": "Etsy", "ticker": "ETSY", "metric": "12-Month Price Target",
         "value": "$89", "fiscal_year_label": "N/A"},
        {"company": "Alibaba", "ticker": "BABA",
         "metric": "12-Month Price Target", "value": "$186",
         "fiscal_year_label": "N/A"},
    ]
    assert ae._clean_peer_multiples(live) == []


def test_the_filter_never_raises_on_junk():
    assert ae._clean_peer_multiples(None) == []
    assert ae._clean_peer_multiples([{}]) == []
    assert ae._clean_peer_multiples([{"metric": None, "value": None}]) == []


# ── One call per document, not per route ─────────────────────────────────

def _fake_extraction():
    return {
        "house": "Goldman Sachs", "as_of": "2026-08",
        "industry_label": "Global eCommerce",
        "anchor_kpi": "GMV growth and market share consolidation",
        "disclosed_metrics": ["GMV", "take rate"],
        "economics": ["ancillary revenue mix"], "competitive": ["scale"],
        "quantitative": [{"name": "TAM", "value": "$4.5tn", "basis": "2025"},
                         {"name": "penetration", "value": "24%", "basis": ""}],
        "peer_multiples": [], "trends": [], "positioning": [],
    }


def test_one_document_is_read_once_however_many_routes_it_serves(tmp_path):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4 sector note")
    routes = [{"market": "US", "sector": "Tech", "profile": "Mature SaaS"},
              {"market": "US", "sector": "Tech", "profile": "Growth SaaS"}]

    with patch.object(ae, "extract_industry_note",
                      return_value=_fake_extraction()) as call, \
         patch("src.utils.research_pdf.extract_research_pdf",
               return_value={"text": "x" * 500}), \
         patch("src.memory.industry_knowledge.industry_note_exists",
               return_value=False), \
         patch("src.memory.industry_knowledge.upsert_industry_knowledge") as up:
        out = ae.extract_and_persist_industry_pdf(
            str(pdf), routes, ai_input_allowed=True)

    assert call.call_count == 1, "one industry, one set of claims, one call"
    assert up.call_count == 2, "filed against both routes"
    assert sorted(out["routes"].values()) == ["extracted", "extracted"]


def test_the_name_value_pairs_are_folded_into_the_2f_mapping(tmp_path):
    """The composer reads a mapping; the schema takes a list, because
    json_mode is more reliable emitting objects in an array than free keys."""
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4 sector note")
    with patch.object(ae, "extract_industry_note", return_value=_fake_extraction()), \
         patch("src.utils.research_pdf.extract_research_pdf",
               return_value={"text": "x" * 500}), \
         patch("src.memory.industry_knowledge.industry_note_exists",
               return_value=False), \
         patch("src.memory.industry_knowledge.upsert_industry_knowledge") as up:
        ae.extract_and_persist_industry_pdf(
            str(pdf), [{"market": "", "sector": "Tech", "profile": "Mature SaaS"}],
            ai_input_allowed=True)

    quant = up.call_args.kwargs["quantitative"]
    assert quant == {"TAM": "$4.5tn (2025)", "penetration": "24%"}


def test_an_already_stored_document_is_not_read_again(tmp_path):
    """Deduped BEFORE the call, because the call is the expensive half."""
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4 sector note")
    with patch.object(ae, "extract_industry_note") as call, \
         patch("src.memory.industry_knowledge.industry_note_exists",
               return_value=True):
        out = ae.extract_and_persist_industry_pdf(
            str(pdf), [{"market": "", "sector": "Tech", "profile": "Mature SaaS"}],
            ai_input_allowed=True)
    assert call.call_count == 0
    assert set(out["routes"].values()) == {"exists"}


def test_the_compliance_gate_is_checked_here_too(tmp_path):
    """Callers gate as well; this is the second check, as the equity
    extractor does it."""
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4 sector note")
    with patch.object(ae, "extract_industry_note") as call:
        out = ae.extract_and_persist_industry_pdf(
            str(pdf), [{"market": "", "sector": "Tech", "profile": "Mature SaaS"}],
            ai_input_allowed=False)
    assert call.call_count == 0
    assert set(out["routes"].values()) == {"gated"}


def test_an_unreadable_pdf_is_reported_not_raised(tmp_path):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"not a pdf")
    with patch("src.utils.research_pdf.extract_research_pdf",
               side_effect=RuntimeError("unreadable")), \
         patch("src.memory.industry_knowledge.industry_note_exists",
               return_value=False):
        out = ae.extract_and_persist_industry_pdf(
            str(pdf), [{"market": "", "sector": "Tech", "profile": "Mature SaaS"}],
            ai_input_allowed=True)
    assert set(out["routes"].values()) == {"failed"}

"""Retrieval-grounded writing style: tagging, retrieval and the leak guard.

The PM's house-style rules were hand-written — someone read the deposited
notes and encoded what they saw, so a new note in the archive changed
nothing. These pin the loop that replaces them.

The test that matters most is the numeric leak guard. "Learn the writing
style, do not copy the numbers" is only an instruction until something
enforces it, and a figure present in the rewrite but absent from the draft
is exactly the signature of a number carried across from a writing sample.

No network and no LLM: rows are supplied directly and the stylist's model
call is stubbed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.pm_stylist import (
    StylistOutput, introduced_figures, numeric_tokens, restyle_rationale,
)
from src.memory.style_exemplars import (
    classify_note_type, format_exemplar_block, get_style_exemplars,
    normalise_house,
)


# ── House normalisation ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Phillip Securities Research",     "Phillip Securities Research"),
    ("PHILLIP SECURITIES RESEARCH",     "Phillip Securities Research"),
    ("PHILLIP SECURITIES RESEARCH (S",  "Phillip Securities Research"),
    ("Phillip Capital",                 "Phillip Securities Research"),
    ("Goldman Sachs",                   "Goldman Sachs"),
    ("DBS Group Research",              "DBS Group Research"),
    ("OCBC Group Research",             "OCBC Group Research"),
])
def test_house_variants_collapse(raw, expected):
    """The store held four spellings of one firm, which fragments any
    grouping by house voice — including the truncated "(S" from a cut-off
    "(SINGAPORE)"."""
    assert normalise_house(raw) == expected


def test_unknown_house_is_left_readable():
    assert normalise_house("Some Boutique Research") == "Some Boutique Research"
    assert normalise_house(None) == ""


# ── Note type ────────────────────────────────────────────────────────────

def test_rating_change_is_a_downgrade():
    """AAPL's real row: field "Rating", NEUTRAL -> REDUCE, "Downgrade"."""
    revisions = [{"field": "Rating", "new_value": "REDUCE",
                  "prior_value": "NEUTRAL", "direction": "Downgrade"}]
    assert classify_note_type(revisions) == "downgrade"


def test_rating_change_is_an_upgrade():
    revisions = [{"field": "Rating", "new_value": "BUY",
                  "prior_value": "NEUTRAL", "direction": "Upgrade"}]
    assert classify_note_type(revisions) == "upgrade"


def test_direction_is_inferred_when_unreadable():
    """A rating line with no usable direction is still a change; rank the
    ratings rather than dropping it to estimate-revision."""
    revisions = [{"field": "Rating", "new_value": "BUY",
                  "prior_value": "REDUCE", "direction": ""}]
    assert classify_note_type(revisions) == "upgrade"


def test_estimate_only_revisions():
    """The common case — a quarterly review that moves numbers, not stance."""
    revisions = [{"field": "2026 Revenue", "new_value": "US$825.1bn",
                  "prior_value": "US$827.6bn", "direction": "cut"}]
    assert classify_note_type(revisions) == "estimate-revision"


def test_no_revisions_is_maintenance():
    assert classify_note_type(None) == "maintenance"
    assert classify_note_type("") == "maintenance"
    assert classify_note_type("[]") == "maintenance"


def test_malformed_revisions_do_not_raise():
    assert classify_note_type("{not json") == "maintenance"
    assert classify_note_type([None, "x", 3]) == "estimate-revision"


def test_json_string_is_accepted():
    """Rows arrive from the DB as a JSON string, not a list."""
    assert classify_note_type(
        '[{"field": "Rating", "direction": "Downgrade", '
        '"new_value": "SELL", "prior_value": "HOLD"}]'
    ) == "downgrade"


# ── Retrieval ────────────────────────────────────────────────────────────

_ROWS = [
    {"ticker": "JPM",    "house": "Phillip Securities Research",
     "report_date": "21 October 2025", "rating": "Neutral",
     "revisions_json": '[{"field": "2026 EPS", "direction": "cut"}]',
     "thesis_json": '{"points": ["NII inched up on 7% loan growth as NIM fell 13bps."],'
                    ' "catalysts": ["Buybacks rose 90% YoY."], "risks": ["Macro uncertainty."]}'},
    {"ticker": "O39.SI", "house": "PHILLIP SECURITIES RESEARCH (S",
     "report_date": "11 May 2026", "rating": "NEUTRAL",
     "revisions_json": '[{"field": "FY26 PATMI", "direction": "cut"}]',
     "thesis_json": '{"points": ["Total income grew 5% on wealth fees."], "catalysts": [], "risks": []}'},
    {"ticker": "AAPL",   "house": "PHILLIP SECURITIES RESEARCH",
     "report_date": "3 Aug 2026", "rating": "REDUCE",
     "revisions_json": '[{"field": "Rating", "direction": "Downgrade",'
                       ' "new_value": "REDUCE", "prior_value": "NEUTRAL"}]',
     "thesis_json": '{"points": ["Supply constraints weigh on the near term."], "catalysts": [], "risks": []}'},
]


@pytest.fixture
def stub_rows():
    with patch("src.memory.style_exemplars._load_rows") as m:
        import json as _json
        m.return_value = [
            {**r, "thesis": _json.loads(r["thesis_json"])} for r in _ROWS
        ]
        yield m


def test_subject_ticker_is_never_its_own_exemplar(stub_rows):
    """The subject's own note is the one document whose numbers and
    conclusion could be laundered into our view without looking foreign.
    get_analyst_thesis supplies that separately, as a view to argue with."""
    out = get_style_exemplars("JPM", "estimate-revision", limit=3)
    assert "JPM" not in {e["ticker"] for e in out}


def test_sector_and_stance_rank_first(stub_rows):
    """D05.SI is a bank: the two Financials estimate-revisions should
    outrank the Tech downgrade."""
    out = get_style_exemplars("D05.SI", "estimate-revision", limit=2)
    assert {e["ticker"] for e in out} == {"JPM", "O39.SI"}


def test_stance_still_matters_outside_the_sector(stub_rows):
    out = get_style_exemplars("MU", "downgrade", limit=1)
    assert out and out[0]["ticker"] == "AAPL"


def test_one_exemplar_per_company(stub_rows):
    out = get_style_exemplars("D05.SI", None, limit=3)
    tickers = [e["ticker"] for e in out]
    assert len(tickers) == len(set(tickers))


def test_retrieval_is_deterministic(stub_rows):
    a = get_style_exemplars("D05.SI", "estimate-revision", limit=2)
    b = get_style_exemplars("D05.SI", "estimate-revision", limit=2)
    assert [e["ticker"] for e in a] == [e["ticker"] for e in b]


def test_empty_corpus_returns_nothing():
    with patch("src.memory.style_exemplars._load_rows", return_value=[]):
        assert get_style_exemplars("D05.SI", "estimate-revision") == []


def test_exemplar_block_states_the_samples_are_not_evidence(stub_rows):
    block = format_exemplar_block(get_style_exemplars("D05.SI", None, limit=2))
    assert "WRITING SAMPLES" in block
    assert "must never appear in your output" in block


def test_exemplar_block_handles_no_exemplars():
    assert "no style exemplars" in format_exemplar_block([])


# ── The leak guard ───────────────────────────────────────────────────────

DRAFT = ("1. Revenue rose 12% to US$14.4bn, ahead of our US$14.0bn estimate, "
         "supporting the PT of US$186.\n"
         "2. Margins held at 31%, with net cash of US$15.5bn.")


def test_numeric_tokens_normalise_separators():
    assert numeric_tokens("14,400 and 14400") == {"14400"}


def test_faithful_rewrite_passes():
    styled = ("Revenue climbed 12% to US$14.4bn, comfortably ahead of our "
              "US$14.0bn estimate and consistent with a US$186 target. "
              "Margins held at 31% against net cash of US$15.5bn.")
    assert introduced_figures(DRAFT, styled) == set()


def test_a_figure_carried_from_a_sample_is_caught():
    """The whole risk of few-shot grounding, made mechanical."""
    styled = DRAFT + " Peers trade at 24x on 45% growth."
    assert introduced_figures(DRAFT, styled) == {"24", "45"}


def test_ordinals_are_not_treated_as_figures():
    styled = "Three themes. First, revenue rose 12% to US$14.4bn; PT US$186. Margins 31%, cash US$15.5bn."
    assert introduced_figures(DRAFT, styled) == set()


# ── The stylist stage ────────────────────────────────────────────────────

def _stub_llm(text):
    return patch("src.agents.pm_stylist.call_llm",
                 return_value=StylistOutput(rationale=text))


def test_restyle_replaces_the_draft_when_faithful(stub_rows):
    good = ("Revenue climbed 12% to US$14.4bn, ahead of our US$14.0bn "
            "estimate, which is what underpins the US$186 target. Margins "
            "held at 31% on net cash of US$15.5bn.")
    with _stub_llm(good):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == good
    assert audit.startswith("restyled")


def test_restyle_keeps_the_draft_when_a_figure_leaks(stub_rows):
    bad = DRAFT + " Peers trade at 24x."
    with _stub_llm(bad):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT, "a leaked figure must fall back to the draft"
    assert "rejected" in audit and "24" in audit


def test_restyle_keeps_the_draft_when_the_call_fails(stub_rows):
    with patch("src.agents.pm_stylist.call_llm", side_effect=RuntimeError("boom")):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT
    assert "failed" in audit


def test_restyle_is_skipped_without_exemplars():
    """With no register to borrow, this is just a paraphrase at a higher
    temperature — more risk than value."""
    with patch("src.memory.style_exemplars._load_rows", return_value=[]):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT
    assert "no style exemplars" in audit


def test_kill_switch(monkeypatch, stub_rows):
    monkeypatch.setenv("PM_STYLIST_DISABLED", "true")
    with _stub_llm("anything at all, long enough to pass the length floor check"):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT and audit == "stylist disabled"


def test_short_draft_is_left_alone(stub_rows):
    out, audit = restyle_rationale("D05.SI", "Too short.", {"data": {}}, "pm")
    assert out == "Too short."


def test_truncated_output_is_rejected(stub_rows):
    with _stub_llm("Revenue up."):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT
    assert "too little" in audit


# ── Sampling calibration reaches the provider, safely ────────────────────

def _bound_kwargs(provider):
    """Run call_llm far enough to capture what it binds to the model."""
    captured = {}

    class _FakeLLM:
        def bind(self, **kw):
            captured.update(kw)
            return self

        def with_structured_output(self, *a, **k):
            return self

        def invoke(self, _prompt):
            return StylistOutput(rationale="x" * 60)

    from src.llm.models import ModelProvider
    with patch("src.utils.llm.get_model", return_value=_FakeLLM()), \
         patch("src.utils.llm.get_model_info", return_value=None), \
         patch("src.utils.llm.get_agent_model_config",
               return_value=("m", ModelProvider(provider))):
        from src.utils.llm import call_llm as _call
        _call(prompt="p", pydantic_model=StylistOutput, agent_name="pm",
              state={"data": {}, "metadata": {}},
              temperature=0.6, top_p=0.9,
              presence_penalty=0.2, frequency_penalty=0.2)
    return captured


def test_penalties_are_dropped_on_anthropic():
    """Anthropic rejects presence/frequency penalties outright, so passing
    them through would turn a style tweak into a hard failure on the
    default provider."""
    kw = _bound_kwargs("Anthropic")
    assert kw.get("temperature") == 0.6
    assert kw.get("top_p") == 0.9
    assert "presence_penalty" not in kw
    assert "frequency_penalty" not in kw


@pytest.mark.parametrize("provider", ["OpenAI", "DeepSeek", "Alibaba"])
def test_penalties_pass_through_on_openai_compatible(provider):
    kw = _bound_kwargs(provider)
    assert kw.get("presence_penalty") == 0.2
    assert kw.get("frequency_penalty") == 0.2


def test_unchanged_output_is_not_reported_as_restyled(stub_rows):
    """call_llm returns its default_factory when the model cannot be
    initialised — and here that default IS the draft. Observed live with a
    missing provider key: the output was the draft and the audit still
    claimed "restyled", which would have hidden a silently dead stage.
    """
    with _stub_llm(DRAFT):
        out, audit = restyle_rationale("D05.SI", DRAFT, {"data": {}}, "pm")
    assert out == DRAFT
    assert "unchanged" in audit and "restyled" not in audit

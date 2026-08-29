"""The refresh digest reports what was learnt, or says nothing at all.

The analyst-document sync runs daily and told nobody anything. The digest
reports which documents arrived, what method and numeric assumptions were
parsed from them, and — the part that makes it self-learning rather than a
job notification — how the multiple the street applied compares with what
that industry's peers actually trade at.

Silence is a feature: no post on a no-op day, so a message in the channel
always means something changed. An unmatched document still counts as news,
because that is a report the system holds and cannot attribute (Samsung
today).

No network: the basis lookup and the comps lookup are both stubbed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.research_ideas.alerts import drive_sync_slack as ds

BASIS = {
    "BN4.SI": {"house": "Phillip Securities Research", "as_of": "2025-08-12",
               "method": "sotp", "target_multiple": 15.0,
               "multiple_basis": "pe"},
    "BIRK": {"house": "Goldman Sachs", "as_of": "13 August 2026",
             "method": "dcf", "wacc": 0.095, "terminal_growth": 0.025},
}


def _stub(basis=None, peer=None):
    """Patch the two lookups the digest makes."""
    return (
        patch.object(ds, "_peer_median", return_value=peer),
        patch("src.memory.analyst_basis.get_analyst_basis",
              side_effect=lambda t: (basis or BASIS).get(t)),
    )


def _build(result, basis=None, peer=None):
    p_peer, p_basis = _stub(basis, peer)
    with p_peer, p_basis:
        return ds.build_drive_sync_digest(result)


def _text(payload) -> str:
    out = []
    for b in payload["blocks"]:
        out.append(b.get("text", {}).get("text", ""))
        out.extend(e.get("text", "") for e in b.get("elements", []))
    return "\n".join(out)


# ── Silence ──────────────────────────────────────────────────────────────

def test_a_no_op_refresh_posts_nothing():
    """24 files listed, nothing new. The channel stays quiet."""
    payload = _build({"listed": 24, "extracted": 0, "gated": 0,
                      "matched": [], "unmatched": [], "errors": []})
    assert payload is None


def test_an_unmatched_document_is_news_on_its_own():
    """A held-but-unattributed report is worth saying even with no ingest."""
    payload = _build({"listed": 25, "extracted": 0, "matched": [],
                      "unmatched": [{"name": "Samsung_Aug2026.pdf"}],
                      "errors": []})
    assert payload is not None
    assert "Samsung_Aug2026.pdf" in _text(payload)
    assert "Matched no ticker" in _text(payload)


def test_errors_break_the_silence():
    payload = _build({"listed": 24, "extracted": 0, "matched": [],
                      "unmatched": [], "errors": ["download failed: 403"]})
    assert payload is not None and "403" in _text(payload)


# ── What was learnt ──────────────────────────────────────────────────────

def test_numeric_assumptions_are_reported():
    """Birkenstock's WACC is the assumption the parser used to drop."""
    payload = _build({"listed": 29, "extracted": 1, "matched":
                      [{"name": "b.pdf", "tickers": ["BIRK"]}],
                      "unmatched": [], "errors": []})
    text = _text(payload)
    assert "BIRK" in text and "WACC 9.5%" in text and "g 2.5%" in text


def test_the_peer_comparison_is_the_point():
    """Street multiple against the peer median, with the peer count, is what
    turns each PDF into an observation rather than a consumed number."""
    payload = _build(
        {"listed": 29, "extracted": 1,
         "matched": [{"name": "k.pdf", "tickers": ["BN4.SI"]}],
         "unmatched": [], "errors": []},
        peer={"value": 13.4723, "peer_count": 22, "basis": "sector"},
    )
    text = _text(payload)
    assert "15x" in text
    assert "13.5x sector median" in text, "median must be rounded for reading"
    assert "n=22" in text
    assert "+11%" in text, "the spread is the learning signal"


def test_no_comparison_line_when_no_peer_set_exists():
    """A missing comp yields no line rather than a spread against nothing."""
    payload = _build(
        {"listed": 29, "extracted": 1,
         "matched": [{"name": "k.pdf", "tickers": ["BN4.SI"]}],
         "unmatched": [], "errors": []},
        peer=None,
    )
    text = _text(payload)
    assert "15x" in text
    assert "median" not in text


def test_a_ticker_is_reported_once_even_across_documents():
    payload = _build({"listed": 29, "extracted": 2, "matched": [
        {"name": "a.pdf", "tickers": ["BIRK"]},
        {"name": "b.pdf", "tickers": ["BIRK"]},
    ], "unmatched": [], "errors": []})
    assert _text(payload).count("*BIRK*") == 1


def test_a_ticker_with_no_parsed_basis_is_skipped_not_blanked():
    payload = _build({"listed": 29, "extracted": 1, "matched":
                      [{"name": "x.pdf", "tickers": ["UNKNOWN"]}],
                      "unmatched": [], "errors": []}, basis={})
    assert payload is not None          # extracted>0 still speaks
    assert "UNKNOWN" not in _text(payload)


# ── Delivery contract ────────────────────────────────────────────────────

def test_poster_no_ops_without_a_webhook(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with patch.object(ds, "build_drive_sync_digest",
                      return_value={"text": "x", "blocks": []}), \
         patch.object(ds.requests, "post") as post:
        assert ds.post_drive_sync_digest({"extracted": 1}) is False
        post.assert_not_called()


def test_poster_stays_silent_when_nothing_was_learnt(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    with patch.object(ds, "build_drive_sync_digest", return_value=None), \
         patch.object(ds.requests, "post") as post:
        assert ds.post_drive_sync_digest({"extracted": 0}) is False
        post.assert_not_called()


def test_poster_never_raises(monkeypatch):
    """A digest must not be able to fail the sync that produced it."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    with patch.object(ds, "build_drive_sync_digest",
                      side_effect=RuntimeError("boom")):
        assert ds.post_drive_sync_digest({"extracted": 1}) is False
    with patch.object(ds, "build_drive_sync_digest",
                      return_value={"text": "x", "blocks": []}), \
         patch.object(ds.requests, "post", side_effect=RuntimeError("net")):
        assert ds.post_drive_sync_digest({"extracted": 1}) is False


def test_peer_median_helper_never_raises():
    with patch("src.data.regional_comps.get_fmp_classification",
               side_effect=RuntimeError("fmp down")):
        assert ds._peer_median("MU", "pe") is None

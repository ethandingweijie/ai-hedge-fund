"""Which commit does the golden baseline belong to? (Phase 0 follow-up)

``_meta`` originally carried one ``commit`` field, sourced from the replay
results' ``meta.commit`` — the commit the FIXTURES were recorded at. The
failure message in ``test_golden_valuations.py`` rendered it as

    baseline  pinned 2026-09-16T16:00:40+00:00 at 30b26702d3

which reads as "this baseline was produced by the engine at 30b2670". It was
not. Three consecutive regenerations all reported ``30b2670`` while two of them
were actually written at ``c87d38c`` and ``a740f0e``. A golden failure is the
moment someone needs to know which engine produced the pinned numbers, and the
one field that claimed to say so pointed two commits early — at a version of
the engine that provably did NOT produce them, since the whole reason for
regenerating was that the engine changed.

Both commits are now recorded, separately and labelled. These tests exist
because the two values are the same string most of the time in casual reading
(both short SHAs, both in ``_meta``) and it would be very easy to collapse
them back into one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.memory import golden_snapshots as gs

TESTS_DIR = Path(__file__).resolve().parent

#: Deliberately different, and deliberately not a prefix of one another.
_FIXTURE_COMMIT = "30b26702d3c786e1835dd8e5cc629191e3d75c95"
_HEAD_COMMIT = "a740f0e1b2c3d4e5f60718293a4b5c6d7e8f9012"


def _replay(ticker: str = "BN4.SI", fixture_commit: str = _FIXTURE_COMMIT) -> dict:
    """One replay result, shaped the way ``golden_replay.replay_fixture``
    returns it — only the fields ``build_doc`` reads."""
    return {
        "ticker": ticker,
        "base_iv": 5.2,
        "projection": {"scenarios.base.intrinsic_value": 5.2},
        "meta": {
            "commit": fixture_commit,
            "captured_at": "2026-09-13T13:22:12+00:00",
            "state_source": "web_runs",
        },
    }


# ── build_doc ───────────────────────────────────────────────────────────────

def test_the_fixture_commit_and_the_regeneration_commit_are_both_recorded():
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r",
                       commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
    meta = doc["_meta"]
    assert meta["commit"] == _FIXTURE_COMMIT
    assert meta["regenerated_at_commit"] == _HEAD_COMMIT


def test_the_two_commits_are_never_collapsed_into_one_field():
    """The regression this file exists for: one field serving both meanings."""
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r",
                       commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
    meta = doc["_meta"]
    assert meta["commit"] != meta["regenerated_at_commit"], (
        "the fixture commit and the regeneration commit are the same value; "
        "either the test inputs stopped differing or the two fields were "
        "collapsed back together"
    )
    assert meta["regenerated_at_commit"] != _FIXTURE_COMMIT
    assert meta["commit"] != _HEAD_COMMIT


def test_head_commit_defaults_rather_than_silently_becoming_the_fixture_commit():
    """A caller that forgets to pass it must get a visible placeholder.

    Defaulting to ``commit`` would reproduce the original bug exactly, and
    quietly: every field present, every value plausible, wrong attribution.
    """
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r", commit=_FIXTURE_COMMIT)
    assert doc["_meta"]["regenerated_at_commit"] == "unknown"
    assert doc["_meta"]["commit"] == _FIXTURE_COMMIT


def test_commit_defaults_to_unknown_when_no_replay_carries_one():
    doc = gs.build_doc({"X": {"ticker": "X", "base_iv": None,
                              "projection": {}, "meta": {}}},
                       reason="r", head_commit=_HEAD_COMMIT)
    assert doc["_meta"]["commit"] == "unknown"
    assert doc["_meta"]["regenerated_at_commit"] == _HEAD_COMMIT


def test_per_ticker_recorded_commit_survives_untouched():
    """``_meta.commit`` and the per-ticker field are different granularities.

    Fixtures are recorded independently, so a single baseline can legitimately
    mix fixture vintages; the per-ticker value is what says which is which.
    """
    older = "0b650ff0000000000000000000000000000000000"
    doc = gs.build_doc({"BN4_SI": _replay(), "MELI": _replay("MELI", older)},
                       reason="r", commit=_FIXTURE_COMMIT,
                       head_commit=_HEAD_COMMIT)
    assert doc["BN4_SI"]["recorded_commit"] == _FIXTURE_COMMIT
    assert doc["MELI"]["recorded_commit"] == older
    assert doc["BN4_SI"]["ticker"] == "BN4.SI"


def test_build_doc_is_sorted_and_carries_the_projections():
    doc = gs.build_doc({"U96_SI": _replay("U96.SI"), "BN4_SI": _replay()},
                       reason="r", commit=_FIXTURE_COMMIT,
                       head_commit=_HEAD_COMMIT)
    assert list(doc) == ["_meta", "BN4_SI", "U96_SI"]
    assert doc["BN4_SI"]["projection"] == {"scenarios.base.intrinsic_value": 5.2}
    assert doc["BN4_SI"]["base_iv"] == 5.2
    assert doc["_meta"]["tolerance"] == gs.TOLERANCE
    assert doc["_meta"]["tickers"] == 2


# ── append_changelog ────────────────────────────────────────────────────────

@pytest.fixture
def changelog(tmp_path, monkeypatch) -> Path:
    """Redirect the changelog so tests cannot touch the committed audit trail."""
    p = tmp_path / "CHANGELOG.md"
    monkeypatch.setattr(gs, "CHANGELOG_PATH", p)
    return p


def test_changelog_labels_both_commits_and_which_is_which(changelog):
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="the reason",
                       commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
    gs.append_changelog(doc, reason="the reason")
    text = changelog.read_text(encoding="utf-8")
    assert f"- regenerated at HEAD: `{_HEAD_COMMIT}`" in text
    assert f"- fixtures recorded at: `{_FIXTURE_COMMIT}`" in text
    assert "- reason: the reason" in text


def test_changelog_no_longer_writes_a_bare_commit_line(changelog):
    """``- commit: <sha>`` is the ambiguous line this change removes.

    It did not say whose commit it was, and every reader assumed "the
    baseline's". Asserting its absence is what stops it coming back.
    """
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r",
                       commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
    gs.append_changelog(doc, reason="r")
    for line in changelog.read_text(encoding="utf-8").splitlines():
        assert not line.strip().startswith("- commit:"), (
            f"ambiguous attribution line reintroduced: {line!r}"
        )


def test_changelog_writes_its_header_once_and_appends_thereafter(changelog):
    for reason in ("first", "second"):
        doc = gs.build_doc({"BN4_SI": _replay()}, reason=reason,
                           commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
        gs.append_changelog(doc, reason=reason)
    text = changelog.read_text(encoding="utf-8")
    assert text.count("# Golden valuation snapshots") == 1
    assert text.count("## ") == 2
    assert "- reason: first" in text and "- reason: second" in text


def test_changelog_records_unknown_when_the_regeneration_commit_is_missing(
        changelog):
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r", commit=_FIXTURE_COMMIT)
    gs.append_changelog(doc, reason="r")
    assert "- regenerated at HEAD: `unknown`" in changelog.read_text(
        encoding="utf-8")


# ── save ────────────────────────────────────────────────────────────────────

def test_save_writes_readable_json_and_appends_the_changelog(
        tmp_path, monkeypatch, changelog):
    snap = tmp_path / "snapshots.json"
    monkeypatch.setattr(gs, "SNAPSHOT_PATH", snap)
    doc = gs.build_doc({"BN4_SI": _replay()}, reason="r",
                       commit=_FIXTURE_COMMIT, head_commit=_HEAD_COMMIT)
    out = gs.save(doc, reason="r")
    assert out == snap
    reloaded = json.loads(snap.read_text(encoding="utf-8"))
    assert reloaded["_meta"]["regenerated_at_commit"] == _HEAD_COMMIT
    assert gs.tickers_in(reloaded) == ["BN4_SI"]
    assert changelog.exists()


def test_the_committed_baseline_records_a_regeneration_commit():
    """The real file, not a tmp copy.

    Baselines written before the field existed will fail this — deliberately.
    The next ``--update-snapshots`` run adds it, and until then the failure
    message falls back to saying the engine commit is unknown rather than
    naming the wrong one.
    """
    if not gs.exists():
        pytest.skip(f"{gs.SNAPSHOT_PATH} not present")
    meta = (gs.load().get("_meta") or {})
    assert "regenerated_at_commit" in meta, (
        "tests/golden/snapshots.json predates regenerated_at_commit; the next "
        "--update-snapshots will add it. Until then the golden failure message "
        "says the engine commit is unknown instead of naming the fixture's."
    )


# ── the failure message ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def suite_source() -> str:
    return (TESTS_DIR / "test_golden_valuations.py").read_text(encoding="utf-8")


def test_the_failure_message_passes_head_commit_to_build_doc(suite_source):
    assert "head_commit=_head_commit()" in suite_source, (
        "the baseline is regenerated without recording which engine produced "
        "it — _meta.regenerated_at_commit would stay 'unknown' forever"
    )


def test_the_failure_message_does_not_present_the_fixture_commit_as_the_baseline(
        suite_source):
    """The exact string that misled: "pinned <when> at <fixture commit>"."""
    assert "pinned {snap_meta.get('generated_at', '?')} at " not in suite_source
    # And the two must be rendered on separately labelled lines.
    assert "regenerated at HEAD " in suite_source
    assert "fixtures recorded at " in suite_source


def test_the_failure_message_has_a_fallback_for_older_baselines(suite_source):
    """A baseline with no ``regenerated_at_commit`` must not render ``None``.

    It should say the engine commit is unknown. Guessing — falling back to the
    fixture commit — is the original bug wearing a different hat.
    """
    assert "baseline predates " in suite_source
    assert "engine commit unknown" in suite_source

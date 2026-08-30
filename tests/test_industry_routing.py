"""A sector report matches no ticker, so today it is downloaded and dropped.

`match_tickers` resolves company names; an industry title resolves to nothing,
so the document is registered as `unmatched` and never read. `match_industry`
routes it instead to `(market, sector, profile)` — the same key an equity is
routed through, and the key `industry_knowledge` and the 2F block already use.

The vocabulary is DERIVED from the profile names rather than hand-listed, for
the reason `build_derived_aliases` is: a hand-kept table drifts out of step
with the routes the engine actually uses, and the drift is invisible until a
document has been filed against a key nothing reads.

Two failures matter more than the matches:

  * an equity note must never take the industry path — one company's numbers
    stored as the whole industry's;
  * a note must never be filed against a market it is not about, because
    `Money Center Bank` is JPM, BAC, C, WFC *and* 02888.HK.
"""

from __future__ import annotations

import pytest

from app.backend.services import drive_sync as ds


@pytest.fixture(scope="module")
def vocab():
    return ds.build_industry_vocabulary()


def _routes(name, vocab):
    return [(r["market"], r["sector"], r["profile"])
            for r in ds.match_industry(name, vocab)]


# ── The documents the work exists for ────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Singapore Banking Sector Outlook.pdf",
     ("SES", "Financials", "Money Center Bank (SG)")),
    ("Semiconductor Industry Primer.pdf",
     ("", "Semiconductor", "")),
    ("Managed Care Industry Chartbook.pdf",
     ("", "HealthcareServices", "Managed Care")),
    ("Korea Memory Sector Deep Dive.pdf",
     ("KSC", "Semiconductor", "Memory / DRAM-NAND")),
    ("US Office REIT Sector Outlook.pdf",
     ("US", "REIT", "US Office")),
])
def test_a_sector_document_routes_to_an_industry(name, expected, vocab):
    assert expected in _routes(name, vocab)


def test_hyphens_are_read_both_ways(vocab):
    """They separate words in one filename and belong to the term in the
    next, and nothing in the character says which. Collapsing them lost
    S-REIT; keeping them lost managed-care."""
    assert _routes("Korea-Memory-Sector-Outlook.pdf", vocab) == \
        [("KSC", "Semiconductor", "Memory / DRAM-NAND")]
    assert _routes("S-REIT Sector Outlook 2026.pdf", vocab) == \
        [("SES", "REIT", "")]
    assert _routes("Managed-Care-Industry-Primer.pdf", vocab) == \
        [("", "HealthcareServices", "Managed Care")]


def test_one_phrase_may_honestly_name_two_routes(vocab):
    """An automotive sector note is about OEMs and EV names alike, and the
    taxonomy splits them."""
    got = _routes("Japan Automotive Sector Primer.pdf", vocab)
    assert ("JPX", "Consumer", "Automotive & EV") in got
    assert ("JPX", "Industrials", "Automotive (OEM)") in got


def test_a_named_profile_outranks_the_bare_sector(vocab):
    """Storing both files the same note twice, and the sector copy would
    then also serve every other REIT sub-profile."""
    assert _routes("US Office REIT Sector Outlook.pdf", vocab) == \
        [("US", "REIT", "US Office")]


# ── An equity note must never take the industry path ─────────────────────

@pytest.mark.parametrize("name", [
    "Micron_Q3FY26.pdf",
    "Keppel DC Reit Aug 2025.pdf",
    "DBS Group 1H26 results.pdf",
    "Apple FY26 Outlook.pdf",
])
def test_an_equity_note_still_resolves_to_its_ticker(name, vocab):
    assert ds.match_tickers(name), "the equity path must still win"


def test_a_company_filename_without_the_marker_routes_nowhere(vocab):
    """The guard that stops one company's numbers being stored as an
    industry's: "Sea Limited 2026.pdf" is not in the gazetteer, so without a
    sector marker in the filename it must resolve to nothing at all."""
    assert ds.match_tickers("Sea Limited 2026.pdf") == []
    assert _routes("Sea Limited 2026.pdf", vocab) == []


def test_an_unrelated_document_is_not_a_sector_note():
    assert ds.looks_like_sector_document("invoice_2026.pdf") is False
    assert ds.looks_like_sector_document("Global eCommerce Handbook.pdf") is True


# ── Market separation ────────────────────────────────────────────────────

def test_the_market_in_the_title_picks_the_market_scoped_profile(vocab):
    """`Money Center Bank (SG)` is DBS/OCBC/UOB; `Money Center Bank` is
    JPM/BAC/C/WFC. Filing the Singapore note against the US route is exactly
    the collision the store is keyed to prevent."""
    assert ("SES", "Financials", "Money Center Bank (SG)") in \
        _routes("Singapore Banking Sector Outlook.pdf", vocab)
    hk = _routes("Hong Kong Banks Sector Outlook.pdf", vocab)
    assert ("HKSE", "Financials", "Money Center Bank") in hk
    assert not any(m == "US" for m, _s, _p in hk)


def test_a_global_note_is_market_agnostic_not_a_market(vocab):
    """A global handbook is not a claim about one exchange."""
    assert all(m == "" for m, _s, _p in
               _routes("Global Cybersecurity Thematic.pdf", vocab))


@pytest.mark.parametrize("title, market", [
    ("Japan Automotive Sector Primer.pdf", "JPX"),
    ("Korea Memory Sector Outlook.pdf", "KSC"),
    ("China Semiconductor Industry Primer.pdf", "SHH"),
    ("Singapore REIT Sector Outlook.pdf", "SES"),
])
def test_every_registered_market_is_detectable(title, market, vocab):
    got = ds.match_industry(title, vocab)
    assert got and all(r["market"] == market for r in got)


# ── The vocabulary stays honest about what it does not cover ─────────────

def test_a_grab_bag_sector_is_never_a_routing_target(vocab):
    """`(Tech, "")` is 44 names spanning search, e-commerce, hardware and
    enterprise software; `(RealEstate, "")` is 43. A note filed against
    either would describe none of them, so blank-profile routes exist only
    for sectors that are one industry."""
    for phrase, routes in vocab.items():
        for _market, sector, profile in routes:
            if not profile:
                assert sector in ds._SECTOR_ONLY_ROUTABLE, (
                    f"{phrase!r} routes to the bare sector {sector}"
                )


def test_a_generic_word_needs_its_sector_named(vocab):
    """"China" is a REIT sub-profile AND a country; "Office" and "Retail"
    are sub-profiles AND ordinary English."""
    assert _routes("China Sector Outlook.pdf", vocab) == []
    assert _routes("Global Retail Industry Primer.pdf", vocab) == []
    # Naming the sector unlocks it — and "China" is read as the market too,
    # which is right: a China REIT note is about Chinese-listed REITs.
    china_reit = _routes("China REIT Sector Outlook.pdf", vocab)
    assert china_reit == [("SHH", "REIT", "China")]


def test_the_vocabulary_tracks_the_taxonomy_rather_than_a_hand_list(vocab):
    """Every named profile the engine routes an equity through is reachable
    by its own name — that is what keeps the two from drifting apart."""
    named = {(s, p) for s, p in ds._all_routes()
             if p and (s, p) not in ds._DUPLICATE_ROUTES}
    reachable = {(s, p) for routes in vocab.values() for _m, s, p in routes}
    missing = named - reachable
    assert not missing, f"profiles no filename can reach: {sorted(missing)}"


def test_derivation_survives_an_unavailable_taxonomy():
    """The vocabulary is rebuilt on every sync, so a broken import here must
    degrade to 'no industry routing' rather than take the whole sync down —
    the equity half of that sync is the half that already works."""
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"src.data": None}):
        assert ds._all_routes() == set()          # ImportError swallowed
        assert ds.build_industry_vocabulary()     # synonyms stand alone


def test_no_routes_means_no_industry_routing_not_a_crash():
    from unittest.mock import patch
    with patch.object(ds, "_all_routes", return_value=set()):
        vocab = ds.build_industry_vocabulary()
    assert ds.match_industry("Semiconductor Industry Primer.pdf", {}) == []
    # The synonyms stand on their own and still resolve; the DERIVED half
    # ("Managed Care" is a profile name, not a synonym) is simply absent.
    assert ds.match_industry("Global Airlines Sector Outlook.pdf", vocab)
    assert ds.match_industry("Managed Care Industry Chartbook.pdf", vocab) == []

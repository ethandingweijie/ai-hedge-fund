"""drive_sync filename → ticker matching.

The archive is filled by filename ("Keppel_Aug 2025.pdf"), not by ticker,
so matching is what decides whether a document ever reaches the research
manifest. A document that matches nothing is downloaded, attributed to no
ticker, and silently dropped — the SOTP extractor and the analyst-basis
benchmark never see it.

On the live folder the gazetteer held 444 tickers, none ending in `.SI`,
and 11 of 20 files matched nothing: DBS, OCBC, Keppel, ST Engineering,
Sembcorp, four REITs, PropNex and Sheng Siong. That is the entire reason
BABA rendered a business-by-business SOTP table and Keppel did not.

No network: these pin the matcher against the real filenames.
"""

from __future__ import annotations

import pytest

from app.backend.services.drive_sync import (
    build_derived_aliases,
    build_gazetteer,
    match_tickers,
)

# Filenames exactly as they appear in the shared archive.
ARCHIVE = [
    ("Alibaba Group (BABA)_ 1QFY27 review_ Cloud acceleration.pdf", {"BABA"}),
    ("Amazon.com Inc. (AMZN)_ Q2'26 Review_ AWS Revenue Growth.pdf", {"AMZN"}),
    ("Apple_Aug 2026.pdf",                       {"AAPL"}),
    ("Capitaland China Trust_Aug 2025.pdf",      {"AU8U.SI"}),
    ("Capitaland India Trust_July 2026.pdf",     {"CY6U.SI"}),
    ("DBS_20260504.pdf",                         {"D05.SI"}),
    ("Fraser Centrepoint Trust_July 2026.pdf",   {"J69U.SI"}),
    ("JPM_20251021.pdf",                         {"JPM"}),
    ("Keppel DC Reit _ July 2026.pdf",           {"AJBU.SI"}),
    ("Keppel_Aug 2025.pdf",                      {"BN4.SI"}),
    ("OCBC_20260511.pdf",                        {"O39.SI"}),
    ("OUE Reit_July 2026.pdf",                   {"TS0U.SI"}),
    ("Propnex_Aug 26.pdf",                       {"OYY.SI"}),
    ("ST Engineering_Aug 2026.pdf",              {"S63.SI"}),
    ("Sembcorp Industries_2025.pdf",             {"U96.SI"}),
    ("Sheng Siong_July 2026.pdf",                {"AGS.SI"}),
    ("Tencent_Sep 2025.pdf",                     {"00700.HK"}),
]


@pytest.fixture(scope="module")
def gazetteer():
    return build_gazetteer()


def test_gazetteer_includes_sgx(gazetteer):
    """Without SGX in the gazetteer every Singapore document is dropped."""
    sgx = {t for t in gazetteer if t.endswith(".SI")}
    assert len(sgx) >= 70, f"only {len(sgx)} SGX tickers in the gazetteer"
    assert {"D05.SI", "BN4.SI", "S63.SI", "AJBU.SI"} <= sgx


@pytest.mark.parametrize("filename, expected", ARCHIVE)
def test_archive_filenames_match_their_ticker(filename, expected, gazetteer):
    matched = set(match_tickers(filename, gazetteer))
    assert expected <= matched, (
        f"{filename!r} -> {sorted(matched)}, expected to include {sorted(expected)}"
    )


def test_parent_and_subsidiary_do_not_collide(gazetteer):
    """Longest match wins, and a matched span is consumed.

    "Keppel DC Reit" contains "keppel". Without span consumption the REIT
    note is attributed to the conglomerate as well, and a REIT document
    becomes evidence for Keppel Ltd's SOTP.
    """
    reit = set(match_tickers("Keppel DC Reit _ July 2026.pdf", gazetteer))
    parent = set(match_tickers("Keppel_Aug 2025.pdf", gazetteer))
    assert "AJBU.SI" in reit and "BN4.SI" not in reit
    assert "BN4.SI" in parent and "AJBU.SI" not in parent


def test_sibling_trusts_do_not_collide(gazetteer):
    """Three CapitaLand vehicles share a leading word."""
    china = set(match_tickers("Capitaland China Trust_Aug 2025.pdf", gazetteer))
    india = set(match_tickers("Capitaland India Trust_July 2026.pdf", gazetteer))
    assert china == {"AU8U.SI"}
    assert india == {"CY6U.SI"}


def test_apple_is_not_apple_hospitality_reit(gazetteer):
    """`entry[3]` is a company name in the SGX table but a free-text note in
    the US one — AAPL's reads "Hardware + services + AI capex", while
    APLE's reads "Apple Hospitality REIT — hotels". Deriving aliases across
    both tables sent an Apple report to the hotel REIT."""
    matched = set(match_tickers("Apple_Aug 2026.pdf", gazetteer))
    assert "AAPL" in matched
    assert "APLE" not in matched


def test_derived_aliases_are_sgx_only():
    """US and HK names stay on the curated list; only SGX is derived."""
    aliases = build_derived_aliases()
    assert aliases, "no aliases derived"
    for _stem, tickers in aliases.items():
        for t in tickers:
            assert t.endswith(".SI"), f"non-SGX ticker {t} in derived aliases"


def test_ambiguous_leading_word_resolves_to_nothing():
    """A leading word owned by several tickers must not pick one.

    "capitaland" alone spans CapitaLand China Trust, CapitaLand India Trust
    and CapitaLand Investment, so it is registered for none of them.
    """
    aliases = build_derived_aliases()
    assert "capitaland" not in aliases


def test_matcher_never_guesses_on_an_unknown_name(gazetteer):
    assert match_tickers("Quarterly Macro Outlook_2026.pdf", gazetteer) == []


# ── Substring collisions ─────────────────────────────────────────────────

def test_micron_is_not_micro_mechanics(gazetteer):
    """"micro" (Micro-Mechanics, 5DD.SI) sits inside "micron".

    Alias matching was a bare substring search, so Goldman's Micron note
    was attributed to a Singapore precision engineer. Aliases now match on
    a word boundary.
    """
    assert set(match_tickers("Micron_June2026.pdf", gazetteer)) == {"MU"}
    assert set(match_tickers("Micro-Mechanics_2026.pdf", gazetteer)) == {"5DD.SI"}


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Nvidia_Aug2026.pdf",    {"NVDA"}),
        ("Coreweave_Aug2026.pdf", {"CRWV"}),
        ("Nebius_Aug2026.pdf",    {"NBIS"}),
        ("Micron_June2026.pdf",   {"MU"}),
    ],
)
def test_ai_infrastructure_reports_match(filename, expected, gazetteer):
    assert set(match_tickers(filename, gazetteer)) == expected


def test_no_alias_matches_inside_a_longer_word(gazetteer):
    """A boundary-free alias silently claims any filename containing it."""
    for filename in ("Microscopy Weekly.pdf", "Applesauce Corp.pdf",
                     "Jdcom Holdings Unrelated.pdf"):
        for ticker in match_tickers(filename, gazetteer):
            assert ticker not in {"5DD.SI", "AAPL"}, (
                f"{filename} wrongly matched {ticker}")

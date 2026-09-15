"""One dual-listing table for the repo: HK line <-> US ADR."""
import pytest

from src.data import dual_listings as dl


@pytest.mark.parametrize("hk,adr,ratio", [
    ("09988.HK", "BABA", 8), ("9988.HK", "BABA", 8), ("09618.HK", "JD", 2),
    ("09888.HK", "BIDU", 8), ("09999.HK", "NTES", 5), ("09961.HK", "TCOM", 1),
    ("02015.HK", "LI", 2), ("09866.HK", "NIO", 1), ("09868.HK", "XPEV", 2),
])
def test_hk_lines_resolve_to_their_adr_and_back(hk, adr, ratio):
    assert dl.adr_for(hk) == adr
    assert dl.company_key(hk) == adr and dl.company_key(adr) == adr
    assert dl.hk_for(adr) == dl._canonical(hk)
    assert dl.ads_ratio(hk) == ratio == dl.ads_ratio(adr)
    assert dl.listings_for(hk) == [adr, dl._canonical(hk)] == dl.listings_for(adr)


def test_single_listings_are_their_own_company():
    assert dl.company_key("0700.HK") == "00700.HK"
    assert dl.adr_for("00700.HK") is None and dl.hk_for("MSFT") is None
    assert dl.listings_for("D05.SI") == ["D05.SI"]


def test_the_sec_alias_is_derived_and_keeps_known_absent_filers():
    from src.tools.sec_segments import _ADR_FILER_ALIAS
    assert _ADR_FILER_ALIAS["09988.HK"] == "BABA" and _ADR_FILER_ALIAS["09999.HK"] == "NTES"
    for hk in ("00700.HK", "01810.HK", "03690.HK"):
        assert hk in _ADR_FILER_ALIAS and _ADR_FILER_ALIAS[hk] is None


def test_snapshot_lookup_uses_the_shared_table():
    from src.agents.analysis.sotp_snapshot import lookup_snapshot
    snap = {"NTES": {"segments": [{"name": "Games"}]}}
    assert lookup_snapshot(snap, "9999.HK")[0] == "NTES"

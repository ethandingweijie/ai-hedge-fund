"""Workstream R1.e — drive_sync offline tests: folder-ref parsing,
filename→ticker matching (the 5 real GS reports + never-guess guard), and
Tier-A embeddedfolderview HTML parsing. No network: listing is fed a
canned flip-entry page via a patched requests.get."""
from __future__ import annotations

import pytest

from app.backend.services import drive_sync as ds

# Exact-token gazetteer fixture (stands in for TICKER_SECTOR_LOOKUP keys;
# HK names use the canonical 5-digit zero-padded form).
_GAZ = {"BABA", "JD", "AMZN", "META", "MSFT", "CRWD", "JPM", "PDD",
        "00700.HK", "09988.HK", "09618.HK", "03690.HK"}


# ── folder_id_from_ref ────────────────────────────────────────────────────────

def test_folder_id_from_url_forms():
    fid = "1sVyHVhQ9i-fOb2hwcovX3bMjYQHf6FX1"
    assert ds.folder_id_from_ref(
        f"https://drive.google.com/drive/folders/{fid}") == fid
    assert ds.folder_id_from_ref(
        f"https://drive.google.com/drive/u/0/folders/{fid}") == fid
    assert ds.folder_id_from_ref(
        f"https://drive.google.com/embeddedfolderview?id={fid}") == fid
    assert ds.folder_id_from_ref(fid) == fid
    assert ds.folder_id_from_ref("") is None
    assert ds.folder_id_from_ref("not-a-folder") is None


# ── match_tickers ─────────────────────────────────────────────────────────────

def test_match_exact_token_convention():
    # GS "Company (TICKER)" convention; the company-name alias also
    # attaches the HK dual listing (same underlying company — the
    # deposited report is the ONLY sell-side source for that name).
    name = "Alibaba Group (BABA)_ 1QFY27 review_ Cloud acceleration.pdf"
    assert ds.match_tickers(name, _GAZ) == ["BABA", "09988.HK"]
    name = "Amazon.com Inc. (AMZN)_ Q2'26 Review_ AWS Revenue Growth.pdf"
    assert ds.match_tickers(name, _GAZ) == ["AMZN"]


def test_match_multi_ticker_alias_file():
    # The real multi-ticker deposit: JD + BABA (+09988.HK) + meituan
    got = ds.match_tickers("Alibaba_Meituan_JD", _GAZ)
    assert set(got) == {"JD", "BABA", "09988.HK", "03690.HK"}


def test_match_all_five_real_folder_files():
    names = [
        "Alibaba Group (BABA)_ 1QFY27 review_ Cloud acceleration…Buy.pdf",
        "Alibaba_Meituan_JD",
        "Amazon.com Inc. (AMZN)_ Q2'26 Review_ AWS Revenue Growth….pdf",
        "Meta Platforms (META)_ Q2'26 Review.pdf",
        "Microsoft (MSFT)_ 4QFY Review.pdf",
    ]
    results = [ds.match_tickers(n, _GAZ) for n in names]
    assert results[0] == ["BABA", "09988.HK"]
    assert set(results[1]) == {"JD", "BABA", "09988.HK", "03690.HK"}
    assert results[2] == ["AMZN"]
    assert results[3] == ["META"]
    assert results[4] == ["MSFT"]


def test_match_never_guesses():
    assert ds.match_tickers("Random Company Report.pdf", _GAZ) == []
    assert ds.match_tickers("notes.txt", _GAZ) == []
    # Stop tokens never match even if the gazetteer contained them
    assert ds.match_tickers("GROUP HOLDINGS BUY.pdf", {"GROUP", "BUY"}) == []


def test_match_bare_hk_number_forms():
    # "(0700)" in a filename resolves to the canonical 00700.HK
    got = ds.match_tickers("Internet Sector (0700) update.pdf", _GAZ)
    assert got == ["00700.HK"]
    # Bare 4-digit non-year forms still resolve ("9988" → 09988.HK)
    assert ds.match_tickers("Coverage (9988) initiation.pdf", _GAZ) == \
        ["09988.HK"]


def test_match_possessive_single_char_noise():
    """Live 2026-08-24: 'Microsoft's' splits on the apostrophe into
    'Microsoft' + 's', and the bare 'S' token matched SentinelOne —
    polluting the MSFT doc with a phantom ticker. Single-char tokens
    must never match on the exact-token path."""
    gaz = _GAZ | {"S", "T", "K", "F"}
    name = ("Microsoft Corp. (MSFT)_ 4QFY_ Microsoft's role is "
            "increasingly strategic as enterprises move from frontier "
            "models to frontier.pdf")
    assert ds.match_tickers(name, gaz) == ["MSFT"]


def test_match_year_and_short_digit_fragments_not_hk():
    # Year/quarter fragments must not zero-pad into HK codes even when
    # the zero-padded code happens to exist in the gazetteer
    gaz = _GAZ | {"00026.HK", "02026.HK", "00003.HK"}
    assert ds.match_tickers("Sector outlook Q2'26 (3).pdf", gaz) == []
    assert ds.match_tickers("Annual review 2026.pdf", gaz) == []
    assert ds.match_tickers("FY2027 prep notes.pdf", gaz) == []


# ── Tier A listing parse (canned flip-entry page) ────────────────────────────

_TIER_A_PAGE = """
<html><body>
<div class="flip-entry" id="entry-1Hhb_AKAzy5c9_RcTu9fDZLmPKKKTyLai">
  <div class="flip-entry-title">Alibaba Group (BABA)_ 1QFY27 review.pdf</div>
  <div class="flip-entry-last-modified">2 days ago</div>
</div>
<div class="flip-entry" id="entry-1uFTZPINeBPFdjQjCAjpnQmQcLXqlXUwt">
  <div class="flip-entry-title">Amazon.com Inc. (AMZN)_ Q2&#39;26 Review.pdf</div>
</div>
</body></html>
"""


class _FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_list_tier_a_parses_entries(monkeypatch):
    monkeypatch.setattr(
        ds.requests, "get",
        lambda url, timeout=None, headers=None: _FakeResp(200, _TIER_A_PAGE))
    entries = ds._list_tier_a("FOLDERID1234567890abcd")
    assert len(entries) == 2
    assert entries[0]["file_id"] == "1Hhb_AKAzy5c9_RcTu9fDZLmPKKKTyLai"
    assert entries[0]["name"].startswith("Alibaba Group (BABA)")
    assert entries[1]["file_id"] == "1uFTZPINeBPFdjQjCAjpnQmQcLXqlXUwt"
    # HTML entity decoded in the title
    assert "Q2'26" in entries[1]["name"]


def test_list_tier_a_detects_signin_wall(monkeypatch):
    wall = "<html>redirect to https://accounts.google.com/ServiceLogin</html>"
    monkeypatch.setattr(
        ds.requests, "get",
        lambda url, timeout=None, headers=None: _FakeResp(200, wall))
    with pytest.raises(RuntimeError, match="sign-in wall"):
        ds._list_tier_a("FOLDERID1234567890abcd")


def test_list_tier_a_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        ds.requests, "get",
        lambda url, timeout=None, headers=None: _FakeResp(404, ""))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        ds._list_tier_a("FOLDERID1234567890abcd")


# ── Sync engine guards ────────────────────────────────────────────────────────

def test_sync_disabled_without_folder_env(monkeypatch):
    monkeypatch.delenv("DRIVE_SYNC_FOLDER", raising=False)
    result = ds.sync_drive_folder(repo_root="/tmp/does-not-matter")
    assert result["errors"]
    assert "DRIVE_SYNC_FOLDER" in result["errors"][0]
    assert result["listed"] == 0


# ── Sync engine: unchanged-branch data-loss regression (live 2026-08-24) ────
#
# Gate-4 re-trigger emptied research/pdfs/: download names are deterministic
# (<sha12>_<fid8>.pdf), so the re-download's dest EQUALLED the stored
# local_path and the "drop the duplicate" os.remove(dest) deleted the ONLY
# copy while reporting unchanged=5. Two guards are pinned below:
#   1. unchanged must NOT remove dest when dest == local_path;
#   2. "same hash known" must require the file to exist ON DISK — a
#      manifest-only hash would mask the loss and skip the re-download.

import hashlib

import src.utils.research_pdf as _rp

_FAKE_BYTES = b"%PDF-1.4 fake analyst report bytes"
_FAKE_HASH = hashlib.sha256(_FAKE_BYTES).hexdigest()
_FID = "1CE7r3QBg0ChCQaZW-u6OW0-LcytOL6_S"


def _patch_listing_and_manifest(monkeypatch, manifest):
    monkeypatch.setattr(ds, "list_drive_folder", lambda folder_id: [
        {"file_id": _FID, "name": "Alibaba Group (BABA)_ review.pdf"}])
    monkeypatch.setattr(_rp, "_load_manifest_raw", lambda root: manifest)


def test_sync_unchanged_keeps_file_when_dest_collides(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "research" / "pdfs"
    pdf_dir.mkdir(parents=True)
    kept = pdf_dir / "b7be22915d72_1CE7r3QB.pdf"
    kept.write_bytes(_FAKE_BYTES)
    manifest = {"documents": [{
        "path": str(kept), "ticker": "BABA", "drive_file_id": _FID,
        "content_hash": _FAKE_HASH, "source": "drive",
        "ai_input_allowed": True}]}
    _patch_listing_and_manifest(monkeypatch, manifest)
    # Deterministic naming: the re-download lands on the SAME path
    monkeypatch.setattr(ds, "download_drive_file",
                        lambda file_id, dest_dir: str(kept))

    result = ds.sync_drive_folder(
        repo_root=str(tmp_path), folder_ref="FOLDERID1234567890abcd",
        auto_allow=True)

    assert result["unchanged"] == 1
    assert result["downloaded"] == 0
    assert result["errors"] == []
    assert kept.exists(), "unchanged branch deleted the ONLY copy"


def test_sync_restores_registered_file_missing_from_disk(tmp_path,
                                                          monkeypatch):
    pdf_dir = tmp_path / "research" / "pdfs"
    pdf_dir.mkdir(parents=True)
    lost = pdf_dir / "b7be22915d72_1CE7r3QB.pdf"   # registered, NOT on disk
    manifest = {"documents": [{
        "path": str(lost), "ticker": "BABA", "drive_file_id": _FID,
        "content_hash": _FAKE_HASH, "source": "drive",
        "ai_input_allowed": True}]}
    _patch_listing_and_manifest(monkeypatch, manifest)

    def _fake_download(file_id, dest_dir):
        lost.write_bytes(_FAKE_BYTES)               # fresh download restores
        return str(lost)
    monkeypatch.setattr(ds, "download_drive_file", _fake_download)

    registered = []
    monkeypatch.setattr(
        _rp, "register_documents",
        lambda root, path, tickers, **kw: registered.append((path, kw)))

    result = ds.sync_drive_folder(
        repo_root=str(tmp_path), folder_ref="FOLDERID1234567890abcd",
        auto_allow=False)

    # Manifest hash matches but nothing is on disk → must re-download and
    # re-register, NOT count unchanged (the masking failure mode)
    assert result["downloaded"] == 1
    assert result["unchanged"] == 0
    assert result["gated"] == 1          # auto_allow=False gate preserved
    assert lost.exists()
    assert len(registered) == 1
    assert registered[0][1].get("drive_file_id") == _FID
    assert registered[0][1].get("content_hash") == _FAKE_HASH

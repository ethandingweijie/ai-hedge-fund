"""
app/backend/services/drive_sync.py
==================================
Workstream R1.e — Google Drive analyst-report folder sync (worker side).

The user saves sell-side reports into a shared Drive folder ("Earnings
Transcript_Equitable"); this service mirrors it into the research deposit
registry (research/manifest.json) and triggers R1 extraction.

Tier A (verified live 2026-08-24): the folder is shared "Anyone with the
link", so BOTH listing and download work anonymously over plain HTTP —
no Drive API, no OAuth, no new dependency:

  * list:     https://drive.google.com/embeddedfolderview?id=<FOLDER_ID>
              (flip-entry blocks: entry-<FILE_ID> + flip-entry-title)
  * download: https://drive.google.com/uc?export=download&id=<FILE_ID>
              (confirm=t cookie retry for the large-file virus-scan page)

Tier B (only if the folder is ever re-restricted): Drive API v3 over
requests with an OAuth refresh token (GOOGLE_OAUTH_CLIENT_ID / _SECRET /
_REFRESH_TOKEN). Same downstream pipeline; only the fetcher differs.

Sync semantics:
  * the manifest is the sync-state store — drive entries carry
    source="drive", drive_file_id, content_hash;
  * embeddedfolderview exposes no stable modified-time → the refresh IS
    download + sha256 diff (files here are small analyst PDFs);
  * filenames map to tickers via gazetteer (exact token) + curated
    company-name aliases (multi-ticker files allowed);
  * unmatched files are REPORTED, never guessed onto a ticker;
  * ai_input_allowed defaults to false (compliance gate preserved);
    DRIVE_SYNC_AUTO_ALLOW=true opts into auto-approval;
  * hand-written manifest entries are never touched or deleted.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_LIST_TIMEOUT_S = 45.0
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Company-name aliases → ticker set (multi-ticker files are first-class).
# Keys are matched as case-insensitive WORDS/SUBSTRINGS of the filename.
# HK listings use the CANONICAL 5-digit zero-padded form the pipeline and
# TICKER_SECTOR_LOOKUP use (00700.HK, 03690.HK, 09988.HK, …).
_COMPANY_ALIASES: dict[str, list[str]] = {
    "alibaba":        ["BABA", "09988.HK"],
    "tencent":        ["00700.HK"],
    "meituan":        ["03690.HK"],
    "jd.com":         ["JD", "09618.HK"],
    "pdd holdings":   ["PDD"],
    "pinduoduo":      ["PDD"],
    "amazon":         ["AMZN"],
    "meta platforms": ["META"],
    "microsoft":      ["MSFT"],
    "alphabet":       ["GOOGL"],
    "google":         ["GOOGL"],
    "apple":          ["AAPL"],
    "nvidia":         ["NVDA"],
    "tesla":          ["TSLA"],
    "crowdstrike":    ["CRWD"],
    "palantir":       ["PLTR"],
    "zoom":           ["ZM"],
    "netflix":        ["NFLX"],
    "adobe":          ["ADBE"],
    "salesforce":     ["CRM"],
    "oracle":         ["ORCL"],
    "jpmorgan":       ["JPM"],
    "goldman":        ["GS"],
    "morgan stanley": ["MS"],
    "bank of america": ["BAC"],
    "wells fargo":    ["WFC"],
    "citigroup":      ["C"],
    "hsbc":           ["00005.HK"],
    "aia":            ["01299.HK"],
    "ping an":        ["02318.HK"],
    "china life":     ["02628.HK"],
    "ccb":            ["00939.HK"],
    "icbc":           ["01398.HK"],
    "byd":            ["01211.HK"],
    "nio":            ["NIO"],
    "li auto":        ["LI"],
    "xpeng":          ["XPEV"],
    "trip.com":       ["TCOM"],
    "baidu":          ["BIDU"],
    "netease":        ["NTES"],
    "xiaomi":         ["01810.HK"],
    "lenovo":         ["00992.HK"],
    "smic":           ["00981.HK"],
}

# Exact-token noise words that must never match as tickers.
_STOP_TOKENS = {"INC", "CORP", "LTD", "GROUP", "HOLDINGS", "CO", "PLC",
                "REVIEW", "PDF", "THE", "AND", "FOR", "Q1", "Q2", "Q3",
                "Q4", "FY", "US", "HK", "BUY", "SELL", "HOLD"}


def folder_id_from_ref(ref: str) -> Optional[str]:
    """Folder id from a full Drive folder URL or a bare id."""
    ref = (ref or "").strip()
    if not ref:
        return None
    m = re.search(r"/folders/([A-Za-z0-9_\-]{20,})", ref)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_\-]{20,})", ref)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", ref):
        return ref
    return None


# ── Listing (Tier A anonymous / Tier B OAuth) ───────────────────────────────

def _list_tier_a(folder_id: str) -> list[dict]:
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}"
    resp = requests.get(url, timeout=_LIST_TIMEOUT_S, headers={"User-Agent": _UA})
    if resp.status_code != 200:
        raise RuntimeError(f"embeddedfolderview HTTP {resp.status_code} — "
                           f"is the folder shared 'Anyone with the link'?")
    page = resp.text
    if "flip-entry" not in page:
        # An empty folder returns the frame with zero entries — distinguish
        # from a sharing wall (which returns a sign-in redirect page).
        if "ServiceLogin" in page or "accounts.google.com" in page:
            raise RuntimeError("folder listing hit a Google sign-in wall — "
                               "sharing is not 'Anyone with the link'")
        return []
    entries: list[dict] = []
    for block in re.split(r'(?=<div class="flip-entry" )', page)[1:]:
        id_m = re.search(r'id="entry-([A-Za-z0-9_\-]{20,})"', block)
        title_m = re.search(r'flip-entry-title[^>]*>(.*?)</div>', block,
                            re.DOTALL)
        if not id_m:
            continue
        name = _html.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() \
            if title_m else ""
        entries.append({"file_id": id_m.group(1), "name": name})
    return entries


def _oauth_access_token() -> Optional[str]:
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    rtok = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        return None
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={"client_id": cid, "client_secret": csec,
                  "refresh_token": rtok, "grant_type": "refresh_token"},
            timeout=30)
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as exc:
        logger.warning("Drive OAuth token refresh failed: %s", exc)
    return None


def _list_tier_b(folder_id: str) -> list[dict]:
    token = _oauth_access_token()
    if not token:
        raise RuntimeError("Tier B listing needs GOOGLE_OAUTH_CLIENT_ID/"
                           "GOOGLE_OAUTH_CLIENT_SECRET/GOOGLE_OAUTH_REFRESH_TOKEN")
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params={"q": f"'{folder_id}' in parents and mimeType != "
                      "'application/vnd.google-apps.folder' and trashed = false",
                "fields": "files(id,name,size,modifiedTime)",
                "pageSize": 500},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Drive API HTTP {resp.status_code}")
    return [{"file_id": f["id"], "name": f.get("name") or ""}
            for f in resp.json().get("files", [])]


def list_drive_folder(folder_id: str) -> list[dict]:
    """Tier A first; falls back to Tier B when credentials exist."""
    try:
        return _list_tier_a(folder_id)
    except Exception as exc:
        logger.info("Tier A listing failed (%s) — trying Tier B", exc)
        return _list_tier_b(folder_id)


# ── Filename → ticker matching ──────────────────────────────────────────────

def build_gazetteer() -> set[str]:
    """Known-ticker set: US/HK sector lookup ∪ SGX lookup.

    SGX was missing entirely, and the effect was total rather than partial:
    the gazetteer held 444 tickers and none ended in `.SI`, so every
    Singapore document in the archive matched nothing. On the live folder
    that was 11 of 20 files — DBS, OCBC, Keppel, ST Engineering, Sembcorp,
    four REITs, PropNex, Sheng Siong. Each was downloaded, attributed to no
    ticker, and therefore never written to the research manifest, so the
    SOTP extractor and the analyst-basis benchmark never saw them.
    """
    tickers: set[str] = set()
    try:
        from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
        tickers.update(TICKER_SECTOR_LOOKUP.keys())
    except Exception as exc:
        logger.warning("TICKER_SECTOR_LOOKUP unavailable: %s", exc)
    try:
        from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP
        tickers.update(SGX_TICKER_SECTOR_LOOKUP.keys())
    except Exception as exc:
        logger.warning("SGX_TICKER_SECTOR_LOOKUP unavailable: %s", exc)
    return tickers


# Words that carry no identifying signal once the company name is reduced
# to an alias. "trust" is deliberately NOT here: dropping it would collapse
# "Capitaland China Trust" and "Capitaland India Trust" onto the same stem.
_ALIAS_NOISE = {
    "ltd", "limited", "plc", "inc", "corp", "corporation", "company",
    "holdings", "holding", "group", "the", "and", "&", "co", "pte",
    "bhd", "berhad", "sa", "nv", "ag",
}


def _clean_company_name(raw: str) -> str:
    """Company name → alias stem, or '' when nothing usable survives."""
    name = (raw or "").split("—")[0].split(" - ")[0]
    name = re.sub(r"\(.*?\)", " ", name)          # drop parentheticals
    name = re.sub(r"[^A-Za-z0-9 ]+", " ", name).lower()
    words = [w for w in name.split() if w and w not in _ALIAS_NOISE]
    return " ".join(words).strip()


def build_derived_aliases() -> dict[str, list[str]]:
    """Company-name aliases derived from the ticker lookups.

    Hand-maintaining this list does not scale past the dozen US names it
    started with, and the archive is filled by filename ("Keppel_Aug
    2025.pdf"), not by ticker. Derivation keeps it in step with the
    lookups for free.
    """
    # SGX ONLY. Field [3] is a company name in SGX_TICKER_SECTOR_LOOKUP
    # ("DBS Group — SG money-center bank") but a free-text note in
    # TICKER_SECTOR_LOOKUP — AAPL's reads "Hardware + services + AI capex;
    # Tech WACC applies", contributing no alias, while APLE's reads "Apple
    # Hospitality REIT — hotels" and would claim the bare stem "apple".
    # Deriving across both tables sent an Apple report to Apple Hospitality
    # REIT. US and HK names stay on the curated _COMPANY_ALIASES list.
    aliases: dict[str, list[str]] = {}
    _heads: dict[str, set[str]] = {}
    for mod_attr in ("SGX_TICKER_SECTOR_LOOKUP",):
        try:
            from src.data import sector_profiles as _sp
            table = getattr(_sp, mod_attr, {}) or {}
        except Exception:
            continue
        for ticker, entry in table.items():
            if not entry or len(entry) < 4:
                continue
            stem = _clean_company_name(entry[3])
            if len(stem) < 3:                      # too short to be safe
                continue
            aliases.setdefault(stem, [])
            if ticker not in aliases[stem]:
                aliases[stem].append(ticker)
            # Archive filenames abbreviate: "OCBC_20260511.pdf" never
            # contains the full "ocbc bank". Register the leading word too,
            # but ONLY where it is unambiguous — "capitaland" alone maps to
            # three different trusts, so it must not resolve to any of them.
            head = stem.split()[0]
            if len(head) >= 3 and head != stem:
                _heads.setdefault(head, set()).add(ticker)
            # Tolerate a plural/singular slip in the leading word:
            # the archive holds "Fraser Centrepoint Trust" for
            # "Frasers Centrepoint Trust".
            if head.endswith("s") and len(head) > 3:
                alt = " ".join([head[:-1]] + stem.split()[1:])
                aliases.setdefault(alt, [])
                if ticker not in aliases[alt]:
                    aliases[alt].append(ticker)

    for head, owners in _heads.items():
        if len(owners) == 1 and head not in aliases:
            aliases[head] = sorted(owners)
    return aliases


def match_tickers(name: str, gazetteer: set[str] | None = None) -> list[str]:
    """Map a filename to tickers: exact uppercase tokens first (the GS
    'Company (TICKER)' convention), then company-name aliases (handles
    multi-ticker files like 'Alibaba_Meituan_JD'). Never guesses."""
    gazetteer = gazetteer if gazetteer is not None else build_gazetteer()
    stem = re.sub(r"\.(pdf|docx?|txt)$", "", name or "", flags=re.IGNORECASE)
    found: list[str] = []

    def _add(t: str):
        if t and t not in found:
            found.append(t)

    # 1) parenthesized / standalone exact ticker tokens
    tokens = re.split(r"[^A-Za-z0-9.\-]+", stem)
    for tok in tokens:
        up = tok.upper().strip("._-")
        # Single-char tokens are possessive/connector noise ('Microsoft's'
        # split into 'Microsoft' + 's' and matched SentinelOne live on
        # 2026-08-24).  Genuine single-char tickers still match through
        # the company-name alias path below.
        if not up or len(up) < 2 or up in _STOP_TOKENS:
            continue
        if up in gazetteer:
            _add(up)
            continue
        # "00700.HK" style tokens split on '.' survive; bare digit forms
        # need >= 4 digits ("9988", "03690") so year/quarter fragments
        # (e.g. "Q2'26" -> "26") cannot zero-pad into HK codes, and
        # 4-digit YEAR tokens (1900-2100) are never treated as codes.
        if re.fullmatch(r"\d{4,5}", up):
            if len(up) == 4 and 1900 <= int(up) <= 2100:
                continue
            hk = f"{up.zfill(5)}.HK"
            if hk in gazetteer:
                _add(hk)

    # 2) company-name aliases (substring — 'Alibaba_Meituan_JD' → 3 names)
    #
    # LONGEST MATCH WINS, and a matched span is consumed. Without that,
    # "Keppel DC Reit" matches both "keppel dc reit" (AJBU.SI, the REIT)
    # and "keppel" (BN4.SI, the parent) and the document is attributed to
    # both — a REIT note would end up as evidence for the conglomerate's
    # SOTP. The same collision sits across "Capitaland China Trust",
    # "Capitaland India Trust" and "Capitaland Investment".
    lowered = f" {stem.lower()} "
    combined: dict[str, list[str]] = dict(_COMPANY_ALIASES)
    try:
        for alias, tickers in build_derived_aliases().items():
            combined.setdefault(alias, [])
            for t in tickers:
                if t not in combined[alias]:
                    combined[alias].append(t)
    except Exception as exc:                       # derivation is best-effort
        logger.warning("derived aliases unavailable: %s", exc)

    consumed: list[tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(a < end and start < b for start, end in consumed)

    for alias in sorted(combined, key=len, reverse=True):
        idx = lowered.find(alias)
        if idx == -1 or _overlaps(idx, idx + len(alias)):
            continue
        consumed.append((idx, idx + len(alias)))
        for t in combined[alias]:
            _add(t)

    return found


# ── Download ─────────────────────────────────────────────────────────────────

def download_drive_file(file_id: str, dest_dir: str) -> Optional[str]:
    """Download one Drive file anonymously → local PDF path (sha-named)."""
    from src.utils.research_pdf import download_google_drive_file
    return download_google_drive_file(file_id, dest_dir)


# ── Sync engine ─────────────────────────────────────────────────────────────

def _already_extracted(tickers: list[str], content_hash: str) -> bool:
    """True when an analyst_reports row already exists for this document.

    Keyed on content_hash so a re-published document (new bytes, same
    ticker) still extracts. Any store error returns True — an unreadable
    store must not trigger an unbounded re-extraction loop on every run.
    """
    if not tickers:
        return True
    try:
        from src.memory import assumption_store
        for tk in tickers:
            rows = assumption_store.get_analyst_reports(tk, limit=25) or []
            if any((r or {}).get("content_hash") == content_hash for r in rows):
                return True
        return False
    except Exception as exc:
        logger.warning("extraction-state check failed (%s) — treating as done", exc)
        return True



def sync_drive_folder(repo_root: str | None = None,
                      folder_ref: str | None = None,
                      auto_allow: bool | None = None,
                      on_progress=None) -> dict:
    """One full sync pass. Returns:
        {folder_id, listed, matched: [{file_id, name, tickers}],
         unmatched: [{file_id, name}], downloaded, unchanged, extracted,
         gated, errors: [...]}
    """
    from src.memory.assumption_extract import extract_and_persist_analyst_pdf
    from src.utils import research_pdf

    root = repo_root or research_pdf._repo_root_default()
    ref = folder_ref or os.environ.get("DRIVE_SYNC_FOLDER", "")
    folder_id = folder_id_from_ref(ref)
    if auto_allow is None:
        auto_allow = os.environ.get(
            "DRIVE_SYNC_AUTO_ALLOW", "false").strip().lower() in ("1", "true", "yes")

    result: dict = {
        "folder_id": folder_id, "listed": 0, "matched": [], "unmatched": [],
        "downloaded": 0, "unchanged": 0, "extracted": 0, "gated": 0,
        "errors": [],
    }
    if not folder_id:
        result["errors"].append("DRIVE_SYNC_FOLDER unset/unparseable — sync disabled")
        return result

    def _progress(msg: str):
        logger.info("[drive_sync] %s", msg)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    entries = list_drive_folder(folder_id)
    result["listed"] = len(entries)
    _progress(f"listed {len(entries)} files in folder {folder_id}")

    gazetteer = build_gazetteer()
    raw_manifest = research_pdf._load_manifest_raw(root)
    drive_entries = [e for e in raw_manifest["documents"]
                     if e.get("drive_file_id")]
    known_by_fid: dict[str, list[dict]] = {}
    for e in drive_entries:
        known_by_fid.setdefault(e["drive_file_id"], []).append(e)
    # Only hashes whose bytes are VERIFIED ON DISK count as "already
    # have it" — the manifest alone can mask a lost file (the 2026-08-24
    # deletion bug left manifest rows pointing at vanished PDFs, and a
    # manifest-built set would have skipped the re-download forever).
    # Map hash -> kept paths so a re-download is only dropped when it is
    # NOT itself one of the kept copies (deterministic <sha12>_<fid8>.pdf
    # names make dest collide with the kept path).
    known_paths_by_hash: dict[str, set[str]] = {}
    for e in raw_manifest["documents"]:
        _ch, _p = e.get("content_hash"), e.get("path")
        if _ch and _p and os.path.exists(_p):
            known_paths_by_hash.setdefault(_ch, set()).add(
                os.path.abspath(_p))

    pdf_dir = os.path.join(root, research_pdf.PDF_DIR_REL_PATH)
    for entry in entries:
        fid, name = entry["file_id"], entry["name"]
        tickers = match_tickers(name, gazetteer)
        if not tickers:
            result["unmatched"].append({"file_id": fid, "name": name})
            continue
        result["matched"].append({"file_id": fid, "name": name,
                                  "tickers": tickers})

        prior = known_by_fid.get(fid) or []
        local_path = prior[0].get("path") if prior else None
        prior_hash = prior[0].get("content_hash") if prior else None
        # Existence checked BEFORE the download: deterministic download
        # names (<sha12>_<fid8>.pdf) overwrite the kept path, so checking
        # after would make a LOST file look present (the download just
        # recreated it) and mask the restore as "unchanged".
        prior_exists = bool(local_path and os.path.exists(local_path))

        # Download when unseen; when seen, re-download + hash-diff IS the
        # time-refresh (embeddedfolderview exposes no stable modifiedTime).
        dest = download_drive_file(fid, pdf_dir)
        if not dest:
            result["errors"].append(f"download failed: {name[:60]} ({fid})")
            continue
        content_hash = research_pdf.file_content_hash(dest)
        kept_paths = known_paths_by_hash.get(content_hash) or set()
        if prior_hash == content_hash and prior_exists:
            # Identical content already on disk — drop the re-download,
            # UNLESS it overwrote the kept copy: download names are
            # deterministic (<sha12>_<fid8>.pdf), so dest may equal
            # the kept path — deleting it then wipes the ONLY copy
            # (live 2026-08-24 bug: Gate-4 re-trigger emptied pdfs/).
            #
            # An unchanged file still needs extraction when the PREVIOUS
            # attempt never produced an analyst_reports row. Extraction can
            # fail independently of the download — a missing API key, a
            # rate limit, an unparseable page — and the manifest records
            # the file as synced regardless. Skipping unconditionally meant
            # one transient failure stranded a document permanently:
            # registered, on disk, and never extracted. Seen live when the
            # first SGX pass ran without DEEP_RESEARCH_API_KEY and every
            # later run reported unchanged=20, extracted=0.
            if _already_extracted(tickers, content_hash):
                if os.path.abspath(dest) not in kept_paths:
                    os.remove(dest)
                result["unchanged"] += 1
                continue
            _progress(f"re-extracting {name} — registered but never extracted")
            dest = local_path or dest
        if kept_paths:
            # Same bytes verified on disk under another file id/path
            if os.path.abspath(dest) not in kept_paths:
                os.remove(dest)
            result["unchanged"] += 1
            # Still (re)link the drive file id to the existing doc below
            dest = local_path or dest
        else:
            # New bytes, or a registered file lost from disk — the
            # download becomes (or restores) the kept copy
            result["downloaded"] += 1

        allowed = auto_allow
        written = research_pdf.register_documents(
            root, dest, tickers, ai_input_allowed=allowed,
            source="drive", drive_file_id=fid,
            source_url=f"https://drive.google.com/file/d/{fid}",
            content_hash=content_hash)
        known_paths_by_hash.setdefault(content_hash, set()).add(
            os.path.abspath(dest))
        _progress(f"registered {name[:50]} → {', '.join(tickers)} "
                  f"({'allowed' if allowed else 'gate: ai_input_allowed=false'})")

        if not allowed:
            result["gated"] += 1
            continue
        # Extract per unique document, not per ticker-entry
        try:
            summary = extract_and_persist_analyst_pdf(
                dest, tickers, ai_input_allowed=True,
                drive_file_id=fid,
                source_url=f"https://drive.google.com/file/d/{fid}")
            done = [t for t, s in summary.get("tickers", {}).items()
                    if str(s).startswith("extracted")]
            result["extracted"] += len(done)
            _progress(f"extracted {len(done)}/{len(tickers)} tickers "
                      f"for {name[:40]}")
        except Exception as exc:
            result["errors"].append(f"extraction failed {name[:40]}: {exc}")
        # Be polite to Qwen rate limits between documents
        time.sleep(1.0)

    _progress(f"done: listed={result['listed']} matched={len(result['matched'])} "
              f"unmatched={len(result['unmatched'])} downloaded="
              f"{result['downloaded']} unchanged={result['unchanged']} "
              f"extracted={result['extracted']} gated={result['gated']}")
    return result


def run_scheduled_sync() -> dict:
    """arq cron entry point — no-op when DRIVE_SYNC_FOLDER is unset."""
    if not os.environ.get("DRIVE_SYNC_FOLDER"):
        logger.info("[drive_sync] DRIVE_SYNC_FOLDER unset — skipping")
        return {"skipped": True}
    return sync_drive_folder()

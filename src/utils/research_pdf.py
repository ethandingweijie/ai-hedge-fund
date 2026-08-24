from __future__ import annotations

"""Research PDF ingestion (SOTP extractor evidence source)

Extracts per-page text from sell-side / company research PDFs with PyMuPDF.
Pages that are effectively raster tables (very little extractable text but
carry images — e.g. GS SOTP exhibits) are additionally rendered to PNG so a
vision model can read the table.

PDF ingestion is ONE corroborating evidence source for the SOTP extractor —
the extractor also runs targeted research passes over public sources.
Ingestion is gated by a per-document manifest (research/manifest.json) with
an explicit ``ai_input_allowed`` flag: sell-side research licenses differ on
whether their content may be fed to an AI system, so compliance is a
deliberate per-document decision.
"""

import base64
import hashlib
import json
import os
import re
from typing import Optional

import fitz  # pymupdf

# Pages with fewer extractable chars than this (and ≥1 image) are treated as
# raster tables and rendered to PNG for the vision path.
_RASTER_TABLE_TEXT_THRESHOLD = 300
_MAX_IMAGE_PAGES = 6
_RENDER_DPI = 150

MANIFEST_REL_PATH = os.path.join("research", "manifest.json")


def extract_research_pdf(path: str, dpi: int = _RENDER_DPI,
                         max_image_pages: int = _MAX_IMAGE_PAGES) -> dict:
    """Extract text + raster-table page images from a research PDF.

    Returns:
        {
          "path": str,
          "pages": [{"page": int, "text": str}, ...],
          "text": str,                      # concatenated page text
          "table_images": [{"page": int, "mime": "image/png",
                            "data_b64": str}, ...],
        }
    Raises on unreadable file — caller decides how loudly to fail.
    """
    doc = fitz.open(path)
    pages: list[dict] = []
    table_images: list[dict] = []
    text_parts: list[str] = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text()
            pages.append({"page": i + 1, "text": text})
            text_parts.append(text)
            if (len(text.strip()) < _RASTER_TABLE_TEXT_THRESHOLD
                    and len(page.get_images()) >= 1
                    and len(table_images) < max_image_pages):
                pix = page.get_pixmap(dpi=dpi)
                table_images.append({
                    "page": i + 1,
                    "mime": "image/png",
                    "data_b64": base64.b64encode(pix.tobytes("png")).decode("ascii"),
                })
    finally:
        doc.close()
    return {
        "path": path,
        "pages": pages,
        "text": "\n\n".join(text_parts),
        "table_images": table_images,
    }


def load_research_manifest(repo_root: str) -> list[dict]:
    """Read research/manifest.json → list of document entries.

    Entry shape: {"path": str, "ticker": str, "ai_input_allowed": bool}.
    Missing manifest → [] (no PDF evidence; extractor falls back to public
    sources). Relative paths resolve against ``repo_root``.
    """
    manifest_path = os.path.join(repo_root, MANIFEST_REL_PATH)
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    docs = raw.get("documents", []) if isinstance(raw, dict) else []
    out: list[dict] = []
    for entry in docs:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path") or ""
        if not rel:
            continue
        abs_path = rel if os.path.isabs(rel) else os.path.join(repo_root, rel)
        out.append({
            "path": abs_path,
            "ticker": str(entry.get("ticker") or ""),
            "ai_input_allowed": bool(entry.get("ai_input_allowed", False)),
            # R1.e sync metadata (absent on hand-written entries)
            "source": entry.get("source"),
            "drive_file_id": entry.get("drive_file_id"),
            "source_url": entry.get("source_url"),
            "content_hash": entry.get("content_hash"),
        })
    return out


def load_pdf_evidence_for_ticker(repo_root: str, ticker: str) -> Optional[dict]:
    """Manifest-filtered PDF evidence for one ticker (allowed docs only)."""
    docs = [d for d in load_research_manifest(repo_root)
            if d["ticker"].upper() == ticker.upper() and d["ai_input_allowed"]]
    if not docs:
        return None
    merged_text: list[str] = []
    images: list[dict] = []
    for doc_entry in docs[:2]:  # cap: two documents of evidence per ticker
        if not os.path.exists(doc_entry["path"]):
            continue
        try:
            extracted = extract_research_pdf(doc_entry["path"])
        except Exception as exc:  # unreadable PDF ≠ fatal run
            print(f"  [sotp_extractor] PDF ingest failed for "
                  f"{doc_entry['path']}: {type(exc).__name__}: {exc}")
            continue
        merged_text.append(f"=== {os.path.basename(doc_entry['path'])} ===\n"
                           + extracted["text"])
        images.extend(extracted["table_images"])
    if not merged_text:
        return None
    return {"text": "\n\n".join(merged_text), "table_images": images[:_MAX_IMAGE_PAGES]}


# ── R1: deposit tooling (Workstream R1.a channel 3 / R1.e) ──────────────────

PDF_DIR_REL_PATH = os.path.join("research", "pdfs")


def file_content_hash(path: str) -> str:
    """sha256 of the file bytes (dedupe key for deposits + Drive sync)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_root_default() -> str:
    """Repo root = three levels up from this file (src/utils/research_pdf.py)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_manifest_raw(repo_root: str) -> dict:
    manifest_path = os.path.join(repo_root, MANIFEST_REL_PATH)
    if not os.path.exists(manifest_path):
        return {"_comment": "Research PDF deposit registry — see src/utils/research_pdf.py. "
                            "ai_input_allowed is the per-document sell-side-license gate.",
                "documents": []}
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"documents": []}
    if not isinstance(raw, dict):
        return {"documents": []}
    raw.setdefault("documents", [])
    return raw


def _save_manifest_raw(repo_root: str, raw: dict) -> None:
    manifest_path = os.path.join(repo_root, MANIFEST_REL_PATH)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, manifest_path)


def register_documents(repo_root: str, path: str, tickers: list[str], *,
                       ai_input_allowed: bool = False,
                       source: str | None = None,
                       drive_file_id: str | None = None,
                       source_url: str | None = None,
                       content_hash: str | None = None) -> list[dict]:
    """Upsert one manifest entry per ticker. Hand-written entries are NEVER
    deleted; drive-sourced entries key by drive_file_id (idempotent sync),
    manual entries by (path, ticker). Returns the entries written."""
    raw = _load_manifest_raw(repo_root)
    docs = raw["documents"]
    written: list[dict] = []
    for ticker in tickers:
        tkr = str(ticker).upper()
        entry: dict = {"path": path, "ticker": tkr,
                       "ai_input_allowed": bool(ai_input_allowed)}
        if source:
            entry["source"] = source
        if drive_file_id:
            entry["drive_file_id"] = drive_file_id
        if source_url:
            entry["source_url"] = source_url
        if content_hash:
            entry["content_hash"] = content_hash
        replaced = False
        for i, existing in enumerate(docs):
            if str(existing.get("ticker") or "").upper() != tkr:
                continue
            same_drive = (drive_file_id and
                          existing.get("drive_file_id") == drive_file_id)
            same_path = os.path.normpath(str(existing.get("path") or "")) == \
                os.path.normpath(str(path))
            if same_drive or (same_path and not drive_file_id):
                docs[i] = entry
                replaced = True
                break
        if not replaced:
            docs.append(entry)
        written.append(entry)
    _save_manifest_raw(repo_root, raw)
    return written


def set_manifest_allowance(repo_root: str, *, path: str | None = None,
                           ticker: str | None = None,
                           drive_file_id: str | None = None,
                           allowed: bool = True) -> int:
    """Flip ai_input_allowed on matching entries. Returns entries changed."""
    raw = _load_manifest_raw(repo_root)
    changed = 0
    for entry in raw["documents"]:
        if path and os.path.normpath(str(entry.get("path") or "")) != \
                os.path.normpath(str(path)):
            continue
        if ticker and str(entry.get("ticker") or "").upper() != ticker.upper():
            continue
        if drive_file_id and entry.get("drive_file_id") != drive_file_id:
            continue
        if not (path or ticker or drive_file_id):
            continue
        if bool(entry.get("ai_input_allowed", False)) != allowed:
            entry["ai_input_allowed"] = allowed
            changed += 1
    if changed:
        _save_manifest_raw(repo_root, raw)
    return changed


def known_content_hashes(repo_root: str) -> set[str]:
    return {e["content_hash"] for e in load_research_manifest(repo_root)
            if e.get("content_hash")}


# ── Google Drive single-file download (no Drive API — plain HTTP) ───────────

_DRIVE_FILE_ID_RE = re.compile(r"/file/d/([A-Za-z0-9_\-]{20,})")


def parse_google_drive_file_id(ref: str) -> Optional[str]:
    """Extract a Drive file id from a share link, or accept a bare id."""
    ref = (ref or "").strip()
    if not ref:
        return None
    m = _DRIVE_FILE_ID_RE.search(ref)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", ref):
        return ref
    return None


def download_google_drive_file(ref: str, dest_dir: str,
                               timeout_s: float = 120.0) -> Optional[str]:
    """Download a Drive file (link or id) anonymously. Returns the local
    path, or None on failure. Large-file virus-scan interstitial handled
    via the confirm=t cookie retry."""
    import requests

    file_id = parse_google_drive_file_id(ref)
    if not file_id:
        print(f"  [research_pdf] not a Drive file link/id: {ref[:80]}")
        return None
    os.makedirs(dest_dir, exist_ok=True)
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        resp = requests.get(url, timeout=timeout_s, stream=True)
        if resp.status_code in (301, 302, 303, 307, 308):
            resp = requests.get(resp.headers["Location"], timeout=timeout_s)
        data = resp.content
        # Virus-scan interstitial for larger files → retry with confirm=t
        if not data.startswith(b"%PDF") and len(data) < 200_000:
            resp = requests.get(
                url, params={"confirm": "t"}, timeout=timeout_s,
                cookies={"download_warning": "t"})
            data = resp.content
        if not data.startswith(b"%PDF"):
            print(f"  [research_pdf] Drive download is not a PDF "
                  f"({len(data)} bytes, file_id={file_id})")
            return None
        # Sanity-hash into the name so repeat drops are detectable
        h = hashlib.sha256(data).hexdigest()[:12]
        dest = os.path.join(dest_dir, f"{h}_{file_id[:8]}.pdf")
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest
    except Exception as exc:
        print(f"  [research_pdf] Drive download failed ({file_id}): {exc}")
        return None


# ── CLI: deposit one document (R1.a channel 3) ──────────────────────────────

def _cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.utils.research_pdf",
        description="Research PDF deposit registry (R1)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="register a document (local path or "
                                       "single-file Drive share link)")
    p_add.add_argument("source", help="local PDF path or Drive file link/id")
    p_add.add_argument("--ticker", action="append", required=True,
                       help="ticker to map (repeatable, e.g. --ticker BABA "
                            "--ticker 9988.HK)")
    p_add.add_argument("--allow", action="store_true",
                       help="set ai_input_allowed=true (sell-side license "
                            "permits AI input) and extract now")
    p_add.add_argument("--repo-root", default=None)

    p_allow = sub.add_parser("allow", help="flip the compliance gate on "
                                           "existing entries")
    p_allow.add_argument("--ticker", default=None)
    p_allow.add_argument("--path", default=None)
    p_allow.add_argument("--drive-file-id", default=None)
    p_allow.add_argument("--revoke", action="store_true")
    p_allow.add_argument("--extract", action="store_true",
                         help="run extraction after allowing")
    p_allow.add_argument("--repo-root", default=None)

    sub.add_parser("list", help="show the manifest")

    args = parser.parse_args(argv)
    repo_root = args.repo_root or _repo_root_default()

    if args.cmd == "list":
        for e in load_research_manifest(repo_root):
            print(f"{e['ticker']:<10} allowed={e['ai_input_allowed']!s:<5} "
                  f"source={e.get('source') or 'manual':<6} {e['path']}")
        return 0

    if args.cmd == "allow":
        changed = set_manifest_allowance(
            repo_root, path=args.path, ticker=args.ticker,
            drive_file_id=args.drive_file_id, allowed=not args.revoke)
        print(f"{'allowed' if not args.revoke else 'revoked'}: "
              f"{changed} manifest entr{'y' if changed == 1 else 'ies'}")
        if args.extract and not args.revoke:
            from src.memory.assumption_extract import extract_and_persist_analyst_pdf
            for e in load_research_manifest(repo_root):
                if not e["ai_input_allowed"]:
                    continue
                if args.ticker and e["ticker"] != args.ticker.upper():
                    continue
                if args.path and os.path.normpath(e["path"]) != \
                        os.path.normpath(args.path):
                    continue
                if not os.path.exists(e["path"]):
                    print(f"  missing file: {e['path']}")
                    continue
                res = extract_and_persist_analyst_pdf(
                    e["path"], [e["ticker"]], ai_input_allowed=True,
                    drive_file_id=e.get("drive_file_id"),
                    source_url=e.get("source_url"))
                print(f"  {e['ticker']}: {res}")
        return 0

    # add
    src_path = args.source
    drive_file_id = None
    source_url = None
    pdf_dir = os.path.join(repo_root, PDF_DIR_REL_PATH)
    if os.path.exists(src_path):
        local = os.path.abspath(src_path)
    else:
        drive_file_id = parse_google_drive_file_id(src_path)
        if not drive_file_id:
            print(f"source not found and not a Drive link: {src_path}")
            return 1
        source_url = src_path if src_path.startswith("http") else \
            f"https://drive.google.com/file/d/{drive_file_id}"
        local = download_google_drive_file(src_path, pdf_dir)
        if not local:
            return 1
        print(f"downloaded → {local}")

    h = file_content_hash(local)
    if h in known_content_hashes(repo_root):
        print(f"already registered (content sha256 {h[:12]}…) — no-op")
        return 0
    written = register_documents(
        repo_root, local, args.ticker,
        ai_input_allowed=bool(args.allow),
        source="drive" if drive_file_id else "manual",
        drive_file_id=drive_file_id, source_url=source_url, content_hash=h)
    print(f"registered {len(written)} entr"
          f"{'y' if len(written) == 1 else 'ies'} "
          f"({', '.join(sorted({w['ticker'] for w in written}))})")
    if args.allow:
        from src.memory.assumption_extract import extract_and_persist_analyst_pdf
        res = extract_and_persist_analyst_pdf(
            local, args.ticker, ai_input_allowed=True,
            drive_file_id=drive_file_id, source_url=source_url)
        print(f"extraction: {res}")
    else:
        print("ai_input_allowed=false — extraction deferred until allowed "
              "(python -m src.utils.research_pdf allow --ticker <T> --extract)")
    return 0


def main() -> None:
    import sys
    raise SystemExit(_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()

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
import json
import os
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

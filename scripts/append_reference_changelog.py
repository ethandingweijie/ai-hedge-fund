"""Append one change-log entry to the reference document, in its own styles.

`scripts/update_reference_docx.py` is a one-off for v1.8/v1.9 with a hardcoded
path. This takes any entry, written as a small markdown file, and appends it to
the end of the change log (entries are chronological, newest last):

    ## 2026-09-21 — v2.20.0: Title          -> Heading 2
    ### Executive Summary                   -> Heading 3
    - a bullet                              -> List Paragraph
    anything else                           -> body paragraph

    python scripts/append_reference_changelog.py docs/changelog/v2.20.0.md            # dry run
    python scripts/append_reference_changelog.py docs/changelog/v2.20.0.md --commit
    python scripts/append_reference_changelog.py entry.md --path other.docx --commit

Dry run by default. On --commit the document is first copied to
`.AI_Hedge_Fund_Reference.pre-<version>-<timestamp>.docx` beside it (the
convention already used in this folder), and an entry whose heading is already
present is refused rather than appended twice.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCX = ROOT / "AI_Hedge_Fund_Reference.backup-20260725.docx"

STYLES = {"h2": "Heading 2", "h3": "Heading 3", "li": "List Paragraph", "p": "Normal"}


def parse(md: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if line.startswith("### "):
            out.append(("h3", line[4:].strip()))
        elif line.startswith("## "):
            out.append(("h2", line[3:].strip()))
        elif line.startswith("- "):
            out.append(("li", line[2:].strip()))
        else:
            out.append(("p", line))
    # The document is plain text: markdown emphasis and code ticks do not belong in it.
    return [(k, re.sub(r"[*`]", "", t)) for k, t in out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("entry", type=Path)
    ap.add_argument("--path", type=Path, default=DEFAULT_DOCX)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    from docx import Document

    blocks = parse(a.entry.read_text(encoding="utf8"))
    heads = [t for k, t in blocks if k == "h2"]
    if len(heads) != 1 or blocks[0][0] != "h2":
        print("an entry is exactly one '## ' heading, first", file=sys.stderr)
        return 2
    doc = Document(str(a.path))
    existing = {p.text.strip() for p in doc.paragraphs if p.style.name == STYLES["h2"]}
    if heads[0] in existing:
        print(f"already present, not appended twice: {heads[0]}", file=sys.stderr)
        return 1
    have = {s.name for s in doc.styles}
    missing = [s for s in STYLES.values() if s not in have]
    if missing:
        print(f"document has no style(s) {missing}", file=sys.stderr)
        return 2
    last = [p.text.strip() for p in doc.paragraphs if p.style.name == STYLES["h2"]][-1]
    print(f"document : {a.path.name} ({len(doc.paragraphs)} paragraphs)")
    print(f"last entry: {last[:100]}")
    print(f"appending: {heads[0]}")
    print("           " + ", ".join(f"{sum(1 for k, _ in blocks if k == kind)} {name}"
                                     for kind, name in STYLES.items()))
    if not a.commit:
        print("dry run -- nothing written; pass --commit")
        return 0
    version = (re.search(r"v(\d+(?:\.\d+)+)", heads[0]) or [None, "entry"])[1].replace(".", "")
    backup = a.path.with_name(f".AI_Hedge_Fund_Reference.pre-{version}-{datetime.now():%Y%m%d-%H%M}.docx")
    shutil.copy2(a.path, backup)
    for kind, text in blocks:
        doc.add_paragraph(text, style=STYLES[kind])
    doc.save(str(a.path))
    print(f"backup   : {backup.name}\nwritten  : {a.path.name} ({len(doc.paragraphs)} paragraphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

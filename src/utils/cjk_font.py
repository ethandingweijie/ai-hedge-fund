"""CJK text in the ReportLab PDF.

Helvetica (a PDF base-14 font) has no Chinese glyphs: "贵州茅台" renders as black boxes. ReportLab has no per-glyph
font fallback, so runs of CJK characters are wrapped in <font name="..."> inside Paragraph markup.

Font resolution, first hit wins:
  1. CJK_FONT_PATH (env) -- a TrueType .ttf/.ttc, EMBEDDED (subset) in the PDF.
  2. A TrueType CJK font already on the host, embedded the same way:
     Linux: fonts-wqy-microhei / fonts-wqy-zenhei; Windows: Microsoft YaHei, SimHei, SimSun.
     (Noto Sans CJK .ttc files are CFF-outline OpenType, which ReportLab cannot embed, so they are not listed.)
  3. ReportLab's built-in Adobe CID font STSong-Light -- ships with ReportLab, needs no file, NOT embedded: Acrobat
     and pdf.js render it with their own Simplified Chinese font.

The production image has no CJK font today, so it takes (3) until `fonts-wqy-microhei` is added to the Dockerfile
(owner decision, docs/markets/CHINA.md). Never raises: on any failure the text is returned unwrapped.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

FONT_NAME = "CJKBody"
_CID_FALLBACK = "STSong-Light"

_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)

#: CJK Unified Ideographs (+ Ext A), CJK symbols & punctuation, full-width forms, Hiragana/Katakana, Hangul.
_CJK_RUN = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]+")

_LOCK = threading.Lock()
_STATE: dict = {"name": None, "source": None}


def has_cjk(text) -> bool:
    return bool(text) and bool(_CJK_RUN.search(str(text)))


def font_name() -> Optional[str]:
    """Register the CJK font once; its ReportLab name, or None when nothing could be registered."""
    with _LOCK:
        if _STATE["name"] is not None:
            return _STATE["name"] or None
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except Exception:                                  # noqa: BLE001
            _STATE["name"] = ""
            return None
        paths = [os.environ.get("CJK_FONT_PATH") or ""] + list(_CANDIDATES)
        for p in paths:
            if not p or not os.path.isfile(p):
                continue
            try:
                kw = {"subfontIndex": 0} if p.lower().endswith(".ttc") else {}
                pdfmetrics.registerFont(TTFont(FONT_NAME, p, **kw))
                _STATE.update(name=FONT_NAME, source=p)
                return FONT_NAME
            except Exception:                              # noqa: BLE001
                continue
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont(_CID_FALLBACK))
            _STATE.update(name=_CID_FALLBACK, source="reportlab CID (not embedded)")
            return _CID_FALLBACK
        except Exception:                                  # noqa: BLE001
            _STATE["name"] = ""
            return None


def font_source() -> Optional[str]:
    font_name()
    return _STATE.get("source")


def wrap_cjk(markup) -> str:
    """Paragraph markup with every CJK run in <font name=CJK>. Text without CJK is returned unchanged."""
    s = "" if markup is None else str(markup)
    if not has_cjk(s):
        return s
    name = font_name()
    if not name:
        return s
    return _CJK_RUN.sub(lambda m: f'<font name="{name}">{m.group(0)}</font>', s)


def cjk_paragraph(base_cls):
    """A Paragraph subclass that wraps CJK runs before parsing. Plain-string table cells are not Paragraphs and
    are left alone (no markup can leak into them)."""

    class CJKParagraph(base_cls):                          # type: ignore[misc, valid-type]
        def __init__(self, text, *args, **kwargs):
            if isinstance(text, str) and has_cjk(text):
                text = wrap_cjk(text)
            super().__init__(text, *args, **kwargs)

    CJKParagraph.__name__ = "Paragraph"
    return CJKParagraph

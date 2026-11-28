"""CJK text in ReportLab PDFs -- one helper for Korean (Hangul), Japanese (kana) and Chinese (Han) text.

ReportLab's standard fonts (Helvetica & co.) carry WinAnsi glyphs only, so a Korean company name, a Japanese
filing title or a Chinese quote renders as blank boxes. This module finds a font that can draw each script and
wraps every CJK run of a Paragraph's text in ``<font name="...">`` so the Latin text keeps its house font.

Font choice, per script, first hit wins:

1. ``CJK_FONT_PATH_<KR|JP|CN>`` -- an owner-supplied TrueType file (``.ttf``, or ``.ttc`` with ``#<index>``).
2. A TrueType font already on the host: Nanum Gothic / IPA Gothic / WenQuanYi Zen Hei on Debian (packages
   ``fonts-nanum``, ``fonts-ipafont-gothic``, ``fonts-wqy-zenhei``), Malgun Gothic / MS Gothic / SimSun on Windows.
   These are EMBEDDED (subset) in the PDF, so any viewer draws them.
3. ReportLab's built-in Adobe CID fonts (HYGothic-Medium, HeiseiKakuGo-W5, STSong-Light). Always available, but NOT
   embedded: Chrome / Edge / macOS Preview substitute a system font; Acrobat may ask for its Asian font pack.

The production image (python:3.11-slim) ships no CJK fonts, so production uses tier 3 until a font package or file
is added (owner decision, docs/markets/KOREA.md). Note: OpenType/CFF fonts (Noto Sans CJK .otf/.ttc) cannot be
read by ReportLab's TTFont; use a glyf-outline TrueType font.

Shared by the Korea, Japan and China market work -- keep it market-neutral.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

__all__ = ["script_of", "has_cjk", "font_for_script", "font_for_text", "cjk_markup"]

#: Hangul syllables / jamo / compatibility jamo
_HANGUL = r"\u1100-\u11ff\u3130-\u318f\uac00-\ud7af"
#: Hiragana, Katakana (+ phonetic extensions, half-width katakana)
_KANA = r"\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f"
#: CJK unified ideographs (+ ext A, compatibility), CJK symbols & punctuation, full-width forms
_HAN = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60\uffe0-\uffee"
_ANY = re.compile(f"[{_HANGUL}{_KANA}{_HAN}]")
#: A run: CJK characters plus the spaces / digits between them, so "삼성전자 2025 사업보고서" is one font switch.
_RUN = re.compile(f"[{_HANGUL}{_KANA}{_HAN}](?:[{_HANGUL}{_KANA}{_HAN}0-9 .,()%·\\-]*[{_HANGUL}{_KANA}{_HAN}])?")
_RE_HANGUL, _RE_KANA = re.compile(f"[{_HANGUL}]"), re.compile(f"[{_KANA}]")

_ENV = {"hangul": "CJK_FONT_PATH_KR", "kana": "CJK_FONT_PATH_JP", "han": "CJK_FONT_PATH_CN"}
_TTF_CANDIDATES = {
    "hangul": ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/nanum/NanumGothic.ttf",
               "C:/Windows/Fonts/malgun.ttf", "/Library/Fonts/AppleGothic.ttf"],
    "kana": ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
             "C:/Windows/Fonts/msgothic.ttc#0"],
    "han": ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc#0", "C:/Windows/Fonts/simsun.ttc#0"],
}
_CID = {"hangul": "HYGothic-Medium", "kana": "HeiseiKakuGo-W5", "han": "STSong-Light"}
_LOCK = threading.Lock()
_RESOLVED: dict[str, Optional[str]] = {}


def has_cjk(text: Optional[str]) -> bool:
    return bool(text) and _ANY.search(text) is not None


def script_of(text: str) -> Optional[str]:
    """'hangul' | 'kana' | 'han' | None. Kana outranks Han (Japanese mixes kanji), Hangul outranks both."""
    if not has_cjk(text):
        return None
    if _RE_HANGUL.search(text):
        return "hangul"
    if _RE_KANA.search(text):
        return "kana"
    return "han"


def _try_ttf(name: str, spec: str) -> bool:
    path, _, idx = spec.partition("#")
    if not os.path.isfile(path):
        return False
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        kw = {"subfontIndex": int(idx)} if idx else {}
        pdfmetrics.registerFont(TTFont(name, path, **kw))
        return True
    except Exception:                                      # noqa: BLE001  (CFF outlines, damaged file)
        return False


def font_for_script(script: str) -> Optional[str]:
    """A registered ReportLab font name that draws ``script``, or None (unknown script / ReportLab missing)."""
    if script not in _CID:
        return None
    with _LOCK:
        if script in _RESOLVED:
            return _RESOLVED[script]
        name = None
        specs = ([os.environ[_ENV[script]]] if os.environ.get(_ENV[script]) else []) + _TTF_CANDIDATES[script]
        for spec in specs:
            cand = f"CJK-{script}-TTF"
            if _try_ttf(cand, spec):
                name = cand
                break
        if name is None:
            try:
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.cidfonts import UnicodeCIDFont
                pdfmetrics.registerFont(UnicodeCIDFont(_CID[script]))
                name = _CID[script]
            except Exception:                              # noqa: BLE001
                name = None
        _RESOLVED[script] = name
        return name


def font_for_text(text: Optional[str], default: str = "Helvetica") -> str:
    """The font to draw a whole string with on a canvas (headers / footers): ``default`` unless it holds CJK."""
    s = script_of(text or "")
    return (font_for_script(s) if s else None) or default


def cjk_markup(text):
    """Paragraph markup with every CJK run wrapped in ``<font name=...>``; non-strings and CJK-free text unchanged.
    A run never contains '<' or '&', so existing markup and entities are untouched."""
    if not isinstance(text, str) or not has_cjk(text):
        return text

    def _wrap(m: "re.Match") -> str:
        run = m.group(0)
        f = font_for_script(script_of(run) or "")
        return f'<font name="{f}">{run}</font>' if f else run

    return _RUN.sub(_wrap, text)


# ── China-branch names (merge 2026-11-28): thin aliases over the shared per-script helper ──────────────────────

def font_name() -> Optional[str]:
    """The font that draws Chinese (Han) text -- the China branch's single-font entry point."""
    return font_for_script("han")


def font_source() -> Optional[str]:
    n = font_name()
    return None if n is None else ("ttf" if n.endswith("-TTF") else "cid")


def wrap_cjk(markup):
    return cjk_markup(markup)


def cjk_paragraph(base_cls):
    """A Paragraph subclass that wraps CJK runs before ReportLab parses the markup."""
    class _CJKParagraph(base_cls):
        def __init__(self, text, *args, **kwargs):
            super().__init__(cjk_markup(text), *args, **kwargs)
    _CJKParagraph.__name__ = base_cls.__name__
    return _CJKParagraph

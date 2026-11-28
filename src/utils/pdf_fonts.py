"""CJK text in ReportLab PDFs -- one helper shared by every market (Japan, China, Korea, HK).

ReportLab's built-in Helvetica has no CJK glyphs: "トヨタ自動車", "年報" or "삼성전자" render as black boxes or vanish.
`cjk_markup` wraps each run of CJK characters in a ``<font name=...>`` tag so a Paragraph draws that run in a font
that has the glyphs and leaves the Latin text in the report's own face.

Font resolution, per script (ja / zh / ko), first hit wins:
  1. ``PDF_CJK_FONT_<SCRIPT>`` or ``PDF_CJK_FONT`` -- path to a TrueType file (.ttf, or .ttc with
     ``PDF_CJK_FONT_INDEX``), registered and EMBEDDED (subset) in the PDF;
  2. a known system TrueType font (Windows MS Gothic / SimHei / Malgun, Linux IPA Gothic, Droid Sans Fallback,
     WenQuanYi Zen Hei, Nanum Gothic), also embedded;
  3. ReportLab's built-in CID font for the script (HeiseiKakuGo-W5 / STSong-Light / HYGothic-Medium). No file is
     needed, but the font is NOT embedded: the viewer supplies the glyphs (Acrobat, Chrome, pdf.js and macOS
     Preview all do). Install a TrueType CJK font in the image (e.g. Debian ``fonts-ipafont-gothic`` or
     ``fonts-droid-fallback``) to get an embedded font in production.

Script detection is by character class: any kana makes a run Japanese, any hangul Korean; a run of Han characters
alone takes ``hint`` (the report's market: "ja" for a Tokyo listing, "ko" for Seoul, else "zh").
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

_LOCK = threading.Lock()
_RESOLVED: dict[str, str] = {}

#: CJK symbols/punctuation, kana, Han (incl. ext. A, compatibility), hangul, half/full-width forms.
_CJK_RUN = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                      r"\uff00-\uffef\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]+")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")

_CID_FONTS = {"ja": "HeiseiKakuGo-W5", "zh": "STSong-Light", "ko": "HYGothic-Medium"}

#: (path, subfont index) TrueType candidates per script, in preference order.
_SYSTEM_TTF = {
    "ja": [("C:/Windows/Fonts/msgothic.ttc", 0),
           ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 0),
           ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", 0),
           ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0)],
    "zh": [("C:/Windows/Fonts/simhei.ttf", 0),
           ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
           ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0)],
    "ko": [("C:/Windows/Fonts/malgun.ttf", 0),
           ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
           ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0)],
}


def has_cjk(text) -> bool:
    return bool(text) and _CJK_RUN.search(str(text)) is not None


def script_of(run: str, hint: Optional[str] = None) -> str:
    if _KANA.search(run):
        return "ja"
    if _HANGUL.search(run):
        return "ko"
    return hint if hint in _CID_FONTS else "zh"


def _try_ttf(name: str, path: str, index: int) -> bool:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        if not os.path.isfile(path):
            return False
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
        return True
    except Exception:                                      # noqa: BLE001  (CFF outlines, corrupt file...)
        return False


def font_for(script: str) -> str:
    """The registered font name for a script ("ja" / "zh" / "ko"); registers it on first use. Never raises."""
    script = script if script in _CID_FONTS else "zh"
    with _LOCK:
        if script in _RESOLVED:
            return _RESOLVED[script]
        name = None
        env = os.environ.get(f"PDF_CJK_FONT_{script.upper()}") or os.environ.get("PDF_CJK_FONT")
        if env:
            try:
                idx = int(os.environ.get("PDF_CJK_FONT_INDEX", "0"))
            except ValueError:
                idx = 0
            if _try_ttf(f"CJK-{script}-env", env, idx):
                name = f"CJK-{script}-env"
        if name is None and os.environ.get("PDF_CJK_SYSTEM_FONTS", "on").lower() not in ("0", "off", "false", "no"):
            for i, (path, idx) in enumerate(_SYSTEM_TTF.get(script, [])):
                if _try_ttf(f"CJK-{script}-{i}", path, idx):
                    name = f"CJK-{script}-{i}"
                    break
        if name is None:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            name = _CID_FONTS[script]
            try:
                pdfmetrics.registerFont(UnicodeCIDFont(name))
            except Exception:                              # noqa: BLE001
                name = "Helvetica"
        _RESOLVED[script] = name
        return name


def is_embedded(font_name: str) -> bool:
    """False for a CID font (the viewer supplies the glyphs)."""
    return font_name not in _CID_FONTS.values() and font_name != "Helvetica"


def cjk_markup(text, hint: Optional[str] = None):
    """Paragraph markup with each CJK run wrapped in a CJK font tag. Non-strings and CJK-free text pass through.

    Safe on text that is already escaped / marked up: CJK characters never occur inside a tag name or an entity,
    so only text content is wrapped."""
    if not isinstance(text, str) or not has_cjk(text):
        return text
    return _CJK_RUN.sub(lambda m: f'<font name="{font_for(script_of(m.group(0), hint))}">{m.group(0)}</font>', text)


def canvas_font(text, default: str, hint: Optional[str] = None) -> str:
    """Font for a canvas.drawString of `text`: the CJK font when the string carries CJK, else `default`."""
    if not has_cjk(text):
        return default
    m = _CJK_RUN.search(str(text))
    return font_for(script_of(m.group(0), hint))

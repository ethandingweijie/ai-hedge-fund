"""Tier 2.7 — PM rationale thesis-density prompt contract test.

Pins the system-prompt wording so a future prompt tweak can't silently drop
the density rule (numbered themes, figure citations, implication line) that
the Summary-tab thesis renders.
"""
from __future__ import annotations

from src.agents.portfolio_manager import _PM_RATIONALE_SYSTEM_PROMPT as P


def test_numbered_themes_not_bullets():
    assert "numbered" in P
    assert '"1. "' in P and '"2. "' in P
    # Legacy bullet glyphs are explicitly banned in the output.
    assert "Do NOT use bullet glyphs" in P


def test_theme_cap():
    assert "FIVE" in P


def test_figure_density_rule():
    assert "TWO specific figures with units" in P


def test_implication_rule():
    assert "implication" in P


def test_dominant_theme_first():
    assert "dominant theme" in P


def test_covers_priced_in_and_pt_moat_risk():
    assert "priced in" in P
    assert "price target" in P
    assert "moat" in P
    assert "primary risk" in P


def test_json_only_output():
    assert "Output JSON only" in P

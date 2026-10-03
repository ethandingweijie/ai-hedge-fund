"""Owner, 2026-10-03: the PM summary is at most 300 words and carries a bold one-line headline of
at most 18 words that quantifies the catalyst or inflection."""
import inspect

from src.agents import portfolio_manager as pm
from src.data.models import AdvancedPortfolioDecision


def test_the_prompt_and_the_model_carry_the_headline_and_the_length_rule():
    P = pm._PM_RATIONALE_SYSTEM_PROMPT
    assert "Length rule" in P and "at most 300 words" in P
    assert "Headline rule" in P and "at most 18 words" in P and "No vague adjectives" in P
    assert "Backlog Burn-Off Accelerates Topside Margins Toward 10.5%; PT to S$2.45." in P
    assert AdvancedPortfolioDecision.model_fields["headline"].default == ""
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    assert '"headline": "<one line, at most 18 words, with a figure>"' in src
    assert "d = _enforce_pm_length(d, _inputs_text)" in src
    assert (pm.PM_RATIONALE_MAX_WORDS, pm.PM_HEADLINE_MAX_WORDS) == (300, 18)


_INPUTS = "Spot price $250.00\n12m PT $262.28\nBlended IV $333.77\nupside to target +5%\nNII compresses 15%\n"


def test_a_long_rationale_is_cut_at_a_theme_boundary_and_the_fact_is_recorded():
    theme = "1. " + " ".join(["word"] * 120) + "."
    rationale = "\n".join([theme, "2. " + " ".join(["word"] * 120) + ".", "3. " + " ".join(["word"] * 120) + "."])
    d = pm._enforce_pm_length({"rationale": rationale, "headline": "NII Compresses 15%; PT to $262."}, _INPUTS)
    assert d["rationale_length"]["trimmed"] is True and d["rationale_length"]["words_before"] == 363   # 3 x (marker + 120 words)
    assert d["rationale"].count("\n") == 1 and d["rationale_length"]["words_after"] == 242   # two themes kept, the third cut whole
    assert d["headline"] == "NII Compresses 15%; PT to $262." and d["headline_flags"] == []


def test_a_single_overlong_theme_is_cut_at_a_sentence_and_short_rationales_are_untouched():
    one = "1. " + " ".join([" ".join(["w"] * 50) + "."] * 8)        # 8 sentences of 50 words, one theme
    d = pm._enforce_pm_length({"rationale": one, "headline": "PT to $262."}, _INPUTS)
    assert d["rationale_length"]["trimmed"] and d["rationale_length"]["words_after"] <= 300 and d["rationale"].endswith(".")
    short = pm._enforce_pm_length({"rationale": "1. Fine.\n2. Also fine.", "headline": "PT to $262."}, _INPUTS)
    assert short["rationale"] == "1. Fine.\n2. Also fine." and short["rationale_length"]["trimmed"] is False


def test_the_headline_is_bounded_quantified_and_rebuilt_when_it_carries_an_unsupported_figure():
    long_head = "**" + " ".join([f"w{i}" for i in range(25)]) + " 15% PT $262**"
    d = pm._enforce_pm_length({"rationale": "1. Overweight: PT $262 against a $250 spot.", "headline": long_head}, _INPUTS)
    assert len(d["headline"].split()) == 18 and "headline trimmed to 18 words" in d["headline_flags"]
    # a figure the inputs do not carry -> rebuilt from theme 1's first sentence
    d2 = pm._enforce_pm_length({"rationale": "1. Overweight: PT $262 against a $250 spot. Second sentence.\n2. Risk theme.",
                                "headline": "Margins Toward 10.5%; PT to $999."}, _INPUTS)
    assert d2["headline"] == "Overweight: PT $262 against a $250 spot." and "rebuilt from theme 1" in d2["headline_flags"][0]
    # vague adjectives and a figure-less line are recorded, not rewritten
    d3 = pm._enforce_pm_length({"rationale": "1. x.", "headline": "Strong momentum builds a solid base"}, _INPUTS)
    assert "vague adjective(s) in headline: solid, strong" in d3["headline_flags"] and "headline carries no figure" in d3["headline_flags"]
    # an empty headline is derived
    d4 = pm._enforce_pm_length({"rationale": "1. NII compresses 15%; downgrading to Underweight. More.", "headline": ""}, _INPUTS)
    assert d4["headline"] == "NII compresses 15%; downgrading to Underweight." and "headline derived from theme 1" in d4["headline_flags"]


def test_the_pdf_and_both_report_paths_print_the_headline():
    from pathlib import Path
    from src.utils import pdf_report as pr
    assert 'decision.get("headline")' in inspect.getsource(pr)
    root = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src"
    assert "decision?.headline" in (root / "components" / "report" / "ReportHeader.tsx").read_text(encoding="utf-8")
    assert "decision.headline" in (root / "components" / "v2" / "V2ReportView.tsx").read_text(encoding="utf-8")
    assert "headline?: string;" in (root / "lib" / "reportTypes.ts").read_text(encoding="utf-8")

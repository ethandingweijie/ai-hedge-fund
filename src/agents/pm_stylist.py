"""Stage 2 of the rationale: the stylist.

The portfolio manager's rationale call is the fact-and-angle stage — it
decides, and it states the argument. Its prompt also MANDATES numbered
themes carrying two figures apiece, which is exactly why the output reads
like a checklist however good the underlying thinking is: one call is being
asked to reason and to write at the same time.

This pass only rewrites the prose. It never sees the decision fields and
cannot change them — action, size, stop and target are pinned in Python
before this runs. If it fails, or if it introduces a figure the draft did
not contain, the draft stands unchanged.

Style comes from retrieved exemplars rather than more hand-written rules;
src/memory/style_exemplars.py explains why the corpus is matched on
(sector, note type) instead of embedded.

The numeric check is the part that matters. "Learn the writing style, do
not copy the numbers" is only an instruction until something enforces it,
and a figure present in the rewrite but absent from the draft is precisely
the signature of a number carried across from a writing sample.
"""

from __future__ import annotations

import os
import re

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.utils.llm import call_llm

_STYLIST_SYSTEM_PROMPT = (
    "You are a senior equity analyst rewriting a colleague's draft so it "
    "reads like a published research note.\n"
    "Keep EVERY fact, figure, currency and conclusion exactly as drafted. "
    "You are changing how it reads, not what it says.\n"
    "Rules:\n"
    "- Open with the thesis itself, not a label or a heading.\n"
    "- Weave figures into sentences rather than listing them: "
    '"3Q25 earnings rose 12% YoY to US$14.4bn, below estimates" beats '
    '"Earnings: US$14.4bn (+12% YoY)".\n'
    "- Say plainly where the view differs from the consensus reading, and "
    "concede the counter-argument in the same breath rather than quarantining "
    "it in a separate risks paragraph.\n"
    "- Three to five short paragraphs. No bullet glyphs, and no numbered "
    "list unless the draft's own logic genuinely requires ordering.\n"
    "- Do NOT invent a figure, a date, a rating or a price target. If it is "
    "not in the draft, it does not belong in the rewrite.\n"
    "- The writing samples are register references drawn from OTHER "
    "companies. Their numbers and conclusions are not about this company.\n"
    "Output JSON only."
)


class StylistOutput(BaseModel):
    """Prose only — the decision fields are pinned upstream and never re-read."""

    rationale: str = Field(description="The rewritten rationale prose")


_NUM_TOKEN = re.compile(r"\d[\d,]*\.?\d*")


def numeric_tokens(text: str) -> set[str]:
    """Normalised numeric tokens, so "14,400" and "14400" compare equal."""
    return {
        t.replace(",", "").rstrip(".")
        for t in _NUM_TOKEN.findall(text or "")
    }


def introduced_figures(draft: str, styled: str) -> set[str]:
    """Figures in the rewrite that the draft never contained.

    Single digits are ignored: a rewrite may legitimately say "three themes"
    or "first". Anything longer is a real figure and must have come from the
    draft.
    """
    new = numeric_tokens(styled) - numeric_tokens(draft)
    return {t for t in new if len(t.replace(".", "")) > 1}


def restyle_rationale(
    ticker: str,
    draft: str,
    state,
    agent_id: str,
    note_type: str | None = None,
) -> tuple[str, str]:
    """Rewrite `draft` in the house register.

    Returns (rationale, audit_line). Falls back to the draft unchanged on
    any failure and on any figure the draft did not already contain.
    """
    if os.getenv("PM_STYLIST_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return draft, "stylist disabled"
    if not draft or len(draft.strip()) < 40:
        return draft, "draft too short to restyle"

    try:
        from src.memory.style_exemplars import (
            format_exemplar_block, get_style_exemplars,
        )
        exemplars = get_style_exemplars(ticker, note_type, limit=2)
        exemplar_block = format_exemplar_block(exemplars)
    except Exception:
        exemplars, exemplar_block = [], "  (no style exemplars available)"

    if not exemplars:
        # Without a register to borrow, this pass is just a paraphrase at a
        # higher temperature — more risk than value.
        return draft, "no style exemplars; draft kept"

    try:
        template = ChatPromptTemplate.from_messages([
            ("system", _STYLIST_SYSTEM_PROMPT),
            ("human", (
                "Company: {ticker}\n\n"
                "Writing samples (register only, other companies):\n{samples}\n\n"
                "Draft to rewrite — every fact in it is correct and must survive:\n"
                "{draft}\n\n"
                "Output:\n"
                '{{ "rationale": "..." }}'
            )),
        ])
        prompt = template.invoke({
            "ticker": ticker, "samples": exemplar_block, "draft": draft,
        })
        # Prose wants a looser sampler than the analytical calls. The
        # penalties are dropped automatically on providers that reject them.
        out: StylistOutput = call_llm(
            prompt=prompt,
            pydantic_model=StylistOutput,
            agent_name=agent_id,
            state=state,
            temperature=0.6,
            top_p=0.9,
            presence_penalty=0.2,
            frequency_penalty=0.2,
            default_factory=lambda: StylistOutput(rationale=draft),
        )
    except Exception as exc:
        return draft, f"stylist call failed ({type(exc).__name__})"

    styled = (out.rationale or "").strip()
    if len(styled) < 40:
        return draft, "stylist returned too little; draft kept"

    # call_llm returns the default_factory result when the model cannot be
    # initialised, which here IS the draft. Without this the audit would
    # claim "restyled" on a run where no model was ever reached — observed
    # live when the provider key was missing.
    if styled == draft.strip():
        return draft, "stylist returned the draft unchanged (model unavailable?)"

    leaked = introduced_figures(draft, styled)
    if leaked:
        return draft, (
            "stylist rejected — figures not in the draft: "
            + ", ".join(sorted(leaked)[:5])
        )

    src = ", ".join(
        f"{e['ticker']}/{e.get('note_type', '')}" for e in exemplars
    )
    return styled, f"restyled (samples: {src})"

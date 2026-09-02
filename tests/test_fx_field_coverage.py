"""
Guard: every field _extract_annual_series() writes onto a row must be
explicitly classified as FX-monetary or dimensionless.

Why this exists
---------------
`_FX_MONETARY` used to be defined ~200k characters downstream of the row
builder, inside run_dcf_agent(). Fields were added to the row builder and
never registered for conversion, so for any company reporting in a currency
other than the one it trades in, converted and unconverted values were mixed
in the same arithmetic. Twenty fields had drifted out of the set by the time
02888.HK (Standard Chartered: USD filings, HKD listing) surfaced it:

  * `tangible_book_value_per_share` stayed in USD while `total_equity` and
    `book_value_per_share` were converted to HKD, so the bank panel showed a
    "P/TBV Fair Value" of HK$19.32 against a HK$228.60 share price.
  * `interest_income` stayed in USD while `interest_expense` was converted,
    making NII negative and printing a NIM of -2.02%.
  * `operating_expense` stayed in USD, printing a 8.27% cost-income ratio
    against the 54.6% the bank reported.

The existing bank harness (tests/verify_bank_ui_fields.py) could not catch
any of it: it calls _extract_annual_series() and _compute_bank_metrics()
directly, and the FX conversion happens in neither — so it asserts against
raw unconverted USD, where those values are correct.

This test fails on the *classification*, not on any single value, so adding a
new line item to the row builder forces a deliberate FX decision.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_AGENT = Path(__file__).resolve().parents[1] / "src" / "agents" / "analysis" / "dcf_agent.py"


def _row_builder_fields() -> set[str]:
    """Parse the dict literal in _extract_annual_series's rows.append(...) call.

    Uses the AST rather than a regex so a reformat, a comment containing a
    quoted key, or a multi-line value cannot silently shrink the field list.
    """
    tree = ast.parse(_AGENT.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_extract_annual_series"
    )
    fields: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            for key in node.args[0].keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    fields.add(key.value)
    return fields


def test_row_builder_is_parseable():
    """Sanity: the AST walk actually found the row dict."""
    fields = _row_builder_fields()
    assert len(fields) > 20, f"only found {len(fields)} row fields — parser drifted"
    assert "revenue" in fields and "total_equity" in fields


def test_every_row_field_is_fx_classified():
    """No row field may be left unclassified."""
    from src.agents.analysis.dcf_agent import (
        _FX_MONETARY_FIELDS,
        _FX_NON_MONETARY_FIELDS,
    )

    classified = _FX_MONETARY_FIELDS | _FX_NON_MONETARY_FIELDS
    unclassified = _row_builder_fields() - classified
    assert not unclassified, (
        "These fields are written onto every row but are classified neither as "
        "FX-monetary nor as dimensionless, so they keep the filing's reporting "
        "currency while their neighbours get converted:\n  "
        + "\n  ".join(sorted(unclassified))
        + "\n\nAdd each to _FX_MONETARY_FIELDS (money, including per-share "
          "amounts) or _FX_NON_MONETARY_FIELDS (counts, ratios, dates) in "
          "src/agents/analysis/dcf_agent.py."
    )


def test_classification_sets_are_disjoint():
    from src.agents.analysis.dcf_agent import (
        _FX_MONETARY_FIELDS,
        _FX_NON_MONETARY_FIELDS,
    )

    overlap = _FX_MONETARY_FIELDS & _FX_NON_MONETARY_FIELDS
    assert not overlap, f"fields classified both ways: {sorted(overlap)}"


@pytest.mark.parametrize(
    "field",
    [
        # The three that produced visible nonsense on 02888.HK.
        "tangible_book_value_per_share",
        "interest_income",
        "operating_expense",
        # The TBV strip inputs — wrong currency here corrupts the fallback
        # derivation equity - goodwill - intangibles.
        "goodwill",
        "intangible_assets",
        # Read by the cash-conversion gate's owner-earnings identity.
        "change_in_working_capital",
    ],
)
def test_known_monetary_fields_are_converted(field):
    """Regression pins for the specific fields behind the 02888.HK report."""
    from src.agents.analysis.dcf_agent import _FX_MONETARY_FIELDS

    assert field in _FX_MONETARY_FIELDS


def test_ratios_and_counts_are_not_converted():
    """Converting a ratio or a share count would corrupt correct values."""
    from src.agents.analysis.dcf_agent import _FX_MONETARY_FIELDS

    for field in ("shares_outstanding", "debt_to_equity", "period"):
        assert field not in _FX_MONETARY_FIELDS


def test_fx_set_is_actually_used_by_the_conversion_loop():
    """The conversion loop must read the module-level set.

    Guards against the previous shape, where a local literal shadowed any
    shared definition and drifted from the row builder unnoticed.
    """
    src = _AGENT.read_text(encoding="utf-8")
    assert "_FX_MONETARY = _FX_MONETARY_FIELDS" in src, (
        "the conversion loop no longer binds to the module-level field set"
    )
    # And no competing inline literal has reappeared.
    assert not re.search(r"_FX_MONETARY\s*=\s*\{", src), (
        "an inline _FX_MONETARY literal reappeared — it will drift from the "
        "row builder again"
    )

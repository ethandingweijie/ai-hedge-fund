"""The pipeline payload allowlist is a contract, and it keeps being broken.

`run_advanced_pipeline` returns an explicit dict literal, and that dict is
what becomes `web_runs.full_result_json`. A key that lives in `state["data"]`
but is not listed there simply never reaches the archived run — the data is
computed, persisted nowhere, and the card that needs it renders empty with
no error anywhere to explain why.

This has now happened at least four times: `saas_metrics`,
`framework_metrics_all`, `dcf_skip_reasons` (whose comment in pipeline.py
documents the pattern), and `financial_statements`. The last one was
especially quiet — checkpoints are serialised by a different, broader path,
so the three-statement view appeared on a half-finished run and vanished on
the completed one.

The test reads the source rather than running the pipeline: a full run costs
LLM calls and ~50 minutes, and the thing being guarded is a static list.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PIPELINE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "pipeline.py"
)

# Keys the frontend, PDF or archive read out of full_result_json. Adding a
# consumer of state["data"] without adding it here is the bug this catches.
REQUIRED_KEYS = {
    "decisions",
    "analyst_signals",
    "raw_financials",
    "financial_statements",   # three-statement view (data_router)
    "style_audit",            # stylist audit line (pm_stylist)
    "dcf_range",
    "dcf_skip_reasons",
    "dcf_engine_error",
    "scenario_analysis",
    "power_law_analysis",
    "value_trap_analysis",
    "peer_comparison",
    "sector_card",
    "industry_brief",
    "deep_research",
    "profile_name",
}


def _returned_payload_keys() -> set[str]:
    """Every string key of the dict literal `run_advanced_pipeline` returns."""
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "run_advanced_pipeline"),
        None,
    )
    assert fn is not None, "run_advanced_pipeline not found in src/pipeline.py"

    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


@pytest.fixture(scope="module")
def payload_keys():
    return _returned_payload_keys()


def test_the_allowlist_is_found_at_all(payload_keys):
    """A guard on the guard: if the return shape is refactored away, this
    test must fail loudly rather than pass on an empty set."""
    assert len(payload_keys) > 20, (
        f"only {len(payload_keys)} keys parsed — has the return shape changed?"
    )


@pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
def test_required_key_reaches_the_archived_run(key, payload_keys):
    assert key in payload_keys, (
        f"'{key}' is absent from the payload allowlist in src/pipeline.py, so "
        f"it never reaches web_runs.full_result_json. Whatever reads it will "
        f"render empty with no error to explain why."
    )


def test_financial_statements_and_raw_financials_travel_together(payload_keys):
    """raw_financials is what dcf_agent and the extractors read;
    financial_statements is the display view built from the same fetch. One
    without the other means the Financials tab and the valuation disagree
    about which run they are describing."""
    assert ("raw_financials" in payload_keys) == (
        "financial_statements" in payload_keys
    )


# ── Serving runs whose stored blob predates the allowlist fix ────────────

def test_hydration_fills_statements_from_ticker_signals():
    """`full_result_json` is returned verbatim, so runs archived before
    `financial_statements` was added to the allowlist have no statements in
    the blob — even though save_run persisted them to
    ticker_signals.financial_statements_json. The API backfills from there
    rather than leaving those runs permanently without a Financials card.
    """
    from unittest.mock import patch
    from app.backend.services.analysis_service import _hydrate_financial_statements

    stored = {"financial_statements_json":
              '{"layout": "bank", "periods": ["FY2025"], "statements": {}}'}
    payload = {"data": {"_archive_run_id": "arch-1", "raw_financials": {}}}

    with patch("app.backend.services.analysis_service._fetch_one",
               return_value=stored):
        _hydrate_financial_statements("web-1", payload)
    assert payload["data"]["financial_statements"]["layout"] == "bank"


def test_hydration_does_not_overwrite_a_current_run():
    from unittest.mock import patch
    from app.backend.services.analysis_service import _hydrate_financial_statements

    payload = {"data": {"_archive_run_id": "arch-1",
                        "financial_statements": {"layout": "reit"}}}
    with patch("app.backend.services.analysis_service._fetch_one") as m:
        _hydrate_financial_statements("web-1", payload)
        m.assert_not_called()          # no query when the blob already has it
    assert payload["data"]["financial_statements"]["layout"] == "reit"


def test_hydration_never_raises():
    """A missing display card must not take down the whole report."""
    from unittest.mock import patch
    from app.backend.services.analysis_service import _hydrate_financial_statements

    payload = {"data": {"_archive_run_id": "arch-1"}}
    with patch("app.backend.services.analysis_service._fetch_one",
               side_effect=RuntimeError("db down")):
        _hydrate_financial_statements("web-1", payload)   # must not raise
    assert "financial_statements" not in payload["data"]

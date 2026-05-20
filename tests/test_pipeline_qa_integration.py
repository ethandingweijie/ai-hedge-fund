"""Phase 6 — pipeline integration tests for Card QA Agent (Phase 10.5).

Tests the contract:
  1. card_qa_audit populates state["data"] when run_card_qa_agent succeeds
  2. card_qa_engine_error captures the exception when QA crashes,
     and state["data"]["card_qa_audit"] still ends up as {} (not missing)
  3. Phase 10.5 is wired into the return dict (web_runs persistence)

We DON'T run the full pipeline here — that requires LLM/FMP/network. We
test the integration AT THE HOOKPOINT by simulating the surrounding state
and exercising the exact code path inserted in src/pipeline.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "audit" / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def _exec_phase_10_5_hookpoint(state: dict, tickers: list[str]) -> tuple[dict, dict]:
    """Execute the Phase 10.5 hookpoint code path against a minimal pipeline-shaped state.

    This mirrors EXACTLY the try/except block added to src/pipeline.py
    so we can verify its behavior in isolation (without invoking the
    entire pipeline).
    """
    primary_ticker = tickers[0] if tickers else ""
    try:
        from src.agents.audit.card_qa_agent import run_card_qa_agent

        _qa_audits: dict[str, dict] = {}
        for _qa_ticker in tickers:
            if not _qa_ticker:
                continue
            _qa_audits[_qa_ticker] = run_card_qa_agent(state, _qa_ticker)
        state["data"]["card_qa_audit"] = _qa_audits
    except Exception as _qa_exc:
        import traceback as _qa_tb
        _qa_trace = "".join(
            _qa_tb.format_exception(type(_qa_exc), _qa_exc, _qa_exc.__traceback__)
        )
        state["data"]["card_qa_engine_error"] = {
            "exception_type": type(_qa_exc).__name__,
            "message":        str(_qa_exc)[:500],
            "traceback":      _qa_trace,
            "primary_ticker": primary_ticker,
            "tickers":        list(tickers),
        }
        state["data"]["card_qa_audit"] = {}

    return state["data"].get("card_qa_audit", {}), state["data"].get("card_qa_engine_error")


def test_hookpoint_populates_card_qa_audit_for_mrna():
    """End-to-end at the integration boundary: MRNA fixture goes in,
    state['data']['card_qa_audit']['MRNA'] comes out populated."""
    fixture = _load_fixture("MRNA__0182e126.json")
    audit, err = _exec_phase_10_5_hookpoint(fixture, ["MRNA"])
    assert err is None
    assert "MRNA" in audit
    assert audit["MRNA"]["qa_version"] == "v1"
    assert "qa_ran_at" in audit["MRNA"]
    assert "qa_schema_versions" in audit["MRNA"]


def test_hookpoint_for_multi_ticker_run():
    """Pipeline supports multi-ticker. Hookpoint must iterate."""
    state = {
        "data": {
            "sectors": {},
            "profile_names": {},
            "deep_research": "",
        }
    }
    audit, err = _exec_phase_10_5_hookpoint(state, ["AAPL", "MSFT", "JPM"])
    assert err is None
    assert set(audit.keys()) == {"AAPL", "MSFT", "JPM"}
    for t in ("AAPL", "MSFT", "JPM"):
        assert audit[t]["qa_version"] == "v1"


def test_hookpoint_swallows_qa_exception_pipeline_does_not_crash():
    """If run_card_qa_agent itself raises (catastrophic bug), the pipeline
    MUST continue. The error is captured to card_qa_engine_error and the
    audit dict becomes empty {}.
    """
    state = {"data": {"deep_research": ""}}

    with patch(
        "src.agents.audit.card_qa_agent.run_card_qa_agent",
        side_effect=RuntimeError("synthetic catastrophic failure"),
    ):
        audit, err = _exec_phase_10_5_hookpoint(state, ["AAPL"])

    assert audit == {}
    assert err is not None
    assert err["exception_type"] == "RuntimeError"
    assert "synthetic catastrophic failure" in err["message"]
    assert "RuntimeError" in err["traceback"]
    assert err["primary_ticker"] == "AAPL"
    assert err["tickers"] == ["AAPL"]


def test_hookpoint_skips_empty_ticker_strings():
    """Defensive: tickers list with empty strings shouldn't bork iteration."""
    state = {"data": {}}
    audit, err = _exec_phase_10_5_hookpoint(state, ["AAPL", "", "MSFT"])
    assert err is None
    assert set(audit.keys()) == {"AAPL", "MSFT"}


def test_hookpoint_with_empty_ticker_list():
    """Empty tickers list → empty audit, no error."""
    state = {"data": {}}
    audit, err = _exec_phase_10_5_hookpoint(state, [])
    assert audit == {}
    assert err is None


def test_zts_fixture_meta_check_failure_captured_in_audit():
    """The ZTS misclassification case must surface in the audit dict
    after pipeline integration. The dashboard banner (Phase 9) reads
    audit['ZTS']['meta_check']['passed'] to decide whether to show
    the critical red palette."""
    fixture = _load_fixture("ZTS__b91aa9b4.json")
    audit, err = _exec_phase_10_5_hookpoint(fixture, ["ZTS"])
    assert err is None
    assert audit["ZTS"]["meta_check"]["passed"] is False
    assert audit["ZTS"]["meta_check"]["suggested_profile"] == "Large Cap Pharma"


# ── pipeline.py source-level regression checks ─────────────────────────────


def test_pipeline_source_contains_phase_10_5_hookpoint():
    """Regression guard: if someone strips the hookpoint accidentally
    while refactoring src/pipeline.py, this test catches it."""
    p = Path(__file__).resolve().parent.parent / "src" / "pipeline.py"
    src = p.read_text(encoding="utf-8")
    assert "PHASE 10.5" in src
    assert "run_card_qa_agent" in src
    assert "card_qa_audit" in src
    assert "card_qa_engine_error" in src


def test_pipeline_return_dict_includes_card_qa_audit():
    """The return dict must include card_qa_audit or it never reaches
    web_runs.full_result_json, and the frontend Phase 9 banner has
    nothing to render."""
    p = Path(__file__).resolve().parent.parent / "src" / "pipeline.py"
    src = p.read_text(encoding="utf-8")
    # Look for the return-dict line that ferries the audit to web_runs
    assert '"card_qa_audit":' in src
    assert '"card_qa_engine_error":' in src

"""Forward estimates on every operating name (2026-11-12): the three causes behind 09988.HK, BX and 00177.HK
printing no FY+1E..FY+5E columns on the page or in the workbook."""
import copy
import importlib.util
from pathlib import Path

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis import three_statement as ts
from src.agents.industry import deep_research as dr
from src.memory import run_archive

_spec = importlib.util.spec_from_file_location("_tst", Path(__file__).resolve().parent / "test_three_statement.py")
_tst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tst)
RAW = _tst.RAW


# ── 00177.HK: a filing's own rounding gap no longer withholds the statements ─────────────────────────

def test_a_rounding_gap_in_the_filing_is_absorbed_at_the_opening_and_the_statements_build():
    raw = copy.deepcopy(RAW)
    raw["FY2025"]["total_assets"] = 14.5e9 + 1000.0                  # 1,000 on 14.5bn: 00177.HK's case
    op = ts.opening_from_raw(raw)
    assert op["balance_gap_as_filed"] == pytest.approx(1000.0)
    assert op["balance_gap_absorbed"] == pytest.approx(1000.0)
    assert op["other_noncurrent_liabilities"] == pytest.approx(10.0e9 - 7.4e9 - 2.0e9 + 1000.0)
    out = ts.build(_tst._fc(), op, ts.assumptions_from_history(raw, spot=190.0))
    assert out["fy_labels"] and out["reconciliation"]["ok"] is True
    assert any("misses A = L + E by 1,000 (rounding)" in n for n in out["notes"])


def test_a_material_gap_is_not_absorbed_and_still_fails_the_suite():
    raw = copy.deepcopy(RAW)
    raw["FY2025"]["total_assets"] = 15.0e9                            # 0.5bn: a data problem, not rounding
    op = ts.opening_from_raw(raw)
    assert "balance_gap_absorbed" not in op
    out = ts.build(_tst._fc(), op, ts.assumptions_from_history(raw, spot=190.0))
    assert out["skipped"].startswith("RECONCILIATION FAILED")


def test_a_balanced_filing_is_untouched():
    op = ts.opening_from_raw(RAW)
    assert op["balance_gap_as_filed"] == pytest.approx(0.0) and "balance_gap_absorbed" not in op


# ── BX: an asset manager takes the statements; its DCF stays levered ──────────────────────────────

@pytest.mark.parametrize("profile", ["Alt Asset Manager", "Asset Manager"])
def test_fee_financials_build_statements_but_keep_the_levered_basis(profile):
    assert d._interest_is_cost_of_goods(profile, "Financials") is True        # owner: fee financials stay levered
    assert d._statements_need_capital_model(profile, "Financials") is False


@pytest.mark.parametrize("profile", ["Money Center Bank", "Insurance", "Holding Company"])
def test_balance_sheet_families_still_withhold_the_statements(profile):
    assert d._statements_need_capital_model(profile, "Financials") is True


def test_operating_companies_and_payment_networks_are_unchanged():
    assert d._statements_need_capital_model("Payment Networks", "Financials") is False
    assert d._statements_need_capital_model("Managed Care", "Healthcare") is False
    assert d._statements_need_capital_model("", "Financials") is True        # an unprofiled financial stays withheld


def test_the_forecast_context_uses_the_statements_gate_not_the_basis_rule():
    src = Path(d.__file__).read_text(encoding="utf-8")
    assert '_fc_ctx["statements_family_ok"] = not _statements_need_capital_model(profile_name, sector)' in src


# ── 09988.HK: archived research written before section 2G is refreshed once ──────────────────────

def _archive(monkeypatch, hit):
    monkeypatch.setattr(run_archive, "get_recent_research", lambda ticker, max_age_days=7, qualifying_tiers=None: hit)


def _hit(sections, text="research"):
    return {"research_tier": "anthropic_web", "age_days": 40.0, "deep_research_text": text,
            "deep_research_sections": sections, "run_id": "r"}


def test_pre_2g_archived_research_is_discarded(monkeypatch):
    monkeypatch.delenv("PRE_2G_RESEARCH_REFRESH", raising=False)
    _archive(monkeypatch, _hit({"2a": "x", "2f": "y"}))
    assert dr._resolve_research_cache("09988.HK", max_age_days=None) is None


def test_research_with_2g_or_a_guidance_block_is_reused(monkeypatch):
    monkeypatch.delenv("PRE_2G_RESEARCH_REFRESH", raising=False)
    _archive(monkeypatch, _hit({"2a": "x", "2g": "GUIDANCE | metric=eps"}))
    assert dr._resolve_research_cache("NEE", max_age_days=None) is not None
    _archive(monkeypatch, _hit({"2a": "x"}, text="... GUIDANCE_BLOCK_START\nGUIDANCE | ...\nGUIDANCE_BLOCK_END"))
    assert dr._resolve_research_cache("NEE", max_age_days=None) is not None


@pytest.mark.parametrize("val", ["false", "0", "off"])
def test_the_kill_switch_keeps_reusing_pre_2g_research(monkeypatch, val):
    monkeypatch.setenv("PRE_2G_RESEARCH_REFRESH", val)
    _archive(monkeypatch, _hit({"2a": "x"}))
    assert dr._resolve_research_cache("09988.HK", max_age_days=None) is not None


def test_a_resume_bundle_is_never_discarded_for_missing_2g(monkeypatch):
    monkeypatch.delenv("PRE_2G_RESEARCH_REFRESH", raising=False)
    bundle = {**_hit({"2a": "x"}), "research_tier": "anthropic_web"}
    got = dr._resolve_research_cache("09988.HK", resume_research=bundle, max_age_days=None)
    assert got is not None

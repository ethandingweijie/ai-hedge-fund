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


# ── 09988.HK re-run: a segment's break-even target drove the group's margin to zero ────────────────

from src.agents.analysis import guidance_forecast as gfm

_gfs = importlib.util.spec_from_file_location("_gft2", Path(__file__).resolve().parent / "test_guidance_forecast.py")
_gft = importlib.util.module_from_spec(_gfs)
_gfs.loader.exec_module(_gft)

_QUICK_COMMERCE = {"metric": "operating_margin", "target_year": "FY2029", "low": 0.0, "mid": 0.0, "high": 0.0, "unit": "decimal",
                   "basis": "operating break-even commitment for Quick Commerce", "source": "Q1 FY27 call"}


def _with_target(mt):
    return {**copy.deepcopy(_gft._BLOCK), "medium_term_target": mt}


@pytest.mark.parametrize("scenario", ["bear", "base", "bull"])
def test_a_break_even_margin_target_on_a_profitable_company_is_dropped_as_segment_scope(scenario):
    t = gfm.targets_for(_with_target(_QUICK_COMMERCE), scenario, {"ebit_margin": 0.136})
    assert t["target"] is None
    assert "segment's target" in t["target_dropped"] and "Quick Commerce" in t["target_dropped"]


def test_a_target_the_extractor_scoped_to_a_segment_is_dropped():
    mt = {**_QUICK_COMMERCE, "mid": 0.12, "low": 0.12, "high": 0.12, "scope": "segment"}
    t = gfm.targets_for(_with_target(mt), "base", {"ebit_margin": 0.136})
    assert t["target"] is None and "scoped to segment" in t["target_dropped"]


def test_a_company_margin_target_is_kept():
    mt = {**_QUICK_COMMERCE, "low": 0.18, "mid": 0.20, "high": 0.22, "basis": "group operating margin target", "scope": "company"}
    t = gfm.targets_for(_with_target(mt), "base", {"ebit_margin": 0.136})
    assert t["target"] and t["target"]["value"] == pytest.approx(0.20) and not t.get("target_dropped")


def test_a_loss_makers_own_break_even_commitment_is_kept():
    t = gfm.targets_for(_with_target(_QUICK_COMMERCE), "base", {"ebit_margin": -0.25})
    assert t["target"] and t["target"]["value"] == 0.0


def test_the_forecast_keeps_its_margin_and_flags_the_dropped_target():
    kw = dict(scenario="base", series=_gft._SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025,
              shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    clean = gfm.build_forecast(_with_target(None), **kw)                  # the same block with no medium-term target
    seg = gfm.build_forecast(_with_target(_QUICK_COMMERCE), **kw)
    assert [r["ebit_margin"] for r in seg["rows"]] == pytest.approx([r["ebit_margin"] for r in clean["rows"]])
    assert any("Medium-term target dropped" in f for f in seg["flags"])


def test_the_extractor_keeps_the_scope_it_was_asked_for():
    out = dr._normalize_guidance_estimates({"estimates": {"base": {"revenue_growth_fy1": 0.09}},
                                            "medium_term_target": {**_QUICK_COMMERCE, "scope": "Segment"}})
    assert out["medium_term_target"]["scope"] == "segment"
    assert "COMPANY-WIDE only" in dr._GUIDANCE_ESTIMATES_SYSTEM


# ── 09988.HK second re-run: EPS per ADS set a 109.6% EBIT margin once the segment target was gone ──────

def _ads_block(mult):
    blk = _with_target(None)
    for sc in blk["estimates"].values():
        for k in ("eps_fy1", "eps_fy2"):
            if sc.get(k) is not None:
                sc[k] = sc[k] * mult
    return blk


def _kw():
    return dict(scenario="base", series=_gft._SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025,
                shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)


def test_an_eps_off_the_share_basis_does_not_set_the_margin_and_the_share_check_fails():
    fc = gfm.build_forecast(_ads_block(20.0), **_kw())                # an EPS in the wrong unit: far past any margin it could earn
    assert max(r["ebit_margin"] for r in fc["rows"]) < 0.10
    assert any("not on the model's share basis" in f for f in fc["flags"])
    inv3 = next(i for i in fc["invariants"] if i["id"] == 3)
    assert inv3["ok"] is False                                        # the check now bites, and the intervention acts


def test_a_plausible_eps_implied_margin_still_replaces_a_conflicting_guided_margin():
    assert gfm._eps_margin_plausible(0.08 * 100, 100, {"ebit_margin": 0.05}) is True       # 8% against 5%: EN3 applies
    assert gfm._eps_margin_plausible(1.096 * 100, 100, {"ebit_margin": 0.136}) is False    # BABA's 109.6%
    assert gfm._eps_margin_plausible(0.30 * 100, 100, {"ebit_margin": 0.136}) is True      # within 20 points
    assert gfm._eps_margin_plausible(0.50 * 100, 100, {}) is True                           # no trailing margin, no bound
    assert gfm._eps_margin_plausible(0.115 * 100, 100, {"ebit_margin": 0.114}) is True     # BSL.SI: the EPS sits on its own margin
    assert gfm._eps_margin_plausible(0.125 * 100, 100, {"ebit_margin": 0.056}) is True     # 01801.HK: a biotech turning profitable

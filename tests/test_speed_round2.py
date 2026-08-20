"""Workstream E (speed round 2, R1–R5) unit tests.

Covers the pure/deterministic pieces of each speed lever:

  R1  deterministic industry-brief assembly — universal indicators + N/A rules
      (``specialist.assemble_industry_brief_merged``) and the SECTION 7 parser
      in ``deep_research._extract_sections``.
  R2  search-profile / brief-mode prompt gating (``_build_research_system``).
  R3  (retired) investor panel resolution + PM voice renormalisation tests —
      the committee was decommissioned in M2 Track D/E.
  R4  fast-tier model routing (``llm.get_agent_model_config``).
  R5  card-QA delta check (``card_qa_agent.compute_card_qa_hash`` /
      ``should_reuse_card_qa``).

No LLM / network calls — all external fetchers are monkeypatched.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

# ─────────────────────────────────────────────────────────────────────────────
# R5 — card-QA delta check
# ─────────────────────────────────────────────────────────────────────────────

from src.agents.audit.card_qa_agent import (
    QA_VERSION,
    compute_card_qa_hash,
    should_reuse_card_qa,
)


def _clean_audit(**overrides) -> dict:
    audit = {
        "qa_version": QA_VERSION,
        "qa_ran_at": "2026-08-08T00:00:00+00:00",
        "qa_model": "qwen3.6-plus",
        "qa_schema_versions": {"valuation": "v1"},
        "meta_check": {"passed": True, "checks_run": ["sector"], "issues": []},
        "cards_inspected": [],
        "auto_remediations": [],
        "human_review_flags": [],
        "kpis_out_of_sane_range": 0,
        "rows_missing_subfields": 0,
        "qa_cost_estimate_usd": 0.01,
        "qa_budget_hit": False,
    }
    audit.update(overrides)
    return audit


class TestCardQaHash:
    def test_deterministic(self):
        card = {"profile": "SaaS", "rows": [{"kpi": "NRR", "value": 1.2}]}
        h1 = compute_card_qa_hash(card, "deep research text")
        h2 = compute_card_qa_hash(card, "deep research text")
        assert h1 == h2 and len(h1) == 64

    def test_key_order_irrelevant(self):
        h1 = compute_card_qa_hash({"a": 1, "b": 2}, "dr")
        h2 = compute_card_qa_hash({"b": 2, "a": 1}, "dr")
        assert h1 == h2

    def test_sensitive_to_card_change(self):
        h1 = compute_card_qa_hash({"rr": 1.2}, "dr")
        h2 = compute_card_qa_hash({"rr": 1.3}, "dr")
        assert h1 != h2

    def test_sensitive_to_research_text_change(self):
        h1 = compute_card_qa_hash({"rr": 1.2}, "dr v1")
        h2 = compute_card_qa_hash({"rr": 1.2}, "dr v2")
        assert h1 != h2

    def test_none_card_does_not_raise(self):
        assert compute_card_qa_hash(None, "") != compute_card_qa_hash({}, "")


class TestShouldReuseCardQa:
    H = compute_card_qa_hash({"k": 1}, "research")

    def test_reuse_clean_matching_audit(self):
        assert should_reuse_card_qa(_clean_audit(), self.H, self.H) is True

    def test_advisory_flags_do_not_block_reuse(self):
        audit = _clean_audit(human_review_flags=[
            {"reason": "value_out_of_sane_range", "card": "valuation"},
        ])
        assert should_reuse_card_qa(audit, self.H, self.H) is True

    @pytest.mark.parametrize("mutate", [
        lambda a: a.update(qa_version="v0"),                              # stale version
        lambda a: a["meta_check"].update(passed=False),                   # meta-check fail
        lambda a: a["human_review_flags"].append(                         # suspect classification
            {"reason": "classification_likely_wrong"}),
        lambda a: a.update(qa_budget_hit=True),                           # incomplete audit
    ])
    def test_dirty_audits_never_reused(self, mutate):
        audit = _clean_audit()
        mutate(audit)
        assert should_reuse_card_qa(audit, self.H, self.H) is False

    def test_hash_mismatch_blocks_reuse(self):
        other = compute_card_qa_hash({"k": 2}, "research")
        assert should_reuse_card_qa(_clean_audit(), other, self.H) is False

    def test_missing_inputs_block_reuse(self):
        assert should_reuse_card_qa(None, self.H, self.H) is False
        assert should_reuse_card_qa({}, self.H, self.H) is False
        assert should_reuse_card_qa(_clean_audit(), None, self.H) is False
        assert should_reuse_card_qa(_clean_audit(), "", self.H) is False


# ─────────────────────────────────────────────────────────────────────────────
# R3 — retired with the investor committee (M2 Track D/E). Panel resolution
# and PM voice renormalisation no longer exist to test.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# R4 — fast-tier model routing
# ─────────────────────────────────────────────────────────────────────────────

from src.utils.llm import get_agent_model_config

_RUN_STATE = {"metadata": {"model_name": "claude-sonnet-4-6",
                           "model_provider": "Anthropic"}}


@pytest.fixture
def clean_model_env(monkeypatch):
    for k in ("PIPELINE_FAST_MODEL", "AGENT_MODEL_SCENARIO_AGENT",
              "AGENT_MODEL_VALUE_TRAP_AGENT", "AGENT_MODEL_DEEP_RESEARCH"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


class TestFastTierRouting:
    @pytest.mark.parametrize("agent", [
        "scenario_agent", "power_law_agent", "value_trap_agent",
    ])
    def test_fast_tier_defaults_to_alibaba_fast_model(self, clean_model_env, agent):
        assert get_agent_model_config(_RUN_STATE, agent) == ("qwen3.6-plus", "Alibaba")

    def test_decommissioned_investor_names_fall_to_run_model(self, clean_model_env):
        # The investor_ prefix left the fast tier with the committee (M2 E):
        # any such legacy name now routes to the run model, not the fast tier.
        assert get_agent_model_config(_RUN_STATE, "investor_buffett") == (
            "claude-sonnet-4-6", "Anthropic")

    def test_research_agents_keep_run_model(self, clean_model_env):
        for agent in ("deep_research", "dcf_agent", "industry_specialist"):
            assert get_agent_model_config(_RUN_STATE, agent) == (
                "claude-sonnet-4-6", "Anthropic")

    def test_empty_pipeline_fast_model_disables_tiering(self, clean_model_env):
        clean_model_env.setenv("PIPELINE_FAST_MODEL", "")
        assert get_agent_model_config(_RUN_STATE, "scenario_agent") == (
            "claude-sonnet-4-6", "Anthropic")

    def test_pipeline_fast_model_override(self, clean_model_env):
        clean_model_env.setenv("PIPELINE_FAST_MODEL", "gpt-4.1")
        assert get_agent_model_config(_RUN_STATE, "scenario_agent") == ("gpt-4.1", "OpenAI")

    def test_agent_model_env_beats_fast_tier(self, clean_model_env):
        clean_model_env.setenv("AGENT_MODEL_SCENARIO_AGENT", "qwen3.6-plus")
        assert get_agent_model_config(_RUN_STATE, "scenario_agent") == (
            "qwen3.6-plus", "Alibaba")

    def test_unknown_agent_model_env_falls_through(self, clean_model_env):
        clean_model_env.setenv("AGENT_MODEL_SCENARIO_AGENT", "no-such-model-xyz")
        assert get_agent_model_config(_RUN_STATE, "scenario_agent") == (
            "qwen3.6-plus", "Alibaba")


# ─────────────────────────────────────────────────────────────────────────────
# R2/R1 — deep-research prompt gating + SECTION 7 parsing
# ─────────────────────────────────────────────────────────────────────────────

from src.agents.industry.deep_research import _build_research_system, _extract_sections
from src.memory.run_archive import _parse_sections_inline


@pytest.fixture
def clean_prompt_env(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_SEARCH_PROFILE", raising=False)
    monkeypatch.delenv("INDUSTRY_BRIEF_MODE", raising=False)
    return monkeypatch


class TestResearchPromptGating:
    def test_default_focused_profile_has_8_searches(self, clean_prompt_env):
        p = _build_research_system("2026", "Tech", "")
        assert "exactly 8 searches" in p
        # kept feeds: management FY+1 guidance (dcf_calibration), market share
        assert "management guidance EBITDA revenue outlook" in p
        assert "market share competitive landscape" in p
        # dropped gap-fill default + legacy queries
        assert "additional searches to fill gaps" not in p
        assert "product launches AI strategy" not in p
        assert "analyst price target consensus" not in p
        assert "customer wins losses" not in p

    def test_full_profile_restores_legacy_13(self, clean_prompt_env):
        clean_prompt_env.setenv("DEEP_RESEARCH_SEARCH_PROFILE", "full")
        p = _build_research_system("2026", "Tech", "")
        assert "Suggested search sequence" in p
        assert "additional searches to fill gaps" in p
        assert "customer wins losses" in p
        assert "analyst price target consensus" in p

    def test_merged_brief_mode_appends_section_7(self, clean_prompt_env):
        assert "INDUSTRY INTELLIGENCE BRIEF" in _build_research_system("2026", "Tech", "")

    def test_legacy_brief_mode_omits_section_7(self, clean_prompt_env):
        clean_prompt_env.setenv("INDUSTRY_BRIEF_MODE", "legacy")
        assert "INDUSTRY INTELLIGENCE BRIEF" not in _build_research_system("2026", "Tech", "")


class TestExtractSectionsBrief:
    REPORT = (
        "## 2A — Value Chain\nalpha\n"
        "## 2B — Competitive Landscape\nbeta\n"
        "## 2F — KPI Monitor\ngamma\n"
        "SECTION 7 — INDUSTRY INTELLIGENCE BRIEF\n- bullet one\n"
    )

    def test_brief_captured_and_spanning_section_trimmed(self):
        s = _extract_sections(self.REPORT)
        assert s["brief"].startswith("SECTION 7")
        assert "bullet one" in s["brief"]
        assert "SECTION 7" not in s["2f"]     # 2F consumers don't see brief text
        assert "gamma" in s["2f"]
        assert "beta" in s["2b"]

    def test_report_without_section_7_unchanged(self):
        text = "## 2A — Value Chain\nalpha\n## 2B — Competitive\nbeta\n"
        s = _extract_sections(text)
        assert "brief" not in s
        assert "alpha" in s["2a"]


class TestArchiveParserLockStep:
    """run_archive._parse_sections_inline re-parses archived research text on
    cache hits; deep_research._extract_sections parses it fresh. The C2
    extractor cache hashes the sections dict, so the two parsers MUST produce
    identical output — any drift silently disables extractor reuse."""

    SECTION7_REPORT = (
        "## 2A — Value Chain\nalpha\n"
        "## 2B — Competitive Landscape\nbeta\n"
        "## 2F — KPI Monitor\ngamma\n"
        "SECTION 7 — INDUSTRY INTELLIGENCE BRIEF\n- bullet one\n"
    )
    PROSE_HEADERS = (
        "Section 2A: value chain\nalpha\n"
        "- 2B) competition\nbeta\n"
        "INDUSTRY INTELLIGENCE BRIEF\n- b\n"
    )

    def test_section7_report_identical(self):
        assert _parse_sections_inline(self.SECTION7_REPORT) == \
            _extract_sections(self.SECTION7_REPORT)

    def test_prose_header_variants_identical(self):
        assert _parse_sections_inline(self.PROSE_HEADERS) == \
            _extract_sections(self.PROSE_HEADERS)

    def test_no_headers_fallback_identical(self):
        text = "plain report without any section headers"
        assert _parse_sections_inline(text) == _extract_sections(text)

    def test_c2_hash_matches_across_parses(self):
        from src.agents.industry.deep_research import _hash_research_sections
        fresh = _extract_sections(self.SECTION7_REPORT)
        cached = _parse_sections_inline(self.SECTION7_REPORT)
        assert _hash_research_sections(fresh, "SaaS") == \
            _hash_research_sections(cached, "SaaS")


# ─────────────────────────────────────────────────────────────────────────────
# R1 — deterministic universal indicators (assemble_industry_brief_merged)
# ─────────────────────────────────────────────────────────────────────────────

from src.agents.industry import specialist


@pytest.fixture
def merged_state():
    return {
        "metadata": {},
        "data": {
            "tickers": ["CRWD"],
            "end_date": "2026-08-08",
            "sector": "Tech",
            "valuation_profile": "",
            "deep_research_sections": {"brief": "- Assertion bullet [1]"},
            "framework_metrics": {"CRWD": {"rule_of_40": 55.0, "_meta": "skip me"}},
        },
    }


def _patch_fmp(monkeypatch, *, metrics, estimates, wacc=0.10):
    monkeypatch.setattr(specialist, "get_api_key_from_state", lambda *a, **k: "fake-key")
    monkeypatch.setattr(specialist, "get_financial_metrics", lambda *a, **k: metrics)
    monkeypatch.setattr(specialist, "get_market_cap", lambda *a, **k: 100_000_000_000)
    monkeypatch.setattr(specialist, "get_analyst_estimates", lambda *a, **k: estimates)
    monkeypatch.setattr(specialist, "get_wacc", lambda *a, **k: wacc)
    monkeypatch.setattr(specialist, "progress",
                        SimpleNamespace(update_status=lambda *a, **k: None))


def _metrics_obj(**overrides):
    base = dict(
        price_to_earnings_ratio=60.0,
        earnings_per_share=2.0,
        enterprise_value_to_ebitda_ratio=50.0,
        return_on_invested_capital=0.12,
        free_cash_flow_per_share=4.0,
        net_margin=0.20,
    )
    base.update(overrides)
    return [SimpleNamespace(**base)]


class TestAssembleIndustryBriefMerged:
    def test_happy_path_indicators_and_kpis(self, monkeypatch, merged_state):
        _patch_fmp(monkeypatch, metrics=_metrics_obj(),
                   estimates=[SimpleNamespace(eps_avg=4.0)])
        assert specialist.assemble_industry_brief_merged(merged_state) is True
        brief = merged_state["data"]["industry_brief"]
        # brief text + deterministic indicator block
        assert "Assertion bullet" in brief
        assert "Forward P/E: 30.0 (consensus EPS 4.00)" in brief   # 60*2 / 4
        assert "Trailing P/E: 60.0" in brief
        assert "EV/EBITDA: 50.0" in brief
        assert "ROIC 12.0% vs WACC 10.0%" in brief and "value creation" in brief
        assert "FCF Margin: 40.0%" in brief                        # (4/2)*0.20
        # KPI plumbing
        assert merged_state["data"]["sector_kpis"] == {"rule_of_40": 55.0}
        assert "rule_of_40" in brief                               # KPI table rendered
        ik = merged_state["data"]["industry_kpis"]
        assert ik["trailing_pe"] == 60.0 and ik["ev_ebitda"] == 50.0
        assert ik["roic"] == 0.12 and ik["rule_of_40"] == 55.0
        assert merged_state["data"]["industry_footnotes"] == []

    def test_returns_false_without_brief_section(self, monkeypatch, merged_state):
        merged_state["data"]["deep_research_sections"] = {"2a": "no brief"}
        _patch_fmp(monkeypatch, metrics=_metrics_obj(), estimates=[])
        assert specialist.assemble_industry_brief_merged(merged_state) is False
        assert "industry_brief" not in merged_state["data"]

    def test_financials_ev_ebitda_not_applicable(self, monkeypatch, merged_state):
        merged_state["data"]["sector"] = "Financials"
        _patch_fmp(monkeypatch, metrics=_metrics_obj(),
                   estimates=[SimpleNamespace(eps_avg=4.0)])
        assert specialist.assemble_industry_brief_merged(merged_state) is True
        brief = merged_state["data"]["industry_brief"]
        assert "EV/EBITDA: Not applicable" in brief
        assert "ev_ebitda" not in merged_state["data"]["industry_kpis"]

    def test_negative_earnings_and_missing_inputs(self, monkeypatch, merged_state):
        _patch_fmp(monkeypatch,
                   metrics=_metrics_obj(price_to_earnings_ratio=-5.0,
                                        return_on_invested_capital=None,
                                        free_cash_flow_per_share=None),
                   estimates=[])
        assert specialist.assemble_industry_brief_merged(merged_state) is True
        brief = merged_state["data"]["industry_brief"]
        assert "Trailing P/E: N/A — earnings are negative" in brief
        assert "Forward P/E: N/A — no forward consensus EPS" in brief
        assert "ROIC vs WACC: N/A" in brief
        assert "FCF Margin: N/A — insufficient data" in brief

    def test_metrics_fetch_failure_degrades_to_na(self, monkeypatch, merged_state):
        _patch_fmp(monkeypatch, metrics=[], estimates=[])
        assert specialist.assemble_industry_brief_merged(merged_state) is True
        brief = merged_state["data"]["industry_brief"]
        assert "Trailing P/E: N/A — not available" in brief

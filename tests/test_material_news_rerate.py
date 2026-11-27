"""Material news re-rate (owner, 2026-11-27).

A rating may not stand on research the freshness check flagged as out of date:
material news amends the reused research and reruns the extractors; results or
guidance news that moves no estimate gets one targeted search round; news that
does not reach the estimates puts the rating Under Review at zero weight; news
already in the valuation is not counted again; the report says what changed.
"""
from types import SimpleNamespace as NS

import pytest

from src.agents.industry import deep_research as dr
from src.memory import run_archive
from tests.test_m2_addendum_delta import _CACHED_ROW, _patch_cache_env


@pytest.fixture(autouse=True)
def _tmp_archive(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(run_archive, "DB_PATH", str(tmp_path / "run_archive.db"))
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    monkeypatch.setenv("PRE_2G_RESEARCH_REFRESH", "false")   # the fixture text predates section 2G
    monkeypatch.delenv("RESEARCH_FORCE_REUSE", raising=False)

_DELTA = {"material": True, "snippets": "13 Nov 2026: FY27 OpCo EBIT guidance raised to mid-to-high single digit.",
          "events": [{"headline": "H1 FY27 results; guidance raised", "date": "2026-11-13",
                      "relevance": "EBIT guidance up"}],
          "verdict": "guidance raised above the prior assumption"}


def _ge(g1):
    return {"estimates": {"base": {"revenue_growth_fy1": g1, "eps_fy1": 0.17}}}


class _FakeClient:
    def __init__(self, replies):
        self.calls, self._replies = [], list(replies)
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(text=self._replies.pop(0) if self._replies else "[2A] NO CHANGE")])


def _run(monkeypatch, *, prior_ge, fanout_ge, focus="", replies=None):
    _patch_cache_env(monkeypatch, _CACHED_ROW)
    persisted = {"dcf_calibration": {"wacc": 0.09}, "guidance_estimates": prior_ge}
    monkeypatch.setattr(run_archive, "get_extractor_outputs",
                        lambda t, h: {"results": persisted, "failures": []})
    client = _FakeClient(replies or ["[2A] Guidance raised to mid-to-high single digit EBIT growth (SGX, Nov 2026)",
                                     "[2A] OpCo EBIT +7% guided; revenue growth 5% (SGX, Nov 2026)"])
    monkeypatch.setattr(dr, "make_sdk_client", lambda **k: client)
    seq = list(fanout_ge)
    fanouts = []

    def _fanout(*a, **k):
        fanouts.append(a[2])
        return {"dcf_calibration": {"wacc": 0.09}, "guidance_estimates": seq.pop(0) if seq else fanout_ge[-1]}, []
    monkeypatch.setattr(dr, "_run_extractor_fanout", _fanout)
    monkeypatch.setattr(dr, "_build_news_supplement", lambda **k: "")
    focus_calls = []
    monkeypatch.setattr(dr, "_focus_search_snippets", lambda *a, **k: focus_calls.append(k) or focus)
    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-11-27", anthropic_key="k",
        model_name="qwen3.6-plus", raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
        freshness_delta=_DELTA)
    return out, client, fanouts, focus_calls


def test_material_news_amends_reused_research_and_reruns_extractors(monkeypatch):
    monkeypatch.delenv("MATERIAL_NEWS_AMEND", raising=False)
    out, client, fanouts, focus = _run(monkeypatch, prior_ge=_ge(0.02), fanout_ge=[_ge(0.05)])
    assert out["research_tier"] == "archive_news_delta"
    assert "DELTA UPDATE" in out["deep_research_sections"]["2a"]
    assert len(fanouts) == 1 and "DELTA UPDATE" in fanouts[0]["2a"]       # extractors saw the amended text
    mu = out["material_update"]
    assert mu["status"] == "amended" and mu["escalated"] is False and not focus
    assert mu["estimate_changes"] == [{"field": "revenue_growth_fy1", "prior": 0.02, "new": 0.05}]
    assert out["guidance_estimates"] == _ge(0.05)


def test_guidance_news_that_moves_nothing_gets_a_targeted_search(monkeypatch):
    out, client, fanouts, focus = _run(monkeypatch, prior_ge=_ge(0.02), fanout_ge=[_ge(0.02), _ge(0.05)],
                                       focus="SGX 13 Nov: OpCo EBIT guidance mid-to-high single digit; revenue +5%")
    assert len(focus) == 1 and len(fanouts) == 2 and len(client.calls) == 2
    assert "OpCo EBIT guidance" in client.calls[1]["messages"][0]["content"]   # focus results reach synthesis
    mu = out["material_update"]
    assert mu["escalated"] is True
    assert mu["estimate_changes"][0]["new"] == 0.05


def test_targeted_search_with_nothing_found_keeps_the_first_amendment(monkeypatch):
    out, client, fanouts, focus = _run(monkeypatch, prior_ge=_ge(0.02), fanout_ge=[_ge(0.02)], focus="")
    assert len(fanouts) == 1 and out["material_update"]["escalated"] is False
    assert out["material_update"]["estimate_changes"] == []


def test_kill_switch_restores_replay(monkeypatch):
    monkeypatch.setenv("MATERIAL_NEWS_AMEND", "false")
    out, client, fanouts, focus = _run(monkeypatch, prior_ge=_ge(0.02), fanout_ge=[_ge(0.05)])
    assert out["research_tier"] == "anthropic_web_cached" and not client.calls
    assert out["material_update"]["status"] == "not_reflected"


def test_non_material_news_keeps_the_fast_path(monkeypatch):
    _patch_cache_env(monkeypatch, _CACHED_ROW)
    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-11-27", anthropic_key="k",
        model_name="qwen3.6-plus", raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
        freshness_delta={"material": False, "events": [], "verdict": "nothing new"})
    assert out["research_tier"] == "anthropic_web_cached" and out["material_update"] is None


@pytest.mark.parametrize("prior,new,expect", [
    (None, _ge(0.05), None),                          # nothing to compare against
    (_ge(0.02), _ge(0.0202), []),                     # inside tolerance
    (_ge(0.02), {}, [{"field": "revenue_growth_fy1", "prior": 0.02, "new": None},
                     {"field": "eps_fy1", "prior": 0.17, "new": None}]),
])
def test_estimate_changes(prior, new, expect):
    assert dr._estimate_changes(prior, new) == expect


def test_focus_queries_name_each_event():
    qs = dr._focus_queries("Singtel (Z74.SI)", _DELTA, "2026-11-01")
    assert qs[0].startswith("Singtel (Z74.SI) H1 FY27 results; guidance raised 2026-11-13")


# ── rating layer ──────────────────────────────────────────────────────────────

def _view(**kw):
    from src.decisions.ratings import build_research_view
    return build_research_view(ticker="Z74.SI", sector="Telecom", price=3.9, price_as_of="2026-11-27",
                               target_12m=3.69, intrinsic_value=3.5, dps=0.17, **kw)


def test_unreflected_material_news_puts_the_rating_under_review():
    v = _view(material_unreflected={"events": [{"date": "2026-11-13", "headline": "Guidance raised"}]})
    assert v["under_review"] is True and v["rating_label"] == "Under Review"
    assert v["trade_action"] == "HOLD" and v["compliance"]["status"] == "under_review"
    assert "2026-11-13: Guidance raised" in v["compliance"]["notes"][0]
    assert "3.69 predates it" in v["compliance"]["notes"][0]
    assert _view()["material_unreflected"] is False


def _state(status, tier="qwen_web"):
    return {"data": {"material_update": ({"Z74.SI": {"status": status, "events": []}} if status else {}),
                     "research_tier": tier}}


@pytest.mark.parametrize("status,tier,unreflected", [
    ("amended", "archive_news_delta", False),
    ("fresh", "qwen_web", False),
    ("not_reflected", "anthropic_web_cached", True),
    (None, "none", True),                      # research produced no record (failed)
    ("amended", "knowledge_only", True),       # stale research cannot carry it
])
def test_material_unreflected(status, tier, unreflected):
    from src.agents.portfolio_manager import _material_unreflected
    r = _material_unreflected(_state(status, tier), "Z74.SI", _DELTA)
    assert bool(r) is unreflected
    if r:
        assert r["events"][0]["headline"] == "H1 FY27 results; guidance raised"
    assert _material_unreflected(_state(status, tier), "Z74.SI", {"material": False}) is None


def test_news_in_the_valuation_is_not_counted_twice():
    from src.agents.portfolio_manager import _news_shift
    delta = {"material": True, "events": [{"headline": "raised guidance"}], "verdict": ""}
    assert _news_shift(_state("amended"), "Z74.SI", delta) == (
        None, "material news already in the valuation (no extra one-step shift)")
    assert _news_shift(_state("not_reflected"), "Z74.SI", delta) == ("positive", None)


# ── disclosure ────────────────────────────────────────────────────────────────

def test_change_attribution_states_what_the_news_changed():
    from src.agents.pm.reader_text import build_change_attribution
    prior = {"run_at": "2026-11-26", "recap_json": {"price_at_run": 3.9, "price_target": 3.69,
                                                     "blended_iv": 3.4, "capture": 0.5,
                                                     "rating_label": "Underweight"}}
    mu = {"status": "amended", "events": [{"date": "2026-11-13", "headline": "Guidance raised"}],
          "estimate_changes": [{"field": "revenue_growth_fy1", "prior": 0.02, "new": 0.05},
                               {"field": "eps_fy1", "prior": 0.17, "new": 0.186}]}
    a = build_change_attribution(prior, spot=3.95, blended_iv=3.9, capture=0.5, target=3.92,
                                 rating_label="Neutral", sym="S$", material_update=mu)
    s = a["material_update_sentence"]
    assert s.startswith("This report incorporates material news since our previous report (2026-11-13: Guidance raised).")
    assert "Base-case estimates changed: FY+1 revenue growth 2.0% to 5.0%, FY+1 EPS S$0.17 to S$0.19." in s
    from src.agents.pm.reader_text import material_update_sentence
    added = material_update_sentence({"status": "amended", "events": [],
                                      "estimate_changes": [{"field": "revenue_growth_fy2", "prior": None, "new": 0.035}]})
    assert added.endswith("Base-case estimates changed: FY+2 revenue growth newly estimated at 3.5%.")
    assert a["sentence"].startswith(s) and "State the material-news sentence" in a["prompt_block"]
    unref = build_change_attribution(prior, spot=3.95, blended_iv=3.9, capture=0.5, target=3.92,
                                     rating_label="Under Review", sym="S$",
                                     material_update={"status": "not_reflected", "events": []})
    assert unref["material_update_sentence"].startswith("Rating under review")
    none = build_change_attribution(prior, spot=3.95, blended_iv=3.9, capture=0.5, target=3.92,
                                    rating_label="Neutral", sym="S$")
    assert none["material_update_sentence"] is None


def test_rating_change_set_by_a_rule_is_not_credited_to_the_news():
    """Live Z74.SI local run, 2026-11-27: the Neutral cap (SOTP 2.9x DCF) lifted Underweight to
    Neutral, and the writer credited the upgrade to the Xiaomi/GovTech news."""
    from src.agents.pm.reader_text import build_change_attribution, check_change_claims
    from src.agents.portfolio_manager import _rating_cause
    view = {"valuation_unreconciled": {"hi_leg": "SOTP / NAV (look-through)", "lo_leg": "DCF", "ratio": 2.9}}
    cause = _rating_cause(view)
    assert cause.startswith("the Neutral cap while the valuation is unreconciled (SOTP")
    prior = {"run_at": "2026-11-26", "recap_json": {"price_at_run": 4.3, "price_target": 3.75, "blended_iv": 3.2,
                                                     "capture": 0.5, "rating_label": "Underweight"}}
    mu = {"status": "amended", "estimate_changes": [],
          "events": [{"date": "2026-11-26", "headline": "Singtel and Xiaomi Partner for AI-Powered Smart Living"}]}
    a = build_change_attribution(prior, spot=4.3, blended_iv=2.8, capture=0.5, target=3.54, rating_label="Neutral",
                                 sym="S$", material_update=mu, rating_cause=cause)
    assert "The rating change reflects the Neutral cap" in a["sentence"]
    assert "Do NOT attribute the rating change to news" in a["prompt_block"]
    text = ("1. Upgraded from Underweight -- Singtel and Xiaomi Partner for AI-Powered Smart Living. "
            "These developments support a 12-month return of -13.4%.\n2. Industry: margins hold.")
    out, hits = check_change_claims(text, a)
    assert "Upgraded from Underweight" not in out and "These developments" not in out
    assert "The rating change reflects the Neutral cap" in out and "Industry: margins hold." in out and hits
    head, h = check_change_claims("Upgraded to Neutral as AI Partnerships Stabilize Margins; PT S$3.5.", a,
                                  replacement=a["headline_fallback"])
    assert head.startswith("Neutral: rating reflects the Neutral cap") and h
    # Without a rule-set rating the same sentence is the writer's to make.
    a2 = build_change_attribution(prior, spot=4.3, blended_iv=2.8, capture=0.5, target=3.54,
                                  rating_label="Neutral", sym="S$", material_update=mu)
    assert a2["rating_cause"] is None and check_change_claims(text, a2)[1] == []

"""D2 (owner, 2026-11-28): research freshness -- window back to the prior report, earnings trigger, 90-day reuse cap,
provenance disclosed."""
from datetime import datetime

from src.memory import freshness as fr

NOW = datetime(2026, 11, 28, 12, 0)


def test_window_reaches_back_to_the_prior_report_within_the_cap(monkeypatch):
    monkeypatch.delenv("FRESHNESS_MAX_LOOKBACK_DAYS", raising=False)
    monkeypatch.delenv("FRESHNESS_SEARCH_WINDOW_DAYS", raising=False)
    assert fr.search_since({"run_at": "2026-10-14T09:00"}, NOW) == "2026-10-14"       # 45 days back, not 3
    assert fr.search_since({"run_at": "2026-11-27"}, NOW) == "2026-11-27"             # a fresh report anchors
    assert fr.search_since({"run_at": "2026-01-01"}, NOW) == "2026-07-31"             # 120-day cap
    assert fr.search_since(None, NOW) == "2026-11-25"


def test_results_since_the_prior_report_force_the_amend_path(monkeypatch):
    monkeypatch.delenv("FRESHNESS_EARNINGS_TRIGGER", raising=False)
    d = {"material": False, "events": [], "verdict": "no material change"}
    out = fr.apply_earnings_trigger("MSFT", d, "2026-10-14", latest="2026-10-29")
    assert out["material"] is True and out["earnings_since_prior"] == "2026-10-29"
    assert out["events"][0]["headline"] == "Results reported on 2026-10-29"
    # results before the prior report, or none known: untouched
    d2 = {"material": False, "events": []}
    assert fr.apply_earnings_trigger("MSFT", d2, "2026-11-01", latest="2026-10-29")["material"] is False
    assert fr.apply_earnings_trigger("MSFT", {"material": False, "events": []}, "2026-11-01", latest=None)[
        "material"] is False
    monkeypatch.setenv("FRESHNESS_EARNINGS_TRIGGER", "false")
    assert fr.apply_earnings_trigger("MSFT", {"material": False}, "2026-10-14", latest="2026-10-29")["material"] is False


def test_an_existing_results_event_is_not_duplicated():
    d = {"material": True, "events": [{"headline": "Q1 results beat", "date": "2026-10-29"}]}
    out = fr.apply_earnings_trigger("MSFT", d, "2026-10-14", latest="2026-10-29")
    assert len(out["events"]) == 1


def test_reuse_is_capped_at_90_days(monkeypatch):
    from src.agents.industry import deep_research as dr
    monkeypatch.delenv("RESEARCH_MAX_AGE_DAYS", raising=False)
    assert dr._research_max_age_days() == 90
    monkeypatch.setenv("RESEARCH_MAX_AGE_DAYS", "60")
    assert dr._research_max_age_days() == 60
    import inspect
    assert "max_age_days=_research_max_age_days() if _force_reuse_enabled()" in inspect.getsource(dr)


def test_provenance_is_disclosed_on_the_research_view():
    from src.agents import portfolio_manager as pm
    state = {"data": {"research_as_of": "2026-09-30T10:00", "research_tier": "qwen_web", "end_date": "2026-11-28"}}
    p = pm._research_provenance(state, {"since": "2026-10-14", "checked_to": "2026-11-28",
                                        "earnings_since_prior": "2026-10-29"})
    assert p == {"research_as_of": "2026-09-30", "research_tier": "qwen_web", "news_from": "2026-10-14",
                 "news_to": "2026-11-28", "earnings_since_prior": "2026-10-29"}

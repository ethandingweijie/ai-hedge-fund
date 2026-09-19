"""B1 prediction ledger and A0 timing summary.

Every test drives the function whose record a later outcome job will read,
and checks the record it leaves -- never the source text that writes it.
"""
import json
from datetime import datetime, timedelta

import pytest

from src.agents.analysis import dcf_agent as d

_PROFILE = [
    {"name": "DCF", "weight": 0.5},
    {"name": "P/E", "weight": 0.3},
    {"name": "EV/EBITDA", "weight": 0.2},
]


def _reproduce(values: dict, breakdown: dict) -> float:
    return sum(e["weight"] * values[e["value_key"]]
               for e in breakdown["effective_weights"])


def _weight(breakdown: dict, method: str) -> float:
    return next(e["weight"] for e in breakdown["effective_weights"]
                if e["method"] == method)


# ── effective weights ───────────────────────────────────────────────────────

class TestEffectiveWeights:
    def test_weights_sum_to_one_and_reproduce_the_blend(self):
        values = {"DCF": 100.0, "P/E": 80.0, "EV/EBITDA": 90.0}
        iv, bd = d._blend_methods(_PROFILE, values, c_macro=0.07,
                                  forward_flags=[], dcf_tv_fraction=0.5)
        assert sum(e["weight"] for e in bd["effective_weights"]) == \
            pytest.approx(1.0, abs=1e-5)
        assert _reproduce(values, bd) == pytest.approx(iv, rel=1e-5)

    def test_a_method_with_no_value_carries_no_weight(self):
        values = {"DCF": 100.0, "P/E": None, "EV/EBITDA": 90.0}
        iv, bd = d._blend_methods(_PROFILE, values, 0.0, [], 0.5)
        assert "P/E" not in [e["method"] for e in bd["effective_weights"]]
        assert _weight(bd, "DCF") == pytest.approx(0.5 / 0.7, abs=1e-5)
        assert _reproduce(values, bd) == pytest.approx(iv, rel=1e-5)

    def test_gate_a_moves_weight_to_the_asset_floor(self):
        values = {"DCF": 100.0, "P/E": 80.0, "EV/EBITDA": 90.0, "P/BV": 60.0}
        iv, bd = d._blend_methods(_PROFILE, values, 0.0, [], 0.9)
        floor = [e for e in bd["effective_weights"]
                 if e["method"] == "P/BV (asset floor)"]
        assert len(floor) == 1 and floor[0]["bucket"] == "multi"
        assert floor[0]["weight"] == pytest.approx(
            0.5 * d._TV_DOMINANCE_REWEIGHT, abs=1e-5)
        assert _weight(bd, "DCF") == pytest.approx(
            0.5 * (1 - d._TV_DOMINANCE_REWEIGHT), abs=1e-5)
        assert _reproduce(values, bd) == pytest.approx(iv, rel=1e-5)

    def test_a_proxy_records_the_value_it_actually_used(self):
        profile = [{"name": "DCF", "weight": 0.6},
                   {"name": "NAV", "weight": 0.4, "implementable": False,
                    "proxy": "P/BV"}]
        values = {"DCF": 100.0, "P/BV": 70.0}
        iv, bd = d._blend_methods(profile, values, 0.0, [], 0.5)
        nav = next(e for e in bd["effective_weights"] if e["method"] == "NAV")
        assert nav["value_key"] == "P/BV"
        assert _reproduce(values, bd) == pytest.approx(iv, rel=1e-5)


# ── industry routing trace ──────────────────────────────────────────────────

class TestIndustryRoutingTrace:
    def test_unmapped_industry_says_so(self, monkeypatch):
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Nonexistent Industry")
        trace: dict = {}
        assert d._industry_routed_profile("ZZZZ", "Tech", trace=trace) is None
        assert trace == {"industry": "Nonexistent Industry",
                         "outcome": "unmapped"}

    def test_applied_route_names_the_profile(self, monkeypatch):
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Gold")
        trace: dict = {}
        got = d._industry_routed_profile("02259.HK", "Tech", trace=trace)
        assert got is not None
        assert trace["outcome"] == "routed"
        assert (trace["routed_sector"], trace["routed_profile"]) == got[:2]

    def test_a_declined_anchor_records_why(self, monkeypatch):
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
        target = None
        for s, profiles in INDUSTRY_VALUATION_PROFILES.items():
            for p, data in profiles.items():
                methods = [m for m in (data.get("methods") or [])
                           if isinstance(m, dict)]
                anchor = next((m for m in methods if m.get("anchor")), None)
                if (anchor and not anchor.get("implementable")
                        and anchor.get("name") not in d._LOOKTHROUGH_ANCHORS):
                    target = (s, p, anchor["name"])
                    break
            if target:
                break
        if not target:
            pytest.skip("no profile with a non-implementable, non-look-through anchor")
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Anything")
        monkeypatch.setattr("src.data.industry_profile_map.profile_for_ticker",
                            lambda t, i: target[:2])
        trace: dict = {}
        assert d._industry_routed_profile("ZZZZ", "Tech", trace=trace) is None
        assert trace["outcome"] == "declined_anchor_not_implementable"
        assert trace["anchor"] == target[2]

    def test_a_lookup_failure_is_recorded_not_raised(self, monkeypatch):
        def _boom(t, api_key=None):
            raise RuntimeError("FMP down")
        monkeypatch.setattr("src.tools.api.get_company_industry", _boom)
        trace: dict = {}
        assert d._industry_routed_profile("AAPL", "Tech", trace=trace) is None
        assert trace == {"outcome": "error", "error": "RuntimeError"}


# ── router profile source ───────────────────────────────────────────────────

class _Choice:
    def __init__(self, name):
        self.profile_name = name


class TestRouterProfileSource:
    @pytest.fixture
    def sr(self, monkeypatch):
        from src.agents.routing import strategic_router as sr
        monkeypatch.setattr(
            "src.data.sector_kpi_framework.SECTOR_KPI_FRAMEWORK",
            {"Mature SaaS": {"sector": "Tech"},
             "Growth SaaS": {"sector": "Tech"}})
        monkeypatch.setattr(sr, "_fetch_company_name",
                            lambda t, api_key=None: "Test Co")
        return sr

    def _run(self, sr, state, sector="Tech"):
        sr._classify_unknown_profiles_with_llm(
            state, ["AAA"], {"AAA": sector}, "AAA", None)
        return state["data"]

    def test_llm_pick_is_recorded_as_llm(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "call_llm", lambda **kw: _Choice("Growth SaaS"))
        data = self._run(sr, {"data": {}})
        assert data["profile_names"]["AAA"] == "Growth SaaS"
        assert data["profile_sources"] == {"AAA": "router_llm"}

    def test_an_unusable_llm_answer_is_recorded_as_default(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "call_llm", lambda **kw: _Choice("no such profile"))
        data = self._run(sr, {"data": {}})
        assert data["profile_sources"] == {"AAA": "router_sector_default"}

    def test_a_sector_without_candidates_is_recorded_as_default(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "call_llm", lambda **kw: pytest.fail("no LLM call expected"))
        data = self._run(sr, {"data": {}}, sector="Energy")
        assert data["profile_names"]["AAA"] == sr._SECTOR_PROFILE_DEFAULT["Energy"]
        assert data["profile_sources"] == {"AAA": "router_sector_default"}

    def test_a_lookup_profile_keeps_its_source(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "call_llm", lambda **kw: pytest.fail("no LLM call expected"))
        state = {"data": {"profile_names": {"AAA": "Mature SaaS"},
                          "profile_sources": {"AAA": "router_lookup"}}}
        data = self._run(sr, state)
        assert data["profile_sources"] == {"AAA": "router_lookup"}


# ── parameter version ───────────────────────────────────────────────────────

class TestParamVersion:
    def test_is_stable_for_unchanged_parameters(self):
        v = d._param_version()
        assert v.startswith("constants-") and v == d._param_version()

    def test_moves_when_a_scalar_moves(self, monkeypatch):
        before = d._param_version()
        monkeypatch.setattr(d, "_SOTP_ANALYST_BLEND_WEIGHT",
                            d._SOTP_ANALYST_BLEND_WEIGHT + 1.0)
        assert d._param_version() != before

    def test_moves_when_a_profile_weight_moves(self, monkeypatch):
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
        before = d._param_version()
        method = next(m for profiles in INDUSTRY_VALUATION_PROFILES.values()
                      for data in profiles.values()
                      for m in (data.get("methods") or [])
                      if isinstance(m, dict) and "weight" in m)
        monkeypatch.setitem(method, "weight", method["weight"] + 0.01)
        assert d._param_version() != before


# ── consensus at run ────────────────────────────────────────────────────────

class TestConsensusAtRun:
    def test_us_consensus_is_recorded_with_its_timestamp(self):
        rec = d._consensus_at_run(
            "MSFT", {"consensus": 500.0, "median": 510.0, "low": 400.0,
                     "high": 600.0})
        assert rec["status"] == "recorded" and rec["target"] == 500.0
        assert rec["currency"] == "USD" and rec["fetched_at"]

    def test_missing_consensus_reads_as_missing_not_zero(self):
        rec = d._consensus_at_run("MSFT", None)
        assert rec["status"] == "unavailable" and "target" not in rec

    @pytest.mark.parametrize("ticker", ["0700.HK", "D05.SI"])
    def test_hk_sg_are_deferred_without_a_live_scrape(self, ticker, monkeypatch):
        monkeypatch.setattr("src.tools.target_price.get_consensus_target",
                            lambda *a, **k: pytest.fail("scraped on the live path"))
        assert d._consensus_at_run(ticker, None)["status"] == "deferred"


# ── cache copies ────────────────────────────────────────────────────────────

class TestCacheCopy:
    def test_a_replayed_entry_is_marked_and_keeps_its_original_date(self):
        from src.pipeline import _mark_cache_copy
        fresh = {"profile": "X", "is_cache_copy": False}
        first = _mark_cache_copy(fresh, "2026-09-01T10:00:00")
        assert first["is_cache_copy"] is True
        assert first["cache_source_run_at"] == "2026-09-01T10:00:00"
        assert fresh["is_cache_copy"] is False
        second = _mark_cache_copy(first, "2026-09-10T10:00:00")
        assert second["cache_source_run_at"] == "2026-09-01T10:00:00"

    def test_an_empty_placeholder_stays_falsy(self):
        from src.pipeline import _mark_cache_copy
        assert _mark_cache_copy({}, "x") == {}
        assert _mark_cache_copy(None, "x") is None


# ── run timing summary ──────────────────────────────────────────────────────

def _run(research_s: float, start=datetime(2026, 9, 14, 10, 0, 0)) -> str:
    def at(sec):
        return (start + timedelta(seconds=sec)).isoformat(timespec="seconds")
    entries = [
        # two front-block legs overlapping over the same 20 s window
        {"phase": "1_macro_regime", "started_at": at(0), "finished_at": at(20),
         "duration_s": 20.0},
        {"phase": "2_strategic_router", "started_at": at(0), "finished_at": at(20),
         "duration_s": 20.0},
        {"phase": "3_deep_research_router", "started_at": at(20),
         "finished_at": at(20 + research_s), "duration_s": research_s},
    ]
    return json.dumps(entries)


class TestRunTiming:
    def test_runs_are_split_by_research_path(self):
        from app.backend.services.run_timing import summarize
        out = summarize([_run(30), _run(40), _run(600)])
        assert out["n_runs"] == 3
        assert out["covered"]["n_runs"] == 2 and out["full"]["n_runs"] == 1

    def test_percentiles_per_phase(self):
        from app.backend.services.run_timing import summarize
        out = summarize([_run(s) for s in (10, 20, 30, 40, 50)])
        research = out["covered"]["phases"]["3_deep_research_router"]
        assert (research["n"], research["p50"], research["p90"]) == (5, 30.0, 46.0)

    def test_wall_time_does_not_double_count_parallel_legs(self):
        from app.backend.services.run_timing import summarize
        out = summarize([_run(30)])
        # 20 s parallel front block + 30 s research = 50 s, not 70 s
        assert out["covered"]["wall_p50"] == 50.0

    def test_unusable_rows_are_skipped(self):
        from app.backend.services.run_timing import summarize
        assert summarize([None, "", "not json", "{}", []])["n_runs"] == 0

    def test_a_run_without_research_is_unknown_not_guessed(self):
        from app.backend.services.run_timing import summarize
        entries = json.loads(_run(30))[:2]
        assert summarize([entries])["unknown"]["n_runs"] == 1

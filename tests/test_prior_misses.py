"""Loop 7 of the self-learning layer: profile-level lessons and prior-miss retrieval."""
import inspect
import json
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.data import db as _db
    from src.memory import agent_lessons as al
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "lessons.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k in ("AGENT_LESSONS", "PROFILE_LESSONS", "PRIOR_MISSES", "RUN_FEATURES_DISABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, rf):
        m._tables_ready_key = None
    al._tables_ready_key = None
    _db.close_all_connections()
    yield
    for m in (vo, rf):
        m._tables_ready_key = None
    al._tables_ready_key = None
    _db.close_all_connections()


from src.memory import agent_lessons as al  # noqa: E402
from src.memory import run_archive  # noqa: E402
from src.memory import run_features as rf  # noqa: E402
from src.memory import valuation_outcomes as vo  # noqa: E402

TODAY = date(2027, 9, 14)


def _run(run_id, ticker, *, iv=100.0, pt=110.0, run_at="2026-01-01T10:00:00", price=90.0,
         methods=None, profile="Mature SaaS", **flags):
    """An archived run with its run_features row (so the join has both sides)."""
    methods = methods or {"DCF": 120.0, "Forward P/E": 100.0}
    dcf = {"base": {"intrinsic_value": iv, "method_iv_table": methods,
                    "effective_weights": [{"method": m, "value_key": m, "weight": 1.0 / len(methods)} for m in methods]},
           "bear": {"intrinsic_value": iv * 0.8}, "bull": {"intrinsic_value": iv * 1.3},
           "profile": profile, "is_cache_copy": False,
           "consensus_at_run": {"status": "recorded", "target": 120.0, "fetched_at": run_at}}
    run_archive._exec("INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector) VALUES (?, ?, ?, ?, ?)",
                      [run_id, run_at, run_at[:10], json.dumps([ticker]), "Tech"])
    run_archive._exec("INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, dcf_range_json, "
                      "scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
                      [run_id, ticker, price, pt, json.dumps(dcf), json.dumps({"12m_price_target": pt})])
    rf.record(run_id, ticker, dcf, {"12m_price_target": pt}, run_at=run_at, sector="Tech",
              price_at_run=price, pm_target=pt)
    if flags:
        from src.data import db
        sets = ", ".join(f"{k} = ?" for k in flags)
        db.execute(f"UPDATE run_features SET {sets} WHERE feature_key = ?", [*flags.values(), f"{run_id}|{ticker}"])


def _label(series):
    """Mature the price labels through the agent's own sweep (no consensus scraping)."""
    vo.score_matured(today=TODAY, closes_fn=lambda t, s, e: [(d, c) for d, c in series.get(t, []) if s <= d <= e],
                     consensus_fn=lambda t: None)


# three names in the cell, every target 110 against prices that landed at 70-80: a ~45% miss
CLOSES = {t: [("2027-01-04", p)] for t, p in (("AAA", 75.0), ("BBB", 70.0), ("CCC", 80.0))}


class TestProfileGap:
    def test_a_cell_that_keeps_missing_is_a_gap_and_consensus_is_never_the_label(self):
        for t in ("AAA", "BBB", "CCC"):
            _run(f"r-{t}", t)
        assert al.detect_profile_gap("US", "Mature SaaS") is None       # nothing matured yet
        _label({})                                                       # consensus labels only
        assert al.detect_profile_gap("US", "Mature SaaS") is None
        _label(CLOSES)                                                   # px_365d matured
        gap = al.detect_profile_gap("US", "Mature SaaS")
        assert gap and gap["n"] == 3 and gap["median_miss_pct"] > 25
        assert "median target miss" in gap["gap_reason"]
        assert gap["worst"][0]["ticker"] == "BBB" and gap["worst"][0]["horizon"] == "px_365d"
        assert gap["cause_shares"]                                       # B3 attribution attached
        assert al.detect_profile_gap("HK", "Mature SaaS") is None

    def test_user_unrated_and_cache_copy_runs_are_excluded_and_the_floor_holds(self):
        _run("r-AAA", "AAA"); _run("r-BBB", "BBB", override_carried=1); _run("r-CCC", "CCC", unrated=1)
        _label(CLOSES)
        assert al.detect_profile_gap("US", "Mature SaaS") is None       # one eligible run < 3

    def test_an_accurate_cell_is_no_gap(self):
        for t in ("AAA", "BBB", "CCC"):
            _run(f"r-{t}", t, pt=76.0, iv=76.0, methods={"DCF": 60.0, "Forward P/E": 92.0})   # legs straddle every label
        _label(CLOSES)
        assert al.detect_profile_gap("US", "Mature SaaS") is None


class TestProfileLessons:
    def _gap_cell(self):
        for t in ("AAA", "BBB", "CCC"):
            _run(f"r-{t}", t)
        _label(CLOSES)

    def test_lessons_are_profile_scoped_capped_apart_and_merged_on_read(self, monkeypatch):
        self._gap_cell()
        monkeypatch.setattr(al, "_post_mortem", lambda human, who: [
            {"agent_key": "dcf_engine", "lesson": "Mature SaaS DCFs overstate terminal margins.", "general": False}])
        out = al.maybe_generate_profile_lessons("US", "Mature SaaS")
        assert len(out) == 1 and out[0]["general"] is True
        rows = al.list_lessons("dcf_engine")
        assert rows[0]["scope"] == "profile" and rows[0]["scope_key"] == "US|Mature SaaS" and rows[0]["ticker"] is None
        # the cooldown: no second LLM call for 28 days
        monkeypatch.setattr(al, "_post_mortem", lambda human, who: (_ for _ in ()).throw(AssertionError("called")))
        assert al.maybe_generate_profile_lessons("US", "Mature SaaS") == []
        # read side: ticker lessons alone by default, the cell's profile lessons when asked
        al.save_lessons([{"agent_key": "dcf_engine", "lesson": "ticker-scope lesson", "general": True}],
                        {"run_id": "x", "ticker": "AAA"})
        assert al.get_active_lessons("dcf_engine") == ["ticker-scope lesson"]
        assert set(al.get_active_lessons("dcf_engine", profile="Mature SaaS", market="US")) == {
            "ticker-scope lesson", "Mature SaaS DCFs overstate terminal margins."}
        assert al.get_active_lessons("dcf_engine", profile="Other", market="US") == ["ticker-scope lesson"]
        # caps are per scope: eight profile lessons never evict the ticker one
        al.save_lessons([{"agent_key": "dcf_engine", "lesson": f"profile lesson {i}", "general": True} for i in range(8)],
                        {"run_id": None, "ticker": None}, scope="profile", scope_key="US|Mature SaaS")
        active = al.list_lessons("dcf_engine")
        assert sum(1 for r in active if r["scope"] == "profile") == 6
        assert any(r["lesson"] == "ticker-scope lesson" for r in active)

    def test_switches_and_no_gap_mean_no_llm(self, monkeypatch):
        self._gap_cell()
        monkeypatch.setattr(al, "_post_mortem", lambda human, who: (_ for _ in ()).throw(AssertionError("called")))
        monkeypatch.setenv("PROFILE_LESSONS", "false")
        assert al.maybe_generate_profile_lessons("US", "Mature SaaS") == []
        assert al.get_active_lessons("dcf_engine", profile="Mature SaaS", market="US") == []
        monkeypatch.delenv("PROFILE_LESSONS")
        assert al.maybe_generate_profile_lessons("US", "Nothing Here") == []

    def test_the_profile_prompt_names_the_cell_and_its_worst_misses(self, monkeypatch):
        self._gap_cell()
        seen = {}
        monkeypatch.setattr(al, "_post_mortem", lambda human, who: seen.update(human=human, who=who) or [])
        al.distill_profile_lessons(al.detect_profile_gap("US", "Mature SaaS"))
        assert "Profile: Mature SaaS (US market)" in seen["human"] and "BBB" in seen["human"]
        assert "THIS PROFILE as a class" in seen["human"] and seen["who"] == "US|Mature SaaS"


class TestPriorMisses:
    def test_ordered_by_miss_capped_at_k_and_off_by_switch(self, monkeypatch):
        for t in ("AAA", "BBB", "CCC"):
            _run(f"r-{t}", t)
        _run("r-USER", "USER", override_carried=1)
        assert al.retrieve_prior_misses("Mature SaaS", "US") == []
        _label({**CLOSES, "USER": [("2027-01-04", 50.0)]})
        lines = al.retrieve_prior_misses("Mature SaaS", "US")
        assert len(lines) == 3 and lines[0].startswith("BBB 2026-01-01: target 110.00 vs 70.00 at px_365d (+57%)")
        assert "cause" in lines[0] and not any(l.startswith("USER") for l in lines)
        assert len(al.retrieve_prior_misses("Mature SaaS", "US", k=2)) == 2
        assert al.retrieve_prior_misses(None, "US") == []
        monkeypatch.setenv("PRIOR_MISSES", "off")
        assert al.retrieve_prior_misses("Mature SaaS", "US") == []

    def test_a_legacy_table_gains_the_scope_columns(self):
        from src.data import db
        db.execute("CREATE TABLE agent_lessons (lesson_id TEXT PRIMARY KEY, lesson_hash TEXT NOT NULL, "
                   "agent_key TEXT NOT NULL, ticker TEXT, run_id TEXT, lesson TEXT NOT NULL, evidence_json TEXT, "
                   "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(agent_key, lesson_hash))")
        db.execute("INSERT INTO agent_lessons (lesson_id, lesson_hash, agent_key, lesson, created_at) "
                   "VALUES ('l1', 'h1', 'dcf_engine', 'old lesson', '2026-01-01T00:00:00')")
        al._tables_ready_key = None
        assert al.get_active_lessons("dcf_engine") == ["old lesson"]      # legacy rows are ticker-scope
        assert al.list_lessons("dcf_engine")[0]["scope"] == "ticker"


class TestWiring:
    def test_the_three_hooks_and_the_guard_are_in_place(self):
        from app.backend.database import schema_guard
        from src.agents.industry import deep_research
        from src.agents import portfolio_manager
        import src.pipeline as pipeline
        assert "retrieve_prior_misses" in inspect.getsource(deep_research._extract_dcf_calibration)
        assert 'get_active_lessons("dcf_engine", profile=_cell_profile, market=_cell_market)' in inspect.getsource(deep_research._extract_dcf_calibration)
        assert "Prior misses in this profile" in inspect.getsource(portfolio_manager._forward_estimates_block)
        assert "maybe_generate_profile_lessons" in inspect.getsource(pipeline)
        assert schema_guard.ensure_agent_lessons_scope in (ensure_all_guards := _guards(schema_guard))


def _guards(schema_guard):
    src = inspect.getsource(schema_guard.ensure_all)
    return [getattr(schema_guard, n) for n in ("ensure_user_extensions", "ensure_api_key_user_scope",
                                               "ensure_calibration_family", "ensure_agent_lessons_scope")
            if n in src]

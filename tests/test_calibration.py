"""B4/B5: the loader is inert until promotion, the fitter only moves what the
holdout supports, and a proposal reaches promotion only through backtest and
shadow."""
import json
import math
from datetime import date, timedelta

import pytest

from src.memory import calibration as cal
from src.memory import calibration_fit as cf

PROFILES = {"Tech": {"P": {"methods": [{"name": "A", "weight": 0.5},
                                       {"name": "B", "weight": 0.5}]}}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "cal.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VALUATION_CALIBRATION_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    monkeypatch.setattr("src.data.sector_profiles.INDUSTRY_VALUATION_PROFILES", PROFILES)
    vo._tables_ready_key = None
    cf._tables_ready_key = None
    cal.clear_cache()
    yield
    cal.clear_cache()


def _store(version_id, status, params, created="2026-01-01", promoted=None):
    from src.data import db
    cf._ensure_tables()
    db.execute("INSERT INTO calibration_versions (version_id, created_at, horizon, status, "
               "params_json, promoted_at) VALUES (?, ?, ?, ?, ?, ?)",
               [version_id, created, "px_90d", status, json.dumps(params), promoted])


PARAMS = {"profile_weights": {"Tech|P": {"A": 0.6, "B": 0.4}},
          "market_iv_multiplier": {"US": 0.9}}


# ── loader ──────────────────────────────────────────────────────────────────

class TestLoader:
    def test_no_table_means_nothing_active_and_hooks_are_identity(self):
        profile = PROFILES["Tech"]["P"]
        assert cal.active_version() is None
        assert cal.apply_profile_weights(profile, "Tech", "P") is profile
        assert cal.iv_multiplier("AAPL") is None

    def test_a_shadow_proposal_is_not_active(self):
        _store("v1", "shadow", PARAMS)
        assert cal.active_version() is None

    def test_an_active_version_applies_copy_on_write(self):
        _store("v1", "active", PARAMS, promoted="2026-02-01")
        active = cal.active_version()
        assert active["version_id"] == "v1"
        profile = PROFILES["Tech"]["P"]
        out = cal.apply_profile_weights(profile, "Tech", "P")
        assert [m["weight"] for m in out["methods"]] == [0.6, 0.4]
        assert [m["weight"] for m in profile["methods"]] == [0.5, 0.5]    # untouched
        assert cal.apply_profile_weights(profile, "Tech", "Other") is profile
        assert cal.iv_multiplier("AAPL") == 0.9
        assert cal.iv_multiplier("0700.HK") is None

    def test_the_kill_switch_wins(self, monkeypatch):
        _store("v1", "active", PARAMS, promoted="2026-02-01")
        monkeypatch.setenv("VALUATION_CALIBRATION_DISABLED", "true")
        assert cal.active_version() is None

    def test_the_answer_is_cached(self, monkeypatch):
        _store("v1", "active", PARAMS, promoted="2026-02-01")
        assert cal.active_version()["version_id"] == "v1"
        monkeypatch.setattr(cal._db, "query_one",
                            lambda *a, **k: pytest.fail("queried again inside the TTL"))
        assert cal.active_version()["version_id"] == "v1"

    def test_a_unit_multiplier_is_no_multiplier(self):
        assert cal.iv_multiplier("AAPL", active={"version_id": "v", "params": {
            "market_iv_multiplier": {"US": 1.0}}}) is None


# ── fitter ──────────────────────────────────────────────────────────────────

def _row(i, *, a, b, label, day, market="US", horizon=90):
    rd = date(2026, 1, 1) + timedelta(days=day)
    live = 0.5 * a + 0.5 * b
    return {"run_id": f"r{i}", "ticker": f"T{i}", "run_date": rd,
            "label_date": rd + timedelta(days=horizon), "label_value": label,
            "base_iv": live, "bear_iv": live * 0.7, "bull_iv": live * 1.3,
            "market": market, "profile": "P",
            "dcf": {"profile": "P",
                    "routing_trace": {"final_sector": "Tech", "final_profile": "P"},
                    "base": {"method_iv_table": {"A": a, "B": b}}}}


def _a_is_right(n=40, spread=2):
    """Method A lands on the label; B is 2x. Deterministic scatter."""
    rows = []
    for i in range(n):
        label = 100.0 + i
        wob = math.exp(0.05 * math.sin(i))
        rows.append(_row(i, a=label * wob, b=2 * label * wob, label=label, day=i * spread))
    return rows


def _both_biased(n=40, bias=1.5):
    rows = []
    for i in range(n):
        label = 100.0 + i
        wob = math.exp(0.02 * math.sin(i))
        rows.append(_row(i, a=label * bias * wob, b=label * bias / wob, label=label, day=i * 2))
    return rows


class TestFitter:
    def test_simplex_projection(self):
        w = cf._project_simplex([0.9, 0.4, -0.2])
        assert sum(w) == pytest.approx(1.0) and min(w) >= 0

    def test_weights_move_toward_the_right_method_but_only_by_the_cap(self):
        samples = [(r["base_iv"], r["label_value"], [r["dcf"]["base"]["method_iv_table"]["A"],
                                                     r["dcf"]["base"]["method_iv_table"]["B"]],
                    r["run_date"]) for r in _a_is_right()]
        w = cf.fit_cell_weights(samples, [0.5, 0.5])
        assert w == pytest.approx([0.6, 0.4], abs=1e-6)

    def test_a_cell_with_a_holdout_gain_is_proposed(self):
        prop = cf.propose(_a_is_right(), horizon="px_90d")
        assert prop["cells"]["Tech|P"]["status"] == "proposed"
        assert prop["params"]["profile_weights"]["Tech|P"] == pytest.approx({"A": 0.6, "B": 0.4})

    def test_too_few_runs_proposes_nothing(self):
        prop = cf.propose(_a_is_right(n=15), horizon="px_90d")
        assert prop["cells"]["Tech|P"]["status"] == "insufficient_data"
        assert prop["params"] == {"profile_weights": {}, "market_iv_multiplier": {}}

    def test_a_shared_bias_goes_to_the_market_multiplier_not_the_weights(self):
        prop = cf.propose(_both_biased(), horizon="px_90d")
        assert "Tech|P" not in prop["params"]["profile_weights"]
        k = prop["params"]["market_iv_multiplier"]["US"]
        assert k == pytest.approx(math.exp(-cf.LOG_BIAS_CAP), rel=1e-6)   # capped

    def test_an_ambiguous_profile_name_is_not_assigned_a_cell(self, monkeypatch):
        monkeypatch.setattr("src.data.sector_profiles.INDUSTRY_VALUATION_PROFILES",
                            {"Tech": {"P": {}}, "Consumer": {"P": {}}})
        assert cf._cell_of({"dcf": {"profile": "P"}}) is None

    def test_predictor_applies_both_and_declines_when_nothing_applies(self):
        predict = cf.predictor(PARAMS)
        row = _row(0, a=100.0, b=200.0, label=100.0, day=0)
        expected = 150.0 * ((0.6 * 100 + 0.4 * 200) / 150.0) * 0.9
        assert predict(row) == pytest.approx(expected)
        other = dict(row, market="HK", dcf={"profile": "Q"})
        assert predict(other) is None


# ── recording + shadow ──────────────────────────────────────────────────────

class TestRecording:
    def test_no_history_is_reported_not_invented(self, monkeypatch):
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: [])
        rep = cf.fit_and_record(today=date(2026, 9, 14))
        assert rep["status"] == "insufficient_data"

    def test_a_passing_proposal_enters_shadow_and_supersedes_the_last(self, monkeypatch):
        rows = _a_is_right()
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: rows if h == "px_90d" else [])
        monkeypatch.setattr(cf.wf, "walk_forward",
                            lambda rs, fit, **k: {"verdict": {"passed": True, "reasons": []}})
        _store("old", "shadow", PARAMS)
        rep = cf.fit_and_record(today=date(2026, 9, 14))
        assert rep["status"] == "shadow" and rep["horizon"] == "px_90d"
        statuses = {v["version_id"]: v["status"] for v in cf.list_versions()}
        assert statuses["old"] == "superseded"
        assert statuses[rep["version_id"]] == "shadow"

    def test_a_failing_backtest_is_stored_as_rejected_with_reasons(self, monkeypatch):
        rows = _a_is_right()
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: rows if h == "px_90d" else [])
        monkeypatch.setattr(cf.wf, "walk_forward", lambda rs, fit, **k: {
            "verdict": {"passed": False, "reasons": ["improved in 25% of folds"]}})
        rep = cf.fit_and_record(today=date(2026, 9, 14))
        assert rep["status"] == "rejected"
        assert {v["version_id"]: v["status"] for v in cf.list_versions()}[rep["version_id"]] == "rejected"

    def test_leakage_in_the_backtest_rejects(self, monkeypatch):
        rows = _a_is_right()
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: rows if h == "px_90d" else [])

        def leak(rs, fit, **k):
            raise cf.wf.LeakageError("planted")
        monkeypatch.setattr(cf.wf, "walk_forward", leak)
        rep = cf.fit_and_record(today=date(2026, 9, 14))
        assert rep["status"] == "rejected"
        assert "leakage" in rep["backtest_verdict"]["reasons"][0]

    def test_write_false_stores_nothing(self, monkeypatch):
        rows = _a_is_right()
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: rows if h == "px_90d" else [])
        monkeypatch.setattr(cf.wf, "walk_forward",
                            lambda rs, fit, **k: {"verdict": {"passed": True, "reasons": []}})
        cf.fit_and_record(today=date(2026, 9, 14), write=False)
        assert cf.list_versions() == []

    def test_weekly_gate(self):
        today = date(2026, 9, 14)
        assert cf.fit_ran_this_week(today) is False
        cf.mark_fit_run({"status": "no_change"}, today)
        assert cf.fit_ran_this_week(today) is True
        assert cf.fit_ran_this_week(today + timedelta(days=7)) is False


class TestShadow:
    def _setup(self, monkeypatch, n=30, after="2026-02-01"):
        _store("v1", "shadow", {"profile_weights": {"Tech|P": {"A": 0.6, "B": 0.4}},
                                "market_iv_multiplier": {}}, created="2026-01-15")
        start = (date.fromisoformat(after) - date(2026, 1, 1)).days
        rows = [dict(r) for r in _a_is_right(n=n)]
        for i, r in enumerate(rows):
            r["run_date"] = date(2026, 1, 1) + timedelta(days=start + i)
        monkeypatch.setattr(cf.wf, "load_rows", lambda h: rows if h == "px_90d" else [])

    def test_eligible_after_enough_time_runs_and_improvement(self, monkeypatch):
        self._setup(monkeypatch)
        rep = cf.shadow_report("v1", today=date(2026, 3, 15))
        assert rep["eligible_for_promotion"], rep["reasons"]
        h = rep["horizons"]["px_90d"]
        assert h["cand_miss_pct"] < h["live_miss_pct"]

    def test_too_soon_is_not_eligible(self, monkeypatch):
        self._setup(monkeypatch)
        rep = cf.shadow_report("v1", today=date(2026, 1, 30))
        assert not rep["eligible_for_promotion"]
        assert any("day(s) in shadow" in r for r in rep["reasons"])

    def test_too_few_runs_in_a_touched_cell_is_not_eligible(self, monkeypatch):
        self._setup(monkeypatch, n=10)
        rep = cf.shadow_report("v1", today=date(2026, 3, 15))
        assert any(r.startswith("Tech|P:") for r in rep["reasons"])

    def test_runs_before_the_proposal_do_not_count(self, monkeypatch):
        self._setup(monkeypatch, after="2025-12-01")   # all 30 runs predate it
        rep = cf.shadow_report("v1", today=date(2026, 3, 15))
        assert rep["horizons"]["px_90d"]["n_touched"] < 30

    def test_unknown_version(self):
        cf._ensure_tables()
        with pytest.raises(KeyError):
            cf.shadow_report("nope")

"""B6: promotion only through eligibility, a frozen cohort, rollback, the live
canary, diagnostics a person can read, and routes only an admin can reach."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.memory import calibration as cal
from src.memory import calibration_fit as cf
from src.memory import calibration_review as review

PROFILES = {"Tech": {"P": {"methods": [{"name": "A", "weight": 0.5},
                                       {"name": "B", "weight": 0.5}]}}}
PARAMS = {"profile_weights": {"Tech|P": {"A": 0.6, "B": 0.4}},
          "market_iv_multiplier": {"US": 1.1}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "review.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VALUATION_CALIBRATION_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    monkeypatch.setattr("src.data.sector_profiles.INDUSTRY_VALUATION_PROFILES", PROFILES)
    vo._tables_ready_key = None
    cf._tables_ready_key = None
    review._tables_ready_key = None
    cal.clear_cache()
    yield
    cal.clear_cache()


def _store(vid, status, params=PARAMS, created="2026-01-01", promoted=None):
    from src.data import db
    review._ensure_tables()
    db.execute("INSERT INTO calibration_versions (version_id, created_at, horizon, status, "
               "params_json, fit_json, backtest_json, promoted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               [vid, created, "px_90d", status, json.dumps(params), "{}",
                json.dumps({"verdict": {"passed": True, "reasons": []}}), promoted])


def _statuses():
    return {v["version_id"]: v["status"] for v in cf.list_versions()}


def _eligible(monkeypatch, ok=True, reasons=()):
    monkeypatch.setattr(cf, "shadow_report", lambda vid, today=None: {
        "eligible_for_promotion": ok, "reasons": list(reasons),
        "days_in_shadow": 30, "horizons": {}})


def _seed_run(run_id, ticker, run_at, *, iv, a, b):
    from src.memory import run_archive as ra
    ra._exec("INSERT INTO runs (run_id, run_at, analysis_date, tickers) VALUES (?, ?, ?, ?)",
             [run_id, run_at, run_at[:10], json.dumps([ticker])])
    dcf = {"base": {"intrinsic_value": iv, "method_iv_table": {"A": a, "B": b}},
           "profile": "P", "routing_trace": {"final_sector": "Tech", "final_profile": "P"}}
    ra._exec("INSERT INTO ticker_signals (run_id, ticker, dcf_range_json) VALUES (?, ?, ?)",
             [run_id, ticker, json.dumps(dcf)])


class TestWording:
    def test_changes_read_in_plain_english(self):
        lines = review.describe_params(PARAMS)
        assert "Raise US intrinsic values by 10.0%" in lines
        assert "Re-weight Tech / P: A 0.50 → 0.60, B 0.50 → 0.40" in lines

    def test_a_multiplier_below_one_lowers(self):
        assert review.describe_params({"market_iv_multiplier": {"HK": 0.9}}) == [
            "Lower HK intrinsic values by 10.0%"]


class TestPromotion:
    def test_only_a_proposal_in_shadow_can_be_promoted(self, monkeypatch):
        _store("v", "rejected")
        _eligible(monkeypatch, ok=True)
        with pytest.raises(review.PromotionRefused):
            review.promote("v")

    def test_an_ineligible_proposal_is_refused_with_its_reasons(self, monkeypatch):
        _store("v", "shadow")
        _eligible(monkeypatch, ok=False, reasons=["3 day(s) in shadow; need 28"])
        with pytest.raises(review.PromotionRefused, match="need 28"):
            review.promote("v")
        assert _statuses()["v"] == "shadow"
        assert cal.active_version() is None

    def test_promotion_goes_live_and_freezes_the_runs_it_changes(self, monkeypatch):
        _store("old", "active", promoted="2025-12-01")
        _store("v", "shadow", created="2026-01-01")
        _eligible(monkeypatch, ok=True)
        _seed_run("r1", "AAPL", "2026-01-05T10:00:00", iv=150.0, a=100.0, b=200.0)
        _seed_run("r0", "MSFT", "2025-12-20T10:00:00", iv=150.0, a=100.0, b=200.0)
        assert cal.active_version()["version_id"] == "old"             # primes the cache
        out = review.promote("v", actor="me@example.com")
        assert out["cohort_size"] == 1                                 # r0 predates it
        assert _statuses() == {"v": "active", "old": "retired"}
        assert cal.active_version()["version_id"] == "v"             # cache cleared
        from src.data import db
        row = db.query_one("SELECT live_iv, cand_iv FROM calibration_cohorts WHERE run_id = 'r1'")
        assert row["live_iv"] == 150.0
        assert row["cand_iv"] == pytest.approx((0.6 * 100 + 0.4 * 200) * 1.1)
        events = review.detail("v")["events"]
        assert events[0]["action"] == "promote" and events[0]["actor"] == "me@example.com"

    def test_rollback_restores_the_previous_version(self, monkeypatch):
        _store("old", "active", promoted="2025-12-01")
        _store("v", "shadow")
        _eligible(monkeypatch, ok=True)
        review.promote("v")
        out = review.rollback("v", actor="me@example.com", reason="looked wrong")
        assert out == {"rolled_back": "v", "restored": "old"}
        assert _statuses() == {"v": "rolled_back", "old": "active"}
        assert cal.active_version()["version_id"] == "old"

    def test_rollback_with_nothing_before_it_restores_the_constants(self):
        _store("v", "active", promoted="2026-02-01")
        assert review.rollback("v")["restored"] == "constants"
        assert cal.active_version() is None

    def test_only_the_active_calibration_can_be_rolled_back(self):
        _store("v", "shadow")
        with pytest.raises(review.PromotionRefused):
            review.rollback("v")

    def test_dismiss(self):
        _store("v", "shadow")
        _store("live", "active", promoted="2026-02-01")
        assert review.dismiss("v")["status"] == "dismissed"
        with pytest.raises(review.PromotionRefused):
            review.dismiss("live")

    def test_unknown_version(self):
        review._ensure_tables()
        with pytest.raises(KeyError):
            review.promote("nope")


class TestCanary:
    def _rows(self, n, label, pv="v+constants-x"):
        start = date(2026, 2, 2)
        return [{"run_id": f"c{i}", "ticker": f"T{i}", "run_date": start + timedelta(days=i),
                 "label_date": start + timedelta(days=i + 90), "label_value": label,
                 "base_iv": 120.0, "market": "US", "profile": "Q",
                 "dcf": {"param_version": pv, "profile": "Q"}} for i in range(n)]

    def _active(self, monkeypatch, rows):
        _store("v", "active", params={"market_iv_multiplier": {"US": 1.2}}, promoted="2026-02-01")
        monkeypatch.setattr(review.wf, "load_rows", lambda h: rows)

    def test_a_calibration_that_makes_things_worse_is_rolled_back(self, monkeypatch):
        # without it: 120 / 1.2 = 100 = the label; with it: 20% too high
        self._active(monkeypatch, self._rows(25, label=100.0))
        out = review.canary_check()
        assert out["status"] == "regressed" and out["rollback"]["restored"] == "constants"
        assert _statuses()["v"] == "rolled_back"

    def test_a_calibration_that_helps_is_kept(self, monkeypatch):
        self._active(monkeypatch, self._rows(25, label=120.0))
        assert review.canary_check()["status"] == "healthy"
        assert _statuses()["v"] == "active"

    def test_too_few_labels_keeps_collecting(self, monkeypatch):
        self._active(monkeypatch, self._rows(5, label=100.0))
        assert review.canary_check()["status"] == "collecting"
        assert _statuses()["v"] == "active"

    def test_runs_not_made_under_it_do_not_count(self, monkeypatch):
        self._active(monkeypatch, self._rows(25, label=100.0, pv="constants-x"))
        out = review.canary_check()
        assert out["n"] == 0 and out["status"] == "collecting"

    def test_nothing_active(self):
        review._ensure_tables()
        assert review.canary_check() == {"active": None}


class TestDiagnostics:
    def _stub(self, monkeypatch):
        monkeypatch.setattr(review, "_label_counts", lambda: {"px_90d": 40})
        monkeypatch.setattr(review.vo, "scorecard", lambda group: {"groups": {"US": {"horizons": {
            "px_90d": {"n": 40, "median_signed_pct": -25.4, "median_miss_factor": 2.4,
                       "direction_hit_rate": 0.375}}}}})
        from src.memory import valuation_attribution as va
        monkeypatch.setattr(va, "attribution_report", lambda h, g, worst_n=0: {"groups": {
            "US / Mature SaaS": {
                "n_runs": 10, "shared_bias_share": 0.55,
                "layers": {"iv": {"miss_factor": 2.5}},
                "routing": {"runs_with_priced_alternative": 4, "alternative_better_share": 0.75},
                "methods": {
                    "EPV": {"n": 9, "miss_factor": 7.1, "median_weight": 0.22, "signed_pct": -85.9},
                    "DCF": {"n": 9, "miss_factor": 9.0, "median_weight": 0.0, "signed_pct": -90.0},
                    "P/E": {"n": 3, "miss_factor": 5.0, "median_weight": 0.3, "signed_pct": 40.0},
                }},
            "US / Tiny": {"n_runs": 2, "shared_bias_share": 1.0, "layers": {"iv": {}},
                          "routing": {}, "methods": {}}}})

    def test_cards_name_the_problem_and_what_fixes_it(self, monkeypatch):
        self._stub(monkeypatch)
        horizon, cards = review.diagnostics()
        titles = {c["title"]: c for c in cards}
        assert horizon == "px_90d"
        bias = titles["US 12-month targets run 25% below the price 90 days later"]
        assert bias["action"] == "calibration"
        epv = titles["EPV misses by 7.1x on US / Mature SaaS runs"]
        assert epv["action"] == "code_change" and "22%" in epv["detail"]
        assert "US / Mature SaaS: every method missed the same way in 55% of runs" in titles
        assert any(t.startswith("US / Mature SaaS: another routing layer") for t in titles)

    def test_thresholds_keep_noise_out(self, monkeypatch):
        self._stub(monkeypatch)
        _, cards = review.diagnostics()
        titles = " | ".join(c["title"] for c in cards)
        assert "DCF misses" not in titles          # carries no weight
        assert "P/E misses" not in titles          # 3 runs
        assert "US / Tiny" not in titles           # 2 runs

    def test_no_labels_no_cards(self, monkeypatch):
        monkeypatch.setattr(review, "_label_counts", lambda: {})
        assert review.diagnostics() == (None, [])


OWNER = "owner@example.com"
USERS = {"owner-token": SimpleNamespace(id=1, email="Owner@Example.com", role="member"),
         "other-token": SimpleNamespace(id=2, email="someone@else.com", role="admin")}


class TestRoutes:
    """Only a signed-in email on MODEL_ACCURACY_EMAILS gets in -- not a role,
    not the service secret."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.backend.database import get_db
        from app.backend.routes import deps
        from app.backend.routes import model_accuracy as ma
        app = FastAPI()
        app.include_router(ma.router)
        app.dependency_overrides[get_db] = lambda: None
        monkeypatch.setenv("DB_UPLOAD_SECRET", "s3cret")
        monkeypatch.setenv("MODEL_ACCURACY_EMAILS", f" {OWNER} ")
        monkeypatch.setattr(deps, "get_user_from_token", lambda token, db: USERS.get(token))
        monkeypatch.setattr(review, "overview", lambda: {"ok": True})
        return TestClient(app)

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def test_the_owner_reaches_the_page_case_insensitively(self, client):
        r = client.get("/model-accuracy/overview", headers=self._bearer("owner-token"))
        assert r.status_code == 200 and r.json() == {"ok": True}

    def test_another_sign_in_is_refused_even_as_an_admin(self, client):
        assert client.get("/model-accuracy/overview",
                          headers=self._bearer("other-token")).status_code == 403

    def test_the_service_secret_does_not_grant_access(self, client):
        assert client.get("/model-accuracy/overview",
                          headers={"X-Admin-Secret": "s3cret"}).status_code == 403

    def test_anonymous_and_bad_tokens_are_refused(self, client):
        assert client.get("/model-accuracy/overview").status_code == 403
        assert client.get("/model-accuracy/overview",
                          headers=self._bearer("nope")).status_code == 403

    def test_an_empty_allowlist_admits_nobody(self, client, monkeypatch):
        monkeypatch.setenv("MODEL_ACCURACY_EMAILS", "")
        assert client.get("/model-accuracy/overview",
                          headers=self._bearer("owner-token")).status_code == 403

    def test_a_refused_change_is_409_and_an_unknown_version_404(self, client, monkeypatch):
        def refuse(vid, **kw):
            raise review.PromotionRefused("not eligible yet")

        def missing(vid, **kw):
            raise KeyError(vid)
        monkeypatch.setattr(review, "promote", refuse)
        monkeypatch.setattr(review, "detail", missing)
        h = self._bearer("owner-token")
        assert client.post("/model-accuracy/calibration/v/promote", headers=h).status_code == 409
        assert client.get("/model-accuracy/calibration/v", headers=h).status_code == 404


class TestAuthMe:
    def test_me_reports_role_and_whether_this_sign_in_may_see_model_accuracy(self, monkeypatch):
        from app.backend.routes import auth as A
        monkeypatch.setenv("MODEL_ACCURACY_EMAILS", OWNER)
        base = dict(id=1, name=None, avatar_url=None, provider="google")
        owner = A.get_me(user=SimpleNamespace(**base, email="OWNER@example.com"))
        other = A.get_me(user=SimpleNamespace(**base, email="x@y.z", role="admin"))
        assert owner.can_view_model_accuracy is True and owner.role == "member"
        assert other.can_view_model_accuracy is False and other.role == "admin"

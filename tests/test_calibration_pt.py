"""Loops 3 and 4 of the self-learning layer: the pt calibration family (capture and scenario
probability shrink), fitted on the long price horizons only and read by the engine only once
promoted."""
import json
import math
from datetime import date, timedelta

import pytest

from src.memory import calibration as cal
from src.memory import calibration_fit as cf
from src.memory import walk_forward as wf


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from src.memory import calibration_review as review
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "pt.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VALUATION_CALIBRATION_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, cf, rf, review):
        m._tables_ready_key = None
    cal.clear_cache()
    yield
    for m in (vo, cf, rf, review):
        m._tables_ready_key = None
    cal.clear_cache()


START = date(2026, 1, 1)


def _row(i, *, spot=100.0, iv=(80.0, 130.0, 160.0), capture=0.35, probs=(0.25, 0.5, 0.25),
         label=None, true_capture=None, day=None, market="US", profile="P", horizon_days=365):
    """A pt row as walk_forward.load_rows(h, family="pt") would return it. The engine's
    target is recomputed from spot, IVs, the recorded capture and the probabilities; the
    label is where the price landed, by default where a `true_capture` would have put it."""
    rd = START + timedelta(days=day if day is not None else i * 3)
    pts = [spot + capture * (v - spot) for v in iv]
    pt = sum(p * t for p, t in zip(probs, pts))
    if label is None:
        tc = true_capture if true_capture is not None else capture
        label = sum(p * (spot + tc * (v - spot)) for p, v in zip(probs, iv))
    return {"run_id": f"r{i}", "ticker": f"T{i % 9}", "run_date": rd,
            "label_date": rd + timedelta(days=horizon_days), "label_value": label,
            "pt_12m": pt, "spot": spot, "capture": capture,
            "pt_bear": pts[0], "pt_base": pts[1], "pt_bull": pts[2],
            "iv_bear": iv[0], "iv_base": iv[1], "iv_bull": iv[2], "base_iv": iv[1], "bear_iv": iv[0], "bull_iv": iv[2],
            "prob_bear": probs[0], "prob_base": probs[1], "prob_bull": probs[2],
            "pt_calibration_version": None, "market": market, "profile": profile, "dcf": {}}


class TestPredictor:
    def test_pt_is_recomputed_from_the_row_and_the_params_move_it(self):
        r = _row(0)
        assert cf.pt_from(r) == pytest.approx(r["pt_12m"])
        # a new capture, probabilities untouched
        c = 0.5
        want = sum(p * (100 + c * (v - 100)) for p, v in zip((0.25, 0.5, 0.25), (80, 130, 160)))
        assert cf.predictor_pt({"capture": {"market:US": c}})(r) == pytest.approx(want)
        # profile scope wins over market
        assert cf.predictor_pt({"capture": {"market:US": 0.5, "profile:P": 0.2}})(r) == pytest.approx(
            sum(p * (100 + 0.2 * (v - 100)) for p, v in zip((0.25, 0.5, 0.25), (80, 130, 160))))
        # lambda = 1 is the neutral 25/50/25 (already neutral here); lambda = 0 is the row
        r2 = _row(1, probs=(0.1, 0.3, 0.6))
        assert cf.predictor_pt({"scenario_prob_shrink": {"lambda": 0.0}})(r2) is None
        full = cf.predictor_pt({"scenario_prob_shrink": {"lambda": 1.0}})(r2)
        assert full == pytest.approx(sum(p * t for p, t in zip((0.25, 0.5, 0.25), (r2["pt_bear"], r2["pt_base"], r2["pt_bull"]))))
        # nothing touched -> None, live stands
        assert cf.predictor_pt({"capture": {"market:HK": 0.4}})(r) is None

    def test_rows_without_probabilities_fall_back_to_the_base_case(self):
        r = {**_row(0), "prob_bear": None, "prob_base": None, "prob_bull": None}
        assert cf.pt_from(r, capture=0.5) == pytest.approx(100 + 0.5 * 30)


class TestProposal:
    def test_refuses_short_and_consensus_horizons(self):
        for h in ("consensus_0d", "px_30d", "px_90d"):
            with pytest.raises(ValueError):
                cf.propose_pt([_row(0)], horizon=h)

    def test_insufficient_rows_propose_nothing(self):
        rep = cf.propose_pt([_row(i) for i in range(10)], horizon="px_365d")
        assert rep["params"] == {"capture": {}, "scenario_prob_shrink": {}}
        assert rep["scopes"]["market:US"]["status"] == "insufficient_data"

    def test_a_planted_capture_is_recovered_shrunk_and_capped(self):
        # the price kept landing where a 0.50 capture would have put it; the engine used 0.35
        rows = [_row(i, true_capture=0.50) for i in range(40)]
        rep = cf.propose_pt(rows, horizon="px_365d")
        c = rep["params"]["capture"]
        assert set(c) == {"market:US", "profile:P"}
        for v in c.values():
            assert 0.35 < v <= 0.45 + 1e-9                      # moved toward 0.50, at most 0.10
        assert rep["scopes"]["market:US"]["status"] == "proposed"
        assert rep["scopes"]["market:US"]["holdout_cand_miss_pct"] < rep["scopes"]["market:US"]["holdout_live_miss_pct"]
        # the current active value anchors the next move
        rep2 = cf.propose_pt(rows, horizon="px_365d", current={"capture": {"market:US": 0.45, "profile:P": 0.45}})
        assert all(v <= 0.55 + 1e-9 for v in rep2["params"]["capture"].values())

    def test_an_honest_engine_proposes_nothing(self):
        rows = [_row(i, label=None, true_capture=0.35 * math.exp(0.02 * math.sin(i))) for i in range(40)]
        rep = cf.propose_pt(rows, horizon="px_365d")
        assert rep["params"]["capture"] == {}

    def test_overconfident_probabilities_earn_a_shrink(self):
        # the LLM said 10/20/70 but the price landed on a 25/50/25 blend every time
        rows = [_row(i, probs=(0.1, 0.2, 0.7),
                     label=sum(p * (100 + 0.35 * (v - 100)) for p, v in zip((0.25, 0.5, 0.25), (80, 130, 160))))
                for i in range(40)]
        rep = cf.propose_pt(rows, horizon="px_180d")
        # the capture fit absorbs part of the gap (a lower capture also lowers a bull-heavy
        # target), so the shrink is a share, not the whole way; it must still be proposed
        lam = rep["params"]["scenario_prob_shrink"]["lambda"]
        assert 0.0 < lam <= 1.0 and rep["lambda"]["status"] == "proposed"
        assert rep["lambda"]["holdout_cand_miss_pct"] < rep["lambda"]["holdout_live_miss_pct"]
        # with the capture held at the recorded value, the shrink carries the whole explanation
        rep2 = cf.propose_pt(rows, horizon="px_180d", current={"capture": {"market:US": 0.35, "profile:P": 0.35}})
        assert rep2["params"]["scenario_prob_shrink"]["lambda"] >= lam


class TestReliability:
    def test_buckets_and_realised_shares(self):
        rows = [_row(i, probs=(0.1, 0.3, 0.6), label=100 + 0.35 * (130 - 100)) for i in range(25)]   # price at the base target
        rep = cf.scenario_reliability("px_365d", rows=rows)
        assert rep["status"] == "ok" and rep["n"] == 25
        assert rep["realised_share"] == {"bear": 0.0, "base": 1.0, "bull": 0.0}
        assert rep["mean_predicted"]["bull"] == pytest.approx(0.6)
        assert rep["base_probability_buckets"]["0.3-0.4"]["realised_share"] == 1.0
        with pytest.raises(ValueError):
            cf.scenario_reliability("consensus_0d", rows=rows)
        assert cf.scenario_reliability("px_365d", rows=rows[:3])["status"] == "insufficient"


def _seed_ledger(n=40, true_capture=0.5, horizon="px_365d", start="2026-01-01"):
    """run_features + valuation_outcomes rows for the pt loader (no archive blob needed)."""
    from src.data import db
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    rf._ensure_tables()
    vo._ensure_tables()
    for i in range(n):
        r = _row(i, true_capture=true_capture, day=(date.fromisoformat(start) - START).days + i * 3)
        feat = {c: None for c in rf.COLUMNS}
        feat.update(feature_key=f"{r['run_id']}|{r['ticker']}", run_id=r["run_id"], ticker=r["ticker"],
                    run_at=r["run_date"].isoformat() + "T10:00:00", run_date=r["run_date"].isoformat(),
                    features_version=rf.FEATURES_VERSION, market="US", sector="Tech", profile="P",
                    spot=r["spot"], iv_bear=r["iv_bear"], iv_base=r["iv_base"], iv_bull=r["iv_bull"],
                    pt_bear=r["pt_bear"], pt_base=r["pt_base"], pt_bull=r["pt_bull"], pt_12m=r["pt_12m"],
                    capture=r["capture"], prob_bear=0.25, prob_base=0.5, prob_bull=0.25,
                    override_carried=0, unrated=0, cache_copy=0, recorded_at="2026-01-01T00:00:00")
        db.execute(vo._upsert_sql("run_features", "feature_key", rf.COLUMNS), [feat[c] for c in rf.COLUMNS])
        row = {c: None for c in vo._COLUMNS}
        row.update(outcome_key=f"{r['run_id']}|{r['ticker']}|{horizon}", run_id=r["run_id"], ticker=r["ticker"],
                   horizon=horizon, run_date=r["run_date"].isoformat(), label_date=r["label_date"].isoformat(),
                   label_value=r["label_value"], label_source="close", base_iv=r["iv_base"], pt_12m=r["pt_12m"],
                   market="US", profile="P", scored_at="2027-01-01T00:00:00")
        db.execute(vo._upsert_sql("valuation_outcomes", "outcome_key", vo._COLUMNS), [row[c] for c in vo._COLUMNS])


class TestRecordingAndPromotion:
    def test_the_pt_loader_joins_the_ledger_and_refuses_consensus(self):
        _seed_ledger(5)
        rows = wf.load_rows("px_365d", family="pt")
        assert len(rows) == 5 and rows[0]["pt_12m"] > 0 and rows[0]["capture"] == 0.35
        with pytest.raises(ValueError):
            wf.load_rows("consensus_0d", family="pt")
        assert all(r["dcf"] == {} for r in wf.load_rows("px_365d"))   # the iv loader found no archive blob

    def test_fit_and_record_writes_a_pt_shadow_without_touching_the_iv_shadow(self):
        from src.data import db
        _seed_ledger(40)
        cf.record_version(version_id="cal-iv", horizon="px_90d", status="shadow",
                          params={"profile_weights": {}, "market_iv_multiplier": {"US": 1.1}},
                          fit={}, backtest={}, family="iv")
        rep = cf.fit_and_record(family="pt", today=date(2027, 3, 1))
        assert rep["status"] in ("shadow", "rejected") and rep["version_id"].startswith("calpt-")
        assert rep["params"]["capture"]["market:US"] > 0.35
        rows = {r["version_id"]: (r["status"], r["family"]) for r in db.query("SELECT version_id, status, family FROM calibration_versions")}
        assert rows["cal-iv"] == ("shadow", "iv") and rows[rep["version_id"]][1] == "pt"
        assert cf.fit_and_record(family="pt", horizon="px_90d", today=date(2027, 3, 1))["status"] == "insufficient_data"

    def test_readers_are_inert_until_promotion_and_scoped_after(self, monkeypatch):
        from src.memory import calibration_review as review
        _seed_ledger(40)
        cf.record_version(version_id="calpt-x", horizon="px_365d", status="shadow",
                          params={"capture": {"profile:P": 0.45, "market:US": 0.40},
                                  "scenario_prob_shrink": {"lambda": 0.3}},
                          fit={}, backtest={"verdict": {"passed": True}}, family="pt", today=date(2026, 1, 1))
        assert cal.capture("T1", "P") is None and cal.prob_shrink() is None
        # not eligible: nothing matured after the proposal date
        sr = cf.shadow_report("calpt-x", today=date(2026, 1, 10))
        assert sr["eligible_for_promotion"] is False and sr["family"] == "pt"
        with pytest.raises(review.PromotionRefused):
            review.promote("calpt-x", actor="owner", today=date(2026, 1, 10))
        # 40 runs made after the proposal, 28+ days on: eligible, and promotion is pt-only
        _seed_ledger(40, start="2026-02-01")
        sr2 = cf.shadow_report("calpt-x", today=date(2027, 6, 1))
        assert sr2["eligible_for_promotion"] is True, sr2["reasons"]
        out = review.promote("calpt-x", actor="owner", today=date(2027, 6, 1))
        assert out["status"] == "active" and out["cohort_size"] == 0
        cal.clear_cache()
        assert cal.capture("T1", "P") == 0.45 and cal.capture("T1", "Other") == 0.40
        assert cal.capture("0005.HK", "Other") is None and cal.prob_shrink() == 0.3
        assert cal.active_version("iv") is None
        monkeypatch.setenv("VALUATION_CALIBRATION_DISABLED", "1")
        cal.clear_cache()
        assert cal.capture("T1", "P") is None and cal.prob_shrink() is None
        monkeypatch.delenv("VALUATION_CALIBRATION_DISABLED")
        cal.clear_cache()
        # overview lists it as the pt active; describe_params reads in English
        ov = review.overview()
        assert ov["actives"]["pt"]["id"] == "calpt-x" and ov["active"] is None
        assert any("capture" in c.lower() for c in ov["actives"]["pt"]["changes"])
        assert any("25/50/25" in c for c in ov["actives"]["pt"]["changes"])
        # the pt canary collects on runs that named the version; none did yet
        assert review.canary_check(family="pt")["status"] == "collecting"
        # rollback restores only within the family
        rb = review.rollback("calpt-x", actor="owner", reason="test")
        assert rb["restored"] == "constants"


class TestWalkForwardValueKey:
    def test_the_harness_scores_the_target_when_told_to(self):
        rows = [_row(i, true_capture=0.5, horizon_days=30) for i in range(120)]   # labels mature inside the folds
        rep = wf.walk_forward(rows, lambda train: cf.predictor_pt(cf.propose_pt(train, horizon="px_365d")["params"]),
                              value_key="pt_12m", band_keys=("pt_bear", "pt_bull"))
        assert rep["verdict"]["passed"], rep["verdict"]["reasons"]
        assert rep["pooled"]["cand_miss_pct"] < rep["pooled"]["live_miss_pct"]


class TestEngineHooks:
    def test_the_engine_reads_capture_and_the_scenario_agent_reads_the_shrink(self):
        import inspect
        from src.agents.analysis import dcf_agent as d
        from src.agents.analysis import scenario_agent as sa
        src = inspect.getsource(d.run_dcf_agent)
        assert "_cal_pt.capture(ticker, profile_name)" in src and '"capture_source": _capture_source' in src
        ssrc = inspect.getsource(sa.run_scenario_agent)
        assert "_cal_pt.prob_shrink()" in ssrc and 'scenario_dict["probability_shrink"]' in ssrc

    def test_run_features_records_the_pt_version_a_run_was_made_under(self):
        from src.memory import run_features as rf
        dr = {"base": {"intrinsic_value": 100.0}, "pt_bridge": {"spot": 90.0, "capture": 0.45,
                                                                 "capture_source": "calibration:calpt-x"}}
        row = rf.extract("r1", "ZZCO", dr, {"12m_price_target": 95.0}, run_at="2026-01-01")
        assert row["pt_calibration_version"] == "calpt-x"
        dr2 = {"base": {"intrinsic_value": 100.0}, "pt_bridge": {"spot": 90.0, "capture": 0.35, "capture_source": "rule"}}
        row2 = rf.extract("r1", "ZZCO", dr2, {"probability_shrink": {"version_id": "calpt-y"}}, run_at="2026-01-01")
        assert row2["pt_calibration_version"] == "calpt-y"
        assert rf.extract("r1", "ZZCO", dr2, {}, run_at="2026-01-01")["pt_calibration_version"] is None

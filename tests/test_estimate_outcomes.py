"""Loop 1 of the self-learning layer: the agent's FY+1 estimates against the prints, the
scorecards, the guidance credibility and the est-family proposal."""
import json
import math
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import calibration as cal
    from src.memory import calibration_fit as cf
    from src.memory import estimate_outcomes as eo
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "est.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ESTIMATE_OUTCOMES_DISABLED", raising=False)
    monkeypatch.delenv("RUN_FEATURES_DISABLED", raising=False)
    monkeypatch.delenv("VALUATION_CALIBRATION_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, rf, eo, cf):
        m._tables_ready_key = None
    cal.clear_cache()
    yield
    for m in (vo, rf, eo, cf):
        m._tables_ready_key = None
    cal.clear_cache()


from src.memory import estimate_outcomes as eo  # noqa: E402
from src.memory import run_features as rf  # noqa: E402

TODAY = date(2027, 3, 1)


class _LI:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _feat(run_id, ticker="ZZCO", run_date="2026-06-01", fy1=2026, fye=None, rg=(0.05, 0.08, 0.11),
          em=(0.20, 0.22, 0.24), eps=(4.0, 4.5, 5.0), cons_rg=0.07, cons_eps=4.4, guid_rg=0.09,
          guid_eps=4.6, archetype="IV", confidence="MEDIUM", market="US", sector="Tech",
          profile="Mature SaaS", **flags):
    """Write a run_features row directly (the ledger is Phase A's concern)."""
    from src.data import db
    rf._ensure_tables()
    row = {c: None for c in rf.COLUMNS}
    row.update(feature_key=f"{run_id}|{ticker}", run_id=run_id, ticker=ticker,
               run_at=run_date + "T10:00:00", run_date=run_date, features_version=rf.FEATURES_VERSION,
               market=market, sector=sector, profile=profile, archetype=archetype, confidence=confidence,
               fiscal_year_1=fy1, fye_month=fye,
               agent_rg_fy1_bear=rg[0], agent_rg_fy1_base=rg[1], agent_rg_fy1_bull=rg[2],
               agent_em_fy1_bear=em[0], agent_em_fy1_base=em[1], agent_em_fy1_bull=em[2],
               agent_eps_fy1_bear=eps[0], agent_eps_fy1_base=eps[1], agent_eps_fy1_bull=eps[2],
               cons_rg_fy1=cons_rg, cons_eps_fy1=cons_eps, guid_rg_mid=guid_rg, guid_eps_mid=guid_eps,
               override_carried=0, unrated=0, cache_copy=0, recorded_at="2026-06-01T10:00:00")
    row.update(flags)
    from src.memory import valuation_outcomes as vo
    db.execute(vo._upsert_sql("run_features", "feature_key", rf.COLUMNS), [row[c] for c in rf.COLUMNS])


def _annuals(by_ticker):
    calls = []

    def fn(ticker, end_date):
        calls.append((ticker, end_date))
        return by_ticker.get(ticker, [])
    fn.calls = calls
    return fn


def _never(ticker, end_date):
    raise AssertionError(f"fetched for {ticker}")


ANNUALS = [{"period_end": "2026-12-31", "revenue": 1100.0, "ebitda": 231.0, "eps": 4.84},
           {"period_end": "2025-12-31", "revenue": 1000.0, "ebitda": 210.0, "eps": 4.2}]
QUARTERS = [{"period_end": "2026-09-30", "revenue": 290.0}, {"period_end": "2026-06-30", "revenue": 270.0},
            {"period_end": "2026-03-31", "revenue": 260.0}, {"period_end": "2025-12-31", "revenue": 265.0},
            {"period_end": "2025-09-30", "revenue": 250.0}, {"period_end": "2025-06-30", "revenue": 245.0}]


def _rows(**where):
    return eo._rows(" AND ".join(f"{k} = ?" for k in where), list(where.values()))


class TestScoring:
    def test_the_fy_print_scores_all_three_fields(self):
        _feat("r1")
        rep = eo.score_matured(today=TODAY, annuals_fn=_annuals({"ZZCO": ANNUALS}),
                               quarters_fn=lambda t, e: QUARTERS)
        assert rep["fy_written"] == 3 and rep["q_written"] == 1 and rep["written"] == 4
        got = {r["field"]: r for r in _rows(period_kind="fy")}
        rg = got["revenue_growth"]
        assert rg["actual"] == pytest.approx(0.10)
        assert rg["err_base"] == pytest.approx(math.log(1.08 / 1.10), abs=1e-6)
        assert rg["err_consensus"] == pytest.approx(math.log(1.07 / 1.10), abs=1e-6)
        assert rg["agent_closer_than_consensus"] == 1 and rg["in_band"] == 1
        assert rg["guidance_mid"] == 0.09 and rg["actual_source"] == "statements;fye_assumed_dec"
        em = got["ebitda_margin"]
        assert em["actual"] == pytest.approx(0.21) and em["err_base"] == pytest.approx(0.01)
        assert em["consensus"] is None and em["err_consensus"] is None
        eps = got["eps"]
        assert eps["err_base"] == pytest.approx(math.log(4.5 / 4.84), abs=1e-6)
        assert eps["in_band"] == 1 and eps["archetype"] == "IV" and eps["market"] == "US"
        q = _rows(period_kind="q_track")[0]
        assert q["actual"] == pytest.approx(290.0 / 250.0 - 1) and q["actual_period_end"] == "2026-09-30"
        assert "tracking_only" in q["actual_source"]

    def test_nothing_is_fetched_before_the_print_can_exist(self):
        _feat("r1", fy1=2026)                       # FY ends 2026-12-31; needs +45 days
        rep = eo.score_matured(today=date(2027, 1, 20), annuals_fn=_never,
                               quarters_fn=lambda t, e: [])
        assert rep["no_print_yet"] == 1 and rep["written"] == 0 and rep["tickers_fetched"] == 1
        # a June year end is ready by March
        _feat("r2", ticker="JUNE", fy1=2026, fye=6)
        fn = _annuals({"JUNE": [{"period_end": "2026-06-30", "revenue": 120.0, "ebitda": 24.0, "eps": 2.0},
                                {"period_end": "2025-06-30", "revenue": 100.0, "ebitda": 20.0, "eps": 1.5}]})
        rep = eo.score_matured(today=date(2027, 1, 20), annuals_fn=fn, quarters_fn=lambda t, e: [])
        assert [c[0] for c in fn.calls] == ["JUNE"]
        r = _rows(ticker="JUNE", field="revenue_growth", period_kind="fy")[0]
        assert r["actual"] == pytest.approx(0.20) and r["actual_source"] == "statements"

    def test_idempotent_and_write_false(self):
        _feat("r1")
        fn = _annuals({"ZZCO": ANNUALS})
        assert eo.score_matured(today=TODAY, annuals_fn=fn, quarters_fn=lambda t, e: QUARTERS,
                                write=False)["written"] == 0
        assert _rows() == []
        eo.score_matured(today=TODAY, annuals_fn=fn, quarters_fn=lambda t, e: QUARTERS)
        rep = eo.score_matured(today=TODAY, annuals_fn=_never, quarters_fn=_never)
        assert rep["written"] == 0 and rep["tickers_fetched"] == 0

    def test_an_eps_sign_flip_is_unscorable_and_a_missing_prior_year_skips_growth(self):
        _feat("r1", eps=(-1.0, 0.5, 1.0))
        rep = eo.score_matured(today=TODAY, annuals_fn=_annuals({"ZZCO": [
            {"period_end": "2026-12-31", "revenue": 1100.0, "ebitda": 231.0, "eps": -0.3}]}),
            quarters_fn=lambda t, e: [])
        assert rep["unscorable"] == 1
        assert {r["field"] for r in _rows()} == {"ebitda_margin"}

    def test_user_runs_are_never_scored(self):
        _feat("r1", override_carried=1)
        _feat("r2", unrated=1)
        _feat("r3", cache_copy=1)
        rep = eo.score_matured(today=TODAY, annuals_fn=_never, quarters_fn=_never)
        assert rep["candidates"] == 0 and _rows() == []

    def test_fetch_errors_are_counted_not_raised(self):
        _feat("r1")
        rep = eo.score_matured(today=TODAY, annuals_fn=_never, quarters_fn=_never)
        assert rep["fetch_errors"] == 2 and rep["written"] == 0

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("ESTIMATE_OUTCOMES_DISABLED", "1")
        assert eo.score_matured(today=TODAY, annuals_fn=_never, quarters_fn=_never).get("disabled")

    def test_fy_end(self):
        assert eo.fy_end(2026, None) == date(2026, 12, 31)
        assert eo.fy_end(2026, 2) == date(2026, 2, 28) and eo.fy_end(2028, 2) == date(2028, 2, 29)


def _seed_scored(n, *, actual_rg=0.10, agent_base=0.08, guid=0.09, archetype="IV", market="US",
                 sector="Tech", start=date(2026, 1, 1), ticker_prefix="T", scored="2026-06-01"):
    from src.data import db
    from src.memory import valuation_outcomes as vo
    eo._ensure_tables()
    rows = []
    for i in range(n):
        rid = f"{ticker_prefix}{i}"
        run_date = date.fromordinal(start.toordinal() + i).isoformat()
        rows.append({
            "outcome_key": f"{rid}|{ticker_prefix}{i % 7}|revenue_growth|fy", "run_id": rid,
            "ticker": f"{ticker_prefix}{i % 7}", "run_date": run_date, "fiscal_year_1": 2026,
            "field": "revenue_growth", "period_kind": "fy",
            "agent_bear": agent_base - 0.03, "agent_base": agent_base, "agent_bull": agent_base + 0.03,
            "consensus": None, "guidance_mid": guid, "actual": actual_rg,
            "actual_period_end": "2026-12-31", "actual_source": "statements",
            "err_base": eo._err("revenue_growth", agent_base, actual_rg), "err_consensus": None,
            "in_band": None, "agent_closer_than_consensus": None,
            "archetype": archetype, "confidence": "MEDIUM", "profile": "P", "market": market,
            "sector": sector, "scored_at": scored + "T00:00:00",
        })
    db.executemany(vo._upsert_sql("estimate_outcomes", "outcome_key", eo._COLUMNS),
                   [[r[c] for c in eo._COLUMNS] for r in rows])


class TestScorecards:
    def test_scorecard_is_insufficient_under_five_and_grouped_above(self):
        _seed_scored(3, archetype="II")
        _seed_scored(8, archetype="IV", ticker_prefix="U")
        sc = eo.scorecard("archetype")
        assert sc["groups"]["II"]["revenue_growth"]["status"] == "insufficient"
        iv = sc["groups"]["IV"]["revenue_growth"]
        assert iv["status"] == "ok" and iv["n"] == 8
        assert iv["bias"] == pytest.approx(math.log(1.08 / 1.10), abs=1e-4)
        with pytest.raises(ValueError):
            eo.scorecard("ticker")

    def test_guidance_credibility_unions_the_steward_scorecard(self):
        _seed_scored(6, actual_rg=0.10, guid=0.09)            # met or beaten, within 2 pt
        from src.memory import assumption_store
        assumption_store.record_scorecard("T0", "earnings", "guidance.eps", 2026, 2,
                                          predicted="1.00", actual="0.80", in_range=False, magnitude=0.2)
        cred = eo.guidance_credibility()
        assert cred["n"] == 7
        t0 = cred["tickers"]["T0"]
        assert t0["n"] == 2 and t0["beat_rate"] == 0.5 and t0["status"] == "insufficient"
        assert cred["markets"]["US"]["n"] == 7 and cred["markets"]["US"]["status"] == "ok"
        assert cred["markets"]["US"]["beat_rate"] == pytest.approx(6 / 7, abs=1e-3)


class TestProposal:
    def test_insufficient_rows_proposes_nothing(self):
        _seed_scored(10)
        rep = eo.propose_est(today=TODAY)
        assert rep["status"] == "insufficient_data" and rep["n_rows"] == 10
        from src.data import db
        assert db.query("SELECT * FROM calibration_versions") == []

    def test_a_consistent_miss_becomes_a_capped_shadow_proposal(self):
        # management guided 9%, companies printed 13%: guidance was 4 pt too low; the agent's
        # base (8%) was 5 pt low for archetype IV. Both move at most 2 pt per cycle.
        _seed_scored(40, actual_rg=0.13, agent_base=0.08, guid=0.09)
        rep = eo.propose_est(today=TODAY)
        assert rep["status"] == "shadow" and rep["version_id"].startswith("calest-2027-03-01-")
        g = rep["params"]["guidance_growth_adj"]
        assert set(g) == {"sector:Tech", "market:US"}
        assert all(v == pytest.approx(eo.ADJ_MOVE_CAP) for v in g.values())
        assert rep["params"]["archetype_growth_adj"]["IV"] == pytest.approx(eo.ADJ_MOVE_CAP)
        assert rep["holdout"]["guidance"]["sector:Tech"]["kept"]["sector:Tech"] == pytest.approx(0.02)
        from src.data import db
        row = db.query_one("SELECT status, family, horizon FROM calibration_versions")
        assert (row["status"], row["family"], row["horizon"]) == ("shadow", "est", "fy_print")
        # nothing is active, so the engine readers return nothing
        from src.memory import calibration as cal
        assert cal.guidance_adj("ZZCO", "Tech") is None and cal.archetype_adj_map() == {}
        # a second proposal supersedes the first est shadow only
        rep2 = eo.propose_est(today=date(2027, 3, 8))
        statuses = {r["version_id"]: r["status"] for r in db.query("SELECT version_id, status FROM calibration_versions")}
        assert statuses[rep["version_id"]] == "superseded" and statuses[rep2["version_id"]] == "shadow"

    def test_noise_without_a_holdout_gain_is_not_proposed(self):
        # alternate +4 / -4 misses: the pooled median is ~0 and the holdout cannot improve
        for i in range(40):
            _seed_scored(1, actual_rg=0.13 if i % 2 else 0.05, guid=0.09, agent_base=0.08,
                         start=date.fromordinal(date(2026, 1, 1).toordinal() + i), ticker_prefix=f"N{i}_")
        rep = eo.propose_est(today=TODAY)
        assert rep["status"] in ("no_change", "insufficient_data")

    def test_shadow_eligibility_needs_new_prints(self):
        _seed_scored(40, actual_rg=0.13, agent_base=0.08, guid=0.09)
        rep = eo.propose_est(today=TODAY)
        sr = eo.shadow_report(rep["version_id"], today=date(2027, 3, 10))
        assert sr["eligible_for_promotion"] is False
        assert any("need 28" in r for r in sr["reasons"]) and any("need 20" in r for r in sr["reasons"])
        # 20 fresh prints per touched scope, scored after the proposal, that the adjustment improves
        _seed_scored(25, actual_rg=0.12, agent_base=0.08, guid=0.09, ticker_prefix="F",
                     start=date(2026, 6, 1), scored="2027-04-01")
        sr2 = eo.shadow_report(rep["version_id"], today=date(2027, 5, 1))
        assert sr2["eligible_for_promotion"] is True and sr2["reasons"] == []
        by = sr2["horizons"]["fy_print"]["by_key"]
        assert by["sector:Tech"]["n"] == 25 and by["sector:Tech"]["cand_mae"] < by["sector:Tech"]["live_mae"]
        # and the review layer promotes it through the same door as an iv calibration
        from src.memory import calibration as cal
        from src.memory import calibration_review as review
        out = review.promote(rep["version_id"], actor="owner", today=date(2027, 5, 1))
        assert out["status"] == "active" and out["cohort_size"] == 0
        cal.clear_cache()
        assert cal.guidance_adj("ZZCO", "Tech") == pytest.approx(0.02)
        assert cal.guidance_adj("0005.HK", None) is None               # no HK scope proposed
        assert cal.archetype_adj_map()["IV"] == pytest.approx(0.02)
        assert cal.active_version("iv") is None                          # families do not mix
        card = review.overview()
        assert card["actives"]["est"]["family"] == "est" and card["active"] is None
        assert any("guided revenue growth" in c for c in card["actives"]["est"]["changes"])

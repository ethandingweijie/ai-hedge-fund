"""Loop 6 of the self-learning layer: gate firings scored against the printed year."""
import json
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import calibration as cal
    from src.memory import calibration_fit as cf
    from src.memory import estimate_outcomes as eo
    from src.memory import gate_outcomes as go
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "gates.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GATE_OUTCOMES_DISABLED", raising=False)
    monkeypatch.delenv("RUN_FEATURES_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, rf, eo, go, cf):
        m._tables_ready_key = None
    cal.clear_cache()
    yield
    for m in (vo, rf, eo, go, cf):
        m._tables_ready_key = None
    cal.clear_cache()


from src.memory import gate_outcomes as go  # noqa: E402
from src.memory import run_features as rf  # noqa: E402

TODAY = date(2027, 3, 1)
GATES = [
    {"gate_id": "GATE_CASH_CONVERSION", "metric": "fcf_margin", "applied": False, "a": 0.30, "b": 0.20},
    {"gate_id": "GATE_GROWTH_CAGR_DIVERGENCE", "metric": "revenue_growth", "applied": True, "a": 0.40, "b": 0.25},
    {"gate_id": "GATE_PT_IV_BAND", "metric": "pt_over_scenario_iv", "applied": True, "a": 1.4, "b": 1.25},
    {"gate_id": "GATE_OE_CASCADE", "metric": "fcf_margin_base", "applied": True, "a": -0.05, "b": None},
]
CUR = {"period_end": "2026-12-31", "revenue": 1100.0, "free_cash_flow": 220.0}
PREV = {"period_end": "2025-12-31", "revenue": 1000.0, "free_cash_flow": 180.0}


def _feat(run_id, ticker="ZZCO", gates=GATES, fy1=2026, fye=None, run_date="2026-06-01", fy0_end=None, **flags):
    from src.data import db
    from src.memory import valuation_outcomes as vo
    rf._ensure_tables()
    row = {c: None for c in rf.COLUMNS}
    row.update(feature_key=f"{run_id}|{ticker}", run_id=run_id, ticker=ticker, run_at=run_date + "T10:00:00",
               run_date=run_date, features_version=rf.FEATURES_VERSION, market="US", sector="Tech",
               profile="P", fiscal_year_1=fy1, fye_month=fye, fy0_end=fy0_end, gates_json=json.dumps(gates),
               override_carried=0, unrated=0, cache_copy=0, recorded_at="2026-06-01T10:00:00")
    row.update(flags)
    db.execute(vo._upsert_sql("run_features", "feature_key", rf.COLUMNS), [row[c] for c in rf.COLUMNS])


def _rows():
    from src.data import db
    return {r["gate_id"]: dict(r) for r in db.query("SELECT * FROM gate_outcomes")}


class TestVerdicts:
    def test_margin_gates_are_scored_in_dollars_of_the_printed_year(self):
        # printed FCF margin 20%: the cap to 20% HELPED against a 30% raw margin
        sc = go.score_record(GATES[0], CUR, PREV)
        assert sc["verdict"] == "HELPED" and sc["actual"] == pytest.approx(0.20)
        assert sc["actual_metric"] == "fcf_margin_reported" and sc["epsilon"] == 0.10
        # the same gate against a year that printed 31%: FALSE_ALARM
        sc2 = go.score_record(GATES[0], {**CUR, "free_cash_flow": 341.0}, PREV)
        assert sc2["verdict"] == "FALSE_ALARM"
        # inside the epsilon either way: NEUTRAL
        sc3 = go.score_record({**GATES[0], "a": 0.21, "b": 0.19}, CUR, PREV)
        assert sc3["verdict"] == "NEUTRAL"

    def test_growth_gates_are_scored_as_revenues_off_the_prior_year(self):
        # printed growth 10%: capping 40% to 25% HELPED
        sc = go.score_record(GATES[1], CUR, PREV)
        assert sc["verdict"] == "HELPED" and sc["actual"] == pytest.approx(0.10)
        assert sc["actual_metric"] == "revenue_growth_reported" and sc["epsilon"] == 0.02
        # no prior year: unscorable
        assert go.score_record(GATES[1], CUR, None)["verdict"] == "UNSCORABLE"

    def test_unknown_metrics_and_missing_paths_are_unscorable(self):
        assert go.score_record(GATES[2], CUR, PREV)["verdict"] == "UNSCORABLE"
        assert go.score_record(GATES[3], CUR, PREV)["verdict"] == "UNSCORABLE"
        assert go.score_record({"gate_id": "X"}, CUR, PREV)["verdict"] == "UNSCORABLE"


class TestSweep:
    def test_scores_once_the_year_printed_and_is_idempotent(self):
        _feat("r1")
        calls = []

        def annuals(ticker, end_date):
            calls.append(ticker)
            return [CUR, PREV]
        rep = go.score_matured(today=TODAY, annuals_fn=annuals)
        assert rep["written"] == 4 and rep["verdicts"] == {"HELPED": 2, "UNSCORABLE": 2}
        rows = _rows()
        assert rows["GATE_CASH_CONVERSION"]["verdict"] == "HELPED"
        assert rows["GATE_CASH_CONVERSION"]["applied"] == 0 and rows["GATE_OE_CASCADE"]["applied"] == 1
        assert rows["GATE_OE_CASCADE"]["path_b"] is None
        rep2 = go.score_matured(today=TODAY, annuals_fn=annuals)
        assert rep2["written"] == 0 and calls == ["ZZCO"]

    def test_nothing_before_the_print_and_user_runs_never(self):
        _feat("r1")
        _feat("r2", ticker="USER", override_carried=1)
        _feat("r3", ticker="UNRT", unrated=1)

        def never(ticker, end_date):
            raise AssertionError(ticker)
        rep = go.score_matured(today=date(2027, 1, 20), annuals_fn=never)
        assert rep["candidates"] == 1 and rep["no_print_yet"] == 1 and rep["written"] == 0
        rep = go.score_matured(today=TODAY, annuals_fn=lambda t, e: [PREV])   # FY2026 not printed yet
        assert rep["no_print_yet"] == 1 and rep["written"] == 0

    def test_gates_are_scored_against_the_year_after_the_last_reported_one(self):
        # a February year end labelled FY2026 by the research: the anchor decides, not the label
        _feat("r1", ticker="LULU", fy1=2026, fye=2, run_date="2026-10-03", fy0_end="2026-02-01")
        cur = {"period_end": "2027-01-31", "revenue": 1100.0, "free_cash_flow": 220.0}
        prev = {"period_end": "2026-02-01", "revenue": 1000.0, "free_cash_flow": 180.0}

        def never(ticker, end_date):
            raise AssertionError(ticker)
        assert go.score_matured(today=date(2026, 12, 1), annuals_fn=never)["no_print_yet"] == 1
        rep = go.score_matured(today=date(2027, 4, 1), annuals_fn=lambda t, e: [cur, prev])
        assert rep["written"] == 4 and _rows()["GATE_CASH_CONVERSION"]["actual_period_end"] == "2027-01-31"
        # without an anchor a label that points at an already-ended year is not scored
        _feat("r2", ticker="OLD", fy1=2026, fye=2, run_date="2026-10-03")
        rep = go.score_matured(today=date(2027, 4, 1), annuals_fn=never)
        assert rep.get("no_fiscal_anchor") == 1 and rep["written"] == 0

    def test_write_false_and_disabled(self, monkeypatch):
        _feat("r1")
        rep = go.score_matured(today=TODAY, annuals_fn=lambda t, e: [CUR, PREV], write=False)
        assert rep["rows"] == 4 and rep["written"] == 0 and _rows() == {}
        monkeypatch.setenv("GATE_OUTCOMES_DISABLED", "1")
        assert go.score_matured(today=TODAY, annuals_fn=lambda t, e: [CUR, PREV]).get("disabled")


class TestReportAndProposal:
    def _seed(self, n_false, n_helped, gate="GATE_CASH_CONVERSION"):
        for i in range(n_false + n_helped):
            printed = 341.0 if i < n_false else 220.0        # 31% margin -> false alarm; 20% -> helped
            _feat(f"r{i}", ticker=f"T{i}", gates=[{**GATES[0], "gate_id": gate}])
            go.score_matured(today=TODAY, annuals_fn=lambda t, e, p=printed: [{**CUR, "free_cash_flow": p}, PREV])

    def test_report_shares_and_insufficient(self):
        self._seed(3, 2)
        g = go.report()["gates"]["GATE_CASH_CONVERSION"]
        assert (g["FALSE_ALARM"], g["HELPED"], g["n_judged"]) == (3, 2, 5)
        assert g["false_alarm_share"] == 0.6 and g["status"] == "insufficient"
        assert go.propose(today=TODAY)["status"] == "no_change"

    def test_a_noisy_gate_becomes_a_review_version_for_the_owner(self):
        self._seed(14, 8)
        rep = go.propose(today=TODAY)
        assert rep["status"] == "shadow" and rep["version_id"].startswith("calgate-")
        assert rep["params"]["gate_threshold_review"]["GATE_CASH_CONVERSION"]["n"] == 22
        from src.data import db
        row = db.query_one("SELECT family, status FROM calibration_versions")
        assert (row["family"], row["status"]) == ("est", "shadow")
        from src.memory import calibration_review as review
        card = review.overview()["proposals"][0]
        assert card["family"] == "est" and "Review the GATE_CASH_CONVERSION threshold" in card["changes"][0]
        # the engine reads nothing from it: no growth adjustment, no capture
        from src.memory import calibration as cal
        assert cal.guidance_adj("ZZCO", "Tech") is None

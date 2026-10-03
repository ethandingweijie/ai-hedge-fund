"""Phase A of the self-learning layer: the flat per-run ledger."""
import json
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "features.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RUN_FEATURES_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    vo._tables_ready_key = None
    rf._tables_ready_key = None
    yield
    vo._tables_ready_key = None
    rf._tables_ready_key = None


from src.memory import run_archive  # noqa: E402
from src.memory import run_features as rf  # noqa: E402


def _dr(**extra):
    d = {
        "base": {"intrinsic_value": 100.0,
                 "method_iv_table": {"DCF": 110.0, "Forward P/E": 90.0, "EV/EBITDA": 95.0},
                 "effective_weights": [{"method": "DCF", "value_key": "DCF", "weight": 0.5},
                                       {"method": "Forward P/E", "value_key": "Forward P/E", "weight": 0.3},
                                       {"method": "EV/EBITDA", "value_key": "EV/EBITDA", "weight": 0.2}]},
        "bear": {"intrinsic_value": 80.0}, "bull": {"intrinsic_value": 130.0},
        "12m_targets": {"bear": 86.5, "base": 93.5, "bull": 104.0},
        "pt_bridge": {"spot": 90.0, "capture": 0.35, "scenarios": {}},
        "gate_evaluations": [{"gate_id": "GATE_CASH_CONVERSION", "metric": "fcf_margin",
                              "raw_input_path_a": 0.31, "gated_output_path_b": 0.22,
                              "applied": False, "basis": "x"},
                             {"gate_id": "GATE_REVENUE_SCALE_CAP", "metric": "revenue_growth",
                              "raw_input_path_a": 0.40, "gated_output_path_b": 0.25}],
        "guidance_estimates": {
            "fiscal_year_1": "FY2026", "confidence": "MEDIUM",
            "estimates": {"bear": {"revenue_growth_fy1": 0.05, "ebitda_margin_fy1": 0.20, "eps_fy1": 4.0},
                          "base": {"revenue_growth_fy1": 0.08, "ebitda_margin_fy1": 0.22, "eps_fy1": 4.5},
                          "bull": {"revenue_growth_fy1": 0.11, "ebitda_margin_fy1": 0.24, "eps_fy1": 5.0}},
            "consensus": {"revenue_growth_fy1": 0.07, "eps_fy1": 4.4},
            "guidance": {"revenue_growth": {"low": 0.06, "mid": None, "high": 0.10},
                         "eps": {"low": 4.2, "mid": 4.4, "high": 4.6}, "source": "Q4 call"}},
        "guidance_forecast": {"archetype": "IV",
                              "rows": [{"year": 1, "growth": 0.085, "ebit_margin": 0.18, "eps": 4.55}]},
        "guidance_forecast_scenarios": {"bear": {"rows": [{"year": 1, "growth": 0.05, "eps": 4.05}]},
                                        "bull": {"rows": [{"year": 1, "growth": 0.11, "eps": 5.05}]}},
        "forecast_context": {"fiscal_year_1": "FY2026",
                             "opening_balance_sheet": {"fy_label": "FY2025", "fiscal_year": 2025,
                                                       "period_end": "2025-06-30"}},
        "profile": "Mature SaaS", "routing_trace": {"winner": "ladder"},
        "param_version": "constants-x", "calibration": {"version_id": "cal-1"},
        "is_cache_copy": False,
    }
    d.update(extra)
    return d


SCEN = {"bear": {"probability": 0.2}, "base": {"probability": 0.55}, "bull": {"probability": 0.25},
        "12m_price_target": 94.6, "current_price": 90.0}


def _add_run(run_id, ticker="ZZCO", run_at="2026-01-01T10:00:00", price=90.0, pt=94.6,
             dcf=None, sector="Tech", pm=120.0, scenario=None):
    run_archive._exec(
        "INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector, research_tier, "
        "regime_risk_appetite) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [run_id, run_at, run_at[:10], json.dumps([ticker]), sector, "fast", "risk_on"])
    scen = dict(scenario or SCEN)
    scen["12m_price_target"] = pt
    run_archive._exec(
        "INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, "
        "dcf_range_json, scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, ticker, price, pm, json.dumps(dcf if dcf is not None else _dr()),
         json.dumps(scen)])


def _all():
    return {r["feature_key"]: r for r in rf.rows(exclude_user=False)}


class TestExtract:
    def test_every_column_is_mapped(self):
        row = rf.extract("r1", "zzco", _dr(), SCEN, run_at="2026-01-01T10:00:00",
                         sector="Tech", price_at_run=90.0, pm_target=120.0,
                         research_tier="fast", regime_risk="risk_on")
        assert set(row) == set(rf.COLUMNS)
        assert row["feature_key"] == "r1|ZZCO" and row["run_date"] == "2026-01-01"
        assert row["market"] == "US" and row["profile"] == "Mature SaaS"
        assert (row["iv_bear"], row["iv_base"], row["iv_bull"]) == (80.0, 100.0, 130.0)
        assert (row["pt_bear"], row["pt_base"], row["pt_bull"]) == (86.5, 93.5, 104.0)
        assert row["pt_12m"] == 94.6 and row["pm_target"] == 120.0
        assert row["spot"] == 90.0 and row["capture"] == 0.35
        assert (row["prob_bear"], row["prob_base"], row["prob_bull"]) == (0.2, 0.55, 0.25)
        methods = json.loads(row["methods_json"])
        assert methods["DCF"] == {"value": 110.0, "weight": 0.5}
        gates = json.loads(row["gates_json"])
        assert gates[0] == {"gate_id": "GATE_CASH_CONVERSION", "metric": "fcf_margin",
                            "applied": False, "a": 0.31, "b": 0.22}
        assert gates[1]["applied"] is None                       # no applied key on the record
        assert row["archetype"] == "IV" and row["confidence"] == "MEDIUM"
        assert row["fiscal_year_1"] == 2026 and row["fye_month"] == 6
        # the forecast's first row is the agent's FY+1; margin comes from the research block
        assert row["agent_rg_fy1_base"] == 0.085 and row["agent_eps_fy1_base"] == 4.55
        assert row["agent_rg_fy1_bear"] == 0.05 and row["agent_eps_fy1_bull"] == 5.05
        assert row["agent_em_fy1_base"] == 0.22
        assert row["agent_source"] == "guidance_forecast.rows[0]+guidance_estimates"
        assert row["cons_rg_fy1"] == 0.07 and row["cons_eps_fy1"] == 4.4
        assert row["guid_rg_mid"] == pytest.approx(0.08)        # low/high midpoint when mid is null
        assert row["guid_eps_mid"] == 4.4 and row["guid_source"] == "Q4 call"
        assert (row["override_carried"], row["unrated"], row["cache_copy"]) == (0, 0, 0)
        assert row["calibration_version"] == "cal-1" and row["research_tier"] == "fast"

    def test_estimates_only_when_there_is_no_forecast(self):
        d = _dr()
        d.pop("guidance_forecast"); d.pop("guidance_forecast_scenarios"); d.pop("forecast_context")
        row = rf.extract("r1", "ZZCO", d, SCEN, run_at="2026-01-01")
        assert row["agent_rg_fy1_base"] == 0.08 and row["agent_eps_fy1_bull"] == 5.0
        assert row["agent_source"] == "guidance_estimates"
        assert row["fiscal_year_1"] == 2026 and row["fye_month"] is None
        assert row["archetype"] is None

    def test_ebit_margin_stands_in_and_says_so(self):
        d = _dr()
        for sc in d["guidance_estimates"]["estimates"].values():
            sc.pop("ebitda_margin_fy1")
        row = rf.extract("r1", "ZZCO", d, SCEN, run_at="2026-01-01")
        assert row["agent_em_fy1_base"] == 0.18
        assert row["agent_source"].endswith("+ebit_margin_proxy")

    def test_the_fiscal_year_end_comes_from_the_rows_the_engine_used(self):
        d = _dr(financials_used={"rows": [{"period": "2025-05-31", "revenue": 1.0}, {"period": "2026-05-31", "revenue": 1.0}]})
        assert rf.extract("r1", "NKE", d, SCEN, run_at="2026-01-01")["fye_month"] == 5       # beats the opening sheet's June
        d2 = _dr(); d2["forecast_context"]["opening_balance_sheet"].pop("period_end")
        assert rf.extract("r1", "ZZCO", d2, SCEN, run_at="2026-01-01")["fye_month"] is None

    def test_a_balance_sheet_family_keeps_no_forecast_eps_or_margin(self):
        # a bank: the generic forecast's EPS (-0.15) and EBIT margin do not describe it
        d = _dr(bank_breakdown={"ggm": 1.0})
        d["guidance_forecast"]["rows"][0]["eps"] = -0.15
        row = rf.extract("r1", "D05.SI", d, SCEN, run_at="2026-01-01")
        assert row["agent_eps_fy1_base"] == 4.5 and row["agent_em_fy1_base"] is None    # research EPS, no margin
        assert row["agent_rg_fy1_base"] == 0.085                                          # growth still from the forecast
        assert row["agent_source"].endswith("+balance_sheet_family")
        # with a bank model, its FY+1 EPS is the agent's base estimate
        d["forecast_context"]["bank_model"] = {"eps_fy1": 3.9}
        row2 = rf.extract("r1", "D05.SI", d, SCEN, run_at="2026-01-01")
        assert row2["agent_eps_fy1_base"] == 3.9 and "bank_model.eps_fy1" in row2["agent_source"]
        assert row2["agent_eps_fy1_bull"] == 5.0

    def test_flags(self):
        row = rf.extract("r1", "ZZCO", _dr(estimate_override_carried={"id": "o1"},
                                          rating_state={"state": "unrated"}, is_cache_copy=True),
                         SCEN, run_at="2026-01-01")
        assert (row["override_carried"], row["unrated"], row["cache_copy"]) == (1, 1, 1)

    def test_fy_parsing(self):
        assert rf._fy_int("FY2026E") == 2026 and rf._fy_int(2027.0) == 2027
        assert rf._fy_int("2026-12") == 2026 and rf._fy_int("n/a") is None
        assert rf._fy_int(1800) is None

    def test_spot_falls_back_to_price_then_scenario(self):
        d = _dr(); d.pop("pt_bridge")
        assert rf.extract("r1", "ZZCO", d, SCEN, run_at="2026-01-01", price_at_run=91.0)["spot"] == 91.0
        assert rf.extract("r1", "ZZCO", d, SCEN, run_at="2026-01-01")["spot"] == 90.0

    def test_garbage_raises_in_extract_but_record_never_does(self):
        with pytest.raises(ValueError):
            rf.extract("r1", "ZZCO", {}, None, run_at="2026-01-01")
        assert rf.record("r1", "ZZCO", {}, None, run_at="2026-01-01") is False
        assert rf.record("r1", "ZZCO", _dr(), None, run_at="not-a-date") is False
        assert _all() == {}


class TestBackfill:
    def test_backfill_is_idempotent_and_force_rewrites(self):
        _add_run("r1"); _add_run("r2", run_at="2026-02-01T10:00:00", dcf=_dr(profile="Other"))
        rep = rf.backfill()
        assert (rep["seen"], rep["written"], rep["cache_copies"]) == (2, 2, 0)
        assert set(_all()) == {"r1|ZZCO", "r2|ZZCO"}
        rep2 = rf.backfill()
        assert rep2["written"] == 0 and rep2["skipped_current"] == 2
        rep3 = rf.backfill(force=True)
        assert rep3["written"] == 2

    def test_write_false_reports_without_writing(self):
        _add_run("r1")
        rep = rf.backfill(write=False)
        assert rep["candidates"] == 1 and rep["written"] == 0 and _all() == {}

    def test_a_replayed_entry_is_a_cache_copy(self):
        _add_run("r1"); _add_run("r2", run_at="2026-03-01T10:00:00")   # identical dcf_range
        _add_run("r3", run_at="2026-04-01T10:00:00", dcf=_dr(is_cache_copy=True, profile="X"))
        rep = rf.backfill()
        rows = _all()
        assert rep["cache_copies"] == 2
        assert rows["r1|ZZCO"]["cache_copy"] == 0
        assert rows["r2|ZZCO"]["cache_copy"] == 1 and rows["r3|ZZCO"]["cache_copy"] == 1
        assert [r["run_id"] for r in rf.rows()] == ["r1"]

    def test_run_level_fields_come_from_the_runs_row(self):
        _add_run("r1")
        rf.backfill()
        r = _all()["r1|ZZCO"]
        assert r["research_tier"] == "fast" and r["regime_risk"] == "risk_on"
        assert r["sector"] == "Tech" and r["pm_target"] == 120.0 and r["pt_12m"] == 94.6

    def test_disabled(self, monkeypatch):
        _add_run("r1")
        monkeypatch.setenv("RUN_FEATURES_DISABLED", "true")
        assert rf.backfill().get("disabled") is True
        assert rf.record("r1", "ZZCO", _dr(), SCEN, run_at="2026-01-01") is False
        assert rf.rows() == []


class TestReaders:
    def test_exclusions_and_ordering(self):
        _add_run("r1", run_at="2026-02-01T10:00:00")
        _add_run("r2", run_at="2026-01-01T10:00:00", dcf=_dr(estimate_override_carried={"id": "o"}))
        _add_run("r3", run_at="2026-03-01T10:00:00", dcf=_dr(rating_state={"state": "unrated"}))
        rf.backfill()
        assert [r["run_id"] for r in rf.rows()] == ["r1"]
        assert [r["run_id"] for r in rf.rows(exclude_user=False)] == ["r2", "r1", "r3"]
        assert [r["run_id"] for r in rf.rows(where="profile = ?", params=["Mature SaaS"],
                                             exclude_user=False)] == ["r2", "r1", "r3"]
        assert rf.latest_for_ticker("zzco")["run_id"] == "r3"
        c = rf.counts()
        assert (c["rows"], c["override_carried"], c["unrated"], c["cache_copy"]) == (3, 1, 1, 0)


class TestSaveRunHook:
    def _state(self):
        class _P:
            close = 91.0
        return {
            "data": {"tickers": ["ZZCO"], "sector": "Tech", "research_tier": "full",
                     "macro_regime": {"risk_appetite": "risk_off"},
                     "dcf_range": {"ZZCO": _dr()},
                     "scenario_analysis": {"ZZCO": dict(SCEN)},
                     "routed_data": {"ZZCO": {"prices": [_P()]}},
                     "analyst_signals": {}},
            "metadata": {"model_name": "m"},
        }, {"ZZCO": {"action": "BUY", "price_target": 118.0}}

    def test_save_run_records_a_row(self):
        state, decisions = self._state()
        run_id = run_archive.save_run(state, decisions)
        assert run_id
        r = _all()[f"{run_id}|ZZCO"]
        assert r["spot"] == 90.0 and r["pm_target"] == 118.0 and r["pt_12m"] == 94.6
        assert r["research_tier"] == "full" and r["regime_risk"] == "risk_off"
        assert r["run_date"] == date.today().isoformat()
        # and the backfill agrees the row is current
        assert rf.backfill()["skipped_current"] == 1

    def test_a_failing_hook_never_fails_the_save(self, monkeypatch):
        state, decisions = self._state()
        monkeypatch.setattr(rf, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert run_archive.save_run(state, decisions)
        assert run_archive._fetch("SELECT COUNT(*) AS n FROM ticker_signals")[0]["n"] == 1

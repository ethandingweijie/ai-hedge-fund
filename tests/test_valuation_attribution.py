"""B3: each cause is named only when it is the one that moved the miss."""
import json
import math
from datetime import date

import pytest

from src.memory import valuation_attribution as va


def _dr(table, weights=None, iv=None, pre=None, profile_weights=None, **extra):
    base = {"method_iv_table": table}
    if weights is not None:
        base["effective_weights"] = [{"method": k, "value_key": k, "bucket": "multi",
                                      "weight": w} for k, w in weights.items()]
    if profile_weights is not None:
        base["profile_weights"] = profile_weights
    blend = sum(w * table[k] for k, w in (weights or {}).items()) if weights else None
    base["intrinsic_value_pre_composite"] = pre if pre is not None else blend
    base["intrinsic_value"] = iv if iv is not None else base["intrinsic_value_pre_composite"]
    d = {"base": base, "profile": extra.pop("profile", "Final")}
    d.update(extra)
    return d


class TestCauses:
    def test_composite_named_when_it_pushed_iv_away(self):
        att = va.attribute_run(_dr({"DCF": 100.0}, {"DCF": 1.0}, iv=130.0, pre=100.0), 100.0)
        assert att["composite_effect"] == pytest.approx(math.log(1.3), abs=1e-6)
        assert att["cause"] == "composite"

    def test_weights_named_when_a_carried_method_was_much_closer(self):
        att = va.attribute_run(_dr({"DCF": 200.0, "P/E": 100.0},
                                   {"DCF": 0.5, "P/E": 0.5}), 100.0)
        assert att["best_method"] == "P/E"
        assert att["weight_headroom"] == pytest.approx(math.log(1.5), abs=1e-6)
        assert att["shared_bias"] is False
        assert att["cause"] == "weights"

    def test_shared_bias_when_every_method_missed_the_same_way(self):
        att = va.attribute_run(_dr({"DCF": 50.0, "P/E": 60.0},
                                   {"DCF": 0.5, "P/E": 0.5}), 100.0)
        assert att["shared_bias"] is True
        assert att["weight_headroom"] < va.CAUSE_THRESHOLD
        assert att["cause"] == "shared_method_bias"

    def test_shared_bias_outranks_weights_when_even_the_best_method_missed(self):
        # both far below the label; the closer one is still 40% off, so no
        # re-weighting could have fixed this run
        att = va.attribute_run(_dr({"DCF": 20.0, "P/E": 60.0},
                                   {"DCF": 0.5, "P/E": 0.5}), 100.0)
        assert att["weight_headroom"] > va.CAUSE_THRESHOLD
        assert att["cause"] == "shared_method_bias"

    def test_methods_that_straddle_the_label_are_not_shared_bias(self):
        att = va.attribute_run(_dr({"DCF": 150.0, "P/E": 70.0},
                                   {"DCF": 0.5, "P/E": 0.5}), 100.0)
        assert att["shared_bias"] is False
        assert att["cause"] == "method_values"

    def test_an_uncarried_method_is_not_credited_as_the_better_blend(self):
        # EPV was closer but carried no weight -- routing/profile question,
        # not a weighting one
        att = va.attribute_run(_dr({"DCF": 200.0, "EPV": 100.0}, {"DCF": 1.0}), 100.0)
        assert att["best_method"] == "DCF" and att["weight_headroom"] == 0.0
        assert att["cause"] != "weights"

    def test_small_effects_are_not_named(self):
        att = va.attribute_run(_dr({"DCF": 105.0, "P/E": 95.0},
                                   {"DCF": 0.5, "P/E": 0.5}, iv=102.0, pre=100.0), 100.0)
        assert att["cause"] == "method_values"


class TestRouting:
    @pytest.fixture(autouse=True)
    def profiles(self, monkeypatch):
        monkeypatch.setattr("src.data.sector_profiles.INDUSTRY_VALUATION_PROFILES", {
            "Tech": {
                "Final": {"methods": [{"name": "DCF", "weight": 1.0}]},
                "Alt": {"methods": [{"name": "P/E", "weight": 1.0}]},
                "Thin": {"methods": [{"name": "P/E", "weight": 0.3},
                                     {"name": "NAV", "weight": 0.7}]},
                "Proxied": {"methods": [{"name": "NAV", "weight": 1.0,
                                         "implementable": False, "proxy": "P/E"}]},
            }})

    def _trace(self, *steps, routed=None):
        rt = {"steps": [{"layer": l, "sector": "Tech", "profile": p} for l, p in steps],
              "final_sector": "Tech", "final_profile": "Final"}
        if routed:
            rt["industry_routing"] = {"routed_sector": "Tech", "routed_profile": routed}
        return rt

    def test_a_closer_alternative_profile_is_named(self):
        dr = _dr({"DCF": 200.0, "P/E": 100.0}, {"DCF": 1.0},
                 routing_trace=self._trace(("ladder", "Alt"), ("ticker_override", "Final")))
        att = va.attribute_run(dr, 100.0)
        (alt,) = att["alternatives"]
        assert (alt["layer"], alt["profile"], alt["coverage"]) == ("ladder", "Alt", 1.0)
        assert att["routing_gain"] == pytest.approx(math.log(2), abs=1e-6)
        assert att["cause"] == "routing"

    def test_an_alternative_the_run_cannot_price_is_not_scored(self):
        dr = _dr({"DCF": 200.0, "P/E": 100.0}, {"DCF": 1.0},
                 routing_trace=self._trace(("ladder", "Thin"), ("ticker_override", "Final")))
        att = va.attribute_run(dr, 100.0)
        assert att["alternatives"][0]["coverage"] == 0.3
        assert att["alternatives"][0]["gain"] is None and att["routing_gain"] is None

    def test_proxy_values_price_an_alternative(self):
        dr = _dr({"DCF": 200.0, "P/E": 100.0}, {"DCF": 1.0},
                 routing_trace=self._trace(routed="Proxied"))
        att = va.attribute_run(dr, 100.0)
        assert att["alternatives"][0]["iv"] == 100.0

    def test_the_final_profile_is_not_its_own_alternative(self):
        dr = _dr({"DCF": 200.0}, {"DCF": 1.0},
                 routing_trace=self._trace(("ticker_override", "Final")))
        assert va.attribute_run(dr, 100.0)["alternatives"] == []


class TestLegacyRows:
    def test_nominal_weights_over_methods_that_produced_a_value(self):
        dr = _dr({"DCF": 120.0, "EPV": 80.0}, iv=110.0, profile_weights=[
            {"name": "DCF", "weight": 0.5}, {"name": "EPV", "weight": 0.25},
            {"name": "LBO Floor", "weight": 0.25}])
        att = va.attribute_run(dr, 100.0)
        assert att["weights_source"] == "nominal"
        assert att["weights"] == pytest.approx({"DCF": 2 / 3, "EPV": 1 / 3})
        assert att["pre_composite_err"] == att["iv_err"]      # no pre-composite stored

    def test_unscorable_inputs_return_none(self):
        assert va.attribute_run({}, 100.0) is None
        assert va.attribute_run(_dr({"DCF": 1.0}, {"DCF": 1.0}), 0) is None


# ── report over the database ────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "attr.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    vo._tables_ready_key = None
    yield ra, vo
    vo._tables_ready_key = None


def _seed(ra, run_id, ticker, dr, pt, pm, price=90.0):
    ra._exec("INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector) "
             "VALUES (?, ?, ?, ?, ?)", [run_id, "2026-09-13T10:00:00", "2026-09-13",
                                        json.dumps([ticker]), "Tech"])
    dr = {**dr, "consensus_at_run": {"status": "recorded", "target": 100.0}}
    ra._exec("INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, "
             "dcf_range_json, scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
             [run_id, ticker, price, pm, json.dumps(dr),
              json.dumps({"12m_price_target": pt})])


class TestReport:
    def test_groups_layers_methods_and_worst(self, db):
        ra, vo = db
        _seed(ra, "a", "AAA", _dr({"DCF": 200.0, "P/E": 100.0}, {"DCF": 0.5, "P/E": 0.5}),
              pt=150.0, pm=100.0)
        _seed(ra, "b", "BBB", _dr({"DCF": 50.0, "P/E": 60.0}, {"DCF": 0.5, "P/E": 0.5}),
              pt=55.0, pm=80.0)
        vo.score_matured(today=date(2026, 9, 14), closes_fn=lambda *a: [],
                         consensus_fn=lambda t: None)
        rep = va.attribution_report("consensus_0d", ("market",), worst_n=5)
        us = rep["groups"]["US"]
        assert us["n_runs"] == 2
        assert us["causes"] == {"weights": 1, "shared_method_bias": 1}
        assert us["methods"]["P/E"]["n"] == 2 and us["methods"]["P/E"]["median_weight"] == 0.5
        assert us["weights_source"] == {"effective": 2}
        # AAA: PM (100) removed the whole engine miss; BBB: PM (80) shrank it
        assert us["pm_layer_change_in_miss"] < 1.0
        assert [w["ticker"] for w in rep["worst"]] == ["BBB", "AAA"]   # |ln .55| > |ln 1.5|

    def test_bad_arguments_are_refused(self, db):
        with pytest.raises(ValueError):
            va.attribution_report("px_7d")
        with pytest.raises(ValueError):
            va.attribution_report("consensus_0d", ("ticker",))

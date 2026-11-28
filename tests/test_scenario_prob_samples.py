"""D6 (owner, 2026-11-28): scenario weights are the median of 3 parallel samples, not one call."""
import itertools

from src.agents.analysis import scenario_agent as sa
from src.data.models import ScenarioOutput


def _out(pb, pm, pu, fv=(80.0, 100.0, 130.0)):
    return ScenarioOutput(bear={"assumptions": "b", "fair_value": fv[0], "probability": pb},
                          base={"assumptions": "m", "fair_value": fv[1], "probability": pm},
                          bull={"assumptions": "u", "fair_value": fv[2], "probability": pu},
                          expected_value=0.0, current_price=90.0, upside_pct=0.0)


def test_median_of_samples_renormalised_and_ev_recomputed():
    seq = iter([_out(0.2, 0.5, 0.3), _out(0.4, 0.4, 0.2), _out(0.3, 0.5, 0.2)])
    res, rec = sa._sampled_scenarios(lambda: next(seq), 3)
    assert rec["n"] == 3 and rec["median"] == [0.3, 0.5, 0.2]
    assert sa._case_prob(res.base, 0) == 0.5 and sa._case_prob(res.bear, 0) == 0.3
    assert res.expected_value == round(80 * 0.3 + 100 * 0.5 + 130 * 0.2, 2)
    assert res.bear.assumptions == "b"              # fair values / text from the first usable sample


def test_default_fallback_samples_are_ignored():
    seq = iter([_out(0.25, 0.5, 0.25, fv=(0, 0, 0)), _out(0.2, 0.6, 0.2), _out(0.4, 0.4, 0.2)])
    res, rec = sa._sampled_scenarios(lambda: next(seq), 3)
    assert rec["n"] == 2 and sa._case_fv(res.base) == 100.0


def test_one_sample_keeps_the_old_behaviour(monkeypatch):
    calls = itertools.count()
    res, rec = sa._sampled_scenarios(lambda: (next(calls), _out(0.2, 0.5, 0.3))[1], 1)
    assert rec is None and next(calls) == 1
    monkeypatch.setenv("SCENARIO_PROB_SAMPLES", "9")
    assert sa._prob_sample_count() == 5
    monkeypatch.delenv("SCENARIO_PROB_SAMPLES")
    assert sa._prob_sample_count() == 3

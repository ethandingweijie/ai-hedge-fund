"""BT2/BT4: the harness scores on unseen runs, and refuses anything that saw the future."""
import math
from datetime import date, timedelta

import pytest

from src.memory import walk_forward as wf

START = date(2026, 1, 1)


def _row(i, *, day, iv, label, horizon_days=30, market="US", profile="P",
         bear=None, bull=None):
    rd = START + timedelta(days=day)
    return {"run_id": f"r{i}", "ticker": f"T{i}", "run_date": rd,
            "label_date": rd + timedelta(days=horizon_days), "label_value": label,
            "base_iv": iv, "bear_iv": bear, "bull_iv": bull,
            "market": market, "profile": profile, "dcf": {}}


def _biased_ledger(n=120, bias=1.5, noise=0.25, seed_market="US"):
    """IV consistently `bias`x the label, with deterministic scatter."""
    rows = []
    for i in range(n):
        wobble = math.exp(noise * math.sin(i * 1.7))
        label = 100.0 + i
        rows.append(_row(i, day=i * 2, iv=label * bias * wobble, label=label,
                         market=seed_market, bear=label * bias * 0.7,
                         bull=label * bias * 1.3))
    return rows


class TestFolds:
    def test_each_test_run_is_scored_once(self):
        rows = _biased_ledger()
        folds = wf.build_folds(rows, 4)
        tested = [r["run_id"] for f in folds for r in f.test]
        assert len(tested) == len(set(tested))

    def test_a_label_that_matured_after_the_cutoff_is_not_training_data(self):
        old_run_late_label = _row(0, day=0, iv=100, label=100, horizon_days=365)
        cutoff = START + timedelta(days=100)
        assert wf._train_rows([old_run_late_label], cutoff) == []

    def test_every_fold_trains_only_on_the_past(self):
        rows = _biased_ledger()
        for f in wf.build_folds(rows, 4):
            assert all(r["run_date"] <= f.cutoff and r["label_date"] <= f.cutoff
                       for r in f.train)
            assert all(r["run_date"] > f.cutoff for r in f.test)


class TestLeakageCanaries:
    def test_a_planted_future_label_is_caught(self, monkeypatch):
        rows = _biased_ledger()
        real = wf._train_rows
        # a builder bug: admits labels that matured after the cutoff
        monkeypatch.setattr(wf, "_train_rows",
                            lambda rs, cutoff: real(rs, cutoff + timedelta(days=400)))
        with pytest.raises(wf.LeakageError):
            wf.walk_forward(rows, wf.market_bias_fit)

    def test_an_old_run_with_a_future_label_is_caught_on_its_own(self, monkeypatch):
        # The subtle builder bug: filter on run date only. No run crosses into
        # the test window, so only the label-date check can catch it.
        rows = [dict(r, label_date=r["run_date"] + timedelta(days=365))
                for r in _biased_ledger()]
        monkeypatch.setattr(wf, "_train_rows",
                            lambda rs, cutoff: [r for r in rs if r["run_date"] <= cutoff])
        with pytest.raises(wf.LeakageError, match="postdates the cutoff"):
            wf.walk_forward(rows, wf.market_bias_fit)

    def test_a_fitter_that_reads_the_test_labels_is_refused(self):
        rows = _biased_ledger()
        answers = {r["run_id"]: r["label_value"] for r in rows}

        def cheating_fit(train):
            return lambda row: answers[row["run_id"]]   # knows the future

        with pytest.raises(wf.LeakageError):
            wf.walk_forward(rows, cheating_fit)


class TestVerdict:
    def test_a_real_bias_correction_passes(self):
        rows = _biased_ledger(bias=1.5)
        rep = wf.walk_forward(rows, wf.market_bias_fit)
        assert rep["verdict"]["passed"], rep["verdict"]["reasons"]
        assert abs(rep["pooled"]["cand_bias_pct"]) < abs(rep["pooled"]["live_bias_pct"])

    def test_the_fitter_recovers_the_planted_bias(self):
        train = _biased_ledger(bias=1.5, noise=0.0)
        predict = wf.market_bias_fit(train, shrink=0.0)
        assert math.exp(predict.bias["US"]) == pytest.approx(1.5, rel=1e-6)

    def test_a_harmful_candidate_fails(self):
        rows = _biased_ledger(bias=1.0)
        rep = wf.walk_forward(rows, lambda train: (lambda row: row["base_iv"] * 1.6))
        assert not rep["verdict"]["passed"]
        assert any("folds" in r for r in rep["verdict"]["reasons"])
        assert any("signed bias" in r for r in rep["verdict"]["reasons"])

    def test_the_fitter_shrinks_a_thin_estimate_toward_zero(self):
        train = _biased_ledger(n=10, bias=1.5, noise=0.0)
        predict = wf.market_bias_fit(train, shrink=10.0)
        # n=10, shrink=10 -> half the observed log bias
        assert predict.bias["US"] == pytest.approx(math.log(1.5) / 2, rel=1e-6)

    def test_a_cell_made_worse_fails_even_if_the_pool_improves(self):
        us = _biased_ledger(n=120, bias=1.5, seed_market="US")
        hk = [dict(r, run_id=f"h{r['run_id']}", ticker=f"H{r['ticker']}", market="HK",
                   base_iv=r["label_value"] * 1.02) for r in _biased_ledger(n=120, bias=1.0)]

        def blunt_fit(train):
            return lambda row: row["base_iv"] / 1.5     # right for US, wrong for HK

        rep = wf.walk_forward(us + hk, blunt_fit)
        assert not rep["verdict"]["passed"]
        assert any(r.startswith("cell HK") for r in rep["verdict"]["reasons"])

    def test_a_window_made_worse_is_named(self):
        rows = _biased_ledger(bias=1.5)
        rep = wf.walk_forward(rows, lambda train: (lambda row: row["base_iv"] * 3.0),
                              windows=[("spring", "2026-03-01", "2026-06-30")])
        assert any(r.startswith("window spring") for r in rep["verdict"]["reasons"])

    def test_band_coverage_collapse_fails(self):
        rows = _biased_ledger(bias=1.0, noise=0.0)
        # shifting IV up 20% keeps the miss small but pushes labels out of band
        rep = wf.walk_forward(rows, lambda train: (lambda row: row["base_iv"] * 1.45))
        assert any("band coverage" in r for r in rep["verdict"]["reasons"])

    def test_too_little_history_is_not_a_pass(self):
        rows = _biased_ledger(n=12)
        rep = wf.walk_forward(rows, wf.market_bias_fit)
        assert rep["verdict"]["passed"] is False
        assert "fold" in rep["verdict"]["reasons"][0]

    def test_a_predictor_that_declines_leaves_live_untouched(self):
        rows = _biased_ledger(bias=1.5)
        rep = wf.walk_forward(rows, lambda train: (lambda row: None))
        assert rep["pooled"]["cand_miss_pct"] == rep["pooled"]["live_miss_pct"]

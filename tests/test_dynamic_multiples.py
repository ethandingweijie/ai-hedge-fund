"""The dynamic multiples engine (Phase 2, owner spec 2026-09-20/21).

Offline: the basket history, the real-rate series and the crack spread are all
stubbed, so every number here is constructed and every assertion is exact.
"""
from __future__ import annotations

import math

import pytest

from src.data import dynamic_multiples as dm
from src.data import regional_comps as rc

YEARS = ["2021", "2022", "2023", "2024", "2025"]


def _daily(annual: dict[str, float]) -> dict[str, float]:
    """A daily series whose annual means and latest month are the given values."""
    out = {}
    for y, v in annual.items():
        for m in range(1, 13):
            out[f"{y}-{m:02d}-15"] = v
    return out


@pytest.fixture
def world(monkeypatch):
    """A refining-like basket; knobs set per test through the returned dict."""
    state = {
        "mult": {"2021": 4.25, "2022": 5.39, "2023": 5.44, "2024": 4.76, "2025": 5.54},
        "roic": {"2021": 0.057, "2022": 0.165, "2023": 0.113, "2024": 0.060, "2025": 0.050},
        "rate": {"2021": -0.010, "2022": 0.000, "2023": 0.015, "2024": 0.020, "2025": 0.020},
        "crack": {"2021": 20.0, "2022": 40.0, "2023": 30.0, "2024": 22.0, "2025": 24.0},
    }

    def load_history(exchange, key, field, cohort="all", level=None):
        src = state["roic"] if field == "roic" else state["mult"]
        return [{"as_of": f"{y}-12-31", "value": v, "peer_count": 8, "source": "backfill",
                 "level": "industry"} for y, v in sorted(src.items())]

    monkeypatch.setattr(rc, "load_history", load_history)
    monkeypatch.setattr(dm, "real_rate_series",
                        lambda *a, **k: {"source": "stub", "series": _daily(state["rate"])})
    monkeypatch.setattr(dm, "crack_spread_series",
                        lambda *a, **k: {"source": "stub", "series": _daily(state["crack"])})
    return state


class TestFitting:
    def test_ols_recovers_known_slopes(self):
        x1 = [0.0, 1.0, 2.0, 3.0, 4.0]
        x2 = [1.0, 0.0, 2.0, 1.0, 3.0]
        y = [1.0 + 0.5 * a - 0.25 * b for a, b in zip(x1, x2)]
        f = dm._ols2(y, x1, x2)
        assert f["b1"] == pytest.approx(0.5)
        assert f["b2"] == pytest.approx(-0.25)
        assert f["r2"] == pytest.approx(1.0)

    def test_ols_refuses_too_few_points(self):
        assert dm._ols2([1, 2, 3], [1, 2, 3], [3, 2, 1]) is None

    def test_five_points_leave_the_prior_in_charge_and_say_so(self, world):
        """n / (n + k) with k = 10: five annual points carry a third of the
        weight. The note must say the prior dominates."""
        b = dm.fit_betas("Oil & Gas Refining & Marketing")
        assert b["n"] == 5
        assert b["shrink_weight"] == pytest.approx(5 / 15)
        assert "prior-dominated" in b["note"]
        assert b["b1"] == pytest.approx(
            b["shrink_weight"] * b["ols"]["b1"] + (1 - b["shrink_weight"]) * b["prior"]["b1"])

    def test_it_fits_the_normalised_multiple_not_the_ttm_one(self, world):
        assert dm.fit_betas("Oil & Gas Refining & Marketing")["field"] == "ev_ebitda_norm"


class TestProposal:
    def test_proposal_carries_its_full_derivation(self, world):
        p = dm.propose("midstream")
        for k in ("baseline", "band", "basket", "real_rate", "roic", "betas",
                  "terms", "raw", "proposed", "flags", "mode"):
            assert k in p, k
        assert p["mode"] == "calibrated"
        assert p["real_rate"]["source"] == "stub"

    def test_it_never_leaves_twenty_percent_of_baseline(self, world, monkeypatch):
        monkeypatch.setitem(dm.PRIORS, "midstream", {"b1": -400.0, "b2": 0.0})
        world["rate"]["2025"] = 0.08          # a big rate shock
        p = dm.propose("midstream")
        assert p["proposed"] >= 10.5 * (1 - dm.MAX_DEVIATION) - 1e-9
        assert any("clamped" in f for f in p["flags"]), "a clamp is never silent"

    def test_it_never_leaves_the_owner_band(self, world, monkeypatch):
        monkeypatch.setitem(dm.PRIORS, "midstream", {"b1": 400.0, "b2": 0.0})
        world["rate"]["2025"] = 0.08
        p = dm.propose("midstream")
        assert p["proposed"] <= dm.SEGMENT_BASELINES["midstream"]["band"][1] + 1e-9

    def test_a_market_outside_the_band_is_a_band_decision_not_a_clamp(self, world):
        """Midstream's through-cycle multiple ran to 14.14x against the original
        9-12x band, was flagged, and the owner re-based the band on it. Any
        market reading above the band is still a question for the owner."""
        world["mult"]["2025"] = dm.SEGMENT_BASELINES["midstream"]["band"][1] + 3.0
        p = dm.propose("midstream")
        assert any("above the owner band" in f for f in p["flags"])

    def test_the_crack_rule_forces_the_trough_multiple(self, world):
        world["crack"]["2025"] = 60.0          # far above the long-run mean
        p = dm.propose("refining")
        assert p["proposed"] == pytest.approx(4.5)
        assert any("crack spread" in r for r in p["rules"])

    def test_no_crack_rule_below_the_threshold(self, world):
        p = dm.propose("refining")
        assert p["rules"] == []

    def test_a_proxy_type_says_it_is_one(self, world):
        assert dm.propose("fuel_marketing")["basket_is_proxy"] is True
        assert dm.propose("midstream")["basket_is_proxy"] is False

    def test_propose_never_writes(self, world, monkeypatch):
        from src.data import valuation_constants as vc
        monkeypatch.setattr(vc, "save", lambda *a, **k: pytest.fail("propose wrote"))
        dm.propose("refining")

    def test_an_unknown_type_raises(self, world):
        with pytest.raises(KeyError):
            dm.propose("hydrogen")


class TestBacktest:
    def test_leave_one_out_reports_both_errors_and_labels_itself(self, world):
        bt = dm.backtest("refining")
        assert bt["n"] == 5
        assert bt["mae_static"] is not None and bt["mae_dynamic"] is not None
        assert "indicative" in bt["note"]


class TestAcceptance:
    def test_the_sotp_uses_an_accepted_multiple_and_says_so(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr(dm, "accepted", lambda t: (
            {"multiple": 5.1, "accepted_at": "2026-09-21",
             "derivation": {"band": [4.5, 6.5]}} if t == "refining" else None))
        p = d._sotp_parts({"Refining": 100e9})[0]
        assert p["multiple"] == pytest.approx(5.1)
        assert "dynamic, accepted 2026-09-21" in p["multiple_source"]
        assert p["band"] == [4.5, 6.5]

    def test_without_acceptance_the_static_band_stands_and_says_so(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr(dm, "accepted", lambda t: None)
        p = d._sotp_parts({"Refining": 100e9})[0]
        assert p["multiple"] == pytest.approx(d._band_multiple(tuple(p["band"])))
        assert p["multiple_source"].startswith("static band")

    def test_accept_records_the_derivation(self, tmp_path, monkeypatch):
        from src.data import valuation_constants as vc
        store = {"version": 1, "profiles": {}}
        monkeypatch.setattr(vc, "load", lambda *a, **k: store)
        monkeypatch.setattr(vc, "save", lambda doc, *a, **k: store.update(doc))
        e = dm.accept({"segment_type": "refining", "proposed": 5.3, "band": [4.5, 6.5]},
                      reviewer="owner")
        assert store["segment_multiples"]["refining"]["multiple"] == 5.3
        assert store["segment_multiples"]["refining"]["derivation"]["band"] == [4.5, 6.5]
        assert e["reviewer"] == "owner"


class TestMacroHelpers:
    def test_latest_is_a_monthly_mean_not_one_print(self):
        s = {"2026-09-01": 1.0, "2026-09-15": 3.0, "2026-06-01": 100.0}
        assert dm.latest(s, days=30) == pytest.approx(2.0)

    def test_quarter_windows_tile_the_year(self):
        from datetime import date
        w = list(dm._quarter_windows(date(2025, 1, 1), date(2025, 12, 31)))
        assert [(a.isoformat(), b.isoformat()) for a, b in w] == [
            ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]


class TestTheGuardsTheDataForced:
    def test_collinear_regressors_fall_back_to_the_prior(self, world):
        """Rates rose steadily 2021-25 while ROIC fell in step: the fit splits
        one trend into two huge offsetting slopes (chemicals: OLS b1 +41.9 at
        R-squared 1.00). Neither is identified, so the prior stands for both."""
        world["rate"] = {"2021": -0.01, "2022": 0.00, "2023": 0.01, "2024": 0.02, "2025": 0.03}
        world["roic"] = {"2021": 0.10, "2022": 0.08, "2023": 0.06, "2024": 0.04, "2025": 0.02}
        b = dm.fit_betas("Chemicals")
        assert abs(b["regressor_corr"]) > dm.COLLINEAR_CORR
        assert "collinear" in b["note"]
        assert b["b1"] == pytest.approx(min(b["prior"]["b1"], 0.0))
        assert b["b2"] == pytest.approx(b["prior"]["b2"])

    def test_a_positive_rate_slope_is_clipped_because_the_spec_says_inverse(self, world, monkeypatch):
        monkeypatch.setitem(dm.PRIORS, "default", {"b1": 50.0, "b2": 0.0})
        b = dm.fit_betas("Oil & Gas Refining & Marketing")
        assert b["b1"] == 0.0
        assert "clipped to 0" in b["note"]

    def test_a_proxy_is_never_compared_against_another_basket_s_level(self, world):
        world["mult"]["2025"] = 3.0          # far below the fuel-marketing band
        p = dm.propose("fuel_marketing")
        assert p["market_multiple_now"] is None
        assert not any("below the owner band" in f for f in p["flags"])
        assert any("no basket of its own" in f for f in p["flags"])


class TestOwnerSetValues:
    """The owner may take a starting point the engine did not propose
    (2026-09-21: refining 4.50x, midstream 14.1x, chemicals 5.2x)."""

    def _store(self, monkeypatch):
        from src.data import valuation_constants as vc
        store = {"version": 1, "profiles": {}}
        monkeypatch.setattr(vc, "load", lambda *a, **k: store)
        monkeypatch.setattr(vc, "save", lambda doc, *a, **k: store.update(doc))
        return store

    def test_an_owner_value_is_recorded_with_the_proposal_beside_it(self, world, monkeypatch):
        store = self._store(monkeypatch)
        e = dm.accept_value("midstream", 14.1, reviewer="owner", basis="market level")
        rec = store["segment_multiples"]["midstream"]
        assert rec["multiple"] == 14.1 and rec["basis"] == "market level"
        assert rec["derivation"]["owner_set"] is True
        assert "proposed" in rec["derivation"], "the engine's own view is kept for the audit"
        assert e["reviewer"] == "owner"

    def test_a_value_outside_its_band_is_refused(self, world, monkeypatch):
        """Move the band first -- a value the band would clamp is not recorded."""
        self._store(monkeypatch)
        with pytest.raises(ValueError):
            dm.accept_value("refining", 9.0, reviewer="owner", basis="x")

    def test_the_rebased_bands_hold_the_owner_starting_points(self):
        for t, v in (("refining", 4.50), ("midstream", 14.1), ("chemicals", 5.2)):
            lo, hi = dm.SEGMENT_BASELINES[t]["band"]
            assert lo <= v <= hi, t
        assert dm.SEGMENT_BASELINES["midstream"]["baseline"] == 14.1
        assert dm.SEGMENT_BASELINES["chemicals"]["baseline"] == 5.2
        assert dm.SEGMENT_BASELINES["refining"]["baseline"] == 5.5

    def test_the_shipped_record_carries_the_three_starting_points(self):
        from src.data import valuation_constants as vc
        sm = vc.load().get("segment_multiples") or {}
        assert {k: v["multiple"] for k, v in sm.items()} == {
            "refining": 4.5, "midstream": 14.1, "chemicals": 5.2}
        assert all(v["reviewer"] == "owner" for v in sm.values())


class TestBandRationale:
    """Every band says why it sits where it does (owner, 2026-09-21)."""

    def test_every_band_has_a_rationale(self):
        for t, cfg in dm.SEGMENT_BASELINES.items():
            assert len(cfg.get("band_rationale") or "") > 80, t

    def test_a_rebased_band_says_what_it_replaced_and_why(self):
        for t, old in (("midstream", "9-12x"), ("chemicals", "7-9x")):
            text = dm.SEGMENT_BASELINES[t]["band_rationale"]
            assert old in text and "superseded" in text and "+/-20%" in text, t

    def test_a_band_with_no_basket_says_it_has_no_market_check(self):
        for t in ("fuel_marketing", "renewable_fuels", "ethanol"):
            assert "no market check" in dm.SEGMENT_BASELINES[t]["band_rationale"], t

    def test_the_proposal_the_record_and_the_sotp_part_all_carry_it(self, world):
        from src.agents.analysis import dcf_agent as d
        from src.data import valuation_constants as vc
        assert dm.propose("midstream")["band_rationale"] == \
            dm.SEGMENT_BASELINES["midstream"]["band_rationale"]
        for t, rec in (vc.load().get("segment_multiples") or {}).items():
            assert rec["band_rationale"] == dm.SEGMENT_BASELINES[t]["band_rationale"], t
        p = d._sotp_parts({"Midstream": 20e9})[0]
        assert p["band_rationale"] == dm.SEGMENT_BASELINES["midstream"]["band_rationale"]

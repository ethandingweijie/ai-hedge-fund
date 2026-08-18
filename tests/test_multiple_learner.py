"""Tests for the learned multiple/margin basis (multiple_learner.py)."""
import json
import math

import pytest

from src.agents.analysis.multiple_learner import (
    ARTIFACT_VERSION,
    MARGIN_CLAMP,
    coefficient,
    fit_model,
    load_artifact,
    predict,
)


def _structured_panel():
    """Log-linear panel with known structure: cloud > retail, growth lifts
    multiples, China discounts, scale lifts slightly. 24 rows."""
    rows = []
    for arch in ("cloud", "retail"):
        for g in (5.0, 15.0, 25.0):
            for cn in (False, True):
                for rev in (10e9, 120e9):
                    rows.append({
                        "ticker": f"T{len(rows)}",
                        "archetype": arch,
                        "china": cn,
                        "pe": math.exp(2.6 + (0.5 if arch == "cloud" else 0.0)
                                       + 0.02 * g
                                       + 0.05 * math.log(rev / 1e9)
                                       - (0.5 if cn else 0.0)),
                        "ev_rev": math.exp(0.8 + (0.4 if arch == "cloud" else 0.0)
                                           + 0.01 * g
                                           - (0.4 if cn else 0.0)),
                        "margin": (0.28 if arch == "cloud" else 0.05)
                                  + (0.02 if rev > 50e9 else 0.0),
                        "g_fwd_pct": g,
                        "log_revenue_scale": math.log(rev),
                    })
    return rows


def test_fit_deterministic_same_input():
    p = _structured_panel()
    a1 = fit_model(p, "2026-08-17")
    a2 = fit_model(p, "2026-08-17")
    assert json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True)


def test_fit_row_order_invariant():
    p = _structured_panel()
    a1 = fit_model(p, "2026-08-17")
    a2 = fit_model(list(reversed(p)), "2026-08-17")
    for t in ("pe", "ev_rev", "margin"):
        assert a1["targets"][t]["coeffs"] == pytest.approx(
            a2["targets"][t]["coeffs"], abs=1e-8)
        assert a1["targets"][t]["r2"] == pytest.approx(
            a2["targets"][t]["r2"], abs=1e-6)


def test_coefficient_signs_and_recovery():
    art = fit_model(_structured_panel(), "2026-08-17")
    # Learned jurisdiction haircut: negative, magnitude near the -0.5 truth.
    gamma = coefficient(art, "pe", "china")
    assert gamma is not None and -0.75 < gamma < -0.25
    assert coefficient(art, "pe", "g_fwd_pct") > 0
    assert coefficient(art, "ev_rev", "china") < 0
    assert coefficient(art, "pe", "no_such_feature") is None
    assert art["version"] == ARTIFACT_VERSION


def test_predict_ci_ordering_and_arch_effect():
    art = fit_model(_structured_panel(), "2026-08-17")
    chars = {"archetype": "cloud", "china": False, "g_fwd_pct": 15.0,
             "log_revenue_scale": math.log(50e9)}
    pred = predict(art, "pe", chars)
    assert pred is not None
    lo, hi = pred["ci"]
    assert lo < pred["point"] < hi
    retail = predict(art, "pe", {**chars, "archetype": "retail"})
    assert pred["point"] > retail["point"]


def test_predict_china_discount_applied():
    art = fit_model(_structured_panel(), "2026-08-17")
    base = {"archetype": "cloud", "g_fwd_pct": 15.0,
            "log_revenue_scale": math.log(50e9)}
    us = predict(art, "pe", {**base, "china": False})
    cn = predict(art, "pe", {**base, "china": True})
    assert cn["point"] < us["point"]


def test_margin_clamped_to_policy_band():
    panel = _structured_panel()
    for r in panel:
        r["margin"] = 0.75
    art = fit_model(panel, "2026-08-17")
    pred = predict(art, "margin", {"archetype": "cloud", "china": False,
                                    "g_fwd_pct": 10.0,
                                    "log_revenue_scale": math.log(50e9)})
    assert pred is not None
    assert pred["point"] <= MARGIN_CLAMP[1]
    assert pred["ci"][1] <= MARGIN_CLAMP[1]
    assert pred["ci"][0] >= MARGIN_CLAMP[0]


def test_thin_or_unseen_archetype_refused():
    panel = _structured_panel()
    panel.append({"ticker": "X1", "archetype": "games_media", "china": False,
                  "pe": 18.0, "ev_rev": 2.0, "margin": 0.10,
                  "g_fwd_pct": 8.0, "log_revenue_scale": math.log(5e9)})
    art = fit_model(panel, "2026-08-17")
    assert art["archetype_n"]["games_media"] == 1
    # Containment gate: thin (< MIN_ARCH_OBS) and unseen archetypes refused.
    assert predict(art, "pe", {"archetype": "games_media"}) is None
    assert predict(art, "pe", {"archetype": "not_fitted"}) is None
    assert predict(art, "pe", {}) is None
    # Well-observed archetypes still predict.
    assert predict(art, "pe", {"archetype": "cloud"}) is not None


def test_small_panel_raises():
    with pytest.raises(ValueError):
        fit_model(_structured_panel()[:4], "2026-08-17")
    with pytest.raises(ValueError):
        fit_model([], "2026-08-17")


def test_artifact_roundtrip_and_validation(tmp_path):
    art = fit_model(_structured_panel(), "2026-08-17")
    p = tmp_path / "art.json"
    p.write_text(json.dumps(art), encoding="utf-8")
    assert load_artifact(p) == art

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_artifact(bad) is None

    wrong_version = dict(art)
    wrong_version["version"] = 99
    p3 = tmp_path / "v.json"
    p3.write_text(json.dumps(wrong_version), encoding="utf-8")
    assert load_artifact(p3) is None

    mismatched = json.loads(json.dumps(art))
    mismatched["targets"]["pe"]["coeffs"] = (
        mismatched["targets"]["pe"]["coeffs"][:-1])
    p4 = tmp_path / "len.json"
    p4.write_text(json.dumps(mismatched), encoding="utf-8")
    assert load_artifact(p4) is None

    assert load_artifact(tmp_path / "nope.json") is None


def test_predict_invalid_artifacts():
    assert predict(None, "pe", {"archetype": "cloud"}) is None
    assert predict({}, "pe", {"archetype": "cloud"}) is None
    art = fit_model(_structured_panel(), "2026-08-17")
    assert predict(art, "ev_ebitda", {"archetype": "cloud"}) is None


def test_r2_high_on_structured_panel():
    art = fit_model(_structured_panel(), "2026-08-17")
    for t in ("pe", "ev_rev", "margin"):
        assert art["targets"][t]["r2"] > 0.9
        assert art["targets"][t]["n_obs"] == 24

"""D4 (owner, 2026-11-27): the bank residual-income leg moves with the scenario like the GGM leg.

ROE (current and target) x (1 +/- _BANK_SCENARIO_ROE_SHIFT), cost of equity -/+ _BANK_SCENARIO_COE_SHIFT. Before,
the leg returned one value in all three scenarios (U11 40.33, D05 39.67) while carrying 30-34% of the blend.
"""
from __future__ import annotations

import pytest

from src.agents.analysis import dcf_agent as d

_PROFILE = "Money Center Bank (SG)"


def _row() -> dict:
    return {"net_income": 6.0e9, "total_equity": 50.0e9, "total_assets": 500.0e9, "shares_outstanding": 1.67e9,
            "book_value_per_share": 30.0, "dividends_per_share": 1.8, "revenue": 14.0e9,
            "interest_income": 20.0e9, "interest_expense": 10.0e9, "operating_expense": 5.0e9,
            "goodwill": 4.0e9, "intangible_assets": 0.0}


def _manual(row: dict, sign: float) -> float:
    """The 2-stage RI with the scenario drivers applied by hand (the prototype's monkeypatch, done inline)."""
    bm = d._compute_bank_metrics(row, _PROFILE)
    cfg = d._bank_profile_calibration(_PROFILE)
    roe = max(-0.05, min(0.50, bm["roe"] * (1 + sign * d._BANK_SCENARIO_ROE_SHIFT)))
    tgt = cfg["target_roe"] * (1 + sign * d._BANK_SCENARIO_ROE_SHIFT)
    coe = cfg["coe"] - sign * d._BANK_SCENARIO_COE_SHIFT
    n, ret, bv = cfg["fade_years"], bm["retention_rate"], row["book_value_per_share"]
    v = bv
    for t in range(1, n + 1):
        r_t = roe + (tgt - roe) * t / n
        v += (r_t - coe) * bv / (1 + coe) ** t
        bv *= 1 + ret * r_t
    v += (tgt - coe) * bv / (1 + coe - d._RI_PERSISTENCE) / (1 + coe) ** n
    floor = bm.get("tbv_per_share") or row["book_value_per_share"] * 0.85
    return max(v, floor * 0.5)


def test_ri_moves_with_the_scenario():
    row = _row()
    sh = row["shares_outstanding"]
    bear, base, bull = (d._compute_residual_income_2stage(row, sh, _PROFILE, scenario_sign=s) for s in (-1, 0, 1))
    assert bear < base < bull
    assert base == pytest.approx(d._compute_residual_income_2stage(row, sh, _PROFILE))     # base unchanged
    for s, v in ((-1, bear), (0, base), (1, bull)):
        assert v == pytest.approx(_manual(row, s), rel=1e-9)


def test_method_value_passes_the_scenario_sign():
    import inspect
    src = inspect.getsource(d._compute_method_value)
    assert "scenario_sign=_sg_ri" in src
    assert "_sg_ri = -1.0 if sm < 1.0 else (1.0 if sm > 1.0 else 0.0)" in src

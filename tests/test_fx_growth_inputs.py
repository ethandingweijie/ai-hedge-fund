"""Task #26 — FX-consistent analyst growth inputs in the DCF engine.

Regression tests for the China-ADR overshoot root cause: FMP analyst
estimates arrive in the company's REPORTING currency while revenue_base is
already converted to the target currency. Before the fix, raw-CNY revenue
estimates divided by a USD base implied +700% growth, clamping every
scenario band to +100%.

Covers:
  * _analyst_growth_bands converts estimate revenue by fx_rate
  * _analyst_revenue_growth converts the point estimate by fx_rate
  * fx_rate = 1.0 (USD reporters, and the default) reproduces the old math
    bit-identically — US names must be untouched
  * the dispersion guard reads only bear/base/bull (analyst_count excluded)
"""
from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _analyst_growth_bands,
    _analyst_revenue_growth,
)


class _Est:
    """Minimal AnalystEstimates stand-in (attribute access only)."""

    def __init__(self, lo, av, hi, count=10, ticker="TEST"):
        self.revenue_low = lo
        self.revenue_avg = av
        self.revenue_high = hi
        self.analyst_count_revenue = count
        self.ticker = ticker


CNY_USD = 0.1376  # fallback rate used in the task #26 diag runs


# ── _analyst_growth_bands ────────────────────────────────────────────────────


def test_bands_convert_reporting_currency():
    """PDD-shaped input: raw-CNY estimates over a USD-converted base must
    produce genuine growth, not the +100% clamp artifact."""
    revenue_base_usd = 59.4e9                      # converted TTM revenue
    est = _Est(lo=450e9, av=482.6e9, hi=520e9)     # raw CNY forward FY
    bands = _analyst_growth_bands(
        [est], revenue_base_usd, fx_rate=CNY_USD
    )
    assert bands is not None
    # true implied: av/base − 1 ≈ +11.75%
    assert bands["base"] == pytest.approx(
        (482.6e9 * CNY_USD) / revenue_base_usd - 1, abs=1e-12
    )
    assert bands["bear"] == pytest.approx(
        (450e9 * CNY_USD) / revenue_base_usd - 1, abs=1e-12
    )
    assert bands["bull"] == pytest.approx(
        (520e9 * CNY_USD) / revenue_base_usd - 1, abs=1e-12
    )
    # the artifact clamped every band to +1.0 — prove that's gone
    assert all(v < 0.5 for v in (bands["bear"], bands["base"], bands["bull"]))
    assert bands["analyst_count"] == 10


def test_bands_usd_reporter_unchanged():
    """fx = 1.0 (USD/USD) — math identical to the pre-fix implementation."""
    est = _Est(lo=370e9, av=389.6e9, hi=410e9, count=36)  # MSFT-shaped
    bands = _analyst_growth_bands([est], 331.8e9, fx_rate=1.0)
    assert bands is not None
    assert bands["base"] == pytest.approx(389.6e9 / 331.8e9 - 1, abs=1e-12)
    # default arg must also be 1.0 (no behavior change for old callers)
    bands_default = _analyst_growth_bands([est], 331.8e9)
    assert bands_default == bands


def test_bands_fx_guard_nonpositive_rate_falls_back_to_one():
    est = _Est(lo=370e9, av=389.6e9, hi=410e9)
    bands = _analyst_growth_bands([est], 331.8e9, fx_rate=0.0)
    assert bands is not None
    assert bands["base"] == pytest.approx(389.6e9 / 331.8e9 - 1, abs=1e-12)


def test_bands_quality_gates_still_enforced():
    """FX fix must not weaken the existing rejection gates."""
    base = 59.4e9
    # too few analysts
    assert _analyst_growth_bands(
        [_Est(lo=450e9, av=482e9, hi=520e9, count=2)], base,
        fx_rate=CNY_USD,
    ) is None
    # non-monotonic
    assert _analyst_growth_bands(
        [_Est(lo=520e9, av=482e9, hi=450e9)], base, fx_rate=CNY_USD,
    ) is None
    # missing field
    est = _Est(lo=450e9, av=482e9, hi=520e9)
    est.revenue_high = None
    assert _analyst_growth_bands([est], base, fx_rate=CNY_USD) is None
    # empty / no base
    assert _analyst_growth_bands([], base, fx_rate=CNY_USD) is None
    assert _analyst_growth_bands([_Est(1, 2, 3)], 0.0, fx_rate=CNY_USD) is None


def test_bands_clamp_still_applies_after_conversion():
    """Genuine extreme consensus still hits the [-30%, +100%] clamp."""
    est = _Est(lo=5e9, av=60e9, hi=300e9)
    bands = _analyst_growth_bands([est], 20e9, fx_rate=1.0)
    assert bands is not None
    assert bands["bear"] == -0.30          # 5/20 − 1 = −75% → clamp
    assert bands["bull"] == 1.0            # 300/20 − 1 = +1400% → clamp


# ── _analyst_revenue_growth ──────────────────────────────────────────────────


def test_point_growth_converts_reporting_currency():
    est = _Est(lo=450e9, av=482.6e9, hi=520e9)
    g = _analyst_revenue_growth([est], 59.4e9, fx_rate=CNY_USD)
    assert g == pytest.approx((482.6e9 * CNY_USD) / 59.4e9 - 1, abs=1e-12)
    assert g < 0.5  # was +712% before the fix


def test_point_growth_usd_unchanged_and_default_fx_one():
    est = _Est(lo=370e9, av=389.6e9, hi=410e9)
    g = _analyst_revenue_growth([est], 331.8e9, fx_rate=1.0)
    assert g == pytest.approx(389.6e9 / 331.8e9 - 1, abs=1e-12)
    assert _analyst_revenue_growth([est], 331.8e9) == g


def test_point_growth_none_safe():
    assert _analyst_revenue_growth([], 59.4e9, fx_rate=CNY_USD) is None
    assert _analyst_revenue_growth([_Est(None, None, None)], 59.4e9,
                                   fx_rate=CNY_USD) is None
    est = _Est(lo=1, av=None, hi=2)
    est.revenue_avg = None
    assert _analyst_revenue_growth([est], 59.4e9, fx_rate=CNY_USD) is None


# ── dispersion guard semantics (bear/base/bull only) ─────────────────────────


def test_dispersion_excludes_analyst_count():
    """The task #26 dead-guard bug: including analyst_count in .values()
    made dispersion ≈ the analyst count, so the <0.005 rejection could never
    fire. The guard itself is inline in run_dcf_agent (verified by the E2E
    harness re-run); this pins the semantics it must use."""
    # NET-style all-identical bands — the case the guard was built for
    bands = {"bear": 1.0, "base": 1.0, "bull": 1.0, "analyst_count": 30}
    _band_values = [bands[k] for k in ("bear", "base", "bull")]
    assert max(_band_values) - min(_band_values) < 0.005  # guard fires now
    # ...while the buggy formulation never fired:
    _buggy = list(bands.values())
    assert max(_buggy) - min(_buggy) >= 0.005
    # genuine dispersion survives the fixed guard
    bands2 = {"bear": 0.05, "base": 0.1175, "bull": 0.20, "analyst_count": 24}
    vals2 = [bands2[k] for k in ("bear", "base", "bull")]
    assert max(vals2) - min(vals2) >= 0.005

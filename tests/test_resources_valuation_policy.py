"""Resources valuation policy: normalised multiples + a depleting-asset DCF.

`Mining (Major)` previously anchored NAV (LoM) at 0.60 and P/NAV at 0.20 with
both marked `implementable: False` -- 80% of a miner's valuation was a generic
corporate DCF and P/BV wearing mine-life labels. `Upstream Oil & Gas` was 90%
proxied the same way. The proxy erred in a known DIRECTION: a perpetual
terminal growth term on a DEPLETING asset values ore that does not exist.
"""
import pytest

from src.agents.analysis.dcf_agent import (
    _DEPLETING_DCF, _DEPLETING_HORIZON_YEARS, _DCF_PROJECTION_FAMILY,
    _project_dcf,
)
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P

_ARGS = dict(revenue_base=10_000e6, fcf_margin_base=0.20, growth_rate=0.03,
             margin_delta_per_year=0.0, wacc=0.10, fcf_floor=0.0,
             net_debt=1_000e6, shares=1_000e6)


class TestFiniteHorizonDCF:
    def test_no_terminal_value_is_actually_zero(self):
        _iv, _pv_fcf, pv_tv, _rows = _project_dcf(
            tgr=0.0, years=_DEPLETING_HORIZON_YEARS,
            include_terminal=False, **_ARGS)
        assert pv_tv == 0.0

    def test_zero_growth_is_NOT_the_same_as_no_terminal_value(self):
        """The trap this method exists to avoid.

        Gordon with g=0 still returns FCF/WACC -- a perpetuity worth roughly
        ten years of cash flow. Setting tgr=0 and believing the terminal value
        is gone leaves most of the valuation in a perpetuity on a mine that
        will be exhausted.
        """
        iv_g0, _f, pv_tv_g0, _r = _project_dcf(tgr=0.0, years=5, **_ARGS)
        assert pv_tv_g0 > 0
        assert pv_tv_g0 / iv_g0 > 0.5, "g=0 still carries most of the value in TV"

    def test_depleting_is_materially_below_the_perpetual_proxy(self):
        iv_perp, _f, _t, _r = _project_dcf(tgr=0.025, years=5, **_ARGS)
        iv_dep, _f2, _t2, _r2 = _project_dcf(
            tgr=0.0, years=_DEPLETING_HORIZON_YEARS,
            include_terminal=False, **_ARGS)
        assert iv_dep < iv_perp
        assert iv_dep / iv_perp - 1 < -0.15

    def test_all_value_comes_from_projected_cash_flows(self):
        iv, pv_fcf, pv_tv, rows = _project_dcf(
            tgr=0.0, years=_DEPLETING_HORIZON_YEARS,
            include_terminal=False, **_ARGS)
        assert len(rows) == _DEPLETING_HORIZON_YEARS
        assert pv_tv == 0.0
        # iv is per-share equity: pv of flows less net debt
        assert iv == pytest.approx(pv_fcf - _ARGS["net_debt"] / _ARGS["shares"])

    def test_terminal_value_is_still_on_by_default(self):
        """Every other profile must be unaffected."""
        _iv, _f, pv_tv, _r = _project_dcf(tgr=0.025, years=5, **_ARGS)
        assert pv_tv > 0


class TestNomenclature:
    def test_the_method_is_not_called_a_life_of_mine_nav(self):
        """A LoM NAV ingests proven & probable reserves, recovery rates and a
        price deck. None of that telemetry exists, so the label must not claim
        a reserve model was run."""
        assert "NAV (LoM)" not in _DEPLETING_DCF
        assert "Finite Life" in _DEPLETING_DCF and "No TV" in _DEPLETING_DCF

    def test_the_old_labels_are_gone_from_resources(self):
        for prof in ("Mining (Major)", "Upstream Oil & Gas"):
            names = {m["name"] for m in P["Resources"][prof]["methods"]}
            assert "NAV (LoM)" not in names
            assert "NAV (PV-10)" not in names

    def test_the_omission_is_stated_not_hidden(self):
        for prof in ("Mining (Major)", "Upstream Oil & Gas"):
            lim = P["Resources"][prof].get("data_limitation") or ""
            assert "omitted" in lim.lower(), prof
            assert "reserve" in lim.lower(), prof


class TestResourcesWeights:
    @pytest.mark.parametrize("prof,anchor", [
        ("Mining (Major)", "EV/EBITDA (norm)"),
        ("Upstream Oil & Gas", "P/CF"),
    ])
    def test_anchored_on_a_computable_normalised_multiple(self, prof, anchor):
        ms = {m["name"]: m for m in P["Resources"][prof]["methods"]}
        assert ms[anchor]["anchor"] is True
        assert ms[anchor]["implementable"] is True
        assert ms[anchor]["weight"] == pytest.approx(0.60)

    @pytest.mark.parametrize("prof", ["Mining (Major)", "Upstream Oil & Gas"])
    def test_nothing_is_proxied_any_more(self, prof):
        ms = P["Resources"][prof]["methods"]
        assert sum(m["weight"] for m in ms if not m.get("implementable")) == 0.0
        assert round(sum(m["weight"] for m in ms), 6) == 1.0

    @pytest.mark.parametrize("prof", ["Mining (Major)", "Upstream Oil & Gas"])
    def test_the_depleting_dcf_carries_thirty_percent(self, prof):
        ms = {m["name"]: m for m in P["Resources"][prof]["methods"]}
        assert ms[_DEPLETING_DCF]["weight"] == pytest.approx(0.30)
        assert ms[_DEPLETING_DCF]["implementable"] is True

    def test_it_is_not_in_the_perpetual_projection_family(self):
        """Membership there would route it back through the standard TV path."""
        assert _DEPLETING_DCF not in _DCF_PROJECTION_FAMILY

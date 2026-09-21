"""The base WACC is computed in one place, `wacc_base_breakdown`, and the
valuation export shows its components. Both must agree with the formula the
engine used before the refactor, for every input."""
from itertools import product

import pytest

from src.data import sector_profiles as sp

_SECTORS = sorted(set(sp.SECTOR_WACC) | set(sp.HK_SECTOR_WACC) | set(sp.SG_SECTOR_WACC)
                  | {"REIT", "RealEstate", "NotASector"})
_PROFILES = [""] + list(sp._ENERGY_PROFILE_WACC) + list(sp._FINANCIALS_PROFILE_WACC)
_GRID = list(product(_SECTORS, _PROFILES, (0.0, 1.2, 1.5, 2.3, 6.0),
                     ("risk-off", "neutral", "risk-on", "unknown"),
                     ((False, False), (True, False), (False, True))))


def _reference(sector, leverage, macro_regime, profile, is_hk, is_sg):
    """The inline formula `get_wacc_for_exchange` carried before 2026-09-19."""
    if is_sg:
        overlay = sp._MACRO_WACC_OVERLAY.get(macro_regime, 0.0)
        if sector == "Energy" and profile in sp._ENERGY_PROFILE_WACC:
            base = sp._ENERGY_PROFILE_WACC[profile] + sp._SG_CRP
            lev_cap = sp._ENERGY_LEVERAGE_CAP.get(profile, 0.035)
        elif sector == "Financials" and profile in sp._FINANCIALS_PROFILE_WACC:
            base = sp._FINANCIALS_PROFILE_WACC[profile] + sp._SG_CRP
            lev_cap = sp._FINANCIALS_LEVERAGE_CAP.get(profile, 0.010)
        else:
            base = sp.SG_SECTOR_WACC.get(sector, sp.SECTOR_WACC.get(sector, 0.090) + sp._SG_CRP)
            lev_cap = 0.040
        prem = max(0.0, (leverage - 1.5) * 0.01)
        return round(min(base + prem + overlay, base + lev_cap), 4)
    if not is_hk:
        return sp.get_wacc(sector, leverage, macro_regime=macro_regime, profile=profile)
    overlay = sp._MACRO_WACC_OVERLAY.get(macro_regime, 0.0)
    if sector == "Energy" and profile in sp._ENERGY_PROFILE_WACC:
        base = sp._ENERGY_PROFILE_WACC[profile] + sp._HK_CHINA_CRP
        lev_cap = sp._ENERGY_LEVERAGE_CAP.get(profile, 0.035)
    elif sector == "Financials" and profile in sp._FINANCIALS_PROFILE_WACC:
        base = sp._FINANCIALS_PROFILE_WACC[profile] + sp._HK_CHINA_CRP
        lev_cap = sp._FINANCIALS_LEVERAGE_CAP.get(profile, 0.010)
    else:
        base = sp.HK_SECTOR_WACC.get(sector, sp.SECTOR_WACC.get(sector, 0.090) + sp._HK_CHINA_CRP)
        lev_cap = 0.040
    prem = max(0.0, (leverage - 1.5) * 0.01)
    return round(min(base + prem + overlay, base + lev_cap), 4)


def test_the_breakdown_reproduces_the_pre_refactor_formula_everywhere():
    bad = [(g, sp.get_wacc_for_exchange(g[0], g[2], macro_regime=g[3], profile=g[1],
                                        is_hk=g[4][0], is_sg=g[4][1]))
           for g in _GRID
           if sp.get_wacc_for_exchange(g[0], g[2], macro_regime=g[3], profile=g[1],
                                       is_hk=g[4][0], is_sg=g[4][1])
           != _reference(g[0], g[2], g[3], g[1], g[4][0], g[4][1])]
    assert not bad, bad[:5]
    assert len(_GRID) > 10_000


@pytest.mark.parametrize("sector,profile", [("Tech", ""), ("REIT", ""), ("Energy", "Regulated Utility"),
                                            ("Financials", "Money Center Bank"), ("NotASector", "")])
def test_the_components_rebuild_the_rate(sector, profile):
    for lev, reg, (hk, sg) in product((0.0, 2.0, 9.0), ("risk-off", "risk-on"),
                                      ((False, False), (True, False), (False, True))):
        b = sp.wacc_base_breakdown(sector, lev, macro_regime=reg, profile=profile, is_hk=hk, is_sg=sg)
        prem = (max(0.0, (b["leverage"] - b["leverage_threshold"]) * b["leverage_slope"])
                if b["leverage_premium_applies"] else 0.0)
        rebuilt = round(min(b["table_rate"] + prem + b["macro_overlay"],
                            b["table_rate"] + b["leverage_cap"]), 4)
        assert rebuilt == b["wacc"]
        assert b["cap_binding"] == (b["table_rate"] + prem + b["macro_overlay"]
                                    > b["table_rate"] + b["leverage_cap"])


def test_us_reits_are_exempt_from_the_leverage_premium_and_hk_sg_are_not():
    assert sp.wacc_base_breakdown("REIT", 9.0)["leverage_premium"] == 0.0
    assert sp.wacc_base_breakdown("REIT", 9.0, is_hk=True)["leverage_premium"] > 0.0


def test_the_older_table_names_are_the_registry_entries_not_copies():
    """The registry generalises the two tables; the old names must stay bound
    to the same objects so neither can drift from the other."""
    assert sp._PROFILE_WACC["Energy"] is sp._ENERGY_PROFILE_WACC
    assert sp._PROFILE_WACC["Financials"] is sp._FINANCIALS_PROFILE_WACC
    assert sp._PROFILE_LEVERAGE_CAP["Energy"][0] is sp._ENERGY_LEVERAGE_CAP
    assert sp._PROFILE_LEVERAGE_CAP["Financials"][0] is sp._FINANCIALS_LEVERAGE_CAP


def test_a_new_sector_is_one_registry_entry_and_no_new_branch(monkeypatch):
    monkeypatch.setitem(sp._PROFILE_WACC, "Industrials", {"Defense Primes": 0.071})
    us = sp.wacc_base_breakdown("Industrials", profile="Defense Primes")
    assert us["table"] == "Industrials profile WACC (Damodaran)"
    assert us["table_rate"] == pytest.approx(0.071)
    assert us["leverage_cap"] == sp._DEFAULT_LEVERAGE_CAP
    assert sp.get_wacc("Industrials", profile="Defense Primes") == us["wacc"]
    hk = sp.wacc_base_breakdown("Industrials", profile="Defense Primes", is_hk=True)
    assert hk["table_rate"] == pytest.approx(0.071 + sp._HK_CHINA_CRP)
    # A profile the sector does not list keeps the flat sector rate.
    flat = sp.wacc_base_breakdown("Industrials", profile="Capital Goods")
    assert flat["table"] == "US sector WACC (Damodaran)"
    assert flat["table_rate"] == sp.SECTOR_WACC["Industrials"]

"""The 14 profiles no routing row or pin reached were removed (owner, 2026-09-27).

Nine had no path at all (EM Bank (Premium), Neo/Challenger, EPC Contractor, Animal Health, the
Biopharma copy of Managed Care, Medical Devices, Payment Processors, OSAT / Packaging, Backlog-Gated
Long Cycle's gate apart); five were reached only by the ratio ladder or a sector default. Every
branch that returned one now returns a profile that exists, and the ladder can never hand the engine
a name the taxonomy does not hold.
"""
import pytest

from src.data import sector_profiles as sp
from src.agents.routing import strategic_router as sr

REMOVED = ["Pre-Revenue Tech", "EPC Contractor", "EM Bank (Premium)", "Neo/Challenger", "Animal Health",
           "Medical Devices", "Backlog-Gated Long Cycle", "Payment Processors", "OSAT / Packaging",
           "Early Platform", "High-Growth Tech / AI", "Levered Subscription", "Stable Growth"]
P = sp.INDUSTRY_VALUATION_PROFILES


def test_the_profiles_are_gone_and_managed_care_has_one_home():
    names = {p for ps in P.values() for p in ps}
    assert not set(REMOVED) & names
    assert "Managed Care" not in P["Biopharma"] and "Managed Care" in P["HealthcareServices"]
    assert sum(len(v) for v in P.values()) == 145   # 135 after the removal; +10 with Wave 10 (owner, 2026-09-27)


def test_the_sector_defaults_point_at_real_profiles():
    for table in (sp._SECTOR_PROFILE_DEFAULT, sr._SECTOR_PROFILE_DEFAULT):
        assert table["Telco"] == "Telecom Carrier" and table["Crypto"] == "Crypto Exchange"
        for sec, prof in table.items():
            if sec in P:
                assert prof in P[sec], (sec, prof)


@pytest.mark.parametrize("sector", sorted(P))
@pytest.mark.parametrize("cagr,fcf,de,rev,pre", [
    (0.60, -0.30, 0.2, 5e8, True), (0.30, 0.10, 0.5, 5e9, False), (0.05, 0.02, 3.0, 2e10, False),
    (0.15, 0.20, 0.1, 2e11, False), (0.02, -0.05, 1.0, 1e9, False), (0.25, 0.01, 0.8, 3e9, False),
])
def test_the_ladder_only_returns_profiles_that_exist(sector, cagr, fcf, de, rev, pre):
    got = sp.classify_valuation_profile(sector=sector, revenue_cagr=cagr, fcf_margin=fcf,
                                        debt_to_equity=de, revenue_base=rev, is_pre_revenue=pre)
    assert got == "" or got in P[sector if sector != "REIT" else "RealEstate"], (sector, got)

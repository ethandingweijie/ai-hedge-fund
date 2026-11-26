"""Singtel look-through (owner, 2026-11-26) and the live USD FX cross it needed."""
import json
from pathlib import Path

from src.agents.analysis import holdco_sotp as h
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
from src.tools import api

_DOC = json.loads((Path(__file__).resolve().parents[1] / "src/data/holdco_sotp_templates.json")
                  .read_text(encoding="utf-8"))
TPL = _DOC["templates"]["Z74.SI"]


def test_every_part_is_sourced_and_the_owner_bands_are_recorded():
    assert TPL["holdco_discount"] == [0.10, 0.20]
    by = {d["name"].split(" (")[0]: d for d in TPL["divisions"]}
    assert by["Bharti Airtel"]["stake_pct"] == 0.269 and by["Bharti Airtel"]["listed"] == "BHARTIARTL.NS"
    assert by["Advanced Info Service"]["stake_pct"] == 0.248
    assert by["Gulf Development"]["stake_pct"] == 0.0495
    assert by["Telkomsel"]["basis"] == "pe_range" and by["Telkomsel"]["multiple_range"] == [8.3, 11.3]
    assert by["Globe Telecom"]["basis"] == "fixed_value" and by["Globe Telecom"]["currency"] == "PHP"
    assert by["Consolidated operations"]["multiple_range"] == [7.5, 8.8]
    assert all(d.get("source") for d in TPL["divisions"])


def test_thai_and_philippine_listings_carry_their_own_currency():
    # Unmapped suffixes fall through to USD: AIS would be read as USD 984bn.
    assert h.currency_of("ADVANC.BK") == "THB" and h.currency_of("GULF.BK") == "THB"
    assert h.currency_of("GLO.PS") == "PHP"


def test_the_sg_telco_slot_is_the_look_through_not_a_published_table():
    methods = {m["name"]: m["weight"] for m in next(v["Telco / Infrastructure (SG)"] for v in INDUSTRY_VALUATION_PROFILES.values() if "Telco / Infrastructure (SG)" in v)["methods"]}
    assert methods == {"EV/EBITDA": 0.4, "SOTP / NAV (look-through)": 0.35, "DCF": 0.25}


def test_a_pair_quoted_only_against_usd_crosses_live_instead_of_returning_one(monkeypatch):
    # PHPSGD is not in the FMP batch; PHPUSD and USDSGD are. It used to return 1.0.
    monkeypatch.setattr(api, "_get_fx_batch", lambda key=None: {
        "PHPUSD": {"price": 0.0157}, "USDSGD": {"price": 1.29}})
    assert abs(api.get_fx_rate("PHP", "SGD") - 0.0157 * 1.29) < 1e-9
    # and through an inverse-only leg (USDTHB only)
    monkeypatch.setattr(api, "_get_fx_batch", lambda key=None: {
        "USDTHB": {"price": 34.0}, "USDSGD": {"price": 1.29}})
    assert abs(api.get_fx_rate("THB", "SGD") - 1.29 / 34.0) < 1e-9

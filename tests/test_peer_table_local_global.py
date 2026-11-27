"""Peer comparison table: local + global peers from the comps store (owner, 2026-11-27).
UOB was compared with JPM/BAC/GS/MS and Alibaba with AMD/AVGO/MSFT/GOOGL."""
import pytest

from src.agents.analysis import peer_comparison as pc


def _m(sym, name, cap, pe=None, pb=None):
    metrics = {}
    if pe is not None:
        metrics["pe"] = {"value": pe, "in_band": True}
    if pb is not None:
        metrics["pb"] = {"value": pb, "in_band": True}
    metrics["ev_ebitda"] = {"value": 0.0, "in_band": False}
    return {"symbol": sym, "name": name, "market_cap": cap, "metrics": metrics}


STORE = {
    ("SES", "Banks"): [_m("D05.SI", "DBS Group Holdings Ltd", 188e9, 16.9, 2.68)],
    ("SES", "Banks - Regional"): [_m("O39.SI", "Oversea-Chinese Banking Corporation Limited", 119e9, 19.2, 1.94),
                                  _m("U11.SI", "United Overseas Bank Limited", 58e9, 9.0, 1.18)],
    ("US", "Banks - Diversified"): [_m("JPM", "JPMorgan Chase & Co.", 825e9, 13.0, 2.2), _m("BAC", "Bank of America", 350e9, 12, 1.3)],
    ("SHH", "Banks - Regional"): [_m("601398.SS", "Industrial & Commercial Bank of China Ltd.", 3350e9, 9.2, 0.8)],
    ("HKSE", "Banks - Diversified"): [_m("1398.HK", "Industrial and Commercial Bank of China Limited", 3689e9, 9.2, 0.8)],
    ("JPX", "Banks - Diversified"): [_m("8306.T", "Mitsubishi UFJ Financial Group", 30e12, 14, 1.2)],
}
FX = {"SGD": 0.77, "HKD": 0.128, "CNY": 0.14, "JPY": 0.0066, "KRW": 0.00072}


@pytest.fixture
def store(monkeypatch):
    import src.data.regional_comps as rc
    import src.tools.api as api
    monkeypatch.setattr(rc, "get_fmp_classification", lambda t: {
        "U11.SI": {"exchange": "SES", "industry": "Banks - Regional", "name": "United Overseas Bank Limited"}}.get(t, {}))
    monkeypatch.setattr(rc, "load_members", lambda ex, lvl, key, cohort="all": list(STORE.get((ex, key), [])))
    monkeypatch.setattr(api, "get_fx_rate", lambda a, b: FX.get(a))


def test_uob_gets_singapore_banks_local_and_one_global_bank_per_region(store):
    t = pc.localized_peer_table("U11.SI", {"ticker": "U11.SI", "pe_ratio": 9.0})
    local = [k for k, r in t.items() if r.get("group") == "local"]
    glob = [k for k, r in t.items() if r.get("group") == "global"]
    assert local == ["D05.SI", "O39.SI"]                       # DBS "Banks" pooled with OCBC "Banks - Regional"
    assert "U11.SI" not in local and t["U11.SI"]["is_subject"]
    assert glob[0] == "8306.T" or "JPM" in glob
    assert not {"601398.SS", "1398.HK"} <= set(glob)           # ICBC A and H are one company, one region
    assert {"JPM", "8306.T"} <= set(glob)
    assert t["D05.SI"]["pe_ratio"] == 16.9 and t["D05.SI"]["ev_ebitda"] is None   # out-of-band prints as "—"
    assert t["D05.SI"]["market"] == "SG" and t["JPM"]["market"] == "US"
    assert not {"BAC", "GS", "MS"} & set(t)


def test_localize_replaces_sector_list_and_drops_wrong_market_fallback(store, monkeypatch):
    data = {"peer_comparison": {
        "U11.SI": {"U11.SI": {"ticker": "U11.SI"}, "JPM": {}, "BAC": {}, "GS": {}, "MS": {}},
        "9999.XX": {"9999.XX": {"ticker": "9999.XX"}, "AMD": {}},
        "AAPL": {"AAPL": {"ticker": "AAPL"}, "MSFT": {}}}}
    out = pc.localize_peer_tables(data)
    assert "D05.SI" in out["U11.SI"] and "GS" not in out["U11.SI"]
    assert list(out["9999.XX"]) == ["9999.XX"]                  # no comps: own row only, never US peers
    assert "MSFT" in out["AAPL"]                                 # US subject without comps keeps the sector list


def test_pipeline_localizes_after_join():
    import inspect
    import src.pipeline as p
    assert "localize_peer_tables(state[\"data\"])" in inspect.getsource(p)

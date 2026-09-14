"""SOTP ground truth: segment-level grading and the live plausibility band.

Reference: the BABA consensus SOTP framework ($152-201/ADS total; China
commerce $60-75, cloud $35-45, AIDC $12-18, Cainiao $8-12, media & other
$4-7; Ant stake + net cash & investments $33-44).
"""
import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.analysis.sotp_ground_truth import check_table, ground_truth_for
from src.agents.analysis.sotp_snapshot import load_sotp_snapshot

ADS = 2.39e9            # BABA ADS-equivalent shares


def _snapshot_table(shares=ADS, fx=1.0):
    return _sotp_analyst_style(load_sotp_snapshot()["BABA"], shares=shares,
                               fx_to_reporting=fx)


def test_the_snapshot_total_is_in_range_for_the_wrong_reasons():
    g = check_table("BABA", _snapshot_table())
    assert g["total"]["status"] == "in_range" and g["plausible"]
    seg = {s["name"]: s for s in g["segments"]}
    assert seg["China E-Commerce"]["status"] == "above"          # ~ $129 vs $60-75
    assert seg["China E-Commerce"]["value_per_ads"] > 100
    assert seg["Cloud & AI Infrastructure"]["status"] == "in_range"
    assert g["balance_sheet"]["status"] == "below"               # net debt, not net cash
    assert "China E-Commerce" in g["off_range"]
    assert g["unmatched_rows"] == []


def test_the_hk_line_grades_identically_in_ads_terms():
    us = check_table("BABA", _snapshot_table())
    hk = check_table("09988.HK", _snapshot_table(shares=ADS * 8, fx=7.8))
    assert hk["total"]["value_per_ads"] == pytest.approx(us["total"]["value_per_ads"], rel=1e-6)
    assert [s["value_per_ads"] for s in hk["segments"]] == \
        pytest.approx([s["value_per_ads"] for s in us["segments"]], rel=1e-6)


def test_the_25_aug_live_extraction_fails_the_plausibility_band():
    # 8x P/E on EBIT $7.56B at 15% tax -> $51.4B commerce, the production run
    live = {"segments": [{"name": "China Commerce", "revenue_fwd": 60e9,
                          "ebit": 7.56e9, "pe_multiple": 8.0}],
            "holdco_discount_pct": 0.15, "net_cash": 30e9, "associates_investments": 40e9}
    g = check_table("BABA", _sotp_analyst_style(live, shares=ADS))
    assert g["total"]["status"] == "below" and g["plausible"] is False


def test_the_band_is_widened_by_the_tolerance_not_exact():
    entry, _ = ground_truth_for("BABA")
    table = {"shares": ADS, "per_share": 152 * 0.9, "rows": [], "associates": 0, "net_cash": 0}
    assert check_table("BABA", table)["plausible"] is True      # 10% under, inside 15%
    table["per_share"] = 152 * 0.8
    assert check_table("BABA", table)["plausible"] is False
    assert entry["ads_ratio"] == 8


_LIVE_61 = {"segments": [{"name": "China Commerce", "revenue_fwd": 60e9,
                          "ebit": 7.56e9, "pe_multiple": 8.0}],
            "holdco_discount_pct": 0.15, "net_cash": 30e9,
            "associates_investments": 40e9, "fx_usd_to_reporting": 7.8}


def test_gate_replaces_an_implausible_live_value_with_the_snapshot():
    from src.agents.analysis.dcf_agent import _gate_live_sotp
    out, flag = _gate_live_sotp("09988.HK", _LIVE_61, ADS * 8, None)
    assert out["_origin"] == "snapshot:BABA"
    assert out["segments"] == load_sotp_snapshot()["BABA"]["segments"]
    assert out["fx_usd_to_reporting"] == 7.8                 # the run's FX is kept
    assert "replaced with the validated snapshot" in flag


def test_gate_passes_plausible_live_values_and_snapshots_through():
    from src.agents.analysis.dcf_agent import _gate_live_sotp
    snap = load_sotp_snapshot()["BABA"]
    plausible_live = {k: v for k, v in snap.items() if k != "_origin"}
    out, flag = _gate_live_sotp("BABA", plausible_live, ADS, None)
    assert out is plausible_live and flag is None
    tagged = {**_LIVE_61, "_origin": "snapshot:BABA"}
    assert _gate_live_sotp("BABA", tagged, ADS, None) == (tagged, None)


def test_gate_ignores_names_without_a_reference():
    from src.agents.analysis.dcf_agent import _gate_live_sotp
    assert _gate_live_sotp("MSFT", _LIVE_61, 7.4e9, None) == (_LIVE_61, None)


def test_no_reference_no_grade():
    assert check_table("MSFT", {"shares": 1, "per_share": 1, "rows": []}) is None
    assert check_table("BABA", None) is None

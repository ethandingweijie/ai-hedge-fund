"""Owner, 2026-11-27 (I2, option 3A): the depleting-asset price deck -- futures strip, fade to the long run,
gated on owner acceptance of the deck and of each ticker's mix."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from src.agents.analysis import dcf_agent as d

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("commodity_deck", ROOT / "scripts" / "commodity_deck.py")
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


def _doc(deck_status="ACCEPTED", mix_status="ACCEPTED"):
    return {"commodity_price_deck": {
        "status": deck_status, "as_of": "2026-11-27", "fade_to_long_run_by_year": 5,
        "commodities": {"BRENT": {"base_price": 80.0, "strip": {"2027": 80.0, "2028": 76.0, "2029": 72.0},
                                  "long_run": 70.0},
                        "QHD": {"base_price": 700.0, "strip": {}, "spot": 1000.0, "long_run": 700.0}},
        "mixes": {"X": {"status": mix_status, "weights": {"BRENT": 0.5}},
                  "Y": {"status": mix_status, "weights": {"QHD": 1.0}}}}}


def test_strip_then_fade_then_long_run():
    r = d.commodity_price_index("X", 7, _doc())
    brent = r["path"]["BRENT"]
    assert brent[:3] == [80.0, 76.0, 72.0]
    assert brent[3] == pytest.approx(71.0)          # halfway from the last strip year to the long run
    assert brent[4:] == [70.0, 70.0, 70.0]
    assert r["index"][0] == pytest.approx(0.5 + 0.5 * 80 / 80)
    assert r["index"][6] == pytest.approx(0.5 + 0.5 * 70 / 80)
    assert r["unpriced_share"] == pytest.approx(0.5)


def test_no_strip_fades_from_spot():
    q = d.commodity_price_index("Y", 6, _doc())["path"]["QHD"]
    assert q[0] == pytest.approx(1000 - 300 / 5) and q[4] == pytest.approx(700.0) and q[5] == 700.0


def test_gated_on_both_acceptances():
    assert d.commodity_price_index("X", 5, _doc(deck_status="PROPOSED")) is None
    assert d.commodity_price_index("X", 5, _doc(mix_status="PROPOSED")) is None
    assert d.commodity_price_index("Z", 5, _doc()) is None


def test_shipped_deck_is_proposed_so_nothing_moves():
    assert d.commodity_price_index("COP", 5) is None


def test_operating_leverage_and_shut_in():
    # base margin 20%: costs are 80% of base revenue per unit; a 10% price fall halves FCF
    assert d._depleting_fcf(100.0, 1.0, 0.20, 1.0) == pytest.approx(20.0)
    assert d._depleting_fcf(100.0, 1.0, 0.20, 0.9) == pytest.approx(10.0)
    assert d._depleting_fcf(100.0, 1.0, 0.20, 0.7) == 0.0


def test_parse_month_formats():
    for s in ("JAN 27", "Jan-27", "Jan27", "2027-01", "January 2027"):
        assert cd.parse_month(s) == (2027, 1), s


def test_strip_from_settlements(tmp_path):
    p = tmp_path / "cl.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Month", "Open", "Settle"])
        for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]):
            w.writerow([f"{m} 27", "", 70 + i])
        w.writerow(["JAN 28", "", 60])                 # one month is not a calendar average
    assert cd.strip_from_csv(str(p), 2026) == {"2027": pytest.approx(75.5)}


def test_writer_touches_only_the_deck():
    raw = (ROOT / "src/data/valuation_constants.json").read_text(encoding="utf-8")
    blk = json.loads(raw)["commodity_price_deck"]
    out = cd._write_block(raw, blk)
    assert out == raw

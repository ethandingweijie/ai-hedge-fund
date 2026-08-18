"""Task #27 — static SOTP assumptions snapshot loader + artifact contract.

The snapshot (src/data/sotp_assumptions_v1.json) is what lifts the trialed
SOTP (analyst) assumptions into production runs for the 6 trialed tickers;
pipeline.py Phase 4.4 attaches entries when the live extractor produced
nothing. These tests pin:

  * the committed artifact's contract (exactly the 6 trialed tickers,
    consumable assumptions dicts, validated meta block)
  * loader failure modes degrade to {} (pre-task-#27 behavior)
  * env controls: SOTP_SNAPSHOT_DISABLED kill-switch,
    SOTP_SNAPSHOT_TICKERS allowlist
"""
from __future__ import annotations

import json

import pytest

from src.agents.analysis.sotp_snapshot import (
    _DATA_PATH,
    attach_snapshot,
    load_sotp_snapshot,
)

TRIALED = {"BABA", "JD", "PDD", "3690.HK", "MSFT", "AMZN"}


# ── committed artifact contract ───────────────────────────────────────────────


def test_artifact_exists_and_covers_exactly_the_trialed_tickers():
    assert _DATA_PATH.exists(), (
        "src/data/sotp_assumptions_v1.json missing — generate with "
        ".venv/Scripts/python.exe .stage7_snapshot_assumptions.py")
    snap = load_sotp_snapshot()
    assert set(snap.keys()) == TRIALED


def test_artifact_assumptions_are_engine_consumable():
    """Every entry must carry what _sotp_analyst_style needs: non-empty
    segments and the holdco/adjustment knobs the engine reads."""
    snap = load_sotp_snapshot()
    for ticker, assumptions in snap.items():
        assert assumptions.get("segments"), f"{ticker}: no segments"
        for seg in assumptions["segments"]:
            assert seg.get("name"), f"{ticker}: unnamed segment"
            # each segment carries a multiple or a margin the engine can use
            assert (seg.get("pe_multiple") or seg.get("ev_rev_multiple")
                    or seg.get("ebit_margin") is not None), (
                f"{ticker}/{seg.get('name')}: no valuation inputs")
        assert "holdco_discount_pct" in assumptions, ticker


def test_artifact_meta_records_validation():
    with open(_DATA_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    meta = doc["_meta"]
    assert meta["version"] == 1
    val = meta["per_ticker_validation"]
    assert set(val.keys()) == TRIALED
    for ticker, v in val.items():
        assert v["tp"] and v["tp"] > 0, ticker
        assert v["gs_tp"], ticker
        # the generation gate: within +/-35% of the GS published TP
        assert abs(v["delta_pct"]) <= 35.0, f"{ticker}: {v['delta_pct']}%"


# ── loader behavior ───────────────────────────────────────────────────────────


def test_kill_switch_returns_empty(monkeypatch):
    monkeypatch.setenv("SOTP_SNAPSHOT_DISABLED", "1")
    assert load_sotp_snapshot() == {}
    monkeypatch.setenv("SOTP_SNAPSHOT_DISABLED", "true")
    assert load_sotp_snapshot() == {}


def test_kill_switch_off_values_are_ignored(monkeypatch):
    monkeypatch.setenv("SOTP_SNAPSHOT_DISABLED", "0")
    assert set(load_sotp_snapshot().keys()) == TRIALED


def test_ticker_allowlist_filters(monkeypatch):
    monkeypatch.setenv("SOTP_SNAPSHOT_TICKERS", "BABA, jd")
    snap = load_sotp_snapshot()
    assert set(snap.keys()) == {"BABA", "JD"}  # case-insensitive, ws-tolerant


def test_allowlist_unknown_ticker_yields_empty(monkeypatch):
    monkeypatch.setenv("SOTP_SNAPSHOT_TICKERS", "NVDA")
    assert load_sotp_snapshot() == {}


def test_missing_and_corrupt_files_return_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("SOTP_SNAPSHOT_DISABLED", raising=False)
    assert load_sotp_snapshot(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_sotp_snapshot(bad) == {}
    # valid JSON, wrong shape
    bad.write_text(json.dumps({"assumptions": "nope"}), encoding="utf-8")
    assert load_sotp_snapshot(bad) == {}


def test_entries_without_segments_are_dropped(tmp_path, monkeypatch):
    monkeypatch.delenv("SOTP_SNAPSHOT_TICKERS", raising=False)
    doc = {"_meta": {"version": 1}, "assumptions": {
        "AAA": {"segments": [{"name": "core", "pe_multiple": 10.0}]},
        "BBB": {"segments": []},
        "CCC": "garbage",
    }}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert set(load_sotp_snapshot(p).keys()) == {"AAA"}


# ── attach_snapshot merge semantics (pipeline Phase 4.4 contract) ────────────


def test_attach_fills_gaps_but_live_output_wins():
    live = {"segments": [{"name": "live"}]}
    snap_assume = {"BABA": {"segments": [{"name": "snap"}]},
                   "JD": {"segments": [{"name": "snap-jd"}]}}
    merged, attached = attach_snapshot({"BABA": live}, snap_assume,
                                       ["BABA", "JD"])
    assert attached == ["JD"]
    assert merged["BABA"] is live           # live extractor output untouched
    assert merged["JD"] == snap_assume["JD"]


def test_attach_only_covers_run_tickers():
    snap_assume = {t: {"segments": [{"name": t}]} for t in ("BABA", "MSFT")}
    merged, attached = attach_snapshot({}, snap_assume, ["BABA"])
    assert attached == ["BABA"]
    assert "MSFT" not in merged


def test_attach_empty_snapshot_is_noop():
    merged, attached = attach_snapshot(None, {}, ["BABA", "JD"])
    assert merged == {} and attached == []


def test_attach_does_not_mutate_existing():
    existing = {"BABA": {"segments": [{"name": "live"}]}}
    before = json.loads(json.dumps(existing))
    attach_snapshot(existing, {"JD": {"segments": [{"name": "x"}]}},
                    ["BABA", "JD"])
    assert existing == before

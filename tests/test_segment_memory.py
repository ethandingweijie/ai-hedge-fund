"""Segment revenue/profit memory: latest reported mix, UI summary, and the
memory -> engine bridge where only multiples come from Gemini."""
import json

import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.industry import gemini_params as gp
from src.data import segment_memory as sm


def _c(v, ccy="CNY", scale="mn", url="https://www.alibabagroup.com/en-US/ir"):
    return {"value": v, "currency": ccy, "scale": scale, "period": "FY", "source_url": url, "quote": str(v)}


def _year(fy, end, rev, profit=None, ccy="CNY"):
    return {"fiscal_year": fy, "period_end": end, "revenue": _c(rev, ccy),
            "profit": _c(profit, ccy) if profit is not None else None,
            "profit_measure": "Adjusted EBITA" if profit is not None else None}


MEMORY = {"_meta": {"status": "pending_review", "model": "gemini-3.8-flash", "updated": "2026-09-15"},
          "tickers": {
              "BABA": {"company": "Alibaba Group", "sotp_basis": "sotp_analyst", "fmp_reporting_currency": "CNY",
                       "retrieved": "2026-09-15", "model": "gemini-3.8-flash", "citation_coverage": 1.0,
                       "reconciliation": {"2025": {"segment_sum": 1e12, "fmp_revenue": 996e9,
                                                   "segment_gap": 0.004, "total_gap": 0.0}},
                       "history": {"reporting_currency": "CNY", "segment_definition_changes": "",
                                   "total_revenue": [],
                                   "segments": [
                                       {"name": "China E-commerce", "years": [
                                           _year("FY2024", "2024-03-31", 420000, 180000),
                                           _year("FY2025", "2025-03-31", 450000, 190000)]},
                                       {"name": "Cloud Intelligence", "years": [
                                           _year("FY2025", "2025-03-31", 120000, 10000)]},
                                       {"name": "All others", "years": [
                                           _year("FY2025", "2025-03-31", 430000)]},
                                   ]}},
              "JD": {"company": "JD.com", "sotp_basis": "sotp_analyst", "error": "RuntimeError: 503"},
          }}

fx_to = lambda a, b: 1.0 if a == b else None                     # noqa: E731


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Reviews go to a throwaway SQLite file, never the local run archive."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "reviews.db"))
    sm._reviews_ready_key = None
    yield
    sm._reviews_ready_key = None


def test_latest_mix_uses_the_most_recent_reported_year():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    assert mix["year"] == "2025" and mix["currency"] == "CNY"
    seg = {s["name"]: s for s in mix["segments"]}
    assert seg["China E-commerce"]["share"] == pytest.approx(450 / 1000)
    assert seg["China E-commerce"]["margin"] == pytest.approx(190 / 450)
    assert seg["All others"]["margin"] is None
    assert [s["name"] for s in mix["segments"]][0] == "China E-commerce"   # largest first


def test_the_hk_line_reads_the_adr_entry_and_errors_are_not_used():
    assert sm.latest_mix("09988.HK", memory=MEMORY, fx_to=fx_to)["year"] == "2025"
    assert sm.latest_mix("JD", memory=MEMORY, fx_to=fx_to) is None
    assert sm.latest_mix("MSFT", memory=MEMORY, fx_to=fx_to) is None


def test_an_entry_with_no_segments_is_not_a_retrieval():
    mem = {"_meta": {}, "tickers": {"00267.HK": {
        "company": "CITIC Limited", "sotp_basis": "holdco_lookthrough", "fmp_reporting_currency": "HKD",
        "history": {"reporting_currency": "HKD", "segments": [], "total_revenue": [],
                    "segment_definition_changes": ""}}}}
    row = sm.ui_summary(memory=mem, fx_to=fx_to)["tickers"][0]
    assert "no segments" in row["error"]
    assert sm.latest_mix("00267.HK", memory=mem, fx_to=fx_to) is None


def test_ui_summary_carries_years_margins_citations_and_errors():
    ui = sm.ui_summary(memory=MEMORY, fx_to=fx_to)
    assert ui["status"] == "pending_review" and ui["model"] == "gemini-3.8-flash"
    baba = next(t for t in ui["tickers"] if t["ticker"] == "BABA")
    assert baba["years"] == ["2024", "2025"]
    commerce = baba["segments"][0]
    assert commerce["name"] == "China E-commerce"
    cell = commerce["years"][1]
    assert cell["revenue"] == 450000e6 and cell["margin"] == pytest.approx(190 / 450)
    assert cell["revenue_url"].startswith("https://")
    cloud_2024 = next(s for s in baba["segments"] if s["name"] == "Cloud Intelligence")["years"][0]
    assert cloud_2024 == {"year": "2024"}                               # not reported that year
    assert baba["profit_coverage"] == pytest.approx(3 / 4)
    assert next(t for t in ui["tickers"] if t["ticker"] == "JD")["error"].startswith("RuntimeError")


def _entry(segments, reconciliation):
    return {"_meta": {}, "tickers": {"X": {
        "company": "X", "sotp_basis": "holdco_lookthrough", "fmp_reporting_currency": "CNY",
        "reconciliation": reconciliation,
        "history": {"reporting_currency": "CNY", "segment_definition_changes": "", "total_revenue": [],
                    "segments": [{"name": n, "years": [_year("FY2023", "2023-12-31", v)]}
                                 for n, v in segments.items()]}}}}


def test_a_subtotal_row_is_removed_and_the_reconciliation_recomputed():
    """Xiaomi FY2023 as returned: 'Smartphone x AIoT' alongside its four parts."""
    mem = _entry({"Smartphone × AIoT": 270970, "Smartphones": 157461, "IoT and lifestyle": 80108,
                  "Internet services": 30107, "Other": 3294},
                 {"2023": {"segment_sum": 541940e6, "fmp_revenue": 270970e6, "segment_gap": 1.0, "total_gap": 0.0}})
    ui = sm.ui_summary(memory=mem, fx_to=fx_to)["tickers"][0]
    assert "Smartphone × AIoT" not in [s["name"] for s in ui["segments"]]
    assert ui["reconciliation"]["2023"]["segment_gap"] == pytest.approx(0.0, abs=1e-3)
    assert any("subtotal" in n for n in ui["notes"])
    mix = sm.latest_mix("X", memory=mem, fx_to=fx_to)
    assert sum(s["share"] for s in mix["segments"]) == pytest.approx(1.0)
    assert len(mix["segments"]) == 4


def test_a_coincidental_one_year_near_match_is_not_a_subtotal():
    """CK Hutchison: Retail 209,267 vs Telecom + Infrastructure + Ports 208,981 (0.14%)."""
    mem = _entry({"Retail": 209267, "Telecom": 101311, "Infrastructure": 58775, "Ports": 48895},
                 {"2023": {"segment_sum": 418248e6, "fmp_revenue": 418248e6, "segment_gap": 0.0, "total_gap": 0.0}})
    ui = sm.ui_summary(memory=mem, fx_to=fx_to)["tickers"][0]
    assert "Retail" in [s["name"] for s in ui["segments"]]


def test_segments_that_do_not_add_up_to_another_are_kept():
    mem = _entry({"Retail": 209267, "Telecom": 101311, "Infrastructure": 58775, "Ports": 48895},
                 {"2023": {"segment_sum": 418248e6, "fmp_revenue": 418248e6, "segment_gap": 0.0, "total_gap": 0.0}})
    ui = sm.ui_summary(memory=mem, fx_to=fx_to)["tickers"][0]
    assert len(ui["segments"]) == 4 and ui["notes"] == []


def _multi_year(values_by_year, recon):
    return {"_meta": {}, "tickers": {"X": {
        "company": "X", "sotp_basis": "holdco_lookthrough", "fmp_reporting_currency": "SGD",
        "reconciliation": recon,
        "history": {"reporting_currency": "SGD", "segment_definition_changes": "", "total_revenue": [],
                    "segments": [{"name": "Core", "years": [
                        _year(f"FY{y}", f"{y}-12-31", v, ccy="SGD") for y, v in values_by_year.items()]}]}}}}


def test_one_year_off_is_a_figure_to_check_not_a_basis():
    """Geely 2024: cited RMB 275.9bn against 240.2bn reported; other years exact."""
    mem = _multi_year({"2023": 179204, "2024": 275910, "2025": 345232},
                      {"2023": {"fmp_revenue": 179204e6, "total_gap": 0.0},
                       "2024": {"fmp_revenue": 240190e6, "total_gap": 0.1487},
                       "2025": {"fmp_revenue": 345232e6, "total_gap": 0.0}})
    notes = sm.ui_summary(memory=mem, fx_to=lambda a, b: 1.0)["tickers"][0]["notes"]
    assert any("2024 +15% only" in n and "check the source" in n for n in notes)
    assert not any("associates" in n for n in notes)


def test_segments_modestly_above_an_agreed_total_read_as_eliminations():
    mem = _multi_year({"2023": 105000, "2024": 106000},
                      {"2023": {"fmp_revenue": 100000e6, "total_gap": 0.0},
                       "2024": {"fmp_revenue": 100000e6, "total_gap": 0.0}})
    notes = sm.ui_summary(memory=mem, fx_to=lambda a, b: 1.0)["tickers"][0]["notes"]
    assert any("inter-segment eliminations" in n for n in notes)


def test_a_company_total_on_another_basis_is_labelled_not_hidden():
    """CK Hutchison: segments and the company's own total ~70-81% above FMP every year."""
    mem = _multi_year({"2024": 476682, "2025": 507297},
                      {"2024": {"fmp_revenue": 281400e6, "total_gap": 0.6943},
                       "2025": {"fmp_revenue": 280000e6, "total_gap": 0.8118}})
    ui = sm.ui_summary(memory=mem, fx_to=lambda a, b: 1.0)["tickers"][0]
    assert ui["reconciliation"]["2025"]["segment_gap"] == pytest.approx(0.8118, abs=1e-3)
    assert any("every year" in n and "associates and joint ventures" in n for n in ui["notes"])
    assert not any("check the source" in n for n in ui["notes"])


# ── live use: 3-year margins, mapping, acceptance ───────────────────────────

def _sec_like(years):
    """BABA-shaped SEC entry: {year: {segment: (revenue, profit)}} in CNY mn."""
    names = sorted({n for segs in years.values() for n in segs})
    return {"company": "Alibaba Group", "sotp_basis": "sotp_analyst", "fmp_reporting_currency": "CNY",
            "source": "sec_segment_footnote",
            "history": {"reporting_currency": "CNY", "total_revenue": [], "segment_definition_changes": "",
                        "segments": [{"name": n, "years": [
                            _year(f"FY{y}", f"{y}-03-31", segs[n][0], segs[n][1])
                            for y, segs in sorted(years.items()) if n in segs]} for n in names]}}


BABA_SEC = _sec_like({
    "2024": {"Alibaba China E-commerce Group": (490101, 186970), "Cloud intelligence group": (106374, 6121),
             "Alibaba International Digital Commerce Group": (102598, -8035), "All others": (317539, -11252)},
    "2025": {"Alibaba China E-commerce Group": (508380, 193223), "Cloud intelligence group": (118028, 10556),
             "Alibaba International Digital Commerce Group": (132300, -15137), "All others": (338347, -9499)},
    "2026": {"Alibaba China E-commerce Group": (554217, 107509), "Cloud intelligence group": (158132, 14265),
             "Alibaba International Digital Commerce Group": (144170, -2051), "All others": (254367, -35737)},
})


def _baba_snapshot():
    from src.agents.analysis.sotp_snapshot import load_sotp_snapshot
    return load_sotp_snapshot()["BABA"]


def test_three_year_margin_smooths_the_trough_year():
    mix = sm.mix_from_entry(BABA_SEC, fx_to=fx_to, margin_basis="avg3")
    commerce = next(s for s in mix["segments"] if s["name"].startswith("Alibaba China"))
    assert commerce["margin_latest"] == pytest.approx(107509 / 554217)            # 19.4%
    expected = (186970 / 490101 + 193223 / 508380 + 107509 / 554217) / 3
    assert commerce["margin"] == commerce["margin_avg3"] == pytest.approx(expected)  # ~32%
    assert commerce["margin_years"] == 3 and mix["year"] == "2026"


def test_baba_memory_maps_onto_the_live_snapshot_rows():
    mix = sm.mix_from_entry(BABA_SEC, fx_to=fx_to, margin_basis="avg3")
    pairs, reason = sm.plan_mapping(_baba_snapshot(), mix)
    assert reason == ""
    by_memory = {seg["name"]: row["name"] for seg, row in pairs}
    assert by_memory["Alibaba China E-commerce Group"].startswith("Taobao and Tmall")   # supplement keyword
    assert by_memory["Cloud intelligence group"] == "Cloud Intelligence Group"
    assert by_memory["All others"].startswith("All Others")                              # by name
    assert "Cainiao" not in " ".join(by_memory.values())                                 # 9% row folds in


def test_rows_finer_than_the_memory_are_not_collapsed():
    snap = {"segments": [{"name": "Food Delivery", "revenue_fwd": 23.3e9, "pe_multiple": 12},
                         {"name": "Instashopping", "revenue_fwd": 5.7e9, "pe_multiple": 25},
                         {"name": "In-store Hotel and Travel", "revenue_fwd": 9.9e9, "pe_multiple": 10},
                         {"name": "New initiatives", "revenue_fwd": 11.8e9, "ev_rev_multiple": 1.3}]}
    mix = {"segments": [{"name": "Core Local Commerce", "share": 0.715, "margin": 0.2},
                        {"name": "New Initiatives", "share": 0.285, "margin": -0.2}]}
    pairs, reason = sm.plan_mapping(snap, mix)
    assert pairs is None and "finer than the memory" in reason


def test_a_memory_segment_without_a_counterpart_blocks_the_mapping():
    snap = {"segments": [{"name": "PDD Domestic Core", "revenue_fwd": 48.5e9, "pe_multiple": 12}]}
    mix = {"segments": [{"name": "Transaction services", "share": 0.5, "margin": None},
                        {"name": "Online marketing services and others", "share": 0.5, "margin": None}]}
    pairs, reason = sm.plan_mapping(snap, mix)
    assert pairs is None and "no counterpart" in reason


def test_apply_takes_revenue_and_margin_from_memory_and_multiples_from_the_row():
    snap = _baba_snapshot()
    new, info = sm.apply_to_sotp(snap, BABA_SEC, entry_key="BABA", fwd_revenue_usd=170e9, fx_to=fx_to)
    assert info["applied"] and info["margin_basis"] == "avg3"
    seg = {s["name"]: s for s in new["segments"]}
    commerce = seg["Alibaba China E-commerce Group"]
    total = 554217 + 158132 + 144170 + 254367
    assert commerce["revenue_fwd"] == pytest.approx(170e9 * 554217 / total)
    assert commerce["pe_multiple"] == 10.4                                   # the Taobao row's
    assert commerce["ebit_margin"] == pytest.approx(
        (186970 / 490101 + 193223 / 508380 + 107509 / 554217) / 3)
    assert seg["All others"]["ebit_margin"] == 0.0                           # negative clamped
    assert new["net_cash"] == snap["net_cash"] and new["holdco_discount_pct"] == snap["holdco_discount_pct"]
    assert new["_sources"]["segments"] == "accepted_segment_memory"
    assert snap["segments"][0]["name"].startswith("Taobao")                  # input not mutated


def test_acceptance_is_per_company_and_expires_when_the_figures_change():
    memory = {"_meta": {}, "tickers": {"BABA": BABA_SEC}}
    assert sm.accepted_entry("09988.HK", memory) == (None, None)
    review = sm.set_review("9988.HK", "accepted", "owner@example.com", memory=memory)
    assert review["memory_key"] == "BABA" and review["status"] == "accepted"
    assert sm.accepted_entry("BABA", memory)[0] == "BABA"
    assert sm.accepted_entry("09988.HK", memory)[0] == "BABA"                # both listings

    changed = {"_meta": {}, "tickers": {"BABA": _sec_like({
        "2026": {"Alibaba China E-commerce Group": (999999, 1), "All others": (1, 1)}})}}
    assert sm.review_for("BABA", changed["tickers"]["BABA"])["status"] == "changed_since_acceptance"
    assert sm.accepted_entry("BABA", changed) == (None, None)

    sm.set_review("BABA", "revoked", "owner@example.com", memory=memory)
    assert sm.accepted_entry("BABA", memory) == (None, None)


def _hk(v, scale="mn"):
    return {"value": v, "currency": "HKD", "scale": scale, "period": "FY2025",
            "source_url": "https://www.ckh.com.hk/ar", "quote": str(v)}


CKH_EBITDA = {"company": "CK Hutchison Holdings", "sotp_basis": "holdco_lookthrough",
              "history_error": "no segments returned",
              "division_ebitda": {"items": [
                  {"division": "Ports & Related Services", "ebitda": _hk(15000), "measure": "EBITDA",
                   "includes_share_of_associates": True, "fiscal_year": "FY2025"},
                  {"division": "Retail (A.S. Watson)", "ebitda": _hk(20000), "measure": "EBITDA",
                   "includes_share_of_associates": True, "fiscal_year": "FY2025"},
              ], "missing": ["Telecommunications (3 Group Europe)"]}}


def test_a_holdco_with_only_division_ebitda_is_reviewable_and_converted():
    memory = {"_meta": {}, "tickers": {"00001.HK": CKH_EBITDA}}
    key, entry = sm.resolve("0001.HK", memory)
    assert key == "00001.HK"
    assert sm.division_ebitda_amounts(entry, "HKD", lambda a, b: 1.0) == {
        "Ports & Related Services": 15000e6, "Retail (A.S. Watson)": 20000e6}
    assert sm.division_ebitda_for("00001.HK", "HKD", memory=memory, fx_to=lambda a, b: 1.0) is None  # not accepted
    sm.set_review("00001.HK", "accepted", "o", memory=memory)
    assert sm.division_ebitda_for("00001.HK", "HKD", memory=memory,
                                  fx_to=lambda a, b: 1.0)["Retail (A.S. Watson)"] == 20000e6


def test_adding_division_ebitda_changes_the_hash_but_old_entries_keep_theirs():
    import hashlib
    plain = {"history": BABA_SEC["history"]}
    legacy = hashlib.sha256(json.dumps(plain["history"], sort_keys=True, ensure_ascii=False)
                            .encode("utf-8")).hexdigest()[:16]
    assert sm.content_hash(plain) == legacy                                   # acceptances survive
    assert sm.content_hash({**plain, "division_ebitda": CKH_EBITDA["division_ebitda"]}) != legacy


def test_holdco_live_effect_lists_the_division_still_missing(monkeypatch):
    from src.agents.analysis import holdco_sotp
    monkeypatch.setattr(holdco_sotp, "enabled", lambda: True)
    effect = sm.live_effect("00001.HK", CKH_EBITDA, fx_to=lambda a, b: 1.0)
    assert effect["method"] == "SOTP / NAV (look-through)" and effect["applies"] is False
    assert effect["missing"] == ["Telecommunications (3 Group Europe)"]
    complete = {**CKH_EBITDA, "division_ebitda": {"items": CKH_EBITDA["division_ebitda"]["items"] + [
        {"division": "Telecommunications (3 Group Europe)", "ebitda": _hk(9000), "measure": "EBITDA",
         "includes_share_of_associates": False, "fiscal_year": "FY2025"}]}}
    assert sm.live_effect("00001.HK", complete, fx_to=lambda a, b: 1.0)["applies"] is True
    monkeypatch.setattr(holdco_sotp, "enabled", lambda: False)
    assert "switched off" in sm.live_effect("00001.HK", complete, fx_to=lambda a, b: 1.0)["reason"]


def test_ui_summary_shows_division_ebitda_for_a_name_without_segment_history():
    memory = {"_meta": {}, "tickers": {"00001.HK": CKH_EBITDA}}
    row = sm.ui_summary(memory=memory, fx_to=lambda a, b: 1.0,
                        reviews=lambda t, e: {"status": "pending"})["tickers"][0]
    assert "error" not in row and row["segments"] == []
    assert [d["division"] for d in row["division_ebitda"]] == ["Ports & Related Services", "Retail (A.S. Watson)"]
    assert row["division_ebitda_missing"] == ["Telecommunications (3 Group Europe)"]
    assert row["history_error"] == "no segments returned"


def test_the_engine_passes_accepted_division_ebitda_to_the_look_through(monkeypatch):
    import src.agents.analysis.dcf_agent as da
    monkeypatch.setattr(sm, "division_ebitda_for", lambda t, ccy, **k: {"Ports & Related Services": 1.0} if ccy == "HKD" else None)
    assert da._accepted_division_ebitda("00001.HK") == {"Ports & Related Services": 1.0}
    assert da._accepted_division_ebitda("MSFT") is None                       # no template


def test_reviewing_a_name_without_memory_is_a_key_error():
    with pytest.raises(KeyError):
        sm.set_review("00267.HK", "accepted", "o", memory={"_meta": {}, "tickers": {}})


def test_ui_summary_carries_listings_review_and_live_effect():
    memory = {"_meta": {}, "tickers": {"BABA": BABA_SEC}}
    ui = sm.ui_summary(memory=memory, fx_to=fx_to)["tickers"][0]
    assert ui["listings"] == ["BABA", "09988.HK"]
    assert ui["review"]["status"] == "pending"
    assert ui["live_effect"]["applies"] is True and ui["live_effect"]["margin_basis"] == "avg3"


def _ranges(**over):
    doc = {"multiples": [
        {"segment": "China E-commerce", "metric": "pe", "low": 9.0, "high": 11.0, "basis": "brokers",
         "source_url": "https://a.com"},
        {"segment": "Cloud Intelligence Group", "metric": "ev_rev", "low": 4.0, "high": 6.0, "basis": "peers",
         "source_url": "https://b.com"},
    ], "associates_investments": _c(150, "CNY", "bn"), "net_cash": _c(300, "CNY", "bn"),
        "holdco_discount_low": 0.15, "holdco_discount_high": 0.25, "holdco_basis": "conglomerate"}
    doc.update(over)
    return doc


def test_memory_bridge_scales_consensus_by_reported_mix_and_uses_reported_margins():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    fx = {"CNY": 0.14, "USD": 1.0}.get
    assumptions, checks = gp.memory_to_engine(mix, _ranges(), fmp_revenue_fwd_usd=170e9, fx_to_usd=fx)
    seg = {s["name"]: s for s in assumptions["segments"]}
    assert seg["China E-commerce"]["revenue_fwd"] == pytest.approx(170e9 * 0.45)
    assert seg["China E-commerce"]["ebit_margin"] == pytest.approx(0.4222, abs=1e-3)
    assert seg["China E-commerce"]["pe_multiple"] == 10.0
    assert seg["Cloud Intelligence"]["ev_rev_multiple"] == 5.0             # name matched by containment
    assert checks["dropped_segments"] == ["All others"]                      # no multiple -> not guessed
    assert assumptions["holdco_discount_pct"] == pytest.approx(0.20)
    assert assumptions["net_cash"] == pytest.approx(300e9 * 0.14)
    assert _sotp_analyst_style(assumptions, shares=2.4e9)["per_share"] > 0


def test_bridge_reads_percent_holdco_and_drops_uncited_multiples():
    mix = sm.latest_mix("BABA", memory=MEMORY, fx_to=fx_to)
    r = _ranges(holdco_discount_low=15, holdco_discount_high=25)
    r["multiples"][1]["source_url"] = ""
    assumptions, checks = gp.memory_to_engine(mix, r, fmp_revenue_fwd_usd=170e9, fx_to_usd={"CNY": 0.14}.get)
    assert assumptions["holdco_discount_pct"] == pytest.approx(0.20)
    assert "Cloud Intelligence" in checks["dropped_segments"]

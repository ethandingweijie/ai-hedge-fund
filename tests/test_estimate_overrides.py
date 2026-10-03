"""Interactive valuation agent (owner, 2026-10-03): the user sees the agent work the estimate out,
overrides what they disagree with, and the change flows to the page, the PDF and the workbook.

Deterministic half: estimate_override_service recomputes forecast → DCF leg → blend → targets on
the stored run and applies the saved override on read. Conversational half: estimate_agent_service
answers from the trace and proposes overrides in the accepted shape; it never writes one."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.backend.services import estimate_override_service as eo
from app.backend.services import estimate_agent_service as ea
from src.agents.analysis import guidance_forecast as gf

_spec = importlib.util.spec_from_file_location("_gft", Path(__file__).resolve().parent / "test_guidance_forecast.py")
_gft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gft)


def _payload():
    """A stored run shaped like production: a DCF leg at 40% beside two multiples legs."""
    fc = gf.build_forecast(_gft._BLOCK, scenario="base", series=_gft._SERIES, profile_name="Managed Care", sector="Healthcare",
                           wacc=0.08, tgr=0.025, shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    hist = gf.history_ratios(_gft._SERIES, gf.load_cfg())
    from src.agents.analysis.dcf_agent import _project_dcf
    iv, pvf, pvt, rows = _project_dcf(revenue_base=42e9, fcf_margin_base=0.03, growth_rate=0.05, margin_delta_per_year=0.0, wacc=0.08, tgr=0.025,
                                      fcf_floor=0.0, net_debt=1e9, shares=52e6, growth_schedule=fc["growth_schedule"], margin_schedule=fc["fcf_margin_schedule"])
    legs = {"DCF": {"kind": "dcf", "value": iv, "revenue_base": 42e9, "fcf_margin_base": 0.03, "growth_base": 0.05, "growth_schedule": fc["growth_schedule"],
                    "margin_delta_absolute": 0.0, "wacc": 0.08, "wacc_schedule": None, "tgr": 0.025, "fcf_floor": 0.0, "net_debt": 1e9, "shares": 52e6,
                    "minority_interest": 0.0, "preferred_equity": 0.0, "pv_fcf_per_share": pvf, "pv_tv_per_share": pvt, "projection_rows": rows},
            "Forward P/E": {"kind": "equity_multiple", "value": 260.0}, "Forward EV/EBITDA": {"kind": "ev_multiple", "value": 240.0}}
    eff = [{"method": "DCF", "value_key": "DCF", "bucket": "dcf", "weight": 0.4}, {"method": "Forward P/E", "value_key": "Forward P/E", "bucket": "multi", "weight": 0.3},
           {"method": "Forward EV/EBITDA", "value_key": "Forward EV/EBITDA", "bucket": "multi", "weight": 0.3}]
    blended = 0.4 * iv + 0.3 * 260.0 + 0.3 * 240.0
    spot, cap = 190.0, 0.5
    scen = {"intrinsic_value": round(blended, 2), "iv_dcf": iv, "leg_inputs": copy.deepcopy(legs), "effective_weights": eff,
            "method_iv_table": {"DCF": round(iv, 2), "Forward P/E": 260.0, "Forward EV/EBITDA": 240.0}, "forward_flags": ["engine flag"]}
    tgt = spot + cap * (blended - spot)
    dr = {"profile": "Managed Care", "reported_currency": "USD", "base": scen, "bear": copy.deepcopy(scen), "bull": copy.deepcopy(scen),
          "pt_bridge": {"rule": "target = spot + capture x (IV - spot)", "spot": spot, "capture": cap,
                        "scenarios": {s: {"intrinsic_value": round(blended, 2), "target": round(tgt, 2)} for s in ("bear", "base", "bull")}},
          "12m_targets": {s: round(tgt, 2) for s in ("bear", "base", "bull")},
          "guidance_estimates": {**{k: _gft._BLOCK[k] for k in ("fiscal_year_1", "fiscal_year_2", "confidence", "estimates", "medium_term_target")},
                                 "applied": True, "channel": {"schedule": fc["growth_schedule"]}, "guidance": {"revenue_growth": {"mid": -0.075}}},
          "guidance_forecast": gf.summary(fc),
          "forecast_context": {"history": hist, "inputs": fc["inputs"], "fiscal_year_1": "FY2026", "fiscal_year_2": "FY2027"},
          "projection_rows": rows, "pv_fcf_base": pvf, "pv_tv_base": pvt}
    sa = {"bear": {"probability": 0.3}, "base": {"probability": 0.5}, "bull": {"probability": 0.2}, "12m_price_target": round(tgt, 2),
          "expected_value": round(blended, 2), "current_price": spot,
          "reconciliation": {"current_price": spot, "blended_iv": round(blended, 2), "expected_value": round(blended, 2), "12m_price_target": round(tgt, 2),
                             "upside_to_pt_pct": round((tgt / spot - 1) * 100, 1), "upside_to_iv_pct": round((blended / spot - 1) * 100, 1)}}
    data = {"dcf_range": {"MOH": dr}, "scenario_analysis": {"MOH": sa}, "decisions": {"MOH": {"action": "BUY", "price_target": round(tgt, 2), "rationale": "x"}},
            "sector": "Healthcare", "industry_brief": "- Molina guides FY2026 revenue down 7.5% [1].", "industry_footnotes": [{"ref_id": 1, "source_name": "Q2 call", "date": "2026-07-24"}]}
    return {"run_id": "run-1", "ticker": "MOH", "run_at": "2026-10-03T00:00:00", "data": data, "decisions": copy.deepcopy(data["decisions"])}


# ── normalisation ─────────────────────────────────────────────────────────────

def test_overrides_are_validated_and_bounded():
    ov = eo.normalize_overrides({"shared": {"fade_years": 3.2, "wacc": 0.09}, "scenarios": {"base": {"revenue_growth_fy1": "0.04"}},
                                 "medium_term_target": {"metric": "EPS", "target_year": "FY2029", "mid": 22}})
    assert ov["shared"] == {"fade_years": 3, "wacc": 0.09} and ov["scenarios"] == {"base": {"revenue_growth_fy1": 0.04}}
    assert ov["medium_term_target"]["metric"] == "eps" and ov["medium_term_target"]["source"] == "user override"
    assert eo.changed_fields(ov) == ["fade_years", "wacc", "base.revenue_growth_fy1", "medium_term_target"]
    with pytest.raises(ValueError, match="outside"):
        eo.normalize_overrides({"scenarios": {"base": {"revenue_growth_fy1": 7.0}}})
    with pytest.raises(ValueError, match="unknown"):
        eo.normalize_overrides({"scenarios": {"base": {"wacc": 0.1}}})
    with pytest.raises(ValueError, match="no overrides"):
        eo.normalize_overrides({"shared": {}, "scenarios": {}})
    assert eo.normalize_overrides({"medium_term_target": {}})["medium_term_target"] == {}       # drop the agent's target


# ── the deterministic chain ──────────────────────────────────────────────────

def test_recompute_moves_the_dcf_leg_the_blend_and_the_target_by_the_engine_formulas():
    p = _payload()
    before = p["data"]["dcf_range"]["MOH"]["base"]["intrinsic_value"]
    res = eo.recompute(p, "MOH", {"scenarios": {"base": {"revenue_growth_fy2": 0.20}}})
    b = res["scenarios"]["base"]
    assert b["skipped"] is None and b["dcf_weight"] == pytest.approx(0.4)
    assert b["forecast"]["rows"][1]["growth"] == pytest.approx(0.20) and b["forecast"]["steps"][0]["title"] == "Read the guidance"
    assert b["dcf"]["value"] > p["data"]["dcf_range"]["MOH"]["base"]["leg_inputs"]["DCF"]["value"]          # more growth, more value
    assert b["intrinsic_value"] == pytest.approx(0.4 * b["dcf"]["value"] + 0.3 * 260.0 + 0.3 * 240.0)         # the DCF at its own weight
    assert b["target"] == pytest.approx(190.0 + 0.5 * (b["intrinsic_value"] - 190.0))                         # the same bridge
    assert res["after"]["intrinsic_value"] > before and res["before"]["intrinsic_value"] == pytest.approx(before)
    # the probability-weighted target uses the run's own probabilities
    pt = sum(res["probabilities"][s] * res["scenarios"][s]["target"] for s in ("bear", "base", "bull"))
    assert res["after"]["12m_price_target"] == pytest.approx(pt)
    assert "base.revenue_growth_fy2" in res["fields"]
    # the payload itself is untouched
    assert p["data"]["dcf_range"]["MOH"]["base"]["intrinsic_value"] == before


def test_recompute_without_dcf_weight_moves_the_cross_check_only_and_says_so():
    p = _payload()
    for s in ("bear", "base", "bull"):
        p["data"]["dcf_range"]["MOH"][s]["effective_weights"] = [{"method": "Forward P/E", "value_key": "Forward P/E", "weight": 0.5},
                                                                 {"method": "Forward EV/EBITDA", "value_key": "Forward EV/EBITDA", "weight": 0.5}]
    res = eo.recompute(p, "MOH", {"scenarios": {"base": {"revenue_growth_fy2": 0.20}}})
    b = res["scenarios"]["base"]
    assert b["dcf_weight"] == 0 and "no weight" in b["note"]
    assert b["intrinsic_value"] == pytest.approx(p["data"]["dcf_range"]["MOH"]["base"]["intrinsic_value"])
    assert b["dcf"]["value"] != p["data"]["dcf_range"]["MOH"]["base"]["leg_inputs"]["DCF"]["value"]


def test_shared_overrides_reach_the_engine_and_the_projector():
    p = _payload()
    res = eo.recompute(p, "MOH", {"shared": {"wacc": 0.12, "tax_rate": 0.30, "fade_years": 2}})
    b = res["scenarios"]["base"]
    assert b["dcf"]["wacc"] == 0.12 and b["dcf"]["value"] < p["data"]["dcf_range"]["MOH"]["base"]["leg_inputs"]["DCF"]["value"]
    assert b["forecast"]["overrides_applied"] == {"tax_rate": 0.30, "fade_years": 2} and b["forecast"]["fade_years"] == 2
    assert b["forecast"]["steps"][0]["title"] == "User overrides in force"


def test_a_run_without_forecast_context_is_refused_readably():
    p = _payload()
    del p["data"]["dcf_range"]["MOH"]["forecast_context"]
    del p["data"]["dcf_range"]["MOH"]["guidance_forecast"]
    with pytest.raises(ValueError, match="forecast context"):
        eo.recompute(p, "MOH", {"scenarios": {"base": {"revenue_growth_fy1": 0.02}}})
    with pytest.raises(KeyError):
        eo.recompute(p, "NOPE", {"scenarios": {"base": {"revenue_growth_fy1": 0.02}}})


def test_the_override_is_applied_on_read_to_every_surface_the_exports_use():
    p = _payload()
    res = eo.recompute(p, "MOH", {"scenarios": {"base": {"revenue_growth_fy2": 0.20}}})
    rec = {"id": "o1", "note": "FY27 reaccelerates on Medicaid rate resets", "created_at": "2026-10-03T12:00:00+00:00", "overrides": res["overrides"], "result": res}
    out = eo.apply_to_payload(copy.deepcopy(p), "MOH", rec)
    dr = out["data"]["dcf_range"]["MOH"]
    b = res["scenarios"]["base"]
    assert dr["base"]["intrinsic_value"] == pytest.approx(b["intrinsic_value"], abs=0.01)
    assert dr["base"]["leg_inputs"]["DCF"]["value"] == pytest.approx(b["dcf"]["value"]) and dr["base"]["leg_inputs"]["DCF"]["user_override"] is True
    assert dr["base"]["method_iv_table"]["DCF"] == pytest.approx(b["dcf"]["value"], abs=0.01)
    assert dr["pt_bridge"]["scenarios"]["base"]["target"] == pytest.approx(b["target"], abs=0.01) and dr["12m_targets"]["base"] == dr["pt_bridge"]["scenarios"]["base"]["target"]
    assert dr["guidance_forecast"]["override"]["note"] == rec["note"] and dr["guidance_forecast"]["rows"][1]["growth"] == pytest.approx(0.20)
    assert dr["estimate_override"]["fields"] == ["base.revenue_growth_fy2"]
    assert dr["base"]["forward_flags"][0].startswith("USER OVERRIDE (2026-10-03): base.revenue_growth_fy2")
    assert dr["guidance_estimates"]["estimates"]["base"]["revenue_growth_fy2"] == 0.20 and dr["guidance_estimates"]["channel"]["source"].startswith("user override")
    sa = out["data"]["scenario_analysis"]["MOH"]
    assert sa["12m_price_target"] == pytest.approx(res["after"]["12m_price_target"], abs=0.01) and sa["reconciliation"]["estimate_override"] is True
    assert out["data"]["decisions"]["MOH"]["price_target"] == sa["12m_price_target"] and out["decisions"]["MOH"]["price_target_agent"] == p["decisions"]["MOH"]["price_target"]
    # the workbook and the PDF print the override where the figures are read
    from src.utils.valuation_workbook import build_workbook
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(build_workbook(out, "MOH")))
    guide = [str(c.value) for c in wb["Guidance"]["A"] if c.value is not None]
    assert any(v.startswith("USER OVERRIDE (2026-10-03)") for v in guide) and any(v.startswith("1. Read the guidance") for v in guide)
    summ = [str(c.value) for c in wb["Summary"]["A"] if c.value is not None]
    assert any(v.startswith("USER ESTIMATE OVERRIDE (2026-10-03)") for v in summ)
    from src.utils import pdf_report as pr
    from reportlab.lib.styles import getSampleStyleSheet
    flow = pr._guidance_forecast_block_pdf(dr, getSampleStyleSheet(), 500.0)
    texts = " ".join(getattr(f, "text", "") for f in flow)
    assert "USER OVERRIDE (2026-10-03)" in texts and "1. Read the guidance" in texts


# ── persistence on the run archive (SQLite here; the same statements run on Postgres) ──

def test_save_get_clear_round_trip_and_apply_on_read(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "archive.db"))
    monkeypatch.setattr(eo, "_ensured", False)
    from app.backend.services import analysis_service as svc
    monkeypatch.setattr(svc.db, "is_postgres", lambda: False)
    p = _payload()
    res = eo.recompute(p, "MOH", {"scenarios": {"base": {"revenue_growth_fy2": 0.20}}})
    assert eo.get_active("run-1", "MOH") is None
    rec = eo.save("run-1", "MOH", 7, res["overrides"], "note", res)
    got = eo.get_active("run-1", "MOH")
    assert got["id"] == rec["id"] and got["user_id"] == 7 and got["overrides"] == res["overrides"] and got["result"]["after"]["intrinsic_value"] == pytest.approx(res["after"]["intrinsic_value"])
    res2 = eo.recompute(p, "MOH", {"shared": {"wacc": 0.10}})
    eo.save("run-1", "MOH", 7, res2["overrides"], None, res2)
    assert eo.get_active("run-1", "MOH")["overrides"] == res2["overrides"] and len(eo.list_active("run-1")) == 1   # one active per run+ticker
    applied = eo.apply_saved("run-1", copy.deepcopy(p))
    assert applied["data"]["dcf_range"]["MOH"]["estimate_override"]["fields"] == ["wacc"]
    assert eo.clear("run-1", "MOH") == 1 and eo.get_active("run-1", "MOH") is None
    assert eo.apply_saved("run-1", copy.deepcopy(p))["data"]["dcf_range"]["MOH"].get("estimate_override") is None


def test_get_run_result_applies_the_override_unless_told_not_to(monkeypatch):
    from app.backend.services import analysis_service as svc
    p = _payload()
    monkeypatch.setattr(svc, "_ensure_web_runs_table", lambda: None)
    monkeypatch.setattr(svc, "_fetch_one", lambda sql, params=None: {"full_result_json": json.dumps(p), "user_id": None})
    monkeypatch.setattr(svc, "_hydrate_financial_statements", lambda run_id, payload: None)
    res = eo.recompute(p, "MOH", {"shared": {"wacc": 0.10}})
    monkeypatch.setattr(eo, "list_active", lambda run_id: [{"id": "o", "ticker": "MOH", "note": None, "created_at": "2026-10-03T00:00:00", "overrides": res["overrides"], "result": res}])
    assert svc.get_run_result("run-1")["data"]["dcf_range"]["MOH"]["estimate_override"]["fields"] == ["wacc"]
    assert svc.get_run_result("run-1", apply_overrides=False)["data"]["dcf_range"]["MOH"].get("estimate_override") is None


# ── the routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.backend.database import get_db
    from app.backend.routes import analysis as ar
    from app.backend.services import analysis_service as svc
    p = _payload()
    store: dict = {}
    monkeypatch.setattr(ar, "get_user_from_token", lambda token, db: SimpleNamespace(id=1) if token == "t1" else None)

    def fake_get(run_id, user_id=None, apply_overrides=True):
        if run_id != "run-1":
            return None
        out = copy.deepcopy(p)
        if apply_overrides and store.get("rec"):
            eo.apply_to_payload(out, "MOH", store["rec"])
        return out

    monkeypatch.setattr(svc, "get_run_result", fake_get)
    monkeypatch.setattr(eo, "get_active", lambda run_id, ticker: store.get("rec"))
    monkeypatch.setattr(eo, "save", lambda run_id, ticker, user_id, overrides, note, result: store.__setitem__("rec", {"id": "o1", "ticker": ticker, "note": note, "created_at": "2026-10-03T00:00:00", "overrides": overrides, "result": result}) or store["rec"])
    monkeypatch.setattr(eo, "clear", lambda run_id, ticker: 1 if store.pop("rec", None) else 0)
    app = FastAPI()
    app.include_router(ar.router)
    app.dependency_overrides[get_db] = lambda: None
    c = TestClient(app)
    c.store = store
    return c


def test_preview_save_and_revert_through_the_api(client):
    h = {"Authorization": "Bearer t1"}
    r = client.post("/analysis/runs/run-1/estimates/preview", json={"ticker": "MOH", "overrides": {"scenarios": {"base": {"revenue_growth_fy2": 0.2}}}}, headers=h)
    assert r.status_code == 200 and r.json()["after"]["intrinsic_value"] > r.json()["before"]["intrinsic_value"] and not client.store
    r = client.post("/analysis/runs/run-1/estimates/preview", json={"ticker": "MOH", "overrides": {"scenarios": {"base": {"revenue_growth_fy2": 9.0}}}}, headers=h)
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    r = client.put("/analysis/runs/run-1/estimates", json={"ticker": "MOH", "overrides": {"shared": {"wacc": 0.1}}, "note": "higher hurdle"}, headers=h)
    assert r.status_code == 200 and r.json()["override"]["note"] == "higher hurdle"
    assert r.json()["run"]["data"]["dcf_range"]["MOH"]["estimate_override"]["fields"] == ["wacc"]
    r = client.get("/analysis/runs/run-1/estimates?ticker=MOH", headers=h)
    assert r.json()["override"]["overrides"] == {"shared": {"wacc": 0.1}, "scenarios": {}, "medium_term_target": None}
    r = client.delete("/analysis/runs/run-1/estimates?ticker=MOH", headers=h)
    assert r.status_code == 200 and r.json()["cleared"] == 1 and r.json()["run"]["data"]["dcf_range"]["MOH"].get("estimate_override") is None
    assert client.post("/analysis/runs/nope/estimates/preview", json={"ticker": "MOH", "overrides": {"shared": {"wacc": 0.1}}}, headers=h).status_code == 404


def test_ask_answers_from_the_trace_and_proposes_in_the_accepted_shape(client, monkeypatch):
    seen = {}

    def fake_llm(system, user):
        seen["system"], seen["user"] = system, user
        return ea._AskOut(answer="Step 5 closes the margin gap 14%, 50%, 86%, 100% on the Managed Care S-curve [1].",
                          proposal={"scenarios": {"base": {"ebitda_margin_fy2": 0.045}}, "rationale": "rate resets lag a year"})

    monkeypatch.setattr(ea, "_default_llm", fake_llm)
    r = client.post("/analysis/runs/run-1/estimates/ask", json={"ticker": "MOH", "question": "why 5% margin by FY2027?", "history": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["proposal_valid"] is True and body["proposal"]["scenarios"] == {"base": {"ebitda_margin_fy2": 0.045}} and body["proposal"]["rationale"] == "rate resets lag a year"
    assert "STEP 5 Shape the margin path" in seen["user"] and "INDUSTRY BRIEF" in seen["user"] and "FOOTNOTES: [1] Q2 call" in seen["user"]
    assert "Respond in JSON format" in seen["system"]
    # an out-of-shape proposal is returned with its error, never applied
    monkeypatch.setattr(ea, "_default_llm", lambda s, u: ea._AskOut(answer="x", proposal={"scenarios": {"base": {"wacc": 0.1}}}))
    body = client.post("/analysis/runs/run-1/estimates/ask", json={"ticker": "MOH", "question": "q"}, headers={"Authorization": "Bearer t1"}).json()
    assert body["proposal_valid"] is False and "unknown" in body["proposal_error"]
    # no model configured → 503, the page still works for direct edits
    monkeypatch.setattr(ea, "_default_llm", lambda s, u: None)
    assert client.post("/analysis/runs/run-1/estimates/ask", json={"ticker": "MOH", "question": "q"}, headers={"Authorization": "Bearer t1"}).status_code == 503


# ── the DCF leg publishes what the page needs ────────────────────────────────

@pytest.mark.slow
def test_the_dcf_leg_builds_the_base_forecast_and_publishes_the_context_on_a_golden_replay():
    """Before this change the forecast block read `_peer_for_gp` before the scenario loop assigned
    it, so the BASE scenario's build raised UnboundLocalError and the FY+1/FY+2 channel ran instead
    (bear and bull built). The replay injects a guidance block into the AAPL fixture."""
    from src.memory.golden_replay import replay_fixture
    block = {"fiscal_year_1": "FY2026", "fiscal_year_2": "FY2027", "confidence": "HIGH", "as_of": "2026-08-01",
             "estimates": {"base": {"revenue_growth_fy1": 0.05, "revenue_growth_fy2": 0.06}, "bear": {"revenue_growth_fy1": 0.02, "revenue_growth_fy2": 0.03},
                           "bull": {"revenue_growth_fy1": 0.08, "revenue_growth_fy2": 0.09}}}
    out = replay_fixture("AAPL", state_patch={"guidance_estimates": {"AAPL": block}})
    assert out["ok"], out["skip_reason"]
    dr = out["entry"]
    assert (dr.get("guidance_forecast") or {}).get("rows"), "the base scenario's forecast did not build"
    assert dr["guidance_forecast"]["scenario"] == "base" and dr["guidance_forecast"]["steps"][0]["title"] == "Read the guidance"
    assert dr["forecast_context"]["history"]["revenue"] > 0 and "wacc" in dr["forecast_context"]["inputs"]
    leg = dr["base"]["leg_inputs"]["DCF"]
    assert "minority_interest" in leg and "preferred_equity" in leg and leg["scenario"] == "base"
    assert not any("did not build" in f for f in dr["base"].get("forward_flags") or [])

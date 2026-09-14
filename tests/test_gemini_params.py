"""Gemini valuation-parameter client: schema, REST handling, citations, guardrails.

Response payloads here are SYNTHETIC, shaped like the v1beta generateContent
REST response. They are replaced by recorded gemini-3.8-flash responses once
the Part E harness can run (the account's prepaid credits were exhausted on
2026-09-14).
"""
import json

import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.industry import gemini_params as gp


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def post(self, url, params=None, json=None, timeout=None):
        self.bodies.append(json)
        return self.responses.pop(0)


def _ok(text, urls=("https://www.alibabagroup.com/ir",)):
    return _Resp(200, {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP",
                        "groundingMetadata": {"webSearchQueries": ["alibaba segment revenue"],
                                              "groundingChunks": [{"web": {"uri": u}} for u in urls]}}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 300, "totalTokenCount": 420},
    })


def _cited(v, url="https://www.alibabagroup.com/ir"):
    return {"value": v, "unit": "USD bn", "period": "FY2027E", "source_url": url, "quote": f"{v}"}


def _sotp(**over):
    doc = {
        "fiscal_year": "FY2027",
        "segments": [
            {"name": "Taobao and Tmall Group", "revenue_fwd_usd_bn": _cited(67.0),
             "ebit_margin": _cited(0.30), "multiple_metric": "pe", "multiple_low": 9.0,
             "multiple_high": 11.0, "multiple_basis": "broker SOTP", "multiple_source_url": "https://x"},
            {"name": "Cloud Intelligence Group", "revenue_fwd_usd_bn": _cited(20.0),
             "ebit_margin": None, "multiple_metric": "ev_rev", "multiple_low": 4.0,
             "multiple_high": 6.0, "multiple_basis": "cloud peers", "multiple_source_url": "https://y"},
        ],
        "associates_investments_usd_bn": _cited(22.3),
        "net_cash_usd_bn": _cited(68.0),
        "holdco_discount_pct": 0.15, "holdco_basis": "conglomerate discount",
    }
    doc.update(over)
    return doc


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_schema_inlines_refs_and_marks_optionals_nullable():
    s = gp.to_gemini_schema(gp.SotpInputs)
    seg = s["properties"]["segments"]["items"]
    assert s["type"] == "OBJECT" and seg["type"] == "OBJECT"
    assert seg["properties"]["revenue_fwd_usd_bn"]["properties"]["source_url"]["type"] == "STRING"
    assert seg["properties"]["ebit_margin"]["nullable"] is True
    assert seg["properties"]["multiple_metric"]["enum"] == ["pe", "ev_rev"]
    assert "$ref" not in json.dumps(s) and "$defs" not in json.dumps(s)


def test_a_grounded_schema_call_parses_json_grounding_and_usage():
    session = _Session(_ok(json.dumps(_sotp())))
    out = gp.generate("p", schema=gp.SotpInputs, session=session)
    body = session.bodies[0]
    assert body["tools"] == [{"google_search": {}}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert out["mode"] == "single" and out["json"]["segments"][0]["name"] == "Taobao and Tmall Group"
    assert out["grounding_urls"] == ["https://www.alibabagroup.com/ir"]
    assert out["usage"]["total"] == 420 and out["model"] == "gemini-3.8-flash"


def test_when_tools_and_schema_cannot_combine_it_runs_two_steps():
    refusal = _Resp(400, {"error": {"message": "Tool use with a response mime type is unsupported"}})
    session = _Session(refusal, _ok("Segment revenue text with sources"),
                       _ok(json.dumps(_sotp()), urls=()))
    out = gp.generate("p", schema=gp.SotpInputs, session=session)
    assert out["mode"] == "two_step"
    assert "tools" in session.bodies[1] and "responseSchema" not in session.bodies[1]["generationConfig"]
    assert "tools" not in session.bodies[2] and "responseSchema" in session.bodies[2]["generationConfig"]
    assert out["grounding_urls"] == ["https://www.alibabagroup.com/ir"]    # from the grounded step


def test_exhausted_credits_are_a_billing_error_not_a_retry():
    msg = {"error": {"code": 429, "message": "Your prepayment credits are depleted."}}
    with pytest.raises(gp.GeminiBillingError):
        gp.generate("p", schema=gp.SotpInputs, session=_Session(_Resp(429, msg)))


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gp.GeminiUnavailable):
        gp.generate("p", session=_Session())


def test_fenced_json_is_tolerated():
    out = gp.generate("p", schema=gp.SotpInputs, session=_Session(_ok("```json\n" + json.dumps(_sotp()) + "\n```")))
    assert len(out["json"]["segments"]) == 2


def test_engine_inputs_from_cited_numbers_value_through_the_engine():
    assumptions, checks = gp.to_engine_assumptions(_sotp(), fmp_revenue_fwd_usd=87.5e9)
    seg = {s["name"]: s for s in assumptions["segments"]}
    assert seg["Taobao and Tmall Group"]["pe_multiple"] == 10.0        # midpoint of 9-11x
    assert seg["Cloud Intelligence Group"]["ev_rev_multiple"] == 5.0
    assert assumptions["net_cash"] == pytest.approx(68e9)
    assert checks["segment_sum_gap"] == pytest.approx(0.0057, abs=1e-3)
    table = _sotp_analyst_style(assumptions, shares=2.39e9)
    # 67bn x 30% x 0.85 x 10 + 20bn x 5 + 22.3 + 68, less 15%, per ADS
    assert table["per_share"] == pytest.approx((170.85e9 + 100e9 + 90.3e9) * 0.85 / 2.39e9, rel=1e-6)


def test_uncited_numbers_never_reach_the_engine():
    doc = _sotp()
    doc["segments"][1]["revenue_fwd_usd_bn"]["source_url"] = ""
    doc["segments"][0]["ebit_margin"]["quote"] = " "
    doc["net_cash_usd_bn"]["source_url"] = "not a url"
    assumptions, checks = gp.to_engine_assumptions(doc)
    assert [s["name"] for s in assumptions["segments"]] == ["Taobao and Tmall Group"]
    assert "ebit_margin" not in assumptions["segments"][0]
    assert "net_cash" not in assumptions
    assert checks["dropped_segments"] == ["Cloud Intelligence Group"]
    assert checks["citation_coverage"] < 1.0


def test_segments_that_do_not_sum_to_fmp_revenue_are_rejected():
    assumptions, checks = gp.to_engine_assumptions(_sotp(), fmp_revenue_fwd_usd=120e9)
    assert assumptions["segments"] == [] and "rejected" in checks


def test_margins_and_holdco_are_clamped_and_bad_ranges_dropped():
    doc = _sotp(holdco_discount_pct=0.9)
    doc["segments"][0]["ebit_margin"]["value"] = 0.85
    doc["segments"][1]["multiple_low"] = 7.0          # low > high
    assumptions, checks = gp.to_engine_assumptions(doc)
    assert assumptions["segments"][0]["ebit_margin"] == 0.60
    assert assumptions["holdco_discount_pct"] == 0.5
    assert checks["dropped_segments"] == ["Cloud Intelligence Group"]
    assert checks["clamped"]

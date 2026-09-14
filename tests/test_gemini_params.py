"""Gemini valuation-parameter client: schema, REST handling, citations, currency, guardrails.

REST payloads here are SYNTHETIC, shaped like the v1beta generateContent
response. Recorded gemini-3.8-flash responses (tests/fixtures/gemini/, written
by scripts/eval_gemini_valuation.py --record) back the replay test below.
"""
import json
from pathlib import Path

import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.industry import gemini_params as gp

FX = {"USD": 1.0, "CNY": 0.14, "HKD": 0.128}
fx = FX.get


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


def _cited(v, ccy="USD", scale="bn", url="https://www.alibabagroup.com/ir"):
    return {"value": v, "currency": ccy, "scale": scale, "period": "FY2027E",
            "source_url": url, "quote": f"{v}"}


def _ratio(v):
    return {"value": v, "period": "FY2027E", "source_url": "https://x.com/a", "quote": f"{v}"}


def _sotp(**over):
    doc = {
        "fiscal_year": "FY2027",
        "segments": [
            {"name": "Taobao and Tmall Group", "revenue_fwd": _cited(67.0),
             "ebit_margin": _ratio(0.30), "multiple_metric": "pe", "multiple_low": 9.0,
             "multiple_high": 11.0, "multiple_basis": "broker SOTP", "multiple_source_url": "https://x"},
            {"name": "Cloud Intelligence Group", "revenue_fwd": _cited(20.0),
             "ebit_margin": None, "multiple_metric": "ev_rev", "multiple_low": 4.0,
             "multiple_high": 6.0, "multiple_basis": "cloud peers", "multiple_source_url": "https://y"},
        ],
        "associates_investments": _cited(22.3),
        "net_cash": _cited(68.0),
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
    rev = seg["properties"]["revenue_fwd"]["properties"]
    assert rev["currency"]["type"] == "STRING"
    assert rev["scale"]["enum"] == ["units", "thousands", "mn", "bn", "tn"]
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
    assert out["grounding_urls"] == ["https://www.alibabagroup.com/ir"]


def test_exhausted_credits_are_a_billing_error_not_a_retry():
    msg = {"error": {"code": 429, "message": "Your prepayment credits are depleted."}}
    with pytest.raises(gp.GeminiBillingError):
        gp.generate("p", schema=gp.SotpInputs, session=_Session(_Resp(429, msg)))


def test_high_demand_503_is_retried_with_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gp.time, "sleep", sleeps.append)
    busy = _Resp(503, {"error": {"message": "This model is currently experiencing high demand."}})
    session = _Session(busy, busy, _ok(json.dumps(_sotp())))
    out = gp.generate("p", schema=gp.SotpInputs, session=session)
    assert len(session.bodies) == 3 and sleeps == [5.0, 15.0]
    assert out["json"]["segments"]


def test_persistent_503_gives_up_after_the_retry_budget(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: None)
    busy = _Resp(503, {"error": {"message": "high demand"}})
    session = _Session(*([busy] * (gp.RETRIES + 1)))
    with pytest.raises(RuntimeError, match="gemini 503"):
        gp.generate("p", session=session)
    assert len(session.bodies) == gp.RETRIES + 1


def test_an_empty_200_with_no_candidates_is_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gp.time, "sleep", sleeps.append)
    empty = _Resp(200, {"usageMetadata": {"promptTokenCount": 9000, "thoughtsTokenCount": 11000},
                        "modelVersion": "gemini-3.8-flash"})
    session = _Session(empty, _ok(json.dumps(_sotp())))
    out = gp.generate("p", schema=gp.SotpInputs, session=session)
    assert len(session.bodies) == 2 and sleeps == [5.0]
    assert out["json"]["segments"]


def test_a_persistently_empty_200_reports_usage_and_model_version(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: None)
    empty = _Resp(200, {"usageMetadata": {"thoughtsTokenCount": 11000}, "modelVersion": "gemini-3.8-flash"})
    session = _Session(*([empty] * (gp.RETRIES + 1)))
    with pytest.raises(gp.GeminiParseError) as info:
        gp.generate("p", schema=gp.SotpInputs, grounded=False, session=session)
    assert len(session.bodies) == gp.RETRIES + 1
    assert "candidates=0" in str(info.value) and "gemini-3.8-flash" in str(info.value)


def test_a_persistently_empty_grounded_schema_call_falls_back_to_two_steps(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: None)
    empty = _Resp(200, {"usageMetadata": {"thoughtsTokenCount": 11000}})
    session = _Session(*([empty] * (gp.RETRIES + 1)),
                       _ok("Swire Pacific segment revenue with sources"),
                       _ok(json.dumps(_sotp()), urls=()))
    out = gp.generate("p", schema=gp.SotpInputs, session=session)
    assert out["mode"] == "two_step_after_empty"
    grounded_text, extract = session.bodies[-2], session.bodies[-1]
    assert "tools" in grounded_text and "responseSchema" not in grounded_text["generationConfig"]
    assert "tools" not in extract and "responseSchema" in extract["generationConfig"]
    assert out["json"]["segments"]


def test_an_empty_ungrounded_call_is_not_rerouted(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: None)
    empty = _Resp(200, {})
    session = _Session(*([empty] * (gp.RETRIES + 1)))
    with pytest.raises(gp.GeminiParseError):
        gp.generate("p", schema=gp.SotpInputs, grounded=False, session=session)
    assert len(session.bodies) == gp.RETRIES + 1


def test_a_blocked_prompt_is_not_retried(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: pytest.fail("retried a blocked prompt"))
    blocked = _Resp(200, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(gp.GeminiParseError, match="no_candidate:SAFETY"):
        gp.generate("p", schema=gp.SotpInputs, session=_Session(blocked))


def test_billing_429_is_not_retried(monkeypatch):
    monkeypatch.setattr(gp.time, "sleep", lambda s: pytest.fail("slept on a billing error"))
    msg = {"error": {"code": 429, "message": "Your prepayment credits are depleted."}}
    session = _Session(_Resp(429, msg))
    with pytest.raises(gp.GeminiBillingError):
        gp.generate("p", session=session)
    assert len(session.bodies) == 1


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gp.GeminiUnavailable):
        gp.generate("p", session=_Session())


def test_a_response_without_json_says_why():
    empty = _Resp(200, {"candidates": [{"content": {"parts": [{"text": "I could not find"}]},
                                        "finishReason": "MAX_TOKENS"}],
                        "usageMetadata": {"promptTokenCount": 27000, "thoughtsTokenCount": 32000}})
    with pytest.raises(gp.GeminiParseError) as info:
        gp.generate("p", schema=gp.SotpInputs, session=_Session(empty))
    assert info.value.finish_reason == "MAX_TOKENS"
    assert info.value.usage["thoughts"] == 32000 and "could not find" in info.value.text_head


def test_thinking_config_is_passed_through_to_both_steps():
    refusal = _Resp(400, {"error": {"message": "unsupported combination"}})
    session = _Session(refusal, _ok("text"), _ok(json.dumps(_sotp())))
    gp.generate("p", schema=gp.SotpInputs, thinking={"thinkingLevel": "low"}, session=session)
    assert all(b["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
               for b in session.bodies)


def test_fenced_json_is_tolerated():
    out = gp.generate("p", schema=gp.SotpInputs, session=_Session(_ok("```json\n" + json.dumps(_sotp()) + "\n```")))
    assert len(out["json"]["segments"]) == 2


def test_engine_inputs_from_cited_numbers_value_through_the_engine():
    assumptions, checks = gp.to_engine_assumptions(_sotp(), fmp_revenue_fwd_usd=87.5e9, fx_to_usd=fx)
    seg = {s["name"]: s for s in assumptions["segments"]}
    assert seg["Taobao and Tmall Group"]["pe_multiple"] == 10.0        # midpoint of 9-11x
    assert seg["Cloud Intelligence Group"]["ev_rev_multiple"] == 5.0
    assert assumptions["net_cash"] == pytest.approx(68e9)
    assert checks["segment_sum_gap"] == pytest.approx(-0.0057, abs=1e-3)
    table = _sotp_analyst_style(assumptions, shares=2.39e9)
    assert table["per_share"] == pytest.approx((170.85e9 + 100e9 + 90.3e9) * 0.85 / 2.39e9, rel=1e-6)


def test_the_rmb_labelled_as_usd_failure_is_converted_not_trusted():
    """The live 2026-09-15 run: RMB 696bn China commerce. Stated as CNY bn it
    converts to USD ~97bn; the same number claimed as USD bn fails reconciliation."""
    doc = _sotp(segments=[{**_sotp()["segments"][0], "revenue_fwd": _cited(696.0, ccy="CNY")}])
    assumptions, checks = gp.to_engine_assumptions(doc, fmp_revenue_fwd_usd=110e9, fx_to_usd=fx)
    assert assumptions["segments"][0]["revenue_fwd"] == pytest.approx(696e9 * 0.14)
    assert "rejected" not in checks and checks["currencies"] == ["CNY bn"]
    mislabelled = _sotp(segments=[{**_sotp()["segments"][0], "revenue_fwd": _cited(696.0, ccy="USD")}])
    assumptions, checks = gp.to_engine_assumptions(mislabelled, fmp_revenue_fwd_usd=110e9, fx_to_usd=fx)
    assert assumptions["segments"] == [] and "rejected" in checks


def test_an_unconvertible_currency_or_scale_drops_the_number():
    doc = _sotp()
    doc["segments"][1]["revenue_fwd"] = _cited(20.0, ccy="XYZ")
    doc["net_cash"] = {**_cited(68.0), "scale": "lakh"}
    assumptions, checks = gp.to_engine_assumptions(doc, fx_to_usd=fx)
    assert [s["name"] for s in assumptions["segments"]] == ["Taobao and Tmall Group"]
    assert "net_cash" not in assumptions and "net_cash" in checks["dropped_fields"]


def test_uncited_numbers_never_reach_the_engine():
    doc = _sotp()
    doc["segments"][1]["revenue_fwd"]["source_url"] = ""
    doc["segments"][0]["ebit_margin"]["quote"] = " "
    doc["net_cash"]["source_url"] = "not a url"
    assumptions, checks = gp.to_engine_assumptions(doc, fx_to_usd=fx)
    assert [s["name"] for s in assumptions["segments"]] == ["Taobao and Tmall Group"]
    assert "ebit_margin" not in assumptions["segments"][0]
    assert "net_cash" not in assumptions
    assert checks["dropped_segments"] == ["Cloud Intelligence Group"]
    assert checks["citation_coverage"] < 1.0


def test_segments_far_from_fmp_revenue_are_rejected_but_eliminations_are_tolerated():
    _, checks = gp.to_engine_assumptions(_sotp(), fmp_revenue_fwd_usd=87.5e9 * 1.12, fx_to_usd=fx)
    assert "rejected" not in checks                               # 11% short: eliminations
    assumptions, checks = gp.to_engine_assumptions(_sotp(), fmp_revenue_fwd_usd=120e9, fx_to_usd=fx)
    assert assumptions["segments"] == [] and "rejected" in checks


def test_margins_percent_or_decimal_are_clamped_and_bad_ranges_dropped():
    doc = _sotp(holdco_discount_pct=0.9)
    doc["segments"][0]["ebit_margin"]["value"] = 85.0             # "85%"
    doc["segments"][1]["multiple_low"] = 7.0                      # low > high
    assumptions, checks = gp.to_engine_assumptions(doc, fx_to_usd=fx)
    assert assumptions["segments"][0]["ebit_margin"] == 0.60
    assert assumptions["holdco_discount_pct"] == 0.5
    assert checks["dropped_segments"] == ["Cloud Intelligence Group"]
    assert checks["clamped"]


def test_history_reconciles_segments_and_cited_total_to_fmp_in_reporting_currency():
    hist = {
        "reporting_currency": "CNY",
        "segments": [
            {"name": "Commerce", "years": [
                {"fiscal_year": "FY2025", "period_end": "2025-03-31", "revenue": _cited(450.0, "CNY")}]},
            {"name": "Cloud", "years": [
                {"fiscal_year": "FY2025", "period_end": "2025-03-31", "revenue": _cited(15.0, "USD")},
                {"fiscal_year": "FY2024", "period_end": "2024-03-31", "revenue": {**_cited(100.0, "CNY"), "source_url": ""}}]},
        ],
        "total_revenue": [{"fiscal_year": "FY2025", "period_end": "2025-03-31", "revenue": _cited(996.0, "CNY")}],
        "segment_definition_changes": "",
    }
    rates = {("USD", "CNY"): 7.1}
    rec = gp.reconcile_history(hist, {"2025": 996.3e9}, "CNY", lambda a, b: rates.get((a, b)))
    y = rec["2025"]
    assert y["segment_sum"] == pytest.approx(450e9 + 15e9 * 7.1)
    assert y["segments"] == 2 and y["total_gap"] == pytest.approx(-0.0003, abs=1e-4)
    assert rec["2024"]["uncited"] == 1 and rec["2024"]["segment_gap"] is None


def test_recorded_live_responses_still_parse():
    """Replays whatever real gemini-3.8-flash responses the harness recorded."""
    files = sorted((Path(__file__).parent / "fixtures" / "gemini").glob("*_G*.json"))
    if not files:
        pytest.skip("no recorded Gemini responses yet")
    arms = {"_G1m_": gp.MultipleRanges, "_G1_": gp.SotpInputs, "_G2_": gp.DirectEstimate}
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        schema = next(s for marker, s in arms.items() if marker in f.name)
        try:
            schema.model_validate(gp._parse_json(rec["text"]))
        except Exception as exc:  # recordings from an older schema version are skipped
            if "_G1_" in f.name and "revenue_fwd_usd_bn" in rec["text"]:
                continue
            raise AssertionError(f"{f.name}: {exc}")

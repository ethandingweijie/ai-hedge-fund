"""QWEN_PIPELINE_THINKING switches qwen3.8-flash thinking on pipeline calls (owner, 2026-11-26)."""
from src.llm.models import ModelProvider, get_model


def test_thinking_is_off_by_default_and_on_with_a_longer_timeout(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "x")
    monkeypatch.delenv("QWEN_PIPELINE_THINKING", raising=False)
    m = get_model("qwen3.8-flash", ModelProvider.ALIBABA)
    assert m.extra_body == {"enable_thinking": False} and m.request_timeout == 120
    monkeypatch.setenv("QWEN_PIPELINE_THINKING", "on")
    m = get_model("qwen3.8-flash", ModelProvider.ALIBABA)
    assert not m.extra_body and m.request_timeout == 300


def test_other_qwen_models_are_untouched(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "x")
    monkeypatch.setenv("QWEN_PIPELINE_THINKING", "on")
    m = get_model("qwen3.6-plus", ModelProvider.ALIBABA)
    assert not m.extra_body and m.request_timeout == 120

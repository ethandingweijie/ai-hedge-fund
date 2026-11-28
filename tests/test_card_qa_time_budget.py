"""D7 (owner, 2026-11-28): card QA stays before delivery but is capped at CARD_QA_TIME_BUDGET_S (default 15 s)."""
from src.agents.audit import card_qa_agent as qa


def test_spent_budget_skips_remaining_cards_without_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(qa, "run_meta_check", lambda state, t: {"passed": True, "checks_run": [], "issues": []})
    monkeypatch.setattr(qa, "_audit_one_card", lambda **kw: calls.append(kw["card_name"]) or ({"card": kw["card_name"]}, [], []))
    from types import SimpleNamespace
    fake = {f"card{i}": SimpleNamespace(schema_version=1, applies_when=lambda state, t: True) for i in range(3)}
    monkeypatch.setattr(qa, "CARD_SCHEMAS", fake)
    monkeypatch.setattr(qa, "_value_sanity_flags", lambda state, t: [])
    ticks = iter(range(0, 10_000, 20))                       # every clock read advances 20 s
    monkeypatch.setattr(qa, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    out = qa.run_card_qa_agent({"data": {}}, "X", time_budget_s=15)
    assert calls == [] and out["qa_time_budget_hit"] is True
    assert all(c.get("_skipped") == "time_budget" for c in out["cards_inspected"])
    assert not qa.should_reuse_card_qa({**out, "qa_version": qa.QA_VERSION}, "h", "h")


def test_default_budget_is_15s(monkeypatch):
    monkeypatch.delenv("CARD_QA_TIME_BUDGET_S", raising=False)
    assert qa._time_budget_s() == 15.0

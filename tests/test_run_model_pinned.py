"""Every run is on Qwen; the server pins the model (owner, 2026-11-28: provider toggle removed)."""
import inspect
import pathlib

from app.backend.routes import analysis as a

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_default_run_model_is_qwen_and_env_can_roll_it_back(monkeypatch):
    monkeypatch.delenv("RUN_MODEL", raising=False)
    assert a._run_model() == "qwen3.8-flash"
    monkeypatch.setenv("RUN_MODEL", "qwen3.6-plus")
    assert a._run_model() == "qwen3.6-plus"


def test_run_start_ignores_the_requested_model():
    src = inspect.getsource(a.run_analysis)
    assert "model_name = _run_model()" in src
    assert "model_name = validate_model_input" not in src


def test_landing_page_has_no_provider_toggle_and_clients_default_to_qwen():
    page = (ROOT / "app/frontend/src/pages/ReportPage.tsx").read_text(encoding="utf-8")
    assert "RunProvider" not in page and "run_provider" not in page and "'anthropic'" not in page
    for rel in ("app/frontend/src/contexts/active-run-context.tsx", "app/frontend/src/hooks/useRunStream.ts",
                "app/frontend/src/lib/api.ts"):
        assert "claude-sonnet-4-6" not in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_pipeline_model_defaults_are_flash(monkeypatch):
    """With no env override, every in-pipeline LLM role defaults to qwen3.8-flash (owner 2026-11-28)."""
    from src.utils import llm
    from src.agents.audit import card_qa_agent
    assert llm._DEFAULT_FAST_MODEL == "qwen3.8-flash"
    assert card_qa_agent.DEFAULT_QA_MODEL == "qwen3.8-flash"
    for rel in ("src/memory/report_recap.py", "src/memory/assumption_extract.py", "src/agents/audit/llm_judge.py",
                "src/data/credit_ratings.py"):
        code = [l for l in (ROOT / rel).read_text(encoding="utf-8").splitlines()
                if "qwen3.6-plus" in l and "until 2026-11-28" not in l]
        assert not code, (rel, code)


def test_credit_rating_search_on_flash_turns_thinking_off():
    from src.agents.industry.deep_research import _qwen_search_extra_body
    body = _qwen_search_extra_body("qwen3.8-flash")
    assert body["enable_thinking"] is False and body["search_options"]["search_strategy"] == "pro"

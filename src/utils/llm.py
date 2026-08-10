"""Helper functions for LLM"""

import json
import os
from pydantic import BaseModel
from src.llm.models import get_model, get_model_info, ModelProvider
from src.utils.progress import progress
from src.graph.state import AgentState

# ── Speed round 2 (R4): model-tiering for non-research agents ─────────────
# The fast tier applies to short-form synthesis agents that do not need the
# run's research-grade model. Research-facing calls (deep research Tier-1,
# extractors, DCF calibration, citation registry) are NOT in this group.
_FAST_TIER_AGENT_NAMES = frozenset({"scenario_agent", "power_law_agent", "value_trap_agent"})
_FAST_TIER_PREFIXES = ("investor_",)
# Default fast model; set PIPELINE_FAST_MODEL to override, or to an empty
# string to disable tiering entirely (all agents use the run model).
# qwen3.6-plus = the Alibaba haiku-equivalent on this deployment: the fast,
# cheap tier already proven for high-volume roles (card-QA DEFAULT_QA_MODEL).
# The ALIBABA client branch carries timeout=120 + max_retries=3 tuned for
# DashScope's limit_burst_rate 429s. Rollback: PIPELINE_FAST_MODEL=claude-haiku-4-5.
_DEFAULT_FAST_MODEL = "qwen3.6-plus"


def _resolve_env_model(model_name: str) -> tuple[str, str | None]:
    """Resolve (model_name, provider) for an env-specified model via the
    registry. Provider is None when the model is unknown — callers must fall
    back rather than route to a broken client."""
    from src.llm.models import find_model_by_name
    _info = find_model_by_name(model_name)
    if _info is not None:
        return model_name, _info.provider.value
    return model_name, None


def call_llm(
    prompt: any,
    pydantic_model: type[BaseModel],
    agent_name: str | None = None,
    state: AgentState | None = None,
    max_retries: int = 3,
    default_factory=None,
    max_tokens: int | None = None,
) -> BaseModel:
    """
    Makes an LLM call with retry logic, handling both JSON supported and non-JSON supported models.

    Args:
        prompt: The prompt to send to the LLM
        pydantic_model: The Pydantic model class to structure the output
        agent_name: Optional name of the agent for progress updates and model config extraction
        state: Optional state object to extract agent-specific model configuration
        max_retries: Maximum number of retries (default: 3)
        default_factory: Optional factory function to create default response on failure

    Returns:
        An instance of the specified Pydantic model
    """
    
    # Extract model configuration if state is provided and agent_name is available
    if state and agent_name:
        model_name, model_provider = get_agent_model_config(state, agent_name)
    else:
        # Use system defaults when no state or agent_name is provided
        model_name = "gpt-4.1"
        model_provider = "OPENAI"

    # Extract API keys from state if available
    api_keys = None
    if state:
        request = state.get("metadata", {}).get("request")
        if request and hasattr(request, 'api_keys'):
            api_keys = request.api_keys

    # Ensure model_provider is a ModelProvider enum (str comparison can fail on Python 3.12+)
    if isinstance(model_provider, str):
        try:
            model_provider = ModelProvider(model_provider)
        except ValueError:
            pass  # leave as string; get_model will fail gracefully

    model_info = get_model_info(model_name, model_provider)
    llm = get_model(model_name, model_provider, api_keys)

    if llm is None:
        print(f"Error: could not initialise LLM for model={model_name} provider={model_provider}")
        if default_factory:
            return default_factory()
        return create_default_response(pydantic_model)

    # Cap output tokens when the caller specifies a limit (e.g. agents that only
    # need a small JSON response and must not generate 10k+ token outputs).
    if max_tokens is not None:
        llm = llm.bind(max_tokens=max_tokens)

    # For non-JSON support models, we can use structured output
    if not (model_info and not model_info.has_json_mode()):
        llm = llm.with_structured_output(
            pydantic_model,
            method="json_mode",
        )

    # Call the LLM with retries
    for attempt in range(max_retries):
        try:
            # Call the LLM
            result = llm.invoke(prompt)

            # For non-JSON support models, we need to extract and parse the JSON manually
            if model_info and not model_info.has_json_mode():
                parsed_result = extract_json_from_response(result.content)
                if parsed_result:
                    return pydantic_model(**parsed_result)
            else:
                return result

        except Exception as e:
            # Qwen returns flat JSON (no nested wrapper) — try auto-nesting
            # e.g. MacroRegimeOutput expects {"regime": {...}, "agent_weights": ...}
            # but Qwen returns {"risk_appetite": "risk-off", "agent_weights": ..., ...}
            if pydantic_model and "Field required" in str(e):
                try:
                    import json as _json
                    _raw = str(e)
                    # Extract the dict from the error message
                    _start = _raw.find("input_value={")
                    if _start > 0:
                        # Try parsing the flat dict and wrapping nested fields
                        _flat = result if isinstance(result, dict) else (
                            _json.loads(result.content) if hasattr(result, 'content') else {}
                        )
                        if isinstance(_flat, dict) and _flat:
                            # Find which field is missing and try to construct it
                            for field_name, field_info in pydantic_model.model_fields.items():
                                if field_name not in _flat and hasattr(field_info.annotation, 'model_fields'):
                                    # This is a nested Pydantic model — try to extract its fields from flat dict
                                    nested_fields = field_info.annotation.model_fields.keys()
                                    nested_data = {k: _flat.pop(k) for k in list(nested_fields) if k in _flat}
                                    if nested_data:
                                        _flat[field_name] = nested_data
                            _result = pydantic_model(**_flat)
                            return _result
                except Exception:
                    pass  # fall through to normal retry

            if agent_name:
                progress.update_status(agent_name, None, f"Error - retry {attempt + 1}/{max_retries}")

            if attempt == max_retries - 1:
                print(f"Error in LLM call after {max_retries} attempts: {e}")
                # Use default_factory if provided, otherwise create a basic default
                if default_factory:
                    return default_factory()
                return create_default_response(pydantic_model)

    # This should never be reached due to the retry logic above
    return create_default_response(pydantic_model)


def create_default_response(model_class: type[BaseModel]) -> BaseModel:
    """Creates a safe default response based on the model's fields."""
    default_values = {}
    for field_name, field in model_class.model_fields.items():
        if field.annotation == str:
            default_values[field_name] = "Error in analysis, using default"
        elif field.annotation == float:
            default_values[field_name] = 0.0
        elif field.annotation == int:
            default_values[field_name] = 0
        elif hasattr(field.annotation, "__origin__") and field.annotation.__origin__ == dict:
            default_values[field_name] = {}
        else:
            # For other types (like Literal), try to use the first allowed value
            if hasattr(field.annotation, "__args__"):
                default_values[field_name] = field.annotation.__args__[0]
            else:
                default_values[field_name] = None

    return model_class(**default_values)


def extract_json_from_response(content: str) -> dict | None:
    """Extracts JSON from markdown-formatted response."""
    try:
        json_start = content.find("```json")
        if json_start != -1:
            json_text = content[json_start + 7 :]  # Skip past ```json
            json_end = json_text.find("```")
            if json_end != -1:
                json_text = json_text[:json_end].strip()
                return json.loads(json_text)
    except Exception as e:
        print(f"Error extracting JSON from response: {e}")
    return None


def get_agent_model_config(state, agent_name):
    """
    Get model configuration for a specific agent from the state.
    Falls back to global model configuration if agent-specific config is not available.
    Always returns valid model_name and model_provider values.

    Override precedence (speed round 2, R4):
      1. Env AGENT_MODEL_<AGENT_NAME_UPPER> — exact per-agent override
         (e.g. AGENT_MODEL_SCENARIO_AGENT=qwen3.6-plus).
      2. Request-level per-agent config (API callers).
      3. Env PIPELINE_FAST_MODEL (default qwen3.6-plus) for the fast-tier
         group: investor_* agents, scenario_agent, power_law_agent.
         Set PIPELINE_FAST_MODEL="" to disable tiering.
      4. Global run model.
    """
    # 1. Exact env override
    if agent_name:
        _env_key = "AGENT_MODEL_" + str(agent_name).upper().replace("-", "_")
        _env_model = os.environ.get(_env_key, "").strip()
        if _env_model:
            _name, _provider = _resolve_env_model(_env_model)
            if _provider:
                return _name, _provider
            print(f"  [model-config] AGENT_MODEL override '{_env_model}' for "
                  f"'{agent_name}' not found in registry — using run model")

    request = state.get("metadata", {}).get("request")

    if request and hasattr(request, 'get_agent_model_config'):
        # Get agent-specific model configuration
        model_name, model_provider = request.get_agent_model_config(agent_name)
        # Ensure we have valid values
        if model_name and model_provider:
            return model_name, model_provider.value if hasattr(model_provider, 'value') else str(model_provider)

    # 3. Fast-tier group override (investors / scenario / trap agents)
    if agent_name:
        _fast_env = os.environ.get("PIPELINE_FAST_MODEL")
        _fast_model = (_fast_env if _fast_env is not None else _DEFAULT_FAST_MODEL).strip()
        if _fast_model and (
            agent_name in _FAST_TIER_AGENT_NAMES
            or str(agent_name).startswith(_FAST_TIER_PREFIXES)
        ):
            _name, _provider = _resolve_env_model(_fast_model)
            if _provider:
                return _name, _provider
            print(f"  [model-config] PIPELINE_FAST_MODEL '{_fast_model}' not found "
                  f"in registry — '{agent_name}' keeps the run model")

    # Fall back to global configuration (system defaults)
    model_name = state.get("metadata", {}).get("model_name") or "gpt-4.1"
    model_provider = state.get("metadata", {}).get("model_provider") or "OPENAI"

    # Convert enum to string if necessary
    if hasattr(model_provider, 'value'):
        model_provider = model_provider.value

    return model_name, model_provider

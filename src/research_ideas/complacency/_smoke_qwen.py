"""Quick liveness probe for the Qwen model used by the qualitative scorer."""
from src.llm.models import get_model, ModelProvider
llm = get_model("qwen3.6-plus", ModelProvider.ALIBABA)
resp = llm.invoke('Reply with the JSON object {"ok": true} and nothing else.')
print("Qwen response:", resp.content if hasattr(resp, "content") else resp)

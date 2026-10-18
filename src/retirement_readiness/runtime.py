"""Request-scoped access to a JSON-capable chat model.

The calculation engine never calls this module. Only interview and report
language stages can use the model. No credential or prompt is written to logs.
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace


class ModelNotConfigured(RuntimeError):
    """Raised before any network request when a model has not been configured."""


class ChatModel:
    def __init__(self, *, api_key: str, model: str, base_url: str):
        if not api_key or not model:
            raise ModelNotConfigured("Set LLM_API_KEY and LLM_MODEL to enable live AI stages.")
        if not base_url.startswith("https://"):
            raise ValueError("LLM_BASE_URL must use HTTPS.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.calls = []

    @classmethod
    def from_environment(cls):
        return cls(
            api_key=os.environ.get("LLM_API_KEY", ""),
            model=os.environ.get("LLM_MODEL", ""),
            base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        )

    async def chat_completion(self, *, stage="followup", **payload):
        import httpx

        payload["model"] = payload.get("model") or self.model
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        self.calls.append({"stage": stage, "usage": result.get("usage", {})})
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("The model response does not contain a chat message.") from error
        if not isinstance(content, str):
            raise ValueError("The model must return text containing JSON.")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


_model: ContextVar[ChatModel | None] = ContextVar("retirement_model", default=None)


@contextmanager
def model_session(model):
    """Isolate model configuration across concurrent household assessments."""
    token = _model.set(model)
    try:
        yield model
    finally:
        _model.reset(token)


class _ModelGateway:
    async def chat_completion(self, **kwargs):
        model = _model.get()
        if model is None:
            raise ModelNotConfigured("Live model calls are disabled in offline mode.")
        return await model.chat_completion(**kwargs)


openai_client = _ModelGateway()
settings = SimpleNamespace(FAST_MODEL=None)

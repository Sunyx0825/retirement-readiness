import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from retirement_readiness.core.report_stages import _call_validated_json_stage
from retirement_readiness.runtime import ChatModel, ModelNotConfigured, model_session


def test_model_requires_credentials_and_https():
    with pytest.raises(ModelNotConfigured):
        ChatModel(api_key="", model="test", base_url="https://example.com/v1")
    with pytest.raises(ValueError, match="HTTPS"):
        ChatModel(api_key="test", model="test", base_url="http://example.com/v1")


@pytest.mark.parametrize("valid", [True, False])
def test_gateway_payload_and_response_validation(monkeypatch, valid):
    requests = []

    def respond(request):
        requests.append(request)
        body = {"choices": [{"message": {"content": '{"ok":true}'}}]} if valid else {}
        return httpx.Response(200, json=body)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    model = ChatModel(api_key="test-only", model="test-model", base_url="https://example.com/v1")
    call = model.chat_completion(stage="assessment", messages=[], model=None)
    if valid:
        response = asyncio.run(call)
        assert json.loads(response.choices[0].message.content) == {"ok": True}
    else:
        with pytest.raises(ValueError, match="chat message"):
            asyncio.run(call)
    assert json.loads(requests[0].content)["model"] == "test-model"
    assert "stage" not in json.loads(requests[0].content)
    assert model.calls == [{"stage": "assessment", "usage": {}}]


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_invalid_stage_retries_once_and_reports_validation_error(repair_succeeds):
    class ScriptedModel:
        def __init__(self):
            self.requests = []

        async def chat_completion(self, **request):
            self.requests.append(request)
            body = {"ok": repair_succeeds and len(self.requests) == 2}
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))]
            )

    def validate(body):
        if not body or body.get("ok") is not True:
            raise ValueError("EXPECTED_TRUE")
        return body

    model = ScriptedModel()
    with model_session(model):
        call = _call_validated_json_stage("test-stage", {}, "test prompt", validate)
        if repair_succeeds:
            assert asyncio.run(call) == {"ok": True}
        else:
            with pytest.raises(ValueError, match="VALIDATION_FAILED"):
                asyncio.run(call)
    assert len(model.requests) == 2
    assert "EXPECTED_TRUE" in model.requests[1]["messages"][-1]["content"]

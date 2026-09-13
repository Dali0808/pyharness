import json

import httpx
import pytest

from ai.openai_compatible import OpenAICompatibleProvider
from ai.provider import ModelRegistry, ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
    ModelSpec,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)


@pytest.mark.asyncio
async def test_registry_routes_to_scripted_provider():
    model = ModelSpec(provider="scripted", id="test-model")
    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(content=[TextPart(text="pong")]),
                finish_reason="stop",
            )
        ]
    )

    registry = ModelRegistry()
    registry.register(model, provider)

    result = await registry.complete(
        model,
        ChatRequest(
            system_prompt="Reply briefly.",
            messages=[UserMessage(content="ping")],
        ),
    )

    assert result.message.content[0].text == "pong"
    assert provider.requests[0].messages[0].content == "ping"


@pytest.mark.asyncio
async def test_openai_adapter_serializes_history_and_parses_tool_calls():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "mock-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "calculator",
                                        "arguments": '{"expression":"21*2"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 7,
                    "total_tokens": 17,
                },
            },
        )

    provider = OpenAICompatibleProvider(
        provider_id="mock",
        base_url="https://example.test/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    model = ModelSpec(provider="mock", id="mock-model")

    request = ChatRequest(
        system_prompt="You are helpful.",
        messages=[
            UserMessage(content="Calculate 1 + 1."),
            AssistantMessage(
                content=[
                    ToolCallPart(
                        id="call-1",
                        name="calculator",
                        arguments_json='{"expression":"1+1"}',
                    )
                ]
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="calculator",
                content="2",
            ),
        ],
        tools=[
            ToolDefinition(
                name="calculator",
                description="Calculate an expression.",
                parameters={"type": "object"},
            )
        ],
    )

    try:
        result = await provider.complete(model, request)
    finally:
        await provider.aclose()

    body = seen["body"]
    print(json.dumps(seen["body"], ensure_ascii=False, indent=2))
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "calculator"
    assert body["messages"][3]["role"] == "tool"
    assert body["tools"][0]["function"]["name"] == "calculator"

    part = result.message.content[0]
    assert isinstance(part, ToolCallPart)
    assert part.name == "calculator"
    assert part.arguments_json == '{"expression":"21*2"}'
    assert result.finish_reason == "tool_calls"
    assert result.usage.total_tokens == 17
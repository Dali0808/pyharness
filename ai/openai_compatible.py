# src/pyharness/ai/openai_compatible.py
from __future__ import annotations

import json
from typing import Any

import httpx

from .provider import ProviderError
from .schemas import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
    ModelSpec,
    TextPart,
    ToolCallPart,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.id = provider_id

        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        model: ModelSpec,
        request: ChatRequest,
    ) -> ChatResponse:
        if model.provider != self.id:
            raise ValueError("The model belongs to another provider")

        if request.tools and not model.supports_tools:
            raise ValueError(f"{model.id} does not support tool calling")

        payload: dict[str, Any] = {
            "model": model.id,
            "messages": self._serialize_messages(request),
        }

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]

        try:
            response = await self._client.post("chat/completions", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                self._extract_error(exc.response),
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.id} request failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("Provider returned invalid JSON") from exc

        return self._parse_response(body, model)

    @staticmethod
    def _serialize_messages(request: ChatRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        if request.system_prompt.strip():
            messages.append({"role": "system", "content": request.system_prompt})

        for message in request.messages:
            if isinstance(message, UserMessage):
                messages.append({"role": "user", "content": message.content})

            elif isinstance(message, AssistantMessage):
                text = "".join(
                    part.text
                    for part in message.content
                    if isinstance(part, TextPart)
                )
                tool_calls = [
                    {
                        "id": part.id,
                        "type": "function",
                        "function": {
                            "name": part.name,
                            "arguments": part.arguments_json,
                        },
                    }
                    for part in message.content
                    if isinstance(part, ToolCallPart)
                ]

                payload: dict[str, Any] = {
                    "role": "assistant",
                    "content": text or None,
                }
                if tool_calls:
                    payload["tool_calls"] = tool_calls
                messages.append(payload)

            elif isinstance(message, ToolResultMessage):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )

        return messages

    @staticmethod
    def _parse_response(body: dict[str, Any], model: ModelSpec) -> ChatResponse:
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError("Provider returned no choices")

        choice = choices[0]
        raw_message = choice.get("message") or {}
        content = raw_message.get("content")
        parts = [TextPart(text=str(content))] if content else []

        for raw_call in raw_message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            call_id = raw_call.get("id")
            name = function.get("name")

            if not call_id or not name:
                raise ProviderError("Provider returned a malformed tool call")

            raw_arguments = function.get("arguments", "{}")
            arguments_json = (
                raw_arguments
                if isinstance(raw_arguments, str)
                else json.dumps(raw_arguments, ensure_ascii=False)
            )
            parts.append(
                ToolCallPart(
                    id=call_id,
                    name=name,
                    arguments_json=arguments_json,
                )
            )

        raw_finish_reason = choice.get("finish_reason") or "stop"
        finish_reason = {
            "tool_calls": "tool_calls",
            "length": "length",
        }.get(raw_finish_reason, "stop")

        raw_usage = body.get("usage") or {}
        prompt_details = raw_usage.get("prompt_tokens_details") or {}

        return ChatResponse(
            message=AssistantMessage(
                content=parts,
                provider=model.provider,
                model=str(body.get("model") or model.id),
                response_id=body.get("id"),
            ),
            finish_reason=finish_reason,
            raw_finish_reason=raw_finish_reason,
            usage=Usage(
                input_tokens=int(raw_usage.get("prompt_tokens") or 0),
                output_tokens=int(raw_usage.get("completion_tokens") or 0),
                total_tokens=int(raw_usage.get("total_tokens") or 0),
                cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
            ),
        )

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        try:
            body = response.json()
            error = body.get("error", {})
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        except ValueError:
            pass

        return f"Provider request failed with HTTP {response.status_code}"
# src/pyharness/ai/provider.py
from __future__ import annotations

from collections import deque
from typing import Iterable, Protocol

from .schemas import ChatRequest, ChatResponse, ModelSpec


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code is None or status_code == 429 or status_code >= 500


class LLMProvider(Protocol):
    id: str

    async def complete(
        self,
        model: ModelSpec,
        request: ChatRequest,
    ) -> ChatResponse: ...


class ModelRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], LLMProvider] = {}

    def register(self, model: ModelSpec, provider: LLMProvider) -> None:
        if model.provider != provider.id:
            raise ValueError("model.provider must equal provider.id")

        key = (model.provider, model.id)
        if key in self._entries:
            raise ValueError(f"Model already registered: {key}")

        self._entries[key] = provider

    async def complete(
        self,
        model: ModelSpec,
        request: ChatRequest,
    ) -> ChatResponse:
        provider = self._entries.get((model.provider, model.id))
        if provider is None:
            raise ProviderError(f"Unknown model: {model.provider}/{model.id}")

        return await provider.complete(model, request)


class ScriptedProvider:
    """用于确定性测试，不调用真实模型。"""

    id = "scripted"

    def __init__(self, responses: Iterable[ChatResponse]) -> None:
        self._responses = deque(responses)
        self.requests: list[ChatRequest] = []

    async def complete(
        self,
        model: ModelSpec,
        request: ChatRequest,
    ) -> ChatResponse:
        self.requests.append(request.model_copy(deep=True))

        if not self._responses:
            raise ProviderError("ScriptedProvider has no remaining response")

        return self._responses.popleft().model_copy(deep=True)
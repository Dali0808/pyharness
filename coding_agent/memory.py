from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class MemoryCoreClient:
    endpoint: str
    service_id: str
    team_id: str
    agent_id: str
    user_id: str
    api_key: str
    transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def from_env(cls) -> MemoryCoreClient | None:
        endpoint = os.environ.get("LARIO_MEMORY_URL")
        if not endpoint:
            return None
        names = {
            "service_id": "LARIO_MEMORY_SERVICE_ID",
            "team_id": "LARIO_MEMORY_TEAM_ID",
            "agent_id": "LARIO_MEMORY_AGENT_ID",
            "user_id": "LARIO_MEMORY_USER_ID",
            "api_key": "LARIO_MEMORY_API_KEY",
        }
        missing = [name for name in names.values() if not os.environ.get(name)]
        if missing:
            raise ValueError("missing " + ", ".join(missing))
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("LARIO_MEMORY_URL must be an http(s) URL without credentials, query, or fragment")
        return cls(endpoint=endpoint, **{field: os.environ[name] for field, name in names.items()})

    async def _post(self, path: str, body: dict[str, object]) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "x-tdai-service-id": self.service_id,
        }
        async with httpx.AsyncClient(base_url=self.endpoint.rstrip("/") + "/", headers=headers,
                                     timeout=3.0, transport=self.transport) as client:
            response = await client.post(path, json={
                "team_id": self.team_id,
                "agent_id": self.agent_id,
                "user_id": self.user_id,
                **body,
            })
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise ValueError("MemoryCore returned an invalid response")
        return payload["data"]

    async def recall(self, question: str) -> str:
        data = await self._post("v3/atomic/search", {
            "query": question[:2048], "limit": 3,
        })
        items = data.get("items", [])
        if not isinstance(items, list):
            raise ValueError("MemoryCore returned invalid memories")
        memories = [item.get("content", "") for item in items if isinstance(item, dict)]
        text = "\n".join(f"- {content[:400]}" for content in memories if isinstance(content, str) and content.strip())
        return text[:1200]

    async def capture(self, session_id: str, question: str, answer: str) -> None:
        # Capture only the user turn and final answer; tool messages remain in JSONL.
        if not answer.strip() or len(question) > 8192 or len(answer) > 8192:
            return
        await self._post("v3/conversation/add", {
            "session_id": session_id,
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
        })

"""Offline browser fixture: real accounts/HTTP/files, temporary data, no paid model."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

import uvicorn

from ai.provider import ScriptedProvider
from ai.schemas import TextPart, Usage
from coding_agent.server import ServerConfig, create_server_app
from test.test_web import call, reply


def provider(config):
    responses = []
    if "读取" in config.task:
        responses.append(call("read_file", {"path": "README.md"}, "read-1"))
    if "禁止编码" in config.task:
        responses.append(call("write_file", {"path": "forbidden.txt", "content": "no"}, "forbidden"))
    responses.extend([
        call("create_artifact", {"name": f"report-{uuid4().hex}.md", "content": "# 测试报告\n\n文件已分析。"}, "artifact-1"),
        reply("已处理你的任务。\n\n**结果**可在资料与产物中下载。"),
    ])
    for response in responses:
        response.usage = Usage(input_tokens=5, output_tokens=3, total_tokens=8)

    class BrowserProvider(ScriptedProvider):
        async def complete_stream(self, model, request, on_text):
            if "等待流式" in config.task:
                self.requests.append(request)
                on_text("未完成的流式文本")
                await asyncio.Event().wait()
            response = await self.complete(model, request)
            for part in response.message.content:
                if isinstance(part, TextPart):
                    on_text(part.text)
            return response

    return BrowserProvider(responses)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lario-web-browser-") as root:
        app = create_server_app(ServerConfig(Path(root), public_url="http://127.0.0.1:4173", model="test-model", provider_id="scripted", daily_requests=10000, daily_tokens=10_000_000), provider_factory=provider)
        for username in ("alice", "bob"):
            app.state.accounts.create_user(username, "browser-only-password-2026")
        uvicorn.run(app, host="127.0.0.1", port=4173, access_log=False)

"""Offline browser fixture: real HTTP/app/tools, isolated files, no model API or Docker."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import uvicorn

from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart, ToolCallPart
from coding_agent import command
from coding_agent.settings import parse_interface_args
from coding_agent.web import create_web_app
from test.conftest import DockerStub


def call(name, arguments, identifier):
    return ChatResponse(message=AssistantMessage(content=[ToolCallPart(id=identifier, name=name, arguments_json=json.dumps(arguments))]), finish_reason="tool_calls")


def provider(config):
    responses = [call("write_file", {"path": f"browser-result-{uuid4().hex}.txt", "content": "written-after-approval"}, "edit-1")]
    if "运行检查" in config.task:
        responses.append(call("run_command", {"argv": ["python", "-V"], "cwd": "."}, "command-1"))
    responses.append(ChatResponse(message=AssistantMessage(content=[TextPart(text="已处理你的任务。\n\n**结果**可在审查面板查看。")]), finish_reason="stop"))
    class BrowserProvider(ScriptedProvider):
        async def complete_stream(self, model, request, on_text):
            if "等待流式" in config.task:
                on_text("未完成的流式文本")
                await asyncio.Event().wait()
            return await self.complete(model, request)
    return BrowserProvider(responses)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lario-web-browser-") as root:
        config = parse_interface_args(["--workspace", root, "--provider-id", "scripted", "--model", "test-model"])
        fake = DockerStub()
        fake.exit_code = 17
        command._docker = fake.execute
        command.shutil.which = lambda name: "docker-stub" if name == "docker" else None
        app = create_web_app(config, token="browser-test-token", port=4173, provider_factory=provider)
        uvicorn.run(app, host="127.0.0.1", port=4173, access_log=False)

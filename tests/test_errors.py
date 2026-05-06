import os

import aiohttp
import httpx
import pytest

from openreward import AsyncOpenReward
from openreward.api.sandboxes.types import (SandboxSettings)
from openreward.environments import Environment, tool, ToolOutput
from openreward.environments.server import Server
from openreward.environments.types import (Blocks, JSONObject, TextBlock)


class SandboxEnv(Environment):
    """使用提供的设置启动/停止沙盒的最小环境。"""

    def __init__(self, task_spec: JSONObject, secrets: dict[str, str] = {}, settings: SandboxSettings | None = None) -> None:
        super().__init__(task_spec, secrets=secrets)
        self._client = AsyncOpenReward()
        self.sandbox = self._client.sandbox(settings)

    async def setup(self):
        await self.sandbox.start()

    async def teardown(self):
        await self.sandbox.stop()

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="test")]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{}]

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="done")], reward=1.0, finished=True)


class FailingEnv(Environment):
    """list_tasks 始终抛出异常的环境，用于触发未处理的异常。"""

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="test")]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        raise RuntimeError("something broke internally")

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="done")], reward=1.0, finished=True)


def _make_server(return_errors: str) -> Server:
    """使用 FailingEnv 和给定的 return_errors 模式构建服务器。"""
    return Server([FailingEnv], return_errors=return_errors)


async def _trigger_error(app) -> httpx.Response:
    """访问 tasks 端点，该端点在 FailingEnv 中引发 RuntimeError。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/failingenv/tasks", json={"split": "train"})

@pytest.mark.asyncio
async def test_return_errors_none():
    """使用 return_errors='none'，500 响应应为不透明的。"""
    server = _make_server("none")
    resp = await _trigger_error(server.app)

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal Server Error"
    assert "RuntimeError" not in body["detail"]
    assert "something broke" not in body["detail"]


@pytest.mark.asyncio
async def test_return_errors_exception():
    """使用 return_errors='exception'，500 响应应包含异常字符串。"""
    server = _make_server("exception")
    resp = await _trigger_error(server.app)

    assert resp.status_code == 500
    body = resp.json()
    assert "RuntimeError" in body["detail"]
    assert "something broke internally" in body["detail"]
    # 不应包含完整的 traceback
    assert "Traceback" not in body["detail"]


@pytest.mark.asyncio
async def test_return_errors_stacktrace():
    """使用 return_errors='stacktrace'，500 响应应包含完整的 traceback。"""
    server = _make_server("stacktrace")
    resp = await _trigger_error(server.app)

    assert resp.status_code == 500
    body = resp.json()
    assert "Traceback (most recent call last)" in body["detail"]
    assert "RuntimeError: something broke internally" in body["detail"]

async def _start_stop(settings: SandboxSettings):
    env = SandboxEnv(task_spec={}, settings=settings)
    try:
        await env.setup()
    finally:
        await env.teardown()


@pytest.mark.asyncio
async def test_nonexistent_environment():
    """引用不存在的 OpenReward 环境的沙盒应失败并返回清晰的错误消息。"""
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await _start_stop(SandboxSettings(
            environment="GeneralReasoning/idontexist",
            image="python:3.11-slim",
            machine_size="1:2",
        ))
    assert exc_info.value.status == 404
    assert "idontexist" in exc_info.value.message
    assert "GeneralReasoning" in exc_info.value.message


# TODO: 此测试当前会无限挂起。
# @pytest.mark.asyncio
# async def test_nonexistent_image():
#     """引用不存在的容器镜像的沙盒应失败并返回清晰的错误消息。"""
#     with pytest.raises(RuntimeError) as exc_info:
#         await _start_stop(SandboxSettings(
#             environment="GeneralReasoning/test-env",
#             image="generalreasoning/idontexist:latest",
#             machine_size="1:2",
#         ))
#     assert "idontexist" in str(exc_info.value)
#     assert "ErrImagePull" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_api_key():
    """未提供 API 密钥时创建沙盒应失败并返回清晰的认证消息。"""
    key = os.environ.pop("OPENREWARD_API_KEY", None)
    try:
        with pytest.raises(Exception) as exc_info:
            await _start_stop(SandboxSettings(
                environment="GeneralReasoning/test-env",
                image="python:3.11-slim",
                machine_size="1:2",
            ))
        assert "Authentication Failed" in str(exc_info.value)
    finally:
        if key is not None:
            os.environ["OPENREWARD_API_KEY"] = key

"""通过 env.session(toolset=...) 传递的会话级工具集测试。

这些测试运行绑定到进程内环境的真实服务器，这些环境使用
模拟沙盒，因此 ClaudeCodeToolset / CodexToolset 工具通过 HTTP 层
端到端执行（POST /create, GET /task_tools, POST /call）。
"""

import asyncio
import base64
from threading import Thread
from typing import Generator

import aiohttp
import pytest
import uvicorn

from openreward import AsyncOpenReward
from openreward.api.environments.types import ToolCallError
from openreward.api.sandboxes.types import RunResult
from openreward.environments import Environment, Server, ToolOutput, tool
from openreward.environments.types import Blocks, JSONObject, TextBlock
from openreward.toolsets import ClaudeCodeToolset


# ── 记录每次调用的模拟沙盒 ──

class _MockSandbox:
    """跟踪对 run/check_run/upload/download 的调用以进行断言。"""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}

    async def run(self, cmd: str, **kwargs) -> RunResult:
        self.commands.append(cmd)
        return RunResult(output=f"ran: {cmd}", return_code=0)

    async def check_run(self, cmd: str, **kwargs) -> str:
        self.commands.append(cmd)
        # 模拟 _upload_text 使用的 "echo BASE64 | base64 -d > path"
        if " | base64 -d > " in cmd:
            encoded = cmd.split("'", 2)[1]
            path = cmd.split(" > ", 1)[1]
            self.files[path] = base64.b64decode(encoded)
        return ""

    async def download(self, path: str) -> bytes:
        return self.files.get(path, b"")

    async def upload(self, local_path, container_path: str) -> None:  # pragma: no cover
        with open(local_path, "rb") as f:
            self.files[container_path] = f.read()


# ── 测试环境 ──

class EnvWithSandbox(Environment):
    """暴露模拟沙盒并拥有自身 ``bash`` 工具的环境，
    用于验证会话工具集是否覆盖同名环境工具。"""

    def __init__(self, task_spec: JSONObject = {}, secrets: dict[str, str] = {}) -> None:
        super().__init__(task_spec, secrets)
        self.sandbox = _MockSandbox()

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="prompt")]

    @tool
    async def bash(self) -> ToolOutput:
        """环境定义的 bash，应被工具集覆盖。"""
        return ToolOutput(blocks=[TextBlock(text="ENV_BASH")], reward=0.0, finished=False)

    @tool
    async def submit(self) -> ToolOutput:
        """环境定义的 submit 工具。"""
        return ToolOutput(blocks=[TextBlock(text="submitted")], reward=1.0, finished=True)


class EnvWithoutSandbox(Environment):
    """没有 ``self.sandbox`` 的环境 —— 工具集绑定必须抛出异常。"""

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="prompt")]

    @tool
    async def submit(self) -> ToolOutput:
        """环境定义的 submit 工具。"""
        return ToolOutput(blocks=[TextBlock(text="submitted")], reward=1.0, finished=True)


# ── 服务器 fixture（与 test_environment.py 使用不同端口） ──

async def _wait_for_server(base_url: str, timeout: float = 5.0) -> None:
    import time
    start = time.monotonic()
    async with aiohttp.ClientSession() as session:
        while time.monotonic() - start < timeout:
            try:
                async with session.get(
                    f"{base_url}/health",
                    timeout=aiohttp.ClientTimeout(total=0.5),
                ) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.1)
    pytest.fail("Server failed to start")


@pytest.fixture(scope="module")
def server() -> Generator[str, None, None]:
    host = "localhost"
    port = 8082
    app = Server(environments=[EnvWithSandbox, EnvWithoutSandbox]).app
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    instance = uvicorn.Server(config)

    thread = Thread(target=instance.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    asyncio.run(_wait_for_server(base_url))
    yield base_url
    instance.should_exit = True


@pytest.fixture
def client() -> AsyncOpenReward:
    return AsyncOpenReward(api_key="test")


# ── list_tools 合并 ──

CLAUDE_CODE_TOOLS = {"bash", "glob", "grep", "read", "write", "edit", "todo_write"}


@pytest.mark.asyncio
async def test_session_with_toolset_lists_merged_tools(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="claude-code") as session:
        tools = await session.list_tools()
        names = [t.name for t in tools]
        for name in CLAUDE_CODE_TOOLS:
            assert name in names, f"missing tool {name}"
        # 环境中未被覆盖的其他工具被保留。
        assert "submit" in names
        # 环境的 `bash` 被工具集的 `bash` 替换。firehorse
        # 描述以 "Executes a given bash command" 开头。
        bash_spec = next(t for t in tools if t.name == "bash")
        assert bash_spec.description.startswith("Executes a given bash command")


@pytest.mark.asyncio
async def test_session_without_toolset_unchanged(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0]) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools}
        assert names == {"bash", "submit"}


# ── call_tool 路由 ──

@pytest.mark.asyncio
async def test_session_toolset_call_routes_to_toolset(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="claude-code") as session:
        result = await session.call_tool("bash", {"command": "echo hi"})
        # 工具集 bash 使用 sandbox.run 并在输出前加上 "ran: "。
        assert "ran: echo hi" in result.blocks[0].text
        # 环境级 bash 返回 "ENV_BASH" —— 确认我们没有命中它。
        assert "ENV_BASH" not in result.blocks[0].text


@pytest.mark.asyncio
async def test_session_toolset_write_then_read_roundtrip(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="claude-code") as session:
        await session.call_tool("write", {"file_path": "/tmp/x.txt", "content": "hello"})
        result = await session.call_tool("read", {"file_path": "/tmp/x.txt"})
        # 读取在每行上使用 cat -n 格式。
        assert "hello" in result.blocks[0].text


# ── 无沙盒环境抛出异常 ──

@pytest.mark.asyncio
async def test_session_toolset_no_sandbox_raises(client: AsyncOpenReward, server: str):
    env = client.environments.get(
        "envwithoutsandbox", variant="envwithoutsandbox", base_url=server,
    )
    tasks = await env.list_tasks(split="train")
    # 服务器将 ValueError 存储在 setup_errors 中，并在下一个
    # 命中 require_existing_session 的请求上抛出（例如 list_tools/call_tool）。
    with pytest.raises(Exception) as exc_info:
        async with env.session(tasks[0], toolset="claude-code") as session:
            await session.list_tools()
    msg = str(exc_info.value)
    assert "sandbox" in msg.lower()


# ── Codex 工具集 ──

@pytest.mark.asyncio
async def test_codex_toolset_only_bash(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="codex") as session:
        tools = await session.list_tools()
        toolset_names = {t.name for t in tools}
        # Codex 工具集仅提供 bash；环境的 submit + 被替换的 bash。
        assert "bash" in toolset_names
        assert "submit" in toolset_names
        # Codex 工具集没有 read/write 等工具。
        for name in ("read", "write", "edit", "grep", "glob", "todo_write"):
            assert name not in toolset_names
        bash_spec = next(t for t in tools if t.name == "bash")
        # Codex bash 描述是上游 Codex shell_command 行。
        assert bash_spec.description.startswith("Runs a shell command")


# ── 客户端拒绝未知工具集 ──

@pytest.mark.asyncio
async def test_unknown_toolset_name_rejected_clientside(client: AsyncOpenReward, server: str):
    env = client.environments.get("envwithsandbox", variant="envwithsandbox", base_url=server)
    with pytest.raises(ValueError, match="Unknown toolset"):
        env.session(split="train", index=0, toolset="nonexistent")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_session_toolset_warns_on_shadow(monkeypatch):
    """当会话工具集遮蔽同名环境工具时，记录警告。

    直接测试会话辅助函数（无 HTTP），以便我们可以猴子补丁
    日志记录器并观察警告 —— ``structlog.testing.capture_logs`` 无法跨
    uvicorn 工作线程边界工作。
    """
    from openreward.environments import session as session_module
    from openreward.environments.session import call_session_tool, list_session_tools

    captured: list[tuple[str, dict]] = []

    def fake_warning(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr(session_module.logger, "warning", fake_warning)

    env = EnvWithSandbox(task_spec={"id": "1"})
    toolset = ClaudeCodeToolset(env)

    # 列出工具时应为 `bash` 触发遮蔽警告。
    list_session_tools(env, toolset)
    list_warnings = [e for e in captured if e[0] == "session_toolset_shadows_env_tool"]
    assert len(list_warnings) >= 1
    assert any(e[1].get("tool") == "bash" for e in list_warnings)
    assert any(e[1].get("toolset") == "ClaudeCodeToolset" for e in list_warnings)

    # 调用被遮蔽的工具时应触发另一条警告。
    captured.clear()
    await call_session_tool(env, toolset, "bash", {"command": "echo hi"})
    call_warnings = [e for e in captured if e[0] == "session_toolset_shadows_env_tool"]
    assert len(call_warnings) == 1
    assert call_warnings[0][1]["tool"] == "bash"

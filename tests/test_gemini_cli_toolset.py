"""Gemini CLI 工具集的测试。

单元测试（无 HTTP 服务器）验证工具集组合、模式正确性，
以及通过模拟沙盒执行工具。会话集成测试运行真实的 FastAPI 服务器，
以验证通过会话层的端到端 HTTP 路由。
"""

import asyncio
import base64
from threading import Thread
from typing import Generator

import aiohttp
import pytest
import uvicorn

from openreward import AsyncOpenReward
from openreward.api.sandboxes.types import RunResult
from openreward.environments import Environment, Server, ToolOutput, tool
from openreward.environments.types import Blocks, JSONObject, TextBlock
from openreward.toolsets import GeminiCliToolset


# ── 模拟沙盒 ──


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
        if " | base64 -d > " in cmd:
            encoded = cmd.split("'", 2)[1]
            path = cmd.split(" > ", 1)[1]
            self.files[path] = base64.b64decode(encoded)
        return ""

    async def download(self, path: str) -> bytes:
        return self.files.get(path, b"")

    async def upload(self, local_path, container_path: str) -> None:
        with open(local_path, "rb") as f:
            self.files[container_path] = f.read()


# ── 测试环境 ──


class EnvWithGeminiCli(Environment):
    """使用 GeminiCliToolset 和模拟沙盒的环境。"""

    toolsets = [GeminiCliToolset]

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
    async def submit(self) -> ToolOutput:
        """提交答案。"""
        return ToolOutput(blocks=[TextBlock(text="submitted")], reward=1.0, finished=True)


# ===== 单元测试（无 HTTP） =====


GEMINI_CLI_TOOLS = {
    "run_shell_command",
    "glob",
    "grep_search",
    "read_file",
    "write_file",
    "replace",
    "list_directory",
    "write_todos",
}


def test_gemini_cli_toolset_name():
    assert GeminiCliToolset.name() == "gemini-cli"


def test_list_tools_discovers_gemini_cli_tools():
    tools_output = EnvWithGeminiCli.list_tools()
    tool_names = {t.name for t in tools_output.tools}
    for name in GEMINI_CLI_TOOLS:
        assert name in tool_names, f"missing tool {name}"
    assert "submit" in tool_names


def test_gemini_cli_tool_schemas():
    """验证每个工具的 input_schema 具有正确的属性。"""
    tools_output = EnvWithGeminiCli.list_tools()
    specs = {t.name: t for t in tools_output.tools}

    # run_shell_command
    schema = specs["run_shell_command"].input_schema
    assert "command" in schema["properties"]
    assert "command" in schema["required"]
    assert "description" in schema["properties"]
    assert "dir_path" in schema["properties"]
    assert "is_background" in schema["properties"]
    assert "delay_ms" in schema["properties"]

    # glob
    schema = specs["glob"].input_schema
    assert "pattern" in schema["properties"]
    assert "pattern" in schema["required"]
    assert "dir_path" in schema["properties"]

    # grep_search
    schema = specs["grep_search"].input_schema
    assert "pattern" in schema["properties"]
    assert "pattern" in schema["required"]
    assert "dir_path" in schema["properties"]
    assert "include_pattern" in schema["properties"]
    assert "exclude_pattern" in schema["properties"]
    assert "names_only" in schema["properties"]
    assert "max_matches_per_file" in schema["properties"]
    assert "total_max_matches" in schema["properties"]

    # read_file
    schema = specs["read_file"].input_schema
    assert "file_path" in schema["properties"]
    assert "file_path" in schema["required"]
    assert "start_line" in schema["properties"]
    assert "end_line" in schema["properties"]

    # write_file
    schema = specs["write_file"].input_schema
    assert "file_path" in schema["properties"]
    assert "content" in schema["properties"]
    assert "file_path" in schema["required"]
    assert "content" in schema["required"]

    # replace
    schema = specs["replace"].input_schema
    assert "file_path" in schema["properties"]
    assert "old_string" in schema["properties"]
    assert "new_string" in schema["properties"]
    assert "allow_multiple" in schema["properties"]
    assert "instruction" in schema["properties"]
    for field in ("file_path", "old_string", "new_string"):
        assert field in schema["required"]

    # list_directory
    schema = specs["list_directory"].input_schema
    assert "dir_path" in schema["properties"]
    assert "dir_path" in schema["required"]
    assert "ignore" in schema["properties"]

    # write_todos
    schema = specs["write_todos"].input_schema
    assert "todos" in schema["properties"]
    assert "todos" in schema["required"]


@pytest.mark.asyncio
async def test_call_run_shell_command():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool("run_shell_command", {"command": "echo hello"})
    assert result.root.ok is True
    assert "ran: echo hello" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_glob():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool("glob", {"pattern": "*.py"})
    assert result.root.ok is True
    assert "ran:" in result.root.output.blocks[0].text
    assert "*.py" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_grep_search():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool("grep_search", {"pattern": "TODO"})
    assert result.root.ok is True
    assert "ran:" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_grep_search_with_options():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool(
        "grep_search",
        {
            "pattern": "TODO",
            "include_pattern": "*.py",
            "names_only": True,
            "total_max_matches": 10,
        },
    )
    assert result.root.ok is True
    text = result.root.output.blocks[0].text
    assert "ran:" in text


@pytest.mark.asyncio
async def test_call_read_file():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    # 在模拟沙盒中预填充文件
    env.sandbox.files["/tmp/test.txt"] = b"line1\nline2\nline3\n"
    result = await env._call_tool("read_file", {"file_path": "/tmp/test.txt"})
    assert result.root.ok is True
    text = result.root.output.blocks[0].text
    assert "line1" in text
    assert "line2" in text
    assert "line3" in text


@pytest.mark.asyncio
async def test_call_write_file_then_read_file():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    await env._call_tool("write_file", {"file_path": "/tmp/x.txt", "content": "hello world"})
    result = await env._call_tool("read_file", {"file_path": "/tmp/x.txt"})
    assert result.root.ok is True
    assert "hello world" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_replace():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    # 写入初始内容
    await env._call_tool("write_file", {"file_path": "/tmp/r.txt", "content": "foo bar baz"})
    # 替换
    result = await env._call_tool(
        "replace",
        {"file_path": "/tmp/r.txt", "old_string": "bar", "new_string": "qux"},
    )
    assert result.root.ok is True
    assert "Successfully edited" in result.root.output.blocks[0].text
    # 验证
    result = await env._call_tool("read_file", {"file_path": "/tmp/r.txt"})
    assert "qux" in result.root.output.blocks[0].text
    assert "bar" not in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_replace_not_found():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    await env._call_tool("write_file", {"file_path": "/tmp/nf.txt", "content": "abc"})
    result = await env._call_tool(
        "replace",
        {"file_path": "/tmp/nf.txt", "old_string": "xyz", "new_string": "123"},
    )
    assert result.root.ok is True  # 工具返回 ToolOutput，而非硬错误
    assert "not found" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_replace_multiple():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    await env._call_tool("write_file", {"file_path": "/tmp/m.txt", "content": "aaa bbb aaa"})
    # 未设置 allow_multiple → 错误，因为 "aaa" 出现了两次
    result = await env._call_tool(
        "replace",
        {"file_path": "/tmp/m.txt", "old_string": "aaa", "new_string": "ccc"},
    )
    assert "appears 2 times" in result.root.output.blocks[0].text

    # 设置 allow_multiple → 成功
    result = await env._call_tool(
        "replace",
        {
            "file_path": "/tmp/m.txt",
            "old_string": "aaa",
            "new_string": "ccc",
            "allow_multiple": True,
        },
    )
    assert "Successfully edited" in result.root.output.blocks[0].text
    result = await env._call_tool("read_file", {"file_path": "/tmp/m.txt"})
    assert "aaa" not in result.root.output.blocks[0].text
    assert "ccc" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_list_directory():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool("list_directory", {"dir_path": "/tmp"})
    assert result.root.ok is True
    assert "ran:" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_call_write_todos():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool(
        "write_todos",
        {
            "todos": [
                {"description": "Task 1", "status": "pending"},
                {"description": "Task 2", "status": "in_progress"},
                {"description": "Task 3", "status": "completed"},
            ]
        },
    )
    assert result.root.ok is True
    text = result.root.output.blocks[0].text
    assert "[pending] Task 1" in text
    assert "[in_progress] Task 2" in text
    assert "[completed] Task 3" in text


@pytest.mark.asyncio
async def test_todo_status_validation():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    result = await env._call_tool(
        "write_todos",
        {"todos": [{"description": "Bad", "status": "invalid_status"}]},
    )
    assert result.root.ok is False
    assert "validation error" in result.root.error.lower()


@pytest.mark.asyncio
async def test_gemini_cli_lazy_instantiation():
    env = EnvWithGeminiCli(task_spec={"id": "1"})
    assert len(env._toolset_instances) == 0
    await env._call_tool("run_shell_command", {"command": "ls"})
    assert len(env._toolset_instances) == 1
    assert GeminiCliToolset in env._toolset_instances


# ===== 会话集成测试（HTTP 服务器） =====


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
    port = 8083
    app = Server(environments=[EnvWithGeminiCli]).app
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


@pytest.mark.asyncio
async def test_session_with_gemini_cli_lists_merged_tools(
    client: AsyncOpenReward, server: str
):
    env = client.environments.get(
        "envwithgeminicli", variant="envwithgeminicli", base_url=server
    )
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="gemini-cli") as session:
        tools = await session.list_tools()
        names = {t.name for t in tools}
        for name in GEMINI_CLI_TOOLS:
            assert name in names, f"missing tool {name}"
        assert "submit" in names
        shell_spec = next(t for t in tools if t.name == "run_shell_command")
        assert shell_spec.description.startswith("This tool executes a given shell command")


@pytest.mark.asyncio
async def test_session_gemini_cli_call_routes_to_toolset(
    client: AsyncOpenReward, server: str
):
    env = client.environments.get(
        "envwithgeminicli", variant="envwithgeminicli", base_url=server
    )
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="gemini-cli") as session:
        result = await session.call_tool(
            "run_shell_command", {"command": "echo hi"}
        )
        assert "ran: echo hi" in result.blocks[0].text


@pytest.mark.asyncio
async def test_session_gemini_cli_write_then_read_roundtrip(
    client: AsyncOpenReward, server: str
):
    env = client.environments.get(
        "envwithgeminicli", variant="envwithgeminicli", base_url=server
    )
    tasks = await env.list_tasks(split="train")
    async with env.session(tasks[0], toolset="gemini-cli") as session:
        await session.call_tool(
            "write_file", {"file_path": "/tmp/roundtrip.txt", "content": "gemini"}
        )
        result = await session.call_tool(
            "read_file", {"file_path": "/tmp/roundtrip.txt"}
        )
        assert "gemini" in result.blocks[0].text

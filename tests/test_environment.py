import pytest
import asyncio
import uvicorn
import aiohttp
from threading import Thread
from typing import Generator
from openreward import AsyncOpenReward
from openreward.environments import Environment, Server, tool, ToolOutput
from openreward.environments.types import Blocks, TextBlock, JSONObject, ToolSpec, ListToolsOutput


class Foo(Environment):
    def setup(self):
        pass

    def teardown(self):
        pass

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=str(self.task_spec["foo"]))]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        assert split == "train"
        return [{"foo": "bar"}]

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="foo_result")], reward=1.0, finished=True)


class Bar(Environment):
    def setup(self):
        pass

    def teardown(self):
        pass

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=str(self.task_spec["bar"]))]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        assert split == "test"
        return [{"bar": "baz"}]

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="bar_result")], reward=0.5, finished=True)


class AsyncBaz(Environment):
    """带有异步 list_tasks 的环境，用于测试服务器中的 maybe_await。"""

    def setup(self):
        pass

    def teardown(self):
        pass

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=str(self.task_spec["baz"]))]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    async def list_tasks(cls, split: str) -> list[JSONObject]:
        assert split == "train"
        return [{"baz": "qux"}]

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="baz_result")], reward=0.75, finished=True)

class LargeEnv(Environment):
    """重写了 num_tasks/get_task 的环境，用于避免实例化所有任务。"""

    def setup(self):
        pass

    def teardown(self):
        pass

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=f"task_{self.task_spec['id']}")]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train", "test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        raise NotImplementedError("LargeEnv doesn't support listing all tasks")

    @classmethod
    async def num_tasks(cls, split: str) -> int:
        """返回固定数量，而不实例化所有任务。"""
        return {"train": 1000, "test": 200}[split]

    @classmethod
    async def get_task(cls, split: str, index: int) -> JSONObject:
        """根据索引即时生成任务。"""
        limit = {"train": 1000, "test": 200}[split]
        if index < 0 or index >= limit:
            raise IndexError(f"index {index} out of range for split {split}")
        return {"id": index, "split": split}

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(
            blocks=[TextBlock(text=f"result_{self.task_spec['id']}")],
            reward=self.task_spec["id"] / 1000,
            finished=True,
        )

class EnvWithTaskTools(Environment):
    def setup(self):
        pass

    def teardown(self):
        pass

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="task tools prompt")]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1", "tools": ["read_file", "write_file"]}]

    def list_task_tools(self) -> ListToolsOutput:
        tools = []
        for tool_name in self.task_spec.get("tools", []):
            tools.append(ToolSpec(name=tool_name, description=f"Task tool: {tool_name}", input_schema=None))
        return ListToolsOutput(tools=tools)

    @tool
    async def submit(self) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text="task_tools_result")], reward=1.0, finished=True)

    @tool(shared=False)
    async def non_shared_helper(self) -> ToolOutput:
        """一个非共享工具，不应出现在 env.list_tools() 中"""
        return ToolOutput(blocks=[TextBlock(text="helper")], reward=0.0, finished=False)


async def wait_for_server(base_url: str, timeout: float = 5.0):
    """使用 aiohttp 等待服务器就绪。"""
    import time
    start = time.monotonic()
    async with aiohttp.ClientSession() as session:
        while time.monotonic() - start < timeout:
            try:
                async with session.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=0.5)) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.1)
    pytest.fail("Server failed to start")

@pytest.fixture(scope="module")
def server() -> Generator[str, None, None]:
    """在后台线程中启动服务器并生成基础 URL。"""
    host = "localhost"
    port = 8080
    app = Server(environments=[Foo, Bar, AsyncBaz, LargeEnv, EnvWithTaskTools]).app
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server_instance = uvicorn.Server(config)

    thread = Thread(target=server_instance.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    asyncio.run(wait_for_server(base_url))

    yield base_url
    server_instance.should_exit = True


@pytest.fixture
def client() -> AsyncOpenReward:
    """创建异步客户端。"""
    return AsyncOpenReward(api_key="test")


# 默认环境测试（未指定变体，使用第一个环境）

@pytest.mark.asyncio
async def test_default_variant_splits(client: AsyncOpenReward, server: str):
    """测试默认变体是否正常工作——重定向到第一个环境。"""
    environment = client.environments.get("foo", base_url=server)
    splits = await environment.list_splits()
    assert splits == ["train"]


@pytest.mark.asyncio
async def test_default_variant_tools(client: AsyncOpenReward, server: str):
    """测试使用默认变体列出工具。"""
    environment = client.environments.get("foo", base_url=server)
    tools = await environment.list_tools()
    tool_names = [t.name for t in tools]
    assert "submit" in tool_names


@pytest.mark.asyncio
async def test_default_variant_list_tasks(client: AsyncOpenReward, server: str):
    """测试使用默认变体列出任务。"""
    environment = client.environments.get("foo", base_url=server)
    tasks = await environment.list_tasks(split="train")
    assert len(tasks) == 1
    assert tasks[0].task_spec == {"foo": "bar"}


@pytest.mark.asyncio
async def test_default_variant_call_tool(client: AsyncOpenReward, server: str):
    """测试使用默认变体调用工具。"""
    environment = client.environments.get("foo", base_url=server)
    tasks = await environment.list_tasks(split="train")
    async with environment.session(tasks[0]) as session:
        res = await session.call_tool("submit")
        assert res.reward == 1.0
        assert res.finished is True
        assert len(res.blocks) == 1
        assert res.blocks[0].type == "text"
        assert res.blocks[0].text == "foo_result"


# 显式变体测试

@pytest.mark.asyncio
async def test_explicit_variant_foo_splits(client: AsyncOpenReward, server: str):
    """测试显式变体 Foo。"""
    environment = client.environments.get("foo", variant="foo", base_url=server)
    splits = await environment.list_splits()
    assert splits == ["train"]


@pytest.mark.asyncio
async def test_explicit_variant_bar_splits(client: AsyncOpenReward, server: str):
    """测试显式变体 Bar。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    splits = await environment.list_splits()
    assert splits == ["test"]


@pytest.mark.asyncio
async def test_explicit_variant_bar_tools(client: AsyncOpenReward, server: str):
    """测试显式变体 Bar 的工具列表。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    tools = await environment.list_tools()
    tool_names = [t.name for t in tools]
    assert "submit" in tool_names


@pytest.mark.asyncio
async def test_explicit_variant_bar_list_tasks(client: AsyncOpenReward, server: str):
    """测试显式变体 Bar 的任务列表。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    tasks = await environment.list_tasks(split="test")
    assert len(tasks) == 1
    assert tasks[0].task_spec == {"bar": "baz"}


@pytest.mark.asyncio
async def test_explicit_variant_bar_call_tool(client: AsyncOpenReward, server: str):
    """测试显式变体 Bar 的工具调用。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    tasks = await environment.list_tasks(split="test")
    async with environment.session(tasks[0]) as session:
        res = await session.call_tool("submit")
        assert res.reward == 0.5
        assert res.finished is True
        assert len(res.blocks) == 1
        assert res.blocks[0].type == "text"
        assert res.blocks[0].text == "bar_result"


# 异步 list_tools 测试

@pytest.mark.asyncio
async def test_async_list_tools_returns_correct_schema(client: AsyncOpenReward, server: str):
    """测试异步 list_tools 返回正确结构的工具规范。"""
    environment = client.environments.get("foo", base_url=server)
    tools = await environment.list_tools()
    assert len(tools) >= 1
    submit_tool = next(t for t in tools if t.name == "submit")
    assert submit_tool.name == "submit"
    assert submit_tool.input_schema is None  # submit 不接受输入模型


@pytest.mark.asyncio
async def test_async_list_tools_with_provider_format(client: AsyncOpenReward, server: str):
    """测试异步 list_tools 支持特定提供商格式。"""
    environment = client.environments.get("foo", base_url=server)

    openai_tools = await environment.list_tools(format="openai")
    assert len(openai_tools) >= 1
    assert openai_tools[0]["type"] == "function"
    assert openai_tools[0]["name"] == "submit"

    anthropic_tools = await environment.list_tools(format="anthropic")
    assert len(anthropic_tools) >= 1
    assert anthropic_tools[0]["type"] == "custom"
    assert anthropic_tools[0]["name"] == "submit"
    assert anthropic_tools[0]["input_schema"] == {"type": "object", "properties": {}}

    google_tools = await environment.list_tools(format="google")
    assert len(google_tools) >= 1
    assert google_tools[0]["name"] == "submit"


@pytest.mark.asyncio
async def test_async_list_tools_on_session(client: AsyncOpenReward, server: str):
    """测试在活跃异步会话上调用 list_tools。"""
    environment = client.environments.get("foo", base_url=server)
    tasks = await environment.list_tasks(split="train")
    async with environment.session(tasks[0]) as session:
        tools = await session.list_tools()
        tool_names = [t.name for t in tools]
        assert "submit" in tool_names


@pytest.mark.asyncio
async def test_async_list_tools_session_with_provider_format(client: AsyncOpenReward, server: str):
    """测试会话 list_tools 支持特定提供商格式。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    tasks = await environment.list_tasks(split="test")
    async with environment.session(tasks[0]) as session:
        openai_tools = await session.list_tools(format="openai")
        assert len(openai_tools) >= 1
        assert openai_tools[0]["type"] == "function"


# 带有异步类方法的环境测试

@pytest.mark.asyncio
async def test_async_env_list_tools(client: AsyncOpenReward, server: str):
    """测试在带有异步 list_tasks 的环境上调用 list_tools。"""
    environment = client.environments.get("asyncbaz", variant="asyncbaz", base_url=server)
    tools = await environment.list_tools()
    tool_names = [t.name for t in tools]
    assert "submit" in tool_names


@pytest.mark.asyncio
async def test_async_env_list_splits(client: AsyncOpenReward, server: str):
    """测试在带有异步 list_tasks 的环境上调用 list_splits。"""
    environment = client.environments.get("asyncbaz", variant="asyncbaz", base_url=server)
    splits = await environment.list_splits()
    assert splits == ["train"]


@pytest.mark.asyncio
async def test_async_env_list_tasks(client: AsyncOpenReward, server: str):
    """测试异步 list_tasks 被正确处理。"""
    environment = client.environments.get("asyncbaz", variant="asyncbaz", base_url=server)
    tasks = await environment.list_tasks(split="train")
    assert len(tasks) == 1
    assert tasks[0].task_spec == {"baz": "qux"}


@pytest.mark.asyncio
async def test_async_env_call_tool(client: AsyncOpenReward, server: str):
    """测试在带有异步类方法的环境上进行完整会话流程。"""
    environment = client.environments.get("asyncbaz", variant="asyncbaz", base_url=server)
    tasks = await environment.list_tasks(split="train")
    async with environment.session(tasks[0]) as session:
        res = await session.call_tool("submit")
        assert res.reward == 0.75
        assert res.finished is True
        assert res.blocks[0].text == "baz_result"


# 多个变体交互测试

@pytest.mark.asyncio
async def test_multiple_variants_different_splits(client: AsyncOpenReward, server: str):
    """测试不同变体返回不同的 splits。"""
    foo_env = client.environments.get("foo", variant="foo", base_url=server)
    bar_env = client.environments.get("bar", variant="bar", base_url=server)

    foo_splits = await foo_env.list_splits()
    bar_splits = await bar_env.list_splits()

    assert foo_splits == ["train"]
    assert bar_splits == ["test"]
    assert foo_splits != bar_splits


@pytest.mark.asyncio
async def test_multiple_variants_different_tasks(client: AsyncOpenReward, server: str):
    """测试不同变体返回不同的任务。"""
    foo_env = client.environments.get("foo", variant="foo", base_url=server)
    bar_env = client.environments.get("bar", variant="bar", base_url=server)

    foo_tasks = await foo_env.list_tasks(split="train")
    bar_tasks = await bar_env.list_tasks(split="test")

    assert foo_tasks[0].task_spec == {"foo": "bar"}
    assert bar_tasks[0].task_spec == {"bar": "baz"}


@pytest.mark.asyncio
async def test_multiple_variants_concurrent_sessions(client: AsyncOpenReward, server: str):
    """测试在多个变体上并发运行会话。"""
    foo_env = client.environments.get("foo", variant="foo", base_url=server)
    bar_env = client.environments.get("bar", variant="bar", base_url=server)

    foo_tasks = await foo_env.list_tasks(split="train")
    bar_tasks = await bar_env.list_tasks(split="test")

    async with foo_env.session(foo_tasks[0]) as foo_session:
        async with bar_env.session(bar_tasks[0]) as bar_session:
            foo_res = await foo_session.call_tool("submit")
            bar_res = await bar_session.call_tool("submit")

            assert foo_res.reward == 1.0
            assert foo_res.blocks[0].text == "foo_result"

            assert bar_res.reward == 0.5
            assert bar_res.blocks[0].text == "bar_result"


@pytest.mark.asyncio
async def test_multiple_variants_prompt_isolation(client: AsyncOpenReward, server: str):
    """测试提示在不同变体之间正确隔离。"""
    foo_env = client.environments.get("foo", variant="foo", base_url=server)
    bar_env = client.environments.get("bar", variant="bar", base_url=server)

    foo_tasks = await foo_env.list_tasks(split="train")
    bar_tasks = await bar_env.list_tasks(split="test")

    async with foo_env.session(foo_tasks[0]) as foo_session:
        foo_prompt = await foo_session.get_prompt()
        assert foo_prompt[0].text == "bar"  # 来自 {"foo": "bar"}

    async with bar_env.session(bar_tasks[0]) as bar_session:
        bar_prompt = await bar_session.get_prompt()
        assert bar_prompt[0].text == "baz"  # 来自 {"bar": "baz"}

# 基于索引的 API 测试（num_tasks, get_task）

@pytest.mark.asyncio
async def test_num_tasks_foo(server: str):
    """测试 Foo 的 num_tasks 返回正确数量。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/num_tasks",
            json={"split": "train"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["num_tasks"] == 1


@pytest.mark.asyncio
async def test_num_tasks_bar(server: str):
    """测试 Bar 的 num_tasks 返回正确数量。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/bar/num_tasks",
            json={"split": "test"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["num_tasks"] == 1


@pytest.mark.asyncio
async def test_num_tasks_invalid_split(server: str):
    """测试 num_tasks 拒绝无效的 split。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/num_tasks",
            json={"split": "nonexistent"},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_num_tasks_async_env(server: str):
    """测试 num_tasks 在带有异步 list_tasks 的环境上正常工作。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/asyncbaz/num_tasks",
            json={"split": "train"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["num_tasks"] == 1


@pytest.mark.asyncio
async def test_get_task_foo(server: str):
    """测试 get_task 返回正确的任务（按索引）。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/task",
            json={"split": "train", "index": 0},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_get_task_bar(server: str):
    """测试 Bar 环境上的 get_task。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/bar/task",
            json={"split": "test", "index": 0},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"bar": "baz"}


@pytest.mark.asyncio
async def test_get_task_invalid_split(server: str):
    """测试 get_task 拒绝无效的 split。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/task",
            json={"split": "nonexistent", "index": 0},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_get_task_index_out_of_bounds(server: str):
    """测试 get_task 对越界索引返回 400。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/task",
            json={"split": "train", "index": 999},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_get_task_async_env(server: str):
    """测试 get_task 在带有异步 list_tasks 的环境上正常工作。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/asyncbaz/task",
            json={"split": "train", "index": 0},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"baz": "qux"}


@pytest.mark.asyncio
async def test_get_task_unknown_env(server: str):
    """测试 get_task 对不存在的环境返回 404。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/doesnotexist/task",
            json={"split": "train", "index": 0},
        ) as resp:
            assert resp.status == 404


# LargeEnv 测试（重写了 num_tasks / get_task）

@pytest.mark.asyncio
async def test_large_env_list_tasks_returns_400(server: str):
    """测试 list_tasks 在环境抛出 NotImplementedError 时返回 400。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/tasks",
            json={"split": "train"},
        ) as resp:
            assert resp.status == 400
            body = await resp.json()
            assert "index-based API" in body["detail"]


@pytest.mark.asyncio
async def test_large_env_num_tasks_train(server: str):
    """测试重写的 num_tasks 为 train 返回自定义数量。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/num_tasks",
            json={"split": "train"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["num_tasks"] == 1000


@pytest.mark.asyncio
async def test_large_env_num_tasks_test(server: str):
    """测试重写的 num_tasks 为 test 返回自定义数量。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/num_tasks",
            json={"split": "test"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["num_tasks"] == 200


@pytest.mark.asyncio
async def test_large_env_get_task_first(server: str):
    """测试重写的 get_task 在索引 0 处返回正确任务。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task",
            json={"split": "train", "index": 0},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"id": 0, "split": "train"}


@pytest.mark.asyncio
async def test_large_env_get_task_mid(server: str):
    """测试重写的 get_task 在任意索引处返回正确任务。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task",
            json={"split": "train", "index": 500},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"id": 500, "split": "train"}


@pytest.mark.asyncio
async def test_large_env_get_task_last(server: str):
    """测试重写的 get_task 在最后一个有效索引处返回正确任务。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task",
            json={"split": "test", "index": 199},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["task"] == {"id": 199, "split": "test"}


@pytest.mark.asyncio
async def test_large_env_get_task_out_of_bounds(server: str):
    """测试重写的 get_task 拒绝越界索引。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task",
            json={"split": "train", "index": 1000},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_large_env_get_task_invalid_split(server: str):
    """测试重写的 get_task 拒绝无效的 split。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/num_tasks",
            json={"split": "nonexistent"},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_large_env_session_from_get_task(server: str):
    """测试使用 split/index 创建会话的完整流程。"""
    sid = str(__import__("uuid").uuid4())
    headers = {"X-Session-ID": sid}
    async with aiohttp.ClientSession() as http:
        # 使用 split/index 而非 task_spec 创建会话
        async with http.post(
            f"{server}/create",
            json={"env_name": "largeenv", "split": "train", "index": 42},
            headers=headers,
        ) as resp:
            assert resp.status == 200

        # 调用工具——服务器内部解析了 task_spec
        async with http.post(
            f"{server}/largeenv/call",
            json={"name": "submit", "input": {}},
            headers=headers,
        ) as resp:
            assert resp.status == 200
            body = await resp.text()
            assert "result_42" in body

        # 清理
        async with http.post(f"{server}/delete", headers=headers) as resp:
            assert resp.status == 200


@pytest.mark.asyncio
async def test_create_session_rejects_both_spec_and_index(server: str):
    """测试同时提供 task_spec 和 split/index 会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"env_name": "foo", "task_spec": {"foo": "bar"}, "split": "train", "index": 0},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 422


@pytest.mark.asyncio
async def test_create_session_rejects_neither_spec_nor_index(server: str):
    """测试既不提供 task_spec 也不提供 split/index 会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"env_name": "foo"},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 422


@pytest.mark.asyncio
async def test_create_session_rejects_split_without_index(server: str):
    """测试仅提供 split 会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"env_name": "foo", "split": "train"},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 422


@pytest.mark.asyncio
async def test_create_session_invalid_split(server: str):
    """测试 create 中无效的 split 会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"env_name": "foo", "split": "nonexistent", "index": 0},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_create_session_index_out_of_bounds(server: str):
    """测试 create 中越界的索引会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"env_name": "largeenv", "split": "train", "index": 9999},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 400


# 通过 AsyncOpenReward 客户端进行基于索引的 API 测试

@pytest.mark.asyncio
async def test_client_num_tasks_foo(client: AsyncOpenReward, server: str):
    """通过客户端 SDK 测试 num_tasks。"""
    environment = client.environments.get("foo", base_url=server)
    count = await environment.num_tasks("train")
    assert count == 1


@pytest.mark.asyncio
async def test_client_num_tasks_bar(client: AsyncOpenReward, server: str):
    """通过客户端 SDK 测试 Bar 的 num_tasks。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    count = await environment.num_tasks("test")
    assert count == 1


@pytest.mark.asyncio
async def test_client_get_task_foo(client: AsyncOpenReward, server: str):
    """通过客户端 SDK 测试 get_task。"""
    environment = client.environments.get("foo", base_url=server)
    task = await environment.get_task("train", 0)
    assert task.task_spec == {"foo": "bar"}


@pytest.mark.asyncio
async def test_client_get_task_bar(client: AsyncOpenReward, server: str):
    """通过客户端 SDK 测试 Bar 的 get_task。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    task = await environment.get_task("test", 0)
    assert task.task_spec == {"bar": "baz"}


@pytest.mark.asyncio
async def test_client_get_task_async_env(client: AsyncOpenReward, server: str):
    """在带有异步 list_tasks 的环境上测试 get_task。"""
    environment = client.environments.get("asyncbaz", variant="asyncbaz", base_url=server)
    task = await environment.get_task("train", 0)
    assert task.task_spec == {"baz": "qux"}


@pytest.mark.asyncio
async def test_client_session_with_split_index(client: AsyncOpenReward, server: str):
    """测试通过 split/index 而非 Task 对象创建会话。"""
    environment = client.environments.get("foo", base_url=server)
    async with environment.session(split="train", index=0) as session:
        res = await session.call_tool("submit")
        assert res.reward == 1.0
        assert res.finished is True
        assert res.blocks[0].text == "foo_result"


@pytest.mark.asyncio
async def test_client_session_with_split_index_bar(client: AsyncOpenReward, server: str):
    """在 Bar 上测试 split/index 会话。"""
    environment = client.environments.get("bar", variant="bar", base_url=server)
    async with environment.session(split="test", index=0) as session:
        res = await session.call_tool("submit")
        assert res.reward == 0.5
        assert res.blocks[0].text == "bar_result"


@pytest.mark.asyncio
async def test_client_session_with_split_index_prompt(client: AsyncOpenReward, server: str):
    """测试 split/index 会话上的 get_prompt。"""
    environment = client.environments.get("foo", base_url=server)
    async with environment.session(split="train", index=0) as session:
        prompt = await session.get_prompt()
        assert prompt[0].text == "bar"


@pytest.mark.asyncio
async def test_client_session_with_split_index_list_tools(client: AsyncOpenReward, server: str):
    """测试 split/index 会话上的 list_tools。"""
    environment = client.environments.get("foo", base_url=server)
    async with environment.session(split="train", index=0) as session:
        tools = await session.list_tools()
        assert any(t.name == "submit" for t in tools)


@pytest.mark.asyncio
async def test_client_session_rejects_both_task_and_index(client: AsyncOpenReward, server: str):
    """测试同时提供 task 和 split/index 会抛出 ValueError。"""
    environment = client.environments.get("foo", base_url=server)
    task = await environment.get_task("train", 0)
    with pytest.raises(ValueError, match="either task or both split and index"):
        environment.session(task=task, split="train", index=0)


@pytest.mark.asyncio
async def test_client_session_rejects_neither_task_nor_index(client: AsyncOpenReward, server: str):
    """测试既不提供 task 也不提供 split/index 会抛出 ValueError。"""
    environment = client.environments.get("foo", base_url=server)
    with pytest.raises(ValueError, match="either task or both split and index"):
        environment.session()


@pytest.mark.asyncio
async def test_client_session_rejects_split_without_index(client: AsyncOpenReward, server: str):
    """测试仅提供 split 会抛出 ValueError。"""
    environment = client.environments.get("foo", base_url=server)
    with pytest.raises(ValueError, match="either task or both split and index"):
        environment.session(split="train")

# 通过客户端 SDK 进行 LargeEnv 测试

@pytest.mark.asyncio
async def test_client_large_env_num_tasks(client: AsyncOpenReward, server: str):
    """通过客户端测试 LargeEnv 的 num_tasks。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    assert await environment.num_tasks("train") == 1000
    assert await environment.num_tasks("test") == 200


@pytest.mark.asyncio
async def test_client_large_env_get_task(client: AsyncOpenReward, server: str):
    """通过客户端测试 LargeEnv 的 get_task。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    task = await environment.get_task("train", 42)
    assert task.task_spec == {"id": 42, "split": "train"}


@pytest.mark.asyncio
async def test_client_large_env_session_with_task(client: AsyncOpenReward, server: str):
    """测试从 get_task 结果创建的 LargeEnv 会话。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    task = await environment.get_task("train", 42)
    async with environment.session(task=task) as session:
        res = await session.call_tool("submit")
        assert "result_42" in res.blocks[0].text


@pytest.mark.asyncio
async def test_client_large_env_session_with_task_different_name(client: AsyncOpenReward, server: str):
    """测试当服务器名称与变体名称不同时，get_task -> session(task) 是否正常工作。"""
    environment = client.environments.get("someserver", variant="largeenv", base_url=server)
    task = await environment.get_task("train", 42)
    async with environment.session(task=task) as session:
        res = await session.call_tool("submit")
        assert "result_42" in res.blocks[0].text


# task_tools 端点测试

@pytest.mark.asyncio
async def test_session_list_tools_returns_task_tools(client: AsyncOpenReward, server: str):
    """测试 session.list_tools() 返回来自 list_task_tools() 的任务特定工具。"""
    environment = client.environments.get("envwithtasktools", variant="envwithtasktools", base_url=server)
    tasks = await environment.list_tasks(split="train")
    async with environment.session(tasks[0]) as session:
        tools = await session.list_tools()
        tool_names = [t.name for t in tools]
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        # 共享工具 (submit) + 2 个任务特定工具
        assert "submit" in tool_names
        assert len(tools) == 3


@pytest.mark.asyncio
async def test_env_list_tools_returns_shared_tools(client: AsyncOpenReward, server: str):
    """测试 environment.list_tools() 返回共享工具（而非任务工具）。"""
    environment = client.environments.get("envwithtasktools", variant="envwithtasktools", base_url=server)
    tools = await environment.list_tools()
    tool_names = [t.name for t in tools]
    # submit 是共享的 @tool，任务工具 (read_file, write_file) 和非共享工具不应出现
    assert "submit" in tool_names
    assert "read_file" not in tool_names
    assert "write_file" not in tool_names
    assert "non_shared_helper" not in tool_names
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_client_large_env_session_with_split_index(client: AsyncOpenReward, server: str):
    """测试直接从 split/index 创建的 LargeEnv 会话。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    async with environment.session(split="train", index=99) as session:
        res = await session.call_tool("submit")
        assert "result_99" in res.blocks[0].text


@pytest.mark.asyncio
async def test_client_large_env_concurrent_index_sessions(client: AsyncOpenReward, server: str):
    """测试 LargeEnv 上的多个并发 split/index 会话。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    async with environment.session(split="train", index=0) as s1:
        async with environment.session(split="train", index=500) as s2:
            r1 = await s1.call_tool("submit")
            r2 = await s2.call_tool("submit")
            assert "result_0" in r1.blocks[0].text
            assert "result_500" in r2.blocks[0].text

# 可选 env_name 测试（服务器默认使用第一个环境）

@pytest.mark.asyncio
async def test_create_session_defaults_env_name(server: str):
    """测试省略 env_name 时默认使用第一个注册的环境（Foo）。"""
    sid = str(__import__("uuid").uuid4())
    headers = {"X-Session-ID": sid}
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"split": "train", "index": 0},
            headers=headers,
        ) as resp:
            assert resp.status == 200

        # Foo 是第一个环境，因此提示应来自 {"foo": "bar"}
        async with http.get(
            f"{server}/foo/prompt",
            headers=headers,
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data[0]["text"] == "bar"

        async with http.post(f"{server}/delete", headers=headers) as resp:
            assert resp.status == 200


@pytest.mark.asyncio
async def test_create_session_rejects_no_task_source_without_env_name(server: str):
    """测试省略所有内容（无 env_name、无 task_spec、无 split/index）会被拒绝。"""
    sid = str(__import__("uuid").uuid4())
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={},
            headers={"X-Session-ID": sid},
        ) as resp:
            assert resp.status == 422


@pytest.mark.asyncio
async def test_create_session_without_env_name_with_split_index(server: str):
    """测试不带 env_name 的 split/index——服务器默认使用第一个环境并解析任务。"""
    sid = str(__import__("uuid").uuid4())
    headers = {"X-Session-ID": sid}
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{server}/create",
            json={"split": "train", "index": 0},
            headers=headers,
        ) as resp:
            assert resp.status == 200

        # 在默认（Foo）环境上调用 submit
        async with http.post(
            f"{server}/foo/call",
            json={"name": "submit", "input": {}},
            headers=headers,
        ) as resp:
            assert resp.status == 200
            body = await resp.text()
            assert "foo_result" in body

        async with http.post(f"{server}/delete", headers=headers) as resp:
            assert resp.status == 200


# get_task_range 服务器端点测试

@pytest.mark.asyncio
async def test_get_task_range_basic(server: str):
    """测试 get_task_range 对基本范围返回任务。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "train", "start": 0, "stop": 3},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 3
            assert data["tasks"][0] == {"id": 0, "split": "train"}
            assert data["tasks"][2] == {"id": 2, "split": "train"}


@pytest.mark.asyncio
async def test_get_task_range_none_start(server: str):
    """测试 get_task_range 在 start 为 None 时默认使用 0。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "test", "stop": 2},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 2
            assert data["tasks"][0] == {"id": 0, "split": "test"}


@pytest.mark.asyncio
async def test_get_task_range_none_stop(server: str):
    """测试 get_task_range 在 stop 为 None 时默认使用 num_tasks。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "test", "start": 198},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 2
            assert data["tasks"][0] == {"id": 198, "split": "test"}
            assert data["tasks"][1] == {"id": 199, "split": "test"}


@pytest.mark.asyncio
async def test_get_task_range_negative_start(server: str):
    """测试 get_task_range 使用负的 start（相对于末尾）。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "test", "start": -3},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 3
            assert data["tasks"][0] == {"id": 197, "split": "test"}
            assert data["tasks"][2] == {"id": 199, "split": "test"}


@pytest.mark.asyncio
async def test_get_task_range_negative_stop(server: str):
    """测试 get_task_range 使用负的 stop。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "test", "start": 0, "stop": -198},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 2
            assert data["tasks"][0] == {"id": 0, "split": "test"}
            assert data["tasks"][1] == {"id": 1, "split": "test"}


@pytest.mark.asyncio
async def test_get_task_range_empty(server: str):
    """测试 get_task_range 在 start >= stop 时返回空列表。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "test", "start": 5, "stop": 5},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["tasks"] == []


@pytest.mark.asyncio
async def test_get_task_range_invalid_split(server: str):
    """测试 get_task_range 拒绝无效的 split。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/largeenv/task_range",
            json={"split": "nonexistent", "start": 0, "stop": 1},
        ) as resp:
            assert resp.status == 400


@pytest.mark.asyncio
async def test_get_task_range_foo(server: str):
    """在小型环境上测试 get_task_range。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server}/foo/task_range",
            json={"split": "train", "start": 0, "stop": 1},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert len(data["tasks"]) == 1
            assert data["tasks"][0] == {"foo": "bar"}


# 通过 AsyncOpenReward 客户端进行 get_task_range 测试

@pytest.mark.asyncio
async def test_client_get_task_range_basic(client: AsyncOpenReward, server: str):
    """通过客户端 SDK 测试 get_task_range。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    tasks = await environment.get_task_range("train", 0, 3)
    assert len(tasks) == 3
    assert tasks[0].task_spec == {"id": 0, "split": "train"}
    assert tasks[2].task_spec == {"id": 2, "split": "train"}


@pytest.mark.asyncio
async def test_client_get_task_range_defaults(client: AsyncOpenReward, server: str):
    """通过客户端测试使用默认 start/stop 的 get_task_range。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    tasks = await environment.get_task_range("test")
    assert len(tasks) == 200


@pytest.mark.asyncio
async def test_client_get_task_range_negative(client: AsyncOpenReward, server: str):
    """通过客户端测试使用负索引的 get_task_range。"""
    environment = client.environments.get("largeenv", variant="largeenv", base_url=server)
    tasks = await environment.get_task_range("test", -2)
    assert len(tasks) == 2
    assert tasks[0].task_spec == {"id": 198, "split": "test"}
    assert tasks[1].task_spec == {"id": 199, "split": "test"}

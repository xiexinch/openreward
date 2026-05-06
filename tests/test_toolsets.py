"""工具集组合系统的测试。"""

from typing import Any

import pytest
from pydantic import BaseModel, Field

from openreward.environments import Environment, Toolset, tool, ToolOutput, TextBlock
from openreward.environments.types import Blocks, JSONObject
from openreward.api.sandboxes.types import RunResult


# ===== 测试工具集 =====

class SimpleParams(BaseModel):
    message: str = Field(..., description="Test message")


class SimpleToolset:
    """不需要沙盒的简单工具集"""

    @tool
    async def simple_tool(self, params: SimpleParams) -> ToolOutput:
        """一个简单的测试工具"""
        return ToolOutput(
            blocks=[TextBlock(text=f"Simple: {params.message}")],
            reward=0.0,
            finished=False,
        )


class MockSandbox:
    """用于测试的模拟沙盒"""
    async def run(self, cmd: str, **kwargs) -> RunResult:
        return RunResult(output=f"Executed: {cmd}", return_code=0)


class SandboxToolset(Toolset):
    """需要沙盒的工具集"""

    @tool
    async def sandbox_tool(self, params: SimpleParams) -> ToolOutput:
        """使用沙盒的工具"""
        output, code = await self.sandbox.run("test command")
        return ToolOutput(
            blocks=[TextBlock(text=f"Sandbox: {params.message}, output={output}")],
            reward=0.0,
            finished=False,
        )


class AnotherToolset:
    """用于测试多个工具集的另一个简单工具集"""

    @tool
    async def another_tool(self) -> ToolOutput:
        """另一个无参数的测试工具"""
        return ToolOutput(
            blocks=[TextBlock(text="Another toolset")],
            reward=0.5,
            finished=False,
        )


# ===== 测试环境 =====

class EnvWithSimpleToolset(Environment):
    """带有简单工具集的环境"""
    toolsets = [SimpleToolset]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt")]

    @tool
    async def env_tool(self) -> ToolOutput:
        """在环境自身上定义的工具"""
        return ToolOutput(
            blocks=[TextBlock(text="From environment")],
            reward=1.0,
            finished=True,
        )


class EnvWithSandboxToolset(Environment):
    """带有沙盒工具集的环境"""
    toolsets = [SandboxToolset]

    def __init__(self, task_spec: JSONObject = {}, secrets: dict[str, str] = {}):
        super().__init__(task_spec, secrets)
        self.sandbox = MockSandbox()

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt with sandbox")]


class EnvWithMultipleToolsets(Environment):
    """带有多个工具集的环境"""
    toolsets = [SimpleToolset, AnotherToolset]

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt")]


class EnvWithCustomSandboxAttr(Environment):
    """带有自定义沙盒属性名称的环境"""

    class CustomSandboxToolset(Toolset):
        def __init__(self, env: Any, sandbox_attr: str = "custom_sandbox"):
            super().__init__(env, sandbox_attr)

        @tool
        async def custom_sandbox_tool(self) -> ToolOutput:
            """使用自定义沙盒属性的工具"""
            output, code = await self.sandbox.run("custom command")
            return ToolOutput(
                blocks=[TextBlock(text=f"Custom sandbox: {output}")],
                reward=0.0,
                finished=False,
            )

    toolsets = [CustomSandboxToolset]

    def __init__(self, task_spec: JSONObject = {}, secrets: dict[str, str] = {}):
        super().__init__(task_spec, secrets)
        self.custom_sandbox = MockSandbox()

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt")]


# ===== 测试 =====

@pytest.mark.asyncio
async def test_list_tools_discovers_toolset_tools():
    """测试 list_tools() 从类级工具集中发现工具"""
    tools_output = EnvWithSimpleToolset.list_tools()
    tool_names = [t.name for t in tools_output.tools]

    # 应同时包含环境工具和工具集工具
    assert "env_tool" in tool_names
    assert "simple_tool" in tool_names


@pytest.mark.asyncio
async def test_list_tools_with_multiple_toolsets():
    """测试 list_tools() 从多个工具集中发现工具"""
    tools_output = EnvWithMultipleToolsets.list_tools()
    tool_names = [t.name for t in tools_output.tools]

    assert "simple_tool" in tool_names
    assert "another_tool" in tool_names


@pytest.mark.asyncio
async def test_call_toolset_tool():
    """测试调用工具集中的工具"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    result = await env._call_tool("simple_tool", {"message": "Hello"})

    assert result.root.ok is True
    assert len(result.root.output.blocks) == 1
    assert result.root.output.blocks[0].text == "Simple: Hello"


@pytest.mark.asyncio
async def test_call_environment_tool():
    """测试调用环境自身上定义的工具"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    result = await env._call_tool("env_tool", {})

    assert result.root.ok is True
    assert len(result.root.output.blocks) == 1
    assert result.root.output.blocks[0].text == "From environment"
    assert result.root.output.reward == 1.0
    assert result.root.output.finished is True


@pytest.mark.asyncio
async def test_lazy_instantiation():
    """测试工具集在第一次工具调用时惰性实例化"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    # 在任何工具调用之前，工具集实例应为空
    assert len(env._toolset_instances) == 0

    # 调用工具集工具
    await env._call_tool("simple_tool", {"message": "Test"})

    # 现在工具集应已实例化并缓存
    assert len(env._toolset_instances) == 1
    assert SimpleToolset in env._toolset_instances


@pytest.mark.asyncio
async def test_toolset_caching():
    """测试工具集实例被缓存和重用"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    # 第一次调用实例化
    await env._call_tool("simple_tool", {"message": "First"})
    first_instance = env._toolset_instances[SimpleToolset]

    # 第二次调用重用同一实例
    await env._call_tool("simple_tool", {"message": "Second"})
    second_instance = env._toolset_instances[SimpleToolset]

    assert first_instance is second_instance


@pytest.mark.asyncio
async def test_toolset_with_sandbox():
    """测试需要沙盒访问的工具集"""
    env = EnvWithSandboxToolset(task_spec={"id": "1"})

    result = await env._call_tool("sandbox_tool", {"message": "Test"})

    assert result.root.ok is True
    assert "Sandbox: Test" in result.root.output.blocks[0].text
    assert "Executed: test command" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_custom_sandbox_attribute():
    """测试带有自定义沙盒属性名称的工具集"""
    env = EnvWithCustomSandboxAttr(task_spec={"id": "1"})

    result = await env._call_tool("custom_sandbox_tool", {})

    assert result.root.ok is True
    assert "Custom sandbox" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_tool_not_found():
    """测试调用不存在的工具"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    result = await env._call_tool("nonexistent_tool", {})

    assert result.root.ok is False
    assert "not a valid tool" in result.root.error


@pytest.mark.asyncio
async def test_multiple_toolsets_tool_calls():
    """测试调用来自不同工具集的工具"""
    env = EnvWithMultipleToolsets(task_spec={"id": "1"})

    # 调用第一个工具集的工具
    result1 = await env._call_tool("simple_tool", {"message": "Test1"})
    assert result1.root.ok is True
    assert "Simple: Test1" in result1.root.output.blocks[0].text

    # 调用第二个工具集的工具
    result2 = await env._call_tool("another_tool", {})
    assert result2.root.ok is True
    assert "Another toolset" in result2.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_tool_validation_error():
    """测试工具参数验证是否有效"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    # 调用缺少必需参数的工具
    result = await env._call_tool("simple_tool", {})

    assert result.root.ok is False
    assert "validation error" in result.root.error.lower()


@pytest.mark.asyncio
async def test_toolset_tool_schema():
    """测试工具集工具在 list_tools() 中具有正确的模式"""
    tools_output = EnvWithSimpleToolset.list_tools()

    simple_tool = next((t for t in tools_output.tools if t.name == "simple_tool"), None)

    assert simple_tool is not None
    assert simple_tool.description == "A simple test tool"
    assert simple_tool.input_schema is not None
    assert "message" in simple_tool.input_schema["properties"]


@pytest.mark.asyncio
async def test_toolset_without_sandbox():
    """测试简单工具集在没有沙盒的情况下也能工作"""
    env = EnvWithSimpleToolset(task_spec={"id": "1"})

    # 不应具有沙盒属性
    assert not hasattr(env, 'sandbox')

    # 但工具集工具仍应正常工作
    result = await env._call_tool("simple_tool", {"message": "No sandbox"})

    assert result.root.ok is True
    assert "Simple: No sandbox" in result.root.output.blocks[0].text


@pytest.mark.asyncio
async def test_tool_name_collision_detected_in_list_tools():
    """测试 list_tools() 检测工具名称冲突并抛出错误"""

    class ToolsetWithSubmit:
        @tool
        async def submit(self) -> ToolOutput:
            return ToolOutput(
                blocks=[TextBlock(text="From toolset")],
                reward=0.0,
                finished=False,
            )

    class EnvWithConflictingTool(Environment):
        toolsets = [ToolsetWithSubmit]

        @classmethod
        def list_splits(cls) -> list[str]:
            return ["train"]

        @classmethod
        def list_tasks(cls, split: str) -> list[JSONObject]:
            return [{"id": "1"}]

        def get_prompt(self) -> Blocks:
            return [TextBlock(text="Test")]

        @tool
        async def submit(self) -> ToolOutput:
            return ToolOutput(
                blocks=[TextBlock(text="From environment")],
                reward=1.0,
                finished=True,
            )

    # list_tools() 应在冲突时抛出 ValueError
    with pytest.raises(ValueError) as excinfo:
        EnvWithConflictingTool.list_tools()

    assert "Tool name collision" in str(excinfo.value)
    assert "'submit'" in str(excinfo.value)
    assert "ToolsetWithSubmit" in str(excinfo.value)


@pytest.mark.asyncio
async def test_tool_name_collision_detected_in_call_tool():
    """测试 _call_tool() 检测工具名称冲突并返回错误"""

    class ToolsetWithCollision:
        @tool
        async def my_tool(self) -> ToolOutput:
            return ToolOutput(
                blocks=[TextBlock(text="From toolset")],
                reward=0.0,
                finished=False,
            )

    class EnvWithCollision(Environment):
        toolsets = [ToolsetWithCollision]

        @classmethod
        def list_splits(cls) -> list[str]:
            return ["train"]

        @classmethod
        def list_tasks(cls, split: str) -> list[JSONObject]:
            return [{"id": "1"}]

        def get_prompt(self) -> Blocks:
            return [TextBlock(text="Test")]

        @tool
        async def my_tool(self) -> ToolOutput:
            return ToolOutput(
                blocks=[TextBlock(text="From environment")],
                reward=1.0,
                finished=True,
            )

    # 调用工具应返回有关冲突的错误
    env = EnvWithCollision(task_spec={"id": "1"})
    result = await env._call_tool("my_tool", {})

    assert result.root.ok is False
    assert "Tool name collision" in result.root.error
    assert "'my_tool'" in result.root.error
    assert "ToolsetWithCollision" in result.root.error


# ===== @tool(shared=False) 和 list_task_tools 的测试 =====


class EnvWithNonSharedTool(Environment):
    """带有共享和非共享工具的环境"""

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt")]

    @tool
    async def shared_tool(self) -> ToolOutput:
        """一个共享工具"""
        return ToolOutput(
            blocks=[TextBlock(text="shared")],
            reward=0.0,
            finished=False,
        )

    @tool(shared=False)
    async def non_shared_tool(self, params: SimpleParams) -> ToolOutput:
        """一个非共享工具"""
        return ToolOutput(
            blocks=[TextBlock(text=f"non-shared: {params.message}")],
            reward=0.0,
            finished=False,
        )


def test_non_shared_tool_excluded_from_list_tools():
    """@tool(shared=False) 方法不应出现在 list_tools() 中"""
    tools_output = EnvWithNonSharedTool.list_tools()
    tool_names = [t.name for t in tools_output.tools]

    assert "shared_tool" in tool_names
    assert "non_shared_tool" not in tool_names


def test_default_tool_is_shared():
    """@tool（无参数）方法应出现在 list_tools() 中"""
    tools_output = EnvWithNonSharedTool.list_tools()
    tool_names = [t.name for t in tools_output.tools]

    assert "shared_tool" in tool_names


@pytest.mark.asyncio
async def test_non_shared_tool_still_callable():
    """非共享 @tool 方法仍可通过 _call_tool 调用"""
    env = EnvWithNonSharedTool(task_spec={"id": "1"})

    result = await env._call_tool("non_shared_tool", {"message": "hello"})

    assert result.root.ok is True
    assert result.root.output.blocks[0].text == "non-shared: hello"


def test_list_task_tools_default_empty():
    """list_task_tools() 默认应返回空列表"""
    env = EnvWithNonSharedTool(task_spec={"id": "1"})
    task_tools = env.list_task_tools()

    assert task_tools.tools == []


class EnvWithTaskTools(Environment):
    """重写了 list_task_tools 的环境"""

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        return [{"id": "1", "tools": ["read_file"]}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text="Test prompt")]

    def list_task_tools(self):
        from openreward.environments.types import ListToolsOutput, ToolSpec
        tools = []
        for tool_name in self.task_spec.get("tools", []):
            tools.append(ToolSpec(name=tool_name, description=f"Task tool: {tool_name}", input_schema=None))
        return ListToolsOutput(tools=tools)


def test_list_task_tools_override():
    """重写了 list_task_tools 的子类应返回任务特定工具"""
    env = EnvWithTaskTools(task_spec={"id": "1", "tools": ["read_file", "write_file"]})
    task_tools = env.list_task_tools()

    tool_names = [t.name for t in task_tools.tools]
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert len(task_tools.tools) == 2

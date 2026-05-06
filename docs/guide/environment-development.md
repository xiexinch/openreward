# 环境开发指南

本指南介绍如何从零开始编写自定义环境。

## 环境的基本结构

每个环境都是 `Environment` 的子类，必须实现三个抽象方法：

```python
from openreward.environments import Environment, tool, ToolOutput
from openreward.environments.types import TextBlock, Blocks

class MyEnv(Environment):
    @classmethod
    def list_splits(cls) -> list[str]:
        """返回环境支持的数据划分。"""
        return ["train", "test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[dict]:
        """返回某划分下的所有任务。"""
        return [{"id": i, "data": f"task-{i}"} for i in range(100)]

    def get_prompt(self) -> Blocks:
        """返回当前任务的提示内容。"""
        return [TextBlock(text=f"任务：{self.task_spec['data']}")]
```

## 定义工具

工具是智能体与环境交互的唯一方式。使用 `@tool` 装饰器定义：

```python
from pydantic import BaseModel, Field

class MoveParams(BaseModel):
    direction: str = Field(..., description="移动方向：up, down, left, right")
    steps: int = Field(1, description="移动步数")

class GridWorldEnv(Environment):
    def __init__(self, task_spec, secrets):
        super().__init__(task_spec, secrets)
        self.x = 0
        self.y = 0
        self.goal = task_spec.get("goal", [3, 3])

    @tool
    async def move(self, params: MoveParams) -> ToolOutput:
        dx, dy = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}[params.direction]
        self.x += dx * params.steps
        self.y += dy * params.steps

        reached = [self.x, self.y] == self.goal
        return ToolOutput(
            blocks=[TextBlock(text=f"位置：({self.x}, {self.y})")],
            reward=1.0 if reached else 0.0,
            finished=reached,
        )
```

### 无参数工具

```python
@tool
async def get_status(self) -> ToolOutput:
    return ToolOutput(blocks=[TextBlock(text=f"位置：({self.x}, {self.y})")])
```

### 非共享工具

```python
@tool(shared=False)
async def _internal_helper(self) -> ToolOutput:
    """仅在类内部使用，不对外暴露。"""
    ...
```

## 生命周期方法

### setup()

在首次工具调用前自动执行，用于初始化资源：

```python
class DatabaseEnv(Environment):
    async def setup(self):
        import asyncpg
        self.db = await asyncpg.connect(self.secrets["DB_URL"])

    async def teardown(self):
        await self.db.close()
```

`setup()` 可以返回 `None` 或 `Awaitable[None]`。如果实现了 `async` 版本，框架会自动 `await`。

### teardown()

在客户端断开连接时执行，用于清理资源：

```python
async def teardown(self):
    if self.sandbox:
        await self.sandbox.stop()
```

## 大数据集优化

如果任务数量很大（如数百万条），避免在 `list_tasks()` 中返回全部数据。改为覆盖 `num_tasks()` 和 `get_task()`：

```python
class LargeScaleEnv(Environment):
    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train", "test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[dict]:
        raise NotImplementedError("此环境不支持全量加载")

    @classmethod
    async def num_tasks(cls, split: str) -> int:
        return await fetch_count_from_database(split)

    @classmethod
    async def get_task(cls, split: str, index: int) -> dict:
        return await fetch_task_by_index(split, index)
```

## 任务特定工具

某些工具仅在特定任务中可用。覆盖 `list_task_tools()` 实现动态工具列表：

```python
class MultiTaskEnv(Environment):
    def list_task_tools(self) -> ListToolsOutput:
        tools = []
        if self.task_spec.get("requires_search"):
            tools.append(ToolSpec(name="web_search", description="搜索网页", input_schema=...))
        return ListToolsOutput(tools=tools)
```

## 组合工具集

通过 `toolsets` 类属性引入预构建工具集：

```python
from openreward.toolsets import CodeToolset

class CodingEnv(Environment):
    toolsets = [CodeToolset]

    def __init__(self, task_spec, secrets):
        super().__init__(task_spec, secrets)
        # 初始化 CodeToolset 需要的 sandbox
        self.sandbox = ...
```

## 测试环境

使用 pytest 编写单元测试：

```python
import pytest
from my_env import GridWorldEnv

@pytest.fixture
def env():
    return GridWorldEnv(task_spec={"goal": [1, 0]})

@pytest.mark.asyncio
async def test_move_right(env):
    result = await env._call_tool("move", {"direction": "right", "steps": 1})
    assert result.output.reward == 1.0
    assert result.output.finished is True
```

集成测试可以使用 `Server` 和 `AsyncOpenReward`：

```python
import asyncio
import uvicorn
from threading import Thread
from openreward.environments import Server
from openreward import AsyncOpenReward

@pytest.fixture(scope="module")
def server_url():
    app = Server(environments=[GridWorldEnv]).app
    config = uvicorn.Config(app, host="localhost", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    Thread(target=server.run, daemon=True).start()
    asyncio.run(wait_for_health("http://localhost:8080"))
    yield "http://localhost:8080"
    server.should_exit = True
```

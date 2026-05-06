# 环境 API

环境 API 分为两部分：

1. **服务端 API** —— `Environment`、`@tool`、`Server`，用于构建和暴露环境
2. **客户端 API** —— `EnvironmentsAPI`、`Session`，用于连接远程环境

## Environment 基类

```python
from openreward.environments import Environment, tool, ToolOutput
from openreward.environments.types import TextBlock, Blocks

class MyEnv(Environment):
    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train", "test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[dict]:
        return [{"question": "1+1=?"}]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=self.task_spec["question"])]

    @tool
    async def answer(self, params: AnswerParams) -> ToolOutput:
        correct = params.answer == "2"
        return ToolOutput(
            blocks=[TextBlock(text="正确！" if correct else "错误！")],
            reward=1.0 if correct else 0.0,
            finished=True,
        )
```

### 必需方法

| 方法 | 签名 | 说明 |
|---|---|---|
| `list_splits()` | `-> Sequence[str]` | 返回环境支持的所有划分名称 |
| `list_tasks(split)` | `-> Sequence[JSONObject]` | 返回某划分下的全部任务字典 |
| `get_prompt()` | `-> Blocks \| Awaitable[Blocks]` | 返回当前任务的提示消息 |

### 可选方法

| 方法 | 说明 |
|---|---|
| `setup()` | 首次工具调用前执行，用于初始化资源 |
| `teardown()` | 客户端断开时执行，用于清理资源 |
| `num_tasks(split)` | 返回某划分的任务总数（大数据集可覆盖以避免全量加载） |
| `get_task(split, index)` | 按索引获取单个任务（大数据集可覆盖） |
| `list_task_tools()` | 返回任务特定的动态工具列表 |

## @tool 装饰器

标记一个 `async` 方法为环境工具。

```python
@tool
async def my_tool(self, params: MyParams) -> ToolOutput:
    ...

@tool(shared=False)  # 非共享工具，不对外暴露
async def helper(self) -> ToolOutput:
    ...
```

**约束：**
- 方法必须是 `async`
- 参数只能是 `self` + 一个 Pydantic `BaseModel`，或仅 `self`
- 返回值必须是 `ToolOutput`

## ToolOutput

```python
class ToolOutput(BaseModel):
    blocks: Blocks              # 结果内容（TextBlock / ImageBlock 列表）
    reward: float | None = None # 奖励信号
    finished: bool = False      # 是否结束当前 episode
    metadata: dict | None = None # 任意附加元数据
```

## Server

将环境包装为 FastAPI 应用：

```python
from openreward.environments import Server

app = Server(environments=[MyEnv]).app
```

### HTTP 端点

| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| POST | `/create` | `CreateSession` | 会话 ID |
| POST | `/{env}/call` | `ToolCall` | SSE 流（`RunToolOutput`） |
| GET | `/{env}/prompt` | - | `Blocks` |
| GET | `/{env}/tools` | - | `ListToolsOutput` |
| POST | `/{env}/tasks` | `ListTasks` | 任务列表 |
| POST | `/{env}/task_range` | `GetTaskRange` | 任务范围列表 |

## 客户端 API

### 异步客户端

```python
from openreward import AsyncOpenReward

client = AsyncOpenReward(api_key="your-api-key")

# 创建会话
session = await client.environments.create(
    "username/environment-name",
    split="test",
    index=0,
)

# 获取提示
prompt = await session.prompt()

# 调用工具
result = await session.call("answer", {"answer": "2"})
```

### 同步客户端

```python
from openreward import OpenReward

client = OpenReward(api_key="your-api-key")
session = client.environments.create(...)
```

### Session 方法

| 方法 | 说明 |
|---|---|
| `prompt()` | 获取当前任务提示 |
| `call(tool_name, input)` | 调用工具，返回 `ToolOutput` |
| `tools()` | 列出可用工具 |
| `tasks(split)` | 列出某划分的任务 |
| `num_tasks(split)` | 获取任务总数 |
| `task(split, index)` | 获取单个任务 |
| `close()` | 关闭会话 |

## Toolset

通过 `toolsets` 类属性将可复用工具集组合到环境中：

```python
from openreward.environments import Toolset, tool, ToolOutput

class MyToolset(Toolset):
    @tool
    async def read_file(self, params: ReadParams) -> ToolOutput:
        output, code = await self.sandbox.run(f"cat {params.path}")
        return ToolOutput(blocks=[TextBlock(text=output)])

class MyEnv(Environment):
    toolsets = [MyToolset]
    ...
```

`Toolset` 基类会自动从环境中查找 `sandbox` 属性并绑定到 `self.sandbox`。

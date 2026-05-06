# Rollout API

Rollout API 用于记录智能体在环境中的完整交互轨迹，包括提示、工具调用、工具结果和奖励信号。记录的数据会上传至 OpenReward 平台，用于分析和训练。

## 核心概念

### Rollout

一个 **rollout** 是智能体从任务开始到结束（或达到最大步数）的完整交互序列。每个 rollout 包含：

- 一个 `run_name` —— 标识所属的实验或训练运行
- 一系列按时间顺序的**事件（Event）**
- 可选的 `run_info`（模型、训练阶段等元数据）
- 可选的 `rollout_info`（任务索引、超参数等）

### 事件类型

| 事件 | 说明 |
|---|---|
| `message` | 智能体或环境产生的消息（提示、工具调用、工具结果、推理过程等） |
| `rollout` | rollout 开始事件 |
| `rollout_update` | rollout 结束时的元数据更新 |

## 快速开始

```python
from openreward import AsyncOpenReward
from openreward.models import RunInfo, RolloutInfo

client = AsyncOpenReward(api_key="your-api-key")
client.run_info = RunInfo(
    model_name="my-model",
    run_type="eval",
)

rollout = client.rollout

# 开始记录
await rollout.start(
    run_name="experiment-1",
    environment="username/my-env",
    split="test",
    index=0,
)

# 记录事件
await rollout.log_user_message([{"type": "text", "text": "你好"}])
await rollout.log_assistant_message([...])
await rollout.log_tool_call("search", {"query": "Python"})
await rollout.log_tool_result("search", [{"type": "text", "text": "..."}], reward=0.5)

# 结束记录（自动上传）
await rollout.end(
    rollout_info=RolloutInfo(task_index=0, duration_ms=1500)
)
```

## 类与接口

### RolloutAPI

```python
class RolloutAPI:
    async def start(
        self,
        run_name: str,
        environment: str | None = None,
        variant: str | None = None,
        split: str | None = None,
        index: int | None = None,
        metadata: dict | None = None,
        task_spec: dict | None = None,
        rollout_name: str | None = None,
        step: int | None = None,
    ) -> str:
        """开始一个新的 rollout 记录，返回 rollout ID。"""

    async def end(
        self,
        rollout_info: RolloutInfo | None = None,
    ) -> None:
        """结束当前 rollout 并触发最终上传。"""

    async def log_user_message(self, content: list[dict]) -> None: ...
    async def log_assistant_message(self, content: list[dict]) -> None: ...
    async def log_tool_call(self, name: str, input: dict) -> None: ...
    async def log_tool_result(self, name: str, content: list[dict], reward: float | None = None) -> None: ...
    async def log_reasoning(self, content: str | None = None, summary: str | None = None) -> None: ...
```

### RunInfo

```python
@dataclass
class RunInfo:
    """运行级上下文 —— 在同一个训练运行的所有 rollout 中保持不变。"""
    model_name: str
    run_type: Literal["train", "eval", "adhoc"] | None = None
    model_params: int | None = None
    training_stage: Literal["pretrained", "sft", "rl"] | None = None
    initial_step: int | None = None
    checkpoint: str | None = None
    peft_type: str | None = None
    peft_rank: int | None = None
    rl_algorithm: str | None = None
    batch_size: int | None = None
    lr: float | None = None
    optimizer: str | None = None
    framework: str | None = None
    framework_version: str | None = None
```

### RolloutInfo

```python
@dataclass
class RolloutInfo:
    task_index: int
    env_version: str | None = None
    current_step: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    duration_ms: int | None = None
    harness: str | None = None
```

## 原始 SDK 消息记录

Rollout API 支持直接记录来自 Anthropic、OpenAI 和 Google GenAI SDK 的原始消息：

```python
# Anthropic
from anthropic.types import MessageParam
await rollout.log_anthropic_message(MessageParam(role="user", content="..."))

# OpenAI
await rollout.log_openai_message({"role": "user", "content": "..."})

# Google GenAI
from google.genai import types
await rollout.log_gdm_message(types.Content(...))
```

内部会自动将不同格式**规范化（normalize）**为统一的事件格式后上传。

## 后台上传机制

Rollout API 使用后台工作进程进行异步上传：

1. 用户调用 `log_*()` 时，事件被放入内存**环形缓冲区**
2. 当满足以下任一条件时触发批量上传：
   - 缓冲区达到 `max_items`（默认 128）
   - 数据量达到 `max_bytes`（默认 4 MB）
   - 超过 `max_age`（默认 1 秒）
3. 上传失败时自动按指数退避重试
4. 进程退出时自动 flush 剩余数据

### 配置

通过 `OpenReward.config.send_loop_config` 调整上传行为：

```python
from openreward.models import SendLoopConfig

client.config.send_loop_config = SendLoopConfig(
    max_items=256,
    max_bytes=8_000_000,
    max_age=2.0,
    max_retries=6,
    ...
)
```

## 日志格式

通过环境变量控制终端输出格式：

| 变量 | 值 | 说明 |
|---|---|---|
| `OPENREWARD_ROLLOUT_LOGGING_FORMAT` | `pretty` | 人类可读的彩色输出（默认） |
| `OPENREWARD_ROLLOUT_LOGGING_FORMAT` | `structured` | JSON 结构化输出 |

生产环境建议同时设置 `OPENREWARD_USE_STRUCTURED_LOGS=1` 启用全局 JSON 日志。

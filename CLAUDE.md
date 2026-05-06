# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 提供在本仓库中工作时的指导。

## 项目概述

这是 **OpenReward Python SDK** —— 一个用于构建语言模型 RL 环境以及针对这些环境训练智能体的库。它有两个互补的角色：

1. **构建环境** —— 定义评估任务，暴露工具，并通过符合标准的 HTTP API 提供服务。
2. **训练智能体** —— 连接任意环境（本地或托管），运行智能体循环，并将带奖励的 rollout 日志回传至 OpenReward。

## 开发命令

安装包含开发依赖的包：

```bash
pip install -e ".[dev,tools]"
```

运行测试：

```bash
pytest
```

运行单个测试文件或单个测试：

```bash
pytest tests/test_environment.py
pytest tests/test_environment.py::test_name
pytest -k keyword
```

代码检查、格式化和类型检查：

```bash
black openreward/ tests/
ruff check openreward/ tests/
mypy openreward/
pyright
```

安装后 `orwd` CLI 可用（入口点：`openreward.cli:main`）。

## 架构

### 核心抽象

- **`Environment`** (`openreward/environments/environment.py`) —— 所有环境的抽象基类。子类需实现：
  - `list_splits()` —— 返回划分名称。
  - `list_tasks(split)` —— 返回按确定性顺序排列的任务字典列表。
  - `get_prompt()` —— 将任务指令返回为 `TextBlock` / `ImageBlock` 列表。
  - 可选：`setup()`、`teardown()`、`num_tasks()`、`get_task()`。

- **`@tool` 装饰器** —— 将 `async` 方法标记为工具。方法接受零个参数或一个 Pydantic `BaseModel`，并必须返回 `ToolOutput`。使用 `@tool(shared=False)` 表示不应在 `list_tools()` 中列出的工具。

- **`ToolOutput`** (`openreward/environments/types.py`) —— 每个工具返回的内容：
  - `blocks` —— `TextBlock` 或 `ImageBlock` 列表。
  - `reward` —— 可选浮点数。
  - `finished` —— episode 是否结束。
  - `metadata` —— 可选字典。

- **`Toolset`** (`openreward/environments/toolset.py`) —— 可复用工具的集合，可通过 `toolsets` 类属性组合到环境中。工具集延迟实例化，并按环境实例缓存。基类期望 `env.sandbox` 存在（可通过 `sandbox_attr` 配置）。

- **`Server`** (`openreward/environments/server.py`) —— FastAPI 包装器，通过 HTTP 以 SSE 流方式暴露一个或多个 `Environment` 类。关键端点：`POST /create`、`POST /{env}/call`、`GET /{env}/prompt`、`GET /{env}/tools`、`POST /{env}/tasks`。

### API 模块

SDK 在 `openreward/api/` 下组织为三个 API 区域：

- **`api/environments/`** —— `EnvironmentsAPI` / `AsyncEnvironmentsAPI` 和 `Session` / `AsyncSession`，用于连接远程环境。处理工具调用序列化、SSE 流和重连逻辑。
- **`api/rollouts/`** —— `RolloutAPI` 和后台工作进程 (`background.py`)，用于异步记录智能体轨迹。支持规范化消息类型以及来自 Anthropic、OpenAI 和 Google GenAI SDK 的原始输出。
- **`api/sandboxes/`** —— `SandboxesAPI` / `AsyncSandboxesAPI`，用于通过 OpenReward 平台启动 Docker 容器。构建在 `api/_session/` 之上。

### 共享原语

- **`api/_session/`** —— 底层 HTTP/SSE 会话原语（`BaseAsyncSession`、重试逻辑、可恢复 SSE 解析、心跳）。这是环境和沙箱客户端使用的传输层。
- **`http_client.py`** —— rollout 和沙箱 API 使用的 `make_request` 辅助函数。
- **`models.py`** —— 用于 `RunInfo`、`RolloutInfo`、`Config`、`SendLoopConfig` 的 dataclass，以及 rollout 日志记录器使用的事件类型。
- **`log_utils.py`** —— 使用 `structlog` 的结构化日志设置。由 `OPENREWARD_USE_STRUCTURED_LOGS` 控制。

### 工具集

`openreward/toolsets/` 包含用于常见任务的预构建工具集：

- `ClaudeCodeToolset`、`CodexToolset`、`GeminiCliToolset`、`HermesToolset`、`OpenClawToolset` —— 代码交互工具集。
- `PDFToolset`、`WordToolset`、`ExcelToolset`、`PowerPointToolset` —— 文档处理工具集（需要 `openreward[tools]`）。

会话作用域的内置工具集在 `toolsets/__init__.py` 的 `BUILTIN_TOOLSETS` 中注册。

### 模板

`openreward/templates/` 提供 `orwd init` CLI 使用的脚手架：

- `basic/` —— 最小化环境模板。
- `sandbox/` —— 带 Docker 沙箱支持的环境模板。

### 测试约定

- 测试使用 `pytest` 和 `pytest-asyncio`。
- 许多测试在后台线程中启动真实的 `uvicorn.Server`（参见 `tests/test_environment.py` 中端口 8080 的模块级 `server` fixture）。
- `tests/test_session.py` 和 `tests/test_toolsets.py` 使用 `unittest.mock` 和伪造的 `aiohttp` 响应，而不是启动真实服务器。
- 工具转换和清理测试是独立的，不需要服务器。

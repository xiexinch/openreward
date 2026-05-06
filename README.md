# OpenReward Python SDK

[![PyPI version](https://img.shields.io/pypi/v/openreward)](https://pypi.org/project/openreward/)
[![Python 3.12+](https://img.shields.io/badge/python-%3E=3.12-green)](https://pypi.org/project/openreward/)
[![Docs](https://img.shields.io/badge/docs-openreward.ai-blue)](https://docs.openreward.ai)

[OpenReward](https://openreward.ai) 的官方 Python SDK —— 一个用于构建、托管和训练语言模型 RL 环境的平台。

该 SDK 有两个互补的角色：

- **构建环境** —— 定义评估任务，暴露工具，并通过符合标准的 API 提供服务，可部署在 OpenReward 平台上。
- **训练智能体** —— 连接任意环境（本地或托管），运行智能体循环，并将带奖励的 rollout 日志回传至 OpenReward。

## 安装

```bash
pip install openreward
```

对于处理文档的环境（PDF、DOCX、Excel、PowerPoint）：

```bash
pip install "openreward[tools]"
```

需要 Python 3.12+。

## 核心概念

### 环境

`Environment` 子类定义了一个基准测试或任务分布。实现三个必需的方法：

| 方法 | 用途 |
|---|---|
| `list_splits()` | 返回划分名称，例如 `["train", "test"]` |
| `list_tasks(split)` | 返回按确定性顺序排列的任务字典列表 |
| `get_prompt()` | 将任务指令返回为 `TextBlock` / `ImageBlock` 列表 |

动作被定义为带有 `@tool` 装饰器的 `async` 方法。每个工具接收一个 Pydantic 模型作为输入，并返回一个 `ToolOutput`。

### ToolOutput

每个工具返回一个包含以下内容的 `ToolOutput`：

- `blocks` —— `TextBlock` 或 `ImageBlock` 结果列表
- `reward` —— 可选的浮点奖励信号
- `finished` —— 该 episode 是否已完成
- `metadata` —— 可选的任意元数据

### 服务器

`Server` 将一个或多个 `Environment` 类包装在 FastAPI 应用中，并通过 HTTP 以 SSE 流方式暴露 [Open Reward Standard](https://docs.openreward.ai) API。

关键端点：

| 端点 | 描述 |
|---|---|
| `POST /create` | 创建新的环境会话 |
| `POST /{env}/call` | 执行工具（通过 SSE 流式传输） |
| `GET /{env}/prompt` | 获取当前任务提示 |
| `GET /{env}/tools` | 列出可用工具 |
| `POST /{env}/tasks` | 列出某个划分的所有任务 |

### 沙箱

需要隔离计算的环境（例如代码执行）可以通过沙箱 API 使用 `SandboxSettings` 启动 Docker 容器。容器自动管理 —— 在 `setup()` 中启动，在 `teardown()` 中销毁。

### 工具集

将可复用的工具分组到 `Toolset` 类中，并通过 `toolsets` 类属性在环境之间进行组合。

### Rollout 日志记录

将带有奖励信号的智能体轨迹日志回传至 OpenReward，用于分析和训练。客户端的 `rollout` API 支持规范化的消息类型，以及来自 Anthropic、OpenAI 和 Google GenAI SDK 的原始输出。

## CLI

`orwd` CLI 帮助你搭建和创建环境。

### 在本地搭建新环境

```bash
# 最小化环境
orwd init my-env

# 带有 Docker 沙箱的代码执行环境
orwd init my-env --template sandbox
```

### 在 OpenReward 上创建环境

在你的账户下注册一个新环境（需要 `OPENREWARD_API_KEY`）：

```bash
orwd create my-env --description "我的环境的简短描述"
```

默认情况下，环境在你的个人命名空间下创建。要在你是成员的机构下创建，请传递 `--namespace`：

```bash
orwd create my-env --description "简短描述" --namespace my-org
```

传递 `--private` 使环境变为私有：

```bash
orwd create my-env --description "简短描述" --private
```

## 部署到 OpenReward

1. 将你的环境推送到 GitHub 仓库。
2. 在 [OpenReward 仪表板](https://openreward.ai) 中连接该仓库。
3. 配置计算资源（CPU、内存、扩展）。
4. 每次推送到连接的分支都会触发自动构建和部署。

然后，任何智能体都可以通过 OpenReward API 使用 `username/environment-name` 命名空间访问你的环境。

## 环境变量

| 变量 | 描述 |
|---|---|
| `OPENREWARD_API_KEY` | 用于身份验证的 API 密钥 |
| `OPENREWARD_URL` | 覆盖基础 URL（默认：`https://openreward.ai`） |
| `OPENREWARD_USE_STRUCTURED_LOGS` | 设置为 `1` 以启用 JSON 日志记录（生产环境推荐） |
| `OPENREWARD_ROLLOUT_LOGGING_FORMAT` | rollout 日志输出的格式：`pretty` 或 `structured` |

## 文档

完整的文档、指南和示例请访问 **[docs.openreward.ai](https://docs.openreward.ai)**。

## 许可证

Apache 2.0

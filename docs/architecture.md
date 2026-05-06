# 架构概述

OpenReward Python SDK 是一个面向语言模型强化学习（RL）的双角色库：

1. **环境服务端** —— 定义评估任务和工具，通过 HTTP API 暴露
2. **训练客户端** —— 连接环境、运行智能体循环、记录 rollout 日志

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        OpenReward SDK                           │
├─────────────────────────────┬───────────────────────────────────┤
│      环境构建侧              │         训练客户端侧              │
│  (openreward/environments)   │     (openreward/api, client)     │
├─────────────────────────────┼───────────────────────────────────┤
│  Environment (ABC)          │  OpenReward / AsyncOpenReward     │
│  @tool                      │    ├─ environments (Session)     │
│  Toolset                    │    ├─ rollout (RolloutAPI)       │
│  Server (FastAPI)           │    └─ sandbox (SandboxesAPI)     │
│                             │                                   │
│  ┌──────────────┐          │  ┌──────────────┐                │
│  │   本地运行    │          │  │  远程连接     │                │
│  │  uvicorn     │          │  │  HTTP/SSE    │                │
│  └──────────────┘          │  └──────────────┘                │
└─────────────────────────────┴───────────────────────────────────┘
```

## 核心组件

### 1. Environment

`Environment` 是所有任务环境的抽象基类，位于 `openreward/environments/environment.py`。每个子类必须实现：

- `list_splits()` —— 返回数据划分名称
- `list_tasks(split)` —— 返回任务列表
- `get_prompt()` —— 返回当前任务的提示内容

环境通过 `@tool` 装饰器定义**工具（Tool）**。工具是 `async` 方法，接收 Pydantic 模型或零参数，返回 `ToolOutput`。

### 2. Server

`Server` 将 `Environment` 子类包装为 FastAPI 应用，暴露标准的 HTTP API。关键端点：

| 端点 | 功能 |
|---|---|
| `POST /create` | 创建环境会话 |
| `POST /{env}/call` | 调用工具（SSE 流式返回） |
| `GET /{env}/prompt` | 获取当前任务提示 |
| `GET /{env}/tools` | 列出可用工具 |
| `POST /{env}/tasks` | 列出某划分的所有任务 |

### 3. Toolset

`Toolset` 是可复用工具的集合，位于 `openreward/environments/toolset.py`。通过 `Environment.toolsets` 类属性组合到环境中，实现跨环境的工具复用。

### 4. 客户端 API

SDK 提供三个主要 API 区域：

#### Environments API

`EnvironmentsAPI` / `AsyncEnvironmentsAPI` 用于连接远程环境会话，处理工具调用序列化、SSE 流解析和断线重连。

#### Rollouts API

`RolloutAPI` 异步记录智能体轨迹。核心流程：

1. 调用 `rollout.start()` 开始记录
2. 使用 `rollout.log_*()` 记录消息事件
3. 后台工作进程批量压缩并上传至 OpenReward 平台

支持的消息格式：规范化格式、Anthropic SDK、OpenAI SDK、Google GenAI SDK。

#### Sandboxes API

`SandboxesAPI` / `AsyncSandboxesAPI` 管理 Docker 容器生命周期：

1. `start()` 创建沙箱（发送 `SandboxSettings`）
2. `run(cmd)` 在容器中执行命令
3. 上下文管理器自动销毁

## 模块关系

```
openreward/
├── environments/          # 环境核心
│   ├── environment.py     # Environment ABC, @tool
│   ├── server.py          # FastAPI Server
│   ├── toolset.py         # Toolset 基类
│   ├── types.py           # ToolOutput, TextBlock, ImageBlock...
│   └── session.py         # 会话工具调用辅助
│
├── api/                   # 客户端 API
│   ├── environments/      # 环境连接客户端
│   ├── rollouts/          # Rollout 日志记录
│   │   ├── rollout.py     # RolloutAPI
│   │   ├── background.py  # 后台上传工作进程
│   │   └── serializers/   # 多 SDK 序列化器
│   ├── sandboxes/         # 沙箱客户端
│   └── _session/          # 共享 HTTP/SSE 传输层
│
├── toolsets/              # 预构建工具集
├── templates/             # CLI 脚手架模板
└── client.py              # OpenReward / AsyncOpenReward 统一入口
```

## 数据流

### 环境服务端数据流

```
Client              Server              Environment
  |                   |                      |
  |-- POST /create -->|                      |
  |                   |-- instantiate ------>| (task_spec + secrets)
  |                   |                      |
  |-- POST /{env}/call -------------------->|
  |                   |                      |-- setup() [首次调用]
  |                   |                      |-- @tool 方法
  |                   |<-- ToolOutput -------|
  |<-- SSE 流 --------|                      |
```

### Rollout 日志数据流

```
用户代码
   |
   v
RolloutAPI.log_*()          # 放入内存队列
   |
   v
BackgroundWorker            # 后台工作进程
   |-- 批量聚合
   |-- 压缩
   |-- 重试上传
   v
OpenReward 平台
```

## 共享传输层

`api/_session/` 提供所有远程 API 共享的低层原语：

- `BaseAsyncSession` —— 带生命周期管理的异步会话
- `request_retryable()` —— 带指数退避的 HTTP 请求
- `resumable_sse()` —— 可断线恢复的 SSE 流解析
- 心跳保活机制

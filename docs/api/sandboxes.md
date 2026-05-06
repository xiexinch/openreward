# 沙箱 API

沙箱 API 提供**隔离计算环境**，通过 Docker 容器执行任意命令。适用于需要运行代码、访问文件系统或执行不安全操作的环境。

## 核心概念

- **SandboxSettings** —— 定义容器的镜像、资源限制、存储桶映射等配置
- **SandboxesAPI** / **AsyncSandboxesAPI** —— 管理容器生命周期
- **RunResult** —— 命令执行结果（输出、返回码）

## 快速开始

```python
from openreward import AsyncOpenReward
from openreward.api.sandboxes.types import SandboxSettings

client = AsyncOpenReward(api_key="your-api-key")

# 定义沙箱配置
settings = SandboxSettings(
    image="python:3.12-slim",
    command=["sleep", "infinity"],
)

# 创建并启动沙箱
sandbox = client.sandbox(settings)
async with sandbox:
    # 执行命令
    result = await sandbox.run("python -c 'print(1+1)'")
    print(result.output)        # "2"
    print(result.return_code)   # 0

# 退出上下文管理器后容器自动销毁
```

## SandboxSettings

```python
class SandboxSettings(BaseModel):
    image: str                          # Docker 镜像名称
    command: list[str] | None = None    # 容器启动命令
    env: dict[str, str] | None = None   # 环境变量
    cpu: str | None = None              # CPU 限制（如 "1"）
    memory: str | None = None           # 内存限制（如 "1Gi"）
    buckets: list[SandboxBucketConfig] | None = None  # 对象存储挂载
    sidecars: list[SandboxSidecarContainer] | None = None  # Sidecar 容器
    host_aliases: list[SandboxHostAlias] | None = None     # 主机别名
```

### 存储桶挂载

将对象存储桶挂载到容器内：

```python
from openreward.api.sandboxes.types import SandboxBucketConfig

settings = SandboxSettings(
    image="python:3.12-slim",
    buckets=[
        SandboxBucketConfig(
            name="my-dataset",
            mount_path="/data",
            read_only=True,
        ),
    ],
)
```

### Sidecar 容器

在同一 Pod 中运行辅助容器：

```python
from openreward.api.sandboxes.types import SandboxSidecarContainer

settings = SandboxSettings(
    image="my-app:latest",
    sidecars=[
        SandboxSidecarContainer(
            name="redis",
            image="redis:7",
        ),
    ],
)
```

## 生命周期管理

### 异步上下文管理器（推荐）

```python
async with client.sandbox(settings) as sandbox:
    result = await sandbox.run("ls -la")
```

### 手动管理

```python
sandbox = client.sandbox(settings)
await sandbox.start()
try:
    result = await sandbox.run("ls -la")
finally:
    await sandbox.stop()
```

## 执行命令

```python
result = await sandbox.run(
    cmd="python script.py",
    timeout=300.0,        # 超时时间（秒）
    max_bytes=50_000,     # 最大输出字节数
    sanitise=True,        # 是否清理 ANSI 转义序列和控制字符
)
```

### RunResult

```python
class RunResult(BaseModel):
    output: str       # 命令标准输出
    return_code: int  # 进程返回码
```

支持元组解包：`output, code = await sandbox.run("...")`

## Secrets

敏感信息通过 `secrets` 参数传入，仅在创建沙箱时发送一次：

```python
sandbox = client.sandbox(
    settings,
    secrets={
        "API_KEY": "sk-...",
        "DB_URL": ("postgres://...", ["db-sidecar"]),  # 仅对指定 sidecar 可见
    },
)
```

在容器内通过环境变量访问：`os.environ["API_KEY"]`

## 在环境中使用沙箱

典型模式是在 `Environment` 的 `setup()` 中创建沙箱，在 `teardown()` 中销毁：

```python
class CodeExecutionEnv(Environment):
    def __init__(self, task_spec, secrets):
        super().__init__(task_spec, secrets)
        self.sandbox = None

    async def setup(self):
        client = AsyncOpenReward(api_key=self.secrets.get("OPENREWARD_API_KEY"))
        self.sandbox = client.sandbox(SandboxSettings(image="python:3.12-slim"))
        await self.sandbox.start()

    async def teardown(self):
        if self.sandbox:
            await self.sandbox.stop()
```

配合 `Toolset` 使用时，工具集基类会自动将 `env.sandbox` 绑定到 `self.sandbox`。

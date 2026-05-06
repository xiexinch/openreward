# 工具集开发指南

工具集（Toolset）是可复用工具的集合，可以跨多个环境共享。本指南介绍如何编写自定义工具集。

## 什么是 Toolset

Toolset 遵循与 `Environment` 相同的 `@tool` 装饰器模式，但不直接实现 `list_splits()` 等方法。它专注于提供**一组相关工具**，如代码执行、文档处理、网页搜索等。

## 创建 Toolset

### 基础工具集

最简单的工具集不需要继承任何基类：

```python
from openreward.environments import tool, ToolOutput
from openreward.environments.types import TextBlock
from pydantic import BaseModel

class SimpleToolset:
    @tool
    async def hello(self, params: HelloParams) -> ToolOutput:
        return ToolOutput(blocks=[TextBlock(text=f"你好，{params.name}！")])
```

然后在环境中引用：

```python
class MyEnv(Environment):
    toolsets = [SimpleToolset]
```

### 需要沙箱的工具集

如果工具集需要 Docker 沙箱，继承 `Toolset` 基类：

```python
from openreward.environments import Toolset, tool, ToolOutput
from openreward.environments.types import TextBlock
from openreward.api.sandboxes.types import RunResult

class CodeExecutionToolset(Toolset):
    @tool
    async def run_python(self, params: RunPythonParams) -> ToolOutput:
        result: RunResult = await self.sandbox.run(
            f"python -c '{params.code}'",
            timeout=30.0,
        )
        return ToolOutput(
            blocks=[TextBlock(text=result.output)],
            reward=0.0,
            finished=False,
        )
```

## Toolset 基类

`Toolset` 基类提供了 `self.sandbox` 快捷访问。在初始化时，它会从环境实例中查找 `sandbox` 属性：

```python
class Toolset(ABC):
    def __init__(self, env, sandbox_attr: str = "sandbox"):
        self.env = env
        self.sandbox = getattr(env, sandbox_attr)
```

如果环境没有 `sandbox` 属性或其为 `None`，初始化会抛出 `ValueError`。

### 自定义沙箱属性名

如果环境中使用不同的属性名存储沙箱：

```python
class MyEnv(Environment):
    toolsets = [CodeExecutionToolset]

    def __init__(self, task_spec, secrets):
        super().__init__(task_spec, secrets)
        self.compute = client.sandbox(...)  # 属性名不是 "sandbox"
```

在工具集中指定：

```python
class CodeExecutionToolset(Toolset):
    def __init__(self, env):
        super().__init__(env, sandbox_attr="compute")
```

## 内置工具集

SDK 提供以下预构建工具集，位于 `openreward/toolsets/`：

| 工具集 | 功能 | 是否需要沙箱 |
|---|---|---|
| `ClaudeCodeToolset` | 代码读写、搜索、执行 | 是 |
| `CodexToolset` | OpenAI Codex 风格代码工具 | 是 |
| `GeminiCliToolset` | Google Gemini CLI 工具 | 是 |
| `HermesToolset` | 通用代码工具集 | 是 |
| `OpenClawToolset` | 文件系统操作 | 是 |
| `PDFToolset` | PDF 读取和处理 | 否（需 `openreward[tools]`） |
| `WordToolset` | Word 文档处理 | 否（需 `openreward[tools]`） |
| `ExcelToolset` | Excel 表格处理 | 否（需 `openreward[tools]`） |
| `PowerPointToolset` | PPT 处理 | 否（需 `openreward[tools]`） |

使用方式：

```python
from openreward.toolsets import HermesToolset, PDFToolset

class DocumentQAEnv(Environment):
    toolsets = [HermesToolset, PDFToolset]
```

## 工具集组合规则

1. **名称冲突检测**：如果工具集与环境的工具同名，初始化时会抛出 `ValueError`
2. **延迟实例化**：工具集在首次工具调用时才被实例化
3. **实例缓存**：同一环境中的工具集实例被缓存，避免重复创建

## 最佳实践

### 1. 工具集应专注于单一职责

```python
# 好的设计
class FileToolset(Toolset): ...
class NetworkToolset(Toolset): ...

# 避免大而全的工具集
class MegaToolset(Toolset): ...  # 包含文件、网络、数据库... 不推荐
```

### 2. 提供清晰的参数文档

```python
class ReadFileParams(BaseModel):
    path: str = Field(..., description="要读取的文件绝对路径")
    offset: int = Field(0, description="起始行号，从 0 开始")
    limit: int = Field(100, description="最多读取的行数")
```

### 3. 处理沙箱不可用的情况

```python
class OptionalSandboxToolset:
    @tool
    async def safe_run(self, params: RunParams) -> ToolOutput:
        if hasattr(self, "sandbox") and self.sandbox:
            result = await self.sandbox.run(params.command)
        else:
            # 降级到本地执行或返回错误
            result = await self._local_run(params.command)
        return ToolOutput(blocks=[TextBlock(text=result.output)])
```

### 4. 注册为内置工具集

如果要让工具集可以通过名称在会话 API 中使用，在 `openreward/toolsets/__init__.py` 的 `BUILTIN_TOOLSETS` 中注册：

```python
BUILTIN_TOOLSETS: dict[str, type[Toolset]] = {
    MyCustomToolset.name(): MyCustomToolset,
    # ...
}
```

注册后，客户端可以通过字符串名称请求该工具集：

```python
session = await client.environments.create(
    "username/env",
    split="test",
    index=0,
    toolset_name="MyCustomToolset",
)
```

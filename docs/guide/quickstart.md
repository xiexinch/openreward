# 快速开始

## 安装

```bash
pip install openreward
```

如需文档处理工具（PDF、Word、Excel、PowerPoint）：

```bash
pip install "openreward[tools]"
```

需要 Python 3.12+。

## 创建第一个环境

### 1. 定义环境类

创建一个文件 `my_env.py`：

```python
from pydantic import BaseModel
from openreward.environments import Environment, tool, ToolOutput
from openreward.environments.types import TextBlock, Blocks

class AnswerParams(BaseModel):
    answer: str

class MathEnv(Environment):
    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train", "test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[dict]:
        return [
            {"question": "1 + 1 = ?", "answer": "2"},
            {"question": "3 * 4 = ?", "answer": "12"},
        ]

    def get_prompt(self) -> Blocks:
        return [TextBlock(text=f"计算：{self.task_spec['question']}")]

    @tool
    async def submit(self, params: AnswerParams) -> ToolOutput:
        correct = params.answer.strip() == self.task_spec["answer"]
        return ToolOutput(
            blocks=[TextBlock(text="正确！" if correct else "错误！")],
            reward=1.0 if correct else 0.0,
            finished=True,
        )
```

### 2. 启动服务器

```python
from openreward.environments import Server
from my_env import MathEnv

app = Server(environments=[MathEnv]).app
```

使用 uvicorn 运行：

```bash
uvicorn my_env:app --host 0.0.0.0 --port 8080
```

或使用 `Server.run()`：

```python
Server(environments=[MathEnv]).run(host="0.0.0.0", port=8080)
```

### 3. 测试环境

```python
import asyncio
from openreward import AsyncOpenReward

async def main():
    client = AsyncOpenReward(api_key="test")

    # 连接本地环境
    session = await client.environments.create(
        "math_env",  # Environment.name() 的小写形式
        split="test",
        index=0,
    )

    # 获取提示
    prompt = await session.prompt()
    print(prompt)  # [TextBlock(text="计算：1 + 1 = ?")]

    # 调用工具
    result = await session.call("submit", {"answer": "2"})
    print(result.output.reward)    # 1.0
    print(result.output.finished)  # True

asyncio.run(main())
```

## 使用 CLI 脚手架

SDK 提供 `orwd` CLI 快速生成环境模板：

```bash
# 创建最小化环境
orwd init my-project

# 创建带 Docker 沙箱的环境
orwd init my-project --template sandbox
```

生成的目录结构：

```
my-project/
├── Dockerfile
├── requirements.txt
└── server.py
```

## 下一步

- [环境开发指南](environment-development.md) —— 深入学习环境开发
- [工具集开发指南](toolset-development.md) —— 编写可复用工具集
- [部署指南](deployment.md) —— 将环境部署到 OpenReward 平台

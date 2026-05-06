"""Codex 会话工具集。

Codex 偏好单个 shell 工具而非离散的文件工具，因此此工具集
仅公开 ``bash``。bash 描述逐字复制自
firehorse codex 描述（``firehorse/firehorse/mcp/codex_descriptions.py``），
最初从上游 Codex（``codex-rs/tools/src/local_tool.rs``）中提取。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from openreward.environments.environment import tool
from openreward.environments.toolset import Toolset
from openreward.environments.types import TextBlock, ToolOutput


class BashParams(BaseModel, extra="forbid"):
    command: str
    description: str = ""
    timeout: Optional[float] = 30.0


BASH_DESCRIPTION = (
    "运行 shell 命令并返回其输出。"
    "始终设置 workdir 参数；除非绝对必要，否则避免使用 cd。"
)


class CodexToolset(Toolset):
    """会话工具集，公开 Codex 的单工具界面（仅 ``bash``）。

    通过将其传递给 ``env.session(...)`` 来绑定到会话：

        from openreward.toolsets import CodexToolset

        with env.session(task=task, toolset="codex") as session:
            session.call_tool("bash", {"command": "ls"})

    要求绑定的环境定义 ``self.sandbox``。
    """

    @classmethod
    def name(cls) -> str:
        return "codex"

    @tool
    async def bash(self, params: BashParams) -> ToolOutput:
        try:
            output, code = await self.sandbox.run(params.command.strip())
            return ToolOutput(
                blocks=[TextBlock(text=f"{output}\n\n(exit {code})")],
                metadata={"output": output, "exit_code": code},
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error executing command: {str(e)}")],
                finished=False,
            )


CodexToolset.bash.__doc__ = BASH_DESCRIPTION

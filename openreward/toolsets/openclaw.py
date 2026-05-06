"""OpenClaw 会话工具集。

提供 OpenClaw 公开的六个内置编码工具
（``exec``、``process``、``read``、``write``、``edit``、``apply_patch``）。
工具名称、参数模式和描述与 OpenClaw 的
上游定义匹配。
"""
from __future__ import annotations

import base64
import os
from typing import Any, List, Optional

from pydantic import BaseModel

from openreward.environments.environment import tool
from openreward.environments.toolset import Toolset
from openreward.environments.types import TextBlock, ToolOutput


async def _download_text(sandbox: Any, path: str) -> str:
    data = await sandbox.download(path)
    return data.decode("utf-8")


async def _upload_text(
    sandbox: Any,
    path: str,
    content: str,
    ensure_trailing_newline: bool = True,
) -> None:
    if ensure_trailing_newline and not content.endswith("\n"):
        content = content + "\n"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    await sandbox.check_run(f"echo '{encoded}' | base64 -d > {path}")


# ── Pydantic 参数模型 ──

class ExecParams(BaseModel, extra="forbid"):
    command: str
    timeout: Optional[float] = 1800.0


class ReadParams(BaseModel, extra="forbid"):
    path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class WriteParams(BaseModel, extra="forbid"):
    path: str
    content: str


class EditItem(BaseModel, extra="forbid"):
    oldText: str
    newText: str


class EditParams(BaseModel, extra="forbid"):
    path: str
    edits: List[EditItem]


class ApplyPatchParams(BaseModel, extra="forbid"):
    input: str


class ProcessParams(BaseModel, extra="forbid"):
    action: str
    sessionId: Optional[str] = None
    data: Optional[str] = None
    eof: Optional[bool] = None
    offset: Optional[int] = None
    limit: Optional[int] = None


# ── 工具描述（与 OpenClaw 上游匹配） ──

EXEC_DESCRIPTION = """\
执行 shell 命令并返回其输出和退出码。\
设置超时时间（秒，默认 1800）。\
用于构建、安装、git、进程、脚本、网络、包管理器以及\
任何需要 shell 的操作。"""

READ_DESCRIPTION = """\
读取给定路径处文件的内容。\
支持可选的 offset（从 1 开始的起始行号）和 limit\
（最多读取行数）以分页大型文件。"""

WRITE_DESCRIPTION = """\
将内容写入文件，如果不存在则创建，如果存在则覆盖。\
自动创建父目录。\
对现有文件的针对性修改请使用 edit 工具。"""

EDIT_DESCRIPTION = """\
对一个文件应用一个或多个有针对性的文本替换。\
每个编辑指定要查找的 oldText 和要替换的 newText。\
对于每个编辑，oldText 在文件中必须是唯一的。"""

PROCESS_DESCRIPTION = """\
管理后台进程。使用 exec 并设置 background=true 启动进程，
然后使用此工具与其交互。

操作：
- list：显示正在运行和已完成的后台会话
- poll：检查会话的新输出（需要 sessionId）
- log：使用可选的 offset/limit 分页读取会话输出（需要 sessionId）
- write：向会话的 stdin 发送数据（需要 sessionId 和 data）
- kill：终止后台会话（需要 sessionId）
- remove：如果正在运行则终止，如果已完成则清除（需要 sessionId）"""

APPLY_PATCH_DESCRIPTION = """\
使用结构化补丁格式应用文件修改，专为多个文件\
或多块编辑设计，在这些情况下单独的 edit 调用会很脆弱。

输入必须包含 '*** Begin Patch' 和 '*** End Patch' 标记。支持\
的操作：'*** Add File:'、'*** Update File:'（可选 '*** Move to:'）、\
'*** Delete File:' 和 '*** End of File' 用于仅 EOF 插入。"""


# ── 工具集 ──

class OpenClawToolset(Toolset):
    """会话工具集，公开 OpenClaw 的六工具编码界面。

    通过将其传递给 ``env.session(...)`` 来绑定到会话：

        from openreward.toolsets import OpenClawToolset

        with env.session(task=task, toolset="openclaw") as session:
            session.call_tool("exec", {"command": "ls"})

    要求绑定的环境定义 ``self.sandbox``。
    """

    @classmethod
    def name(cls) -> str:
        return "openclaw"

    @tool
    async def exec(self, params: ExecParams) -> ToolOutput:
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

    @tool
    async def process(self, params: ProcessParams) -> ToolOutput:
        try:
            action = params.action
            sid = params.sessionId or ""

            if action == "list":
                output, code = await self.sandbox.run("ps aux --no-headers 2>/dev/null || ps aux")
                return ToolOutput(
                    blocks=[TextBlock(text=output if output.strip() else "No background processes.")],
                    metadata={"output": output, "exit_code": code},
                    reward=0.0,
                    finished=False,
                )

            if not sid:
                return ToolOutput(
                    metadata={"error": "sessionId is required for this action"},
                    blocks=[TextBlock(text=f"Error: sessionId is required for action '{action}'.")],
                    finished=False,
                )

            if action in ("poll", "log"):
                tail_n = params.limit or 200
                cmd = f"cat /tmp/_oc_proc_{sid}.log 2>/dev/null || echo 'No output available for session {sid}'"
                if params.offset is not None:
                    cmd = f"tail -n +{params.offset} /tmp/_oc_proc_{sid}.log 2>/dev/null | head -n {tail_n}"
                elif params.limit:
                    cmd = f"tail -n {tail_n} /tmp/_oc_proc_{sid}.log 2>/dev/null"
                output, code = await self.sandbox.run(cmd)
                return ToolOutput(
                    blocks=[TextBlock(text=output)],
                    metadata={"output": output, "exit_code": code},
                    reward=0.0,
                    finished=False,
                )

            if action == "write":
                data = params.data or ""
                output, code = await self.sandbox.run(
                    f"echo '{data}' >> /tmp/_oc_proc_{sid}.stdin 2>/dev/null"
                )
                return ToolOutput(
                    blocks=[TextBlock(text=f"Sent data to session {sid}")],
                    metadata={"output": output, "exit_code": code},
                    reward=0.0,
                    finished=False,
                )

            if action in ("kill", "remove"):
                output, code = await self.sandbox.run(
                    f"kill $(cat /tmp/_oc_proc_{sid}.pid 2>/dev/null) 2>/dev/null; "
                    f"rm -f /tmp/_oc_proc_{sid}.pid /tmp/_oc_proc_{sid}.log /tmp/_oc_proc_{sid}.stdin"
                )
                return ToolOutput(
                    blocks=[TextBlock(text=f"Session {sid} terminated and cleaned up.")],
                    metadata={"output": output, "exit_code": code},
                    reward=0.0,
                    finished=False,
                )

            return ToolOutput(
                metadata={"error": f"Unknown action: {action}"},
                blocks=[TextBlock(text=f"Error: unknown process action '{action}'. "
                        "Use list, poll, log, write, kill, or remove.")],
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error managing process: {str(e)}")],
                finished=False,
            )

    @tool
    async def read(self, params: ReadParams) -> ToolOutput:
        try:
            content = await _download_text(self.sandbox, params.path)
            lines = content.splitlines()

            if params.offset is not None or params.limit is not None:
                start = (params.offset or 1) - 1
                if params.limit is not None:
                    lines = lines[start:start + params.limit]
                else:
                    lines = lines[start:]

            output = "\n".join(lines)
            return ToolOutput(
                metadata={"output": output, "exit_code": 0},
                blocks=[TextBlock(text=output)],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error reading file: {str(e)}")],
                finished=False,
            )

    @tool
    async def write(self, params: WriteParams) -> ToolOutput:
        try:
            dir_name = os.path.dirname(params.path)
            if dir_name:
                await self.sandbox.run(f"mkdir -p {dir_name}")
            await _upload_text(
                self.sandbox,
                params.path,
                params.content,
                ensure_trailing_newline=True,
            )
            return ToolOutput(
                metadata={"output": "", "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully wrote to {params.path}")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error writing file: {str(e)}")],
                finished=False,
            )

    @tool
    async def edit(self, params: EditParams) -> ToolOutput:
        try:
            content = await _download_text(self.sandbox, params.path)

            for item in params.edits:
                count = content.count(item.oldText)
                if count == 0:
                    return ToolOutput(
                        metadata={"error": f"oldText not found in {params.path}"},
                        blocks=[TextBlock(text=f"Error: oldText not found in {params.path}: {item.oldText[:100]}")],
                        finished=False,
                    )
                if count > 1:
                    return ToolOutput(
                        metadata={"error": f"oldText appears {count} times; must be unique"},
                        blocks=[TextBlock(text=f"Error: oldText appears {count} times in {params.path}. Must be unique.")],
                        finished=False,
                    )
                content = content.replace(item.oldText, item.newText, 1)

            await _upload_text(self.sandbox, params.path, content, ensure_trailing_newline=True)

            return ToolOutput(
                metadata={"output": "", "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully edited {params.path}")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error editing file: {str(e)}")],
                finished=False,
            )

    @tool
    async def apply_patch(self, params: ApplyPatchParams) -> ToolOutput:
        try:
            patch_tmp = "/tmp/_openclaw_patch.diff"
            await _upload_text(self.sandbox, patch_tmp, params.input, ensure_trailing_newline=True)

            output, code = await self.sandbox.run(f"patch -p1 < {patch_tmp}")
            if code != 0:
                await self.sandbox.run(f"rm -f {patch_tmp}")
                return ToolOutput(
                    metadata={"error": output, "exit_code": code},
                    blocks=[TextBlock(text=f"apply_patch failed (exit {code}):\n{output}")],
                    finished=False,
                )

            await self.sandbox.run(f"rm -f {patch_tmp}")
            return ToolOutput(
                metadata={"output": output, "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully applied patch:\n{output}")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error applying patch: {str(e)}")],
                finished=False,
            )


OpenClawToolset.exec.__doc__ = EXEC_DESCRIPTION
OpenClawToolset.process.__doc__ = PROCESS_DESCRIPTION
OpenClawToolset.read.__doc__ = READ_DESCRIPTION
OpenClawToolset.write.__doc__ = WRITE_DESCRIPTION
OpenClawToolset.edit.__doc__ = EDIT_DESCRIPTION
OpenClawToolset.apply_patch.__doc__ = APPLY_PATCH_DESCRIPTION

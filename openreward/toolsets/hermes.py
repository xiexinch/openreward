"""Hermes Agent 会话工具集。

提供 Hermes Agent 公开的五个内置编码工具
（``terminal``、``read_file``、``write_file``、``search_files``、``patch``）。
工具名称、参数模式和描述与 Hermes Agent 的
上游注册表定义（``nousresearch/hermes-agent``）匹配。
"""
from __future__ import annotations

import base64
import os
from typing import Any, Optional

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

class TerminalParams(BaseModel, extra="forbid"):
    command: str
    timeout: Optional[int] = 180


class ReadFileParams(BaseModel, extra="forbid"):
    path: str
    offset: int = 1
    limit: int = 500


class WriteFileParams(BaseModel, extra="forbid"):
    path: str
    content: str


class SearchFilesParams(BaseModel, extra="forbid"):
    pattern: str
    target: str = "content"
    path: str = "."
    file_glob: Optional[str] = None
    limit: int = 50
    offset: int = 0
    output_mode: str = "content"
    context: int = 0


class PatchParams(BaseModel, extra="forbid"):
    mode: str = "replace"
    path: Optional[str] = None
    old_string: Optional[str] = None
    new_string: Optional[str] = None
    replace_all: bool = False
    patch: Optional[str] = None


# ── 工具描述（与 Hermes 上游注册表匹配） ──

TERMINAL_DESCRIPTION = """\
在 Linux 环境中执行 shell 命令。文件系统通常在调用之间保持持久。

不要使用 cat/head/tail 读取文件——请改用 read_file。
不要使用 grep/rg/find 搜索——请改用 search_files。
不要使用 ls 列出目录——请改用 search_files(target='files')。
不要使用 sed/awk 编辑文件——请改用 patch。
不要使用 echo/cat heredoc 创建文件——请改用 write_file。
将 terminal 保留用于：构建、安装、git、进程、脚本、网络、包管理器以及任何需要 shell 的操作。

前台（默认）：命令完成后返回。为长时间构建/脚本设置超时。默认超时：180 秒，最大：600 秒。"""

READ_FILE_DESCRIPTION = """\
使用行号和分页读取文本文件。请改用此工具而不是 terminal 中的 cat/head/tail。\
输出格式：'LINE_NUM|CONTENT'。如果未找到，则建议类似的文件名。\
对大型文件使用 offset 和 limit。默认 offset：1，默认 limit：500，最大 limit：2000。"""

WRITE_FILE_DESCRIPTION = """\
将内容写入文件，完全替换现有内容。请改用此工具而不是 terminal 中的 echo/cat heredoc。\
自动创建父目录。覆盖整个文件——对于针对性编辑，请使用 'patch'。"""

SEARCH_FILES_DESCRIPTION = """\
使用正则表达式/glob 模式搜索文件内容或按名称查找文件。请改用此工具而不是
terminal 中的 grep/rg/find/ls。

target='content'（默认）：使用正则表达式搜索文件内容。返回匹配行及其
行号和可选上下文。
target='files'：按名称/glob 模式搜索文件。返回匹配文件路径。

使用 file_glob 过滤要搜索的文件（例如，'*.py'）。使用 output_mode 控制\
输出格式：'content'（匹配行）、'files_only'（文件路径）、'count'（匹配计数）。"""

PATCH_DESCRIPTION = """\
在文件中进行有针对性的查找和替换编辑。请改用此工具而不是 terminal 中的 sed/awk。

替换模式（默认）：查找唯一字符串并替换。
补丁模式：应用 V4A 多文件补丁以进行批量更改。

在替换模式下，除非 replace_all 为 true，否则 old_string 在文件中必须是唯一的。\
包含足够的周围上下文以确保唯一性。"""


# ── 工具集 ──

class HermesToolset(Toolset):
    """会话工具集，公开 Hermes Agent 的五工具编码界面。

    通过将其传递给 ``env.session(...)`` 来绑定到会话：

        from openreward.toolsets import HermesToolset

        with env.session(task=task, toolset="hermes") as session:
            session.call_tool("terminal", {"command": "ls"})

    要求绑定的环境定义 ``self.sandbox``。
    """

    @classmethod
    def name(cls) -> str:
        return "hermes"

    @tool
    async def terminal(self, params: TerminalParams) -> ToolOutput:
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
    async def read_file(self, params: ReadFileParams) -> ToolOutput:
        try:
            content = await _download_text(self.sandbox, params.path)
            lines = content.splitlines()

            start = max(0, params.offset - 1)
            end = start + params.limit
            selected_lines = lines[start:end]

            output_lines = [f"{i}|{line}" for i, line in enumerate(selected_lines, start=start + 1)]
            output = "\n".join(output_lines)

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
    async def write_file(self, params: WriteFileParams) -> ToolOutput:
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
    async def search_files(self, params: SearchFilesParams) -> ToolOutput:
        try:
            if params.target == "files":
                cmd = f"find {params.path} -type f -name '{params.pattern}'"
                output, code = await self.sandbox.run(cmd)
                if code != 0:
                    return ToolOutput(
                        metadata={"error": output, "exit_code": code},
                        blocks=[TextBlock(text=f"search_files failed (exit {code}):\n{output}")],
                        finished=False,
                    )
                lines = [l for l in output.splitlines() if l.strip()]
                lines = lines[params.offset:params.offset + params.limit]
                result = "\n".join(lines)
                return ToolOutput(
                    metadata={"output": result, "exit_code": 0},
                    blocks=[TextBlock(text=result if result else "No files found.")],
                    reward=0.0,
                    finished=False,
                )
            else:
                glob_flag = f" --include='{params.file_glob}'" if params.file_glob else ""
                context_flag = f" -C {params.context}" if params.context > 0 else ""

                if params.output_mode == "files_only":
                    mode_flag = " -l"
                elif params.output_mode == "count":
                    mode_flag = " -c"
                else:
                    mode_flag = " -n"

                cmd = f"grep -r{mode_flag}{context_flag}{glob_flag} '{params.pattern}' {params.path}"
                output, code = await self.sandbox.run(cmd)

                # grep 返回 1 表示无匹配——不是错误
                if code > 1:
                    return ToolOutput(
                        metadata={"error": output, "exit_code": code},
                        blocks=[TextBlock(text=f"search_files failed (exit {code}):\n{output}")],
                        finished=False,
                    )

                lines = output.splitlines()
                lines = lines[params.offset:params.offset + params.limit]
                result = "\n".join(lines)

                return ToolOutput(
                    metadata={"output": result, "exit_code": 0},
                    blocks=[TextBlock(text=result if result else "No matches found.")],
                    reward=0.0,
                    finished=False,
                )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error searching files: {str(e)}")],
                finished=False,
            )

    @tool
    async def patch(self, params: PatchParams) -> ToolOutput:
        if params.mode == "replace":
            return await self._patch_replace(params)
        elif params.mode == "patch":
            return await self._patch_v4a(params)
        else:
            return ToolOutput(
                metadata={"error": f"Unknown mode: {params.mode}"},
                blocks=[TextBlock(text=f"Error: unknown patch mode '{params.mode}'. Use 'replace' or 'patch'.")],
                finished=False,
            )

    async def _patch_replace(self, params: PatchParams) -> ToolOutput:
        try:
            if not params.path or params.old_string is None or params.new_string is None:
                return ToolOutput(
                    metadata={"error": "path, old_string, and new_string are required for replace mode"},
                    blocks=[TextBlock(text="Error: path, old_string, and new_string are required for replace mode.")],
                    finished=False,
                )

            content = await _download_text(self.sandbox, params.path)

            count = content.count(params.old_string)
            if count == 0:
                return ToolOutput(
                    metadata={"error": f"old_string not found in {params.path}"},
                    blocks=[TextBlock(text=f"Error: old_string not found in {params.path}")],
                    finished=False,
                )
            if not params.replace_all and count > 1:
                return ToolOutput(
                    metadata={"error": f"old_string appears {count} times; use replace_all or provide more context"},
                    blocks=[TextBlock(text=f"Error: old_string appears {count} times in {params.path}. Must be unique unless replace_all=true.")],
                    finished=False,
                )

            if params.replace_all:
                new_content = content.replace(params.old_string, params.new_string)
            else:
                new_content = content.replace(params.old_string, params.new_string, 1)

            await _upload_text(self.sandbox, params.path, new_content, ensure_trailing_newline=True)

            return ToolOutput(
                metadata={"output": "", "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully patched {params.path}")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error patching file: {str(e)}")],
                finished=False,
            )

    async def _patch_v4a(self, params: PatchParams) -> ToolOutput:
        try:
            if not params.patch:
                return ToolOutput(
                    metadata={"error": "patch content is required for patch mode"},
                    blocks=[TextBlock(text="Error: patch content is required for patch mode.")],
                    finished=False,
                )

            patch_tmp = "/tmp/_hermes_patch.diff"
            await _upload_text(self.sandbox, patch_tmp, params.patch, ensure_trailing_newline=True)

            output, code = await self.sandbox.run(f"patch -p1 < {patch_tmp}")
            if code != 0:
                await self.sandbox.run(f"rm -f {patch_tmp}")
                return ToolOutput(
                    metadata={"error": output, "exit_code": code},
                    blocks=[TextBlock(text=f"patch failed (exit {code}):\n{output}")],
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


HermesToolset.terminal.__doc__ = TERMINAL_DESCRIPTION
HermesToolset.read_file.__doc__ = READ_FILE_DESCRIPTION
HermesToolset.write_file.__doc__ = WRITE_FILE_DESCRIPTION
HermesToolset.search_files.__doc__ = SEARCH_FILES_DESCRIPTION
HermesToolset.patch.__doc__ = PATCH_DESCRIPTION

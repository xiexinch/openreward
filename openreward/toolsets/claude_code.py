"""Claude Code 会话工具集。

提供 Claude Code CLI 公开的七个内置工具
（``bash``、``glob``、``grep``、``read``、``write``、``edit``、``todo_write``），
每个工具都由绑定环境中的 ``self.sandbox`` 支持。

工具描述复制自 firehorse 内置描述
（``firehorse/firehorse/mcp/builtin_descriptions.py``），最初从
``claude-code/src/tools/*/prompt.ts`` 中提取。此处内联以便
该工具集在运行时无需依赖 firehorse。
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from openreward.environments.environment import tool
from openreward.environments.toolset import Toolset
from openreward.environments.types import TextBlock, ToolOutput


# ── Pydantic 参数模型 ──

class BashParams(BaseModel, extra="forbid"):
    command: str
    description: str = ""
    timeout: Optional[float] = 30.0


class GlobParams(BaseModel, extra="forbid"):
    pattern: str
    path: Optional[str] = None


class GrepParams(BaseModel, extra="forbid"):
    pattern: str
    path: Optional[str] = None
    glob: Optional[str] = None


class ReadParams(BaseModel, extra="forbid"):
    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class WriteParams(BaseModel, extra="forbid"):
    file_path: str
    content: str


class EditParams(BaseModel, extra="forbid"):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class TodoWriteParams(BaseModel, extra="forbid"):
    todos: List[Dict[str, Any]]


# ── 沙箱文本辅助函数（内联；sdk 沙箱仅提供字节级辅助） ──

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


# ── 工具描述（直接来自 firehorse builtin_descriptions.py） ──

BASH_DESCRIPTION = """\
执行给定的 bash 命令并返回其输出。

工作目录在命令之间保持持久，但 shell 状态不会。shell 环境从用户的配置文件（bash 或 zsh）初始化。

重要：避免使用此工具运行 `find`、`grep`、`cat`、`head`、`tail`、`sed`、`awk` 或 `echo` 命令，除非明确指示或在验证专用工具无法完成任务后。相反，使用适当的专用工具，因为这将为用户提供更好的体验：

 - 文件搜索：使用 mcp__openreward__glob（如果可用）（不要使用 find 或 ls）
 - 内容搜索：使用 mcp__openreward__grep（如果可用）（不要使用 grep 或 rg）
 - 读取文件：使用 mcp__openreward__read（如果可用）（不要使用 cat/head/tail）
 - 编辑文件：使用 mcp__openreward__edit（如果可用）（不要使用 sed/awk）
 - 写入文件：使用 mcp__openreward__write（如果可用）（不要使用 echo >/cat <<EOF）
 - 通信：直接输出文本（不要使用 echo/printf）
虽然 bash 工具可以做类似的事情，但最好使用专用工具，因为它们提供更好的用户体验，并使审查工具调用和授予权限更容易。

# 说明
 - 如果你的命令将创建新目录或文件，首先使用此工具运行 `ls` 以验证父目录存在且位置正确。
 - 始终在命令中用双引号引用包含空格的文件路径（例如，cd "path with spaces/file.txt"）
 - 尝试通过使用绝对路径并避免使用 `cd` 来保持整个会话的当前工作目录。如果用户明确要求，你可以使用 `cd`。
 - 你可以指定可选的超时时间（以毫秒为单位，最多 600000 毫秒 / 10 分钟）。默认情况下，你的命令将在 120000 毫秒（2 分钟）后超时。
 - 你可以使用 `run_in_background` 参数在后台运行命令。仅在你不需要立即获得结果并且愿意在命令稍后完成时收到通知的情况下使用此功能。你不需要立即检查输出——完成后你会收到通知。使用此参数时，不需要在命令末尾使用 '&'。
 - 发出多个命令时：
  - 如果命令相互独立且可以并行运行，请在单个消息中发出多个 Bash 工具调用。示例：如果你需要运行 "git status" 和 "git diff"，请发送包含两个并行 Bash 工具调用的单个消息。
  - 如果命令相互依赖且必须按顺序运行，请使用单个 Bash 调用并用 '&&' 链式连接。
  - 仅在需要按顺序运行命令但不在乎先前命令是否失败时使用 ';'。
  - 不要使用换行符分隔命令（换行符在引号字符串中是可以的）。
 - 对于 git 命令：
  - 优先创建新提交，而不是修改现有提交。
  - 在运行破坏性操作（例如 git reset --hard、git push --force、git checkout --）之前，考虑是否有更安全的替代方案可以达到相同目标。仅在破坏性操作确实是最佳方法时才使用它们。
  - 除非用户明确要求，否则不要跳过钩子（--no-verify）或绕过签名（--no-gpg-sign、-c commit.gpgsign=false）。如果钩子失败，请调查并修复根本问题。
 - 避免不必要的 `sleep` 命令：
  - 不要在可以立即运行的命令之间 sleep——直接运行它们。
  - 如果你的命令长时间运行，并且你希望在完成时收到通知——请使用 `run_in_background`。不需要 sleep。
  - 不要在 sleep 循环中重试失败的命令——诊断根本原因。
  - 如果你正在等待使用 `run_in_background` 启动的后台任务，完成后你会收到通知——不要轮询。
  - 如果你必须轮询外部进程，请使用检查命令（例如 `gh run view`）而不是先 sleep。
  - 如果你必须 sleep，请保持持续时间较短（1-5 秒）以避免阻塞用户。
"""

GREP_DESCRIPTION = """\
基于 ripgrep 构建的强大搜索工具

  用法：
  - 始终使用 Grep 进行搜索任务。切勿将 `grep` 或 `rg` 作为 Bash 命令调用。Grep 工具已针对正确的权限和访问进行了优化。
  - 支持完整的正则表达式语法（例如，"log.*Error"、"function\s+\w+"）
  - 使用 glob 参数过滤文件（例如，"*.js"、"**/*.tsx"）或使用 type 参数（例如，"js"、"py"、"rust"）
  - 输出模式："content" 显示匹配行，"files_with_matches" 仅显示文件路径（默认），"count" 显示匹配计数
  - 对于需要多轮搜索的开放式搜索，请使用 Agent 工具
  模式语法：使用 ripgrep（不是 grep）——字面量花括号需要转义（使用 `interface\{\}` 在 Go 代码中查找 `interface{}`）
  - 多行匹配：默认情况下，模式仅匹配单行。对于跨行模式，如 `struct \{[\s\S]*?field`，请使用 `multiline: true`
"""

GLOB_DESCRIPTION = """\
- 适用于任何代码库大小的快速文件模式匹配工具
- 支持 glob 模式，如 "**/*.js" 或 "src/**/*.ts"
- 返回按修改时间排序的匹配文件路径
- 在需要按名称模式查找文件时使用此工具
- 当你进行可能需要多轮 glob 和 grep 的开放式搜索时，请改用 Agent 工具"""

READ_DESCRIPTION = """\
从本地文件系统读取文件。你可以通过使用此工具直接访问任何文件。
假设此工具能够读取机器上的所有文件。如果用户提供了文件路径，请假设该路径有效。读取不存在的文件是可以的；将返回错误。

用法：
- file_path 参数必须是绝对路径，而不是相对路径
- 默认情况下，它从文件开头读取最多 2000 行
- 当你已经知道需要文件的哪一部分时，只读取该部分。这对于较大的文件可能很重要。
- 结果使用 cat -n 格式返回，行号从 1 开始
- 此工具允许 Claude Code 读取图像（例如 PNG、JPG 等）。读取图像文件时，内容以视觉方式呈现，因为 Claude Code 是多模态 LLM。
- 此工具可以读取 PDF 文件（.pdf）。对于大型 PDF（超过 10 页），你必须提供 pages 参数以读取特定页面范围（例如，pages: "1-5"）。没有 pages 参数读取大型 PDF 将失败。每次请求最多 20 页。
- 此工具可以读取 Jupyter 笔记本（.ipynb 文件）并返回所有单元格及其输出，结合代码、文本和可视化。
- 此工具只能读取文件，不能读取目录。要读取目录，请通过 mcp__openreward__bash 工具使用 ls 命令（如果可用）。
- 你会定期被要求读取屏幕截图。如果用户提供了屏幕截图的路径，请始终使用此工具查看该路径的文件。此工具适用于所有临时文件路径。
- 如果你读取的文件存在但内容为空，你将收到系统提醒警告，而不是文件内容。"""

EDIT_DESCRIPTION = """\
在文件中执行精确的字符串替换。

用法：
- 如果可用，你必须在对话中至少使用一次 `mcp__openreward__read` 工具，然后才能进行编辑。如果你尝试在未读取文件的情况下进行编辑，此工具将报错。
- 从 read 工具输出编辑文本时，请确保保留行号前缀之后的精确缩进（制表符/空格）。行号前缀格式为：行号 + 制表符。其后的所有内容都是要匹配的实际文件内容。切勿在 old_string 或 new_string 中包含行号前缀的任何部分。
- 始终优先编辑代码库中的现有文件。除非明确要求，否则切勿写入新文件。
- 仅在用户明确要求时使用表情符号。除非要求，否则避免在文件中添加表情符号。
- 如果 `old_string` 在文件中不唯一，编辑将失败。请提供包含更多上下文的更大字符串以使其唯一，或使用 `replace_all` 更改 `old_string` 的每个实例。
- 当你想替换和重命名文件中的字符串时，请使用 `replace_all`。此参数在你想重命名变量等情况下很有用。"""

WRITE_DESCRIPTION = """\
将文件写入本地文件系统。

用法：
- 如果提供的路径上已有文件，此工具将覆盖该文件。
- 如果这是现有文件，如果可用，你必须首先使用 mcp__openreward__read 工具读取文件内容。如果你未先读取文件，此工具将失败。
- 优先使用 mcp__openreward__edit 工具（如果可用）修改现有文件——它只发送差异。仅在创建新文件或完全重写时使用此工具。
- 切勿创建文档文件（*.md）或 README 文件，除非用户明确要求。
- 仅在用户明确要求时使用表情符号。除非要求，否则避免在文件中写入表情符号。"""

TODO_WRITE_DESCRIPTION = """\
管理待办事项列表以进行任务规划和进度跟踪。

每个待办事项应有：id、content、status、priority。
状态选项："pending"、"in_progress"、"completed"
优先级选项："high"、"medium"、"low""""


# ── 工具集 ──

class ClaudeCodeToolset(Toolset):
    """会话工具集，公开 Claude Code 的七工具界面。

    通过将其传递给 ``env.session(...)`` 来绑定到会话：

        from openreward.toolsets import ClaudeCodeToolset

        with env.session(task=task, toolset="claude-code") as session:
            session.call_tool("bash", {"command": "ls"})

    要求绑定的环境定义 ``self.sandbox``。
    """

    @classmethod
    def name(cls) -> str:
        return "claude-code"

    def __init__(self, env: Optional[Any] = None, sandbox_attr: str = "sandbox"):
        super().__init__(env, sandbox_attr)
        self.todos: List[Dict[str, Any]] = []

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

    @tool
    async def glob(self, params: GlobParams) -> ToolOutput:
        try:
            search_path = params.path or "."
            cmd = f"find {search_path} -name '{params.pattern}' -type f | sort"
            output, code = await self.sandbox.run(cmd)
            return ToolOutput(
                metadata={"output": output, "exit_code": code},
                blocks=[TextBlock(text=f"{output}\n\n(exit {code})")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error in glob search: {str(e)}")],
                finished=False,
            )

    @tool
    async def grep(self, params: GrepParams) -> ToolOutput:
        try:
            search_path = params.path or "."
            if params.glob:
                cmd = f"find {search_path} -name '{params.glob}' -type f -exec grep -Hn '{params.pattern}' {{}} \\;"
            else:
                cmd = f"grep -r -n '{params.pattern}' {search_path}"
            output, code = await self.sandbox.run(cmd)
            return ToolOutput(
                metadata={"output": output, "exit_code": code},
                blocks=[TextBlock(text=f"{output}\n\n(exit {code})")],
                reward=0.0,
                finished=False,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error in grep search: {str(e)}")],
                finished=False,
            )

    @tool
    async def read(self, params: ReadParams) -> ToolOutput:
        try:
            if params.offset and params.limit:
                end_line = params.offset + params.limit
                cmd = f"sed -n '{params.offset},{end_line}p' {params.file_path} | cat -n"
                output, code = await self.sandbox.run(cmd)
            elif params.offset:
                cmd = f"tail -n +{params.offset} {params.file_path} | cat -n"
                output, code = await self.sandbox.run(cmd)
            elif params.limit:
                cmd = f"head -n {params.limit} {params.file_path} | cat -n"
                output, code = await self.sandbox.run(cmd)
            else:
                content = await _download_text(self.sandbox, params.file_path)
                lines = content.splitlines()
                output = "\n".join(f"{idx + 1}\t{line}" for idx, line in enumerate(lines))
                if content.endswith("\n") and output:
                    output += "\n"
                code = 0
            return ToolOutput(
                metadata={"output": output, "exit_code": code},
                blocks=[TextBlock(text=f"{output}\n\n(exit {code})")],
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
            dir_name = os.path.dirname(params.file_path)
            if dir_name:
                await self.sandbox.run(f"mkdir -p {dir_name}")
            await _upload_text(
                self.sandbox,
                params.file_path,
                params.content,
                ensure_trailing_newline=True,
            )
            return ToolOutput(
                metadata={"output": "", "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully wrote to {params.file_path}\n\n(exit 0)")],
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
            content = await _download_text(self.sandbox, params.file_path)

            count = content.count(params.old_string)
            if count == 0:
                return ToolOutput(
                    metadata={"error": f"String not found in {params.file_path}"},
                    blocks=[TextBlock(text=f"Error: old_string not found in {params.file_path}")],
                    finished=False,
                )
            if not params.replace_all and count > 1:
                return ToolOutput(
                    metadata={"error": f"old_string appears {count} times; use replace_all or provide more context"},
                    blocks=[TextBlock(text=f"Error: old_string appears {count} times in {params.file_path}. Must be unique unless replace_all=true.")],
                    finished=False,
                )

            if params.replace_all:
                new_content = content.replace(params.old_string, params.new_string)
            else:
                new_content = content.replace(params.old_string, params.new_string, 1)

            await _upload_text(self.sandbox, params.file_path, new_content, ensure_trailing_newline=True)

            return ToolOutput(
                metadata={"output": "", "exit_code": 0},
                blocks=[TextBlock(text=f"Successfully edited {params.file_path}\n\n(exit 0)")],
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
    def todo_write(self, params: TodoWriteParams) -> ToolOutput:
        try:
            self.todos = params.todos

            output_lines = ["=== TODO LIST ==="]
            for todo in self.todos:
                status = todo.get("status", "pending")
                priority = todo.get("priority", "medium")
                output_lines.append(
                    f"[{status}] [{priority}] {todo.get('content', 'No description')}"
                )

            text = "\n".join(output_lines)
            return ToolOutput(
                metadata={"todos": self.todos, "count": len(self.todos)},
                blocks=[TextBlock(text=text)],
                finished=False,
                reward=0.0,
            )
        except Exception as e:
            return ToolOutput(
                metadata={"error": str(e)},
                blocks=[TextBlock(text=f"Error managing todos: {str(e)}")],
                finished=False,
            )


# 将描述分配给每个工具方法的 __doc__，以便框架的
# 内省（读取 fn.__doc__ 作为 ToolSpec.description）能够获取它们。
ClaudeCodeToolset.bash.__doc__ = BASH_DESCRIPTION
ClaudeCodeToolset.glob.__doc__ = GLOB_DESCRIPTION
ClaudeCodeToolset.grep.__doc__ = GREP_DESCRIPTION
ClaudeCodeToolset.read.__doc__ = READ_DESCRIPTION
ClaudeCodeToolset.write.__doc__ = WRITE_DESCRIPTION
ClaudeCodeToolset.edit.__doc__ = EDIT_DESCRIPTION
ClaudeCodeToolset.todo_write.__doc__ = TODO_WRITE_DESCRIPTION

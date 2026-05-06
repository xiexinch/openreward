"""常见任务的预置工具集。"""

from openreward.environments.toolset import Toolset

from .claude_code import ClaudeCodeToolset
from .codex import CodexToolset
from .excel import ExcelToolset
from .gemini_cli import GeminiCliToolset
from .hermes import HermesToolset
from .openclaw import OpenClawToolset
from .pdf import PDFToolset
from .powerpoint import PowerPointToolset
from .word import WordToolset

# 会话级内置工具集的注册表，可以通过名称传递给
# ``env.session(toolset=...)``。会话 API 将类或实例解析为
# 其注册名称，并将该名称转发给服务器，服务器会针对
# 每个会话的 ``Environment`` 实例化该类。
BUILTIN_TOOLSETS: dict[str, type[Toolset]] = {
    ClaudeCodeToolset.name(): ClaudeCodeToolset,
    CodexToolset.name(): CodexToolset,
    GeminiCliToolset.name(): GeminiCliToolset,
    HermesToolset.name(): HermesToolset,
    OpenClawToolset.name(): OpenClawToolset,
}

__all__ = [
    "BUILTIN_TOOLSETS",
    "ClaudeCodeToolset",
    "CodexToolset",
    "ExcelToolset",
    "GeminiCliToolset",
    "HermesToolset",
    "OpenClawToolset",
    "PowerPointToolset",
    "WordToolset",
    "PDFToolset",
]

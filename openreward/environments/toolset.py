"""提供可重用工具集合的 toolsets 基类。"""

from abc import ABC
from typing import Any


class Toolset(ABC):
    """
    toolsets 的可选基类。为基于沙箱的 toolsets
    提供通用工具。

    Toolsets 是可以轻松在不同环境中重用的
    相关工具的集合。它们遵循与 Environment 方法相同的 @tool 装饰器
    模式。

    用法：
        from openreward.environments import Toolset, tool, ToolOutput

        class MyToolset(Toolset):
            @tool
            async def my_tool(self) -> ToolOutput:
                # 使用 self.sandbox 与沙箱交互
                output, code = await self.sandbox.run("ls")
                return ToolOutput(blocks=[TextBlock(text=output)])

        # 在你的环境中：
        class MyEnv(Environment):
            toolsets = [MyToolset]

            def __init__(self, task_spec, secrets):
                super().__init__(task_spec, secrets)
                self.sandbox = ...  # MyToolset 将自动访问它
    """

    def __init__(self, env: Any, sandbox_attr: str = "sandbox"):
        """
        使用环境依赖初始化 toolset。

        参数：
            env: 拥有此 toolset 的环境实例。
            sandbox_attr: env 上沙箱属性的名称（默认："sandbox"）。
                         toolset 将查找此属性并将其存储为
                         self.sandbox 以便轻松访问。

        引发：
            ValueError: 如果 env 未定义 ``sandbox_attr``（或它为 None）。
        """
        self.env = env
        if not hasattr(env, sandbox_attr) or getattr(env, sandbox_attr) is None:
            raise ValueError(
                f"Toolset {type(self).__name__} requires `env.{sandbox_attr}` to be set "
                f"(typically assigned in the environment's __init__ or setup()) but the "
                f"environment does not define one or it is None."
            )
        self.sandbox = getattr(env, sandbox_attr)

    @classmethod
    def name(cls) -> str:
        """此 toolset 的稳定标识符，用于线序列化。"""
        return cls.__name__

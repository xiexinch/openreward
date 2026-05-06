"""用于将 Environment 的工具与会话级 Toolset 合并的会话作用域辅助函数。

这些辅助函数与 ``Environment`` 并存，以便 Environment 类本身
保持对会话作用域 toolsets 的无感知。服务器单独持有每个会话的
``Toolset`` 实例，并在处理 ``/{env_name}/task_tools`` 和 ``/{env_name}/call`` 请求时
通过这些辅助函数传递它。
"""

from __future__ import annotations

import time
from typing import Optional

from openreward.log_utils import get_logger as _get_logger

from .environment import Environment, _introspect_tool
from .toolset import Toolset
from .types import JSONObject, ListToolsOutput, RunToolOutput, ToolSpec

logger = _get_logger("openreward.environments.session")


def _toolset_specs(toolset: Toolset) -> list[ToolSpec]:
    """将 Toolset 实例内省为 ToolSpecs。

    镜像 ``Environment.list_tools`` 发现：遍历实例中
    标记为 ``@tool``（且 ``shared=True``）的方法，从 Pydantic 参数模型中提取 schema，
    并使用每个函数的 docstring 作为描述。
    """
    out: list[ToolSpec] = []
    for attr in dir(toolset):
        fn = getattr(toolset, attr, None)
        if fn is None or not Environment._is_tool(fn) or not getattr(fn, "_env_tool_shared", True):
            continue
        _, hints, params = _introspect_tool(fn)
        schema = None
        if params:
            mdl = hints[params[0].name]
            schema = mdl.model_json_schema() if hasattr(mdl, "model_json_schema") else mdl.schema()  # type: ignore[attr-defined]
        out.append(ToolSpec(
            name=attr,
            description=(fn.__doc__ or "").strip(),
            input_schema=schema,
        ))
    return out


def list_session_tools(env: Environment, toolset: Optional[Toolset]) -> ListToolsOutput:
    """返回实时会话可见的合并工具列表。

    组合类级共享工具、实例级任务工具以及任何
    会话绑定的 toolset。来自会话 toolset 的工具会替换环境中
    或其声明的 toolsets 中同名的任何工具，并为每个
    影子记录警告。
    """
    merged: list[ToolSpec] = list(env.list_tools().tools) + list(env.list_task_tools().tools)
    if toolset is None:
        return ListToolsOutput(tools=merged)

    ts_specs = _toolset_specs(toolset)
    ts_names = {s.name for s in ts_specs}
    for spec in merged:
        if spec.name in ts_names:
            logger.warning(
                "session_toolset_shadows_env_tool",
                tool=spec.name,
                toolset=type(toolset).__name__,
                env=type(env).__name__,
            )
    return ListToolsOutput(tools=[s for s in merged if s.name not in ts_names] + ts_specs)


async def call_session_tool(
    env: Environment,
    toolset: Optional[Toolset],
    name: str,
    input: JSONObject,
) -> RunToolOutput:
    """为实时会话分派工具调用。

    当名称冲突时，会话 toolset 优先于环境级工具
    和类级声明的 toolsets。每当会话 toolset 工具
    遮蔽环境定义的工具时，都会记录警告。
    """
    if toolset is not None:
        ts_fn = getattr(toolset, name, None)
        if ts_fn is not None and Environment._is_tool(ts_fn):
            env_fn = getattr(env, name, None)
            if env_fn is not None and Environment._is_tool(env_fn):
                logger.warning(
                    "session_toolset_shadows_env_tool",
                    tool=name,
                    toolset=type(toolset).__name__,
                    env=type(env).__name__,
                )
            return await env._invoke_tool_fn(name, ts_fn, input, time.monotonic())
    return await env._call_tool(name, input)

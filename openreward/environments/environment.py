import asyncio
import functools
import inspect
import time
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, ClassVar, Optional, Sequence, TypeVar, Union, get_type_hints, overload

from pydantic import BaseModel, ValidationError

from openreward.log_utils import get_logger as _get_logger
from .types import (Blocks, JSONObject, ListToolsOutput, RunToolError,
                    RunToolOutput, RunToolSuccess, Split, ToolOutput, ToolSpec)
from .utils import maybe_await

T = TypeVar("T")

logger = _get_logger("openreward.environments")


@overload
def tool(fn: Callable[..., Any]) -> Callable[..., Any]: ...
@overload
def tool(*, shared: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
def tool(fn: Optional[Callable[..., Any]] = None, *, shared: bool = True) -> Callable[..., Any]:
    if fn is None:
        # 使用参数调用：@tool(shared=False)
        def wrapper(f: Callable[..., Any]) -> Callable[..., Any]:
            setattr(f, "_env_tool", True)
            setattr(f, "_env_tool_shared", shared)
            return f
        return wrapper
    # 无参数调用：@tool
    setattr(fn, "_env_tool", True)
    setattr(fn, "_env_tool_shared", True)
    return fn


def _introspect_tool(fn: Callable[..., Any]) -> tuple[Any, dict, list]:
    """返回工具函数的 (unwrapped_fn, type_hints, non_self params)。"""
    real = inspect.unwrap(fn)
    hints = get_type_hints(real, include_extras=True)
    params = [p for p in inspect.signature(real).parameters.values() if p.name != "self"]
    return (real, hints, params)


class Environment(ABC):
    """
    环境是有状态计算的接口。客户端通过持久连接与环境交互以执行操作。
    环境具有 _tasks_，即描述特定设置和目标状态的 JSON 对象。
    例如，在 Ubuntu 环境中，一个任务可以是从互联网下载文件并将其内容保存到 csv 文件。
    """
    toolsets: ClassVar[Sequence[type]] = ()

    def __init__(self, task_spec: JSONObject = {}, secrets: dict[str, str] = {}) -> None:
        self.task_spec = task_spec
        self._toolset_instances: dict[type, Any] = {}  # 已实例化 toolset 的缓存

    def setup(self) -> Optional[Awaitable[None]]:
        """
        设置环境。在已连接客户端首次调用工具时调用。
        """
        pass

    def teardown(self) -> Optional[Awaitable[None]]:
        """
        拆卸环境。在客户端断开连接时调用。
        """
        pass

    @abstractmethod
    def get_prompt(self) -> Union[Blocks, Awaitable[Blocks]]:
        """
        获取当前任务的默认提示。例如，如果任务是问答对，
        返回问题将是一个合理的选择。
        """

    @classmethod
    @abstractmethod
    def list_tasks(cls, split: str) -> Union[Sequence[JSONObject], Awaitable[Sequence[JSONObject]]]:
        """
        获取给定 split 的任务列表。默认为空列表。
        """

    @classmethod
    @abstractmethod
    def list_splits(cls) -> Sequence[Union[Split, str]]:
        """
        获取环境的分割列表。默认为空列表。
        """

    @classmethod
    def _list_splits_cached(cls) -> Sequence[Union[Split, str]]:
        """返回 list_splits() 的缓存结果。"""
        if not hasattr(cls, '_splits_cache'):
            cls._splits_cache: Sequence[Union[Split, str]] = cls.list_splits()
        return cls._splits_cache

    @classmethod
    async def _list_tasks_cached(cls, split: str) -> Sequence[JSONObject]:
        """返回 list_tasks(split) 的缓存结果，使用每个 split 的锁来防止惊群效应。"""
        if not hasattr(cls, '_tasks_cache'):
            cls._tasks_cache: dict[str, Sequence[JSONObject]] = {}
        if not hasattr(cls, '_tasks_cache_locks'):
            cls._tasks_cache_locks: dict[str, asyncio.Lock] = {}

        if split not in cls._tasks_cache:
            if split not in cls._tasks_cache_locks:
                cls._tasks_cache_locks[split] = asyncio.Lock()
            async with cls._tasks_cache_locks[split]:
                if split not in cls._tasks_cache:
                    cls._tasks_cache[split] = await maybe_await(cls.list_tasks(split))
        return cls._tasks_cache[split]

    @classmethod
    async def num_tasks(cls, split: str) -> int:
        """获取给定 split 的任务数量。"""
        tasks = await cls._list_tasks_cached(split)
        return len(tasks)

    @classmethod
    async def get_task(cls, split: str, index: int) -> JSONObject:
        """
        获取给定 `split` 的任务 `index`。默认列出所有任务并取索引。
        """
        tasks = await cls._list_tasks_cached(split)
        return tasks[index]

    @classmethod
    async def get_task_range(cls, split: str, start: Optional[int] = None, stop: Optional[int] = None) -> list[JSONObject]:
        """
        获取给定 split 中索引在 range(start, stop) 范围内的任务。
        遵循 Python range/slice 约定：start 包含，stop 不包含。
        支持负索引（相对于 num_tasks 解析）和 None
        （None start 默认为 0，None stop 默认为 num_tasks）。
        """
        try:
            tasks = await cls._list_tasks_cached(split)
        except NotImplementedError:
            # LargeEnv 及类似情况：回退到按索引 get_task
            total = await cls.num_tasks(split)
            if start is None:
                start = 0
            if stop is None:
                stop = total
            if start < 0:
                start = max(total + start, 0)
            if stop < 0:
                stop = max(total + stop, 0)
            start = min(start, total)
            stop = min(stop, total)
            return [await cls.get_task(split, i) for i in range(start, stop)]

        total = len(tasks)
        # 解析 None
        if start is None:
            start = 0
        if stop is None:
            stop = total
        # 解析负索引
        if start < 0:
            start = max(total + start, 0)
        if stop < 0:
            stop = max(total + stop, 0)
        # 限制在边界内
        start = min(start, total)
        stop = min(stop, total)
        return list(tasks[start:stop])

    @staticmethod
    def _is_tool(fn: Callable[..., Any]) -> bool:
        if not callable(fn) or not getattr(fn, "_env_tool", False):
            return False
        _, hints, params = _introspect_tool(fn)
        ret = hints.get("return")
        if len(params) == 0:
            return ret == ToolOutput
        if len(params) == 1:
            pt = hints.get(params[0].name)
            return (
                pt is not None and ret is not None and inspect.isclass(pt)
                and issubclass(pt, BaseModel) and ret == ToolOutput
            )
        return False

    @classmethod
    @functools.cache
    def list_tools(cls) -> ListToolsOutput:
        """
        列出此环境类上所有可用的工具。结果按类缓存，因为
        工具集是静态的。
        """
        out: list[ToolSpec] = []
        env_tool_names: set[str] = set()

        # 从类本身发现共享工具
        for name in dir(cls):
            fn = getattr(cls, name)
            if not cls._is_tool(fn) or not getattr(fn, "_env_tool_shared", True):
                continue
            _, hints, params = _introspect_tool(fn)
            schema = None
            if params:
                mdl: type[BaseModel] = hints[params[0].name]  # type: ignore[assignment]
                schema = mdl.model_json_schema() if hasattr(mdl, "model_json_schema") else mdl.schema()  # type: ignore[attr-defined]
            out.append(ToolSpec(name=name, description=(fn.__doc__ or "").strip(), input_schema=schema))
            env_tool_names.add(name)

        # 从类级别声明的 toolsets 发现工具并检查冲突
        if cls.toolsets:
            for toolset_cls in cls.toolsets:
                for name in dir(toolset_cls):
                    fn = getattr(toolset_cls, name)
                    if not cls._is_tool(fn) or not getattr(fn, "_env_tool_shared", True):
                        continue

                    if name in env_tool_names:
                        raise ValueError(
                            f"Tool name collision: '{name}' is defined in both the environment "
                            f"and toolset '{toolset_cls.__name__}'. Please rename one of them to avoid conflicts."
                        )

                    _, hints, params = _introspect_tool(fn)
                    schema = None
                    if params:
                        mdl: type[BaseModel] = hints[params[0].name]  # type: ignore[assignment]
                        schema = mdl.model_json_schema() if hasattr(mdl, "model_json_schema") else mdl.schema()  # type: ignore[attr-defined]
                    out.append(ToolSpec(name=name, description=(fn.__doc__ or "").strip(), input_schema=schema))

        return ListToolsOutput(tools=out)

    def list_task_tools(self) -> ListToolsOutput:
        """重写以基于 self.task_spec 返回任务特定工具。
        默认返回空列表。"""
        return ListToolsOutput(tools=[])

    async def _call_tool(self, name: str, input: JSONObject) -> RunToolOutput:
        start = time.monotonic()

        # 检查工具是否存在于 self（环境）上
        env_fn = getattr(self, name, None)
        has_env_tool = env_fn is not None and self._is_tool(env_fn)

        # 检查工具是否存在于任何 toolset 中
        toolset_fn = None
        toolset_source = None

        if self.__class__.toolsets:
            for toolset_cls in self.__class__.toolsets:
                # 延迟实例化 toolsets，在实例上缓存
                if toolset_cls not in self._toolset_instances:
                    try:
                        self._toolset_instances[toolset_cls] = toolset_cls(self)
                    except TypeError:
                        try:
                            self._toolset_instances[toolset_cls] = toolset_cls()
                        except Exception as e:
                            logger.error("toolset_instantiation_failed", toolset=toolset_cls.__name__, error=str(e))
                            continue

                toolset_instance = self._toolset_instances[toolset_cls]
                candidate = getattr(toolset_instance, name, None)

                if candidate is not None and self._is_tool(candidate):
                    toolset_fn = candidate
                    toolset_source = toolset_cls.__name__
                    break

        # 检查冲突
        if has_env_tool and toolset_fn is not None:
            logger.error("tool_name_collision", tool=name, toolset=toolset_source)
            return RunToolOutput(RunToolError(
                error=f"Tool name collision: '{name}' is defined in both the environment "
                      f"and toolset '{toolset_source}'. Please rename one of them to avoid conflicts."
            ))

        # 确定使用哪个函数
        fn: Callable[..., Any]
        if has_env_tool:
            assert env_fn is not None
            fn = env_fn
        elif toolset_fn is not None:
            fn = toolset_fn
        else:
            logger.warning("tool_not_found", tool=name)
            return RunToolOutput(RunToolError(error=f"{name!r} is not a valid tool"))

        return await self._invoke_tool_fn(name, fn, input, start)

    async def _invoke_tool_fn(
        self,
        name: str,
        fn: Callable[..., Any],
        input: JSONObject,
        start: float,
    ) -> RunToolOutput:
        """根据工具的 Pydantic 模型验证输入并调用它。"""
        _, hints, params = _introspect_tool(fn)
        try:
            if not params:
                res = await maybe_await(fn())
            else:
                mdl: type[BaseModel] = hints[params[0].name]  # type: ignore[assignment]
                try:
                    inp = mdl(**input)
                except ValidationError as e:
                    logger.warning("tool_input_validation_error", tool=name, error=str(e.errors()))
                    return RunToolOutput(RunToolError(error=f"Tool input validation error: {str(e.errors())}"))
                res = await maybe_await(fn(inp))
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("tool_call_failed", tool=name, duration_ms=duration_ms)
            raise

        if not isinstance(res, ToolOutput):
            raise TypeError(f"{name!r} returned {type(res).__name__}; expected ToolOutput")

        duration_ms = (time.monotonic() - start) * 1000
        logger.info("tool_call_completed", tool=name, duration_ms=duration_ms)
        return RunToolOutput(RunToolSuccess(output=res))

    @classmethod
    def name(cls) -> str:
        return cls.__name__

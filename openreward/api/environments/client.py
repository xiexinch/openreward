import asyncio
import atexit
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Literal, Optional, Union, overload

import aiohttp
from openreward.api._session.http import (
    _raise_for_status,
    request_retryable,
    resumable_sse,
)
from openreward.api._session.session import BaseAsyncSession, SessionTerminatedError

BuiltinToolset = Literal["claude-code", "codex", "gemini-cli", "hermes", "openclaw"]
_VALID_BUILTIN_TOOLSETS = {"claude-code", "codex", "gemini-cli", "hermes", "openclaw"}
from .types import (
    ImageBlock,
    JSONObject,
    JSONValue,
    Mapping,
    Provider,
    Server,
    Task,
    TextBlock,
    ToolCallError,
    ToolOutput,
    ToolSpec,
)
from openreward.api.sandboxes.secrets import (
    build_secrets_header,
    augment_secrets_with_api_key,
)

GOOGLE_UNSUPPORTED_SCHEMA_KEYS = {
    "additionalProperties",  # JSON Schema
    "additional_properties",  # 有时已经转换过
    "title",  # 你已经去掉了，但这里也保留
    "default",  # 在函数模式中通常不受支持
    "examples",
    "example",
    "patternProperties",
    "oneOf",
    "allOf",
    "anyOf",
    "not",
}

OPENAI_UNSUPPORTED_SCHEMA_KEYS = {
    "additionalProperties",
    "patternProperties",
    "oneOf",
    "allOf",
    "anyOf",
    "not",
}


def _sanitize_google_schema(x: Any) -> Any:
    """递归移除 Gemini/Google 函数调用拒绝的 schema 键。"""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k in GOOGLE_UNSUPPORTED_SCHEMA_KEYS:
                continue
            if k == "$ref":
                k = "ref"
            elif k == "$defs":
                k = "defs"
            out[k] = _sanitize_google_schema(v)
        return out
    if isinstance(x, list):
        return [_sanitize_google_schema(i) for i in x]
    return x


def _fix_array_schemas(obj: Any) -> Any:
    """递归地为数组 schema 添加缺失的 'items'（OpenAI 需要）。"""
    if isinstance(obj, list):
        return [_fix_array_schemas(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    obj = {k: _fix_array_schemas(v) for k, v in obj.items()}
    t = obj.get("type")
    is_array = t == "array" or (isinstance(t, list) and "array" in t)
    if is_array and "items" not in obj:
        obj["items"] = {}
    return obj


def _sanitize_openai_schema(x: Any) -> Any:
    """
    递归地清理 OpenAI 函数调用所需的 schema。

    - 将 anyOf/oneOf/allOf 折叠为单个选项（anyOf 取第一个非 null，
      其他取第一个条目），同时保留外围 schema 的同级元数据，如
      description/default/title。
    - 移除不受支持的关键字（additionalProperties、patternProperties、
      not 等）
    - 确保数组类型具有 'items' 字段
    """
    if isinstance(x, dict):
        for key in ("anyOf", "oneOf", "allOf"):
            if key not in x:
                continue
            options = x[key]
            if not options:
                continue
            chosen = None
            if key == "anyOf":
                for option in options:
                    if not (isinstance(option, dict) and option.get("type") == "null"):
                        chosen = option
                        break
            if chosen is None:
                chosen = options[0]
            siblings = {k: v for k, v in x.items() if k != key}
            if isinstance(chosen, dict):
                merged = {**siblings, **chosen}
            else:
                return _sanitize_openai_schema(chosen)
            return _sanitize_openai_schema(merged)

        out = {}
        for k, v in x.items():
            if k in OPENAI_UNSUPPORTED_SCHEMA_KEYS:
                continue
            out[k] = _sanitize_openai_schema(v)

        if out.get("type") == "array" and "items" not in out:
            out["items"] = {}

        return out

    if isinstance(x, list):
        return [_sanitize_openai_schema(i) for i in x]

    return x


def _strip_titles(value: Any) -> Any:
    """递归移除 JSON schema 中的 `title` 键。"""
    if isinstance(value, dict):
        return {k: _strip_titles(v) for k, v in value.items() if k != "title"}
    if isinstance(value, list):
        return [_strip_titles(item) for item in value]
    return value


def sanitize_tool_schema(
    schema: Optional[Mapping[str, Any]],
    provider: Provider,
) -> dict[str, Any]:
    """为指定提供商的函数调用 API 清理 JSON Schema。

    去除 ``title`` 键并应用提供商特定的修复：

    - ``openai``/``openrouter``：折叠 anyOf/oneOf/allOf，删除不受支持的
      关键字（``additionalProperties``、``patternProperties``、``not``），并
      为数组类型默认添加缺失的 ``items``。
    - ``anthropic``：仅去除标题（Anthropic 接受标准 JSON Schema）。
    - ``google``：删除 Gemini 不支持的键并重命名 ``$ref``/``$defs``。

    当 ``schema`` 为 ``None`` 或空时返回 ``{}``，以便调用者可以安全地
    展开结果（``ToolParams(**sanitize_tool_schema(...))``）。
    """
    if not schema:
        return {}
    stripped = _strip_titles(schema)
    if provider in ("openai", "openrouter", "openai-compatible"):
        return _fix_array_schemas(_sanitize_openai_schema(stripped))
    if provider == "anthropic":
        return stripped
    if provider == "google":
        return _sanitize_google_schema(stripped)
    raise ValueError(f"Invalid provider: {provider!r}")


@overload
def convert_tool_response(
    res: Mapping[str, Any], format: None = None
) -> list[ToolSpec]: ...


@overload
def convert_tool_response(
    res: Mapping[str, Any], format: Provider = ...
) -> list[dict[str, Any]]: ...


def convert_tool_response(
    res: Mapping[str, Any],
    format: Optional[Provider] = None,
) -> Union[list[ToolSpec], list[dict[str, Any]]]:
    if format is None:
        return [ToolSpec(**tool) for tool in res["tools"]]

    if format not in (
        "openai",
        "openrouter",
        "anthropic",
        "google",
        "openai-compatible",
    ):
        raise ValueError(f"Invalid format: {format!r}")

    out: list[dict[str, Any]] = []
    for tool in res["tools"]:
        raw_schema = tool.get("input_schema")
        sanitized = sanitize_tool_schema(raw_schema, format)
        meta = {
            k: _strip_titles(v)
            for k, v in tool.items()
            if k not in {"input_schema", "title"}
        }

        if format == "openai":
            out.append(
                {
                    "type": "function",
                    **meta,
                    "parameters": sanitized if raw_schema else None,
                }
            )
        elif format == "openrouter":
            out.append(
                {
                    "type": "function",
                    "function": meta,
                    "parameters": sanitized if raw_schema else None,
                }
            )
        elif format == "anthropic":
            out.append(
                {
                    "type": "custom",
                    **meta,
                    "input_schema": (
                        sanitized
                        if raw_schema
                        else {"type": "object", "properties": {}}
                    ),
                }
            )
        elif format == "openai-compatible":
            meta["parameters"] = sanitized if raw_schema else None
            out.append({"type": "function", "function": meta})
        else:  # google
            out.append(
                {
                    **meta,
                    "parameters": sanitized if raw_schema else None,
                }
            )

    return out


def _validate_toolset_name(toolset: Optional[str]) -> Optional[str]:
    """验证 *toolset* 是否为已知的内置工具集名称。"""
    if toolset is None:
        return None
    if toolset not in _VALID_BUILTIN_TOOLSETS:
        raise ValueError(
            f"Unknown toolset {toolset!r}; "
            f"valid options: {sorted(_VALID_BUILTIN_TOOLSETS)}"
        )
    return toolset


@asynccontextmanager
async def matrix_sid_provider(
    client: aiohttp.ClientSession, server_name: str, token: Optional[str]
) -> AsyncGenerator[str, None]:
    """使用基于 SSE 的 /create_session 的临时 SID 提供器，通过 /delete 清理。"""
    sid: Optional[str] = None

    def on_event(event: str, data: str) -> None:
        nonlocal sid
        if event == "task_id":
            sid = data.strip()

    await resumable_sse(
        client,
        "/create_session",
        token=token,
        deployment=server_name,
        max_retries=3,
        on_event=on_event,
    )

    assert sid is not None, "No SID returned from /create_session"
    try:
        yield sid
    finally:
        try:
            await request_retryable(
                client,
                "POST",
                "/delete_session",
                sid=sid,
                expect_json=False,
                token=token,
            )
        except Exception:
            pass


class AsyncSession(BaseAsyncSession):

    def __init__(
        self,
        env: "AsyncEnvironment",
        task: Optional[Task] = None,
        secrets: Optional[Mapping[str, Union[str, tuple[str, list[str]]]]] = None,
        api_key: Optional[str] = None,
        split: Optional[str] = None,
        index: Optional[int] = None,
        toolset_name: Optional[str] = None,
        env_overrides: Optional[Mapping[str, str]] = None,
    ):
        has_task = task is not None
        has_index = split is not None and index is not None
        if has_task == has_index:
            raise ValueError(
                "Provide either task or both split and index, not both/neither"
            )
        if (split is None) != (index is None):
            raise ValueError("split and index must both be provided together")

        secrets = augment_secrets_with_api_key(
            secrets,
            api_key,
            base_url=str(env.client._base_url),
            api_base_url=str(env.api_client._base_url) if env.api_client else None,
        )

        creation_headers: Optional[dict[str, str]] = None
        if secrets:
            creation_headers = {"X-Secrets": build_secrets_header(secrets)}

        creation_payload: dict[str, Any] = {}
        if env_overrides:
            creation_payload["env"] = dict(env_overrides)

        super().__init__(
            base_url=str(env.client._base_url),
            api_key=api_key,
            creation_endpoint="/create_session",
            creation_payload=creation_payload,
            deployment=env.deployment_name,
            client=env.client,
            creation_headers=creation_headers,
        )

        self._secrets_headers = creation_headers
        self.env = env
        self.task = task
        self.split = split
        self.index = index
        self.toolset_name = toolset_name

        self._has_task_tools: bool = True

    def _env_path(self, suffix: str) -> str:
        """构建 URL 路径，匹配 AsyncEnvironment 的路由模式。

        当 variant 为 None 时，使用裸路径（重定向中间件会处理）。
        当 variant 已设置时，前缀加上 variant 名称。
        """
        if self.env.variant is None:
            return suffix
        return f"/{self.env.variant}{suffix}"

    async def _post_create(self) -> None:
        """获取 SID 后，发送 POST /create 并携带任务负载。"""
        create_payload: dict[str, Any] = {}
        if self.task is not None:
            create_payload["task_spec"] = self.task.task_spec
            create_payload["env_name"] = self.task.environment_name
        else:
            create_payload["split"] = self.split
            create_payload["index"] = self.index
            if self.env.variant is not None:
                create_payload["env_name"] = self.env.variant
        if self.toolset_name is not None:
            create_payload["toolset_name"] = self.toolset_name

        # 注意：即使代理在 /create_session 后的后续请求中注入这些头部
        # 我们在这里也需要传递它们，以支持本地开发（不经过代理）
        await request_retryable(
            self.client,
            "POST",
            "/create",
            expect_json=True,
            sid=self.sid,
            deployment=self.deployment,
            json=create_payload,
            token=self.api_key,
            extra_headers=self._secrets_headers,
        )

    async def _pre_delete(self) -> None:
        """发送 POST /delete 以在服务器上拆除环境。"""
        if self.sid:
            await request_retryable(
                self.client,
                "POST",
                "/delete",
                expect_json=False,
                sid=self.sid,
                token=self.api_key,
            )

    async def version(self) -> dict[str, Optional[str]]:
        return await self._run_or_die(
            request_retryable(
                self.client,
                "GET",
                "/version",
                expect_json=True,
                sid=self.sid,
                deployment=self.deployment,
                token=self.api_key,
            )
        )

    async def get_prompt(self) -> list[Union[TextBlock, ImageBlock]]:
        res = await self._run_or_die(
            request_retryable(
                self.client,
                "GET",
                self._env_path("/prompt"),
                expect_json=True,
                sid=self.sid,
                deployment=self.deployment,
                token=self.api_key,
            )
        )
        blocks: list[Union[TextBlock, ImageBlock]] = []
        for block in res:
            if block["type"] == "text":
                blocks.append(TextBlock(text=block["text"], detail=block["detail"]))
            elif block["type"] == "image":
                blocks.append(
                    ImageBlock(
                        mimeType=block["mimeType"],
                        detail=block["detail"],
                        data=block["data"],
                    )
                )
        return blocks

    @overload
    async def list_tools(self, format: None = None) -> list[ToolSpec]: ...

    @overload
    async def list_tools(self, format: Provider) -> list[dict]: ...

    async def list_tools(
        self, format: Optional[Provider] = None
    ) -> Union[list[ToolSpec], list[dict]]:
        if self._has_task_tools:
            try:
                res = await self._run_or_die(
                    request_retryable(
                        self.client,
                        "GET",
                        self._env_path("/task_tools"),
                        expect_json=True,
                        sid=self.sid,
                        deployment=self.deployment,
                        token=self.api_key,
                    )
                )
                return convert_tool_response(res, format=format)
            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    self._has_task_tools = False
                else:
                    raise
        res = await self._run_or_die(
            request_retryable(
                self.client,
                "GET",
                self._env_path("/tools"),
                expect_json=True,
                sid=self.sid,
                deployment=self.deployment,
                token=self.api_key,
            )
        )
        return convert_tool_response(res, format=format)

    async def call_tool(self, tool_name: str, input: JSONObject = {}) -> ToolOutput:
        if not isinstance(input, Mapping):
            raise ToolCallError(
                f"Tool input must be a dictionary, got {type(input).__name__}"
            )

        if not all(isinstance(k, str) for k in input.keys()):
            non_string_keys = [k for k in input.keys() if not isinstance(k, str)]
            raise ToolCallError(
                f"All keys in tool input must be strings. Found non-string keys: {non_string_keys}"
            )

        res = await self._run_or_die(
            resumable_sse(
                self.client,
                self._env_path("/call"),
                sid=self.sid,
                deployment=self.deployment,
                token=self.api_key,
                json={"name": tool_name, "input": input},
                max_retries=5,
            )
        )

        if res["ok"]:
            blocks: list[Union[TextBlock, ImageBlock]] = []
            for block in res["output"]["blocks"]:
                if block["type"] == "text":
                    blocks.append(TextBlock(text=block["text"], detail=block["detail"]))
                elif block["type"] == "image":
                    blocks.append(
                        ImageBlock(
                            mimeType=block["mimeType"],
                            detail=block["detail"],
                            data=block["data"],
                        )
                    )
            return ToolOutput(
                blocks=blocks,
                metadata=res["output"]["metadata"],
                reward=res["output"]["reward"],
                finished=res["output"]["finished"],
            )
        else:
            raise ToolCallError(res["error"])


class AsyncEnvironment:

    def __init__(
        self,
        namespace: Optional[str],
        name: str,
        variant: Optional[str],
        client: aiohttp.ClientSession,
        api_key: Optional[str],
        api_client: Optional[aiohttp.ClientSession] = None,
    ) -> None:

        self.server = name
        self.namespace = namespace
        self.name = name
        self.variant = variant
        self.client = client
        self.api_key = api_key
        self.api_client = api_client

    @property
    def deployment_name(self) -> str:
        if self.namespace is None:
            return self.name
        else:
            return f"{self.namespace}/{self.name}"

    async def list_splits(self) -> list[str]:
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            path = "/splits" if self.variant is None else f"/{self.variant}/splits"
            res = await request_retryable(
                self.client,
                "GET",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                token=self.api_key,
            )
            return [s["name"] for s in res]

    async def list_tasks(self, split: str) -> list[Task]:
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            path = "/tasks" if self.variant is None else f"/{self.variant}/tasks"
            res = await request_retryable(
                self.client,
                "POST",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                json={"split": split},
                token=self.api_key,
            )
            return [
                Task(
                    server_name=self.server,
                    environment_name=res["env_name"],
                    task_spec=task,
                    namespace=self.namespace,
                )
                for task in res["tasks"]
            ]

    async def num_tasks(self, split: str) -> int:
        """获取指定 split 的任务数量。"""
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            path = (
                "/num_tasks" if self.variant is None else f"/{self.variant}/num_tasks"
            )
            res = await request_retryable(
                self.client,
                "POST",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                json={"split": split},
                token=self.api_key,
            )
            return res["num_tasks"]

    async def get_task(self, split: str, index: int) -> Task:
        """通过 split 和 index 获取单个任务。"""
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            path = "/task" if self.variant is None else f"/{self.variant}/task"
            res = await request_retryable(
                self.client,
                "POST",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                json={"split": split, "index": index},
                token=self.api_key,
            )
            return Task(
                server_name=self.server,
                environment_name=res["env_name"],
                task_spec=res["task"],
                namespace=self.namespace,
            )

    async def get_task_range(
        self, split: str, start: Optional[int] = None, stop: Optional[int] = None
    ) -> list[Task]:
        """获取 range(start, stop) 范围内的任务。支持负数和 None 索引。"""
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            path = (
                "/task_range" if self.variant is None else f"/{self.variant}/task_range"
            )
            payload: dict[str, Any] = {"split": split}
            if start is not None:
                payload["start"] = start
            if stop is not None:
                payload["stop"] = stop
            res = await request_retryable(
                self.client,
                "POST",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                json=payload,
                token=self.api_key,
            )
            return [
                Task(
                    server_name=self.server,
                    environment_name=res["env_name"],
                    task_spec=task,
                    namespace=self.namespace,
                )
                for task in res["tasks"]
            ]

    async def list_tools(
        self, format: Optional[Provider] = None
    ) -> Union[list[ToolSpec], list[dict]]:
        path = "/tools" if self.variant is None else f"/{self.variant}/tools"
        async with matrix_sid_provider(
            self.client, self.deployment_name, self.api_key
        ) as sid:
            res = await request_retryable(
                self.client,
                "GET",
                path,
                expect_json=True,
                sid=sid,
                deployment=self.deployment_name,
                token=self.api_key,
            )
            return convert_tool_response(res, format=format)

    async def get_prompt(self, task: Task) -> str:
        async with matrix_sid_provider(
            self.client, task.deployment_name, self.api_key
        ) as sid:
            path = "/prompt" if self.variant is None else f"/{self.variant}/prompt"
            res = await request_retryable(
                self.client,
                "GET",
                path,
                expect_json=True,
                sid=sid,
                deployment=task.deployment_name,
                token=self.api_key,
            )
            return res

    def session(
        self,
        task: Optional[Task] = None,
        secrets: Optional[Mapping[str, Union[str, tuple[str, list[str]]]]] = None,
        *,
        split: Optional[str] = None,
        index: Optional[int] = None,
        toolset: Optional[BuiltinToolset] = None,
        env_overrides: Optional[Mapping[str, str]] = None,
    ) -> AsyncSession:
        """从 Task 对象或 split/index 创建一个会话。

        ``toolset`` 是内置工具集的名称（例如 ``"claude-code"``
        或 ``"codex"``）。会话会将该名称转发给服务器，服务器
        会实例化绑定到每个会话环境的工具集。绑定工具集上定义的
        工具会覆盖环境或其声明的工具集中任何同名工具。

        ``env_overrides`` 在会话创建时覆盖主容器上的环境变量。
        仅限环境所有者使用——非所有者会从后端收到 403。
        """
        toolset_name = _validate_toolset_name(toolset)
        return AsyncSession(
            self,
            task=task,
            secrets=secrets,
            api_key=self.api_key,
            split=split,
            index=index,
            toolset_name=toolset_name,
            env_overrides=env_overrides,
        )

    async def list_required_secrets(self) -> list[str]:
        """获取此环境所需的 secret 键列表。"""
        if self.api_client is None:
            raise RuntimeError(
                "API base URL not configured; cannot fetch required secrets"
            )
        owner = self.namespace or ""
        path = f"/v1/environments/{owner}/{self.name}/required-secrets"
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        async with self.api_client.get(path, headers=headers) as resp:
            await _raise_for_status(resp)
            data = await resp.json()
            return data["secrets"]


class AsyncEnvironmentsAPI:

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_base_url: Optional[str] = None,
    ):
        self.api_key = api_key

        self.base_url = base_url
        self.api_base_url = api_base_url
        self.timeout = aiohttp.ClientTimeout(total=None)

        # 延迟初始化 - 连接器需要运行中的事件循环
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._clients: dict[str, aiohttp.ClientSession] = {}

    def _get_connector(self) -> aiohttp.TCPConnector:
        """在运行中的事件循环内延迟创建连接器。"""
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(limit=1_000_000)
        return self._connector

    def get(
        self, name: str, variant: Optional[str] = None, base_url: Optional[str] = None
    ) -> AsyncEnvironment:

        parts = name.split("/", maxsplit=1)
        namespace = None
        if len(parts) == 1:
            env_name = parts[0]
        elif len(parts) == 2:
            namespace, env_name = parts
        else:
            raise RuntimeError("impossible")

        if namespace and self.api_key is None:
            raise ValueError(
                f"Expected api_key to be passed when accessing remote environment"
            )

        if base_url is None:
            base_url = self.base_url

        if base_url not in self._clients:
            self._clients[base_url] = aiohttp.ClientSession(
                base_url=base_url,
                timeout=self.timeout,
                connector=self._get_connector(),
                trust_env=True,
            )
        client = self._clients[base_url]

        api_client = None
        if self.api_base_url:
            if self.api_base_url not in self._clients:
                self._clients[self.api_base_url] = aiohttp.ClientSession(
                    base_url=self.api_base_url,
                    timeout=self.timeout,
                    connector=self._get_connector(),
                    trust_env=True,
                )
            api_client = self._clients[self.api_base_url]

        return AsyncEnvironment(
            namespace=namespace,
            name=env_name,
            variant=variant,
            client=client,
            api_key=self.api_key,
            api_client=api_client,
        )

    async def aclose(self) -> None:
        """关闭所有 aiohttp 会话和共享连接器。

        关闭后短暂休眠，让 aiohttp 的连接器完成其
        优雅关闭任务（``_wait_for_close``）。如果不这样做，当
        事件循环拆除时，该任务可能会处于挂起状态，在退出时产生
        ``ERROR Task was destroyed but it is pending!`` 日志。
        """
        for client in self._clients.values():
            if not client.closed:
                await client.close()
        self._clients.clear()
        if self._connector is not None and not self._connector.closed:
            await self._connector.close()
            self._connector = None
        # https://docs.aiohttp.org/en/stable/client_advanced.html#graceful-shutdown
        await asyncio.sleep(0.25)

    async def __aenter__(self) -> "AsyncEnvironmentsAPI":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


class Session:
    """AsyncSession 的同步包装器。"""

    def __init__(self, async_session: AsyncSession, loop: asyncio.AbstractEventLoop):
        self._async = async_session
        self._loop = loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    @property
    def sid(self) -> Optional[str]:
        return self._async.sid

    @property
    def task(self) -> Optional[Task]:
        return self._async.task

    def __enter__(self) -> "Session":
        self._run(self._async.__aenter__())
        return self

    def __exit__(self, *exc):
        self._run(self._async.__aexit__(*exc))

    def version(self) -> dict[str, Optional[str]]:
        return self._run(self._async.version())

    def get_prompt(self) -> list[Union[TextBlock, ImageBlock]]:
        return self._run(self._async.get_prompt())

    @overload
    def list_tools(self, format: None = None) -> list[ToolSpec]: ...

    @overload
    def list_tools(self, format: Provider) -> list[dict]: ...

    def list_tools(
        self, format: Optional[Provider] = None
    ) -> Union[list[ToolSpec], list[dict]]:
        return self._run(self._async.list_tools(format))

    def call_tool(self, tool_name: str, input: JSONObject = {}) -> ToolOutput:
        return self._run(self._async.call_tool(tool_name, input))


class Environment:
    """AsyncEnvironment 的同步包装器。"""

    def __init__(self, async_env: AsyncEnvironment, loop: asyncio.AbstractEventLoop):
        self._async = async_env
        self._loop = loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    @property
    def server(self) -> str:
        return self._async.server

    @property
    def namespace(self) -> Optional[str]:
        return self._async.namespace

    @property
    def name(self) -> str:
        return self._async.name

    @property
    def variant(self) -> Optional[str]:
        return self._async.variant

    @property
    def deployment_name(self) -> str:
        return self._async.deployment_name

    def list_splits(self) -> list[str]:
        return self._run(self._async.list_splits())

    def list_tasks(self, split: str) -> list[Task]:
        return self._run(self._async.list_tasks(split))

    def num_tasks(self, split: str) -> int:
        """获取指定 split 的任务数量。"""
        return self._run(self._async.num_tasks(split))

    def get_task(self, split: str, index: int) -> Task:
        """通过 split 和 index 获取单个任务。"""
        return self._run(self._async.get_task(split, index))

    def get_task_range(
        self, split: str, start: Optional[int] = None, stop: Optional[int] = None
    ) -> list[Task]:
        """获取 range(start, stop) 范围内的任务。支持负数和 None 索引。"""
        return self._run(self._async.get_task_range(split, start, stop))

    @overload
    def list_tools(self, format: None = None) -> list[ToolSpec]: ...

    @overload
    def list_tools(self, format: Provider) -> list[dict]: ...

    def list_tools(
        self, format: Optional[Provider] = None
    ) -> Union[list[ToolSpec], list[dict]]:
        return self._run(self._async.list_tools(format))

    def get_prompt(self, task: Task) -> str:
        return self._run(self._async.get_prompt(task))

    def session(
        self,
        task: Optional[Task] = None,
        secrets: Optional[Mapping[str, Union[str, tuple[str, list[str]]]]] = None,
        *,
        split: Optional[str] = None,
        index: Optional[int] = None,
        toolset: Optional[BuiltinToolset] = None,
        env_overrides: Optional[Mapping[str, str]] = None,
    ) -> Session:
        """从 Task 对象或 split/index 创建一个会话。

        有关 ``toolset`` 和 ``env_overrides`` 参数，请参见
        :meth:`AsyncEnvironment.session`。
        """
        async_session = self._async.session(
            task=task,
            secrets=secrets,
            split=split,
            index=index,
            toolset=toolset,
            env_overrides=env_overrides,
        )
        return Session(async_session, self._loop)

    def list_required_secrets(self) -> list[str]:
        """获取此环境所需的 secret 键列表。"""
        return self._run(self._async.list_required_secrets())


class EnvironmentsAPI:
    """AsyncEnvironmentsAPI 的同步包装器。

    事件循环在后台守护线程中运行，以便 ping
    任务在同步调用之间保持存活。
    """

    def __init__(self, base_url: str, api_key: str, api_base_url: Optional[str] = None):
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()
        self._async = AsyncEnvironmentsAPI(base_url, api_key, api_base_url=api_base_url)
        self._closed = False
        atexit.register(self._atexit_handler)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def get(
        self, name: str, variant: Optional[str] = None, base_url: Optional[str] = None
    ) -> Environment:
        async def _get():
            return self._async.get(name, variant, base_url)

        async_env = self._run(_get())
        return Environment(async_env, self._loop)

    def close(self):
        """清理资源。幂等操作。"""
        if self._closed:
            return
        self._closed = True
        if not self._loop.is_running():
            return
        try:
            self._run(self._async.aclose())
            self._run(self._loop.shutdown_asyncgens())
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        if not self._loop.is_closed():
            self._loop.close()

    def _atexit_handler(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "EnvironmentsAPI":
        return self

    def __exit__(self, *exc):
        self.close()

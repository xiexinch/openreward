import asyncio
import base64
import re
import threading
from pathlib import Path
from typing import Mapping, Optional, Union

import aiohttp
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt
from openreward.api._session.http import request_retryable, resumable_sse
from openreward.api._session.session import BaseAsyncSession, SessionTerminatedError
from openreward.api.sandboxes.secrets import build_secrets_header, augment_secrets_with_api_key
from openreward.api.sandboxes.types import PodTerminatedError, RunResult, SandboxSettings

def _is_unknown_task_id(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and "unknown task_id" in str(exc)

# ECMA-48 转义序列：CSI (Fe) 序列和单字符转义
_ANSI_ESCAPE_RE = re.compile(r'\x1b(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])')

# 要去除的控制字符：C0（除 \t \n \r 外）、DEL、C1
_CONTROL_CHAR_TABLE = {
    c: None
    for c in (*range(0x00, 0x09), 0x0b, 0x0c, *range(0x0e, 0x20), *range(0x7f, 0xa0))
}

def _sanitise_content(content: str) -> str:
    """清理字符串，使其适合 JSON 编码和 LLM 分词。"""
    content = content.encode("utf-8", "backslashreplace").decode("utf-8")
    content = _ANSI_ESCAPE_RE.sub("", content)
    content = content.translate(_CONTROL_CHAR_TABLE)
    return content

def _decode_output(output: str) -> str:
    """终端输出是 base64 编码的，因为它可以是任意二进制数据。"""
    return base64.b64decode(output.encode('utf-8')).decode('utf-8', 'surrogateescape').rstrip()


class AsyncSandboxesAPI(BaseAsyncSession):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        settings: SandboxSettings,
        creation_timeout: int = 60 * 30,
        secrets: Optional[Mapping[str, Union[str, tuple[str, list[str]]]]] = None,
        api_base_url: Optional[str] = None,
    ) -> None:
        secrets = augment_secrets_with_api_key(secrets, api_key, base_url, api_base_url)
        # 如果提供了 secrets，则构建 X-Secrets 请求头——仅在创建请求时发送
        creation_headers: Optional[dict[str, str]] = None
        if secrets:
            creation_headers = {"X-Secrets": build_secrets_header(secrets)}

        super().__init__(
            base_url=base_url,
            api_key=api_key,
            creation_endpoint="/create_sandbox",
            creation_payload=settings.model_dump(),
            creation_timeout=creation_timeout,
            creation_headers=creation_headers,
        )
        self.settings = settings

    def _ensure_started(self):
        if (self._external_client or self._own_client) is None:
            raise RuntimeError("Sandbox not started. Call start() or use as context manager.")

    async def run(
        self,
        cmd: str,
        timeout: Optional[float] = 300,
        max_bytes: Optional[int] = 50_000,
        sanitise: bool = True,
    ) -> RunResult:
        """在容器中运行命令。

        返回一个 :class:`RunResult`，支持向后兼容的
        二元组解包::

            output, return_code = await sandbox.run("ls")

        其他字段可作为属性访问::

            result = await sandbox.run("ls")
            if result.truncated:
                ...
            if result.timed_out:
                ...
        """
        self._ensure_alive()
        self._ensure_started()

        # 多请求一个字节，以便在输出恰好为 max_bytes 长时
        # 可以检测到截断而不会误报。
        fetch_bytes = (max_bytes + 1) if max_bytes is not None else None

        # 如果 exec-agent 重启（例如 OOM）并丢失了内存中的任务映射，则重试一次。
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_unknown_task_id),
            stop=stop_after_attempt(2),
            reraise=True,
        ):
            with attempt:
                run_coro = resumable_sse(
                    self.client,
                    "/run",
                    token=self.api_key,
                    json={
                        "cmd": cmd,
                        "timeout_s": timeout,
                        "max_bytes": fetch_bytes,
                        "shell": "/bin/bash",
                    },
                    sid=self.sid,
                    max_retries=5,
                )
                res = await self._run_or_die(run_coro)

        return_code = res["return_code"]
        output = _decode_output(res["output"])
        if sanitise:
            output = _sanitise_content(output)

        truncated = max_bytes is not None and len(output) > max_bytes
        if truncated:
            output = output[:max_bytes]

        # return_code 124 是标准的 Unix 超时退出码，
        # 当上下文截止时间超过时，exec-agent 会显式使用它。
        timed_out = timeout is not None and return_code == 124

        return RunResult(
            output=output,
            return_code=return_code,
            truncated=truncated,
            timed_out=timed_out,
            sanitised=sanitise,
        )

    async def check_run(
        self,
        cmd: str,
        timeout: Optional[float] = 300,
        max_bytes: Optional[int] = 50_000,
        sanitise: bool = True,
    ) -> str:
        """在容器中运行命令，如果失败则抛出错误。"""
        self._ensure_alive()
        result = await self.run(cmd, timeout=timeout, max_bytes=max_bytes, sanitise=sanitise)
        if result.return_code != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{result.output}")
        return result.output

    async def upload(self, local_path: Union[str, Path], container_path: str) -> None:
        """将单个文件从本地文件系统上传到容器。"""
        self._ensure_alive()
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        max_size = 10 * 1024 * 1024
        if local_path.stat().st_size > max_size:
            raise ValueError(f"File is too large: {local_path.stat().st_size} bytes > {max_size} bytes")

        file_content = local_path.read_bytes()
        encoded_content = base64.b64encode(file_content).decode('ascii')

        cmd = f"echo '{encoded_content}' | base64 -d > {container_path}"
        await self.check_run(cmd, max_bytes=max_size)

    async def download(self, container_path: str) -> bytes:
        """从容器下载单个文件。"""
        self._ensure_alive()
        cmd = f"base64 {container_path}"
        output = await self.check_run(cmd, max_bytes=None)

        try:
            file_content = base64.b64decode(output.encode('ascii'))
            return file_content
        except Exception as e:
            raise RuntimeError(f"Failed to decode and write file: {e}")

    async def start(self) -> None:
        await self.__aenter__()

    async def stop(self) -> None:
        await self.__aexit__(None, None, None)



class SandboxesAPI:
    """AsyncSandboxesAPI 的同步包装器。

    事件循环在后台守护线程中运行，以便 ping 任务
    在同步调用之间保持活跃（防止 Redis 中的会话 TTL 过期）。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        settings: SandboxSettings,
        creation_timeout: int = 60 * 30,
        secrets: Optional[Mapping[str, Union[str, tuple[str, list[str]]]]] = None,
        api_base_url: Optional[str] = None,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._loop_thread.start()
        self._base_url = base_url
        self._api_key = api_key
        self._settings = settings
        self._creation_timeout = creation_timeout
        self._secrets = secrets
        self._api_base_url = api_base_url
        self._async: Optional[AsyncSandboxesAPI] = None

    def _run(self, coro):
        """将协程提交到后台循环并阻塞直到完成。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    @property
    def sid(self) -> Optional[str]:
        return self._async.sid if self._async else None

    def run(
        self,
        cmd: str,
        timeout: Optional[float] = 300,
        max_bytes: Optional[int] = 50_000,
        sanitise: bool = True,
    ) -> RunResult:
        """在容器中运行命令。"""
        if self._async is None:
            raise RuntimeError("Sandbox not started. Call start() or use as context manager.")
        return self._run(
            self._async.run(cmd, timeout=timeout, max_bytes=max_bytes, sanitise=sanitise)
        )

    def check_run(
        self,
        cmd: str,
        timeout: Optional[float] = 300,
        max_bytes: Optional[int] = 50_000,
        sanitise: bool = True,
    ) -> str:
        """在容器中运行命令，如果失败则抛出错误。"""
        if self._async is None:
            raise RuntimeError("Sandbox not started. Call start() or use as context manager.")
        return self._run(
            self._async.check_run(cmd, timeout=timeout, max_bytes=max_bytes, sanitise=sanitise)
        )

    def upload(self, local_path: Union[str, Path], container_path: str) -> None:
        """将单个文件从本地文件系统上传到容器。"""
        if self._async is None:
            raise RuntimeError("Sandbox not started. Call start() or use as context manager.")
        self._run(
            self._async.upload(local_path, container_path)
        )

    def download(self, container_path: str) -> bytes:
        """从容器下载单个文件。"""
        if self._async is None:
            raise RuntimeError("Sandbox not started. Call start() or use as context manager.")
        return self._run(
            self._async.download(container_path)
        )

    def start(self) -> None:
        self._async = AsyncSandboxesAPI(
            base_url=self._base_url,
            api_key=self._api_key,
            settings=self._settings,
            creation_timeout=self._creation_timeout,
            secrets=self._secrets,
            api_base_url=self._api_base_url,
        )
        self._run(self._async.start())

    def stop(self) -> None:
        if self._async is not None:
            self._run(self._async.stop())

    def close(self):
        """清理资源。"""
        self.stop()
        self._run(self._loop.shutdown_asyncgens())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        self._loop.close()

    def __enter__(self) -> "SandboxesAPI":
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

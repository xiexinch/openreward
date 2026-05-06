"""统一 _session 模块的测试。"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openreward.api._session.http import (
    HeartbeatTimeoutError,
    MaxRetriesError,
    _parse_sse_events,
    request_retryable,
    resumable_sse,
)
from openreward.api._session.session import BaseAsyncSession, SessionTerminatedError


def _make_sse_bytes(events: list[tuple[str, str]]) -> bytes:
    """从 (event, data) 对构建原始 SSE 字节负载。"""
    lines = []
    for event, data in events:
        lines.append(f"event: {event}")
        lines.append(f"data: {data}")
        lines.append("")  # 空行终止事件
    return "\n".join(lines).encode()


class FakeContent:
    """模拟 aiohttp response.content 作为异步行迭代器。"""

    def __init__(self, raw: bytes):
        self._lines = raw.split(b"\n")

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0) + b"\n"


class FakeResponse:
    """用于 SSE 测试的最小化模拟 aiohttp 响应。"""

    def __init__(self, raw: bytes, status: int = 200):
        self.content = FakeContent(raw)
        self.status = status
        self.ok = status < 400
        self.headers = {}
        self.request_info = MagicMock()
        self.history = ()

    async def text(self):
        return ""

    async def json(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


@pytest.mark.asyncio
async def test_parse_sse_events():
    raw = _make_sse_bytes([
        ("task_id", "abc123"),
        ("chunk", '{"partial":'),
        ("end", '"done"}'),
    ])
    resp = FakeResponse(raw)
    events = []
    async for event, data in _parse_sse_events(resp):
        events.append((event, data))
    assert events == [
        ("task_id", "abc123"),
        ("chunk", '{"partial":'),
        ("end", '"done"}'),
    ]


@pytest.mark.asyncio
async def test_resumable_sse_empty_end_data():
    """SSE 中空的 'end' 数据应返回 None 而非崩溃。"""
    raw = _make_sse_bytes([
        ("task_id", "sid-001"),
        ("end", ""),
    ])

    call_count = 0
    def fake_post(path, **kwargs):
        nonlocal call_count
        call_count += 1
        return FakeResponse(raw)

    client = MagicMock()
    client.post = MagicMock(side_effect=fake_post)

    result = await resumable_sse(
        client, "/create_session", token="tok",
        max_retries=0, timeout=5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_resumable_sse_captures_task_id_via_on_event():
    """验证 on_event 回调接收 task_id 事件。"""
    raw = _make_sse_bytes([
        ("task_id", "sid-xyz"),
        ("end", '{"sid": "sid-xyz"}'),
    ])

    def fake_post(path, **kwargs):
        return FakeResponse(raw)

    client = MagicMock()
    client.post = MagicMock(side_effect=fake_post)

    captured = []
    result = await resumable_sse(
        client, "/create", token="tok",
        max_retries=0, timeout=5,
        on_event=lambda e, d: captured.append((e, d)),
    )
    assert result == {"sid": "sid-xyz"}
    assert ("task_id", "sid-xyz") in captured


@pytest.mark.asyncio
async def test_resumable_sse_retry_on_5xx():
    """验证在 5xx 服务器错误时重试。"""
    raw_ok = _make_sse_bytes([
        ("task_id", "s1"),
        ("end", '{"ok": true}'),
    ])

    attempt = 0
    def fake_post(path, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return FakeResponse(b"", status=500)
        return FakeResponse(raw_ok)

    client = MagicMock()
    client.post = MagicMock(side_effect=fake_post)

    result = await resumable_sse(
        client, "/test", token="tok",
        max_retries=3, timeout=10, backoff_base=0.01,
    )
    assert result == {"ok": True}
    assert attempt == 2


@pytest.mark.asyncio
async def test_resumable_sse_max_retries_exceeded():
    """验证在超过重试次数后抛出 MaxRetriesError。"""
    def fake_post(path, **kwargs):
        return FakeResponse(b"", status=502)

    client = MagicMock()
    client.post = MagicMock(side_effect=fake_post)

    with pytest.raises(MaxRetriesError):
        await resumable_sse(
            client, "/test", token="tok",
            max_retries=2, timeout=10, backoff_base=0.01,
        )


@pytest.mark.asyncio
async def test_session_terminated_error():
    err = SessionTerminatedError("pod gone", sid="abc")
    assert err.sid == "abc"
    assert err.reason == "pod gone"
    assert "abc" in str(err)


@pytest.mark.asyncio
async def test_base_session_lifecycle():
    """模拟 SSE 创建返回 SID，验证 ping 已启动且退出时调用了 /delete。"""
    raw = _make_sse_bytes([
        ("task_id", "sid-lifecycle"),
        ("end", '{"sid": "sid-lifecycle"}'),
    ])

    def fake_post(path, **kwargs):
        return FakeResponse(raw)

    client = MagicMock()
    client.post = MagicMock(side_effect=fake_post)
    client.closed = False
    client._base_url = "http://test"

    delete_calls = []
    original_request_retryable = request_retryable.__wrapped__

    async def mock_request(client, method, path, expect_json, token, **kw):
        if path == "/delete_session":
            delete_calls.append(kw.get("sid"))
            return None
        return None

    async def mock_run_ping(self_, **kw):
        await asyncio.sleep(1000)

    with patch("openreward.api._session.session.resumable_sse") as mock_sse, \
         patch("openreward.api._session.session.request_retryable", side_effect=mock_request), \
         patch.object(BaseAsyncSession, "_run_ping", mock_run_ping):

        async def sse_side_effect(*args, **kwargs):
            on_event = kwargs.get("on_event", lambda e, d: None)
            on_event("task_id", "sid-lifecycle")
            return {"sid": "sid-lifecycle"}
        mock_sse.side_effect = sse_side_effect

        session = BaseAsyncSession(
            base_url="http://test",
            api_key="key",
            creation_endpoint="/create_sandbox",
            creation_payload={"foo": "bar"},
            client=client,
        )

        async with session:
            assert session.sid == "sid-lifecycle"
            assert session._ping_task is not None

        assert session._ping_task is None
        assert "sid-lifecycle" in delete_calls


@pytest.mark.asyncio
async def test_run_or_die_cancels_on_death():
    """启动一个慢协程，标记会话死亡，验证抛出 SessionTerminatedError。"""
    from openreward.api._session.ping import ErrorResponse

    async def mock_run_ping(self_, **kw):
        await asyncio.sleep(1000)

    with patch("openreward.api._session.session.resumable_sse") as mock_sse, \
         patch.object(BaseAsyncSession, "_run_ping", mock_run_ping):

        async def sse_side_effect(*args, **kwargs):
            on_event = kwargs.get("on_event", lambda e, d: None)
            on_event("task_id", "sid-die")
            return {"sid": "sid-die"}
        mock_sse.side_effect = sse_side_effect

        client = MagicMock()
        client.closed = False
        client._base_url = "http://test"

        session = BaseAsyncSession(
            base_url="http://test",
            api_key="key",
            creation_endpoint="/create",
            creation_payload={},
            client=client,
        )

        with patch("openreward.api._session.session.request_retryable", new=AsyncMock(return_value=None)):
            await session.__aenter__()

        async def slow_task():
            await asyncio.sleep(100)

        # 短暂延迟后标记死亡
        async def kill_later():
            await asyncio.sleep(0.05)
            session._mark_dead(ErrorResponse(type="error", message="pod gone"))

        asyncio.create_task(kill_later())

        with pytest.raises(SessionTerminatedError):
            await session._run_or_die(slow_task())

        with patch("openreward.api._session.session.request_retryable", new=AsyncMock(return_value=None)):
            await session.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_sandbox_inherits_base():
    """验证 AsyncSandboxesAPI 使用 /create 端点并继承自 BaseAsyncSession。"""
    from openreward.api.sandboxes.client import AsyncSandboxesAPI
    from openreward.api.sandboxes.types import SandboxSettings

    settings = SandboxSettings(
        environment="test",
        image="python:3.10",
        machine_size="1:1",
    )

    api = AsyncSandboxesAPI(
        base_url="http://test",
        api_key="key",
        settings=settings,
    )

    assert isinstance(api, BaseAsyncSession)
    assert api._creation_endpoint == "/create_sandbox"
    assert api._creation_payload == settings.model_dump()
    api.sid = "test-sid"
    assert api.sid == "test-sid"

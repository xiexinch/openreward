import json
from unittest.mock import MagicMock

from openreward.models import (
    RolloutConfig,
    RolloutUpdateEvent,
    RunInfo,
    RolloutInfo,
)
from openreward.api.rollouts.background import _encode_event
from openreward.api.rollouts.rollout import RolloutAPI, Rollout


def _make_api(**kwargs) -> RolloutAPI:
    """使用存根后台工作进程创建 RolloutAPI。"""
    api = object.__new__(RolloutAPI)
    api.send_loop_config = MagicMock()
    api.shutdown_timeout = 10.0
    api.web_base_url = "https://example.com"
    api.run_info = kwargs.get("run_info", None)
    api._closed = False
    api._loop = MagicMock()
    api._in = MagicMock()
    api._thread = MagicMock()
    return api


class TestMergeRunInfo:
    def test_both_none(self):
        api = _make_api(run_info=None)
        assert api._merge_run_info(None) is None

    def test_only_client_level(self):
        client_info = RunInfo(model_name="llama", lr=1e-5)
        api = _make_api(run_info=client_info)
        result = api._merge_run_info(None)
        assert result is client_info

    def test_only_per_rollout(self):
        override = RunInfo(model_name="llama", checkpoint="step-500")
        api = _make_api(run_info=None)
        result = api._merge_run_info(override)
        assert result is override

    def test_merge_override_wins(self):
        client_info = RunInfo(model_name="llama", lr=1e-5, batch_size=32)
        override = RunInfo(model_name="llama", lr=3e-5, checkpoint="step-500")
        api = _make_api(run_info=client_info)
        result = api._merge_run_info(override)
        assert result is not None
        assert result.lr == 3e-5
        assert result.checkpoint == "step-500"
        assert result.batch_size == 32
        assert result.model_name == "llama"

    def test_merge_none_in_override_uses_client(self):
        """覆盖中值为 None 的字段应回退到客户端级别。"""
        client_info = RunInfo(model_name="llama", lr=1e-5, optimizer="adam")
        override = RunInfo(model_name="llama-v2")  # lr 和 optimizer 为 None
        api = _make_api(run_info=client_info)
        result = api._merge_run_info(override)
        assert result is not None
        assert result.model_name == "llama-v2"
        assert result.lr == 1e-5
        assert result.optimizer == "adam"


class TestEncodeEvent:
    def test_encode_rollout_update_event(self):
        event = RolloutUpdateEvent(
            eventId="abc-123",
            timestamp=1000,
            rollout_info={"task_index": 5, "temperature": 0.9},
        )
        encoded = _encode_event(event)
        parsed = json.loads(encoded)
        assert parsed["eventType"] == "rollout_update"
        assert parsed["eventId"] == "abc-123"
        assert parsed["rollout_info"]["task_index"] == 5
        assert parsed["rollout_info"]["temperature"] == 0.9

    def test_encode_filters_none_values(self):
        event = RolloutUpdateEvent(
            eventId="abc-123",
            timestamp=1000,
            rollout_info=None,
        )
        encoded = _encode_event(event)
        parsed = json.loads(encoded)
        assert "rollout_info" not in parsed
        assert parsed["eventType"] == "rollout_update"


class TestLogMessageWithRolloutInfo:
    def _make_rollout(self) -> tuple[Rollout, list]:
        """创建带有已捕获事件队列的 Rollout。"""
        queued_events: list = []
        config = RolloutConfig(run_name="test-run")
        rollout = object.__new__(Rollout)
        rollout.config = config
        rollout.event_id = "rollout-abc"
        rollout.logged_messages = 0
        rollout.print_messages = False
        rollout.environment_id = None

        mock_queue = MagicMock()
        mock_queue.put_nowait = lambda event: queued_events.append(event)
        mock_loop = MagicMock()
        mock_loop.call_soon_threadsafe = lambda fn, event: fn(event)

        rollout._loop = mock_loop
        rollout._in = mock_queue

        return rollout, queued_events

    def test_rollout_info_queues_update_event(self):
        rollout, queued = self._make_rollout()
        from openreward.api.rollouts.serializers.base import AssistantMessage
        rollout.log(
            AssistantMessage(content="hello"),
            rollout_info=RolloutInfo(task_index=3, temperature=0.7),
        )
        # 应入队：1 个 RolloutUpdateEvent + 1 个 LogMessageEvent
        assert len(queued) == 2
        update_event = queued[0]
        assert isinstance(update_event, RolloutUpdateEvent)
        assert update_event.eventId == "rollout-abc"
        assert update_event.rollout_info["task_index"] == 3
        assert update_event.rollout_info["temperature"] == 0.7

    def test_no_rollout_info_no_update_event(self):
        rollout, queued = self._make_rollout()
        from openreward.api.rollouts.serializers.base import AssistantMessage
        rollout.log(AssistantMessage(content="hello"))
        assert len(queued) == 1
        assert not isinstance(queued[0], RolloutUpdateEvent)

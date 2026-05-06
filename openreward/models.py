from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel

from openreward.api.rollouts.serializers.models import NormalizedType


TrainingStage = Literal["pretrained", "sft", "rl"]
RunType = Literal["train", "eval", "adhoc"]


@dataclass
class RunInfo:
    """运行级别上下文 —— 在一次训练运行的每个 rollout 中保持不变。"""
    model_name: str
    run_type: Optional[RunType] = None
    model_params: Optional[int] = None
    model_active_params: Optional[int] = None
    training_stage: Optional[TrainingStage] = None
    initial_step: Optional[int] = None
    checkpoint: Optional[str] = None
    peft_type: Optional[str] = None
    peft_rank: Optional[int] = None
    rl_algorithm: Optional[str] = None
    batch_size: Optional[int] = None
    lr: Optional[float] = None
    optimizer: Optional[str] = None
    framework: Optional[str] = None
    framework_version: Optional[str] = None


@dataclass
class RolloutInfo:
    task_index: int
    env_version: Optional[str] = None
    current_step: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    num_compactions: Optional[int] = None
    duration_ms: Optional[int] = None
    harness: Optional[str] = None


class RolloutConfig(BaseModel):
    run_name: str
    rollout_name: Optional[str] = None
    environment: Optional[str] = None
    variant: Optional[str] = None
    split: Optional[str] = None
    metadata: Optional[dict] = None
    task_spec: Optional[Dict[str, Any]] = None
    run_info: Optional[dict] = None


@dataclass
class Config:
    shutdown_timeout: float
    send_loop_config: "SendLoopConfig"


@dataclass
class SendLoopConfig:
    max_items: int # 最大项目阈值
    max_bytes: int # 最大字节阈值
    max_age: float # 最大年龄
    jitter: float # 刷新间隔的抖动百分比

    ring_capacity: int # 最大项目数，达到此值时环形缓冲区将被刷新

    max_batch_items: int # 每次刷新的最大项目数
    max_batch_bytes: int # 每次刷新的最大字节数

    max_retries: int # 最大重试次数
    backoff_base: float # 退避的基础时间
    backoff_factor: float # 退避的因子
    backoff_cap: float # 退避的时间上限

    max_upload_concurrency: int

    api_key: str
    base_url: str

@dataclass
class LogMessageEvent:
    # rollout 信息
    eventId: str
    timestamp: int
    index: int

    rolloutEventId: str

    # 归一化事件信息
    type: NormalizedType
    content: Optional[str] = None # 可见文本或工具的 JSON 字符串
    contentReference: Optional[str] = None # 仅用于隐藏推理
    summary: Optional[str] = None # 仅用于推理
    name: Optional[str] = None # 工具名称（仅限 tool_call）
    callId: Optional[str] = None # tool_call/result 的关联键

    # 额外信息
    environment_id: Optional[str] = None
    reward: Optional[float] = None
    isFinished: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None

    eventType: Literal["message"] = "message"


@dataclass # 是否希望此操作在后台进程上执行？
class RolloutStartedEvent:
    # rollout 信息
    eventId: str
    timestamp: int

    # rollout 信息
    runName: str
    step: Optional[int] = None
    environment: Optional[str] = None
    environment_id: Optional[str] = None
    rolloutName: Optional[str] = None
    variant: Optional[str] = None
    variant_id: Optional[str] = None
    split: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    task_spec: Optional[Dict[str, Any]] = None
    run_info: Optional[Dict[str, Any]] = None
    rollout_info: Optional[Dict[str, Any]] = None

    eventType: Literal["rollout"] = "rollout"

    @classmethod
    def from_config(cls, event_id: str, timestamp: int, config: RolloutConfig, step: Optional[int] = None, environment_id: Optional[str] = None, variant_id: Optional[str] = None):
        return cls(
            eventId=event_id,
            timestamp=timestamp,
            runName=config.run_name,
            rolloutName=config.rollout_name,
            environment=config.environment,
            environment_id=environment_id,
            variant=config.variant,
            variant_id=variant_id,
            split=config.split,
            metadata=config.metadata,
            task_spec=config.task_spec,
            run_info=config.run_info,
            step=step
        )

@dataclass
class RolloutUpdateEvent:
    """rollout 进行中的元数据更新（例如 rollout 结束时的 rollout_info）。"""
    eventId: str          # rollout 的 eventId
    timestamp: int
    rollout_info: Optional[Dict[str, Any]] = None
    eventType: Literal["rollout_update"] = "rollout_update"

@dataclass
class FlushEvent:
    event_type: Literal["flush"] = "flush"

@dataclass
class ShutdownEvent:
    event_type: Literal["shutdown"] = "shutdown"

InputEvent = Union[LogMessageEvent, RolloutStartedEvent, RolloutUpdateEvent, FlushEvent, ShutdownEvent]


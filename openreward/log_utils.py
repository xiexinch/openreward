import logging
import os
import structlog
import sys

from openreward._version import __version__ as _sdk_version


OPENREWARD_USE_STRUCTURED_LOGS = bool(os.getenv("OPENREWARD_USE_STRUCTURED_LOGS", False))

# 由 provisioner 在托管的 env-server pod 中运行时设置。
# 为每条结构化日志行添加标签，以便可以将日志追溯到确切的构建。
_openreward_build_sha = os.getenv("OPENREWARD_BUILD_SHA")


def _add_runtime_metadata(_, __, event_dict):
    event_dict["sdk_version"] = _sdk_version
    if _openreward_build_sha:
        event_dict["build_sha"] = _openreward_build_sha
    return event_dict


_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

_STRUCTURED_PROCESSORS = [*_SHARED_PROCESSORS, _add_runtime_metadata]

def _rename_for_gcp(_, method, event_dict):
    event_dict["message"] = event_dict.pop("event")
    event_dict["severity"] = event_dict.pop("level", method).upper()
    return event_dict


def _resolve_log_level() -> int:
    """从环境变量解析日志级别：OPENREWARD_LOG_LEVEL -> LOG_LEVEL -> INFO。"""
    raw = os.environ.get("OPENREWARD_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO"
    return getattr(logging, raw.upper(), logging.INFO)


def get_logger(name: str) -> structlog.BoundLogger:
    """返回一个作用域限定在 openreward 的 structlog 日志记录器，并带有实例级配置。

    这样可以避免污染全局的 ``structlog.configure()`` 命名空间，
    使得导入 SDK 的训练脚本不会在环境服务器的 ``setup_logging()`` 尚未调用时看到调试信息刷屏。
    """
    if OPENREWARD_USE_STRUCTURED_LOGS:
        processors = [*_STRUCTURED_PROCESSORS, _rename_for_gcp, structlog.processors.JSONRenderer()]
    else:
        processors = [*_SHARED_PROCESSORS, structlog.dev.ConsoleRenderer()]

    return structlog.wrap_logger(
        structlog.PrintLogger(),
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(_resolve_log_level()),
    ).bind(logger_name=name)


def setup_logging(level: int = logging.INFO):
    """为当前进程配置日志。

    当设置了 OPENREWARD_USE_STRUCTURED_LOGS 时使用 JSON 结构化日志，
    否则使用人类可读的控制台渲染器。
    """
    if OPENREWARD_USE_STRUCTURED_LOGS:
        final_processors = [*_STRUCTURED_PROCESSORS, _rename_for_gcp, structlog.processors.JSONRenderer()]
    else:
        final_processors = [*_SHARED_PROCESSORS, structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=final_processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    if OPENREWARD_USE_STRUCTURED_LOGS:
        # 生产环境：同时配置 stdlib 根日志记录器，
        # 以便第三方库的消息（uvicorn、aiohttp 等）也能通过 structlog 输出。
        formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=[*_STRUCTURED_PROCESSORS, structlog.stdlib.ExtraAdder()],
        )

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)

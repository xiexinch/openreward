from typing import Literal, Optional, Union

from pydantic import BaseModel, field_validator, model_validator

# 机器大小格式为 'cpu:memory'（例如，'1:2' = 1 CPU，2GB 内存）
MachineSize = Literal[
    '0.5:0.5',
    '1:1',
    '2:2',
    '4:4',
    '0.5:1',
    '1:2',
    '2:4',
    '4:8',
    '0.5:2',
    '1:4',
    '2:8',
    '4:16',
    'nvidia-l4'
]

class SandboxBucketConfig(BaseModel):
    mount_path: str
    """存储桶将在容器内部挂载的路径。"""

    read_only: Literal[True] = True
    """存储桶始终以只读模式挂载。"""

    only_dir: Optional[str] = None
    """如果设置，则仅从存储桶挂载指定目录。"""

    implicit_dirs: bool = True
    """如果为 True，则挂载存储桶的所有子目录。"""

class SandboxSidecarContainer(BaseModel):
    name: str
    """此 sidecar 容器的唯一名称。"""

    image: str
    """要运行的容器镜像。"""

    command: Optional[list[str]] = None
    """容器入口点的可选命令覆盖。"""

    args: Optional[list[str]] = None
    """传递给命令的可选参数。"""

    env: Optional[dict[str, str]] = None
    """此容器的环境变量。"""

    ports: Optional[list[int]] = None
    """此容器暴露的端口（信息性，用于探针）。"""

    @field_validator("name")
    def validate_name_not_reserved(cls, value: str) -> str:
        if value.lower() in {"main", "sidecar"}:
            raise ValueError("Sidecar container names 'main' and 'sidecar' are reserved.")
        return value


class SandboxHostAlias(BaseModel):
    """用于向 /etc/hosts 添加主机名别名的配置。"""

    ip: str
    """要映射主机名的 IP 地址，例如 '127.0.0.1'。"""

    hostnames: list[str]
    """要映射到该 IP 的主机名列表"""

# HACK: 硬编码的常见 API 密钥环境变量名称列表。我们同时发送大写和
# 小写版本，以防止常见的失败模式，即服务端代码期望一种大小写
# 但用户提供另一种。
_API_KEY_NAMES = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "TAVILY_API_KEY",
    "GOOGLE_API_KEY",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "REPLICATE_API_TOKEN",
    "HUGGINGFACE_API_KEY",
    "HF_TOKEN",
    "PERPLEXITY_API_KEY",
    "FIREWORKS_API_KEY",
    "DEEPSEEK_API_KEY",
    "KAGGLE_KEY",
    "KAGGLE_USERNAME",
    "E2B_API_KEY",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "DAYTONA_API_KEY",
})

class SandboxSettings(BaseModel):
    """容器的计算资源设置。"""

    environment: str
    """运行容器的 OpenReward 环境。"""

    image: str
    """要运行的容器镜像，例如 "python:3.10-slim"。"""

    machine_size: MachineSize
    """
    运行容器的机器大小。
    示例："0.5:1" = 0.5 CPU 和 1 GB 内存。
    """

    # TODO: 是否需要这些？
    # disk_request: Optional[str] = None
    # """请求的最小临时存储（如果使用 emptyDir 卷）。示例："1Gi" = 1 GB。"""

    # disk_limit: Optional[str] = None
    # """允许的最大临时存储；超过此值将导致驱逐。"""

    env: Optional[dict[str, str]] = None
    """容器的环境变量，例如 {"ENV": "prod"}。"""

    block_network: bool = False
    """如果为 True，则禁用出站网络访问（需要 Kubernetes 出口策略）。"""

    bucket_config: Optional[SandboxBucketConfig] = None
    """要挂载的存储桶列表；在运行时挂载到容器中。"""

    # labels: Optional[dict[str, str]] = None
    # """添加到运行中的计算机以用于归因的标签。"""

    sidecars: Optional[list[SandboxSidecarContainer]] = None
    """在同一 pod 中运行的附加容器。"""

    host_aliases: Optional[list[SandboxHostAlias]] = None
    """要添加到所有容器的 /etc/hosts 中的主机名别名。"""

    @model_validator(mode="after")
    def _duplicate_api_keys_lowercase(self) -> "SandboxSettings":
        """HACK: 将知名的 API 密钥环境变量复制为小写，以防止
        客户端和服务器之间命名不一致的常见故障点。"""
        if self.env is None:
            return self
        for key in _API_KEY_NAMES:
            if key in self.env:
                lower = key.lower()
                if lower not in self.env:
                    self.env[lower] = self.env[key]
        return self

class RunResult:
    """sandbox.run() 调用的结果。

    支持向后兼容的二元组解包::

        output, return_code = sandbox.run("ls")

    新字段可作为属性访问::

        result = sandbox.run("ls")
        if result.truncated:
            print("Output was cut short!")
        if result.timed_out:
            print("Command exceeded timeout!")
    """

    def __init__(
        self,
        output: str,
        return_code: int,
        truncated: bool = False,
        timed_out: bool = False,
        sanitised: bool = True,
    ) -> None:
        self.output = output
        self.return_code = return_code
        self.truncated = truncated
        self.timed_out = timed_out
        self.sanitised = sanitised

    def __iter__(self):
        """生成 (output, return_code) 以支持向后兼容的二元组解包。"""
        yield self.output
        yield self.return_code

    def __repr__(self) -> str:
        return (
            f"RunResult(return_code={self.return_code}, "
            f"truncated={self.truncated}, timed_out={self.timed_out}, "
            f"output={self.output[:50]!r}{'...' if len(self.output) > 50 else ''})"
        )


class PodTerminatedError(RuntimeError):
    def __init__(self, reason: str, *, sid: Optional[str]):
        super().__init__(f"Pod terminated (sid={sid!r}): {reason}")
        self.reason = reason
        self.sid = sid

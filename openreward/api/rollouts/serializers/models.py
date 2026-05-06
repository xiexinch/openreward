from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional


def _sanitise_content(content: str) -> str:
    """去除空字节并安全编码孤立代理对。"""
    content = content.replace("\x00", "")
    content = content.encode("utf-8", "backslashreplace").decode("utf-8")
    return content

NormalizedType = Literal[
    "reasoning",
    "tool_call",
    "tool_result",
    "user_message",
    "assistant_message",
    "system_message",
]

@dataclass(slots=True)
class NormalizedEvent:
    type: NormalizedType
    content: Optional[str] = None # 可见文本或工具的 JSON 字符串
    content_reference: Optional[str] = None # 仅用于隐藏推理
    summary: Optional[str] = None # 仅用于推理
    name: Optional[str] = None # 工具名称（仅 tool_call）
    call_id: Optional[str] = None # tool_call/result 的关联键

    def __post_init__(self) -> None:
        if self.content is not None:
            object.__setattr__(self, "content", _sanitise_content(self.content))
        if self.summary is not None:
            object.__setattr__(self, "summary", _sanitise_content(self.summary))
        t = self.type
        if self.content_reference is not None and t != "reasoning":
            raise ValueError("content_reference is only valid for type='reasoning'")
        if self.summary is not None and t != "reasoning":
            raise ValueError("summary is only valid for type='reasoning'")
        if t == "tool_call":
            if not self.name:
                raise ValueError("tool_call requires 'name'")
            if not (isinstance(self.content, str) and self.content.strip()):
                raise ValueError("tool_call requires 'content' (arguments JSON string)")
        if t == "tool_result":
            if not isinstance(self.content, str):
                raise ValueError("tool_result requires 'content' (output JSON/string)")
        if self.name is not None and t != "tool_call":
            raise ValueError("name is only valid for type='tool_call'")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


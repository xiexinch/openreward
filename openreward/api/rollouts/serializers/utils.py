import json
from typing import Any, List, Optional, Union

from .models import NormalizedEvent, NormalizedType


def get(obj: Any, key: str, default: Any = None) -> Any:
    """鸭子类型的属性/字典访问器。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def add_text_event(events: List[NormalizedEvent], role: str, text: Optional[str]) -> None:
    text = (text or "").strip()
    if not text:
        return

    role_to_type: dict[str, NormalizedType] = {
        "user": "user_message",
        "assistant": "assistant_message",
        "system": "system_message",
    }
    ev_type = role_to_type.get(role)
    if not ev_type:
        return  # 未知角色；忽略

    events.append(NormalizedEvent(type=ev_type, content=text))

def ensure_json_str(x: Any) -> str:
    """返回 JSON 字符串；字符串直接通过，字典/列表/对象进行转储。"""
    if isinstance(x, str):
        return x
    return json.dumps(x, ensure_ascii=False)


def stringify_if_needed(x: Any) -> Optional[str]:
    """返回 None、字符串原样或非字符串的 JSON 字符串。"""
    if x is None:
        return None
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)



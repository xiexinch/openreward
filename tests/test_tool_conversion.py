import pytest

from openreward import sanitize_tool_schema
from openreward.api.environments.client import convert_tool_response


def _walk(obj):
    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "array" or (isinstance(t, list) and "array" in t):
            assert "items" in obj, f"数组模式缺少 items: {obj}"
        for v in obj.values():
            _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v)


def test_openai_array_schemas_always_have_items():
    res = {
        "tools": [
            {
                "name": "search",
                "description": "Search things",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tags": {
                            "anyOf": [
                                {"type": "array"},
                                {"type": "null"},
                            ]
                        },
                        "ids": {"type": ["array", "null"]},
                        "filters": {
                            "type": "object",
                            "properties": {
                                "values": {"type": "array"},
                            },
                        },
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    assert len(converted) == 1
    _walk(converted[0]["parameters"])


def test_openrouter_array_schemas_always_have_items():
    res = {"tools": [{"name": "x", "input_schema": {"type": "array"}}]}
    converted = convert_tool_response(res, format="openrouter")
    _walk(converted[0]["parameters"])


def test_openai_anyOf_collapse_preserves_sibling_metadata():
    """Pydantic 的 Optional[str] 带有描述时会同时输出 anyOf 和
    description/default/title；这些兄弟属性必须在折叠后保留。"""
    res = {
        "tools": [
            {
                "name": "greet",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "description": "Person's name",
                            "default": None,
                            "title": "Name",
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    name = converted[0]["parameters"]["properties"]["name"]
    assert name["type"] == "string"
    assert name["description"] == "Person's name"
    assert name["default"] is None
    # title 被 _strip_titles 在整个模式上剥离
    assert "title" not in name


def test_openai_allOf_collapse_preserves_sibling_metadata():
    """Pydantic 对引用模型字段会同时输出 allOf=[{$ref: ...}] 和 description；
    描述必须保留。"""
    res = {
        "tools": [
            {
                "name": "upsert",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user": {
                            "allOf": [{"type": "object"}],
                            "description": "The user to upsert",
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    user = converted[0]["parameters"]["properties"]["user"]
    assert user["type"] == "object"
    assert user["description"] == "The user to upsert"


def test_openai_oneOf_collapse_preserves_sibling_metadata():
    res = {
        "tools": [
            {
                "name": "set",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "oneOf": [{"type": "integer"}, {"type": "string"}],
                            "description": "The value",
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    value = converted[0]["parameters"]["properties"]["value"]
    assert value["type"] == "integer"
    assert value["description"] == "The value"


def test_openai_anyOf_nested_option_with_array_gets_items():
    """非 null 的 anyOf 选项如果是数组，在折叠后仍应获得默认的 items。"""
    res = {
        "tools": [
            {
                "name": "tag",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tags": {
                            "anyOf": [{"type": "array"}, {"type": "null"}],
                            "description": "Optional tag list",
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    tags = converted[0]["parameters"]["properties"]["tags"]
    assert tags["type"] == "array"
    assert tags["items"] == {}
    assert tags["description"] == "Optional tag list"


def test_openai_anyOf_all_null_falls_back_to_first():
    res = {
        "tools": [
            {
                "name": "weird",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "x": {
                            "anyOf": [{"type": "null"}],
                            "description": "null only",
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    x = converted[0]["parameters"]["properties"]["x"]
    assert x["type"] == "null"
    assert x["description"] == "null only"


def test_openai_strips_not_and_additional_properties():
    res = {
        "tools": [
            {
                "name": "strict",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "patternProperties": {"^x": {"type": "string"}},
                    "properties": {
                        "x": {"type": "string", "not": {"const": "forbidden"}},
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    params = converted[0]["parameters"]
    assert "additionalProperties" not in params
    assert "patternProperties" not in params
    assert "not" not in params["properties"]["x"]


def test_openai_nested_anyOf_inside_option_is_also_collapsed():
    """外层保留的兄弟属性不应阻止对所选选项的递归折叠。"""
    res = {
        "tools": [
            {
                "name": "nested",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "description": "outer",
                            "anyOf": [
                                {
                                    "description": "inner",
                                    "anyOf": [
                                        {"type": "integer"},
                                        {"type": "null"},
                                    ],
                                },
                                {"type": "null"},
                            ],
                        }
                    },
                },
            }
        ]
    }

    converted = convert_tool_response(res, format="openai")
    field = converted[0]["parameters"]["properties"]["field"]
    assert field["type"] == "integer"
    # 内部选项在冲突时获胜；外部描述被覆盖。
    assert field["description"] == "inner"


def test_sanitize_tool_schema_openai_strips_unsupported_and_fixes_arrays():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tags": {
                "anyOf": [{"type": "array"}, {"type": "null"}],
                "description": "tags",
                "title": "Tags",
            },
        },
    }
    out = sanitize_tool_schema(schema, "openai")
    assert "additionalProperties" not in out
    assert "title" not in out["properties"]["tags"]
    tags = out["properties"]["tags"]
    assert tags["type"] == "array"
    assert tags["items"] == {}
    assert tags["description"] == "tags"


def test_sanitize_tool_schema_anthropic_strips_titles_only():
    schema = {
        "type": "object",
        "title": "Root",
        "additionalProperties": False,
        "properties": {"x": {"type": "string", "title": "X"}},
    }
    out = sanitize_tool_schema(schema, "anthropic")
    # Anthropic 接受 additionalProperties；仅剥离 titles。
    assert out["additionalProperties"] is False
    assert "title" not in out
    assert "title" not in out["properties"]["x"]


def test_sanitize_tool_schema_google_drops_unsupported_and_renames_refs():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "anyOf": [{"type": "object"}],
        "$ref": "#/defs/Foo",
        "$defs": {"Foo": {"type": "string"}},
        "properties": {"x": {"type": "string"}},
    }
    out = sanitize_tool_schema(schema, "google")
    assert "additionalProperties" not in out
    assert "anyOf" not in out
    assert "$ref" not in out and out["ref"] == "#/defs/Foo"
    assert "$defs" not in out and "Foo" in out["defs"]


def test_sanitize_tool_schema_openrouter_matches_openai():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"xs": {"type": "array"}},
    }
    assert sanitize_tool_schema(schema, "openrouter") == sanitize_tool_schema(schema, "openai")


def test_sanitize_tool_schema_none_returns_empty_dict():
    assert sanitize_tool_schema(None, "openai") == {}
    assert sanitize_tool_schema({}, "anthropic") == {}


def test_sanitize_tool_schema_invalid_provider_raises():
    with pytest.raises(ValueError):
        sanitize_tool_schema({"type": "object"}, "bogus")  # type: ignore[arg-type]


def test_sanitize_tool_schema_matches_convert_tool_response():
    """公共辅助函数必须产生与 convert_tool_response 内部使用的相同模式，
    以便桥接消费者不会偏离。"""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "n",
                "title": "Name",
            },
            "tags": {"type": "array"},
        },
    }
    res = {"tools": [{"name": "t", "description": "d", "input_schema": schema}]}

    for provider, key in [
        ("openai", "parameters"),
        ("openrouter", "parameters"),
        ("google", "parameters"),
        ("anthropic", "input_schema"),
    ]:
        converted = convert_tool_response(res, format=provider)
        assert converted[0][key] == sanitize_tool_schema(schema, provider)

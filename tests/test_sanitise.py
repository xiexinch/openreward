import json

import pytest

from openreward.api.rollouts.serializers.models import _sanitise_content

# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def is_json_serialisable(s: str) -> bool:
    try:
        json.dumps(s)
        return True
    except (ValueError, UnicodeEncodeError):
        return False


# ── 代理字符处理 ────────────────────────────────────────────────────────

class TestSurrogates:
    def test_lone_surrogate_becomes_backslash_escape(self):
        # \udcff 是字节 0xFF 的 surrogateescape 占位符
        s = "hello\udcffworld"
        result = _sanitise_content(s)
        assert "\udcff" not in result
        assert is_json_serialisable(result)

    def test_surrogate_pair_encoded_safely(self):
        s = "😀"  # 在 str 中不是正确的代理对 —— 两者都保留为代理字符
        result = _sanitise_content(s)
        assert is_json_serialisable(result)

    def test_no_surrogates_unchanged(self):
        s = "clean string"
        assert _sanitise_content(s) == s


# ── 空字节剥离 ──────────────────────────────────────────────────────

class TestNullBytes:
    def test_null_byte_stripped(self):
        result = _sanitise_content("before\x00after")
        assert "\x00" not in result
        assert result == "beforeafter"

    def test_multiple_nulls_stripped(self):
        result = _sanitise_content("\x00a\x00b\x00")
        assert result == "ab"


# ── 应保留的字符 ──────────────────────────────────────

class TestPreserved:
    def test_tab_preserved(self):
        s = "col1\tcol2"
        assert _sanitise_content(s) == s

    def test_newline_preserved(self):
        s = "line1\nline2"
        assert _sanitise_content(s) == s

    def test_carriage_return_preserved(self):
        s = "line1\r\nline2"
        assert _sanitise_content(s) == s

    def test_ansi_preserved(self):
        s = "\x1b[31mred text\x1b[0m"
        assert _sanitise_content(s) == s

    def test_bel_preserved(self):
        s = "alert\x07done"
        assert _sanitise_content(s) == s


# ── JSON 可序列性（核心保证） ─────────────────────────────────

class TestJsonSerialisable:
    @pytest.mark.parametrize("raw", [
        "\x1b[31mred\x1b[0m",
        "hello\udcffworld",
        "\x00\x01\x02",
        "\x1b[38;5;200m\udcfe some output \x07",
        "perfectly normal string",
        "unicode: café, 日本語, emoji: 😀",
        "\t\n\r preserved",
    ])
    def test_output_is_always_json_serialisable(self, raw):
        assert is_json_serialisable(_sanitise_content(raw))


# ── 组合 / 真实输入 ───────────────────────────────────────────────

class TestRealistic:
    def test_mixed_ansi_and_null(self):
        s = "\x1b[32mOK\x1b[0m\x07 done\x00"
        result = _sanitise_content(s)
        assert result == "\x1b[32mOK\x1b[0m\x07 done"
        assert is_json_serialisable(result)

    def test_binary_garbage_from_surrogateescape(self):
        # 模拟使用 surrogateescape 读取二进制文件
        raw_bytes = bytes(range(0x80, 0xA0))
        s = raw_bytes.decode("utf-8", "surrogateescape")
        result = _sanitise_content(s)
        assert is_json_serialisable(result)

    def test_empty_string(self):
        assert _sanitise_content("") == ""

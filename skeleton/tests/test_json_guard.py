"""JSON 容错层测试：模型返回的脏数据，全部要能接住。"""

import json

import pytest

from gateway.json_guard import (
    JsonGuardError,
    dumps_json,
    find_first_json_object,
    loads_object,
    loads_object_or_none,
    repair_json_text,
    strip_code_fence,
)


# --------------------------------------------------------------------------- #
# 第 1 级：剥围栏
# --------------------------------------------------------------------------- #
def test_strip_code_fence_plain_text_kept():
    assert strip_code_fence('{"option": "rock"}') == '{"option": "rock"}'


def test_strip_code_fence_with_language_tag():
    assert strip_code_fence('```json\n{"option": "rock"}\n```') == '{"option": "rock"}'
    assert strip_code_fence('```JSON\n{"option": "rock"}\n```') == '{"option": "rock"}'


def test_strip_code_fence_without_language_tag():
    assert strip_code_fence('```\n{"option": "rock"}\n```') == '{"option": "rock"}'


def test_strip_code_fence_in_the_middle_of_text():
    assert strip_code_fence('好的：```json\n{"option": "rock"}\n``` 完毕') == '{"option": "rock"}'


def test_strip_code_fence_unterminated():
    assert strip_code_fence('```json\n{"option": "rock"') == '{"option": "rock"'


# --------------------------------------------------------------------------- #
# 第 2 级：括号配对
# --------------------------------------------------------------------------- #
def test_find_first_json_object_simple():
    assert find_first_json_object('xx {"a": 1} yy') == '{"a": 1}'


def test_find_first_json_object_nested():
    assert find_first_json_object('{"a": {"b": [1, 2]}} tail') == '{"a": {"b": [1, 2]}}'


def test_find_first_json_object_ignores_braces_inside_strings():
    text = '{"reason": "a } b { c", "option": "rock"}'
    assert find_first_json_object(text) == text


def test_find_first_json_object_handles_escaped_quote():
    text = r'{"reason": "say \"hi\" }", "option": "rock"}'
    assert find_first_json_object(text) == text


def test_find_first_json_object_skips_unbalanced_prefix():
    assert find_first_json_object('}{ "option": "rock" }') == '{ "option": "rock" }'


def test_find_first_json_object_returns_none():
    assert find_first_json_object("这里没有对象") is None
    assert find_first_json_object('{"option": "rock"') is None


# --------------------------------------------------------------------------- #
# 第 3 级：文本修复
# --------------------------------------------------------------------------- #
def test_repair_trailing_comma():
    assert json.loads(repair_json_text('{"option": "rock",}')) == {"option": "rock"}
    assert json.loads(repair_json_text('{"a": [1, 2,],}')) == {"a": [1, 2]}


def test_repair_python_literals():
    assert json.loads(repair_json_text('{"a": None, "b": True, "c": False}')) == {
        "a": None,
        "b": True,
        "c": False,
    }


def test_repair_single_quotes():
    assert json.loads(repair_json_text("{'option': 'rock'}")) == {"option": "rock"}


def test_repair_smart_quotes_outside_string():
    assert json.loads(repair_json_text('{\u201coption\u201d: \u201crock\u201d}')) == {"option": "rock"}


def test_repair_keeps_smart_quotes_inside_value():
    repaired = repair_json_text('{"a": "他说\u201c好\u201d"}')
    assert json.loads(repaired)["a"] == "他说\u201c好\u201d"


def test_repair_raw_newline_inside_string():
    assert json.loads(repair_json_text('{"a": "第一行\n第二行"}'))["a"] == "第一行\n第二行"


def test_repair_strips_comments():
    assert json.loads(repair_json_text('{\n// 注释\n"option": "rock" /* 块注释 */\n}')) == {"option": "rock"}


def test_repair_keeps_double_slash_inside_string():
    assert json.loads(repair_json_text('{"a": "https://example.com/x"}'))["a"] == "https://example.com/x"


def test_repair_closes_unfinished_object():
    assert json.loads(repair_json_text('{"a": {"b": "c"')) == {"a": {"b": "c"}}


# --------------------------------------------------------------------------- #
# 组合：loads_object
# --------------------------------------------------------------------------- #
def test_loads_object_direct():
    assert loads_object('{"option": "rock"}') == {"option": "rock"}


def test_loads_object_passthrough_dict():
    payload = {"option": "rock"}
    assert loads_object(payload) is payload


def test_loads_object_from_full_mess():
    text = '```json\n{\n  "time": "2026-01-01T00:00:00+00:00",  // 本轮\n  "option": \'scissor\',\n}\n```'
    assert loads_object(text) == {"time": "2026-01-01T00:00:00+00:00", "option": "scissor"}


def test_loads_object_takes_first_object_from_array():
    assert loads_object('[{"option": "rock"}, {"option": "paper"}]') == {"option": "rock"}


def test_loads_object_garbage_raises():
    for bad in ("抱歉，我无法回答。", "", "   ", None, 42, "[1, 2, 3]"):
        with pytest.raises(JsonGuardError):
            loads_object(bad)


def test_loads_object_or_none():
    assert loads_object_or_none("不是 JSON") is None
    assert loads_object_or_none('{"option": "rock"}') == {"option": "rock"}


def test_dumps_json_is_compact_and_utf8():
    assert dumps_json({"time": "t", "option": "rock"}) == '{"time":"t","option":"rock"}'
    assert dumps_json({"a": "中文"}) == '{"a":"中文"}'

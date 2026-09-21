"""决策字段解析、规范化、兜底与裁判逻辑测试。"""

import pytest

from gateway.rules import (
    ALIASES,
    BEATS,
    OPTIONS,
    build_decision,
    counter_to,
    fallback_decision,
    get_time,
    history_of,
    is_valid_decision,
    judge,
    normalize_option,
    normalize_time,
    now_iso,
    parse_request,
    validate_decision,
)


# --------------------------------------------------------------------------- #
# 读：输入解析
# --------------------------------------------------------------------------- #
def test_normalize_time_string_and_number():
    assert normalize_time("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"
    assert normalize_time("  2026-01-01  ") == "2026-01-01"
    assert normalize_time(0) == "1970-01-01T00:00:00+00:00"
    assert normalize_time(1767225600).startswith("2026-01-01")


def test_normalize_time_rejects_bad_values():
    assert normalize_time(None) is None
    assert normalize_time(True) is None  # bool 是 int 的子类，必须单独挡掉
    assert normalize_time("") is None
    assert normalize_time("  ") is None
    assert normalize_time("x" * 65) is None
    assert normalize_time([1]) is None


def test_now_iso_is_string():
    assert isinstance(now_iso(), str) and len(now_iso()) > 10


def test_parse_request_accepts_dict_and_json_text():
    assert parse_request({"time": "t"}) == {"time": "t"}
    assert parse_request('{"time": "t"}') == {"time": "t"}


def test_parse_request_rejects_bad_input():
    for bad in ("", "   ", "不是 JSON", "[1, 2]", 42, None):
        with pytest.raises(ValueError):
            parse_request(bad)


def test_get_time_and_history_of():
    request = {"time": "t", "history": ["rock", "PAPER", "不是出拳", "scissors", 7]}
    assert get_time(request) == "t"
    assert history_of(request) == ["rock", "paper", "scissor"]
    assert history_of({"history": "rock"}) == []
    assert history_of({}) == []
    assert get_time({}) is None


def test_history_is_capped():
    assert history_of({"history": ["rock"] * 50}) == ["rock"] * 10


# --------------------------------------------------------------------------- #
# 整理：枚举归一化
# --------------------------------------------------------------------------- #
def test_normalize_option_accepts_standard_values():
    for option in OPTIONS:
        assert normalize_option(option) == option


def test_normalize_option_cleans_dirty_values():
    assert normalize_option("  ROCK ") == "rock"
    assert normalize_option("Scissor") == "scissor"
    assert normalize_option("scissors") == "scissor"
    assert normalize_option("石头") == "rock"
    assert normalize_option("剪刀") == "scissor"
    assert normalize_option("布") == "paper"


def test_normalize_option_rejects_unknown():
    for bad in ("spock", "", None, 42, ["rock"], "水"):
        assert normalize_option(bad) is None


def test_aliases_point_to_valid_options():
    assert set(ALIASES.values()) <= set(OPTIONS)


# --------------------------------------------------------------------------- #
# 出口闸门
# --------------------------------------------------------------------------- #
def test_build_decision_has_exactly_two_keys():
    decision = build_decision("t", "rock")
    assert decision == {"time": "t", "option": "rock"}
    assert set(decision) == {"time", "option"}


def test_is_valid_decision():
    assert is_valid_decision({"time": "t", "option": "rock"})
    assert not is_valid_decision({"time": "t", "option": "spock"})
    assert not is_valid_decision({"time": "t"})
    assert not is_valid_decision({"time": "t", "option": "rock", "extra": 1})
    assert not is_valid_decision({"time": "", "option": "rock"})
    assert not is_valid_decision({"time": 1, "option": "rock"})
    assert not is_valid_decision("rock")


# --------------------------------------------------------------------------- #
# 校验与兜底
# --------------------------------------------------------------------------- #
def test_validate_decision_accepts_clean_answer():
    decision, notes = validate_decision({"time": "t", "option": "rock"}, {"time": "t"})
    assert decision == {"time": "t", "option": "rock"}
    assert notes == []


def test_validate_decision_normalizes_option():
    decision, _ = validate_decision({"option": "SCISSORS"}, {"time": "t"})
    assert decision == {"time": "t", "option": "scissor"}


def test_validate_decision_keeps_strict_two_keys():
    decision, _ = validate_decision(
        {"time": "t", "option": "rock", "reason": "因为", "history": [1]}, {"time": "t"}
    )
    assert set(decision) == {"time", "option"}


def test_validate_decision_request_time_wins():
    decision, notes = validate_decision({"time": "1999-01-01T00:00:00+00:00", "option": "rock"}, {"time": "2026"})
    assert decision["time"] == "2026"
    assert "丢弃模型的 time" in notes


def test_validate_decision_uses_model_time_when_request_lacks_it():
    decision, notes = validate_decision({"time": "2026-01-01T00:00:00+00:00", "option": "rock"}, {})
    assert decision["time"] == "2026-01-01T00:00:00+00:00"
    assert "请求无 time->用模型的 time" in notes


def test_validate_decision_falls_back_on_bad_option():
    for bad in ({"option": "spock"}, {"option": None}, {}, "rock", None, [1]):
        decision, notes = validate_decision(bad, {"time": "t", "history": ["rock"]})
        assert is_valid_decision(decision)
        assert decision["time"] == "t"
        assert any("兜底" in note for note in notes)


def test_fallback_decision_counters_last_history():
    decision, notes = fallback_decision({"time": "t", "history": ["rock", "paper"]})
    assert decision == {"time": "t", "option": "scissor"}  # 剪刀赢布
    assert any("counter" in note for note in notes)


def test_fallback_decision_defaults_to_rock():
    decision, notes = fallback_decision({"time": "t"})
    assert decision == {"time": "t", "option": "rock"}
    assert any("rock" in note for note in notes)


def test_fallback_decision_fills_missing_time():
    decision, _ = fallback_decision({})
    assert normalize_time(decision["time"]) is not None


# --------------------------------------------------------------------------- #
# 裁判
# --------------------------------------------------------------------------- #
def test_judge_full_table():
    assert judge("rock", "scissor") == 1
    assert judge("scissor", "paper") == 1
    assert judge("paper", "rock") == 1
    assert judge("scissor", "rock") == -1
    assert judge("paper", "scissor") == -1
    assert judge("rock", "paper") == -1
    for option in OPTIONS:
        assert judge(option, option) == 0


def test_counter_to_beats_input():
    for option in OPTIONS:
        assert BEATS[counter_to(option)] == option
    with pytest.raises(ValueError):
        counter_to("spock")

"""对局逻辑测试：本地对手策略、记分与裁判。"""

import json
import random

import pytest

from gateway.game import STRATEGIES, MatchResult, Opponent, play_rounds
from gateway.rules import OPTIONS, counter_to, judge


# --------------------------------------------------------------------------- #
# 本地对手
# --------------------------------------------------------------------------- #
def test_opponent_cycle_is_deterministic():
    opponent = Opponent("cycle")
    own = []
    picks = []
    for _ in range(4):
        pick = opponent.choose([], own)
        picks.append(pick)
        own.append(pick)
    assert picks == [OPTIONS[0], OPTIONS[1], OPTIONS[2], OPTIONS[0]]


def test_opponent_counter_counters_model_last_move():
    opponent = Opponent("counter")
    assert opponent.choose(["rock"], []) == counter_to("rock")
    assert opponent.choose(["paper"], []) == counter_to("paper")
    assert opponent.choose([], []) in OPTIONS


def test_opponent_copy_repeats_model():
    opponent = Opponent("copy")
    assert opponent.choose(["scissor"], []) == "scissor"
    assert opponent.choose([], []) in OPTIONS


def test_opponent_random_is_seeded_and_valid():
    first = [Opponent("random", random.Random(7)).choose([], []) for _ in range(5)]
    second = [Opponent("random", random.Random(7)).choose([], []) for _ in range(5)]
    assert first == second
    assert set(first) <= set(OPTIONS)


def test_opponent_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        Opponent("mind-reader")
    assert "random" in STRATEGIES


# --------------------------------------------------------------------------- #
# 对局
# --------------------------------------------------------------------------- #
class OfflineGateway:
    """不联网的假网关：始终出 rock。"""

    def __init__(self, option="rock"):
        self.option = option
        self.seen_requests = []

    def handle_line(self, line):
        request = json.loads(line)
        self.seen_requests.append(request)
        return {"time": request["time"], "option": self.option}, []


def test_play_rounds_returns_one_round_per_request():
    gateway = OfflineGateway()
    result = play_rounds(gateway, rounds=5, strategy="random", seed=1)
    assert isinstance(result, MatchResult)
    assert len(result.rounds) == 5
    assert result.wins + result.draws + result.losses == 5
    assert all(item.model_option in OPTIONS and item.opponent_option in OPTIONS for item in result.rounds)


def test_play_rounds_sends_time_and_history():
    gateway = OfflineGateway()
    play_rounds(gateway, rounds=3, strategy="cycle", seed=1)
    for index, request in enumerate(gateway.seen_requests):
        assert request["time"]
        assert isinstance(request["history"], list)
        assert len(request["history"]) == index  # 第 1 轮没有历史，之后逐轮累积
        assert set(request["history"]) <= set(OPTIONS)


def test_play_rounds_against_counter_strategy_never_wins():
    gateway = OfflineGateway("rock")
    result = play_rounds(gateway, rounds=4, strategy="counter", seed=1)
    # 对手一旦有历史就出布（布赢石头）；第 1 轮没有历史，可能随机到石头打平
    assert result.wins == 0
    assert result.losses >= 3


def test_play_rounds_summary_and_fallback_counter():
    gateway = OfflineGateway()
    result = play_rounds(gateway, rounds=2, strategy="random", seed=3)
    assert "共 2 轮" in result.summary()
    assert result.fallback_rounds == 0


def test_rounds_are_judged_consistently():
    gateway = OfflineGateway("scissor")
    result = play_rounds(gateway, rounds=3, strategy="cycle")
    for item in result.rounds:
        assert item.result == judge(item.model_option, item.opponent_option)

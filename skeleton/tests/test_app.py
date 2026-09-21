"""网关编排测试：一行进一行出、任何异常都要有合法输出、真实 HTTP 链路。

这里的两个「上游」都是本地的：``ScriptedClient``（直接返回预设文本）和
``MockLlmServer``（真的走 HTTP，但返回的是预设的坏回复）。
"""

import io
import json

from gateway.app import JsonGateway
from gateway.client import LlmClient, LlmError
from gateway.config import GatewayConfig
from gateway.rules import OPTIONS, is_valid_decision, normalize_time
from mock_llm_server import MockLlmServer


class ScriptedClient:
    """按顺序返回预设文本（或抛异常）的假客户端。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def decide(self, request):
        self.calls.append(request)
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply, {"attempt": 1, "http_status": 200, "latency_s": 0.0, "finish_reason": "stop",
                       "has_reasoning": False}

    def close(self):
        pass


def quiet_gateway(client=None, **config_overrides):
    config = GatewayConfig(offline=client is None, **config_overrides)
    return JsonGateway(config, client=client, log=lambda _message: None)


# --------------------------------------------------------------------------- #
# 基本编排
# --------------------------------------------------------------------------- #
def test_handle_line_offline_always_returns_valid_decision():
    gateway = quiet_gateway()
    for line in ('{"time": "t1"}', '{"time": "t2", "history": ["rock"]}', "垃圾输入", "{}"):
        decision, _notes = gateway.handle_line(line)
        assert is_valid_decision(decision)
        assert decision["option"] in OPTIONS


def test_handle_line_bad_input_uses_current_time():
    gateway = quiet_gateway()
    decision, notes = gateway.handle_line("这不是 JSON")
    assert is_valid_decision(decision)
    assert normalize_time(decision["time"]) is not None
    assert any("输入不是合法 JSON" in note for note in notes)


def test_handle_request_uses_model_answer():
    client = ScriptedClient(['{"time": "t", "option": "scissor"}'])
    decision, notes = quiet_gateway(client).handle_request({"time": "t"})
    assert decision == {"time": "t", "option": "scissor"}
    assert notes == []


def test_handle_request_calls_model_with_request():
    client = ScriptedClient(['{"option": "rock"}'])
    quiet_gateway(client).handle_request({"time": "t", "history": ["paper"]})
    assert client.calls == [{"time": "t", "history": ["paper"]}]


def test_handle_request_strips_extra_keys_from_model():
    client = ScriptedClient(['{"time": "t", "option": "rock", "reason": "因为", "confidence": 0.9}'])
    decision, _ = quiet_gateway(client).handle_request({"time": "t"})
    assert set(decision) == {"time", "option"}


def test_handle_request_request_time_wins():
    client = ScriptedClient(['{"time": "1999-01-01T00:00:00+00:00", "option": "rock"}'])
    decision, notes = quiet_gateway(client).handle_request({"time": "2026-01-01T00:00:00+00:00"})
    assert decision["time"] == "2026-01-01T00:00:00+00:00"
    assert any("丢弃模型的 time" in note for note in notes)


def test_handle_request_falls_back_on_upstream_error():
    client = ScriptedClient([LlmError("上游 500")])
    decision, notes = quiet_gateway(client).handle_request({"time": "t", "history": ["rock"]})
    assert is_valid_decision(decision)
    assert decision == {"time": "t", "option": "paper"}  # 布赢石头
    assert any("兜底" in note for note in notes)


def test_handle_request_falls_back_on_garbage_content():
    client = ScriptedClient(["抱歉，我无法回答。"])
    decision, notes = quiet_gateway(client).handle_request({"time": "t"})
    assert is_valid_decision(decision)
    assert any("兜底" in note for note in notes)


def test_handle_request_falls_back_on_invalid_option():
    client = ScriptedClient(['{"time": "t", "option": "spock"}'])
    decision, notes = quiet_gateway(client).handle_request({"time": "t"})
    assert is_valid_decision(decision)
    assert any("兜底" in note for note in notes)


def test_handle_request_survives_unexpected_exception():
    class Exploding:
        def decide(self, request):
            raise RuntimeError("想都想不到的错")

    decision, notes = quiet_gateway(Exploding()).handle_request({"time": "t"})
    assert is_valid_decision(decision)
    assert any("上游异常" in note for note in notes)


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def test_run_writes_one_valid_line_per_input():
    gateway = quiet_gateway()
    lines = ['{"time": "t1"}', "", "坏输入", '{"time": "t2", "history": ["scissor"]}']
    writer = io.StringIO()
    stats = gateway.run(lines, writer)

    output = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(output) == 3  # 空行不算请求
    assert all(is_valid_decision(item) for item in output)
    assert stats.lines == 3
    assert stats.bad_input == 1
    assert stats.fallback == 3  # 离线模式全部走兜底


def test_run_counts_ok_and_fallback():
    client = ScriptedClient(['{"time": "t1", "option": "rock"}', "垃圾"])
    gateway = quiet_gateway(client)
    writer = io.StringIO()
    stats = gateway.run(['{"time": "t1"}', '{"time": "t2"}'], writer)
    assert stats.ok == 1
    assert stats.fallback == 1
    decisions = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert decisions[0] == {"time": "t1", "option": "rock"}
    assert decisions[1]["time"] == "t2"
    assert decisions[1]["option"] in OPTIONS


# --------------------------------------------------------------------------- #
# 端到端：真 HTTP + 预设坏回复
# --------------------------------------------------------------------------- #
def test_end_to_end_against_mock_upstream():
    requests = ['{"time": "2026-01-01T00:00:%02d+00:00", "history": ["rock"]}' % index for index in range(12)]
    requests.append("最后一行不是 JSON")

    with MockLlmServer() as server:
        config = GatewayConfig(
            api_base_url=server.base_url,
            api_key="test-key",
            max_retries=1,
            backoff_base=0.0,
        )
        client = LlmClient(config, sleep=lambda _seconds: None)
        gateway = JsonGateway(config, client=client, log=lambda _message: None)
        writer = io.StringIO()
        stats = gateway.run(requests, writer)

        decisions = [json.loads(line) for line in writer.getvalue().splitlines()]
        assert len(decisions) == len(requests)  # 一行进、一行出
        assert stats.lines == len(requests)
        assert stats.bad_input == 1

        # 每一条都是严格合法的决策，且 time 与请求对齐（除了坏输入那行）
        for index, decision in enumerate(decisions[:-1]):
            assert is_valid_decision(decision)
            assert decision["time"] == "2026-01-01T00:00:%02d+00:00" % index
            assert decision["option"] in OPTIONS

        # 假上游的 reasoning_content 里写死的是 rock；只要网关误用思维链，这里必然全是 rock
        options = {decision["option"] for decision in decisions}
        assert options != {"rock"}
        assert "scissor" in options or "paper" in options

        # 请求侧：鉴权头、JSON 模式、思考开关都要对
        assert server.requests
        assert all(item["auth"] == "Bearer test-key" for item in server.requests)
        assert all(item["response_format"] == {"type": "json_object"} for item in server.requests)
        assert all(item["thinking"] == {"type": "disabled"} for item in server.requests)
        assert all("json" in item["system_prompt"].lower() for item in server.requests)

        # 空回复那条（第 10 条）应该触发了一次重试，因此请求数多于结果数
        assert len(server.requests) >= len(requests)

        client.close()


def test_end_to_end_handles_empty_reply_by_retrying():
    with MockLlmServer(scenarios=["", '{"time": "{time}", "option": "paper"}']) as server:
        config = GatewayConfig(api_base_url=server.base_url, api_key="k", max_retries=1, backoff_base=0.0)
        client = LlmClient(config, sleep=lambda _seconds: None)
        gateway = JsonGateway(config, client=client, log=lambda _message: None)
        decision, notes = gateway.handle_line('{"time": "t"}')
        assert decision == {"time": "t", "option": "paper"}  # 第一次空回复，重试后成功
        assert notes == []
        assert len(server.requests) == 2
        client.close()


def test_end_to_end_all_bad_replies_fall_back():
    with MockLlmServer(scenarios=["垃圾", "还是垃圾"]) as server:
        config = GatewayConfig(api_base_url=server.base_url, api_key="k", max_retries=0, backoff_base=0.0)
        client = LlmClient(config, sleep=lambda _seconds: None)
        gateway = JsonGateway(config, client=client, log=lambda _message: None)
        writer = io.StringIO()
        stats = gateway.run(['{"time": "t"}'], writer)
        assert stats.fallback == 1
        assert json.loads(writer.getvalue()) == {"time": "t", "option": "rock"}
        client.close()


def test_unreachable_upstream_falls_back():
    config = GatewayConfig(api_base_url="http://127.0.0.1:9", api_key="k", max_retries=0, connect_timeout=0.5)
    client = LlmClient(config, sleep=lambda _seconds: None)
    gateway = JsonGateway(config, client=client, log=lambda _message: None)
    decision, notes = gateway.handle_line('{"time": "t", "history": ["scissor"]}')
    assert is_valid_decision(decision)
    assert decision == {"time": "t", "option": "rock"}  # 石头赢剪刀
    assert any("兜底" in note for note in notes)
    client.close()

"""上游转发层测试：请求构造、重试策略、只取 content（全部不联网）。"""

import json

import pytest
import requests

from gateway.client import LlmAuthError, LlmClient, LlmError, LlmFatalError
from gateway.config import GatewayConfig
from gateway.prompts import SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload, status_code=200, text="", headers=None):
        self._payload = payload
        self.status_code = status_code
        if text:
            self.text = text
        else:
            try:
                self.text = json.dumps(payload, ensure_ascii=False)
            except TypeError:  # payload 是异常对象时
                self.text = str(payload)
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def chat_response(content, reasoning=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}


class FakeSession:
    """按顺序吐出预设响应（或异常）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        result = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def make_client(**overrides):
    options = {"api_key": "test-key"}
    options.update(overrides)
    config = GatewayConfig(**options)
    return LlmClient(config, session=FakeSession([]), sleep=lambda _seconds: None)


# --------------------------------------------------------------------------- #
# 请求构造
# --------------------------------------------------------------------------- #
def test_endpoint_join():
    assert make_client(api_base_url="https://api.deepseek.com/").endpoint == (
        "https://api.deepseek.com/chat/completions"
    )
    assert make_client(api_base_url="http://127.0.0.1:8801/chat/completions").endpoint == (
        "http://127.0.0.1:8801/chat/completions"
    )


def test_headers_carry_bearer_token():
    headers = make_client().build_headers()
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"].startswith("application/json")


def test_body_has_model_messages_and_json_format():
    body = make_client().build_body({"time": "t", "history": ["rock"]})
    assert body["model"] == "deepseek-flash"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert body["messages"][1]["role"] == "user"
    assert '"time":"t"' in body["messages"][1]["content"]
    assert "rock" in body["messages"][1]["content"]
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 256
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}


def test_body_json_format_and_thinking_switches():
    assert "response_format" not in make_client(enable_json_format=False).build_body({})
    assert make_client(enable_thinking=True).build_body({})["thinking"] == {"type": "enabled"}


def test_prompt_contains_json_word_for_json_mode():
    # 官方 JSON Output 要求提示词里出现 "json" 字样
    assert "json" in SYSTEM_PROMPT.lower()


# --------------------------------------------------------------------------- #
# 取 content
# --------------------------------------------------------------------------- #
def test_extract_content_normal():
    content, has_reasoning, finish = LlmClient.extract_content(
        FakeResponse(chat_response('{"option": "rock"}'))
    )
    assert content == '{"option": "rock"}'
    assert has_reasoning is False
    assert finish == "stop"


def test_extract_content_ignores_reasoning_content():
    response = FakeResponse(chat_response('{"option": "rock"}', reasoning='{"option": "paper"}'))
    content, has_reasoning, _ = LlmClient.extract_content(response)
    assert content == '{"option": "rock"}'  # 思维链不是答案
    assert has_reasoning is True


def test_extract_content_rejects_empty_and_broken():
    with pytest.raises(LlmError):
        LlmClient.extract_content(FakeResponse(chat_response("", reasoning="想了好久")))
    with pytest.raises(LlmError):
        LlmClient.extract_content(FakeResponse(chat_response("", finish_reason="length")))
    with pytest.raises(LlmError):
        LlmClient.extract_content(FakeResponse(chat_response("")))
    with pytest.raises(LlmError):
        LlmClient.extract_content(FakeResponse({"choices": []}))
    with pytest.raises(LlmError):
        LlmClient.extract_content(FakeResponse(ValueError("not json")))


# --------------------------------------------------------------------------- #
# 发送与重试
# --------------------------------------------------------------------------- #
def test_decide_success_returns_content_and_meta():
    client = make_client()
    client._session = FakeSession([FakeResponse(chat_response('{"option": "scissor"}'))])
    content, meta = client.decide({"time": "t"})
    assert content == '{"option": "scissor"}'
    assert meta["attempt"] == 1
    assert meta["http_status"] == 200
    assert meta["has_reasoning"] is False


def test_decide_retries_5xx_then_succeeds():
    session = FakeSession([FakeResponse({}, status_code=503), FakeResponse(chat_response('{"option": "rock"}'))])
    client = make_client(max_retries=2)
    client._session = session
    content, meta = client.decide({"time": "t"})
    assert content == '{"option": "rock"}'
    assert meta["attempt"] == 2
    assert len(session.calls) == 2


def test_decide_retries_empty_content():
    session = FakeSession([FakeResponse(chat_response("")), FakeResponse(chat_response('{"option": "paper"}'))])
    client = make_client(max_retries=1)
    client._session = session
    content, _ = client.decide({"time": "t"})
    assert content == '{"option": "paper"}'
    assert len(session.calls) == 2


def test_decide_retries_network_errors_until_exhausted():
    session = FakeSession([requests.exceptions.ConnectionError("boom")])
    client = make_client(max_retries=2)
    client._session = session
    with pytest.raises(LlmError):
        client.decide({"time": "t"})
    assert len(session.calls) == 3  # 1 次原始 + 2 次重试


def test_decide_fails_fast_on_400():
    session = FakeSession([FakeResponse({"error": {"message": "Model Not Exist"}}, status_code=400)])
    client = make_client(max_retries=3)
    client._session = session
    with pytest.raises(LlmFatalError) as excinfo:
        client.decide({"time": "t"})
    assert len(session.calls) == 1
    assert "Model Not Exist" in str(excinfo.value)


def test_decide_fails_fast_on_401():
    session = FakeSession([FakeResponse({"error": "bad key"}, status_code=401)])
    client = make_client(max_retries=3)
    client._session = session
    with pytest.raises(LlmAuthError):
        client.decide({"time": "t"})
    assert len(session.calls) == 1


def test_decide_without_key_raises_auth_error():
    client = make_client(api_key="", api_key_env="TRAINING_REPO_UNSET_KEY")
    with pytest.raises(LlmAuthError):
        client.decide({"time": "t"})


def test_retry_delay_respects_retry_after_and_cap():
    client = make_client(backoff_base=1.0, backoff_max=4.0)
    assert client.retry_delay(1, "2") == 2.0
    assert client.retry_delay(1, "99") == 4.0  # 不超过 backoff_max
    assert client.retry_delay(1, "bad") <= 2.0
    assert client.retry_delay(5, None) <= 4.0 + 1.0

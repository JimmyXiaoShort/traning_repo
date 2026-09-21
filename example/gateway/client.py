"""上游 API 转发层：构造请求、发送、重试、取出答案。

这一层要解决的真实问题：

* **鉴权**：``Authorization: Bearer <key>``，key 只从配置/环境变量来，绝不写死；
* **请求格式**：OpenAI 兼容的 ``/chat/completions``，开 ``response_format={"type":"json_object"}``
  让上游保证返回合法 JSON，同时默认关掉思考模式（``thinking={"type":"disabled"}``）省时间；
* **重试**：网络错误、429、5xx、**空回复**都值得退避重试；而 401/403/400（key 错、模型名错、
  参数非法）重试没有意义，要立刻失败；
* **只取 content**：思考模型还会返回 ``reasoning_content``（思维链），它**不是**答案，必须丢掉。
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from .config import GatewayConfig
from .prompts import SYSTEM_PROMPT, USER_PREFIX

__all__ = ["LlmError", "LlmAuthError", "LlmFatalError", "LlmClient"]

#: 这些 HTTP 状态码值得重试（服务端临时不可用 / 限流）
RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})


class LlmError(RuntimeError):
    """调用上游失败（重试耗尽或响应结构异常）。"""


class LlmAuthError(LlmError):
    """API Key 缺失/无效（401/403）。"""


class LlmFatalError(LlmError):
    """重试也不会变好的错误（HTTP 400：模型名或参数不合法）。"""


class LlmClient:
    """OpenAI 兼容 Chat Completions 的最小客户端（线程安全）。"""

    def __init__(
        self,
        config: GatewayConfig,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.api_key = config.resolved_api_key()
        self._session = session or requests.Session()
        self._sleep = sleep
        self._log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ #
    # 请求构造
    # ------------------------------------------------------------------ #
    @property
    def endpoint(self) -> str:
        """拼出完整 URL：``<base>/chat/completions``（base 已经带了就不重复拼）。"""
        base = (self.config.api_base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def build_headers(self) -> Dict[str, str]:
        """鉴权与内容类型。"""
        return {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def build_body(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """构造 Chat Completions 请求体。

        必须包含：``model``、``messages``（system = 预设提示词，user = 前缀 + 请求 JSON）、
        ``stream=False``、``temperature``、``max_tokens``；开启 json_object 时加
        ``response_format={"type": "json_object"}`；思考开关按配置发
        ``thinking={"type": "enabled"/"disabled"}``。
        """
        user_content = USER_PREFIX + json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.enable_json_format:
            body["response_format"] = {"type": "json_object"}
        body["thinking"] = {"type": "enabled" if self.config.enable_thinking else "disabled"}
        return body

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    def decide(self, request: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """请求一次出拳，返回 ``(content 文本, 元信息)``；重试耗尽抛 :class:`LlmError`。"""
        if not self.api_key:
            raise LlmAuthError("没有可用的 API Key（参数 api_key 为空且环境变量未设置）")
        if self._closed:
            raise LlmError("客户端已关闭")

        body = self.build_body(request)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self.build_headers()
        timeout = (self.config.connect_timeout, self.config.request_timeout)
        attempts = max(1, self.config.max_retries + 1)

        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                with self._lock:  # requests.Session 复用连接，加锁避免并发问题
                    response = self._session.post(self.endpoint, data=payload, headers=headers, timeout=timeout)
                latency = time.monotonic() - started
                status = response.status_code

                if status == 200:
                    content, has_reasoning, finish_reason = self.extract_content(response)
                    return content, {
                        "attempt": attempt,
                        "http_status": status,
                        "latency_s": round(latency, 3),
                        "has_reasoning": has_reasoning,
                        "finish_reason": finish_reason,
                    }
                if status in (401, 403):
                    raise LlmAuthError("上游拒绝了这个 API Key（HTTP %d）" % status)
                if status == 400:
                    snippet = (response.text or "")[:300].replace("\n", " ")
                    raise LlmFatalError("上游返回 HTTP 400：%s" % snippet)
                if status in RETRY_STATUS and attempt < attempts:
                    delay = self.retry_delay(attempt, response.headers.get("Retry-After"))
                    self._log("HTTP %d，第 %d/%d 次重试将在 %.2fs 后开始" % (status, attempt, attempts - 1, delay))
                    self._sleep(delay)
                    continue
                snippet = (response.text or "")[:300].replace("\n", " ")
                raise LlmError("上游返回 HTTP %d：%s" % (status, snippet))
            except (LlmAuthError, LlmFatalError):
                raise  # 重试无意义
            except LlmError as exc:  # 空回复 / 结构异常等，值得重试
                last_error = exc
                if attempt >= attempts:
                    raise
                delay = self.retry_delay(attempt, None)
                self._log("%s，第 %d/%d 次重试将在 %.2fs 后开始" % (exc, attempt, attempts - 1, delay))
                self._sleep(delay)
            except requests.exceptions.RequestException as exc:  # 网络层错误
                last_error = exc
                if attempt >= attempts:
                    break
                delay = self.retry_delay(attempt, None)
                self._log("%s，第 %d/%d 次重试将在 %.2fs 后开始" % (type(exc).__name__, attempt, attempts - 1, delay))
                self._sleep(delay)

        raise LlmError("上游不可用（已尝试 %d 次）：%s" % (attempts, last_error or "未知错误"))

    def retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        """指数退避 + 抖动；服务端给了 ``Retry-After`` 就听它的（不超过 backoff_max）。"""
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), self.config.backoff_max))
            except (TypeError, ValueError):
                pass
        delay = min(self.config.backoff_base * (2 ** (attempt - 1)), self.config.backoff_max)
        return delay + random.uniform(0.0, self.config.backoff_base)

    # ------------------------------------------------------------------ #
    # 取出答案
    # ------------------------------------------------------------------ #
    @staticmethod
    def extract_content(response: Any) -> Tuple[str, bool, str]:
        """从 200 响应里取出 ``content``，返回 ``(content, 是否带思维链, finish_reason)``。

        三种异常都要当成可重试错误（上层会退避重试，最终兜底）：

        * content 为空且带 ``reasoning_content`` —— 思考模式没关掉，只回了思维链；
        * content 为空且 ``finish_reason == "length"`` —— max_tokens 被思维链/长回答吃满；
        * content 为空 —— 上游偶发空回复（官方 JSON Output 文档承认存在）。
        """
        try:
            data = response.json()
        except ValueError as exc:
            raise LlmError("上游返回的不是 JSON：%s" % exc) from exc
        choices = data.get("choices") or []
        if not choices:
            raise LlmError("上游响应里没有 choices：%s" % (data.get("error") or data))
        choice = choices[0] or {}
        finish_reason = str(choice.get("finish_reason") or "")
        message = choice.get("message") or {}
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())
        if not isinstance(content, str) or not content.strip():
            if has_reasoning:
                raise LlmError("上游只返回了 reasoning_content（思维链），content 为空")
            if finish_reason == "length":
                raise LlmError("上游 content 为空且 finish_reason=length：max_tokens 不够用")
            raise LlmError("上游 content 为空（finish_reason=%s）" % finish_reason)
        return content, has_reasoning, finish_reason

    def close(self) -> None:
        self._closed = True
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass

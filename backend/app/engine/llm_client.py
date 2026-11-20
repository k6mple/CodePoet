"""OpenAI 兼容 Chat Completions 客户端（默认对接 DeepSeek API）。

安全边界：
- API Key 与 Base URL 仅来自服务端 Settings，绝不接受客户端传入；
- 任何异常与错误信息均做脱敏处理，严禁包含 API Key、Authorization 头或完整请求头；
- 强制请求 ``response_format={"type": "json_object"}``，确保模型输出结构化 JSON。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx


class LlmClientError(Exception):
    """脱敏后的上游模型调用错误。"""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class LlmResponse:
    """一次模型调用的原始文本与模型名回执。"""

    content: str
    model: str


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", flags=re.DOTALL | re.IGNORECASE)


def extract_json_object(raw: str) -> dict[str, Any]:
    """从模型文本中提取 JSON 对象，兼容偶发的代码块包裹。"""
    text = (raw or "").strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("模型输出必须是顶层 JSON 对象")
    return parsed


def chat_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    messages: list[dict[str, str]],
) -> LlmResponse:
    """调用 OpenAI 兼容 ``/chat/completions`` 并返回文本内容与实际模型名。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LlmClientError(
            "ASSISTANT_TIMEOUT",
            f"模型服务在 {timeout_seconds:.0f} 秒内未响应，请稍后重试",
            {"timeoutSeconds": timeout_seconds},
        ) from exc
    except httpx.HTTPError as exc:
        raise LlmClientError(
            "ASSISTANT_UPSTREAM_ERROR",
            "模型服务网络连接失败，请稍后重试",
            {"reason": type(exc).__name__},
        ) from exc

    if response.status_code >= 400:
        # 严禁回显上游响应正文或请求头，防止意外透传敏感上下文
        code = "ASSISTANT_UPSTREAM_ERROR"
        if response.status_code == 429:
            msg = "模型服务当前触发频率或额度限制，请稍后重试"
        elif response.status_code in (401, 403):
            msg = "模型服务鉴权失败，请检查服务端密钥配置"
        else:
            msg = f"模型服务返回异常状态（HTTP {response.status_code}）"
        raise LlmClientError(code, msg, {"httpStatus": response.status_code})

    try:
        data = response.json()
        choices = data.get("choices") or []
        content = choices[0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("empty content")
    except Exception as exc:  # noqa: BLE001
        raise LlmClientError(
            "ASSISTANT_UPSTREAM_ERROR",
            "模型服务返回了无法解析的响应结构",
            {"reason": type(exc).__name__},
        ) from exc

    actual_model = str(data.get("model") or model).strip()[:120] or model
    return LlmResponse(content=content, model=actual_model)

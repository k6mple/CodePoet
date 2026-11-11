"""请求级上下文。

服务层按分层约定不接触 Request/Response，但审计日志必须记 ``requestId``
——它是把一个请求改过的多张表串起来的唯一线索（改了知识、又改了标签、
再写了一条审核记录，靠 requestId 才能还原成一次操作）。

用 ``ContextVar`` 而不是层层传参：传参会污染每个服务的签名，而这些签名
本来就不该知道 HTTP 的存在。中间件在入口 set 一次，服务层按需取。
"""

from __future__ import annotations

import contextvars

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "codepoet_request_id", default=None
)


def set_request_id(value: str) -> None:
    """由中间件在请求入口调用。"""
    _request_id.set(value)


def current_request_id(fallback: str = "req-detached") -> str:
    """取当前请求 ID。

    脱离 HTTP 上下文时（脚本、后台任务、直接调服务的测试）返回占位值，
    不让审计因为拿不到 ID 就整体失败——少一个可追溯的字段，
    总好过关键操作完全不留痕。
    """
    return _request_id.get() or fallback

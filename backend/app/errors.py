"""类型化 API 错误。

业务代码只负责抛出 ``ApiError``（或下面的语义化子类），统一响应格式由
``app/main.py`` 里的异常处理器完成。这样控制器不必手写 JSONResponse，
也不会出现某个接口漏了 requestId 的情况。

错误码严格取自 docs/接口文档.md 第 2.2 节的表格，前端按 code 分支处理，
所以不能随手编新名字。
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """带 HTTP 状态码与业务错误码的异常。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: list[dict[str, str]] | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        # 字段级错误，供前端定位到具体输入框
        self.field_errors = field_errors or []
        self.details = details


# ---- 常用错误的快捷构造 ---------------------------------------------------
# 401 一律不区分「账号不存在」和「密码错误」，避免账号枚举


def auth_required() -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", "请先登录")


def session_expired() -> ApiError:
    return ApiError(401, "SESSION_EXPIRED", "登录已过期，请重新登录")


def invalid_credentials() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "用户名或密码不正确")


def csrf_invalid() -> ApiError:
    return ApiError(403, "CSRF_INVALID", "CSRF token 缺失或已失效，请重新获取")


def forbidden(message: str = "没有执行该操作的权限") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def not_found(message: str = "资源不存在") -> ApiError:
    """私有资源无权访问时也用它，不暴露资源是否存在。"""
    return ApiError(404, "RESOURCE_NOT_FOUND", message)


def revision_conflict(expected: int, actual: int) -> ApiError:
    return ApiError(
        409,
        "REVISION_CONFLICT",
        f"数据已被其他人修改（期望版本 {expected}，当前版本 {actual}），请刷新后重试",
    )


def validation_error(message: str, field_errors: list[dict[str, str]] | None = None) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", message, field_errors)


def ir_invalid(message: str, details: Any = None) -> ApiError:
    """IR 结构不合法。details 里带 ValidationReport，前端可直接标注节点。"""
    return ApiError(422, "IR_INVALID", message, details=details)


def unsupported_capability(message: str) -> ApiError:
    """请求的能力还没实现。契约要求明确拒绝，不许假装成功。"""
    return ApiError(422, "UNSUPPORTED_CAPABILITY", message)

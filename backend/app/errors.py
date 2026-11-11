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


# ---- P1A 分类体系（标签 / 本体）------------------------------------------


def dependency_conflict(message: str, details: Any = None) -> ApiError:
    """还有存活的下级依赖，不能停用。契约 2.2 表里归 409。"""
    return ApiError(409, "DEPENDENCY_CONFLICT", message, details=details)


def tag_conflict(message: str, details: Any = None) -> ApiError:
    """互斥标签同时出现。契约 2.2 把它归在 422（输入本身合法，组合非法）。"""
    return ApiError(422, "TAG_CONFLICT", message, details=details)


def ontology_cycle(message: str, details: Any = None) -> ApiError:
    """本体父子关系会成环。改完就查不出根，必须拒掉。"""
    return ApiError(422, "ONTOLOGY_CYCLE", message, details=details)


def invalid_state(message: str) -> ApiError:
    """当前状态不允许这个操作（例如更新已停用的条目）。"""
    return ApiError(409, "INVALID_STATE", message)


def no_semantic_match(message: str, details: Any = None) -> ApiError:
    """输入没有命中任何映射规则。

    这里必须报错而不是画一张兜底图：用户输入"随便什么"却得到一朵莲，
    会以为规则生效了，实际是系统在替它做决定。错误码取自契约 422 分支。
    """
    return ApiError(422, "NO_SEMANTIC_MATCH", message, details=details)


def unpublished_source(message: str, details: Any = None) -> ApiError:
    """内容派生自未发布的源，不能公开送审。

    典型场景：用草稿模板生成的图被存成作品后直接送审。审核人看到的
    会是一张「来源还不存在」的作品；等模板真发布了，这张图也不会自动
    跟着更新（契约 5.1 明确：模板后来通过不会改旧 generation）。
    所以这里必须拒绝，让用户先让模板过审再重新生成。
    """
    return ApiError(409, "UNPUBLISHED_SOURCE", message, details=details)


def self_review_forbidden(message: str = "不能审核自己提交的内容") -> ApiError:
    return ApiError(403, "SELF_REVIEW_FORBIDDEN", message)


def execution_busy(
    message: str = "未完成的上传任务已达上限，请先完成、取消或等待旧任务过期",
    details: Any = None,
) -> ApiError:
    """契约 2.3 的 429：每用户最多两个未完成上传任务。

    这不是防刷限流，而是因为每个未完成任务都会占住浏览器端的一次完整
    出图与上传流程；放开会让页面同时挂着多个互不相干的上传会话，
    前端也很难判断该把哪一帧交给哪个任务。
    """
    return ApiError(429, "EXECUTION_BUSY", message, details=details)

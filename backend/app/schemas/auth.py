"""认证与账号相关的请求/响应模型。

字段名、长度、枚举严格对应 ``docs/openapi-core.json`` 的
``LoginRequest`` / ``RegisterRequest`` / ``AuthSession`` /
``CsrfResponse`` / ``MeUpdate`` / ``User`` / ``RoleApplication``。

时间字段统一用 ISO-8601 字符串（UTC），与数据库 TEXT 列一一对应，
避免「数据库存字符串、模型存 datetime」两处来回转换。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["creator", "developer", "expert", "admin"]
UserStatus = Literal["active", "disabled"]

# 注册时用户名只能用字母数字下划线；登录时不做 pattern 限制
# （历史账号可能含连字符，登录不该因为格式被挡）
RegisterUsername = Annotated[
    str, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
]
LoginUsername = Annotated[str, Field(min_length=3, max_length=32)]

CsrfToken = Annotated[str, Field(min_length=32, max_length=256)]


class RoleApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestedRole: Role
    status: Literal["pending", "approved", "rejected"]
    reason: str = Field(max_length=500)
    decidedAt: str | None


class User(BaseModel):
    """对外暴露的用户信息。绝不包含 password_hash。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int
    createdAt: str
    updatedAt: str
    username: str
    nickname: str
    bio: str
    role: Role
    status: UserStatus
    roleApplication: RoleApplication | None
    # 由角色推导，不存库，避免角色改了权限没跟着改
    permissions: list[str]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: RegisterUsername
    password: str = Field(min_length=10, max_length=128)
    nickname: str | None = Field(default=None, max_length=40)
    # 注册一律先成为 creator，申请的更高角色走审批
    requestedRole: Role | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: LoginUsername
    password: str = Field(min_length=1, max_length=128)


class AuthSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: User
    csrfToken: CsrfToken
    expiresAt: str


class CsrfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csrfToken: CsrfToken
    expiresAt: str


class MeUpdate(BaseModel):
    """修改个人信息。契约要求 minProperties=2，即除了版本号还要改点别的。"""

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    nickname: str | None = Field(default=None, max_length=40)
    bio: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _requires_a_change(self) -> "MeUpdate":
        if self.nickname is None and self.bio is None:
            raise ValueError("除了 expectedRevision，至少要提供 nickname 或 bio 之一")
        return self

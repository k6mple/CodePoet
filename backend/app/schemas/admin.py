"""管理员视角的用户列表与角色审批模型。

对标 ``docs/openapi-core.json`` 的 ``UserPage`` 与 ``RoleDecision``。
``User`` 复用 ``app/schemas/auth.py`` 里的定义——同一个用户对象在
``/me`` 和 ``/admin/users`` 里必须是同一套字段，否则前端要写两套解析。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import Role, User

ApplicationStatus = Literal["pending", "approved", "rejected"]

VALID_ROLES: frozenset[str] = frozenset({"creator", "developer", "expert", "admin"})


class RoleDecision(BaseModel):
    """批准或拒绝一次角色申请。

    ``role`` 必须等于用户当初申请的 ``role_applied_for``，服务层会比对——
    这条不是形式主义：审批接口如果能任选角色，一次误操作就能把普通创作者
    直接提成管理员。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    approved: bool
    role: Role
    # 理由必填、至少 1 个字：审批是留痕操作，空理由等于没记录
    reason: str = Field(min_length=1, max_length=500)


class UserPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[User] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


__all__ = ["ApplicationStatus", "RoleDecision", "UserPage", "VALID_ROLES"]

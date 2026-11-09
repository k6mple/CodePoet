"""管理员接口：用户列表与角色审批（P1A-01）。

``admin.py`` 最终还要承载审核台（P1A-04）、导入导出（P1A-07）与审计检索，
本提交只实现当前任务的两个操作。**不注册空壳路由**——契约明确要求
「未实现时不展示入口」，返回假成功的接口比缺接口更糟。

权限判断全在 ``app/services/roles.py``：控制器只解析参数、调服务、
格式化响应，这样「禁止自审」「角色必须与申请一致」这类规则能脱离
HTTP 单独测。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.admin import ApplicationStatus, RoleDecision, UserPage
from app.schemas.auth import Role, User
from app.services import accounts
from app.services import roles as roles_service

router = APIRouter(tags=["管理"])

IdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="用户 ID，不透明串",
    ),
]


@router.get(
    "/admin/users",
    response_model=UserPage,
    summary="用户与角色申请列表",
    description=(
        "仅管理员可用。applicationStatus 按申请状态过滤；"
        "role 按当前角色过滤；q 同时匹配用户名与昵称。"
    ),
)
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    role: Role | None = Query(default=None),
    application_status: ApplicationStatus | None = Query(
        default=None, alias="applicationStatus"
    ),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    admin: UserModel = Depends(require_user),
) -> UserPage:
    return roles_service.list_users(
        db,
        admin,
        page=page,
        page_size=page_size,
        role=role,
        application_status=application_status,
        q=q,
    )


@router.post(
    "/admin/users/{id}/role-decision",
    response_model=User,
    summary="批准或拒绝角色申请",
    description=(
        "仅管理员可用，且禁止审批自己的申请。role 必须等于用户申请的角色；"
        "批准后角色立即生效，拒绝时保留用户当前角色（不降级）。"
    ),
)
def decide_role(
    payload: RoleDecision,
    id: IdPath,
    db: Session = Depends(get_db),
    admin: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> User:
    updated = roles_service.decide_role(db, admin, id, payload)
    return accounts.build_user_schema(updated)

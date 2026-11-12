"""管理员接口：用户与角色审批（P1A-01）、备份导入导出（P1A-07）。

**不注册空壳路由**——契约明确要求「未实现时不展示入口」，
返回假成功的接口比缺接口更糟。审计检索在 ``api/audit.py``。

权限判断全在服务层：控制器只解析参数、调服务、格式化响应，
这样「禁止自审」「重复 ID 整批拒绝」这类规则能脱离 HTTP 单独测。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.admin import ApplicationStatus, RoleDecision, UserPage
from app.schemas.auth import Role, User
from app.schemas.operations import (
    BackupPackage,
    CoreImportPackage,
    ExportRequest,
    ImportApplied,
    ImportApply,
    ImportValidation,
)
from app.services import accounts
from app.services import import_export
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


@router.post(
    "/admin/exports",
    response_model=BackupPackage,
    summary="导出知识/模板/规则与作品 IR 备份",
    description=(
        "权限 A。按类型整库导出，core-demo 不支持按 ids 选择、智能合并或 "
        "workSnapshots 导入。结果超过 5MiB 时返回 413，请分类型导出。"
    ),
)
def create_export(
    request: Request,
    payload: ExportRequest,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> BackupPackage:
    # settings 用于核对产物文件是否真的还在（workSnapshots.missingArtifacts）；
    # 直接取而不 getattr 兜底：配置缺失应该立刻炸，而不是悄悄降级成「都算在」
    return import_export.export_backup(
        db, user, list(payload.types), request.app.state.settings
    )


@router.post(
    "/admin/imports/validate",
    response_model=ImportValidation,
    summary="预校验 JSON 备份包",
    description=(
        "权限 A。逐条校验结构与引用：包内重复 ID、与库内 ID 冲突、"
        "parentId 悬空、条目引用不存在的标签或概念，都会进 errors。"
        "valid=false 时仍返回 200，前端按 errors 逐条展示。"
        "凭证 10 分钟有效、绑定用户、单次消费。"
    ),
)
def validate_import(
    payload: CoreImportPackage,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> ImportValidation:
    return import_export.validate_import(db, user, payload)


@router.post(
    "/admin/imports/apply",
    response_model=ImportApplied,
    status_code=status.HTTP_201_CREATED,
    summary="事务应用已验证导入",
    description=(
        "权限 A。全程一个事务，任一步失败整批回滚。"
        "验证后若 dataRevision 变化，返回 409 要求重新验证——"
        "之前的结构与引用结论已经失效。导入的模板一律落 draft。"
    ),
)
def apply_import(
    payload: ImportApply,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> ImportApplied:
    return import_export.apply_import(db, user, payload)

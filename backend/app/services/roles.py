"""角色申请的审批流程与管理员用户列表。

三条硬规则，都不是形式主义：

1. **只有 admin 能审批**（``users.approveRole``）。这是整个权限体系里唯一
   能把人往上提的操作，放给 expert 等于把提权路径扩大一圈。
2. **禁止本人审批**。自己给自己批角色，权限体系就退化成摆设。
3. **批准的角色必须等于申请的角色**。如果审批接口能任选角色，一次误操作
   就能把普通创作者直接提成 admin，而审批页面上根本看不出来。

拒绝时**不降级**：用户当前是什么角色就留在什么角色，只是把申请标成
rejected。契约写的是「拒绝时保留 creator」——对本来就是 creator 的申请者
两者一致；但对「已经是 developer 又申请 expert 被拒」的情况，
把它降回 creator 显然不是本意。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import errors
from app.models.users import User
from app.schemas.admin import ApplicationStatus, RoleDecision, UserPage
from app.schemas.auth import Role
from app.services import accounts, permissions


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_admin(actor: User) -> None:
    if not permissions.has_permission(actor, permissions.USERS_APPROVE_ROLE):
        raise errors.forbidden("只有管理员能审批角色申请")


def list_users(
    db: Session,
    actor: User,
    *,
    page: int = 1,
    page_size: int = 20,
    role: Role | None = None,
    application_status: ApplicationStatus | None = None,
    q: str | None = None,
) -> UserPage:
    _require_admin(actor)

    statement = select(User)
    if role is not None:
        statement = statement.where(User.role == role)
    if application_status is not None:
        statement = statement.where(
            User.role_application_status == application_status
        )
    if q:
        # 用户名和昵称一起匹配：审批时通常只记得其中一个
        statement = statement.where(
            or_(
                User.username.contains(q, autoescape=True),
                User.nickname.contains(q, autoescape=True),
            )
        )

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        db.scalars(
            statement.order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return UserPage(
        items=[accounts.build_user_schema(row) for row in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def decide_role(
    db: Session, actor: User, user_id: str, payload: RoleDecision
) -> User:
    _require_admin(actor)

    if actor.id == user_id:
        raise errors.forbidden("不能审批自己的角色申请")

    target = db.get(User, user_id)
    if target is None:
        raise errors.not_found("用户不存在")
    if target.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, target.revision)

    if target.role_applied_for is None or target.role_application_status != "pending":
        # 没有申请、或已经处理过：再批一次会把结果覆盖掉，直接拒
        raise errors.invalid_state("该用户当前没有待处理的角色申请")

    if payload.role != target.role_applied_for:
        raise errors.validation_error(
            "批准的角色必须与申请的角色一致",
            [
                {
                    "path": "role",
                    "message": f"用户申请的是 {target.role_applied_for}",
                }
            ],
        )

    if payload.approved:
        target.role = payload.role
        target.role_application_status = "approved"
    else:
        # 不降级：当前角色原样保留，只把申请标成已拒绝
        target.role_application_status = "rejected"

    target.role_decision_reason = payload.reason
    target.role_decided_at = _now_iso()
    target.role_decided_by = actor.id
    target.revision += 1
    target.updated_at = _now_iso()
    db.commit()
    return target

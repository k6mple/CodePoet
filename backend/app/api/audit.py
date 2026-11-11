"""审计日志检索。

只有管理员能看（``audit.read``）。这里不提供写入接口：审计记录由业务
服务在关键操作里顺带写入（``app/services/audit.py``），对外只读。
能通过 HTTP 写审计的系统，审计本身就不可信。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import errors
from app.deps import get_db, require_user
from app.models.users import User as UserModel
from app.schemas.operations import AuditLogPage
from app.services import audit as audit_service
from app.services import permissions

router = APIRouter(tags=["审计"])


@router.get(
    "/audit-logs",
    response_model=AuditLogPage,
    summary="审计日志检索",
    description=(
        "权限 A。支持按 actorId / action / targetType / requestId / 时间区间过滤。"
        "时间用 ISO 8601 UTC 字符串比较——它的字典序与时间序一致。"
        "记录只读：数据库层面也禁止 UPDATE 与 DELETE。"
    ),
)
def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    actorId: str | None = Query(default=None, max_length=80),
    action: str | None = Query(default=None, max_length=80),
    targetType: str | None = Query(default=None, max_length=80),
    requestId: str | None = Query(default=None, max_length=80),
    fromTime: str | None = Query(default=None, max_length=40),
    toTime: str | None = Query(default=None, max_length=40),
    db: Session = Depends(get_db),
    viewer: UserModel = Depends(require_user),
) -> AuditLogPage:
    if not permissions.has_permission(viewer, permissions.AUDIT_READ):
        raise errors.forbidden("只有管理员可以查看审计日志")
    return audit_service.search(
        db,
        page=page,
        page_size=page_size,
        actor_id=actorId,
        action=action,
        target_type=targetType,
        request_id=requestId,
        from_time=fromTime,
        to_time=toTime,
    )

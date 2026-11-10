"""关键写操作的审计。

设计上的三层约束：

1. **不记录敏感字段**。``input_summary`` 由调用方显式挑字段，绝不把整个
   请求体倒进去——密码、CSRF token、Cookie 一旦落库就再也收不回来。
   这条靠「白名单式地传参」保证，而不是靠事后过滤。

2. **跟随业务事务**。``record`` 只 ``add`` 不 ``commit``：审核成功与
   「我审过这件事」的留痕必须同生共死，否则会出现「记录说审了、实际没审」
   或者反过来。失败场景需要独立事务时用 ``record_failure``。

3. **requestId 从上下文取**。服务层拿不到 Request 对象，靠
   ``observability.current_request_id()``。拿不到时用占位值，
   不让审计因为缺一个字段就整体失败。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.operations import AuditLog as AuditLogModel
from app.models.users import User
from app.observability import current_request_id
from app.schemas.operations import AuditLog, AuditLogPage

__all__ = ["record", "record_failure", "search"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"aud-{uuid4().hex}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def record(
    db: Session,
    *,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: str | None = None,
    version_id: str | None = None,
    input_summary: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    result: str = "success",
    error_code: str | None = None,
    request_id: str | None = None,
) -> AuditLogModel:
    """往当前事务里加一条审计。**不 commit**，由调用方跟业务一起提交。"""
    row = AuditLogModel(
        id=_new_id(),
        created_at=_now_iso(),
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else None,
        action=action[:80],
        target_type=target_type[:80],
        target_id=target_id,
        version_id=version_id,
        request_id=(request_id or current_request_id())[:80],
        parent_request_id=None,
        generation_id=None,
        job_id=None,
        work_id=None,
        # 只存调用方挑出来的字段，绝不整份塞
        input_summary_json=json.dumps(input_summary or {}, ensure_ascii=False),
        before_json=_dumps(before),
        after_json=_dumps(after),
        result=result,
        error_code=error_code[:80] if error_code else None,
    )
    db.add(row)
    return row


def record_failure(
    db: Session,
    *,
    actor: User | None,
    action: str,
    error_code: str,
    target_type: str = "",
    target_id: str | None = None,
    input_summary: dict[str, Any] | None = None,
) -> None:
    """记录一次失败并立即提交。

    调用点通常在 ``except`` 里，此时业务事务已被回滚——如果放进同一个
    事务，这条失败记录会跟着一起消失，而那正是最需要留下的一条。
    """
    record(
        db,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        input_summary=input_summary,
        result="failure",
        error_code=error_code,
    )
    db.commit()


def _build(row: AuditLogModel) -> AuditLog:
    return AuditLog(
        id=row.id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        actorId=row.actor_id,
        actorRole=row.actor_role,  # type: ignore[arg-type]
        action=row.action,
        targetType=row.target_type,
        targetId=row.target_id,
        versionId=row.version_id,
        requestId=row.request_id,
        parentRequestId=row.parent_request_id,
        generationId=row.generation_id,
        jobId=row.job_id,
        workId=row.work_id,
        inputSummary=json.loads(row.input_summary_json or "{}"),
        before=json.loads(row.before_json) if row.before_json else None,
        after=json.loads(row.after_json) if row.after_json else None,
        result=row.result,  # type: ignore[arg-type]
        errorCode=row.error_code,
    )


def search(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    actor_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    request_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
) -> AuditLogPage:
    """按条件检索。时间用字符串比较——ISO 8601 UTC 字符串的字典序与时间序一致。"""
    conditions = []
    if actor_id:
        conditions.append(AuditLogModel.actor_id == actor_id)
    if action:
        conditions.append(AuditLogModel.action == action)
    if target_type:
        conditions.append(AuditLogModel.target_type == target_type)
    if request_id:
        conditions.append(AuditLogModel.request_id == request_id)
    if from_time:
        conditions.append(AuditLogModel.created_at >= from_time)
    if to_time:
        conditions.append(AuditLogModel.created_at <= to_time)

    total = db.scalar(
        select(func.count()).select_from(AuditLogModel).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(AuditLogModel)
        .where(*conditions)
        .order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return AuditLogPage(
        items=[_build(row) for row in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )

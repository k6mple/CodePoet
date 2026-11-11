"""编辑会话与快照接口。

控制器只解析参数、调服务、声明响应模型。业务规则（保存即产生快照、
恢复留退路、无所有权 404）全在 ``app/services/sessions.py``。

六个路由全部是权限 O（本人），所以统一 ``require_user`` + 服务层判归属。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.sessions import (
    CreationSession,
    CreationSessionPage,
    RestoreRequest,
    SessionCreate,
    SessionUpdate,
    SnapshotPage,
    SnapshotSaved,
)
from app.services import sessions as sessions_service

router = APIRouter(tags=["会话"])

"""路径 ID 注解。

不用模块级 ``ID_PATH = Path(...)`` 共用默认值：FastAPI 会把首个注册时的
参数名记在对象上，后续路由会要求错误的参数名（P0-04 踩过一次）。
``Annotated`` 每次生成独立副本。
"""
SessionIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="会话 ID，不透明串",
    ),
]


@router.get(
    "/sessions",
    response_model=CreationSessionPage,
    summary="个人创作会话列表",
)
def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> CreationSessionPage:
    return sessions_service.list_sessions(db, user, page, page_size)


@router.post(
    "/sessions",
    response_model=CreationSession,
    status_code=status.HTTP_201_CREATED,
    summary="创建编辑会话",
    description=(
        "创建当前编辑现场及首个不可变快照，latestSnapshotId 指向它。"
        "不执行任何代码。"
    ),
)
def create_session(
    payload: SessionCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> CreationSession:
    return sessions_service.create_session(db, user, payload)


@router.get(
    "/sessions/{session_id}",
    response_model=CreationSession,
    summary="读取本人编辑会话",
)
def get_session(
    session_id: SessionIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> CreationSession:
    return sessions_service.get_session(db, user, session_id)


@router.patch(
    "/sessions/{session_id}",
    response_model=CreationSession,
    summary="保存当前编辑现场",
    description=(
        "显式保存 name/state，并在同一事务追加 SessionSnapshot，revision 只加 1。"
        "不是每次击键都自动保存——那样历史会被无意义的中间态淹没。"
    ),
)
def update_session(
    payload: SessionUpdate,
    session_id: SessionIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> CreationSession:
    return sessions_service.update_session(db, user, session_id, payload)


@router.get(
    "/sessions/{session_id}/snapshots",
    response_model=SnapshotPage,
    summary="会话快照列表",
    description="按 sessionRevision 降序。同一秒落两条快照时，时间戳分不出先后，revision 能。",
)
def list_snapshots(
    session_id: SessionIdPath,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> SnapshotPage:
    return sessions_service.list_snapshots(db, user, session_id, page, page_size)


@router.post(
    "/sessions/{session_id}/restore",
    response_model=SnapshotSaved,
    summary="从旧快照恢复并追加新快照",
    description=(
        "snapshot 必须属于该会话；会新建「恢复前自动保存」与「恢复后」两条快照，"
        "session revision 只加 1；不执行源码。"
    ),
)
def restore_session(
    payload: RestoreRequest,
    session_id: SessionIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> SnapshotSaved:
    return sessions_service.restore_session(
        db, user, session_id, payload.expectedRevision, payload.snapshotId
    )

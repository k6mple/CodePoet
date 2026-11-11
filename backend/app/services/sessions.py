"""编辑会话与快照的业务规则。

三条规则是这个模块的全部价值：

1. **保存即产生快照，且在同一事务里**
   契约 5.2：「每次用户显式保存时，同事务追加 snapshot，session revision 只加 1」。
   分开提交会留下「revision 涨了但没有对应快照」的空档，回退时就少一档。

2. **恢复要留退路**
   恢复旧快照时先写一份「恢复前自动保存」，再写「恢复后」快照。只写一个的话，
   用户点错恢复就再也回不到原来的现场——这跟「可恢复历史」的承诺矛盾。

3. **无所有权一律 404**
   和作品、模板一致：返回 403 等于承认这个 ID 是别人的会话。

分层约定：不导入 FastAPI，不接触 Request/Response。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import errors
from app.models.sessions import CreationSession as SessionModel
from app.models.sessions import SessionSnapshot as SnapshotModel
from app.models.users import User
from app.schemas.sessions import (
    CreationSession as CreationSessionSchema,
    CreationSessionPage,
    EditorState,
    SessionCreate,
    SessionUpdate,
    Snapshot as SnapshotSchema,
    SnapshotPage,
    SnapshotSaved,
)

__all__ = [
    "create_session",
    "get_session",
    "list_sessions",
    "list_snapshots",
    "restore_session",
    "update_session",
]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _require_owned(db: Session, user: User, session_id: str) -> SessionModel:
    row = db.get(SessionModel, session_id)
    if row is None or row.owner_id != user.id:
        raise errors.not_found("会话不存在")
    return row


def _cas_update(
    db: Session,
    row: SessionModel,
    expected: int,
    values: dict,
    now: str,
) -> int:
    """带 revision 条件的更新，返回受影响行数。

    这是保存与恢复的唯一并发闸门。只在 Python 里比一次 revision 是不够的：
    两个并发请求都会读到同一个 expectedRevision、都通过比较，然后各自
    无条件 UPDATE，后写的把先写的覆盖掉，而两边都收到 200——更新静默丢失，
    客户端无从察觉。把条件放进 UPDATE 的 WHERE，数据库保证只有一个能让
    rowcount 变成 1。
    """
    statement = (
        update(SessionModel)
        .where(SessionModel.id == row.id, SessionModel.revision == expected)
        .values(revision=expected + 1, updated_at=now, **values)
    )
    return db.execute(statement).rowcount


def _raise_conflict(db: Session, session_id: str, expected: int) -> None:
    """CAS 失败：回滚（失败请求不能留下任何快照），报当前真实 revision。"""
    db.rollback()
    actual = db.scalar(
        select(SessionModel.revision).where(SessionModel.id == session_id)
    )
    raise errors.revision_conflict(expected, actual if actual is not None else 0)


def _parse_state(raw: str) -> EditorState:
    """库里的 state_json 解析失败属于服务端数据损坏，让它报 500。

    这里不兜底成空状态：静默返回一个假的编辑现场，用户下次保存就会
    把他真正的草稿覆盖掉。
    """
    return EditorState.model_validate_json(raw)


def _build_session(row: SessionModel) -> CreationSessionSchema:
    return CreationSessionSchema(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        updatedAt=_parse_dt(row.updated_at) or datetime.now(timezone.utc),
        ownerId=row.owner_id,
        name=row.name,
        state=_parse_state(row.state_json),
        latestSnapshotId=row.latest_snapshot_id,
    )


def _build_snapshot(row: SnapshotModel) -> SnapshotSchema:
    return SnapshotSchema(
        id=row.id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        sessionId=row.session_id,
        sessionRevision=row.session_revision,
        state=_parse_state(row.state_json),
        name=row.name,
        note=row.note,
        restoredFromSnapshotId=row.restored_from_snapshot_id,
    )


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def create_session(
    db: Session, user: User, payload: SessionCreate
) -> CreationSessionSchema:
    """建会话 + 首个不可变快照。不执行任何代码。"""
    now = _now_iso()
    session_id = _new_id("ses")
    snapshot_id = _new_id("snp")
    state_json = payload.state.model_dump_json()

    row = SessionModel(
        id=session_id,
        owner_id=user.id,
        name=payload.name,
        state_json=state_json,
        latest_snapshot_id=snapshot_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.add(
        SnapshotModel(
            id=snapshot_id,
            session_id=session_id,
            session_revision=1,
            state_json=state_json,
            name=payload.name,
            note="创建会话",
            restored_from_snapshot_id=None,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(row)
    return _build_session(row)


def list_sessions(db: Session, user: User, page: int, page_size: int) -> CreationSessionPage:
    """本人会话列表。按契约全局约定：createdAt 降序、id 降序。"""
    total = db.scalar(
        select(func.count())
        .select_from(SessionModel)
        .where(SessionModel.owner_id == user.id)
    ) or 0
    rows = db.scalars(
        select(SessionModel)
        .where(SessionModel.owner_id == user.id)
        .order_by(SessionModel.created_at.desc(), SessionModel.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return CreationSessionPage(
        items=[_build_session(row) for row in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def get_session(db: Session, user: User, session_id: str) -> CreationSessionSchema:
    return _build_session(_require_owned(db, user, session_id))


def update_session(
    db: Session, user: User, session_id: str, payload: SessionUpdate
) -> CreationSessionSchema:
    """显式保存。同事务追加快照，revision 只加 1。"""
    row = _require_owned(db, user, session_id)
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    now = _now_iso()
    new_revision = payload.expectedRevision + 1
    name = payload.name if payload.name is not None else row.name
    state_json = (
        payload.state.model_dump_json() if payload.state is not None else row.state_json
    )
    snapshot_id = _new_id("snp")

    # 真正的并发闸门在这里：revision 条件进 UPDATE 的 WHERE。
    # 只在 Python 里比一次是不够的——两个并发请求都会读到同一个
    # expectedRevision、都通过比较，然后各自无条件更新，后写的把先写的
    # 覆盖掉，而两边都收到 200，客户端根本无从察觉。
    affected = _cas_update(
        db,
        row,
        payload.expectedRevision,
        {
            "name": name,
            "state_json": state_json,
            "latest_snapshot_id": snapshot_id,
        },
        now,
    )
    if affected != 1:
        _raise_conflict(db, row.id, payload.expectedRevision)

    db.add(
        SnapshotModel(
            id=snapshot_id,
            session_id=row.id,
            # 快照记「这次保存后的 revision」，和会话对上号
            session_revision=new_revision,
            state_json=state_json,
            name=name,
            note="保存",
            restored_from_snapshot_id=None,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(row)
    return _build_session(row)


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


def list_snapshots(
    db: Session, user: User, session_id: str, page: int, page_size: int
) -> SnapshotPage:
    """历史快照。

    按 ``session_revision`` 降序排，而不是只按 ``created_at``：同一秒里
    落两条快照（恢复场景）时，时间戳无法分辨先后，revision 能。
    """
    row = _require_owned(db, user, session_id)
    total = db.scalar(
        select(func.count())
        .select_from(SnapshotModel)
        .where(SnapshotModel.session_id == row.id)
    ) or 0
    rows = db.scalars(
        select(SnapshotModel)
        .where(SnapshotModel.session_id == row.id)
        .order_by(
            SnapshotModel.session_revision.desc(),
            SnapshotModel.created_at.desc(),
            SnapshotModel.id.desc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return SnapshotPage(
        items=[_build_snapshot(item) for item in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def restore_session(
    db: Session, user: User, session_id: str, expected_revision: int, snapshot_id: str
) -> SnapshotSaved:
    """从旧快照恢复。

    写两条快照：恢复前（安全网）与恢复后（新状态）；会话 revision 只加 1。
    如果只写恢复后那条，用户点错就再也回不到原来的现场——「可恢复历史」
    这个承诺就只剩前半句。
    """
    row = _require_owned(db, user, session_id)
    snapshot = db.get(SnapshotModel, snapshot_id)
    # 快照必须属于这个会话，否则会拿别人的历史来覆盖自己的现场
    if snapshot is None or snapshot.session_id != row.id:
        raise errors.not_found("快照不存在")
    if row.revision != expected_revision:
        raise errors.revision_conflict(expected_revision, row.revision)

    now = _now_iso()
    new_revision = expected_revision + 1

    # 恢复前的现场必须在 CAS 之前抓下来：CAS 走的是 bulk UPDATE，会绕过
    # ORM 的 identity map，之后再读 row 拿到的已经是新值了。
    before_state_json = row.state_json
    before_name = row.name
    restored_id = _new_id("snp")

    # 恢复同样要过 CAS：两个人同时点恢复，后一个的「恢复前自动保存」
    # 会记录一份已经被覆盖的现场，那条安全网就没意义了。
    affected = _cas_update(
        db,
        row,
        expected_revision,
        {
            "state_json": snapshot.state_json,
            "name": snapshot.name or before_name,
            "latest_snapshot_id": restored_id,
        },
        now,
    )
    if affected != 1:
        _raise_conflict(db, row.id, expected_revision)

    # 恢复前自动保存：记录「点恢复之前是什么样」
    db.add(
        SnapshotModel(
            id=_new_id("snp"),
            session_id=row.id,
            session_revision=expected_revision,
            state_json=before_state_json,
            name=before_name,
            note="恢复前自动保存",
            restored_from_snapshot_id=None,
            created_at=now,
        )
    )

    restored = SnapshotModel(
        id=restored_id,
        session_id=row.id,
        session_revision=new_revision,
        state_json=snapshot.state_json,
        name=snapshot.name or before_name,
        note=f"从 {snapshot.id} 恢复",
        restored_from_snapshot_id=snapshot.id,
        created_at=now,
    )
    db.add(restored)

    db.commit()
    db.refresh(row)
    db.refresh(restored)
    return SnapshotSaved(session=_build_session(row), snapshot=_build_snapshot(restored))
